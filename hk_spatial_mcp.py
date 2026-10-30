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
import socket
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


# Only Esri hosts ever receive the token. Without this, any tool argument (or an agent
# talked into it by a hostile page) could POST ARCGIS_TOKEN to an arbitrary server.
_TOKEN_HOSTS = ("arcgis.com", "esri.com")


def _token_host_ok(url) -> bool:
    """True when url's host is an Esri domain (or a subdomain of one)."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _TOKEN_HOSTS)


class _NoLeakRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow a redirect that would carry a token to a non-Esri host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if "token=" in (req.full_url or "") and not _token_host_ok(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect to non-Esri host blocked",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_NoLeakRedirect)


def _http(url, data=None, headers=None, timeout=60):
    # ponytail: one retry on 5xx. The public Esri services return a transient 500 now and then
    # (observed on MapServer/export); a second attempt is cheaper than teaching every caller to retry.
    last = None
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 or attempt == 2:
                raise
            time.sleep(1.5)
    raise last


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


def _id_list(dataset_ids):
    """Normalise dataset ids: a real list, a JSON-array string, a comma-joined string, or one
    bare id all become a list. A bare comma-joined string used to become ONE id and fail
    downstream with a misleading 404."""
    if isinstance(dataset_ids, str):
        s = dataset_ids.strip()
        if s.startswith("["):
            try:
                v = json.loads(s)
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()]
            except Exception:
                pass
        return [x.strip() for x in s.split(",") if x.strip()]
    return [str(x) for x in (dataset_ids or [])]


def _build(dataset_ids, title: str, basemap: str, labels: bool, mode: str, page: str,
           tilt: float = 60) -> str:
    """Shared builder for the 2D map (index.html) and the 3D scene (scene.html)."""
    dataset_ids = _id_list(dataset_ids)
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
    # json.dumps does not escape "</" — a title holding </script> would close the block.
    html = html.replace("/*%TITLE%*/",
                        json.dumps(title, ensure_ascii=False).replace("</", "<\\/"))
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
    by_place: dict = {}
    for c in cands:  # the same place comes back twice with different labels and a tied score
        key = (round(c["lon"] or 0, 4), round(c["lat"] or 0, 4))
        if key not in by_place or (c["score"] or 0) > (by_place[key]["score"] or 0):
            by_place[key] = c
    cands = list(by_place.values())
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
    dataset_ids = _id_list(dataset_ids)
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


def _poly_parts(geom):
    """Polygon/MultiPolygon -> (list of parts, bbox). A part is a list of linear rings (holes)."""
    t = (geom or {}).get("type")
    if t == "Polygon":
        parts = [geom.get("coordinates") or []]
    elif t == "MultiPolygon":
        parts = list(geom.get("coordinates") or [])
    else:
        return [], None
    parts = [p for p in parts if p and p[0]]
    pts = [xy for p in parts for ring in p for xy in p[0]]
    if not pts:
        return [], None
    bbox = (min(q[0] for q in pts), min(q[1] for q in pts),
            max(q[0] for q in pts), max(q[1] for q in pts))
    return parts, bbox


BINS = 64  # ponytail: fixed y-buckets; enough for district-sized polygons, rebuild per shape


def _index_part(rings, y0: float, y1: float):
    """Bucket the ring segments by y so a point only tests the segments that can cross its row."""
    scale = BINS / ((y1 - y0) or 1.0)
    bins: list = [[] for _ in range(BINS)]
    for ring in rings:
        for k in range(len(ring) - 1):
            x1, y1s = ring[k][0], ring[k][1]
            x2, y2s = ring[k + 1][0], ring[k + 1][1]
            lo = int((min(y1s, y2s) - y0) * scale)
            hi = int((max(y1s, y2s) - y0) * scale)
            lo = 0 if lo < 0 else BINS - 1 if lo >= BINS else lo
            hi = 0 if hi < 0 else BINS - 1 if hi >= BINS else hi
            seg = (x1, y1s, x2, y2s)
            for b in range(lo, hi + 1):
                bins[b].append(seg)
    return bins, y0, scale


def _pip(x: float, y: float, indexed) -> bool:
    """Even-odd point-in-polygon over one part; holes fall out of the parity rule."""
    bins, y0, scale = indexed
    b = int((y - y0) * scale)
    if b < 0 or b >= BINS:
        return False
    inside = False
    for x1, y1s, x2, y2s in bins[b]:
        if (y1s > y) != (y2s > y):
            if x < x1 + (y - y1s) * (x2 - x1) / (y2s - y1s):
                inside = not inside
    return inside


def spatial_join(points_dataset: str, polygons_dataset: str, field: str = "",
                 top: int = 20) -> str:
    """Count the points of one dataset inside each polygon of another — the join summarise cannot do.

    Both datasets must already be loaded with load_hk_data(). `field` is the polygon attribute to
    label rows by (e.g. "dc_eng"); leave it empty and the first text-like field is used.
    Point-in-polygon runs locally in pure Python — no geometry service, no credential, nothing
    leaves the machine. Answers "which district has the most of these" in one call.
    """
    pts, err = _cached_features(points_dataset)
    if err:
        return json.dumps(err, ensure_ascii=False)
    polys, err = _cached_features(polygons_dataset)
    if err:
        return json.dumps(err, ensure_ascii=False)

    prop_sets = [f.get("properties") or {} for f in polys]

    def _label_rank(k):
        """Text-like, name-looking fields first. No guessing: the caller still picks one."""
        values = {str(r.get(k)) for r in prop_sets}
        hint = bool(re.search(r"name|district|region|area|zone|label|title|_chi|_eng|^dc_",
                             k, re.I))
        return (all(_num(v) is not None for v in values), not hint, k)

    candidates = sorted(((k, len({str(r.get(k)) for r in prop_sets}))
                         for k in (prop_sets[0] if prop_sets else {})
                         if 2 <= len({str(r.get(k)) for r in prop_sets}) <= 60
                         and not all(str(r.get(k)).startswith("http") for r in prop_sets)),
                        key=lambda kv: _label_rank(kv[0]))
    label_field = field
    field_names = [c[0] for c in candidates[:12]] or list((prop_sets[0] if prop_sets else {}))[:12]
    if not field:  # no guessing: same overview-then-call-again pattern as summarise_hk_data
        return json.dumps({
            "polygons_dataset": polygons_dataset, "points_dataset": points_dataset,
            "polygons": len(polys), "points": len(pts),
            "label_fields": field_names,
            "hint": "call again with field=<one of label_fields> to count the points per polygon",
        }, ensure_ascii=False)
    if field not in (prop_sets[0] if prop_sets else {}):
        return json.dumps({"error": f"{polygons_dataset}: no field {field!r}",
                           "label_fields": field_names}, ensure_ascii=False)

    shapes, skipped_shapes = [], 0
    for i, f in enumerate(polys):
        parts, bbox = _poly_parts(f.get("geometry"))
        if not parts:
            skipped_shapes += 1
            continue
        props = f.get("properties") or {}
        label = str(props.get(label_field) or f"polygon {i + 1}") if label_field else f"polygon {i + 1}"
        shapes.append({"label": label, "bbox": bbox,
                       "indexed": [_index_part(p, bbox[1], bbox[3]) for p in parts], "n": 0})
    if not shapes:
        return json.dumps({"error": f"{polygons_dataset}: no Polygon/MultiPolygon features"},
                          ensure_ascii=False)

    matched = unmatched = skipped_pts = 0
    for f in pts:
        g = f.get("geometry") or {}
        if g.get("type") != "Point" or not g.get("coordinates"):
            skipped_pts += 1
            continue
        x, y = g["coordinates"][0], g["coordinates"][1]
        hit = False
        for s in shapes:  # bbox reject first: without it this is every segment for every point
            x0, y0, x1, y1 = s["bbox"]
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            if any(_pip(x, y, part) for part in s["indexed"]):
                s["n"] += 1
                hit = True
                break  # first polygon wins; districts and constituencies do not overlap
        if hit:
            matched += 1
        else:
            unmatched += 1

    rows = sorted(({"group": s["label"], "count": s["n"]} for s in shapes), key=lambda r: -r["count"])
    rows = rows[:max(1, int(top))]
    w = min(28, max([len(r["group"]) for r in rows] + [len("group")]))
    table = [f"{'group':<{w}}  {'points':>18}", "-" * (w + 20)]
    for r in rows:
        table.append(f"{r['group'][:w]:<{w}}  {r['count']:>18,}")
    chart_url = _chart_page(f"{points_dataset} in {polygons_dataset} — points per polygon", rows, w)
    return json.dumps({
        "points_dataset": points_dataset, "polygons_dataset": polygons_dataset,
        "label_field": label_field, "label_fields": field_names,
        "polygons": len(shapes), "points": len(pts),
        "matched": matched, "unmatched": unmatched,
        "rows": rows, "table": "\n".join(table), "chart_url": chart_url,
        "skipped": {"non_polygon_features": skipped_shapes, "non_point_features": skipped_pts},
        "note": "pure-Python point-in-polygon, y-bucketed segments, bbox prefilter; "
                "first matching polygon wins",
    }, ensure_ascii=False)


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
    html = ("<!doctype html><meta charset=utf-8><link rel=\"icon\" href=\"data:,\"><title>"
            + _esc(title) + "</title>"
            "<body style='margin:0;background:#0b1018;color:#eaf0f7;"
            "font:13px/1.5 ui-sans-serif,system-ui,sans-serif'>"
            f"<div style='padding:22px'>{svg}"
            "<p style='color:#5f7185;font-size:11px'>Data: CSDI portal.csdi.gov.hk · "
            "Powered by Esri</p></div>")
    name = f"chart-{_slug(title, [len(rows)])}.html"
    WORK.mkdir(parents=True, exist_ok=True)   # chart-first calls must not die on a cold WORK
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
    # a full http(s) url is used verbatim — feature-service queries and GP tasks live
    # outside the sharing/rest root (servicesN.arcgis.com)
    url = path if path.startswith("http") else f"{AGOL}/{path.lstrip('/')}"
    if not _token_host_ok(url):
        return json.dumps({"error": "arcgis_request: only Esri hosts (*.arcgis.com, *.esri.com) "
                                    "may be called — the token must not leave Esri",
                           "tool": "arcgis_request", "url": url}, ensure_ascii=False)
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
    return json.dumps(_publish_geojson(src, title, tags), ensure_ascii=False)


def _agol_user():
    """The token's own AGOL username, or (None, raw reply) when the token is rejected."""
    tok = _token()
    me = json.loads(_redact(_http(f"{AGOL}/community/self?f=json&token={urllib.parse.quote(tok)}").decode()))
    return me.get("username"), me


def _await_publish(user, item_id, svc, tries=20, delay=6):
    """Poll an async AGOL publish job to its final status -> (ok, error dict | None).

    Scene jobs must be polled on the NEW service item: the source package item answers
    'failed' for the same jobId even while the service builds fine (observed 2026-10-29),
    so the service item is always preferred over the source item."""
    tok = _token()
    job = svc.get("jobId")
    if not job:
        return False, None
    target = svc.get("serviceItemId") or item_id
    for _ in range(tries):
        st = json.loads(_redact(_http(
            f"{AGOL}/content/users/{user}/items/{target}/status",
            urllib.parse.urlencode({"jobId": job, "f": "json", "token": tok}).encode(),
            timeout=60).decode()))
        if st.get("status") == "completed":
            return True, None
        if st.get("status") == "failed":
            return False, {"error": "publish job failed", "itemId": item_id, "detail": st}
        time.sleep(delay)
    return False, None


def _publish_geojson(src, title, tags):
    """Upload a GeoJSON file and publish it as a hosted feature layer, polling the async
    publish job to its final status (verified end-to-end on 2026-10-29)."""
    tok = _token()
    user, me = _agol_user()
    if not user:
        return {"error": "token rejected by ArcGIS Online", "detail": me}
    boundary = secrets.token_hex(12)
    body = _multipart(boundary, {"f": "json", "token": tok, "title": title, "type": "GeoJson",
                                 "tags": tags, "fileName": src.name},
                      {"file": (src.name, src.read_bytes(), "application/geo+json")})
    add = json.loads(_redact(_http(f"{AGOL}/content/users/{user}/addItem", body,
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                   timeout=300).decode()))
    if not add.get("success"):
        return {"error": "addItem failed", "detail": add}
    item_id = add["id"]
    pub = json.loads(_redact(_http(
        f"{AGOL}/content/users/{user}/publish",
        urllib.parse.urlencode({"itemId": item_id, "filetype": "geojson", "f": "json", "token": tok,
                                "publishParameters": json.dumps(
                                    {"name": re.sub(r"[^A-Za-z0-9_]", "_", title)[:90],
                                     "layerName": title[:90]})}).encode(),
        timeout=300).decode()))
    svc = (pub.get("services") or [{}])[0]
    ok, err = _await_publish(user, item_id, svc)
    if err:
        return err
    return {"itemId": item_id, "title": title,
            "item_url": f"https://www.arcgis.com/home/item.html?id={item_id}",
            "publish": ok, "service_url": svc.get("serviceurl"),
            "service_item_id": svc.get("serviceItemId")}


