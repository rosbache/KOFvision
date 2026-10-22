from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import matplotlib.image as mpimg
import numpy as np


@dataclass(frozen=True)
class WMSConfig:
    url: str
    layer: str
    crs: str


@dataclass(frozen=True)
class WMSMap:
    image: np.ndarray
    extent: tuple[float, float, float, float]


def validate_wms_config(url: str, layer: str, crs: str) -> WMSConfig:
    url = url.strip()
    layer = layer.strip()
    crs = crs.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("Enter a valid WMS service URL beginning with http:// or https://.")
    if not layer:
        raise ValueError("Enter the WMS layer name.")
    if not crs:
        raise ValueError("Enter the map CRS (for example, EPSG:25832).")
    return WMSConfig(url=url, layer=layer, crs=crs)


def fetch_wms_map(
    config: WMSConfig,
    extent: tuple[float, float, float, float],
    width: int,
    height: int,
) -> WMSMap:
    xmin, xmax, ymin, ymax = extent
    xmin, xmax = sorted((xmin, xmax))
    ymin, ymax = sorted((ymin, ymax))
    extent = (xmin, xmax, ymin, ymax)
    if not all(math.isfinite(value) for value in extent) or xmin >= xmax or ymin >= ymax:
        raise ValueError("WMS map extent must contain finite, increasing bounds.")
    if not (1 <= width <= 2048 and 1 <= height <= 2048):
        raise ValueError("WMS image dimensions must be between 1 and 2048 pixels.")

    parts = urlsplit(config.url)
    request_params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": config.layer,
        "STYLES": "",
        "SRS": config.crs,
        "BBOX": ",".join(f"{value:.10g}" for value in extent),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
    }
    replaced = set(request_params)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.upper() not in replaced
    ]
    query.extend(request_params.items())
    request_url = urlunsplit(parts._replace(query=urlencode(query)))

    request = Request(request_url, headers={"User-Agent": "KOFvision"})
    with urlopen(request, timeout=20) as response:
        image_data = response.read()
    image = np.asarray(mpimg.imread(BytesIO(image_data), format="png"))
    if image.ndim not in (2, 3) or image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError("WMS returned an empty map image.")
    return WMSMap(image=image, extent=extent)
