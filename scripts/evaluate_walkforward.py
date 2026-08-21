"""Walk-forward evaluation of the directional model on real Binance daily data.

Design constraints, all of which the previous evaluation violated:

* Every feature is computed from data available at or before the close of day t.
* Labels look forward exactly one day; nothing else does.
* Folds are expanding-window and strictly chronological, with an embargo between
  train and test so no bar contributes to both.
* The scaler is fitted on the training fold only, inside each fold.
* Accuracy is reported with a confidence interval, a binomial p-value against
  the coin-flip null, and the majority-class rate. A hit rate on its own is not
  a result.
* P&L runs through the corrected backtest engine with fees and slippage.

The simulated sentiment CSVs in data_ingestion/output/sentiment are deliberately
excluded: sentiment_score there is generated as clip(same_day_return * 15) plus
noise, so it correlates 0.92 with the same day's return and -0.02 with the next
day's. It is the label wearing a disguise, not a feature.

Usage:
    python scripts/evaluate_walkforward.py [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import types
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KLINES = ROOT / "data_ingestion" / "output" / "klines"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
INITIAL_TRAIN = 1000  # days before the first test fold
TEST_SIZE = 250       # days per fold
EMBARGO = 5           # days dropped between train and test
FEE_RATE = 0.0004     # Binance spot taker, per side
SLIPPAGE = 0.0005     # per side


def _import_engine():
    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)

    async def _unused(*a, **k):
        raise AssertionError("no database access expected")

    stub.execute_raw_sql = _unused
    saved = sys.modules.get("backend.database")
    sys.modules["backend.database"] = stub
    try:
        from backend.backtest.engine import BacktestEngine

        return BacktestEngine
    finally:
        if saved is not None:
            sys.modules["backend.database"] = saved
        else:
            sys.modules.pop("backend.database", None)


BacktestEngine = _import_engine()


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Causal features from OHLCV plus Binance's real microstructure columns.

    Every column here is a function of bars at index <= t.
    """
    out = pd.DataFrame(index=df.index)
    close, high, low, open_ = df["close"], df["high"], df["low"], df["open"]
    volume, quote_vol, n_trades = df["volume"], df["quote_asset_volume"], df["number_of_trades"]

    logret = np.log(close / close.shift(1))
    out["logret_1"] = logret
    for k in (2, 3, 5, 10, 20):
        out[f"logret_{k}"] = np.log(close / close.shift(k))
    for k in (5, 10, 20, 60):
        out[f"vol_{k}"] = logret.rolling(k).std()
        out[f"zclose_{k}"] = (close - close.rolling(k).mean()) / close.rolling(k).std()

    out["hl_range"] = (high - low) / close
    out["co_spread"] = (close - open_) / open_
    out["close_loc"] = (close - low) / (high - low).replace(0, np.nan)

    # Real order-flow proxies from Binance's kline payload.
    out["taker_buy_ratio"] = df["taker_buy_base_asset_volume"] / volume.replace(0, np.nan)
    out["taker_buy_ratio_ma5"] = out["taker_buy_ratio"].rolling(5).mean()
    out["avg_trade_size"] = quote_vol / n_trades.replace(0, np.nan)
    out["avg_trade_size_z"] = (
        out["avg_trade_size"] - out["avg_trade_size"].rolling(60).mean()
    ) / out["avg_trade_size"].rolling(60).std()
    out["ntrades_z"] = (n_trades - n_trades.rolling(60).mean()) / n_trades.rolling(60).std()
    out["volume_z"] = (volume - volume.rolling(60).mean()) / volume.rolling(60).std()
    out["volume_ratio_20"] = volume / volume.rolling(20).mean().replace(0, np.nan)

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # MACD histogram
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()

    # Bollinger position and ATR
    ma20, sd20 = close.rolling(20).mean(), close.rolling(20).std()
    out["bb_pos"] = (close - ma20) / (2 * sd20).replace(0, np.nan)
    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
    ).max(axis=1)
    out["atr_norm"] = tr.rolling(14).mean() / close

    dow = df["date"].dt.dayofweek
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    return out.replace([np.inf, -np.inf], np.nan)


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def accuracy_stats(correct: np.ndarray) -> dict:
    """Hit rate with a Wald interval and a two-sided test against p=0.5."""
    n = int(correct.size)
    if n == 0:
        return {"n": 0, "accuracy": 0.0, "ci95": [0.0, 0.0], "p_value": 1.0, "z": 0.0}
    acc = float(correct.mean())
    se = math.sqrt(max(acc * (1 - acc), 1e-12) / n)
    z = (acc - 0.5) / math.sqrt(0.25 / n)
    return {
        "n": n,
        "accuracy": acc,
        "ci95": [max(0.0, acc - 1.959963985 * se), min(1.0, acc + 1.959963985 * se)],
        "p_value": float(math.erfc(abs(z) / math.sqrt(2))),
        "z": float(z),
    }


