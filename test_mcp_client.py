"""Protocol-level check: spawn the MCP server over stdio, list tools, call one."""
import asyncio
import json
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = pathlib.Path(__file__).resolve().parent
EXPECTED = {"search_hk_data", "load_hk_data", "query_hk_data", "summarise_hk_data", "build_map", "build_scene",
            "geocode_address",
            "geometry_op", "elevation_at", "export_static_map", "export_static_svg", "arcgis_request",
            "publish_layer", "dataset_status", "spatial_join",
            "bim_audit", "bim_floors", "bim_elements", "site_zoning", "lot_check"}

BIM = ("https://tiles.arcgis.com/tiles/V6ZHFr6zdgNZuVG0/arcgis/rest/services/"
       "Bldg_E_Color_UC2020_demo/SceneServer")


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
            # the new BIM tools must work over the protocol, not just as functions
            bm = json.loads((await s.call_tool("bim_audit", {"source": BIM})).content[0].text)
            assert bm["elements"] == 24787, bm["elements"]
            print("bim_audit:", bm["elements"], "elements,", bm["completeness_pct"]["Mark"],
                  "% Mark empty,", len(bm["verdict"]), "findings")
            fl = json.loads((await s.call_tool("bim_floors", {"source": BIM})).content[0].text)
            assert fl["slabs_total"] == 9, fl
            print("bim_floors:", fl["slabs_total"], "slabs, coverage_ok =", fl["coverage_ok"])
            sz = json.loads((await s.call_tool("site_zoning",
                                               {"lat": 22.337, "lon": 114.175, "radius_m": 100})).content[0].text)
            assert any(z["zone"] == "OU" for z in sz["zones"]), sz["zones"][:3]
            print("site_zoning:", len(sz["zones"]), "zones,",
                  sz["registered_slopes"]["count"], "slopes, map link:",
                  bool(sz["links"].get("map_viewer")))
    print("MCP stdio OK")


asyncio.run(main())
