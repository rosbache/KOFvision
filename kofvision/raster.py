from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
from matplotlib import image as mpimg

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is typically present via matplotlib
    Image = None


_PIL_FAILURE_TYPES: tuple[type[BaseException], ...] = (MemoryError, OSError, ValueError)


@dataclass(frozen=True)
class RasterLayer:
    path: str
    image: np.ndarray
    extent: tuple[float, float, float, float]
    origin: str


@dataclass(frozen=True)
class ElevationRasterSurface:
    """Raster-backed elevation surface with bilinear interpolation in world XY."""

    name: str
    values: np.ndarray
    extent: tuple[float, float, float, float]
    origin: str

    def xy_bounds(self) -> Optional[tuple[float, float, float, float]]:
        xmin, xmax, ymin, ymax = self.extent
        # KOF convention: (y_min, x_min, y_max, x_max)
        return float(xmin), float(ymin), float(xmax), float(ymax)

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        ys = np.asarray(y, dtype=float)
        xs = np.asarray(x, dtype=float)
        out = np.full_like(ys, np.nan, dtype=float)

        if ys.shape != xs.shape:
            raise ValueError("y and x must have the same shape.")
        if self.values.ndim != 2:
            raise ValueError("Elevation raster must be a 2D array.")

        rows, cols = self.values.shape
        if rows < 2 or cols < 2:
            return out

        xmin, xmax, ymin, ymax = self.extent
        span_x = xmax - xmin
        span_y = ymax - ymin
        if span_x <= 0 or span_y <= 0:
            return out

        colf = (ys - xmin) / span_x * (cols - 1)
        if self.origin == "upper":
            rowf = (ymax - xs) / span_y * (rows - 1)
        else:
            rowf = (xs - ymin) / span_y * (rows - 1)

        inside = (
            np.isfinite(colf)
            & np.isfinite(rowf)
            & (colf >= 0.0)
            & (colf <= cols - 1)
            & (rowf >= 0.0)
            & (rowf <= rows - 1)
        )
        if not np.any(inside):
            return out

        ci = np.clip(colf[inside], 0.0, cols - 1)
        ri = np.clip(rowf[inside], 0.0, rows - 1)

        c0 = np.floor(ci).astype(int)
        r0 = np.floor(ri).astype(int)
        c1 = np.clip(c0 + 1, 0, cols - 1)
        r1 = np.clip(r0 + 1, 0, rows - 1)

        dc = ci - c0
        dr = ri - r0

        z00 = self.values[r0, c0]
        z10 = self.values[r0, c1]
        z01 = self.values[r1, c0]
        z11 = self.values[r1, c1]

        valid = np.isfinite(z00) & np.isfinite(z10) & np.isfinite(z01) & np.isfinite(z11)
        if np.any(valid):
            z = (
                z00[valid] * (1.0 - dc[valid]) * (1.0 - dr[valid])
                + z10[valid] * dc[valid] * (1.0 - dr[valid])
                + z01[valid] * (1.0 - dc[valid]) * dr[valid]
                + z11[valid] * dc[valid] * dr[valid]
            )
            idx = np.flatnonzero(inside)[valid]
            out[idx] = z

        return out


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


def _extent_from_worldfile(
    rows: int,
    cols: int,
    a: float,
    e: float,
    c: float,
    f: float,
) -> tuple[float, float, float, float, str]:
    x0 = c - (a / 2.0)
    x1 = c + a * (cols - 0.5)
    y0 = f - (e / 2.0)
    y1 = f + e * (rows - 0.5)

    xmin, xmax = (x0, x1) if x0 <= x1 else (x1, x0)
    ymin, ymax = (y0, y1) if y0 <= y1 else (y1, y0)
    origin = "upper" if e < 0 else "lower"
    return xmin, xmax, ymin, ymax, origin


def _compute_clip_window(
    rows: int,
    cols: int,
    a: float,
    e: float,
    c: float,
    f: float,
    clip_bounds: tuple[float, float, float, float],
    buffer_m: float,
) -> Optional[tuple[int, int, int, int]]:
    y_min, x_min, y_max, x_max = clip_bounds
    req_xmin = min(y_min, y_max) - buffer_m
    req_xmax = max(y_min, y_max) + buffer_m
    req_ymin = min(x_min, x_max) - buffer_m
    req_ymax = max(x_min, x_max) + buffer_m

    x0 = c - (a / 2.0)
    y0 = f - (e / 2.0)

    if abs(a) < 1e-15 or abs(e) < 1e-15:
        return None

    c1 = (req_xmin - x0) / a
    c2 = (req_xmax - x0) / a
    r1 = (req_ymin - y0) / e
    r2 = (req_ymax - y0) / e

    col_start = int(np.floor(min(c1, c2)))
    col_end = int(np.ceil(max(c1, c2)))
    row_start = int(np.floor(min(r1, r2)))
    row_end = int(np.ceil(max(r1, r2)))

    col_start = max(col_start, 0)
    row_start = max(row_start, 0)
    col_end = min(col_end, cols)
    row_end = min(row_end, rows)

    if row_end <= row_start or col_end <= col_start:
        return None
    return row_start, row_end, col_start, col_end


def _windowed_worldfile_ref(
    a: float,
    e: float,
    c: float,
    f: float,
    row_start: int,
    col_start: int,
) -> tuple[float, float]:
    c_win = c + a * col_start
    f_win = f + e * row_start
    return c_win, f_win


