from __future__ import annotations

from typing import Any

from kofvision.ditch import DitchLayerCatalog
from kofvision.pit import PitSettings


def clear_cached_mask_volume_results(viewer: Any) -> None:
    """Clear cached per-mask compute results and refresh related UI actions."""
    viewer._last_volume_per_mask_rows.clear()
    viewer._last_mask_volume_results.clear()
    if hasattr(viewer, "_last_volume_result_item_kind"):
        viewer._last_volume_result_item_kind = "mask"
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
    viewer._section_volume_bars.clear()
    if hasattr(viewer, "runtime_state"):
        viewer.runtime_state.volume_history.clear()
        viewer.runtime_state.last_render_2d_signature = None
        viewer.runtime_state.last_render_3d_signature = None
    viewer.last_volume = None
    viewer.volume_text.set("Volume: (not computed)")
    viewer._measure_results.clear()
    viewer._measure_click_points.clear()
    viewer._sections.clear()
    viewer._ditch_centerlines.clear()
    viewer._clear_active_section_marker(redraw=False)
    viewer._active_section_index = None
    viewer._active_ditch_centerline_index = None
    viewer.active_ditch_centerline_var.set("")
    viewer._masks.clear()
    viewer._mask_visibility_vars.clear()
    viewer._last_volume_per_mask_rows.clear()
    viewer._last_mask_volume_results.clear()
    viewer._active_mask_index = None
    viewer.active_mask_var.set("")
    viewer._mask_click_points.clear()
    viewer._pit_polygons.clear()
    viewer._pit_visibility_vars.clear()
    viewer._active_pit_index = None
    viewer.active_pit_var.set("")
    viewer._pit_settings = PitSettings()
    viewer._pit_click_points.clear()
    viewer._notes.clear()
    viewer._note_visibility_vars.clear()
    viewer._active_note_index = None
    viewer._cancel_pending_note_single_click()
    viewer._ditch_rules.clear()
    viewer._ditch_layer_catalog = DitchLayerCatalog()
    if viewer._mask_mode:
        viewer._end_mask_mode()
    if getattr(viewer, "_ditch_mode", False):
        viewer._end_ditch_mode()
    if viewer._mask_edit_mode:
        viewer._end_mask_edit_mode()
    if getattr(viewer, "_pit_mode", False):
        viewer._end_pit_mode()
    if getattr(viewer, "_pit_edit_mode", False):
        viewer._end_pit_edit_mode()
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
    viewer._ditch_centerlines.clear()
    viewer._clear_active_section_marker(redraw=False)
    viewer._active_section_index = None
    viewer._active_ditch_centerline_index = None
    viewer.active_ditch_centerline_var.set("")
    viewer._measure_results.clear()
    viewer._measure_click_points.clear()
    viewer._masks.clear()
    viewer._mask_visibility_vars.clear()
    viewer._active_mask_index = None
    viewer.active_mask_var.set("")
    viewer._pit_polygons.clear()
    viewer._pit_visibility_vars.clear()
    viewer._active_pit_index = None
    viewer.active_pit_var.set("")
    viewer._pit_settings = PitSettings()
    viewer._pit_click_points.clear()
    viewer._notes.clear()
    viewer._note_visibility_vars.clear()
    viewer._active_note_index = None
    viewer._cancel_pending_note_single_click()
    viewer._raster_layers.clear()
    viewer._active_raster_index = None
    viewer._elevation_raster_index = None
    viewer._latest_section_report_path = None
    viewer._section_volume_bars.clear()
    viewer._ditch_rules.clear()
    viewer._ditch_layer_catalog = DitchLayerCatalog()
    if hasattr(viewer, "runtime_state"):
        viewer.runtime_state.volume_history.clear()
        viewer.runtime_state.last_render_2d_signature = None
        viewer.runtime_state.last_render_3d_signature = None
    if viewer._measure_mode:
        viewer._end_measure_mode()
    if getattr(viewer, "_pit_mode", False):
        viewer._end_pit_mode()
    if getattr(viewer, "_pit_edit_mode", False):
        viewer._end_pit_edit_mode()
    if getattr(viewer, "_ditch_mode", False):
        viewer._end_ditch_mode()
    if viewer._note_mode:
        viewer._end_note_mode()
    clear_cached_mask_volume_results(viewer)
