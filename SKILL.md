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
3. `build_map([dataset_id, ...], "Title")` → returns `http://localhost:8777/`. Tell the user to open it
   in their browser or the client's preview pane; refresh after each rebuild.

## ArcGIS Online (optional)

- `arcgis_request("search", {"q": "owner:me", "num": 10})` — find items
- `arcgis_request("content/items/<id>/data")` — item JSON
- `arcgis_request("<featureServiceUrl>/0/query", {"where": "1=1", "returnCountOnly": "true"})` — counts
- `publish_layer(dataset_id, "My Layer")` — new hosted feature layer (costs credits/capacity)
- Analysis = find the GP service task and `submitJob`, then poll `jobs/<jobId>`; same `arcgis_request`.

If `ARCGIS_TOKEN` is missing the tool says so — tell the user to put a **user/OAuth token** in the MCP
server `env` block. An API key will not work for content.

## Rules

- Keep "Powered by Esri" attribution on every map.
- Say which dataset id every layer came from; never present derived/estimated values as official CSDI data.
- If a keyword returns nothing, retry with synonyms (or a shorter Chinese term) before telling the user
  the data does not exist.
- Big layers exist (Registered Man-made Slopes = 61,527 features). Warn the user before putting them in
  a live demo instead of hoping the browser copes.
