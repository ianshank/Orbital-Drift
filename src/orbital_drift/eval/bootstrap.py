"""Deterministic moving-block bootstrap for uncertainty under spatial dependence.

Hall's spatial tile bootstrap predates the moving-block terminology: Hall (1985),
https://doi.org/10.1016/0304-4149(85)90212-1, introduced block resampling for
spatial coverage patterns. Künsch (1989),
https://doi.org/10.1214/aos/1176347265, is the later moving-block reference for
one-dimensional stationary processes. This module adapts that scheme to a regular
spatial grid by sampling contiguous, overlapping windows rather than independent
pixels, preserving local dependence within every drawn block.

This is uncertainty quantification for an already-defined evaluation set. It is
not blocked spatial cross-validation: Roberts et al. (2017),
https://onlinelibrary.wiley.com/doi/10.1111/ecog.02881, concerns how train/test
splits estimate generalisation, a different question that is not interchangeable
with a block-bootstrap interval.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.random import PCG64, Generator
from numpy.typing import NDArray

from orbital_drift.observability.logging import get_logger

type FloatArray = NDArray[np.float64]
LOGGER = get_logger("eval.bootstrap")


@dataclass(frozen=True)
class BlockSize:
    """Required dimensions of one contiguous spatial block in grid cells."""

    rows: int
    columns: int


@dataclass(frozen=True)
class SpatialBlockBootstrapConfig:
    """All policy and reproducibility inputs for one block-bootstrap interval."""

    block_size: BlockSize
    confidence_level: float
    replicates: int
    seed: int


@dataclass(frozen=True)
class SpatialResample:
    """One moving-block draw, retaining source indices as audit evidence."""

    values: FloatArray
    source_indices: NDArray[np.intp]


@dataclass(frozen=True)
class BootstrapResult:
    """Observed statistic and a percentile interval from dependent resamples."""

    observed_statistic: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    replicates: int
    seed: int


Statistic = Callable[[FloatArray], float]


def _as_finite_grid(values: NDArray[np.generic]) -> FloatArray:
    """Validate the regular grid required to define contiguous spatial blocks."""
    grid = np.asarray(values, dtype=np.float64)
    if grid.ndim != 2:
        raise ValueError("values must be a two-dimensional spatial grid")
    if grid.size == 0:
        raise ValueError("values must not be empty")
    if not bool(np.all(np.isfinite(grid))):
        raise ValueError("values must contain only finite numbers")
    return grid


def _validate_block_size(grid: FloatArray, block_size: BlockSize) -> None:
    """Reject infeasible windows before any random draw hides a configuration error."""
    if block_size.rows <= 0 or block_size.columns <= 0:
        raise ValueError("block dimensions must be positive")
    if block_size.rows > grid.shape[0] or block_size.columns > grid.shape[1]:
        raise ValueError("block dimensions must not exceed the spatial grid")


def _validate_config(config: SpatialBlockBootstrapConfig) -> None:
    """Make interval policy explicit rather than silently repairing caller input."""
    if config.replicates <= 0:
        raise ValueError("replicates must be positive")
    if not 0.0 < config.confidence_level < 1.0:
        raise ValueError("confidence_level must be strictly between zero and one")


def _moving_block_indices(
    grid: FloatArray, block_size: BlockSize, rng: Generator
) -> NDArray[np.intp]:
    """Draw overlapping contiguous windows until they contain one grid's sample size."""
    _validate_block_size(grid, block_size)
    row_starts = grid.shape[0] - block_size.rows + 1
    column_starts = grid.shape[1] - block_size.columns + 1
    block_area = block_size.rows * block_size.columns
    blocks_needed = math.ceil(grid.size / block_area)
    source_grid = np.arange(grid.size, dtype=np.intp).reshape(grid.shape)
    blocks: list[NDArray[np.intp]] = []
    for _ in range(blocks_needed):
        row_start = int(rng.integers(row_starts))
        column_start = int(rng.integers(column_starts))
        blocks.append(
            source_grid[
                row_start : row_start + block_size.rows,
                column_start : column_start + block_size.columns,
            ].reshape(-1)
        )
    return np.concatenate(blocks)[: grid.size]


def resample_spatial_blocks(
    values: NDArray[np.generic], *, block_size: BlockSize, rng: Generator
) -> SpatialResample:
    """Return one moving-block resample using only the caller-provided generator.

    The explicit generator is mandatory so experiment evidence can reproduce the
    exact selected windows without relying on process-global random state.
    """
    grid = _as_finite_grid(values)
    source_indices = _moving_block_indices(grid, block_size, rng)
    return SpatialResample(values=grid.reshape(-1)[source_indices], source_indices=source_indices)


def _statistic_value(statistic: Statistic, sample: FloatArray) -> float:
    """Turn a metric callback's scalar-like result into validated audit evidence."""
    value = float(statistic(sample))
    if not bool(np.isfinite(value)):
        raise ValueError("statistic must return a finite value")
    return value


def spatial_block_bootstrap(
    values: NDArray[np.generic], *, statistic: Statistic, config: SpatialBlockBootstrapConfig
) -> BootstrapResult:
    """Estimate a percentile interval by resampling contiguous moving spatial blocks.

    The observed statistic is deliberately included in the returned interval when
    a finite finite-sample percentile draw falls to one side. This makes the
    result a conservative promotion-gate summary and gives callers the useful
    invariant that the reported interval brackets the statistic it qualifies.
    """
    grid = _as_finite_grid(values)
    _validate_block_size(grid, config.block_size)
    _validate_config(config)
    rng = Generator(PCG64(config.seed))
    LOGGER.debug(
        "starting spatial block bootstrap",
        extra={
            "block_columns": config.block_size.columns,
            "block_rows": config.block_size.rows,
            "replicates": config.replicates,
            "seed": config.seed,
        },
    )
    observed = _statistic_value(statistic, grid.reshape(-1))
    replicates = np.empty(config.replicates, dtype=np.float64)
    for replicate_index in range(config.replicates):
        indices = _moving_block_indices(grid, config.block_size, rng)
        replicates[replicate_index] = _statistic_value(statistic, grid.reshape(-1)[indices])

    tail_probability = (1.0 - config.confidence_level) / 2.0
    lower = min(float(np.quantile(replicates, tail_probability)), observed)
    upper = max(float(np.quantile(replicates, 1.0 - tail_probability)), observed)
    return BootstrapResult(
        observed_statistic=observed,
        lower_bound=lower,
        upper_bound=upper,
        confidence_level=config.confidence_level,
        replicates=config.replicates,
        seed=config.seed,
    )
