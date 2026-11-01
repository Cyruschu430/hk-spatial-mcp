"""Record the reference trace for each fixture, once, against the live server.

    python -m eval.record            # (re)record every fixture into eval/traces/<id>.jsonl
    python -m eval.record --freeze   # also write the observed answer into the fixture

This is the ONLY step that touches the network. `eval/run.py` replays the frozen traces offline, so
CI needs no credentials and no connectivity. Ground truth is computed here and frozen into the
fixture file — never recomputed by the agent under test.

The trace records {"i","tool","args","ok","ms"} per the protocol, plus "out" (the parsed tool
output), which is what makes outcome accuracy checkable without re-running anything.
"""
import argparse
import asyncio
import json
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import mcp_client  # noqa: E402


def _dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise KeyError(f"{path}: no {part!r}")
    return cur


def _freeze_value(v):
    """Strings longer than 40 chars are frozen as a '~' substring match; everything else as-is."""
    if isinstance(v, str) and len(v) > 40:
        return "~" + v[:40]
    return v


async def record(fixtures, freeze):
    out = {}
    # keep the server's workspace out of the repo while recording
    env = {"HK_MCP_DIR": str(pathlib.Path(tempfile.mkdtemp(prefix="hks-eval-")))}
    async with mcp_client.session(env) as s:
        for fx in fixtures:
            trace, i = [], 0
            for call in fx["expected_calls"]:
                t0 = time.time()
                res = await s.call_tool(call["tool"], call.get("args") or {})
                ms = int((time.time() - t0) * 1000)
                text = getattr(res.content[0], "text", "") if res.content else ""
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = text
                ok = not (isinstance(parsed, dict) and "error" in parsed)
                trace.append({"i": i, "tool": call["tool"], "args": call.get("args") or {},
                              "ok": ok, "ms": ms, "out": parsed})
                i += 1
            out[fx["id"]] = trace
            src = fx.get("answer_from", -1)
            last = trace[src]["out"] if trace else None
            print(f"{fx['id']:26} {len(trace)} call(s)  "
                  f"{'error: ' + str(last.get('error'))[:70] if isinstance(last, dict) and 'error' in last else 'ok'}")
            if freeze:
                vals, problems = {}, []
                for path in fx.get("freeze") or []:
                    try:
                        vals[path] = _freeze_value(_dig(last, path))
                    except KeyError as e:
                        problems.append(str(e))
                if problems:
                    print("   FREEZE PROBLEMS:", problems)
                    print("   available keys:", sorted(last) if isinstance(last, dict) else type(last))
                else:
                    fx["expected_value"] = vals
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true", help="write expected_value into each fixture")
    ap.add_argument("--only", default="", help="substring filter on fixture id")
    a = ap.parse_args(argv)

    paths = sorted((HERE / "fixtures").glob("*.json"))
    fixtures = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if a.only:
        fixtures = [f for f in fixtures if a.only in f["id"]]
    if not fixtures:
        print("no fixtures")
        return 1

    traces = asyncio.run(record(fixtures, a.freeze))

    (HERE / "traces").mkdir(exist_ok=True)
    for fx in fixtures:
        p = HERE / "traces" / f"{fx['id']}.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for row in traces[fx["id"]]:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        if a.freeze:
            (HERE / "fixtures" / f"{fx['id']}.json").write_text(
                json.dumps(fx, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(traces)} trace(s) to eval/traces/"
          + (" and froze expected_value into eval/fixtures/" if a.freeze else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
