#!/usr/bin/env python3
"""hk-spatial-mcp — local MCP server: Hong Kong CSDI spatial data + ArcGIS.

One file, stdlib + `mcp`. Run:  python3 hk_spatial_mcp.py        (stdio, for WorkBuddy/CodeBuddy)
                               python3 hk_spatial_mcp.py --selftest
Env:
  HK_MCP_DIR    workspace dir (default ./hk-spatial)
  HK_MCP_PORT   local preview port (default 8777)
  ARCGIS_TOKEN  ArcGIS Online user/OAuth token (only needed for org content: publish/retrieve)
  ARCGIS_URL    portal base, default https://www.arcgis.com
"""
import json
import os
import pathlib
import re
import secrets
import threading
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

CSDI_CSW = "https://portal.csdi.gov.hk/geoportal/csw"
CSDI_FILE = "https://portal.csdi.gov.hk/csdi-webpage/file-api?dataset_id={}&format=geojson"
AGOL = os.environ.get("ARCGIS_URL", "https://www.arcgis.com").rstrip("/") + "/sharing/rest"
WORK = pathlib.Path(os.environ.get("HK_MCP_DIR") or "hk-spatial").resolve()
PORT = int(os.environ.get("HK_MCP_PORT") or 8777)

CSW_BODY = """<?xml version="1.0"?><csw:GetRecords service="CSW" version="2.0.2" maxRecords="{n}" resultType="results" outputSchema="http://www.opengis.net/cat/csw/2.0.2" xmlns:csw="http://www.opengis.net/cat/csw/2.0.2"><csw:Query typeNames="csw:Record"><csw:ElementSetName>summary</csw:ElementSetName><csw:Constraint version="1.1.0"><ogc:Filter xmlns:ogc="http://www.opengis.net/ogc"><ogc:PropertyIsLike wildCard="%" singleChar="_" escapeChar="\\"><ogc:PropertyName>AnyText</ogc:PropertyName><ogc:Literal>%{kw}%</ogc:Literal></ogc:PropertyIsLike></ogc:Filter></csw:Constraint></csw:Query></csw:GetRecords>"""


def _http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- CSDI

def search_hk_data(keyword: str, limit: int = 8) -> str:
    """Search the Hong Kong CSDI catalogue (1100+ official datasets). Returns dataset_id + title.

    Keywords must be ENGLISH — CSDI metadata is English only ("country park", "public toilet",
    "pedestrian", "land boundary", "rainfall", "zoning"). Place names work: "Wan Chai", "Sha Tin".
    """
    kw = keyword.replace("%", "").replace("'", "")
    body = CSW_BODY.format(n=max(1, min(limit, 50)), kw=kw).encode()
    xml = _http(CSDI_CSW, body, {"Content-Type": "application/xml"}).decode("utf-8", "replace")
    out = []
    for rec in re.findall(r"<csw:SummaryRecord>(.*?)</csw:SummaryRecord>", xml, re.S):
        g = lambda t: ((re.search(rf"<dc:{t}>(.*?)</dc:{t}>", rec, re.S) or [None, None])[1] or "").strip()
        if g("identifier"):
            out.append({"dataset_id": g("identifier"), "title": g("title")})
    if not out:
        return json.dumps({"hits": [], "note": f"No dataset matched {keyword!r}. Try English synonyms."})
    return json.dumps({"hits": out}, ensure_ascii=False)


def load_hk_data(dataset_id: str, name: str = "") -> str:
    """Download one CSDI dataset as GeoJSON (WGS84) into the workspace. Returns a SUMMARY, not the
    data itself — never paste the features into the chat, put them on a map with build_map()."""
    fc = json.loads(_http(CSDI_FILE.format(urllib.parse.quote(dataset_id)), timeout=120))
    feats = fc.get("features") or []
    if not feats:
        return json.dumps({"error": f"{dataset_id}: dataset returned 0 features (not spatial or empty)"})
    (WORK / "data").mkdir(parents=True, exist_ok=True)
    path = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', dataset_id)}.geojson"
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    if name:  # sidecar so build_map can label the layer with the dataset title
        path.with_suffix(".name").write_text(name, encoding="utf-8")
    lons, lats = [], []
    for f in feats:
        for x, y in _coords(f.get("geometry") or {}):
            lons.append(x), lats.append(y)
    return json.dumps({
        "dataset_id": dataset_id, "features": len(feats),
        "geometry": sorted({(f.get("geometry") or {}).get("type") or "?" for f in feats}),
        "fields": list((feats[0].get("properties") or {}).keys())[:20],
        "bbox": [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)]
        if lons else None,
        "file": str(path),
        "next": "call build_map([dataset_id, ...]) to see it in the ArcGIS web map",
    }, ensure_ascii=False)


