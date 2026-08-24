"""Regression and invariance tests for the paired superiority promotion gate."""

from __future__ import annotations

from io import StringIO

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orbital_drift.eval.bootstrap import (
    BlockSize,
    SpatialBlockBootstrapConfig,
    spatial_block_bootstrap,
)
from orbital_drift.eval.superiority import SuperiorityConfig, superiority_gate
from orbital_drift.observability.logging import configure_logging


def _mean(values: np.ndarray) -> float:
    return float(np.mean(values))


def _config(seed: int, minimum_effect: float = 0.0) -> SuperiorityConfig:
    return SuperiorityConfig(
        block_size=BlockSize(rows=2, columns=2),
        confidence_level=0.9,
        minimum_effect=minimum_effect,
        replicates=47,
        seed=seed,
    )


def test_paired_gate_rejects_a_worse_candidate_that_the_old_single_metric_test_passes() -> None:
    candidate = np.full((4, 4), 0.8)
    champion = np.full((4, 4), 0.9)
    old_result = spatial_block_bootstrap(
        candidate,
        statistic=_mean,
        config=SpatialBlockBootstrapConfig(BlockSize(2, 2), 0.9, 31, 67),
    )
    result = superiority_gate(candidate, champion, metric=_mean, config=_config(67))
    assert old_result.lower_bound > 0.0
    assert result.observed_difference < 0.0
    assert result.passes is False


def test_same_seed_produces_bit_identical_paired_evidence() -> None:
    candidate = np.arange(36.0).reshape(6, 6)
    champion = np.flipud(candidate) / 2.0
    first = superiority_gate(candidate, champion, metric=_mean, config=_config(71))
    repeated = superiority_gate(candidate, champion, metric=_mean, config=_config(71))
    assert first == repeated


def test_different_seed_changes_paired_bootstrap_evidence() -> None:
    candidate = np.arange(36.0).reshape(6, 6)
    champion = np.flipud(candidate) / 2.0
    first = superiority_gate(candidate, champion, metric=_mean, config=_config(73))
    different = superiority_gate(candidate, champion, metric=_mean, config=_config(79))
    assert first != different


@given(
    st.lists(st.floats(min_value=-5.0, max_value=5.0, allow_nan=False), min_size=9, max_size=9),
    st.lists(st.floats(min_value=-5.0, max_value=5.0, allow_nan=False), min_size=9, max_size=9),
)
@settings(max_examples=20)
def test_difference_interval_is_antisymmetric_when_models_are_swapped(
    candidate: list[float], champion: list[float]
) -> None:
    candidate_grid = np.asarray(candidate, dtype=np.float64).reshape(3, 3)
    champion_grid = np.asarray(champion, dtype=np.float64).reshape(3, 3)
    config = SuperiorityConfig(BlockSize(1, 1), 0.8, 0.0, 19, 83)
    forward = superiority_gate(candidate_grid, champion_grid, metric=_mean, config=config)
    reverse = superiority_gate(champion_grid, candidate_grid, metric=_mean, config=config)
    assert forward.observed_difference == pytest.approx(-reverse.observed_difference)
    assert forward.lower_bound == pytest.approx(-reverse.upper_bound)
    assert forward.upper_bound == pytest.approx(-reverse.lower_bound)


def test_gate_logs_the_reconstructible_final_verdict() -> None:
    stream = StringIO()
    configure_logging(level="DEBUG", stream=stream)
    result = superiority_gate(np.ones((2, 2)), np.zeros((2, 2)), metric=_mean, config=_config(89))
    output = stream.getvalue()
    assert result.passes is True
    assert '"passes": true' in output
    assert '"seed": 89' in output


@pytest.mark.parametrize(
    ("candidate", "champion", "config", "message"),
    [
        (np.ones((2, 2)), np.ones((3, 3)), _config(97), "shape"),
        (
            np.ones((2, 2)),
            np.ones((2, 2)),
            SuperiorityConfig(BlockSize(1, 1), 0.9, 0.0, 0, 97),
            "replicates",
        ),
        (
            np.ones((2, 2)),
            np.ones((2, 2)),
            SuperiorityConfig(BlockSize(1, 1), 1.0, 0.0, 3, 97),
            "confidence",
        ),
        (
            np.ones((2, 2)),
            np.ones((2, 2)),
            SuperiorityConfig(BlockSize(1, 1), 0.9, np.nan, 3, 97),
            "minimum",
        ),
        (
            np.ones((2, 2)),
            np.ones((2, 2)),
            SuperiorityConfig(BlockSize(0, 1), 0.9, 0.0, 3, 97),
            "block",
        ),
        (
            np.ones((2, 2)),
            np.ones((2, 2)),
            SuperiorityConfig(BlockSize(3, 1), 0.9, 0.0, 3, 97),
            "block",
        ),
    ],
)
def test_gate_rejects_invalid_policy_or_alignment(
    candidate: np.ndarray, champion: np.ndarray, config: SuperiorityConfig, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        superiority_gate(candidate, champion, metric=_mean, config=config)


def test_gate_rejects_a_nonfinite_metric() -> None:
    with pytest.raises(ValueError, match="metric"):
        superiority_gate(
            np.ones((2, 2)),
            np.zeros((2, 2)),
            metric=lambda _values: float("nan"),
            config=_config(101),
        )
