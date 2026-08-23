"""Behaviour tests for deterministic PySAL Moran inference."""

from __future__ import annotations

import numpy as np
import pytest
from libpysal.weights import W

from orbital_drift.eval.spatial import MoranConfig, morans_i


def _weights() -> W:
    return W(
        neighbors={0: [1], 1: [0, 2], 2: [1, 3], 3: [2]},
        silence_warnings=True,
    )


def test_morans_i_reports_pysal_permutation_evidence_and_row_standardises() -> None:
    weights = _weights()
    result = morans_i(np.asarray([1.0, 2.0, 3.0, 4.0]), weights=weights, config=MoranConfig(31, 47))
    assert result.permutations == 31
    assert result.seed == 47
    assert 0.0 < result.pseudo_p_value <= 1.0
    assert weights.transform == "R"


def test_morans_i_is_repeatable_with_the_same_seed() -> None:
    values = np.asarray([1.0, 3.0, 2.0, 4.0])
    first = morans_i(values, weights=_weights(), config=MoranConfig(37, 53))
    repeated = morans_i(values, weights=_weights(), config=MoranConfig(37, 53))
    assert first == repeated


@pytest.mark.parametrize(
    ("values", "config", "message"),
    [
        (np.asarray([]), MoranConfig(3, 59), "empty"),
        (np.asarray([1.0, np.nan, 3.0, 4.0]), MoranConfig(3, 59), "finite"),
        (np.asarray([1.0, 2.0, 3.0, 4.0]), MoranConfig(0, 59), "permutations"),
    ],
)
def test_morans_i_rejects_invalid_inputs(
    values: np.ndarray, config: MoranConfig, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        morans_i(values, weights=_weights(), config=config)


def test_morans_i_rejects_weights_for_a_different_observation_count() -> None:
    with pytest.raises(ValueError, match="weights"):
        morans_i(np.asarray([1.0, 2.0, 3.0]), weights=_weights(), config=MoranConfig(3, 61))


def test_constant_values_surface_pysals_degenerate_inference_warning() -> None:
    with pytest.warns(RuntimeWarning):
        result = morans_i(np.ones(4), weights=_weights(), config=MoranConfig(3, 71))
    assert np.isnan(result.statistic)
