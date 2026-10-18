"""Graph tools the agents call. Every tool runs parameterized Cypher against the tenant graph and
returns the rows *plus* the exact Cypher, params and the subgraph it touched, so every agent
decision can be traced back to graph evidence.
"""
import difflib
import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .db import query, now


@dataclass
class ToolResult:
    tool: str
    args: dict
    rows: list
    cypher: list[str] = field(default_factory=list)
    nodes: set = field(default_factory=set)      # names of nodes to highlight
    edges: list = field(default_factory=list)    # [src, type, dst] to highlight

    def for_llm(self, limit: int = 12) -> str:
        rows = self.rows[:limit]
        extra = f" (+{len(self.rows) - limit} more)" if len(self.rows) > limit else ""
        return json.dumps(rows, default=str)[:3500] + extra

    def to_dict(self):
        return {"tool": self.tool, "args": self.args, "rows": self.rows, "cypher": self.cypher,
                "nodes": sorted(self.nodes), "edges": self.edges}


TOOLS: dict[str, dict] = {}


def tool(description: str, params: dict, required: list[str] | None = None, writes: bool = False):
    def deco(fn: Callable):
        TOOLS[fn.__name__] = {
            "fn": fn, "writes": writes,
            "schema": {"type": "function", "function": {
                "name": fn.__name__, "description": description,
                "parameters": {"type": "object", "properties": params,
                               "required": required if required is not None else list(params)}}},
        }
        return fn
    return deco


S = {"type": "string"}
I = {"type": "integer"}


def _run(tenant, res: ToolResult, cypher: str, params: dict, ro=True):
    res.cypher.append(cypher.strip() + "\n// params: " + json.dumps(params, default=str))
    return query(tenant, cypher, params, read_only=ro)


# ---------------------------------------------------------------- Track 1: act on connected data

@tool("Services that transitively depend on a service (what breaks if it goes down). Multi-hop upstream traversal.",
      {"service": S, "max_hops": I}, ["service"])
def blast_radius(tenant: str, service: str, max_hops: int = 6) -> ToolResult:
    r = ToolResult("blast_radius", {"service": service, "max_hops": max_hops}, [])
    hops = max(1, min(int(max_hops or 6), 8))
    rows = _run(tenant, r, f"""
        MATCH p = (up:Service)-[:DEPENDS_ON*1..{hops}]->(s:Service {{name: $service}})
        WITH up, min(length(p)) AS hops, collect(p)[0] AS path
        OPTIONAL MATCH (t:Team)-[:OWNS]->(up)
        RETURN up.name AS service, up.tier AS tier, t.name AS team, hops,
               [n IN nodes(path) | n.name] AS via
        ORDER BY hops, tier""", {"service": service})
    r.rows = rows
    r.nodes = {service} | {x["service"] for x in rows}
    for x in rows:
        via = x["via"]
        r.edges += [[via[i], "DEPENDS_ON", via[i + 1]] for i in range(len(via) - 1)]
    return r


@tool("Downstream dependency chain of a service: everything it calls, directly or indirectly.",
      {"service": S}, ["service"])
