"""Live demo: drive the hk-spatial MCP server over the real stdio protocol.

Nothing runs in-process — this is exactly what WorkBuddy / DSH / Claude Desktop do.
"""
import asyncio
import json
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = pathlib.Path("/home/admin/hk-spatial-mcp")
BIM = ("https://tiles.arcgis.com/tiles/V6ZHFr6zdgNZuVG0/arcgis/rest/services/"
       "Bldg_E_Color_UC2020_demo/SceneServer")


def show(label, payload, keys=None):
    print(f"\n=== {label} ===")
    if keys:
        payload = {k: payload[k] for k in keys if k in payload}
    print(json.dumps(payload, ensure_ascii=False, indent=1)[:2200])


async def main():
    params = StdioServerParameters(command=sys.executable, args=["hk_spatial_mcp.py"], cwd=str(HERE))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print(f"connected: {len(tools)} tools ->", ", ".join(tools[-5:]))

            bm = json.loads((await s.call_tool("bim_audit", {"source": BIM})).content[0].text)
            show("1. bim_audit — 交得未？", bm,
                 ["elements", "levels", "completeness_pct", "tags", "catalogue", "floors", "verdict"])

            fl = json.loads((await s.call_tool("bim_floors", {"source": BIM})).content[0].text)
            show("2. bim_floors — 樓板齊唔齊？", fl,
                 ["slabs_total", "per_level", "levels_present", "levels_with_slabs", "coverage_ok"])

            el = json.loads((await s.call_tool(
                "bim_elements", {"source": BIM, "where": "Category=Doors", "limit": 3})).content[0].text)
            show("3. bim_elements — 門喺邊？", el, ["matched", "returned", "rows"])

            sz = json.loads((await s.call_tool(
                "site_zoning", {"lat": 22.337, "lon": 114.175, "radius_m": 150})).content[0].text)
            show("4. site_zoning — 呢塊地受咩限制？", sz,
                 ["bbox", "zones", "height_control", "registered_slopes", "landslide_records", "links"])

            png = json.loads((await s.call_tool(
                "export_static_map", {"bbox": "114.168,22.333,114.182,22.341",
                                      "basemap": "streets", "size": "900,600"})).content[0].text)
            print("\n=== 5. export_static_map ===")
            print(json.dumps(png, ensure_ascii=False)[:300])


asyncio.run(main())
