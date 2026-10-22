from __future__ import annotations

from typing import Any

import numpy as np

from kofvision.models import KOFFile, VectorSurface, koffile_to_vector_surface
from kofvision.viewer_ui import apply_matplotlib_axes_theme, apply_matplotlib_figure_theme, apply_matplotlib_legend_theme


def initialize_2d_axes(viewer: Any) -> None:
    ax = viewer.ax2d
    ax.clear()
    apply_matplotlib_figure_theme(viewer.fig2d, viewer._theme)
    apply_matplotlib_axes_theme(ax, viewer._theme)
    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")


def _max_label_count(viewer: Any) -> int:
    var = getattr(viewer, "max_labels", None)
    if var is None:
        return 400
    try:
        value = int(var.get())
    except Exception:  # noqa: BLE001
        return 400
    return max(0, value)


def _label_step(count: int, max_labels: int) -> int:
    if max_labels <= 0:
        return 10**9
    if count <= max_labels:
        return 1
    return int(np.ceil(count / max_labels))


def initialize_3d_axes(viewer: Any) -> None:
    ax = viewer.ax3d
    ax.clear()
    apply_matplotlib_figure_theme(viewer.fig3d, viewer._theme)
    apply_matplotlib_axes_theme(ax, viewer._theme, is_3d=True)
    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.set_zlabel("Elevation (H)")


def _draw_rasters(viewer: Any) -> None:
    wms_map = getattr(viewer, "_wms_map", None)
    wms_visible = getattr(viewer, "wms_visible", None)
    if wms_map is not None and wms_visible is not None and wms_visible.get():
        viewer.ax2d.imshow(
            wms_map.image,
            extent=wms_map.extent,
            origin="upper",
            zorder=0,
        )
    if not (viewer.show_raster.get() and viewer._raster_layers):
        return
    for layer in viewer._raster_layers:
        if not layer.visible.get():
            continue
        viewer.ax2d.imshow(
            layer.image,
            extent=layer.extent,
            origin=layer.origin,
            alpha=float(layer.alpha.get()),
            zorder=0,
        )


def _draw_surface_layers(viewer: Any, file_colors: list[str]) -> None:
    for idx, surface in enumerate(viewer.files):
        if not viewer._is_layer_visible(idx):
            continue
        color = file_colors[idx % len(file_colors)]
        _draw_surface_points(viewer, surface, color)
        _draw_surface_triangulation(viewer, surface, color)


def _draw_surface_points(viewer: Any, surface: Any, color: str) -> None:
    vector = _coerce_surface(surface)
    labeled = False
    if vector.line_strings:
        for line_idx, line in enumerate(vector.line_strings):
            if not line:
                continue
            xs = [p[0] for p in line]
            ys = [p[1] for p in line]
            label = vector.name if not labeled else None
            labeled = True
            if viewer.show_lines.get() and len(line) >= 2:
                viewer.ax2d.plot(xs, ys, "-", color=color, linewidth=1.2, label=label)
                if viewer.show_markers.get():
                    viewer.ax2d.plot(xs, ys, "o", color=color, markersize=3)
            elif viewer.show_markers.get():
                viewer.ax2d.plot(xs, ys, "o", color=color, markersize=4, linestyle="None", label=label)

            if viewer.show_labels.get() and vector.point_labels:
                step = _label_step(len(line), _max_label_count(viewer))
                start = 0
                for prev in vector.line_strings[:line_idx]:
                    start += len(prev)
                for pidx, point in enumerate(line):
                    if pidx % step != 0:
                        continue
                    label_idx = start + pidx
                    text = vector.point_labels[label_idx] if label_idx < len(vector.point_labels) else str(label_idx)
                    viewer.ax2d.annotate(
                        text,
                        (point[0], point[1]),
                        fontsize=7,
                        xytext=(3, 3),
                        textcoords="offset points",
                        color=color,
                    )
        return

    if not viewer.show_markers.get() or not vector.points:
        return
    xs = [p[0] for p in vector.points]
    ys = [p[1] for p in vector.points]
    viewer.ax2d.plot(xs, ys, "o", color=color, markersize=3, linestyle="None", label=vector.name)
    if viewer.show_labels.get() and vector.point_labels:
        step = _label_step(len(vector.points), _max_label_count(viewer))
        for pidx, point in enumerate(vector.points):
            if pidx % step != 0:
                continue
            text = vector.point_labels[pidx] if pidx < len(vector.point_labels) else str(pidx)
            viewer.ax2d.annotate(
                text,
                (point[0], point[1]),
                fontsize=7,
                xytext=(3, 3),
                textcoords="offset points",
                color=color,
            )


