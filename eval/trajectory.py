"""Deterministic trajectory scoring for recorded agent runs.

A trace is a list of calls, one dict each:
    {"i": 0, "tool": "load_hk_data", "args": {...}, "ok": true, "ms": 412, "out": {...}}

Scoring is pure: same (trace, expected) in => same metrics out, byte-identical. No LLM judge, no
network, no imports beyond the standard library, and nothing here calls the server under test.

Metrics
  1 tool_any_order     set of tools actually used == set expected
  2 tool_in_order      expected tool sequence is a subsequence of the actual one
  3 tool_exact_match   actual sequence == expected sequence, after dropping declared-irrelevant calls
  4 parameter_accuracy every expected call is found in order and its arguments match
                       (exact for identifiers, within tolerance for numbers), and no forbidden tool
                       was called — this is what catches a fabricated dataset or layer
  5 outcome_accuracy   the answer taken from the declared call matches the frozen expected value
  6 efficiency         calls_used / min_calls — reported, never failed on

`pass` is metric 1-4 by default (the trajectory path). A fixture may widen it, e.g.
"fail_on": ["m1","m2","m3","m4","m5"], which the negative fixture uses so a fabricated number
cannot pass either.
"""
import json

DEFAULT_FAIL_ON = ("m1", "m2", "m3", "m4")


# ---------------------------------------------------------------- helpers

def load_trace(path):
    """Read JSONL, skipping blanks. Keeps the file order, which is the trajectory."""
    calls = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                calls.append(json.loads(line))
    return calls


