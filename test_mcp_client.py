"""Protocol-level check: spawn the MCP server over stdio, list tools, call one."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["hk_spatial_mcp.py"],
                                   cwd="/home/admin/hk-spatial-mcp")
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            assert {"search_hk_data", "load_hk_data", "build_map", "arcgis_request",
                    "publish_layer"} <= set(names), names
            res = await s.call_tool("search_hk_data", {"keyword": "public toilet", "limit": 3})
            payload = json.loads(res.content[0].text)
            print("hits:", [h["title"] for h in payload["hits"]])
            assert payload["hits"], payload
    print("MCP stdio OK")


asyncio.run(main())
