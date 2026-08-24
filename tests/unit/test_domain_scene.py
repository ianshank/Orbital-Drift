"""Scene immutability, band selection, and catalog/tile port fake tests."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from orbital_drift.domain.errors import InvalidTemporalRangeError, MissingBandError
from orbital_drift.domain.geometry import BoundingBox, Tile
from orbital_drift.domain.scene import BandRef, SceneRef, select_bands
from orbital_drift.domain.temporal import TemporalRange
from orbital_drift.ports.catalog import InMemorySceneCatalog, SceneCatalogPort
from orbital_drift.ports.tiles import InMemoryTileStore, RasterPayload, TileStorePort


def _exact(message: str) -> str:
    """Build a regex that accepts one exact exception message."""
    return rf"^{re.escape(message)}$"


def _scene(scene_id: str = "scene-1", cloud_fraction: float = 0.2) -> SceneRef:
    return SceneRef(
        scene_id=scene_id,
        bounds=BoundingBox(0.0, 0.0, 2.0, 2.0),
        acquired_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        cloud_fraction=cloud_fraction,
        bands={"B02": BandRef("B02", "memory://blue"), "B03": BandRef("B03", "memory://green")},
    )


def test_scene_copies_and_exposes_a_genuinely_immutable_band_mapping() -> None:
    source = {"B02": BandRef("B02", "memory://blue")}
    scene = SceneRef(
        "scene-1", BoundingBox(0.0, 0.0, 1.0, 1.0), datetime(2026, 1, 1, tzinfo=UTC), 0.0, source
    )
    source["B03"] = BandRef("B03", "memory://green")

    assert dict(scene.bands) == {"B02": BandRef("B02", "memory://blue")}
    assert isinstance(scene.bands, MappingProxyType)
    with pytest.raises(TypeError, match="does not support item assignment"):
        scene.bands["B03"] = BandRef("B03", "memory://green")  # type: ignore[index]


def test_scene_validates_metadata_and_band_selection_lists_all_missing_names() -> None:
    scene = _scene()

    assert select_bands(scene, ("B03", "B02")) == {
        "B03": BandRef("B03", "memory://green"),
        "B02": BandRef("B02", "memory://blue"),
    }
    with pytest.raises(
        MissingBandError, match=_exact("scene 'scene-1' is missing bands: B01, B08")
    ):
        select_bands(scene, ("B01", "B08"))
    with pytest.raises(ValueError, match=_exact("scene_id must be non-empty")):
        SceneRef("", scene.bounds, scene.acquired_at, 0.0, {})
    with pytest.raises(
        InvalidTemporalRangeError, match=_exact("acquired_at must be timezone-aware")
    ):
        SceneRef("scene", scene.bounds, datetime(2026, 1, 1), 0.0, {})
    with pytest.raises(ValueError, match=_exact("cloud_fraction must be within [0.0, 1.0]")):
        SceneRef("scene", scene.bounds, scene.acquired_at, -0.01, {})
    with pytest.raises(ValueError, match=_exact("cloud_fraction must be within [0.0, 1.0]")):
        SceneRef("scene", scene.bounds, scene.acquired_at, 1.01, {})
    with pytest.raises(ValueError, match=_exact("band name must be non-empty")):
        BandRef("", "memory://band")
    with pytest.raises(ValueError, match=_exact("band href must be non-empty")):
        BandRef("B02", "")


def test_catalog_port_fake_runtime_protocol_and_filtering() -> None:
    clear = _scene("clear", 0.2)
    cloudy = _scene("cloudy", 0.8)
    catalog = InMemorySceneCatalog((clear, cloudy))
    when = TemporalRange(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))

    assert isinstance(catalog, SceneCatalogPort)
    assert catalog.search(BoundingBox(2.0, 1.0, 3.0, 2.0), when, 0.2, 5) == (clear,)
    assert catalog.search(BoundingBox(4.0, 4.0, 5.0, 5.0), when, 1.0, 5) == ()
    assert catalog.search(clear.bounds, when, 1.0, 0) == ()
    with pytest.raises(ValueError, match=_exact("limit must be non-negative")):
        catalog.search(clear.bounds, when, 1.0, -1)


def test_tile_port_fake_runtime_protocol_and_payload_validation() -> None:
    tile = Tile("tile-a", BoundingBox(0.0, 0.0, 1.0, 1.0), "EPSG:4326")
    payload = RasterPayload((2, 3), "uint16", memoryview(b"abc"))
    store = InMemoryTileStore()

    assert isinstance(store, TileStorePort)
    assert payload.buffer == b"abc"
    assert store.exists(tile) is False
    assert store.write(tile, payload) == "tile-a"
    assert store.exists(tile) is True
    assert store.read(tile) == payload
    assert store.list_tiles("tile") == ("tile-a",)
    with pytest.raises(KeyError, match=_exact("'tile not found: absent'")):
        store.read(Tile("absent", tile.bounds, tile.crs))
    with pytest.raises(
        ValueError, match=_exact("shape must contain at least one non-negative dimension")
    ):
        RasterPayload((), "uint8", b"")
    with pytest.raises(
        ValueError, match=_exact("shape must contain at least one non-negative dimension")
    ):
        RasterPayload((1, -1), "uint8", b"")
    with pytest.raises(ValueError, match=_exact("dtype must be non-empty")):
        RasterPayload((1,), "", b"")
