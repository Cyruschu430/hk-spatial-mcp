"""Cache freshness: an old export is re-downloaded, a fresh one is not, and a failed refresh
keeps the copy it already had. Offline — `_http` is replaced, so no CSDI call is made."""
import json
import os
import pathlib
import tempfile
import time
import urllib.error

os.environ.setdefault("HK_MCP_DIR", tempfile.mkdtemp(prefix="hk-fresh-"))

import hk_spatial_mcp as m

m.WORK = pathlib.Path(os.environ["HK_MCP_DIR"]).resolve()
OLD = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.10, 22.30]},
     "properties": {"A": 1}}]}
NEW = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.20, 22.40]},
     "properties": {"A": 2}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.21, 22.41]},
     "properties": {"A": 3}}]}


def demo():
    d = m.WORK / "data"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / "demo_ds.geojson"
    fp.write_text(json.dumps(OLD), encoding="utf-8")

    calls = []

    def fake_http(url, data=None, headers=None, timeout=60):
        calls.append(url)
        return json.dumps(NEW).encode()

    m._http = fake_http

    # 1. a file inside the window is reused, and says how old it is
    out = json.loads(m.load_hk_data("demo_ds"))
    assert not calls, calls
    assert out["cached"] is True and out["cached_days"] < 1, out
    assert out["features"] == 1, out

    # 2. a file past STALE_DAYS is re-downloaded instead of quoted as current
    old = time.time() - (m.STALE_DAYS + 15) * 86400
    os.utime(fp, (old, old))
    out = json.loads(m.load_hk_data("demo_ds"))
    assert calls, "stale cache was served without a re-download"
    assert out["cached"] is False and out["features"] == 2, out
    assert out["cached_days"] == 0, out

    # 3. a failed refresh keeps the older copy and admits it
    fp.write_text(json.dumps(OLD), encoding="utf-8")
    os.utime(fp, (old, old))  # write_text resets mtime — age it AFTER writing

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://x/demo_ds", 500, "boom", {}, None)

    m._http = boom
    out = json.loads(m.load_hk_data("demo_ds"))
    assert out["features"] == 1, out
    assert "re-download failed" in out.get("warning", ""), out

    print("cache freshness: 3/3 ok")


if __name__ == "__main__":
    demo()
