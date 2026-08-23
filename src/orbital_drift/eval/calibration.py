"""Expected Calibration Error (ECE) through scikit-learn's calibration curve.

Guo et al. (ICML 2017), https://proceedings.mlr.press/v70/guo17a.html, popularised
binned ECE for modern neural networks. Equal-width bins are biased: equal-mass
(``quantile``) bins have lower bias according to Roelofs et al. (AISTATS 2022),
https://proceedings.mlr.press/v151/roelofs22a.html, and adaptive binning is also
recommended by Nixon et al., https://arxiv.org/abs/1904.01685. Kumar et al.,
https://arxiv.org/abs/1909.10155, likewise identifies the limitations of common
plug-in calibration estimators. Therefore callers must choose a strategy and
should normally choose ``quantile`` for the preferred equal-mass estimator.

``netcal`` and ``torchmetrics`` are intentionally not dependencies: both bring
Torch into every importing stage, and TorchMetrics' CalibrationError documents a
uniform-bin estimator, https://lightning.ai/docs/torchmetrics/stable/classification/calibration_error.html,
which is the biased equal-width option this adapter makes explicit rather than
silently selecting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray
from sklearn.calibration import calibration_curve

type FloatArray = NDArray[np.float64]
PERCENT_SCALE: Final[float] = 100.0  # pin: numpy.percentile accepts percentile, not quantile units
"""Converts a unit-interval quantile into the percentile unit ``numpy.percentile`` requires."""

CalibrationStrategy = Literal["uniform", "quantile"]


@dataclass(frozen=True)
class CalibrationResult:
    """ECE and the reliability-curve values delegated to scikit-learn."""

    expected_calibration_error: float
    strategy: CalibrationStrategy
    requested_bins: int
    populated_bins: int
    fraction_of_positives: FloatArray
    mean_predicted_value: FloatArray


def _finite_vector(values: NDArray[np.generic], *, name: str) -> FloatArray:
    """Validate non-empty finite vectors before sklearn raises less focused errors."""
    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    if vector.size == 0:
        raise ValueError(f"{name} must not be empty")
    if not bool(np.all(np.isfinite(vector))):
        raise ValueError(f"{name} must contain only finite numbers")
    return vector


def _bin_weights(
    probabilities: FloatArray, *, bin_count: int, strategy: CalibrationStrategy
) -> FloatArray:
    """Recover bin occupancy for weighting sklearn's returned calibration values.

    ``calibration_curve`` intentionally returns only non-empty bin means, not
    their counts. This helper mirrors only its documented strategy boundaries to
    calculate ECE's required sample weights; the calibration fractions and means
    themselves remain exclusively the values supplied by scikit-learn.
    """
    if strategy == "uniform":
        boundaries = np.linspace(0.0, 1.0, bin_count + 1)
    else:
        quantiles = np.linspace(0.0, 1.0, bin_count + 1)
        boundaries = np.percentile(probabilities, quantiles * PERCENT_SCALE)
    bin_indices = np.searchsorted(boundaries[1:-1], probabilities)
    counts = np.bincount(bin_indices, minlength=bin_count)
    return counts[counts > 0] / probabilities.size


def calibration_error(
    y_true: NDArray[np.generic],
    probabilities: NDArray[np.generic],
    *,
    bin_count: int,
    strategy: CalibrationStrategy,
) -> CalibrationResult:
    """Return binned ECE using ``sklearn.calibration.calibration_curve``.

    The strategy is required to prevent accidental use of biased equal-width
    bins. ``quantile`` asks sklearn for equal-mass bins and is the preferred
    estimator when a caller does not have a pre-registered reason for uniform
    visualisation bins.
    """
    labels = _finite_vector(y_true, name="y_true")
    scores = _finite_vector(probabilities, name="probabilities")
    if labels.size != scores.size:
        raise ValueError("y_true and probabilities must have equal length")
    if bin_count <= 0:
        raise ValueError("bin_count must be positive")
    if strategy not in {"uniform", "quantile"}:
        raise ValueError("strategy must be 'uniform' or 'quantile'")
    if bool(np.any(scores < 0.0)) or bool(np.any(scores > 1.0)):
        raise ValueError("probabilities must lie in the closed interval [zero, one]")

    fraction_of_positives, mean_predicted_value = calibration_curve(
        labels,
        scores,
        n_bins=bin_count,
        strategy=strategy,
    )
    weights = _bin_weights(scores, bin_count=bin_count, strategy=strategy)
    deviations = np.abs(fraction_of_positives - mean_predicted_value)
    ece = float(np.sum(weights * deviations))
    return CalibrationResult(
        expected_calibration_error=ece,
        strategy=strategy,
        requested_bins=bin_count,
        populated_bins=fraction_of_positives.size,
        fraction_of_positives=np.asarray(fraction_of_positives, dtype=np.float64),
        mean_predicted_value=np.asarray(mean_predicted_value, dtype=np.float64),
    )
