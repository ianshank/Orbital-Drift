"""Behaviour tests for spatially dependent moving-block bootstrap intervals."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from orbital_drift.eval.bootstrap import (
    BlockSize,
    SpatialBlockBootstrapConfig,
    resample_spatial_blocks,
    spatial_block_bootstrap,
)


def _mean(values: np.ndarray) -> float:
    return float(np.mean(values))


def _config(seed: int) -> SpatialBlockBootstrapConfig:
    return SpatialBlockBootstrapConfig(
        block_size=BlockSize(rows=2, columns=2),
        confidence_level=0.9,
        replicates=41,
        seed=seed,
    )


def test_resample_preserves_the_grid_sample_count() -> None:
    values = np.arange(16.0).reshape(4, 4)
    resample = resample_spatial_blocks(
        values,
        block_size=BlockSize(rows=2, columns=2),
        rng=np.random.default_rng(11),
    )
    assert resample.values.size == values.size
    assert resample.source_indices.size == values.size


def test_same_seed_produces_bit_identical_bootstrap_result() -> None:
    values = np.arange(36.0).reshape(6, 6)
    first = spatial_block_bootstrap(values, statistic=_mean, config=_config(17))
    repeated = spatial_block_bootstrap(values, statistic=_mean, config=_config(17))
    assert first == repeated


def test_different_seed_changes_bootstrap_result() -> None:
    values = np.arange(36.0).reshape(6, 6)
    first = spatial_block_bootstrap(values, statistic=_mean, config=_config(17))
    different = spatial_block_bootstrap(values, statistic=_mean, config=_config(23))
    assert first != different


@given(
    st.lists(st.floats(min_value=-10.0, max_value=10.0, allow_nan=False), min_size=9, max_size=9)
)
@settings(max_examples=20)
def test_bootstrap_interval_always_brackets_the_observed_statistic(values: list[float]) -> None:
    result = spatial_block_bootstrap(
        np.asarray(values, dtype=np.float64).reshape(3, 3),
        statistic=_mean,
        config=SpatialBlockBootstrapConfig(
            block_size=BlockSize(rows=1, columns=1),
            confidence_level=0.8,
            replicates=19,
            seed=29,
        ),
    )
    assert result.lower_bound <= result.observed_statistic <= result.upper_bound


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (np.asarray([1.0, 2.0]), "two-dimensional"),
        (np.empty((0, 0)), "empty"),
        (np.asarray([[np.nan]]), "finite"),
    ],
)
def test_resampling_rejects_an_invalid_spatial_grid(values: np.ndarray, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resample_spatial_blocks(
            values,
            block_size=BlockSize(rows=1, columns=1),
            rng=np.random.default_rng(31),
        )


@pytest.mark.parametrize(
    "block_size",
    [BlockSize(rows=0, columns=1), BlockSize(rows=1, columns=0), BlockSize(rows=3, columns=1)],
)
def test_resampling_rejects_an_infeasible_block_size(block_size: BlockSize) -> None:
    with pytest.raises(ValueError):
        resample_spatial_blocks(
            np.ones((2, 2)), block_size=block_size, rng=np.random.default_rng(37)
        )


@pytest.mark.parametrize(
    "config",
    [
        SpatialBlockBootstrapConfig(BlockSize(1, 1), 0.9, 0, 41),
        SpatialBlockBootstrapConfig(BlockSize(1, 1), 0.0, 3, 41),
        SpatialBlockBootstrapConfig(BlockSize(1, 1), 1.0, 3, 41),
    ],
)
def test_bootstrap_rejects_an_invalid_interval_policy(config: SpatialBlockBootstrapConfig) -> None:
    with pytest.raises(ValueError):
        spatial_block_bootstrap(np.ones((2, 2)), statistic=_mean, config=config)


def test_bootstrap_rejects_a_nonfinite_statistic() -> None:
    with pytest.raises(ValueError, match="statistic"):
        spatial_block_bootstrap(
            np.ones((2, 2)),
            statistic=lambda _values: float("nan"),
            config=_config(43),
        )
