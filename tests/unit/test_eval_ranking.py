"""Regression tests ensuring PR-AUC remains non-interpolated average precision."""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import auc, precision_recall_curve

from orbital_drift.eval.ranking import average_precision


def test_average_precision_differs_from_trapezoidal_precision_recall_area() -> None:
    labels = np.asarray([1, 0, 0, 0])
    scores = np.asarray([0.1, 0.9, 0.8, 0.7])
    result = average_precision(labels, scores)
    precision, recall, _ = precision_recall_curve(labels, scores)
    trapezoidal_area = auc(recall, precision)
    assert result.average_precision != trapezoidal_area


def test_all_identical_predictions_have_a_finite_average_precision() -> None:
    result = average_precision(np.asarray([0, 1, 0, 1]), np.full(4, 0.5))
    assert result.average_precision == 0.5


def test_single_negative_class_emits_sklearn_warning_without_suppression() -> None:
    with pytest.warns(UserWarning, match="No positive class"):
        result = average_precision(np.zeros(3), np.asarray([0.1, 0.2, 0.3]))
    assert result.average_precision == 0.0


@pytest.mark.parametrize(
    ("labels", "scores", "message"),
    [
        (np.asarray([]), np.asarray([]), "empty"),
        (np.asarray([0]), np.asarray([0.1, 0.2]), "equal length"),
        (np.asarray([0, 1]), np.asarray([0.1, np.nan]), "finite"),
        (np.asarray([0.0, np.nan]), np.asarray([0.1, 0.2]), "finite"),
    ],
)
def test_average_precision_rejects_invalid_inputs(
    labels: np.ndarray, scores: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        average_precision(labels, scores)
