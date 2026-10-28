"""Regression tests for the 2026-10-29 P0 fixes — run over real stdio MCP, the path DSH uses.

1. plain 3DObject scene services (no Building wrapper, no nested sublayers) must be readable
2. bim_report / bim_findings must survive a plain Windows spawn (no -X utf8): CJK file writes
3. tool failures must return structured {"error": ...} JSON, not the masked
   'Error executing tool X' protocol exception from the mcp 2.x server
4. build_map must accept a bare comma-joined dataset_ids string (not just a JSON-array string)
5. Scene Package (.slpk) item ids must read end-to-end (zip backend, both i3s layouts)
6. multi-sublayer merge must cover every discipline sublayer and exclude the duplicate
   'Overview' copy (GrandCentral: 9 real sublayers, 6,919 elements, exactly 757 Walls —
   the same count agol-bim-mcp reports)
7. the where grammar: AND / NOT / LIKE / IN / IS NULL / bare word
8. bad filters return where_parse; zero matches return field_values hints

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
GC_ITEM = "5ced7f09314e45578d1212d005ff3b19"                 # GrandCentral_V2 Scene Package (.slpk)
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

            # 5+6. Scene Package end-to-end: zip backend, discipline merge, Overview excluded.
            # agol-bim-mcp reports 757 Walls for this same package — parity is the assertion.
            w = await call("bim_elements", {"source": GC_ITEM, "where": "Category=Walls", "limit": 2})
            assert "error" not in w, w
            assert w["matched"] == 757, w["matched"]
            print(f"5 slpk       : GrandCentral .slpk, {w['matched']} Walls (agol-bim-mcp parity)")
            a = await call("bim_audit", {"source": GC_ITEM})
            assert "error" not in a, a
            assert a["sublayers"] == 9 and a["elements"] == 6919, (a["sublayers"], a["elements"])
            print(f"6 merge      : {a['elements']} elements across {a['sublayers']} discipline sublayers")

            # 7. where grammar (counts verified against agol-bim-mcp / direct runs)
            expect = {"Category=Walls AND BldgLevel=20": 8, "BldgLevel IN (10, 20)": 78,
                      "Family LIKE 'Basic%'": 757, "NOT Category=Floors": 6411,
                      "BldgLevel_Desc IS NULL": 3, "Stairs": 44}
            for where, n in expect.items():
                r = await call("bim_elements", {"source": GC_ITEM, "where": where, "limit": 1})
                assert r.get("matched") == n, (where, r.get("matched"), n)
            print(f"7 where      : {len(expect)} grammar forms, all counts exact")

            # 8. filter ergonomics: parse errors and zero-match hints are structured
            r = await call("bim_elements", {"source": GC_ITEM, "where": "Category=Walls AND", "limit": 1})
            assert r.get("error") == "where_parse", r
            r = await call("bim_elements", {"source": GC_ITEM, "where": "NoSuchField=1", "limit": 1})
            assert r["matched"] == 0 and r["field_values"][0]["problem"] == "no such field", r
            r = await call("bim_elements", {"source": GC_ITEM, "where": "Category=Nope", "limit": 1})
            assert r["matched"] == 0 and r["field_values"][0]["sample_values"], r
            print("8 hints      : where_parse + no-such-field + sample_values")

    print("bim regressions OK")


asyncio.run(main())