def _find_owned_scene_service(user, sname):
    """Item id of the caller's own hosted Scene Service whose URL ends in /<sname>/SceneServer."""
    tok = _token()
    srch = json.loads(_redact(_http(
        f"{AGOL}/search?" + urllib.parse.urlencode(
            {"q": f'owner:{user} AND type:"Scene Service"', "num": 100,
             "f": "json", "token": tok}), timeout=60).decode()))
    suffix = f"/{sname}/SceneServer"
    for r in srch.get("results", []):
        if (r.get("url") or "").rstrip("/").endswith(suffix):
            return r["id"]
    return None


def publish_scene(source: str, title: str = "", refresh: bool = False) -> str:
    """Publish a Scene Package as a hosted scene layer in YOUR ArcGIS Online content.

    source: AGOL item id of a Scene Package (.slpk) — 3D Object and Building Scene Layer
    packages both publish. The package is downloaded once into the local cache,
    re-uploaded to your content, and published. The new scene service stays PRIVATE —
    this tool never shares anything; share it yourself in ArcGIS Online.
    Needs ARCGIS_TOKEN. Returns the hosted SceneServer url: pass it to bim_findings
    (mode='scene') so the 3D viewer can draw the model, or to any other BIM tool.
    """
    if not _token():
        return _no_token("publish_scene")
    service = _bim_service(source)
    if not service.startswith("slpk://"):
        return json.dumps({"error": "publish_scene takes a Scene Package item id — "
                           f"{service[:80]} is already a hosted scene service",
                           "service": service}, ensure_ascii=False)
    pkg_item = service[len("slpk://"):]
    _slpk_zip(pkg_item)  # ensures the package bytes are cached locally
    fp = WORK / "bim" / "slpk" / f"{pkg_item}.slpk"
    tok = _token()
    user, me = _agol_user()
    if not user:
        return json.dumps({"error": "token rejected by ArcGIS Online", "detail": me})
    title = title or f"{_service_tag(pkg_item)} scene"
    # hosted service names must be clean identifiers — a trailing/double underscore fails
    # the publish job (observed 2026-10-29)
    sname = (re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", title)).strip("_")
             or "scene_layer")[:90]
    clash = _find_owned_scene_service(user, sname)
    if clash:
        return json.dumps({"error": f"a hosted scene service named '{sname}' already exists in "
                           "your content — delete it first or pass a different title (a duplicate "
                           "service name makes the publish job fail silently)",
                           "itemId": clash}, ensure_ascii=False)
    boundary = secrets.token_hex(12)
    # the uploaded file name becomes the item NAME, and AGOL requires that to be unique per
    # user folder — reusing the cached package's own name 409s on the second publish of the
    # same model (observed 2026-10-29), so derive it from the unique service name instead.
    # The multipart part filename is what AGOL reads, so set it in both places.
    body = _multipart(boundary, {"f": "json", "token": tok, "title": title,
                                 "type": "Scene Package", "tags": "hk-spatial-mcp,BIM",
                                 "fileName": f"{sname}.slpk"},
                      {"file": (f"{sname}.slpk", fp.read_bytes(), "application/octet-stream")})
    add = json.loads(_redact(_http(f"{AGOL}/content/users/{user}/addItem", body,
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                   timeout=600).decode()))
    if not add.get("success"):
        return json.dumps({"error": "addItem failed", "detail": add})
    pkg_id = add["id"]
    pub = json.loads(_redact(_http(
        f"{AGOL}/content/users/{user}/publish",
        urllib.parse.urlencode({"itemId": pkg_id, "filetype": "scenepackage", "f": "json",
                                "token": tok,
                                # without outputType the publisher defaults to a feature
                                # service and answers 500 'Unable to determine Service Type'
                                "outputType": "sceneService",
                                "publishParameters": json.dumps({"name": sname})}).encode(),
        timeout=300).decode()))
    svc = (pub.get("services") or [{}])[0]
    if not svc.get("serviceurl") and not svc.get("jobId"):
        return json.dumps({"error": "publish did not start", "itemId": pkg_id, "detail": pub})
    ok, err = _await_publish(user, pkg_id, svc, tries=40, delay=15)  # scene jobs: minutes
    if err:
        return json.dumps(err)
    return json.dumps({"itemId": pkg_id, "title": title,
                       "item_url": f"https://www.arcgis.com/home/item.html?id={pkg_id}",
                       "publish": ok, "service_url": svc.get("serviceurl"),
                       "service_item_id": svc.get("serviceItemId"),
                       "access": "private — this tool never shares items",
                       "next": "pass service_url to bim_findings(mode='scene') for a 3D viewer "
                               "that draws the model itself"}, ensure_ascii=False)


# ---- web scene viewer -------------------------------------------------------------

_SCENE_LEAF = {"ArcGISSceneServiceLayer": "SceneLayer",
               "IntegratedMeshLayer": "IntegratedMeshLayer"}


def _webscene_leaves(layers, ancestors_visible=True, group=""):
    """Flatten a web scene's operational layers to the leaves this viewer can draw."""
    out = []
    for lay in layers or []:
        vis = ancestors_visible and bool(lay.get("visibility"))
        kids = lay.get("layers")
        if isinstance(kids, list) and kids:
            out += _webscene_leaves(kids, vis, lay.get("title") or group)
        elif lay.get("layerType") in _SCENE_LEAF and lay.get("url"):
            url = lay["url"]
            out.append({"title": lay.get("title") or url.rstrip("/").rsplit("/", 3)[-3],
                        "url": url, "kind": _SCENE_LEAF[lay["layerType"]],
                        "visible": vis, "group": group})
    return out


def _mercator_to_lonlat(x, y):
    return (x / 20037508.342789244 * 180.0,
            math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2))


def webscene_viewer(item_id: str, refresh: bool = False) -> str:
    """Draw an ArcGIS Web Scene in a local 3D viewer and open it in the browser.

    item_id: a Web Scene item id (the .json document, not a scene layer). Every
    operational layer this viewer understands — 3D object scene services and integrated
    meshes — becomes a toggleable layer; the scene's own initial camera is reused, so the
    building is framed on load. Point clouds, Gaussian splats and the scene's basemap are
    skipped (the satellite basemap stands in). Private scene documents need ARCGIS_TOKEN.
    Returns a localhost viewer url — layers that are publicly readable need no token.
    """
    fp = WORK / "bim" / "webscene" / f"{item_id}.json"
    if refresh or not fp.exists():
        q = {"f": "json"}
        if _token():
            q["token"] = _token()
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(_http(f"{AGOL}/content/items/{item_id}/data?"
                             + urllib.parse.urlencode(q), timeout=120))
    data = json.loads(fp.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "operationalLayers" not in data:
        return json.dumps({"error": "not a web scene item (no operationalLayers)",
                           "itemId": item_id}, ensure_ascii=False)
    layers = _webscene_leaves(data.get("operationalLayers"))
    if not layers:
        return json.dumps({"error": "no drawable operational layers — supported layer types "
                           "are 3D object scene services and integrated meshes",
                           "itemId": item_id}, ensure_ascii=False)
    cam = (((data.get("initialState") or {}).get("viewpoint") or {}).get("camera") or {})
    pos = cam.get("position") or {}
    if "x" in pos and "y" in pos:                     # web mercator camera
        cam_lon, cam_lat = _mercator_to_lonlat(float(pos["x"]), float(pos["y"]))
        cam_z = float(pos.get("z") or 500)
    else:
        cam_lon = float(pos.get("longitude") or 114.17)
        cam_lat = float(pos.get("latitude") or 22.32)
        cam_z = float(pos.get("z") or 500)
    slug = _slug("webscene", [item_id[:8]])
    page = WORK / f"{slug}.html"
    checks = "".join(
        f'<li><label><input type="checkbox" id="cb{i}"'
        f'{" checked" if l["visible"] else ""}> {l["title"]}</label></li>'
        for i, l in enumerate(layers))
    page.write_text(_WEBSCENE_PAGE.format(
        title=f"Web Scene — {item_id[:8]}", checks=checks,
        layers_js=json.dumps([{k: l[k] for k in ("title", "url", "kind", "visible")}
                              for l in layers], ensure_ascii=False),
        cam_lon=round(cam_lon, 6), cam_lat=round(cam_lat, 6), cam_z=int(cam_z),
        cam_heading=round(float(cam.get("heading") or 25), 1),
        cam_tilt=round(float(cam.get("tilt") or 62), 1)), encoding="utf-8")
    return json.dumps({
        "itemId": item_id, "mode": "webscene",
        "layers": len(layers),
        "visible_on_load": sum(1 for l in layers if l["visible"]),
        "viewer_url": f"http://localhost:{PORT}/{page.name}",
        "viewer_file": str(page),
        "note": "tick layers in the panel to add the BIM disciplines; skipped: point clouds, "
                "gaussian splats, basemap layers",
    }, ensure_ascii=False)


_WEBSCENE_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://js.arcgis.com/4.31/esri/themes/light/main.css">
<style>
 html,body,#view {{ margin:0; height:100%; font-family:-apple-system,"Noto Sans CJK TC",Arial,sans-serif; }}
 #panel {{ position:absolute; top:14px; right:14px; z-index:9; background:#fff; padding:12px 14px;
   border-radius:6px; box-shadow:0 2px 10px rgba(0,0,0,.18); font-size:12px; color:#1a202c;
   max-height:70vh; overflow:auto; width:230px; }}
 #panel h4 {{ margin:0 0 6px; font-size:12px; color:#1a365d; letter-spacing:.04em; }}
 #panel ul {{ list-style:none; margin:0; padding:0; }}
 #panel li {{ margin:3px 0; }}
 #panel label {{ cursor:pointer; }}
 #panel button {{ margin-top:8px; width:100%; padding:5px 8px; border:1px solid #cbd5e0;
   border-radius:4px; background:#f7fafc; color:#1a365d; font-size:11px; cursor:pointer; }}
 #panel button:hover {{ background:#edf2f7; }}
 #msg {{ position:absolute; left:14px; top:14px; z-index:9; background:rgba(0,0,0,.65); color:#fff;
   padding:6px 10px; border-radius:4px; font-size:11px; }}
