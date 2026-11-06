/* ============================================================
   PXXCICXXZZA BIM Audit — app logic
   Data: hk-spatial (CSDI) audit of a public i3s building scene layer
   ============================================================ */
import Map from "https://js.arcgis.com/5.0/@arcgis/core/Map.js";
import MapView from "https://js.arcgis.com/5.0/@arcgis/core/views/MapView.js";
import SceneView from "https://js.arcgis.com/5.0/@arcgis/core/views/SceneView.js";
import BuildingSceneLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/BuildingSceneLayer.js";
import BuildingFilter from "https://js.arcgis.com/5.0/@arcgis/core/layers/support/BuildingFilter.js";
import BuildingFilterBlock from "https://js.arcgis.com/5.0/@arcgis/core/layers/support/BuildingFilterBlock.js";
import GeoJSONLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/GeoJSONLayer.js";
import TileLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/TileLayer.js";
import GraphicsLayer from "https://js.arcgis.com/5.0/@arcgis/core/layers/GraphicsLayer.js";
import Graphic from "https://js.arcgis.com/5.0/@arcgis/core/Graphic.js";
import Camera from "https://js.arcgis.com/5.0/@arcgis/core/Camera.js";
import Point from "https://js.arcgis.com/5.0/@arcgis/core/geometry/Point.js";
import Zoom from "https://js.arcgis.com/5.0/@arcgis/core/widgets/Zoom.js";
import Home from "https://js.arcgis.com/5.0/@arcgis/core/widgets/Home.js";
import Compass from "https://js.arcgis.com/5.0/@arcgis/core/widgets/Compass.js";
import ScaleBar from "https://js.arcgis.com/5.0/@arcgis/core/widgets/ScaleBar.js";

/* ---------------- data ---------------- */
const SERVICE = "https://services5.arcgis.com/BFucnLaN4NszOvuE/arcgis/rest/services/PXXCICXXZZA/SceneServer";
const [AUDIT, COMP] = await Promise.all([
  fetch("assets/audit.json").then(r => r.json()),
  fetch("assets/compliance.json").then(r => r.json()),
]);
const M = AUDIT.model, C = AUDIT.counts, T = M.elements;
const fmt = n => n.toLocaleString("en-US");
const pct = n => Math.round(n * 1000 / T) / 10;

/* ---------------- tab routing ---------------- */
const views = {};
document.querySelectorAll(".view").forEach(v => { views[v.id.replace("view-", "")] = v; });
document.querySelectorAll("#tabs button").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b));
    Object.entries(views).forEach(([k, v]) => v.classList.toggle("on", k === b.dataset.view));
    if (b.dataset.view === "model") ensureScene();
    if (b.dataset.view === "context") ensureMap();
  };
});

/* ---------------- overview render ---------------- */
document.getElementById("stamp").textContent = "audited 5 Nov 2026";
document.getElementById("elCount").textContent = fmt(T);

