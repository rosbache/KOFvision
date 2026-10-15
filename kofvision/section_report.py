from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

import numpy as np
from matplotlib.figure import Figure
from kofvision.report_templates import render_report_html

FILE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
]


@dataclass
class ProfileSample:
    profile_id: str
    station_m: float
    center: tuple[float, float]
    start: tuple[float, float]
    end: tuple[float, float]
    length_m: float
    dists_m: np.ndarray
    z_top: np.ndarray
    z_bottom: np.ndarray
    z_reference: Optional[np.ndarray]
    reference_name: Optional[str]
    z_secondary_reference: Optional[np.ndarray]
    secondary_reference_name: Optional[str]
    z_area_top: np.ndarray
    z_area_bottom: np.ndarray
    area_top_name: str
    area_bottom_name: str
    area_net_m2: float
    area_cut_m2: float
    area_fill_m2: float
    area_upper_leg_m2: Optional[float]
    area_upper_leg_cut_m2: Optional[float]
    area_upper_leg_fill_m2: Optional[float]
    area_lower_leg_m2: Optional[float]
    area_lower_leg_cut_m2: Optional[float]
    area_lower_leg_fill_m2: Optional[float]
    valid_fraction: float


class _ProfileSurfaceLike(Protocol):
    name: str

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        ...


def station_values(length_m: float, spacing_m: float) -> np.ndarray:
    if spacing_m <= 0:
        raise ValueError("Spacing must be positive.")
    if length_m <= 0:
        return np.array([0.0], dtype=float)

    count = int(np.floor(length_m / spacing_m))
    values = [i * spacing_m for i in range(count + 1)]
    if not np.isclose(values[-1], length_m):
        values.append(length_m)
    return np.asarray(values, dtype=float)


