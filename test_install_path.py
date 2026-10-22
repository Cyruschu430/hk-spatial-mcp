"""Install-path check: the shipped git URL must work as an MCP server, not just the local file."""
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

URL = "git+https://github.com/Cyruschu430/hk-spatial-mcp"


async def main():
    params = StdioServerParameters(command="uvx", args=["--reinstall", "--from", URL, "hk-spatial-mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools:", names)
            res = await s.call_tool("search_hk_data", {"keyword": "廁所", "limit": 3})
            out = json.loads(res.content[0].text)
            print("catalogue size:", out["total_datasets"], "| matched:", out["matched"])
            print("top hit:", out["hits"][0]["title"], "|", out["hits"][0].get("title_zh"))
            assert out["total_datasets"] > 1100, out["total_datasets"]
            assert "toilet" in out["hits"][0]["title"].lower(), out["hits"][0]
            assert "build_map" in names


asyncio.run(main())
print("install path OK (whole catalogue, Chinese query)")
