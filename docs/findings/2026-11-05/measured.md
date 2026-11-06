# Measured figures

Each row names the command that reproduces it. Run from `workspace/` (the `HK_MCP_DIR`, gitignored),
where the i3s attribute pages are cached as JSON.

> **Reading the cache.** The files carry bare `NaN`, so `json.loads` fails on them as written. Every
> snippet below sanitises first: `.replace(": NaN", ": null")`. This is a real property of the cache,
> not a typo — see [`open-questions.md`](open-questions.md#why-does-the-cache-contain-bare-nan).

---

## Model totals

| figure | value | how |
|---|---:|---|
| services in the web scene | 21 | one `ArcGISSceneServiceLayer` each, under a `GroupLayer` |
| elements, all services | 3,978 | sum of the 21 per-service caches |
| elements in `Wall_Mullions` | 3,910 | single cache |
| share in one service | **98.3%** | 3910 / 3978 |
| services holding ≤2 elements | 18 of 21 | see the per-service table below |
| levels present | 0, 1, 3, 4, 5 | **level 2 absent** |
| elements with `BldgLevel = NaN` | 4 | `Stair_Glass` 1, `StairRailings` 1, `ExtriorStairRailings` 2 |

```python
import json, glob, pathlib
seen = {}
for fp in glob.glob("bim/*.json"):
    d = json.loads(pathlib.Path(fp).read_text(encoding="utf-8").replace(": NaN", ": null"))
    sl, rows = d.get("sublayer") or "", d.get("rows") or []
    if sl.startswith("BuildingE") and rows:
        if sl not in seen or len(rows) > len(seen[sl]):
            seen[sl] = rows
print(len(seen), sum(len(v) for v in seen.values()))     # -> 21 3978
```

### Level census

| level | elements |
|---|---:|
| 0 | 106 |
| 1 | 1,269 |
| 3 | 1,346 |
| 4 | 1,251 |
| 5 | 2 |
| NaN | 4 |

Note level 5 holds **2 elements** — not a storey worth offering as a floor filter, but it is a real
level and `bim_viewer()` lists it. Level 2 is absent, so a caller asking for `floors="2"` gets an
empty result rather than an error.

### Per-service sizes

| service | elements | | service | elements |
|---|---:|---|---|---:|
| `BuildingEWallMullions` | 3,910 | | `BuildingEParkinglines` | 1 |
| `BuildingEWalls` | 39 | | `BuildingEWoodPanels` | 1 |
| `BuildingEFloors` | 7 | | `BuildingERamps` | 1 |
| `BuildingEDoorMullions` | 2 | | `BuildingEConcrete` | 1 |
| `BuildingECurtainWall` | 2 | | `BuildingEDoorGlass` | 1 |
| `BuildingEExStairRailings` | 2 | | `BuildingEParkingLightConcretebase` | 1 |
| `BuildingEDoorHandles` | 2 | | `BuildingEParkingPad` | 1 |
| `BuildingELightpoles` | 1 | | `BuildingERoof` | 1 |
| `BuildingEStairs` | 1 | | `BuildingEStairGlass` | 1 |
| `BuildingEStairRailings` | 1 | | | |
| `BuildingEParkingLightTops` | 1 | | | |
| `BuildingEGrass` | 1 | | | |

18 of 21 services hold ≤2 elements. The model is effectively one façade plus 68 other components —
which is itself the headline audit finding.

---

## Node granularity — the cause of the broken floor filter

| figure | value |
|---|---:|
| elements in `Wall_Mullions` | 3,910 |
| distinct i3s nodes | **34** |
| nodes that are **level-pure** | **0 of 34** |
| nodes holding levels 1+3+4 | 27 |
| nodes holding levels 0+1+3+4 | 7 |
| median element bbox height | **20.55 m** |

```python
import json, pathlib
from collections import Counter, defaultdict
d = json.loads(pathlib.Path("bim/ca9b73ef024625d4469cc9cb2ae47984.json")
               .read_text(encoding="utf-8").replace(": NaN", ": null"))
rows = d["rows"]
per = defaultdict(set)
for r in rows:
    lv = r.get("BldgLevel")
    per[r["node"]].add(None if lv is None else str(int(lv)))
print(len(rows), len(per), sum(1 for s in per.values() if len(s) == 1))
# -> 3910 34 0
print(Counter(tuple(sorted(s, key=str)) for s in per.values()).most_common())
# -> [(('1','3','4'), 27), (('0','1','3','4'), 7)]
```

Median height, from the `mbb` z-span:

```python
import statistics
hs = [r["mbb"][5] - r["mbb"][2] for r in rows if len(r.get("mbb") or []) >= 6 and r["mbb"][5] > r["mbb"][2]]
print(len(hs), round(statistics.median(hs), 2))   # -> 3910 20.55
```

`mbb` is `[xmin, ymin, zmin, xmax, ymax, zmax]`. A median height of 20.55 m against a ~4.6 m storey
means the typical mullion spans four storeys — the geometry itself is not per-storey, so no
attribute filter could have worked.

> **Correction (2026-11-06).** An earlier version of `README.md`/`SKILL.md` stated "14 hold levels
> 0+1+3+4, 19 hold 1+3+4". That was wrong — and self-evidently so, since 14+19 = 33, not 34. Both
> caches agree on **27 / 7**. Corrected in both files.

---

## Storey elevations — measured, not read from a field

The model publishes no reliable storey-elevation field for this purpose, so these came from clustering
`view.hitTest()` z values on a near-orthographic elevation view of `Floors` + `Roof`.

| surface | z (m) |
|---|---:|
| ground slab | 391.73 |
| slab 1 | 394.66 |
| slab 2 / level 3 | 399.20 |
| slab 3 / level 4 | 403.61 |
| slab 4 / level 5 | 408.19 |
| roof cap | 408.64 |

Building `fullExtent` z range is **339.3 – 460.9** and is *not* the building — it includes ground and
context. Do not use it to frame the model.

Slice-cut verification, by z-sampling `hitTest()` after placing the plane:

| plane z | observed hits span | conclusion |
|---|---|---|
| 408.20 | 391.73 – 408.20 | above L4 |
| 403.61 | 391.73 – 403.60 | above L3 |
| 399.20 | 391.73 – 399.18 | above L1 |
| 394.65 | 391.73 – 394.60 | above L0 |

The cut works because it removes geometry, not because a predicate matched — so node grouping is
irrelevant to it.

---

## Element identity

| check | result |
|---|---|
| `Wall_Mullions` nodes | 34 for 3,910 elements (≈115 elements per node) |
| `ids=[33]` on ZCB | matches **17** distinct elements across 17 sublayers |
| `uids=['Doors/0:33']` on ZCB | matches exactly **1** |

Reproduced in `test_bim_regressions.py` check 9b, which prints both counts on every run:

```
9 viewer : handle accepted; uid 'Doors/0:33' -> 1 element, bare id -> 17 (uid is the exact key)
```

`uid` format is `sublayer/node:id`. Feature ids restart inside a node, and node ids restart in every
sublayer, so only the triple is unique.

---

## Data-quality findings

| finding | count |
|---|---:|
| elements with `BldgLevel = NaN` | 4 |
| duplicate `Mark` — `Door_Handles` "3" | ×2 |
| duplicate `Mark` — `DoorMullions` "4" | ×2 |
| site layers mis-tagged `BldgLevel = 3` | 7 of 7 |
| levels missing from the model | level 2 |
| disciplines present | Architecture only |

Every service is `Architecture` — a model with no structural or MEP geometry. Any claim about
clashes, services coordination, or structural adequacy is unsupportable from it.
