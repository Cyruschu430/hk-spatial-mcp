# hk-spatial-mcp

![HK spatial map](preview.png)

An **MCP server for Hong Kong CSDI spatial data** — any MCP-capable AI agent can search the whole
Common Spatial Data Infrastructure catalogue (1,154 official datasets), pull the geometry, and turn it
into a live interactive map, without writing fetch code or knowing the portal's quirks.

> **Unofficial, community project.** CSDI is operated by the Spatial Data Office, Development Bureau
> (HKSAR Government). The Office has said it plans to develop its own CSDI MCP server in 2027 — this
> repo is an independent implementation and claims no affiliation.

Data is the point: **CSDI is the source of truth here**. The bundled web map is rendered with the
ArcGIS Maps SDK (keyless public tiles, "Powered by Esri" attribution kept), and optional tools can
publish to / read from ArcGIS Online — but nothing about the server requires ArcGIS to be useful.

## What it gives an agent

1. **Search** the entire CSDI catalogue offline-cached, English or 繁體/简体, ranked.
2. **Load** any dataset as GeoJSON (EPSG:4326) with a summary, not a wall of features.
3. **Map** one or many datasets into an interactive web map served on localhost.
4. *(optional)* **Talk to ArcGIS Online** — publish a dataset as a hosted feature layer, or call any
   REST endpoint (item search, feature queries, GP jobs) with your own token.

## Install — any MCP client

Generic stdio server, no daemon, no service account. Add it to whatever MCP client you use:

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

Where that file lives depends on the client — e.g.
`~/Library/Application Support/Claude/claude_desktop_config.json` (Claude Desktop), `.cursor/mcp.json`
(Cursor), `~/.workbuddy/mcp.json` (WorkBuddy / CodeBuddy), `~/.codex/config.toml` (Codex). Any client that
speaks MCP over stdio works; the config shape above is the common one.

Then in the chat:

> 幫我搵香港郊野公園數據，出個 map 喺 preview 睇

## Copy-paste prompts (30-second start)

Paste any of these into your agent after the server shows a green/connected status.

**1. Smoke test — does it work at all?**
```
用 hk-spatial 搜「郊野公園」，列返前 5 個 dataset 俾我睇。
```

**2. The 30-second demo — data → map → preview**
```
用 hk-spatial 搵香港郊野公園數據，載入之後出一個 web map，標題「HK Country Parks」，
話我知 preview URL。
```

**3. Chinese query, single layer**
```
用 hk-spatial 搵全港公廁/洗手間嘅位置數據，出個 map。
```

**4. Multi-layer by theme (no dataset id needed)**
```
用 hk-spatial 一次過搵：電動車充電站、公立醫院、行人路網絡，全部放埋同一個 map，
每層唔同顏色，圖例寫返個 dataset 名。
```

**5. Iterate on the same map**
```
用 hk-spatial 再加多層「巴士路線」落而家嗰個 map，加完話我知。
```

**6. Ask what exists before guessing**
```
用 hk-spatial 列出同「斜坡安全」有關嘅全部 dataset，逐個講一句係咩數據。
```

**7. Publish to ArcGIS Online** *(needs `ARCGIS_TOKEN`)*
```
用 hk-spatial 將郊野公園呢個 dataset publish 上我 ArcGIS Online，標題「HK Country Parks」，
出 item link 俾我。
```

**8. Read your own org content** *(needs `ARCGIS_TOKEN`)*
```
用 hk-spatial 嘅 arcgis_request 搵我 ArcGIS Online 帳戶下面最近 5 個 feature layer，
列返名同 item id。
```

**9. Make the agent always follow the loop** — load `SKILL.md` as the agent's skill / system prompt.
It pins: search → load → build_map, never invent data, never paste features into the chat, keep
"Powered by Esri".

Tip: if the agent free-forms and starts writing its own `requests` code, tell it
**"唔好自己寫 fetch code，用 hk-spatial 嘅 tool"** — the tools already handle the CSDI quirks
(CSW search, GeoJSON export, EPSG:4326, 13 metadata-only datasets that 404 on export).

## Tools

| Tool | Does |
|---|---|
| `search_hk_data(keyword="", limit=10, offset=0)` | search **all ~1150 CSDI datasets** (English **or Chinese**, ranked). Empty keyword = browse |
| `load_hk_data(dataset_id, name="")` | download dataset as GeoJSON (WGS84) → **summary** (count, fields, bbox, file path) |
| `build_map(dataset_ids, title)` | build an interactive web map from the loaded datasets, served on `http://localhost:8777` |
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
  電動車 → electric vehicle charging, 巴士 → bus, 郊野公園 → country park, 斜坡安全 → slope, …).
- **Big layers**: `file-api` returns the full dataset (Registered Man-made Slopes = 61,527 features /
  153 MB). Layers over 3000 features are streamed from the served file instead of inlined into the
  page, and >60 MB gets an explicit warning — the data is never silently sampled.
- Basemap is keyless (Esri World Imagery tiles) with the SDK's own attribution — keep "Powered by Esri".
- Data source: CSDI portal (`portal.csdi.gov.hk`), CSW 2.0.2 catalogue + `file-api` GeoJSON (EPSG:4326).
  CSDI data is published by the HKSAR Government; check each dataset's licence before redistribution.
- Workspace defaults to `./hk-spatial` (`HK_MCP_DIR`), preview port `HK_MCP_PORT` (default 8777).

## Verify

```bash
python3 hk_spatial_mcp.py --selftest     # catalogue search + load + map + served page, asserts HK coords
```
