#!/usr/bin/env python3
"""hk-spatial-mcp — an MCP server for Hong Kong CSDI spatial data.

One file, stdlib + `mcp`. Works with any MCP client, locally (stdio) or remotely (HTTP).
Run:  python3 hk_spatial_mcp.py            (stdio MCP server, default)
      HK_MCP_TRANSPORT=streamable-http python3 hk_spatial_mcp.py   (remote / HTTP)
      python3 hk_spatial_mcp.py --selftest
Env:
  HK_MCP_DIR          workspace dir (default ./hk-spatial)
  HK_MCP_PORT         map preview port (default 8777)
  HK_MCP_BIND         map preview bind address (default 127.0.0.1; 0.0.0.0 behind a proxy)
  HK_MCP_PUBLIC_URL   public base URL returned by build_map, e.g. https://maps.example.hk/
  HK_MCP_TRANSPORT    stdio (default) | streamable-http | sse
  HK_MCP_HOST         HTTP transport bind address (default 127.0.0.1)
  HK_MCP_HTTP_PORT    HTTP transport port (default 8788)
  ARCGIS_TOKEN        ArcGIS Online user/OAuth token (only needed for org content: publish/retrieve)
  ARCGIS_URL          portal base, default https://www.arcgis.com
"""
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

CSDI_CSW = "https://portal.csdi.gov.hk/geoportal/csw"
CSDI_FILE = "https://portal.csdi.gov.hk/csdi-webpage/file-api?dataset_id={}&format=geojson"
AGOL = os.environ.get("ARCGIS_URL", "https://www.arcgis.com").rstrip("/") + "/sharing/rest"
WORK = pathlib.Path(os.environ.get("HK_MCP_DIR") or "hk-spatial").resolve()
PORT = int(os.environ.get("HK_MCP_PORT") or 8777)
BIND = os.environ.get("HK_MCP_BIND") or "127.0.0.1"
PUBLIC = (os.environ.get("HK_MCP_PUBLIC_URL") or "").rstrip("/")

CSW_BODY = """<?xml version="1.0"?><csw:GetRecords service="CSW" version="2.0.2" maxRecords="{n}" resultType="results" outputSchema="http://www.opengis.net/cat/csw/2.0.2" xmlns:csw="http://www.opengis.net/cat/csw/2.0.2"><csw:Query typeNames="csw:Record"><csw:ElementSetName>summary</csw:ElementSetName><csw:Constraint version="1.1.0"><ogc:Filter xmlns:ogc="http://www.opengis.net/ogc"><ogc:PropertyIsLike wildCard="%" singleChar="_" escapeChar="\\"><ogc:PropertyName>AnyText</ogc:PropertyName><ogc:Literal>%{kw}%</ogc:Literal></ogc:PropertyIsLike></ogc:Filter></csw:Constraint></csw:Query></csw:GetRecords>"""


CSW_ALL = """<?xml version="1.0"?><csw:GetRecords service="CSW" version="2.0.2" maxRecords="{n}" startPosition="1" resultType="results" outputSchema="http://www.opengis.net/cat/csw/2.0.2" xmlns:csw="http://www.opengis.net/cat/csw/2.0.2"><csw:Query typeNames="csw:Record"><csw:ElementSetName>full</csw:ElementSetName></csw:Query></csw:GetRecords>"""


def _http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------- CSDI catalogue

# Hong Kong Chinese term -> catalogue wording. CSDI titles are often 洗手間 where users say 廁所.
SYNONYMS = {
    "廁所": "洗手間 toilets", "洗手間": "toilets", "公廁": "洗手間 toilets",
    "樓宇": "building 大廈", "大廈": "building 樓宇", "建築物": "building",
    "公立醫院": "hospital authority 醫院", "私家醫院": "private hospital 醫院",
    "行人路": "pedestrian footpath walkway 行人", "行人": "pedestrian 行人路",
    "巴士": "bus 巴士站", "港鐵": "mtr railway 鐵路", "渡輪": "ferry 碼頭",
    "學校": "school 學校", "醫院": "hospital 醫院", "診所": "clinic 診所",
    "垃圾": "waste refuse 廢物", "回收": "recycling 回收", "樹": "tree 樹木",
    "充電": "charging 電動車", "電動車": "electric vehicle charging 電動車",
    "雨量": "rainfall 雨量", "降雨": "rainfall 雨量", "斜坡": "slope 斜坡",
    "郊野公園": "country park 郊野公園", "海岸公園": "marine park 海岸",
    "停車場": "parking car park 停車", "海灘": "beach 泳灘", "泳灘": "beach",
    "單車": "cycling bicycle 單車徑", "渠務": "drainage sewer 渠",
    "水管": "water main 水管", "煤氣": "gas 氣體", "電纜": "cable 電纜",
    "地契": "lease 地段", "分區計劃大綱": "outline zoning plan 分區計劃大綱",
    "屋邨": "estate 屋邨", "公屋": "public housing 公營房屋", "私樓": "private housing",
    "火警": "fire 消防", "消防局": "fire station 消防局", "警署": "police 警署",
    "街市": "market 街市", "墳場": "cemetery columbarium 墳",
    "空氣": "air quality 空氣", "水質": "water quality 水質", "山泥傾瀉": "landslide 山泥傾瀉",
}


def _catalog(force: bool = False) -> list:
    """The WHOLE CSDI catalogue (1150+ datasets) in one request, cached on disk for 7 days.
    Full element set = English + 繁體/简体 titles, keywords and abstracts."""
    cache = WORK / "catalog.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    body = CSW_ALL.format(n=5000).encode()
    xml = _http(CSDI_CSW, body, {"Content-Type": "application/xml"}, timeout=300).decode("utf-8", "replace")
    out = []
    for rec in re.findall(r"<csw:Record>(.*?)</csw:Record>", xml, re.S):
        g = lambda t: re.sub(r"\s+", " ", "".join(re.findall(rf"<{t}>(.*?)</{t}>", rec, re.S))).strip()
        did = g("dc:identifier")
        if not did:
            continue
        out.append({
            "id": did, "title": g("dc:title"), "title_zh": g("dc:title_tc") or g("dc:title_sc"),
            "kw": [k.strip() for k in re.findall(r'<dc:subject scheme="keywords">(.*?)</dc:subject>', rec, re.S)],
            "abstract": g("dct:abstract")[:300], "abstract_zh": (g("dct:abstract_tc") or g("dct:abstract_sc"))[:300],
            "org": g("dc:creator"), "updated": g("dct:modified")[:10],
            "exportable": "_rcd_" in did,  # 13 metadata-only datasets 404 on export
        })
    if not out:
        raise RuntimeError("CSDI catalogue harvest returned 0 records (endpoint changed?)")
    WORK.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _score(query: str, d: dict) -> int:
    q = query.lower().strip()
    terms = [t for t in re.split(r"\s+", q) if t]
    # expand any HK term that appears INSIDE the query, not just an exact match
    # (公立醫院 -> 醫院 -> hospital; 斜坡安全 -> 斜坡 -> slope)
    for k, v in SYNONYMS.items():
        if k in q:
            terms += [w for w in v.split() if w]
    terms = list(dict.fromkeys(terms))
    title = f"{d['title']} {d['title_zh']}".lower()
    kw = " ".join(d["kw"]).lower()
    abst = f"{d['abstract']} {d['abstract_zh']}".lower()
    s = 0
    for t in terms:
        s += 8 * (t in title) + 4 * (t in kw) + 2 * (t in abst)
        s += 4 * (title.startswith(t) or title.split(" ")[0] == t)
    return s


