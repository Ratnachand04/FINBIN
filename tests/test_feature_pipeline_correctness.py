"""Tests for train/serve consistency in the feature pipeline.

These pin the two defects that made model outputs meaningless: a scaler fitted
at inference time on a single row, and a train/validation split whose folds
overlapped in time.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest


def _import_ml():
    """Import the ML modules without requiring a database connection."""
    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)

    async def _unused(*args, **kwargs):
        raise AssertionError("database access is not expected in these tests")

    stub.execute_raw_sql = _unused
    saved = sys.modules.get("backend.database")
    sys.modules["backend.database"] = stub
    try:
        from backend.ml.feature_engineer import FeatureEngineer
        from backend.ml.model_trainer import ModelTrainer

        return FeatureEngineer, ModelTrainer
    finally:
        if saved is not None:
            sys.modules["backend.database"] = saved
        else:
            sys.modules.pop("backend.database", None)


FeatureEngineer, ModelTrainer = _import_ml()


class TestScaler:
    def test_single_row_transform_is_not_all_zeros(self):
        """The original bug: fit_transform on one row zeroes every feature."""
        fe = FeatureEngineer()
        if fe._scaler is None:
            pytest.skip("sklearn unavailable")

        rng = np.random.default_rng(0)
        train = rng.normal(loc=[10.0, -3.0, 100.0], scale=[2.0, 0.5, 25.0], size=(500, 3))
        fe.fit_scaler(train, feature_names=["a", "b", "c"])

        # A row far from the training mean must map to a large z-score, not 0.
        row = np.array([[20.0, -3.0, 100.0]])
        out = fe.transform(row)
        assert out.shape == (1, 3)
        assert not np.allclose(out, 0.0), "single-row transform collapsed to zeros"
        assert out[0, 0] > 3.0, "a +5 sigma feature should transform to a large z-score"

        # Demonstrate the old behaviour for contrast.
        from sklearn.preprocessing import StandardScaler

        assert np.allclose(StandardScaler().fit_transform(row), 0.0)

    def test_fit_is_not_refit_by_transform(self):
        fe = FeatureEngineer()
        if fe._scaler is None:
            pytest.skip("sklearn unavailable")
        train = np.arange(200, dtype=float).reshape(100, 2)
        fe.fit_scaler(train)
        first = fe.transform(np.array([[50.0, 51.0]]))
        fe.transform(np.array([[9999.0, -9999.0]]))  # must not change the fit
        second = fe.transform(np.array([[50.0, 51.0]]))
        assert np.allclose(first, second)

    def test_sequence_shape_is_preserved(self):
        fe = FeatureEngineer()
        if fe._scaler is None:
            pytest.skip("sklearn unavailable")
        rng = np.random.default_rng(1)
        X = rng.normal(size=(40, 12, 5))
        fe.fit_scaler(X)
        out = fe.transform(X)
        assert out.shape == X.shape
        flat = out.reshape(-1, 5)
        assert np.allclose(flat.mean(axis=0), 0.0, atol=1e-9)
        assert np.allclose(flat.std(axis=0), 1.0, atol=1e-9)

    def test_round_trip_through_disk_preserves_the_transform(self, tmp_path):
        fe = FeatureEngineer()
        if fe._scaler is None:
            pytest.skip("sklearn unavailable")
        pytest.importorskip("joblib")

        rng = np.random.default_rng(2)
        train = rng.normal(loc=5.0, scale=3.0, size=(300, 4))
        fe.fit_scaler(train, feature_names=["w", "x", "y", "z"])
        probe = rng.normal(size=(1, 4))
        expected = fe.transform(probe)

        path = tmp_path / "scaler.joblib"
        fe.save_scaler(path)

        loaded = FeatureEngineer()
        assert loaded.load_scaler(path) is True
        assert loaded._feature_order == ["w", "x", "y", "z"]
        assert np.allclose(loaded.transform(probe), expected)

    def test_unfitted_scaler_returns_input_unchanged(self):
        fe = FeatureEngineer()
        X = np.array([[1.0, 2.0, 3.0]])
        assert np.allclose(fe.transform(X), X), "unfitted scaler must not zero the features"


class TestEmbargo:
    def _trainer(self):
        try:
            return ModelTrainer()
        except Exception as exc:  # numpy/pandas missing
            pytest.skip(f"ModelTrainer unavailable: {exc}")

    def test_gap_between_folds_is_at_least_the_sequence_length(self):
        trainer = self._trainer()
        seq_len = 60
        n = 1000
        X = np.arange(n * seq_len * 2, dtype=float).reshape(n, seq_len, 2)
        y = np.arange(n) % 2

        X_tr, X_val, y_tr, y_val = trainer.split_with_embargo(X, y, train_frac=0.8)

        assert len(X_tr) == 800
        # 61 samples dropped: 60 for the window, 1 for the label horizon.
        assert len(X_val) == n - 800 - 61
        assert len(y_tr) == len(X_tr)
        assert len(y_val) == len(X_val)

    def test_no_row_appears_in_both_folds(self):
        trainer = self._trainer()
        seq_len = 10
        n = 200
        # Each sample carries a unique, ordered marker so overlap is detectable.
        X = np.stack([np.arange(i, i + seq_len, dtype=float) for i in range(n)])[:, :, None]
        y = np.zeros(n, dtype=int)

        X_tr, X_val, _, _ = trainer.split_with_embargo(X, y, train_frac=0.8)
        train_rows = set(np.unique(X_tr).tolist())
        val_rows = set(np.unique(X_val).tolist())
        assert not (train_rows & val_rows), "train and validation windows share underlying bars"

    def test_split_is_chronological(self):
        trainer = self._trainer()
        X = np.arange(500 * 3, dtype=float).reshape(500, 3, 1)
        y = np.arange(500) % 2
        X_tr, X_val, _, _ = trainer.split_with_embargo(X, y, train_frac=0.7)
        assert X_tr.max() < X_val.min(), "validation data must come strictly after training data"

    def test_short_series_still_yields_both_folds(self):
        trainer = self._trainer()
        X = np.arange(80 * 60, dtype=float).reshape(80, 60, 1)
        y = np.arange(80) % 2
        X_tr, X_val, y_tr, y_val = trainer.split_with_embargo(X, y, train_frac=0.8)
        assert len(X_tr) > 0 and len(X_val) > 0
        assert X_tr.max() < X_val.min()
