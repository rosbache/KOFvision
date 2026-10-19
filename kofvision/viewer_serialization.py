from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from kofvision.ditch import DitchLayerCatalog, ditch_rules_to_payload, parse_ditch_rules
from kofvision.pit import parse_pit_polygons, parse_pit_settings, pit_polygons_to_payload, pit_settings_to_payload
from kofvision.viewer_state import reset_for_project_load

PROJECT_TYPE = "kofvision.project"
PROJECT_VERSION = 3
SUPPORTED_PROJECT_VERSIONS = {1, 2, PROJECT_VERSION}


class ProjectPayloadError(ValueError):
    """Raised when project payload metadata is invalid."""


def migrate_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate older payloads to the latest schema version."""
    version = int(payload.get("version", 0))
    upgraded = dict(payload)
    if version == PROJECT_VERSION:
        return payload

    if version == 1:
        upgraded.setdefault("volume_history", [])
        upgraded.setdefault("saved_preferences", {})
        upgraded.setdefault("pit_polygons", [])
        upgraded.setdefault("pit_settings", {})
        upgraded.setdefault("active_pit", "")
        version = 2

    if version == 2:
        upgraded.setdefault("pit_polygons", [])
        upgraded.setdefault("pit_settings", {})
        upgraded.setdefault("active_pit", "")
        upgraded.setdefault("pit_compute_scope", "selected")
        upgraded["version"] = PROJECT_VERSION
        return upgraded

    raise ProjectPayloadError(f"Unsupported project version: {payload.get('version')}")


def build_project_payload(viewer: Any) -> dict[str, Any]:
    source_files: list[str] = []
    seen_sources: set[str] = set()
    for path in viewer._surface_source_paths:
        if not path or path in seen_sources:
            continue
        seen_sources.add(path)
        source_files.append(path)

    return {
        "type": PROJECT_TYPE,
        "version": PROJECT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source_files": source_files,
        "surface_layers": [
            {
                "name": surface.name,
                "source_path": viewer._surface_source_paths[idx] if idx < len(viewer._surface_source_paths) else None,
                "owner_path": viewer._surface_owner_paths[idx] if idx < len(viewer._surface_owner_paths) else None,
                "visible": bool(viewer.layer_vars[idx].get()) if idx < len(viewer.layer_vars) else True,
            }
            for idx, surface in enumerate(viewer.files)
        ],
        "landxml_groups": {
            path: bool(var.get())
            for path, var in viewer._landxml_group_vars.items()
        },
        "raster_layers": [
            {
                "path": layer.path,
                "visible": bool(layer.visible.get()),
                "alpha": float(layer.alpha.get()),
                "is_elevation": (idx == viewer._elevation_raster_index),
            }
            for idx, layer in enumerate(viewer._raster_layers)
        ],
        "show_raster": bool(viewer.show_raster.get()),
        "top_surface": viewer.top_var.get(),
        "bottom_surface": viewer.bottom_var.get(),
        "volume_source_mode": viewer.volume_source_mode.get(),
        "volume_method": viewer.volume_method.get(),
        "intermediate_type": viewer.intermediate_type.get(),
        "intermediate_kof": viewer.intermediate_kof_var.get(),
        "intermediate_raster": viewer.intermediate_raster_var.get(),
        "intermediate_constant_z": float(viewer.intermediate_constant_z.get()),
        "mask_compute_scope": viewer.mask_compute_scope.get(),
        "pit_compute_scope": viewer.pit_compute_scope.get() if hasattr(viewer, "pit_compute_scope") else "selected",
        "sections": [
            {
                "start": [float(start[0]), float(start[1])],
                "end": [float(end[0]), float(end[1])],
            }
            for start, end in viewer._sections
        ],
        "ditch_centerlines": [
            {
                "start": [float(start[0]), float(start[1])],
                "end": [float(end[0]), float(end[1])],
            }
            for start, end in getattr(viewer, "_ditch_centerlines", [])
        ],
        "active_ditch_centerline_index": getattr(viewer, "_active_ditch_centerline_index", None),
        "masks": [
            {
                "name": mask.name,
                "visible": bool(mask.visible),
                "vertices": [[float(y), float(x)] for y, x in mask.vertices],
            }
            for mask in viewer._masks
            if len(mask.vertices) >= 3
        ],
        "pit_polygons": pit_polygons_to_payload(getattr(viewer, "_pit_polygons", [])),
        "pit_settings": pit_settings_to_payload(getattr(viewer, "_pit_settings", parse_pit_settings(None))),
        "active_mask": viewer.active_mask_var.get(),
        "active_pit": viewer.active_pit_var.get() if hasattr(viewer, "active_pit_var") else "",
        "volume_history": list(getattr(getattr(viewer, "runtime_state", None), "volume_history", [])),
        "saved_preferences": {
            "theme": viewer.theme_mode.get(),
            "volume_method": viewer.volume_method.get(),
            "station_interval_m": float(viewer.station_interval_m.get()),
            "station_label_format": viewer.station_label_format.get(),
            "show_labels": bool(viewer.show_labels.get()),
            "show_markers": bool(viewer.show_markers.get()),
            "show_lines": bool(viewer.show_lines.get()),
            "show_triangulation": bool(viewer.show_triangulation.get()),
        },
        "notes": [
            {
                "title": note.title,
                "text": note.text,
                "y": float(note.y),
                "x": float(note.x),
                "visible": bool(note.visible),
            }
            for note in viewer._notes
        ],
        "ditch_rules": ditch_rules_to_payload(getattr(viewer, "_ditch_rules", [])),
        "ditch_layer_catalog": getattr(viewer, "_ditch_layer_catalog", DitchLayerCatalog()).to_payload(),
    }


def validate_project_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProjectPayloadError("Invalid project file type.")
    if payload.get("type") != PROJECT_TYPE:
        raise ProjectPayloadError("Invalid project file type.")
    version = int(payload.get("version", 0))
    if version not in SUPPORTED_PROJECT_VERSIONS:
        raise ProjectPayloadError(f"Unsupported project version: {payload.get('version')}")
    return migrate_project_payload(payload)


def apply_project_payload(
    viewer: Any,
    payload: dict[str, Any],
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
    allowed_volume_modes: set[str],
    allowed_intermediate_types: set[str],
    allowed_mask_scopes: set[str],
) -> list[str]:
    reset_for_project_load(viewer)

    source_files = payload.get("source_files")
    if not isinstance(source_files, list):
        source_files = []

    missing_sources: list[str] = []
    for raw_path in source_files:
        path = str(raw_path)
        if not path_exists(path):
            missing_sources.append(path)
            continue
        viewer._load_file(path, redraw=False)

    surface_layers_raw = payload.get("surface_layers")
    if isinstance(surface_layers_raw, list) and viewer.files:
        index_by_key: dict[tuple[str, str | None], list[int]] = {}
        for idx, surface in enumerate(viewer.files):
            src = viewer._surface_source_paths[idx] if idx < len(viewer._surface_source_paths) else None
            key = (surface.name, src)
            index_by_key.setdefault(key, []).append(idx)
        for entry in surface_layers_raw:
            if not isinstance(entry, dict):
                continue
            key = (str(entry.get("name", "")), entry.get("source_path"))
            candidates = index_by_key.get(key)
            if not candidates:
                continue
            idx = candidates.pop(0)
            if idx < len(viewer.layer_vars):
                viewer.layer_vars[idx].set(bool(entry.get("visible", True)))

    landxml_groups_raw = payload.get("landxml_groups")
    if isinstance(landxml_groups_raw, dict):
        for path, value in landxml_groups_raw.items():
            var = viewer._landxml_group_vars.get(str(path))
            if var is not None:
                var.set(bool(value))

    raster_layers_raw = payload.get("raster_layers")
    if isinstance(raster_layers_raw, list):
        for entry in raster_layers_raw:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", ""))
            if not path or not path_exists(path):
                continue
            viewer._load_raster(path)
            idx = len(viewer._raster_layers) - 1
            if idx < 0:
                continue
            layer = viewer._raster_layers[idx]
            layer.visible.set(bool(entry.get("visible", True)))
            try:
                layer.alpha.set(float(entry.get("alpha", layer.alpha.get())))
            except (TypeError, ValueError):
                pass
            if bool(entry.get("is_elevation", False)) and layer.elevation_surface is not None:
                viewer._elevation_raster_index = idx

    viewer.show_raster.set(bool(payload.get("show_raster", bool(viewer._raster_layers))))

    sections_raw = payload.get("sections")
    if isinstance(sections_raw, list):
        for item in sections_raw:
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            end = item.get("end")
            if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
                continue
            if len(start) != 2 or len(end) != 2:
                continue
            try:
                p0 = (float(start[0]), float(start[1]))
                p1 = (float(end[0]), float(end[1]))
            except (TypeError, ValueError):
                continue
            viewer._sections.append((p0, p1))
    viewer._active_section_index = 0 if viewer._sections else None

    ditch_centerlines_raw = payload.get("ditch_centerlines")
    if isinstance(ditch_centerlines_raw, list):
        for item in ditch_centerlines_raw:
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            end = item.get("end")
            if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
                continue
            if len(start) != 2 or len(end) != 2:
                continue
            try:
                p0 = (float(start[0]), float(start[1]))
                p1 = (float(end[0]), float(end[1]))
            except (TypeError, ValueError):
                continue
            viewer._ditch_centerlines.append((p0, p1))
    active_ditch = payload.get("active_ditch_centerline_index")
    if isinstance(active_ditch, int) and 0 <= active_ditch < len(viewer._ditch_centerlines):
        viewer._active_ditch_centerline_index = active_ditch
    else:
        viewer._active_ditch_centerline_index = (len(viewer._ditch_centerlines) - 1) if viewer._ditch_centerlines else None
    if hasattr(viewer, "active_ditch_centerline_var"):
        if viewer._active_ditch_centerline_index is not None and hasattr(viewer, "_ditch_label"):
            viewer.active_ditch_centerline_var.set(viewer._ditch_label(viewer._active_ditch_centerline_index))
        else:
            viewer.active_ditch_centerline_var.set("")

    masks_raw = payload.get("masks")
    if isinstance(masks_raw, list):
        for item in masks_raw:
            if not isinstance(item, dict):
                continue
            vertices_raw = item.get("vertices")
            if not isinstance(vertices_raw, list):
                continue
            vertices: list[tuple[float, float]] = []
            for vertex in vertices_raw:
                if not isinstance(vertex, (list, tuple)) or len(vertex) != 2:
                    continue
                try:
                    vertices.append((float(vertex[0]), float(vertex[1])))
                except (TypeError, ValueError):
                    continue
            if len(vertices) < 3:
                continue
            name = str(item.get("name", "")).strip() or None
            visible = bool(item.get("visible", True))
            viewer._append_mask(vertices, name=name, visible=visible)

    active_mask = str(payload.get("active_mask", "")).strip()
    if active_mask:
        for idx, mask in enumerate(viewer._masks):
            if mask.name == active_mask:
                viewer._set_active_mask_index(idx)
                break

    viewer._pit_polygons = parse_pit_polygons(payload.get("pit_polygons"))
    viewer._pit_settings = parse_pit_settings(payload.get("pit_settings"))
    if hasattr(viewer, "_pit_visibility_vars"):
        viewer._pit_visibility_vars = []
    viewer._active_pit_index = None
    active_pit = str(payload.get("active_pit", "")).strip()
    if active_pit:
        for idx, polygon in enumerate(viewer._pit_polygons):
            if polygon.name == active_pit:
                viewer._active_pit_index = idx
                break
    if hasattr(viewer, "active_pit_var"):
        if viewer._active_pit_index is not None:
            viewer.active_pit_var.set(viewer._pit_polygons[viewer._active_pit_index].name)
        else:
            viewer.active_pit_var.set("")

    notes_raw = payload.get("notes")
    if isinstance(notes_raw, list):
        for item in notes_raw:
            if not isinstance(item, dict):
                continue
            try:
                y = float(item.get("y"))
                x = float(item.get("x"))
            except (TypeError, ValueError):
                continue
            title = str(item.get("title", "")).strip()
            text = str(item.get("text", "")).strip()
            if not title and not text:
                continue
            visible = bool(item.get("visible", True))
            viewer._append_note(title=title, text=text, y=y, x=x, visible=visible)

    viewer._ditch_rules = parse_ditch_rules(payload.get("ditch_rules"))
    viewer._ditch_layer_catalog = DitchLayerCatalog.from_payload(payload.get("ditch_layer_catalog"))

    names = [surface.name for surface in viewer.files]
    top_name = str(payload.get("top_surface", ""))
    bottom_name = str(payload.get("bottom_surface", ""))
    if top_name in names:
        viewer.top_var.set(top_name)
    if bottom_name in names:
        viewer.bottom_var.set(bottom_name)

    volume_mode = str(payload.get("volume_source_mode", ""))
    if volume_mode in allowed_volume_modes:
        viewer.volume_source_mode.set(volume_mode)
    volume_method = str(payload.get("volume_method", "")).strip()
    if volume_method in {"raster", "triangles"}:
        viewer.volume_method.set(volume_method)

    intermediate_type = str(payload.get("intermediate_type", ""))
    if intermediate_type in allowed_intermediate_types:
        viewer.intermediate_type.set(intermediate_type)
    viewer.intermediate_kof_var.set(str(payload.get("intermediate_kof", "")))
    viewer.intermediate_raster_var.set(str(payload.get("intermediate_raster", "")))
    try:
        viewer.intermediate_constant_z.set(
            float(payload.get("intermediate_constant_z", viewer.intermediate_constant_z.get()))
        )
    except (TypeError, ValueError):
        pass

    scope = str(payload.get("mask_compute_scope", ""))
    if scope in allowed_mask_scopes:
        viewer.mask_compute_scope.set(scope)

    pit_scope = str(payload.get("pit_compute_scope", "")).strip()
    if hasattr(viewer, "pit_compute_scope") and pit_scope:
        viewer.pit_compute_scope.set(pit_scope)

    history = payload.get("volume_history")
    if isinstance(history, list):
        if hasattr(viewer, "runtime_state"):
            viewer.runtime_state.volume_history = [str(item) for item in history if isinstance(item, str)]

    prefs = payload.get("saved_preferences")
    if isinstance(prefs, dict):
        theme = str(prefs.get("theme", "")).strip().lower()
        if theme in {"dark", "light"}:
            viewer.theme_mode.set(theme)
        method = str(prefs.get("volume_method", "")).strip().lower()
        if method in {"raster", "triangles"}:
            viewer.volume_method.set(method)
        fmt = str(prefs.get("station_label_format", "")).strip().lower()
        if fmt in {"plain", "chainage"}:
            viewer.station_label_format.set(fmt)
        try:
            viewer.station_interval_m.set(float(prefs.get("station_interval_m", viewer.station_interval_m.get())))
        except (TypeError, ValueError):
            pass
        viewer.show_labels.set(bool(prefs.get("show_labels", viewer.show_labels.get())))
        viewer.show_markers.set(bool(prefs.get("show_markers", viewer.show_markers.get())))
        viewer.show_lines.set(bool(prefs.get("show_lines", viewer.show_lines.get())))
        viewer.show_triangulation.set(bool(prefs.get("show_triangulation", viewer.show_triangulation.get())))

    viewer._refresh_file_selectors()
    viewer._refresh_intermediate_raster_selector()
    viewer._refresh_raster_selector()
    viewer._refresh_mask_controls()
    if hasattr(viewer, "_refresh_pit_controls"):
        viewer._refresh_pit_controls()
    viewer._refresh_section_controls()
    if hasattr(viewer, "_refresh_ditch_controls"):
        viewer._refresh_ditch_controls()
    viewer._refresh_layer_controls()
    viewer._on_intermediate_type_change()
    viewer._redraw()

    return missing_sources
