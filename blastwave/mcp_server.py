"""Expose Blastwave's graph tools over MCP (stdio), so any MCP client (Claude Desktop, Claude Code,
Cursor, ...) can use the same FalkorDB-backed tools the built-in agents use.

    claude mcp add blastwave -- uv run --directory /path/to/repo python -m blastwave.mcp_server
"""
import inspect
import json
import os

from mcp.server.fastmcp import FastMCP

from . import memory  # noqa: F401  (registers incident tools)
from . import tools

mcp = FastMCP("blastwave")
TENANT = os.getenv("BLASTWAVE_TENANT", "acme")


def _register(name: str, spec: dict):
    fn = spec["fn"]
    params = [p for p in inspect.signature(fn).parameters.values() if p.name != "tenant"]

    def handler(**kwargs):
        tenant = kwargs.pop("tenant", None) or TENANT
        res = tools.call(tenant, name, kwargs)
        return json.dumps({"rows": res.rows, "cypher": res.cypher}, default=str)

    handler.__name__ = name
    handler.__doc__ = spec["schema"]["function"]["description"] + " Optional `tenant` selects the tenant graph."
    handler.__signature__ = inspect.Signature(
        params + [inspect.Parameter("tenant", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str | None)])
    handler.__annotations__ = {p.name: p.annotation for p in params} | {"tenant": str | None, "return": str}
    mcp.tool()(handler)


for _name, _spec in tools.TOOLS.items():
    _register(_name, _spec)


if __name__ == "__main__":
    mcp.run()
