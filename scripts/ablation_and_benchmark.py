"""Ablation, decision-layer evaluation and system benchmarks.

Closes three gaps in the system evaluation:

1. **Ablation.** The architecture combines three model families with fixed
   weights (0.30/0.40/0.30). Nothing established that the combination beats its
   best member, or that the fixed weights beat fitted ones. Both are tested here
   under the same walk-forward protocol used elsewhere.

2. **Decision layer.** Measured separately by scripts/decision_layer_study.py
   using real calibrated predict_proba output. An earlier version of this script
   approximated model confidence with a constant, which silently turned the
   confidence gate into a no-op and inflated the firing rate; that code was
   removed rather than left to produce a misleading number.

3. **Latency and throughput.** A systems paper that reports no timings is
   incomplete. Feature construction, per-model inference, scaler transform and
   backtest throughput are measured here with percentiles.

Substitutions, stated because they matter: Prophet and XGBoost are optional
dependencies not installed in this environment. The statistical slot is filled
by regularised logistic regression and the boosted slot by scikit-learn
histogram gradient boosting, which is algorithmically close to XGBoost. The LSTM
is the real Keras model. Conclusions about *ensembling* transfer; conclusions
about Prophet specifically do not.

Usage:
    python scripts/ablation_and_benchmark.py [--out docs/ablation_results.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import numpy as np
import pandas as pd
import psutil
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_walkforward import (  # noqa: E402
    INITIAL_TRAIN, TEST_SIZE, EMBARGO, DAYS_PER_YEAR,
    build_features, load_symbol, wilson_interval, prop_test,
    paired_accuracy_test, sharpe,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
SEQ_LEN = 30
PAPER_WEIGHTS = np.array([0.30, 0.40, 0.30])   # statistical / neural / boosted


# --------------------------------------------------------------------------
def build_lstm(n_features: int):
    from tensorflow import keras

    model = keras.Sequential([
        keras.layers.Input(shape=(SEQ_LEN, n_features)),
        keras.layers.LSTM(24),
        keras.layers.Dropout(0.2),
        keras.layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="binary_crossentropy")
    return model


def sequences(X: np.ndarray, y: np.ndarray, idx_from: int, idx_to: int):
    """Windows ending at each index in [idx_from, idx_to), causal by construction."""
    xs, ys, pos = [], [], []
    for i in range(idx_from, idx_to):
        if i < SEQ_LEN:
            continue
        xs.append(X[i - SEQ_LEN:i])
        ys.append(y[i])
        pos.append(i)
    if not xs:
        return np.empty((0, SEQ_LEN, X.shape[1])), np.empty(0), np.empty(0, dtype=int)
    return np.asarray(xs), np.asarray(ys), np.asarray(pos)


def fit_weights(probs: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Grid search over the 3-simplex, step 0.05, maximising validation accuracy."""
    best, best_acc = PAPER_WEIGHTS.copy(), -1.0
    grid = np.arange(0.0, 1.0001, 0.05)
    for a in grid:
        for b in grid:
            c = 1.0 - a - b
            if c < -1e-9 or c > 1.0 + 1e-9:
                continue
            w = np.array([a, b, max(c, 0.0)])
            acc = float(((probs @ w >= 0.5).astype(int) == y).mean())
            if acc > best_acc:
                best_acc, best = acc, w
    return best


