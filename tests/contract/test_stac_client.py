"""Contract tests for Earth Search STAC API Client (Constitution Principle V).

Validates that STACClient conforms to the expected query contract, handles backoff retry,
and accurately extracts band asset URLs.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from orbital_drift.config import OrbitalDriftConfig
from orbital_drift.ingest.stac_client import STACClient

# Not real credentials -- fixed test doubles for OrbitalDriftConfig's
# required lakeFS fields, matching tests/unit/test_config.py's convention so
# every test below can build a valid config without separately re-deriving
# the missing-credential defect that module covers.
_TEST_ACCESS_KEY = "unit-test-access-value"
_TEST_SECRET_KEY = "unit-test-secret-value"  # noqa: S105 -- test double, not a real secret


class MockSTACResponse:
    def __init__(self, status_code: int, data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._data = data
        self.text = "mock text"

    def json(self) -> dict[str, Any]:
        return self._data


class MockSession:
    def __init__(self, responses: list[MockSTACResponse | requests.RequestException]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        json: dict[str, Any],
        timeout: float = 30.0,
    ) -> MockSTACResponse:
        self.calls.append({"url": url, "payload": json, "timeout": timeout})
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, requests.RequestException):
                raise response
            return response
        return MockSTACResponse(200, {"features": []})


@pytest.mark.contract
def test_stac_client_query_contract() -> None:
    """Probes STAC search parameter payload construction."""
    sample_feature = {
        "id": "S2A_MSIL2A_20260810T184921",
        "assets": {
            "B02": {"href": "https://sentinel-cogs.s3.amazonaws.com/B02.tif"},
            "B03": {"href": "https://sentinel-cogs.s3.amazonaws.com/B03.tif"},
            "B04": {"href": "https://sentinel-cogs.s3.amazonaws.com/B04.tif"},
            "B08": {"href": "https://sentinel-cogs.s3.amazonaws.com/B08.tif"},
            "SCL": {"href": "https://sentinel-cogs.s3.amazonaws.com/SCL.tif"},
        },
    }
    mock_session = MockSession([MockSTACResponse(200, {"features": [sample_feature]})])
    client = STACClient(session=mock_session)  # type: ignore[arg-type]

    bbox = (-122.5, 37.5, -122.0, 38.0)
    date_range = "2026-08-01T00:00:00Z/2026-08-20T00:00:00Z"
    features = client.search_scenes(bbox=bbox, date_range=date_range, max_cloud_cover=20.0)

    assert len(features) == 1
    assert features[0]["id"] == "S2A_MSIL2A_20260810T184921"
    assert len(mock_session.calls) == 1

    payload = mock_session.calls[0]["payload"]
    assert payload["bbox"] == [-122.5, 37.5, -122.0, 38.0]
    assert payload["datetime"] == date_range
    assert payload["query"]["eo:cloud_cover"]["lte"] == 20.0


@pytest.mark.contract
def test_stac_client_asset_url_extraction() -> None:
    """Probes extraction of spectral band URLs from STAC item assets."""
    client = STACClient()
    stac_item = {
        "assets": {
            "b02": {"href": "s3://bucket/b02.tif"},
            "B03": {"href": "s3://bucket/b03.tif"},
            "red": {"href": "s3://bucket/red.tif"},
            "B08": {"href": "s3://bucket/b08.tif"},
            "scl": {"href": "s3://bucket/scl.tif"},
        }
    }
    urls = client.get_band_asset_urls(stac_item, bands=("B02", "B03", "B04", "B08", "SCL"))
    assert urls["B02"] == "s3://bucket/b02.tif"
    assert urls["B03"] == "s3://bucket/b03.tif"
    assert urls["B04"] == "s3://bucket/red.tif"
    assert urls["B08"] == "s3://bucket/b08.tif"
    assert urls["SCL"] == "s3://bucket/scl.tif"


@pytest.mark.contract
def test_stac_client_retry_and_backoff() -> None:
    """Probes retry behavior when encountering transient errors."""
    mock_session = MockSession(
        [
            MockSTACResponse(500, {}),
            MockSTACResponse(200, {"features": [{"id": "recovered"}]}),
        ]
    )
    client = STACClient(
        retry_budget=2,
        backoff_factor=0.01,
        session=mock_session,  # type: ignore[arg-type]
    )
    features = client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )
    assert len(features) == 1
    assert features[0]["id"] == "recovered"
    assert len(mock_session.calls) == 2


@pytest.mark.contract
def test_stac_client_raises_after_timeout() -> None:
    """Returns a terminal error when the retry budget is exhausted by a timeout."""
    mock_session = MockSession([requests.Timeout("request exceeded 30 seconds")])
    client = STACClient(retry_budget=1, session=mock_session)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=r"failed after 1 attempts.*request exceeded 30 seconds"):
        client.search_scenes(
            bbox=(-122.5, 37.5, -122.0, 38.0),
            date_range="2026-08-01/2026-08-02",
        )


@pytest.mark.contract
def test_stac_client_raises_after_connection_error() -> None:
    """Returns a terminal error when a connection cannot be established."""
    mock_session = MockSession([requests.ConnectionError("service unavailable")])
    client = STACClient(retry_budget=1, session=mock_session)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=r"failed after 1 attempts.*service unavailable"):
        client.search_scenes(
            bbox=(-122.5, 37.5, -122.0, 38.0),
            date_range="2026-08-01/2026-08-02",
        )


@pytest.mark.contract
def test_stac_client_returns_empty_list_after_terminal_http_failure() -> None:
    """Returns no scenes when every HTTP response is non-successful."""
    mock_session = MockSession([MockSTACResponse(503, {})])
    client = STACClient(retry_budget=1, session=mock_session)  # type: ignore[arg-type]

    scenes = client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    assert scenes == []
    assert mock_session.calls[0]["url"] == "https://earth-search.aws.element84.com/v1/search"


@pytest.mark.contract
def test_stac_client_returns_empty_list_for_response_without_features() -> None:
    """Treats a valid JSON object without the expected features field as empty."""
    mock_session = MockSession([MockSTACResponse(200, {"type": "FeatureCollection"})])
    client = STACClient(session=mock_session)  # type: ignore[arg-type]

    scenes = client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    assert scenes == []


@pytest.mark.contract
def test_stac_client_returns_empty_features_list_unchanged() -> None:
    """Preserves an explicitly empty STAC features list."""
    mock_session = MockSession([MockSTACResponse(200, {"features": []})])
    client = STACClient(session=mock_session)  # type: ignore[arg-type]

    scenes = client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    assert scenes == []


@pytest.mark.contract
def test_stac_client_extracts_band_url_from_partial_asset_name() -> None:
    """Finds a requested band when only a descriptive asset key is available."""
    client = STACClient()

    urls = client.get_band_asset_urls(
        {"assets": {"sentinel_band_b11": {"href": "s3://bucket/b11.tif"}}},
        bands=("B11",),
    )

    assert urls == {"B11": "s3://bucket/b11.tif"}


# -----------------------------------------------------------------------------
# RB-010 part 5: per-module config wiring (Constitution Principle III)
# -----------------------------------------------------------------------------


@pytest.mark.contract
def test_stac_client_constructor_sources_defaults_from_config() -> None:
    """Probes that endpoint/collection/retry/backoff/timeout are sourced from
    `OrbitalDriftConfig` when the client is constructed with one and no
    explicit constructor argument overrides them.
    """
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        stac_api_url="https://example-stac.test/v1",
        stac_collection="custom-collection",
        ingest_retry_budget=7,
        stac_backoff_factor=2.5,
        stac_request_timeout_seconds=12.5,
    )

    client = STACClient(config=cfg)

    assert client.endpoint_url == "https://example-stac.test/v1"
    assert client.collection == "custom-collection"
    assert client.retry_budget == 7
    assert client.backoff_factor == 2.5
    assert client.timeout == 12.5


@pytest.mark.contract
def test_stac_client_explicit_constructor_args_override_config() -> None:
    """An explicit constructor argument always wins over a passed `config`."""
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        stac_api_url="https://example-stac.test/v1",
        ingest_retry_budget=7,
    )

    client = STACClient(endpoint_url="https://override.test/v1", retry_budget=1, config=cfg)

    assert client.endpoint_url == "https://override.test/v1"
    assert client.retry_budget == 1


@pytest.mark.contract
def test_stac_client_no_config_matches_pre_wiring_defaults() -> None:
    """A client built with neither an explicit argument nor `config` is
    unchanged from this module's behavior before config wiring.
    """
    client = STACClient()

    assert client.endpoint_url == "https://earth-search.aws.element84.com/v1"
    assert client.collection == "sentinel-2-l2a"
    assert client.retry_budget == 3
    assert client.backoff_factor == 1.5
    assert client.timeout == 30.0


@pytest.mark.contract
def test_stac_client_config_sourced_timeout_reaches_session_post() -> None:
    """Probes that the config-sourced timeout is actually passed to the HTTP call."""
    mock_session = MockSession([MockSTACResponse(200, {"features": []})])
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        stac_request_timeout_seconds=5.0,
    )
    client = STACClient(session=mock_session, config=cfg)  # type: ignore[arg-type]

    client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    assert mock_session.calls[0]["timeout"] == 5.0


@pytest.mark.contract
def test_stac_client_search_scenes_converts_config_cloud_fraction_to_percent() -> None:
    """Pins the cloud-cover unit-scale conversion (RB-010 part 5).

    `OrbitalDriftConfig.cloud_cover_max_threshold` is a 0.0-1.0 fraction;
    Earth Search's STAC `eo:cloud_cover` query property is a 0-100 percent
    value (see `test_stac_client_query_contract` above, which pins
    `max_cloud_cover=20.0` reaching the payload as `20.0`). Wiring the
    fraction through unconverted would silently request <=0.20% cloud cover
    instead of the intended <=20% -- this test pins the exact numeric value
    reaching the query payload, not merely that config wiring "happened".
    """
    mock_session = MockSession([MockSTACResponse(200, {"features": []})])
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        cloud_cover_max_threshold=0.35,
    )
    client = STACClient(session=mock_session, config=cfg)  # type: ignore[arg-type]

    client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    lte = mock_session.calls[0]["payload"]["query"]["eo:cloud_cover"]["lte"]
    assert lte == pytest.approx(35.0)
    # Explicit guard against the silent-scale defect: the raw fraction must
    # never reach the query unconverted.
    assert lte != pytest.approx(0.35)


@pytest.mark.contract
def test_stac_client_explicit_max_cloud_cover_overrides_config() -> None:
    """An explicit `max_cloud_cover` argument always wins over a passed `config`."""
    mock_session = MockSession([MockSTACResponse(200, {"features": []})])
    cfg = OrbitalDriftConfig(
        lakefs_access_key=_TEST_ACCESS_KEY,
        lakefs_secret_key=_TEST_SECRET_KEY,
        cloud_cover_max_threshold=0.35,
    )
    client = STACClient(session=mock_session, config=cfg)  # type: ignore[arg-type]

    client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
        max_cloud_cover=42.0,
    )

    lte = mock_session.calls[0]["payload"]["query"]["eo:cloud_cover"]["lte"]
    assert lte == pytest.approx(42.0)


@pytest.mark.contract
def test_stac_client_search_scenes_without_config_keeps_no_filtering_default() -> None:
    """No config, no explicit `max_cloud_cover` -> unchanged pre-wiring default (100.0)."""
    mock_session = MockSession([MockSTACResponse(200, {"features": []})])
    client = STACClient(session=mock_session)  # type: ignore[arg-type]

    client.search_scenes(
        bbox=(-122.5, 37.5, -122.0, 38.0),
        date_range="2026-08-01/2026-08-02",
    )

    lte = mock_session.calls[0]["payload"]["query"]["eo:cloud_cover"]["lte"]
    assert lte == pytest.approx(100.0)
