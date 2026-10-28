"""Regression tests for the 2026-10-29 P0 fixes — run over real stdio MCP, the path DSH uses.

1. plain 3DObject scene services (no Building wrapper, no nested sublayers) must be readable
2. bim_report / bim_findings must survive a plain Windows spawn (no -X utf8): CJK file writes
3. tool failures must return structured {"error": ...} JSON, not the masked
   'Error executing tool X' protocol exception from the mcp 2.x server
4. build_map must accept a bare comma-joined dataset_ids string (not just a JSON-array string)

Hermetic: HK_MCP_DIR goes to a temp dir, the preview server to a non-default port.
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = pathlib.Path(__file__).resolve().parent
MEINAU = ("https://tiles.arcgis.com/tiles/PD34KSBxbKxXBglp/arcgis/rest/services/"
          "BIM_STRUCTURE_PROJET_MEINAU_WM_v3/SceneServer")  # plain 3DObject layer, 0 sublayers
ZCB_ITEM = "5275b4eda9a441d28b9b04925fbbf7b2"                # Zero Carbon Building (BSL, item id)
EV = "epd_rcd_1631080339740_69941"                           # 990 EV chargers (points)
DIST = "had_rcd_1634523272907_75218"                         # 18 district boundaries (polygons)


async def main():
    env = dict(os.environ)
    env["HK_MCP_DIR"] = tempfile.mkdtemp(prefix="hk-mcp-test-")
    env["HK_MCP_PORT"] = "8878"
    env["PYTHONIOENCODING"] = "utf-8"
    params = StdioServerParameters(command=sys.executable, args=["hk_spatial_mcp.py"],
                                   cwd=str(HERE), env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args):
                res = await s.call_tool(name, args)
                assert not res.is_error, f"{name}: protocol-level error {res.content[0].text[:200]}"
                return json.loads(res.content[0].text)

            # 0. tool schemas must survive the _safe wrapper (functools.wraps + signature follow)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            props = tools["bim_audit"].input_schema.get("properties") or {}
            assert "source" in props, f"bim_audit schema lost its params: {tools['bim_audit'].input_schema}"
            print(f"0 schema     : {len(tools)} tools, bim_audit params intact")

            # 1. plain 3DObject scene service (was: ValueError 'no 3DObject sublayer found')
            el = await call("bim_elements", {"source": MEINAU, "limit": 3})
            assert "error" not in el, el
            assert el["matched"] > 0 and el["rows"], el
            print(f"1 3DObject   : {el['matched']} elements from a plain scene service")

            # 2. CJK file writes under a plain spawn (was: UnicodeEncodeError at write_text)
            rp = await call("bim_report", {"source": ZCB_ITEM})
            assert "error" not in rp, rp
            assert pathlib.Path(rp["html_file"]).exists(), rp
            print(f"2 bim_report : {pathlib.Path(rp['html_file']).name}, pdf_bytes={rp['pdf_bytes']}")
            fi = await call("bim_findings", {"source": ZCB_ITEM, "mode": "scene"})
            assert "error" not in fi, fi
            assert pathlib.Path(fi["viewer_file"]).exists(), fi
            print(f"2b findings  : {fi['findings']} findings, viewer {pathlib.Path(fi['viewer_file']).name}")

            # 3. structured error JSON instead of the masked protocol exception
            bad = await call("bim_audit", {"source": "deadbeef" * 4})
            assert "error" in bad and bad.get("tool") == "bim_audit", bad
            print(f"3 error json : {bad['error'][:80]}")

            # 4. comma-joined dataset_ids string (was: treated as ONE id -> misleading 404)
            mp = await call("build_map", {"dataset_ids": f"{EV},{DIST}", "title": "regression csv"})
            assert "error" not in mp, mp
            assert len(mp["layers"]) == 2 and mp["features"] > 1000, mp
            print(f"4 csv ids    : {len(mp['layers'])} layers, {mp['features']} features")

    print("bim regressions OK")


asyncio.run(main())
