"""Rebuild dated predictions, saved folds, inference and Overleaf result tables.

Run from the project root: python scripts/build_jfds_research.py
This is a retrospective replication, not an untouched prospective holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_walkforward import build_features, load_symbol, make_models, SYMBOLS
from scripts.research_statistics import (paired_hac, sharpe, stationary_indices,
    percentile, portfolio_series, cost_statistics, round_trip_return)

NAMES = {"logistic": "Logistic", "gbdt": "Boosted trees", "lag1": "Lag-1 logistic",
         "inverse": "Inverse persistence", "persistence": "Persistence", "majority": "Training majority"}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def evaluate(out):
    rows, folds, audits = [], [], []
    feature_names = None
    for symbol in SYMBOLS:
        print(f"Fitting chronological folds: {symbol}", flush=True)
        raw = load_symbol(symbol)
        if raw.date.duplicated().any() or not (raw.date.diff().dropna() == pd.Timedelta(days=1)).all():
            raise ValueError("Daily data have duplicates or gaps")
        prices = raw[["open", "high", "low", "close"]]
        if (prices <= 0).any().any() or prices.isna().any().any():
            raise ValueError("Invalid price data")
        if ((raw.high < raw[["open", "close"]].max(axis=1)) |
            (raw.low > raw[["open", "close"]].min(axis=1))).any():
            raise ValueError("OHLC ordering violation")
        f = build_features(raw)
        feature_names = list(f.columns)
        # Preserve the supplied manuscript's decision-date cutoff: final two
        # raw bars excluded. One is label-only; the other is an unused buffer.
        keep = f.notna().all(axis=1) & (raw.index < len(raw) - 2)
        ix = np.flatnonzero(keep)
        x = f.iloc[ix].reset_index(drop=True)
        y = (raw.close.shift(-1) > raw.close).astype(int).iloc[ix].to_numpy()
        dates = raw.date.iloc[ix].reset_index(drop=True)
        start_oos = 1005
        for fold, start in enumerate(range(start_oos, len(x) - 1, 250)):
            end, train_end = min(start + 250, len(x)), start - 5
            if end - start < 30:
                break
            fold_rec = dict(symbol=symbol, fold=fold, train_n=train_end,
                            train_start=str(dates.iloc[0].date()),
                            train_end=str(dates.iloc[train_end - 1].date()),
                            last_train_label=str((dates.iloc[train_end - 1] + pd.Timedelta(days=1)).date()),
                            test_start=str(dates.iloc[start].date()),
                            test_end=str(dates.iloc[end - 1].date()), test_n=end-start,
                            excluded_rows=5)
            folds.append(fold_rec)
            predictions, probabilities = {}, {}
            factories = make_models() | {"lag1": lambda: LogisticRegression(C=.1, max_iter=2000)}
            for name, factory in factories.items():
                columns = ["logret_1"] if name == "lag1" else feature_names
                model = Pipeline([("scale", StandardScaler()), ("classifier", factory())])
                model.fit(x.iloc[:train_end][columns], y[:train_end])
                p = model.predict_proba(x.iloc[start:end][columns])[:, 1]
                predictions[name] = (p > .5).astype(int)
                probabilities[name] = p
                joblib.dump(dict(pipeline=model, features=columns, fold=fold_rec),
                            out / "models" / f"{symbol}_{name}_fold{fold}.joblib")
            prev = (raw.close > raw.close.shift(1)).astype(int).iloc[ix[start:end]].to_numpy()
            predictions.update(inverse=1-prev, persistence=prev,
                               majority=np.full(end-start, int(y[:train_end].mean() > .5)))
            for offset, i in enumerate(range(start, end)):
                r = int(ix[i])
                row = dict(symbol=symbol, date=str(dates.iloc[i].date()),
                           return_date=str(raw.date.iloc[r+1].date()), fold=fold, y=int(y[i]),
                           forward_return=float(raw.close.iloc[r+1] / raw.close.iloc[r] - 1),
                           next_open=float(raw.open.iloc[r+1]), next_close=float(raw.close.iloc[r+1]))
                row.update({f"pred_{name}": int(v[offset]) for name, v in predictions.items()})
                row.update({f"prob_{name}": float(v[offset]) for name, v in probabilities.items()})
                rows.append(row)
        file = ROOT / "data_ingestion/output/klines" / f"{symbol}_daily.csv"
        audits.append(dict(symbol=symbol, raw_n=len(raw), usable_n=len(x),
                           raw_start=str(raw.date.iloc[0].date()), raw_end=str(raw.date.iloc[-1].date()),
                           oos_start=str(dates.iloc[start_oos].date()), oos_end=str(dates.iloc[-1].date()),
                           oos_n=len(x)-start_oos, folds=sum(f["symbol"] == symbol for f in folds),
                           missing_days=0, duplicate_dates=0, sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    return pd.DataFrame(rows), pd.DataFrame(folds), audits, feature_names


def execution(predictions, out):
    """Independent bar-open/bar-close cash ledger; all positions flat daily.

    Equal allocations based on equity before *any* same-day entry, avoiding
    same-day close prices in sizing later assets. Asset shortages stay in cash.
    """
    equity, trade_rows, curves = 10000., [], []
    max_error = 0.
    for date, group in predictions.groupby("return_date", sort=True):
        opening_equity, cash = equity, equity
        allocation = opening_equity / len(group)
        daily_pnl = 0.
        for row in group.itertuples():
            s = 1 if row.pred_logistic else -1
            entry = row.next_open * (1 + s * .0005)
            exit_ = row.next_close * (1 - s * .0005)
            qty = allocation / entry
            entry_fee, exit_fee = .0004 * qty * entry, .0004 * qty * exit_
            cash -= s * qty * entry + entry_fee
            cash += s * qty * exit_ - exit_fee
            pnl = s * qty * (exit_ - entry) - entry_fee - exit_fee
            daily_pnl += pnl
            expected = allocation * round_trip_return(s, row.next_open, row.next_close)
            if not np.isclose(pnl, expected, rtol=1e-10, atol=1e-8):
                raise AssertionError("Trade ledger disagrees with independent return formula")
            trade_rows.append(dict(date=date, symbol=row.symbol, side=s, quantity=qty,
                                   entry=entry, exit=exit_, entry_fee=entry_fee,
                                   exit_fee=exit_fee, pnl=pnl))
        equity = cash
        max_error = max(max_error, abs(equity - opening_equity - daily_pnl))
        curves.append(dict(date=date, equity=equity, net_return=equity/opening_equity-1,
                           open_positions=0))
    pd.DataFrame(trade_rows).to_csv(out / "execution_trades.csv", index=False)
    curve = pd.DataFrame(curves)
    curve.to_csv(out / "execution_equity.csv", index=False)
    return dict(trades=len(trade_rows), days=len(curve), initial_equity=10000., final_equity=equity,
                total_return=equity/10000-1, sharpe=sharpe(curve.net_return),
                max_cash_reconciliation_error=max_error,
                cumulative_reconciliation_error=abs(equity-10000-sum(t["pnl"] for t in trade_rows)),
                all_days_end_flat=True, risk_free_rate=0,
                policy="next-open to same-day-close; 100% gross daily round trips; no barriers, borrow, funding or latency")


def analyse(pred, reps):
    accuracy, paired = [], []
    for symbol, group in pred.groupby("symbol", sort=False):
        for name in NAMES:
            correct = (group[f"pred_{name}"] == group.y).to_numpy()
            accuracy.append(dict(symbol=symbol, model=name, n=len(group), accuracy=float(correct.mean())))
            if name in ("logistic", "gbdt", "lag1"):
                b = (group.pred_inverse == group.y).to_numpy()
                paired.append(dict(symbol=symbol, model=name, **paired_hac(correct, b)))
    pooled = {name: float((pred[f"pred_{name}"] == pred.y).mean()) for name in NAMES}
    returns = pred.pivot(index="date", columns="symbol", values="forward_return").reindex(columns=SYMBOLS)
    probs = pred.pivot(index="date", columns="symbol", values="prob_logistic").reindex(columns=SYMBOLS)
    panel_pred = {name: pred.pivot(index="date", columns="symbol", values=f"pred_{name}").reindex(columns=SYMBOLS)
                  for name in NAMES}
    diffs = pred.assign(diff=(pred.pred_logistic == pred.y).astype(int) - (pred.pred_inverse == pred.y).astype(int))
    d = diffs.pivot(index="date", columns="symbol", values="diff").reindex(columns=SYMBOLS).to_numpy()
    n_by_day, sum_by_day = np.isfinite(d).sum(axis=1), np.nansum(d, axis=1)
    gross, turn, passive = portfolio_series(panel_pred["logistic"], returns)
    boot_results = []
    for length in (5, 20, 60):
        ix = stationary_indices(len(returns), replicates=reps, mean_block=length)
        comparison = sum_by_day[ix].sum(axis=1) / n_by_day[ix].sum(axis=1)
        boot_results.append(dict(mean_block=length, pooled_diff=float(np.nansum(d)/np.isfinite(d).sum()),
                                 pooled_diff_ci95=percentile(comparison),
                                 **cost_statistics(gross, turn, ix)))
    curve = [dict(bps=c, sharpe=sharpe(gross-c/10000*turn), mean_daily=float((gross-c/10000*turn).mean()))
             for c in (0, 1, 3, 5, 7, 9, 12, 15, 20, 30)]
    variants = []
    economic_pairs = []
    comparison_ix = stationary_indices(len(returns), replicates=reps, mean_block=20)
    for name in NAMES:
        g, t, _ = portfolio_series(panel_pred[name], returns)
        variants.append(dict(variant=NAMES[name], days=len(g), gross_sharpe=sharpe(g),
                             net_sharpe=sharpe(g-.0009*t), turnover=float(t.mean())))
        if name != "logistic":
            diff_net = (gross-.0009*turn) - (g-.0009*t)
            diff_gross = gross-g
            economic_pairs.append(dict(baseline=name,
                annual_mean_net_diff=float(365*diff_net.mean()),
                annual_mean_net_diff_ci95=percentile(365*diff_net[comparison_ix].mean(axis=1)),
                annual_mean_gross_diff=float(365*diff_gross.mean()),
                annual_mean_gross_diff_ci95=percentile(365*diff_gross[comparison_ix].mean(axis=1))))
    mask = returns.notna().all(axis=1)
    g, t, _ = portfolio_series(panel_pred["logistic"][mask], returns[mask])
    variants.append(dict(variant="Logistic, common three-asset dates", days=len(g), gross_sharpe=sharpe(g),
                         net_sharpe=sharpe(g-.0009*t), turnover=float(t.mean())))
    g, t, _ = portfolio_series(panel_pred["logistic"], returns, long_only=True)
    variants.append(dict(variant="Logistic, long/cash", days=len(g), gross_sharpe=sharpe(g),
                         net_sharpe=sharpe(g-.0009*t), turnover=float(t.mean())))
    gates = []
    for gate in (0., .02, .04, .06, .08):
        g, t, _ = portfolio_series(panel_pred["logistic"], returns, gate=gate, proba=probs)
        gates.append(dict(gate=gate, net_sharpe=sharpe(g-.0009*t), turnover=float(t.mean())))
    fold_rows = []
    for (symbol, fold), f in pred.groupby(["symbol", "fold"], sort=False):
        fold_rows.append(dict(symbol=symbol, fold=int(fold), n=len(f), start=f.date.min(), end=f.date.max(),
                              logistic=float((f.pred_logistic == f.y).mean()),
                              inverse=float((f.pred_inverse == f.y).mean())))
    net = gross-.0009*turn
    beta = float(np.sum((net-net.mean())*(passive-passive.mean())) / np.sum((passive-passive.mean())**2))
    daily = pd.DataFrame(dict(date=returns.index, gross=gross, turnover=turn, net=net, passive_gross=passive))
    result = dict(accuracy=accuracy, paired_hac=paired, pooled_accuracy_descriptive=pooled,
                  bootstrap=boot_results, cost_curve=curve, variants=variants, gates=gates, folds=fold_rows,
                  economic_pairs=economic_pairs,
                  benchmark=dict(gross_sharpe=sharpe(passive), correlation=float(np.corrcoef(net, passive)[0,1]),
                                 beta=beta, daily_intercept=float(net.mean()-beta*passive.mean()),
                                 weighting="equal over assets with OOS forecasts on each date; gross rebalanced long-only basket"))
    return result, daily


def latex_outputs(result, folder):
    folder.mkdir(parents=True, exist_ok=True)
    def write(name, lines):
        (folder / name).write_text("% Generated by scripts/build_jfds_research.py; do not hand-edit.\n" + "\n".join(lines) + "\n", encoding="utf-8")
    stats = result["bootstrap"][1]
    economic = next(r for r in result["economic_pairs"] if r["baseline"] == "inverse")
    pooled = result["pooled_accuracy_descriptive"]
    numbers = dict(FeatureCount=len(result["features"]), PredictionCount=sum(a["oos_n"] for a in result["data"]),
                   PanelDays=stats["days"], LogisticAccuracy=f"{pooled['logistic']*100:.2f}",
                   InverseAccuracy=f"{pooled['inverse']*100:.2f}",
                   PooledDifference=f"{stats['pooled_diff']*100:.2f}",
                   PooledLow=f"{stats['pooled_diff_ci95'][0]*100:.2f}", PooledHigh=f"{stats['pooled_diff_ci95'][1]*100:.2f}",
                   GrossSharpe=f"{stats['gross_sharpe']:.3f}", NetSharpe=f"{stats['net_sharpe']:.3f}",
                   GrossLow=f"{stats['gross_ci95'][0]:.2f}", GrossHigh=f"{stats['gross_ci95'][1]:.2f}",
                   NetLow=f"{stats['net_ci95'][0]:.2f}", NetHigh=f"{stats['net_ci95'][1]:.2f}",
                   CostRoot=f"{stats['breakeven_bps']:.2f}", RootLow=f"{stats['breakeven_ci95'][0]:.2f}",
                   RootHigh=f"{stats['breakeven_ci95'][1]:.2f}", Turnover=f"{stats['turnover']:.3f}",
                   DailyCost=f"{stats['daily_cost_bps']:.2f}",
                   NetAnnualMean=f"{stats['annual_net_mean']*100:.2f}",
                   NetAnnualVol=f"{stats['annual_net_vol']*100:.2f}",
                   ExecutionSharpe=f"{result['execution']['sharpe']:.3f}",
                   ExecutionReturn=f"{result['execution']['total_return']*100:.2f}",
                   EconomicDifference=f"{100*economic['annual_mean_net_diff']:.2f}",
                   EconomicLow=f"{100*economic['annual_mean_net_diff_ci95'][0]:.2f}",
                   EconomicHigh=f"{100*economic['annual_mean_net_diff_ci95'][1]:.2f}")
    write("numbers.tex", [f"\\newcommand{{\\{k}}}{{{v}}}" for k,v in numbers.items()])
    write("sample.tex", [f"{a['symbol'].replace('USDT','')} & {a['raw_n']:,} & {a['usable_n']:,} & {a['oos_n']:,} & {a['folds']} & {a['oos_start']} \\\\" for a in result["data"]])
    acc = {(r["symbol"],r["model"]): r["accuracy"] for r in result["accuracy"]}
    write("accuracy.tex", [NAMES[n] + " & " + " & ".join(f"{100*acc[(s,n)]:.2f}" for s in SYMBOLS) + f" & {100*pooled[n]:.2f} \\\\" for n in NAMES])
    write("paired.tex", [f"{r['symbol'].replace('USDT','')} & {NAMES[r['model']]} & {100*r['diff']:+.2f} & [{100*r['ci95'][0]:.2f}, {100*r['ci95'][1]:.2f}] & {r['p_value']:.3f} \\\\" for r in result["paired_hac"]])
    write("cost.tex", [f"{r['bps']} & {r['mean_daily']*10000:+.2f} & {r['sharpe']:+.3f} \\\\" for r in result["cost_curve"]])
    write("bootstrap.tex", [f"{r['mean_block']} & [{r['pooled_diff_ci95'][0]*100:.2f}, {r['pooled_diff_ci95'][1]*100:.2f}] & [{r['gross_ci95'][0]:.2f}, {r['gross_ci95'][1]:.2f}] & [{r['net_ci95'][0]:.2f}, {r['net_ci95'][1]:.2f}] & [{r['breakeven_ci95'][0]:.2f}, {r['breakeven_ci95'][1]:.2f}] \\\\" for r in result["bootstrap"]])
    write("variants.tex", [f"{r['variant']} & {r['days']} & {r['gross_sharpe']:+.3f} & {r['net_sharpe']:+.3f} & {r['turnover']:.3f} \\\\" for r in result["variants"]])
    write("folds.tex", [f"{r['symbol'].replace('USDT','')} & {r['fold']+1} & {r['start']} & {r['end']} & {r['n']} & {100*r['logistic']:.1f} & {100*r['inverse']:.1f} \\\\" for r in result["folds"]])
    write("gates.tex", [f"{r['gate']:.2f} & {r['net_sharpe']:+.3f} & {r['turnover']:.3f} \\\\" for r in result["gates"]])
    return numbers


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "docs/jfds")
    p.add_argument("--tex", type=Path, default=ROOT / "paper/jfds_overleaf/generated")
    p.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "models").mkdir(exist_ok=True)
    predictions, folds, audits, features = evaluate(args.out)
    predictions.to_csv(args.out / "oos_predictions.csv", index=False)
    folds.to_csv(args.out / "fold_manifest.csv", index=False)
    print("Computing paired inference and synchronized block bootstraps", flush=True)
    result, daily = analyse(predictions, args.bootstrap_replicates)
    daily.to_csv(args.out / "daily_portfolio.csv", index=False)
    result.update(data=audits, features=features, execution=execution(predictions, args.out),
                  config=dict(initial_train=1000, embargo=5, fold_size=250, cutoff="2026-03-30",
                              bootstrap_replicates=args.bootstrap_replicates, bootstrap_seed=20260401,
                              model_seed=0, primary_hac_lags=20, periods_per_year=365,
                              terminal_liquidation_in_turnover=True, retrospective=True),
                  environment=dict(python=platform.python_version(), packages={n: importlib.metadata.version(n)
                                   for n in ("numpy", "pandas", "scipy", "scikit-learn", "joblib")}))
    write_json(args.out / "results.json", result)
    write_json(args.out / "latex_numbers.json", latex_outputs(result, args.tex))
    print(json.dumps(dict(pooled=result["pooled_accuracy_descriptive"],
                          primary=result["bootstrap"][1], execution=result["execution"]), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
