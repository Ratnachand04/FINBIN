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
        [sys.executable, "-m", "pytest", "-c", "research_pytest.ini", *TEST_FILES,
         "-p", "scripts.mutation_plugin", "-p", "no:cacheprovider",
         "-o", "addopts=", "-q", "--tb=no", "-rf"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    failed = sorted({m.split("::")[-1] for m in FAILED_RE.findall(out)})
    m = re.search(r"(\d+) passed", out)
    n_pass = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", out)
    n_fail = int(m.group(1)) if m else 0
    if proc.returncode not in (0, 1) or n_pass + n_fail == 0 or re.search(r"\d+ (?:error|skipped)", out):
        raise RuntimeError(f"Invalid test run for {mutation}: exit={proc.returncode}\n{out}")
    if (proc.returncode == 0) != (n_fail == 0):
        raise RuntimeError(f"Inconsistent pytest exit status for {mutation}\n{out}")
    return n_pass, n_fail, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "mutation_matrix.json")
    args = ap.parse_args()

    print("baseline (no mutation) ...", flush=True)
    base_pass, base_fail, base_failed = run_suite(None)
    print(f"  {base_pass} passed, {base_fail} failed")
    if base_fail or base_pass < 29:
        print("  baseline is not green; mutation results would be meaningless")
        for t in base_failed:
            print(f"    {t}")
        return 1

    rows = []
    expected = {
        "D2_lookahead_price_lookup": "test_price_lookup_never_selects_a_future_bar",
        "D4_short_mark_to_market": "test_short_equity_falls_when_price_rises",
        "D5_close_only_exits": "test_stop_wins_when_one_bar_touches_both_levels",
        "D6_sharpe_annualisation": "test_annualisation_matches_the_closed_form",
        "D7_monte_carlo_permutation": "test_bootstrap_is_not_degenerate",
        "D10_scaler_refit": "test_single_row_transform_is_not_all_zeros",
        "D11_no_embargo": "test_gap_between_folds_is_at_least_the_sequence_length",
        "DX_fee_split_evenly": "test_equity_curve_reconciles_with_summed_trade_pnl",
    }
    for name in MUTATIONS:
        n_pass, n_fail, failed = run_suite(name)
        killed = n_pass + n_fail == base_pass and expected[name] in failed
        rows.append({"mutation": name, "killed": killed, "n_failed": n_fail,
                     "n_passed": n_pass, "failing_tests": failed,
                     "expected_test": expected[name], "exit_status_validated": True})
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