def dependencies(tenant: str, service: str) -> ToolResult:
    r = ToolResult("dependencies", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH p = (s:Service {name: $service})-[:DEPENDS_ON*1..5]->(d:Service)
        WITH d, min(length(p)) AS hops, collect(p)[0] AS path
        RETURN d.name AS service, d.kind AS kind, hops, [n IN nodes(path) | n.name] AS via
        ORDER BY hops""", {"service": service})
    r.rows = rows
    r.nodes = {service} | {x["service"] for x in rows}
    for x in rows:
        via = x["via"]
        r.edges += [[via[i], "DEPENDS_ON", via[i + 1]] for i in range(len(via) - 1)]
    return r


@tool("Recent deploys to a service OR anything it depends on (likely root causes of a symptom). window_minutes defaults to 240.",
      {"service": S, "window_minutes": I}, ["service"])
def recent_deploys_upstream(tenant: str, service: str, window_minutes: int = 240) -> ToolResult:
    r = ToolResult("recent_deploys_upstream", {"service": service, "window_minutes": window_minutes}, [])
    rows = _run(tenant, r, """
        MATCH p = (s:Service {name: $service})-[:DEPENDS_ON*0..5]->(d:Service)<-[:OF]-(dep:Deploy)<-[:AUTHORED]-(a:Person)
        WHERE dep.ts >= $since
        WITH dep, d, a, min(length(p)) - 2 AS hops, collect(p)[0] AS path
        RETURN dep.id AS deploy, d.name AS service, dep.summary AS summary, dep.risky AS risky,
               ($now - dep.ts) / 60 AS minutes_ago, a.handle AS author, hops,
               [n IN nodes(path) WHERE n:Service | n.name] AS via
        ORDER BY dep.risky DESC, minutes_ago ASC""",
                {"service": service, "since": now() - int(window_minutes or 240) * 60, "now": now()})
    r.rows = rows
    for x in rows:
        via = x["via"]
        r.nodes |= set(via) | {x["deploy"]}
        r.edges += [[via[i], "DEPENDS_ON", via[i + 1]] for i in range(len(via) - 1)]
        r.edges.append([x["deploy"], "OF", x["service"]])
    return r


@tool("Shortest dependency path between two services.", {"source": S, "target": S})
def path_between(tenant: str, source: str, target: str) -> ToolResult:
    r = ToolResult("path_between", {"source": source, "target": target}, [])
    rows = _run(tenant, r, """
        MATCH (a:Service {name: $source}), (b:Service {name: $target})
        WITH shortestPath((a)-[:DEPENDS_ON*1..8]->(b)) AS p WHERE p IS NOT NULL
        RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops""", {"source": source, "target": target})
    r.rows = rows
    if rows:
        path = rows[0]["path"]
        r.nodes = set(path)
        r.edges = [[path[i], "DEPENDS_ON", path[i + 1]] for i in range(len(path) - 1)]
    return r


@tool("Rank services by structural criticality using PageRank over DEPENDS_ON (high = many things rely on it).",
      {"top_k": I}, [])
def critical_services(tenant: str, top_k: int = 8) -> ToolResult:
    r = ToolResult("critical_services", {"top_k": top_k}, [])
    rows = _run(tenant, r, """
        CALL algo.pageRank('Service', 'DEPENDS_ON') YIELD node, score
        RETURN node.name AS service, node.tier AS tier, round(score * 1000) / 1000.0 AS pagerank
        ORDER BY pagerank DESC LIMIT $k""", {"k": int(top_k or 8)})
    r.rows = rows
    r.nodes = {x["service"] for x in rows}
    return r


@tool("Find past incidents that affected this service or anything within 2 hops of it, with their root cause and resolver.",
      {"service": S}, ["service"])
def similar_past_incidents(tenant: str, service: str) -> ToolResult:
    r = ToolResult("similar_past_incidents", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH (s:Service {name: $service})-[:DEPENDS_ON*0..4]->(n:Service)<-[:AFFECTED]-(i:Incident)
        WHERE i.status = 'resolved'
        OPTIONAL MATCH (i)-[:CAUSED_BY]->(d:Deploy)-[:OF]->(ds:Service)
        OPTIONAL MATCH (i)-[:RESOLVED_BY]->(p:Person)
        OPTIONAL MATCH (doc:Doc)-[:DOCUMENTS]->(i)
        RETURN i.id AS incident, i.title AS title, i.severity AS severity, i.summary AS summary,
               ds.name AS root_cause_service, d.version AS root_cause_version, p.handle AS resolved_by,
               doc.id AS postmortem, count(DISTINCT n) AS overlap
        ORDER BY overlap DESC""", {"service": service})
    r.rows = rows
    for x in rows:
        r.nodes |= {x["incident"]} | ({x["root_cause_service"]} if x["root_cause_service"] else set())
        if x["root_cause_service"]:
            r.edges.append([x["incident"], "AFFECTED", x["root_cause_service"]])
    return r


@tool("Procedural memory: known fix procedures for a service or its dependencies, learned from past incidents.",
      {"service": S}, ["service"])
