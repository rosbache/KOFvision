from __future__ import annotations

from typing import Any

import numpy as np

from kofvision.models import KOFFile, LandXMLSurface


def initialize_2d_axes(ax: Any) -> None:
    ax.clear()
    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.grid(True, linestyle=":", alpha=0.5)


def initialize_3d_axes(ax: Any) -> None:
    ax.clear()
    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.set_zlabel("Elevation (H)")


def render_2d(viewer: Any, file_colors: list[str]) -> None:
    initialize_2d_axes(viewer.ax2d)

    if viewer.show_raster.get() and viewer._raster_layers:
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

    for idx, kof in enumerate(viewer.files):
        if not viewer._is_layer_visible(idx):
            continue
        color = file_colors[idx % len(file_colors)]
        if isinstance(kof, KOFFile):
            labeled = False
            for block in kof.blocks:
                if not block.points:
                    continue
                xs = [p.y for p in block.points]
                ys = [p.x for p in block.points]
                label = kof.name if not labeled else None
                labeled = True
                if block.is_polyline and viewer.show_lines.get() and len(block.points) >= 2:
                    viewer.ax2d.plot(xs, ys, "-", color=color, linewidth=1.2, label=label)
                    if viewer.show_markers.get():
                        viewer.ax2d.plot(xs, ys, "o", color=color, markersize=3)
                else:
                    marker = "o" if viewer.show_markers.get() else "."
                    viewer.ax2d.plot(xs, ys, marker, color=color, markersize=4, linestyle="None", label=label)

                if viewer.show_labels.get():
                    for point in block.points:
                        viewer.ax2d.annotate(
                            point.pkt,
                            (point.y, point.x),
                            fontsize=7,
                            xytext=(3, 3),
                            textcoords="offset points",
                            color=color,
                        )
        else:
            if viewer.show_markers.get() and kof.ensure_triangulation() and kof._xy is not None:
                viewer.ax2d.plot(
                    kof._xy[:, 0],
                    kof._xy[:, 1],
                    "o",
                    color=color,
                    markersize=2,
                    linestyle="None",
                    label=kof.name,
                )

        draw_tin_faces = isinstance(kof, LandXMLSurface) and len(kof.faces) > 0 and viewer.show_lines.get()
        if (viewer.show_triangulation.get() or draw_tin_faces) and kof.ensure_triangulation():
            kept = kof.kept_simplices
            if kept is not None and len(kept) > 0:
                if draw_tin_faces and viewer.show_tin_fill.get() and kof._xy is not None:
                    face_vals = np.zeros(len(kept), dtype=float)
                    viewer.ax2d.tripcolor(
                        kof._xy[:, 0],
                        kof._xy[:, 1],
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
                    kof._xy[:, 0],
                    kof._xy[:, 1],
                    kept,
                    color=color,
                    linewidth=0.45,
                    alpha=0.75,
                )

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
                for station in alignment.stations:
                    viewer.ax2d.plot([station.y], [station.x], marker="x", color=color, markersize=4)
                    viewer.ax2d.annotate(
                        station.label,
                        (station.y, station.x),
                        fontsize=7,
                        xytext=(3, 3),
                        textcoords="offset points",
                        color=color,
                    )

    if viewer.equal_aspect.get():
        viewer.ax2d.set_aspect("equal", adjustable="datalim")
    else:
        viewer.ax2d.set_aspect("auto")

    if viewer._view_extent_2d is not None:
        xmin, xmax, ymin, ymax = viewer._view_extent_2d
        viewer.ax2d.set_xlim(xmin, xmax)
        viewer.ax2d.set_ylim(ymin, ymax)

    for idx, ((y0, x0), (y1, x1)) in enumerate(viewer._sections):
        label = viewer._section_label(idx)
        viewer.ax2d.plot([y0, y1], [x0, x1], "-", color="black", linewidth=1.6, zorder=5)
        viewer.ax2d.plot([y0, y1], [x0, x1], "s", color="black", markersize=4, zorder=6)
        viewer.ax2d.annotate(
            label,
            (y0, x0),
            fontsize=9,
            fontweight="bold",
            color="black",
            xytext=(6, 6),
            textcoords="offset points",
            zorder=7,
        )
        viewer.ax2d.annotate(
            f"{label}'",
            (y1, x1),
            fontsize=9,
            fontweight="bold",
            color="black",
            xytext=(6, 6),
            textcoords="offset points",
            zorder=7,
        )

    if (
        viewer._active_section_marker_index is not None
        and viewer._active_section_marker_chainage_m is not None
        and 0 <= viewer._active_section_marker_index < len(viewer._sections)
    ):
        (y0, x0), (y1, x1) = viewer._sections[viewer._active_section_marker_index]
        dy = y1 - y0
        dx = x1 - x0
        length = float(np.hypot(dy, dx))
        if length > 0:
            chainage = min(max(viewer._active_section_marker_chainage_m, 0.0), length)
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
                f"s={chainage:,.2f} m",
                (py, px),
                fontsize=8,
                color="#b00020",
                xytext=(8, -12),
                textcoords="offset points",
                zorder=18,
            )

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

    if viewer._mask_mode and viewer._mask_click_points:
        arr = np.asarray(viewer._mask_click_points, dtype=float)
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#cc8a00", linewidth=1.3, zorder=11)
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00", markersize=4, zorder=12)
        if len(arr) >= 2:
            viewer.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]], ":", color="#cc8a00", linewidth=1.0, zorder=11)

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
            peri = float(item.get("perimeter_m", viewer._measure_polygon_perimeter([(float(p[0]), float(p[1])) for p in arr])))
            center = (float(np.mean(arr[:, 0])), float(np.mean(arr[:, 1])))
            viewer.ax2d.annotate(
                f"{label}: {area:,.2f} m2 | {peri:,.2f} m",
                center,
                fontsize=8,
                color=color,
                xytext=(6, 6),
                textcoords="offset points",
                zorder=14,
            )

    if viewer._measure_mode and viewer._measure_click_points:
        arr = np.asarray(viewer._measure_click_points, dtype=float)
        kind = viewer._current_measure_mode_key()
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#0b7285", linewidth=1.4, zorder=14)
        viewer.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#0b7285", markersize=4, zorder=15)
        if kind == "polygon" and len(arr) >= 2:
            viewer.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]], ":", color="#0b7285", linewidth=1.0, zorder=14)

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

    if viewer.files or viewer.landxml_files:
        viewer.ax2d.legend(loc="best", fontsize=8)
    else:
        viewer.ax2d.text(
            0.5,
            0.5,
            "Open a .kof or .xml file to begin",
            transform=viewer.ax2d.transAxes,
            ha="center",
            va="center",
            color="gray",
        )

    viewer.fig2d.tight_layout()
    viewer.canvas2d.draw_idle()