// hero mini bars
document.getElementById("heroBars").innerHTML = AUDIT.disciplines.map(d => {
  const w = Math.max(6, Math.round(100 * d.n / T));
  return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
    <div style="flex:1;height:6px;background:#eef1f6;border-radius:3px;overflow:hidden">
      <div style="width:${w}%;height:100%;background:#0b5fa5;border-radius:3px"></div></div>
    <span style="font:10px/1 var(--mono);color:var(--dim);width:26px;text-align:right">${d.pct}%</span></div>`;
}).join("");

const KPIS = [
  { lbl: "Blocking issues", num: "3", cls: "bad",
    sub: "Marks, level assignment, slab coverage" },
  { lbl: "Empty sublayers", num: `${C.sublayers_empty}/${C.sublayers_total}`, cls: "bad",
    sub: "Infrastructure suite is entirely empty" },
  { lbl: "Mark field filled", num: `${(100 - AUDIT.tags.empty_pct).toFixed(1)}%`, cls: "bad",
    sub: `${fmt(AUDIT.tags.empty)} of ${fmt(T)} elements untagged` },
  { lbl: "Slab coverage", num: `${C.levels_with_slabs}/${C.levels_declared}`, cls: "bad",
    sub: "R/F and TR/F have no floor slab" },
];
document.getElementById("kpis").innerHTML = KPIS.map(k => `
  <div class="kpi"><div class="lbl">${k.lbl}</div>
  <div class="num ${k.cls}">${k.num}</div><div class="sub">${k.sub}</div></div>`).join("");

/* ---------------- reusable bar list ---------------- */
function barList(el, rows, opt = {}) {
  const max = Math.max(...rows.map(r => r.n));
  el.innerHTML = rows.map(r => {
    const w = Math.max(1.5, 100 * r.n / max);
    const p = opt.pct ? `<span>${(100 * r.n / T).toFixed(1)}%</span>` : "";
    return `<div class="brow">
      <div><div class="bl"><span>${r.name}</span>${p}</div>
      <div class="btrack"><div class="bfill${opt.dim ? " dim" : ""}" style="width:${w}%"></div></div></div>
      <div class="bn">${fmt(r.n)}</div></div>`;
  }).join("");
}
barList(document.getElementById("chartDisc"), AUDIT.disciplines, { pct: true });
barList(document.getElementById("chartCat"), AUDIT.categories.slice(0, 18), { pct: true });

/* ---------------- level stack ---------------- */
(function levels() {
  const max = Math.max(...AUDIT.levels.map(l => l.elements));
  document.getElementById("chartLevels").innerHTML = AUDIT.levels.slice().reverse().map(l => {
    const w = Math.max(1, 100 * l.elements / max);
    const lbl = l.label === "UNASSIGNED" ? "unassigned" : l.label;
    const elev = l.elev === null ? "—" : `${l.elev}`;
    return `<div class="lvl">
      <div class="ln" title="${lbl}">${lbl.replace(" +", " +")}</div>
      <div class="ltrack"><div class="lfill" style="width:${w}%"></div></div>
      <div class="le">${fmt(l.elements)}</div>
    </div>`;
  }).join("") + `<div class="lvl" style="margin-top:6px;border-top:1px solid var(--line2);padding-top:6px">
      <div class="ln" style="color:var(--dim)">slabs</div>
      <div class="le" style="grid-column:2/4;text-align:left">${C.slabs} slabs · R/F +56.75 and TR/F +63.25 carry none</div></div>`;
})();

/* ---------------- completeness ---------------- */
(function completeness() {
  const rows = [
    { name: "BldgLevel (numeric ID)", filled: 100 },
    { name: "Discipline", filled: 100 },
    { name: "Category", filled: 100 },
    { name: "Family", filled: 99.1 },
    { name: "ElementType", filled: 100 },
    { name: "Mark", filled: 14.0 },
    { name: "Typ_Mark", filled: 4.2 },
    { name: "BldgLevel_Desc", filled: 94.0 },
  ];
  document.getElementById("chartFill").innerHTML = rows.map(r => {
    const bad = r.filled < 50;
    return `<div class="brow"><div>
      <div class="bl"><span>${r.name}</span><span>${r.filled}%</span></div>
      <div class="btrack"><div class="bfill" style="width:${r.filled}%;background:${bad ? "var(--fail)" : "var(--accent)"}"></div></div>
    </div><div class="bn" style="color:${bad ? "var(--fail)" : "var(--ink2)"}">${bad ? "gap" : "ok"}</div></div>`;
  }).join("");
})();

/* ============================================================
   FINDINGS
   ============================================================ */
const FINDINGS = [
  {
    n: "F-01", title: "Mark is not unique", cls: "",
    metric: fmt(AUDIT.tags.extra_rows), mlbl: "extra rows any join produces",
    body: `Only ${fmt(AUDIT.tags.filled)} elements carry a Mark — ${AUDIT.tags.empty_pct}% do not.
      Of the ${fmt(AUDIT.tags.distinct)} distinct values, ${fmt(AUDIT.tags.duplicate_values)} repeat,
      so the field is not a key. Mark <code>104</code> alone appears ${AUDIT.tags.worst[0].n} times.`,
    impact: "Any asset register, COBie drop or maintenance ticket keyed on Mark will double-count or bind to the wrong component.",
  },
  {
    n: "F-02", title: "2,409 elements have no level", cls: "cant",
    metric: fmt(AUDIT.no_level.n), mlbl: `elements on level ID −2147483648 (${AUDIT.no_level.pct}%)`,
    body: `Unassigned Revit level IDs export as a signed-32-bit sentinel. The largest groups are
      stair railings (${AUDIT.no_level.by_sublayer[0].n}), structural framing
      (${AUDIT.no_level.by_sublayer[1].n}) and lighting fixtures (${AUDIT.no_level.by_sublayer[2].n}).`,
    impact: "Floor-by-floor quantities silently omit these, so every storey subtotal is low — and nothing flags it.",
  },
  {
    n: "F-03", title: "Element bounds are not geometry", cls: "",
    metric: `${C.levels_with_slabs} / ${C.levels_declared}`, mlbl: "levels carrying a floor slab",
    body: `410 slabs across ${C.levels_with_slabs} levels. R/F +56.75 and TR/F +63.25 have none, so a
      level-by-level area cannot be closed. Worse, the \`mbb\` on every element is not its own
      bounding box: all 40,283 rows inherit a node-level cube whose x/y are in DEGREES and whose
      0.05–122 degree extent covers thousands of kilometres. It cannot yield an area in m² at all.`,
    impact: "GFA, compartment area and floor-area ratios are not computable from this model — report 'cannot check', not a number. Any area derived from mbb is a unit error.",
  },
  {
    n: "F-04", title: "20 of 61 sublayers are empty", cls: "cant",
    metric: "20", mlbl: "declared sublayers with zero geometry",
    body: `The whole Infrastructure suite (abutments, decks, piers, bearings, tendons, framing) is empty,
      along with furniture, planting, mass, parking, topography and five electrical device classes.`,
    impact: "The layer advertises scope it does not deliver. Any discipline-completeness claim must exclude these.",
  },
  {
    n: "F-05", title: "Structural is 1.5% of the model", cls: "cant",
    metric: "616", mlbl: "structural elements (1.5% of all)",
    body: `Against 18,297 piping elements. Beams is an empty sublayer; columns, framing and foundations
      are present but sparse. This is a coordinated MEP/architectural model, not a structural one.`,
    impact: "Load paths, framing continuity and structural coordination cannot be reviewed here.",
  },
  {
    n: "F-06", title: "Rooms carry no name", cls: "cant",
    metric: "0 / 204", mlbl: "room elements with a room name or number",
    body: `204 room elements exist, none has a name, number or occupancy attribute — the fields are
      absent from the export schema rather than merely blank.`,
    impact: "Space-type checks, occupancy load, and any room-based schedule are not possible from this source.",
  },
];
document.getElementById("cards").innerHTML = FINDINGS.map(f => `
  <article class="card ${f.cls}">
    <div class="ct"><span class="cn">${f.n}</span>
      <span class="v ${f.cls === "cant" ? "cant" : "fail"}">${f.cls === "cant" ? "cannot check" : "fail"}</span></div>
    <h4>${f.title}</h4>
    <div class="metric">${f.metric}</div>
    <div class="mlbl">${f.mlbl}</div>
    <p>${f.body}</p>
    <div class="impact">${f.impact}</div>
  </article>`).join("");

