"""Install-path check: the shipped git URL must work as an MCP server, not just the local file."""
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

URL = "git+https://github.com/Cyruschu430/hk-spatial-mcp"


async def main():
    params = StdioServerParameters(command="uvx", args=["--from", URL, "hk-spatial-mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print("tools:", names)
            res = await s.call_tool("search_hk_data", {"keyword": "country park", "limit": 2})
            print("hits:", [h["title"] for h in json.loads(res.content[0].text)["hits"]])
            assert "build_map" in names


asyncio.run(main())
print("install path OK")
