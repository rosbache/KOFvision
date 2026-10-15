from __future__ import annotations

from dataclasses import replace

from kofvision.viewer_undo import EditSnapshot, UndoRedoManager


def _snapshot(seed: int) -> EditSnapshot:
    return EditSnapshot(
        sections=[((0.0 + seed, 0.0), (1.0 + seed, 1.0))],
        masks=[],
        notes=[],
        measurements=[],
        active_section_index=0,
        active_mask_index=None,
        active_note_index=None,
    )


def test_undo_redo_roundtrip() -> None:
    applied: list[EditSnapshot] = []

    mgr = UndoRedoManager(apply_snapshot=lambda s: applied.append(s))
    s0 = _snapshot(0)
    s1 = _snapshot(1)

    mgr.record("add section", s0, s1)
    assert mgr.can_undo
    assert not mgr.can_redo

    label = mgr.undo()
    assert label == "add section"
    assert applied[-1] == s0
    assert not mgr.can_undo
    assert mgr.can_redo

    label = mgr.redo()
    assert label == "add section"
    assert applied[-1] == s1


def test_record_skips_identical_snapshots() -> None:
    mgr = UndoRedoManager(apply_snapshot=lambda _s: None)
    s0 = _snapshot(0)
    mgr.record("noop", s0, replace(s0))
    assert not mgr.can_undo
