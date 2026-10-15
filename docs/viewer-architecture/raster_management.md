# raster_management.py

## Role

Raster-layer lifecycle and state transitions.

## Responsibilities

- Open/load raster sources and optional elevation DEM interpretation.
- Maintain active raster and elevation raster selection.
- Maintain layer ordering and per-layer visibility/alpha interactions.
- Refresh related combobox selector state.

## Interaction Pattern

The manager updates viewer state and requests redraw/refresh through coordinator callbacks.

## Why It Exists

Raster handling was a dense high-churn area. Isolating it reduces accidental coupling with unrelated viewer features.
