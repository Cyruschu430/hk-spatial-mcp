"""The one MCP client for this repo.

`test_mcp_client.py` (protocol check) and `eval/record.py` (trace capture) both go through here,
so there is a single definition of how the server is spawned and a single place to change it.
"""
import os
import pathlib
import sys
from contextlib import asynccontextmanager

HERE = pathlib.Path(__file__).resolve().parent


def server_params(env=None):
    """stdio parameters for the local server, with optional env overrides."""
    from mcp import StdioServerParameters

    merged = {**os.environ}
    merged.update(env or {})
    return StdioServerParameters(command=sys.executable, args=["hk_spatial_mcp.py"],
                                 cwd=str(HERE), env=merged)


@asynccontextmanager
async def session(env=None):
    """Initialised ClientSession against a freshly spawned server."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params(env)) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s
