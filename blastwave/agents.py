"""Multi-agent incident response on top of FalkorDB.

triage -> investigator -> comms. Agents never pass findings to each other in prompts: each one
writes to the shared Incident node and records a HANDED_OFF edge; the next agent reads state
back from the graph with `incident_state`. Any agent, in any later session, can pick up from there.

Small local models sometimes skip tools, so each agent declares the tools it *must* run. If the
model doesn't call one, a guardrail runs it with graph-derived arguments and feeds the result back.
"""
import json
import os
import re
from typing import Callable

from openai import OpenAI

from . import memory, tools
from .db import query

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "local")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "6"))

Emit = Callable[[dict], None]
KEY_ARGS = ("service", "entity", "incident_id", "handle")


def llm() -> OpenAI:
    return OpenAI(base_url=os.getenv("LLM_BASE_URL", LLM_BASE_URL), api_key=os.getenv("LLM_API_KEY", "local"),
                  timeout=120)


def _clean(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _chat(messages, tool_names):
    kw = dict(model=os.getenv("LLM_MODEL", LLM_MODEL), messages=messages, temperature=0.2, max_tokens=900,
              extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    if tool_names:
        kw["tools"] = tools.schemas(tool_names)
        kw["tool_choice"] = "auto"
    return llm().chat.completions.create(**kw).choices[0].message


def run_agent(tenant: str, agent: str, system: str, user: str, tool_names: list[str], emit: Emit,
              required: list[tuple[str, Callable[[], dict]]] = (), history: list[dict] = ()) -> tuple[str, set]:
    """Tool-calling loop. Returns (final_text, entities_touched)."""
    emit({"type": "agent_start", "agent": agent})
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
    called, touched, final, last = [], set(), "", ""

    def execute(name, args, guardrail=False):
        try:
            res = tools.call(tenant, name, args)
            out = res.for_llm()
            emit({"type": "tool", "agent": agent, "guardrail": guardrail, **res.to_dict()})
            touched.update(res.nodes)
        except Exception as e:  # surface tool errors to the model instead of crashing the run
            out = json.dumps({"error": str(e)})
            emit({"type": "tool_error", "agent": agent, "tool": name, "args": args, "error": str(e)})
        called.append((name, args or {}))
        return out

    def satisfied(name, argf):
        # A required check counts only if it ran on the right entity, not just with the right tool name.
        want = {k: v for k, v in argf().items() if k in KEY_ARGS}
        return any(n == name and all(str(a.get(k, "")).lstrip("@") == str(v) for k, v in want.items())
                   for n, a in called)

    llm_ok = True
    for _ in range(MAX_STEPS):
        try:
            msg = _chat(messages, tool_names)
        except Exception as e:
            emit({"type": "llm_error", "agent": agent, "error": str(e)[:300]})
            llm_ok = False
            break
        if not msg.tool_calls:
            final = _clean(msg.content)
            missing = [(n, a) for n, a in required if not satisfied(n, a)]
            if not missing:
                break
            last, final = final, ""
            # Guardrail: run what the agent was required to check, then let it reconsider.
            for name, argf in missing:
                out = execute(name, argf(), guardrail=True)
                messages.append({"role": "user", "content": f"[guardrail ran {name}] result: {out}"})
            messages.append({"role": "user", "content": "Use those results. Call any action tools you still need, "
                                                       "then give your final answer."})
            continue
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": execute(tc.function.name, args)})

    if not llm_ok or not final:
        for name, argf in required:
            if not satisfied(name, argf):
                execute(name, argf(), guardrail=True)
        final = final or last or "(LLM unavailable - ran required graph checks deterministically; see trace.)"
    emit({"type": "agent_end", "agent": agent, "text": final})
    return final, touched


# ------------------------------------------------------------------ incident pipeline

def _state(tenant, iid):
    rows = memory.incident_state(tenant, iid).rows
    return rows[0] if rows else {}


def _top_suspect(tenant, service):
    rows = tools.recent_deploys_upstream(tenant, service).rows
    return rows[0] if rows else None


TRIAGE = """You are the TRIAGE agent for an on-call team. The company's systems live in a graph database.
Assess impact of the alert. Steps: 1) call blast_radius on the alerting service AND recall_memory for it,
2) call critical_services to see if it is structurally critical,
3) call update_incident with severity (SEV1 if any tier-1 customer-facing service is impacted, else SEV2/SEV3)
and a one-line impact. Then reply in <=3 sentences citing the services you found. Never invent service names."""

INVESTIGATOR = """You are the INVESTIGATOR agent. You receive only an incident id; read everything from the graph.
Steps: 1) incident_state, 2) recent_deploys_upstream for the service named in incident_state's symptom_service field
(e.g. if symptom_service is "checkout-service", pass service="checkout-service"); root causes are often several hops
downstream, 3) similar_past_incidents and procedures_for that same service,
4) decision_trace for the suspected service if relevant,
5) update_incident with hypothesis and suspected_deploy (exact deploy id from the results).
Reply in <=4 sentences: root cause hypothesis, evidence path (service -> service), and the known fix procedure."""

COMMS = """You are the COMMS agent. You receive only an incident id; read state from the graph.
Steps: 1) incident_state, 2) who_to_page for the suspected_service (or affected service if none),
3) page the on-call person AND the strongest expert with incident history, giving a short reason each,
4) post_status with a 2-sentence customer-safe update. Reply with who you paged and why."""


def run_incident(tenant: str, service: str, title: str, emit: Emit, alert_id: str | None = None,
                 session_id: str | None = None) -> dict:
    sid = memory.start_session(tenant, "incident", session_id)
    iid = memory.open_incident(tenant, service, title, alert_id, sid)
    emit({"type": "session", "session": sid, "incident": iid, "service": service, "title": title})

    # Triage --------------------------------------------------------------
    def triage_sev():
        rows = tools.blast_radius(tenant, service).rows
        t1 = [r["service"] for r in rows if r["tier"] == 1]
        return {"incident_id": iid, "severity": "SEV1" if t1 or _tier(tenant, service) == 1 else "SEV2",
                "impact": f"{len(rows)} upstream services exposed; tier-1: {', '.join(t1) or 'none'}"}
    text, touched = run_agent(
        tenant, "triage", TRIAGE,
        f"Incident {iid}. Alert: '{title}' on service '{service}'.",
        ["blast_radius", "critical_services", "chokepoints", "recall_memory", "update_incident"], emit,
        required=[("blast_radius", lambda: {"service": service}),
                  ("recall_memory", lambda: {"entity": service}),
                  ("update_incident", triage_sev)])
    if _state(tenant, iid).get("severity") in (None, "TBD"):
        tools.call(tenant, "update_incident", triage_sev())
    memory.write_episode(tenant, sid, "triage", text, touched | {iid, service})
    memory.handoff(tenant, "triage", "investigator", iid, text)
    emit({"type": "handoff", "from": "triage", "to": "investigator", "incident": iid, "note": text})

    # Investigator --------------------------------------------------------
    def inv_update():
        top = _top_suspect(tenant, service)
        if not top:
            return {"incident_id": iid, "hypothesis": "No recent deploys in dependency chain; check infra."}
        return {"incident_id": iid, "suspected_deploy": top["deploy"],
                "hypothesis": f"{top['deploy']} ({top['summary']}) {top['minutes_ago']}m ago, "
                              f"path {' -> '.join(top['via'])}"}
    text, touched = run_agent(
        tenant, "investigator", INVESTIGATOR, f"Investigate incident {iid}.",
        ["incident_state", "recent_deploys_upstream", "dependencies", "similar_past_incidents", "procedures_for",
         "decision_trace", "runbook", "path_between", "update_incident"], emit,
        required=[("incident_state", lambda: {"incident_id": iid}),
                  ("recent_deploys_upstream", lambda: {"service": service}),
                  ("similar_past_incidents", lambda: {"service": service}),
                  ("procedures_for", lambda: {"service": service}),
                  ("update_incident", inv_update)])
    if not _state(tenant, iid).get("suspected_deploy"):
        upd = inv_update()
        if upd.get("suspected_deploy"):
            tools.call(tenant, "update_incident", upd)
    memory.write_episode(tenant, sid, "investigator", text, touched | {iid})
    memory.handoff(tenant, "investigator", "comms", iid, text)
    emit({"type": "handoff", "from": "investigator", "to": "comms", "incident": iid, "note": text})

    # Comms ---------------------------------------------------------------
    def suspect_service():
        return _state(tenant, iid).get("suspected_service") or service

    def default_page():
        rows = tools.who_to_page(tenant, suspect_service()).rows
        oc = next((r for r in rows if r["reason"] == "on_call" and r.get("handle")), None)
        return {"incident_id": iid, "handle": oc["handle"] if oc else "grace",
                "reason": f"on-call for {suspect_service()}"}

    def expert_page():
        rows = tools.who_to_page(tenant, suspect_service()).rows
        oc = {r["handle"] for r in rows if r["reason"] == "on_call"}
        ex = next((r for r in rows if r["reason"] == "expert" and "RESOLVED_BY" in r["evidence"]
                   and r["handle"] not in oc), None)
        if not ex:
            return {"incident_id": iid, "handle": next(iter(oc), "grace"), "reason": "on-call"}
        return {"incident_id": iid, "handle": ex["handle"],
                "reason": f"resolved {', '.join(v for v in ex['via'] if v.startswith('INC'))} on {suspect_service()}"}

    def default_status():
        st = _state(tenant, iid)
        return {"incident_id": iid, "message": f"We are investigating elevated errors affecting {service}. "
                                               f"Likely cause identified ({st.get('suspected_deploy') or 'under review'}); "
                                               f"mitigation in progress."}
    text, touched = run_agent(
        tenant, "comms", COMMS, f"Handle comms and paging for incident {iid}.",
        ["incident_state", "who_to_page", "who_knows", "page", "post_status"], emit,
        required=[("incident_state", lambda: {"incident_id": iid}),
                  ("who_to_page", lambda: {"service": suspect_service()}),
                  ("page", default_page),
                  ("page", expert_page),
                  ("post_status", default_status)])
    memory.write_episode(tenant, sid, "comms", text, touched | {iid})
    st = _state(tenant, iid)
    emit({"type": "done", "incident": iid, "session": sid, "state": st})
    return {"incident": iid, "session": sid, "state": st}


def _tier(tenant, service):
    rows = query(tenant, "MATCH (s:Service {name: $s}) RETURN s.tier AS t", {"s": service}, read_only=True)
    return rows[0]["t"] if rows else 3


# ------------------------------------------------------------------ free-form Q&A with memory

ASK = """You are Blastwave, an engineering-org copilot. All knowledge lives in a graph database; ALWAYS use
tools to answer and never invent names. Cite evidence (ids of incidents, tickets, docs, deploys) in your answer.
If the user tells you something worth remembering about a service or person, call remember.
Service names look like 'checkout-service', 'payments-db', 'ledger-service'. People are referred to by handle."""


def link_entities(tenant: str, text: str) -> list[dict]:
    """Resolve mentions in free text to real graph nodes (services by full name or stem, people, incident/ticket ids)."""
    low = text.lower()
    words = set(re.findall(r"[a-z0-9@-]+", low))
    rows = query(tenant, """
        MATCH (n) WHERE n:Service OR n:Person OR n:Incident OR n:Ticket OR n:Decision OR n:Deploy
        RETURN labels(n)[0] AS label, coalesce(n.name, n.id) AS name, n.handle AS handle""", read_only=True)
    found = []
    for r in rows:
        name, handle = (r["name"] or "").lower(), (r["handle"] or "").lower()
        stem = name.split("-")[0] if r["label"] == "Service" else None
        if (name and name in low) or (handle and (handle in words or "@" + handle in words)) \
                or (stem and len(stem) > 3 and stem in words and not any(f["name"].startswith(stem) for f in found)):
            found.append({"label": r["label"], "name": r["handle"] or r["name"]})
    return found[:6]


def _intent_checks(question: str, ents: list[dict]):
    """When the model answers without evidence, pick the graph checks that fit the question."""
    q = question.lower()
    svcs = [e["name"] for e in ents if e["label"] == "Service"]
    people = [e["name"] for e in ents if e["label"] == "Person"]
    checks = []
    if "single point" in q or "chokepoint" in q or "critical" in q:
        checks.append(("chokepoints", lambda: {"top_k": 6}))
    if len(svcs) >= 2 and "path" in q:
        checks.append(("path_between", lambda: {"source": svcs[0], "target": svcs[1]}))
    for svc in svcs[:2]:
        if any(w in q for w in ("why", "decision", "source", "decid")):
            checks.append(("decision_trace", lambda svc=svc: {"service": svc}))
        if any(w in q for w in ("break", "down", "impact", "blast", "depend")):
            checks.append(("blast_radius", lambda svc=svc: {"service": svc}))
        if any(w in q for w in ("who", "call", "page", "know", "own", "expert")):
            checks.append(("who_to_page", lambda svc=svc: {"service": svc}))
        if any(w in q for w in ("before", "incident", "history", "last time", "happened")):
            checks.append(("similar_past_incidents", lambda svc=svc: {"service": svc}))
        if any(w in q for w in ("fix", "procedure", "runbook", "how do")):
            checks.append(("procedures_for", lambda svc=svc: {"service": svc}))
    for p in people[:1]:
        checks.append(("recall_memory", lambda p=p: {"entity": p}))
    if not checks and svcs:
        checks.append(("recall_memory", lambda: {"entity": svcs[0]}))
    return checks


def ask(tenant: str, question: str, emit: Emit, session_id: str | None = None) -> dict:
    sid = memory.start_session(tenant, "chat", session_id)
    history = [{"role": h["role"], "content": h["text"]} for h in memory.session_history(tenant, sid)]
    ents = link_entities(tenant, question)
    emit({"type": "session", "session": sid, "history_turns": len(history), "entities": ents})
    names = [n for n in tools.READ_TOOLS if n not in ("incident_state",)] + ["remember"]
    prompt = question
    if ents:
        prompt += "\n\n[graph entity linker] mentions resolved to: " + \
                  ", ".join(f"{e['name']} ({e['label']})" for e in ents) + ". Call tools on these exact names."
    text, touched = run_agent(tenant, "assistant", ASK, prompt, names, emit, history=history,
                              required=_intent_checks(question, ents))
    memory.write_episode(tenant, sid, "assistant", question, touched | {e["name"] for e in ents}, kind="user")
    memory.write_episode(tenant, sid, "assistant", text, touched, kind="assistant")
    emit({"type": "done", "session": sid})
    return {"session": sid, "answer": text}
