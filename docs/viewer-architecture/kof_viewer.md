# kof_viewer.py (Coordinator)

## Role

`kof_viewer.py` is the application coordinator and Tk root window.

## Responsibilities

- Own top-level Tk lifecycle and widget references.
- Bind callbacks and keyboard shortcuts.
- Coordinate feature modules.
- Hold shared runtime state that modules operate on.
- Coordinate async background jobs (volume/report) and apply results on UI thread.
- Own undo/redo snapshot boundaries for user-edit operations.

## What Should Stay Here

- App startup and teardown.
- Cross-module orchestration.
- High-level user messaging.
- Final wiring from UI events to delegated module methods.

## What Should Move Out Over Time

- Pure rendering details.
- Tool-specific state machines.
- Data serialization format logic.
- Raster layer state transitions.

## Current Delegations

- State resets -> `viewer_state.py`
- Project save/load payload handling -> `viewer_serialization.py`
- Raster layer operations -> `raster_management.py`
- Tool workflows -> `viewer_tools.py`
- 2D/3D draw implementation -> `viewer_rendering.py`
- Undo/redo stack implementation -> `viewer_undo.py`
- Background execution -> `task_executor.py`
- Report math helpers -> `report_service.py`
- Volume compute helpers -> `volume_controller.py`
- Preferences persistence -> `preferences.py`

## Ditch Workflow Notes

- The coordinator owns ditch setup dialogs and selected ditch centerline state.
- Section report baseline selection prefers selected ditch centerline when present.
- Ditch rule configuration and layer mappings are persisted through project serialization.
