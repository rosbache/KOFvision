# viewer_tools.py

## Role

Interactive 2D tool state machines.

## Tools

- `SectionTool`
- `MaskTool`
- `MaskEditTool`
- `MeasureTool`
- `NoteTool`
- `ToolDispatcher` (tracks active mode)

## Responsibilities

- Enter/exit tool modes.
- Consume matplotlib events (click/move/release).
- Update transient tool state and finalize persisted results.

## Recent Extraction Scope

- Note dialog helpers.
- Note map-click routing and pick logic.
- Mask-edit press/motion/release handlers.

## Design Notes

- Tool classes are static helper style, called by coordinator wrappers.
- This keeps migration risk low while enabling future conversion to instance-based tools.
