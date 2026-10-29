# Publishing Scene Packages to ArcGIS Online — field notes

Everything below was observed live against `www.arcgis.com` on 2026-10-29 while building
`publish_scene`. Each entry is a failure mode that cost a debugging round, what it actually
means, and how the code handles it. Numbers/errors are verbatim where quoted.

## 1. `500 "Unable to determine Service Type"` — missing `outputType`

Publishing a Scene Package item with the documented parameters (`itemId`, `filetype`,
`publishParameters.name`) fails with this generic 500 for **any** package variant
(3DObject, Building; canonical or lowercase root doc).

**Cause.** `/content/users/<user>/publish` defaults to producing a *feature* service when
`outputType` is omitted — the parameter table lists only `Tiles | 3DTilesService |
3DTilesPackage`, and a scene package cannot become a feature service.

**Fix.** Send `outputType=sceneService`. Response becomes
`{"services":[{"type":"Scene Service","serviceurl":.../SceneServer,"jobId":...}]}`.

## 2. Publish job status — poll the NEW service item, not the source item

Both geojson and scene publishes return a `jobId`, but the source item tracks it
inconsistently. For scene publishes the **source package item answers `failed` for a job
that is building fine**; the new `serviceItemId` answers `processing` → `completed`.
Polling the source item makes every successful publish look like a failure and leaves the
service running.

**Fix.** `_await_publish` polls `svc["serviceItemId"]` first, falling back to the source
item only when no service item exists. Ghost service items from failed publishes are
garbage-collected by AGOL asynchronously and briefly linger in search results with
`url: null`.

## 3. Hosted scene service names — silent job failure

Two name shapes fail the publish job with no useful message (`status: failed`,
`statusMessage: failed`):

- double or trailing underscores (`regression_test_scene__delete_me_`)
- a name already used by any scene service in the org (even a ghost from a deleted run)

**Fix.** Sanitize the derived name (collapse `_+`, strip leading/trailing `_`), and probe
the caller's own content for a Scene Service whose URL ends with `/<name>/SceneServer`
*before* uploading — refuse up front and name the clashing item.

## 4. `addItem` 409 — the uploaded FILE NAME is the item name, and it must be unique

`addItem` creates the item with the **file name** as its name, and ArcGIS Online requires that
name to be unique in the user's folder. Uploading `f"{item_id}.slpk"` — the cached package's
own name — therefore 409s (`Item '<id>.slpk' already exists`) on the *second* publish of the
same model, including a publish that failed midway and left its package behind. What looked
like a phantom duplicate delivery was this name collision.

**Fix.** Derive the uploaded file name from the unique service name
(`fileName = f"{sname}.slpk"`), which is already sanitized and clash-checked, so publishing the
same model twice under two titles works. A 263 MB upload that still 409s now means an item with
that exact name exists — search for it before re-uploading.

## 5. Revit lowercase root doc — NOT a blocker (red herring)

Revit exports ship the i3s root document as `3dscenelayer.json.gz` (lowercase). Esri
documentation implies a case-sensitive `3dSceneLayer.json.gz` requirement, and an early
repack that *decompressed* the root doc did trip addItem validation
(`CONT_0175 Missing file in package: 3dSceneLayer.json.gz`). But verbatim packages with
the lowercase name pass addItem **and** publish. The original publish failures were
entirely explained by item 1. A rename-only repack was implemented, proven unnecessary,
and removed — upload pristine bytes.

## 6. Drawing a PRIVATE scene layer in the local viewer — IdentityManager normalization

`bim_findings(mode="scene")` on a private hosted service needs browser credentials, and
this was surprisingly hard:

- A `?token=` on the SceneLayer URL is **not** forwarded to i3s child requests
  (`/layers/0`, `/sublayers/N`, `/nodepages/*`) — the root `f=json` may load while every
  deeper request 499s.
