# BIM audit — PXXCICXXZZA, Tsim Sha Tsui · 2026-11-06

A worked audit of a public Building Scene Layer, written up so another agent can
**re-run it, check the numbers, and argue with the conclusions**. Every figure was read
from the i3s attribute pages or from a published public service; none is estimated from
a screenshot.

If you only read one section, read [What this cannot tell you](#what-this-cannot-tell-you).
The most important result of this audit is the list of questions it refuses to answer.

---

## The subject

| | |
|---|---|
| Service | `.../PXXCICXXZZA/SceneServer` (public, no key) |
| Item | `563b20ee8c314e58a6fc007b733a48bd` |
| Publisher | `rng@esrichina.hk` · published **2023-12-19** |
| i3s | 1.9 · **61** sublayers · **1,836** node groups · **40,283** elements |
| Location | Tsim Sha Tsui, Kowloon · centre `114.173988, 22.29919` |
| Footprint | 81.4 m × 44.3 m bbox = **3,604 m²** |
| Storeys | **27** declared labels · 25 carry slabs · roof at `TR/F +63.25` |

The 3D viewer needs **~110 s** to stream fully. Captured earlier it draws as bare slabs
and looks broken — see the main README.

---

## Findings

Six, each with its evidence. `fail` means the model is demonstrably wrong or unusable
for the stated purpose. `cannot check` means the data needed to answer the question is
not in the export — it is **not** a pass.

### F-01 · Mark is empty on 86% of the model — `fail`

`Mark` is the field an asset register, a maintenance ticket or a clash report keys on.

| | |
|---|---:|
| `Mark` filled | 5,638 |
| `Mark` empty | **34,645** |
| empty | **86.0%** |
| distinct values | 1,409 |
| values used more than once | **926** |
| extra join rows they create | **4,229** |

Mark `104` alone appears **26 times**; `112`, `149`, `100` and the literal string
`"Plumbing"` each appear 24 times.

Two separate defects. The 86% empty rate means most of the model cannot be identified
at all. The 926 duplicates mean a join on `Mark` — the obvious thing to write —
**silently multiplies rows**: 4,229 extra rows from a 40,283-element model, and any
cost or quantity rollup built on it is wrong by an unknown factor.

### F-02 · 2,409 elements sit on the sentinel level `-2147483648` — `fail`

Revit exports an unassigned element with `BldgLevel = -2147483648` (`INT_MIN`) rather
than null. Any filter, storey count or floor plan that groups by level gets a phantom
28th storey.

| sublayer | elements |
|---|---:|
| StairsRailing | 740 |
| StructuralFraming | 412 |
| LightingFixtures | 376 |
| GenericModel | 374 |
| ElectricalFixtures | 238 |
| Walls | 87 |
| PlumbingFixtures | 85 |
| FireAlarmDevices | 54 |

**6.0%** of the model. Structural framing at 412 is the concerning one: those beams
belong to *some* storey, and the export does not say which.

### F-03 · Element bounds are not geometry — `fail`

410 slabs across 25 of 27 levels; `R/F +56.75` and `TR/F +63.25` carry none, so a
level-by-level area cannot be closed.

But the deeper problem is that **`mbb` is not an element bounding box at all**, which
voids any area or volume derived from it:

| test | result |
|---|---|
| `dx == dy == dz` to float precision | **40,283 / 40,283 rows (100%)**; worst deviation **1.8 × 10⁻¹⁴** |
| nodes whose elements all share one `mbb` | **1,836 / 1,836 (100%)** |
| elements inheriting a node-level `mbb` | **40,283 / 40,283 (100%)** |
| `mbb` centre vs. row `lon`/`lat` | identical to 6 decimal places |
| x/y span | 0.051 – 122.47 **degrees** (up to ~13,000 km) |
| z span | −27.3 – 95.2 (metres; reads as mPD) |
| median cube side | **54.2°** |

Every element reports a perfect **cube** centred on its point, with horizontal extents
in degrees and a vertical extent in metres — mixed units in one array, identical for
every element packed into the same node. A 2.1 m door reports a 2.24° cube; the median
element reports 54.2°, a span that at this latitude is over 6,000 km.

So `mbb` is a node-level placeholder, not geometry. Summing `dx·dy` over the slabs gives
887,272 — but the unit is **square degrees**, not m², and the shape it describes is
fictional. Any floor area computed this way is wrong twice: wrong unit, wrong shape.

> **Correction worth recording.** This audit originally reported "887,272 m²" and the
> app reported "74,144 m²" for the roof. Both were `dx·dy` in **deg²**, mislabelled as
> m². The arithmetic was right and the unit was wrong — exactly the silent error this
> audit is meant to catch. The figures are removed rather than converted, because the
> underlying cube is not real either.

### F-04 · The entire Infrastructure subtree is empty — `cannot check`

20 of 61 sublayers contain **zero** elements, and they are not a random 20: they are the
whole civil/infrastructure set — substairs, decks, piers, bearings, tendons, framing —
plus furniture, planting, mass, parking, topography and five electrical device classes.
A building model with no substructure export cannot support any foundation, bearing or
bridge-related question.

### F-05 · Structural content is 1.5% of the model — `cannot check`

| discipline | elements | share |
|---|---:|---:|
| Piping | 18,297 | 45.4% |
| Architectural | 11,689 | 29.0% |
| Electrical | 6,254 | 15.5% |
| Mechanical | 3,427 | 8.5% |
| **Structural** | **616** | **1.5%** |

This is a services-heavy model. 616 structural elements across 27 storeys is not a
structural model, and any structural conclusion drawn from it would rest on 1.5% of the
available evidence.

### F-06 · 204 Rooms exist; none has a name or number — `cannot check`

`Rooms` = 204, `rooms_named` = **0**. Room-level anything — occupancy, area by use,
wayfinding — is impossible without inferring identity from geometry, which this audit
will not do.

---

## What this cannot tell you

The honest core of the audit. These were **not** answered, and no number is reported
for them:

| question | why not |
|---|---|
| Gross floor area | no slab/room areas in the export; `mbb` is not geometry (F-03) |
| Compartment / fire-compartment size | no compartment attribute |
| Travel distance | no egress geometry, no room boundaries |
| Clash detection | needs element geometry; the browser cannot read BSL geometry at all (`queryFeatures` throws, `hitTest` returns `[]`) |
| "Compliant" / "non-compliant" | a planning control is not a compliance determination, and no submission set is in evidence |

If a downstream task needs any of these, it needs a different export — not a different
query against this one.

---

## Statutory context

Used as **reference**, not as a compliance check.

| | |
|---|---|
| Plan | **`S/K1/28`** Tsim Sha Tsui 尖沙咀 |
| Approved / published | 2013-12-03 / 2013-12-13 · s.9(1)(a) · 1:5,000 |
| Zone at centre | **`C(6)`** Commercial (6) 商業(6), 3,246 m² |
| Height control | **`BHCB 110` mPD**, 3,810 m² area |

Both the zone and the height control are **non-uniform across the footprint**, which is
why a single number would be wrong:

- Zone: a 60×60 grid over the footprint lands **63.1%** in `C(6)` and **36.9%** in the
  `MRDJ` (Major Road and Junction) reservation.
- Height: **55.2%** of the grid is under `110 mPD`, **7.8%** under `130 mPD`, and
  **36.9%** carries no BHC row at all.

**Arithmetic, not a determination:** surveyed ground is **57.5 mPD** (Terrain3D) and the
highest modelled level `TR/F +63.25` reads **63.25 mPD** — because the export names
levels by absolute mPD, not by height above ground. That roof is **5.8 m above the
ground surface**, leaving roughly **45.8 m** of headroom under the 110 mPD control.

That headroom is a subtraction on a control value. It says nothing about permitted
development, plot ratio, or whether any submission would be accepted.

---

## Provenance

Every layer, with its id. The OZP / BHC layers are **public mirrors** — the statutory
plan prevails over any copy held here.

| layer | source |
|---|---|
| BIM model | `SceneServer/PXXCICXXZZA` · item `563b20ee8c314e58a6fc007b733a48bd` |
| OZP boundary | `Outline_Zoning_Plans_Planning_Scheme_Boundary/FeatureServer` |
| Zoning, height control | same OZP FeatureServer |
| Lots | `Lot_in_Hong_Kong/FeatureServer/0` (Lands Department mirror) |
| Roads | CSDI road centrelines |
| Ground elevation | Esri `Terrain3D` via `elevation_at` |

Bundle layout:

```
docs/findings/2026-11-06/
├── README.md              # this file
├── app/                   # the 4-tab presentation app (open index.html over http://)
│   └── assets/            # audit.json, compliance.json, elements.json, *.geojson
├── data/                  # raw query results, kept for provenance
└── evidence/              # captured tabs, 1:1 with the claims above
```

---

## Reproducing it

```bash
# serve it — i3s will not load from file://
cd docs/findings/2026-11-06/app && python -m http.server 8901
```

Then `bim_audit` on the service URL reproduces the counts, `bim_floors` the level table,
and `bim_elements` any element row quoted here. The 110 s streaming caveat applies.

To re-check the F-03 `mbb` claim without trusting it, run this against the cached rows
(or any `bim_elements` dump — every element row carries these fields):

```python
import json
from collections import defaultdict

rows = json.load(open("<cache>/bim/<md5(service)>.json"))["rows"]

cube = 0
bynode = defaultdict(set)
for r in rows:
    m = r["mbb"]
    dx, dy, dz = abs(m[3]-m[0]), abs(m[4]-m[1]), abs(m[5]-m[2])
    if abs(dx-dy) <= 1e-9 and abs(dy-dz) <= 1e-9:   # float noise, not shape variation
        cube += 1
    bynode[(r["sublayer"], r["node"])].add(tuple(m))

print("cube:", cube, "/", len(rows))                                   # 40283 / 40283
print("node groups with one mbb:",
      sum(1 for v in bynode.values() if len(v) == 1), "/", len(bynode))  # 1836 / 1836
print("sides:", round(min(abs(r["mbb"][3]-r["mbb"][0]) for r in rows), 3),
      "to", round(max(abs(r["mbb"][3]-r["mbb"][0]) for r in rows), 3))   # 0.051 to 122.472
```

Uses a 1e-9 tolerance: exact `==` matches only 70% of rows because the values carry
float noise (worst deviation 1.8 × 10⁻¹⁴), which is arithmetic, not shape. A degree value
over 100 cannot be a metre value on a building, so the unit is settled by inspection
alone. The findings written into `app/assets/audit.json` under `mbb_evidence` are the
exact output of this script.

**Attribution:** "Powered by Esri" stays on every map — it is a basemap licensing
requirement, not decoration.

---

## For the reviewing agent

Worth attacking, in this order:

1. **F-03's `mbb` finding.** It is the strongest claim here and the most falsifiable: if
   you can produce a slab whose `mbb` differs from its node-mates, or whose `dx` is a
   plausible metre value rather than a degree value, the finding collapses. Check
   `mbb[3]-mbb[0]` against a known door width — 2.24 is not 2.24 m.
2. **The 86% empty Mark.** Is `Mark` even the right key for this dataset? The answer
   decides whether F-01 is a defect or a non-issue.
3. **The headroom subtraction.** It mixes an absolute-mPD control with a level-name-mPD
   figure. The arithmetic is sound; whether the comparison is *meaningful* is a fair
   challenge.
4. **Provenance of the OZP mirrors.** The zoning and height numbers come from public
   mirrors, not the statutory plan itself. If `S/K1/28` has been amended since
   2013-12-13, these are stale — the plan governs.