def _coords(geom):
    """Yield (lon, lat) from any GeoJSON geometry — point, line, polygon, multi."""
    t, c = geom.get("type"), geom.get("coordinates")
    if not c:
        return
    if t == "Point":
        yield c[0], c[1]
    elif t in ("MultiPoint", "LineString"):
        for p in c:
            yield p[0], p[1]
    elif t in ("MultiLineString", "Polygon"):
        for ring in c:
            for p in ring:
                yield p[0], p[1]
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for p in ring:
                    yield p[0], p[1]


# ---------------------------------------------------------------- local preview

def _serve():
    """Start the static preview server once (daemon thread). ponytail: one process-wide port."""
    with _serve_lock:
        if _serve_state.get("up"):
            return
        WORK.mkdir(parents=True, exist_ok=True)
        h = lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=str(WORK), **k)
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), h)
        except OSError:  # port already serving an earlier run — fine, nothing to do
            _serve_state["up"] = True
            return
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _serve_state["up"] = True


_serve_lock = threading.Lock()
_serve_state: dict = {}


def build_map(dataset_ids, title: str = "Hong Kong Spatial Data") -> str:
    """Build an ArcGIS Maps SDK web map from loaded datasets and serve it on localhost.
    Open the returned URL in WorkBuddy's Preview tab."""
    if isinstance(dataset_ids, str):
        dataset_ids = [dataset_ids]
    layers, missing = [], []
    for did in dataset_ids:
        p = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', did)}.geojson"
        if not p.exists():
            r = json.loads(load_hk_data(did))
            if r.get("error"):
                missing.append(r["error"])
                continue
            p = pathlib.Path(r["file"])
        label = p.with_suffix(".name")
        layers.append({"id": did, "name": label.read_text(encoding="utf-8") if label.exists() else did,
                       "data": json.loads(p.read_text(encoding="utf-8"))})
    if not layers:
        return json.dumps({"error": "nothing to map", "details": missing})
    html = TEMPLATE.replace("/*%TITLE%*/", json.dumps(title, ensure_ascii=False))
    html = html.replace("/*%LAYERS%*/", json.dumps(layers, ensure_ascii=False))
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "index.html").write_text(html, encoding="utf-8")
    _serve()
    return json.dumps({"url": f"http://localhost:{PORT}/", "layers": [l["id"] for l in layers],
                       "features": sum(len(l["data"].get("features") or []) for l in layers),
                       "missing": missing,
                       "next": "open the url in WorkBuddy's Preview tab (refresh it to see changes)"})


# ---------------------------------------------------------------- ArcGIS Online

def _token() -> str:
    t = os.environ.get("ARCGIS_TOKEN") or ""
    if not t:
        raise RuntimeError(
            "ARCGIS_TOKEN not set. Set an ArcGIS Online user/OAuth token to publish or read org "
            "content (an API key only covers basemaps/geocoding). WorkBuddy: put it in the MCP "
            "server's env block in mcp.json.")
    return t


def arcgis_request(path: str, params: dict | None = None, method: str = "GET") -> str:
    """Call any ArcGIS Online REST endpoint with the configured token.

    Examples:
      path="search", params={"q": 'title:"my layer"', "num": 5}                 → find items
      path="content/items/<itemId>/data", params={}                            → item JSON
      path="content/items/<itemId>", params={}                                 → item metadata
      path="<featureServiceUrl>/0/query",
           params={"where":"1=1","outFields":"*","f":"json","returnCountOnly":"true"} → count
      analysis: run a GP task under <gpServiceUrl>/<task>/submitJob (then /jobs/<jobId>)
    """
    p = dict(params or {})
    p.setdefault("f", "json")
    p["token"] = _token()
    url = f"{AGOL}/{path.lstrip('/')}"
    if method.upper() == "GET":
        raw = _http(url + "?" + urllib.parse.urlencode(p), timeout=120)
    else:
        raw = _http(url, urllib.parse.urlencode(p).encode(),
                    {"Content-Type": "application/x-www-form-urlencoded"}, timeout=120)
    return _redact(raw.decode("utf-8", "replace"))


