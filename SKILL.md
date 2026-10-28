---
name: hk-spatial
description: Get Hong Kong CSDI spatial data onto a live web map, or publish/query it in ArcGIS Online — use whenever the user wants HK spatial data, a map, or ArcGIS content.
---

# HK Spatial (CSDI)

CSDI is the source of truth: 1,150+ official Hong Kong datasets. The map renderer is ArcGIS Maps SDK
(keyless public basemap), and ArcGIS Online tools are optional extras.

## The 3-step loop — do NOT write fetch code yourself

1. `search_hk_data("公廁")` / `search_hk_data("pedestrian")` → pick a `dataset_id`. **English or Chinese
   both work** (index covers 繁體/简体 titles + keywords + abstracts, with HK synonym expansion).
   No keyword = browse the whole catalogue page by page (`offset`). Check `exportable` — 13 datasets
   are metadata-only and have no GeoJSON to map.
2. `load_hk_data(dataset_id, name="Country Parks")` → returns a summary only. Never paste features
   into the chat; the data is already on disk.
3. `build_map([dataset_id, ...], "Title")` → 2D map at `http://localhost:8777/`. Tell the user to open
   it in their browser or the client's preview pane; refresh after each rebuild.
   Use `build_scene(...)` instead when relief, height or a walkthrough view matters — same data, 3D
   terrain, tilted camera (`tilt=60`), served at `/scene.html`.

## Spatial operations (no key needed)

- `build_map(ids, basemap="dark-gray", labels=True)` — keyless styles: imagery, topographic, streets,
  light-gray, dark-gray, terrain, hillshade, ocean, physical, natgeo, shaded-relief.
- `geocode_address("Central, Hong Kong")` → lon/lat candidates; `geocode_address(lon=…, lat=…)` →
  street address (Chinese in HK).
- `elevation_at(lon, lat)` → ground elevation in metres (terrain context for trails, slopes, views).
- `export_static_map(bbox="minx,miny,maxx,maxy", basemap="streets")` → basemap-only PNG for reports.
- `geometry_op("project", geom, in_sr=2326, out_sr=4326)` — CSDI layers are sometimes published in
  HK1980 Grid (2326); project before combining with WGS84 data.
- `geometry_op("buffer", geom, distance=500)` — buffer/coverage rings returned as GeoJSON.
- `geometry_op("simplify", geom, max_deviation=0.0001)` — large polygons before putting them on a map.
- Do the arithmetic locally when it is simple (distance, counts). Use the service when you need real
  projection or a proper geodesic buffer.

## What needs a token, and what does not

Keyless and verified: the Geometry service (project/buffer/simplify), the World Geocoder, basemap and
terrain tiles. Everything below requires `ARCGIS_TOKEN` and mostly consumes credits — read the task's
`?f=json` before calling it, never invent parameters:

| Task | Endpoint pattern | Notes |
|---|---|---|
| AGOL standard analysis | `analysis.arcgis.com/arcgis/rest/services/tasks/GPServer/<Task>/submitJob` then poll `jobs/<jobId>` | credits |
| Feature query / statistics | `<featureServiceUrl>/0/query?where=…&outStatistics=[…]` | no credits |
| Routing | `route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve` | credits |
| GeoEnrichment (demographics) | `geoenrich.arcgis.com/arcgis/rest/services/World/geoenrichmentserver/GeoEnrichment/enrich` | credits |
| Print / export map | `utility.arcgisonline.com/arcgis/rest/services/Utilities/PrintingTools/GPServer/Export%20Web%20Map/submitJob` | async job |

## ArcGIS Online (optional)

- `arcgis_request("search", {"q": "owner:me", "num": 10})` — find items
- `arcgis_request("content/items/<id>/data")` — item JSON
- `arcgis_request("<featureServiceUrl>/0/query", {"where": "1=1", "returnCountOnly": "true"})` — counts
- `publish_layer(dataset_id, "My Layer")` — new hosted feature layer (costs credits/capacity)
- `dataset_status(dataset_id, probe=True)` — is it still served, and how stale? (no id = the oldest 10)
- Analysis = find the GP service task and `submitJob`, then poll `jobs/<jobId>`; same `arcgis_request`.

If `ARCGIS_TOKEN` is missing the tool says so — tell the user to put a **user/OAuth token** in the MCP
server `env` block. An API key will not work for content.

## BIM scene layers (i3s) — audit, never guess

BIM on ArcGIS is an i3s scene layer (`.slpk` / `/SceneServer`). There is **no** `/query`: the service may
advertise Query, but `/layers/{n}/query` answers `Invalid URL`. These tools read the attribute pages for
you, so do not try to query a scene layer directly and do not install the `arcgis` package.

- `bim_audit(source)` → elements, levels, completeness, duplicate tags, catalogue, slab coverage, verdict.
- `bim_floors(source)` → slabs per level + bbox area (an upper bound, not GFA).
- `bim_elements(source, where="Category=Doors", limit=20, group_by="ElementType")` → filter or count.
- `site_zoning(source=<model> | lat=…&lon=…)` → OZP zone, height control, slopes, landslides.
- `lot_check(source)` → lots under the model extent (bbox level) + zone labels.

Workflow when someone hands you a model: `bim_audit` → `bim_floors` → `site_zoning` → answer with a table
plus the `links.map_viewer` / `links.scene_viewer` pair. One read caches every row (`refresh=True` to
re-read); a second call is instant.

Never claim: GFA, floor area, travel distance, compartment size, clash detection, or "compliant". Slab
bbox area is an upper bound and only meaningful when `bim_floors().coverage_ok` is true. Report pass /
fail / **cannot check** — the third state is the honest one when geometry is missing. `Mark` of 0 means
unset, not a tag; >90% empty means the model cannot drive asset tracking. OZP/BHC here are public
mirrors: the statutory plan prevails.

## Rules

- Keep "Powered by Esri" attribution on every map.
- Say which dataset id every layer came from; never present derived/estimated values as official CSDI data.
- If a keyword returns nothing, retry with synonyms (or a shorter Chinese term) before telling the user
  the data does not exist.
- Big layers exist (Registered Man-made Slopes = 61,527 features). Warn the user before putting them in
  a live demo instead of hoping the browser copes.