def _draw_surface_triangulation(viewer: Any, surface: Any, color: str) -> None:
    vector = _coerce_surface(surface)
    draw_tin_faces = len(vector.triangles) > 0 and viewer.show_lines.get()
    if not ((viewer.show_triangulation.get() or draw_tin_faces) and vector.ensure_triangulation()):
        return
    kept = vector.kept_simplices
    if kept is None or len(kept) == 0:
        return
    if draw_tin_faces and viewer.show_tin_fill.get() and vector._xy is not None:
        face_vals = np.zeros(len(kept), dtype=float)
        viewer.ax2d.tripcolor(
            vector._xy[:, 0],
            vector._xy[:, 1],
            face_vals,
            triangles=kept,
            shading="flat",
            vmin=0.0,
            vmax=1.0,
            color=color,
            alpha=float(viewer.tin_fill_alpha.get()),
            zorder=1,
        )
    viewer.ax2d.triplot(
        vector._xy[:, 0],
        vector._xy[:, 1],
        kept,
        color=color,
        linewidth=0.45,
        alpha=0.75,
    )


def _draw_landxml_surface_points(viewer: Any, surface: Any, color: str) -> None:
    vector = _coerce_surface(surface)
    if viewer.show_markers.get() and vector.ensure_triangulation() and vector._xy is not None:
        viewer.ax2d.plot(
            vector._xy[:, 0],
            vector._xy[:, 1],
            "o",
            color=color,
            markersize=2,
            linestyle="None",
            label=vector.name,
        )


def _draw_kof_surface(viewer: Any, surface: KOFFile, color: str) -> None:
    vector = _coerce_surface(surface)
    for line in vector.line_strings:
        if not line:
            continue
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        viewer.ax2d.plot(xs, ys, "-", color=color, linewidth=1.2)


def _draw_alignments_and_stations(viewer: Any, file_colors: list[str]) -> None:
    for lidx, landxml in enumerate(viewer.landxml_files):
        color = file_colors[(len(viewer.files) + lidx) % len(file_colors)]
        labeled = False
        for alignment in landxml.alignments:
            if viewer.show_lines.get() and len(alignment.centerline) >= 2:
                ys = [pt[0] for pt in alignment.centerline]
                xs = [pt[1] for pt in alignment.centerline]
                viewer.ax2d.plot(
                    ys,
                    xs,
                    "-",
                    color=color,
                    linewidth=1.6,
                    alpha=0.85,
                    label=f"{landxml.name}::{alignment.name}" if not labeled else None,
                )
                labeled = True
            if viewer.show_stations.get():
                step = _label_step(len(alignment.stations), _max_label_count(viewer))
                for sidx, station in enumerate(alignment.stations):
                    if sidx % step != 0:
                        continue
                    viewer.ax2d.plot([station.y], [station.x], marker="x", color=color, markersize=4)
                    viewer.ax2d.annotate(
                        station.label,
                        (station.y, station.x),
                        fontsize=7,
                        xytext=(3, 3),
                        textcoords="offset points",
                        color=color,
                    )


def _apply_2d_view(viewer: Any) -> None:
    if viewer.equal_aspect.get():
        viewer.ax2d.set_aspect("equal", adjustable="datalim")
    else:
        viewer.ax2d.set_aspect("auto")

    if viewer._view_extent_2d is not None:
        xmin, xmax, ymin, ymax = viewer._view_extent_2d
        viewer.ax2d.set_xlim(xmin, xmax)
        viewer.ax2d.set_ylim(ymin, ymax)


