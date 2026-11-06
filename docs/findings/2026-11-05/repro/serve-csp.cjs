#!/usr/bin/env node
/**
 * Reference CSP server — the policy in ../csp-lockdown.md, served so it can be tested.
 *
 * Two modes, because the point is the contrast:
 *
 *   node serve-csp.cjs --mode none   --root <dir> --port 8777   # no CSP header (the control)
 *   node serve-csp.cjs --mode strict --root <dir> --port 8779   # explicit ArcGIS hosts
 *   node serve-csp.cjs --mode host   --root <dir> --port 8780   # same, plus a bare `https:`
 *
 * With --mode strict the page loads 21/21 layers and reports 0 violations. With --mode none it also
 * loads 21/21 — that is what proves the header, not the code, is the variable. Point verify-csp.cjs
 * at both.
 *
 * `GET /__csp` returns the policy as text, so you can diff what was served against what the docs say
 * instead of trusting that the two match.
 *
 * --root is the directory holding the generated pages (the MCP workspace, HK_MCP_DIR).
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const ARCGIS = [
  "https://js.arcgis.com",
  "https://*.arcgis.com",
  "https://*.arcgisonline.com",
  "https://static.arcgis.com",
].join(" ");

const POLICIES = {
  // no header at all — the control condition
  none: null,

  // explicit hosts, no bare https:. Verified: 21/21 layers, 0 violations.
  strict: [
    `default-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data: ${ARCGIS}`,
    `script-src  'self' 'unsafe-inline' 'unsafe-eval' blob: data: ${ARCGIS}`,
    `style-src   'self' 'unsafe-inline' ${ARCGIS}`,
    `connect-src 'self' http://127.0.0.1:* ${ARCGIS}`,
    `img-src     'self' data: blob: ${ARCGIS}`,
    `font-src    'self' data: blob: ${ARCGIS}`,
    `worker-src  'self' blob: ${ARCGIS}`,
  ].join("; "),

  // the tempting shortcut: unblock the CDN by allowing the whole web. Works, but is not a fix.
  host: [
    `default-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data: ${ARCGIS}`,
    `script-src  'self' 'unsafe-inline' 'unsafe-eval' blob: data: https: ${ARCGIS}`,
    `style-src   'self' 'unsafe-inline' https: ${ARCGIS}`,
    `connect-src 'self' http://127.0.0.1:* https: ${ARCGIS}`,
    `img-src     'self' data: blob: https: ${ARCGIS}`,
    `font-src    'self' data: blob: https: ${ARCGIS}`,
    `worker-src  'self' blob: https: ${ARCGIS}`,
  ].join("; "),
};

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript",
  ".css": "text/css", ".json": "application/json", ".geojson": "application/json",
  ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".woff2": "font/woff2",
  ".glb": "model/gltf-binary", ".bin": "application/octet-stream",
};

function parseArgs(argv) {
  const a = { mode: "strict", root: "", port: 8779, index: "" };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--mode") a.mode = argv[++i];
    else if (k === "--root") a.root = argv[++i];
    else if (k === "--port") a.port = parseInt(argv[++i], 10) || a.port;
    else if (k === "--index") a.index = argv[++i];
    else if (k === "--help" || k === "-h") a.help = true;
  }
  return a;
}

const args = parseArgs(process.argv);
if (args.help || !args.root || !(args.mode in POLICIES)) {
  console.log("usage: node serve-csp.cjs --mode none|strict|host --root <dir> [--port 8779] [--index page.html]");
  console.log("modes:", Object.keys(POLICIES).join(", "));
  process.exit(args.help ? 0 : 2);
}

const ROOT = path.resolve(args.root);
if (!fs.existsSync(ROOT)) {
  console.error(`--root does not exist: ${ROOT}`);
  process.exit(2);
}

// a sane default landing page, if the workspace has one
const INDEX = args.index || (fs.existsSync(path.join(ROOT, "bim-audit-building-e.html"))
  ? "bim-audit-building-e.html" : "");

const CSP = POLICIES[args.mode];

http.createServer((req, res) => {
  const urlPath = decodeURIComponent(String(req.url).split("?")[0]);

  if (urlPath === "/__csp") {
    res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
    return res.end(CSP || "(no content-security-policy header in this mode)");
  }

  let rel = urlPath === "/" ? "/" + INDEX : urlPath;
  const target = path.join(ROOT, rel);

  // refuse anything that escapes --root
  if (!target.startsWith(ROOT + path.sep) && target !== ROOT) {
    res.writeHead(403, { "content-type": "text/plain" });
    return res.end("outside root");
  }
  if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) {
    res.writeHead(404, { "content-type": "text/plain" });
    return res.end("not found: " + rel);
  }

  const headers = {
    "content-type": TYPES[path.extname(target)] || "application/octet-stream",
    "cache-control": "no-store",
  };
  if (CSP) headers["content-security-policy"] = CSP;

  res.writeHead(200, headers);
  fs.createReadStream(target).pipe(res);
}).listen(args.port, "127.0.0.1", () => {
  console.log(`mode=${args.mode}  http://127.0.0.1:${args.port}/`);
  console.log(`root=${ROOT}`);
  console.log(CSP ? `csp: ${CSP}` : "csp: (none)");
  console.log(`policy: http://127.0.0.1:${args.port}/__csp`);
});
