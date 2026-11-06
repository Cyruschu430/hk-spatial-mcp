#!/usr/bin/env node
/**
 * Count elements per level / per service from the i3s attribute cache.
 *
 * Reproduces the numbers in ../measured.md with no browser, no network and no token — it only reads
 * the cache that `bim_audit` / `bim_elements` already wrote. Use it to check a documented figure in
 * a few seconds instead of re-auditing a model.
 *
 *   node count-nodes.cjs --dir <HK_MCP_DIR> [--prefix BuildingE] [--sublayer BuildingEWallMullions]
 *
 * --dir      the MCP workspace (HK_MCP_DIR). Caches live in <dir>/bim/*.json
 * --prefix   only services whose sublayer name starts with this string
 * --sublayer report node granularity for this one sublayer (the level-purity test)
 *
 * Exits 0 always — this is a report, not a gate. Read the output.
 */
const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const a = { prefix: "", sublayer: "", dir: "" };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--dir") a.dir = argv[++i];
    else if (k === "--prefix") a.prefix = argv[++i];
    else if (k === "--sublayer") a.sublayer = argv[++i];
    else if (k === "--help" || k === "-h") a.help = true;
  }
  return a;
}

/** The cache is written with json.dumps default, so unset floats are bare NaN — not valid JSON. */
function readCache(fp) {
  const raw = fs.readFileSync(fp, "utf8").replace(/:\s*NaN/g, ": null");
  return JSON.parse(raw);
}

function levelKey(v) {
  if (v === null || v === undefined || v === "") return "NaN";
  const f = Number(v);
  if (!Number.isFinite(f)) return "NaN";
  return Number.isInteger(f) ? String(f) : String(f);
}

function main() {
  const a = parseArgs(process.argv);
  if (a.help || !a.dir) {
    console.log("usage: node count-nodes.cjs --dir <HK_MCP_DIR> [--prefix BuildingE] [--sublayer NAME]");
    process.exit(a.help ? 0 : 2);
  }

  const bimDir = path.join(a.dir, "bim");
  if (!fs.existsSync(bimDir)) {
    console.error(`no cache at ${bimDir} — run bim_audit or bim_elements first, or check --dir`);
    process.exit(2);
  }

  const files = fs.readdirSync(bimDir).filter(f => f.endsWith(".json"));
  const best = new Map();      // sublayer -> rows (keep the largest read)
  const granularity = new Map(); // sublayer -> { nodes:Set, levels:Map<node,Set> }

  let read = 0, skipped = 0;
  for (const f of files) {
    let d;
    try { d = readCache(path.join(bimDir, f)); } catch (_) { skipped++; continue; }
    const rows = d.rows || [];
    const sl = d.sublayer || "";
    if (!rows.length || !sl) { skipped++; continue; }
    // a multi-sublayer dump lists them comma-joined; it is not a single-service cache
    if (sl.includes(",")) { skipped++; continue; }
    if (a.prefix && !sl.startsWith(a.prefix)) { skipped++; continue; }
    read++;

    if (!best.has(sl) || rows.length > best.get(sl).length) best.set(sl, rows);

    if (!granularity.has(sl)) granularity.set(sl, { nodes: new Set(), per: new Map() });
    const g = granularity.get(sl);
    for (const r of rows) {
      const n = String(r.node);
      g.nodes.add(n);
      if (!g.per.has(n)) g.per.set(n, new Set());
      g.per.get(n).add(levelKey(r.BldgLevel));
    }
  }

  const services = [...best.keys()].sort((x, y) => best.get(y).length - best.get(x).length);
  const total = services.reduce((s, k) => s + best.get(k).length, 0);

  console.log(`\nread ${read} cache file(s), skipped ${skipped} (not single-service / empty / bad JSON)`);
  console.log(`services: ${services.length}   elements: ${total}\n`);

  console.log("per service:");
  for (const k of services) {
    const n = best.get(k).length;
    const pct = total ? (100 * n / total).toFixed(1) : "0.0";
    console.log(`  ${k.padEnd(36)} ${String(n).padStart(7)}  ${pct.padStart(5)}%`);
  }
  const tiny = services.filter(k => best.get(k).length <= 2).length;
  console.log(`  ${tiny} of ${services.length} services hold <= 2 elements`);

  console.log("\nlevel census:");
  const census = new Map();
  for (const k of services) {
    for (const r of best.get(k)) {
      const key = levelKey(r.BldgLevel);
      census.set(key, (census.get(key) || 0) + 1);
    }
  }
  const ordered = [...census.entries()].sort((x, y) => {
    const nx = x[0] === "NaN" ? Infinity : Number(x[0]);
    const ny = y[0] === "NaN" ? Infinity : Number(y[0]);
    return nx - ny;
  });
  for (const [k, v] of ordered) console.log(`  level ${k.padEnd(6)} ${String(v).padStart(7)}`);

  // node granularity — the test that decides whether a BldgLevel filter can work at all
  const targets = a.sublayer ? [a.sublayer] : services.slice(0, 1);
  for (const sl of targets) {
    const g = granularity.get(sl);
    if (!g) { console.log(`\n(no cache for sublayer ${sl})`); continue; }
    const pure = [...g.per.values()].filter(s => s.size === 1).length;
    const shapes = new Map();
    for (const s of g.per.values()) {
      const key = [...s].sort().join("+");
      shapes.set(key, (shapes.get(key) || 0) + 1);
    }
    console.log(`\nnode granularity — ${sl}`);
    console.log(`  elements            ${best.get(sl).length}`);
    console.log(`  distinct nodes      ${g.nodes.size}`);
    console.log(`  level-pure nodes    ${pure} of ${g.nodes.size}`);
    for (const [k, v] of [...shapes.entries()].sort((x, y) => y[1] - x[1])) {
      console.log(`    holds ${k.padEnd(12)} ${String(v).padStart(4)} nodes`);
    }
    if (pure === 0) {
      console.log("  => a BldgLevel filter CANNOT isolate a storey on this service.");
      console.log("     A predicate selects a node, never elements inside one.");
    }
  }
  console.log("");
}

main();
