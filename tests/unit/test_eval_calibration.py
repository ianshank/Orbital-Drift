"""Behaviour and boundedness tests for calibration-curve ECE."""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orbital_drift.eval.calibration import CalibrationStrategy, calibration_error


def test_quantile_ece_uses_each_requested_policy_field() -> None:
    result = calibration_error(
        np.asarray([0, 0, 1, 1]),
        np.asarray([0.1, 0.4, 0.6, 0.9]),
        bin_count=2,
        strategy="quantile",
    )
    assert result.strategy == "quantile"
    assert result.requested_bins == 2
    assert result.populated_bins == 2


def test_uniform_ece_supports_equal_width_bins_explicitly() -> None:
    result = calibration_error(
        np.asarray([0, 0, 1, 1]),
        np.asarray([0.1, 0.4, 0.6, 0.9]),
        bin_count=2,
        strategy="uniform",
    )
    assert result.strategy == "uniform"


def test_identical_predictions_collapse_to_one_populated_quantile_bin() -> None:
    result = calibration_error(
        np.asarray([0, 1, 0, 1]),
        np.full(4, 0.5),
        bin_count=4,
        strategy="quantile",
    )
    assert result.populated_bins == 1


def test_single_class_labels_are_handled_by_sklearn_calibration_curve() -> None:
    result = calibration_error(
        np.zeros(4),
        np.asarray([0.1, 0.2, 0.3, 0.4]),
        bin_count=2,
        strategy="quantile",
    )
    assert 0.0 <= result.expected_calibration_error <= 1.0


@given(
    st.lists(st.booleans(), min_size=2, max_size=20),
    st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=2, max_size=20),
)
@settings(max_examples=20)
def test_ece_is_bounded_for_equal_length_binary_inputs(
    labels: list[bool], probabilities: list[float]
) -> None:
    size = min(len(labels), len(probabilities))
    result = calibration_error(
        np.asarray(labels[:size]),
        np.asarray(probabilities[:size]),
        bin_count=2,
        strategy="quantile",
    )
    assert 0.0 <= result.expected_calibration_error <= 1.0


@pytest.mark.parametrize(
    ("labels", "scores", "bin_count", "strategy", "message"),
    [
        (np.asarray([]), np.asarray([]), 2, "quantile", "empty"),
        (np.asarray([0]), np.asarray([0.1, 0.2]), 2, "quantile", "equal length"),
        (np.asarray([0, 1]), np.asarray([0.1, np.nan]), 2, "quantile", "finite"),
        (np.asarray([0, 1]), np.asarray([0.1, 1.1]), 2, "quantile", "closed"),
        (np.asarray([0, 1]), np.asarray([0.1, 0.2]), 0, "quantile", "bin_count"),
        (np.asarray([0, 1]), np.asarray([0.1, 0.2]), 2, "other", "strategy"),
    ],
)
def test_ece_rejects_invalid_inputs(
    labels: np.ndarray, scores: np.ndarray, bin_count: int, strategy: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        calibration_error(
            labels,
            scores,
            bin_count=bin_count,
            strategy=cast(CalibrationStrategy, strategy),
        )
