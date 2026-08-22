"""Evaluation of the rule-composed decision layer.

The signal generator was described in the architecture but never measured. This
quantifies it: how often each condition group passes, how often a signal
actually fires, and whether fired signals beat the base rate.

Two honest scoping notes.

*The sentiment gate cannot be evaluated.* The deployed BUY rule requires three
primary conditions, one of which is a 24-hour aggregate sentiment above 0.70.
The only historical sentiment series available is synthetic -- generated from
same-day returns -- and is excluded from all results. We therefore evaluate the
rule with the sentiment primary removed. That a mandatory gate depends on a
signal the system cannot currently validate is itself a finding about the
architecture, not a gap in this script.

*Probabilities are real.* Model confidence comes from calibrated walk-forward
predict_proba output, not a placeholder, so the confidence gate does real work.

Usage:
    python scripts/decision_layer_study.py [--out docs/decision_layer.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_walkforward import (  # noqa: E402
    INITIAL_TRAIN, TEST_SIZE, EMBARGO, build_features, load_symbol,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
CONF_GATE = 0.75
RSI_OVERSOLD = 40.0
VOL_RATIO = 1.0
TAKER_BUY = 0.5
DROP_1D = -0.10
ATR_RATIO = 2.0


def indicators(raw: pd.DataFrame) -> dict[str, np.ndarray]:
    close, high, low = raw["close"], raw["high"], raw["low"]
    d = close.diff()
    gain = d.clip(lower=0).rolling(14).mean()
    loss = (-d.clip(upper=0)).rolling(14).mean()
    tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    return {
        "rsi": (100 - 100 / (1 + gain / loss.replace(0, np.nan))).to_numpy(),
        "vol_ratio": (raw["volume"] / raw["volume"].rolling(20).mean()).to_numpy(),
        "taker_buy": (raw["taker_buy_base_asset_volume"] / raw["volume"].replace(0, np.nan)).to_numpy(),
        "atr_ratio": (atr / atr.rolling(60).mean()).to_numpy(),
        "drop_1d": (close / close.shift(1) - 1).to_numpy(),
    }


def walk_forward_proba(symbol: str):
    df = load_symbol(symbol)
    feats = build_features(df)
    target = (df["close"].shift(-1) > df["close"]).astype(float)
    fwd = df["close"].pct_change().shift(-1)
    valid = feats.notna().all(axis=1) & target.notna() & fwd.notna()
    idx = np.flatnonzero(valid.to_numpy())

    feats = feats.iloc[idx].reset_index(drop=True)
    raw = df.iloc[idx].reset_index(drop=True)
    y = target.iloc[idx].to_numpy().astype(int)
    r = fwd.iloc[idx].to_numpy()
    X = feats.to_numpy(dtype=float)
    n = len(X)

    proba, pos = [], []
    for start in range(INITIAL_TRAIN + EMBARGO, n - 1, TEST_SIZE):
        train_end, test_end = start - EMBARGO, min(start + TEST_SIZE, n)
        if test_end - start < 30:
            break
        sc = StandardScaler().fit(X[:train_end])
        m = LogisticRegression(max_iter=2000, C=0.1).fit(sc.transform(X[:train_end]), y[:train_end])
        proba.extend(m.predict_proba(sc.transform(X[start:test_end]))[:, 1].tolist())
        pos.extend(range(start, test_end))
    return raw, y, r, np.array(proba), np.array(pos)


def study(symbol: str) -> dict:
    raw, y, r, proba, pos = walk_forward_proba(symbol)
    ind = indicators(raw)

    n = len(pos)
    stage = {"candidates": n, "primary": 0, "supporting": 0, "risk": 0}
    fired_idx = []
    support_hist = np.zeros(4, dtype=int)

    for j, i in enumerate(pos):
        # Primary: direction UP and confidence above gate.
        # (Sentiment primary omitted -- see module docstring.)
        if not (proba[j] >= 0.5 and proba[j] > CONF_GATE):
            continue
        stage["primary"] += 1

        s = 0
        s += 1 if np.isfinite(ind["taker_buy"][i]) and ind["taker_buy"][i] > TAKER_BUY else 0
        s += 1 if np.isfinite(ind["rsi"][i]) and ind["rsi"][i] < RSI_OVERSOLD else 0
        s += 1 if np.isfinite(ind["vol_ratio"][i]) and ind["vol_ratio"][i] > VOL_RATIO else 0
        support_hist[min(s, 3)] += 1
        if s < 2:
            continue
        stage["supporting"] += 1

        risk = 0
        risk += 1 if np.isfinite(ind["drop_1d"][i]) and ind["drop_1d"][i] < DROP_1D else 0
        risk += 1 if np.isfinite(ind["atr_ratio"][i]) and ind["atr_ratio"][i] > ATR_RATIO else 0
        if risk > 0:
            continue
        stage["risk"] += 1
        fired_idx.append(i)

    fired = np.array(fired_idx, dtype=int)
    base_rate = float((y[pos] == 1).mean())
    base_ret = float(r[pos].mean())

    out = {
        "symbol": symbol,
        "stages": stage,
        "fired": int(fired.size),
        "fire_rate": float(fired.size / n) if n else 0.0,
        "support_histogram": support_hist.tolist(),
        "base_rate_up": base_rate,
        "base_mean_fwd_return": base_ret,
        "confidence_gate": CONF_GATE,
        "max_proba_observed": float(proba.max()),
        "frac_proba_above_gate": float((proba > CONF_GATE).mean()),
        "sentiment_gate_evaluable": False,
    }
    if fired.size:
        out["signal_hit_rate"] = float((y[fired] == 1).mean())
        out["signal_mean_fwd_return"] = float(r[fired].mean())
        out["lift_vs_base_pp"] = (out["signal_hit_rate"] - base_rate) * 100
    else:
        out["signal_hit_rate"] = None
        out["signal_mean_fwd_return"] = None
        out["lift_vs_base_pp"] = None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "decision_layer.json")
    args = ap.parse_args()

    results = {}
    print(f"{'symbol':<9}{'cand':>7}{'primary':>9}{'+supp':>7}{'+risk':>7}{'fired':>7}"
          f"{'rate':>8}{'hit':>8}{'base':>8}{'lift':>8}")
    print("-" * 80)
    for s in SYMBOLS:
        d = study(s)
        results[s] = d
        hit = f"{d['signal_hit_rate']:.4f}" if d["signal_hit_rate"] is not None else "n/a"
        lift = f"{d['lift_vs_base_pp']:+.2f}pp" if d["lift_vs_base_pp"] is not None else "n/a"
        print(f"{s:<9}{d['stages']['candidates']:>7}{d['stages']['primary']:>9}"
              f"{d['stages']['supporting']:>7}{d['stages']['risk']:>7}{d['fired']:>7}"
              f"{d['fire_rate']:>7.2%}{hit:>8}{d['base_rate_up']:>8.4f}{lift:>8}")

    print("\nconfidence gate diagnostics")
    for s, d in results.items():
        print(f"  {s:<9} max P(up) observed = {d['max_proba_observed']:.4f}   "
              f"fraction above {d['confidence_gate']} gate = {d['frac_proba_above_gate']:.4%}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
