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


def test_ece_bounded_with_boundary_values() -> None:
    """Regression test (T063): ECE must stay in [0,1] even with values at bin boundaries.

    Prior to T063 fix, _bin_weights used different binning logic than sklearn's
    calibration_curve, causing shape mismatches that numpy silently broadcast,
    producing ECE > 1.0 for this input.
    """
    y_true = np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1])
    y_prob = np.array([0.0, 0.0, 0.0, 0.5, 0.5, 0.5, 1.0, 1.0, 1.0, 1.0])

    result = calibration_error(y_true, y_prob, bin_count=10, strategy="uniform")
    assert 0.0 <= result.expected_calibration_error <= 1.0, (
        f"ECE={result.expected_calibration_error} is out of bounds; "
        "this indicates a bin-weight/calibration-curve shape mismatch"
    )


def test_ece_bounded_quantile_with_ties() -> None:
    """Regression test (T063): quantile strategy with tied values must stay bounded.

    Tied probabilities can cause quantile bin boundaries to collapse, potentially
    causing different bin assignments between _bin_weights and calibration_curve.
    """
    y_true = np.array([0, 0, 0, 1, 1, 1, 0, 0, 1, 1])
    y_prob = np.array([0.2, 0.2, 0.2, 0.2, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8])

    result = calibration_error(y_true, y_prob, bin_count=5, strategy="quantile")
    assert 0.0 <= result.expected_calibration_error <= 1.0, (
        f"ECE={result.expected_calibration_error} is out of bounds"
    )


def test_ece_bounded_rb012_reproduction_case() -> None:
    """Exact RB-012 reproduction case: ECE must not exceed 1.0.

    This is the exact input that was documented in RB-012 as producing ECE=1.5
    before the T063 fix. The old code used side="right" for searchsorted while
    sklearn uses side="left", causing bin assignment divergence.
    """
    y_true = np.array([False, False, False])
    y_prob = np.array([1.0, 0.5, 0.5])

    result = calibration_error(y_true, y_prob, bin_count=2, strategy="quantile")
    assert 0.0 <= result.expected_calibration_error <= 1.0, (
        f"ECE={result.expected_calibration_error} exceeds valid [0,1] range; "
        "this is the exact RB-012 reproduction case"
    )


def test_bin_weight_mismatch_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """T063 defensive check: RuntimeError when bin counts mismatch.

    The defensive check at calibration_error:121-127 ensures we fail fast rather
    than silently broadcast misaligned arrays. This test verifies the check works
    by monkeypatching _bin_weights to return wrong-sized output.
    """
    import orbital_drift.eval.calibration as cal_module

    original_bin_weights = cal_module._bin_weights

    def mock_bin_weights(*args: object, **kwargs: object) -> np.ndarray:
        # Return weights with deliberately wrong size (1 extra element)
        real_weights = original_bin_weights(*args, **kwargs)  # type: ignore[arg-type]
        return np.append(real_weights, [0.1])

    monkeypatch.setattr(cal_module, "_bin_weights", mock_bin_weights)

    with pytest.raises(RuntimeError, match="Internal calibration bin-count mismatch"):
        calibration_error(
            np.array([0, 1, 0, 1]),
            np.array([0.1, 0.4, 0.6, 0.9]),
            bin_count=2,
            strategy="quantile",
        )


@given(
    st.lists(st.booleans(), min_size=10, max_size=100),
    st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=10, max_size=100
    ),
    st.integers(min_value=2, max_value=20),
    st.sampled_from(["uniform", "quantile"]),
)
@settings(max_examples=50)
def test_ece_bounded_property(
    labels: list[bool],
    probabilities: list[float],
    bin_count: int,
    strategy: str,
) -> None:
    """Property test (T063): ECE must always be in [0, 1] for valid inputs.

    This is a stronger version of the existing hypothesis test, with larger
    inputs and variable bin counts to stress-test the bin-weight alignment.
    """
    size = min(len(labels), len(probabilities))
    if size < 2:
        return  # Skip trivial inputs

    result = calibration_error(
        np.asarray(labels[:size]),
        np.asarray(probabilities[:size]),
        bin_count=bin_count,
        strategy=cast(CalibrationStrategy, strategy),
    )
    assert 0.0 <= result.expected_calibration_error <= 1.0, (
        f"ECE={result.expected_calibration_error} out of bounds for "
        f"{size} samples, {bin_count} bins, {strategy} strategy"
    )