def search_hk_data(keyword: str = "", limit: int = 10, offset: int = 0) -> str:
    """Search ALL ~1150 CSDI datasets locally (English or Chinese), ranked. No keyword = browse.

    Chinese and English both work — Chinese terms are expanded with Hong Kong usage
    (廁所 -> 洗手間, 行人路 -> pedestrian, 電動車 -> electric vehicle charging, …).
    Returns dataset_id + bilingual title + why it matched. Feed dataset_id to load_hk_data().
    """
    cat = _catalog()
    limit, offset = max(1, min(limit, 30)), max(0, offset)
    if not keyword.strip():
        page = cat[offset:offset + limit]
        return json.dumps({"total_datasets": len(cat), "offset": offset,
                           "hits": [{"dataset_id": d["id"], "title": d["title"],
                                     "title_zh": d["title_zh"], "org": d["org"]} for d in page]},
                          ensure_ascii=False)
    # shorter title breaks score ties: "Building" beats "Quality Water Supply Scheme for Buildings – …"
    ranked = sorted(((s, d) for d in cat if (s := _score(keyword, d)) > 0),
                    key=lambda kv: (-kv[0], len(kv[1]["title"]), kv[1]["title"]))
    if not ranked:  # 斜坡安全 -> 斜坡: drop trailing CJK chars until something matches
        for n in range(len(keyword.strip()) - 1, 1, -1):
            ranked = sorted(((s, d) for d in cat if (s := _score(keyword.strip()[:n], d)) > 0),
                            key=lambda kv: (-kv[0], len(kv[1]["title"]), kv[1]["title"]))
            if ranked:
                break
    hits = [{"dataset_id": d["id"], "title": d["title"], "title_zh": d["title_zh"],
             "org": d["org"], "updated": d["updated"], "score": s,
             "exportable": d["exportable"],
             "match": d["abstract"][:150] or d["abstract_zh"][:150]}
            for s, d in ranked[offset:offset + limit]]
    if not hits and offset == 0:  # nothing local -> ask the catalogue server (broad recall)
        body = CSW_BODY.format(n=limit, kw=keyword.replace("%", "").replace("'", "")).encode()
        xml = _http(CSDI_CSW, body, {"Content-Type": "application/xml"}).decode("utf-8", "replace")
        hits = [{"dataset_id": t[0], "title": re.sub(r"\s+", " ", t[1]).strip(), "source": "csw"}
                for t in ((i, x) for i, x in zip(re.findall(r"<dc:identifier>(.*?)</dc:identifier>", xml, re.S),
                                                 re.findall(r"<dc:title>(.*?)</dc:title>", xml, re.S)))]
    return json.dumps({"query": keyword, "matched": len(ranked),
                       "total_datasets": len(cat), "hits": hits,
                       "next": "load_hk_data(dataset_id) then build_map([...])"}, ensure_ascii=False)


def load_hk_data(dataset_id: str, name: str = "", refresh: bool = False) -> str:
    """Download one CSDI dataset as GeoJSON (WGS84) into the workspace. Returns a SUMMARY, not the
    data itself — never paste the features into the chat, put them on a map with build_map().
    The local copy is reused on later calls; pass refresh=True to re-download."""
    path = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', dataset_id)}.geojson"
    cached = path.exists() and not refresh
    fc = None
    if cached:
        try:
            fc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # truncated/partial file: fall through to a re-download
            cached = False
    if fc is None:
        try:
            fc = json.loads(_http(CSDI_FILE.format(urllib.parse.quote(dataset_id)), timeout=180))
        except urllib.error.HTTPError as e:
            return json.dumps({
                "error": f"{dataset_id}: HTTP {e.code} from the CSDI export endpoint",
                "likely": "metadata-only / non-spatial dataset (13 of ~1150 have no GeoJSON export), "
                          "or the id is wrong — call search_hk_data() and use a hit whose exportable is true",
                "metadata_page": f"https://portal.csdi.gov.hk/geoportal/rest/metadata/item/{dataset_id}/html",
            }, ensure_ascii=False)
    feats = fc.get("features") or []
    if not feats:
        return json.dumps({"error": f"{dataset_id}: dataset returned 0 features (not spatial or empty)"})
    if not cached:
        (WORK / "data").mkdir(parents=True, exist_ok=True)
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
        "file": str(path), "cached": cached,
        "next": "call build_map([dataset_id, ...]) to see it in the ArcGIS web map",
    }, ensure_ascii=False)



# ---------------------------------------------------------------- dataset status

def _age_days(date_str: str) -> float | None:
    """'2026-03-14' -> days since. None if the catalogue gave nothing usable."""
    try:
        return round((time.time() - time.mktime(time.strptime(date_str[:10], "%Y-%m-%d"))) / 86400, 1)
    except (ValueError, TypeError, OverflowError):
        return None


def _probe_export(dataset_id: str, timeout: int = 15) -> dict:
    """Is the CSDI export endpoint still serving THIS dataset?
    Reads 2 KB and hangs up — never pulls the whole GeoJSON just to say 'yes'."""
    url = CSDI_FILE.format(urllib.parse.quote(dataset_id))
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hk-spatial-mcp"}), timeout=timeout) as r:
            head = r.read(2048)
        ms = int((time.time() - t0) * 1000)
        if head.lstrip()[:1] in (b"{", b"["):
            return {"http": r.status, "ms": ms, "body": "geojson"}
        return {"http": r.status, "ms": ms, "body": "unexpected", "sample": head[:80].decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "ms": int((time.time() - t0) * 1000),
                "body": "error", "note": "HTTP error — likely metadata-only (no GeoJSON export) or a wrong id"}
    except Exception as e:  # timeout, DNS, TLS — all mean "not serving right now"
        return {"http": None, "ms": int((time.time() - t0) * 1000), "body": "unreachable",
                "note": f"{type(e).__name__}: {str(e)[:120]}"}


