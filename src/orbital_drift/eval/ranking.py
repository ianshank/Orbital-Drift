"""Average precision for imbalanced ranking, delegated to scikit-learn.

Davis and Goadrich (2006), https://doi.org/10.1145/1143844.1143874, show that
linear interpolation in precision-recall space is invalid and can report 0.50
where the true area is 0.031. Scikit-learn therefore specifies that
``average_precision_score`` is non-interpolated and warns that trapezoidal
``auc(recall, precision)`` is overly optimistic:
https://scikit-learn.org/stable/modules/model_evaluation.html#precision-recall-f-measure-metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sklearn.metrics import average_precision_score


@dataclass(frozen=True)
class AveragePrecisionResult:
    """Non-interpolated PR-AUC reported with a named field rather than a bare float."""

    average_precision: float


def average_precision(
    y_true: NDArray[np.generic], scores: NDArray[np.generic]
) -> AveragePrecisionResult:
    """Return sklearn's non-interpolated average precision, never trapezoidal PR area."""
    labels = np.asarray(y_true, dtype=np.float64).reshape(-1)
    probabilities = np.asarray(scores, dtype=np.float64).reshape(-1)
    if labels.size == 0 or probabilities.size == 0:
        raise ValueError("y_true and scores must not be empty")
    if labels.size != probabilities.size:
        raise ValueError("y_true and scores must have equal length")
    if not bool(np.all(np.isfinite(labels))):
        raise ValueError("y_true must contain only finite numbers")
    if not bool(np.all(np.isfinite(probabilities))):
        raise ValueError("scores must contain only finite numbers")
    return AveragePrecisionResult(
        average_precision=float(average_precision_score(labels, probabilities))
    )