def procedures_for(tenant: str, service: str) -> ToolResult:
    r = ToolResult("procedures_for", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH (s:Service {name: $service})-[:DEPENDS_ON*0..3]->(t:Service)<-[:FIXES]-(p:Procedure)
        OPTIONAL MATCH (p)-[:LEARNED_FROM]->(i:Incident)
        RETURN DISTINCT p.id AS procedure, p.title AS title, t.name AS fixes, p.steps AS steps,
               p.uses AS uses, p.learned_by AS learned_by, i.id AS learned_from
        ORDER BY uses DESC""", {"service": service})
    r.rows = rows
    for x in rows:
        r.nodes |= {x["procedure"], x["fixes"]}
        r.edges.append([x["procedure"], "FIXES", x["fixes"]])
    return r


# ---------------------------------------------------------------- Track 3: company brain

@tool("Who to page for a service: current on-call, owning team + manager, and people with hands-on history "
      "(tickets worked, incidents resolved, deploys authored).", {"service": S}, ["service"])
def who_to_page(tenant: str, service: str) -> ToolResult:
    r = ToolResult("who_to_page", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH (s:Service {name: $service})
        OPTIONAL MATCH (oc:Person)-[:ON_CALL]->(s)
        OPTIONAL MATCH (t:Team)-[:OWNS]->(s)
        OPTIONAL MATCH (m:Person)-[:MEMBER_OF]->(t) WHERE m.role CONTAINS 'Manager'
        RETURN 'on_call' AS reason, oc.handle AS handle, oc.name AS name, t.name AS team, m.handle AS manager""",
                {"service": service})
    experts = _run(tenant, r, """
        MATCH (s:Service {name: $service})
        MATCH (p:Person)-[r:WORKED_ON|RESOLVED_BY|AUTHORED]-(x)-[:TOUCHES|AFFECTED|OF]->(s)
        RETURN p.handle AS handle, p.name AS name, collect(DISTINCT type(r)) AS evidence,
               collect(DISTINCT coalesce(x.id, x.title)) AS via, count(*) AS score
        ORDER BY score DESC LIMIT 5""", {"service": service})
    r.rows = rows + [{"reason": "expert", **e} for e in experts]
    r.nodes = {service} | {x["handle"] for x in r.rows if x.get("handle")}
    for x in r.rows:
        if x.get("handle"):
            r.edges.append([x["handle"], "ON_CALL" if x["reason"] == "on_call" else "KNOWS", service])
    return r


@tool("Who knows about a system: people ranked by how many tickets/incidents/deploys connect them to it.",
      {"service": S}, ["service"])