def _draw_sections(viewer: Any) -> None:
    section_color = viewer._theme.get("mpl_section_line", "#1f2937")
    for idx, ((y0, x0), (y1, x1)) in enumerate(viewer._sections):
        label = viewer._section_label(idx)
        viewer.ax2d.plot([y0, y1], [x0, x1], "-", color=section_color, linewidth=1.6, zorder=5)
        viewer.ax2d.plot([y0, y1], [x0, x1], "s", color=section_color, markersize=4, zorder=6)
        viewer.ax2d.annotate(
            label,
            (y0, x0),
            fontsize=9,
            fontweight="bold",
            color=section_color,
            xytext=(6, 6),
            textcoords="offset points",
            zorder=7,
        )


def _draw_ditch_centerlines(viewer: Any) -> None:
    ditch_color = viewer._theme.get("mpl_section_line", "#1f2937")
    for idx, ((y0, x0), (y1, x1)) in enumerate(viewer._ditch_centerlines):
        is_active = idx == viewer._active_ditch_centerline_index
        label = viewer._ditch_label(idx)
        color = "#8b5e00" if is_active else ditch_color
        width = 2.2 if is_active else 1.3
        viewer.ax2d.plot([y0, y1], [x0, x1], "--", color=color, linewidth=width, zorder=7)
        viewer.ax2d.plot([y0, y1], [x0, x1], "^", color=color, markersize=4, zorder=8)
        viewer.ax2d.annotate(
            label,
            (y0, x0),
            fontsize=8,
            fontweight="bold" if is_active else "normal",
            color=color,
            xytext=(6, -10),
            textcoords="offset points",
            zorder=9,
        )


def _draw_active_ditch_preview(viewer: Any) -> None:
    if not (viewer._ditch_mode and viewer._ditch_click_points):
        return
    arr = np.asarray(viewer._ditch_click_points, dtype=float)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#8b5e00", linewidth=1.3, zorder=13)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#8b5e00", markersize=4, zorder=14)
    viewer.ax2d.annotate(
            f"{label}'",
            (y1, x1),
            fontsize=9,
            fontweight="bold",
            color=section_color,
            xytext=(6, 6),
            textcoords="offset points",
            zorder=7,
        )


def _draw_active_section_marker(viewer: Any) -> None:
    if (
        viewer._active_section_marker_index is None
        or viewer._active_section_marker_chainage_m is None
        or not (0 <= viewer._active_section_marker_index < len(viewer._sections))
    ):
        return

    (y0, x0), (y1, x1) = viewer._sections[viewer._active_section_marker_index]
    dy = y1 - y0
    dx = x1 - x0
    length = float(np.hypot(dy, dx))
    if length <= 0:
        return

    chainage = min(max(viewer._active_section_marker_chainage_m, 0.0), length)
    section_label = viewer._section_label(viewer._active_section_marker_index)
    t = chainage / length
    py = y0 + (dy * t)
    px = x0 + (dx * t)

    xmin, xmax = viewer.ax2d.get_xlim()
    ymin, ymax = viewer.ax2d.get_ylim()
    span = max(abs(xmax - xmin), abs(ymax - ymin), 1e-9)
    tick_half_len = max(0.5, span * 0.012)
    perp_y = -dx / length
    perp_x = dy / length
    y_tick0 = py - (perp_y * tick_half_len)
    x_tick0 = px - (perp_x * tick_half_len)
    y_tick1 = py + (perp_y * tick_half_len)
    x_tick1 = px + (perp_x * tick_half_len)

    viewer.ax2d.plot([y_tick0, y_tick1], [x_tick0, x_tick1], "-", color="#b00020", linewidth=2.0, zorder=16)
    viewer.ax2d.plot([py], [px], "o", color="#b00020", markersize=6, zorder=17)
    viewer.ax2d.annotate(
        f"{section_label} | s={chainage:,.2f} m",
        (py, px),
        fontsize=8,
        color="#b00020",
        xytext=(8, -12),
        textcoords="offset points",
        zorder=18,
    )


