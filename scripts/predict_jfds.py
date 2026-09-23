"""Offline probability inference from a trusted, saved research fold pipeline.

This script neither submits orders nor establishes that a historical model is
appropriate for current trading. Never load an untrusted joblib/pickle file.
"""
import argparse
from pathlib import Path
import sys
import json
import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_walkforward import build_features


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--asof", help="UTC day whose bar is complete (YYYY-MM-DD)")
    a = p.parse_args()
    saved = joblib.load(a.model)
    raw = pd.read_csv(a.csv, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    if a.asof:
        raw = raw.loc[raw.date <= pd.Timestamp(a.asof)].reset_index(drop=True)
    if raw.empty or raw.date.duplicated().any():
        raise ValueError("No input history or duplicate dates")
    last = raw.date.iloc[-1]
    if last <= pd.Timestamp(saved["fold"]["last_train_label"]):
        raise ValueError("Inference date is not after the saved model's training information")
    features = build_features(raw)[saved["features"]].iloc[[-1]]
    if features.isna().any().any():
        raise ValueError("Insufficient or invalid causal feature history")
    prob = float(saved["pipeline"].predict_proba(features)[0,1])
    print(json.dumps(dict(decision_bar=str(last.date()), target="next close exceeds this close",
                          probability_up=prob, predicted_up=prob > .5,
                          model_train_end=saved["fold"]["train_end"],
                          warning="Historical research model; not validated for live deployment"), indent=2))


if __name__ == "__main__":
    main()