def _read_full_or_windowed_array(
    path: str,
    *,
    pixel_window: Optional[tuple[int, int, int, int]] = None,
    max_dim: Optional[int] = None,
) -> tuple[np.ndarray, int, int]:
    """Read raster as ndarray with optional crop and optional downsample."""
    if Image is None:
        arr = mpimg.imread(path)
        if arr.ndim < 2:
            raise ValueError("Unsupported raster format.")
        if pixel_window is not None:
            r0, r1, c0, c1 = pixel_window
            arr = arr[r0:r1, c0:c1]
        rows, cols = int(arr.shape[0]), int(arr.shape[1])
        return arr, rows, cols

    old_max_pixels = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as src:
            img = src
            if pixel_window is not None:
                r0, r1, c0, c1 = pixel_window
                img = src.crop((c0, r0, c1, r1))

            cols, rows = img.size
            if rows < 1 or cols < 1:
                raise ValueError("Raster image is empty.")

            if max_dim is not None:
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

            if img.mode not in ("L", "I", "F", "RGB", "RGBA"):
                img = img.convert("RGB")
            arr = np.asarray(img)
            return arr, rows, cols
    finally:
        Image.MAX_IMAGE_PIXELS = old_max_pixels


def read_raster_for_display(path: str, max_dim: int = 3200) -> tuple[np.ndarray, int, int]:
    """Return display image plus original raster size (rows, cols)."""
    return _read_full_or_windowed_array(path, max_dim=max_dim)


def _find_worldfile(path: str) -> str:
    worldfile_path = None
    for candidate in worldfile_candidates(path):
        if os.path.exists(candidate):
            worldfile_path = candidate
            break
    if worldfile_path is None:
        raise FileNotFoundError(
            "No world file found for raster. Expected .jgw/.pgw/.tfw/.wld beside the image."
        )
    return worldfile_path


def _read_raster_dimensions(path: str) -> tuple[int, int]:
    if Image is not None:
        with Image.open(path) as src:
            cols, rows = src.size
            return rows, cols
    arr = mpimg.imread(path)
    if arr.ndim < 2:
        raise ValueError("Unsupported raster format.")
    return int(arr.shape[0]), int(arr.shape[1])


def _load_raster_layer_internal(
    path: str,
    *,
    clip_bounds: Optional[tuple[float, float, float, float]] = None,
    clip_buffer_m: float = 10.0,
    max_dim: Optional[int] = 3200,
) -> RasterLayer:
    worldfile_path = _find_worldfile(path)
    a, d, b, e, c, f = parse_worldfile(worldfile_path)

    if abs(b) > 1e-12 or abs(d) > 1e-12:
        raise ValueError("Rotated world files are not supported (non-zero B/D terms).")

    full_rows, full_cols = _read_raster_dimensions(path)
    window = None
    c_ref = c
    f_ref = f
    if clip_bounds is not None:
        window = _compute_clip_window(full_rows, full_cols, a, e, c, f, clip_bounds, clip_buffer_m)
        if window is not None:
            r0, _r1, c0, _c1 = window
            c_ref, f_ref = _windowed_worldfile_ref(a, e, c, f, r0, c0)

    img, rows, cols = _read_full_or_windowed_array(path, pixel_window=window, max_dim=max_dim)
    if img.ndim < 2:
        raise ValueError("Unsupported raster format.")

    xmin, xmax, ymin, ymax, origin = _extent_from_worldfile(rows, cols, a, e, c_ref, f_ref)
    return RasterLayer(path=path, image=img, extent=(xmin, xmax, ymin, ymax), origin=origin)


def load_raster_layer(
    path: str,
    *,
    clip_bounds: Optional[tuple[float, float, float, float]] = None,
    clip_buffer_m: float = 10.0,
    prefer_clip: bool = False,
) -> RasterLayer:
    """Load display raster, optionally clipped to KOF bounds plus buffer."""
    if prefer_clip and clip_bounds is not None:
        return _load_raster_layer_internal(
            path,
            clip_bounds=clip_bounds,
            clip_buffer_m=clip_buffer_m,
        )

    try:
        return _load_raster_layer_internal(path)
    except _PIL_FAILURE_TYPES:
        if clip_bounds is None:
            raise
        return _load_raster_layer_internal(
            path,
            clip_bounds=clip_bounds,
            clip_buffer_m=clip_buffer_m,
        )


def load_elevation_surface(
    path: str,
    *,
    clip_bounds: Optional[tuple[float, float, float, float]] = None,
    clip_buffer_m: float = 10.0,
    prefer_clip: bool = False,
) -> ElevationRasterSurface:
    """Load a single-band elevation raster as an interpolable surface."""
    if prefer_clip and clip_bounds is not None:
        layer = _load_raster_layer_internal(
            path,
            clip_bounds=clip_bounds,
            clip_buffer_m=clip_buffer_m,
            max_dim=None,
        )
    else:
        try:
            layer = _load_raster_layer_internal(path, max_dim=None)
        except _PIL_FAILURE_TYPES:
            if clip_bounds is None:
                raise
            layer = _load_raster_layer_internal(
                path,
                clip_bounds=clip_bounds,
                clip_buffer_m=clip_buffer_m,
                max_dim=None,
            )
    arr = np.asarray(layer.image)
    if arr.ndim != 2:
        raise ValueError("Elevation raster must be single-band (one channel).")

    values = arr.astype(float, copy=False)
    return ElevationRasterSurface(
        name=os.path.basename(path),
        values=values,
        extent=layer.extent,
        origin=layer.origin,
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