def _draw_masks(viewer: Any, file_colors: list[str]) -> None:
    for idx, mask in enumerate(viewer._masks):
        if not viewer._mask_is_visible(idx):
            continue
        if len(mask.vertices) < 3:
            continue
        arr = np.asarray(mask.vertices, dtype=float)
        closed = np.vstack([arr, arr[0]])
        is_active = idx == viewer._active_mask_index
        fill_alpha = 0.20 if is_active else 0.10
        edge_width = 2.4 if is_active else 1.2
        color = file_colors[idx % len(file_colors)]
        viewer.ax2d.fill(closed[:, 0], closed[:, 1], color=color, alpha=fill_alpha, zorder=8)
        viewer.ax2d.plot(closed[:, 0], closed[:, 1], "-", color=color, linewidth=edge_width, zorder=9)
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color=color, markersize=4, zorder=10)
        viewer.ax2d.annotate(
            mask.name,
            (arr[0, 0], arr[0, 1]),
            fontsize=8,
            color=color,
            xytext=(6, 6),
            textcoords="offset points",
            fontweight="bold" if is_active else "normal",
            zorder=11,
        )


def _draw_active_mask_preview(viewer: Any) -> None:
    if not (viewer._mask_mode and viewer._mask_click_points):
        return
    arr = np.asarray(viewer._mask_click_points, dtype=float)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#cc8a00", linewidth=1.3, zorder=11)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00", markersize=4, zorder=12)
    if len(arr) >= 2:
        viewer.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]], ":", color="#cc8a00", linewidth=1.0, zorder=11)


def _draw_pits(viewer: Any) -> None:
    for idx, pit in enumerate(getattr(viewer, "_pit_polygons", [])):
        if not viewer._pit_is_visible(idx):
            continue
        if len(pit.vertices) < 3:
            continue
        arr = np.asarray(pit.vertices, dtype=float)
        closed = np.vstack([arr, arr[0]])
        is_active = idx == viewer._active_pit_index
        color = "#8b1e3f" if is_active else "#b23a48"
        viewer.ax2d.fill(closed[:, 0], closed[:, 1], color=color, alpha=0.08 if not is_active else 0.14, zorder=10)
        viewer.ax2d.plot(closed[:, 0], closed[:, 1], "-", color=color, linewidth=2.1 if is_active else 1.2, zorder=11)
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color=color, markersize=4, zorder=12)
        viewer.ax2d.annotate(
            f"{pit.name} ({pit.target_z:.2f} m)",
            (arr[0, 0], arr[0, 1]),
            fontsize=8,
            color=color,
            xytext=(6, -10),
            textcoords="offset points",
            fontweight="bold" if is_active else "normal",
            zorder=13,
        )


