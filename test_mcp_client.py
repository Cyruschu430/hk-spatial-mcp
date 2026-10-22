"""Protocol-level check: spawn the MCP server over stdio, list tools, call one."""
import asyncio
import json
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = pathlib.Path(__file__).resolve().parent
EXPECTED = {"search_hk_data", "load_hk_data", "build_map", "build_scene", "geocode_address",
            "geometry_op", "elevation_at", "export_static_map", "arcgis_request", "publish_layer"}


async def main():
    params = StdioServerParameters(command=sys.executable, args=["hk_spatial_mcp.py"], cwd=str(HERE))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools:", names)
            assert set(names) == EXPECTED, f"unexpected tool set: {sorted(set(names) ^ EXPECTED)}"
            res = await s.call_tool("search_hk_data", {"keyword": "public toilet", "limit": 3})
            payload = json.loads(res.content[0].text)
            print("hits:", [h["title"] for h in payload["hits"]])
            assert payload["hits"], payload
    print("MCP stdio OK")


asyncio.run(main())
