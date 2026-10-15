# viewer_state.py

## Role

State transition helpers for bulk reset operations.

## Responsibilities

- Clear state for "new/clear" flow.
- Clear state for project-load flow.
- Clear cached per-mask volume result structures.

## Design Notes

- Stateless utility functions that operate on a viewer object.
- Minimal behavior, low risk, easy to test.
- Intentionally avoids direct UI construction logic.

## Typical Usage

- Called by `kof_viewer.py` when clearing all data.
- Called by `viewer_serialization.py` before applying payload.
