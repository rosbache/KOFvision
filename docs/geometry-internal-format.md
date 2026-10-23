# Internal Geometry Structure (KOF, SOSI, LandXML)

This document describes the canonical in-memory geometry structures KOFvision uses after import, and how KOF/SOSI/LandXML inputs are normalized into that shared format.

## Canonical geometry model

KOFvision uses `VectorSurface` as the common internal container for imported vector surfaces.

Core fields:

- `points: list[(y_easting, x_northing, z)]`
- `line_strings: list[list[(y_easting, x_northing, z)]]`
- `triangles: list[(i0, i1, i2)]` (optional, indices into `points`)
- `source_kind: "kof" | "sosi" | "landxml" | "vector"`
- `layer_name` (for multi-surface sources such as LandXML)
- `point_labels`, `point_codes` (metadata used in UI/export)

Coordinate convention used internally:

- Axis order in tuples is `(Y, X, Z)` where:
  - `Y = Easting`
  - `X = Northing`
  - `Z = Elevation`

This ordering is intentionally consistent across KOF, SOSI, LandXML, and raster-derived surfaces so interpolation/volume tools can stay format-agnostic.

## Import mapping by format

### KOF -> `VectorSurface`

Input characteristics:

- Coordinate rows are parsed from `05` records.
- Polyline sections are interpreted from `09 91 ... 09 99` and `09 96`.

Normalization:

- Parsed legacy points are stored as `KOFPoint(pkt, code, x, y, h, ...)`.
- Conversion to `VectorSurface` remaps each point into `(p.y, p.x, p.h)` => `(Easting, Northing, Elevation)`.
- KOF polyline blocks become `line_strings`.
- `triangles` is initially empty; triangulation is built on demand (Delaunay) unless explicit triangles are already present.

### SOSI -> `VectorSurface`

Input characteristics:

- Uses `sosi_parser` object model (`PUNKT`, `BUEP`, `FLATE`, etc.).
- Supports geometry densification for curved entities where needed.

Normalization:

- SOSI scaled coordinates are read as `(northing, easting, height)`.
- KOFvision converts each vertex to `(easting, northing, height)` for internal consistency.
- Line/polygon paths are stored as `line_strings`.
- All usable vertices are also collected in `points`.
- `triangles` is empty at import; triangulation is built later when requested.

### LandXML -> `VectorSurface`

Input characteristics:

- Surface points come from `<Definition><Pnts><P>...</P></Pnts>`.
- TIN faces come from `<Definition><Faces><F>...</F></Faces>`.

Normalization:

- Parsed XYZ values are converted to internal `(y_easting, x_northing, z)`.
- Point ids are preserved during parse and mapped to zero-based array indices.
- Face triplets are resolved either:
  - by explicit id lookup, or
  - by fallback 1-based index conversion (`v - 1`).
- Result is stored as `VectorSurface(..., triangles=faces, source_kind="landxml")`.

Important behavior:

- When explicit LandXML faces exist, KOFvision keeps those triangles exactly (no boundary filtering of imported TIN faces).

## Specialized runtime surface wrappers

At runtime, imported data is used through surface-like objects exposing:

- `interpolate(y, x) -> z`
- `xy_bounds()`
- optional triangulation helpers

Main surface wrappers:

- `KOFFile` (legacy block model with on-demand triangulation)
- `VectorSurface` (shared generic representation)
- `LandXMLSurface` (explicit faces support)
- `ElevationRasterSurface` (DEM-backed interpolator)
- `TheoreticalPitSurface` (procedural surface from pit polygons/settings)

All of these are consumable by volume and section pipelines through the same interpolation contract.

## How this fits volume, ditch, and pit workflows

- Volume setup can combine KOF, SOSI-derived, LandXML, raster, constant-Z, and pit surfaces because they all expose compatible interpolation behavior.
- Ditch stop layers resolve by material+slot mapping into a selected surface name, then sample via the same `interpolate(y, x)` path.
- Pit stop layers use the same mapping strategy and clamp pit depths against the mapped stop surface.
- Section and report generation sample top/bottom/intermediate surfaces without format-specific branching in the UI workflow.

## Serialization implications

Project save/load persists viewer state (selected layers, ditch config, pit config, masks, etc.), not raw original source files.

- Ditch centerlines/rules and pit polygons/settings are persisted in project payload.
- Imported KOF/SOSI/LandXML files are still the authoritative geometry sources and should remain accessible when reopening projects that depend on them.

## Practical guidance for contributors

- Add format-specific parsing in parser modules, but normalize to `VectorSurface` as early as possible.
- Keep `(Y, X, Z)` tuple ordering consistent across all geometry-producing code.
- If adding a new source format, prioritize:
  1. deterministic point mapping,
  2. optional line strings,
  3. optional explicit triangles,
  4. compatibility with `interpolate`/`xy_bounds` expectations.
