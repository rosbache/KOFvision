from __future__ import annotations

from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import matplotlib.image as mpimg
import numpy as np
import pytest
from matplotlib.figure import Figure

from kofvision import wms
from kofvision.viewer_rendering import _draw_rasters


def test_validate_wms_config_trims_fields() -> None:
    config = wms.validate_wms_config(" https://maps.example/wms?map=base ", " topo ", " EPSG:25832 ")
    assert config == wms.WMSConfig(
        url="https://maps.example/wms?map=base",
        layer="topo",
        crs="EPSG:25832",
    )


@pytest.mark.parametrize(
    ("url", "layer", "crs"),
    [
        ("not a URL", "topo", "EPSG:25832"),
        ("https://maps.example/wms", "", "EPSG:25832"),
        ("https://maps.example/wms", "topo", ""),
    ],
)
def test_validate_wms_config_rejects_missing_or_invalid_values(url: str, layer: str, crs: str) -> None:
    with pytest.raises(ValueError):
        wms.validate_wms_config(url, layer, crs)


def test_fetch_wms_map_requests_extent_and_decodes_image(monkeypatch: pytest.MonkeyPatch) -> None:
    png = BytesIO()
    mpimg.imsave(png, np.zeros((2, 3, 4), dtype=np.uint8), format="png")
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return png.getvalue()

    def _urlopen(request, *, timeout: int) -> _Response:  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(wms, "urlopen", _urlopen)
    extent = (100.0, 200.0, 300.0, 400.0)
    result = wms.fetch_wms_map(
        wms.WMSConfig("https://maps.example/wms?token=test", "base:topo", "EPSG:25832"),
        extent,
        640,
        480,
    )

    request = captured["request"]
    query = parse_qs(urlsplit(request.full_url).query)
    assert query["token"] == ["test"]
    assert query["SERVICE"] == ["WMS"]
    assert query["VERSION"] == ["1.1.1"]
    assert query["REQUEST"] == ["GetMap"]
    assert query["LAYERS"] == ["base:topo"]
    assert query["SRS"] == ["EPSG:25832"]
    assert query["BBOX"] == ["100,200,300,400"]
    assert query["WIDTH"] == ["640"]
    assert query["HEIGHT"] == ["480"]
    assert captured["timeout"] == 20
    assert result.extent == extent
    assert result.image.shape == (2, 3, 4)


def test_fetch_wms_map_rejects_invalid_extent_and_dimensions() -> None:
    config = wms.WMSConfig("https://maps.example/wms", "topo", "EPSG:25832")
    with pytest.raises(ValueError, match="extent"):
        wms.fetch_wms_map(config, (1.0, 1.0, 0.0, 1.0), 256, 256)
    with pytest.raises(ValueError, match="dimensions"):
        wms.fetch_wms_map(config, (0.0, 1.0, 0.0, 1.0), 2049, 256)


def test_draw_rasters_places_wms_before_local_rasters() -> None:
    class _Var:
        def __init__(self, value) -> None:  # noqa: ANN001
            self.value = value

        def get(self):  # noqa: ANN201
            return self.value

    figure = Figure()
    axes = figure.add_subplot(111)
    viewer = type("Viewer", (), {})()
    viewer.ax2d = axes
    viewer._wms_map = wms.WMSMap(
        image=np.zeros((2, 2, 4)),
        extent=(0.0, 2.0, 0.0, 2.0),
    )
    viewer.wms_visible = _Var(True)
    viewer.show_raster = _Var(True)
    viewer._raster_layers = [
        type(
            "RasterLayer",
            (),
            {
                "visible": _Var(True),
                "image": np.ones((2, 2, 4)),
                "extent": (10.0, 12.0, 10.0, 12.0),
                "origin": "upper",
                "alpha": _Var(0.5),
            },
        )()
    ]

    _draw_rasters(viewer)

    assert len(axes.images) == 2
    assert axes.images[0].get_extent() == [0.0, 2.0, 0.0, 2.0]
    assert axes.images[1].get_extent() == [10.0, 12.0, 10.0, 12.0]