def evaluate_symbol(symbol: str) -> dict:
    df = load_symbol(symbol)
    feats = build_features(df)
    target = (df["close"].shift(-1) > df["close"]).astype(float)
    fwd = df["close"].pct_change().shift(-1)

    valid = feats.notna().all(axis=1) & target.notna() & fwd.notna()
    idx = np.flatnonzero(valid.to_numpy())
    feats = feats.iloc[idx].reset_index(drop=True)
    dates = df["date"].iloc[idx].reset_index(drop=True)
    raw = df.iloc[idx].reset_index(drop=True)
    y = target.iloc[idx].to_numpy().astype(int)
    r = fwd.iloc[idx].to_numpy()
    X = feats.to_numpy(dtype=float)
    n = len(X)

    arms = ["logistic", "gbdt", "lstm", "ens_equal", "ens_paper", "ens_fitted"]
    store = {a: {"pred": [], "idx": []} for a in arms}
    fitted_ws = []
    timings = {"fit_logistic": [], "fit_gbdt": [], "fit_lstm": []}

    for start in range(INITIAL_TRAIN + EMBARGO, n - 1, TEST_SIZE):
        train_end = start - EMBARGO
        test_end = min(start + TEST_SIZE, n)
        if test_end - start < 30:
            break

        inner = int(train_end * 0.8)
        scaler = StandardScaler().fit(X[:inner])
        Xs = scaler.transform(X)

        # --- flat-input models, trained on the inner training block ----------
        t0 = time.perf_counter()
        lg = LogisticRegression(max_iter=2000, C=0.1).fit(Xs[:inner], y[:inner])
        timings["fit_logistic"].append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        gb = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                            l2_regularization=1.0, early_stopping=False,
                                            random_state=0).fit(Xs[:inner], y[:inner])
        timings["fit_gbdt"].append(time.perf_counter() - t0)

        # --- sequence model --------------------------------------------------
        Xtr_s, ytr_s, _ = sequences(Xs, y, SEQ_LEN, inner)
        t0 = time.perf_counter()
        lstm = build_lstm(X.shape[1])
        lstm.fit(Xtr_s, ytr_s, epochs=10, batch_size=128, verbose=0, shuffle=False)
        timings["fit_lstm"].append(time.perf_counter() - t0)

        # --- validation slice for weight fitting -----------------------------
        Xva_s, yva_s, pos_va = sequences(Xs, y, inner, train_end)
        if len(pos_va) < 20:
            continue
        p_va = np.column_stack([
            lg.predict_proba(Xs[pos_va])[:, 1],
            lstm.predict(Xva_s, verbose=0).ravel(),
            gb.predict_proba(Xs[pos_va])[:, 1],
        ])
        w_fit = fit_weights(p_va, y[pos_va])
        fitted_ws.append(w_fit.tolist())

        # --- test ------------------------------------------------------------
        Xte_s, yte_s, pos_te = sequences(Xs, y, start, test_end)
        if len(pos_te) == 0:
            continue
        p_te = np.column_stack([
            lg.predict_proba(Xs[pos_te])[:, 1],
            lstm.predict(Xte_s, verbose=0).ravel(),
            gb.predict_proba(Xs[pos_te])[:, 1],
        ])

        preds = {
            "logistic":   (p_te[:, 0] >= 0.5).astype(int),
            "lstm":       (p_te[:, 1] >= 0.5).astype(int),
            "gbdt":       (p_te[:, 2] >= 0.5).astype(int),
            "ens_equal":  ((p_te @ np.array([1/3, 1/3, 1/3])) >= 0.5).astype(int),
            "ens_paper":  ((p_te @ PAPER_WEIGHTS) >= 0.5).astype(int),
            "ens_fitted": ((p_te @ w_fit) >= 0.5).astype(int),
        }
        for a in arms:
            store[a]["pred"].extend(preds[a].tolist())
            store[a]["idx"].extend(pos_te.tolist())

    out = {"symbol": symbol, "n_folds": len(fitted_ws),
           "fitted_weights_mean": np.mean(fitted_ws, axis=0).tolist() if fitted_ws else None,
           "fit_seconds": {k: float(np.mean(v)) for k, v in timings.items() if v}}

    for a in arms:
        pr = np.array(store[a]["pred"]); ix = np.array(store[a]["idx"])
        if ix.size == 0:
            continue
        correct = (pr == y[ix])
        k, m = int(correct.sum()), int(correct.size)
        signed = np.where(pr == 1, 1.0, -1.0) * r[ix]
        out[a] = {"n": m, "accuracy": k / m, "ci95": wilson_interval(k, m),
                  "vs_coinflip": prop_test(k, m, 0.5),
                  "gross_sharpe": sharpe(signed)}
        out[f"_correct_{a}"] = correct

    # inverse persistence over the identical index, for reference
    ix = np.array(store["logistic"]["idx"])
    inv = (1 - y[ix - 1])
    corr_inv = (inv == y[ix])
    out["inverse_persistence"] = {"n": int(corr_inv.size),
                                  "accuracy": float(corr_inv.mean()),
                                  "ci95": wilson_interval(int(corr_inv.sum()), int(corr_inv.size))}
    for a in arms:
        if f"_correct_{a}" in out:
            out[a]["vs_best_single"] = None
            out[a]["vs_inverse_persistence"] = paired_accuracy_test(out[f"_correct_{a}"], corr_inv)

    # paired: each ensemble against the best single model on this symbol
    singles = {a: out[a]["accuracy"] for a in ("logistic", "gbdt", "lstm") if a in out}
    best_single = max(singles, key=singles.get)
    out["best_single"] = best_single
    for a in ("ens_equal", "ens_paper", "ens_fitted"):
        if f"_correct_{a}" in out:
            out[a]["vs_best_single"] = paired_accuracy_test(
                out[f"_correct_{a}"], out[f"_correct_{best_single}"])

    out["_raw"] = raw
    out["_dates"] = dates
    out["_probs_idx"] = ix
    out["_y"] = y
    out["_r"] = r
    return {k: v for k, v in out.items()}


