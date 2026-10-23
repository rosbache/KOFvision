"""General-purpose SOSI file parser.

Parses the full .HODE header and all geometry object types
(PUNKT, KURVE, BUEP, FLATE, TEKST) into structured dataclasses.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_OBJECT_START_RE = re.compile(
    r"^\.([A-ZÆØÅa-zæøå]+)\s+(\d+)\s*:\s*$"
)
_HODE_RE = re.compile(r"^\.HODE\s*$")

# Coordinate key variants (normalised forms that trigger coord-reading mode)
_COORD_KEYS = {"NO", "NOH", "NH", "N"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SosiHeader:
    """Structured representation of the .HODE block."""

    tegnsett: str = "UTF-8"
    sosi_versjon: Optional[str] = None
    sosi_niva: Optional[int] = None
    koordsys: Optional[int] = None
    origo_n: float = 0.0
    origo_e: float = 0.0
    enhet: float = 1.0
    vert_datum: Optional[str] = None
    min_n: Optional[float] = None
    min_e: Optional[float] = None
    max_n: Optional[float] = None
    max_e: Optional[float] = None
    kvalitet: Optional[str] = None

    # Raw key-value pairs for anything not captured above.
    extra: Dict[str, str] = field(default_factory=dict)

    def epsg_code(self, koordsys_map: Optional[Dict] = None) -> Optional[int]:
        """Look up horizontal EPSG code from *koordsys_map*.

        *koordsys_map* should map string KOORDSYS codes to dicts with an
        ``"epsg"`` key, matching the ``"koordsys"`` section of
        ``sosi_koordsys.jsonc``.
        """
        if self.koordsys is None:
            return None
        if koordsys_map is None:
            return None
        entry = koordsys_map.get(str(self.koordsys))
        if entry is None:
            return None
        return entry.get("epsg")


@dataclass
class SosiObject:
    """Structured representation of one SOSI geometry object."""

    object_type: str  # PUNKT, KURVE, BUEP, FLATE, TEKST, …
    object_id: int

    objtype: Optional[str] = None  # value of ..OBJTYPE
    top_level: Dict[str, str] = field(default_factory=dict)
    nested: Dict[str, Dict[str, str]] = field(default_factory=dict)

    raw_coordinates: List[Tuple[float, float, float]] = field(default_factory=list)
    coord_type: Optional[str] = None  # "NØH", "NØ", …

    refs: List[int] = field(default_factory=list)  # signed refs for FLATE
    streng: Optional[str] = None  # text string for TEKST

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_property(self, key: str) -> Optional[str]:
        """Return property from top-level first, then any nested section."""
        if key in self.top_level:
            return self.top_level[key]
        for section_values in self.nested.values():
            if key in section_values:
                return section_values[key]
        return None

    def scaled_coordinates(
        self,
        enhet: float,
        origo_n: float = 0.0,
        origo_e: float = 0.0,
        z_override: Optional[float] = None,
    ) -> List[Tuple[float, float, float]]:
        """Return coordinates scaled by *enhet* and shifted by origin.

        Raw SOSI coordinates are stored as (N, E, H).  Returned values
        are in the same axis order (northing, easting, height) — the
        exporter layer is responsible for any axis swap.

        Parameters
        ----------
        z_override:
            When provided, replace the H component of every returned point
            with this value (e.g. from a ``..HØYDE`` property on the object).
            Useful when the coordinate block contains no Z (``..NØ``) and the
            elevation is stored as a separate attribute.
        """
        if z_override is not None:
            return [
                (n * enhet + origo_n, e * enhet + origo_e, z_override)
                for n, e, h in self.raw_coordinates
            ]
        return [
            (n * enhet + origo_n, e * enhet + origo_e, h * enhet)
            for n, e, h in self.raw_coordinates
        ]


@dataclass
class SosiFile:
    """Result of parsing a complete SOSI file."""

    header: SosiHeader
    objects: Dict[int, SosiObject] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_koordsys_map(jsonc_path: Optional[str] = None) -> Dict:
    """Load the KOORDSYS → EPSG lookup from a JSONC file.

    Falls back to ``sosi_koordsys.jsonc`` next to this module.
    """
    if jsonc_path is None:
        jsonc_path = os.path.join(os.path.dirname(__file__), "sosi_koordsys.jsonc")
    try:
        with open(jsonc_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        logger.warning("KOORDSYS mapping file not found: %s. Continuing with empty mapping.", jsonc_path)
        return {"tegnsett": {}, "koordsys": {}}
    # Strip JSONC comments (reuse same approach as config_utils)
    pattern = r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*[\s\S]*?\*/'
    cleaned = re.sub(pattern, lambda m: "" if m.group(0).startswith("/") else m.group(0), text)
    return json.loads(cleaned)


def parse_sosi_file(
    file_path: str,
    *,
    koordsys_jsonc: Optional[str] = None,
) -> SosiFile:
    """Parse a SOSI file into a :class:`SosiFile`.

    Parameters
    ----------
    file_path:
        Path to the ``.sos`` / ``.sosi`` text file.
    koordsys_jsonc:
        Optional path to a ``sosi_koordsys.jsonc`` override.
    """
    lookup = load_koordsys_map(koordsys_jsonc)
    tegnsett_map: Dict[str, str] = lookup.get("tegnsett", {})

    encoding = _detect_encoding(file_path, tegnsett_map)
    lines = _read_lines(file_path, encoding)

    # Strip UTF-8 BOM if present on the first line.
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0][1:]

    header = SosiHeader()
    objects: Dict[int, SosiObject] = {}

    idx = 0
    total = len(lines)

    # ---- Parse .HODE ----
    while idx < total:
        line = lines[idx].strip()
        idx += 1
        if _HODE_RE.match(line):
            idx = _parse_header(lines, idx, header)
            break

    # ---- Parse objects ----
    active: Optional[SosiObject] = None
    active_section: Optional[str] = None
    in_coords = False

    while idx < total:
        raw_line = lines[idx]
        idx += 1
        line = raw_line.strip()
        if not line:
            continue

        # .SLUTT terminates the file
        if line == ".SLUTT":
            break

        # New top-level object?
        m = _OBJECT_START_RE.match(line)
        if m:
            if active is not None:
                _finalise(active, objects)
            obj_type = m.group(1).upper()
            obj_id = int(m.group(2))
            active = SosiObject(object_type=obj_type, object_id=obj_id)
            active_section = None
            in_coords = False
            continue

        # Any other single-dot line that is NOT a data continuation ends
        # the active object (e.g. `.HODE` appearing again, or unknown blocks).
        if line.startswith(".") and not line.startswith(".."):
            if active is not None:
                _finalise(active, objects)
            active = None
            active_section = None
            in_coords = False
            continue

        if active is None:
            continue

        # --- Three-dot property (nested) ---
        if line.startswith("..."):
            content = line[3:]
            # Inline ...KP annotations on coordinate lines are NOT
            # nested properties — they appear after coordinate numbers
            # within a coordinate block and should be ignored here.
            # They are stripped during coordinate parsing.
            if in_coords:
                continue
            key, value = _parse_key_value(content)
            if active_section is None:
                active_section = "ROOT"
            bucket = active.nested.setdefault(active_section, {})
            bucket[key] = value
            continue

        # --- Two-dot property ---
        if line.startswith(".."):
            content = line[2:]
            key, value = _parse_key_value(content)
            key_upper = key.upper()
            key_norm = _normalize_sosi_key(key)

            # Coordinate block start?
            if key_norm in _COORD_KEYS:
                in_coords = True
                active.coord_type = key_upper
                active_section = None
                # Some coordinate blocks have the first point on the same
                # line as the key; those lines sometimes also carry ...KP
                # annotations — _try_parse_coord handles this.
                # value may contain first coord pair inline (rare but happens)
                if value.strip():
                    _try_parse_coord(value, active)
                continue

            in_coords = False

            # Special keys
            if key_upper == "OBJTYPE":
                active.objtype = value
                active.top_level[key] = value
                active_section = key
                continue

            if key_upper == "STRENG":
                # Remove surrounding quotes if present
                active.streng = value.strip('"')
                active.top_level[key] = value
                active_section = key
                continue

            if key_upper == "REF":
                active.refs.extend(_parse_refs(value))
                active.top_level[key] = value
                active_section = key
                continue

            active.top_level[key] = value
            active_section = key
            continue

        # --- Plain data line (coordinates or REF continuation) ---
        if in_coords:
            _try_parse_coord(line, active)
            continue

        # REF continuation lines (unsigned/signed integers with colon prefix)
        if active_section and active_section.upper() == "REF":
            active.refs.extend(_parse_refs(line))
            continue

    if active is not None:
        _finalise(active, objects)

    header_enhet = header.enhet
    if header_enhet <= 0:
        header_enhet = 1.0
    header.enhet = header_enhet

    return SosiFile(header=header, objects=objects)


# ---------------------------------------------------------------------------
# FLATE topology resolution
# ---------------------------------------------------------------------------

def resolve_flate_geometry(
    flate: SosiObject,
    all_objects: Dict[int, SosiObject],
    enhet: float,
    origo_n: float = 0.0,
    origo_e: float = 0.0,
) -> Optional[List[Tuple[float, float, float]]]:
    """Build a polygon ring from a FLATE's REF list.

    Returns a list of (N, E, H) coordinate tuples forming the outer ring,
    or *None* if critical references are missing.
    """
    if not flate.refs:
        return None

    ring: List[Tuple[float, float, float]] = []
    for signed_id in flate.refs:
        abs_id = abs(signed_id)
        ref_obj = all_objects.get(abs_id)
        if ref_obj is None:
            logger.warning(
                "FLATE %d references missing object %d — skipping ref",
                flate.object_id, abs_id,
            )
            continue

        coords = ref_obj.scaled_coordinates(enhet, origo_n, origo_e)
        if signed_id < 0:
            coords = list(reversed(coords))

        # Avoid duplicating the junction point between consecutive refs.
        if ring and coords and _points_close(ring[-1], coords[0]):
            coords = coords[1:]
        ring.extend(coords)

    if len(ring) < 3:
        return None

    # Close the ring if not already closed.
    if not _points_close(ring[0], ring[-1]):
        ring.append(ring[0])

    return ring


def _ring_area_2d(ring: List[Tuple[float, float, float]]) -> float:
    """Return the absolute 2D area of a ring using the shoelace formula.

    Ring points are ``(N, E, H)``; only N and E are used.
    """
    area = 0.0
    n = len(ring)
    for i in range(n - 1):
        n0, e0, _ = ring[i]
        n1, e1, _ = ring[i + 1]
        area += e0 * n1 - e1 * n0
    return abs(area) * 0.5


def _split_into_rings(
    chain: List[Tuple[float, float, float]],
) -> List[List[Tuple[float, float, float]]]:
    """Split a chain of points into closed sub-rings.

    Whenever the chain returns to the start of the current ring segment, that
    segment is closed off and a new segment begins.  Any trailing unclosed
    segment is closed by appending its first point.
    """
    rings: List[List[Tuple[float, float, float]]] = []
    start = 0
    for i in range(1, len(chain)):
        if _points_close(chain[i], chain[start]):
            ring = chain[start : i + 1]
            if len(ring) >= 4:  # at least 3 distinct + closing point
                rings.append(ring)
            start = i + 1
    # Handle any remaining unclosed tail
    tail = chain[start:]
    if len(tail) >= 3:
        if not _points_close(tail[0], tail[-1]):
            tail = tail + [tail[0]]
        if len(tail) >= 4:
            rings.append(tail)
    return rings


def resolve_flate_rings(
    flate: SosiObject,
    all_objects: Dict[int, SosiObject],
    enhet: float,
    origo_n: float = 0.0,
    origo_e: float = 0.0,
) -> Tuple[Optional[List[Tuple[float, float, float]]], List[List[Tuple[float, float, float]]]]:
    """Build outer ring and hole rings from a FLATE's REF list.

    All ref IDs are chained in order; the **sign** of each ref indicates only
    the traversal direction (positive = forward, negative = reversed) — it
    does **not** distinguish outer boundary from holes.  Multiple closed loops
    within the chained result are split into separate rings; the ring with the
    largest 2-D area is returned as the outer ring and the rest as holes.

    Returns a tuple ``(outer_ring, holes)`` where each ring is a list of
    ``(N, E, H)`` tuples.  ``outer_ring`` is *None* if it could not be built
    (e.g. missing references or fewer than 3 points).  ``holes`` may be empty.
    """
    if not flate.refs:
        return None, []

    # Build a single continuous chain from all refs, using sign for direction.
    chain: List[Tuple[float, float, float]] = []
    for signed_id in flate.refs:
        abs_id = abs(signed_id)
        ref_obj = all_objects.get(abs_id)
        if ref_obj is None:
            logger.warning(
                "FLATE %d references missing object %d — skipping ref",
                flate.object_id, abs_id,
            )
            continue
        coords = ref_obj.scaled_coordinates(enhet, origo_n, origo_e)
        if signed_id < 0:
            coords = list(reversed(coords))
        # Deduplicate junction between consecutive refs.
        if chain and coords and _points_close(chain[-1], coords[0]):
            coords = coords[1:]
        chain.extend(coords)

    if len(chain) < 3:
        return None, []

    # Split the chain into closed sub-rings.
    rings = _split_into_rings(chain)

    if not rings:
        # Chain never closed — close it manually as a single ring.
        chain.append(chain[0])
        if len(chain) >= 4:
            rings = [chain]
        else:
            return None, []

    # Largest area ring → outer boundary; rest → holes.
    rings.sort(key=_ring_area_2d, reverse=True)
    outer_ring = rings[0]
    holes = rings[1:]

    return outer_ring, holes


def densify_buep(
    obj: SosiObject,
    enhet: float,
    origo_n: float = 0.0,
    origo_e: float = 0.0,
    num_segments: int = 16,
) -> List[Tuple[float, float, float]]:
    """Densify a BUEP (circular arc) into a polyline.

    BUEP objects carry exactly 3 raw coordinate points:
    start, point-on-arc, end.  This function fits a circular arc through
    those three points and returns *num_segments* + 1 evenly spaced points.

    Falls back to a straight line through the three points when a valid
    circle cannot be determined (collinear points).
    """
    coords = obj.scaled_coordinates(enhet, origo_n, origo_e)
    if len(coords) < 3:
        return coords

    p0 = coords[0]
    pm = coords[1]
    p1 = coords[2]

    centre = _circumcentre(p0, pm, p1)
    if centre is None:
        # Collinear — fall back to straight segments
        return coords

    cx, cy = centre
    r = math.hypot(p0[0] - cx, p0[1] - cy)

    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    am = math.atan2(pm[1] - cy, pm[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)

    # Determine arc direction so that am lies between a0 and a1.
    sweep = _arc_sweep(a0, am, a1)

    # Interpolate Z linearly along the arc.
    z0, z1 = p0[2], p1[2]
    out: List[Tuple[float, float, float]] = []
    for i in range(num_segments + 1):
        t = i / num_segments
        angle = a0 + sweep * t
        n = cx + r * math.cos(angle)
        e = cy + r * math.sin(angle)
        z = z0 + (z1 - z0) * t
        out.append((n, e, z))
    return out


# ---------------------------------------------------------------------------
# Internal: header parsing
# ---------------------------------------------------------------------------

def _parse_header(lines: List[str], idx: int, header: SosiHeader) -> int:
    """Parse .HODE properties starting at *idx*. Return index past the block."""
    total = len(lines)
    in_transpar = False
    in_omrade = False

    while idx < total:
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue

        # A new top-level block ends .HODE
        if line.startswith(".") and not line.startswith(".."):
            break

        idx += 1

        # --- three-dot (nested inside TRANSPAR or OMRÅDE) ---
        if line.startswith("..."):
            key, val = _parse_key_value(line[3:])
            key_norm = _normalize_sosi_key(key)

            if in_transpar:
                if key_norm == "KOORDSYS":
                    header.koordsys = _safe_int(val)
                elif key_norm in ("ORIGO_NO", "ORIGINO", "ORIGINOE", "ORIGINO_E"):
                    # ...ORIGO-NØ  <n> <e>
                    parts = val.split()
                    if len(parts) >= 2:
                        header.origo_n = _safe_float(parts[0])
                        header.origo_e = _safe_float(parts[1])
                    elif len(parts) == 1:
                        header.origo_n = _safe_float(parts[0])
                elif key_norm == "ENHET":
                    header.enhet = _safe_float(val) or 1.0
                elif key_norm in ("VERTDATUM", "VERT_DATUM"):
                    header.vert_datum = val
                else:
                    header.extra[key] = val

            elif in_omrade:
                if key_norm in ("MINNO", "MIN_NO"):
                    parts = val.split()
                    if len(parts) >= 2:
                        header.min_n = _safe_float(parts[0])
                        header.min_e = _safe_float(parts[1])
                elif key_norm in ("MAXNO", "MAX_NO"):
                    parts = val.split()
                    if len(parts) >= 2:
                        header.max_n = _safe_float(parts[0])
                        header.max_e = _safe_float(parts[1])
                else:
                    header.extra[key] = val
            else:
                header.extra[key] = val
            continue

        # --- two-dot ---
        if line.startswith(".."):
            key, val = _parse_key_value(line[2:])
            key_norm = _normalize_sosi_key(key)
            in_transpar = False
            in_omrade = False

            if key_norm == "TEGNSETT":
                header.tegnsett = val
            elif key_norm in ("SOSIVERSJON", "SOSI_VERSJON"):
                header.sosi_versjon = val
            elif key_norm in ("SOSINIVA", "SOSI_NIVA"):
                header.sosi_niva = _safe_int(val)
            elif key_norm == "KVALITET":
                header.kvalitet = val
            elif key_norm == "TRANSPAR":
                in_transpar = True
            elif key_norm in ("OMRADE", "OMRAADE"):
                in_omrade = True
            else:
                header.extra[key] = val
            continue

    return idx


# ---------------------------------------------------------------------------
# Internal: encoding detection
# ---------------------------------------------------------------------------

_TEGNSETT_RE = re.compile(r"^\.\.[Tt][Ee][Gg][Nn][Ss][Ee][Tt][Tt]\s+(.+)$")


def _detect_encoding(file_path: str, tegnsett_map: Dict[str, str]) -> str:
    """Read first lines with latin-1 to find ..TEGNSETT, return Python encoding."""
    try:
        with open(file_path, "r", encoding="latin-1") as fh:
            for _ in range(30):
                line = fh.readline()
                if not line:
                    break
                m = _TEGNSETT_RE.match(line.strip())
                if m:
                    declared = m.group(1).strip().upper()
                    return tegnsett_map.get(declared, "utf-8")
    except OSError:
        pass
    return "utf-8"


def _read_lines(file_path: str, encoding: str) -> List[str]:
    """Read entire file with the given encoding, fallback chain on error."""
    fallbacks = [encoding, "utf-8", "latin-1"]
    seen = set()
    for enc in fallbacks:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            with open(file_path, "r", encoding=enc) as fh:
                return fh.readlines()
        except (UnicodeDecodeError, LookupError):
            continue
    raise OSError(f"Could not decode {file_path} with any known encoding")


# ---------------------------------------------------------------------------
# Internal: parsing helpers
# ---------------------------------------------------------------------------

def _finalise(obj: SosiObject, store: Dict[int, SosiObject]) -> None:
    """Store a completed object. Warn on duplicate IDs."""
    if obj.object_id in store:
        logger.warning(
            "Duplicate object ID %d (type %s) — overwriting previous",
            obj.object_id, obj.object_type,
        )
    store[obj.object_id] = obj


def _try_parse_coord(text: str, obj: SosiObject) -> None:
    """Attempt to parse a coordinate line, stripping ...KP annotations."""
    # Strip any trailing ...KP N annotation
    text = re.sub(r"\s*\.\.\.KP\s+\d+\s*$", "", text).strip()
    if not text:
        return
    parts = text.split()
    if len(parts) < 2:
        return
    try:
        n = float(parts[0])
        e = float(parts[1])
        h = float(parts[2]) if len(parts) >= 3 else 0.0
    except ValueError:
        return
    obj.raw_coordinates.append((n, e, h))


def _parse_refs(text: str) -> List[int]:
    """Parse REF tokens like ``':8460 :7232 :-5578'`` into signed ints."""
    out: List[int] = []
    for token in text.split():
        token = token.lstrip(":")
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def _parse_key_value(content: str) -> Tuple[str, str]:
    content = content.strip()
    if not content:
        return "", ""
    parts = content.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def _normalize_sosi_key(key: str) -> str:
    key = key.upper()
    key = key.replace("Ø", "O").replace("Æ", "AE").replace("Å", "A")
    key = key.replace("-", "_")
    return re.sub(r"[^A-Z0-9_]", "", key)


def _safe_float(text: str) -> float:
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError):
        return 0.0


def _safe_int(text: str) -> Optional[int]:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


def _points_close(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    tol: float = 1e-6,
) -> bool:
    return (abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol)


def _circumcentre(
    p0: Tuple[float, float, float],
    pm: Tuple[float, float, float],
    p1: Tuple[float, float, float],
) -> Optional[Tuple[float, float]]:
    """Return 2D circumcentre of three points, or None if collinear."""
    ax, ay = p0[0], p0[1]
    bx, by = pm[0], pm[1]
    cx, cy = p1[0], p1[1]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) +
          (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) +
          (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy


def _arc_sweep(a0: float, am: float, a1: float) -> float:
    """Compute signed sweep angle from *a0* through *am* to *a1*."""
    def _wrap(a: float) -> float:
        while a < -math.pi:
            a += 2 * math.pi
        while a > math.pi:
            a -= 2 * math.pi
        return a

    # Try counter-clockwise
    ccw = _wrap(a1 - a0)
    if ccw < 0:
        ccw += 2 * math.pi
    mid_ccw = _wrap(am - a0)
    if mid_ccw < 0:
        mid_ccw += 2 * math.pi
    if mid_ccw < ccw:
        return ccw

    # Clockwise
    cw = ccw - 2 * math.pi
    return cw
