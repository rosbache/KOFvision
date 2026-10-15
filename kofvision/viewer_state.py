from __future__ import annotations

from typing import Any


def clear_cached_mask_volume_results(viewer: Any) -> None:
    """Clear cached per-mask compute results and refresh related UI actions."""
    viewer._last_volume_per_mask_rows.clear()
    viewer._last_mask_volume_results.clear()
    viewer._refresh_mask_result_actions()


def reset_for_clear(viewer: Any) -> None:
    """Reset viewer runtime state for the Clear action."""
    viewer._close_section_windows()
    viewer.files.clear()
    viewer.landxml_files.clear()
    viewer.layer_vars.clear()
    viewer._surface_owner_paths.clear()
    viewer._surface_source_paths.clear()
    viewer._landxml_group_vars.clear()
    viewer._view_extent_2d = None
    viewer._latest_section_report_path = None
    viewer.last_volume = None
    viewer.volume_text.set("Volume: (not computed)")
    viewer._measure_results.clear()
    viewer._measure_click_points.clear()
    viewer._sections.clear()
    viewer._clear_active_section_marker(redraw=False)
    viewer._active_section_index = None
    viewer._masks.clear()
    viewer._mask_visibility_vars.clear()
    viewer._last_volume_per_mask_rows.clear()
    viewer._last_mask_volume_results.clear()
    viewer._active_mask_index = None
    viewer.active_mask_var.set("")
    viewer._mask_click_points.clear()
    viewer._notes.clear()
    viewer._note_visibility_vars.clear()
    viewer._active_note_index = None
    viewer._cancel_pending_note_single_click()
    if viewer._mask_mode:
        viewer._end_mask_mode()
    if viewer._mask_edit_mode:
        viewer._end_mask_edit_mode()
    if viewer._measure_mode:
        viewer._end_measure_mode()
    if viewer._note_mode:
        viewer._end_note_mode()


def reset_for_project_load(viewer: Any) -> None:
    """Reset viewer runtime state before loading a project payload."""
    viewer._close_section_windows()
    viewer.files.clear()
    viewer.landxml_files.clear()
    viewer.layer_vars.clear()
    viewer._surface_owner_paths.clear()
    viewer._surface_source_paths.clear()
    viewer._landxml_group_vars.clear()
    viewer._sections.clear()
    viewer._clear_active_section_marker(redraw=False)
    viewer._active_section_index = None
    viewer._measure_results.clear()
    viewer._measure_click_points.clear()
    viewer._masks.clear()
    viewer._mask_visibility_vars.clear()
    viewer._active_mask_index = None
    viewer.active_mask_var.set("")
    viewer._notes.clear()
    viewer._note_visibility_vars.clear()
    viewer._active_note_index = None
    viewer._cancel_pending_note_single_click()
    viewer._raster_layers.clear()
    viewer._active_raster_index = None
    viewer._elevation_raster_index = None
    viewer._latest_section_report_path = None
    if viewer._measure_mode:
        viewer._end_measure_mode()
    if viewer._note_mode:
        viewer._end_note_mode()
    clear_cached_mask_volume_results(viewer)
