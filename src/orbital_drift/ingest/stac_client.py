"""Earth Search STAC API Client for Sentinel-2 L2A scene discovery.

Queries STAC collections for a configured AOI bounding box and date range,
with exponential backoff and retry budgeting.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

import requests

logger = logging.getLogger(__name__)

DEFAULT_STAC_URL: Final[str] = "https://earth-search.aws.element84.com/v1"
DEFAULT_COLLECTION: Final[str] = "sentinel-2-l2a"


class STACClient:
    """Client for Earth Search STAC queries."""

    def __init__(
        self,
        endpoint_url: str = DEFAULT_STAC_URL,
        collection: str = DEFAULT_COLLECTION,
        retry_budget: int = 3,
        backoff_factor: float = 1.5,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.collection = collection
        self.retry_budget = retry_budget
        self.backoff_factor = backoff_factor
        self.session = session or requests.Session()

    def search_scenes(
        self,
        bbox: tuple[float, float, float, float],
        date_range: str,
        max_cloud_cover: float = 100.0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Queries STAC API for Sentinel-2 scenes matching criteria.

        Args:
            bbox: Bounding box (min_lon, min_lat, max_lon, max_lat).
            date_range: ISO8601 interval string (e.g. '2026-08-01T00:00:00Z/2026-08-20T00:00:00Z').
            max_cloud_cover: Maximum scene-level cloud percentage (0 to 100).
            limit: Maximum number of scenes to return.

        Returns:
            List of STAC item feature dictionaries.
        """
        search_url = f"{self.endpoint_url}/search"
        payload: dict[str, Any] = {
            "collections": [self.collection],
            "bbox": list(bbox),
            "datetime": date_range,
            "query": {
                "eo:cloud_cover": {"lte": max_cloud_cover},
            },
            "limit": limit,
        }

        attempt = 0
        last_exception: Exception | None = None
        while attempt < self.retry_budget:
            try:
                response = self.session.post(search_url, json=payload, timeout=30.0)
                if response.status_code == 200:
                    data = response.json()
                    features: list[dict[str, Any]] = data.get("features", [])
                    logger.info("Discovered %d STAC scenes for bbox %s", len(features), bbox)
                    return features
                logger.warning(
                    "STAC query failed with status %d: %s (attempt %d/%d)",
                    response.status_code,
                    response.text,
                    attempt + 1,
                    self.retry_budget,
                )
            except requests.RequestException as exc:
                last_exception = exc
                logger.warning(
                    "STAC request exception: %s (attempt %d/%d)",
                    exc,
                    attempt + 1,
                    self.retry_budget,
                )

            attempt += 1
            if attempt < self.retry_budget:
                sleep_sec = self.backoff_factor**attempt
                time.sleep(sleep_sec)

        if last_exception:
            msg = f"STAC search failed after {self.retry_budget} attempts: {last_exception}"
            raise RuntimeError(msg)
        return []

    def get_band_asset_urls(
        self,
        stac_item: dict[str, Any],
        bands: tuple[str, ...] = ("B02", "B03", "B04", "B08", "SCL"),
    ) -> dict[str, str]:
        """Extracts download/read URLs for desired spectral bands from a STAC item."""
        assets = stac_item.get("assets", {})
        band_urls: dict[str, str] = {}
        for band in bands:
            # Check lowercase, uppercase, and common STAC asset keys
            red_cand = "red" if band == "B04" else ""
            nir_cand = "nir" if band == "B08" else ""
            candidates = [band, band.lower(), red_cand, nir_cand]
            found = False
            for cand in candidates:
                if cand and cand in assets and "href" in assets[cand]:
                    band_urls[band] = assets[cand]["href"]
                    found = True
                    break
            if not found:
                # Direct match fallback
                for asset_key, asset_val in assets.items():
                    if band.lower() in asset_key.lower() and "href" in asset_val:
                        band_urls[band] = asset_val["href"]
                        found = True
                        break
        return band_urls
