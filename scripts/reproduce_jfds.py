"""One-command local reproduction; no credentials or network required after installation."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    logdir = ROOT / "docs/jfds/logs"
    logdir.mkdir(parents=True, exist_ok=True)
    commands = [
        ("evaluation", ["scripts/build_jfds_research.py"]),
        ("regression", ["-m", "pytest", "-c", "research_pytest.ini", "tests/test_jfds_research.py",
          "tests/test_backtest_engine_correctness.py", "tests/test_feature_pipeline_correctness.py",
          "tests/test_evaluation_statistics.py", "-o", "addopts=", "-q"]),
        ("mutations", ["scripts/mutation_matrix.py", "--out", "docs/jfds/mutation_matrix.json"]),
        ("figures", ["scripts/render_jfds_figures.py"]),
    ]
    summary = []
    for name, args in commands:
        print(f"Running {name} ...", flush=True)
        proc = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
        (logdir / f"{name}.txt").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        summary.append(dict(step=name, returncode=proc.returncode, command=["python", *args]))
        if proc.returncode:
            print(proc.stdout, proc.stderr)
            raise SystemExit(proc.returncode)
        print(f"Completed {name}; log: docs/jfds/logs/{name}.txt", flush=True)
    (ROOT / "docs/jfds/verification.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print("Reproduction complete. Compile paper/jfds_overleaf/main.tex to refresh the PDF.")


if __name__ == "__main__":
    main()
