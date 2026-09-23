"""Regression fixtures for the measurements actually reported in the new paper."""
import json
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

from scripts.evaluate_walkforward import build_features, load_symbol
from scripts.research_statistics import (paired_hac, stationary_indices, portfolio_series,
                                        cost_statistics, round_trip_return)
from scripts import mutation_matrix

ROOT = Path(__file__).resolve().parents[1]


def test_hac_matches_hand_computed_bartlett_variance():
    # d=(1,0,-1,0): gamma0=1/2, gamma1=0, mean=0; SE=sqrt(1/8).
    result = paired_hac([1, 0, 0, 0], [0, 0, 1, 0], lags=1)
    assert result["se_hac"] == pytest.approx(np.sqrt(1/8))
    assert result["p_value"] == 1


@pytest.mark.parametrize("a,b,k", [([1],[0],1), ([1,0],[0],1), ([1,np.nan],[0,1],1),
                                     ([1,0],[0,1],-1), ([1,0],[0,1],1.5)])
def test_hac_rejects_invalid_inputs(a, b, k):
    with pytest.raises(ValueError):
        paired_hac(a, b, k)


def test_hac_does_not_call_constant_nonzero_difference_nonsignificant():
    result = paired_hac([1, 1, 1], [0, 0, 0])
    assert result["p_value"] is None
    assert result["status"] == "degenerate_nonzero"


def test_bootstrap_is_reproducible_and_preserves_columns():
    ix = stationary_indices(20, 40, 5, 7)
    assert np.array_equal(ix, stationary_indices(20, 40, 5, 7))
    panel = np.column_stack([np.arange(20), np.arange(20)*3])
    sampled = panel[ix]
    assert np.array_equal(sampled[:, :, 0]*3, sampled[:, :, 1])
    assert len(np.unique(ix[0])) < 20  # repeats, not a permutation


def test_portfolio_uses_available_assets_and_includes_liquidation():
    p = np.array([[1, np.nan], [0, 1]])
    r = np.array([[.1, np.nan], [.2, .4]])
    gross, turn, passive = portfolio_series(p, r)
    assert gross == pytest.approx([.1, .1])
    assert passive == pytest.approx([.1, .3])
    assert turn == pytest.approx([1., 3.])  # rebalance 2, final liquidation 1


def test_cost_root_is_exact_and_negative_values_are_not_clipped():
    gross = np.array([-.01, .005, -.01, .005])
    turn = np.ones(4)
    ix = np.tile(np.arange(4), (10, 1))
    result = cost_statistics(gross, turn, ix)
    assert result["breakeven_bps"] == pytest.approx(-25)
    assert (gross - result["breakeven_bps"]/10000*turn).mean() == pytest.approx(0)
    assert result["breakeven_ci95"] == pytest.approx([-25, -25])


@pytest.mark.parametrize("side,expected", [(1, .079), (-1, -.121)])
def test_round_trip_own_notional_fees(side, expected):
    # Entry 100, exit 110, fee 1 + 1.1: long 7.9%, short -12.1%.
    assert round_trip_return(side, 100, 110, fee=.01, slip=0) == pytest.approx(expected)


def test_feature_count_and_prefix_invariance():
    raw = load_symbol("BTCUSDT")
    full = build_features(raw)
    assert len(full.columns) == 30
    pd.testing.assert_frame_equal(build_features(raw.iloc[:400]), full.iloc[:400])
    altered = raw.copy()
    altered.loc[400:, ["open", "high", "low", "close", "volume"]] *= 100
    pd.testing.assert_frame_equal(build_features(altered).iloc[:400], full.iloc[:400])


@pytest.mark.parametrize("exit_code,output", [(2, "1 error during collection"), (5, "no tests ran"),
                                              (0, "29 skipped"), (0, "")])
def test_mutation_runner_rejects_broken_collection(monkeypatch, exit_code, output):
    monkeypatch.setattr(mutation_matrix.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=exit_code, stdout=output, stderr=""))
    with pytest.raises(RuntimeError):
        mutation_matrix.run_suite(None)


def test_saved_folds_and_predictions_reconcile():
    pred = pd.read_csv(ROOT / "docs/jfds/oos_predictions.csv")
    folds = pd.read_csv(ROOT / "docs/jfds/fold_manifest.csv")
    results = json.loads((ROOT / "docs/jfds/results.json").read_text())
    assert len(pred) == 5562
    assert not pred.duplicated(["symbol", "date"]).any()
    assert folds.test_n.sum() == len(pred)
    assert (pd.to_datetime(folds.last_train_label) < pd.to_datetime(folds.test_start)).all()
    for name, value in results["pooled_accuracy_descriptive"].items():
        assert (pred[f"pred_{name}"] == pred.y).mean() == pytest.approx(value)
    assert results["execution"]["max_cash_reconciliation_error"] < 1e-7
    assert results["execution"]["cumulative_reconciliation_error"] < 1e-7


def test_saved_model_reproduces_first_fold_without_refitting():
    saved = joblib.load(ROOT / "docs/jfds/models/BTCUSDT_logistic_fold0.joblib")
    pred = pd.read_csv(ROOT / "docs/jfds/oos_predictions.csv", parse_dates=["date"])
    pred = pred[(pred.symbol == "BTCUSDT") & (pred.fold == 0)]
    raw = load_symbol("BTCUSDT")
    x = build_features(raw).loc[raw.date.isin(pred.date), saved["features"]]
    pipeline = saved["pipeline"]
    before = pipeline.named_steps["scale"].mean_.copy()
    actual = pipeline.predict_proba(x)[:, 1]
    assert actual == pytest.approx(pred.prob_logistic.to_numpy(), abs=1e-12)
    assert np.array_equal(before, pipeline.named_steps["scale"].mean_)
