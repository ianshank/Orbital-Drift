"""Earth Search STAC API Client for Sentinel-2 L2A scene discovery.

Queries STAC collections for a configured AOI bounding box and date range,
with exponential backoff and retry budgeting.

Configuration wiring (RB-010 part 5, Constitution Principle III): every
tunable constructor/query argument below is sourced from
``orbital_drift.config.OrbitalDriftConfig`` when a config instance is passed
to :class:`STACClient`. Precedence is: an explicit argument always wins,
then a value read off ``config``, then a module-level default that mirrors
``OrbitalDriftConfig``'s own default -- so a caller that constructs
``STACClient()`` with neither an explicit argument nor ``config`` is
behavior-identical to this module before it was config-wired.

Unit-scale note (the defect this wiring had to avoid): ``OrbitalDriftConfig
.cloud_cover_max_threshold`` is a 0.0-1.0 FRACTION -- the scale shared with
``ingest/cloud.py`` and the drift module -- while Earth Search's STAC
``eo:cloud_cover`` query property follows the ``eo`` STAC extension
convention of a 0-100 PERCENT value (see the existing
``test_stac_client_query_contract`` contract test, which pins a
``max_cloud_cover=20.0`` argument reaching the payload as ``20.0``, not
``0.2``). :meth:`STACClient.search_scenes` therefore converts
``config.cloud_cover_max_threshold`` to percent (``* 100.0``) at the point it
substitutes for an omitted ``max_cloud_cover`` argument, rather than either
changing the STAC query's scale or passing the fraction through unconverted
-- the latter would silently request <=0.20% cloud cover instead of the
intended <=20%.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Final

import requests

from orbital_drift.config import OrbitalDriftConfig

logger = logging.getLogger(__name__)

DEFAULT_STAC_URL: Final[str] = "https://earth-search.aws.element84.com/v1"  # pin: fallback default
DEFAULT_COLLECTION: Final[str] = "sentinel-2-l2a"
DEFAULT_RETRY_BUDGET: Final[int] = 3  # pin: fallback default (config-wired below)
DEFAULT_BACKOFF_FACTOR: Final[float] = 1.5  # pin: fallback default (config-wired below)
DEFAULT_TIMEOUT_SECONDS: Final[float] = 30.0  # pin: fallback default (config-wired below)
DEFAULT_MAX_CLOUD_COVER_PERCENT: Final[float] = 100.0  # pin: "no filtering" sentinel, see docstring


def _resolve[T](explicit: T | None, from_config: T | None, default: T) -> T:
    """Resolves a config-sourced value: explicit argument > config > default.

    Shared precedence rule for every :class:`STACClient` constructor argument
    that can be sourced from :class:`OrbitalDriftConfig`. An explicitly
    supplied argument always wins over ``config``, which in turn wins over
    the module-level default (see module docstring for why the defaults must
    mirror ``OrbitalDriftConfig``'s own).
    """
    if explicit is not None:
        return explicit
    if from_config is not None:
        return from_config
    return default


class STACClient:
    """Client for Earth Search STAC queries."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        collection: str | None = None,
        retry_budget: int | None = None,
        backoff_factor: float | None = None,
        timeout: float | None = None,
        session: requests.Session | None = None,
        config: OrbitalDriftConfig | None = None,
    ) -> None:
        """Args:
        endpoint_url: STAC endpoint. Explicit value wins; else sourced from
            ``config.stac_api_url`` when ``config`` is given; else
            ``DEFAULT_STAC_URL``.
        collection: Target STAC collection. Explicit value wins; else
            sourced from ``config.stac_collection``; else
            ``DEFAULT_COLLECTION``.
        retry_budget: Max query attempts. Explicit value wins; else sourced
            from ``config.ingest_retry_budget``; else ``DEFAULT_RETRY_BUDGET``.
        backoff_factor: Exponential backoff base for retry sleeps. Explicit
            value wins; else sourced from ``config.stac_backoff_factor``;
            else ``DEFAULT_BACKOFF_FACTOR``.
        timeout: Per-request HTTP timeout in seconds passed to
            ``session.post``. Explicit value wins; else sourced from
            ``config.stac_request_timeout_seconds``; else
            ``DEFAULT_TIMEOUT_SECONDS``.
        session: Injected HTTP session (tests substitute a mock).
        config: Optional central configuration. Seeds any of the above left
            unset (``None``); also consulted by :meth:`search_scenes` for a
            ``max_cloud_cover`` default (see module docstring for the
            percent/fraction scale conversion).
        """
        self.config = config
        self.endpoint_url = _resolve(
            endpoint_url,
            config.stac_api_url if config is not None else None,
            DEFAULT_STAC_URL,
        ).rstrip("/")  # pin: trailing-slash normalization
        self.collection = _resolve(
            collection,
            config.stac_collection if config is not None else None,
            DEFAULT_COLLECTION,
        )
        self.retry_budget = _resolve(
            retry_budget,
            config.ingest_retry_budget if config is not None else None,
            DEFAULT_RETRY_BUDGET,
        )
        self.backoff_factor = _resolve(
            backoff_factor,
            config.stac_backoff_factor if config is not None else None,
            DEFAULT_BACKOFF_FACTOR,
        )
        self.timeout = _resolve(
            timeout,
            config.stac_request_timeout_seconds if config is not None else None,
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.session = session or requests.Session()

    def search_scenes(
        self,
        bbox: tuple[float, float, float, float],
        date_range: str,
        max_cloud_cover: float | None = None,
        limit: int = 10,  # pin: follow-up D-012 F5 (no config field for page size)
    ) -> list[dict[str, Any]]:
        """Queries STAC API for Sentinel-2 scenes matching criteria.

        Args:
            bbox: Bounding box (min_lon, min_lat, max_lon, max_lat).
            date_range: ISO8601 interval string (e.g. '2026-08-01T00:00:00Z/2026-08-20T00:00:00Z').
            max_cloud_cover: Maximum scene-level cloud percentage (0 to 100),
                matching Earth Search's ``eo:cloud_cover`` STAC query
                convention. When omitted (``None``) and this client was
                constructed with ``config``, this is derived from
                ``config.cloud_cover_max_threshold`` -- scaled from its
                native 0.0-1.0 fraction to the 0-100 percent this query
                expects (see module docstring). When omitted with no
                ``config``, defaults to ``DEFAULT_MAX_CLOUD_COVER_PERCENT``
                (100.0, i.e. no filtering) -- identical to this parameter's
                behavior before config wiring.
            limit: Maximum number of scenes to return.

        Returns:
            List of STAC item feature dictionaries.
        """
        if max_cloud_cover is None:
            if self.config is not None:
                # Scale conversion: config.cloud_cover_max_threshold is a
                # 0.0-1.0 fraction; Earth Search's eo:cloud_cover query
                # property is 0-100 percent. Passing the fraction through
                # unconverted would silently request <=0.20% cloud cover
                # instead of the intended <=20%.
                max_cloud_cover = (
                    self.config.cloud_cover_max_threshold * 100.0  # pin: fraction-to-percent
                )
            else:
                max_cloud_cover = DEFAULT_MAX_CLOUD_COVER_PERCENT

        search_url = f"{self.endpoint_url}/search"  # pin: STAC search endpoint path
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
                response = self.session.post(search_url, json=payload, timeout=self.timeout)
                if response.status_code == 200:  # pin: well-known HTTP status code (OK)
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
