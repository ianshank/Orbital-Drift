"""Configuration management for Orbital-Drift.

Adheres strictly to Constitution Principle III (No Hardcoded Values):
All configuration parameters (AOI geometries, band sets, cloud thresholds,
drift thresholds, hysteresis windows, endpoints, GPU devices, canary ratios)
are dynamically sourced from environment variables, `.env`, or Helm values via Pydantic Settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_BANDS: Final[tuple[str, ...]] = ("B02", "B03", "B04", "B08")
DEFAULT_BBOX: Final[tuple[float, float, float, float]] = (-122.5, 37.5, -122.0, 38.0)


class OrbitalDriftConfig(BaseSettings):
    """Central configuration for Orbital-Drift CT pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ORBITAL_DRIFT_",
        extra="ignore",
    )

    # --- AOI & Geospatial Ingestion ---
    aoi_name: str = Field(default="default-aoi", description="Identifier for target AOI")
    bbox: tuple[float, float, float, float] = Field(
        default=DEFAULT_BBOX,
        description="Bounding box [min_lon, min_lat, max_lon, max_lat]",
    )
    bands: tuple[str, ...] = Field(
        default=DEFAULT_BANDS,
        description="Sentinel-2 spectral bands to ingest",
    )
    cloud_cover_max_threshold: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Max acceptable cloud fraction before exclusion from training",
    )
    stac_api_url: str = Field(
        default="https://earth-search.aws.element84.com/v1",
        description="STAC endpoint for Sentinel-2 L2A collections",
    )
    stac_collection: str = Field(
        default="sentinel-2-l2a",
        description="Target STAC collection",
    )
    ingest_retry_budget: int = Field(
        default=3,
        ge=1,
        description="Max retries for STAC queries and COG fetches; a value of 0 "
        "would mean STACClient.search_scenes never attempts a request "
        "(see ingest/stac_client.py's `while attempt < self.retry_budget` loop)",
    )
    stac_backoff_factor: float = Field(
        default=1.5,
        gt=0.0,
        description="Exponential backoff base for STACClient retry sleeps "
        "(sleep_sec = backoff_factor ** attempt); mirrors the currently-hardcoded "
        "default in ingest/stac_client.py's STACClient.__init__. Not yet wired "
        "there -- a separate RB-010 part owns per-module config wiring.",
    )
    stac_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Per-request HTTP timeout for STACClient.search_scenes; mirrors "
        "the currently-hardcoded `timeout=30.0` in ingest/stac_client.py. Not yet "
        "wired there -- a separate RB-010 part owns per-module config wiring.",
    )

    # --- Storage & Data Versioning ---
    storage_backend: Literal["local", "s3", "lakefs"] = Field(
        default="local",
        description="Storage backend for raster tiles",
    )
    tile_store_path: Path = Field(
        default=Path("data/tiles"),
        description="Local directory or S3 bucket path for tile store",
    )
    lakefs_endpoint: str = Field(
        default="http://localhost:8000",
        description="lakeFS API endpoint",
    )
    lakefs_repository: str = Field(
        default="orbital-drift",
        description="lakeFS repository name",
    )
    lakefs_main_branch: str = Field(
        default="main",
        description="lakeFS base branch for training reference",
    )
    lakefs_access_key: str = Field(
        description="lakeFS API access key. Required, no default: a placeholder "
        "default here would let the app silently start with a fake, guessable "
        "'credential' instead of failing fast. Set via "
        "ORBITAL_DRIFT_LAKEFS_ACCESS_KEY or .env.",
    )
    lakefs_secret_key: str = Field(
        description="lakeFS API secret key. Required, no default: a placeholder "
        "default here would let the app silently start with a fake, guessable "
        "'credential' instead of failing fast. Set via "
        "ORBITAL_DRIFT_LAKEFS_SECRET_KEY or .env.",
    )

    # --- Hardware & GPU Allocation ---
    train_device: str = Field(
        default="cuda:0",
        description="PyTorch device for model training (e.g. cuda:0, cpu)",
    )
    serve_device: str = Field(
        default="cuda:1",
        description="PyTorch device for serving and inference (e.g. cuda:1, cpu)",
    )
    train_gpu_uuid: str = Field(
        default="",
        description="Physical GPU UUID for node A training GPU (RTX 5060 Ti 16GB)",
    )
    serve_gpu_uuid: str = Field(
        default="",
        description="Physical GPU UUID for node A serving GPU (RTX 5060 8GB)",
    )

    # --- Training & Experiment Tracking ---
    mlflow_tracking_uri: str = Field(
        default="http://localhost:5000",
        description="MLflow tracking server URI",
    )
    mlflow_experiment_name: str = Field(
        default="orbital-drift-segmentation",
        description="MLflow experiment name",
    )
    model_name: str = Field(
        default="orbital-drift-unet",
        description="Registered model name in MLflow",
    )
    batch_size: int = Field(
        default=16,
        ge=1,
        description="Batch size for training",
    )
    gradient_accumulation_steps: int = Field(
        default=2,
        ge=1,
        description="Gradient accumulation steps for 16GB VRAM constraint; a value "
        "of 0 divides by zero in train/baseline.py's `loss / grad_accum_steps` "
        "and its `% grad_accum_steps` step-boundary check",
    )
    learning_rate: float = Field(
        default=1e-4,
        description="Initial learning rate",
    )
    num_classes: int = Field(
        default=10,
        ge=1,
        description="Number of land-cover segmentation classes",
    )
    patch_size: int = Field(
        default=256,
        ge=1,
        description="Spatial patch dimension (pixels)",
    )
    dataset_normalize_max: float = Field(
        default=10000.0,
        gt=0.0,
        description="Maximum reflectance scaling factor for Sentinel2PatchDataset "
        "patch normalization (band DN / this value -> [0.0, 1.0]); mirrors the "
        "currently-hardcoded `normalize_max=10000.0` default (standard Sentinel-2 "
        "L2A surface-reflectance scale) in data/dataset.py. Not yet wired there -- "
        "a separate RB-010 part owns per-module config wiring.",
    )
    use_amp: bool = Field(
        default=True,
        description="Enable Automated Mixed Precision (AMP fp16)",
    )

    # --- Drift Monitoring & Trigger Engine ---
    psi_threshold: float = Field(
        default=0.25,
        ge=0.0,
        description="Population Stability Index threshold indicating significant drift",
    )
    psi_moderate_threshold: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Population Stability Index threshold indicating a moderate "
        "(sub-significant) shift, used alongside the KS p-value as a secondary "
        "drift signal. Named field for the bare `0.10` literal currently "
        "hardcoded at drift/metrics.py:99 "
        "(`is_drifted = (psi >= psi_threshold) or "
        "(psi >= 0.10 and ks_pval < ks_alpha)`). Not yet wired there -- that "
        "file is owned by a separate, concurrently-running RB-010 part; a later "
        "part wires this field in.",
    )
    ks_alpha: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Significance level for two-sample Kolmogorov-Smirnov test",
    )
    drift_hysteresis_window: int = Field(
        default=3,
        ge=1,
        description="Number of consecutive drifted scenes required before trigger; "
        "a value of 0 would mean the hysteresis check in "
        "DriftTriggerManager.process_scene_verdict never actually requires a "
        "drifted scene (see drift/trigger.py)",
    )
    drift_cooldown_scenes: int = Field(
        default=5,
        ge=1,
        description="Cooldown period in scenes to prevent trigger storms; a value "
        "of 0 defeats the cooldown's own purpose (see drift/trigger.py's "
        "`scenes_since_last_trigger < cooldown_scenes` check)",
    )

    # --- Serving & Canary Deployment ---
    canary_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Fraction of inference traffic routed to candidate Staging model (0.0 to 1.0)",
    )
    serving_port: int = Field(
        default=8080,
        description="FastAPI HTTP serving port",
    )
    auto_promote_margin: float = Field(
        default=0.02,
        ge=0.0,
        description="Required IoU improvement margin over Production to trigger auto-promotion",
    )

    @property
    def cloud_cover_threshold(self) -> float:
        """Alias for cloud_cover_max_threshold."""
        return self.cloud_cover_max_threshold

    @property
    def grad_accum_steps(self) -> int:
        """Alias for gradient_accumulation_steps."""
        return self.gradient_accumulation_steps

    @property
    def gpu_train_device(self) -> str:
        """Alias for train_device."""
        return self.train_device

    @property
    def gpu_serve_device(self) -> str:
        """Alias for serve_device."""
        return self.serve_device


def get_config(**overrides: object) -> OrbitalDriftConfig:
    """Instantiates and returns the validated application configuration."""
    return OrbitalDriftConfig(**overrides)  # type: ignore[arg-type]
