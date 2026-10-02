from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from .models import KOFBlock, KOFFile, KOFPoint

logger = logging.getLogger(__name__)


def split_tokens(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped:
        return []
    tab_tokens = [part.strip() for part in stripped.split("\t") if part.strip()]
    if len(tab_tokens) >= 2:
        return tab_tokens
    return re.split(r"\s+", stripped)


def parse_number(value: str) -> float:
    return float(value.replace(",", "."))


def is_db_header(payload: list[str]) -> bool:
    if len(payload) < 6:
        return False
    return [p.lower() for p in payload[:6]] == ["pkt", "tk", "x", "y", "h", "bk"]


def parse_kof_coordinate_line(
    line: str,
    *,
    line_number: int = 0,
    log: Optional[logging.Logger] = None,
) -> Optional[KOFPoint]:
    """Parse one KOF line and return a coordinate record if present."""
    active_log = log or logger
    tokens = split_tokens(line)
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
            if is_db_header(payload):
                return None
            if len(payload) < 5:
                active_log.warning(
                    "Skipping malformed 05 DB row at line %s: expected at least 5 fields after DB",
                    line_number,
                )
                return None

            pkt = payload[0]
            code = payload[1]
            x = parse_number(payload[2])
            y = parse_number(payload[3])
            h = parse_number(payload[4])
            return KOFPoint(pkt=pkt, code=code, x=x, y=y, h=h, line_number=line_number)

        if len(payload) >= 4 and payload[0].lower() == "pkt" and payload[1].lower() == "x":
            return None

        if len(payload) >= 5:
            try:
                x = parse_number(payload[2])
                y = parse_number(payload[3])
                h = parse_number(payload[4])
                return KOFPoint(
                    pkt=payload[0],
                    code=payload[1],
                    x=x,
                    y=y,
                    h=h,
                    line_number=line_number,
                )
            except ValueError:
                pass

        if len(payload) >= 4:
            x = parse_number(payload[1])
            y = parse_number(payload[2])
            h = parse_number(payload[3])
            return KOFPoint(
                pkt=payload[0],
                code="",
                x=x,
                y=y,
                h=h,
                line_number=line_number,
            )

        active_log.warning(
            "Skipping malformed 05 row at line %s: expected fields Pkt X Y H",
            line_number,
        )
        return None
    except ValueError:
        active_log.warning(
            "Skipping malformed 05 row at line %s: non-numeric coordinate values",
            line_number,
        )
        return None


def read_lines_with_fallback(file_path: str, encoding: str = "utf-8") -> Iterable[str]:
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


def parse_kof(file_path: str, *, encoding: str = "utf-8") -> KOFFile:
    """Parse a KOF file into blocks (polylines + loose points)."""
    blocks: list[KOFBlock] = []
    current: Optional[KOFBlock] = None
    loose = KOFBlock(is_polyline=False)

    for i, line in enumerate(read_lines_with_fallback(file_path, encoding), start=1):
        tokens = split_tokens(line)
        if not tokens:
            continue
        head = tokens[0]

        if head == "09":
            code = tokens[1] if len(tokens) > 1 else ""
            if code == "91":
                if current is not None and current.points:
                    blocks.append(current)
                current = KOFBlock(is_polyline=True)
            elif code in ("99", "96"):
                if current is not None and current.points:
                    blocks.append(current)
                current = None
            continue

        if head == "05":
            point = parse_kof_coordinate_line(line, line_number=i)
            if point is None:
                continue
            if current is not None:
                current.points.append(point)
            else:
                loose.points.append(point)

    if current is not None and current.points:
        blocks.append(current)
    if loose.points:
        blocks.append(loose)

    return KOFFile(path=file_path, blocks=blocks)
