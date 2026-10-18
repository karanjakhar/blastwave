"""FalkorDB access. One graph per tenant (FalkorDB multigraph): `bw_<tenant>`."""
import os
import time
from functools import lru_cache

from dotenv import load_dotenv
from falkordb import FalkorDB, Node, Edge, Path

load_dotenv()

GRAPH_PREFIX = "bw_"


@lru_cache(maxsize=1)
def client() -> FalkorDB:
    return FalkorDB(
        host=os.getenv("FALKORDB_HOST", "localhost"),
        port=int(os.getenv("FALKORDB_PORT", "6379")),
    )


def graph(tenant: str):
    return client().select_graph(GRAPH_PREFIX + tenant)


def tenants() -> list[str]:
    return sorted(g[len(GRAPH_PREFIX):] for g in client().list_graphs() if g.startswith(GRAPH_PREFIX))


def now() -> int:
    return int(time.time())


def _plain(v):
    """Convert FalkorDB result values into JSON-friendly structures."""
    if isinstance(v, Node):
        return {"_id": v.id, "_labels": list(v.labels), **v.properties}
    if isinstance(v, Edge):
        return {"_id": v.id, "_type": v.relation, "_src": v.src_node, "_dst": v.dest_node, **v.properties}
    if isinstance(v, Path):
        return {"nodes": [_plain(n) for n in v.nodes()], "edges": [_plain(e) for e in v.edges()]}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def query(tenant: str, cypher: str, params: dict | None = None, read_only: bool = False) -> list[dict]:
    g = graph(tenant)
    res = g.ro_query(cypher, params or {}) if read_only else g.query(cypher, params or {})
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in res.header]
    return [dict(zip(cols, (_plain(v) for v in row))) for row in res.result_set]
