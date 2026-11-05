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

## Earth observation (satellite — discovery is keyless)

- `eo_search(bbox="minx,miny,maxx,maxy", start="YYYY-MM-DD", end="YYYY-MM-DD", collection="sentinel-2-l2a",
  max_cloud=30, limit=10)` → scene metadata from Microsoft Planetary Computer, plus a 512 px preview
  PNG per scene. The preview is the whole granule — say so, do not imply it was cut to the bbox.
  Collections: sentinel-2-l2a, landsat-c2-l2, naip, cop-dem-glo-30, sentinel-1-rtc, io-lulc-annual-v02.
- `eo_stats(bbox, start, end, index="NDVI"|"NDBI"|"NDMI", max_cloud=30)` → mean index per month,
  statistics only (a few kB, no raster). Needs free Copernicus OAuth credentials; without them the
  tool says which variable is missing instead of guessing a value.

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

Both i3s generations read the same way: 1.7+ lists nodes in `nodepages/{n}`, while 1.4–1.6 (anything
published before ~2019) inlines the tree in `nodes/root` and answers 404 on nodepages — the reader tries
one then the other, so an old service is not a dead end. Hosted Building layers nest disciplines under
group sublayers ('Full Model' → 'Structural' → 'Beams'); all of them are merged, each row tagged with
`sublayer`. A 61-discipline model is 1,897 nodes: the first read takes minutes, then it is cached.

- `bim_audit(source)` → elements, levels, completeness, duplicate tags, catalogue, slab coverage, verdict.
- `bim_floors(source)` → slabs per level + bbox area (see the area warning below).
- `bim_elements(source, where="Category=Doors", limit=20, group_by="ElementType")` → filter or count.
- `site_zoning(source=<model> | lat=…&lon=…)` → OZP zone, height control, slopes, landslides.
- `lot_check(source)` → lots under the model extent (bbox level) + zone labels.

### Driving the map (do this, not just a table)

Open a viewer, then act on it. The page polls its state file, so every later call updates the open tab
with no reload — the three tools below all take the same `viewer` handle.

- `bim_viewer(source, title="", overlay="<CSDI ids>")` → `viewer` handle + `url` (open it), plus
  `levels` / `categories` / `disciplines` with element counts. **Call this first.**
- `bim_filter(viewer, floors="5,10", categories="Walls,Doors", disciplines="Structural", clear=False)` →
  narrows what draws; returns `elements_shown` / `elements_total` so the number is real, not a guess.
  Floors use the `BldgLevel` number, not an elevation. An unknown name is an error listing the valid ones.
- `bim_select(viewer, uids="Doors/0:33" | ids=[…] | where="Category=door AND BldgLevel=10", isolate=True)` →
  flies the camera and marks the elements; returns `matched`, a `record` card, and `ghost`.
- `bim_clear(viewer)` → whole model, no selection.

Name an element by `uid`, never by `id`. `bim_elements()` rows carry `uid` = `sublayer/node:id`, and that
triple is the only unique key: i3s feature ids restart inside a node *and* node ids restart in every
sublayer, so `ids=[33]` on the ZCB model matches 17 different elements and `ids` cannot be trusted to mean
one thing. `bim_select` reports `unknown_uids`, so a typo is visible instead of silently selecting nothing.

**Ghosting.** For a focused selection `bim_select` leaves the rest of the building as a wire-frame
outline rather than hiding it, so the element reads in context. Two traps were measured, both of which
look like the feature being broken:

- Filter by the **`Category` attribute**, not the sublayer title. The attribute holds lowercase Revit
  values (`'door'`) while the sublayer is called `Doors`. A bare quoted string (`'Doors'`) is not a
  predicate — it silently matched *everything*, the solid block covered the whole building, and no ghost
  appeared.
- Ghosting must **not** be combined with `sublayer.visible`. Shell sublayers (`ExteriorShell`, `Site`,
  `Stairs`) are absent from the element catalogue, so a hide-by-catalogue list leaves the envelope drawn
  solid on top of the ghost.

Only the *two-block* form ghosts: block 1 = the targets `solid`, block 2 = `1=1` as `wire-frame`. The mode
values are hyphenated (`wire-frame`, `x-ray`); bare `wireframe`/`xray` throw inside the SDK.

Which mechanism actually filters, measured in a browser rather than assumed:

| what you reach for | result |
|---|---|
| `BuildingFilter` expression (`BldgLevel` / `Discipline` / `Category`) | **works** — isolates a storey, a discipline, a category |
| second block `1=1` as `wire-frame` | **works** — ghosts the context, keeps the target solid |
| `sublayer.visible` | **works** — the reliable category switch |
| `sublayer.definitionExpression` | **no effect** — 0.0% pixel change; never rely on it |
| `sublayer.queryFeatures` / `queryExtent` | **throws** `query-not-available` on the sublayer too |
| `BuildingFilter` on `OBJECTID` / `ObjectId` | **matches ~17 elements, not 1** — see below |

Two claims from the Esri docs do not hold for hosted building layers. `queryFeatures()` throws even on a
*sublayer* (the docs say to query the sublayer, not the root), and so does `queryExtent()`, so no geometry
can be read in the browser at all. And isolating one component with `OBJECTID = n` does not work: on ZCB
`OBJECTID_1 = 33` matches **17** elements across 17 sublayers — a beam, a column, a door, a pipe — because
the field restarts per sublayer. The rendered result is a building-shaped scatter, not one component
(verified by screenshot). Use `uid` and ghost instead.

