# viewer_serialization.py

## Role

Project payload ownership for save/load.

## Responsibilities

- Build JSON-serializable payload from live viewer state.
- Validate payload metadata (`type`, `version`).
- Apply payload back onto runtime state.

## Why It Exists

Separates fragile schema mapping from Tk/UI code, making payload evolution and tests safer.

## Error Model

- `ProjectPayloadError` for invalid payload metadata.
- The coordinator handles user-facing dialogs.

## Extension Guidance

When adding project fields:

1. Add field to `build_project_payload`.
2. Add load/apply logic to `apply_project_payload`.
3. Keep backward compatibility checks centralized.
