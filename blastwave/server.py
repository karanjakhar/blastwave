"""HTTP API + UI. Agent runs stream every step (tool, Cypher, rows, handoffs) over Server-Sent Events."""
import asyncio
import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agents, memory, tools
from .db import query, tenants

app = FastAPI(title="Blastwave")
WEB = Path(__file__).resolve().parent.parent / "web"


def _stream(fn, *args, **kwargs):
    """Run a blocking agent function in a thread and stream its emitted events as SSE."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(ev):
        loop.call_soon_threadsafe(q.put_nowait, ev)

    def work():
        try:
            fn(*args, emit=emit, **kwargs)
        except Exception as e:
            emit({"type": "error", "error": str(e)})
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()

    async def gen():
        while (ev := await q.get()) is not None:
            yield f"data: {json.dumps(ev, default=str)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


class IncidentReq(BaseModel):
    tenant: str = "acme"
    service: str
    title: str
    alert_id: str | None = None


class AskReq(BaseModel):
    tenant: str = "acme"
    question: str
    session_id: str | None = None


class ResolveReq(BaseModel):
    tenant: str = "acme"
    incident_id: str
    resolver: str
    notes: str


class ToolReq(BaseModel):
    tenant: str = "acme"
    args: dict = {}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/tenants")
def api_tenants():
    return tenants()


@app.get("/api/graph")
def api_graph(tenant: str = "acme", memory_nodes: bool = True):
    """Whole tenant graph for visualisation. Nodes keyed by their natural id (name/handle/id)."""
    skip = "" if memory_nodes else "AND NOT (n:Episode OR n:Session OR n:Fact OR n:StatusUpdate)"
    nodes = query(tenant, f"""
        MATCH (n) WHERE true {skip}
        RETURN coalesce(n.handle, n.name, n.id, n.key, toString(id(n))) AS key, labels(n)[0] AS label,
               coalesce(n.title, n.name, n.text, n.id) AS title, n.tier AS tier, n.status AS status""", read_only=True)
    edges = query(tenant, f"""
        MATCH (a)-[r]->(b) WHERE true {skip.replace('n:', 'a:')} {skip.replace('n:', 'b:')}
        RETURN coalesce(a.handle, a.name, a.id, a.key, toString(id(a))) AS src, type(r) AS type,
               coalesce(b.handle, b.name, b.id, b.key, toString(id(b))) AS dst""", read_only=True)
    return {"nodes": nodes, "edges": edges}


@app.get("/api/alerts")
def api_alerts(tenant: str = "acme"):
    return query(tenant, """MATCH (a:Alert)-[:FIRED_ON]->(s:Service)
                            RETURN a.id AS id, a.title AS title, s.name AS service, a.status AS status""", read_only=True)


@app.get("/api/services")
def api_services(tenant: str = "acme"):
    return [r["name"] for r in query(tenant, "MATCH (s:Service) RETURN s.name AS name ORDER BY name", read_only=True)]


@app.get("/api/incidents")
def api_incidents(tenant: str = "acme"):
    return query(tenant, """
        MATCH (i:Incident) OPTIONAL MATCH (i)-[:SUSPECTED_CAUSE|CAUSED_BY]->(d:Deploy)
        RETURN i.id AS id, i.title AS title, i.status AS status, i.severity AS severity, i.ts AS ts,
               collect(DISTINCT d.id) AS cause ORDER BY ts DESC""", read_only=True)


@app.get("/api/memory")
def api_memory(tenant: str = "acme"):
    return {
        "sessions": query(tenant, """
            MATCH (s:Session) OPTIONAL MATCH (s)-[:HAS_EPISODE]->(e:Episode)
            RETURN s.id AS id, s.kind AS kind, s.ts AS ts, count(e) AS episodes ORDER BY ts DESC LIMIT 20""",
                          read_only=True),
        "procedures": query(tenant, """
            MATCH (p:Procedure)-[:FIXES]->(s:Service) OPTIONAL MATCH (p)-[:LEARNED_FROM]->(i:Incident)
            RETURN p.id AS id, p.title AS title, s.name AS fixes, p.uses AS uses, p.learned_by AS learned_by,
                   collect(i.id) AS from_incidents, p.steps AS steps ORDER BY p.uses DESC""", read_only=True),
        "facts": query(tenant, """MATCH (f:Fact)-[:ABOUT]->(x)
            RETURN f.text AS text, coalesce(x.name, x.handle, x.id) AS about, f.ts AS ts ORDER BY ts DESC LIMIT 20""",
                       read_only=True),
        "handoffs": query(tenant, """MATCH (a:Agent)-[h:HANDED_OFF]->(b:Agent)
            RETURN a.name AS src, b.name AS dst, h.incident AS incident, h.note AS note, h.ts AS ts
            ORDER BY ts DESC LIMIT 20""", read_only=True),
    }


@app.get("/api/tools")
def api_tools():
    return [{"name": n, "description": t["schema"]["function"]["description"], "writes": t["writes"],
             "params": t["schema"]["function"]["parameters"]["properties"]} for n, t in tools.TOOLS.items()]


@app.post("/api/tools/{name}")
def api_tool(name: str, req: ToolReq):
    if name not in tools.TOOLS or tools.TOOLS[name]["writes"]:
        raise HTTPException(404, "unknown or write tool")
    return tools.call(req.tenant, name, req.args).to_dict()


@app.post("/api/incident")
async def api_incident(req: IncidentReq):
    return _stream(agents.run_incident, req.tenant, req.service, req.title, alert_id=req.alert_id)


@app.post("/api/ask")
async def api_ask(req: AskReq):
    return _stream(agents.ask, req.tenant, req.question, session_id=req.session_id)


@app.post("/api/resolve")
def api_resolve(req: ResolveReq):
    sid = memory.start_session(req.tenant, "resolution")
    try:
        return memory.resolve_incident(req.tenant, req.incident_id, req.resolver, req.notes, sid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/reset")
def api_reset():
    from . import seed
    seed.main()
    return {"ok": True}