- `IdentityManager.registerToken({server: "<service host>/arcgis/rest", token})` **never
  matches anything**: AGOL normalizes every `*.arcgis.com` host to `www.arcgis.com` for
  credential lookup, so requests leave without a token. The failure is silent —
  `layer.load()` never settles and never rejects, `view.when()` also stalls, and no
  console error appears. The only visible clue is an 83-byte
  `{"error":{"code":499,"message":"Token Required."}}` response body on an HTTP 200.
- Working form: `idMgr.registerToken({server: "https://www.arcgis.com", token})` before
  creating the layer. All requests then carry `token=` (verified in network traces) and a
  Building Scene Layer loads with its 2 sublayers and full node pages.

**Privacy posture.** The token is embedded only in the HTML written under the local
`HK_MCP_DIR`, served from `localhost`, and expires with the session. The tool never
shares published items; `publish_scene` output states this explicitly and the code has no
`/share` call path.

---

## 7. A published Building scene layer can draw NOTHING while everything reports success

`BuildingSceneLayer.load()` resolves, `layer.sublayers.length` is correct, `view.when()`
fires — and the model is completely absent. No error anywhere.

**Cause.** The service stores its root group (`Full Model`, `layerType: group`) with
`"visibility": false`, and the JS API honours that: an invisible root group hides the whole
tree even though every child component says `visibility: true`. The Scene Viewer and ArcGIS
Pro hide this because they re-apply their own layer visibility on open.

**Fix.** For an audit viewer, walk `bim.sublayers` after load and set `visible = true` on
every group and component. Measured on the GrandCentral Building Scene Layer: 0 % of the
frame before, **11 %** after — verified by toggling the layer and diffing `view.takeScreenshot()`
pixels, which is the only reliable check when no vision model is available.

## 8. Framing a tilted 3D camera: three traps, one working form

All three of these load the layer, report a camera, and leave the building off-screen:

1. `view.goTo({ target: extent, tilt, heading })` — the eye settles hundreds of metres from
   the target (measured: camera 3.7 km away on Building E).
2. `view.goTo({ target: { type: "point", longitude, latitude }, zoom, tilt })` — **silently
   no-ops**. The promise resolves, no error, camera unchanged.
3. `view.goTo({ ... }, { duration: 400 })` — can be interrupted or dropped; use
   `{ animate: false }` for a deterministic landing.

**Working form:** centre the extent, then derive the zoom from its span:

```js
const cx = (e.xmin + e.xmax) / 2, cy = (e.ymin + e.ymax) / 2;
const coslat = Math.cos(cy * Math.PI / 180);
const spanM = Math.max((e.xmax - e.xmin) * 111320 * coslat, (e.ymax - e.ymin) * 111320);
const zoom = Math.max(14, Math.min(20, Math.log2(156543.03 * coslat / (spanM / 840))));
view.goTo({ center: [cx, cy], tilt: 62, heading: 25, zoom }, { animate: false });
```

Verified: 19.8 % of the frame is the building on the Building E web scene, 11.2 % on
GrandCentral. Guard `wkid !== 4326` (Web Mercator extents) with a plain `goTo(extent)`.

---

## Enhancement roadmap

Picked up next, in rough priority order:

1. **Reuse on 409** (item 4): search-by-recency and reuse the already-landed upload
   instead of re-sending large packages.
2. **Overwrite/replace flow**: `replaceService`-style update of an existing hosted scene
   layer from a refreshed package (today: refuse on name clash; a deliberate replace is
   the better UX for iterating audits).
3. **Share control, still opt-in**: a separate `share_item(item_id, everyone|org|groups)`
   tool so users can publish deliberately — keeps `publish_scene` itself share-free.
4. **GitHub Actions CI**: run `test_bim_regressions.py` (token-less cases) on push; keep
   the token-gated case 9 manual.
5. **mbb area units for geographic-SRS models**: current slab areas assume projected
   metres; geographic models (e.g. WGS84 slpk) over-report.
6. **PyPI release**: `pipx run hk-spatial-mcp` install path.

Deferred ideas: OGC 3D Tiles packages (`outputType=3DTilesService`), point-cloud package
publishing, thumbnail generation for published items.