def who_knows(tenant: str, service: str) -> ToolResult:
    r = ToolResult("who_knows", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH (p:Person)-[r]-(x)-[:TOUCHES|AFFECTED|OF]->(s:Service {name: $service})
        WHERE type(r) IN ['WORKED_ON', 'RESOLVED_BY', 'AUTHORED']
        RETURN p.name AS person, p.handle AS handle, count(*) AS touchpoints,
               collect(DISTINCT coalesce(x.id, x.title)) AS evidence
        ORDER BY touchpoints DESC LIMIT 5""", {"service": service})
    r.rows = rows
    r.nodes = {service} | {x["handle"] for x in rows}
    return r


@tool("Trace decisions about a service back to their source docs and tickets, and who made them.", {"service": S}, ["service"])
def decision_trace(tenant: str, service: str) -> ToolResult:
    r = ToolResult("decision_trace", {"service": service}, [])
    rows = _run(tenant, r, """
        MATCH (d:Decision)-[:ABOUT]->(s:Service {name: $service})
        OPTIONAL MATCH (o:Person)-[:DECIDED]->(d)
        OPTIONAL MATCH (d)-[:SOURCED_FROM]->(src)
        RETURN d.id AS decision, d.title AS title, o.handle AS decided_by,
               collect(DISTINCT {id: src.id, title: src.title}) AS sources""", {"service": service})
    r.rows = rows
    for x in rows:
        r.nodes |= {x["decision"], service}
        r.edges.append([x["decision"], "ABOUT", service])
        for src in x["sources"]:
            if src.get("id"):
                r.nodes.add(src["id"])
                r.edges.append([x["decision"], "SOURCED_FROM", src["id"]])
    return r


@tool("Runbooks for a service.", {"service": S}, ["service"])
def runbook(tenant: str, service: str) -> ToolResult:
    r = ToolResult("runbook", {"service": service}, [])
    r.rows = _run(tenant, r, """
        MATCH (rb:Runbook)-[:FOR]->(s:Service {name: $service}) RETURN rb.id AS id, rb.title AS title, rb.body AS body""",
                  {"service": service})
    r.nodes = {service} | {x["id"] for x in r.rows}
    return r


@tool("Community detection (label propagation) over the service graph: which services cluster together.", {}, [])
def service_clusters(tenant: str) -> ToolResult:
    r = ToolResult("service_clusters", {}, [])
    r.rows = _run(tenant, r, """
        CALL algo.labelPropagation({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) YIELD node, communityId
        RETURN communityId AS cluster, collect(node.name) AS services, count(*) AS size ORDER BY size DESC""", {})
    return r


@tool("Single points of failure: services with highest betweenness centrality on the dependency graph.", {"top_k": I}, [])
def chokepoints(tenant: str, top_k: int = 6) -> ToolResult:
    r = ToolResult("chokepoints", {"top_k": top_k}, [])
    r.rows = _run(tenant, r, """
        CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['DEPENDS_ON']}) YIELD node, score
        RETURN node.name AS service, round(score * 100) / 100.0 AS betweenness ORDER BY betweenness DESC LIMIT $k""",
                  {"k": int(top_k or 6)})
    r.nodes = {x["service"] for x in r.rows}
    return r


# ---------------------------------------------------------------- Track 2: memory

@tool("Recall memory about an entity (service, person or incident id): prior agent episodes from any session, facts and procedures.",
      {"entity": S}, ["entity"])
def recall_memory(tenant: str, entity: str) -> ToolResult:
    r = ToolResult("recall_memory", {"entity": entity}, [])
    rows = _run(tenant, r, """
        MATCH (x) WHERE x.name = $e OR x.handle = $e OR x.id = $e
        MATCH (ep:Episode)-[:MENTIONS]->(x)
        OPTIONAL MATCH (ses:Session)-[:HAS_EPISODE]->(ep)
        OPTIONAL MATCH (a:Agent)-[:WROTE]->(ep)
        RETURN 'episode' AS kind, ep.text AS text, a.name AS agent, ses.id AS session, ep.ts AS ts
        ORDER BY ep.seq DESC LIMIT 8""", {"e": entity})
    facts = _run(tenant, r, """
        MATCH (f:Fact)-[:ABOUT]->(x) WHERE x.name = $e OR x.handle = $e OR x.id = $e
        RETURN 'fact' AS kind, f.text AS text, f.source AS agent, f.key AS session, f.ts AS ts
        ORDER BY ts DESC LIMIT 8""", {"e": entity})
    r.rows = rows + facts
    r.nodes = {entity}
    return r


@tool("Store a durable fact about an entity so future sessions and other agents can recall it.",
      {"entity": S, "fact": S}, writes=True)
def remember(tenant: str, entity: str, fact: str, source: str = "agent") -> ToolResult:
    r = ToolResult("remember", {"entity": entity, "fact": fact}, [])
    key = f"{entity}:{abs(hash(fact)) % 10**8}"
    r.rows = _run(tenant, r, """
        MATCH (x) WHERE x.name = $e OR x.handle = $e OR x.id = $e
        WITH x LIMIT 1
        MERGE (f:Fact {key: $key}) ON CREATE SET f.text = $fact, f.ts = $ts, f.source = $source
        MERGE (f)-[:ABOUT]->(x)
        RETURN f.key AS key""", {"e": entity, "fact": fact, "key": key, "ts": now(), "source": source}, ro=False)
    return r


READ_TOOLS = [n for n, t in TOOLS.items() if not t["writes"]]


def schemas(names: list[str]) -> list[dict]:
    return [TOOLS[n]["schema"] for n in names]


def call(tenant: str, name: str, args: dict) -> ToolResult:
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name}")
    fn = TOOLS[name]["fn"]
    allowed = set(inspect.signature(fn).parameters) - {"tenant"}
    args = {k: v for k, v in (args or {}).items() if k in allowed}
    for key in ("service", "source", "target"):
        if key in args:
            args[key] = _check_service(tenant, str(args[key]))
    return fn(tenant, **args)


def _check_service(tenant: str, name: str) -> str:
    """Ground LLM-supplied names in the graph: unknown names fail with the closest real ones so the model can retry."""
    names = [r["n"] for r in query(tenant, "MATCH (s:Service) RETURN s.name AS n", read_only=True)]
    if name in names:
        return name
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
    raise ValueError(f"unknown service '{name}'. Closest services in the graph: {close or names[:8]}")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