barList(document.getElementById("chartDup"), AUDIT.tags.worst.map(w => ({ name: `Mark ${w.mark}`, n: w.n })),
  { dim: true });
barList(document.getElementById("chartNoLevel"), AUDIT.no_level.by_sublayer.map(w => ({ name: w.name, n: w.n })),
  { dim: true });

/* ============================================================
   STATUTORY 2D
   ============================================================ */
let mapView = null, mapLayers = {};
const ZONE_COLOR = {
  "C(6)": [11, 95, 165, 0.42], "C(2)": [56, 189, 248, 0.40], "C": [37, 99, 235, 0.34],
  "C(10)": [30, 64, 175, 0.40], "G/IC": [21, 128, 61, 0.38], "O": [22, 163, 74, 0.34],
  "MRDJ": [148, 163, 184, 0.26], "R": [168, 85, 247, 0.32],
};
const LAYERDEFS = [
  { key: "ozp_plan", name: "OZP scheme boundary", file: "assets/ozp_plan.geojson", on: true,
    color: [15, 23, 32, 0.03], outline: [15, 23, 32, 0.9], width: 2.2, dash: [6, 3] },
  { key: "zones", name: "Zoning (OZP zones)", file: "assets/zones.geojson", on: true,
    zone: true, outline: [255, 255, 255, 0.85], width: 1 },
  { key: "bhc", name: "Height control areas", file: "assets/bhc.geojson", on: false,
    color: [217, 119, 6, 0.16], outline: [180, 83, 9, 0.9], width: 1.4, dash: [4, 3] },
  { key: "lots", name: "Lot boundaries", file: "assets/lots.geojson", on: true,
    color: [0, 0, 0, 0], outline: [100, 116, 139, 0.75], width: 0.9 },
  { key: "roads", name: "Road centrelines", file: "assets/roads.geojson", on: false,
    line: true, color: [120, 113, 108, 0.95], width: 1.6 },
];
// Basemap ids must be ones the 5.0 Basemap module actually knows. "light-gray" and
// "light-gray-vector" are NOT among them — Basemap.fromId() returns null for both, the Map
// silently drops the property, and map.basemap ends up null with no ground drawn at all.
// "gray-vector" is the real id behind the Light Gray Canvas style.
const BASE = {
  "Light gray": "gray-vector",
  "Streets": "streets-vector",
  "Imagery": "satellite",
  "Topographic": "topo-vector",
};

