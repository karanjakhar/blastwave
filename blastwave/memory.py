"""Agent memory + shared state, all stored in the tenant graph.

- Episodic:   (Session)-[:HAS_EPISODE]->(Episode)-[:MENTIONS]->(entity), chained with [:NEXT]
- Semantic:   (Fact)-[:ABOUT]->(entity)            (see tools.remember)
- Procedural: (Procedure)-[:FIXES]->(Service), [:LEARNED_FROM]->(Incident)
- Shared state between agents: the Incident node itself + (Agent)-[:HANDED_OFF]->(Agent) edges.
  Agents receive only an incident id; everything else they read back from the graph.
"""
import time

from .db import query, now
from .tools import tool, ToolResult, _run, S, new_id

ENTITY_LABELS = ("Service", "Person", "Incident", "Deploy", "Procedure", "Decision")


def start_session(tenant: str, kind: str, session_id: str | None = None) -> str:
    sid = session_id or new_id("SES")
    query(tenant, "MERGE (s:Session {id: $id}) ON CREATE SET s.kind = $kind, s.ts = $ts",
          {"id": sid, "kind": kind, "ts": now()})
    return sid


def write_episode(tenant: str, session_id: str, agent: str, text: str, mentions: list[str], kind: str = "note"):
    eid = new_id("EP")
    query(tenant, """
        MATCH (s:Session {id: $sid})
        OPTIONAL MATCH (s)-[:HAS_EPISODE]->(prev:Episode) WHERE NOT (prev)-[:NEXT]->()
        CREATE (e:Episode {id: $eid, text: $text, ts: $ts, seq: $seq, kind: $kind, agent: $agent})
        CREATE (s)-[:HAS_EPISODE]->(e)
        FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END | CREATE (prev)-[:NEXT]->(e))
        WITH e
        OPTIONAL MATCH (a:Agent {name: $agent})
        FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | CREATE (a)-[:WROTE]->(e))
        WITH e
        UNWIND $mentions AS m
        MATCH (x) WHERE (x:Service OR x:Person OR x:Incident OR x:Deploy OR x:Procedure OR x:Decision)
                    AND (x.name = m OR x.handle = m OR x.id = m)
        MERGE (e)-[:MENTIONS]->(x)""",
          {"sid": session_id, "eid": eid, "text": text[:2000], "ts": now(), "kind": kind,
           "agent": agent, "mentions": list(mentions)[:25], "seq": time.time_ns()})
    return eid


def session_history(tenant: str, session_id: str, limit: int = 10) -> list[dict]:
    rows = query(tenant, """
        MATCH (s:Session {id: $sid})-[:HAS_EPISODE]->(e:Episode) WHERE e.kind IN ['user', 'assistant']
        RETURN e.kind AS role, e.text AS text ORDER BY e.seq DESC LIMIT $n""",
                 {"sid": session_id, "n": limit}, read_only=True)
    return list(reversed(rows))


def handoff(tenant: str, src: str, dst: str, incident_id: str, note: str):
    query(tenant, """
        MATCH (a:Agent {name: $src}), (b:Agent {name: $dst})
        CREATE (a)-[:HANDED_OFF {incident: $iid, note: $note, ts: $ts}]->(b)""",
          {"src": src, "dst": dst, "iid": incident_id, "note": note[:500], "ts": now()})


def open_incident(tenant: str, service: str, title: str, alert_id: str | None, session_id: str) -> str:
    iid = new_id("INC")
    query(tenant, """
        MATCH (s:Service {name: $svc}), (ses:Session {id: $sid})
        CREATE (i:Incident {id: $iid, title: $title, status: 'open', severity: 'TBD', ts: $ts})
        CREATE (i)-[:AFFECTED]->(s), (ses)-[:ABOUT]->(i)
        WITH i
        OPTIONAL MATCH (al:Alert {id: $alert})
        FOREACH (_ IN CASE WHEN al IS NULL THEN [] ELSE [1] END | CREATE (al)-[:TRIGGERED]->(i))""",
          {"svc": service, "sid": session_id, "iid": iid, "title": title, "ts": now(), "alert": alert_id or ""})
    return iid


# ------------------------------------------------------------- shared-state tools agents call

@tool("Read the shared incident state from the graph: alert, symptom service, severity, hypothesis, suspected deploy, "
      "pages sent, and handoff notes from previous agents.", {"incident_id": S})
def incident_state(tenant: str, incident_id: str) -> ToolResult:
    r = ToolResult("incident_state", {"incident_id": incident_id}, [])
    r.rows = _run(tenant, r, """
        MATCH (i:Incident {id: $iid})-[:AFFECTED]->(s:Service)
        OPTIONAL MATCH (al:Alert)-[:TRIGGERED]->(i)
        OPTIONAL MATCH (i)-[:SUSPECTED_CAUSE]->(d:Deploy)-[:OF]->(ds:Service)
        OPTIONAL MATCH (i)-[:PAGED]->(p:Person)
        OPTIONAL MATCH ()-[h:HANDED_OFF {incident: $iid}]->()
        RETURN i.id AS incident, i.title AS title, i.status AS status, i.severity AS severity,
               i.impact AS impact, i.hypothesis AS hypothesis, collect(DISTINCT s.name)[0] AS symptom_service,
               collect(DISTINCT s.name) AS affected,
               al.title AS alert, d.id AS suspected_deploy, ds.name AS suspected_service,
               collect(DISTINCT p.handle) AS paged, collect(DISTINCT h.note) AS handoff_notes""",
                  {"iid": incident_id})
    for x in r.rows:
        r.nodes |= {incident_id, *x["affected"]} | ({x["suspected_deploy"]} if x["suspected_deploy"] else set())
    return r