def render_3d(viewer: Any, file_colors: list[str]) -> None:
    initialize_3d_axes(viewer.ax3d)

    for idx, kof in enumerate(viewer.files):
        if not viewer._is_layer_visible(idx):
            continue
        color = file_colors[idx % len(file_colors)]
        labeled = False
        if viewer.show_triangulation.get() and hasattr(kof, "ensure_triangulation") and kof.ensure_triangulation():
            kept = kof.kept_simplices
            if kept is not None and len(kept) > 0:
                viewer.ax3d.plot_trisurf(
                    kof._xy[:, 0],
                    kof._xy[:, 1],
                    kof._z,
                    triangles=kept,
                    color=color,
                    alpha=0.35,
                    linewidth=0.2,
                    edgecolor=color,
                )
        if isinstance(kof, KOFFile):
            for block in kof.blocks:
                if not block.points:
                    continue
                xs = [p.y for p in block.points]
                ys = [p.x for p in block.points]
                zs = [p.h for p in block.points]
                label = kof.name if not labeled else None
                labeled = True
                if block.is_polyline and viewer.show_lines.get() and len(block.points) >= 2:
                    viewer.ax3d.plot(xs, ys, zs, "-", color=color, linewidth=1.2, label=label)
                    if viewer.show_markers.get():
                        viewer.ax3d.scatter(xs, ys, zs, color=color, s=10)
                else:
                    if viewer.show_markers.get():
                        viewer.ax3d.scatter(xs, ys, zs, color=color, s=12, label=label)

                if viewer.show_labels.get():
                    for point in block.points:
                        viewer.ax3d.text(point.y, point.x, point.h, point.pkt, fontsize=7, color=color)
        else:
            if kof.ensure_triangulation() and kof._xy is not None and kof._z is not None and viewer.show_markers.get():
                viewer.ax3d.scatter(kof._xy[:, 0], kof._xy[:, 1], kof._z, color=color, s=10, label=kof.name)

    if viewer.files:
        viewer.ax3d.legend(loc="best", fontsize=8)
        viewer._equalize_3d_aspect()
    else:
        viewer.ax3d.text2D(
            0.5,
            0.5,
            "Open a surface-enabled file to begin",
            transform=viewer.ax3d.transAxes,
            ha="center",
            va="center",
            color="gray",
        )

    viewer.fig3d.tight_layout()
    viewer.canvas3d.draw_idle()
