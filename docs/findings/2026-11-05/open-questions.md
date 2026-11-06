# Open questions and known limits

Read this before treating anything in `docs/findings/2026-11-05/` as settled. Each item is something
a reviewer should either confirm or correct.

---

## 1. One document figure was wrong and is now corrected

`README.md` and `SKILL.md` stated:

> 14 hold levels 0+1+3+4, 19 hold 1+3+4

That was wrong, and internally inconsistent — **14 + 19 = 33, not 34**. Recomputed from two
independent v8 caches, both agreeing:

| levels held | nodes |
|---|---:|
| 1+3+4 | **27** |
| 0+1+3+4 | **7** |

Corrected in both files on 2026-11-06. The qualitative claim is unaffected (0 of 34 are level-pure,
which is what the finding rests on), but the split as published was not reproducible.

**Action for reviewer:** verify with the snippet in `measured.md` and confirm 27/7.

---

## 2. The measurement environment is one Windows machine

Everything in the findings was measured on **Chromium 1243 via Playwright on Windows**, ANGLE/D3D11,
software-rendering fallback enabled.

Consequences worth knowing:

- The `~40 s` first paint is a **software-rendering** figure. On real GPU hardware it should be far
  faster. Do not quote it as a general expectation.
- The camera-aim error (**2 px horizontal, 27 px vertical**) was measured at one viewport (1440×900)
  and one tilt. It is precise for that case, not a guarantee.
- Nothing was validated on mobile Safari, Firefox, or WebGL1.

---

## 3. Only one model was examined in depth

Building E (`2b495455eb4148bbb05f1b255c0a527b`). The ZCB model was used only for the `uid` uniqueness
and `OBJECTID` collision checks. Two models is not a sample.

Specifically **unverified across models**:

- Whether Revit→i3s "commonly groups nodes by type rather than storey". This is asserted from a single
  21-service export. It is a plausible and commonly-reported failure, but the frequency is unknown.
- Whether `BldgLevel = -2147483648` (the "unset" sentinel) is universal, or an artifact of this
  exporter. It appears in the CIC model (2,409 rows) and in Building E's 4 `NaN` rows, so it is at
  least not unique to one model.
- Whether the `98.3% in one service` concentration is typical. It almost certainly is not — it reflects
  a pathological export, and it is the headline finding *for this model*.

---

## 4. The `sublayer.queryFeatures` result deserves a second look

The finding — that `queryFeatures()` throws `query-not-available` on a sublayer, contradicting the
docs — was measured on **hosted scene services**, not on a layer created through a different
publishing path.

It is possible that:

- it works on a BSL published by ArcGIS Pro with an associated feature layer,
- or on the root layer of some configurations,
- or that the docs describe behaviour of a version other than 5.0.

The error is real and reproducible for the services tested. The **generalisation** is not established.
`bim_select` does not depend on it (it reads positions from cache), so the tool is safe either way —
but do not repeat the claim as universal without re-testing.

---

## 5. Two-plane slices were not formed, but this may have been my error

A storey "band" (cut above and below) was attempted with two opposing `SliceAnalysis` planes. Both
planes were accepted — `analyses.length === 2` — and `hitTest` showed the band was **not** formed.

I could not establish whether this is an SDK limitation or a mistake in plane orientation. It is
recorded as a negative result, and the implementation uses a single cumulative cut instead, which is
verified. If two planes are supposed to combine, this note is the place to correct.

Likewise `view.clippingArea` produced **identical before/after screenshots** in local viewing mode. That
is consistent with it being unsupported there rather than misconfigured, but again not proven.

---

## 6. Why does the cache contain bare `NaN`?

`workspace/bim/*.json` is written with `json.dumps(...)`, which by default emits bare `NaN` for
`float('nan')` — technically invalid JSON, accepted by JavaScript `JSON.parse`.

Every reader in this directory therefore sanitises with `.replace(": NaN", ": null")` first. That is a
workaround, not a fix.

**This is arguably a real bug in `hk_spatial_mcp.py`:** it writes JSON that standard parsers reject, and
it bit the tool's own page (a `NaN` in the state file made the page drop an entire update). Worth
fixing with `json.dumps(..., allow_nan=False)` plus an explicit `None` conversion, so the failure is
loud rather than silent. Not done in this branch — it changes cache format again.

---

## 7. The CSP finding is a diagnosis, not a platform fix

The four-environment experiment proves the header is the cause and that the explicit-host policy
resolves it. It does **not** change any host's configuration. A user on a locked-down host still has to
either open the page on localhost or get the policy changed.

Also: `'unsafe-eval'` remains required. The policy is host-tightened, not free of unsafe directives.
Do not describe it as hardened.

---

## 8. The audit findings are about data, and are opinions about the export

Statements like "the model is effectively one façade plus 68 other components" and "all layers are
Architecture only" are counts. Statements like "this model cannot drive asset tracking" are
**judgements** based on those counts.

The counts are reproducible. The judgements are argued, not measured, and a reviewer should treat them
as such. In particular, a model that legitimately represents only a façade (a refurbishment scope, say)
would be misrepresented by the framing in the README.

---

## 9. Not tested

- `bim_filter` / `bim_select` against a `Scene Package` (`.slpk`). The code path exists and returns a
  warning that filters have nothing to act on, but the warning has not been exercised end to end.
- Concurrent viewers. The state file is keyed by service, with a `rev` counter; two browser tabs on the
  same model were not tested.
- The `streamable-http` / `sse` transport paths with the viewer tools.
- Anything on a machine without a GPU where the BSL never finishes streaming.