@tool("Update the shared incident record. severity is SEV1/SEV2/SEV3. suspected_deploy is a deploy id like "
      "'svc@v1.2.3'. Only pass fields you want to change.",
      {"incident_id": S, "severity": S, "impact": S, "hypothesis": S, "suspected_deploy": S}, ["incident_id"], writes=True)
def update_incident(tenant: str, incident_id: str, severity: str = None, impact: str = None,
                    hypothesis: str = None, suspected_deploy: str = None) -> ToolResult:
    r = ToolResult("update_incident", {k: v for k, v in dict(incident_id=incident_id, severity=severity, impact=impact,
                                       hypothesis=hypothesis, suspected_deploy=suspected_deploy).items() if v}, [])
    sets = {k: v for k, v in dict(severity=severity, impact=impact, hypothesis=hypothesis).items() if v}
    if sets:
        _run(tenant, r, "MATCH (i:Incident {id: $iid}) SET i += $props", {"iid": incident_id, "props": sets}, ro=False)
    if suspected_deploy:
        _run(tenant, r, """
            MATCH (i:Incident {id: $iid}), (d:Deploy {id: $dep})
            OPTIONAL MATCH (i)-[old:SUSPECTED_CAUSE]->() DELETE old
            WITH i, d MERGE (i)-[:SUSPECTED_CAUSE]->(d)""", {"iid": incident_id, "dep": suspected_deploy}, ro=False)
        r.nodes = {incident_id, suspected_deploy}
        r.edges = [[incident_id, "SUSPECTED_CAUSE", suspected_deploy]]
    r.rows = [{"ok": True, **r.args}]
    return r


@tool("Page a person for an incident (action). Records a PAGED edge in the graph.",
      {"incident_id": S, "handle": S, "reason": S}, writes=True)
def page(tenant: str, incident_id: str, handle: str, reason: str) -> ToolResult:
    r = ToolResult("page", {"incident_id": incident_id, "handle": handle, "reason": reason}, [])
    r.rows = _run(tenant, r, """
        MATCH (i:Incident {id: $iid}), (p:Person {handle: $h})
        MERGE (i)-[e:PAGED]->(p) ON CREATE SET e.reason = $reason, e.ts = $ts
        RETURN p.name AS paged, p.handle AS handle""",
                  {"iid": incident_id, "h": handle.lstrip("@"), "reason": reason, "ts": now()}, ro=False)
    r.nodes = {incident_id, handle.lstrip("@")}
    r.edges = [[incident_id, "PAGED", handle.lstrip("@")]]
    return r


@tool("Post a status update for the incident (action).", {"incident_id": S, "message": S}, writes=True)
def post_status(tenant: str, incident_id: str, message: str) -> ToolResult:
    r = ToolResult("post_status", {"incident_id": incident_id, "message": message}, [])
    r.rows = _run(tenant, r, """
        MATCH (i:Incident {id: $iid})
        CREATE (u:StatusUpdate {text: $msg, ts: $ts})-[:FOR]->(i) RETURN u.text AS posted""",
                  {"iid": incident_id, "msg": message, "ts": now()}, ro=False)
    return r


def resolve_incident(tenant: str, incident_id: str, resolver: str, notes: str, session_id: str) -> dict:
    """Close the incident and turn it into procedural memory for future sessions."""
    st = incident_state(tenant, incident_id).rows
    if not st:
        raise ValueError("unknown incident")
    st = st[0]
    query(tenant, """
        MATCH (i:Incident {id: $iid}) SET i.status = 'resolved', i.resolved_ts = $ts, i.summary = $notes
        WITH i OPTIONAL MATCH (i)-[:SUSPECTED_CAUSE]->(d:Deploy)
        FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (i)-[:CAUSED_BY]->(d))
        WITH i MATCH (p:Person {handle: $who}) MERGE (i)-[:RESOLVED_BY]->(p)""",
          {"iid": incident_id, "ts": now(), "notes": notes, "who": resolver})
    target = st["suspected_service"] or st["affected"][0]
    existing = query(tenant, """
        MATCH (p:Procedure)-[:FIXES]->(:Service {name: $svc}) RETURN p.id AS id ORDER BY p.uses DESC LIMIT 1""",
                     {"svc": target})
    steps = [s.strip(" -•") for s in notes.replace(";", "\n").split("\n") if s.strip(" -•")]
    if existing:
        pid = existing[0]["id"]
        query(tenant, """
            MATCH (p:Procedure {id: $pid}), (i:Incident {id: $iid})
            SET p.uses = p.uses + 1, p.last_used = $ts MERGE (p)-[:LEARNED_FROM]->(i)""",
              {"pid": pid, "iid": incident_id, "ts": now()})
        learned = f"Reinforced {pid} (uses+1) for {target}"
    else:
        pid = new_id("PROC")
        query(tenant, """
            MATCH (s:Service {name: $svc}), (i:Incident {id: $iid})
            CREATE (p:Procedure {id: $pid, title: $title, steps: $steps, uses: 1, learned_by: 'agent', ts: $ts})
            CREATE (p)-[:FIXES]->(s), (p)-[:LEARNED_FROM]->(i)""",
              {"svc": target, "iid": incident_id, "pid": pid, "title": st["title"], "steps": steps, "ts": now()})
        learned = f"Learned new procedure {pid} for {target}"
    write_episode(tenant, session_id, "comms", f"Resolved {incident_id} by @{resolver}: {notes}. {learned}.",
                  [incident_id, target, resolver, pid], kind="resolution")
    return {"incident": incident_id, "procedure": pid, "learned": learned, "target": target}