</style></head><body>
<div id="view"></div>
<div id="panel"><h4>WEB SCENE · 3D</h4><ul>{checks}</ul>
<button id="recentre" type="button" onclick="window.__recentre()">重設視角 · Re-centre</button>
<div style="margin-top:8px;color:#4a5568">Powered by Esri</div></div>
<div id="msg">載入中…</div>
<script src="https://js.arcgis.com/4.31/"></script>
<script>
require(["esri/Map","esri/views/SceneView","esri/layers/SceneLayer","esri/layers/IntegratedMeshLayer"],
function(Map, SceneView, SceneLayer, IntegratedMeshLayer) {{
  const SPECS = {layers_js};
  const msg = document.getElementById("msg");
  const built = [];
  let loaded = 0;
  function bump() {{ msg.textContent = "已載入 " + loaded + "/" + SPECS.length; }}
  const map = new Map({{ basemap: "satellite", ground: "world-elevation" }});
  const view = new SceneView({{ container: "view", map: map,
    camera: {{ position: {{ longitude: {cam_lon}, latitude: {cam_lat}, z: {cam_z} }},
              heading: {cam_heading}, tilt: {cam_tilt} }} }});
  SPECS.forEach(function (s, i) {{
    try {{
      const lyr = s.kind === "IntegratedMeshLayer"
        ? new IntegratedMeshLayer({{ url: s.url, title: s.title }})
        : new SceneLayer({{ url: s.url, title: s.title }});
      lyr.visible = s.visible;
      const cb = document.getElementById("cb" + i);
      if (cb) cb.onchange = function () {{ lyr.visible = cb.checked; }};
      lyr.when().then(function () {{ loaded++; bump(); }}).catch(function () {{
        if (cb) {{ cb.disabled = true; cb.parentElement.style.opacity = .4; }}
      }});
      map.add(lyr, i);
      built.push(lyr);
    }} catch (e) {{}}
  }});
  // same framing rule as the findings viewer: centre the extent, derive the zoom from span
  window.__fit = function (layer) {{
    try {{
      const e = layer && layer.fullExtent;
      if (!e) return false;
      if (e.spatialReference && e.spatialReference.wkid !== 4326) {{
        view.goTo({{ target: e, tilt: 62 }}, {{ duration: 400 }}).catch(function () {{}});
        return true;
      }}
      const cx = (e.xmin + e.xmax) / 2, cy = (e.ymin + e.ymax) / 2;
      const coslat = Math.cos(cy * Math.PI / 180);
      const spanM = Math.max((e.xmax - e.xmin) * 111320 * coslat, (e.ymax - e.ymin) * 111320);
      const zoom = Math.max(14, Math.min(20, Math.log2(156543.03 * coslat / (spanM / 840))));
      // center+zoom, animate:false — a point `target` with zoom silently no-ops and an
      // animated goTo can be interrupted before it lands (both observed 2026-10-29)
      view.goTo({{ center: [cx, cy], tilt: 62, heading: 25, zoom: zoom }}, {{ animate: false }})
        .then(function () {{
          window.__cam = {{ z: Math.round(view.camera.position.z),
                          tilt: Math.round(view.camera.tilt) }};
        }}).catch(function () {{}});
      return true;
    }} catch (err) {{ window.__frameError = String(err); return false; }}
  }};
  window.__recentre = function () {{
    return window.__fit(built.filter(function (l) {{ return l.visible && l.fullExtent; }})[0]);
  }};
  window.__layers = built.length;
  window.__view = view;      // debugging handle: view.takeScreenshot() and camera state
  window.__ready = false;
  view.when(function () {{ window.__recentre(); setTimeout(window.__recentre, 2500);
    window.__ready = true; }});
}});
</script></body></html>"""


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
// ponytail: basemap + labels are TileLayers too, so index-based skipping goes to the world extent;
// filter by layer type and wait for the GeoJSON layers to finish loading before reading fullExtent.
await Promise.allSettled(map.layers.toArray().map(l => l.when()));
const ext = map.layers.toArray()
  .filter(l => l.type === "geojson")
  .map(l => l.fullExtent)
  .filter(e => e && isFinite(e.xmin) && isFinite(e.ymax) && (e.xmax - e.xmin) < 180);
if (ext.length) {
  view.goTo({ xmin: Math.min(...ext.map(e => e.xmin)), ymin: Math.min(...ext.map(e => e.ymin)),
              xmax: Math.max(...ext.map(e => e.xmax)), ymax: Math.max(...ext.map(e => e.ymax)),
              spatialReference: ext[0].spatialReference });
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- BIM (i3s) + statutory checks

import gzip as _gzip
import struct as _struct
from concurrent.futures import ThreadPoolExecutor as _Pool

HK_ZONE = "https://services3.arcgis.com/6j1KwZfY2fZrfNMR/arcgis/rest/services/ZONE/FeatureServer/0"
HK_BHC = ("https://services3.arcgis.com/6j1KwZfY2fZrfNMR/arcgis/rest/services/"
          "Outline_Zoning_Plans_Building_Height_Control_Areas/FeatureServer/0")
HK_LOT = "https://services3.arcgis.com/6j1KwZfY2fZrfNMR/arcgis/rest/services/Lot_in_Hong_Kong/FeatureServer/0"
HK_SLOPE = ("https://services3.arcgis.com/6j1KwZfY2fZrfNMR/arcgis/rest/services/"
            "Registered_Man_made_Slopes_in_Hong_Kong/FeatureServer/0")
HK_LANDSLIDE = ("https://services3.arcgis.com/6j1KwZfY2fZrfNMR/arcgis/rest/services/"
                "Enhanced_Natural_Terrain_Landslide_Inventory/FeatureServer/0")

BIM_FIELDS = ("Category", "Family", "FamilyType", "ElementType", "Discipline", "Mark",
              "Typ_Mark", "BldgLevel", "BldgLevel_Desc", "BldgLevel_Elev")
BIM_NOTE = ("面積為 element bbox 上限，唔係法定 GFA；OZP/BHC 為公開鏡像，"
            "以法定圖則現行版本為準；非 Esri 官方產品。")


def _i3s_get(url, timeout=60):
    # ponytail: every i3s call is one small resource; parallel callers, no connection pooling.
    if url.startswith("slpk://"):
        return _slpk_get(url)
    body = _http(url, headers={"Accept-Encoding": "gzip", "User-Agent": "hk-spatial-mcp/bim"},
                 timeout=timeout)
    return _gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def _with_token(url):
    if url.startswith("slpk://"):
        return url
    if not _token_host_ok(url):  # never attach the token to a non-Esri host
        return url
    tok = os.environ.get("ARCGIS_TOKEN") or ""
    if not tok:
        return url
    return url + ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(tok)


# ---------------------------------------------------------------- slpk backend
#
# A Scene Package (.slpk) is a zip holding the same i3s resources a SceneServer would
# serve, so the whole BIM pipeline above it stays unchanged — _i3s_get just routes
# slpk:// pseudo-URLs here. Two layouts exist in the wild: Esri's
# 'layers/<L>/sublayers/<S>/...' and the Revit exporter's root-level 'sublayers/<S>/...';
# entries may be plain, gzipped, or carry a .json.gz / .bin.gz suffix.

_SLPK = {}  # item id -> {"zf": ZipFile, "names": {lower_name: actual_name}}


def _slpk_zip(item_id):
    hit = _SLPK.get(item_id)
    if hit:
        return hit
    import zipfile
    cache = WORK / "bim" / "slpk"
    cache.mkdir(parents=True, exist_ok=True)
    fp = cache / f"{item_id}.slpk"
    if not fp.exists():
        url = f"{AGOL}/content/items/{item_id}/data"
        tok = os.environ.get("ARCGIS_TOKEN") or ""
        if tok:
            url += "?token=" + urllib.parse.quote(tok)
        part = fp.with_suffix(".slpk.part")
        req = urllib.request.Request(url, headers={"User-Agent": "hk-spatial-mcp/bim"})
        with urllib.request.urlopen(req, timeout=900) as r, open(part, "wb") as f:
            while True:  # stream: packages run to several hundred MB
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        with open(part, "rb") as f:
            magic = f.read(2)
        if magic != b"PK":  # private/invalid items answer HTTP 200 with a JSON error body
            part.unlink()
            raise ValueError("item data is not an slpk zip — a private item needs ARCGIS_TOKEN, "
                             "or the id is not a Scene Package")
        os.replace(part, fp)
    zf = zipfile.ZipFile(fp)
    _SLPK[item_id] = {"zf": zf, "names": {n.lower(): n for n in zf.namelist()}}
    return _SLPK[item_id]


def _slpk_get(url):
    rest = url[len("slpk://"):]
    item, _, path = rest.partition("/")
    path, _, query = path.partition("?")
    z = _slpk_zip(item_id=item)
    bases = [path]
    m = re.fullmatch(r"layers/\d+/sublayers/(\d+)(?:/(.*))?", path)
    if m:
        bases.append(f"sublayers/{m.group(1)}" + (f"/{m.group(2)}" if m.group(2) else ""))
    cands = []
    for b in bases:
        if "f=json" in query:
            b = (b + "/") if b else ""
            cands += [b + "3dSceneLayer.json", b + "3dSceneLayer.json.gz"]
        else:
            cands += [b, b + ".gz", b + ".json.gz", b + ".bin.gz"]
    for c in cands:
        hit = z["names"].get(c.lower())
        if hit:
            buf = z["zf"].read(hit)
            return _gzip.decompress(buf) if buf[:2] == b"\x1f\x8b" else buf
    raise FileNotFoundError(f"{path or '(root)'} not in slpk {item}")


def _slpk_sublayer_paths(service):
    """Sublayer base paths inside the package, from the zip directory itself — the root
    document does not always list them the way a service would."""
    item = service[len("slpk://"):]
    z = _slpk_zip(item)
    paths = set()
    for name in z["names"].values():
        m = re.match(r"^(layers/\d+/sublayers/\d+|sublayers/\d+)/3dSceneLayer\.json", name, re.I)
        if m:
            paths.add(m.group(1))
    if not paths:
        return [service]  # single-layer package: the root document IS the layer
    return [f"{service}/{p}" for p in sorted(paths)]


def _slpk_node_ids(base):
    """Node ids from the zip directory, for packages with no nodepages index."""
    item, _, path = base[len("slpk://"):].partition("/")
    z = _slpk_zip(item)
    prefixes = [path]
    m = re.fullmatch(r"layers/\d+/sublayers/(\d+)", path)
    if m:
        prefixes.append(f"sublayers/{m.group(1)}")
    for pre in prefixes:
        pat = re.compile("^" + re.escape((pre + "/") if pre else "") + r"nodes/([^/]+)/")
        ids = [m.group(1) for n in z["names"].values() if (m := pat.match(n)) and m.group(1) != "root"]
        if ids:
            return sorted(set(ids), key=lambda s: (len(s), s))
    return []


def _i3s_decode(buf):
    """i3s attribute page. strings: [count][byteCount][lens][blob]; numbers: [count][(pad)][values].

    A page is only read as strings when its own length arithmetic proves it — the two numeric
    layouts below both exist in the wild (BldgLevel has no pad, BldgLevel_Elev has one).
    """
    if len(buf) < 8:
        return []
    n = int.from_bytes(buf[0:4], "little")
    if n == 0:
        return []
    bl = int.from_bytes(buf[4:8], "little")
    if len(buf) == 8 + 4 * n + bl:
        lens = _struct.unpack_from(f"<{n}I", buf, 8)
        if sum(lens) == bl:
            p, out = 8 + 4 * n, []
            for L in lens:
                out.append(buf[p:p + L].split(b"\x00")[0].decode("utf-8", "replace"))
                p += L
            return out
    for off in (4, 8):
        rest = len(buf) - off
        if rest % n == 0 and rest // n in (4, 8):
            return list(_struct.unpack_from(f"<{n}{'d' if rest // n == 8 else 'i'}", buf, off))
    raise ValueError(f"unknown i3s attribute page (len={len(buf)}, count={n})")


def _bim_service(source):
    s = (source or "").strip()
    if s.startswith(("http", "slpk://")):
        return s.rstrip("/")
    if re.fullmatch(r"[0-9a-fA-F]{32}", s):
        item = json.loads(_http(_with_token(f"{AGOL}/content/items/{s}?f=json")))
        url = (item.get("url") or "").rstrip("/")
        if url:
            return url
        if (item.get("type") or "").lower() == "scene package":
            return f"slpk://{s}"
        if item.get("error"):
            raise ValueError(f"AGOL item error: {item['error'].get('message')}")
        raise ValueError(f"item has no service url — it is a {item.get('type') or 'unknown'} item, "
                         "not a scene service or Scene Package")
    raise ValueError("pass a SceneServer url or a 32-char AGOL item id")


def _legacy_node_ids(base):
    """Node ids of an i3s 1.4–1.6 layer, where the tree is inlined in the node documents.

    Those services carry `rootNode: ./nodes/root` and answer 404 on `nodepages/{n}`; the
    only way in is walking `children` level by level. Per-node resources (`features/0`,
    `attributes/f_N/0`) are identical to the 1.7 layout, so callers need no other change.
    """
    def doc(nid):
        try:
            return json.loads(_i3s_get(_with_token(f"{base}/nodes/{nid}")))
        except Exception:
            return {}

    ids, seen, level = [], set(), [("root", doc("root"))]
    with _Pool(10) as ex:
        while level:
            nxt = []
            for nid, d in level:
                if nid in seen:
                    continue
                seen.add(nid)
                ids.append(nid)
                nxt += [c["id"] for c in (d.get("children") or []) if c.get("id")]
            nxt = [n for n in dict.fromkeys(nxt) if n not in seen]
            level = list(zip(nxt, ex.map(doc, nxt))) if nxt else []
    return ids


def _bim_node_ids(base):
    """(node ids, layout) for one (sub)layer, whichever i3s generation published it.

    1.7+ lists nodes in `nodepages/{n}`; 1.4–1.6 inlines them in the node documents and
    404s on nodepages (Building E, the 2018 trade services, is 1.6). Reporting 0 nodes for
    a readable layer turns into a false "not element-level BIM" verdict, so fall back.
    """
    ids = []
    for page in range(200):
        try:
            d = json.loads(_i3s_get(_with_token(f"{base}/nodepages/{page}")))
        except Exception:
            break
        if not d.get("nodes"):
            break
        ids += [x["index"] for x in d["nodes"]]
    if ids:
        return ids, "nodepages"
    if base.startswith("slpk://"):
        return [], "none"          # packages fall back to the zip directory instead
    legacy = _legacy_node_ids(base)
    return legacy, ("nodes-tree" if legacy else "none")


def _bim_sublayers(service, report=None):
    """All 3DObject (sub)layers that actually hold geometry, best first: (base, meta) pairs.

    A Building Scene Layer exposes 'Full Model' *and* 'Overview'; the former often has no
    metadata at all (layerType null, 0 attributes). Counting nodes is what tells them apart.
    A plain 3DObject scene service has no Building wrapper and no nested sublayers — the
    layer itself is the 3DObject, so it becomes its own candidate. Scene Packages are
    enumerated from the zip directory. Every live sublayer is returned so multi-discipline
    models (structure + architectural + MEP…) are read whole, not just their Overview.

    report: pass a list to collect every skipped candidate with its reason — the audit
    surfaces it instead of swallowing the gap silently.
    """
    if service.startswith("slpk://"):
        cands = _slpk_sublayer_paths(service)
    else:
        doc = json.loads(_i3s_get(_with_token(service + "?f=json")))
        cands = []
        for layer in doc.get("layers", []) or []:
            subs = layer.get("sublayers") or []
            if not subs:
                cands.append(f"{service}/layers/{layer['id']}")
                continue

            def leaves(nodes, lid=layer["id"]):
                # A Building layer nests disciplines under group sublayers
                # ('Full Model' -> 'Structural' -> Beams); only leaves hold geometry.
                # Sublayer ids are global across the tree, so the URL depth never changes.
                for s in nodes or []:
                    kids = s.get("sublayers") or []
                    if kids or s.get("layerType") == "group":
                        leaves(kids)
                    else:
                        cands.append(f"{service}/layers/{lid}/sublayers/{s['id']}")

            leaves(subs)
    scored = []
    for base in cands:
        try:
            meta = json.loads(_i3s_get(_with_token(base + "?f=json")))
        except Exception as e:
            if report is not None:
                report.append({"base": base, "reason": f"meta unreadable: {type(e).__name__}: {e}"})
            continue
        if meta.get("layerType") != "3DObject":
            if report is not None:
                report.append({"base": base, "name": meta.get("name"),
                               "reason": f"layerType is {meta.get('layerType')}, not 3DObject"})
            continue
        nodes = len(_bim_node_ids(base)[0])
        scored.append(((nodes, len(meta.get("attributeStorageInfo") or [])), base, meta))
    if not scored:
        raise ValueError("no 3DObject sublayer found")
    scored.sort(key=lambda t: t[0], reverse=True)
    live = [t for t in scored if t[0][0] > 0]
    if report is not None:
        for (n, _), base, meta in scored:
            if n == 0:
                report.append({"base": base, "name": meta.get("name"), "reason": "0 nodes"})
    picked = live or scored
    # A Building package ships an 'Overview' sublayer: a reduced duplicate of the
    # discipline sublayers for fast display. Reading it alongside them double-counts —
    # drop it when real discipline sublayers are live (GrandCentral: 879 of its 10,049
    # rows duplicate the 757 Walls).
    real = [t for t in picked if (t[2].get("name") or "").lower() != "overview"]
    if real:
        if report is not None:
            for _, base, meta in picked:
                if (meta.get("name") or "").lower() == "overview":
                    report.append({"base": base, "name": meta.get("name"),
                                   "reason": "duplicate of the discipline sublayers"})
        picked = real
    return [(base, meta) for _, base, meta in picked]


def _bim_sublayer(service):
    """The single best (sub)layer — kept for callers that only want one."""
    return _bim_sublayers(service)[0]


_BIM_ROWS_V = 5   # v5: coverage splits structural empties from real failures
_BIM_LAYER_V = 5  # v5: + i3s 1.4-1.6 node-tree fallback (stale 0-node caches must expire)


def _reproject_rows(rows, wkid):
    """i3s positions arrive in the layer's own SRS (Meinau: Lambert-93 metres), but the
    maps, viewers and HK statutory joins all need WGS84 degrees. Batch through Esri's
    public geometry service — the same keyless endpoint geometry_op uses."""
    idx = [i for i, r in enumerate(rows) if r.get("lon") is not None and r.get("lat") is not None]
    if not idx:
        return
    if all(abs(rows[i]["lon"]) <= 180 and abs(rows[i]["lat"]) <= 90 for i in idx):
        return  # degree-plausible already; some layers mislabel their wkid
    out = []
    for i in range(0, len(idx), 1000):
        chunk = idx[i:i + 1000]
        payload = json.dumps({"geometryType": "esriGeometryPoint",
                              "geometries": [{"x": rows[j]["lon"], "y": rows[j]["lat"]}
                                             for j in chunk]})
        params = urllib.parse.urlencode({"f": "json", "inSR": wkid, "outSR": 4326,
                                         "geometries": payload}).encode()
        d = json.loads(_http(f"{GEOM_SRV}/project", data=params,
                             headers={"Content-Type": "application/x-www-form-urlencoded"},
                             timeout=120))
        out += [(g["x"], g["y"]) for g in d.get("geometries", [])]
    if len(out) != len(idx):
        raise ValueError(f"reprojection returned {len(out)}/{len(idx)} points (inSR {wkid})")
    for i, (x, y) in zip(idx, out):
        rows[i]["lon"], rows[i]["lat"] = round(x, 7), round(y, 7)


def _bim_layer_rows(base, meta, refresh=False):
    """Rows of ONE (sub)layer under its own persistent cache, so a refresh or a crash on a
    multi-discipline model only redoes the layers that need it."""
    cache = WORK / "bim"
    cache.mkdir(parents=True, exist_ok=True)
    fp = cache / (hashlib.md5(base.encode()).hexdigest() + ".layer.json")
    if fp.exists() and not refresh:
        d = json.loads(fp.read_text(encoding="utf-8"))
        if d.get("v") == _BIM_LAYER_V:
            return d
    info = {a["name"]: a["key"] for a in meta.get("attributeStorageInfo") or []}
    fields = [f for f in BIM_FIELDS if f in info]
    ids, layout = _bim_node_ids(base)
    if not ids and base.startswith("slpk://"):
        ids = _slpk_node_ids(base)  # packages that only carry per-node resources, no index
        layout = "zip" if ids else layout

    def feats(nid):
        # i3s interior nodes legitimately carry no features of their own (geometry lives
        # in the leaves) — a missing page is 'empty', a broken one is 'failed'.
        try:
            d = json.loads(_i3s_get(_with_token(f"{base}/nodes/{nid}/features/0")))
        except FileNotFoundError:
            return nid, [], "empty"
        except urllib.error.HTTPError as e:
            return nid, [], "empty" if e.code == 404 else "failed"
        except Exception:
            return nid, [], "failed"
        return nid, [(f.get("id"), f.get("mbb") or [], f.get("position") or [])
                     for f in d.get("featureData", [])], "ok"

    with _Pool(10) as ex:
        res = list(ex.map(feats, ids))
    active = {nid: fl for nid, fl, _ in res if fl}
    n_empty = sum(1 for _, fl, st in res if not fl and st == "empty")
    n_failed = sum(1 for _, fl, st in res if not fl and st == "failed")

    def attr(nid, key, n):
        try:
            v = _i3s_decode(_i3s_get(_with_token(f"{base}/nodes/{nid}/attributes/{key}/0")))
            return v if len(v) == n else None
        except Exception:
            return None

    data = {}
    for f in fields:
        with _Pool(10) as ex:
            data[f] = dict(zip(active.keys(),
                               ex.map(lambda n, k=info[f]: attr(n, k, len(active[n])), active.keys())))

    rows = []
    for nid, fl in active.items():
        cols = {f: data[f].get(nid) for f in fields}
        for i, (fid, mbb, pos) in enumerate(fl):
            row = {"id": fid, "mbb": mbb, "lon": pos[0] if pos else None,
                   "lat": pos[1] if pos else None}
            for f in fields:
                c = cols[f]
                row[f] = c[i] if c and i < len(c) else None
            rows.append(row)

    sr = meta.get("spatialReference") or {}
    wkid = sr.get("latestWkid") or sr.get("wkid")
    if wkid and int(wkid) != 4326:
        _reproject_rows(rows, int(wkid))
    attr_failed = {f: sum(1 for nid in active if data[f].get(nid) is None) for f in fields}
    out = {"v": _BIM_LAYER_V, "base": base, "sublayer": meta.get("name"), "nodes": len(ids),
           "i3s_layout": layout, "fields": fields, "pos_wkid": 4326, "source_wkid": wkid,
           "coverage": {"nodes": len(ids), "nodes_with_features": len(active),
                        **({"nodes_empty": n_empty} if n_empty else {}),
                        **({"nodes_failed": n_failed} if n_failed else {}),
                        "attr_failed": {f: n for f, n in attr_failed.items() if n}},
           "rows": rows}
    fp.write_text(json.dumps(out), encoding="utf-8")
    return out


def _bim_rows(source, refresh=False):
    service = _bim_service(source)
    cache = WORK / "bim"
    cache.mkdir(parents=True, exist_ok=True)
    fp = cache / (hashlib.md5(service.encode()).hexdigest() + ".json")
    if fp.exists() and not refresh:
        d = json.loads(fp.read_text(encoding="utf-8"))
        if d.get("v") == _BIM_ROWS_V:
            return d

    merged, fields, names = [], [], []
    skipped = []
    cov = {"nodes": 0, "nodes_with_features": 0, "attr_failed": {}}
    for base, meta in _bim_sublayers(service, report=skipped):
        d = _bim_layer_rows(base, meta, refresh)
        for r in d["rows"]:
            r["sublayer"] = d["sublayer"]
        merged += d["rows"]
        c = d.get("coverage") or {}
        cov["nodes"] += c.get("nodes", d["nodes"])
        cov["nodes_with_features"] += c.get("nodes_with_features", 0)
        for k in ("nodes_empty", "nodes_failed"):
            if c.get(k):
                cov[k] = cov.get(k, 0) + c[k]
        for f, n in (c.get("attr_failed") or {}).items():
            cov["attr_failed"][f] = cov["attr_failed"].get(f, 0) + n
        for f in d["fields"]:
            if f not in fields:
                fields.append(f)
        if d["sublayer"]:
            names.append(str(d["sublayer"]))
    if skipped:
        cov["sublayers_skipped"] = skipped
    out = {"v": _BIM_ROWS_V, "service": service, "sublayer": ", ".join(names),
           "sublayers": len(names), "nodes": cov["nodes"], "layerType": "3DObject",
           "fields": fields, "coverage": cov, "rows": merged}
    fp.write_text(json.dumps(out), encoding="utf-8")
    return out


def _bim_bbox(rows):
    lons = [r["lon"] for r in rows if r.get("lon") is not None]
    lats = [r["lat"] for r in rows if r.get("lat") is not None]
    if not lons:
        return None
    return [min(lons), min(lats), max(lons), max(lats)]


def _service_tag(service):
    """Last non-empty path segment, slug-safe for any service form — SceneServer urls and
    slpk:// pseudo-services alike ('.../SceneServer' → its name, 'slpk://<id>' → the id)."""
    segs = [s for s in str(service).split("/") if s]
    return (segs[-1] if segs else "model")[:28]


