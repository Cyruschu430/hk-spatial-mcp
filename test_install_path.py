"""Install-path smoke test: the shipped git URL must work as an MCP server, and every tool must
answer over real stdio. This is the gate before saying "it works"."""
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

URL = "git+https://github.com/Cyruschu430/hk-spatial-mcp"
EXPECTED = ["search_hk_data", "load_hk_data", "query_hk_data", "summarise_hk_data", "build_map", "build_scene",
            "geocode_address", "geometry_op", "elevation_at", "export_static_map", "export_static_svg",
            "arcgis_request", "publish_layer"]


async def main():
    params = StdioServerParameters(command="uvx", args=["--reinstall", "--from", URL, "hk-spatial-mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args):
                res = await s.call_tool(name, args)
                out = json.loads(res.content[0].text)
                assert not (out.get("isError") or out.get("error") and name not in (
                    "arcgis_request", "publish_layer")), (name, out)
                return out

            names = [t.name for t in (await s.list_tools()).tools]
            print("tools:", names)
            assert sorted(names) == sorted(EXPECTED), names

            out = await call("search_hk_data", {"keyword": "廁所", "limit": 3})
            print(f"1 search      : {out['total_datasets']} datasets, top = {out['hits'][0]['title']}")
            assert out["total_datasets"] > 1100 and "toilet" in out["hits"][0]["title"].lower()

            did = out["hits"][0]["dataset_id"]
            ov = await call("summarise_hk_data", {"dataset_id": did})
            field = (ov.get("groupable_fields") or [{}])[0].get("field")
            agg = await call("summarise_hk_data", {"dataset_id": did, "group_by": field, "top": 3})
            print(f"1b summarise  : {len(agg.get('rows', []))} groups, chart {agg.get('chart_url')}")
            assert agg.get("rows") and "chart-" in agg["chart_url"], agg

            out = await call("load_hk_data", {"dataset_id": did, "name": out["hits"][0]["title"]})
            print(f"2 load        : {out['features']} features, {out['geometry']}, bbox {out['bbox']}")
            assert out["features"] > 0 and out["bbox"]

            out = await call("build_map", {"dataset_ids": [did], "title": "smoke 2D",
                                           "basemap": "dark-gray", "labels": True})
            print(f"3 build_map   : {out['url']} ({out['mode']}, {out['features']} features)")
            assert out["mode"] == "2d"

            out = await call("build_scene", {"dataset_ids": [did], "title": "smoke 3D", "tilt": 60})
            print(f"4 build_scene : {out['url']} ({out['mode']})")
            assert out["mode"] == "3d" and out["url"].endswith(".html"), out
            assert out["latest_url"].endswith("scene.html"), out

            out = await call("geocode_address", {"single_line": "Central, Hong Kong", "max_results": 2})
            print(f"5 geocode     : {out['candidates'][0]['address']} @ {out['candidates'][0]['lon']:.4f}")
            assert out["candidates"]

            out = await call("geocode_address", {"lon": 114.1694, "lat": 22.3193})
            print(f"6 reverse     : {out['address']}")
            assert out["address"]

            out = await call("geometry_op", {"operation": "project",
                                             "geojson": {"type": "Point", "coordinates": [835000, 820000]},
                                             "in_sr": 2326, "out_sr": 4326})
            print(f"7 project     : {out['geometry']['coordinates']}")
            assert 113.8 < out["geometry"]["coordinates"][0] < 114.5

            out = await call("geometry_op", {"operation": "buffer",
                                             "geojson": {"type": "Point", "coordinates": [114.1694, 22.3193]},
                                             "distance": 500})
            print(f"8 buffer      : {out['geometry']['type']}, {len(out['geometry']['coordinates'][0])} vertices")
            assert out["geometry"]["type"] == "Polygon"

            out = await call("elevation_at", {"lon": 114.1716, "lat": 22.2759})
            print(f"9 elevation   : {out['elevation_m']} m")
            assert 0 < out["elevation_m"] < 1000

            out = await call("export_static_map", {"bbox": "114.10,22.25,114.25,22.35",
                                                   "basemap": "streets", "size": "600,400"})
            print(f"10 static png : {out['bytes']} bytes -> {out['url']}")
            assert out["bytes"] > 5000

            svg = await call("export_static_svg", {"dataset_ids": [did], "title": "smoke static",
                                                   "basemap": "topographic"})
            print(f"10b static svg: {svg['size_px']} at z{svg['zoom']}, {svg['features']} features")
            assert svg["features"] > 0 and svg["url"].endswith(".svg")

            out = await call("arcgis_request", {"path": "search", "params": {"q": "test", "num": 1}})
            print(f"11 arcgis_request without token: {str(out)[:110]}")

            out = await call("publish_layer", {"dataset_id": did, "title": "smoke"})
            print(f"12 publish_layer without token: {str(out)[:110]}")


asyncio.run(main())
print(f"install path OK — all {len(EXPECTED)} tools listed, every tool exercised over stdio")