- `position` in an i3s node is the **node anchor**, not the element — one node's 241 features share it. It
  still locates the building correctly (the model's ground pad draws exactly at that centre), so frame by
  it; just do not read it as a per-element coordinate.
- The service's declared `fullExtent` is not a reliable camera target either: on the ZCB layer that
  document sat ~200 m from the geometry. Aim at the element-position extent instead.
- With ghosting on, the camera frames the **building**, because the whole point is "where is this inside
  it". Aiming at a single element gives a zero-span box and puts the eye inside the roof (measured: the
  viewport filled with one door panel). The vertical aim comes from `BldgLevel_Elev`, not a fixed height —
  storeys are real elevations (a storey-10 door on ZCB is at 3.95 m), so a hard-coded 12 m put it low in
  frame and mostly showed car park.

A hosted BSL takes a long time to stream on first paint (measured ~40 s for a 14 k-element model on
software rendering). If the page looks empty, wait — do not conclude the layer failed.

Workflow when someone hands you a model: `bim_audit` → `bim_floors` → `site_zoning` → **`bim_viewer`**, then
`bim_filter` / `bim_select` to show the finding → answer with a table plus the `links.map_viewer` /
`links.scene_viewer` pair. One read caches every row (`refresh=True` to re-read); a second call is instant.

Never claim: GFA, floor area, travel distance, compartment size, clash detection, or "compliant". Report
pass / fail / **cannot check** — the third state is the honest one when geometry is missing. `Mark` of 0
means unset, not a tag; >90% empty means the model cannot drive asset tracking. OZP/BHC here are public
mirrors: the statutory plan prevails.

Two field names lie, so quote the numbers with care:

- `completeness_pct` is **% empty**, not % filled. `Mark: 86.0` means 14% of elements carry one.
- `bim_floors().per_level[].area_m2_bbox` stays an upper bound only if the export publishes a bbox per
  feature. When it does not, every feature in a node inherits the node's bbox (CIC: 410 slabs, 67
  distinct boxes), and per-level "area" sums node boxes — 15× the site. Count slabs; do not add areas.

### Floor filtering on a non-BSL model — check before you promise it

A BIM in a **web scene** is often not one `BuildingSceneLayer` but a `GroupLayer` of sibling
`ArcGISSceneServiceLayer`s (Building E is 21). Then there is no sublayer tree and no `BuildingFilter`,
and `BldgLevel` is only an attribute — so whether a storey can be isolated depends on how the exporter
grouped geometry into i3s **nodes**, which is the unit the renderer hides. A predicate selects a node,
never elements inside it.

Building E, measured: `Wall_Mullions` = 3,910 elements (98.3% of the model) in **34 nodes**, and
**0 of 34 nodes are level-pure** (14 hold levels 0+1+3+4, 19 hold 1+3+4; median element height
20.55 m). Filtering `BldgLevel` there returns whole nodes spanning the full façade — "L0" draws the
entire glass frame. Revit→i3s exports commonly group by element *type*, not storey.

So: count nodes against elements, and check whether any node holds a single level. If none does,
**say the filter cannot work and report it as a finding** — do not quietly return the whole model.

The mechanism that does work is to **cut the geometry**, since `SliceAnalysis` removes everything
above a plane regardless of node grouping. Verified by z-sampling `hitTest()` either side of the cut:

| plane z | hits span |
|---|---|
| 408.20 | 391.73 – 408.20 |
| 403.61 | 391.73 – 403.60 |
| 399.20 | 391.73 – 399.18 |
| 394.65 | 391.73 – 394.60 |

SDK 5.0 gotchas, each measured:

- `analysis/SliceAnalysis.js` is correct; `views/analysis/SliceAnalysis.js` is a **404**.
- Two opposing planes to form a storey **band do not compose** — both accepted, band not formed.
- `view.clippingArea` in local viewing mode **silently does nothing** (before/after identical).
- `elevationInfo.mode` must be hyphenated: `"absolute-height"`, not `"absoluteHeight"`.
- `tilt` is measured **from vertical** — 0 is straight down, 90 is the horizon.
- `hitTest()` hits carry `mapPoint` but **no `graphic`**; filter on `mapPoint.z`.
- Put the cut **just below** the slab top, or the plane is capped by the slab's own solid sheet and
  the section reads as a blank plate. A 4.6 m storey needs its own distance and tilt to be legible.

### When a generated page renders blank

Try it on `http://127.0.0.1:8777` (the built-in server sends no CSP) before debugging any code. If it
works there, a **Content Security Policy** on the original host is blocking the ArcGIS CDN — typically
`script-src` allowing `https:` (so modules load and the panel draws) while `connect-src` refuses the
tiles. Measured: 79 violations and 0/21 layers on the locked-down host, 0 violations and 21/21 on
localhost. `connect-src` is the blocker. Do not report a CSP failure as an application bug, and do not
chase widget errors that vanish on localhost.

## Rules

- Keep "Powered by Esri" attribution on every map.
- Say which dataset id every layer came from; never present derived/estimated values as official CSDI data.
- If a keyword returns nothing, retry with synonyms (or a shorter Chinese term) before telling the user
  the data does not exist.
- Big layers exist (Registered Man-made Slopes = 61,527 features). Warn the user before putting them in
  a live demo instead of hoping the browser copes.
