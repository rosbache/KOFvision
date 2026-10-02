from __future__ import annotations

from pathlib import Path
import sys

import matplotlib
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

# Force a non-interactive backend for deterministic image export.
matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kof_viewer import compute_volume, parse_kof  # noqa: E402


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_surfaces(root: Path):
    top = parse_kof(str(root / "12_A (1).kof"))
    bottom = parse_kof(str(root / "12_A(1) (1).kof"))
    top.ensure_triangulation()
    bottom.ensure_triangulation()
    return top, bottom


def _save_figure(fig: Figure, out_path: Path) -> None:
    FigureCanvasAgg(fig)
    fig.tight_layout()
    fig.savefig(out_path, dpi=170)


def _plot_plan_with_triangulation(top, bottom, out_path: Path) -> None:
    fig = Figure(figsize=(10, 7))
    ax = fig.add_subplot(111)

    for surf, color in [(top, "#1f77b4"), (bottom, "#d62728")]:
        if surf._xy is None:
            continue
        kept = surf.kept_simplices
        if kept is not None and len(kept) > 0:
            ax.triplot(
                surf._xy[:, 0],
                surf._xy[:, 1],
                kept,
                color=color,
                linewidth=0.35,
                alpha=0.35,
            )
        ax.scatter(
            surf._xy[:, 0],
            surf._xy[:, 1],
            s=8,
            c=color,
            alpha=0.7,
            label=surf.name,
        )

    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.legend(loc="best", fontsize=9)
    ax.set_title("2D plan view with triangulation (top and bottom surfaces)")

    _save_figure(fig, out_path)


def _plot_volume_heatmap(top, bottom, out_path: Path) -> None:
    result = compute_volume(top, bottom, target_cells=220)
    if result is None:
        raise RuntimeError("Could not compute sample volume for image export.")

    y_min, x_min, y_max, x_max = result.y_min, result.x_min, result.y_max, result.x_max
    span = max(y_max - y_min, x_max - x_min)
    cell = span / 220

    ny = max(int(np.ceil((y_max - y_min) / cell)), 2)
    nx = max(int(np.ceil((x_max - x_min) / cell)), 2)
    ys = np.linspace(y_min + cell / 2, y_max - cell / 2, ny)
    xs = np.linspace(x_min + cell / 2, x_max - cell / 2, nx)
    yy, xx = np.meshgrid(ys, xs, indexing="xy")

    z_top = top.interpolate(yy.ravel(), xx.ravel()).reshape(nx, ny)
    z_bot = bottom.interpolate(yy.ravel(), xx.ravel()).reshape(nx, ny)
    diff = z_top - z_bot

    v = np.nanpercentile(np.abs(diff), 95)
    if not np.isfinite(v) or v <= 0:
        v = 1.0

    fig = Figure(figsize=(10, 7))
    ax = fig.add_subplot(111)

    im = ax.imshow(
        diff,
        origin="lower",
        extent=[ys.min(), ys.max(), xs.min(), xs.max()],
        cmap="coolwarm",
        vmin=-v,
        vmax=v,
        aspect="equal",
        interpolation="nearest",
    )

    cbar = fig.colorbar(im, ax=ax, shrink=0.88)
    cbar.set_label("Elevation difference (top - bottom) [m]")

    ax.set_xlabel("Easting (Y)")
    ax.set_ylabel("Northing (X)")
    ax.set_title(
        "Volume sampling grid (cut/fill map)\n"
        f"Net {result.net:+,.1f} m^3 | Cut {result.cut:,.1f} m^3 | Fill {result.fill:,.1f} m^3"
    )
    ax.grid(False)

    _save_figure(fig, out_path)


def _plot_section_profile(top, bottom, out_path: Path) -> None:
    tb = top.xy_bounds()
    bb = bottom.xy_bounds()
    if tb is None or bb is None:
        raise RuntimeError("Missing bounds for section image export.")

    y_min = max(tb[0], bb[0])
    x_min = max(tb[1], bb[1])
    y_max = min(tb[2], bb[2])
    x_max = min(tb[3], bb[3])

    p0 = (y_min + 0.15 * (y_max - y_min), x_min + 0.30 * (x_max - x_min))
    p1 = (y_min + 0.88 * (y_max - y_min), x_min + 0.75 * (x_max - x_min))

    y0, x0 = p0
    y1, x1 = p1

    n_samples = 260
    ts = np.linspace(0.0, 1.0, n_samples)
    ys = y0 + (y1 - y0) * ts
    xs = x0 + (x1 - x0) * ts
    d = np.hypot(ys - ys[0], xs - xs[0])

    z_top = top.interpolate(ys, xs)
    z_bot = bottom.interpolate(ys, xs)

    both = np.isfinite(z_top) & np.isfinite(z_bot)
    if not np.any(both):
        raise RuntimeError("Section line had no overlapping valid interpolation.")

    fig = Figure(figsize=(10, 5.5))
    ax = fig.add_subplot(111)

    ax.plot(d, z_top, color="#1f77b4", linewidth=1.8, label=f"Top: {top.name}")
    ax.plot(d, z_bot, color="#d62728", linewidth=1.8, label=f"Bottom: {bottom.name}")

    cut_mask = both & (z_top >= z_bot)
    fill_mask = both & (z_top < z_bot)

    if np.any(cut_mask):
        ax.fill_between(d, z_bot, z_top, where=cut_mask, color="#1f77b4", alpha=0.28)
    if np.any(fill_mask):
        ax.fill_between(d, z_bot, z_top, where=fill_mask, color="#d62728", alpha=0.28)

    diff = z_top - z_bot
    dv = d[both]
    delta = diff[both]
    area_cut = float(np.trapezoid(np.clip(delta, 0, None), dv))
    area_fill = float(-np.trapezoid(np.clip(delta, None, 0), dv))
    area_net = float(np.trapezoid(delta, dv))

    ax.set_xlabel("Distance along section (m)")
    ax.set_ylabel("Elevation (m)")
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="best", fontsize=9)
    ax.set_title(
        "Section profile example\n"
        f"Area net {area_net:+,.2f} m^2 | Cut {area_cut:,.2f} m^2 | Fill {area_fill:,.2f} m^2"
    )

    _save_figure(fig, out_path)


def main() -> int:
    root = _resolve_repo_root()
    out_dir = root / "docs" / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    top, bottom = _load_surfaces(root)

    _plot_plan_with_triangulation(top, bottom, out_dir / "plan-triangulation.png")
    _plot_volume_heatmap(top, bottom, out_dir / "volume-diff-heatmap.png")
    _plot_section_profile(top, bottom, out_dir / "section-profile.png")

    print("Wrote:")
    for name in [
        "plan-triangulation.png",
        "volume-diff-heatmap.png",
        "section-profile.png",
    ]:
        print(f"- {out_dir / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