def load_symbol(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(KLINES / f"{symbol}_daily.csv", parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def make_models():
    return {
        "logistic": lambda: LogisticRegression(max_iter=2000, C=0.1),
        "gbdt": lambda: HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            l2_regularization=1.0, early_stopping=False, random_state=0,
        ),
    }


def evaluate_symbol(symbol: str) -> dict:
    df = load_symbol(symbol)
    feats = build_features(df)

    # Label: direction of the next daily close. Uses t+1 and nothing beyond.
    target = (df["close"].shift(-1) > df["close"]).astype(int)
    fwd_ret = df["close"].pct_change().shift(-1)

    valid = feats.notna().all(axis=1) & target.notna() & fwd_ret.notna()
    feats, target, fwd_ret = feats[valid], target[valid], fwd_ret[valid]
    dates = df.loc[valid, "date"].reset_index(drop=True)
    feats = feats.reset_index(drop=True)
    target = target.reset_index(drop=True).to_numpy()
    fwd_ret = fwd_ret.reset_index(drop=True).to_numpy()

    X_all = feats.to_numpy(dtype=float)
    n = len(X_all)
    results = {name: {"correct": [], "pred": [], "idx": []} for name in make_models()}
    baselines = {"persistence": [], "majority": []}

    fold_starts = list(range(INITIAL_TRAIN + EMBARGO, n - 1, TEST_SIZE))
    for start in fold_starts:
        train_end = start - EMBARGO
        test_end = min(start + TEST_SIZE, n)
        if test_end - start < 30:
            break

        X_tr_raw, y_tr = X_all[:train_end], target[:train_end]
        X_te_raw, y_te = X_all[start:test_end], target[start:test_end]

        # Scaler fitted inside the fold, on training rows only.
        scaler = StandardScaler().fit(X_tr_raw)
        X_tr, X_te = scaler.transform(X_tr_raw), scaler.transform(X_te_raw)

        for name, factory in make_models().items():
            model = factory()
            model.fit(X_tr, y_tr)
            pred = model.predict(X_te)
            results[name]["correct"].extend((pred == y_te).tolist())
            results[name]["pred"].extend(pred.tolist())
            results[name]["idx"].extend(range(start, test_end))

        # Persistence: tomorrow repeats today. Majority: the training fold's
        # more common class, held fixed across the test window.
        prev_dir = (target[start - 1 : test_end - 1] == 1).astype(int)
        baselines["persistence"].extend((prev_dir == y_te).tolist())
        maj = int(round(y_tr.mean()))
        baselines["majority"].extend((np.full_like(y_te, maj) == y_te).tolist())

    out = {"symbol": symbol, "rows": n, "folds": len(fold_starts),
           "date_start": str(dates.iloc[0].date()), "date_end": str(dates.iloc[-1].date())}

    for name in results:
        stats = accuracy_stats(np.array(results[name]["correct"], dtype=bool))
        idx = np.array(results[name]["idx"])
        pred = np.array(results[name]["pred"])
        # Gross long/short return from acting on each signal, before costs.
        signed = np.where(pred == 1, 1.0, -1.0) * fwd_ret[idx]
        stats["gross_mean_daily_ret"] = float(signed.mean())
        stats["gross_sharpe_annual"] = float(
            signed.mean() / signed.std(ddof=1) * math.sqrt(365)
        ) if signed.std(ddof=1) > 0 else 0.0
        out[name] = stats

    for name, correct in baselines.items():
        out[f"baseline_{name}"] = accuracy_stats(np.array(correct, dtype=bool))

    test_idx = np.array(results["gbdt"]["idx"])
    bh = fwd_ret[test_idx]
    out["baseline_buy_and_hold"] = {
        "n": int(bh.size),
        "mean_daily_ret": float(bh.mean()),
        "sharpe_annual": float(bh.mean() / bh.std(ddof=1) * math.sqrt(365)) if bh.std(ddof=1) > 0 else 0.0,
        "total_return_pct": float((np.prod(1 + bh) - 1) * 100),
    }
    out["_signals"] = {
        name: {
            "dates": dates.iloc[np.array(results[name]["idx"])].tolist(),
            "pred": np.array(results[name]["pred"]).tolist(),
            "fwd_ret": fwd_ret[np.array(results[name]["idx"])].tolist(),
        }
        for name in results
    }
    out["_df"] = df
    return out


def cost_sensitivity(per_symbol: dict, model: str = "logistic") -> dict:
    """Sharpe of an equal-weight daily long/short book as costs rise.

    Position is +/-1 per symbol. Changing position costs
    ``|pos_t - pos_{t-1}| * cost_per_side`` in notional terms, so a full flip
    pays twice. Reporting the whole curve, and the cost level at which Sharpe
    crosses zero, avoids selecting a fee assumption that flatters the result.
    """
    series = []
    for res in per_symbol.values():
        sig = res["_signals"][model]
        pos = np.where(np.array(sig["pred"]) == 1, 1.0, -1.0)
        ret = np.array(sig["fwd_ret"], dtype=float)
        turnover = np.abs(np.diff(pos, prepend=0.0))
        series.append((pos, ret, turnover))

    n = min(len(p) for p, _, _ in series)
    gross = np.mean([p[:n] * r[:n] for p, r, _ in series], axis=0)
    turn = np.mean([t[:n] for _, _, t in series], axis=0)

    curve = []
    for bps in (0, 1, 2, 5, 9, 10, 15, 20, 30):
        cost = bps / 10_000.0
        net = gross - turn * cost
        sd = net.std(ddof=1)
        curve.append({
            "cost_bps_per_side": bps,
            "sharpe_annual": float(net.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
            "mean_daily_ret": float(net.mean()),
        })

    # Linear interpolation of the zero crossing between adjacent grid points.
    breakeven = None
    for a, b in zip(curve, curve[1:]):
        if a["sharpe_annual"] > 0 >= b["sharpe_annual"]:
            span = a["sharpe_annual"] - b["sharpe_annual"]
            frac = a["sharpe_annual"] / span if span else 0.0
            breakeven = a["cost_bps_per_side"] + frac * (b["cost_bps_per_side"] - a["cost_bps_per_side"])
            break

    return {
        "model": model,
        "days": int(n),
        "avg_daily_turnover": float(turn.mean()),
        "curve": curve,
        "breakeven_cost_bps_per_side": breakeven,
    }


def backtest_through_engine(per_symbol: dict, model: str = "logistic") -> dict:
    """Run the chosen model's signals through the corrected backtest engine."""
    prices, signals = [], []
    for symbol, res in per_symbol.items():
        df = res["_df"]
        coin = symbol.replace("USDT", "")
        for row in df.itertuples(index=False):
            ts = row.date.to_pydatetime().replace(tzinfo=UTC)
            prices.append({"ts": ts, "symbol": coin, "open": float(row.open),
                           "high": float(row.high), "low": float(row.low),
                           "close": float(row.close)})
        sig = res["_signals"][model]
        by_date = {d: p for d, p in zip(sig["dates"], sig["pred"])}
        closes = dict(zip(df["date"], df["close"]))
        for date, pred in by_date.items():
            price = float(closes[date])
            side = "BUY" if pred == 1 else "SELL"
            signals.append({
                "id": len(signals),
                "ts": date.to_pydatetime().replace(tzinfo=UTC),
                "symbol": coin, "signal": side, "strength": 10.0,
                "take_profit": price * (1.05 if side == "BUY" else 0.95),
                "stop_loss": price * (0.95 if side == "BUY" else 1.05),
            })

    engine = BacktestEngine()
    trades, curve = engine.simulate_trades(
        signals, prices, 10_000.0,
        {"transaction_cost": FEE_RATE, "slippage": SLIPPAGE, "timeout_hours": 24,
         "max_position_pct": 0.2, "max_concurrent_positions": 3},
    )
    metrics = engine.calculate_performance_metrics(trades, curve)
    return {"metrics": metrics, "n_trades": len(trades),
            "start": str(curve[0]["ts"].date()) if curve else None,
            "end": str(curve[-1]["ts"].date()) if curve else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evaluation_results.json")
    args = parser.parse_args()

    per_symbol = {}
    for symbol in SYMBOLS:
        print(f"evaluating {symbol} ...", flush=True)
        per_symbol[symbol] = evaluate_symbol(symbol)

    print("\n" + "=" * 78)
    print("WALK-FORWARD DIRECTIONAL ACCURACY (next-day close direction)")
    print("=" * 78)
    hdr = f"{'symbol':<9}{'model':<13}{'n':>6}{'acc':>8}{'95% CI':>16}{'p':>9}{'gross SR':>10}"
    print(hdr)
    print("-" * 78)
    for symbol, res in per_symbol.items():
        for name in ("logistic", "gbdt"):
            s = res[name]
            ci = f"[{s['ci95'][0]:.3f},{s['ci95'][1]:.3f}]"
            print(f"{symbol:<9}{name:<13}{s['n']:>6}{s['accuracy']:>8.4f}{ci:>16}"
                  f"{s['p_value']:>9.3f}{s['gross_sharpe_annual']:>10.2f}")
        for base in ("persistence", "majority"):
            s = res[f"baseline_{base}"]
            print(f"{symbol:<9}{'~' + base:<13}{s['n']:>6}{s['accuracy']:>8.4f}"
                  f"{'':>16}{s['p_value']:>9.3f}{'':>10}")
        bh = res["baseline_buy_and_hold"]
        print(f"{symbol:<9}{'~buy&hold':<13}{bh['n']:>6}{'':>8}{'':>16}{'':>9}"
              f"{bh['sharpe_annual']:>10.2f}")
        print("-" * 78)

    # Pooled across symbols: the number that belongs on a resume.
    print("\nPOOLED ACROSS SYMBOLS")
    for name in ("logistic", "gbdt"):
        allc = np.concatenate([
            np.concatenate([np.ones(int(round(res[name]["accuracy"] * res[name]["n"]))),
                            np.zeros(res[name]["n"] - int(round(res[name]["accuracy"] * res[name]["n"])))])
            for res in per_symbol.values()
        ])
        s = accuracy_stats(allc.astype(bool))
        print(f"  {name:<10} n={s['n']:<6} acc={s['accuracy']:.4f} "
              f"CI=[{s['ci95'][0]:.4f},{s['ci95'][1]:.4f}] p={s['p_value']:.4f} z={s['z']:.2f}")

    print("\nCOST SENSITIVITY (equal-weight daily long/short, logistic signals)")
    cs = cost_sensitivity(per_symbol, "logistic")
    print(f"  days={cs['days']}  avg daily turnover={cs['avg_daily_turnover']:.3f}")
    print(f"  {'cost/side (bps)':<18}{'annual Sharpe':>15}")
    for point in cs["curve"]:
        marker = "   <- actual (4bp fee + 5bp slippage)" if point["cost_bps_per_side"] == 9 else ""
        print(f"  {point['cost_bps_per_side']:<18}{point['sharpe_annual']:>15.3f}{marker}")
    be = cs["breakeven_cost_bps_per_side"]
    print(f"  breakeven cost  : {be:.2f} bps per side" if be is not None
          else "  breakeven cost  : never positive on this grid")

    print("\nEND-TO-END BACKTEST (logistic signals through the corrected engine)")
    bt = backtest_through_engine(per_symbol, "logistic")
    m = bt["metrics"]
    print(f"  window          : {bt['start']} -> {bt['end']}")
    print(f"  trades          : {bt['n_trades']}")
    print(f"  win rate        : {m['win_rate_pct']:.2f}%")
    print(f"  total return    : {m['total_return_pct']:.2f}%")
    print(f"  max drawdown    : {m['max_drawdown_pct']:.2f}%")
    print(f"  SHARPE (net)    : {m['sharpe_ratio']:.3f}")
    print(f"  profit factor   : {m['profit_factor']:.3f}")

    payload = {
        "config": {"initial_train_days": INITIAL_TRAIN, "test_days_per_fold": TEST_SIZE,
                   "embargo_days": EMBARGO, "fee_rate_per_side": FEE_RATE,
                   "slippage_per_side": SLIPPAGE},
        "per_symbol": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                       for k, v in per_symbol.items()},
        "cost_sensitivity": cs,
        "backtest": bt,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
