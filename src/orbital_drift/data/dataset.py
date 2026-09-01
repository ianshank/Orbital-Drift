"""Multi-spectral PyTorch Dataset for Sentinel-2 land-cover segmentation.

Generates normalized multi-spectral tensor patches (C x H x W)
with land-cover classification targets.

Configuration wiring (RB-010 part 5, Constitution Principle III):
``normalize_max`` is sourced from
``orbital_drift.config.OrbitalDriftConfig.dataset_normalize_max`` when a
config instance is passed to :class:`Sentinel2PatchDataset`. Precedence is:
an explicit argument always wins, then a value read off ``config``, then
``DEFAULT_NORMALIZE_MAX`` (which mirrors ``OrbitalDriftConfig``'s own
default) -- so a caller that passes neither sees identical behavior to
before this module was config-wired.
"""

from __future__ import annotations

from typing import Final

import numpy as np
import torch
from torch.utils.data import Dataset

from orbital_drift.config import OrbitalDriftConfig

DEFAULT_NORMALIZE_MAX: Final[float] = 10000.0


class Sentinel2PatchDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Slices multi-spectral raster cubes into fixed-size patch tensors."""

    def __init__(
        self,
        raster_data: np.ndarray,
        labels: np.ndarray | None = None,
        patch_size: int = 256,
        stride: int = 256,
        normalize_max: float | None = None,
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Args:
        raster_data: (C, H, W) numpy array of Sentinel-2 surface reflectance bands.
        labels: (H, W) numpy array of land cover classes (or None for inference).
        patch_size: Square patch size in pixels.
        stride: Stride between extracted patches.
        normalize_max: Maximum reflectance scaling factor (standard
            Sentinel-2 L2A is 10000). Explicit value wins; else sourced from
            ``config.dataset_normalize_max`` when ``config`` is given; else
            ``DEFAULT_NORMALIZE_MAX``.
        config: Optional central configuration; see ``normalize_max`` above.
        """
        super().__init__()
        self.raster_data = raster_data
        self.labels = labels
        self.patch_size = patch_size
        self.stride = stride
        self.normalize_max = (
            normalize_max
            if normalize_max is not None
            else (config.dataset_normalize_max if config is not None else DEFAULT_NORMALIZE_MAX)
        )

        _c, h, w = raster_data.shape
        self.patches: list[tuple[int, int]] = []
        for y in range(0, h - patch_size + 1, stride):
            for x in range(0, w - patch_size + 1, stride):
                self.patches.append((y, x))

        if not self.patches:
            # Handle smaller images by taking a single center crop or resizing
            self.patches.append((0, 0))

    def __len__(self) -> int:
        return len(self.patches)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        y, x = self.patches[idx]
        p = self.patch_size

        img_patch = self.raster_data[:, y : y + p, x : x + p]

        # Handle edge boundary padding if image is smaller than patch_size
        c, h, w = img_patch.shape
        if h < p or w < p:
            padded_img = np.zeros((c, p, p), dtype=img_patch.dtype)
            padded_img[:, :h, :w] = img_patch
            img_patch = padded_img

        # Normalize to [0.0, 1.0]
        normalized = np.clip(img_patch.astype(np.float32) / self.normalize_max, 0.0, 1.0)
        img_tensor = torch.from_numpy(normalized)

        if self.labels is not None:
            lbl_patch = self.labels[y : y + p, x : x + p]
            lh, lw = lbl_patch.shape
            if lh < p or lw < p:
                padded_lbl = np.zeros((p, p), dtype=lbl_patch.dtype)
                padded_lbl[:lh, :lw] = lbl_patch
                lbl_patch = padded_lbl
            lbl_tensor = torch.from_numpy(lbl_patch.astype(np.int64))
        else:
            lbl_tensor = torch.zeros((p, p), dtype=torch.int64)

        return img_tensor, lbl_tensor
