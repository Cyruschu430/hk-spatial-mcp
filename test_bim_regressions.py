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
6b. legacy i3s 1.4-1.6 services inline the node tree in `nodes/root` and 404 on
   `nodepages/0`; they must still read (was: 0 nodes -> false "not element-level BIM")
6c. hosted Building layers nest disciplines under group sublayers; reading only the first
   level leaves a 61-discipline model as one shell (HK_MCP_SLOW=1: ~12 min)
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
GC_ITEM = "5ced7f09314e45578d1212d005ff3b19"
BLDGE_FLOORS = ("https://tiles.arcgis.com/tiles/C9eaAQefzoUvxmZ5/arcgis/rest/services/"
                "Floors/SceneServer")                          # i3s 1.6 (2018), children inlined
CIC = ("https://services5.arcgis.com/BFucnLaN4NszOvuE/arcgis/rest/services/"
       "PXXCICXXZZA/SceneServer")   # Building layer, 61 disciplines under group sublayers
ZERO_ITEM = "5275b4eda9a441d28b9b04925fbbf7b2"  # hosted scene service (not a package)                 # GrandCentral_V2 Scene Package (.slpk)
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

            def _live_state(handle):
                """The bridge the page polls — read it straight off disk to check what the
                viewer was actually told to draw, not just what the tool replied."""
                fp = pathlib.Path(env["HK_MCP_DIR"]) / "live" / f"{handle}.json"
                return json.loads(fp.read_text(encoding="utf-8"))

            # 0. tool schemas must survive the _safe wrapper (functools.wraps + signature follow)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            props = tools["bim_audit"].input_schema.get("properties") or {}
            assert "source" in props, f"bim_audit schema lost its params: {tools['bim_audit'].input_schema}"
            print(f"0 schema     : {len(tools)} tools, bim_audit params intact")

            # 1. plain 3DObject scene service (was: ValueError 'no 3DObject sublayer found')
            #    positions must be reprojected to WGS84 — the layer declares Web Mercator
            el = await call("bim_elements", {"source": MEINAU, "limit": 3})
            assert "error" not in el, el
            assert el["matched"] > 0 and el["rows"], el
            lon, lat = el["rows"][0]["lon"], el["rows"][0]["lat"]
            assert 7 < lon < 9 and 48 < lat < 49, (lon, lat)  # Strasbourg, not raw Mercator metres
            print(f"1 3DObject   : {el['matched']} elements, reprojected to {lon:.4f},{lat:.4f}")

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
            cov = a.get("coverage") or {}
            # interior i3s nodes legitimately have no features; real failures must be zero
            assert not cov.get("nodes_failed") and not cov.get("attr_failed"), cov
            assert (cov.get("nodes_with_features", 0) + cov.get("nodes_empty", 0)
                    + cov.get("nodes_failed", 0)) == cov.get("nodes"), cov
            print(f"6 merge      : {a['elements']} elements, {a['sublayers']} sublayers, "
                  f"coverage {cov['nodes_with_features']}/{cov['nodes']} nodes "
                  f"({cov.get('nodes_empty', 0)} structural empties)")

            # 6b. legacy i3s 1.6: node tree inlined in the node documents, `nodepages/0` 404s.
            #     Building E (2018 trade services) has 7 floor slabs over levels 1,3,4,5.
            a = await call("bim_audit", {"source": BLDGE_FLOORS})
            assert "error" not in a, a
            assert a["elements"] == 7 and a["sublayers"] == 1, (a["elements"], a["sublayers"])
            assert not any("element-level" in v for v in a.get("verdict") or []), a["verdict"]
            fl = await call("bim_floors", {"source": BLDGE_FLOORS})
            assert fl["slabs_total"] == 7 and fl["coverage_ok"], fl
            print(f"6b legacy    : i3s 1.6 tree, {a['elements']} slabs, "
                  f"levels {sorted(fl['per_level'])}")

            # 6c. nested group sublayers: CIC (Kai Tak) publishes 61 disciplines two levels
            #     down ('Full Model' -> 'Structural' -> Beams). Slow (~12 min, 1,897 nodes),
            #     so opt-in; the counts are the measured totals.
            if os.environ.get("HK_MCP_SLOW"):
                a = await call("bim_audit", {"source": CIC})
                assert a["sublayers"] == 61 and a["elements"] == 40283, (a["sublayers"], a["elements"])
                print(f"6c nested    : {a['sublayers']} sublayers, {a['elements']} elements")

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

            # 9. interactive viewer: element identity + the live state bridge.
            #    `uid` is sublayer/node:id — the only unique key, because i3s feature ids
            #    restart inside a node AND node ids restart in every sublayer (on ZCB,
            #    ids=[33] matches 17 distinct elements across 17 sublayers).
            v = await call("bim_viewer", {"source": ZCB_ITEM})
            assert v.get("viewer") and v.get("url"), v
            assert v["elements"] > 1000, v
            assert all(x["level"] != "-2147483648" for x in v["levels"]), v["levels"]
            handle = v["viewer"]

            # 9a. the handle is accepted everywhere, not only by the viewer tools
            r = await call("bim_elements", {"source": handle, "where": "Category=door", "limit": 3})
            assert r.get("matched", 0) > 0, r
            uid = r["rows"][0]["uid"]
            assert uid.count("/") == 1 and ":" in uid, uid          # 'Doors/0:33'

            # 9b. one uid identifies exactly one element; a bare id does not
            one = await call("bim_select", {"source": handle, "uids": [uid], "isolate": False})
            assert one["matched"] == 1, one
            many = await call("bim_select", {"source": handle, "ids": [r["rows"][0]["id"]],
                                             "isolate": False})
            assert many["matched"] >= 1, many
            print(f"9 viewer     : handle accepted; uid {uid!r} -> 1 element, "
                  f"bare id -> {many['matched']} (uid is the exact key)")

            # 9c. filter reports a real element count and refuses an unknown category
            f = await call("bim_filter", {"source": handle, "floors": "5"})
            assert f["expression"] == "BldgLevel IN (5)", f
            assert 0 < f["elements_shown"] < f["elements_total"], f
            bad = await call("bim_filter", {"source": handle, "categories": "Nope"})
            assert bad.get("error", "").startswith("no such category"), bad

            # 9d. select-nothing is refused, and an unknown uid is reported not ignored
            empty = await call("bim_select", {"source": handle, "isolate": False})
            assert "nothing to select" in json.dumps(empty), empty
            miss = await call("bim_select", {"source": handle, "uids": ["Nope/9:9"],
                                             "isolate": False})
            assert miss["matched"] == 0 and miss.get("unknown_uids") == ["Nope/9:9"], miss
            print(f"9c filter    : floors=5 -> {f['elements_shown']}/{f['elements_total']}; "
                  f"bad category + empty select + unknown uid all refuse cleanly")

            # 9e. the bridge is a file the page polls: rev must advance on every write
            c1 = await call("bim_clear", {"source": handle})
            c2 = await call("bim_filter", {"source": handle, "floors": "5"})
            c3 = await call("bim_clear", {"source": handle})
            revs = [c1["state_rev"], c2["state_rev"], c3["state_rev"]]
            assert revs == sorted(set(revs)), revs
            print(f"9e bridge    : state_rev advances on every call {revs}")

            # 9f. ghost mode: a focused selection keeps the rest of the building as a
            #     wire-frame outline instead of hiding it. Two things must hold —
            #     (a) the predicate names the Category ATTRIBUTE ('door'), not the sublayer
            #         ('Doors'), and (b) no sublayer visibility list is written, because the
            #         shell sublayers (ExteriorShell, Site, Stairs) are absent from the element
            #         catalogue and would stay solid over the ghost.
            g = await call("bim_select", {"source": handle, "uids": [uid]})
            assert g["ghost"] is True, g
            live = _live_state(handle)
            assert live.get("ghost") is True, live
            assert not (live.get("hidden") or []), live.get("hidden")
            expr = (live.get("filter") or {}).get("expression") or ""
            assert "Category IN ('door')" in expr, expr
            # a wide selection must NOT ghost — the outline of everything is noise
            wide = await call("bim_select", {"source": handle, "where": "Discipline=Structural"})
            assert wide["matched"] > 200, wide
            assert wide["ghost"] is False, wide
            print(f"9f ghost     : '{expr}' + 0 hidden sublayers; "
                  f"{wide['matched']}-element select leaves ghost off")

            # 9g. the camera aim height comes from the storey elevation, not a fixed 12 m:
            #     a storey-10 door sits at 3.95 m here, so aiming at 12 m put it low in frame.
            #     It must also be a finite number — NaN here writes invalid JSON and the page
            #     drops the whole update (seen when averaging rows with unset elevations).
            assert g["camera"]["aim_z"] is not None, g
            assert 0 < g["camera"]["aim_z"] < 60, g["camera"]
            assert "NaN" not in json.dumps(live), "state file carries NaN — not valid JSON"
            print(f"9g aim_z     : {g['camera']['aim_z']} m from BldgLevel_Elev (not a fixed 12 m)")

            # 9. publish_scene e2e — token-gated; publishes GrandCentral as a PRIVATE hosted
            #    scene layer, verifies it answers, then deletes both items (org hygiene).
            if os.environ.get("ARCGIS_TOKEN"):
                me = await call("arcgis_request", {"path": "community/self", "params": {}})
                user = me.get("username")
                assert user, me
                # 9a. non-package sources are refused before any upload
                r = await call("publish_scene", {"source": ZERO_ITEM})
                assert "already a hosted scene service" in (r.get("error") or ""), r
                # 9b. GrandCentral publishes end-to-end as a PRIVATE scene layer, then is deleted
                #     (unique title: any residue from earlier failed runs can't collide)
                import time as _time
                p = await call("publish_scene", {
                    "source": GC_ITEM,
                    "title": f"regression-test scene {int(_time.time())} (delete me)"})
                assert p.get("publish") and p.get("service_url"), p
                chk = await call("arcgis_request", {
                    "path": p["service_url"], "params": {"f": "json"}})
                assert chk.get("layers"), chk
                for iid in (p["service_item_id"], p["itemId"]):
                    d = await call("arcgis_request", {
                        "path": f"content/users/{user}/items/{iid}/delete",
                        "params": {}, "method": "POST"})
                    assert d.get("success"), d
                print(f"10 scene     : refused non-package; published PRIVATE scene layer, "
                      f"verified, deleted ({len(chk['layers'])} layers)")
            else:
                print("10 scene     : skipped (no ARCGIS_TOKEN in env)")

    print("bim regressions OK")


asyncio.run(main())
