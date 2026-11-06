#!/usr/bin/env node
/**
 * Verify a generated page loads under a given CSP — the experiment behind ../csp-lockdown.md.
 *
 * Serves nothing itself: point it at a URL that is already serving, and it reports CSP violations,
 * loaded layers and errors. Run it against two servers with different headers to reproduce the
 * four-environment table.
 *
 *   node verify-csp.cjs --url http://127.0.0.1:8777/bim-audit-building-e.html
 *   node verify-csp.cjs --url http://127.0.0.1:8779/... --expect-layers 21
 *
 * Requires: playwright + a chromium build.
 *   npm i -D playwright && npx playwright install chromium
 *
 * Browser resolution order: --executable, $CHROME_PATH, then playwright's bundled chromium.
 * That is deliberate — the original harness hardcoded a path under C:/Users/<me>/..., which made it
 * unrunnable for anyone else. Do not put an absolute path back in.
 *
 * Exit 1 if violations > 0 or layers loaded < --expect-layers, so it can gate a build.
 */
const { chromium } = require("playwright");

function parseArgs(argv) {
  const a = { url: "", expectLayers: 0, wait: 40000, page: "" };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--url") a.url = argv[++i];
    else if (k === "--expect-layers") a.expectLayers = parseInt(argv[++i], 10) || 0;
    else if (k === "--wait") a.wait = parseInt(argv[++i], 10) || 40000;
    else if (k === "--executable") a.page = argv[++i];
    else if (k === "--help" || k === "-h") a.help = true;
  }
  return a;
}

function classifyCsp(text) {
  const d = (text.match(/directive:\s*"([^"]+)"/) || [])[1];
  const u = (text.match(/'(https?:\/\/[^']+)'/) || [])[1];
  let host = "?";
  try { host = new URL(u).host; } catch (_) { /* keep '?' */ }
  return { directive: d ? d.split(" ")[0] : "?", host };
}

(async () => {
  const a = parseArgs(process.argv);
  if (a.help || !a.url) {
    console.log("usage: node verify-csp.cjs --url <URL> [--expect-layers 21] [--wait 40000]");
    process.exit(a.help ? 0 : 2);
  }

  const launchOpts = { args: ["--use-gl=angle", "--use-angle=d3d11", "--enable-unsafe-swiftshader"] };
  const exe = a.page || process.env.CHROME_PATH;
  if (exe) launchOpts.executablePath = exe;

  const browser = await chromium.launch(launchOpts);
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const violations = [];
  page.on("console", m => {
    const t = m.text();
    if (/Content Security Policy/i.test(t)) violations.push(classifyCsp(t));
  });
  const pageErrors = [];
  page.on("pageerror", e => pageErrors.push(String(e).slice(0, 200)));
  const failed = [];
  page.on("requestfailed", r => {
    const f = r.failure() || {};
    let host = "?";
    try { host = new URL(r.url()).host; } catch (_) { /* keep */ }
    failed.push({ host, err: f.errorText || "?" });
  });

  const t0 = Date.now();
  await page.goto(a.url, { waitUntil: "load", timeout: 120000 });
  await page.waitForFunction("window.__ready === true", null, { timeout: a.wait }).catch(() => {});
  await page.waitForTimeout(12000);
  const bootMs = Date.now() - t0;

  const state = await page.evaluate(() => ({
    loaded: window.__audit ? window.__audit.state.loaded : null,
    failed: window.__audit ? window.__audit.state.failed.length : null,
    stat: document.getElementById("stat") ? document.getElementById("stat").innerText : null,
  }));

  // ERR_ABORTED means cancelled (camera motion / teardown), not blocked
  const realFailures = failed.filter(f => !/ERR_ABORTED/.test(f.err));
  const byDirective = {}, byHost = {};
  for (const v of violations) {
    byDirective[v.directive] = (byDirective[v.directive] || 0) + 1;
    byHost[v.host] = (byHost[v.host] || 0) + 1;
  }

  console.log(`\n${a.url}`);
  console.log(`  CSP violations  : ${violations.length}` +
    (violations.length ? "  " + JSON.stringify({ byDirective, byHost }) : ""));
  console.log(`  page errors     : ${pageErrors.length ? JSON.stringify([...new Set(pageErrors)].slice(0, 4)) : "none"}`);
  console.log(`  failed requests : ${realFailures.length}` +
    (realFailures.length ? "  " + JSON.stringify(realFailures.slice(0, 5)) : " (0 non-abort)"));
  console.log(`  layers          : ${state.loaded}/${state.loaded !== null ? 21 : "?"} loaded, ${state.failed} failed`);
  console.log(`  boot            : ${(bootMs / 1000).toFixed(1)}s`);
  if (state.stat) console.log(`  status bar      : ${state.stat.replace(/\n/g, " | ")}`);

  await browser.close();

  const ok = violations.length === 0 && (a.expectLayers === 0 || (state.loaded || 0) >= a.expectLayers);
  console.log(`  RESULT          : ${ok ? "PASS" : "FAIL"}\n`);
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error("FATAL", e); process.exit(1); });