def profile_segment_from_station(
    baseline_start: tuple[float, float],
    baseline_end: tuple[float, float],
    station_m: float,
    section_length_m: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    y0, x0 = baseline_start
    y1, x1 = baseline_end
    dy = y1 - y0
    dx = x1 - x0
    length = float(np.hypot(dy, dx))
    if length <= 0:
        raise ValueError("Baseline length must be positive.")
    if section_length_m <= 0:
        raise ValueError("Section length must be positive.")

    t = min(max(station_m / length, 0.0), 1.0)
    cy = y0 + dy * t
    cx = x0 + dx * t

    # Unit normal to baseline direction (left side when looking toward
    # increasing station / baseline end).
    ny = -dx / length
    nx = dy / length

    half = section_length_m * 0.5
    # Enforce left -> right order in the section profile.
    start = (cy + ny * half, cx + nx * half)
    end = (cy - ny * half, cx - nx * half)
    return start, end, (cy, cx)


def sample_profile(
    top: _ProfileSurfaceLike,
    bottom: _ProfileSurfaceLike,
    p0: tuple[float, float],
    p1: tuple[float, float],
    *,
    profile_id: str,
    station_m: float,
    center: tuple[float, float],
    n_samples: int = 200,
    reference_surface: Optional[_ProfileSurfaceLike] = None,
    reference_name: Optional[str] = None,
    secondary_reference_surface: Optional[_ProfileSurfaceLike] = None,
    secondary_reference_name: Optional[str] = None,
    area_top_surface: Optional[_ProfileSurfaceLike] = None,
    area_bottom_surface: Optional[_ProfileSurfaceLike] = None,
    area_top_name: Optional[str] = None,
    area_bottom_name: Optional[str] = None,
) -> ProfileSample:
    y0, x0 = p0
    y1, x1 = p1
    total_len = float(np.hypot(y1 - y0, x1 - x0))
    if total_len <= 0:
        raise ValueError("Section length must be positive.")

    ts = np.linspace(0.0, 1.0, n_samples)
    ys = y0 + (y1 - y0) * ts
    xs = x0 + (x1 - x0) * ts
    dists = ts * total_len

    z_top = top.interpolate(ys, xs)
    z_bot = bottom.interpolate(ys, xs)
    z_ref = None
    if reference_surface is not None:
        z_ref = reference_surface.interpolate(ys, xs)
    z_secondary_ref = None
    if secondary_reference_surface is not None:
        z_secondary_ref = secondary_reference_surface.interpolate(ys, xs)

    z_area_top = area_top_surface.interpolate(ys, xs) if area_top_surface is not None else z_top
    z_area_bottom = area_bottom_surface.interpolate(ys, xs) if area_bottom_surface is not None else z_bot
    area_top_label = area_top_name or top.name
    area_bottom_label = area_bottom_name or bottom.name

    diff = z_area_top - z_area_bottom
    valid = np.isfinite(diff)
    valid_fraction = float(valid.mean()) if len(valid) else 0.0
    if valid.any():
        d_valid = dists[valid]
        diff_valid = diff[valid]
        pos = np.clip(diff_valid, 0, None)
        neg = np.clip(diff_valid, None, 0)
        area_cut = float(np.trapezoid(pos, d_valid))
        area_fill = float(-np.trapezoid(neg, d_valid))
        area_net = float(np.trapezoid(diff_valid, d_valid))
    else:
        area_cut = float("nan")
        area_fill = float("nan")
        area_net = float("nan")

    area_upper_leg: Optional[float] = None
    area_upper_leg_cut: Optional[float] = None
    area_upper_leg_fill: Optional[float] = None
    area_lower_leg: Optional[float] = None
    area_lower_leg_cut: Optional[float] = None
    area_lower_leg_fill: Optional[float] = None
    if z_secondary_ref is not None:
        upper_diff = z_top - z_secondary_ref
        upper_valid = np.isfinite(upper_diff)
        if upper_valid.any():
            upper_d = dists[upper_valid]
            upper_delta = upper_diff[upper_valid]
            area_upper_leg = float(np.trapezoid(upper_delta, upper_d))
            area_upper_leg_cut = float(np.trapezoid(np.clip(upper_delta, 0, None), upper_d))
            area_upper_leg_fill = float(-np.trapezoid(np.clip(upper_delta, None, 0), upper_d))

        lower_diff = z_secondary_ref - z_bot
        lower_valid = np.isfinite(lower_diff)
        if lower_valid.any():
            lower_d = dists[lower_valid]
            lower_delta = lower_diff[lower_valid]
            area_lower_leg = float(np.trapezoid(lower_delta, lower_d))
            area_lower_leg_cut = float(np.trapezoid(np.clip(lower_delta, 0, None), lower_d))
            area_lower_leg_fill = float(-np.trapezoid(np.clip(lower_delta, None, 0), lower_d))

    return ProfileSample(
        profile_id=profile_id,
        station_m=float(station_m),
        center=center,
        start=p0,
        end=p1,
        length_m=total_len,
        dists_m=dists,
        z_top=z_top,
        z_bottom=z_bot,
        z_reference=z_ref,
        reference_name=reference_name,
        z_secondary_reference=z_secondary_ref,
        secondary_reference_name=secondary_reference_name,
        z_area_top=z_area_top,
        z_area_bottom=z_area_bottom,
        area_top_name=area_top_label,
        area_bottom_name=area_bottom_label,
        area_net_m2=area_net,
        area_cut_m2=area_cut,
        area_fill_m2=area_fill,
        area_upper_leg_m2=area_upper_leg,
        area_upper_leg_cut_m2=area_upper_leg_cut,
        area_upper_leg_fill_m2=area_upper_leg_fill,
        area_lower_leg_m2=area_lower_leg,
        area_lower_leg_cut_m2=area_lower_leg_cut,
        area_lower_leg_fill_m2=area_lower_leg_fill,
        valid_fraction=valid_fraction,
    )


def save_profile_plot_png(path: str, sample: ProfileSample, *, top_name: str, bottom_name: str) -> None:
    fig = Figure(figsize=(8, 4.5), dpi=120)
    ax = fig.add_subplot(111)

    z_top = sample.z_top
    z_bot = sample.z_bottom

    if np.any(np.isfinite(z_top)):
        ax.plot(sample.dists_m, z_top, color="#1f77b4", linewidth=1.6, label=f"Top: {top_name}")
    if np.any(np.isfinite(z_bot)):
        ax.plot(sample.dists_m, z_bot, color="#d62728", linewidth=1.6, label=f"Bottom: {bottom_name}")
    if sample.z_reference is not None and np.any(np.isfinite(sample.z_reference)):
        ref_name = sample.reference_name or "Reference raster"
        ax.plot(
            sample.dists_m,
            sample.z_reference,
            color="#2ca02c",
            linewidth=1.4,
            linestyle="--",
            label=f"Raster: {ref_name}",
        )
    if sample.z_secondary_reference is not None and np.any(np.isfinite(sample.z_secondary_reference)):
        secondary_name = sample.secondary_reference_name or "Intermediate"
        ax.plot(
            sample.dists_m,
            sample.z_secondary_reference,
            color="#ff7f0e",
            linewidth=1.4,
            linestyle=":",
            label=f"Intermediate: {secondary_name}",
        )

    if sample.z_secondary_reference is not None:
        both_upper = np.isfinite(sample.z_top) & np.isfinite(sample.z_secondary_reference)
        both_lower = np.isfinite(sample.z_secondary_reference) & np.isfinite(sample.z_bottom)
        secondary_name = sample.secondary_reference_name or "Intermediate"
        if both_upper.any():
            ax.fill_between(
                sample.dists_m,
                sample.z_secondary_reference,
                sample.z_top,
                where=both_upper,
                color="#4c78a8",
                alpha=0.20,
                label=f"Upper leg ({top_name} - {secondary_name})",
            )
        if both_lower.any():
            ax.fill_between(
                sample.dists_m,
                sample.z_bottom,
                sample.z_secondary_reference,
                where=both_lower,
                color="#f58518",
                alpha=0.20,
                label=f"Lower leg ({secondary_name} - {bottom_name})",
            )
    else:
        both = np.isfinite(sample.z_area_top) & np.isfinite(sample.z_area_bottom)
        if both.any():
            cut_mask = both & (sample.z_area_top >= sample.z_area_bottom)
            fill_mask = both & (sample.z_area_top < sample.z_area_bottom)
            if cut_mask.any():
                ax.fill_between(
                    sample.dists_m,
                    sample.z_area_bottom,
                    sample.z_area_top,
                    where=cut_mask,
                    color="#1f77b4",
                    alpha=0.22,
                )
            if fill_mask.any():
                ax.fill_between(
                    sample.dists_m,
                    sample.z_area_bottom,
                    sample.z_area_top,
                    where=fill_mask,
                    color="#d62728",
                    alpha=0.22,
                )

    # Show the profile center line (midpoint between start/end section endpoints).
    center_dist = sample.length_m * 0.5
    ax.axvline(center_dist, color="#444444", linestyle="--", linewidth=1.2, alpha=0.9)
    ax.annotate(
        "Center",
        (center_dist, 1.0),
        xycoords=("data", "axes fraction"),
        xytext=(6, -8),
        textcoords="offset points",
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "pad": 1.5},
    )

    ax.set_xlabel("Distance along profile (m) [Left -> Right, looking toward higher station]")
    ax.set_ylabel("Elevation (m)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="best", fontsize=8)
    ax.set_title(
        f"{sample.profile_id} @ {sample.station_m:,.2f} m | "
        f"Area ({sample.area_top_name} - {sample.area_bottom_name}) "
        f"net {sample.area_net_m2:+,.2f} m2 "
        f"(cut {sample.area_cut_m2:,.2f} / fill {sample.area_fill_m2:,.2f})"
    )

    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path)


