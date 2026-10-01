# KOF Reader

This README documents the standalone parser in `kof_reader.py`.

`kof_reader.py` reads KOF point files and exports coordinates to a GeoDataFrame or directly to a GeoPackage.

## What It Parses

The parser reads block-based KOF lines and handles these variants for coordinate rows:

1. `05 DB Pkt Tk X Y H Bk Merk`
2. `05 Pkt X Y H`

It ignores:

- `00` comment block lines
- `01` administrative block lines

From coordinate rows, it extracts:

- `Pkt`
- `X`
- `Y`
- `H`

## Output Schema

`read_kof_file(...)` returns a GeoDataFrame with these columns:

- `Pkt` (string)
- `X` (float)
- `Y` (float)
- `H` (float)
- `geometry` (Point with X, Y, H)

## Python API

### Read KOF into GeoDataFrame

```python
from kof_reader import read_kof_file

gdf = read_kof_file("kof-mc/innmaling.kof", crs="EPSG:25832")
print(gdf.head())
```

### Read and Save to GeoPackage

```python
from kof_reader import save_kof_to_gpkg

gdf = save_kof_to_gpkg(
    "kof-mc/innmaling.kof",
    "output/innmaling_points.gpkg",
    layer_name="kof_points",
    crs="EPSG:25832",
)
print(f"Saved {len(gdf)} points")
```

## CLI Usage

Run as a module:

```powershell
python -m kof_reader --input "kof-mc/innmáling.kof" --output "output/innmaling_points_cli.gpkg" --layer "kof_points" --crs "EPSG:25832"
```

### CLI Options

| Option | Required | Default | Description |
|---|---|---|---|
| `--input` | Yes | - | Path to input KOF file |
| `--output` | Yes | - | Path to output GeoPackage (`.gpkg`) |
| `--layer` | No | `kof_points` | Output layer name inside the GeoPackage |
| `--crs` | No | `None` | CRS for output, e.g. `EPSG:25832` |
| `--encoding` | No | `utf-8` | Text encoding for input KOF file |

## Notes

- Numeric parsing supports both decimal dot and decimal comma.
- Malformed `05` rows are skipped with warnings.
- If UTF-8 decoding fails, the reader retries with ISO-8859-1.