function zoneFill(label) {
  return ZONE_COLOR[label] || [11, 95, 165, 0.32];
}

async function ensureMap() {
  if (mapView) return;
  // The SDK's own basemap styles carry the correct tiling scheme. A hand-built TileLayer to
  // Canvas/World_Light_Gray_Base leaves the view at zoom -1 (no matching LOD), which silently
  // pins the map to a territory-wide scale.
  // "gray-vector", not "light-gray": the latter is not a 5.0 basemap id, so the Map silently
  // drops it and the ground comes up empty. See the BASE table for the full id mapping.
  const map = new Map({ basemap: "gray-vector" });
  mapView = new MapView({
    container: "map2d", map,
    center: [114.173988, 22.29919], zoom: 18,
    constraints: { snapToZoom: false, minZoom: 14, maxZoom: 20 },
    popupEnabled: false,
  });
  const [sx0, sy0, sx1, sy1] = M.bbox;
  mapView.when(() => {
    // `scale`, not `zoom`: a WebTileLayer basemap resolves zoom to -1 until its LODs settle.
    mapView.goTo({
      center: { longitude: (sx0 + sx1) / 2 - 0.00055, latitude: (sy0 + sy1) / 2 },
      scale: 4200,
    }, { animate: false }).catch(() => { });
  });
  window.__mv = mapView;

  for (const d of LAYERDEFS) {
    const isLine = !!d.line;
    const isZone = !!d.zone;
    const layer = new GeoJSONLayer({
      url: d.file,
      title: d.name,
      renderer: isLine
        ? { type: "simple", symbol: { type: "simple-line", color: d.color, width: d.width } }
        : isZone
          ? { type: "unique-value", field: "ZONE_LABEL",
              uniqueValueInfos: Object.keys(ZONE_COLOR).map(k => ({
                value: k, label: k,
                symbol: { type: "simple-fill", color: zoneFill(k),
                  outline: { color: d.outline, width: d.width } },
              })),
              defaultSymbol: { type: "simple-fill", color: zoneFill(null),
                outline: { color: d.outline, width: d.width } },
              defaultLabel: "other zone" }
          : { type: "simple", symbol: { type: "simple-fill", color: d.color,
              outline: { color: d.outline, width: d.width, style: d.dash ? "dash" : "solid" } } },
      opacity: 1,
    });
    layer.visible = d.on;
    map.add(layer);
    mapLayers[d.key] = { layer, def: d };
  }

  // model footprint outline
  const fp = new GraphicsLayer({ title: "Model footprint" });
  map.add(fp);
  const [x0, y0, x1, y1] = M.bbox;
  fp.add(new Graphic({
    geometry: { type: "polygon", rings: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] },
    symbol: { type: "simple-fill", color: [194, 65, 12, 0.14],
      outline: { color: [194, 65, 12, 1], width: 2.4 } },
  }));

  mapView.ui.add(new Zoom({ view: mapView }), "bottom-right");
  mapView.ui.add(new ScaleBar({ view: mapView, unit: "metric" }), "bottom-left");
  mapView.ui.add(new Home({ view: mapView }), "bottom-right");
  mapView.ui.add(new Compass({ view: mapView }), "bottom-right");
  window.__mv = mapView;

  // The model extent is the subject: label it so the footprint reads as "this is the site".
  const lbl = new GraphicsLayer({ title: "Site label" });
  map.add(lbl);
  const [bx0, by0, bx1, by1] = M.bbox;
  lbl.add(new Graphic({
    geometry: { type: "point", longitude: (bx0 + bx1) / 2, latitude: by1 },
    symbol: {
      type: "text", color: "#7c2d12", haloColor: "#ffffff", haloSize: 2,
      text: "PXXCICXXZZA model", font: { size: 10, weight: "bold", family: "sans-serif" },
      yoffset: -8,
    },
  }));

  // hover / click readout
  mapView.on("click", async e => {
    const hit = (await mapView.hitTest(e)).results.find(r => r.graphic && r.graphic.layer);
    if (!hit) return;
    const a = hit.graphic.attributes || {};
    const bits = [];
    if (a.ZONE_LABEL) bits.push(`<b>${a.ZONE_LABEL}</b> ${a.DESC_ENG || ""}`);
    if (a.BHC_VALUE) bits.push(`Height control <b>${a.BHC_VALUE}</b> mPD`);
    if (a.LOTCSUID) bits.push(`Lot <b>${a.LOTCSUID}</b>`);
    if (a.ENGLISHSTREETNAME) bits.push(a.ENGLISHSTREETNAME);
    if (a.PLAN_NO) bits.push(`Plan ${a.PLAN_NO}`);
    if (bits.length) pickBox2(bits.join(" · "));
  });

  renderLayerToggles();
  renderBasemaps();
  renderHeadroom();
}

