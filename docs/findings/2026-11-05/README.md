# Findings — 2026-11-05 · BIM audit, i3s filtering, CSP

A consolidated record of what was measured while auditing the **Building E** `web scene`
(`2b495455eb4148bbb05f1b255c0a527b`) and porting the results into this repo's BIM tools.

Prose lives in `README.md` and `SKILL.md`, and is what users read. **This directory is the evidence
behind it** — the counts, the mechanisms that failed, and the scripts that reproduce them. Read this
when you want to check a claim rather than use the tool.

Every number here was recomputed from the cached i3s attribute pages in `workspace/bim/` (gitignored —
107 MB) at the time of writing. Where a figure could not be reproduced, it says so instead of being
repeated.

---

## Contents

| File | What it is |
|---|---|
| [`measured.md`](measured.md) | Every figure, with the command that produces it |
| [`i3s-filtering-matrix.md`](i3s-filtering-matrix.md) | Which SDK mechanism actually filters — the negative results matter more than the positive ones |
| [`csp-lockdown.md`](csp-lockdown.md) | The 4-environment CSP experiment and why a blank page is not a broken page |
| [`repro/`](repro/) | Runnable harnesses; see `repro/README.md` |
| [`open-questions.md`](open-questions.md) | What is unresolved, untested, or inferred — **read before trusting anything** |

---

## The five findings

**1. A `BldgLevel` filter cannot isolate a storey on this model.** `Wall_Mullions` holds 3,910
elements in **34 nodes**, and **0 of 34 are level-pure** (27 hold levels 1+3+4, 7 hold 0+1+3+4).
An i3s predicate selects a *node*, never elements inside one, so `BldgLevel IN (0)` returns the whole
glass façade. This is an export-grouping fact, not a client bug, and cannot be fixed by asking
differently. `bim_elements` now reports it; the viewer cuts geometry instead.

**2. Most mechanisms you would reach for do not filter.** `definitionExpression` has **no effect**
(0.0% pixel change). `queryFeatures` / `queryExtent` **throw** `query-not-available` even on a
sublayer — which contradicts the published guidance. `OBJECTID = n` matches **~17 elements, not 1**.
`BuildingFilter` expressions, `sublayer.visible`, and a `1=1` `wire-frame` block do work. See
[`i3s-filtering-matrix.md`](i3s-filtering-matrix.md).

**3. `uid` is the only unique element key.** i3s feature ids restart inside a node *and* node ids
restart in every sublayer. On ZCB, `ids=[33]` matches **17** distinct elements across 17 sublayers;
`uids=['Doors/0:33']` matches exactly **1**. Verified in the regression suite.

**4. A blank page is usually a CSP on the host, not a bug in the page.** Same files, same code, only
the header varying: **79 violations / 0-of-21 layers** locked down, **0 / 21-of-21** on localhost,
and **0 / 21-of-21** with the same policy plus explicit ArcGIS hosts. `connect-src` alone accounts
for all 40 network violations. A bare `https:` does work but is not needed — the explicit-host policy
is verified. See [`csp-lockdown.md`](csp-lockdown.md).

**5. A partial i3s read must never be cached.** One transient node failure was silently dropped and
then cached, leaving the model permanently short (14,215 rows instead of 14,416, inherited by every
later call). Nodes are now retried, failures reported in `coverage.nodes_failed`, and the result is
deliberately not cached.

---

## Slope of the same lesson

Four of the five are cases where **the obvious approach fails silently** rather than erroring. That is
why the negative results are recorded in as much detail as the positive ones — an agent that has not
met them will confidently return the wrong answer, and no stack trace will appear.

---

## Provenance

| | |
|---|---|
| Building E services | 21 sibling `ArcGISSceneServiceLayer`s under one `GroupLayer` |
| SDK | ArcGIS Maps SDK for JavaScript **5.0** (CDN ES modules) |
| Browser | Chromium 1243 (Playwright), ANGLE/D3D11, Windows |
| Verified against repo | `hk-spatial-mcp` `main` @ `0388f94`, 31 tools |
| Regression suite | `test_bim_regressions.py` — passes end to end (7m36s) |

Model totals used throughout: **3,978 elements** across 21 services; `Wall_Mullions` is **3,910** of
them (**98.3%**). Levels present: 0, 1, 3, 4, 5 — **level 2 is absent everywhere**; 4 elements carry
`BldgLevel = NaN`.
