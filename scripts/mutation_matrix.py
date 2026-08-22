"""Mutation testing for the evaluation harness.

Reintroduces each known defect one at a time and records which regression tests
turn red. A test count says how much effort went in; a mutation score says
whether the tests actually detect the failures they were written for. Only the
second is evidence.

A mutation that no test catches is a hole in the suite and is reported as
SURVIVED.

Usage:
    python scripts/mutation_matrix.py [--out docs/mutation_matrix.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.mutation_plugin import MUTATIONS  # noqa: E402

TEST_FILES = [
    "tests/test_backtest_engine_correctness.py",
    "tests/test_feature_pipeline_correctness.py",
]
FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def run_suite(mutation: str | None) -> tuple[int, int, list[str]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if mutation:
        env["BINFIN_MUTATION"] = mutation
    else:
        env.pop("BINFIN_MUTATION", None)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES,
         "-p", "scripts.mutation_plugin", "-p", "no:cacheprovider",
         "-o", "addopts=", "-q", "--tb=no", "-rf"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    failed = sorted({m.split("::")[-1] for m in FAILED_RE.findall(out)})
    passed = len(re.findall(r"(\d+) passed", out) and [0] or [])
    m = re.search(r"(\d+) passed", out)
    n_pass = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", out)
    n_fail = int(m.group(1)) if m else 0
    return n_pass, n_fail, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "mutation_matrix.json")
    args = ap.parse_args()

    print("baseline (no mutation) ...", flush=True)
    base_pass, base_fail, base_failed = run_suite(None)
    print(f"  {base_pass} passed, {base_fail} failed")
    if base_fail:
        print("  baseline is not green; mutation results would be meaningless")
        for t in base_failed:
            print(f"    {t}")
        return 1

    rows = []
    for name in MUTATIONS:
        n_pass, n_fail, failed = run_suite(name)
        killed = n_fail > 0
        rows.append({"mutation": name, "killed": killed, "n_failed": n_fail,
                     "n_passed": n_pass, "failing_tests": failed})
        status = "KILLED" if killed else "SURVIVED  <-- gap in the suite"
        print(f"{name:<32} {status:<28} ({n_fail} test(s) red)")
        for t in failed[:4]:
            print(f"    - {t}")
        if len(failed) > 4:
            print(f"    ... and {len(failed) - 4} more")

    killed = sum(1 for r in rows if r["killed"])
    score = killed / len(rows) if rows else 0.0
    print(f"\nmutation score: {killed}/{len(rows)} = {score:.0%}")

    payload = {"baseline_tests_passing": base_pass,
               "mutation_score": score,
               "killed": killed, "total": len(rows), "results": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0 if killed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
