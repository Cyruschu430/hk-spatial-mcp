# CSP lockdown — a blank page is not a broken page

A generated ArcGIS page can render its panel and controls perfectly while loading **zero** geometry.
The cause is a Content Security Policy on the *hosting* origin, not the page. This records the
experiment that isolates it.

---

## Method

Four environments, same HTML, same JS, **only the response header varying**. Each run counts CSP
violations, loaded scene layers, page errors and failed requests.

| # | environment | header | violations | layers | page errors |
|---|---|---|---:|---:|---|
| 1 | locked-down host (as shipped) | host's own CSP | **79** | **0 / 21** | 4 |
| 2 | localhost, no CSP | *(none)* | **0** | **21 / 21** | none |
| 3 | same policy + ArcGIS hosts | verbose whitelist | **0** | **21 / 21** | none |
| 4 | explicit-host policy | no bare `https:` | **0** | **21 / 21** | none |

Rows 2–4 isolate the header as the only variable. Row 4 is the important one: the fix does **not**
require allowing the whole web.

Rows 2 and 4 were re-confirmed with `repro/serve-csp.cjs` + `repro/verify-csp.cjs` on 2026-11-06 —
`--mode none` and `--mode strict` both report **21/21 layers, 0 violations, 0 page errors**, boot
~25–26 s. Both harnesses exit 0 only when the layer count is met, so this is a gate rather than a
narrative.

---

## What row 1 actually blocks

| host | violations | directive | consequence |
|---|---:|---|---|
| `tiles.arcgis.com` | 21 | `connect-src` | every i3s scene service, one per layer |
| `js.arcgis.com` | 16 | `connect-src`, `default-src`, `worker-src` | lazy SDK chunks, theme CSS, i3s decode workers |
| `cdn.arcgis.com` | 2 | `connect-src` | basemap style, web scene item JSON |
| `elevation3d.arcgis.com` | 1 | `connect-src` | ground elevation |

`connect-src` alone accounts for all **40** network violations.

Representative message:

```
Refused to connect to 'https://tiles.arcgis.com/tiles/.../Wall_Mullions/SceneServer'
because it violates the following Content Security Policy directive: "connect-src ...".
```

### Why it looks like an application bug

`script-src` commonly allows `https:`, so the ES modules **do** load, the DOM builds, and the panel
with its buttons renders. Only `connect-src` is restricted. The page therefore looks structurally
complete and completely empty at once — which reads as "the layer failed to load", not "the host
refused it".

The knock-on error is a red herring:

```
TypeError: Cannot destructure property 'zoomIn' of 'this.messages' as it is null.
```

`this.messages` is the widget i18n bundle from `js.arcgis.com`. With that blocked the Zoom widget has
no labels and throws. It is not a widget bug, and it disappears entirely on localhost.

---

## The verified policy (row 4)

Explicit hosts, no bare `https:` anywhere in `connect-src`:

```
connect-src  'self' http://127.0.0.1:* https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com https://static.arcgis.com
default-src  'self' 'unsafe-inline' 'unsafe-eval' blob: data: https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com https://static.arcgis.com
script-src   'self' 'unsafe-inline' 'unsafe-eval' blob: data: https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com https://static.arcgis.com
worker-src   'self' blob: https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com
img-src      'self' data: blob: https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com https://static.arcgis.com
style-src    'self' 'unsafe-inline' https://js.arcgis.com https://*.arcgis.com https://*.arcgisonline.com https://static.arcgis.com
font-src     'self' data: blob: https://js.arcgis.com https://*.arcgis.com https://static.arcgis.com
```

Served with exactly these directives: **21/21 layers, 0 violations, 0 page errors, 0 failed requests.**

Notes:

- **`worker-src` is not optional.** i3s geometry decodes in workers. Without it the layer loads its
  metadata and still draws nothing — a different silent failure with the same symptom.
- **`'unsafe-eval'` is required by the SDK.** This cannot be tightened away; do not claim the policy is
  free of it. What *is* tightened is the host list.
- **`http://127.0.0.1:*` in `connect-src`** exists so a generated page can poll its own state file
  during local development. Remove it for a hosted deployment.
- `font-src` and `style-src` are easy to forget and produce only cosmetic breakage (icon fonts, widget
  chrome), which is easy to misread as "the page half-loaded".

---

## The one-step diagnosis

Load the page on `http://127.0.0.1:8777` — the built-in server sends **no** CSP — and then where it
fails.

- Works on localhost → the header is the cause. No amount of application debugging will find it,
  because the blocked requests never reach the code.
- Fails on both → it is a real application problem; debug normally.

This is worth doing *first*, because the two failure modes present identically.

---

## Validation traps

While establishing this, three assertions reported failures on a perfectly healthy page. Each cost a
cycle and each is a plausible thing for the next agent to write:

| assertion | why it is wrong |
|---|---|
| `button.getAttribute("aria-label")` on Zoom/Compass | SDK 5.0 renders `<calcite-button class="esri-widget--button">` with the name in **`title`** |
| `search.allSources.length` read right after `when()` | populated **asynchronously**; poll instead |
| `suggest(...).length` | `suggest()` resolves to a **non-array** object — use `search()` as the liveness probe |

And one classification rule: a failed request whose `errorText` is **`net::ERR_ABORTED`** was
*cancelled* — camera motion or page teardown. Only a policy block or a 4xx/5xx is evidence of a real
failure. An early run logged 5 `tiles.arcgis.com` failures that were pure teardown noise; re-running
with classification showed zero.

---

## Scope

This applies to any page the tool generates — the map viewer, the scene viewer, the BIM viewer and the
findings report — because they all load the SDK from `js.arcgis.com` at runtime. A host that locks
`connect-src` down will break all of them identically.

The concrete case that prompted this: the WorkBuddy / CodeBuddy preview pane. See the troubleshooting
row in the main `README.md`.
