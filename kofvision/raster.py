from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from matplotlib import image as mpimg

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is typically present via matplotlib
    Image = None


@dataclass(frozen=True)
class RasterLayer:
    path: str
    image: np.ndarray
    extent: tuple[float, float, float, float]
    origin: str


def worldfile_candidates(raster_path: str) -> list[str]:
    base, ext = os.path.splitext(raster_path)
    ext_l = ext.lower()
    candidates: list[str] = []

    if ext_l in (".jpg", ".jpeg"):
        candidates.extend([base + ".jgw", base + ".jpgw", base + ".jpegw"])
    elif ext_l == ".png":
        candidates.extend([base + ".pgw", base + ".pngw"])
    elif ext_l in (".tif", ".tiff"):
        candidates.extend([base + ".tfw", base + ".tifw", base + ".tiffw"])
    elif ext_l == ".bmp":
        candidates.extend([base + ".bpw", base + ".bmpw"])

    candidates.append(base + ".wld")
    candidates.append(raster_path + "w")

    seen: set[str] = set()
    ordered: list[str] = []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            ordered.append(cand)
    return ordered


def parse_worldfile(path: str) -> tuple[float, float, float, float, float, float]:
    values: list[float] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            txt = raw.strip()
            if not txt:
                continue
            values.append(float(txt.replace(",", ".")))
    if len(values) < 6:
        raise ValueError("World file must contain at least 6 numeric lines.")
    return values[0], values[1], values[2], values[3], values[4], values[5]


def read_raster_for_display(path: str, max_dim: int = 3200) -> tuple[np.ndarray, int, int]:
    """Return display image plus original raster size (rows, cols)."""
    if Image is None:
        arr = mpimg.imread(path)
        if arr.ndim < 2:
            raise ValueError("Unsupported raster format.")
        rows, cols = int(arr.shape[0]), int(arr.shape[1])
        return arr, rows, cols

    old_max_pixels = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as src:
            cols, rows = src.size
            if rows < 1 or cols < 1:
                raise ValueError("Raster image is empty.")

            img = src
            largest = max(rows, cols)
            if largest > max_dim:
                scale = max_dim / float(largest)
                out_cols = max(int(round(cols * scale)), 1)
                out_rows = max(int(round(rows * scale)), 1)
                try:
                    img.draft("RGB", (out_cols, out_rows))
                except Exception:  # noqa: BLE001
                    pass
                img = img.resize((out_cols, out_rows))

            if img.mode not in ("L", "RGB", "RGBA"):
                img = img.convert("RGB")
            arr = np.asarray(img)
            return arr, rows, cols
    finally:
        Image.MAX_IMAGE_PIXELS = old_max_pixels


def load_raster_layer(path: str) -> RasterLayer:
    worldfile_path = None
    for candidate in worldfile_candidates(path):
        if os.path.exists(candidate):
            worldfile_path = candidate
            break

    if worldfile_path is None:
        raise FileNotFoundError(
            "No world file found for raster. Expected .jgw/.pgw/.tfw/.wld beside the image."
        )

    img, rows, cols = read_raster_for_display(path)
    a, d, b, e, c, f = parse_worldfile(worldfile_path)

    if abs(b) > 1e-12 or abs(d) > 1e-12:
        raise ValueError("Rotated world files are not supported (non-zero B/D terms).")

    if img.ndim < 2:
        raise ValueError("Unsupported raster format.")

    x0 = c - (a / 2.0)
    x1 = c + a * (cols - 0.5)
    y0 = f - (e / 2.0)
    y1 = f + e * (rows - 0.5)

    xmin, xmax = (x0, x1) if x0 <= x1 else (x1, x0)
    ymin, ymax = (y0, y1) if y0 <= y1 else (y1, y0)
    origin = "upper" if e < 0 else "lower"

    return RasterLayer(path=path, image=img, extent=(xmin, xmax, ymin, ymax), origin=origin)