def dataset_status(dataset_id: str = "", probe: bool = False, limit: int = 10,
                   stale_after_days: int = 365) -> str:
    """Are these datasets still being served, and how stale are they?

    No dataset_id -> lists the `limit` oldest datasets in the catalogue, so you can see drift at a
    glance. probe=True also calls the CSDI export endpoint to confirm it answers right now (reads
    2 KB only, costs a round trip each — capped at 10 per call).

    Treat catalogue date and data freshness as SEPARATE things: the date below is CSDI's
    dct:modified (when the record was revised), not the statistical reference period of the data.
    """
    cat = _catalog()
    limit = max(1, min(limit, 30))

    if dataset_id:
        picked = [d for d in cat if d["id"] == dataset_id]
        if not picked:
            near = [d["id"] for d in cat if dataset_id[:12] in d["id"]][:5]
            return json.dumps({"error": f"{dataset_id} not in the catalogue",
                               "similar_ids": near,
                               "next": "search_hk_data(keyword) to get a valid dataset_id"},
                              ensure_ascii=False)
    else:
        picked = sorted(cat, key=lambda d: d["updated"] or "9999")[:limit]

    rows, probed = [], 0
    for d in picked:
        age = _age_days(d["updated"])
        f = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', d['id'])}.geojson"
        row = {
            "dataset_id": d["id"], "title": d["title"][:80], "title_zh": d["title_zh"][:40] or None,
            "org": d["org"], "catalogue_updated": d["updated"] or None,
            "age_days": age,
            "state": ("stale" if age is not None and age > stale_after_days
                      else "aging" if age is not None and age > stale_after_days / 2
                      else "fresh" if age is not None else "unknown"),
            "exportable": d["exportable"],
            "local_cache": ({"bytes": f.stat().st_size, "age_days": round((time.time() - f.stat().st_mtime) / 86400, 1)}
                            if f.exists() else None),
        }
        if probe and d["exportable"] and probed < 10:
            row["live"] = _probe_export(d["id"])
            probed += 1
        rows.append(row)

    return json.dumps({
        "checked": len(rows), "probed": probed,
        "dates_basis": "CSDI catalogue dct:modified — record revision date, NOT the data's reference period",
        "stale_after_days": stale_after_days,
        "stale": sum(1 for r in rows if r["state"] == "stale"),
        "datasets": rows,
        "next": "load_hk_data(dataset_id, refresh=True) to re-pull anything that moved",
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
            srv = ThreadingHTTPServer((BIND, PORT), h)
        except OSError:  # port already serving an earlier run — fine, nothing to do
            _serve_state["up"] = True
            return
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _serve_state["up"] = True


_serve_lock = threading.Lock()
_serve_state: dict = {}


def build_map(dataset_ids, title: str = "Hong Kong Spatial Data",
              basemap: str = "imagery", labels: bool = False) -> str:
    """Build an interactive 2D web map (ArcGIS Maps SDK, keyless basemap) from loaded datasets and
    serve it on localhost. Open the returned URL in your client's browser or preview pane."""
    return _build(dataset_ids, title, basemap, labels, mode="2d", page="index.html")


def build_scene(dataset_ids, title: str = "Hong Kong Spatial Data",
                basemap: str = "imagery", labels: bool = False,
                tilt: float = 60) -> str:
    """Same datasets, rendered in a 3D scene: terrain relief, tilted camera, layers draped on the
    ground. Keyless (public imagery + Terrain3D elevation). Use it when relief, height or a
    walk-through view matters — towers, slopes, trails, coastal context."""
    return _build(dataset_ids, title, basemap, labels, mode="3d", page="scene.html", tilt=tilt)


def _slug(title: str, ids) -> str:
    """Filesystem-safe, build-specific page name so two maps can coexist."""
    base = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:40].strip("-") or "map"
    return base + "-" + hashlib.sha1(("|".join(map(str, ids)) + str(title)).encode()).hexdigest()[:6]


def _build(dataset_ids, title: str, basemap: str, labels: bool, mode: str, page: str,
           tilt: float = 60) -> str:
    """Shared builder for the 2D map (index.html) and the 3D scene (scene.html)."""
    if isinstance(dataset_ids, str):
        dataset_ids = [dataset_ids]
    layers, missing, warn = [], [], []
    for did in dataset_ids:
        p = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', did)}.geojson"
        if not p.exists():
            r = json.loads(load_hk_data(did))
            if r.get("error"):
                missing.append(r["error"])
                continue
            p = pathlib.Path(r["file"])
        label = p.with_suffix(".name")
        fc = json.loads(p.read_text(encoding="utf-8"))
        feats = fc.get("features") or []
        gtype = ((feats[0].get("geometry") or {}).get("type") or "Point") if feats else "Point"
        # Every layer is served from disk and referenced by URL. GeoJSONLayer with an inline `blob`
        # fails to load on SDK 5.0 ("Cannot read properties of null (reading 'type')"), and urls keep
        # the HTML small for big layers — one code path, no size threshold.
        props = (feats[0].get("properties") or {}) if feats else {}
        fields = [k for k in props if not k.startswith("__")][:20]
        # a sensible popup title: prefer a name/location label, else the dataset title
        title_field = next((k for k in fields if k.upper() in
                            ("NAME_EN", "NAME", "NAME_TC", "TITLE", "FACILITY_NAME", "LOCATION_EN")), "")
        entry = {"id": did, "name": label.read_text(encoding="utf-8") if label.exists() else did,
                 "url": f"data/{p.name}", "count": len(feats), "gtype": gtype,
                 "fields": fields, "title_field": title_field}
        if p.stat().st_size > 60_000_000:  # honest warning, never silently sample the data
            warn.append(f"{did} is {p.stat().st_size // 1_000_000} MB / "
                        f"{len(feats)} features — the browser will be slow; "
                        f"pick a smaller or district-level dataset for a live demo")
        layers.append(entry)
    if not layers:
        return json.dumps({"error": "nothing to map", "details": missing})
    svc = BASEMAPS.get(str(basemap).lower())
    if not svc:
        return json.dumps({"error": f"unknown basemap {basemap!r}", "basemaps": sorted(BASEMAPS)})
    html = TEMPLATE.replace("/*%MODE%*/", json.dumps(mode))
    html = html.replace("/*%TILT%*/", json.dumps(float(tilt)))
    html = html.replace("/*%ELEV_URL%*/", json.dumps(f"{TERRAIN}"))
    html = html.replace("/*%BASEMAP_URL%*/",
                        json.dumps(f"{TILES}/{svc}/MapServer" if svc else ""))
    html = html.replace("/*%LABELS_URL%*/",
                        json.dumps(f"{TILES}/{LABELS_LAYER}/MapServer" if labels else ""))
    html = html.replace("/*%TITLE%*/", json.dumps(title, ensure_ascii=False))
    html = html.replace("/*%LAYERS%*/", json.dumps(layers, ensure_ascii=False))
    WORK.mkdir(parents=True, exist_ok=True)
    # A build must never destroy an earlier map: keep a stable per-build page, and refresh the
    # conventional name so old links (and the newest build) still land somewhere sensible.
    slug_page = f"{mode}-{_slug(title, [l['id'] for l in layers])}.html"
    (WORK / slug_page).write_text(html, encoding="utf-8")
    (WORK / page).write_text(html, encoding="utf-8")
    _serve()
    base = (PUBLIC + "/" if PUBLIC else f"http://localhost:{PORT}/")
    return json.dumps({"url": base + slug_page,
                       "latest_url": base + (page if page != "index.html" else ""),
                       "mode": mode,
                       "layers": [l["id"] for l in layers],
                       "features": sum(l.get("count", 0) for l in layers),
                       "missing": missing, "warn": warn,
                       "next": "open the url in your client's browser/preview pane (refresh to see changes)"})


# ------------------------------------------------- ArcGIS public services (no key)

GEOM_SRV = "https://utility.arcgisonline.com/arcgis/rest/services/Geometry/GeometryServer"
GEOCODER = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer"
TERRAIN = "https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer"
TILES = "https://server.arcgisonline.com/ArcGIS/rest/services"
# every basemap below answers without a key or token (verified 2026-10-22)
BASEMAPS = {
    "imagery": "World_Imagery", "topographic": "World_Topo_Map", "streets": "World_Street_Map",
    "light-gray": "Canvas/World_Light_Gray_Base", "dark-gray": "Canvas/World_Dark_Gray_Base",
    "terrain": "World_Terrain_Base", "hillshade": "Elevation/World_Hillshade",
    "ocean": "Ocean/World_Ocean_Base", "physical": "World_Physical_Map",
    "natgeo": "NatGeo_World_Map", "shaded-relief": "World_Shaded_Relief",
}
LABELS_LAYER = "Reference/World_Boundaries_and_Places"
UNITS = {"meters": 9001, "kilometers": 9036, "feet": 9002, "miles": 9093, "nauticalmiles": 9030}


def _gj_to_esri(g: dict) -> dict:
    t, c = g.get("type"), g.get("coordinates")
    if t == "Point":
        return {"geometryType": "esriGeometryPoint", "geometry": {"x": c[0], "y": c[1]}}
    if t in ("LineString", "MultiLineString"):
        return {"geometryType": "esriGeometryPolyline",
                "geometry": {"paths": [c] if t == "LineString" else c}}
    if t in ("Polygon", "MultiPolygon"):
        return {"geometryType": "esriGeometryPolygon",
                "geometry": {"rings": c if t == "Polygon" else [r for poly in c for r in poly]}}
    if t == "MultiPoint":
        return {"geometryType": "esriGeometryMultipoint", "geometry": {"points": c}}
    raise ValueError(f"unsupported GeoJSON type {t!r}: use Point, MultiPoint, LineString, MultiLineString, Polygon or MultiPolygon")


def _esri_to_gj(item: dict) -> dict:
    if "x" in item:
        return {"type": "Point", "coordinates": [item["x"], item["y"]]}
    if "points" in item:
        return {"type": "MultiPoint", "coordinates": item["points"]}
    if "paths" in item:
        return {"type": "MultiLineString", "coordinates": item["paths"]}
    if "rings" in item:
        return {"type": "Polygon", "coordinates": item["rings"]}
    return {"type": "Unknown", "raw": item}


def geocode_address(single_line: str = "", max_results: int = 5,
                    lon: float | None = None, lat: float | None = None) -> str:
    """Geocode an address/place to coordinates, or reverse-geocode a coordinate to an address.
    Esri public world geocoder — no key required. Reverse mode: pass `lon` and `lat`."""
    if lon is not None and lat is not None:
        params = {"f": "json", "location": f"{lon},{lat}", "outSR": 4326}
        raw = json.loads(_http(GEOCODER + "/reverseGeocode?" + urllib.parse.urlencode(params), timeout=60))
        addr = raw.get("address") or {}
        return json.dumps({"lon": lon, "lat": lat,
                           "address": addr.get("LongLabel") or addr.get("Match_addr"),
                           "short": addr.get("ShortLabel"), "type": addr.get("Addr_type")},
                          ensure_ascii=False)
    params = {"f": "json", "SingleLine": single_line,
              "maxLocations": max(1, min(int(max_results), 20)), "outSR": 4326}
    raw = json.loads(_http(GEOCODER + "/findAddressCandidates?" + urllib.parse.urlencode(params),
                           timeout=60))
    cands = [{"address": c.get("address"), "lon": (c.get("location") or {}).get("x"),
              "lat": (c.get("location") or {}).get("y"), "score": c.get("score")}
             for c in raw.get("candidates") or []]
    seen, unique = set(), []
    for c in cands:  # the same place comes back twice with slightly different labels
        key = (round(c["lon"] or 0, 5), round(c["lat"] or 0, 5), c["score"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    cands = unique
    if not cands:
        return json.dumps({"query": single_line, "candidates": [],
                           "note": "no candidate — try a fuller address, or romanised HK place names"})
    return json.dumps({"query": single_line, "candidates": cands,
                       "source": "Esri World Geocoder (public endpoint, rate-limited)"},
                      ensure_ascii=False)


def geometry_op(operation: str, geojson, in_sr: int = 4326, out_sr: int | None = None,
                distance: float = 500, unit: str = "meters",
                max_deviation: float | None = None) -> str:
    """Project, buffer or simplify one GeoJSON geometry with Esri's public Geometry service — no key.

    operation: "project" (in_sr -> out_sr, e.g. 2326 -> 4326), "buffer" (distance in `unit`),
    "simplify" (max_deviation in the input SR's units).
    """
    try:
        esri = _gj_to_esri(geojson if isinstance(geojson, dict) else json.loads(geojson))
    except Exception as e:
        return json.dumps({"error": str(e)})
    out_sr = out_sr or in_sr
    payload = json.dumps({"geometryType": esri["geometryType"], "geometries": [esri["geometry"]]})
    if operation == "project":
        path, params = "project", {"inSR": in_sr, "outSR": out_sr, "geometries": payload}
    elif operation == "buffer":
        path, params = "buffer", {"inSR": in_sr, "outSR": out_sr, "unit": UNITS.get(unit.lower(), 9001),
                                  "distances": distance, "geometries": payload}
    elif operation == "simplify":
        path, params = "simplify", {"sr": in_sr, "maxDeviation": max_deviation or 0.0001,
                                    "geometries": payload}
    else:
        return json.dumps({"error": f"unknown operation {operation!r}: use project, buffer or simplify"})
    params["f"] = "json"
    raw = json.loads(_http(f"{GEOM_SRV}/{path}?" + urllib.parse.urlencode(params), timeout=90))
    if "error" in raw:
        return json.dumps({"operation": operation, "error": raw["error"]})
    out = _esri_to_gj((raw.get("geometries") or [{}])[0])
    return json.dumps({"operation": operation, "in_sr": in_sr, "out_sr": out_sr, "geometry": out},
                      ensure_ascii=False)


def elevation_at(lon: float, lat: float) -> str:
    """Ground elevation in metres at a coordinate, from Esri's public Terrain3D service — no key.
    Handy for hiking/relief context (e.g. trail comfort, slope warnings) alongside CSDI layers."""
    params = {"f": "json", "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
              "geometryType": "esriGeometryPoint", "sr": 4326, "returnCatalogItems": "false"}
    raw = json.loads(_http(TERRAIN + "/identify?" + urllib.parse.urlencode(params), timeout=60))
    if "error" in raw:
        return json.dumps({"lon": lon, "lat": lat, "error": raw["error"]})
    return json.dumps({"lon": lon, "lat": lat, "elevation_m": float(raw.get("value", "nan"))})



def _merc(lon: float, lat: float, z: int):
    """Web-mercator pixel coordinates at zoom z (256 px tiles)."""
    scale = 256 * (2 ** z)
    x = (lon + 180.0) / 360.0 * scale
    s = math.sin(math.radians(max(min(lat, 85.05), -85.05)))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale
    return x, y


def export_static_svg(dataset_ids, title: str = "Hong Kong", basemap: str = "streets",
                      max_px: int = 1400) -> str:
    """Write a printable static map WITH the data drawn, as one self-contained SVG page.

    Basemap tiles are referenced by URL (they load in any browser with internet), features are drawn as
    real vector shapes on top — so the result prints cleanly to PDF from the browser's print dialog.
    No key, no headless browser, no image library.
    """
    svc = BASEMAPS.get(str(basemap).lower())
    if not svc:
        return json.dumps({"error": f"unknown basemap {basemap!r}", "basemaps": sorted(BASEMAPS)})
    if isinstance(dataset_ids, str):
        dataset_ids = [dataset_ids]
    layers = []
    for did in dataset_ids:
        feats, err = _cached_features(did)
        if err or not feats:
            continue
        pts = [p for f in feats for p in _coords(f.get("geometry") or {})]
        if pts:
            namefile = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', did)}.name"
            label = namefile.read_text(encoding="utf-8") if namefile.exists() else did
            layers.append({"id": did, "name": label, "features": feats, "pts": pts})
    if not layers:
        return json.dumps({"error": "nothing to draw", "datasets": dataset_ids})
    lons = [p[0] for l in layers for p in l["pts"]]
    lats = [p[1] for l in layers for p in l["pts"]]
    pad = 0.02
    w, s_, e, n = min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad
    z = 16
    while z > 4:  # biggest zoom whose pixel span still fits the requested image size
        x0, y1 = _merc(w, s_, z)
        x1, y0 = _merc(e, n, z)
        if max(x1 - x0, y1 - y0) <= max_px:
            break
        z -= 1
    x0, y1 = _merc(w, s_, z)
    x1, y0 = _merc(e, n, z)
    W, H = int(x1 - x0), int(y1 - y0)
    tiles = []
    for tx in range(int(x0 // 256), int(x1 // 256) + 1):
        for ty in range(int(y0 // 256), int(y1 // 256) + 1):
            tiles.append(f'<image x="{tx * 256 - x0:.0f}" y="{ty * 256 - y0:.0f}" width="256" '
                         f'height="256" href="{TILES}/{svc}/MapServer/tile/{z}/{ty}/{tx}"/>')
    colours = ["#00e5c0", "#ffb020", "#7c8cff", "#ff6b9d", "#8ce99a", "#ffd43b"]
    shapes = []
    for i, layer in enumerate(layers):
        col = colours[i % len(colours)]
        for f in layer["features"]:
            g = f.get("geometry") or {}
            t, c = g.get("type"), g.get("coordinates")
            if not c:
                continue
            if t == "Point":
                px, py = _merc(c[0], c[1], z)
                shapes.append(f'<circle cx="{px - x0:.1f}" cy="{py - y0:.1f}" r="4.5" fill="{col}" '
                              f'stroke="#08121a" stroke-width="1"/>')
            elif t in ("LineString", "MultiLineString"):
                for path in ([c] if t == "LineString" else c):
                    pts = " ".join(f"{_merc(a, b, z)[0] - x0:.1f},{_merc(a, b, z)[1] - y0:.1f}"
                                   for a, b in path)
                    shapes.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                                  f'stroke-width="2" stroke-opacity=".9"/>')
            elif t in ("Polygon", "MultiPolygon"):
                for poly in ([c] if t == "Polygon" else c):
                    for ring in poly:
                        pts = " ".join(f"{_merc(a, b, z)[0] - x0:.1f},{_merc(a, b, z)[1] - y0:.1f}"
                                       for a, b in ring)
                        shapes.append(f'<polygon points="{pts}" fill="{col}" fill-opacity=".22" '
                                      f'stroke="{col}" stroke-width="1.2"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}">'
           + "".join(tiles) + "".join(shapes)
           + f'<g font-family="Helvetica,Arial,sans-serif"><rect x="14" y="14" rx="8" width="330" '
             f'height="{24 + 20 * len(layers)}" fill="#0b1018" fill-opacity=".82"/>'
           + f'<text x="26" y="36" fill="#eaf0f7" font-size="14" font-weight="bold">'
             f'{_esc(title)[:46]}</text>'
           + "".join(
               f'<rect x="26" y="{52 + 20 * i}" width="10" height="10" rx="2" '
               f'fill="{colours[i % len(colours)]}"/>'
               f'<text x="42" y="{61 + 20 * i}" fill="#cfe0f0" font-size="11">'
               f'{_esc(layer["name"])[:34]} · {len(layer["features"])} features</text>'
               for i, layer in enumerate(layers))
           + f'<text x="26" y="{40 + 20 * len(layers) + 4}" fill="#7f93a8" font-size="9">'
             f'Data: CSDI portal.csdi.gov.hk · Basemap: Esri · Powered by Esri</text></g></svg>')
    (WORK / "exports").mkdir(parents=True, exist_ok=True)
    name = f"static-{_slug(title, [l['id'] for l in layers])}.svg"
    (WORK / "exports" / name).write_text(svg, encoding="utf-8")
    _serve()
    base = (PUBLIC + "/exports/") if PUBLIC else f"http://localhost:{PORT}/exports/"
    return json.dumps({"file": str(WORK / "exports" / name), "url": base + name,
                       "features": sum(len(l["features"]) for l in layers), "zoom": z,
                       "size_px": [W, H], "layers": [l["id"] for l in layers],
                       "note": "vector SVG: open it and print to PDF for a report; basemap tiles load from "
                               "the internet when the page is opened"}, ensure_ascii=False)


def export_static_map(bbox: str | list | None = None, center: str | list | None = None,
                      zoom: int = 14, basemap: str = "streets", size: str = "900,600") -> str:
    """Render a basemap-only PNG (no key) into the workspace — for reports, decks and docs.
    Give either `bbox` ("minx,miny,maxx,maxy" in EPSG:4326) or `center` + `zoom`.
    NOTE: CSDI features are NOT drawn on this image; use build_map for the interactive version,
    or an ArcGIS Online print service (needs ARCGIS_TOKEN) for data-on-basemap statics."""
    svc = BASEMAPS.get(str(basemap).lower())
    if not svc:
        return json.dumps({"error": f"unknown basemap {basemap!r}", "basemaps": sorted(BASEMAPS)})
    if bbox is None:
        if center is None:
            return json.dumps({"error": "give bbox or center (web-mercator bbox from center+zoom)"})
        c = [float(v) for v in (center.split(",") if isinstance(center, str) else center)]
        w_px, h_px = (int(v) for v in str(size).split(","))
        # web-mercator ground resolution at this latitude, then pixels -> degrees
        res = 156543.03392 * math.cos(math.radians(c[1])) / (2 ** int(zoom))
        dlon = w_px * res / (111320.0 * math.cos(math.radians(c[1])))
        dlat = h_px * res / 111320.0
        bbox = f"{c[0] - dlon / 2},{c[1] - dlat / 2},{c[0] + dlon / 2},{c[1] + dlat / 2}"
    bbox_s = bbox if isinstance(bbox, str) else ",".join(str(v) for v in bbox)
    w, h = (int(v) for v in str(size).split(","))
    params = {"bbox": bbox_s, "bboxSR": 4326, "imageSR": 4326, "size": f"{w},{h}",
              "format": "png32", "f": "image"}
    png = _http(f"{TILES}/{svc}/MapServer/export?" + urllib.parse.urlencode(params), timeout=120)
    if not png.startswith(b"\x89PNG"):
        return json.dumps({"error": "export did not return a PNG", "basemap": basemap})
    (WORK / "exports").mkdir(parents=True, exist_ok=True)
    out = WORK / "exports" / f"{svc.replace('/', '_')}_{int(time.time())}.png"
    out.write_bytes(png)
    _serve()
    base = PUBLIC or f"http://localhost:{PORT}"
    return json.dumps({"file": str(out), "url": f"{base}/exports/{out.name}",
                       "bytes": len(png), "bbox": bbox_s, "basemap": basemap,
                       "note": "basemap only — CSDI features are not drawn on this image"})



def query_hk_data(dataset_id: str, where: str = "", fields: str = "", limit: int = 5) -> str:
    """Read a FEW individual features, so a single value can be verified without opening the GeoJSON.

    `where` is either `FIELD=value` (case-insensitive match on one field) or bare text searched across all
    fields. Rows are capped at 20 — this tool exists to check a value, not to dump a dataset; use
    `summarise_hk_data` for aggregates and `build_map` to see the layer.
    """
    feats, err = _cached_features(dataset_id)
    if err:
        return json.dumps(err, ensure_ascii=False)
    if not feats:
        return json.dumps({"error": f"{dataset_id}: no features"})
    cap = max(1, min(int(limit), 20))
    field_list = [k for k in ((feats[0].get("properties") or {})) if not k.startswith("__")]
    cond = (where or "").strip()
    field_name, needle = "", cond.lower()
    if "=" in cond:
        field_name, _, needle = [s.strip() for s in cond.partition("=")]
        if field_name not in field_list:
            return json.dumps({"error": f"{dataset_id}: no field {field_name!r}", "fields": field_list[:25]},
                              ensure_ascii=False)
        needle = needle.lower()

    def matches(row):
        if field_name:
            return needle in str(row.get(field_name) or "").lower()
        return any(needle in str(v).lower() for v in row.values() if v is not None)

    rows = [f for f in feats if matches(f.get("properties") or {})]
    keep = [s.strip() for s in fields.split(",") if s.strip()] or field_list[:12]
    out = []
    for f in rows[:cap]:
        props = {k: v for k, v in (f.get("properties") or {}).items() if k in keep and not k.startswith("__")}
        geom = f.get("geometry") or {}
        c = geom.get("coordinates")
        while isinstance(c, list) and c and isinstance(c[0], list):  # first vertex, any geometry type
            c = c[0]
        props["_geometry"] = geom.get("type")
        props["_first_vertex"] = c if isinstance(c, list) else None
        out.append(props)
    return json.dumps({"dataset_id": dataset_id, "where": where or "(all)", "matched": len(rows),
                       "returned": len(out), "capped_at": cap, "rows": out,
                       "note": "values are raw as published by CSDI; no unit metadata in the GeoJSON"},
                      ensure_ascii=False)


# ---------------------------------------------------------------- answers in text + charts

def _cached_features(dataset_id: str):
    """Features of a dataset, from the local cache (downloads only if missing)."""
    p = WORK / "data" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', dataset_id)}.geojson"
    if not p.exists():
        r = json.loads(load_hk_data(dataset_id))
        if r.get("error"):
            return None, r
    return json.loads(p.read_text(encoding="utf-8")).get("features") or [], None


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def summarise_hk_data(dataset_id: str, group_by: str = "", value_field: str = "",
                      agg: str = "count", top: int = 20) -> str:
    """Answer a question about a dataset in TEXT (and a bar chart), without putting features in the chat.

    - `group_by` empty: an overview — feature count, geometry, which fields are worth grouping by.
    - `group_by="NAME_OF_DISTRICT_COUNCIL_DISTRICT_EN"` with `agg="count"`: how many features per group.
    - `agg="sum"` / `"avg"` with `value_field="MEDIUM_IEC62196_no"`: totals or averages per group.
    Returns an aligned text table, the same rows as data, and a chart URL served on localhost.
    """
    feats, err = _cached_features(dataset_id)
    if err:
        return json.dumps(err, ensure_ascii=False)
    if not feats:
        return json.dumps({"error": f"{dataset_id}: no features to summarise"})
    props = [f.get("properties") or {} for f in feats]
    geoms = sorted({(f.get("geometry") or {}).get("type") or "?" for f in feats})

    if not group_by:
        counts = {}
        for row in props:
            for k, v in row.items():
                if k.startswith("__") or v in (None, ""):
                    continue
                counts.setdefault(k, set()).add(str(v))
        # text-like fields first: grouping by a numeric column ("0", "1", …) is rarely the question
        groupable = sorted(
            ((k, len(s)) for k, s in counts.items()
             if 2 <= len(s) <= 60 and not all(str(v).startswith("http") for v in counts[k])),
            key=lambda kv: (all(_num(v) is not None for v in counts[kv[0]]), kv[1]))[:10]
        numeric = sorted({k for row in props for k, v in row.items()
                          if not k.startswith("__") and _num(v) is not None})
        return json.dumps({
            "dataset_id": dataset_id, "features": len(feats), "geometry": geoms,
            "groupable_fields": [{"field": k, "distinct": n} for k, n in groupable],
            "numeric_fields": numeric[:12],
            "next": "call again with group_by=<field> (and optionally agg/value_field) for counts, "
                    "sums or averages with a chart",
        }, ensure_ascii=False)

    if group_by not in props[0]:
        return json.dumps({"error": f"{dataset_id}: no field {group_by!r}",
                           "fields": list(props[0].keys())[:25]}, ensure_ascii=False)
    buckets: dict = {}
    for row in props:
        key = str(row.get(group_by) or "(blank)")
        val = _num(row.get(value_field)) if value_field else None
        b = buckets.setdefault(key, [0, 0.0])
        b[0] += 1
        if val is not None:
            b[1] += val
    mode = agg if agg in ("count", "sum", "avg") else ("sum" if value_field else "count")
    rows = []
    for key, (n, total) in buckets.items():
        if mode == "count":   # one number per row: count and value were the same thing
            rows.append({"group": key, "count": n})
            continue
        rows.append({"group": key, "count": n,
                     "value": round(total if mode == "sum" else (total / n if n else 0), 2)})
    rows.sort(key=lambda r: -(r.get("value") if mode != "count" else r["count"]))
    rows = rows[:max(1, int(top))]
    label = {"count": "features", "sum": f"sum of {value_field}", "avg": f"avg of {value_field}"}[mode]
    w = min(28, max([len(r["group"]) for r in rows] + [len("group")]))
    table = [f"{'group':<{w}}  {label:>18}", "-" * (w + 20)]
    for r in rows:
        v = r["count"] if mode == "count" else r["value"]
        table.append(f"{r['group'][:w]:<{w}}  {v:>18,.2f}".replace(".00", ""))
    chart_url = _chart_page(f"{dataset_id} — {label} by {group_by}", rows, w)
    return json.dumps({"dataset_id": dataset_id, "group_by": group_by, "aggregation": label,
                       "groups": len(buckets), "rows": rows, "table": "\n".join(table),
                       "chart_url": chart_url,
                       "basis": f"{len(feats)} features from the local cache"}, ensure_ascii=False)


def _chart_page(title: str, rows, width_chars: int) -> str:
    """One SVG bar chart page, no dependency. ponytail: bars only — swap in a chart lib when a
    client needs more than one chart type."""
    W, row_h, pad, left = 860, 30, 24, 300
    h = pad * 2 + row_h * len(rows) + 40
    top = max((r.get("value", r.get("count", 0)) for r in rows), default=1) or 1
    colours = ["#3fe0d0", "#f5a524", "#7c8cff", "#ff6b9d", "#8ce99a", "#ffd43b", "#6ee7f9"]
    bars = []
    for i, r in enumerate(rows):
        y = pad + 40 + i * row_h
        # leave room for the value label, otherwise the longest bar pushes it off the canvas
        val = r.get("value", r.get("count", 0))
        bw = max(2, int((W - left - pad - 90) * (val / top)))
        bars.append(
            f'<text x="{left - 10}" y="{y + 15}" text-anchor="end" fill="#c9d5e3" '
            f'font-size="12">{_esc(str(r["group"])[:34])}</text>'
            f'<rect x="{left}" y="{y + 3}" width="{bw}" height="{row_h - 10}" rx="4" '
            f'fill="{colours[i % len(colours)]}" opacity=".85"/>'
            f'<text x="{left + bw + 8}" y="{y + 15}" fill="#8fa3ba" font-size="11">'
            f'{val:,.0f}</text>')
    svg = (f'<svg viewBox="0 0 {W} {h}" width="100%" style="max-width:{W}px" '
           f'xmlns="http://www.w3.org/2000/svg">'
           f'<text x="{pad}" y="{pad + 14}" fill="#eaf0f7" font-size="15" font-weight="600">'
           f'{_esc(title)[:78]}{"…" if len(_esc(title)) > 78 else ""}</text>'
           + "".join(bars) + "</svg>")
    html = ("<!doctype html><meta charset=utf-8><title>" + _esc(title) + "</title>"
            "<body style='margin:0;background:#0b1018;color:#eaf0f7;"
            "font:13px/1.5 ui-sans-serif,system-ui,sans-serif'>"
            f"<div style='padding:22px'>{svg}"
            "<p style='color:#5f7185;font-size:11px'>Data: CSDI portal.csdi.gov.hk · "
            "Powered by Esri</p></div>")
    name = f"chart-{_slug(title, [len(rows)])}.html"
    (WORK / name).write_text(html, encoding="utf-8")
    (WORK / "chart.html").write_text(html, encoding="utf-8")
    _serve()
    return (PUBLIC + f"/{name}") if PUBLIC else f"http://localhost:{PORT}/{name}"


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------------------------------------------------------- ArcGIS Online

NO_TOKEN = ("ARCGIS_TOKEN is not set. Publishing and org-content reads need an ArcGIS Online "
            "user/OAuth token (an API key only covers basemaps and geocoding). Put it in this MCP "
            "server's env block in your client's config. Everything else — search, load, map, "
            "geocode, geometry, elevation, static export — works without it.")


def _token() -> str:
    """Empty string when no token is configured — never raises, so tools can answer in JSON."""
    return (os.environ.get("ARCGIS_TOKEN") or "").strip()


def _no_token(tool: str) -> str:
    return json.dumps({"error": f"{tool}: {NO_TOKEN}", "tool": tool, "needs": "ARCGIS_TOKEN"},
                      ensure_ascii=False)


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
    if not _token():
        return _no_token("arcgis_request")
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
    if not _token():
        return _no_token("publish_layer")
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
<link rel="icon" href="data:," />
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
  #info{position:absolute;left:14px;bottom:14px;z-index:10;width:330px;max-height:44vh;overflow:auto;
    background:var(--glass);border:1px solid var(--line);border-radius:14px;padding:12px 14px;
    backdrop-filter:blur(16px) saturate(150%);box-shadow:0 18px 44px rgba(0,0,0,.45)}
  #info[hidden]{display:none}
  #info .ih{font-weight:600;font-size:13px;color:var(--fg);margin-bottom:8px;padding-bottom:7px;
    border-bottom:1px solid var(--line)}
  #info .ir{display:flex;gap:10px;padding:2.5px 0}
  #info .ik{flex:0 0 42%;color:var(--dim);font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;
    word-break:break-word}
  #info .iv{flex:1;color:var(--fg);word-break:break-word;font-size:12px}
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
  <div class="sub">Hong Kong CSDI data · ArcGIS Maps SDK 5 · <span id="mode"></span></div>
  <div id="layers"></div>
  <div class="foot">Data: CSDI portal.csdi.gov.hk · Basemap: Esri · Powered by Esri</div>
</div>
<div id="info" hidden></div>
<script type="module">
import Map from "https://js.arcgis.com/5.0/@arcgis/core/Map.js";
import MapView from "https://js.arcgis.com/5.0/@arcgis/core/views/MapView.js";
import SceneView from "https://js.arcgis.com/5.0/@arcgis/core/views/SceneView.js";
import ElevationLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/ElevationLayer.js";
import TileLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/TileLayer.js";
import GeoJSONLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/GeoJSONLayer.js";

const LAYERS = /*%LAYERS%*/;
const TITLE = /*%TITLE%*/;
const BASEMAP_URL = /*%BASEMAP_URL%*/;
const LABELS_URL = /*%LABELS_URL%*/;
const MODE = /*%MODE%*/;
const TILT = /*%TILT%*/;
const ELEV_URL = /*%ELEV_URL%*/;
const COLORS = ["#3fe0d0", "#f5a524", "#7c8cff", "#ff6b9d", "#8ce99a", "#ffd43b"];
const hex = c => [1, 3, 5].map(i => parseInt(c.substr(i, 2), 16));

document.getElementById("ttl").textContent = TITLE;
document.getElementById("mode").textContent =
  (MODE === "3d" ? "3D scene" : "2D map") + " · " + LAYERS.length + " layer" + (LAYERS.length > 1 ? "s" : "");
const map = new Map({ basemap: null });
if (BASEMAP_URL) map.add(new TileLayer({ url: BASEMAP_URL }));
if (LABELS_URL) map.add(new TileLayer({ url: LABELS_URL }));
if (MODE === "3d") map.ground = { layers: [new ElevationLayer({ url: ELEV_URL })] };

const HK = [114.1694, 22.3193];
const view = MODE === "3d"
  ? new SceneView({
      container: "view", map, zoom: 12,
      camera: { position: { x: HK[0], y: HK[1], z: 2400, spatialReference: { wkid: 4326 } },
                tilt: TILT, heading: 12 },
    })
  : new MapView({ container: "view", map, center: HK, zoom: 11 });
window.__view = view;  // exposed for tests/debugging
view.when(() => { window.__readyMs = Math.round(performance.now()); }).catch(() => {});

// click a feature -> attribute card (bottom left)
const info = document.getElementById("info");
const PRETTY = { NAME_EN: "Name (EN)", NAME_TC: "名稱（中文）", LOCATION_EN: "Location (EN)",
                 LOCATION_TC: "位置（中文）", WEBSITE: "Website", EASTING: "Easting (HK80)",
                 NORTHING: "Northing (HK80)" };
view.on("click", async (event) => {
  try {
    const hit = (await view.hitTest(event)).results.find(r => r.graphic && r.graphic.layer);
    if (!hit) { info.hidden = true; return; }
    const a = hit.graphic.attributes || {};
    const rows = Object.keys(a)
      .filter(k => !k.startsWith("__") && a[k] !== null && a[k] !== "")
      .slice(0, 24)
      .map(k => {
        const label = PRETTY[k] || k;
        const v = /^https?:\/\//.test(String(a[k]))
          ? `<a href="${a[k]}" target="_blank" rel="noopener" style="color:#3fe0d0">link</a>`
          : String(a[k]);
        return `<div class="ir"><span class="ik">${label}</span><span class="iv">${v}</span></div>`;
      }).join("");
    const heading = a.NAME_TC && a.NAME_EN ? `${a.NAME_EN} · ${a.NAME_TC}` : (hit.graphic.layer.title || "");
    info.innerHTML = `<div class="ih">${heading}</div>${rows}`;
    info.hidden = false;
    window.__info = a;  // exposed for tests
  } catch (e) { info.hidden = true; }
});
const box = document.getElementById("layers");

LAYERS.forEach((L, i) => {
  const color = COLORS[i % COLORS.length];
  const gtype = L.gtype || "Point";
  const isPoint = gtype.includes("Point");
  const isLine = gtype.includes("Line");
  const symbol = isPoint
    ? { type: "simple-marker", color, size: 7, outline: { color: "#0b1018", width: 1 } }
    : isLine
      ? { type: "simple-line", color, width: 2 }
      : { type: "simple-fill", color: [...hex(color), 0.35],
          outline: { color, width: 1.2 } };
  // ponytail: no SDK popup. In 5.0 the popup UI is a beta component that a programmatic View does
  // not inject, and popupEnabled alone opens nothing. One click handler + one card is smaller,
  // version-proof, and looks intentional.
  const opts = { title: L.name || L.id, url: L.url, popupEnabled: false, outFields: ["*"],
                 renderer: { type: "simple", symbol } };
  // in 3D, drape lines and polygons onto the terrain instead of floating at sea level
  if (MODE === "3d" && !isPoint) opts.elevationInfo = { mode: "on-the-ground" };
  const gl = new GeoJSONLayer(opts);
  map.add(gl);

  const row = document.createElement("label");
  row.className = "layer";
  row.innerHTML = `<input type="checkbox" checked>
    <span class="sw" style="background:${color}"></span>
    <span class="nm" title="${L.id}">${L.name || L.id}</span>
    <span class="ct">${L.count}</span>`;
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
    for fn in (search_hk_data, load_hk_data, query_hk_data, summarise_hk_data, build_map, build_scene,
               geocode_address, geometry_op, elevation_at, export_static_map, export_static_svg,
               arcgis_request, publish_layer, dataset_status):
        mcp.tool()(fn)
    transport = (os.environ.get("HK_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return
    host = os.environ.get("HK_MCP_HOST") or "127.0.0.1"
    port = int(os.environ.get("HK_MCP_HTTP_PORT") or 8788)
    if hasattr(mcp, "settings"):  # mcp 1.x FastMCP reads bind details from mcp.settings
        for attr, val in (("host", host), ("port", port)):
            try:
                setattr(mcp.settings, attr, val)
            except Exception:
                pass  # mcp 2.x validates settings; host/port come from run() kwargs
    try:
        mcp.run(transport=transport, host=host, port=port)
    except TypeError:  # server reads settings/env instead of kwargs
        mcp.run(transport=transport)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        import tempfile
        globals()["WORK"] = pathlib.Path(tempfile.mkdtemp(prefix="hk-spatial-test-"))
        globals()["PORT"] = 8799  # never reuse a live preview port, or the check hits another server
        cat = _catalog()
        assert len(cat) > 1100, f"catalogue only {len(cat)} datasets — harvest incomplete"
        assert any(d["exportable"] for d in cat), "no exportable datasets in catalogue"
        for q, want in [("country park", "country park"), ("廁所", "toilet"), ("電動車", "electric"),
                        ("斜坡安全", "slope"), ("公立醫院", "hospital")]:
            hits = json.loads(search_hk_data(q, 3))["hits"]
            assert hits, f"{q!r} returned nothing"
            assert want in hits[0]["title"].lower(), f"{q!r} top hit is {hits[0]['title']!r}"
            print(f"  search {q!r} -> {hits[0]['title'][:50]}")
        gc = json.loads(geocode_address("Central, Hong Kong", 2))["candidates"]
        assert gc and 113.8 < gc[0]["lon"] < 114.5 and 22.1 < gc[0]["lat"] < 22.6, gc
        print(f"  geocode -> {gc[0]['address']} ({gc[0]['lon']:.4f}, {gc[0]['lat']:.4f})")
        pr = json.loads(geometry_op("project", {"type": "Point", "coordinates": [835000, 820000]},
                                    in_sr=2326, out_sr=4326))["geometry"]["coordinates"]
        assert 113.8 < pr[0] < 114.5 and 22.1 < pr[1] < 22.6, pr
        print(f"  project 2326->4326 -> {pr[0]:.4f}, {pr[1]:.4f}")
        buf = json.loads(geometry_op("buffer", {"type": "Point", "coordinates": [114.1694, 22.3193]},
                                     distance=500))["geometry"]
        assert buf["type"] == "Polygon" and len(buf["coordinates"][0]) > 10, buf["type"]
        print(f"  buffer 500 m -> Polygon with {len(buf['coordinates'][0])} vertices")
        rev = json.loads(geocode_address(lon=114.1694, lat=22.3193))
        assert rev.get("address"), rev
        print(f"  reverse geocode -> {rev['address'][:40]}")
        el = json.loads(elevation_at(114.1716, 22.2759))["elevation_m"]
        assert 0 < el < 1000, el
        print(f"  elevation -> {el:.1f} m")
        png = json.loads(export_static_map(bbox="114.10,22.25,114.25,22.35", basemap="streets",
                                           size="600,400"))
        assert png.get("bytes", 0) > 5000, png
        print(f"  static map -> {png['file'].split('/')[-1]} ({png['bytes']} bytes)")
        svg = json.loads(export_static_svg(["afcd_rcd_1728896853370_57183"], "Selftest static",
                                          "topographic"))
        page = _http(svg["url"]).decode()
        assert page.count("<image") >= 4 and page.count("<circle") == 53, (page.count("<image"),
                                                                           page.count("<circle"))
        print(f"  static svg  -> {svg['zoom']}z, {page.count('<image')} tiles, "
              f"{page.count('<circle')} features drawn")
        q = json.loads(query_hk_data("afcd_rcd_1635129924112_81508", "NAME_EN=Country", "", 3))
        assert q["matched"] >= 1 and q["returned"] <= 3, q
        print(f"  query       -> {q['matched']} matched, {q['returned']} returned, capped at {q['capped_at']}")
        agg = json.loads(summarise_hk_data("epd_rcd_1631080339740_69941",
                                           "NAME_OF_DISTRICT_COUNCIL_DISTRICT_EN", "", "count", 50))
        assert sum(r["count"] for r in agg["rows"]) == 989, agg["rows"]
        assert "chart-" in agg["chart_url"], agg["chart_url"]
        assert "<svg" in _http(agg["chart_url"]).decode(), "chart page not served"
        print(f"  summarise   -> {len(agg['rows'])} groups, {agg['rows'][0]['count']} max, chart served")
        st = json.loads(dataset_status(limit=5))
        assert st["checked"] == 5 and st["dates_basis"], st
        ages = [d["age_days"] for d in st["datasets"] if d["age_days"] is not None]
        assert ages == sorted(ages, reverse=True), f"status order: {ages}"
        assert all(d["state"] in ("fresh", "aging", "stale") for d in st["datasets"]), st
        live = json.loads(dataset_status(hits[0]["dataset_id"], probe=True))["datasets"][0]["live"]
        assert live["http"] == 200, live
        print(f"  status      -> {st['stale']}/{st['checked']} stale, probe {live['http']} in {live['ms']} ms")
        hits = json.loads(search_hk_data("country park", 3))["hits"]
        s = json.loads(load_hk_data(hits[0]["dataset_id"], hits[0]["title"]))
        assert s.get("features", 0) > 0, s
        lo, la = s["bbox"][0], s["bbox"][1]
        assert 113.8 < lo < 114.5 and 22.1 < la < 22.6, f"projection drift: {s['bbox']}"
        out = json.loads(build_map([hits[0]["dataset_id"]], "Selftest"))
        assert out.get("url"), out
        page = _http(out["url"]).decode()
        assert "GeoJSONLayer" in page and hits[0]["dataset_id"] in page
        sc = json.loads(build_scene([hits[0]["dataset_id"]], "Selftest scene"))
        assert sc.get("mode") == "3d" and sc["url"].endswith(".html"), sc
        assert sc["latest_url"].endswith("scene.html"), sc
        assert "SceneView" in _http(sc["url"]).decode()
        # two builds must coexist: an earlier map must not be destroyed by a later one
        a = json.loads(build_map([hits[0]["dataset_id"]], "Selftest alpha"))["url"]
        b = json.loads(build_map([hits[0]["dataset_id"]], "Selftest beta"))["url"]
        assert a != b, (a, b)
        assert b"Selftest alpha" in _http(a) and b"Selftest beta" in _http(b)
        print(f"  stable maps -> {a.rsplit('/', 1)[-1]} + {b.rsplit('/', 1)[-1]} both alive")
        print("selftest OK:", s["features"], "features,", out["url"])
    else:
        main()
