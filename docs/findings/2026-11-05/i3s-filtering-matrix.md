# i3s filtering — what actually works

Measured in Chromium against hosted Building / scene layers with Maps SDK **5.0**. The negative
results are the point: each one fails *silently* or throws something unhelpful, so an agent that has
not met them will return a confident wrong answer.

---

## The matrix

| mechanism | result | evidence |
|---|---|---|
| `BuildingFilter` expression on `BldgLevel` / `Discipline` / `Category` | **works** | isolates a storey or discipline; used by `bim_filter` |
| second `BuildingFilterBlock` `1=1` as `wire-frame` | **works** | ghosts context, target stays solid |
| `sublayer.visible` | **works** | the reliable category switch |
| `sublayer.definitionExpression` | **no effect** | 0.0% pixel change across before/after screenshots |
| `sublayer.queryFeatures` | **throws** `query-not-available` | on the sublayer, not just the root |
| `sublayer.queryExtent` | **throws** `query-not-available` | same |
| `BuildingFilter` on `OBJECTID` / `ObjectId` | **matches ~17, not 1** | field restarts per sublayer |
| `BuildingSceneLayer` on these sibling services | **fails to load** | `Cannot set properties of null (setting 'spatialReference')` |
| two opposing `SliceAnalysis` planes as a band | **no band** | both accepted, geometry unchanged between them |
| `view.clippingArea` in local viewing mode | **no effect** | before/after identical |
| `SliceAnalysis` single plane | **works** | cuts i3s geometry regardless of node grouping |

---

## The two that contradict the published guidance

Esri's docs say, for a building scene layer, to query the **sublayer** rather than the root, and that
one component can be isolated with `OBJECTID = n`. On a hosted BSL neither holds:

1. **`queryFeatures()` / `queryExtent()` throw on the sublayer too.** So there is no way to read
   element geometry from the browser at all. This is why `bim_select` takes positions from the cached
   i3s attribute pages (`_bim_rows`) instead of from the view.

2. **`OBJECTID = n` is not unique.** `OBJECTID_1` restarts in every sublayer. On ZCB, `OBJECTID_1 = 33`
   matches 17 elements — a beam, a column, a door, a pipe — and renders as a building-shaped scatter,
   not one component. Verified by screenshot: the result looks like the whole model, which is exactly
   the failure mode that makes an agent report success.

The correction is `uid` = `sublayer/node:id`, which is unique because it names the sublayer and node
explicitly. `BuildingSceneLayer` cannot even load these services, so `BuildingFilter` is unavailable
when the BIM is published as 21 sibling `ArcGISSceneServiceLayer`s — which is the common shape for a
web scene exported from Revit.

---

## Ghosting — the mechanism, and its two traps

Ghosting draws the selected elements solid and everything else as a wire-frame outline. It is the
honest alternative to per-instance isolation, which i3s does not offer.

It requires **two blocks**:

```js
new BuildingFilter({ filterBlocks: [
  new BuildingFilterBlock({ title: "selection",     filterExpression: expr,
                            filterMode: { type: "solid" } }),
  new BuildingFilterBlock({ title: "context (ghost)", filterExpression: "1=1",
                            filterMode: { type: "wire-frame" } }),
]});
```

### Trap 1 — filter the `Category` attribute, not the sublayer title

The attribute holds lowercase Revit values (`'door'`) while the sublayer is called `Doors`. A bare
quoted string is not a predicate:

```js
// silently matches EVERYTHING — the solid block covers the whole building, no ghost appears
filterExpression: "'Doors'"

// correct
filterExpression: "Category IN ('door')"
```

The failure is invisible: the filter applies without error, and the result looks like the ghost simply
did not work.

### Trap 2 — do not also set `sublayer.visible`

Shell sublayers (`ExteriorShell`, `Site`, `Stairs`) are absent from the element catalogue. Hiding
"every category except Doors" therefore leaves the envelope drawn solid *over* the ghost. The ghost
block applies, and nothing looks ghosted.

Measured: the ghost only reads correctly with **0 hidden sublayers** and the category predicate doing
the selecting.

### Mode values are hyphenated

```js
{ type: "wire-frame" }   // works
{ type: "x-ray" }        // works
{ type: "wireframe" }    // throws: Cannot read properties of null (reading 'type')
{ type: "xray" }         // throws: same
```

The bare forms are accepted at construction and throw later, inside the SDK.

---

## SDK 5.0 API traps

| what you reach for | what actually happens |
|---|---|
| `views/analysis/SliceAnalysis.js` | **404** — correct path is `analysis/SliceAnalysis.js` |
| `elevationInfo.mode: "absoluteHeight"` | invalid — must be **`"absolute-height"`** |
| `tilt` | measured **from vertical**: 0 = straight down, 90 = horizon |
| `view.goTo({ center, scale, tilt })` | resolves without error, **camera does not move** |
| `view.hitTest()` on hits | carry `mapPoint` but **no `graphic`** — filter on `mapPoint.z` |
| `view.hitTest()` to detect a loaded BSL | **returns empty even when the model is on screen** |
| `map` with `ground: null` | `view.when()` **never resolves** — set `ground.opacity = 0` instead |
| `Search.suggest()` | resolves to a **non-array** object — `.length` reads 0 on success |
| `Search.allSources` | populated **asynchronously** after `when()` |
| Zoom/Compass button labels | in **`title`**, not `aria-label` |

Each cost a debugging cycle. The `hitTest` row is the dangerous one: it makes "is the model drawn?"
unanswerable from the page, so a slow first paint looks identical to a failure. A hosted BSL takes
**~40 s** for a 14 k-element model on software rendering.

---

## Camera aiming

The viewer assigns a `Camera` directly, because `goTo({center, scale})` resolves without moving
anything. The view centre is where the optical axis meets the ground, so with the eye at height `z`
and tilt `t`, the camera must sit `z·tan(t)` **behind** the target along the heading. Placing it there
centres the target in one step — measured error **2 px horizontal, 27 px vertical** — instead of
iterating a feedback loop that never converged.

Two facts that look like bugs and are not:

- **`position` in an i3s node is the node anchor, not the element.** One node's 241 features share it.
  It still locates the building correctly (the drawn model's ground pad lands exactly at that centre),
  so frame by it — just do not read it as a per-element coordinate.
- **The declared `fullExtent` is unreliable.** On ZCB that document sat ~200 m from the geometry, and
  the SDK's own value only converges once every tile has streamed in. Aim at the element-position
  extent.

Vertical aim comes from `BldgLevel_Elev`, not a fixed height. Storey numbers are not heights: on ZCB
`BldgLevel = 10` sits at 3.95 m, so a hard-coded 12 m put the element **~77 px low** in an 800 px
view, framed mostly on car park. The average must be guarded with `math.isfinite` — unset levels carry
`NaN`, `isinstance(nan, float)` is `True`, and one `NaN` poisons the mean into `NaN`, which
`json.dumps` writes raw and the page's `JSON.parse` rejects, silently dropping the whole state update.
