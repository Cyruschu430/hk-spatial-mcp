# Findings — index

Two evidence packs, written on consecutive days against two different models. They are
deliberately separate and **must not be read as one dataset**: the first is about what the
*SDK and the tool* can do, the second is about what a *specific model* contains. Neither
supersedes the other, and numbers are not comparable across them.

Start here if you are reviewing rather than using the tool.

---

## Which pack answers your question

| You want to know | Read | Time |
|---|---|---|
| Does `BldgLevel` / `definitionExpression` / `queryFeatures` filter an i3s layer? | [`2026-11-05/i3s-filtering-matrix.md`](2026-11-05/i3s-filtering-matrix.md) | 10 min |
| Why is my page blank under a CSP? | [`2026-11-05/csp-lockdown.md`](2026-11-05/csp-lockdown.md) | 8 min |
| Where did this number come from? | [`2026-11-05/measured.md`](2026-11-05/measured.md) | 15 min |
| What is a BIM audit actually supposed to produce, on a real model? | [`2026-11-06/README.md`](2026-11-06/README.md) | 25 min |
| Can I see it working? | [`2026-11-06/app/`](2026-11-06/app/) — 4-tab app, `python -m http.server` | 2 min |
| What is not proven? | [`2026-11-05/open-questions.md`](2026-11-05/open-questions.md) | 10 min |

If you read only one file and you are deciding whether to trust this repo: **`open-questions.md`**.

---

## 2026-11-05 · Mechanism

Subject: Building E, `2b495455eb4148bbb05f1b255c0a527b` — a `GroupLayer` of **21** sibling
`ArcGISSceneServiceLayer`s, **3,978** elements. SDK 5.0, Chromium, Windows.

The finding: on a model published this way there is **no sublayer tree and no `BuildingFilter`**,
so the SDK gives you no floor filtering at all, and the mechanisms you would reach for either
do nothing or throw. Of the five headlines, four are **silent failures** — no stack trace, a
confidently wrong answer. That is the reason this pack exists.

| File | Contents |
|---|---|
| [`README.md`](2026-11-05/README.md) | the five findings, 79 lines |
| [`i3s-filtering-matrix.md`](2026-11-05/i3s-filtering-matrix.md) | what works, what silently does not, what throws |
| [`csp-lockdown.md`](2026-11-05/csp-lockdown.md) | 4-environment experiment, 79 vs 0 violations |
| [`measured.md`](2026-11-05/measured.md) | every figure + the command that produces it |
| [`repro/`](2026-11-05/repro/README.md) | 3 standalone scripts, no token, no hardcoded paths |
| [`open-questions.md`](2026-11-05/open-questions.md) | 9 sections of what is unresolved or single-sample |

**Reproduce the core claim** (no browser, no network — reads the same cache the tool reads):

```bash
node 2026-11-05/repro/count-nodes.cjs --dir "$HK_MCP_DIR" \
  --prefix BuildingE --sublayer BuildingEWallMullions
# => elements 3910, distinct nodes 34, level-pure nodes 0 of 34
```

Zero of 34 nodes hold a single level, so a `BldgLevel` predicate — which selects a *node*, never
elements inside one — cannot isolate a storey. This is an export-grouping property, not a client bug.

---

## 2026-11-06 · Content

Subject: `PXXCICXXZZA` / `563b20ee8c314e58a6fc007b733a48bd`, Tsim Sha Tsui — a single public
hosted `BuildingSceneLayer`, i3s 1.9, **61** sublayers, **1,836** node groups, **40,283** elements,
**27** storey labels, checked against OZP `S/K1/28`.

Where the first pack asks *"what does the tool do"*, this one asks *"is this model fit to audit"*.
Six findings: three `fail`, three `cannot check` — the latter meaning **the export does not contain
the answer**, which is not a pass.

| Finding | Verdict | One line |
|---|---|---|
| F-01 `Mark` empty on 86.0% | `fail` | 34,645 of 40,283 unidentifiable; 926 duplicate values add **4,229** phantom join rows |
| F-02 sentinel level `-2147483648` | `fail` | 2,409 elements on `INT_MIN`, not null — every floor grouping gets a phantom storey |
| F-03 `mbb` is not element geometry | `fail` | all 40,283 rows inherit a node cube whose x/y are in **degrees**; `mbb[3]-mbb[0]` reaches **122.472** |
| F-04 Infrastructure subtree empty | `cannot check` | published with no content |
| F-05 Structural content 1.5% | `cannot check` | far below what a 27-storey building should carry |
| F-06 204 Rooms, none named | `cannot check` | no `Name`, no `Number` |

F-03 is the one to attack first: a degree value above 100 cannot be a metre value on a building, so
the unit is settled by inspection without trusting the analysis. The full check is in
[`2026-11-06/README.md` § Reproducing it](2026-11-06/README.md#reproducing-it) and prints
`cube: 40283 / 40283` and `sides: 0.051 to 122.472`.

**Known correction.** The Tsim Sha Tsui figures were once labelled m² when they were deg². The pack
records this rather than hiding it — treat any area or volume derived from `mbb` as invalid.

### The app

```bash
cd 2026-11-06/app && python -m http.server 8901
```

Four tabs (Overview / Findings / Statutory / Model). i3s **will not load from `file://`**, and the
live model needs **~110 s** to stream — captured early it draws as bare slabs and looks broken.
Screenshots in [`2026-11-06/evidence/`](2026-11-06/evidence/) are the loaded state, not that.

> If you screenshot streamed i3s with CDP, do **not** use `--virtual-time-budget`. It fast-forwards
> timers past the tile fetches and yields a blank canvas every time. Drive Chrome over the DevTools
> protocol with real wall-clock sleeps. This cost hours and is a *measurement* artefact, not a bug.

---

## Shared caveats

These apply to both packs and are the reason neither should be quoted without qualification.

1. **One machine, one operator.** Windows, Chromium + ANGLE/D3D11. No macOS, no Linux, no Firefox.
2. **Two models, both Hong Kong, both public.** Nothing here proves behaviour on a private or
   differently-authored export.
3. **Attribute-only CSDI exports.** Two datasets (`LandSD Building` 87,982 features, `LandSD Lot`
   1,606) export **every feature as `geometry: null`**. `load_hk_data` now refuses to claim success
   on these. A feature count is not evidence that anything can be drawn.
4. **`hitTest` returns empty for BSL geometry** even when the model is visible. Never use it as a
   liveness check.
5. **A slow first paint is indistinguishable from a blocked load.** Wait the full ~40 s (software
   rendering) or ~110 s (this model) before concluding failure.

---

## Verifying the repo state these were measured against

```bash
python test_version_parity.py      # pyproject.toml == plugin.json, 31 tools registered
python test_geometry_guard.py      # the attribute-only refusal, 5 cases
```

31 tools, `0.16.0`. `test_install_path.py` needs `uvx`, which is not present on the machine these
were measured on — it has **not** been run, and that is reported rather than assumed.
