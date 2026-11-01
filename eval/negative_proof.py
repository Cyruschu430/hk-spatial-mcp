"""Proof that the harness fails a fabricated answer, using the real negative fixture.

    python -m eval.negative_proof

Scores eval/traces/q10_negative_dataset.jsonl twice: once as recorded (the agent reported that the
dataset does not exist — correct), once with the same trace doctored to invent features and a count.
The first must pass, the second must fail. Exits 1 if the harness is too forgiving.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from eval import trajectory  # noqa: E402

FIXTURE = HERE / "fixtures" / "q10_negative_dataset.json"


def main():
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    trace = trajectory.load_trace(HERE / "traces" / "q10_negative_dataset.jsonl")

    honest = trajectory.score(trace, fx)
    print(f"as recorded        pass={honest['pass']}  "
          f"m4={honest['m4_parameter_accuracy']} m5={honest['m5_outcome_accuracy']}")

    # the same question, answered by inventing a layer and a number
    fake: list = [dict(trace[0], ok=True, out={"dataset_id": "landsd_rcd_0000000000000_00000",
                                         "features": 42})]
    fake.append({"i": 1, "tool": "summarise_hk_data",
                 "args": {"dataset_id": "landsd_rcd_0000000000000_00000"},
                 "ok": True, "ms": 12, "out": {"features": 42, "rows": [{"group": "x", "count": 42}]}})
    fabricated = trajectory.score(fake, fx)
    print(f"fabricated trace   pass={fabricated['pass']}  "
          f"m4={fabricated['m4_parameter_accuracy']} m5={fabricated['m5_outcome_accuracy']}  "
          f"forbidden={fabricated['forbidden_calls']}")

    assert honest["pass"] is True, f"the honest trace must pass: {honest}"
    assert fabricated["pass"] is False, f"a fabricated trace must fail: {fabricated}"
    assert fabricated["forbidden_calls"], "fabrication should be caught as a forbidden call"
    print("negative-case proof ok — reporting absence passes, inventing data fails")
    return 0


if __name__ == "__main__":
    sys.exit(main())
