from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class EditSnapshot:
    sections: list[tuple[tuple[float, float], tuple[float, float]]]
    ditch_centerlines: list[tuple[tuple[float, float], tuple[float, float]]]
    masks: list[Any]
    pit_polygons: list[Any]
    pit_settings: Any
    notes: list[Any]
    measurements: list[dict[str, Any]]
    active_section_index: int | None
    active_ditch_centerline_index: int | None
    active_mask_index: int | None
    active_pit_index: int | None
    active_note_index: int | None


@dataclass
class UndoRedoEntry:
    label: str
    before: EditSnapshot
    after: EditSnapshot


class UndoRedoManager:
    """Simple snapshot-based undo/redo stack for viewer edits."""

    def __init__(
        self,
        *,
        apply_snapshot: Callable[[EditSnapshot], None],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._apply_snapshot = apply_snapshot
        self._on_change = on_change
        self._entries: list[UndoRedoEntry] = []
        self._next_index = 0

    @property
    def can_undo(self) -> bool:
        return self._next_index > 0

    @property
    def can_redo(self) -> bool:
        return self._next_index < len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._next_index = 0
        self._notify()

    def record(self, label: str, before: EditSnapshot, after: EditSnapshot) -> None:
        if before == after:
            return
        if self._next_index < len(self._entries):
            self._entries = self._entries[: self._next_index]
        self._entries.append(UndoRedoEntry(label=label, before=before, after=after))
        self._next_index += 1
        self._notify()

    def undo(self) -> str | None:
        if not self.can_undo:
            return None
        self._next_index -= 1
        entry = self._entries[self._next_index]
        self._apply_snapshot(entry.before)
        self._notify()
        return entry.label

    def redo(self) -> str | None:
        if not self.can_redo:
            return None
        entry = self._entries[self._next_index]
        self._next_index += 1
        self._apply_snapshot(entry.after)
        self._notify()
        return entry.label

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()