def _empty(v, field=""):
    if v is None or v == "":
        return True
    return field in ("Mark", "Typ_Mark") and v == 0


def _hk_query(layer, bbox, fields, where="1=1"):
    env = json.dumps({"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
                      "spatialReference": {"wkid": 4326}})
    q = {"where": where, "geometry": env, "geometryType": "esriGeometryEnvelope",
         "spatialRel": "esriSpatialRelIntersects", "inSR": 4326, "outFields": fields,
         "returnGeometry": "false", "f": "json"}
    url = layer + "/query?" + urllib.parse.urlencode(q)
    doc = json.loads(_http(_with_token(url), timeout=60))
    return [f.get("attributes", {}) for f in doc.get("features", [])]


def _links(bbox, service=""):
    out = {}
    if bbox:
        out["map_viewer"] = ("https://www.arcgis.com/apps/mapviewer/index.html?bbox="
                             f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}")
    if service:
        out["scene_viewer"] = ("https://www.arcgis.com/home/webscene/viewer.html?url="
                               + urllib.parse.quote(service, safe="")
                               + f"&viewpoint=cam:{((bbox[0]+bbox[2])/2) if bbox else 114.17},"
                               f"{((bbox[1]+bbox[3])/2) if bbox else 22.32},800;70,0")
    return out


def bim_audit(source: str, refresh: bool = False) -> str:
    """Audit a BIM scene layer (slpk / scene service) and report data quality. Read-only.

    source: SceneServer url or AGOL item id (scene service or Scene Package .slpk)."""
    d = _bim_rows(source, refresh)
    rows, total = d["rows"], len(d["rows"])
    lv = {}
    for r in rows:
        k = r.get("BldgLevel_Desc") or r.get("BldgLevel")
        lv[str(k)] = lv.get(str(k), 0) + 1
    comp = {}
    for f in d["fields"]:
        comp[f] = sum(1 for r in rows if _empty(r.get(f), f))
    marks = [r.get("Mark") for r in rows if not _empty(r.get("Mark"), "Mark")]
    dup = len(marks) - len(set(marks))
    floors = [r for r in rows if str(r.get("Category") or "").lower() in ("floors", "floor", "slab")]
    lv_count = len({str(r.get("BldgLevel_Desc") or r.get("BldgLevel")) for r in rows})
    cov_raw = d.get("coverage") or {}
    coverage = {"nodes": cov_raw.get("nodes"), "nodes_with_features": cov_raw.get("nodes_with_features")}
    for k in ("nodes_empty", "nodes_failed", "attr_failed", "sublayers_skipped"):
        if cov_raw.get(k):
            coverage[k] = cov_raw[k]
    out = {
        "service": d["service"], "sublayer": d["sublayer"], "sublayers": d.get("sublayers", 1),
        "nodes": d["nodes"],
        "elements": total, "levels": lv, "coverage": coverage,
        "completeness_pct": {f: round(100 * n / max(total, 1), 1) for f, n in comp.items()},
        "tags": {"filled": len(marks), "duplicated": dup},
        "catalogue": {"category": len({r.get("Category") for r in rows if r.get("Category")}),
                      "family": len({r.get("Family") for r in rows if r.get("Family")}),
                      "family_type": len({r.get("FamilyType") for r in rows if r.get("FamilyType")})},
        "floors": {"slabs": len(floors),
                   "levels_with_slabs": len({str(r.get("BldgLevel_Desc") or r.get("BldgLevel"))
                                             for r in floors}),
                   "levels_total": lv_count},
        "verdict": [],
        "links": _links(_bim_bbox(rows)),
        "note": BIM_NOTE,
    }
    # skipped sublayers and structurally-empty nodes are listed for transparency but are
    # NOT gaps: excluding the duplicate Overview is by design, and i3s interior nodes
    # legitimately carry no features. Only real failures reach the verdict.
    gaps = []
    if coverage.get("nodes_failed"):
        gaps.append(f"{coverage['nodes_failed']} 個 node 嘅 features 讀取失敗")
    if coverage.get("attr_failed"):
        gaps.append("attribute 缺頁 " + ", ".join(f"{f}×{n}" for f, n in coverage["attr_failed"].items()))
    if gaps:
        out["verdict"].append("讀取有缺口（" + "；".join(gaps) + "）→ 審核只涵蓋成功讀取嘅部分")
    if comp.get("Mark", 0) / max(total, 1) > 0.9:
        out["verdict"].append("Mark 幾乎全空 → 不能用作資產編號 / FM 對接")
    if dup:
        out["verdict"].append(f"{dup} 個 Mark 重複 → join 會產生歧義")
    if out["floors"]["slabs"] and out["floors"]["levels_with_slabs"] < lv_count:
        out["verdict"].append("樓板覆蓋不完整 → 不可用此模型計算面積或 GFA")
    if not out["catalogue"]["category"]:
        out["verdict"].append("無 Category／Family 分類 → 唔係 element-level BIM（外殼或 LOD 匯出），"
                              "只可以做視覺化，唔可以做資產審核")
    return json.dumps(out, ensure_ascii=False)


