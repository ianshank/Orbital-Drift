"""Tile store for multi-spectral raster storage and windowed I/O.

Supports local filesystem and object store backends with read throughput telemetry.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class TileStore:
    """Manages storage and retrieval of multi-band Sentinel-2 raster patches."""

    def __init__(self, base_dir: str | Path = "data/tiles") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_scene(
        self,
        scene_id: str,
        bands_data: dict[str, np.ndarray],
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Saves a multi-band scene array and its metadata.

        Args:
            scene_id: Unique identifier for the scene.
            bands_data: Dictionary mapping band name (e.g. 'B02') to 2D numpy array.
            metadata: Additional metadata dictionary.

        Returns:
            Path to saved scene directory.
        """
        scene_dir = self.base_dir / scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        total_bytes = 0

        for band_name, array in bands_data.items():
            band_file = scene_dir / f"{band_name}.npy"
            np.save(band_file, array)
            total_bytes += array.nbytes

        meta_file = scene_dir / "metadata.json"
        meta_payload = metadata or {}
        meta_payload["scene_id"] = scene_id
        meta_payload["saved_at"] = time.time()
        meta_payload["bands"] = list(bands_data.keys())
        meta_file.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

        duration = time.perf_counter() - t0
        mb_written = total_bytes / (1024 * 1024)
        throughput = mb_written / duration if duration > 0 else 0.0
        logger.info(
            "Saved scene %s: %.2f MB in %.4fs (%.2f MB/s)",
            scene_id,
            mb_written,
            duration,
            throughput,
        )
        return scene_dir

    def load_scene(
        self,
        scene_id: str,
        bands: tuple[str, ...] = ("B02", "B03", "B04", "B08"),
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Loads spectral bands into a stacked (C, H, W) numpy array.

        Args:
            scene_id: Scene ID to load.
            bands: Ordered tuple of band names to load.

        Returns:
            Tuple of (C, H, W) array and metadata dict.
        """
        scene_dir = self.base_dir / scene_id
        if not scene_dir.exists():
            raise FileNotFoundError(f"Scene directory {scene_dir} not found")

        t0 = time.perf_counter()
        arrays: list[np.ndarray] = []
        total_bytes = 0

        for band in bands:
            band_file = scene_dir / f"{band}.npy"
            if not band_file.exists():
                raise FileNotFoundError(f"Band file {band_file} missing in scene {scene_id}")
            arr = np.load(band_file)
            arrays.append(arr)
            total_bytes += arr.nbytes

        meta_file = scene_dir / "metadata.json"
        metadata: dict[str, Any] = {}
        if meta_file.exists():
            metadata = json.loads(meta_file.read_text(encoding="utf-8"))

        stacked = np.stack(arrays, axis=0)
        duration = time.perf_counter() - t0
        mb_read = total_bytes / (1024 * 1024)
        throughput = mb_read / duration if duration > 0 else 0.0
        logger.info(
            "Loaded scene %s: %.2f MB in %.4fs (%.2f MB/s)",
            scene_id,
            mb_read,
            duration,
            throughput,
        )
        return stacked, metadata

    def list_scenes(self) -> list[str]:
        """Lists all scene IDs currently present in the tile store."""
        return [
            p.name for p in self.base_dir.iterdir() if p.is_dir() and (p / "metadata.json").exists()
        ]