def _redact(s: str) -> str:
    return s.replace(os.environ.get("ARCGIS_TOKEN") or "\x00", "<token>")


def publish_layer(dataset_id: str, title: str = "", tags: str = "CSDI,Hong Kong") -> str:
    """Publish a loaded CSDI dataset to ArcGIS Online as a hosted feature layer.
    Needs ARCGIS_TOKEN. Returns itemId + the new service URL."""
    tok = _token()
    src = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', dataset_id)}.geojson"
    if not src.exists():
        r = json.loads(load_hk_data(dataset_id))
        if r.get("error"):
            return json.dumps(r)
        src = pathlib.Path(r["file"])
    title = title or dataset_id
    me = json.loads(_redact(_http(f"{AGOL}/community/self?f=json&token={urllib.parse.quote(tok)}").decode()))
    user = me.get("username")
    if not user:
        return json.dumps({"error": "token rejected by ArcGIS Online", "detail": me})
    boundary = secrets.token_hex(12)
    body = _multipart(boundary, {"f": "json", "token": tok, "title": title, "type": "GeoJson",
                                 "tags": tags, "fileName": src.name},
                      {"file": (src.name, src.read_bytes(), "application/geo+json")})
    add = json.loads(_redact(_http(f"{AGOL}/content/users/{user}/addItem", body,
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                   timeout=300).decode()))
    if not add.get("success"):
        return json.dumps({"error": "addItem failed", "detail": add})
    item_id = add["id"]
    pub = json.loads(_redact(_http(
        f"{AGOL}/content/users/{user}/publish",
        urllib.parse.urlencode({"itemId": item_id, "filetype": "geojson", "f": "json", "token": tok,
                                "publishParameters": json.dumps(
                                    {"name": re.sub(r"[^A-Za-z0-9_]", "_", title)[:90],
                                     "layerName": title[:90]})}).encode(),
        timeout=300).decode()))
    return json.dumps({"itemId": item_id, "title": title,
                       "item_url": f"https://www.arcgis.com/home/item.html?id={item_id}",
                       "publish": pub.get("success", False), "detail": pub}, ensure_ascii=False)


def _multipart(boundary: str, fields: dict, files: dict) -> bytes:
    out = b""
    for k, v in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for k, (name, data, ctype) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


# ---------------------------------------------------------------- web map template

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>HK Spatial — ArcGIS</title>
<style>
  :root{--bg:#080b12;--glass:rgba(14,20,32,.72);--line:rgba(120,160,220,.18);--fg:#e6eefc;--dim:#8fa3c0;--teal:#3fe0d0}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:13px/1.45 ui-sans-serif,-apple-system,"Segoe UI","PingFang HK","Microsoft JhengHei",sans-serif}
  #view{position:absolute;inset:0}
  #panel{position:absolute;top:14px;left:14px;z-index:10;width:286px;max-height:calc(100% - 28px);
    overflow:auto;background:var(--glass);border:1px solid var(--line);border-radius:14px;
    padding:14px 14px 10px;backdrop-filter:blur(16px) saturate(150%);
    box-shadow:0 18px 48px rgba(0,0,0,.5)}
  h1{margin:0 0 2px;font-size:14px;font-weight:650;letter-spacing:.2px}
  .sub{color:var(--dim);font-size:11px;margin-bottom:12px}
  .layer{display:flex;align-items:center;gap:9px;padding:7px 8px;border-radius:9px;cursor:pointer;
    border:1px solid transparent}
  .layer:hover{background:rgba(120,160,220,.08);border-color:var(--line)}
  .layer input{accent-color:var(--teal);margin:0;width:13px;height:13px}
  .sw{width:9px;height:9px;border-radius:3px;flex:0 0 auto}
  .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
  .ct{color:var(--dim);font-size:10px;font-variant-numeric:tabular-nums}
  .foot{margin-top:10px;padding-top:9px;border-top:1px solid var(--line);color:var(--dim);font-size:10px}
  .esri-ui .esri-attribution{position:absolute!important;bottom:0!important;right:0!important;
    left:auto!important;top:auto!important;background:rgba(8,11,18,.8)!important;color:var(--dim)!important;
    font-size:10px!important;border-radius:8px 0 0 0;padding:3px 8px!important}
  .esri-ui .esri-attribution a{color:var(--teal)!important}