function pickBox2(html) {
  const el = document.getElementById("ctxPick");
  if (!el) return;
  el.innerHTML = html;
  el.classList.remove("empty");
}

function renderLayerToggles() {
  document.getElementById("layerToggles").innerHTML = LAYERDEFS.map(d => {
    const sw = d.zone ? "#0b5fa5" : d.line ? "#78716c" : `rgb(${d.color.slice(0, 3)})`;
    const cnt = { ozp_plan: 1, zones: 33, bhc: 33, lots: 442, roads: 233 }[d.key];
    return `<label class="lrow">
      <input type="checkbox" ${d.on ? "checked" : ""} data-layer="${d.key}">
      <span class="lswatch" style="background:${sw}"></span>
      <span class="lnm">${d.name}</span><span class="lc">${cnt}</span></label>`;
  }).join("");
  document.querySelectorAll("[data-layer]").forEach(cb => {
    cb.onchange = () => {
      const d = mapLayers[cb.dataset.layer];
      if (d) d.layer.visible = cb.checked;
    };
  });
}

function renderBasemaps() {
  const el = document.getElementById("baseChips");
  el.innerHTML = Object.keys(BASE).map((k, i) =>
    `<button class="chip${i === 0 ? " on" : ""}" data-base="${k}">${k}</button>`).join("");
  el.querySelectorAll("[data-base]").forEach(b => {
    b.onclick = () => {
      el.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === b));
      // swapping basemap keeps the overlay layers: the SDK re-creates the basemap and leaves
      // operational layers alone, so no removeAll() here.
      mapView.map.basemap = BASE[b.dataset.base];
    };
  });
}

function renderHeadroom() {
  const top = COMP.model_top.highest_elev_m;
  const rows = [
    { label: "C(6) parcel", cap: 110, note: "55.2% of footprint" },
    { label: "East strip", cap: 130, note: "7.8% of footprint" },
  ];
  document.getElementById("headroom").innerHTML = rows.map(r => {
    const used = Math.min(100, 100 * top / r.cap);
    return `<div class="hrow"><div>
      <div class="hl"><span>${r.label}</span><span style="color:var(--dim)">${r.cap} mPD</span></div>
      <div class="htrack"><div class="hfill" style="width:${used}%"></div>
        <div class="hmark" style="left:${Math.min(99, used)}%"></div></div>
    </div><div class="hval">+${(r.cap - top).toFixed(1)} m</div></div>`;
  }).join("") + `<p class="note sm" style="margin-top:2px">
    Model top <b>${top} mPD</b> against each cap. Headroom is arithmetic on the control value, not a
    determination of permitted development.</p>`;
}

