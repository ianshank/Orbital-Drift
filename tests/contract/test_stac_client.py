"""Contract tests for Earth Search STAC API Client (Constitution Principle V).

Validates that STACClient conforms to the expected query contract, handles backoff retry,
and accurately extracts band asset URLs.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from orbital_drift.ingest.stac_client import STACClient


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
