"""Moran's I spatial-autocorrelation adapter backed by PySAL's ``esda``.

Moran (1950), https://doi.org/10.2307/2332142, introduced the global statistic.
The implementation is :class:`esda.Moran` as documented at
https://pysal.org/esda/generated/esda.Moran.html and explained in the PySAL
textbook chapter https://geographicdata.science/book/notebooks/06_spatial_autocorrelation.html.

Weights are row-standardised before inference. This makes each observation's
neighbours sum to one, so locations with more neighbours do not receive greater
aggregate influence solely because of the weights topology; it also makes the
specified weights transformation explicit because Moran's I depends on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from unittest.mock import patch

import numpy as np
from esda import Moran
from libpysal.weights import W
from numpy.random import PCG64, Generator
from numpy.typing import NDArray

from orbital_drift.observability.logging import get_logger

ROW_STANDARDISATION: Final[str] = "r"
"""PySAL's row-standardisation code, required for comparable neighbour influence."""

type FloatArray = NDArray[np.float64]
LOGGER = get_logger("eval.spatial")


@dataclass(frozen=True)
class MoranConfig:
    """Required permutation policy and seed for reproducible spatial inference."""

    permutations: int
    seed: int


@dataclass(frozen=True)
class MoranResult:
    """Moran's observed value, theoretical moments, and permutation inference."""

    statistic: float
    expected_value: float
    variance: float
    pseudo_p_value: float
    permutations: int
    seed: int


def _finite_vector(values: NDArray[np.generic]) -> FloatArray:
    """Reject inputs for which spatial randomisation inference has no defined meaning."""
    vector = np.asarray(values, dtype=np.float64).reshape(-1)
    if vector.size == 0:
        raise ValueError("values must not be empty")
    if not bool(np.all(np.isfinite(vector))):
        raise ValueError("values must contain only finite numbers")
    return vector


def morans_i(values: NDArray[np.generic], *, weights: W, config: MoranConfig) -> MoranResult:
    """Compute row-standardised Moran's I and ``esda``'s permutation pseudo-p-value.

    ``esda.Moran`` currently reads ``numpy.random.permutation`` internally. The
    narrow patch below supplies the caller's seeded generator only for this call,
    preserving PySAL's established statistic and inference implementation while
    avoiding process-global random state and making every permutation repeatable.
    """
    vector = _finite_vector(values)
    if config.permutations <= 0:
        raise ValueError("permutations must be positive")
    if weights.n != vector.size:
        raise ValueError("weights observation count must equal values length")

    rng = Generator(PCG64(config.seed))
    LOGGER.debug(
        "starting Moran permutation inference",
        extra={"permutations": config.permutations, "seed": config.seed},
    )
    with patch("esda.moran.np.random.permutation", new=rng.permutation):
        statistic = Moran(
            vector,
            weights,
            transformation=ROW_STANDARDISATION,
            permutations=config.permutations,
        )
    return MoranResult(
        statistic=float(statistic.I),
        expected_value=float(statistic.EI),
        variance=float(statistic.VI_norm),
        pseudo_p_value=float(statistic.p_sim),
        permutations=config.permutations,
        seed=config.seed,
    )
