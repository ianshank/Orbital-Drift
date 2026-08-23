"""Paired spatial block-bootstrap promotion gate for candidate superiority.

A confidence interval for one model's metric only shows whether that metric
exceeds zero; it cannot establish superiority over a champion. The correct
comparison resamples the same test observations for both systems and forms
``candidate_metric - champion_metric`` in every replicate. This paired bootstrap
is applicable to complex performance metrics as described by Berg-Kirkpatrick,
Burkett, and Klein (EMNLP 2012), https://aclanthology.org/D12-1091/.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.random import PCG64, Generator
from numpy.typing import NDArray

from orbital_drift.eval.bootstrap import BlockSize, _as_finite_grid, _moving_block_indices
from orbital_drift.observability.logging import get_logger

type FloatArray = NDArray[np.float64]
LOGGER = get_logger("eval.superiority")


@dataclass(frozen=True)
class SuperiorityConfig:
    """Explicit effect, spatial resampling, interval, and seed policy for one gate."""

    block_size: BlockSize
    confidence_level: float
    minimum_effect: float
    replicates: int
    seed: int


@dataclass(frozen=True)
class SuperiorityResult:
    """Evidence and explicit pass/fail verdict for a paired superiority decision."""

    observed_difference: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    minimum_effect: float
    replicates: int
    seed: int
    passes: bool


Metric = Callable[[FloatArray], float]


def _metric_value(metric: Metric, values: FloatArray) -> float:
    """Ensure an arbitrary approved metric supplies finite gate evidence."""
    value = float(metric(values))
    if not bool(np.isfinite(value)):
        raise ValueError("metric must return a finite value")
    return value


def _validate_config(config: SuperiorityConfig) -> None:
    """Fail fast when a promotion policy is incomplete or mathematically invalid."""
    if config.replicates <= 0:
        raise ValueError("replicates must be positive")
    if not 0.0 < config.confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between zero and one")
    if not bool(np.isfinite(config.minimum_effect)):
        raise ValueError("minimum_effect must be finite")


def superiority_gate(
    candidate_values: NDArray[np.generic],
    champion_values: NDArray[np.generic],
    *,
    metric: Metric,
    config: SuperiorityConfig,
) -> SuperiorityResult:
    """Pass only if the paired interval's lower bound strictly exceeds minimum effect.

    ``candidate_values`` and ``champion_values`` must be aligned grids from the
    same evaluation examples. Each replicate takes one moving-block index draw
    and applies it to both grids, retaining pairing and its statistical power.
    """
    candidate_grid = _as_finite_grid(candidate_values)
    champion_grid = _as_finite_grid(champion_values)
    if candidate_grid.shape != champion_grid.shape:
        raise ValueError("candidate_values and champion_values must share a grid shape")
    _validate_config(config)
    if config.block_size.rows <= 0 or config.block_size.columns <= 0:
        raise ValueError("block dimensions must be positive")
    if (
        config.block_size.rows > candidate_grid.shape[0]
        or config.block_size.columns > candidate_grid.shape[1]
    ):
        raise ValueError("block dimensions must not exceed the spatial grid")

    rng = Generator(PCG64(config.seed))
    LOGGER.debug(
        "starting paired spatial block bootstrap",
        extra={
            "block_columns": config.block_size.columns,
            "block_rows": config.block_size.rows,
            "replicates": config.replicates,
            "seed": config.seed,
        },
    )
    candidate_flat = candidate_grid.reshape(-1)
    champion_flat = champion_grid.reshape(-1)
    observed = _metric_value(metric, candidate_flat) - _metric_value(metric, champion_flat)
    differences = np.empty(config.replicates, dtype=np.float64)
    for replicate_index in range(config.replicates):
        indices = _moving_block_indices(candidate_grid, config.block_size, rng)
        differences[replicate_index] = _metric_value(
            metric, candidate_flat[indices]
        ) - _metric_value(metric, champion_flat[indices])

    tail_probability = (1.0 - config.confidence_level) / 2.0
    lower = min(float(np.quantile(differences, tail_probability)), observed)
    upper = max(float(np.quantile(differences, 1.0 - tail_probability)), observed)
    passes = lower > config.minimum_effect
    LOGGER.info(
        "paired superiority gate completed",
        extra={
            "confidence_level": config.confidence_level,
            "lower_bound": lower,
            "minimum_effect": config.minimum_effect,
            "passes": passes,
            "replicates": config.replicates,
            "seed": config.seed,
        },
    )
    return SuperiorityResult(
        observed_difference=observed,
        lower_bound=lower,
        upper_bound=upper,
        confidence_level=config.confidence_level,
        minimum_effect=config.minimum_effect,
        replicates=config.replicates,
        seed=config.seed,
        passes=passes,
    )
