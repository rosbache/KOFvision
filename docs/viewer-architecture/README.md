# KOF Viewer Modular Architecture

This folder documents the modularized viewer architecture introduced to split the original monolithic `kof_viewer.py` into maintainable pieces.

## Goals

- Keep runtime behavior unchanged while reducing file complexity.
- Isolate state transitions, tool workflows, rendering, and persistence.
- Make each subsystem easier to test in isolation.

## Main Runtime Flow

1. `kof_viewer.py` acts as coordinator and Tk root window.
2. UI callbacks are bound in the coordinator.
3. Callback methods delegate work to focused modules.
4. Modules call back into the coordinator only where cross-cutting context is required.

## Modules

- `kofvision/viewer_state.py`
  - Reset and state-transition helpers for clear/load workflows.
- `kofvision/viewer_serialization.py`
  - Project payload build/validate/apply logic.
- `kofvision/raster_management.py`
  - Raster layer lifecycle, ordering, alpha, elevation-raster selection.
- `kofvision/viewer_tools.py`
  - Interactive tool behavior (section, mask, mask edit, measure, notes).
- `kofvision/viewer_rendering.py`
  - 2D/3D rendering entrypoints plus split draw-pass functions.
- `kofvision/viewer_ui.py`
  - Style setup helpers for ttk-based look-and-feel.

## Dependency Direction

- Coordinator -> modules is the intended direction.
- Modules should avoid importing coordinator internals.
- Shared data types remain in `kofvision.models` and existing domain modules.

## Testing Strategy

- Keep full integration tests in `kof_viewer.py` workflows.
- Add module-level tests for serialization/state/raster operations.
- Add tool behavior tests using lightweight viewer doubles.
- Add draw-pass smoke tests for rendering with deterministic fixtures.

## Practical Extension Rule

When adding features:

- Put interaction state transitions in `viewer_tools.py`.
- Put raster list and selection logic in `raster_management.py`.
- Put payload structure changes in `viewer_serialization.py`.
- Put plotting changes in `viewer_rendering.py`.
- Keep `kof_viewer.py` as orchestration glue only.
