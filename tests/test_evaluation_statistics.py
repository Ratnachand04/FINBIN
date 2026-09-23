import numpy as np

from scripts.evaluate_walkforward import paired_accuracy_test


def test_paired_accuracy_uses_requested_hac_lag():
    model = np.array([True, False, True, True, False, True, False, False] * 8)
    baseline = np.array([False, False, True, False, True, True, False, True] * 8)

    result = paired_accuracy_test(model, baseline, hac_lags=5)

    assert result["hac_lags"] == 5
    assert result["diff"] == np.mean(model.astype(float) - baseline.astype(float))
    assert result["se_hac"] >= 0
    assert 0 <= result["p_value"] <= 1


def test_paired_accuracy_caps_hac_lag_at_sample_length():
    result = paired_accuracy_test(
        np.array([True, False, True]),
        np.array([False, False, True]),
        hac_lags=20,
    )

    assert result["hac_lags"] == 2


def test_identical_correctness_vectors_have_no_difference():
    correctness = np.array([True, False, True, False, True, True])

    result = paired_accuracy_test(correctness, correctness)

    assert result["diff"] == 0
    assert result["se_hac"] == 0
    assert result["z_hac"] == 0
    assert result["p_value"] == 1