def _draw_pit_slope_end_lines(viewer: Any) -> None:
    viewer._pit_daylight_debug = ""
    pits = getattr(viewer, "_pit_polygons", [])
    if not pits:
        viewer._pit_daylight_debug = "no pits"
        return
    settings = getattr(viewer, "_pit_settings", None)
    if settings is None or bool(getattr(settings, "vertical_wall", False)):
        viewer._pit_daylight_debug = "pit daylight disabled (vertical wall)"
        return
    slope_abs = abs(float(getattr(settings, "edge_slope", 0.0)))
    if slope_abs <= 0.0:
        viewer._pit_daylight_debug = "pit daylight disabled (slope <= 0)"
        return
    top_name = str(viewer.top_var.get()).strip() if hasattr(viewer, "top_var") else ""
    if not top_name:
        viewer._pit_daylight_debug = "pit daylight skipped (no top surface selected)"
        return
    top_surface = viewer._find_volume_surface(top_name) if hasattr(viewer, "_find_volume_surface") else None
    if top_surface is None or not hasattr(top_surface, "xy_bounds"):
        viewer._pit_daylight_debug = "pit daylight skipped (top surface unavailable)"
        return
    top_bounds = top_surface.xy_bounds()
    if top_bounds is None:
        viewer._pit_daylight_debug = "pit daylight skipped (top bounds unavailable)"
        return

    first_label = True
    debug_parts: list[str] = []
    for idx, pit in enumerate(pits):
        if not viewer._pit_is_visible(idx):
            continue
        if len(pit.vertices) < 3:
            debug_parts.append(f"{pit.name}: skipped (<3 vertices)")
            continue
        pit_surface = viewer._pit_surface_for_indices([idx]) if hasattr(viewer, "_pit_surface_for_indices") else None
        if pit_surface is None or not hasattr(pit_surface, "xy_bounds"):
            debug_parts.append(f"{pit.name}: skipped (pit surface unavailable)")
            continue
        pit_bounds = pit_surface.xy_bounds()
        if pit_bounds is None:
            debug_parts.append(f"{pit.name}: skipped (pit bounds unavailable)")
            continue

        y_min = max(float(top_bounds[0]), float(pit_bounds[0]))
        x_min = max(float(top_bounds[1]), float(pit_bounds[1]))
        y_max = min(float(top_bounds[2]), float(pit_bounds[2]))
        x_max = min(float(top_bounds[3]), float(pit_bounds[3]))
        if y_min >= y_max or x_min >= x_max:
            debug_parts.append(f"{pit.name}: skipped (no bbox overlap with top)")
            continue

        vertices = np.asarray(pit.vertices, dtype=float)
        z_top_edge = top_surface.interpolate(vertices[:, 0], vertices[:, 1])
        finite_edge = np.isfinite(z_top_edge)
        if finite_edge.any():
            depth_max = max(float(np.max(z_top_edge[finite_edge] - float(pit.target_z))), 0.0)
        else:
            depth_max = 0.0
        margin = max(2.0, (depth_max / slope_abs) * 1.15 if depth_max > 0 else 2.0)

        y_min = max(float(top_bounds[0]), y_min - margin)
        x_min = max(float(top_bounds[1]), x_min - margin)
        y_max = min(float(top_bounds[2]), y_max + margin)
        x_max = min(float(top_bounds[3]), x_max + margin)
        if y_min >= y_max or x_min >= x_max:
            continue

        color = "#d90429" if idx == getattr(viewer, "_active_pit_index", None) else "#b23a48"
        label = f"Pit slope end: {pit.name}" if first_label else None
        draw_line_width = 2.4 if idx == getattr(viewer, "_active_pit_index", None) else 1.6

        margin_scale = 1.0
        drew_contour = False
        min_delta = float("nan")
        max_delta = float("nan")
        for _attempt in range(6):
            yy_min = max(float(top_bounds[0]), y_min - (margin * (margin_scale - 1.0)))
            xx_min = max(float(top_bounds[1]), x_min - (margin * (margin_scale - 1.0)))
            yy_max = min(float(top_bounds[2]), y_max + (margin * (margin_scale - 1.0)))
            xx_max = min(float(top_bounds[3]), x_max + (margin * (margin_scale - 1.0)))
            if yy_min >= yy_max or xx_min >= xx_max:
                break

            span_y = yy_max - yy_min
            span_x = xx_max - xx_min
            n_y = int(np.clip(np.ceil(span_y / 0.5), 180, 700))
            n_x = int(np.clip(np.ceil(span_x / 0.5), 180, 700))
            ys = np.linspace(yy_min, yy_max, n_y)
            xs = np.linspace(xx_min, xx_max, n_x)
            yy, xx = np.meshgrid(ys, xs, indexing="xy")
            z_top = top_surface.interpolate(yy, xx)
            z_pit = pit_surface.interpolate(yy, xx)
            finite = np.isfinite(z_top) & np.isfinite(z_pit)
            if np.count_nonzero(finite) < 20:
                debug_parts.append(f"{pit.name}: finite overlap too small")
                break
            delta = np.where(finite, z_top - z_pit, np.nan)
            finite_delta = delta[finite]
            if finite_delta.size == 0:
                debug_parts.append(f"{pit.name}: no finite delta samples")
                break
            min_delta = float(np.min(finite_delta))
            max_delta = float(np.max(finite_delta))
            has_crossing = min_delta <= 0.0 <= max_delta
            if has_crossing:
                viewer.ax2d.contour(
                    yy,
                    xx,
                    delta,
                    levels=[0.0],
                    colors=[color],
                    linewidths=[draw_line_width],
                    linestyles=["-"],
                    zorder=16,
                )
                if label is not None:
                    # Add a lightweight proxy artist for legend text once.
                    viewer.ax2d.plot([], [], "-", color=color, linewidth=2.0, label=label)
                    first_label = False
                drew_contour = True
                debug_parts.append(
                    f"{pit.name}: daylight yes; depth={depth_max:.2f}m slope={slope_abs:.3f} "
                    f"delta=[{min_delta:.3f},{max_delta:.3f}]"
                )
                break

            # If pit is still entirely below top surface in this window, expand
            # search radius and try again; this catches shallow slopes where the
            # daylight line sits far outside the pit border.
            if min_delta > 0.0:
                full_y_covered = abs(yy_min - float(top_bounds[0])) < 1e-9 and abs(yy_max - float(top_bounds[2])) < 1e-9
                full_x_covered = abs(xx_min - float(top_bounds[1])) < 1e-9 and abs(xx_max - float(top_bounds[3])) < 1e-9
                if full_y_covered and full_x_covered:
                    break
                margin_scale *= 1.8
                continue
            break

        if not drew_contour:
            if np.isfinite(min_delta) and np.isfinite(max_delta):
                debug_parts.append(
                    f"{pit.name}: daylight no; depth={depth_max:.2f}m slope={slope_abs:.3f} "
                    f"delta=[{min_delta:.3f},{max_delta:.3f}]"
                )
            continue
    viewer._pit_daylight_debug = " | ".join(debug_parts)


