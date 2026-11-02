"""Supplementary diagnostics cited in the JFDS manuscript.

Every quantity here is derived from files the main build already writes
(``scripts/build_jfds_research.py``) or from the frozen input CSVs. Nothing is
refitted and no model is selected. Run after the build:

    python scripts/build_jfds_research.py
    python scripts/supplementary_diagnostics.py

Outputs ``docs/jfds/supplementary_diagnostics.json`` containing:

* per-asset inference for each predictor (Wilson interval, two-sided test
  against 0.5, Pesaran--Timmermann test), recomputed from the dated
  prediction file rather than copied from an older results file;
* magnitude-aware measures (return-weighted hit rate, signed-forecast
  correlation with the next-day return, single-asset gross Sharpe);
* date-synchronized stationary-bootstrap intervals for pooled accuracy, and
  the share of asset-days with an 'up' logistic forecast;
* agreement between the logistic forecast and inverse persistence, and
  accuracy by within-asset terciles of the absolute next-day return;
* a per-trade decomposition of the daily-liquidation cash ledger;
* annualised moments of the target-notional diagnostic and the passive
  basket, with the regression intercept (OLS and 20-lag HAC t-statistics)
  and appraisal ratio of the net strategy on the passive basket;
* close-to-next-open versus open-to-close return dispersion;
* SHA-256 hashes of the input files with LF and CRLF line endings;
* dependence of the excluded simulated sentiment score on same-day and
  next-day returns (provenance check only);
* correlations among the three same-day features (collinearity check);
* mean standardised logistic coefficients across folds, with their rank by
  absolute size (needs the saved fold pipelines in ``docs/jfds/models``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_walkforward import (SYMBOLS, build_features,  # noqa: E402
                                          load_symbol, pesaran_timmermann,
                                          prop_test, wilson_interval)
from scripts.research_statistics import percentile, stationary_indices  # noqa: E402

KLINES = ROOT / "data_ingestion" / "output" / "klines"
PREDICTORS = ("logistic", "gbdt", "lag1", "inverse", "persistence", "majority")


def sharpe(x: np.ndarray, periods: int = 365) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.mean() / x.std(ddof=1) * np.sqrt(periods))


def per_asset_inference(pred: pd.DataFrame) -> list[dict]:
    rows = []
    for symbol, g in pred.groupby("symbol", sort=False):
        y, r = g.y.to_numpy(), g.forward_return.to_numpy()
        for name in PREDICTORS:
            p = g[f"pred_{name}"].to_numpy()
            correct = p == y
            k, n = int(correct.sum()), int(correct.size)
            signed = np.where(p == 1, 1.0, -1.0)
            rec = dict(symbol=symbol, predictor=name, n=n, accuracy=k / n,
                       wilson95=wilson_interval(k, n), vs_half=prop_test(k, n, 0.5))
            if name != "majority":
                rec.update(
                    pesaran_timmermann=pesaran_timmermann(p, y),
                    return_weighted_hit_rate=float((np.abs(r) * correct).sum() / np.abs(r).sum()),
                    signed_forecast_return_corr=float(np.corrcoef(signed, r)[0, 1]),
                    single_asset_gross_sharpe=sharpe(signed * r))
            rows.append(rec)
    return rows


def pooled_accuracy_bootstrap(pred: pd.DataFrame, replicates: int = 2000,
                              mean_block: int = 20) -> dict:
    """Date-synchronized stationary-bootstrap intervals for pooled accuracy.

    Uses the same index generator and seed as the main build, so dates (not
    asset-days) are resampled and cross-asset dependence is preserved.
    """
    dates = np.sort(pred.date.unique())
    ix = stationary_indices(len(dates), replicates=replicates, mean_block=mean_block)
    out = {}
    for name in PREDICTORS:
        correct = (pred[f"pred_{name}"] == pred.y).astype(float)
        by_day = correct.groupby(pred.date).agg(["sum", "count"]).reindex(dates)
        hits, n = by_day["sum"].to_numpy(), by_day["count"].to_numpy()
        boot = hits[ix].sum(axis=1) / n[ix].sum(axis=1)
        out[name] = dict(accuracy=float(hits.sum() / n.sum()), ci95=percentile(boot),
                         mean_block=mean_block, replicates=replicates)
    return out


def forecast_shares(pred: pd.DataFrame) -> dict:
    """Share of asset-days with an 'up' logistic forecast (long-or-cash exposure)."""
    up = pred.pred_logistic == 1
    return dict(pooled=float(up.mean()),
                by_asset={s: float(v) for s, v in up.groupby(pred.symbol).mean().items()})


def agreement_and_terciles(pred: pd.DataFrame) -> dict:
    agree = (pred.pred_logistic == pred.pred_inverse)
    tercile = pred.groupby("symbol").forward_return.transform(
        lambda x: pd.qcut(x.abs(), 3, labels=False))
    by_tercile = []
    for q, g in pred.groupby(tercile):
        by_tercile.append(dict(tercile=int(q), n=len(g),
                               logistic=float((g.pred_logistic == g.y).mean()),
                               inverse=float((g.pred_inverse == g.y).mean())))
    return dict(agreement_pooled=float(agree.mean()),
                agreement_by_asset={s: float(v) for s, v in agree.groupby(pred.symbol).mean().items()},
                accuracy_by_abs_return_tercile=by_tercile)


def ledger_decomposition(pred: pd.DataFrame, out: Path) -> dict:
    trades = pd.read_csv(out / "execution_trades.csv")
    equity = pd.read_csv(out / "execution_equity.csv")
    market = pred[["symbol", "return_date", "next_open", "next_close"]].rename(
        columns={"return_date": "date"})
    t = trades.merge(market, on=["symbol", "date"], validate="one_to_one")
    notional = t.quantity * t.entry
    raw = t.side * (t.next_close / t.next_open - 1)
    after_slip = t.side * (t.exit / t.entry - 1)
    fees = (t.entry_fee + t.exit_fee) / notional
    net = t.pnl / notional
    drawdown = 1 - (equity.equity / equity.equity.cummax()).min()
    return dict(trades=len(t), days=len(equity),
                mean_signed_open_to_close_bps=float(raw.mean() * 1e4),
                mean_after_slippage_bps=float(after_slip.mean() * 1e4),
                mean_fees_bps=float(fees.mean() * 1e4),
                mean_net_bps=float(net.mean() * 1e4),
                directional_hit_rate=float((raw > 0).mean()),
                net_win_rate=float((t.pnl > 0).mean()),
                final_equity=float(equity.equity.iloc[-1]),
                max_drawdown=float(drawdown))


def portfolio_moments(out: Path) -> dict:
    d = pd.read_csv(out / "daily_portfolio.csv")
    x, y = d.passive_gross.to_numpy(), d.net.to_numpy()
    beta = float(np.sum((y - y.mean()) * (x - x.mean())) / np.sum((x - x.mean()) ** 2))
    alpha = float(y.mean() - beta * x.mean())
    resid = y - alpha - beta * x
    resid_sd = float(resid.std(ddof=2))
    X = np.column_stack([np.ones_like(x), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    ols_se = float(np.sqrt(resid @ resid / (len(y) - 2) * xtx_inv[0, 0]))
    scores = X * resid[:, None]
    meat = scores.T @ scores
    for lag in range(1, 21):
        w = 1 - lag / 21
        g = scores[lag:].T @ scores[:-lag]
        meat += w * (g + g.T)
    hac_se = float(np.sqrt((xtx_inv @ meat @ xtx_inv)[0, 0]))
    return dict(gross_annual_mean=float(365 * d.gross.mean()),
                gross_annual_vol=float(np.sqrt(365) * d.gross.std(ddof=1)),
                net_annual_mean=float(365 * d.net.mean()),
                net_annual_vol=float(np.sqrt(365) * d.net.std(ddof=1)),
                passive_annual_mean=float(365 * d.passive_gross.mean()),
                passive_annual_vol=float(np.sqrt(365) * d.passive_gross.std(ddof=1)),
                passive_sharpe=sharpe(d.passive_gross),
                net_beta=beta, net_alpha_daily=alpha, net_alpha_annual=365 * alpha,
                net_correlation=float(np.corrcoef(x, y)[0, 1]),
                appraisal_ratio_annual=float(alpha / resid_sd * np.sqrt(365)),
                net_alpha_t_ols=alpha / ols_se, net_alpha_t_hac20=alpha / hac_se)


def market_facts(pred: pd.DataFrame) -> dict:
    first, last = pred.date.min(), pred.return_date.max()
    facts = {}
    for symbol in SYMBOLS:
        path = KLINES / f"{symbol}_daily.csv"
        lf = path.read_bytes().replace(b"\r\n", b"\n")
        k = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        c2o = k.open.shift(-1) / k.close - 1
        o2c = k.close / k.open - 1
        window = (k.date >= first) & (k.date <= last)
        facts[symbol] = dict(
            rows=len(k), first=str(k.date.iloc[0].date()), last=str(k.date.iloc[-1].date()),
            sha256_lf=hashlib.sha256(lf).hexdigest(),
            sha256_crlf=hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest(),
            close_to_next_open_sd_bps_full=float(c2o.std() * 1e4),
            open_to_close_sd_bps_full=float(o2c.std() * 1e4),
            close_to_next_open_sd_bps_eval=float(c2o[window].std() * 1e4),
            open_to_close_sd_bps_eval=float(o2c[window].std() * 1e4))
    counts = pred.groupby("date").symbol.nunique()
    facts["panel"] = dict(dates=int(counts.size),
                          dates_by_active_assets={str(k): int(v) for k, v in counts.value_counts().sort_index().items()})
    return facts


def sentiment_proxy() -> dict:
    """Dependence of the simulated sentiment score on same-day and next-day returns.

    The score is excluded from every model; this only documents its provenance.
    ``r2_same_day`` is the R^2 of a one-regressor OLS fit of the score on the
    same-day open-to-close return (equal to the squared correlation). The rank
    correlation is reported because the generator saturates at +/-1 on large
    moves, which depresses the linear fit for volatile assets such as DOGE.
    """
    out = {}
    for symbol in SYMBOLS:
        k = pd.read_csv(KLINES / f"{symbol}_daily.csv", parse_dates=["date"])
        s = pd.read_csv(ROOT / "data_ingestion/output/sentiment" / f"{symbol}_sentiment_metrics.csv",
                        parse_dates=["date"])
        m = k.merge(s, on="date", how="inner", validate="one_to_one").sort_values("date")
        same = (m.close - m.open) / m.open
        nxt = m.close.pct_change().shift(-1)
        v = nxt.notna()
        rho_same = float(np.corrcoef(m.sentiment_score, same)[0, 1])
        out[symbol] = dict(
            n_same_day=int(len(m)), n_next_day=int(v.sum()),
            corr_same_day=rho_same, r2_same_day=rho_same ** 2,
            spearman_same_day=float(m.sentiment_score.corr(same, method="spearman")),
            sign_agreement_same_day=float((np.sign(m.sentiment_score) == np.sign(same)).mean()),
            corr_next_day=float(np.corrcoef(m.sentiment_score[v], nxt[v])[0, 1]),
            sign_agreement_next_day=float((np.sign(m.sentiment_score[v]) == np.sign(nxt[v])).mean()))
    return out


def feature_correlations() -> dict:
    """Overlap among the three same-day features, on the usable rows of each asset.

    Usable rows follow the build script: complete features and not one of the
    last two raw bars.
    """
    out = {}
    for symbol in SYMBOLS:
        raw = load_symbol(symbol)
        f = build_features(raw)
        f = f[f.notna().all(axis=1) & (raw.index < len(raw) - 2)]
        out[symbol] = dict(
            rows=int(len(f)),
            logret_1__co_spread=float(f.logret_1.corr(f.co_spread)),
            logret_1__close_loc=float(f.logret_1.corr(f.close_loc)),
            close_loc__co_spread=float(f.close_loc.corr(f.co_spread)))
    return out


def coefficient_summary(models: Path) -> list[dict] | None:
    files = sorted(models.glob("*_logistic_fold*.joblib"))
    if not files:
        return None
    import joblib  # local import: only needed when saved models are present
    rows = []
    for f in files:
        symbol, fold = re.search(r"([A-Z]+USDT)_logistic_fold(\d+)", f.name).groups()
        saved = joblib.load(f)
        coef = saved["pipeline"].named_steps["classifier"].coef_[0]
        rows += [dict(symbol=symbol, fold=int(fold), feature=n, coef=float(c))
                 for n, c in zip(saved["features"], coef)]
    df = pd.DataFrame(rows)
    out = []
    for (symbol, feature), g in df.groupby(["symbol", "feature"], sort=False):
        mean = g.coef.mean()
        out.append(dict(symbol=symbol, feature=feature, folds=len(g), mean=float(mean),
                        sd=float(g.coef.std(ddof=1)),
                        same_sign_folds=int((np.sign(g.coef) == np.sign(mean)).sum())))
    ranked = pd.DataFrame(out)
    ranked["rank_abs_mean"] = ranked.groupby("symbol")["mean"].transform(
        lambda x: x.abs().rank(ascending=False, method="min")).astype(int)
    return ranked.to_dict(orient="records")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=ROOT / "docs/jfds")
    ap.add_argument("--models", type=Path, default=ROOT / "docs/jfds/models")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/jfds/supplementary_diagnostics.json")
    args = ap.parse_args()
    pred = pd.read_csv(args.results / "oos_predictions.csv")
    payload = dict(per_asset=per_asset_inference(pred),
                   pooled_accuracy_bootstrap=pooled_accuracy_bootstrap(pred),
                   forecast_shares=forecast_shares(pred),
                   agreement=agreement_and_terciles(pred),
                   execution_ledger=ledger_decomposition(pred, args.results),
                   portfolio=portfolio_moments(args.results),
                   market=market_facts(pred),
                   sentiment_proxy=sentiment_proxy(),
                   feature_correlations=feature_correlations(),
                   logistic_coefficients=coefficient_summary(args.models))
    if payload["logistic_coefficients"] is None:
        print(f"No saved fold pipelines in {args.models}; run scripts/build_jfds_research.py "
              "first. Coefficient summary omitted.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
