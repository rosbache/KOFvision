# viewer_rendering.py

## Role

All 2D/3D plotting implementation.

## Entry Points

- `render_2d(viewer, file_colors)`
- `render_3d(viewer, file_colors)`

## 2D Draw Passes

The 2D renderer is split into explicit passes:

- axes init
- rasters
- surfaces and triangulation
- alignments and stations
- view/aspect application
- sections and active section marker
- masks and mask preview
- measurements and measurement preview
- notes
- legend/empty state

## 3D Draw Passes

- axes init
- surfaces and optional triangulation
- legend/empty state and aspect equalization

## Why This Split Matters

- Easier reasoning about ordering and z-order.
- Smaller, testable units for future draw-pass tests.
- Safer future modifications to one layer type without touching others.