def _draw_active_pit_preview(viewer: Any) -> None:
    if not (getattr(viewer, "_pit_mode", False) and getattr(viewer, "_pit_click_points", [])):
        return
    arr = np.asarray(viewer._pit_click_points, dtype=float)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#b23a48", linewidth=1.3, zorder=14)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#b23a48", markersize=4, zorder=15)
    if len(arr) >= 2:
        viewer.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]], ":", color="#b23a48", linewidth=1.0, zorder=14)


def _draw_measurements(viewer: Any) -> None:
    for idx, item in enumerate(viewer._measure_results):
        kind = item.get("kind", "")
        points = item.get("points", [])
        if not isinstance(points, list) or len(points) < 2:
            continue
        arr = np.asarray(points, dtype=float)
        label = viewer._measurement_label(idx)
        color = "#0b7285"

        if kind == "line" and len(arr) >= 2:
            p0 = arr[0]
            p1 = arr[1]
            viewer.ax2d.plot([p0[0], p1[0]], [p0[1], p1[1]], "-", color=color, linewidth=1.8, zorder=12)
            viewer.ax2d.plot([p0[0], p1[0]], [p0[1], p1[1]], "o", color=color, markersize=4, zorder=13)
            mid = (0.5 * (p0[0] + p1[0]), 0.5 * (p0[1] + p1[1]))
            dist = float(item.get("length_m", viewer._measure_line_length((p0[0], p0[1]), (p1[0], p1[1]))))
            viewer.ax2d.annotate(
                f"{label}: {dist:,.2f} m",
                mid,
                fontsize=8,
                color=color,
                xytext=(6, 6),
                textcoords="offset points",
                zorder=14,
            )
        elif kind == "polygon" and len(arr) >= 3:
            closed = np.vstack([arr, arr[0]])
            viewer.ax2d.fill(closed[:, 0], closed[:, 1], color=color, alpha=0.10, zorder=11)
            viewer.ax2d.plot(closed[:, 0], closed[:, 1], "-", color=color, linewidth=1.6, zorder=12)
            viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color=color, markersize=3.5, zorder=13)
            area = float(item.get("area_m2", viewer._measure_polygon_area([(float(p[0]), float(p[1])) for p in arr])))
            perimeter = float(item.get("perimeter_m", viewer._measure_polygon_perimeter([(float(p[0]), float(p[1])) for p in arr])))
            center = (float(np.mean(arr[:, 0])), float(np.mean(arr[:, 1])))
            viewer.ax2d.annotate(
                f"{label}: {area:,.2f} m2 | {perimeter:,.2f} m",
                center,
                fontsize=8,
                color=color,
                xytext=(6, 6),
                textcoords="offset points",
                zorder=14,
            )