def bim_floors(source: str, refresh: bool = False) -> str:
    """Floor-slab inventory per level (bbox area = upper bound). source: SceneServer url, or AGOL item id (scene service or Scene Package .slpk)."""
    d = _bim_rows(source, refresh)
    rows = d["rows"]
    floors = [r for r in rows if str(r.get("Category") or "").lower() in ("floors", "floor", "slab")]
    per = {}
    for r in floors:
        k = str(r.get("BldgLevel_Desc") or r.get("BldgLevel"))
        mbb = r.get("mbb") or []
        area = (mbb[3] - mbb[0]) * (mbb[4] - mbb[1]) if len(mbb) == 6 else 0.0
        e = per.setdefault(k, {"slabs": 0, "area_m2_bbox": 0.0})
        e["slabs"] += 1
        e["area_m2_bbox"] = round(e["area_m2_bbox"] + area, 1)
    levels = {str(r.get("BldgLevel_Desc") or r.get("BldgLevel")) for r in rows}
    return json.dumps({
        "service": d["service"], "elements": len(rows), "slabs_total": len(floors),
        "per_level": dict(sorted(per.items())),
        "levels_present": len(levels), "levels_with_slabs": len(per),
        "coverage_ok": len(per) == len(levels) and bool(floors),
        "note": BIM_NOTE,
    }, ensure_ascii=False)


# ---------------------------------------------------------------- where engine
#
# SQL-flavoured, case-insensitive keywords (ported from agol-bim-mcp):
#
#   expr    := or_expr
#   or_expr := and_expr ( OR and_expr )*
#   and_expr:= atom ( AND atom )*
#   atom    := [NOT] ( comparison | is_null | like | in_list | bare_word )
#   comparison := FIELD (=|==|!=|<>|>|>=|<|<=) VALUE
#   is_null := FIELD IS [NOT] NULL
#   like    := FIELD [NOT] LIKE 'pat%'      -- % any run, _ one char
#   in_list := FIELD [NOT] IN (a, b, c)
#
# VALUE is taken raw to the next top-level AND/OR, so spaces and embedded double
# quotes survive untouched.

_WOPS = {
    "=": lambda a, b: a == b, "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b, "<>": lambda a, b: a != b,
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}
_WRE_CMP = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(==|!=|<>|>=|<=|=|>|<)\s*(.*)$", re.S)
_WRE_IS = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(NOT\s+)?NULL\s*$", re.I)
_WRE_LIKE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(NOT\s+)?LIKE\s+(.+)$", re.I | re.S)
_WRE_IN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(NOT\s+)?IN\s*\((.*)\)\s*$", re.I | re.S)


class WhereError(ValueError):
    pass


def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        inner = v[1:-1]
        return inner.replace(v[0] * 2, v[0])
    return v


def _wnum(x):
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def _matching_paren(s, i):
    depth, q = 0, None
    for j in range(i, len(s)):
        c = s[j]
        if q:
            if c == q:
                q = None
            continue
        if c in "'\"":
            q = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _strip_outer(s):
    s = s.strip()
    while s.startswith("(") and _matching_paren(s, 0) == len(s) - 1:
        s = s[1:-1].strip()
    return s


def _split_top(s, kw):
    """Split on a top-level SQL keyword, ignoring quoted strings and parenthesised groups."""
    parts, depth, q, start, i = [], 0, None, 0, 0
    n, k = len(s), len(kw)
    while i < n:
        c = s[i]
        if q:
            if c == q:
                q = None
            i += 1
            continue
        if c in "'\"":
            q = c
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0 and s[i:i + k].upper() == kw:
            before = s[i - 1] if i else " "
            after = s[i + k] if i + k < n else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                parts.append(s[start:i])
                start = i + k
                i += k
                continue
        i += 1
    parts.append(s[start:])
    return [p.strip() for p in parts]


def _like_re(pat):
    out = []
    for ch in pat:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("^" + "".join(out) + "$", re.I | re.S)


def _wcompare(v, raw, op):
    if v is None:
        return False
    a, b = _wnum(v), _wnum(raw)
    if a is not None and b is not None:
        return _WOPS[op](a, b)
    return _WOPS[op](str(v).lower(), raw.lower())


def _weq(v, raw):
    if v is None:
        return raw.strip() == "" or raw.strip().lower() in ("null", "none")
    a, b = _wnum(v), _wnum(raw)
    if a is not None and b is not None:
        return a == b
    return str(v).strip().lower() == raw.strip().lower()


def _parse_atom(text, used):
    t = _strip_outer(text)
    if not t:
        raise WhereError("empty condition")

    m = re.match(r"^NOT\s+(.+)$", t, re.I | re.S)
    if m:
        inner = _parse_atom(m.group(1), used)
        return lambda row, f=inner: not f(row)

    m = _WRE_IS.match(t)
    if m:
        fld, neg = m.group(1), bool(m.group(2))
        used.add(fld)
        return lambda row, k=fld, n=neg: (row.get(k) is None) != n

    m = _WRE_LIKE.match(t)
    if m:
        fld, neg, pat = m.group(1), bool(m.group(2)), _unquote(m.group(3))
        used.add(fld)
        rx = _like_re(pat)
        return lambda row, k=fld, r=rx, n=neg: \
            ((row.get(k) is not None and r.match(str(row.get(k))) is not None) != n)

    m = _WRE_IN.match(t)
    if m:
        fld, neg, body = m.group(1), bool(m.group(2)), m.group(3)
        used.add(fld)
        vals = {_unquote(p).lower() for p in body.split(",") if p.strip()}
        return lambda row, k=fld, v=vals, n=neg: \
            ((row.get(k) is not None and str(row.get(k)).strip().lower() in v) != n)

    m = _WRE_CMP.match(t)
    if m:
        fld, op, raw = m.group(1), m.group(2), _unquote(m.group(3))
        used.add(fld)
        if op in ("=", "=="):
            return lambda row, k=fld, r=raw: _weq(row.get(k), r)
        if op in ("!=", "<>"):
            return lambda row, k=fld, r=raw: not _weq(row.get(k), r)
        return lambda row, k=fld, o=op, r=raw: _wcompare(row.get(k), r, o)

    # bare word: substring match across every field — still useful when you only know
    # a fragment of a value
    needle = _unquote(t).lower()
    return lambda row, s=needle: \
        any(s in str(v).lower() for v in row.values() if v is not None)


def _compile_where(expr, used):
    e = _strip_outer(expr)
    ors = _split_top(e, "OR")
    if len(ors) > 1:
        fns = [_compile_where(p, used) for p in ors]
        return lambda row: any(f(row) for f in fns)
    ands = _split_top(e, "AND")
    if len(ands) > 1:
        fns = [_compile_where(p, used) for p in ands]
        return lambda row: all(f(row) for f in fns)
    return _parse_atom(e, used)


def _build_where(where):
    """-> (predicate | None, referenced fields, None) or (None, used, error dict)."""
    where = str(where or "").strip()
    if not where:
        return None, set(), None
    used = set()
    try:
        pred = _compile_where(where, used)
    except WhereError as e:
        return None, used, {"error": "where_parse", "detail": str(e), "where": where}
    except Exception as e:  # never let a bad filter kill the tool
        return None, used, {"error": "where_parse", "detail": f"{type(e).__name__}: {e}",
                            "where": where}
    return pred, used, None


def _canon(field, names):
    """Field lookup is case-insensitive; returns the real spelling or None."""
    if field in names:
        return field
    low = field.lower()
    for n in names:
        if n.lower() == low:
            return n
    return None


def _zero_hint(rows, used, names):
    """A filter that matched nothing is the most common LLM dead end. Say why."""
    info = {"matched": 0}
    for f in sorted(used):
        c = _canon(f, names)
        if not c:
            info.setdefault("field_values", []).append(
                {"field": f, "problem": "no such field", "available": names[:40]})
            continue
        vals, seen = [], set()
        for r in rows:
            v = r.get(c)
            if v is None:
                continue
            s = str(v)
            if s not in seen:
                seen.add(s)
                vals.append(s)
            if len(vals) >= 12:
                break
        info.setdefault("field_values", []).append(
            {"field": c, "sample_values": vals, "rows_scanned": len(rows)})
    return info


def bim_elements(source: str, where: str = "", limit: int = 20,
                 group_by: str = "", refresh: bool = False) -> str:
    """Filter BIM elements. where — a real expression, not a single equality:
        Category=Walls AND BldgLevel_Desc=03 OG OKFFB     (raw value; spaces and quotes survive)
        RoomName LIKE '%PANTRY%'                          (% any run, _ one char)
        Mark IN (32, 31, 05)
        RoomNumber IS NULL
        NOT Category=Doors
    A bare word with no operator is matched as a substring across every field.
    If a filter matches nothing you get field_values showing what the field holds.
    group_by: field name to count instead of listing.
    source: SceneServer url, or AGOL item id of a scene service or Scene Package (.slpk)."""
    d = _bim_rows(source, refresh)
    rows = d["rows"]
    if where:
        pred, used, werr = _build_where(where)
        if werr:
            return json.dumps({**werr, "tool": "bim_elements", "service": d["service"]},
                              ensure_ascii=False)
        hits = [r for r in rows if pred(r)]
        if not hits and used:
            hint = _zero_hint(rows, used, d["fields"])
            return json.dumps({"service": d["service"], **hint, "note": BIM_NOTE},
                              ensure_ascii=False)
    else:
        hits = rows
    if group_by:
        cnt = {}
        for r in hits:
            k = str(r.get(group_by))
            cnt[k] = cnt.get(k, 0) + 1
        pairs = sorted(cnt.items(), key=lambda t: -t[1])[:max(1, limit)]
        return json.dumps({"service": d["service"], "matched": len(hits),
                           "group_by": group_by, "rows": [{"value": k, "count": v} for k, v in pairs],
                           "note": BIM_NOTE}, ensure_ascii=False)
    slim = [{k: r.get(k) for k in ("id", "Category", "Family", "FamilyType", "BldgLevel", "Mark",
                                   "sublayer", "lon", "lat")} for r in hits[:max(1, limit)]]
    return json.dumps({"service": d["service"], "matched": len(hits), "returned": len(slim),
                       "rows": slim, "note": BIM_NOTE}, ensure_ascii=False)


def site_zoning(lat: float = 0.0, lon: float = 0.0, source: str = "", radius_m: int = 0) -> str:
    """Statutory context for a BIM model or a point: OZP zone, height control, slopes, landslides.

    Give source=<BIM scene url or item id> to use the model extent, or lat/lon + radius_m."""
    d = None
    if source:
        d = _bim_rows(source)
        bbox = _bim_bbox(d["rows"])
        if not bbox:
            raise ValueError("model has no positions")
    else:
        if not (lat and lon):
            raise ValueError("pass source= or lat/lon")
        dlat = max(radius_m, 50) / 111320.0
        dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
        bbox = [lon - dlon, lat - dlat, lon + dlon, lat + dlat]

    zones = _hk_query(HK_ZONE, bbox, "PLAN_NO,ZONE_LABEL,DESC_ENG,DESC_CHT,SPUSE_ENG,SPUSE_CHT")
    seen, zrows = set(), []
    for z in zones:
        key = (z.get("PLAN_NO"), z.get("ZONE_LABEL"))
        if key in seen:
            continue
        seen.add(key)
        zrows.append({"plan_no": z.get("PLAN_NO"), "zone": z.get("ZONE_LABEL"),
                      "desc": (z.get("DESC_ENG") or "").strip(),
                      "desc_cn": (z.get("DESC_CHT") or "").strip(),
                      "permitted_use": (z.get("SPUSE_ENG") or "").strip()[:200]})

    bhc = _hk_query(HK_BHC, bbox, "PLAN_NO,BHC_TYPE,BHC_VALUE")
    hrows = []
    for b in bhc:
        t, v = (b.get("BHC_TYPE") or ""), (b.get("BHC_VALUE") or "")
        try:
            val = float(v)
        except (TypeError, ValueError):
            val = None
        if t == "BHCS":
            hrows.append({"plan_no": b.get("PLAN_NO"), "type": "storeys", "value": val})
        elif t == "BHCB":
            hrows.append({"plan_no": b.get("PLAN_NO"), "type": "metres", "value": val})
        else:
            hrows.append({"plan_no": b.get("PLAN_NO"), "type": t, "value": val})

    slopes = _hk_query(HK_SLOPE, bbox, "Identifier_of_the_Slope_Feature,District___地區")
    slides = _hk_query(HK_LANDSLIDE, bbox, "SLIDE_TYPE,YEAR_1")

    return json.dumps({
        "bbox": [round(x, 6) for x in bbox],
        "model": {"service": d["service"], "elements": len(d["rows"])} if d else None,
        "zones": zrows,
        "height_control": hrows[:30],
        "registered_slopes": {"count": len(slopes),
                              "sample": [s.get("Identifier_of_the_Slope_Feature") for s in slopes[:5]]},
        "landslide_records": {"count": len(slides),
                              "latest_year": max((s.get("YEAR_1") or 0) for s in slides) if slides else None},
        "links": _links(bbox, d["service"] if d else ""),
        "note": BIM_NOTE,
    }, ensure_ascii=False)


