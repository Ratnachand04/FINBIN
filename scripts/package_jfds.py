"""Build explicitly allowlisted local delivery archives; no upload or publication."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def archive(path, members):
    records = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for file, name in sorted(members, key=lambda x:x[1]):
            if not file.is_file():
                raise FileNotFoundError(file)
            raw = file.read_bytes()
            # Retain test warnings/results but do not expose the local account
            # directory in a reviewer-facing archive's generated log files.
            if "/logs/" in name and file.suffix == ".txt":
                raw = raw.decode("utf-8").replace(str(Path.home()), "<USER_HOME>").encode("utf-8")
            z.writestr(name, raw)
            records.append(f"{hashlib.sha256(raw).hexdigest()}  {name}")
        z.writestr("MANIFEST.sha256", "\n".join(records)+"\n")
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError("Archive CRC verification failed")
        for line in z.read("MANIFEST.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            if hashlib.sha256(z.read(name)).hexdigest() != digest:
                raise RuntimeError(f"Archive manifest mismatch: {name}")
    return dict(file=path.name, files=len(members), bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), verified=True)


def main():
    out = ROOT / "deliverables"
    out.mkdir(exist_ok=True)
    tex = ROOT / "paper/jfds_overleaf"
    tex_members = [(p,p.relative_to(tex).as_posix()) for p in tex.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".tex", ".bib", ".pdf", ".png", ".md", ".txt")]
    overview = [archive(out / "JFDS_Overleaf.zip", tex_members)]
    paths = ["RESEARCH_README.md", "requirements-research.txt", "requirements-research-lock.txt", "research_pytest.ini",
             "scripts/__init__.py", "scripts/build_jfds_research.py", "scripts/research_statistics.py",
             "scripts/evaluate_walkforward.py", "scripts/render_jfds_figures.py",
             "scripts/mutation_matrix.py", "scripts/mutation_plugin.py", "scripts/reproduce_jfds.py",
             "scripts/verify_jfds_sources.py", "scripts/predict_jfds.py", "scripts/package_jfds.py",
             "tests/__init__.py", "tests/conftest.py", "tests/test_jfds_research.py",
             "tests/test_backtest_engine_correctness.py", "tests/test_feature_pipeline_correctness.py",
             "tests/test_evaluation_statistics.py", "backend/__init__.py", "backend/backtest/__init__.py",
             "backend/backtest/engine.py", "backend/backtest/metrics.py", "backend/ml/__init__.py",
             "backend/ml/feature_engineer.py", "backend/ml/model_trainer.py", "backend/ml/price_predictor.py",
             "backend/ml/sentiment_analyzer.py", "data_ingestion/scripts/fetch_sentiment_data.py"]
    paths += [f"data_ingestion/output/klines/{s}_daily.csv" for s in ("BTCUSDT","ETHUSDT","DOGEUSDT")]
    paths += [p.relative_to(ROOT).as_posix() for p in (ROOT / "docs/jfds").rglob("*")
              if p.is_file() and p.suffix in (".json", ".csv", ".md", ".txt", ".joblib")]
    paths += ["paper/jfds_overleaf/"+name for _,name in tex_members]
    overview.append(archive(out / "JFDS_Replication.zip", [(ROOT / p,p) for p in paths]))
    (out / "checksums.json").write_text(json.dumps(overview,indent=2)+"\n", encoding="utf-8")
    print(json.dumps(overview,indent=2))


if __name__ == "__main__":
    main()