def save_plan_view_png(
    path: str,
    *,
    files: list[Any],
    show_markers: bool,
    show_lines: bool,
    show_labels: bool,
    show_triangulation: bool,
    sections: list[tuple[tuple[float, float], tuple[float, float]]],
    baseline: tuple[tuple[float, float], tuple[float, float]],
    profile_points: list[tuple[str, tuple[float, float]]],
    raster_img: Optional[np.ndarray],
    raster_extent: Optional[tuple[float, float, float, float]],
    raster_origin: str,
    raster_alpha: float,
) -> None:
    fig = Figure(figsize=(10, 8), dpi=120)
    ax = fig.add_subplot(111)
    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.grid(True, linestyle=":", alpha=0.5)

    if raster_img is not None and raster_extent is not None:
        ax.imshow(
            raster_img,
            extent=raster_extent,
            origin=raster_origin,
            alpha=raster_alpha,
            zorder=0,
        )

    for idx, kof in enumerate(files):
        color = FILE_COLORS[idx % len(FILE_COLORS)]
        if hasattr(kof, "blocks"):
            labeled = False
            for block in kof.blocks:
                if not block.points:
                    continue
                ys = [p.y for p in block.points]
                xs = [p.x for p in block.points]
                label = kof.name if not labeled else None
                labeled = True
                if block.is_polyline and show_lines and len(block.points) >= 2:
                    ax.plot(ys, xs, "-", color=color, linewidth=1.2, label=label)
                    if show_markers:
                        ax.plot(ys, xs, "o", color=color, markersize=3)
                else:
                    marker = "o" if show_markers else "."
                    ax.plot(ys, xs, marker, color=color, markersize=4, linestyle="None", label=label)

                if show_labels:
                    for p in block.points:
                        ax.annotate(
                            p.pkt,
                            (p.y, p.x),
                            fontsize=7,
                            xytext=(3, 3),
                            textcoords="offset points",
                            color=color,
                        )
        elif hasattr(kof, "ensure_triangulation") and kof.ensure_triangulation() and getattr(kof, "_xy", None) is not None:
            ax.plot(
                kof._xy[:, 0],
                kof._xy[:, 1],
                "o" if show_markers else ".",
                color=color,
                markersize=2,
                linestyle="None",
                label=kof.name,
            )

        if show_triangulation and kof.ensure_triangulation():
            kept = kof.kept_simplices
            if kept is not None and len(kept) > 0:
                ax.triplot(kof._xy[:, 0], kof._xy[:, 1], kept, color=color, linewidth=0.4, alpha=0.5)

    for i, ((y0, x0), (y1, x1)) in enumerate(sections):
        ax.plot([y0, y1], [x0, x1], "-", color="#333333", linewidth=1.0, zorder=4)
        ax.annotate(f"S{i + 1}", (y0, x0), fontsize=8, color="#333333", xytext=(4, 4), textcoords="offset points")

    (b0, b1) = baseline
    ax.plot([b0[0], b1[0]], [b0[1], b1[1]], "-", color="#111111", linewidth=2.4, zorder=8, label="Baseline")
    ax.plot([b0[0], b1[0]], [b0[1], b1[1]], "s", color="#111111", markersize=5, zorder=9)

    for profile_id, (cy, cx) in profile_points:
        ax.plot([cy], [cx], "o", color="#b30000", markersize=3, zorder=10)
        ax.annotate(profile_id, (cy, cx), fontsize=7, color="#b30000", xytext=(4, 3), textcoords="offset points")

    ax.set_aspect("equal", adjustable="datalim")
    if files:
        ax.legend(loc="best", fontsize=8)

    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path)


