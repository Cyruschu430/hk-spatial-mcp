"""Replay frozen traces through the trajectory evaluator.

    python -m eval.run                 # score every fixture, print the table
    python -m eval.run --out report.json
    python -m eval.run --only eo       # substring filter

Offline and deterministic by construction: it reads eval/fixtures/*.json and eval/traces/*.jsonl
and does arithmetic. No network, no credentials, no server process, no model in the loop.
Exit code 1 if any fixture fails the metrics its fixture declares in "fail_on".
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from eval import trajectory  # noqa: E402

COLS = ("q", "calls", "m1", "m2", "m3", "m4", "m5", "eff", "result")


def _mark(flag):
    return "PASS" if flag else "FAIL"


def run(fixtures, traces_dir):
    rows, missing = [], []
    for fx in fixtures:
        path = traces_dir / f"{fx['id']}.jsonl"
        if not path.exists():
            missing.append(fx["id"])
            continue
        trace = trajectory.load_trace(path)
        r = trajectory.score(trace, fx)
        rows.append((fx, r))
    return rows, missing


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="write the full report as JSON here")
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)

    fixtures = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((HERE / "fixtures").glob("*.json"))]
    if a.only:
        fixtures = [f for f in fixtures if a.only in f["id"]]

    rows, missing = run(fixtures, HERE / "traces")

    head = (f"{'question':26} {'calls':>5} {'m1':>4} {'m2':>4} {'m3':>4} {'m4':>4} {'m5':>4} "
            f"{'eff':>5}  result")
    print(head)
    print("-" * len(head))
    failed = []
    for fx, r in rows:
        print(f"{fx['id'][:26]:26} {r['calls']:>5} {_mark(r['m1_tool_any_order']):>4} "
              f"{_mark(r['m2_tool_in_order']):>4} {_mark(r['m3_tool_exact_match']):>4} "
              f"{_mark(r['m4_parameter_accuracy']):>4} {_mark(r['m5_outcome_accuracy']):>4} "
              f"{r['efficiency']:>5.2f}  {_mark(r['pass'])}")
        if not r["pass"]:
            failed.append((fx, r))

    for fid in missing:
        print(f"{fid[:26]:26} {'-':>5}   no trace recorded — run: python -m eval.record")

    print("-" * len(head))
    print(f"{len(rows)} scored, {len(rows) - len(failed)} pass, {len(failed)} fail"
          + (f", {len(missing)} missing trace" if missing else ""))
    print("m1 tools-any-order  m2 tools-in-order  m3 tools-exact  m4 parameters+forbidden  "
          "m5 outcome  eff = calls/min_calls")

    for fx, r in failed:
        print(f"\n{fx['id']}")
        for key in ("missing_tools", "extra_tools", "param_problems", "forbidden_calls",
                    "value_problems"):
            if r.get(key):
                print(f"  {key}: {r[key]}")

    if a.out:
        report = {
            "fixtures": len(rows),
            "passed": len(rows) - len(failed),
            "failed": len(failed),
            "missing_traces": missing,
            "results": {fx["id"]: r for fx, r in rows},
        }
        pathlib.Path(a.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"\nreport -> {a.out}")

    return 1 if (failed or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