def _draw_active_measurement_preview(viewer: Any) -> None:
    if not (viewer._measure_mode and viewer._measure_click_points):
        return
    arr = np.asarray(viewer._measure_click_points, dtype=float)
    kind = viewer._current_measure_mode_key()
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#0b7285", linewidth=1.4, zorder=14)
    viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#0b7285", markersize=4, zorder=15)
    if kind == "polygon" and len(arr) >= 2:
        viewer.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]], ":", color="#0b7285", linewidth=1.0, zorder=14)


def _draw_notes(viewer: Any) -> None:
    for idx, note in enumerate(viewer._notes):
        if not note.visible:
            continue
        is_active = idx == viewer._active_note_index
        color = "#b23a48" if is_active else "#495057"
        edge_width = 2.0 if is_active else 1.2
        marker_size = 11 if is_active else 9
        viewer.ax2d.plot(
            [note.y],
            [note.x],
            "o",
            markerfacecolor="#fff9db",
            markeredgecolor=color,
            markeredgewidth=edge_width,
            markersize=marker_size,
            zorder=19,
        )
        viewer.ax2d.annotate(
            "N",
            (note.y, note.x),
            ha="center",
            va="center",
            fontsize=7,
            fontweight="bold",
            color=color,
            zorder=20,
        )
        viewer.ax2d.annotate(
            note.title,
            (note.y, note.x),
            fontsize=8,
            color=color,
            xytext=(8, 8),
            textcoords="offset points",
            fontweight="bold" if is_active else "normal",
            zorder=21,
        )


def _draw_2d_legend_or_empty_state(viewer: Any) -> None:
    if viewer.files or viewer.landxml_files:
        viewer.ax2d.legend(loc="best", fontsize=8)
        apply_matplotlib_legend_theme(viewer.ax2d, viewer._theme)
        return
    viewer.ax2d.text(
        0.5,
        0.5,
        "Open a .kof or .xml file to begin",
        transform=viewer.ax2d.transAxes,
        ha="center",
        va="center",
        color=viewer._theme.get("mpl_empty_state", "gray"),
    )


def render_2d(viewer: Any, file_colors: list[str]) -> None:
    initialize_2d_axes(viewer)
    _draw_rasters(viewer)
    _draw_surface_layers(viewer, file_colors)
    _draw_alignments_and_stations(viewer, file_colors)
    _apply_2d_view(viewer)
    _draw_sections(viewer)
    _draw_ditch_centerlines(viewer)
    _draw_active_section_marker(viewer)
    _draw_active_ditch_preview(viewer)
    _draw_masks(viewer, file_colors)
    _draw_active_mask_preview(viewer)
    _draw_pits(viewer)
    _draw_pit_slope_end_lines(viewer)
    _draw_active_pit_preview(viewer)
    _draw_measurements(viewer)
    _draw_active_measurement_preview(viewer)
    _draw_notes(viewer)
    _draw_2d_legend_or_empty_state(viewer)
    viewer.fig2d.tight_layout()
    viewer.canvas2d.draw_idle()


def _draw_3d_surface_layers(viewer: Any, file_colors: list[str]) -> None:
    for idx, surface in enumerate(viewer.files):
        if not viewer._is_layer_visible(idx):
            continue
        color = file_colors[idx % len(file_colors)]
        vector = _coerce_surface(surface)
        labeled = False
        if viewer.show_triangulation.get() and vector.ensure_triangulation():
            kept = vector.kept_simplices
            if kept is not None and len(kept) > 0:
                viewer.ax3d.plot_trisurf(
                    vector._xy[:, 0],
                    vector._xy[:, 1],
                    vector._z,
                    triangles=kept,
                    color=color,
                    alpha=0.35,
                    linewidth=0.2,
                    edgecolor=color,
                )
        for line in vector.line_strings:
            if not line:
                continue
            xs = [p[0] for p in line]
            ys = [p[1] for p in line]
            zs = [p[2] for p in line]
            label = vector.name if not labeled else None
            labeled = True
            if viewer.show_lines.get() and len(line) >= 2:
                viewer.ax3d.plot(xs, ys, zs, "-", color=color, linewidth=1.2, label=label)
                if viewer.show_markers.get():
                    viewer.ax3d.scatter(xs, ys, zs, color=color, s=10)
            elif viewer.show_markers.get():
                viewer.ax3d.scatter(xs, ys, zs, color=color, s=12, label=label)

        if not vector.line_strings and viewer.show_markers.get():
            viewer.ax3d.scatter(
                [p[0] for p in vector.points],
                [p[1] for p in vector.points],
                [p[2] for p in vector.points],
                color=color,
                s=10,
                label=vector.name if not labeled else None,
            )
        if viewer.show_labels.get() and vector.point_labels:
            for idx, (y, x, h) in enumerate(vector.points):
                label = vector.point_labels[idx] if idx < len(vector.point_labels) else str(idx)
                viewer.ax3d.text(y, x, h, label, fontsize=7, color=color)


