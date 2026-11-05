"""Attribute-only CSDI exports must be refused, not drawn.

LandSD "Building" and "Lot" export a valid GeoJSON FeatureCollection in which every feature is
`geometry: null` (verified live 2026-11-05: 87,982 / 87,982 and 1,606 / 1,606). Before this guard,
load_hk_data() reported `features: 87982` with no warning and build_map() added a Point layer that
could not draw anything — a dataset property that reads as a tool bug.

Offline: `_http` is replaced, so no CSDI call is made.
"""
import json
import os
import pathlib
import tempfile

os.environ.setdefault("HK_MCP_DIR", tempfile.mkdtemp(prefix="hk-nogeom-"))

import hk_spatial_mcp as m

m.WORK = pathlib.Path(os.environ["HK_MCP_DIR"]).resolve()

ATTR_ONLY = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": None,
     "properties": {"OBJECTID": i, "BuildingStructureID": 1572134 + i, "LotNo": "136"}}
    for i in range(1, 6)]}
WITH_GEOM = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.20, 22.40]},
     "properties": {"OBJECTID": 1, "NAME_EN": "a"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.21, 22.41]},
     "properties": {"OBJECTID": 2, "NAME_EN": "b"}}]}


def _seed(ds, fc):
    d = m.WORK / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ds}.geojson").write_text(json.dumps(fc), encoding="utf-8")


def _http_returning(fc):
    def fake(url, data=None, headers=None, timeout=60):
        return json.dumps(fc).encode()
    return fake


def demo():
    # 1. an all-null-geometry export is an error that names the cause, not a silent success
    _seed("no_geom_ds", ATTR_ONLY)
    out = json.loads(m.load_hk_data("no_geom_ds"))
    assert "error" in out, out
    assert "none carry geometry" in out["error"], out
    assert "features" not in out, "a geometry-less dataset must not report a feature count"
    # the reply has to point somewhere useful, or the caller is simply stuck
    assert "query_hk_data" in out.get("still_usable", ""), out
    assert "Lot_in_Hong_Kong" in out.get("alternative", ""), out

    # 2. the same guard fires on a fresh download, not only on a cache hit
    m._http = _http_returning(ATTR_ONLY)
    out = json.loads(m.load_hk_data("fresh_no_geom", refresh=True))
    assert "error" in out and "none carry geometry" in out["error"], out

    # 3. a dataset with geometry still works, bbox and all
    _seed("good_ds", WITH_GEOM)
    out = json.loads(m.load_hk_data("good_ds"))
    assert "error" not in out, out
    assert out["features"] == 2 and out["bbox"] == [114.2, 22.4, 114.21, 22.41], out

    # 4. build_map drops an attribute-only layer instead of drawing an empty Point layer,
    #    and says why — the map still builds from the layer that does have geometry
    m._serve = lambda *a, **k: None  # no localhost server in a test
    _seed("no_geom_ds", ATTR_ONLY)
    _seed("good_ds", WITH_GEOM)
    out = json.loads(m.build_map(["good_ds", "no_geom_ds"], title="geom guard"))
    assert "error" not in out, out
    assert out["layers"] == ["good_ds"], out
    assert out["features"] == 2, out
    assert any("no geometry" in s and "no_geom_ds" in s for s in out["missing"]), out

    # 5. a map made only of geometry-less layers refuses rather than serving a blank page
    out = json.loads(m.build_map(["no_geom_ds"], title="only attr"))
    assert "error" in out, out

    print("geometry guard: 5/5 ok")


if __name__ == "__main__":
    demo()