def write_html_report(
    path: str,
    *,
    title: str,
    metadata: dict[str, Any],
    plan_image_rel: str,
    profile_rows: list[dict[str, Any]],
    template_name: str = "standard",
    theme_name: str = "light",
) -> None:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_optional(value: Any, *, signed: bool = False) -> str:
        if value in (None, ""):
            return "-"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "-"
        if not np.isfinite(number):
            return "-"
        if signed:
            return f"{number:+,.2f}"
        return f"{number:,.2f}"

    table_rows = []
    profile_sections = []
    for row in profile_rows:
        profile_id = html.escape(str(row["profile_id"]))
        station = float(row["station_m"])
        length_m = float(row["length_m"])
        valid = float(row["valid_fraction"]) * 100.0
        area_net = float(row["area_net_m2"])
        area_cut = float(row["area_cut_m2"])
        area_fill = float(row["area_fill_m2"])
        area_upper = row.get("area_upper_leg_m2")
        area_lower = row.get("area_lower_leg_m2")
        volume_net = row.get("volume_net_m3")
        volume_cut = row.get("volume_cut_m3")
        volume_fill = row.get("volume_fill_m3")
        volume_upper = row.get("volume_upper_leg_m3")
        volume_upper_cut = row.get("volume_upper_leg_cut_m3")
        volume_upper_fill = row.get("volume_upper_leg_fill_m3")
        volume_lower = row.get("volume_lower_leg_m3")
        volume_lower_cut = row.get("volume_lower_leg_cut_m3")
        volume_lower_fill = row.get("volume_lower_leg_fill_m3")
        img_rel = html.escape(str(row["image_rel"]))

        upper_text = _format_optional(area_upper, signed=True)
        lower_text = _format_optional(area_lower, signed=True)
        vol_net_text = _format_optional(volume_net, signed=True)
        vol_cut_text = _format_optional(volume_cut)
        vol_fill_text = _format_optional(volume_fill)
        vol_upper_text = _format_optional(volume_upper, signed=True)
        vol_upper_cut_text = _format_optional(volume_upper_cut)
        vol_upper_fill_text = _format_optional(volume_upper_fill)
        vol_lower_text = _format_optional(volume_lower, signed=True)
        vol_lower_cut_text = _format_optional(volume_lower_cut)
        vol_lower_fill_text = _format_optional(volume_lower_fill)

        table_rows.append(
            "<tr>"
            f"<td><a href='#{profile_id}'>{profile_id}</a></td>"
            f"<td>{station:,.2f}</td>"
            f"<td>{length_m:,.2f}</td>"
            f"<td>{valid:,.1f}%</td>"
            f"<td>{area_net:+,.2f}</td>"
            f"<td>{area_cut:,.2f}</td>"
            f"<td>{area_fill:,.2f}</td>"
            f"<td>{upper_text}</td>"
            f"<td>{lower_text}</td>"
            f"<td>{vol_net_text}</td>"
            f"<td>{vol_cut_text}</td>"
            f"<td>{vol_fill_text}</td>"
            f"<td>{vol_upper_text}</td>"
            f"<td>{vol_upper_cut_text}</td>"
            f"<td>{vol_upper_fill_text}</td>"
            f"<td>{vol_lower_text}</td>"
            f"<td>{vol_lower_cut_text}</td>"
            f"<td>{vol_lower_fill_text}</td>"
            "</tr>"
        )

        profile_sections.append(
            f"<section id='{profile_id}' class='profile'>"
            f"<h3>{profile_id} at station {station:,.2f} m</h3>"
            f"<img src='{img_rel}' alt='Profile {profile_id}' />"
            "</section>"
        )

        html_text = render_report_html(
                title=title,
                generated=generated,
                metadata=metadata,
                plan_image_rel=plan_image_rel,
                table_rows_html="".join(table_rows),
                profile_sections_html="".join(profile_sections),
                template_name=template_name,
                theme_name=theme_name,
        )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_text)