def _dig(obj, path):
    """Value at a dotted path ('best_scene.id'), or a sentinel when absent."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


class _Missing:
    def __repr__(self):
        return "<missing>"


_MISSING = _Missing()


def _close(a, b, tol):
    """Numbers within tol, strings exact, or substring when the expectation starts with '~'."""
    if isinstance(b, str) and b.startswith("~"):
        return isinstance(a, str) and b[1:] in a
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    return a == b


def _args_match(expected_args, actual_args, tolerance, subset_keys):
    """Identifiers exact, numbers within tolerance, declared list keys as a subset."""
    if not expected_args:
        return True, []
    problems = []
    for key, want in expected_args.items():
        if key not in actual_args:
            problems.append(f"missing arg {key!r}")
            continue
        got = actual_args[key]
        if key in subset_keys:
            if not isinstance(got, list) or not set(want) <= set(got):
                problems.append(f"arg {key!r}: {got!r} does not contain {want!r}")
        elif key in tolerance:
            if not _close(got, want, tolerance[key]):
                problems.append(f"arg {key!r}: {got!r} != {want!r} +/-{tolerance[key]}")
        elif not _close(got, want, 0):
            problems.append(f"arg {key!r}: {got!r} != {want!r}")
    return (not problems), problems


def _align(actual, expected_calls):
    """Greedy in-order alignment of expected calls onto actual calls, by tool name.

    Returns [(expected_index, actual_index or None)].
    """
    pairs, cursor = [], 0
    for k, want in enumerate(expected_calls):
        found = None
        for j in range(cursor, len(actual)):
            if actual[j].get("tool") == want["tool"]:
                found = j
                break
        pairs.append((k, found))
        if found is not None:
            cursor = found + 1
    return pairs


# ---------------------------------------------------------------- scoring

def score(trace, expected):
    """Score one trace against one frozen fixture. Pure function."""
    expected_calls = expected.get("expected_calls") or []
    irrelevant = set(expected.get("irrelevant_tools") or [])
    forbidden = set(expected.get("forbid_tools") or [])
    tolerance = expected.get("args_tolerance") or {}
    subset_keys = set(expected.get("args_subset") or [])
    value_tol = expected.get("value_tolerance") or {}

    # tools used, with declared-irrelevant noise removed (for metrics 1-3 only)
    used = [c.get("tool") for c in trace]
    used_rel = [t for t in used if t not in irrelevant]
    want_tools = [c["tool"] for c in expected_calls]

    # 1 any order
    missing = sorted(set(want_tools) - set(used))
    extra = sorted(set(used) - set(want_tools) - irrelevant)
    m1 = not missing and not extra

    # 2 in order (subsequence)
    it = iter(used_rel)
    m2 = all(any(t == want for t in it) for want in want_tools)

    # 3 exact match
    m3 = used_rel == want_tools

    # 4 parameters + forbidden tools
    pairs = _align(trace, expected_calls)
    param_problems = []
    for k, j in pairs:
        want = expected_calls[k]
        if j is None:
            param_problems.append(f"call {k} ({want['tool']}) never happened")
            continue
        ok, problems = _args_match(want.get("args") or {}, trace[j].get("args") or {},
                                   tolerance, subset_keys)
        if not ok:
            param_problems.extend(f"call {k} ({want['tool']}): {p}" for p in problems)
    forbidden_hits = sorted({c.get("tool") for c in trace if c.get("tool") in forbidden})
    m4 = not param_problems and not forbidden_hits

    # 5 outcome
    idx = expected.get("answer_from", -1)
    answer = trace[idx].get("out") if trace else None
    value_problems = []
    for path, want in (expected.get("expected_value") or {}).items():
        got = _dig(answer, path)
        if got is _MISSING:
            value_problems.append(f"{path}: absent from the answer")
        elif not _close(got, want, value_tol.get(path, 0)):
            value_problems.append(f"{path}: {got!r} != {want!r} +/-{value_tol.get(path, 0)}")
    m5 = not value_problems

    # 6 efficiency
    min_calls = int(expected.get("min_calls") or len(want_tools) or 1)
    efficiency = round(len(trace) / min_calls, 2) if min_calls else 0.0

    fail_on = expected.get("fail_on") or list(DEFAULT_FAIL_ON)
    flags = {"m1": m1, "m2": m2, "m3": m3, "m4": m4, "m5": m5}
    passed = all(flags[m] for m in fail_on)

    return {
        "m1_tool_any_order": m1,
        "m2_tool_in_order": m2,
        "m3_tool_exact_match": m3,
        "m4_parameter_accuracy": m4,
        "m5_outcome_accuracy": m5,
        "efficiency": efficiency,
        "calls": len(trace),
        "min_calls": min_calls,
        "missing_tools": missing,
        "extra_tools": extra,
        "param_problems": param_problems,
        "forbidden_calls": forbidden_hits,
        "value_problems": value_problems,
        "fail_on": list(fail_on),
        "pass": passed,
    }


# ---------------------------------------------------------------- self-check

def _selfcheck():
    exp = {
        "expected_calls": [
            {"tool": "load_hk_data", "args": {"dataset_id": "afcd_1"}, "args_tolerance": {}},
            {"tool": "summarise_hk_data", "args": {"dataset_id": "afcd_1", "agg": "count"}},
        ],
        "min_calls": 2,
        "expected_value": {"total": 100},
        "value_tolerance": {"total": 0},
    }
    good = [
        {"i": 0, "tool": "load_hk_data", "args": {"dataset_id": "afcd_1"}, "ok": True, "ms": 10,
         "out": {"features": 100}},
        {"i": 1, "tool": "summarise_hk_data", "args": {"dataset_id": "afcd_1", "agg": "count"},
         "ok": True, "ms": 5, "out": {"total": 100}},
    ]
    r = score(good, exp)
    assert r["m1_tool_any_order"] and r["m2_tool_in_order"] and r["m3_tool_exact_match"], r
    assert r["m4_parameter_accuracy"] and r["m5_outcome_accuracy"] and r["pass"], r
    assert r["efficiency"] == 1.0, r

    # wrong order: same set, not a subsequence, not exact
    swapped = [good[1], good[0]]
    r = score(swapped, exp)
    assert r["m1_tool_any_order"] is True, r          # same tools
    assert r["m2_tool_in_order"] is False, r          # wrong order
    assert r["m3_tool_exact_match"] is False, r
    assert r["pass"] is False, r

    # right answer by the wrong path
    lucky = [
        {"i": 0, "tool": "query_hk_data", "args": {"dataset_id": "afcd_1"}, "ok": True, "ms": 3,
         "out": {}},
        {"i": 1, "tool": "summarise_hk_data", "args": {"dataset_id": "afcd_1", "agg": "count"},
         "ok": True, "ms": 3, "out": {"total": 100}},
    ]
    r = score(lucky, exp)
    assert r["m1_tool_any_order"] is False and r["m5_outcome_accuracy"] is True, r
    assert r["pass"] is False, r

    # numeric tolerance
    exp_tol = dict(exp, expected_calls=[
        {"tool": "load_hk_data", "args": {"dataset_id": "afcd_1"}},
        {"tool": "summarise_hk_data", "args": {"dataset_id": "afcd_1", "agg": "count"}}],
        args_tolerance={"distance": 5}, expected_value={"dist": 500}, value_tolerance={"dist": 5})
    near = [{"i": 0, "tool": "load_hk_data", "args": {"dataset_id": "afcd_1"}, "ok": True, "ms": 1,
             "out": {}},
            {"i": 1, "tool": "summarise_hk_data", "args": {"dataset_id": "afcd_1", "agg": "count"},
             "ok": True, "ms": 1, "out": {"dist": 503}}]
    assert score(near, exp_tol)["m5_outcome_accuracy"] is True
    near[1]["out"] = {"dist": 512}
    assert score(near, exp_tol)["m5_outcome_accuracy"] is False

    # irrelevant noise is dropped, but a forbidden tool is not
    noisy = [{"i": 0, "tool": "search_hk_data", "args": {}, "ok": True, "ms": 1, "out": {}}] + good
    r = score(noisy, dict(exp, irrelevant_tools=["search_hk_data"]))
    assert r["m1_tool_any_order"] and r["m3_tool_exact_match"] and r["pass"], r

    neg = {"expected_calls": [{"tool": "load_hk_data", "args": {"dataset_id": "does_not_exist"}}],
           "forbid_tools": ["build_map", "summarise_hk_data"],
           "expected_value": {"error_kind": "dataset_not_found"},
           "fail_on": ["m1", "m2", "m3", "m4", "m5"]}
    honest = [{"i": 0, "tool": "load_hk_data", "args": {"dataset_id": "does_not_exist"},
               "ok": False, "ms": 9, "out": {"error_kind": "dataset_not_found"}}]
    assert score(honest, neg)["pass"] is True
    # the same question answered by inventing a layer and a number MUST fail
    fabricated = [
        {"i": 0, "tool": "load_hk_data", "args": {"dataset_id": "does_not_exist"}, "ok": True,
         "ms": 9, "out": {"features": 42}},
        {"i": 1, "tool": "summarise_hk_data", "args": {"dataset_id": "does_not_exist"},
         "ok": True, "ms": 9, "out": {"total": 42}},
    ]
    r = score(fabricated, neg)
    assert r["m4_parameter_accuracy"] is False, r
    assert r["forbidden_calls"] == ["summarise_hk_data"], r
    assert r["pass"] is False, r

    # determinism: same inputs twice => identical serialisation
    a = json.dumps(score(good, exp), sort_keys=True)
    b = json.dumps(score(good, exp), sort_keys=True)
    assert a == b
    print("self-check ok — metrics 1-6, tolerance, irrelevant noise, fabrication guard, determinism")


if __name__ == "__main__":
    _selfcheck()
