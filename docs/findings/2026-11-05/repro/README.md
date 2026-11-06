# Reproducing the findings

Three scripts. Each is standalone, takes its paths as arguments, and has no dependency on the machine
the findings were measured on — an earlier version of these hardcoded `C:/Users/<me>/...`, which made
them unrunnable for anyone else.

None of them need an ArcGIS token, and only `verify-csp.cjs` needs a browser.

---

## 1. `count-nodes.cjs` — the numbers, from the cache (no browser, no network)

```bash
node count-nodes.cjs --dir /path/to/HK_MCP_DIR --prefix BuildingE --sublayer BuildingEWallMullions
```

`--dir` is the MCP workspace (`HK_MCP_DIR`, where `bim/*.json` lives). Run `bim_audit` or
`bim_elements` at least once first so the cache exists.

Output matches [`../measured.md`](../measured.md) exactly: 21 services, 3,978 elements, `98.3%` in
`Wall_Mullions`, the level census, and the node-purity test.

The last block is the one that matters:

```
node granularity — BuildingEWallMullions
  elements            3910
  distinct nodes      34
  level-pure nodes    0 of 34
    holds 1+3+4          27 nodes
    holds 0+1+3+4         7 nodes
  => a BldgLevel filter CANNOT isolate a storey on this service.
```

If you are reviewing the floor-filter finding, this is the check to run. It reads the same cache the
tool reads, so it cannot disagree with the tool.

---

## 2. `serve-csp.cjs` — the reference policy

```bash
node serve-csp.cjs --mode none   --root /path/to/HK_MCP_DIR --port 8777   # control: no header
node serve-csp.cjs --mode strict --root /path/to/HK_MCP_DIR --port 8779   # explicit hosts
node serve-csp.cjs --mode host   --root /path/to/HK_MCP_DIR --port 8780   # same + bare `https:`
```

`GET /__csp` returns the policy as text. Use it to diff what is served against what the docs claim —
that mismatch is how a wrong policy block survived into the README once already.

- `none` and `strict` both load 21/21. That equivalence is the whole experiment: the header, not the
  code, decides.
- `host` is the tempting shortcut (allow `https:`, unblock the CDN by allowing the web). It works, and
  it is **not** the recommendation.
- `--root` is required and path-escape is rejected, so it will not serve outside the workspace.

---

## 3. `verify-csp.cjs` — the measurement

```bash
npm i -D playwright && npx playwright install chromium      # once

node verify-csp.cjs --url http://127.0.0.1:8791/bim-audit-building-e.html --expect-layers 21
```

Reports CSP violations grouped by directive and host, page errors, failed requests (with
`net::ERR_ABORTED` excluded — those are cancelled, not blocked), layers loaded, and boot time. Exits
non-zero on violations or short layer count, so it can gate a build.

Browser resolution: `--executable`, then `$CHROME_PATH`, then playwright's own chromium. On a machine
where the bundled chromium is missing:

```bash
CHROME_PATH=/path/to/chrome node verify-csp.cjs --url ... --expect-layers 21
```

### Reading the result

| output | meaning |
|---|---|
| `CSP violations: 0`, `layers 21/21` | policy admits everything the SDK needs |
| violations concentrated in `connect-src` | the SDK loaded but cannot fetch tiles — the classic case |
| violations in `worker-src` | geometry decodes will fail; the layer loads metadata and draws nothing |
| `ERR_ABORTED` requests, no violations | normal — camera motion or teardown cancelled in-flight tiles |

**Expect ~40 s on software rendering** for a 2M-element model. Do not conclude failure early: a slow
first paint is indistinguishable from a blocked load, and `hitTest` will not tell you (it returns empty
for BSL geometry even when the model is on screen).

---

## What these do not cover

The scripts verify the **cache-derived counts** and the **CSP behaviour**. They do not re-audit a live
model, and they do not exercise the SDK's filter mechanisms — those were measured interactively in a
browser and are recorded in [`../i3s-filtering-matrix.md`](../i3s-filtering-matrix.md) without an
automated harness.

See [`../open-questions.md`](../open-questions.md) for which claims are solid and which are single-sample.