/* statutory panel text */
document.getElementById("planDate").textContent = COMP.plan.approved;
document.getElementById("zoneLbl").textContent = COMP.zone.label;
document.getElementById("zoneDesc").textContent = COMP.zone.desc_en;
document.getElementById("bhcVal").textContent = COMP.height.value;
document.getElementById("lotN").textContent = COMP.lots.under_bbox;
document.getElementById("topLvl").textContent = COMP.model_top.highest_elev_m;

/* ============================================================
   3D MODEL
   ============================================================ */
let sceneView = null, bim = null, sceneBuilt = false;
let picked = { floors: new Set(), discs: new Set(), hiddenCats: new Set() };
const leafAll = [];

async function ensureScene() {
  if (sceneBuilt) { sceneView && sceneView.resize(); return; }
  sceneBuilt = true;
  const status = document.getElementById("sceneStatus");
  status.textContent = "opening scene…";

  // Light-gray canvas, not satellite: Esri's World_Imagery has a genuine data gap over this part
  // of Tsim Sha Tsui at high zoom (grey "Map data not yet available" tiles across the site).
  // A neutral cartographic ground also reads better behind a BIM model in a presentation.
  // Use the SDK's own style, not a hand-built TileLayer — a raw TileLayer has no matching LOD
  // scheme for a SceneView and fails to create a layerview.
  // "gray-vector" — NOT "light-gray". Basemap.fromId("light-gray") returns null in 5.0, the Map
  // silently discards the property, and map.basemap === null: no ground is drawn at all.
  // ground:"none" is deliberate: the model's levels are absolute mPD and the site sits at ~57 mPD,
  // so a world-elevation terrain surface buries all but the top 7 m of the building.
  const map = new Map({ basemap: "gray-vector", ground: "none" });
  sceneView = new SceneView({
    container: "scene3d", map,
    camera: { position: { longitude: 114.1722, latitude: 22.2976, z: 300 }, tilt: 62, heading: 18 },
    environment: {
      atmosphereEnabled: false, starsEnabled: false,
      // explicit type: a bare {color} fails autocast to ColorBackground in 5.0
      background: { type: "color", color: "#eef1f6" },
    },
    popupEnabled: false,
  });
  sceneView.ui.add(new Zoom({ view: sceneView }), "bottom-right");
  sceneView.ui.add(new Compass({ view: sceneView }), "bottom-right");
  sceneView.ui.add(new ScaleBar({ view: sceneView, unit: "metric" }), "bottom-left");
  window.__sv = sceneView;

  // Virtual lighting, set BEFORE the building layer exists.
  // The default is a date-based sun, which at this latitude throws a warm yellow cast that stains
  // the model's neutral grey and glass materials gold. Setting it in the SceneView constructor is
  // accepted but the first frames still shade with the sun, and re-assigning it later forces the
  // BSL to rebuild its tiles — during which the curtain wall drops out. Applying it here, while
  // the scene is still empty, gives colour-neutral shading with nothing to invalidate.
  await sceneView.when();
  sceneView.environment.lighting = { type: "virtual" };

  bim = new BuildingSceneLayer({ url: SERVICE, title: "PXXCICXXZZA" });
  map.add(bim, 0);
  try {
    await bim.load();
  } catch (e) {
    status.textContent = "model failed to load";
    return;
  }
  // a hosted Building layer ships its root groups invisible
  (function show(list) {
    (list || []).forEach(s => { try { s.visible = true; } catch (e) { } if (s.sublayers) show(s.sublayers); });
  })(bim.sublayers);
  (function walk(list) {
    (list || []).forEach(s => {
      if (s.sublayers && s.sublayers.length) walk(s.sublayers);
      else leafAll.push(s);
    });
  })(bim.sublayers);

  buildModelPanel();
  await frameModel(46, 18, 30);
  status.textContent = `${leafAll.length} components · i3s live`;
  status.classList.add("ok");
  initInspector();
}