</style>
</head>
<body>
<div id="view"></div>
<div id="panel">
  <h1 id="ttl"></h1>
  <div class="sub">Hong Kong CSDI data · ArcGIS Maps SDK 5</div>
  <div id="layers"></div>
  <div class="foot">Data: CSDI portal.csdi.gov.hk · Basemap: Esri World Imagery · Powered by Esri</div>
</div>
<script type="module">
import Map from "https://js.arcgis.com/5.0/@arcgis/core/Map.js";
import MapView from "https://js.arcgis.com/5.0/@arcgis/core/views/MapView.js";
import TileLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/TileLayer.js";
import GeoJSONLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/GeoJSONLayer.js";

const LAYERS = /*%LAYERS%*/;
const TITLE = /*%TITLE%*/;
const COLORS = ["#3fe0d0", "#f5a524", "#7c8cff", "#ff6b9d", "#8ce99a", "#ffd43b"];
const hex = c => [1, 3, 5].map(i => parseInt(c.substr(i, 2), 16));

document.getElementById("ttl").textContent = TITLE;
const map = new Map({ basemap: null });
map.add(new TileLayer({ url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer" }));
const view = new MapView({ container: "view", map, center: [114.1694, 22.3193], zoom: 11 });
const box = document.getElementById("layers");

LAYERS.forEach((L, i) => {
  const color = COLORS[i % COLORS.length];
  const gtype = ((L.data.features || [])[0] || {}).geometry?.type || "Point";
  const isPoint = gtype.includes("Point");
  const isLine = gtype.includes("Line");
  const symbol = isPoint
    ? { type: "simple-marker", color, size: 7, outline: { color: "#0b1018", width: 1 } }
    : isLine
      ? { type: "simple-line", color, width: 2 }
      : { type: "simple-fill", color: [...hex(color), 0.35],
          outline: { color, width: 1.2 } };
  const gl = new GeoJSONLayer({
    title: L.id, data: L.data, popupEnabled: true, outFields: ["*"],
    renderer: { type: "simple", symbol }
  });
  map.add(gl);

  const row = document.createElement("label");
  row.className = "layer";
  row.innerHTML = `<input type="checkbox" checked>
    <span class="sw" style="background:${color}"></span>
    <span class="nm" title="${L.id}">${L.name || L.id}</span>
    <span class="ct">${(L.data.features || []).length}</span>`;
  row.querySelector("input").onchange = e => { gl.visible = e.target.checked; };
  box.appendChild(row);
});

await view.when();
for (const lyr of map.layers.toArray().slice(1)) {
  const ext = lyr.fullExtent;
  if (ext && isFinite(ext.xmin)) { view.goTo(ext); break; }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- MCP wiring

def main():
    try:  # mcp 2.x renamed FastMCP -> MCPServer; keep 1.x working too
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server
    mcp = Server("hk-spatial")
    for fn in (search_hk_data, load_hk_data, build_map, arcgis_request, publish_layer):
        mcp.tool()(fn)
    mcp.run()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        import tempfile
        globals()["WORK"] = pathlib.Path(tempfile.mkdtemp(prefix="hk-spatial-test-"))
        globals()["PORT"] = 8799  # never reuse a live preview port, or the check hits another server
        hits = json.loads(search_hk_data("country park", 3))["hits"]
        assert hits, "CSDI catalogue search returned nothing"
        s = json.loads(load_hk_data(hits[0]["dataset_id"]))
        assert s.get("features", 0) > 0, s
        lo, la = s["bbox"][0], s["bbox"][1]
        assert 113.8 < lo < 114.5 and 22.1 < la < 22.6, f"projection drift: {s['bbox']}"
        out = json.loads(build_map([hits[0]["dataset_id"]], "Selftest"))
        assert out.get("url"), out
        page = _http(out["url"]).decode()
        assert "GeoJSONLayer" in page and hits[0]["dataset_id"] in page
        print("selftest OK:", s["features"], "features,", out["url"])
    else:
        main()
