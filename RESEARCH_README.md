# JFDS research replication

This is the measured daily technical-feature study underlying `paper/jfds_overleaf/main.tex`. It is **not** a release of a validated live cryptocurrency trading product.

## Reproduce

Use a dedicated Python 3.12 environment. On Windows, if the default `python` is broken, create the environment with `py -3.12 -m venv .venv-research` and invoke `.venv-research\Scripts\python.exe` explicitly.

```text
python -m pip install -r requirements-research.txt
python scripts/reproduce_jfds.py
```

The second command rebuilds all 72 fitted pipelines (24 folds times three classifiers), writes 5,562 dated prediction rows, computes the inference and cost tables, checks the daily trade ledger, runs 51 focused regression tests, exercises eight selected mutations, and regenerates the analytical figure. Read `docs/jfds/logs/` and `docs/jfds/verification.json`. The wider application/integration suite is not part of this service-free replication.

The extracted archive was also reproduced in a fresh Windows Python 3.12 environment. Eight primary CSV/JSON outputs were byte-identical to the workspace results; see `docs/jfds/validation_summary.json`. `requirements-research-lock.txt` records the complete installed-distribution snapshot from that check. The clean environment emitted one non-fatal Starlette/AnyIO deprecation warning. User-home prefixes in archived test logs are replaced with `<USER_HOME>`; test output and warnings otherwise remain intact.

Optional read-only public-source spot check (network required):

```text
python scripts/verify_jfds_sources.py
```

This checks 275 available asset-days against nine official monthly archives. It does not validate the entire history or overwrite inputs. The existing source check and input hashes are preserved for offline users.

Example offline inference using a trusted saved model:

```text
python scripts/predict_jfds.py --model docs/jfds/models/BTCUSDT_logistic_fold8.joblib --csv data_ingestion/output/klines/BTCUSDT_daily.csv --asof 2026-03-30
```

Never load joblib/pickle files from untrusted parties. The file is executable Python serialization. The inference command does not trade and does not retrain or demonstrate current predictive usefulness.

## Files and conventions

- `scripts/build_jfds_research.py`: fixed expanding folds, saved models, prediction export, comparative inference, portfolio and independent execution calculations.
- `scripts/research_statistics.py`: HAC estimator, synchronized stationary bootstrap, explicit target-turnover policy, exact cost root, one-bar trading formula.
- `scripts/evaluate_walkforward.py`: inherited feature/model definitions. Its old `main()` is a historical evaluation and is **not** the source of the revised paper's economic results.
- `docs/jfds/oos_predictions.csv`: prediction origins, return dates, labels, fold IDs, all model/baseline decisions, probabilities and execution prices.
- `docs/jfds/fold_manifest.csv`: training-feature and training-label end dates, OOS dates, counts and gap contract.
- `docs/jfds/daily_portfolio.csv`: daily gross/net diagnostics, target turnover including terminal liquidation, correctly weighted gross passive reference.
- `docs/jfds/execution_trades.csv`, `execution_equity.csv`: distinct daily-open-to-close, fully liquidated cash-ledger experiment.
- `docs/jfds/results.json`: machine-readable measurements, configuration, dependency versions and input hashes.
- `docs/jfds/mutation_matrix.json`: baseline count, expected failing test, and detected status for each selected mutant.
- `paper/jfds_overleaf/`: complete manuscript source, generated tables, references and figure.

Bootstrap seed: 20260401; 2,000 replicates; primary mean block length 20; sensitivity lengths 5 and 60. The primary HAC lag count is 20. No model selection was performed by maximizing a reported test score, but the work is retrospective, not preregistered. Stored predictions are not refitted inside bootstrap replicates.

Target-weight changes approximate turnover and ignore drift. The daily round-trip cash ledger is a different policy, with full daily exit/re-entry and own-notional fees. Both shorting and bar-boundary fills remain idealizations. No borrow/funding/latency/capacity or live trading evidence is claimed. The negative findings concern the tested setup, not all crypto prediction or a proven absence of information.

## Publication status and integrity

Author identities, funding, competing interests, CRediT roles, all-author approval and human review remain to be confirmed. No journal submission or public archive deposit has occurred, and no DOI has been fabricated. Confirm redistribution permissions before sharing. The local model files can contain paths in binary serialization metadata; regenerate them in a neutral directory or exclude them when preparing an anonymized review supplement.

The archives are produced with an explicit allowlist by `scripts/package_jfds.py`. They exclude `.git`, `.env`, tokens, unrelated application data, attachments and old drafts. `MANIFEST.sha256` inside each archive identifies its contents (excluding the manifest itself). Manifests provide file integrity, not provenance rights or evidence of correctness.

Read `docs/jfds/revision_audit.md` for the corrections made to the original manuscript. Public-source bibliography links are recorded in `docs/jfds/reference_verification.md`. The old paper and old results remain in the original workspace but are not the revised empirical source.