def _coerce_surface(surface: Any) -> VectorSurface:
    if isinstance(surface, VectorSurface):
        return surface
    if isinstance(surface, KOFFile):
        return koffile_to_vector_surface(surface)
    if hasattr(surface, "points") and hasattr(surface, "path"):
        return VectorSurface(
            path=str(surface.path),
            points=[(float(y), float(x), float(z)) for (y, x, z) in surface.points],
            line_strings=[[(float(y), float(x), float(z)) for (y, x, z) in line] for line in getattr(surface, "line_strings", [])],
            triangles=[tuple(int(i) for i in tri) for tri in getattr(surface, "triangles", getattr(surface, "faces", []))],
            layer_name=getattr(surface, "layer_name", None),
            source_kind=str(getattr(surface, "source_kind", "vector")),
            point_labels=[str(v) for v in getattr(surface, "point_labels", [])],
            point_codes=[str(v) for v in getattr(surface, "point_codes", [])],
        )
    raise TypeError(f"Unsupported surface type: {type(surface).__name__}")


def _draw_3d_volume_bars(viewer: Any) -> None:
    if not viewer.show_volume_bars.get():
        return

    upper_labeled = False
    lower_labeled = False
    for bar in viewer._section_volume_bars:
        width = float(bar["width_m"])
        depth = float(bar.get("depth_m", width))
        y = float(bar["y"]) - width * 0.5
        x = float(bar["x"]) - depth * 0.5

        upper_height = float(bar["upper_height_m"])
        upper_base = float(bar["upper_base_z"])
        if viewer.show_upper_leg_bars.get() and np.isfinite(upper_height) and np.isfinite(upper_base):
            viewer.ax3d.bar3d(
                y,
                x,
                upper_base,
                width,
                depth,
                upper_height,
                color="#4c78a8",
                alpha=0.78,
                shade=True,
                label="Upper leg / cut volume" if not upper_labeled else None,
            )
            upper_labeled = True

        lower_height = float(bar["lower_height_m"])
        lower_base = float(bar["lower_base_z"])
        if viewer.show_lower_leg_bars.get() and np.isfinite(lower_height) and np.isfinite(lower_base):
            viewer.ax3d.bar3d(
                y,
                x,
                lower_base,
                width,
                depth,
                lower_height,
                color="#f58518",
                alpha=0.78,
                shade=True,
                label="Lower leg / fill volume" if not lower_labeled else None,
            )
            lower_labeled = True


def _draw_3d_legend_or_empty_state(viewer: Any) -> None:
    if viewer.files:
        viewer.ax3d.legend(loc="best", fontsize=8)
        apply_matplotlib_legend_theme(viewer.ax3d, viewer._theme)
        viewer._equalize_3d_aspect()
        return
    viewer.ax3d.text2D(
        0.5,
        0.5,
        "Open a surface-enabled file to begin",
        transform=viewer.ax3d.transAxes,
        ha="center",
        va="center",
        color=viewer._theme.get("mpl_empty_state", "gray"),
    )


def render_3d(viewer: Any, file_colors: list[str]) -> None:
    initialize_3d_axes(viewer)
    _draw_3d_surface_layers(viewer, file_colors)
    _draw_3d_volume_bars(viewer)
    _draw_3d_legend_or_empty_state(viewer)
    viewer.fig3d.tight_layout()
    viewer.canvas3d.draw_idle()