# --------------------------------------------------------------------------
def benchmarks() -> dict:
    """Latency and throughput of the offline path."""
    import tensorflow as tf

    df = load_symbol("BTCUSDT")
    out = {"host": {
        "cpu": platform.processor()[:70],
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "python": platform.python_version(),
        "tf_gpus": len(tf.config.list_physical_devices("GPU")),
    }}
    try:
        import torch
        out["host"]["torch_cuda"] = bool(torch.cuda.is_available())
        out["host"]["gpu_name"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        out["host"]["torch_cuda"] = False

    # feature construction
    ts = []
    for _ in range(7):
        t0 = time.perf_counter(); build_features(df); ts.append(time.perf_counter() - t0)
    out["feature_build"] = {"bars": int(len(df)), "median_s": float(np.median(ts)),
                            "us_per_bar": float(np.median(ts) / len(df) * 1e6)}

    feats = build_features(df)
    valid = feats.notna().all(axis=1)
    X = feats[valid].to_numpy(dtype=float)
    y = (df["close"].shift(-1) > df["close"]).astype(int).to_numpy()[valid.to_numpy()]
    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)

    lg = LogisticRegression(max_iter=2000, C=0.1).fit(Xs, y)
    gb = HistGradientBoostingClassifier(max_depth=3, max_iter=200, learning_rate=0.05,
                                        random_state=0).fit(Xs, y)
    lstm = build_lstm(X.shape[1])
    seq = np.stack([Xs[i - SEQ_LEN:i] for i in range(SEQ_LEN, SEQ_LEN + 256)])
    lstm.fit(seq, y[SEQ_LEN:SEQ_LEN + 256], epochs=1, batch_size=64, verbose=0)

    def percentiles(fn, reps=200):
        lat = []
        for _ in range(reps):
            t0 = time.perf_counter(); fn(); lat.append((time.perf_counter() - t0) * 1000)
        a = np.array(lat)
        return {"p50_ms": float(np.percentile(a, 50)), "p95_ms": float(np.percentile(a, 95)),
                "p99_ms": float(np.percentile(a, 99))}

    one = Xs[-1:].copy()
    one_seq = Xs[-SEQ_LEN:][None, :, :]
    out["inference"] = {
        "scaler_transform": percentiles(lambda: scaler.transform(X[-1:])),
        "logistic": percentiles(lambda: lg.predict_proba(one)),
        "gbdt": percentiles(lambda: gb.predict_proba(one)),
        "lstm": percentiles(lambda: lstm.predict(one_seq, verbose=0), reps=60),
    }

    # backtest throughput
    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)
    async def _u(*a, **k): raise AssertionError
    stub.execute_raw_sql = _u
    saved = sys.modules.get("backend.database")
    sys.modules["backend.database"] = stub
    try:
        from backend.backtest.engine import BacktestEngine
    finally:
        if saved is not None: sys.modules["backend.database"] = saved
        else: sys.modules.pop("backend.database", None)

    START = datetime(2020, 1, 1, tzinfo=UTC)
    prices, signals = [], []
    rng = np.random.default_rng(0)
    for sym, p0 in (("BTC", 40000.0), ("ETH", 2000.0), ("DOGE", 0.1)):
        p = p0
        for i in range(6000):
            o = p; p *= math.exp(rng.normal(0, 0.02))
            prices.append({"ts": START + timedelta(days=i), "symbol": sym, "open": o,
                           "high": max(o, p) * 1.01, "low": min(o, p) * 0.99, "close": p})
            if rng.random() < 0.25:
                signals.append({"id": len(signals), "ts": START + timedelta(days=i),
                                "symbol": sym, "signal": "BUY" if rng.random() < 0.5 else "SELL",
                                "strength": 10.0})
    eng = BacktestEngine()
    t0 = time.perf_counter()
    trades, curve = eng.simulate_trades(signals, prices, 10_000.0, {"hold_bars": 1})
    el = time.perf_counter() - t0
    out["backtest"] = {"bars": len(prices), "signals": len(signals), "trades": len(trades),
                       "seconds": el, "bars_per_second": len(prices) / el}

    proc = psutil.Process()
    out["memory"] = {"rss_mb": round(proc.memory_info().rss / 1e6, 1)}
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "ablation_results.json")
    args = ap.parse_args()

    print("=" * 92)
    print("ABLATION: does the ensemble beat its best member?")
    print("=" * 92)
    per = {}
    for s in SYMBOLS:
        print(f"  {s} ...", flush=True)
        per[s] = evaluate_symbol(s)

    arms = ["logistic", "gbdt", "lstm", "ens_equal", "ens_paper", "ens_fitted"]
    print(f"\n{'symbol':<9}{'arm':<13}{'n':>6}{'acc':>8}{'95% CI':>18}{'grossSR':>9}"
          f"{'vs best single':>18}")
    print("-" * 92)
    for s, res in per.items():
        for a in arms:
            if a not in res: continue
            v = res[a]
            ci = f"[{v['ci95'][0]:.3f},{v['ci95'][1]:.3f}]"
            vs = v.get("vs_best_single")
            vst = f"{vs['diff']*100:+.2f}pp p={vs['p_value']:.2f}" if vs else ""
            print(f"{s:<9}{a:<13}{v['n']:>6}{v['accuracy']:>8.4f}{ci:>18}"
                  f"{v['gross_sharpe']:>9.2f}{vst:>18}")
        ip = res["inverse_persistence"]
        print(f"{s:<9}{'~inv_persist':<13}{ip['n']:>6}{ip['accuracy']:>8.4f}"
              f"{'':>18}{'':>9}{'  (best single: ' + res['best_single'] + ')':>18}")
        w = res.get("fitted_weights_mean")
        if w:
            print(f"{'':<9}fitted weights (stat/neural/boost): "
                  f"{w[0]:.2f}/{w[1]:.2f}/{w[2]:.2f}   vs paper 0.30/0.40/0.30")
        print("-" * 92)

    print("\nBENCHMARKS")
    bm = benchmarks()
    h = bm["host"]
    print(f"  host: {h['physical_cores']}c/{h['logical_cores']}t, {h['ram_gb']} GB RAM, "
          f"TF GPUs={h['tf_gpus']}, torch CUDA={h.get('torch_cuda')} ({h.get('gpu_name')})")
    fb = bm["feature_build"]
    print(f"  feature build: {fb['bars']} bars in {fb['median_s']*1000:.1f} ms "
          f"= {fb['us_per_bar']:.1f} us/bar")
    for k, v in bm["inference"].items():
        print(f"  inference {k:<18} p50={v['p50_ms']:.3f} ms  p95={v['p95_ms']:.3f} ms  p99={v['p99_ms']:.3f} ms")
    b = bm["backtest"]
    print(f"  backtest: {b['bars']} bars, {b['trades']} trades in {b['seconds']:.2f} s "
          f"= {b['bars_per_second']:,.0f} bars/s")
    print(f"  process RSS: {bm['memory']['rss_mb']} MB")
    for s, res in per.items():
        f = res.get("fit_seconds", {})
        if f:
            print(f"  train/fold {s:<9} " + "  ".join(f"{k.replace('fit_','')}={v:.2f}s"
                                                       for k, v in f.items()))

    payload = {
        "ablation": {s: {k: v for k, v in r.items() if not k.startswith("_")} for s, r in per.items()},
        "benchmarks": bm,
        "notes": {
            "substitutions": "Prophet -> logistic, XGBoost -> HistGradientBoosting (deps unavailable)",
            "sentiment_gate": "not evaluable; only synthetic sentiment available and it is excluded",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
