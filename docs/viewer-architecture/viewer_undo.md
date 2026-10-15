# viewer_undo.py

## Role

Snapshot-based undo/redo stack for interactive editing operations.

## Scope

- Sections
- Masks
- Notes
- Measurements

## Core Types

- `EditSnapshot`: Deep-copy state container for editable collections.
- `UndoRedoEntry`: One user-visible operation (`label`, `before`, `after`).
- `UndoRedoManager`: Push/undo/redo stack and UI-state notifications.

## Integration Pattern

1. Coordinator captures `before` snapshot.
2. Coordinator performs mutation.
3. Coordinator records operation with a human label.
4. Undo/redo applies snapshot through coordinator callback.

## Notes

- Snapshots are intentionally coarse-grained to keep integration low-risk.
- Batch operations (load masks/sections) are recorded as one operation.
