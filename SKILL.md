---
name: hk-spatial
description: Get Hong Kong CSDI spatial data onto an ArcGIS web map, publish it to ArcGIS Online, or call ArcGIS REST — use whenever the user wants HK spatial data, a map preview, or ArcGIS publishing.
---

# HK Spatial (CSDI + ArcGIS)

## The 3-step loop — do NOT write fetch code yourself

1. `search_hk_data("country park")` → pick a `dataset_id`. **Keywords must be English** (CSDI metadata
   is English only): "public toilet", "pedestrian", "building", "rainfall", "Wan Chai", "Sha Tin".
2. `load_hk_data(dataset_id, name="Country Parks")` → returns a summary only. Never paste features
   into the chat; the data is already on disk.
3. `build_map([dataset_id, ...], "Title")` → returns `http://localhost:8777/`. Tell the user to open
   the Preview tab. Refresh the tab after each rebuild.

## ArcGIS Online

- `arcgis_request("search", {"q": "owner:me", "num": 10})` — find items
- `arcgis_request("content/items/<id>/data")` — item JSON
- `arcgis_request("<featureServiceUrl>/0/query", {"where": "1=1", "returnCountOnly": "true"})` — counts
- `publish_layer(dataset_id, "My Layer")` — new hosted feature layer (costs credits/capacity)
- Analysis = find the GP service task and `submitJob`, then poll `jobs/<jobId>`; same `arcgis_request`.

If `ARCGIS_TOKEN` is missing the tool says so — tell the user to put a **user/OAuth token** in the MCP
server `env` block. An API key will not work for content.

## Rules

- Keep "Powered by Esri" attribution on every map.
- Say which dataset id every layer came from; never present derived/estimated values as official.
- If a keyword returns nothing, retry with synonyms before telling the user the data does not exist.