/* ---------------------------------------------------------------
   Element inspector.
   hitTest() returns nothing on a BuildingSceneLayer — the SDK has no
   picking for hosted BSL sublayers — so the inspector drives a curated
   sample pulled from the same i3s attribute pages the audit read.
   --------------------------------------------------------------- */
let ELEMENTS = [], elIdx = 0;
async function initInspector() {
  if (ELEMENTS.length) return;
  ELEMENTS = await fetch("assets/elements.json").then(r => r.json());
  showElement(0);
  const box = document.getElementById("pickBox");
  document.getElementById("btnPrev").onclick = () => showElement(--elIdx);
  document.getElementById("btnNext").onclick = () => showElement(++elIdx);
  document.getElementById("btnPickDup").onclick = () => {
    // demonstrate the duplicate-Mark finding: jump to two components sharing one Mark
    const first = ELEMENTS.findIndex(e => e.mark && ELEMENTS.filter(x => x.mark === e.mark).length > 1);
    showElement(first < 0 ? 0 : first);
  };
}

const DUP_COUNT = {};
function showElement(i) {
  if (!ELEMENTS.length) return;
  elIdx = (i + ELEMENTS.length) % ELEMENTS.length;
  const e = ELEMENTS[elIdx];
  const dup = e.mark ? ELEMENTS.filter(x => x.mark === e.mark).length : 0;
  const flagged = e.lvlId === -2147483648;
  const rows = [
    ["UID", e.uid], ["Category", e.cat], ["Family", e.fam || "—"],
    ["Type", e.type || "—"], ["Discipline", e.disc], ["Element type", e.et],
    ["Level", e.lvl || "(none)"], ["Level ID", String(e.lvlId)],
    ["Level elev (mPD)", e.elev === null ? "—" : String(e.elev)],
    ["Mark", e.mark || "(empty)"], ["Type Mark", e.typ || "(empty)"],
  ].map(([k, v]) => `<div class="pr"><span class="pk">${k}</span><span class="pv">${v}</span></div>`).join("");
  const warn = [
    flagged ? `<div class="pw bad">Level ID is the sentinel <code>${e.lvlId}</code> — this element belongs to no storey.</div>` : "",
    dup > 1 ? `<div class="pw bad">Mark <b>${e.mark}</b> is shared by ${dup} components in this sample alone.</div>` : "",
    !e.mark ? `<div class="pw warn">No Mark — 86% of the model is in this state.</div>` : "",
  ].join("");
  const box = document.getElementById("pickBox");
  box.className = "pick";
  box.innerHTML = `<div class="hd">${e.cat}${e.fam ? " · " + e.fam : ""}</div>${rows}${warn}`;
  document.getElementById("elIdx").textContent = `${elIdx + 1} / ${ELEMENTS.length}`;
}