def lot_check(source: str, refresh: bool = False) -> str:
    """Which lots does the model extent sit on (bbox level), plus height control.

    bbox level only: a model can share a bbox with a neighbouring lot without touching it."""
    d = _bim_rows(source, refresh)
    bbox = _bim_bbox(d["rows"])
    if not bbox:
        raise ValueError("model has no positions")
    lots = _hk_query(HK_LOT, bbox, "LOTID,LOTCSUID")
    bhc = _hk_query(HK_BHC, bbox, "PLAN_NO,BHC_TYPE,BHC_VALUE")
    zones = _hk_query(HK_ZONE, bbox, "PLAN_NO,ZONE_LABEL")
    return json.dumps({
        "bbox": [round(x, 6) for x in bbox],
        "lots": {"count": len(lots), "sample": [l.get("LOTCSUID") for l in lots[:10]]},
        "zone_labels": sorted({z.get("ZONE_LABEL") for z in zones if z.get("ZONE_LABEL")}),
        "height_control_rows": len(bhc),
        "precision": "bbox level — not a footprint intersection",
        "links": _links(bbox, d["service"]),
        "note": BIM_NOTE,
    }, ensure_ascii=False)


def _print_pdf(html_path, pdf_path, timeout=120):
    """Chromium headless print. Returns the pdf path, or None when no browser is available."""
    import shutil
    import subprocess
    cands = [shutil.which(e) for e in ("chromium-browser", "chromium", "google-chrome", "chrome")]
    # Windows: browsers install to fixed dirs, not PATH — Edge ships with the OS, so this is the
    # difference between "no PDF on every colleague machine" and "PDF everywhere".
    for env, tail in (("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
                      ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
                      ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
                      ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe")):
        base = os.environ.get(env)
        if base:
            cands.append(str(pathlib.Path(base) / tail))
    for exe in cands:
        if not exe or not pathlib.Path(exe).exists():
            continue
        try:
            # ponytail: --no-sandbox only as root (Chromium refuses its own sandbox at uid 0).
            # This renders HTML built from third-party feeds, so keep the sandbox everywhere else.
            args = [exe, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", f"file://{html_path}"]
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                args.insert(2, "--no-sandbox")
            subprocess.run(args, check=True, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if pdf_path.exists() and pdf_path.stat().st_size > 1000:
                return pdf_path
        except Exception:
            continue
    return None


_REPORT_CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
* { box-sizing: border-box; }
body { font-family: "Noto Sans CJK TC","Noto Sans CJK SC","Helvetica Neue",Arial,sans-serif;
       color:#1a202c; font-size:10.5pt; line-height:1.55; margin:0; }
.cover { page-break-after: always; padding-top: 34mm; }
.cover .bar { height:8mm; background:#1a365d; margin:0 -18mm 0 -18mm; }
.cover .gold { height:2.4mm; background:#c9a84c; margin:0 -18mm 0 -18mm; }
.cover h1 { font-size:26pt; color:#1a365d; margin:18mm 0 4mm 0; line-height:1.2; }
.cover h2 { font-size:12pt; color:#4a5568; font-weight:400; margin:0 0 14mm 0; }
.cover table { width:100%; border-collapse:collapse; font-size:9.5pt; }
.cover td { padding:2.2mm 0; border-bottom:1px solid #e2e8f0; vertical-align:top; }
.cover td:first-child { color:#4a5568; width:34%; }
.verdict { margin-top:10mm; padding:5mm; border-left:4px solid #c9a84c; background:#faf7ef; }
.verdict b { color:#1a365d; }
h3 { font-size:13pt; color:#1a365d; border-bottom:1.6pt solid #c9a84c;
     padding-bottom:1.6mm; margin:8mm 0 3.5mm 0; page-break-after:avoid; }
h4 { font-size:10.5pt; color:#2b6cb0; margin:5mm 0 2mm 0; page-break-after:avoid; }
table.d { width:100%; border-collapse:collapse; font-size:9pt; margin:2mm 0 4mm 0; }
table.d th { background:#1a365d; color:#fff; text-align:left; padding:2mm 2.4mm; font-weight:600; }
table.d td { padding:1.7mm 2.4mm; border-bottom:1px solid #e2e8f0; }
table.d tr:nth-child(even) td { background:#f7fafc; }
.num { text-align:right; font-variant-numeric: tabular-nums; }
.tag { display:inline-block; padding:0.6mm 2mm; border-radius:2mm; font-size:8pt; font-weight:600; }
.bad { background:#fde8e8; color:#9b1c1c; }
.warn { background:#fdf6e3; color:#8a6d1f; }
.ok { background:#e6f4ea; color:#1e6b3a; }
.note { font-size:8.6pt; color:#4a5568; border-left:2.4pt solid #e2e8f0; padding-left:3mm; margin:3mm 0; }
ul { margin:1.5mm 0 3mm 5mm; padding:0; }
li { margin-bottom:1.4mm; }
.foot { margin-top:8mm; font-size:8pt; color:#718096; border-top:1px solid #e2e8f0; padding-top:2.5mm; }
a { color:#2b6cb0; text-decoration:none; word-break:break-all; }
.two { display:flex; gap:6mm; }
.two > div { flex:1; }
"""


def _report_html(a, fl, sz, generated):
    def table(headers, rows, num_cols=()):
        th = "".join(f"<th>{h}</th>" for h in headers)
        body = ""
        for r in rows:
            cells = ""
            for i, c in enumerate(r):
                cls = ' class="num"' if i in num_cols else ""
                cells += f"<td{cls}>{c}</td>"
            body += f"<tr>{cells}</tr>"
        return f'<table class="d"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'

    total = a["elements"]
    comp = a["completeness_pct"]
    gaps = [f for f, p in comp.items() if p > 90]
    verdict_rows = "".join(f"<li>{v}</li>" for v in a["verdict"]) or "<li>無重大資料問題</li>"
    level_rows = [[f"Level {k}", f"{v:,}"] for k, v in
                  sorted(a["levels"].items(), key=lambda t: (t[0] in ("None", ""), t[0]))]
    comp_rows = []
    for f, p in comp.items():
        flag = '<span class="tag bad">未設定</span>' if p > 90 else (
            '<span class="tag warn">部分</span>' if p > 10 else '<span class="tag ok">完整</span>')
        comp_rows.append([f, f"{100 - p:.1f}%", f"{p:.1f}%", flag])
    cat_rows = []
    floor_rows = [[f"Level {k}", f'{v["slabs"]}', f'{v["area_m2_bbox"]:,.1f}'] for k, v in
                  fl["per_level"].items()] or [["（無樓板）", "0", "0.0"]]

    site_block = ""
    if sz:
        zone_rows = [[z["plan_no"], z["zone"], z["desc"]] for z in sz.get("zones", [])]
        hc_rows = [[h["plan_no"], h["type"], ("" if h["value"] is None else f'{h["value"]:g}')]
                   for h in sz.get("height_control", [])] or [["—", "—", "—"]]
        site_block = f"""
  <h3>5&nbsp;&nbsp;法定規劃背景</h3>
  <p>模型範圍所處的法定圖則與地帶。界線以公開鏡像圖層計算，<b>並非</b>法定圖則的現行版本。</p>
  {table(["圖則編號", "地帶", "英文描述"], zone_rows)}
  <h4>建築物高度管制</h4>
  {table(["圖則編號", "管制類型", "數值"], hc_rows)}
  <div class="note">地帶內的准許用途須查閱該圖則的《註釋》（Notes）；本報告不作用途合規判斷。
  已登記人造斜坡 <b>{sz.get('registered_slopes', {}).get('count', 0)}</b> 個，
  天然山坡山泥傾瀉記錄 <b>{sz.get('landslide_records', {}).get('count', 0)}</b> 宗。</div>"""

    checked = ["構件總數、樓層分佈、類別／族／類型目錄",
               "欄位完整度、標識重複、樓板覆蓋"]
    checked.append("模型範圍與分區計劃大綱圖、建築物高度管制區、已登記斜坡、山泥傾瀉記錄的空間對照"
                   if sz else "（模型不在香港範圍，未套用本地法定圖層）")
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>BIM 模型審核報告 — {a['sublayer'] or ''}</title><style>{_REPORT_CSS}</style></head><body>

<div class="cover">
  <div class="bar"></div><div class="gold"></div>
  <h1>BIM 模型審核報告<br><span style="font-size:15pt;color:#2b6cb0">BIM Model Audit Report</span></h1>
  <h2>ArcGIS i3s 場景圖層 · 資料完整性與法定規劃背景</h2>
  <table>
    <tr><td>模型圖層</td><td>{a['sublayer'] or '—'}</td></tr>
    <tr><td>服務端點</td><td style="word-break:break-all">{a['service']}</td></tr>
    <tr><td>構件總數</td><td><b>{total:,}</b></td></tr>
    <tr><td>i3s 節點數</td><td>{a['nodes']}</td></tr>
    <tr><td>報告日期</td><td>{generated}</td></tr>
    <tr><td>資料來源</td><td>ArcGIS Online 場景服務（i3s 屬性頁）＋香港公開空間數據</td></tr>
  </table>
  <div class="verdict"><b>總結判斷</b><ul>{verdict_rows}</ul></div>
</div>

<h3>1&nbsp;&nbsp;摘要</h3>
<p>本報告以程式直接讀取 i3s 場景圖層的節點屬性頁（該類服務沒有 SQL 查詢端點），
逐件構件核對分類、樓層、標識與樓板覆蓋，並把模型範圍與法定規劃圖層對照。
全部數字由模型自身資料產生，不含估算。</p>
<div class="two">
  <div><h4>模型概況</h4>{table(["項目", "數值"], [
      ["構件總數", f"{total:,}"], ["樓層數", str(len(a["levels"]))],
      ["類別 (Category)", str(a["catalogue"]["category"])],
      ["族 (Family)", str(a["catalogue"]["family"])],
      ["類型 (FamilyType)", str(a["catalogue"]["family_type"])],
      ["樓板數量", str(a["floors"]["slabs"])]], (1,))}</div>
  <div><h4>必須修正</h4><ul>
    {"".join(f"<li>{f}：{comp[f]:.1f}% 未設定</li>" for f in gaps) or "<li>無</li>"}
    <li>重複標識：{a["tags"]["duplicated"]} 個</li>
    <li>樓板覆蓋：{a["floors"]["levels_with_slabs"]}/{a["floors"]["levels_total"]} 層</li>
  </ul></div>
</div>

<h3>2&nbsp;&nbsp;模型清單</h3>
<h4>各層構件數</h4>
{table(["樓層", "構件數"], level_rows, (1,))}

<h3>3&nbsp;&nbsp;資料完整性</h3>
<p>以下為各屬性欄位的未設定比率。標識（Mark）為資產管理與工單系統的連接鍵，
未設定即無法對接 FM。</p>
{table(["欄位", "已填", "未設定", "狀態"], comp_rows, (1, 2))}
<div class="note">判讀規則：同一標識出現多於一次即為重複，會令 join 產生歧義；
本模型非空標識 <b>{a["tags"]["filled"]}</b> 個，其中 <b>{a["tags"]["duplicated"]}</b> 個重複。</div>

<h3>4&nbsp;&nbsp;樓板覆蓋</h3>
{table(["樓層", "樓板數", "面積 (m², bbox 上限)"], floor_rows, (1, 2))}
<div class="note">面積為構件邊界盒（bbox）計算，屬<b>上限值</b>，並非實際樓板面積，
亦不等同法定總樓面面積（GFA）。{"樓板覆蓋不完整，本模型不可用於任何面積計算。" if not fl["coverage_ok"] else "樓板覆蓋完整。"}</div>
{site_block}

<h3>{6 if sz else 5}&nbsp;&nbsp;方法與限制</h3>
<h4>已核查</h4>
<ul>
  {"".join(f"<li>{c}</li>" for c in checked)}
</ul>
<h4>未核查（本方法無法判定）</h4>
<ul>
  <li>總樓面面積（GFA）、實際樓板面積（需幾何解算，且須符合法定定義）</li>
  <li>逃生距離、隔火間面積、走廊淨闊（需完整幾何與拓撲）</li>
  <li>碰撞檢測（構件級 bbox 不足以構成碰撞判定）</li>
  <li>任何「合規」結論 — 本報告只提供事前篩查（pre-check）</li>
</ul>
<div class="note">分區計劃大綱圖與高度管制區在本報告中為<b>公開鏡像</b>，
一切以城市規劃委員會／建築事務監督的現行法定文件為準。
本報告由自動化流程產生，並非 Esri 官方產品。</div>

<h3>{7 if sz else 6}&nbsp;&nbsp;附錄：線上檢視</h3>
<p>Map Viewer（2D 範圍）：<br><a href="{a['links'].get('map_viewer', '')}">{a['links'].get('map_viewer', '—')}</a></p>
<p>Scene Viewer（3D 模型）：<br><a href="{a['links'].get('scene_viewer', '')}">{a['links'].get('scene_viewer', '—')}</a></p>
<div class="foot">BIM Model Audit Report · 由 hk-spatial-mcp 自動產生 · {generated} ·
資料：ArcGIS Online 場景服務；香港公開空間數據（規劃署／地政總署／土木工程拓展署）</div>
</body></html>"""


def bim_report(source: str, site: bool = True, refresh: bool = False) -> str:
    """Produce a professional PDF audit report for a BIM scene layer.

    Reads the model over i3s, joins its extent to HK statutory layers, writes an A4 HTML report,
    serves it on the local port and prints a PDF when a Chromium binary is present.
    source: SceneServer url or AGOL item id (scene service or Scene Package .slpk)."""
    a = json.loads(bim_audit(source, refresh))
    fl = json.loads(bim_floors(source, refresh))
    sz = None
    if site:
        try:
            sz = json.loads(site_zoning(source=source))
            if not sz.get("zones"):
                sz = None
        except Exception:
            sz = None
    generated = time.strftime("%Y-%m-%d %H:%M")
    html = _report_html(a, fl, sz, generated)
    slug = _slug("bim-audit", [_service_tag(a["service"])])
    fp = WORK / f"{slug}.html"
    fp.write_text(html, encoding="utf-8")
    pdf = _print_pdf(fp.resolve(), (WORK / f"{slug}.pdf").resolve())
    return json.dumps({
        "html_url": f"http://localhost:{PORT}/{slug}.html",
        "html_file": str(fp),
        "pdf_file": str(pdf) if pdf else None,
        "pdf_url": f"http://localhost:{PORT}/{slug}.pdf" if pdf else None,
        "pdf_bytes": pdf.stat().st_size if pdf else 0,
        "sections": 7 if sz else 6,
        "elements": a["elements"],
        "site_context": bool(sz),
        "note": BIM_NOTE,
    }, ensure_ascii=False)


def _findings(rows):
    """Turn rows into a short, defensible finding list — each finding has a location and a reason."""
    out = []
    marks = {}
    for r in rows:
        m = r.get("Mark")
        if not _empty(m, "Mark"):
            marks.setdefault(str(m), []).append(r)
    dup_ids = {id(r) for v in marks.values() if len(v) > 1 for r in v}
    for r in rows:
        if id(r) in dup_ids and r.get("lon") is not None:
            out.append(dict(r, finding="duplicate_mark",
                            detail=f"Mark {r.get('Mark')} 出現多於一次"))
    for r in rows:
        if str(r.get("Category") or "").lower() in ("doors", "door") and r.get("lon") is not None:
            ft = str(r.get("FamilyType") or "")
            if not re.search(r"\d", ft):
                out.append(dict(r, finding="no_dimension",
                                detail=f"門類型無尺寸：{ft or '(空)'}"))
    for r in rows:
        if str(r.get("Category") or "").lower() in ("floors", "floor", "slab") \
                and r.get("lon") is not None:
            out.append(dict(r, finding="slab_coverage",
                            detail=f"樓板 Level {r.get('BldgLevel')}"))
    return out


def _findings_geojson(source, refresh=False):
    d = _bim_rows(source, refresh)
    items = _findings(d["rows"])
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
              "properties": {k: r.get(k) for k in ("finding", "detail", "Category", "Family",
                                                   "FamilyType", "BldgLevel", "Mark", "id")}}
             for r in items]
    return {"type": "FeatureCollection", "features": feats}, d["service"], d.get("layerType") or "3DObject"


def _viewer_service_auth(service: str):
    """(token for the viewer | None, note | None) — a private scene service cannot be drawn
    by a browser without credentials: the layer's load() stalls instead of failing and the
    JS IdentityManager pops a sign-in dialog (observed 2026-10-29). A ?token= on the layer
    URL is not forwarded to i3s child requests, so the page registers the token with
    IdentityManager instead. Probe anonymously; on error hand the LOCAL viewer a session
    token — it expires with the session and the page is only ever served from localhost."""
    tok = _token()
    if not service.startswith("http") or not tok:
        return None, None
    try:
        d = json.loads(_http(f"{service}?f=json", timeout=60).decode())
    except Exception:
        return None, None
    if d.get("layers"):
        return None, None  # public — no credentials needed
    return tok, ("private scene service — the local viewer embeds a session token "
                 "(localhost use only; the token expires with your session)")


def bim_findings(source: str, mode: str = "map", refresh: bool = False) -> str:
    """Write the audit findings as GeoJSON plus a viewer page, ready to publish.

    mode: "map" (2D MapView) or "scene" (3D SceneView with the BIM model itself).
    Findings are element-level and carry a reason: duplicate_mark, no_dimension, slab_coverage."""
    gj, service, ltype = _findings_geojson(source, refresh)
    slug = _slug("bim-findings", [_service_tag(service)])
    fp = WORK / f"{slug}.geojson"
    fp.write_text(json.dumps(gj), encoding="utf-8")
    counts = {}
    for f in gj["features"]:
        k = f["properties"]["finding"]
        counts[k] = counts.get(k, 0) + 1
    page = WORK / f"{slug}-{'3d' if mode == 'scene' else '2d'}.html"
    colours = (("duplicate_mark", "#c53030"), ("no_dimension", "#b7791f"),
               ("slab_coverage", "#2b6cb0"))
    legend = "".join(f'<li><span class="sw {k}"></span>{k} · {v}</li>' for k, v in counts.items())
    css = "".join(f".sw.{k}{{background:{c}}}" for k, c in colours)
    viewer_note = None
    if mode == "scene":
        lons = [f["geometry"]["coordinates"][0] for f in gj["features"]]
        lats = [f["geometry"]["coordinates"][1] for f in gj["features"]]
        if not lons:                       # no findings: fall back to the model extent
            rows = _bim_rows(source, refresh)["rows"]
            lons = [r["lon"] for r in rows if r.get("lon") is not None]
            lats = [r["lat"] for r in rows if r.get("lat") is not None]
        if lons:
            xmin, xmax, ymin, ymax = min(lons), max(lons), min(lats), max(lats)
            cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
        else:
            cx, cy = 114.17, 22.32
            xmin, xmax, ymin, ymax = cx - 0.002, cx + 0.002, cy - 0.002, cy + 0.002
        span_m = max((ymax - ymin) * 111320,
                     (xmax - xmin) * 111320 * math.cos(math.radians(cy)), 30)
        tok_viewer, viewer_note = _viewer_service_auth(
            "" if service.startswith("slpk://") else service)
        token_js = ""
        if tok_viewer:
            # AGOL normalizes every *.arcgis.com host to www.arcgis.com inside IdentityManager —
            # registering the service's own host never matches and the token never attaches (499)
            token_js = f'idMgr.registerToken({{ server: "https://www.arcgis.com", token: "{tok_viewer}" }});'
        page.write_text(_SCENE_PAGE.format(
            title="BIM Findings — 3D", data_url=f"{slug}.geojson",
            # a Scene Package has no hosted layer for the viewer to load — markers only
            service="" if service.startswith("slpk://") else service,
            token_js=token_js,
            legend=legend, css=css,
            cam_lon=round(cx, 6), cam_lat=round(cy, 6),
            cam_above=int(min(240, max(60, span_m * 2.5))),
            symbols="".join(
                f'{{ value: "{k}", symbol: {{ type: "point-3d", symbolLayers: [{{ type: "object", '
                f'resource: {{ primitive: "cylinder" }}, material: {{ color: "{c}" }}, '
                f'height: 12, width: 2.4, anchor: "bottom" }}] }} }},'
                for k, c in colours)), encoding="utf-8")
    else:
        page.write_text(_FINDINGS_PAGE.format(
            title="BIM Audit Findings", data_url=f"{slug}.geojson", legend=legend, css=css),
            encoding="utf-8")
    return json.dumps({
        "service": service,
        "mode": mode,
        "findings": len(gj["features"]),
        "by_type": counts,
        "geojson_file": str(fp),
        "viewer_url": f"http://localhost:{PORT}/{page.name}",
        "viewer_file": str(page),
        **({"viewer_note": viewer_note} if viewer_note else {}),
        "note": BIM_NOTE,
    }, ensure_ascii=False)


def publish_findings(source: str, title: str = "BIM Audit Findings", refresh: bool = False) -> str:
    """Publish the audit findings to ArcGIS Online as a hosted point layer (needs ARCGIS_TOKEN).

    source: SceneServer url, or AGOL item id (scene service or Scene Package .slpk)."""
    if not _token():
        return _no_token("publish_findings")
    gj, service, _ltype = _findings_geojson(source, refresh)
    if not gj["features"]:
        return json.dumps({"error": "no findings to publish — the audit found nothing "
                           "(bim_findings returns 0 features for this model)"}, ensure_ascii=False)
    slug = _slug("bim-findings", [_service_tag(service)])
    fp = WORK / f"{slug}.geojson"
    fp.write_text(json.dumps(gj), encoding="utf-8")
    out = _publish_geojson(fp, title, "hk-spatial-mcp,BIM,findings")
    out["findings"] = len(gj["features"])
    return json.dumps(out, ensure_ascii=False)


_SCENE_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://js.arcgis.com/4.31/esri/themes/light/main.css">
<style>
 html,body,#view {{ margin:0; height:100%; font-family:-apple-system,"Noto Sans CJK TC",Arial,sans-serif; }}
 #panel {{ position:absolute; top:14px; right:14px; z-index:9; background:rgba(255,255,255,.95);
   padding:12px 14px; border-radius:6px; box-shadow:0 2px 10px rgba(0,0,0,.25); font-size:12px; color:#1a202c; }}
 #panel h4 {{ margin:0 0 6px; font-size:12px; color:#1a365d; letter-spacing:.04em; }}
 #panel ul {{ list-style:none; margin:0; padding:0; }}
 #panel li {{ margin:3px 0; }}
 .sw {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }}
 #panel button {{ margin-top:8px; width:100%; padding:5px 8px; border:1px solid #cbd5e0;
   border-radius:4px; background:#f7fafc; color:#1a365d; font-size:11px; cursor:pointer; }}
 #panel button:hover {{ background:#edf2f7; }}
 #hint {{ position:absolute; left:14px; bottom:34px; z-index:9; background:rgba(26,54,93,.9); color:#fff;
   padding:8px 12px; border-radius:4px; font-size:11px; }}
 #msg {{ position:absolute; left:14px; top:14px; z-index:9; background:rgba(0,0,0,.65); color:#fff;
   padding:6px 10px; border-radius:4px; font-size:11px; }}
{css}
</style></head><body>
<div id="view"></div>
<div id="panel"><h4>BIM FINDINGS · 3D</h4><ul>{legend}</ul>
<button id="recentre" type="button" onclick="window.__recentre()">重設視角 · Re-centre</button>
<div style="margin-top:8px;color:#c53030;font-size:11px">柱高 12 m，僅作定位提示，非實際構件</div>
<div style="margin-top:6px;color:#4a5568">Powered by Esri</div></div>
<div id="hint">拖曳旋轉 · 滾輪縮放 · 右鍵平移</div>
<div id="msg">載入中…</div>
<script src="https://js.arcgis.com/4.31/"></script>
<script>
require(["esri/Map","esri/views/SceneView","esri/layers/BuildingSceneLayer","esri/layers/SceneLayer",
         "esri/layers/GraphicsLayer","esri/Graphic","esri/PopupTemplate","esri/identity/IdentityManager"],
function(Map, SceneView, BuildingSceneLayer, SceneLayer, GraphicsLayer, Graphic, PopupTemplate, idMgr) {{
  const msg = document.getElementById("msg");
  const COLOURS = {{ duplicate_mark: "#c53030", no_dimension: "#b7791f", slab_coverage: "#2b6cb0" }};
  const gLayer = new GraphicsLayer({{ title: "Findings" }});
  const map = new Map({{ basemap: "satellite", ground: "world-elevation", layers: [gLayer] }});
  const CAM = {{ longitude: {cam_lon}, latitude: {cam_lat}, heading: 25, tilt: 62, above: {cam_above} }};
  // ponytail: goTo the layer's own extent, never a hand-computed camera. A camera set
  // before the layer resolves leaves the user looking at the region from orbit — the
  // model is then a speck and reads as "no model" (observed 2026-10-29).
  window.__frames = 0;
  // goTo(extent) does not reliably centre a tilted 3D camera — the eye can settle several
  // hundred metres off-target and the building then reads as "missing" (2026-10-29).
  // Target the extent CENTRE as a point instead, with the zoom derived from its span.
  window.__fit = function (layer) {{
    try {{
      const e = layer && layer.fullExtent;
      if (!e) return false;
      if (e.spatialReference && e.spatialReference.wkid !== 4326) {{
        view.goTo({{ target: e, tilt: 62 }}, {{ duration: 400 }}).catch(function () {{}});
        window.__frames++;
        return true;
      }}
      const cx = (e.xmin + e.xmax) / 2, cy = (e.ymin + e.ymax) / 2;
      const coslat = Math.cos(cy * Math.PI / 180);
      const spanM = Math.max((e.xmax - e.xmin) * 111320 * coslat, (e.ymax - e.ymin) * 111320);
      const zoom = Math.max(14, Math.min(20, Math.log2(156543.03 * coslat / (spanM / 840))));
      // center+zoom, animate:false — a point `target` with zoom silently no-ops and an
      // animated goTo can be interrupted before it lands (both observed 2026-10-29)
      view.goTo({{ center: [cx, cy], tilt: 62, heading: 25, zoom: zoom }}, {{ animate: false }})
        .then(function () {{
          window.__cam = {{ z: Math.round(view.camera.position.z),
                          tilt: Math.round(view.camera.tilt) }};
        }}).catch(function () {{}});
      window.__frames++;
      return true;
    }} catch (err) {{ window.__frameError = String(err); return false; }}
  }};
  window.__recentre = function () {{
    if (window.__fit(window.__bimLayer)) return true;
    try {{
      view.camera = {{ position: {{ longitude: CAM.longitude, latitude: CAM.latitude, z: CAM.above }},
                      tilt: CAM.tilt, heading: CAM.heading }};
      return true;
    }} catch (err) {{ window.__frameError = String(err); return false; }}
  }};
  function frame() {{ return window.__recentre(); }}
  const view = new SceneView({{ container: "view", map: map,
    camera: {{ position: {{ latitude: {cam_lat}, longitude: {cam_lon}, z: 600 }}, tilt: 62 }} }});
  window.__view = view;      // debugging handle: view.takeScreenshot() and camera state

  // BIM first, so it draws under the markers. Empty service = slpk source, markers only.
  const SVC = "{service}";
  if (SVC) {{
  {token_js}
  const bim = new BuildingSceneLayer({{ url: SVC, title: "BIM model" }});
  map.add(bim, 0);
  window.__bimLayer = bim;
  bim.load().then(() => {{
    // A Building scene service usually stores its root "Full Model" group INVISIBLE, and
    // the JS API honours that — the layer loads, reports sublayers, and draws nothing.
    // An audit viewer wants the whole building, so show the whole sublayer tree.
    (function show(list) {{
      (list || []).forEach(function (s) {{
        try {{ s.visible = true; }} catch (e) {{}}
        if (s.sublayers) show(s.sublayers);
      }});
    }})(bim.sublayers);
    window.__layerKind = "BuildingSceneLayer";
    msg.textContent = "BIM: " + (bim.sublayers ? bim.sublayers.length : 0) + " sublayers";
    frame();
    setTimeout(frame, 2000);   // extent can settle a beat after load; re-frame once
  }}).catch(() => {{
    map.remove(bim);
    const alt = new SceneLayer({{ url: SVC, title: "BIM model" }});
    map.add(alt, 0);
    alt.load().then(() => {{ window.__layerKind = "SceneLayer"; window.__bimLayer = alt;
      msg.textContent = "BIM: scene layer"; frame(); setTimeout(frame, 2000); }})
      .catch(() => {{ window.__layerKind = "failed"; msg.textContent = "BIM 載入失敗"; }});
  }});
  }} else {{ window.__layerKind = "none-slpk"; msg.textContent = "BIM: package source（無 hosted layer）"; }}

  fetch("{data_url}").then(r => r.json()).then(gj => {{
    let n = 0;
    for (const f of gj.features) {{
      const pr = f.properties, c = COLOURS[pr.finding] || "#7f7f7f";
      gLayer.add(new Graphic({{
        geometry: {{ type: "point", longitude: f.geometry.coordinates[0], latitude: f.geometry.coordinates[1] }},
        symbol: {{ type: "point-3d", symbolLayers: [{{ type: "object",
          resource: {{ primitive: "cylinder" }}, material: {{ color: c }},
          height: 12, width: 2.4, anchor: "bottom" }}] }},
        attributes: pr,
        popupTemplate: {{ title: pr.finding,
          content: pr.detail + "<br/>Category: " + pr.Category + "<br/>Type: " + pr.FamilyType +
                   "<br/>Level: " + pr.BldgLevel + "<br/>Mark: " + pr.Mark }}
      }}));
      n++;
    }}
    window.__findings = n;
    msg.textContent += " · findings " + n;
  }});

  window.__ready = false;
  view.when(() => {{ frame(); setTimeout(frame, 1500); window.__ready = true; }});
}});
</script></body></html>"""

_FINDINGS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://js.arcgis.com/4.31/esri/themes/light/main.css">
<style>
 html,body,#view {{ margin:0; height:100%; font-family:-apple-system,"Noto Sans CJK TC",Arial,sans-serif; }}
 #panel {{ position:absolute; top:14px; right:14px; z-index:9; background:#fff; padding:12px 14px;
   border-radius:6px; box-shadow:0 2px 10px rgba(0,0,0,.18); font-size:12px; color:#1a202c; }}
 #panel h4 {{ margin:0 0 6px; font-size:12px; color:#1a365d; letter-spacing:.04em; }}
 #panel ul {{ list-style:none; margin:0; padding:0; }}
 #panel li {{ margin:3px 0; }}
 .sw {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }}
{css}
</style></head><body>
<div id="view"></div>
<div id="panel"><h4>BIM AUDIT FINDINGS</h4><ul>{legend}</ul>
<div style="margin-top:8px;color:#4a5568">Powered by Esri</div></div>
<script src="https://js.arcgis.com/4.31/"></script>
<script>
require(["esri/Map","esri/views/MapView","esri/layers/GeoJSONLayer"], function(Map, MapView, GeoJSONLayer) {{
  const layer = new GeoJSONLayer({{
    url: new URL("{data_url}", window.location.href).href,
    title: "Findings",
    popupTemplate: {{
      title: "{{finding}}",
      content: "{{detail}}<br/>Category: {{Category}}<br/>Type: {{FamilyType}}<br/>Level: {{BldgLevel}}<br/>Mark: {{Mark}}"
    }},
    renderer: {{
      type: "unique-value", field: "finding",
      uniqueValueInfos: [
        {{ value: "duplicate_mark", symbol: {{ type: "simple-marker", color: "#c53030", size: 9, outline: {{ color: "#fff", width: 1 }} }} }},
        {{ value: "no_dimension", symbol: {{ type: "simple-marker", color: "#b7791f", size: 8, outline: {{ color: "#fff", width: 1 }} }} }},
        {{ value: "slab_coverage", symbol: {{ type: "simple-marker", color: "#2b6cb0", size: 10, outline: {{ color: "#fff", width: 1 }} }} }}
      ]
    }}
  }});
  const map = new Map({{ basemap: "streets-navigation-vector", layers: [layer] }});
  const view = new MapView({{ container: "view", map: map, zoom: 17, center: [114.17, 22.32] }});
  layer.when(() => {{
    if (layer.fullExtent) view.goTo(layer.fullExtent.expand(1.4));
    try {{ view.popup.dockEnabled = false; }} catch (e) {{}}
  }});
  window.__ready = false;
  view.when(() => {{ frame(); setTimeout(frame, 1500); window.__ready = true; }});
}});
</script></body></html>"""


# ---------------------------------------------------------------- MCP wiring

def _safe(fn):
    """Tool errors as data, not protocol exceptions.

    The mcp 2.x server masks any raised exception as 'Error executing tool X' and drops the
    message, so a failing tool is indistinguishable from a bug. Returning structured JSON lets
    the caller read the reason and self-correct. Direct (in-process) calls still raise, which
    keeps tests and debugging honest.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}", "tool": fn.__name__},
                              ensure_ascii=False)

    return wrapper


def main():
    try:  # mcp 2.x renamed FastMCP -> MCPServer; keep 1.x working too
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server
    mcp = Server("hk-spatial")
    bim_tools = (bim_audit, bim_floors, bim_elements, site_zoning, lot_check,
                 bim_findings, bim_report, publish_findings, publish_scene, webscene_viewer)
    tools = [search_hk_data, load_hk_data, query_hk_data, summarise_hk_data, build_map, build_scene,
             geocode_address, geometry_op, elevation_at, export_static_map, export_static_svg,
             arcgis_request, publish_layer, dataset_status, spatial_join]
    if (os.environ.get("HK_MCP_BIM") or "1").strip().lower() not in ("0", "false", "off", "no"):
        tools += list(bim_tools)          # HK_MCP_BIM=0 → CSDI-only server, same install
    for fn in tools:
        mcp.tool()(_safe(fn))
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
        # Never reuse a live preview port, or the check silently hits another server: pick a
        # free ephemeral one instead of a constant (8799 was taken on this machine and the
        # fetch below 404'd against an unrelated app).
        _s = socket.socket()
        _s.bind(("127.0.0.1", 0))
        globals()["PORT"] = _s.getsockname()[1]
        _s.close()
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
        # EPD 廢物回收點數量會變（989 → 990 seen 2026-10-28）；只鎖不變量，唔好鎖實數
        assert 900 <= sum(r["count"] for r in agg["rows"]) <= 1100, agg["rows"]
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
        # spatial join: counts must add up, and the overview must not guess a label field
        districts = "censtatd_rcd_1635932856094_19982"  # 18 district polygons
        ov = json.loads(spatial_join("epd_rcd_1631080339740_69941", districts))
        assert ov.get("label_fields") and ov.get("hint"), ov
        jn = json.loads(spatial_join("epd_rcd_1631080339740_69941", districts, "dc_eng"))
        assert 900 <= jn["points"] <= 1100 and jn["matched"] + jn["unmatched"] == jn["points"], jn
        assert jn["matched"] >= 980, f"join matched only {jn['matched']} of {jn['points']}"
        assert sum(r["count"] for r in jn["rows"]) == jn["matched"], jn["rows"]
        print(f"  join        -> {jn['points']} points, {jn['matched']} inside "
              f"{jn['polygons']} polygons, chart {'yes' if jn.get('chart_url') else 'no'}")
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
        # ---- BIM (i3s) + statutory checks
        # codec: three page layouts that all exist in the wild
        assert _i3s_decode(_struct.pack("<II", 2, 7) + _struct.pack("<2I", 3, 4) + b"AB\x00CDE\x00") \
            == ["AB", "CDE"]
        assert _i3s_decode(_struct.pack("<I", 2) + _struct.pack("<2i", 7, 9)) == [7, 9]
        assert _i3s_decode(_struct.pack("<II", 2, 0) + _struct.pack("<2d", 1.5, 2.5)) == [1.5, 2.5]
        assert _empty(0, "Mark") and not _empty(0, "BldgLevel")
        bim_url = ("https://tiles.arcgis.com/tiles/V6ZHFr6zdgNZuVG0/arcgis/rest/services/"
                   "Bldg_E_Color_UC2020_demo/SceneServer")
        bm = json.loads(bim_audit(bim_url))
        # discipline sublayers, not the Overview copy: they nest under group sublayers and
        # were unreachable before the enumeration recursed (Overview alone was 24,787)
        assert bm["elements"] == 17237, bm["elements"]
        assert bm["completeness_pct"]["Mark"] >= 99, bm["completeness_pct"]["Mark"]  # % empty
        assert bm["tags"]["duplicated"] >= 4, bm["tags"]
        print(f"  bim audit   -> {bm['elements']} elements, Mark {bm['completeness_pct']['Mark']}% empty,"
              f" {len(bm['verdict'])} findings")
        fl = json.loads(bim_floors(bim_url))
        assert fl["slabs_total"] == 163 and fl["coverage_ok"] is False, fl
        print(f"  bim floors  -> {fl['slabs_total']} slabs on {fl['levels_with_slabs']}/"
              f"{fl['levels_present']} levels, coverage_ok={fl['coverage_ok']}")
        el = json.loads(bim_elements(bim_url, "Category=Doors", 5))
        assert el["matched"] == 256 and el["returned"] == 5, el
        gr = json.loads(bim_elements(bim_url, "", 3, "ElementType"))
        assert gr["rows"] and gr["rows"][0]["count"] == 13360, gr["rows"][:2]
        print(f"  bim query   -> {el['matched']} doors, top ElementType {gr['rows'][0]}")
        sz = json.loads(site_zoning(lat=22.337, lon=114.175, radius_m=100))
        assert any(z["zone"] == "OU" for z in sz["zones"]), sz["zones"][:3]
        assert sz["bbox"][0] < 114.175 < sz["bbox"][2], sz["bbox"]
        print(f"  site zoning -> {len(sz['zones'])} zones, {sz['zones'][0]['plan_no']} "
              f"{sz['zones'][0]['zone']}, {sz['registered_slopes']['count']} slopes")
        lots = _hk_query(HK_LOT, [114.170, 22.296, 114.178, 22.303], "LOTID,LOTCSUID")
        assert len(lots) > 100, len(lots)
        print(f"  lot query   -> {len(lots)} lots in a 0.008 deg box")
        print("selftest OK:", s["features"], "features,", out["url"], "| BIM", bm["elements"], "elements")
    else:
        main()
