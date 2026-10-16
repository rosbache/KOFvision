# viewer_serialization.py

## Role

Project payload ownership for save/load.

## Responsibilities

- Build JSON-serializable payload from live viewer state.
- Validate payload metadata (`type`, `version`).
- Apply payload back onto runtime state.
- Migrate legacy payload versions forward.
- Persist and restore ditch configuration:
	- ditch centerlines
	- active ditch centerline index
	- ditch rules
	- ditch layer catalog mappings

## Why It Exists

Separates fragile schema mapping from Tk/UI code, making payload evolution and tests safer.

## Error Model

- `ProjectPayloadError` for invalid payload metadata.
- The coordinator handles user-facing dialogs.

## Versioning

- Current payload version: `2`.
- Supported input versions: `1`, `2`.
- `migrate_project_payload` upgrades v1 payloads by adding new optional fields.

## Extension Guidance

When adding project fields:

1. Add field to `build_project_payload`.
2. Add load/apply logic to `apply_project_payload`.
3. Keep backward compatibility checks centralized.
