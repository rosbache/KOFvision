from __future__ import annotations

from kofvision.validation import safe_choice, safe_float


def test_safe_float_clamps_and_defaults() -> None:
    assert safe_float("bad", default=2.0) == 2.0
    assert safe_float(5.0, default=1.0, min_value=0.0, max_value=3.0) == 3.0
    assert safe_float(-5.0, default=1.0, min_value=0.0) == 0.0


def test_safe_choice_returns_allowed_or_default() -> None:
    assert safe_choice("raster", allowed={"raster", "triangles"}, default="raster") == "raster"
    assert safe_choice("other", allowed={"raster", "triangles"}, default="raster") == "raster"
