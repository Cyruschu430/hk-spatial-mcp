# hk-spatial-mcp

![HK spatial map](preview.png)

Local MCP server for WorkBuddy / CodeBuddy: **Hong Kong CSDI spatial data + ArcGIS**, so an AI agent can
go from a sentence to a live ArcGIS web map without writing fetch code.

## Install (WorkBuddy / CodeBuddy)

`~/.workbuddy/mcp.json` (user level) or `<project>/.workbuddy/mcp.json` (project level):

```json
{
  "mcpServers": {
    "hk-spatial": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Cyruschu430/hk-spatial-mcp", "hk-spatial-mcp"],
      "env": { "ARCGIS_TOKEN": "", "HK_MCP_PORT": "8777" }
    }
  }
}
```

GUI path: 側邊欄 **插件 → MCP 伺服器 → 配置 MCP**, paste the JSON, wait for the 🟢 light.

Then in the chat:

> 幫我搵香港郊野公園數據，出個 ArcGIS web map 喺 preview 睇

## Tools

| Tool | Does |
|---|---|
| `search_hk_data(keyword="", limit=10, offset=0)` | search **all ~1150 CSDI datasets** (English **or Chinese**, ranked). Empty keyword = browse |
| `load_hk_data(dataset_id, name="")` | download dataset as GeoJSON (WGS84) → **summary** (count, fields, bbox, file path) |
| `build_map(dataset_ids, title)` | build the ArcGIS Maps SDK web map + serve it on `http://localhost:8777` |
| `arcgis_request(path, params, method)` | any ArcGIS Online REST call with the token (search items, item data, feature queries, GP jobs) |
| `publish_layer(dataset_id, title)` | upload GeoJSON to ArcGIS Online as a hosted feature layer → `itemId` + item URL |

`ARCGIS_TOKEN` is only needed for the last two. An ArcGIS **API key** covers basemaps/geocoding but cannot
read or create org content — use a user/OAuth token.

## Notes

- **Coverage**: the whole CSDI catalogue (1154 datasets, verified 2026-10-22) is harvested in one
  request and cached in the workspace for 7 days (`catalog.json`) — search runs offline over titles,
  keywords and abstracts in English + 繁體 + 简体, so ranking is not limited to what a server-side
  `AnyText` match returns. 13 metadata-only datasets have no GeoJSON export (`exportable: false`).
- **Chinese queries** are expanded with Hong Kong wording (廁所 → 洗手間, 行人路 → pedestrian,
  電動車 → electric vehicle charging, 巴士 → bus, 郊野公園 → country park, …).
- Basemap is keyless (Esri World Imagery tiles) with the SDK's own attribution — keep "Powered by Esri".
- Data source: CSDI portal (`portal.csdi.gov.hk`), CSW 2.0.2 catalogue + `file-api` GeoJSON (EPSG:4326).
- Workspace defaults to `./hk-spatial` (`HK_MCP_DIR`), preview port `HK_MCP_PORT` (default 8777).

## Verify

```bash
python3 hk_spatial_mcp.py --selftest     # search → load → map → served page, asserts HK coords
```
