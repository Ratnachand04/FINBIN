"""Walk-forward evaluation of daily cryptocurrency direction forecasting.

Design constraints:

* Every feature is computed from data available at or before the close of day t.
* Labels look forward exactly one day; nothing else does.
* Folds are expanding-window and strictly chronological, with an embargo between
  train and test so no bar contributes to both.
* The scaler is fitted on the training fold only, inside each fold.
* Accuracy is reported with a Wilson interval and tested against three nulls:
  the coin flip, the majority class, and -- decisively -- inverse persistence.
* Sharpe ratios carry stationary-bootstrap confidence intervals. A Sharpe
  without one, over a sample this short, is not interpretable.
* P&L runs through the corrected backtest engine, and the gap between the
  frictionless and executed series is decomposed one change at a time.

The simulated sentiment CSVs in data_ingestion/output/sentiment are excluded:
sentiment_score there is generated as clip(same_day_return * 15) plus noise, so
it correlates 0.92 with the same day return and -0.02 with the next day. It is
the label wearing a disguise, not a feature.

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
INITIAL_TRAIN = 1000
TEST_SIZE = 250
EMBARGO = 5
FEE_RATE = 0.0004
SLIPPAGE = 0.0005
COST_PER_SIDE = FEE_RATE + SLIPPAGE
DAYS_PER_YEAR = 365
RNG = np.random.default_rng(20260401)


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
    """Causal features from OHLCV plus Binance real microstructure columns."""
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

    out["taker_buy_ratio"] = df["taker_buy_base_asset_volume"] / volume.replace(0, np.nan)
    out["taker_buy_ratio_ma5"] = out["taker_buy_ratio"].rolling(5).mean()
    out["avg_trade_size"] = quote_vol / n_trades.replace(0, np.nan)
    out["avg_trade_size_z"] = (
        out["avg_trade_size"] - out["avg_trade_size"].rolling(60).mean()
    ) / out["avg_trade_size"].rolling(60).std()
    out["ntrades_z"] = (n_trades - n_trades.rolling(60).mean()) / n_trades.rolling(60).std()
    out["volume_z"] = (volume - volume.rolling(60).mean()) / volume.rolling(60).std()
    out["volume_ratio_20"] = volume / volume.rolling(20).mean().replace(0, np.nan)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()

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
def wilson_interval(k: int, n: int, z: float = 1.959963985) -> list[float]:
    """Wilson score interval. Preferred to Wald near a boundary and at small n."""
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, centre - half), min(1.0, centre + half)]


def prop_test(k: int, n: int, p0: float) -> dict:
    """Two-sided test of an observed hit rate against a fixed null p0."""
    if n == 0 or p0 <= 0 or p0 >= 1:
        return {"z": 0.0, "p_value": 1.0}
    a = k / n
    se = math.sqrt(p0 * (1 - p0) / n)
    z = (a - p0) / se if se > 0 else 0.0
    return {"z": float(z), "p_value": float(math.erfc(abs(z) / math.sqrt(2)))}


def paired_accuracy_test(a: np.ndarray, b: np.ndarray) -> dict:
    """Paired test on two correctness vectors over the same observations.

    Because both forecasts are evaluated on identical days, their errors are
    correlated and an unpaired comparison of two independent intervals is the
    wrong instrument. Uses the mean of the per-observation difference.
    """
    d = a.astype(float) - b.astype(float)
    n = d.size
    if n < 2:
        return {"diff": 0.0, "se": 0.0, "t": 0.0, "p_value": 1.0}
    mean = float(d.mean())
    se = float(d.std(ddof=1) / math.sqrt(n))
    t = mean / se if se > 0 else 0.0
    return {
        "diff": mean,
        "se": se,
        "t": float(t),
        "p_value": float(math.erfc(abs(t) / math.sqrt(2))),
    }


def pesaran_timmermann(pred_up: np.ndarray, actual_up: np.ndarray) -> dict:
    """Pesaran-Timmermann (1992) test of directional predictive accuracy.

    Tests independence of predicted and realised direction, which is the
    hypothesis a directional forecast actually needs to reject. It corrects for
    the fact that a forecast can appear accurate purely by matching the
    unconditional frequency of up moves.
    """
    n = pred_up.size
    if n < 3:
        return {"pt_stat": 0.0, "p_value": 1.0}
    p = float((pred_up == actual_up).mean())
    py = float(actual_up.mean())
    px = float(pred_up.mean())
    pstar = py * px + (1 - py) * (1 - px)
    var_p = pstar * (1 - pstar) / n
    var_pstar = (
        ((2 * py - 1) ** 2) * px * (1 - px) / n
        + ((2 * px - 1) ** 2) * py * (1 - py) / n
        + 4 * py * px * (1 - py) * (1 - px) / (n * n)
    )
    denom = var_p - var_pstar
    if denom <= 0:
        return {"pt_stat": 0.0, "p_value": 1.0}
    stat = (p - pstar) / math.sqrt(denom)
    return {"pt_stat": float(stat), "p_value": float(math.erfc(abs(stat) / math.sqrt(2)))}


def sharpe(returns: np.ndarray, periods: int = DAYS_PER_YEAR) -> float:
    if returns.size < 2:
        return 0.0
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd * math.sqrt(periods)) if sd > 0 else 0.0


def stationary_bootstrap_sharpe(returns: np.ndarray, n_boot: int = 2000,
                                mean_block: float = 20.0) -> dict:
    """Politis-Romano stationary bootstrap CI for an annualised Sharpe ratio.

    Cited in the paper and, in the previous revision, never used. Serial
    dependence makes the iid interval too narrow.
    """
    n = returns.size
    if n < 30:
        return {"sharpe": sharpe(returns), "ci95": [0.0, 0.0], "se": 0.0}
    p = 1.0 / mean_block
    stats = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=int)
        i = RNG.integers(0, n)
        for t in range(n):
            idx[t] = i
            if RNG.random() < p:
                i = RNG.integers(0, n)
            else:
                i = (i + 1) % n
        stats[b] = sharpe(returns[idx])
    return {
        "sharpe": sharpe(returns),
        "ci95": [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))],
        "se": float(stats.std(ddof=1)),
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


# --------------------------------------------------------------------------
# Per-symbol walk-forward
# --------------------------------------------------------------------------
def evaluate_symbol(symbol: str) -> dict:
    df = load_symbol(symbol)
    feats = build_features(df)

    target = (df["close"].shift(-1) > df["close"]).astype(float)
    fwd_ret = df["close"].pct_change().shift(-1)
    # Execution windows, used for the attribution ladder.
    exec_1d = (df["close"] / df["open"] - 1).shift(-1)              # O_{t+1} -> C_{t+1}
    exec_2d = (df["close"].shift(-2) / df["open"].shift(-1) - 1)     # O_{t+1} -> C_{t+2}

    valid = feats.notna().all(axis=1) & target.notna() & fwd_ret.notna() & exec_2d.notna()
    idx = np.flatnonzero(valid.to_numpy())
    feats = feats.iloc[idx].reset_index(drop=True)
    dates = df["date"].iloc[idx].reset_index(drop=True)
    y = target.iloc[idx].to_numpy().astype(int)
    r_fwd = fwd_ret.iloc[idx].to_numpy()
    r_e1 = exec_1d.iloc[idx].to_numpy()
    r_e2 = exec_2d.iloc[idx].to_numpy()

    X_all = feats.to_numpy(dtype=float)
    n = len(X_all)

    store = {m: {"pred": [], "proba": [], "idx": [], "fold": []} for m in make_models()}
    base = {"persistence": [], "inverse_persistence": [], "majority": []}
    base_idx: list[int] = []
    folds: list[dict] = []

    for fold_id, start in enumerate(range(INITIAL_TRAIN + EMBARGO, n - 1, TEST_SIZE)):
        train_end = start - EMBARGO
        test_end = min(start + TEST_SIZE, n)
        if test_end - start < 30:
            break

        X_tr_raw, y_tr = X_all[:train_end], y[:train_end]
        X_te_raw, y_te = X_all[start:test_end], y[start:test_end]
        scaler = StandardScaler().fit(X_tr_raw)
        X_tr, X_te = scaler.transform(X_tr_raw), scaler.transform(X_te_raw)

        fold_row = {"fold": fold_id,
                    "test_start": str(dates.iloc[start].date()),
                    "test_end": str(dates.iloc[test_end - 1].date()),
                    "n": int(test_end - start)}

        for name, factory in make_models().items():
            model = factory()
            model.fit(X_tr, y_tr)
            pred = model.predict(X_te)
            proba = model.predict_proba(X_te)[:, 1] if hasattr(model, "predict_proba") else pred.astype(float)
            store[name]["pred"].extend(pred.tolist())
            store[name]["proba"].extend(proba.tolist())
            store[name]["idx"].extend(range(start, test_end))
            store[name]["fold"].extend([fold_id] * (test_end - start))
            fold_row[f"{name}_acc"] = float((pred == y_te).mean())

        prev = y[start - 1:test_end - 1]
        base["persistence"].extend((prev == y_te).tolist())
        base["inverse_persistence"].extend(((1 - prev) == y_te).tolist())
        maj = int(round(y_tr.mean()))
        base["majority"].extend((np.full_like(y_te, maj) == y_te).tolist())
        base_idx.extend(range(start, test_end))
        fold_row["inverse_persistence_acc"] = float(((1 - prev) == y_te).mean())
        folds.append(fold_row)

    out = {"symbol": symbol, "rows": n, "folds": len(folds),
           "date_start": str(dates.iloc[0].date()), "date_end": str(dates.iloc[-1].date()),
           "fold_detail": folds}

    tix = np.array(store["logistic"]["idx"])
    for name in store:
        pred = np.array(store[name]["pred"])
        i = np.array(store[name]["idx"])
        correct = (pred == y[i])
        k, m = int(correct.sum()), int(correct.size)
        acc = k / m
        signed = np.where(pred == 1, 1.0, -1.0)
        gross = signed * r_fwd[i]
        rec = {
            "n": m, "accuracy": acc,
            "ci95_wilson": wilson_interval(k, m),
            "vs_coinflip": prop_test(k, m, 0.5),
            "pt_test": pesaran_timmermann(pred, y[i]),
            "gross_mean_daily_ret": float(gross.mean()),
            "gross_sharpe_annual": sharpe(gross),
            # Return-weighted: does the model get the *big* days right?
            "return_weighted_hit_rate": float(
                (np.abs(r_fwd[i]) * correct).sum() / np.abs(r_fwd[i]).sum()
            ),
            "information_coefficient": float(
                np.corrcoef(signed, r_fwd[i])[0, 1]
            ) if signed.std() > 0 else 0.0,
        }
        out[name] = rec
        out[f"_ret_{name}"] = gross

    for bname, vals in base.items():
        arr = np.array(vals, dtype=bool)
        k, m = int(arr.sum()), int(arr.size)
        out[f"baseline_{bname}"] = {
            "n": m, "accuracy": k / m,
            "ci95_wilson": wilson_interval(k, m),
            "vs_coinflip": prop_test(k, m, 0.5),
        }
        out[f"_correct_{bname}"] = arr

    # Paired comparisons: model against each baseline on identical days.
    for name in store:
        pred = np.array(store[name]["pred"])
        i = np.array(store[name]["idx"])
        mc = (pred == y[i])
        out[name]["vs_inverse_persistence"] = paired_accuracy_test(
            mc, out["_correct_inverse_persistence"]
        )
        out[name]["vs_majority"] = paired_accuracy_test(mc, out["_correct_majority"])
        out[name]["vs_majority_null"] = prop_test(
            int(mc.sum()), int(mc.size), out["baseline_majority"]["accuracy"]
        )

    bh = r_fwd[tix]
    out["baseline_buy_and_hold"] = {
        "n": int(bh.size),
        "mean_daily_ret": float(bh.mean()),
        "sharpe_annual": sharpe(bh),
        "total_return_pct": float((np.prod(1 + bh) - 1) * 100),
        "annual_vol": float(bh.std(ddof=1) * math.sqrt(DAYS_PER_YEAR)),
    }
    out["_bh"] = bh
    out["_signals"] = {
        name: {"dates": dates.iloc[np.array(store[name]["idx"])].tolist(),
               "pred": np.array(store[name]["pred"]).tolist(),
               "proba": np.array(store[name]["proba"]).tolist()}
        for name in store
    }
    out["_series"] = {
        "dates": dates.iloc[tix].tolist(),
        "pred": np.array(store["logistic"]["pred"]),
        "proba": np.array(store["logistic"]["proba"]),
        "r_fwd": r_fwd[tix], "r_e1": r_e1[tix], "r_e2": r_e2[tix],
        "inv_pred": 1 - y[tix - 1],
    }
    out["_df"] = df
    return out


# --------------------------------------------------------------------------
# Portfolio-level analysis on a date-aligned panel
# --------------------------------------------------------------------------
def build_panel(per_symbol: dict, model: str = "logistic") -> pd.DataFrame:
    """Date-aligned panel across symbols.

    The previous revision truncated every series to the shortest one, silently
    discarding roughly 690 days of BTC and ETH history. Aligning on dates and
    averaging over whatever is present on each day keeps all of it.
    """
    frames = []
    for sym, res in per_symbol.items():
        s = res["_series"]
        frames.append(pd.DataFrame({
            "date": pd.to_datetime(s["dates"]),
            f"pos_{sym}": np.where(s["pred"] == 1, 1.0, -1.0),
            f"inv_{sym}": np.where(s["inv_pred"] == 1, 1.0, -1.0),
            f"prob_{sym}": s["proba"],
            f"r_{sym}": s["r_fwd"],
        }).set_index("date"))
    return pd.concat(frames, axis=1).sort_index()


def book_returns(panel: pd.DataFrame, symbols: list[str], cost: float,
                 prefix: str = "pos", vol_scale: bool = False,
                 gate: float = 0.0) -> tuple[np.ndarray, float]:
    """Equal-weight (or inverse-vol) long/short book. Returns (net series, turnover)."""
    pos_cols = [f"{prefix}_{s}" for s in symbols]
    ret_cols = [f"r_{s}" for s in symbols]
    pos = panel[pos_cols].to_numpy(dtype=float)
    ret = panel[ret_cols].to_numpy(dtype=float)

    if gate > 0 and prefix == "pos":
        prob = panel[[f"prob_{s}" for s in symbols]].to_numpy(dtype=float)
        pos = np.where(np.abs(prob - 0.5) >= gate, pos, 0.0)

    present = ~np.isnan(ret)
    pos = np.where(present, np.nan_to_num(pos), 0.0)
    ret = np.nan_to_num(ret)

    if vol_scale:
        w = np.zeros_like(pos)
        for j in range(pos.shape[1]):
            r = pd.Series(ret[:, j]).replace(0, np.nan)
            v = r.rolling(60, min_periods=20).std().shift(1).to_numpy()
            w[:, j] = np.where(np.isfinite(v) & (v > 0), 1.0 / v, 0.0)
        w *= present
        norm = w.sum(axis=1, keepdims=True)
        weight = np.divide(w, norm, out=np.zeros_like(w), where=norm > 0)
    else:
        cnt = present.sum(axis=1, keepdims=True)
        weight = np.divide(present.astype(float), cnt, out=np.zeros_like(pos), where=cnt > 0)

    signed = pos * weight
    gross = (signed * ret).sum(axis=1)
    turn = np.abs(np.diff(signed, axis=0, prepend=0.0)).sum(axis=1)
    return gross - turn * cost, float(turn.mean())


def cost_sensitivity(panel: pd.DataFrame, symbols: list[str], **kw) -> dict:
    curve = []
    for bps in (0, 1, 2, 3, 5, 7, 9, 12, 15, 20, 30):
        net, turn = book_returns(panel, symbols, bps / 1e4, **kw)
        curve.append({"cost_bps_per_side": bps, "sharpe_annual": sharpe(net),
                      "mean_daily_ret": float(net.mean()), "turnover": turn})
    breakeven = None
    for a, b in zip(curve, curve[1:]):
        if a["sharpe_annual"] > 0 >= b["sharpe_annual"]:
            span = a["sharpe_annual"] - b["sharpe_annual"]
            f = a["sharpe_annual"] / span if span else 0.0
            breakeven = a["cost_bps_per_side"] + f * (b["cost_bps_per_side"] - a["cost_bps_per_side"])
            break

    gross, turn = book_returns(panel, symbols, 0.0, **kw)
    boot = stationary_bootstrap_sharpe(gross)
    slope = (curve[0]["sharpe_annual"] - [c for c in curve if c["cost_bps_per_side"] == 9][0]["sharpe_annual"]) / 9.0
    be_ci = None
    if slope > 0:
        be_ci = [max(0.0, boot["ci95"][0] / slope), boot["ci95"][1] / slope]

    return {"days": int(len(panel)), "avg_daily_turnover": turn, "curve": curve,
            "breakeven_cost_bps_per_side": breakeven,
            "breakeven_ci95": be_ci,
            "gross_sharpe_bootstrap": boot,
            "daily_cost_bps_at_9": turn * 9.0}


def attribution_ladder(per_symbol: dict, panel: pd.DataFrame, symbols: list[str]) -> list[dict]:
    """Decompose the frictionless-to-executed gap one change at a time."""
    rows = []

    def add(label, series, note):
        rows.append({"step": label, "sharpe_annual": sharpe(np.asarray(series)),
                     "mean_daily_ret": float(np.mean(series)), "note": note})

    net0, _ = book_returns(panel, symbols, 0.0)
    add("1. label window C_t->C_t+1, no cost", net0, "the forecast being tested")
    net9, _ = book_returns(panel, symbols, COST_PER_SIDE)
    add("2. + transaction costs (9 bps/side)", net9, "cost of turnover only")

    # Execution windows, equal-weighted across whatever symbols are present.
    for key, label, note in (
        ("r_e1", "3. + 1-bar lag, 1-day hold (O_t+1->C_t+1)", "isolates the entry lag"),
        ("r_e2", "4. + 2-day hold (O_t+1->C_t+2)", "the previous engine holding period"),
    ):
        cols = []
        for sym, res in per_symbol.items():
            s = res["_series"]
            pos = np.where(s["pred"] == 1, 1.0, -1.0)
            cols.append(pd.Series(pos * s[key], index=pd.to_datetime(s["dates"])))
        merged = pd.concat(cols, axis=1).sort_index()
        gross = merged.mean(axis=1, skipna=True).fillna(0.0).to_numpy()
        _, turn = book_returns(panel, symbols, 0.0)
        add(label, gross - turn * COST_PER_SIDE, note)
    return rows


def run_engine(per_symbol: dict, model: str, hold_bars: int | None,
               timeout_hours: int = 24) -> dict:
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
        closes = dict(zip(df["date"], df["close"]))
        for date, pred in zip(sig["dates"], sig["pred"]):
            price = float(closes[date])
            side = "BUY" if pred == 1 else "SELL"
            signals.append({"id": len(signals),
                            "ts": date.to_pydatetime().replace(tzinfo=UTC),
                            "symbol": coin, "signal": side, "strength": 10.0,
                            "take_profit": price * (1.05 if side == "BUY" else 0.95),
                            "stop_loss": price * (0.95 if side == "BUY" else 1.05)})

    cfg = {"transaction_cost": FEE_RATE, "slippage": SLIPPAGE,
           "timeout_hours": timeout_hours, "max_position_pct": 0.2,
           "max_concurrent_positions": 3}
    if hold_bars is not None:
        cfg["hold_bars"] = hold_bars

    engine = BacktestEngine()
    trades, curve = engine.simulate_trades(signals, prices, 10_000.0, cfg)
    metrics = engine.calculate_performance_metrics(trades, curve)
    gross_per_trade = (metrics["avg_return_per_trade"] + 2 * COST_PER_SIDE) if trades else 0.0
    return {"metrics": metrics, "n_trades": len(trades),
            "hold_bars": hold_bars,
            "implied_gross_return_per_trade": gross_per_trade,
            "start": str(curve[0]["ts"].date()) if curve else None,
            "end": str(curve[-1]["ts"].date()) if curve else None}


def pnl_concentration(returns: np.ndarray) -> dict:
    """How much of the edge comes from a handful of days."""
    r = np.asarray(returns)
    total = r.sum()
    order = np.argsort(-np.abs(r))
    out = {}
    for k in (5, 10, 25, 50):
        if k <= r.size and total != 0:
            out[f"top{k}_share_of_total"] = float(r[order[:k]].sum() / total)
    out["days"] = int(r.size)
    return out


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evaluation_results.json")
    args = parser.parse_args()

    per_symbol = {}
    for symbol in SYMBOLS:
        print(f"evaluating {symbol} ...", flush=True)
        per_symbol[symbol] = evaluate_symbol(symbol)

    W = 96
    print("\n" + "=" * W)
    print("DIRECTIONAL ACCURACY, WALK-FORWARD OUT-OF-SAMPLE")
    print("=" * W)
    print(f"{'symbol':<9}{'model':<21}{'n':>6}{'acc':>8}{'95% CI (Wilson)':>19}"
          f"{'p vs .5':>9}{'PT p':>8}{'grossSR':>9}")
    print("-" * W)
    pooled_correct = {m: [0, 0] for m in ("logistic", "gbdt", "inverse_persistence", "majority", "persistence")}
    for symbol, res in per_symbol.items():
        for name in ("logistic", "gbdt"):
            s = res[name]
            ci = f"[{s['ci95_wilson'][0]:.3f},{s['ci95_wilson'][1]:.3f}]"
            print(f"{symbol:<9}{name:<21}{s['n']:>6}{s['accuracy']:>8.4f}{ci:>19}"
                  f"{s['vs_coinflip']['p_value']:>9.3f}{s['pt_test']['p_value']:>8.3f}"
                  f"{s['gross_sharpe_annual']:>9.2f}")
            pooled_correct[name][0] += round(s["accuracy"] * s["n"]); pooled_correct[name][1] += s["n"]
        for b in ("inverse_persistence", "persistence", "majority"):
            s = res[f"baseline_{b}"]
            tag = "~" + b
            print(f"{symbol:<9}{tag:<21}{s['n']:>6}{s['accuracy']:>8.4f}"
                  f"{'':>19}{s['vs_coinflip']['p_value']:>9.3f}{'':>8}{'':>9}")
            pooled_correct[b][0] += round(s["accuracy"] * s["n"]); pooled_correct[b][1] += s["n"]
        bh = res["baseline_buy_and_hold"]
        print(f"{symbol:<9}{'~buy&hold':<21}{bh['n']:>6}{'':>8}{'':>19}{'':>9}{'':>8}"
              f"{bh['sharpe_annual']:>9.2f}")
        print("-" * W)

    print("\nPOOLED")
    for name in ("logistic", "gbdt", "inverse_persistence", "persistence", "majority"):
        k, n = int(pooled_correct[name][0]), pooled_correct[name][1]
        ci = wilson_interval(k, n)
        t = prop_test(k, n, 0.5)
        print(f"  {name:<20} n={n:<6} acc={k/n:.4f} CI=[{ci[0]:.4f},{ci[1]:.4f}] "
              f"z={t['z']:+.2f} p={t['p_value']:.4f}")

    print("\nPAIRED: model minus inverse persistence, same days")
    for symbol, res in per_symbol.items():
        for name in ("logistic", "gbdt"):
            v = res[name]["vs_inverse_persistence"]
            verdict = "model better" if v["diff"] > 0 else "BASELINE BETTER"
            print(f"  {symbol:<9} {name:<9} diff={v['diff']*100:+6.2f}pp  "
                  f"se={v['se']*100:4.2f}pp  t={v['t']:+5.2f}  p={v['p_value']:.3f}   {verdict}")

    symbols = list(per_symbol)
    panel = build_panel(per_symbol)
    print(f"\nPANEL: {len(panel)} dates, {panel.index.min().date()} -> {panel.index.max().date()}")

    print("\nBOOK STATISTICS (equal-weight daily long/short, logistic)")
    for label, kw in (("equal weight", {}), ("inverse-vol weight", {"vol_scale": True})):
        net, turn = book_returns(panel, symbols, COST_PER_SIDE, **kw)
        gross, _ = book_returns(panel, symbols, 0.0, **kw)
        print(f"  {label:<20} mu={net.mean()*DAYS_PER_YEAR*100:+6.2f}%/yr  "
              f"sigma={net.std(ddof=1)*math.sqrt(DAYS_PER_YEAR)*100:5.2f}%/yr  "
              f"SR_net={sharpe(net):+.3f}  SR_gross={sharpe(gross):+.3f}  turnover={turn:.3f}")

    cs = cost_sensitivity(panel, symbols)
    print("\nCOST SENSITIVITY")
    print(f"  turnover={cs['avg_daily_turnover']:.4f}  -> {cs['daily_cost_bps_at_9']:.2f} bps/day at 9 bps/side")
    for p in cs["curve"]:
        mark = "   <- assumed real cost" if p["cost_bps_per_side"] == 9 else ""
        print(f"    {p['cost_bps_per_side']:>3} bps/side   SR = {p['sharpe_annual']:+.3f}{mark}")
    b = cs["gross_sharpe_bootstrap"]
    print(f"  gross SR = {b['sharpe']:.3f}, stationary-bootstrap 95% CI "
          f"[{b['ci95'][0]:+.3f}, {b['ci95'][1]:+.3f}]")
    if cs["breakeven_cost_bps_per_side"] is not None:
        lo, hi = cs["breakeven_ci95"] or (0, 0)
        print(f"  breakeven = {cs['breakeven_cost_bps_per_side']:.1f} bps/side, "
              f"95% CI [{lo:.1f}, {hi:.1f}]  <- interval spans the assumed cost")

    print("\nCONFIDENCE GATING (sensitivity, not a selected result)")
    for g in (0.0, 0.02, 0.04, 0.06, 0.08):
        net, turn = book_returns(panel, symbols, COST_PER_SIDE, gate=g)
        print(f"  |p-0.5| >= {g:.2f}   SR_net={sharpe(net):+.3f}  turnover={turn:.3f}")

    print("\nATTRIBUTION LADDER")
    ladder = attribution_ladder(per_symbol, panel, symbols)
    for row in ladder:
        print(f"  {row['step']:<44} SR = {row['sharpe_annual']:+.3f}   ({row['note']})")

    print("\nENGINE BACKTEST")
    engines = {}
    for label, hb, to in (("hold_bars=1 (matches label)", 1, 24),
                          ("hold_bars=2", 2, 24),
                          ("wall-clock timeout (previous)", None, 24)):
        bt = run_engine(per_symbol, "logistic", hb, to)
        engines[label] = bt
        m = bt["metrics"]
        print(f"  {label:<30} trades={bt['n_trades']:>5}  win={m['win_rate_pct']:5.2f}%  "
              f"SR={m['sharpe_ratio']:+.3f}  net/trade={m['avg_return_per_trade']*100:+.4f}%  "
              f"gross/trade={bt['implied_gross_return_per_trade']*100:+.4f}%")

    print("\nVS BUY-AND-HOLD (correlation and appraisal, not raw Sharpe)")
    net, _ = book_returns(panel, symbols, COST_PER_SIDE)
    bh_cols = [f"r_{s}" for s in symbols]
    bh = np.nan_to_num(panel[bh_cols].to_numpy(dtype=float)).mean(axis=1)
    corr = float(np.corrcoef(net, bh)[0, 1])
    beta = float(np.cov(net, bh)[0, 1] / np.var(bh)) if np.var(bh) > 0 else 0.0
    alpha = net.mean() - beta * bh.mean()
    resid = net - beta * bh
    appraisal = float(alpha / resid.std(ddof=1) * math.sqrt(DAYS_PER_YEAR)) if resid.std(ddof=1) > 0 else 0.0
    print(f"  corr(strategy, long-only basket) = {corr:+.3f}   beta = {beta:+.3f}")
    print(f"  appraisal ratio (annualised)     = {appraisal:+.3f}")
    print(f"  buy-and-hold basket SR           = {sharpe(bh):+.3f}")

    conc = pnl_concentration(net)
    print(f"\nP&L CONCENTRATION over {conc['days']} days: " +
          "  ".join(f"top{k}={conc[f'top{k}_share_of_total']:+.2f}"
                    for k in (5, 10, 25, 50) if f"top{k}_share_of_total" in conc))

    payload = {
        "config": {"initial_train_days": INITIAL_TRAIN, "test_days_per_fold": TEST_SIZE,
                   "embargo_days": EMBARGO, "fee_rate_per_side": FEE_RATE,
                   "slippage_per_side": SLIPPAGE, "instrument": "Binance spot, USDT quoted",
                   "days_per_year": DAYS_PER_YEAR},
        "per_symbol": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                       for k, v in per_symbol.items()},
        "pooled": {name: {"n": pooled_correct[name][1],
                          "accuracy": pooled_correct[name][0] / pooled_correct[name][1],
                          "ci95_wilson": wilson_interval(int(pooled_correct[name][0]), pooled_correct[name][1]),
                          "vs_coinflip": prop_test(int(pooled_correct[name][0]), pooled_correct[name][1], 0.5)}
                   for name in pooled_correct},
        "panel": {"days": int(len(panel)),
                  "start": str(panel.index.min().date()), "end": str(panel.index.max().date())},
        "cost_sensitivity": cs,
        "attribution": ladder,
        "engine": {k: {kk: vv for kk, vv in v.items()} for k, v in engines.items()},
        "vs_benchmark": {"correlation": corr, "beta": beta,
                         "appraisal_ratio": appraisal, "buy_and_hold_sharpe": sharpe(bh)},
        "pnl_concentration": conc,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
