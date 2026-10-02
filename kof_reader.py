"""KOF point parser.

Parses KOF text files with block-prefixed rows and extracts point coordinates
from ``05`` coordinate rows. The parser supports both common variants:

1. ``05 DB Pkt Tk X Y H Bk Merk``
2. ``05 Pkt X Y H``

Only ``Pkt``, ``X``, ``Y``, and ``H`` are extracted.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point

from kofvision.models import KOFPoint
from kofvision.parser import parse_kof, parse_kof_coordinate_line

logger = logging.getLogger(__name__)

__all__ = ["KOFPoint", "parse_kof_coordinate_line", "read_kof_file", "save_kof_to_gpkg"]


def read_kof_file(file_path: str, *, crs: Optional[str] = None, encoding: str = "utf-8") -> gpd.GeoDataFrame:
    """Read a KOF file into a GeoDataFrame.

    Returns a GeoDataFrame with columns:
    - ``Pkt``
    - ``X``
    - ``Y``
    - ``H``
    - ``geometry`` (3D Point using X, Y, H)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"KOF file not found: {file_path}")

    parsed = parse_kof(file_path, encoding=encoding)
    points: list[KOFPoint] = parsed.all_points

    gdf = gpd.GeoDataFrame(
        {
            "Pkt": [p.pkt for p in points],
            "X": [p.x for p in points],
            "Y": [p.y for p in points],
            "H": [p.h for p in points],
        },
        geometry=[Point(p.x, p.y, p.h) for p in points],
        crs=crs,
    )
    return gdf


def save_kof_to_gpkg(
    kof_path: str,
    output_gpkg_path: str,
    *,
    layer_name: str = "kof_points",
    crs: Optional[str] = None,
    encoding: str = "utf-8",
) -> gpd.GeoDataFrame:
    """Read a KOF file and save the parsed points to a GeoPackage.

    Returns the GeoDataFrame that is written to disk.
    """
    gdf = read_kof_file(kof_path, crs=crs, encoding=encoding)

    output_dir = os.path.dirname(output_gpkg_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    gdf.to_file(output_gpkg_path, layer=layer_name, driver="GPKG")
    logger.info(
        "Saved %d KOF points to GeoPackage: %s (layer=%s)",
        len(gdf),
        output_gpkg_path,
        layer_name,
    )
    return gdf


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for KOF -> GeoPackage export."""
    parser = argparse.ArgumentParser(
        description="Read a KOF file and export parsed points to a GeoPackage."
    )
    parser.add_argument("--input", required=True, help="Path to input KOF file")
    parser.add_argument("--output", required=True, help="Path to output GeoPackage (.gpkg)")
    parser.add_argument("--layer", default="kof_points", help="Output GeoPackage layer name")
    parser.add_argument("--crs", default=None, help="CRS for output, e.g. EPSG:25832")
    parser.add_argument("--encoding", default="utf-8", help="Text encoding for input KOF file")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint for KOF -> GeoPackage export."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    gdf = save_kof_to_gpkg(
        args.input,
        args.output,
        layer_name=args.layer,
        crs=args.crs,
        encoding=args.encoding,
    )
    print(
        f"Saved {len(gdf)} points to {args.output} "
        f"(layer={args.layer})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