function buildModelPanel() {
  // disciplines
  const dc = document.getElementById("discChips");
  dc.innerHTML = AUDIT.disciplines.map(d =>
    `<button class="chip" data-disc="${d.name}">${d.name}<span class="n">${fmt(d.n)}</span></button>`).join("");
  dc.querySelectorAll("[data-disc]").forEach(b => {
    b.onclick = () => {
      const k = b.dataset.disc;
      picked.discs.has(k) ? picked.discs.delete(k) : picked.discs.add(k);
      b.classList.toggle("on");
      applyFilter();
    };
  });

  // levels
  const lc = document.getElementById("lvlChips");
  lc.innerHTML = AUDIT.levels.filter(l => l.label !== "UNASSIGNED").map(l => {
    const num = l.label.match(/^(\d+)\//) ? l.label : null;
    const key = l.label;
    return `<button class="chip" data-lvl="${key}">${l.label.replace(/ \+[\d.]+$/, "")}<span class="n">${fmt(l.elements)}</span></button>`;
  }).join("");
  lc.querySelectorAll("[data-lvl]").forEach(b => {
    b.onclick = () => {
      const k = b.dataset.lvl;
      picked.floors.has(k) ? picked.floors.delete(k) : picked.floors.add(k);
      b.classList.toggle("on");
      document.getElementById("lvlLbl").textContent =
        picked.floors.size ? `${picked.floors.size} selected` : "all";
      applyFilter();
    };
  });

  // categories
  const cl = document.getElementById("catList");
  cl.innerHTML = AUDIT.categories.filter(c => c.n > 0).map(c =>
    `<label class="crow"><input type="checkbox" checked data-cat="${c.name}">
      <span class="cnm">${c.name || "(untyped)"}</span><span class="cn2">${fmt(c.n)}</span></label>`).join("");
  cl.querySelectorAll("[data-cat]").forEach(cb => {
    cb.onchange = () => {
      const k = cb.dataset.cat;
      cb.checked ? picked.hiddenCats.delete(k) : picked.hiddenCats.add(k);
      document.getElementById("catLbl").textContent =
        picked.hiddenCats.size ? `${AUDIT.categories.filter(c => c.n > 0).length - picked.hiddenCats.size} of 45` : "all 45";
      applyFilter();
    };
  });

  document.getElementById("btnHome").onclick = () => frameModel(46, 18, 30);
  document.getElementById("btnTop").onclick = () => frameModel(6, 0, 30);
  document.getElementById("btnReset").onclick = () => {
    picked = { floors: new Set(), discs: new Set(), hiddenCats: new Set() };
    document.querySelectorAll("#modelPanel .chip.on").forEach(c => c.classList.remove("on"));
    document.querySelectorAll("#catList input").forEach(c => c.checked = true);
    document.getElementById("lvlLbl").textContent = "all";
    document.getElementById("catLbl").textContent = "all 45";
    applyFilter();
  };
}

let filterUid = 0;
function applyFilter() {
  if (!bim) return;
  // All three controls become ONE BuildingFilter expression. The SDK honours a filter expression on
  // BldgLevel / Discipline / Category, whereas sublayer.visible requires a hand-maintained mapping
  // between Revit category names ("Pipe Fittings") and sublayer titles ("PipeFitting") that drifts.
  const parts = [];
  const total = AUDIT.categories.filter(c => c.n > 0).length;
  if (picked.hiddenCats.size && picked.hiddenCats.size < total) {
    const kept = AUDIT.categories.filter(c => c.n > 0 && !picked.hiddenCats.has(c.name))
      .map(c => `'${c.name.replace(/'/g, "''")}'`);
    if (kept.length) parts.push(`Category IN (${kept.join(",")})`);
  }
  if (picked.floors.size) {
    parts.push(`BldgLevel_Desc IN (${[...picked.floors].map(f => `'${f.replace(/'/g, "''")}'`).join(",")})`);
  }
  if (picked.discs.size) {
    parts.push(`Discipline IN (${[...picked.discs].map(f => `'${f.replace(/'/g, "''")}'`).join(",")})`);
  }
  if (!parts.length) { bim.activeFilterId = ""; return; }
  const bf = new BuildingFilter({
    name: "audit-" + (++filterUid),
    filterBlocks: [new BuildingFilterBlock({
      title: "audit filter", filterExpression: parts.join(" AND "), filterMode: { type: "solid" },
    })],
  });
  bim.filters.add(bf);
  bim.activeFilterId = bf.id;
}

const M_LAT = 110574, M_LON = 111320;
async function frameModel(tilt, heading, aimZ) {
  if (!bim) return;
  const [x0, y0, x1, y1] = M.bbox;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const cos = Math.cos(cy * Math.PI / 180);
  // Aim at the tower's mid-height, not the ground: arcgis tilt is measured from vertical, so a
  // camera aimed at z=0 pushes a 73 m building out of the top of the frame.
  const h = Number.isFinite(aimZ) ? aimZ : 30;
  const H = 132;                                        // eye height above datum
  const dist = Math.max((H - h) * Math.tan(tilt * Math.PI / 180), 40);
  // Nudge the aim west so the mass clears the 266 px control panel on the left.
  const aimLon = cx - 0.00055;
  const eyeLat = cy - (dist * Math.cos(heading * Math.PI / 180)) / M_LAT;
  const eyeLon = aimLon - (dist * Math.sin(heading * Math.PI / 180)) / (M_LON * cos);
  sceneView.camera = new Camera({
    position: { longitude: eyeLon, latitude: eyeLat, z: H },
    tilt, heading,
  });
}
