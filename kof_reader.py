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
import re
from dataclasses import dataclass
from typing import Iterable, Optional

import geopandas as gpd
from shapely.geometry import Point

logger = logging.getLogger(__name__)

__all__ = ["KOFPoint", "parse_kof_coordinate_line", "read_kof_file", "save_kof_to_gpkg"]


@dataclass(frozen=True)
class KOFPoint:
    """Parsed KOF point record.

    Attributes:
        pkt: Point identifier.
        x: X coordinate.
        y: Y coordinate.
        h: Height/elevation.
        line_number: Source line number in the input file.
    """

    pkt: str
    x: float
    y: float
    h: float
    line_number: int


def _split_tokens(line: str) -> list[str]:
    """Split a KOF line into tokens.

    Prefer tab-delimited parsing, but fall back to whitespace splitting.
    """
    stripped = line.strip()
    if not stripped:
        return []

    tab_tokens = [part.strip() for part in stripped.split("\t") if part.strip()]
    if len(tab_tokens) >= 2:
        return tab_tokens

    return re.split(r"\s+", stripped)


def _parse_number(value: str) -> float:
    """Parse float supporting both decimal point and decimal comma."""
    return float(value.replace(",", "."))


def _is_db_header(payload: list[str]) -> bool:
    """Return True if payload appears to be DB field header text."""
    if len(payload) < 6:
        return False

    normalized = [p.lower() for p in payload[:6]]
    return normalized == ["pkt", "tk", "x", "y", "h", "bk"]


def parse_kof_coordinate_line(
    line: str,
    *,
    line_number: int = 0,
    log: Optional[logging.Logger] = None,
) -> Optional[KOFPoint]:
    """Parse one KOF line and return a coordinate record if present.

    Behavior:
    - Ignores block types ``00`` and ``01``.
    - Parses only block type ``05``.
    - Supports both ``05 DB ...`` and ``05 ...`` formats.
    - Returns ``None`` for non-coordinate lines or malformed rows.
    - Logs a warning for malformed ``05`` rows.
    """
    active_log = log or logger
    tokens = _split_tokens(line)
    if not tokens:
        return None

    block_type = tokens[0]
    if block_type in {"00", "01"}:
        return None
    if block_type != "05":
        return None

    payload = tokens[1:]
    if not payload:
        active_log.warning("Skipping malformed 05 row at line %s: missing payload", line_number)
        return None

    try:
        if payload[0].upper() == "DB":
            payload = payload[1:]
            if _is_db_header(payload):
                return None
            if len(payload) < 5:
                active_log.warning(
                    "Skipping malformed 05 DB row at line %s: expected at least 5 fields after DB",
                    line_number,
                )
                return None

            pkt = payload[0]
            x = _parse_number(payload[2])
            y = _parse_number(payload[3])
            h = _parse_number(payload[4])
            return KOFPoint(pkt=pkt, x=x, y=y, h=h, line_number=line_number)

        if len(payload) >= 4 and payload[0].lower() == "pkt" and payload[1].lower() == "x":
            return None

        if len(payload) < 4:
            active_log.warning(
                "Skipping malformed 05 row at line %s: expected fields Pkt X Y H",
                line_number,
            )
            return None

        pkt = payload[0]
        x = _parse_number(payload[1])
        y = _parse_number(payload[2])
        h = _parse_number(payload[3])
        return KOFPoint(pkt=pkt, x=x, y=y, h=h, line_number=line_number)

    except ValueError:
        active_log.warning(
            "Skipping malformed 05 row at line %s: non-numeric coordinate values",
            line_number,
        )
        return None


def _read_lines_with_fallback(file_path: str, encoding: str) -> Iterable[str]:
    """Read file lines with UTF-8 then Latin-1 fallback."""
    try:
        with open(file_path, "r", encoding=encoding) as handle:
            return handle.readlines()
    except UnicodeDecodeError:
        if encoding.lower().replace("_", "-") == "iso-8859-1":
            raise
        logger.warning(
            "Failed to decode '%s' with %s; retrying with ISO-8859-1",
            file_path,
            encoding,
        )
        with open(file_path, "r", encoding="ISO-8859-1") as handle:
            return handle.readlines()


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

    lines = _read_lines_with_fallback(file_path, encoding)

    points: list[KOFPoint] = []
    for i, line in enumerate(lines, start=1):
        point = parse_kof_coordinate_line(line, line_number=i)
        if point is not None:
            points.append(point)

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
