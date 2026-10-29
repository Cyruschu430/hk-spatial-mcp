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

## 4. `addItem` 409 on large multipart uploads — duplicate delivery, not a name clash

Uploading a 263 MB package twice produced `409 "Item '<name>.slpk' already exists"` even
for names never used before, while search showed no such item. Both uploads had in fact
**landed** (the items existed, correct size, correct bytes served back via `/data`);
AGOL's front end had delivered the multipart request twice and the losing copy's 409 won
the race back to the client.

**Handling.** Treat a 409 after a big upload as "probably already there": search the
caller's content for the freshly uploaded item (by title/type/created time) and reuse it
rather than re-uploading 263 MB. (`publish_scene` currently re-uploads; see roadmap.)

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
