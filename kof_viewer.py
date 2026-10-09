"""Standalone KOF 2D/3D viewer with triangulation, volume and section tools.

A small Tkinter + matplotlib application for inspecting KOF point files.

Features
--------
- Loads one or more KOF files (via CLI args or the "Open..." button).
- Parses 05 coordinate rows (both ``05 DB Pkt Tk X Y H ...`` and
  ``05 Pkt [Code] X Y H`` variants).
- Preserves polyline grouping using the ``09 91`` (start) and
  ``09 99`` / ``09 96`` (end) delimiters, so points inside a block are
  rendered as a connected line.
- Draws 2D (Easting vs Northing) and 3D (adds elevation) tabs that share
  the same loaded data.
- Toggles for point labels, markers, lines, and triangulation overlay.
- Triangulates loaded files (Delaunay on XY) and computes the volume
  between a selected top and bottom surface over their overlap, with
  cut/fill split.
- Section tool: click two points on the 2D plot to draw a profile that
  shows both surfaces along the line and the area between them.
- Export to GeoPackage (original points and polylines only, no triangles).

Only ``matplotlib``, ``numpy`` and ``scipy`` plus the standard library
(``tkinter``) are required for viewing. GeoPackage export additionally
requires ``geopandas`` and ``shapely``.

Usage
-----
    python kof_viewer.py                       # start empty, use Open...
    python kof_viewer.py file1.kof file2.kof   # preload files
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from tkinter import simpledialog
from typing import Any, Optional
from datetime import datetime

import matplotlib
import numpy as np

matplotlib.use("TkAgg")

from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (registers 3d projection)

from kofvision import compute_volume, compute_volume_with_intermediate, parse_kof, parse_landxml
from kofvision.export import export_kof_files_to_gpkg
from kofvision.models import KOFFile, LandXMLFile, LandXMLStation, LandXMLSurface
from kofvision.mask_io import MaskDataError, load_mask_json, save_mask_json
from kofvision.section_report import (
    profile_segment_from_station,
    sample_profile,
    save_plan_view_png,
    save_profile_plot_png,
    station_values,
    write_html_report,
)
from kofvision.sections_io import SectionDataError, load_sections_json, save_sections_json
from kofvision.volume import ConstantElevationSurface, SplitVolumeResult, SurfaceLike, VolumeResult
from kofvision.raster import ElevationRasterSurface, load_elevation_surface, load_raster_layer

try:
    import customtkinter as ctk
except Exception:  # noqa: BLE001
    ctk = None


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------


FILE_COLORS = [
    "#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
]

VOLUME_MODE_LABELS: dict[str, str] = {
    "kof_vs_kof": "KOF - KOF",
    "top_minus_raster": "Top KOF - Raster",
    "raster_minus_bottom": "Raster - Bottom KOF",
    "raster_minus_top": "Raster - Top KOF",
}
VOLUME_LABEL_TO_MODE: dict[str, str] = {v: k for k, v in VOLUME_MODE_LABELS.items()}

INTERMEDIATE_TYPE_LABELS: dict[str, str] = {
    "none": "None",
    "kof": "KOF layer",
    "raster": "Raster layer",
    "constant": "Constant Z",
}
INTERMEDIATE_LABEL_TO_TYPE: dict[str, str] = {v: k for k, v in INTERMEDIATE_TYPE_LABELS.items()}


@dataclass
class _RasterViewLayer:
    path: str
    image: np.ndarray
    extent: tuple[float, float, float, float]
    origin: str
    visible: tk.BooleanVar
    alpha: tk.DoubleVar
    elevation_surface: Optional[ElevationRasterSurface]


class _HoverTooltip:
    """Small tooltip that appears when hovering over a widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<ButtonPress>", self._hide)

    def _show(self, _event=None) -> None:  # noqa: ANN001
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()
        self.tip_window = tip

    def _hide(self, _event=None) -> None:  # noqa: ANN001
        if self.tip_window is None:
            return
        self.tip_window.destroy()
        self.tip_window = None


class KOFViewer(tk.Tk):
    def __init__(self, initial_files: Optional[list[str]] = None) -> None:
        super().__init__()
        self.title("KOF Viewer – 2D / 3D / Volume / Section")
        self.geometry("1300x860")
        self._ctk_enabled = ctk is not None and os.environ.get("KOFVISION_DISABLE_CTK", "0") != "1"
        if self._ctk_enabled:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("blue")

        self.files: list[KOFFile | LandXMLSurface] = []
        self.landxml_files: list[LandXMLFile] = []
        self.layer_vars: list[tk.BooleanVar] = []
        self._surface_owner_paths: list[Optional[str]] = []
        self._landxml_group_vars: dict[str, tk.BooleanVar] = {}
        self.last_volume: Optional[VolumeResult | SplitVolumeResult] = None

        self.show_labels = tk.BooleanVar(value=False)
        self.show_stations = tk.BooleanVar(value=True)
        self.show_markers = tk.BooleanVar(value=True)
        self.show_lines = tk.BooleanVar(value=True)
        self.show_triangulation = tk.BooleanVar(value=False)
        self.show_tin_fill = tk.BooleanVar(value=True)
        self.tin_fill_alpha = tk.DoubleVar(value=0.14)
        self.equal_aspect = tk.BooleanVar(value=True)
        self.show_raster = tk.BooleanVar(value=False)
        self.raster_alpha = tk.DoubleVar(value=0.7)

        self.top_var = tk.StringVar()
        self.bottom_var = tk.StringVar()
        self.volume_source_mode = tk.StringVar(value=VOLUME_MODE_LABELS["kof_vs_kof"])
        self.volume_method = tk.StringVar(value="raster")
        self.intermediate_type = tk.StringVar(value=INTERMEDIATE_TYPE_LABELS["none"])
        self.intermediate_kof_var = tk.StringVar()
        self.intermediate_raster_var = tk.StringVar()
        self.intermediate_constant_z = tk.DoubleVar(value=0.0)
        self.intermediate_constant_step = tk.DoubleVar(value=0.1)
        self.volume_text = tk.StringVar(value="Volume: (not computed)")
        self.boundary_factor = tk.DoubleVar(value=2.5)
        self.raster_clip_to_kof = tk.BooleanVar(value=False)
        self.raster_clip_buffer_m = tk.DoubleVar(value=10.0)
        self.active_raster_name = tk.StringVar(value="")
        self.station_interval_m = tk.DoubleVar(value=10.0)
        self.station_label_format = tk.StringVar(value="plain")
        self.strict_xsd_validation = tk.BooleanVar(value=False)
        self.xsd_allow_network = tk.BooleanVar(value=True)
        self.xsd_local_schema_path = tk.StringVar(value="")

        # Raster background state (2D tab only)
        self._raster_layers: list[_RasterViewLayer] = []
        self._active_raster_index: Optional[int] = None
        self._elevation_raster_index: Optional[int] = None
        self._intermediate_raster_index_map: dict[str, int] = {}
        self._view_extent_2d: Optional[tuple[float, float, float, float]] = None

        # Section-drawing state
        self._section_mode = False
        self._section_click_points: list[tuple[float, float]] = []
        self._section_cid: Optional[int] = None
        self._section_preview = None  # transient Line2D while drawing
        self._sections: list[tuple[tuple[float, float], tuple[float, float]]] = []

        # Polygon mask state for volume limitation in 2D.
        self._mask_mode = False
        self._mask_click_points: list[tuple[float, float]] = []
        self._mask_polygon: list[tuple[float, float]] = []
        self._mask_cid: Optional[int] = None

        self._configure_styles()

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._bind_shortcuts()

        for path in initial_files or []:
            self._load_file(path, redraw=False)
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = tkfont.nametofont("TkDefaultFont")
        header_font = base_font.copy()
        header_font.configure(weight="bold", size=max(base_font.cget("size"), 10))
        style.configure("TopBar.TFrame", padding=4)
        style.configure("PanelHeader.TLabel", font=header_font)
        style.configure("PanelBody.TFrame", relief="groove", borderwidth=1)
        style.configure("Primary.TButton", padding=(10, 6))

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._invoke_shortcut(self._on_open))
        self.bind_all("<Control-e>", lambda _e: self._invoke_shortcut(self._on_export_gpkg))
        self.bind_all("<Control-Shift-K>", lambda _e: self._invoke_shortcut(self._on_clear))
        self.bind_all("<Control-Shift-Z>", lambda _e: self._invoke_shortcut(self._on_zoom_selected_layers))
        self.bind_all("<Control-Shift-R>", lambda _e: self._invoke_shortcut(self._on_reset_view))
        self.bind_all("<Control-Shift-V>", lambda _e: self._invoke_shortcut(self._on_compute_volume))
        self.bind_all("<Control-Shift-S>", lambda _e: self._invoke_shortcut(self._on_toggle_section))
        self.bind_all("<Control-Shift-M>", lambda _e: self._invoke_shortcut(self._on_toggle_mask))

    @staticmethod
    def _invoke_shortcut(callback) -> str:  # noqa: ANN001
        callback()
        return "break"

    def _make_toolbar_button(
        self,
        parent: tk.Widget,
        *,
        text: str,
        command,
        primary: bool = False,
    ) -> tk.Widget:
        if self._ctk_enabled:
            fg = "#1f6aa5" if primary else None
            btn = ctk.CTkButton(
                parent,
                text=text,
                command=command,
                height=30,
                corner_radius=8,
                fg_color=fg,
            )
            return btn
        style = "Primary.TButton" if primary else "TButton"
        return ttk.Button(parent, text=text, command=command, style=style)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        top = ttk.Frame(self, style="TopBar.TFrame", padding=(8, 6, 8, 6))
        top.pack(side=tk.TOP, fill=tk.X)

        open_btn = self._make_toolbar_button(top, text="Open", command=self._on_open)
        open_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(open_btn, "Shortcut: Ctrl+O")

        export_btn = self._make_toolbar_button(top, text="Export GPKG", command=self._on_export_gpkg)
        export_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(export_btn, "Shortcut: Ctrl+E")

        clear_btn = self._make_toolbar_button(top, text="Clear", command=self._on_clear)
        clear_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(clear_btn, "Shortcut: Ctrl+Shift+K")

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        zoom_btn = self._make_toolbar_button(top, text="Zoom to selected", command=self._on_zoom_selected_layers)
        zoom_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(zoom_btn, "Shortcut: Ctrl+Shift+Z")

        reset_btn = self._make_toolbar_button(top, text="Reset view", command=self._on_reset_view)
        reset_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(reset_btn, "Shortcut: Ctrl+Shift+R")

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        compute_btn = self._make_toolbar_button(top, text="Compute volume", command=self._on_compute_volume, primary=True)
        compute_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(compute_btn, "Shortcut: Ctrl+Shift+V")

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.section_btn = self._make_toolbar_button(top, text="Draw section", command=self._on_toggle_section)
        self.section_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(self.section_btn, "Shortcut: Ctrl+Shift+S")

        self.mask_btn = self._make_toolbar_button(top, text="Draw mask", command=self._on_toggle_mask)
        self.mask_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(self.mask_btn, "Shortcut: Ctrl+Shift+M")

        ttk.Label(
            top,
            text="Shortcuts: Ctrl+O open, Ctrl+E export, Ctrl+Shift+V compute",
            foreground="#4a5a70",
        ).pack(side=tk.RIGHT, padx=(8, 0))

    def _add_collapsible_panel(
        self,
        parent: tk.Widget,
        title: str,
        *,
        expanded: bool = True,
    ) -> ttk.Frame:
        panel = ttk.Frame(parent, padding=(0, 0, 0, 2))
        panel.pack(fill=tk.X, pady=(0, 8))

        header = ttk.Frame(panel, padding=(2, 2, 2, 2))
        header.pack(fill=tk.X)
        indicator = tk.StringVar(value="▼" if expanded else "▶")
        content = ttk.Frame(panel, style="PanelBody.TFrame", padding=(8, 8, 8, 8))

        def toggle() -> None:
            if content.winfo_ismapped():
                content.pack_forget()
                indicator.set("▶")
            else:
                content.pack(fill=tk.X, pady=(3, 0))
                indicator.set("▼")

        ttk.Button(header, textvariable=indicator, width=2, command=toggle).pack(side=tk.LEFT)
        ttk.Label(header, text=title, style="PanelHeader.TLabel").pack(side=tk.LEFT, padx=(4, 0))

        if expanded:
            content.pack(fill=tk.X, pady=(3, 0))
        return content

    def _build_control_panels(self, parent: tk.Widget) -> None:
        ttk.Label(parent, text="Analysis Controls", style="PanelHeader.TLabel").pack(anchor="w", padx=2, pady=(0, 8))

        display_panel = self._add_collapsible_panel(parent, "Display", expanded=True)
        ttk.Checkbutton(display_panel, text="Labels", variable=self.show_labels, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="Stations", variable=self.show_stations, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="Markers", variable=self.show_markers, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="Lines", variable=self.show_lines, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="Triangulation", variable=self.show_triangulation, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="TIN fill", variable=self.show_tin_fill, command=self._redraw).pack(anchor="w")
        ttk.Checkbutton(display_panel, text="Equal aspect (2D)", variable=self.equal_aspect, command=self._redraw).pack(anchor="w")

        station_row = ttk.Frame(display_panel)
        station_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(station_row, text="Station step (m):").pack(side=tk.LEFT)
        self.station_interval_spin = ttk.Spinbox(
            station_row,
            from_=1.0,
            to=1000.0,
            increment=1.0,
            width=6,
            textvariable=self.station_interval_m,
            command=self._on_station_settings_changed,
        )
        self.station_interval_spin.pack(side=tk.LEFT, padx=(6, 0))
        self.station_interval_spin.bind("<Return>", lambda _e: self._on_station_settings_changed())
        self.station_interval_spin.bind("<FocusOut>", lambda _e: self._on_station_settings_changed())

        station_label_row = ttk.Frame(display_panel)
        station_label_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(station_label_row, text="Station labels:").pack(side=tk.LEFT)
        self.station_label_combo = ttk.Combobox(
            station_label_row,
            textvariable=self.station_label_format,
            values=["plain", "chainage"],
            width=11,
            state="readonly",
        )
        self.station_label_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.station_label_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_station_settings_changed())

        tin_row = ttk.Frame(display_panel)
        tin_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(tin_row, text="TIN alpha:").pack(side=tk.LEFT)
        self.tin_fill_alpha_spin = ttk.Spinbox(
            tin_row,
            from_=0.0,
            to=1.0,
            increment=0.01,
            width=6,
            textvariable=self.tin_fill_alpha,
            command=self._on_tin_fill_alpha_change,
        )
        self.tin_fill_alpha_spin.pack(side=tk.LEFT, padx=(6, 6))
        self.tin_fill_alpha_spin.bind("<Return>", lambda _e: self._on_tin_fill_alpha_change())
        self.tin_fill_alpha_spin.bind("<FocusOut>", lambda _e: self._on_tin_fill_alpha_change())
        self.tin_fill_alpha_scale = ttk.Scale(
            tin_row,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            variable=self.tin_fill_alpha,
            command=self._on_tin_fill_alpha_scale,
            length=120,
        )
        self.tin_fill_alpha_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        raster_panel = self._add_collapsible_panel(parent, "Raster", expanded=False)
        raster_btns = ttk.Frame(raster_panel)
        raster_btns.pack(fill=tk.X)
        ttk.Button(raster_btns, text="Open...", command=self._on_open_raster).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(raster_btns, text="Remove", command=self._on_clear_raster).pack(side=tk.LEFT, padx=4)
        ttk.Button(raster_btns, text="Up", command=self._on_move_raster_up).pack(side=tk.LEFT, padx=4)
        ttk.Button(raster_btns, text="Down", command=self._on_move_raster_down).pack(side=tk.LEFT, padx=4)

        ttk.Button(raster_panel, text="Set elevation raster", command=self._on_set_elevation_raster).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(raster_panel, text="Show raster", variable=self.show_raster, command=self._redraw).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(raster_panel, text="Clip raster to KOF", variable=self.raster_clip_to_kof).pack(anchor="w")

        active_row = ttk.Frame(raster_panel)
        active_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(active_row, text="Active:").pack(side=tk.LEFT)
        self.active_raster_combo = ttk.Combobox(
            active_row,
            textvariable=self.active_raster_name,
            values=[],
            width=24,
            state="readonly",
        )
        self.active_raster_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        self.active_raster_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_select_active_raster())

        clip_row = ttk.Frame(raster_panel)
        clip_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(clip_row, text="Clip buffer (m):").pack(side=tk.LEFT)
        self.raster_clip_buffer_spin = ttk.Spinbox(
            clip_row,
            from_=0.0,
            to=10000.0,
            increment=1.0,
            width=8,
            textvariable=self.raster_clip_buffer_m,
        )
        self.raster_clip_buffer_spin.pack(side=tk.LEFT, padx=(6, 0))

        alpha_row = ttk.Frame(raster_panel)
        alpha_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(alpha_row, text="Raster alpha:").pack(side=tk.LEFT)
        self.raster_alpha_spin = ttk.Spinbox(
            alpha_row,
            from_=0.05,
            to=1.0,
            increment=0.05,
            width=8,
            textvariable=self.raster_alpha,
            command=self._on_raster_alpha_change,
        )
        self.raster_alpha_spin.pack(side=tk.LEFT, padx=(6, 0))
        self.raster_alpha_spin.bind("<Return>", lambda _e: self._on_raster_alpha_change())
        self.raster_alpha_spin.bind("<FocusOut>", lambda _e: self._on_raster_alpha_change())

        volume_panel = self._add_collapsible_panel(parent, "Volume setup", expanded=True)
        top_row = ttk.Frame(volume_panel)
        top_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(top_row, text="Top:").pack(side=tk.LEFT)
        self.top_combo = ttk.Combobox(top_row, textvariable=self.top_var, width=28, state="readonly")
        self.top_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        bottom_row = ttk.Frame(volume_panel)
        bottom_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(bottom_row, text="Bottom:").pack(side=tk.LEFT)
        self.bottom_combo = ttk.Combobox(bottom_row, textvariable=self.bottom_var, width=28, state="readonly")
        self.bottom_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        source_row = ttk.Frame(volume_panel)
        source_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(source_row, text="Volume from:").pack(side=tk.LEFT)
        self.volume_source_combo = ttk.Combobox(
            source_row,
            textvariable=self.volume_source_mode,
            values=list(VOLUME_MODE_LABELS.values()),
            width=20,
            state="readonly",
        )
        self.volume_source_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        _HoverTooltip(
            self.volume_source_combo,
            "KOF - KOF: existing KOF top/bottom workflow.\n"
            "Top KOF - Raster: top KOF minus loaded elevation raster.\n"
            "Raster - Bottom KOF: loaded elevation raster minus bottom KOF.\n"
            "Raster - Top KOF: loaded elevation raster minus top KOF.",
        )

        int_type_row = ttk.Frame(volume_panel)
        int_type_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(int_type_row, text="Intermediate:").pack(side=tk.LEFT)
        self.intermediate_type_combo = ttk.Combobox(
            int_type_row,
            textvariable=self.intermediate_type,
            values=list(INTERMEDIATE_TYPE_LABELS.values()),
            width=20,
            state="readonly",
        )
        self.intermediate_type_combo.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        self.intermediate_type_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_intermediate_type_change())
        _HoverTooltip(
            self.intermediate_type_combo,
            "Optional split-volume reference.\n"
            "None: single result (top-bottom).\n"
            "KOF layer: split into Top-KOF and KOF-Bottom.\n"
            "Raster layer: split into Top-Raster and Raster-Bottom.\n"
            "Constant Z: split against a horizontal plane.",
        )

        self.intermediate_kof_combo = ttk.Combobox(
            volume_panel,
            textvariable=self.intermediate_kof_var,
            values=[],
            width=26,
            state="readonly",
        )
        self.intermediate_kof_combo.pack(fill=tk.X, pady=(0, 2))

        self.intermediate_raster_combo = ttk.Combobox(
            volume_panel,
            textvariable=self.intermediate_raster_var,
            values=[],
            width=26,
            state="readonly",
        )
        self.intermediate_raster_combo.pack(fill=tk.X, pady=(0, 2))

        const_row = ttk.Frame(volume_panel)
        const_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(const_row, text="Constant Z:").pack(side=tk.LEFT)
        self.intermediate_constant_z_spin = ttk.Spinbox(
            const_row,
            from_=-100000.0,
            to=100000.0,
            increment=0.1,
            width=12,
            textvariable=self.intermediate_constant_z,
        )
        self.intermediate_constant_z_spin.pack(side=tk.LEFT, padx=(6, 4))
        self.intermediate_constant_down_btn = ttk.Button(
            const_row,
            text="-",
            width=3,
            command=lambda: self._nudge_intermediate_constant_z(-1),
        )
        self.intermediate_constant_down_btn.pack(side=tk.LEFT, padx=(0, 2))
        self.intermediate_constant_up_btn = ttk.Button(
            const_row,
            text="+",
            width=3,
            command=lambda: self._nudge_intermediate_constant_z(+1),
        )
        self.intermediate_constant_up_btn.pack(side=tk.LEFT)

        const_step_row = ttk.Frame(volume_panel)
        const_step_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(const_step_row, text="Step:").pack(side=tk.LEFT)
        self.intermediate_constant_step_spin = ttk.Spinbox(
            const_step_row,
            from_=0.01,
            to=1000.0,
            increment=0.01,
            width=8,
            textvariable=self.intermediate_constant_step,
        )
        self.intermediate_constant_step_spin.pack(side=tk.LEFT, padx=(6, 0))

        self.intermediate_constant_z_spin.bind("<Return>", lambda _e: self._on_intermediate_type_change())
        self.intermediate_constant_z_spin.bind("<FocusOut>", lambda _e: self._on_intermediate_type_change())
        self.intermediate_constant_z_spin.bind("<Up>", lambda _e: self._nudge_intermediate_constant_z(+1))
        self.intermediate_constant_z_spin.bind("<Down>", lambda _e: self._nudge_intermediate_constant_z(-1))

        method_row = ttk.Frame(volume_panel)
        method_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(method_row, text="Method:").pack(side=tk.LEFT)
        self.volume_method_combo = ttk.Combobox(
            method_row,
            textvariable=self.volume_method,
            values=["raster", "triangles"],
            width=12,
            state="readonly",
        )
        self.volume_method_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.volume_method_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_intermediate_type_change())
        _HoverTooltip(
            self.volume_method_combo,
            "raster: fastest for quick estimates.\n"
            "triangles: exact overlap integration; slower but more accurate.",
        )

        boundary_row = ttk.Frame(volume_panel)
        boundary_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(boundary_row, text="Boundary tightness:").pack(side=tk.LEFT)
        self.boundary_spin = ttk.Spinbox(
            boundary_row,
            from_=1.0,
            to=10.0,
            increment=0.5,
            width=6,
            textvariable=self.boundary_factor,
            command=self._on_boundary_change,
        )
        self.boundary_spin.pack(side=tk.LEFT, padx=(6, 0))
        self.boundary_spin.bind("<Return>", lambda _e: self._on_boundary_change())
        self.boundary_spin.bind("<FocusOut>", lambda _e: self._on_boundary_change())

        ttk.Button(volume_panel, text="Compute volume", command=self._on_compute_volume).pack(anchor="w", pady=(2, 0))
        ttk.Label(volume_panel, textvariable=self.volume_text, foreground="#204080", wraplength=300, justify=tk.LEFT).pack(fill=tk.X, pady=(4, 0))

        sections_panel = self._add_collapsible_panel(parent, "Sections and mask", expanded=False)
        ttk.Button(sections_panel, text="Clear sections", command=self._on_clear_sections).pack(fill=tk.X, pady=(0, 2))
        ttk.Button(sections_panel, text="Save sections...", command=self._on_save_sections_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Load sections...", command=self._on_load_sections_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Export section report...", command=self._on_export_section_report).pack(fill=tk.X, pady=2)
        ttk.Separator(sections_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Button(sections_panel, text="Save mask...", command=self._on_save_mask_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Load mask...", command=self._on_load_mask_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Clear mask", command=self._on_clear_mask).pack(fill=tk.X, pady=2)

        schema_panel = self._add_collapsible_panel(parent, "LandXML schema", expanded=False)
        ttk.Checkbutton(schema_panel, text="Strict XSD", variable=self.strict_xsd_validation).pack(anchor="w")
        ttk.Checkbutton(schema_panel, text="Allow net schema", variable=self.xsd_allow_network).pack(anchor="w")
        ttk.Button(schema_panel, text="XSD file...", command=self._on_pick_local_xsd).pack(anchor="w", pady=(4, 0))

        self._on_intermediate_type_change()

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)

        self.layers_frame = ttk.LabelFrame(body, text="Layers", padding=6)
        self.layers_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 2), pady=(0, 4))
        self.layers_inner = ttk.Frame(self.layers_frame)
        self.layers_inner.pack(fill=tk.BOTH, expand=True)
        self._refresh_layer_controls()

        center = ttk.Frame(body)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(center)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 2D tab
        frame2d = ttk.Frame(self.notebook)
        self.fig2d = Figure(figsize=(8, 6), dpi=100)
        self.ax2d = self.fig2d.add_subplot(111)
        self.canvas2d = FigureCanvasTkAgg(self.fig2d, master=frame2d)
        self.canvas2d.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar2d = NavigationToolbar2Tk(self.canvas2d, frame2d)
        self.toolbar2d.update()
        self.notebook.add(frame2d, text="2D (plan)")

        # 3D tab
        frame3d = ttk.Frame(self.notebook)
        self.fig3d = Figure(figsize=(8, 6), dpi=100)
        self.ax3d = self.fig3d.add_subplot(111, projection="3d")
        self.canvas3d = FigureCanvasTkAgg(self.fig3d, master=frame3d)
        self.canvas3d.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar3d = NavigationToolbar2Tk(self.canvas3d, frame3d)
        toolbar3d.update()
        self.notebook.add(frame3d, text="3D")

        self.controls_frame = ttk.Frame(body, padding=(2, 2, 4, 4), width=380)
        self.controls_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.controls_frame.pack_propagate(False)

        controls_header = ttk.Frame(self.controls_frame)
        controls_header.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(controls_header, text="Panels", style="PanelHeader.TLabel").pack(side=tk.LEFT)

        self.controls_canvas = tk.Canvas(self.controls_frame, highlightthickness=0, borderwidth=0)
        self.controls_scrollbar = ttk.Scrollbar(
            self.controls_frame,
            orient=tk.VERTICAL,
            command=self.controls_canvas.yview,
        )
        self.controls_canvas.configure(yscrollcommand=self.controls_scrollbar.set)

        self.controls_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.controls_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.controls_inner = ttk.Frame(self.controls_canvas)
        self._controls_inner_window = self.controls_canvas.create_window(
            (0, 0),
            window=self.controls_inner,
            anchor="nw",
        )

        self.controls_inner.bind(
            "<Configure>",
            lambda _e: self.controls_canvas.configure(scrollregion=self.controls_canvas.bbox("all")),
        )
        self.controls_canvas.bind(
            "<Configure>",
            lambda e: self.controls_canvas.itemconfigure(self._controls_inner_window, width=e.width),
        )
        self._wire_mousewheel_scrolling(self.controls_canvas)
        self._wire_mousewheel_scrolling(self.controls_inner)

        self._build_control_panels(self.controls_inner)

    def _wire_mousewheel_scrolling(self, target: tk.Widget) -> None:
        def _on_wheel(event) -> str:  # noqa: ANN001
            if getattr(event, "num", None) == 4:
                self.controls_canvas.yview_scroll(-1, "units")
                return "break"
            if getattr(event, "num", None) == 5:
                self.controls_canvas.yview_scroll(1, "units")
                return "break"
            delta = int(getattr(event, "delta", 0))
            if delta == 0:
                return "break"
            self.controls_canvas.yview_scroll(-int(delta / 120), "units")
            return "break"

        def _bind_wheel(_event=None) -> None:  # noqa: ANN001
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_event=None) -> None:  # noqa: ANN001
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        target.bind("<Enter>", _bind_wheel)
        target.bind("<Leave>", _unbind_wheel)

    def _build_statusbar(self) -> None:
        self.status = tk.StringVar(value="No file loaded.")
        bar = ttk.Frame(self, padding=(6, 2))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bar, textvariable=self.status, anchor=tk.W).pack(fill=tk.X)

    # -------------------------------------------------------------- Actions
    def _on_pick_local_xsd(self) -> None:
        path = filedialog.askopenfilename(
            title="Select local LandXML XSD",
            filetypes=[("XSD files", "*.xsd"), ("All files", "*.*")],
        )
        if not path:
            return
        self.xsd_local_schema_path.set(path)

    @staticmethod
    def _format_station_label(station_m: float, label_format: str) -> str:
        if label_format == "chainage":
            rounded = int(round(station_m))
            km = rounded // 1000
            m = rounded % 1000
            return f"{km}+{m:03d}"
        return f"{station_m:.0f}"

    @staticmethod
    def _interpolate_alignment_point(
        centerline: list[tuple[float, float]],
        offset: float,
    ) -> Optional[tuple[float, float]]:
        if len(centerline) < 2 or offset < 0:
            return None
        for i in range(1, len(centerline)):
            y0, x0 = centerline[i - 1]
            y1, x1 = centerline[i]
            seg_len = float(np.hypot(y1 - y0, x1 - x0))
            if seg_len <= 0:
                continue
            if offset <= seg_len:
                t = offset / seg_len
                return (y0 + (y1 - y0) * t, x0 + (x1 - x0) * t)
            offset -= seg_len
        return centerline[-1]

    def _rebuild_alignment_stations(self) -> None:
        try:
            step = float(self.station_interval_m.get())
        except (tk.TclError, ValueError):
            step = 10.0
        if not np.isfinite(step) or step <= 0:
            step = 10.0
        self.station_interval_m.set(step)

        fmt = self.station_label_format.get().strip().lower() or "plain"
        if fmt not in {"plain", "chainage"}:
            fmt = "plain"
            self.station_label_format.set(fmt)

        for lx in self.landxml_files:
            for alignment in lx.alignments:
                alignment.stations.clear()
                if len(alignment.centerline) < 2 or alignment.length <= 0:
                    continue
                n_steps = int(np.floor(alignment.length / step))
                for i in range(n_steps + 1):
                    sta = alignment.sta_start + i * step
                    if sta > alignment.sta_start + alignment.length + 1e-6:
                        break
                    pt = self._interpolate_alignment_point(alignment.centerline, sta - alignment.sta_start)
                    if pt is None:
                        continue
                    alignment.stations.append(
                        LandXMLStation(
                            label=self._format_station_label(sta, fmt),
                            station=sta,
                            y=pt[0],
                            x=pt[1],
                            z=None,
                        )
                    )
                end_sta = alignment.sta_start + alignment.length
                if not alignment.stations or abs(alignment.stations[-1].station - end_sta) > 1e-6:
                    end_pt = self._interpolate_alignment_point(alignment.centerline, alignment.length)
                    if end_pt is not None:
                        alignment.stations.append(
                            LandXMLStation(
                                label=self._format_station_label(end_sta, fmt),
                                station=end_sta,
                                y=end_pt[0],
                                x=end_pt[1],
                                z=None,
                            )
                        )

    def _on_station_settings_changed(self) -> None:
        self._rebuild_alignment_stations()
        self._redraw_2d()
        self._update_status()

    def _on_open(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Open surface/alignment file(s)",
            filetypes=[("Supported files", "*.kof *.xml"), ("KOF files", "*.kof"), ("LandXML", "*.xml"), ("All files", "*.*")],
        )
        for path in paths:
            self._load_file(path, redraw=False)
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _on_export_gpkg(self) -> None:
        kof_files = [k for k in self.files if isinstance(k, KOFFile)]
        if not kof_files:
            messagebox.showinfo("KOF Viewer", "Load one or more KOF files first.")
            return

        default_name = "kof_export.gpkg"
        if kof_files:
            first_name, _ext = os.path.splitext(kof_files[0].name)
            default_name = f"{first_name}_export.gpkg"

        out_path = filedialog.asksaveasfilename(
            title="Export to GeoPackage",
            defaultextension=".gpkg",
            initialfile=default_name,
            filetypes=[("GeoPackage", "*.gpkg"), ("All files", "*.*")],
        )
        if not out_path:
            return

        try:
            summary = export_kof_files_to_gpkg(kof_files, out_path)
        except ImportError:
            messagebox.showerror(
                "KOF Viewer",
                "GeoPackage export requires geopandas and shapely.\n\n"
                "Install with:\n"
                "  pip install geopandas shapely",
            )
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to export GeoPackage:\n{exc}")
            return

        messagebox.showinfo(
            "KOF Viewer",
            "GeoPackage export completed.\n\n"
            f"File: {summary['path']}\n"
            f"Layers:\n"
            f"- {summary['points_layer']} ({summary['point_count']} points)\n"
            f"- {summary['polylines_layer']} "
            f"({summary['polyline_count']} polylines)",
        )

    def _on_clear(self) -> None:
        self.files.clear()
        self.landxml_files.clear()
        self.layer_vars.clear()
        self._surface_owner_paths.clear()
        self._landxml_group_vars.clear()
        self._view_extent_2d = None
        self.last_volume = None
        self.volume_text.set("Volume: (not computed)")
        self._sections.clear()
        self._mask_polygon.clear()
        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _on_save_mask_json(self) -> None:
        if len(self._mask_polygon) < 3:
            messagebox.showinfo(
                "KOF Viewer",
                "No closed mask polygon to save. Draw and close a mask first.",
            )
            return

        default_name = "mask_polygon.json"
        out_path = filedialog.asksaveasfilename(
            title="Save mask polygon",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out_path:
            return

        try:
            save_mask_json(
                out_path,
                self._mask_polygon,
                metadata={
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "vertex_count": len(self._mask_polygon),
                },
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to save mask JSON:\n{exc}")
            return

        messagebox.showinfo(
            "KOF Viewer",
            f"Saved mask with {len(self._mask_polygon)} vertices to:\n{out_path}",
        )

    def _on_load_mask_json(self) -> None:
        in_path = filedialog.askopenfilename(
            title="Load mask polygon",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not in_path:
            return

        try:
            vertices = load_mask_json(in_path)
        except (MaskDataError, OSError, ValueError) as exc:
            messagebox.showerror("KOF Viewer", f"Failed to load mask JSON:\n{exc}")
            return

        self._mask_click_points.clear()
        self._mask_polygon = list(vertices)
        if self._mask_mode:
            self._end_mask_mode()
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self.notebook.select(0)
        if len(self._mask_polygon) >= 3:
            self._zoom_to_bounds(self._polygon_bounds(self._mask_polygon))
        self._redraw()

    def _on_save_sections_json(self) -> None:
        if not self._sections:
            messagebox.showinfo("KOF Viewer", "No section lines to save.")
            return

        out_path = filedialog.asksaveasfilename(
            title="Save section lines",
            defaultextension=".json",
            initialfile="sections.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out_path:
            return

        try:
            save_sections_json(out_path, self._sections)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to save sections JSON:\n{exc}")
            return

        messagebox.showinfo(
            "KOF Viewer",
            f"Saved {len(self._sections)} section line(s) to:\n{out_path}",
        )

    def _on_load_sections_json(self) -> None:
        in_path = filedialog.askopenfilename(
            title="Load section lines",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not in_path:
            return

        try:
            sections = load_sections_json(in_path)
        except (SectionDataError, OSError, ValueError) as exc:
            messagebox.showerror("KOF Viewer", f"Failed to load sections JSON:\n{exc}")
            return

        self._sections = list(sections)
        self._redraw()

    def _on_export_section_report(self) -> None:
        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        if top is None or bot is None:
            messagebox.showwarning("KOF Viewer", "Select both top and bottom surfaces first.")
            return
        if top is bot:
            messagebox.showwarning("KOF Viewer", "Top and bottom must be different files.")
            return
        if not self._sections:
            messagebox.showinfo(
                "KOF Viewer",
                "Draw at least one section line first. The latest line is used as baseline.",
            )
            return

        spacing_m = simpledialog.askfloat(
            "Section report",
            "Profile spacing along baseline (m):",
            initialvalue=1.0,
            minvalue=0.01,
        )
        if spacing_m is None:
            return

        section_length_m = simpledialog.askfloat(
            "Section report",
            "Cross-section length (m):",
            initialvalue=20.0,
            minvalue=0.1,
        )
        if section_length_m is None:
            return

        n_samples = simpledialog.askinteger(
            "Section report",
            "Samples per profile:",
            initialvalue=200,
            minvalue=25,
            maxvalue=2000,
        )
        if n_samples is None:
            return

        out_root = filedialog.askdirectory(title="Choose report output folder")
        if not out_root:
            return

        baseline = self._sections[-1]
        (b0, b1) = baseline
        baseline_len = float(np.hypot(b1[0] - b0[0], b1[1] - b0[1]))
        if baseline_len <= 0:
            messagebox.showerror("KOF Viewer", "Baseline section has zero length.")
            return

        stations = station_values(baseline_len, float(spacing_m))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(out_root, f"section_report_{timestamp}")
        profiles_dir = os.path.join(run_dir, "profiles")
        os.makedirs(profiles_dir, exist_ok=True)

        profile_rows: list[dict[str, object]] = []
        plan_points: list[tuple[str, tuple[float, float]]] = []
        section_raster = self._section_reference_raster()
        section_intermediate = self._section_intermediate_surface()
        area_top_surface, area_bottom_surface, area_top_name, area_bottom_name = self._section_area_pair(top, bot)

        for idx, station in enumerate(stations, start=1):
            profile_id = f"P{idx:04d}"
            p0, p1, center = profile_segment_from_station(
                b0,
                b1,
                float(station),
                float(section_length_m),
            )
            sample = sample_profile(
                top,
                bot,
                p0,
                p1,
                profile_id=profile_id,
                station_m=float(station),
                center=center,
                n_samples=int(n_samples),
                reference_surface=section_raster,
                reference_name=section_raster.name if section_raster is not None else None,
                secondary_reference_surface=section_intermediate,
                secondary_reference_name=section_intermediate.name if section_intermediate is not None else None,
                area_top_surface=area_top_surface,
                area_bottom_surface=area_bottom_surface,
                area_top_name=area_top_name,
                area_bottom_name=area_bottom_name,
            )

            image_name = f"{profile_id}.png"
            image_rel = os.path.join("profiles", image_name)
            image_path = os.path.join(profiles_dir, image_name)
            save_profile_plot_png(
                image_path,
                sample,
                top_name=top.name,
                bottom_name=bot.name,
            )

            profile_rows.append(
                {
                    "profile_id": profile_id,
                    "station_m": sample.station_m,
                    "length_m": sample.length_m,
                    "valid_fraction": sample.valid_fraction,
                    "area_net_m2": sample.area_net_m2,
                    "area_cut_m2": sample.area_cut_m2,
                    "area_fill_m2": sample.area_fill_m2,
                    "image_rel": image_rel.replace("\\", "/"),
                }
            )
            plan_points.append((profile_id, center))

        plan_image_rel = "plan_view.png"
        plan_image_path = os.path.join(run_dir, plan_image_rel)
        plan_raster = self._top_visible_raster_layer()
        save_plan_view_png(
            plan_image_path,
            files=[k for i, k in enumerate(self.files) if self._is_layer_visible(i)],
            show_markers=bool(self.show_markers.get()),
            show_lines=bool(self.show_lines.get()),
            show_labels=bool(self.show_labels.get()),
            show_triangulation=bool(self.show_triangulation.get()),
            sections=self._sections,
            baseline=baseline,
            profile_points=plan_points,
            raster_img=plan_raster.image if plan_raster is not None else None,
            raster_extent=plan_raster.extent if plan_raster is not None else None,
            raster_origin=plan_raster.origin if plan_raster is not None else "upper",
            raster_alpha=float(plan_raster.alpha.get()) if plan_raster is not None else 1.0,
        )

        report_path = os.path.join(run_dir, "report.html")
        write_html_report(
            report_path,
            title="KOF Section Report",
            metadata={
                "Top surface": top.name,
                "Bottom surface": bot.name,
                "Section area pair": f"{area_top_name} - {area_bottom_name}",
                "Input files": ", ".join(k.name for k in self.files),
                "Spacing (m)": f"{float(spacing_m):.2f}",
                "Cross-section length (m)": f"{float(section_length_m):.2f}",
                "Samples per profile": int(n_samples),
                "Profile count": len(profile_rows),
                "Baseline length (m)": f"{baseline_len:.2f}",
            },
            plan_image_rel=plan_image_rel,
            profile_rows=profile_rows,
        )

        manifest_path = os.path.join(run_dir, "report_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "type": "kofvision.section_report",
                    "version": 1,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "top_surface": top.name,
                    "bottom_surface": bot.name,
                    "section_area_pair": {
                        "top": area_top_name,
                        "bottom": area_bottom_name,
                    },
                    "input_files": [k.name for k in self.files],
                    "spacing_m": float(spacing_m),
                    "section_length_m": float(section_length_m),
                    "samples_per_profile": int(n_samples),
                    "baseline": {
                        "start": [float(b0[0]), float(b0[1])],
                        "end": [float(b1[0]), float(b1[1])],
                        "length_m": baseline_len,
                    },
                    "profiles": profile_rows,
                },
                fh,
                indent=2,
            )

        messagebox.showinfo(
            "KOF Viewer",
            "Section report export completed.\n\n"
            f"Output folder:\n{run_dir}\n\n"
            f"Profiles exported: {len(profile_rows)}\n"
            "Baseline: latest drawn section line",
        )

    def _on_open_raster(self) -> None:
        path = filedialog.askopenfilename(
            title="Open raster background",
            filetypes=[
                ("Raster images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._load_raster(path)

    def _raster_display_name(self, idx: int) -> str:
        layer = self._raster_layers[idx]
        name = os.path.basename(layer.path)
        marks: list[str] = []
        if idx == self._elevation_raster_index:
            marks.append("elev")
        if idx == len(self._raster_layers) - 1:
            marks.append("top")
        if marks:
            return f"{name} ({', '.join(marks)})"
        return name

    def _refresh_raster_selector(self) -> None:
        names = [self._raster_display_name(i) for i in range(len(self._raster_layers))]
        self.active_raster_combo["values"] = names
        if not names:
            self._active_raster_index = None
            self.active_raster_name.set("")
            self._refresh_intermediate_raster_selector()
            return
        if self._active_raster_index is None or self._active_raster_index >= len(names):
            self._active_raster_index = len(names) - 1
        self.active_raster_name.set(names[self._active_raster_index])
        self._sync_alpha_from_active()
        self._refresh_intermediate_raster_selector()

    def _set_active_raster_index(self, idx: Optional[int]) -> None:
        if idx is None or idx < 0 or idx >= len(self._raster_layers):
            self._active_raster_index = None
            self.active_raster_name.set("")
            return
        self._active_raster_index = idx
        self._refresh_raster_selector()

    def _on_select_active_raster(self) -> None:
        idx = self.active_raster_combo.current()
        if idx < 0 or idx >= len(self._raster_layers):
            return
        self._active_raster_index = idx
        self._sync_alpha_from_active()

    def _sync_alpha_from_active(self) -> None:
        layer = self._get_active_raster_layer()
        if layer is None:
            return
        self.raster_alpha.set(float(layer.alpha.get()))

    def _get_active_raster_layer(self) -> Optional[_RasterViewLayer]:
        idx = self._active_raster_index
        if idx is None or idx < 0 or idx >= len(self._raster_layers):
            return None
        return self._raster_layers[idx]

    def _top_visible_raster_layer(self) -> Optional[_RasterViewLayer]:
        if not self.show_raster.get():
            return None
        for layer in reversed(self._raster_layers):
            if layer.visible.get():
                return layer
        return None

    def _on_move_raster_up(self) -> None:
        idx = self._active_raster_index
        if idx is None or idx >= len(self._raster_layers) - 1:
            return
        self._raster_layers[idx], self._raster_layers[idx + 1] = self._raster_layers[idx + 1], self._raster_layers[idx]
        if self._elevation_raster_index == idx:
            self._elevation_raster_index = idx + 1
        elif self._elevation_raster_index == idx + 1:
            self._elevation_raster_index = idx
        self._active_raster_index = idx + 1
        self._refresh_raster_selector()
        self._refresh_layer_controls()
        self._redraw()

    def _on_move_raster_down(self) -> None:
        idx = self._active_raster_index
        if idx is None or idx <= 0:
            return
        self._raster_layers[idx], self._raster_layers[idx - 1] = self._raster_layers[idx - 1], self._raster_layers[idx]
        if self._elevation_raster_index == idx:
            self._elevation_raster_index = idx - 1
        elif self._elevation_raster_index == idx - 1:
            self._elevation_raster_index = idx
        self._active_raster_index = idx - 1
        self._refresh_raster_selector()
        self._refresh_layer_controls()
        self._redraw()

    def _on_set_elevation_raster(self) -> None:
        idx = self._active_raster_index
        if idx is None:
            messagebox.showinfo("KOF Viewer", "No active raster selected.")
            return
        layer = self._raster_layers[idx]
        if layer.elevation_surface is None:
            messagebox.showwarning(
                "KOF Viewer",
                "Selected raster is not a supported single-band elevation raster.",
            )
            return
        self._elevation_raster_index = idx
        self._refresh_raster_selector()
        self._refresh_layer_controls()
        self._redraw_2d()

    def _on_clear_raster(self) -> None:
        idx = self._active_raster_index
        if idx is None or idx < 0 or idx >= len(self._raster_layers):
            return
        self._raster_layers.pop(idx)
        if self._elevation_raster_index is not None:
            if self._elevation_raster_index == idx:
                self._elevation_raster_index = None
            elif self._elevation_raster_index > idx:
                self._elevation_raster_index -= 1
        if not self._raster_layers:
            self.show_raster.set(False)
            self._active_raster_index = None
        else:
            self._active_raster_index = min(idx, len(self._raster_layers) - 1)
        self._refresh_raster_selector()
        self._refresh_layer_controls()
        self._redraw()

    def _on_raster_alpha_change(self) -> None:
        try:
            alpha = float(self.raster_alpha.get())
        except (tk.TclError, ValueError):
            return
        alpha = min(max(alpha, 0.05), 1.0)
        self.raster_alpha.set(alpha)
        layer = self._get_active_raster_layer()
        if layer is not None:
            layer.alpha.set(alpha)
        if self._raster_layers and self.show_raster.get():
            self._redraw_2d()

    def _on_tin_fill_alpha_change(self) -> None:
        try:
            alpha = float(self.tin_fill_alpha.get())
        except (tk.TclError, ValueError):
            alpha = 0.14
        alpha = min(max(alpha, 0.0), 1.0)
        self.tin_fill_alpha.set(alpha)
        self._redraw_2d()

    def _on_tin_fill_alpha_scale(self, _value: str) -> None:
        # Scale updates the same variable continuously; just redraw.
        self._on_tin_fill_alpha_change()

    def _load_raster(self, path: str) -> None:
        clip_bounds = self._all_files_bounds()
        clip_buffer_m = self._safe_clip_buffer_m()
        try:
            layer = load_raster_layer(
                path,
                clip_bounds=clip_bounds,
                clip_buffer_m=clip_buffer_m,
                prefer_clip=bool(self.raster_clip_to_kof.get()),
            )
        except FileNotFoundError:
            messagebox.showerror(
                "KOF Viewer",
                "No world file was found for the selected raster.\n\n"
                "Expected one of: .jgw/.pgw/.tfw/.wld beside the image.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "KOF Viewer",
                f"Failed to load raster or world file:\n{exc}",
            )
            return

        elevation_surface: Optional[ElevationRasterSurface] = None
        try:
            elevation_surface = load_elevation_surface(
                path,
                clip_bounds=clip_bounds,
                clip_buffer_m=clip_buffer_m,
                prefer_clip=bool(self.raster_clip_to_kof.get()),
            )
        except Exception:
            elevation_surface = None

        self._raster_layers.append(
            _RasterViewLayer(
                path=path,
                image=layer.image,
                extent=layer.extent,
                origin=layer.origin,
                visible=tk.BooleanVar(value=True),
                alpha=tk.DoubleVar(value=float(self.raster_alpha.get())),
                elevation_surface=elevation_surface,
            )
        )
        self._active_raster_index = len(self._raster_layers) - 1
        if self._elevation_raster_index is None and elevation_surface is not None:
            self._elevation_raster_index = self._active_raster_index
        self.show_raster.set(True)
        self._refresh_raster_selector()
        self._refresh_layer_controls()
        self._redraw()

        if elevation_surface is None:
            messagebox.showinfo(
                "KOF Viewer",
                "Raster loaded for display, but it is not a supported single-band elevation raster.\n"
                "Volume modes using raster elevation require a single-band DEM.",
            )

    def _on_reset_view(self) -> None:
        self._view_extent_2d = None
        self._redraw_2d()
        self._update_status()

    def _on_zoom_selected_layers(self) -> None:
        bounds = self._selected_layers_2d_bounds()
        if bounds is None:
            messagebox.showinfo(
                "KOF Viewer",
                "No visible layers with valid extents. Select one or more layers first.",
            )
            return
        self._zoom_to_bounds(bounds)
        self.notebook.select(0)
        self._redraw_2d()
        self._update_status()

    @staticmethod
    def _polygon_bounds(
        polygon: list[tuple[float, float]],
    ) -> tuple[float, float, float, float]:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return min(xs), max(xs), min(ys), max(ys)

    def _zoom_to_bounds(self, bounds: tuple[float, float, float, float]) -> None:
        xmin, xmax, ymin, ymax = bounds
        span_x = xmax - xmin
        span_y = ymax - ymin
        if span_x <= 0 and span_y <= 0:
            pad_x = 1.0
            pad_y = 1.0
        else:
            pad_x = max(span_x * 0.03, 1.0)
            pad_y = max(span_y * 0.03, 1.0)

        self._view_extent_2d = (
            xmin - pad_x,
            xmax + pad_x,
            ymin - pad_y,
            ymax + pad_y,
        )

    def _selected_layers_2d_bounds(self) -> Optional[tuple[float, float, float, float]]:
        xmin: Optional[float] = None
        xmax: Optional[float] = None
        ymin: Optional[float] = None
        ymax: Optional[float] = None

        def include(x0: float, x1: float, y0: float, y1: float) -> None:
            nonlocal xmin, xmax, ymin, ymax
            xmin = x0 if xmin is None else min(xmin, x0)
            xmax = x1 if xmax is None else max(xmax, x1)
            ymin = y0 if ymin is None else min(ymin, y0)
            ymax = y1 if ymax is None else max(ymax, y1)

        if self.show_raster.get():
            for layer in self._raster_layers:
                if not layer.visible.get():
                    continue
                rxmin, rxmax, rymin, rymax = layer.extent
                include(rxmin, rxmax, rymin, rymax)

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            if isinstance(kof, KOFFile):
                points = kof.all_points
                if not points:
                    continue
                xs = [p.y for p in points]
                ys = [p.x for p in points]
                include(min(xs), max(xs), min(ys), max(ys))
            else:
                b = kof.xy_bounds()
                if b is None:
                    continue
                include(b[0], b[2], b[1], b[3])

        for lx in self.landxml_files:
            for alignment in lx.alignments:
                if len(alignment.centerline) < 2:
                    continue
                ys = [pt[0] for pt in alignment.centerline]
                xs = [pt[1] for pt in alignment.centerline]
                include(min(ys), max(ys), min(xs), max(xs))

        if xmin is None or xmax is None or ymin is None or ymax is None:
            return None
        return xmin, xmax, ymin, ymax

    def _on_clear_sections(self) -> None:
        if not self._sections:
            return
        self._sections.clear()
        self._redraw()

    def _load_file(self, path: str, *, redraw: bool = True) -> None:
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".xml":
                schema_path = self.xsd_local_schema_path.get().strip() or None
                landxml = parse_landxml(
                    path,
                    station_interval=float(self.station_interval_m.get()),
                    strict_xsd_validation=bool(self.strict_xsd_validation.get()),
                    local_schema_path=schema_path,
                    allow_network_schema=bool(self.xsd_allow_network.get()),
                )
                self.landxml_files.append(landxml)
                self._landxml_group_vars[path] = tk.BooleanVar(value=True)
                for surface in landxml.surfaces:
                    self.files.append(surface)
                    self.layer_vars.append(tk.BooleanVar(value=True))
                    self._surface_owner_paths.append(path)
                self._on_toggle_landxml_child(path)
                self._rebuild_alignment_stations()
                if not landxml.surfaces and not landxml.alignments:
                    messagebox.showwarning(
                        "KOF Viewer",
                        f"LandXML '{os.path.basename(path)}' did not contain supported surfaces or alignments.",
                    )
                if landxml.xsd_validation_enabled:
                    verdict = "PASS" if landxml.xsd_validation_passed else "FAIL"
                    details = "\n".join(landxml.xsd_validation_errors[:8])
                    source = landxml.xsd_schema_source or "(unknown schema source)"
                    if landxml.xsd_validation_passed:
                        messagebox.showinfo(
                            "KOF Viewer",
                            f"Strict XSD validation {verdict}\nFile: {landxml.name}\nSchema: {source}",
                        )
                    else:
                        messagebox.showwarning(
                            "KOF Viewer",
                            f"Strict XSD validation {verdict}\nFile: {landxml.name}\nSchema: {source}\n\n{details}",
                        )
            else:
                kof = parse_kof(path)
                self.files.append(kof)
                self.layer_vars.append(tk.BooleanVar(value=True))
                self._surface_owner_paths.append(None)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to read '{path}':\n{exc}")
            return
        if redraw:
            self._refresh_file_selectors()
            self._refresh_layer_controls()
            self._redraw()

    def _refresh_file_selectors(self) -> None:
        names = [k.name for k in self.files]
        self.top_combo["values"] = names
        self.bottom_combo["values"] = names
        self.intermediate_kof_combo["values"] = names
        if names:
            if self.top_var.get() not in names:
                self.top_var.set(names[0])
            if self.bottom_var.get() not in names:
                self.bottom_var.set(names[1] if len(names) > 1 else names[0])
            if self.intermediate_kof_var.get() not in names:
                self.intermediate_kof_var.set(names[0])
        else:
            self.top_var.set("")
            self.bottom_var.set("")
            self.intermediate_kof_var.set("")

    def _refresh_intermediate_raster_selector(self) -> None:
        self._intermediate_raster_index_map.clear()
        names: list[str] = []
        for idx, layer in enumerate(self._raster_layers):
            if layer.elevation_surface is None:
                continue
            label = self._raster_display_name(idx)
            names.append(label)
            self._intermediate_raster_index_map[label] = idx
        self.intermediate_raster_combo["values"] = names
        if names and self.intermediate_raster_var.get() not in self._intermediate_raster_index_map:
            self.intermediate_raster_var.set(names[0])
        if not names:
            self.intermediate_raster_var.set("")

    def _refresh_layer_controls(self) -> None:
        for child in self.layers_inner.winfo_children():
            child.destroy()

        has_raster_layer = bool(self._raster_layers)

        if not self.files and not has_raster_layer and not self.landxml_files:
            ttk.Label(self.layers_inner, text="No layers loaded.").pack(anchor="w")
            return

        if has_raster_layer:
            ttk.Checkbutton(
                self.layers_inner,
                text="Raster layers",
                variable=self.show_raster,
                command=self._redraw,
            ).pack(anchor="w", pady=1)
            for idx in range(len(self._raster_layers) - 1, -1, -1):
                layer = self._raster_layers[idx]
                name = os.path.basename(layer.path)
                marks: list[str] = []
                if idx == self._elevation_raster_index:
                    marks.append("elev")
                if idx == len(self._raster_layers) - 1:
                    marks.append("top")
                suffix = f" ({', '.join(marks)})" if marks else ""
                ttk.Checkbutton(
                    self.layers_inner,
                    text=f"  Raster: {name}{suffix}",
                    variable=layer.visible,
                    command=self._redraw,
                ).pack(anchor="w", pady=1)
            ttk.Separator(self.layers_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(2, 4))

        # Flat KOF entries.
        for idx, surface in enumerate(self.files):
            if idx >= len(self.layer_vars):
                self.layer_vars.append(tk.BooleanVar(value=True))
            owner_path = self._surface_owner_paths[idx] if idx < len(self._surface_owner_paths) else None
            if owner_path is not None:
                continue

            if isinstance(surface, KOFFile):
                n_pts = sum(len(b.points) for b in surface.blocks)
                n_pl = sum(1 for b in surface.blocks if b.is_polyline and b.points)
                label = f"{surface.name} ({n_pts} pts, {n_pl} pl)"
            else:
                n_pts = len(surface.all_points)
                n_pl = len(surface.faces)
                label = f"{surface.name} ({n_pts} pts, {n_pl} tri)"
            ttk.Checkbutton(
                self.layers_inner,
                text=label,
                variable=self.layer_vars[idx],
                command=self._redraw,
            ).pack(anchor="w", pady=1)

        # Nested LandXML groups + child surfaces.
        if self.landxml_files:
            ttk.Separator(self.layers_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(2, 4))
            for lx in self.landxml_files:
                group_var = self._landxml_group_vars.setdefault(lx.path, tk.BooleanVar(value=True))
                n_align = len(lx.alignments)
                n_sta = sum(len(a.stations) for a in lx.alignments)
                child_indices = [
                    i for i, owner in enumerate(self._surface_owner_paths)
                    if owner == lx.path
                ]
                ttk.Checkbutton(
                    self.layers_inner,
                    text=f"LandXML: {lx.name} ({len(child_indices)} surfaces, {n_align} alignments, {n_sta} stations)",
                    variable=group_var,
                    command=lambda p=lx.path: self._on_toggle_landxml_group(p),
                ).pack(anchor="w", pady=(1, 0))

                for idx in child_indices:
                    surface = self.files[idx]
                    if not isinstance(surface, LandXMLSurface):
                        continue
                    n_pts = len(surface.all_points)
                    n_tri = len(surface.faces)
                    label = f"    - {surface.layer_name} ({n_pts} pts, {n_tri} tri)"
                    ttk.Checkbutton(
                        self.layers_inner,
                        text=label,
                        variable=self.layer_vars[idx],
                        command=lambda p=lx.path: self._on_toggle_landxml_child(p),
                    ).pack(anchor="w", pady=1)

    def _is_layer_visible(self, idx: int) -> bool:
        if idx < 0 or idx >= len(self.layer_vars):
            return True
        visible = bool(self.layer_vars[idx].get())
        owner_path = self._surface_owner_paths[idx] if idx < len(self._surface_owner_paths) else None
        if owner_path is not None:
            group_var = self._landxml_group_vars.get(owner_path)
            if group_var is not None and not bool(group_var.get()):
                return False
        return visible

    def _on_toggle_landxml_group(self, owner_path: str) -> None:
        group_var = self._landxml_group_vars.get(owner_path)
        if group_var is None:
            return
        state = bool(group_var.get())
        for idx, owner in enumerate(self._surface_owner_paths):
            if owner == owner_path and idx < len(self.layer_vars):
                self.layer_vars[idx].set(state)
        self._redraw()

    def _on_toggle_landxml_child(self, owner_path: str) -> None:
        selected = [
            bool(self.layer_vars[idx].get())
            for idx, owner in enumerate(self._surface_owner_paths)
            if owner == owner_path and idx < len(self.layer_vars)
        ]
        group_var = self._landxml_group_vars.get(owner_path)
        if group_var is not None:
            group_var.set(any(selected) if selected else False)
        self._redraw()

    def _find_file(self, name: str) -> Optional[SurfaceLike]:
        for k in self.files:
            if k.name == name:
                return k
        return None

    def _all_files_bounds(self) -> Optional[tuple[float, float, float, float]]:
        bounds = [k.xy_bounds() for k in self.files]
        valid = [b for b in bounds if b is not None]
        if not valid:
            return None
        y_mins, x_mins, y_maxs, x_maxs = zip(*valid)
        return min(y_mins), min(x_mins), max(y_maxs), max(x_maxs)

    def _safe_clip_buffer_m(self) -> float:
        try:
            buf = float(self.raster_clip_buffer_m.get())
        except (tk.TclError, ValueError):
            buf = 10.0
        if not np.isfinite(buf) or buf < 0.0:
            buf = 10.0
        self.raster_clip_buffer_m.set(buf)
        return buf

    def _current_volume_mode_key(self) -> str:
        """Resolve combobox value to internal mode key."""
        raw = self.volume_source_mode.get().strip()
        if raw in VOLUME_LABEL_TO_MODE:
            return VOLUME_LABEL_TO_MODE[raw]
        key = raw.lower()
        if key in VOLUME_MODE_LABELS:
            return key
        return "kof_vs_kof"

    def _current_intermediate_type_key(self) -> str:
        raw = self.intermediate_type.get().strip()
        if raw in INTERMEDIATE_LABEL_TO_TYPE:
            return INTERMEDIATE_LABEL_TO_TYPE[raw]
        key = raw.lower()
        if key in INTERMEDIATE_TYPE_LABELS:
            return key
        return "none"

    def _set_widget_state(
        self,
        widget: tk.Widget,
        enabled: bool,
        *,
        mode_when_enabled: str = "readonly",
    ) -> None:
        desired = mode_when_enabled if enabled else "disabled"
        try:
            widget.configure(state=desired)
        except tk.TclError:
            widget.configure(state="normal" if enabled else "disabled")

    def _safe_constant_step(self) -> float:
        try:
            step = float(self.intermediate_constant_step.get())
        except (tk.TclError, ValueError):
            step = 0.1
        if not np.isfinite(step) or step <= 0.0:
            step = 0.1
        self.intermediate_constant_step.set(step)
        return step

    def _nudge_intermediate_constant_z(self, direction: int) -> str:
        step = self._safe_constant_step()
        try:
            z_val = float(self.intermediate_constant_z.get())
        except (tk.TclError, ValueError):
            z_val = 0.0
        if not np.isfinite(z_val):
            z_val = 0.0
        z_val += float(direction) * step
        self.intermediate_constant_z.set(z_val)
        self._on_intermediate_type_change()
        return "break"

    def _on_intermediate_type_change(self) -> None:
        key = self._current_intermediate_type_key()
        method = self.volume_method.get().strip().lower()
        allow_intermediate = method != "triangles"
        constant_enabled = allow_intermediate and key == "constant"

        self._set_widget_state(self.intermediate_type_combo, allow_intermediate)
        self._set_widget_state(self.intermediate_kof_combo, allow_intermediate and key == "kof")
        self._set_widget_state(self.intermediate_raster_combo, allow_intermediate and key == "raster")
        self._set_widget_state(
            self.intermediate_constant_z_spin,
            constant_enabled,
            mode_when_enabled="normal",
        )
        self._set_widget_state(
            self.intermediate_constant_step_spin,
            constant_enabled,
            mode_when_enabled="normal",
        )
        self._set_widget_state(
            self.intermediate_constant_down_btn,
            constant_enabled,
            mode_when_enabled="normal",
        )
        self._set_widget_state(
            self.intermediate_constant_up_btn,
            constant_enabled,
            mode_when_enabled="normal",
        )

    def _all_surface_bounds(self) -> Optional[tuple[float, float, float, float]]:
        bounds: list[tuple[float, float, float, float]] = []
        kof_bounds = self._all_files_bounds()
        if kof_bounds is not None:
            bounds.append(kof_bounds)
        for layer in self._raster_layers:
            surface = layer.elevation_surface
            if surface is None:
                continue
            sb = surface.xy_bounds()
            if sb is not None:
                bounds.append(sb)
        if not bounds:
            return None
        y_mins, x_mins, y_maxs, x_maxs = zip(*bounds)
        return min(y_mins), min(x_mins), max(y_maxs), max(x_maxs)

    def _resolve_intermediate_surface(self) -> Optional[SurfaceLike]:
        return self._resolve_intermediate_surface_with_feedback(show_warnings=True)

    def _resolve_intermediate_surface_with_feedback(
        self,
        *,
        show_warnings: bool,
    ) -> Optional[SurfaceLike]:
        key = self._current_intermediate_type_key()

        def _warn(msg: str) -> None:
            if show_warnings:
                messagebox.showwarning("KOF Viewer", msg)

        if key == "none":
            return None
        if key == "kof":
            surface = self._find_file(self.intermediate_kof_var.get())
            if surface is None:
                _warn("Select an intermediate KOF layer.")
                return None
            return surface
        if key == "raster":
            idx = self._intermediate_raster_index_map.get(self.intermediate_raster_var.get())
            if idx is None:
                _warn("Select an intermediate raster layer.")
                return None
            if idx < 0 or idx >= len(self._raster_layers):
                _warn("Selected intermediate raster is no longer available.")
                return None
            layer = self._raster_layers[idx]
            if layer.elevation_surface is None:
                _warn("Selected intermediate raster is not a supported single-band elevation raster.")
                return None
            return layer.elevation_surface
        if key == "constant":
            try:
                z_val = float(self.intermediate_constant_z.get())
            except (tk.TclError, ValueError):
                _warn("Intermediate Constant Z must be numeric.")
                return None
            if not np.isfinite(z_val):
                _warn("Intermediate Constant Z must be finite.")
                return None
            bounds = self._all_surface_bounds()
            if bounds is None:
                _warn("No surface bounds available to build Constant Z layer.")
                return None
            return ConstantElevationSurface(name=f"Constant Z {z_val:.3f} m", z_value=z_val, bounds=bounds)
        _warn(f"Unknown intermediate layer type: {key}")
        return None

    def _section_intermediate_surface(self) -> Optional[SurfaceLike]:
        """Return currently selected intermediate surface for section/profile visualization."""
        if self.volume_method.get().strip().lower() == "triangles":
            return None
        if self._current_intermediate_type_key() == "none":
            return None
        return self._resolve_intermediate_surface_with_feedback(show_warnings=False)

    def _section_reference_raster(self) -> Optional[ElevationRasterSurface]:
        if self._elevation_raster_index is None:
            return None
        if self._elevation_raster_index < 0 or self._elevation_raster_index >= len(self._raster_layers):
            return None
        layer = self._raster_layers[self._elevation_raster_index]
        if layer.elevation_surface is None:
            return None
        mode = self._current_volume_mode_key()
        if mode in {"top_minus_raster", "raster_minus_bottom", "raster_minus_top"}:
            return layer.elevation_surface
        return None

    def _section_area_pair(
        self,
        top: SurfaceLike,
        bottom: SurfaceLike,
    ) -> tuple[Any, Any, str, str]:
        """Return surfaces used for section area shading based on active volume mode."""
        mode = self._current_volume_mode_key()
        raster = self._section_reference_raster()

        if mode == "top_minus_raster" and raster is not None:
            return top, raster, top.name, raster.name
        if mode == "raster_minus_bottom" and raster is not None:
            return raster, bottom, raster.name, bottom.name
        if mode == "raster_minus_top" and raster is not None:
            return raster, top, raster.name, top.name

        return top, bottom, top.name, bottom.name

    def _on_compute_volume(self) -> None:
        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        mode = self._current_volume_mode_key()
        intermediate_type = self._current_intermediate_type_key()

        if mode == "kof_vs_kof":
            if top is None or bot is None:
                messagebox.showwarning("KOF Viewer", "Select both a top and bottom surface.")
                return
            if top is bot:
                messagebox.showwarning("KOF Viewer", "Top and bottom must be different files.")
                return
            top_surface = top
            bottom_surface = bot
        elif mode == "top_minus_raster":
            if top is None:
                messagebox.showwarning("KOF Viewer", "Select a top KOF surface.")
                return
            elev = self._section_reference_raster()
            if elev is None:
                messagebox.showwarning(
                    "KOF Viewer",
                    "Set a single-band elevation raster first (Set elevation raster).",
                )
                return
            top_surface = top
            bottom_surface = elev
        elif mode == "raster_minus_bottom":
            if bot is None:
                messagebox.showwarning("KOF Viewer", "Select a bottom KOF surface.")
                return
            elev = self._section_reference_raster()
            if elev is None:
                messagebox.showwarning(
                    "KOF Viewer",
                    "Set a single-band elevation raster first (Set elevation raster).",
                )
                return
            top_surface = elev
            bottom_surface = bot
        elif mode == "raster_minus_top":
            if top is None:
                messagebox.showwarning("KOF Viewer", "Select a top KOF surface.")
                return
            elev = self._section_reference_raster()
            if elev is None:
                messagebox.showwarning(
                    "KOF Viewer",
                    "Set a single-band elevation raster first (Set elevation raster).",
                )
                return
            top_surface = elev
            bottom_surface = top
        else:
            messagebox.showwarning("KOF Viewer", f"Unknown volume mode: {mode}")
            return

        mask_poly = self._mask_polygon if len(self._mask_polygon) >= 3 else None
        method = self.volume_method.get().strip().lower()

        if method == "triangles" and intermediate_type != "none":
            messagebox.showwarning(
                "KOF Viewer",
                "Intermediate layer split requires 'raster' method. "
                "'triangles' currently supports top-bottom pairs only.",
            )
            return

        intermediate_surface: Optional[SurfaceLike] = None
        if intermediate_type != "none":
            intermediate_surface = self._resolve_intermediate_surface()
            if intermediate_surface is None:
                return

        if intermediate_surface is top_surface or intermediate_surface is bottom_surface:
            messagebox.showwarning(
                "KOF Viewer",
                "Intermediate layer must be different from top and bottom surfaces.",
            )
            return

        try:
            if intermediate_surface is None:
                result = compute_volume(top_surface, bottom_surface, mask_polygon=mask_poly, method=method)
            else:
                result = compute_volume_with_intermediate(
                    top_surface,
                    intermediate_surface,
                    bottom_surface,
                    mask_polygon=mask_poly,
                    method=method,
                )
        except ValueError as exc:
            messagebox.showerror("KOF Viewer", str(exc))
            return
        if result is None:
            messagebox.showerror(
                "KOF Viewer",
                "Could not compute volume. Check that both surfaces have "
                ">= 3 points and that their XY extents overlap.",
            )
            return
        self.last_volume = result
        mask_suffix = " (masked)" if mask_poly is not None else ""
        if isinstance(result, SplitVolumeResult):
            upper = result.upper
            lower = result.lower
            self.volume_text.set(
                f"{mode}/{method} split: {upper.top_name}-{upper.bottom_name} "
                f"Net {upper.net:+,.2f} m³; {lower.top_name}-{lower.bottom_name} "
                f"Net {lower.net:+,.2f} m³; Total {result.net:+,.2f} m³{mask_suffix}"
            )
        else:
            self.volume_text.set(
                f"{mode}/{method}: Net {result.net:+,.2f} m³   (Cut {result.cut:,.2f} / "
                f"Fill {result.fill:,.2f})   over {result.area:,.1f} m²{mask_suffix}"
            )
        if self.show_triangulation.get():
            self._redraw()

    def _on_boundary_change(self) -> None:
        try:
            factor = float(self.boundary_factor.get())
        except (tk.TclError, ValueError):
            return
        for kof in self.files:
            if kof._tri is not None or kof.ensure_triangulation():
                kof.set_boundary_factor(factor)
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw()

    # --------------------------------------------------------- Section tool
    def _on_toggle_section(self) -> None:
        if self._mask_mode:
            self._end_mask_mode()
        if self._section_mode:
            self._end_section_mode()
            return
        if len(self.files) < 2:
            messagebox.showinfo("KOF Viewer",
                                "Load at least two files and select Top/Bottom first.")
            return
        if not self.top_var.get() or not self.bottom_var.get():
            messagebox.showinfo("KOF Viewer",
                                "Pick a top and bottom surface before drawing a section.")
            return
        self._section_mode = True
        self._section_click_points = []
        self.section_btn.configure(text="Cancel section")
        self.notebook.select(0)  # switch to 2D tab
        self.status.set("Section mode: click two points on the 2D plot "
                        "(right-click or press Esc to cancel).")
        self._section_cid = self.canvas2d.mpl_connect(
            "button_press_event", self._on_section_click
        )
        self.bind("<Escape>", lambda _e: self._end_section_mode())

    def _end_section_mode(self) -> None:
        self._section_mode = False
        if self._section_cid is not None:
            self.canvas2d.mpl_disconnect(self._section_cid)
            self._section_cid = None
        self.section_btn.configure(text="Draw section")
        self.unbind("<Escape>")
        self._section_click_points = []
        # Drop any in-progress preview; completed sections are kept via
        # self._sections and redrawn by _redraw_2d.
        self._section_preview = None
        self._update_status()

    def _on_section_click(self, event) -> None:  # noqa: ANN001
        if event.inaxes is not self.ax2d:
            return
        if event.button == 3:  # right-click cancels
            self._end_section_mode()
            return
        if event.xdata is None or event.ydata is None:
            return
        self._section_click_points.append((event.xdata, event.ydata))

        if len(self._section_click_points) == 1:
            self.status.set(
                "Section mode: click the second endpoint "
                "(right-click or Esc to cancel)."
            )
            (y0, x0) = self._section_click_points[0]
            self.ax2d.plot([y0], [x0], marker="x", color="black", markersize=8)
            self.canvas2d.draw_idle()
            return

        (y0, x0), (y1, x1) = self._section_click_points
        self._sections.append(((y0, x0), (y1, x1)))

        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        self._end_section_mode()
        self._redraw_2d()  # draw the persisted section (and clear preview)
        if top is not None and bot is not None:
            section_intermediate = self._section_intermediate_surface()
            area_top_surface, area_bottom_surface, area_top_name, area_bottom_name = self._section_area_pair(top, bot)
            SectionWindow(
                self,
                top,
                bot,
                (y0, x0),
                (y1, x1),
                raster_surface=self._section_reference_raster(),
                intermediate_surface=section_intermediate,
                area_top_surface=area_top_surface,
                area_bottom_surface=area_bottom_surface,
                area_top_name=area_top_name,
                area_bottom_name=area_bottom_name,
                label=self._section_label(len(self._sections) - 1),
            )

    # ----------------------------------------------------------- Polygon mask
    def _on_toggle_mask(self) -> None:
        if self._section_mode:
            self._end_section_mode()
        if self._mask_mode:
            self._end_mask_mode()
            self._redraw_2d()
            return
        self._mask_mode = True
        self._mask_click_points = []
        self.mask_btn.configure(text="Cancel mask")
        self.notebook.select(0)  # switch to 2D tab
        self.status.set(
            "Mask mode: left-click to add polygon vertices. "
            "Right-click (or double-click) to close. Esc to cancel."
        )
        self._mask_cid = self.canvas2d.mpl_connect(
            "button_press_event", self._on_mask_click
        )
        self.bind("<Escape>", lambda _e: self._cancel_mask_mode())

    def _cancel_mask_mode(self) -> None:
        self._mask_click_points = []
        self._end_mask_mode()
        self._redraw_2d()

    def _end_mask_mode(self) -> None:
        self._mask_mode = False
        if self._mask_cid is not None:
            self.canvas2d.mpl_disconnect(self._mask_cid)
            self._mask_cid = None
        self.mask_btn.configure(text="Draw mask")
        self.unbind("<Escape>")
        self._update_status()

    def _on_clear_mask(self) -> None:
        had_mask = len(self._mask_polygon) >= 3
        self._mask_polygon.clear()
        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        if had_mask and self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw()

    def _on_mask_click(self, event) -> None:  # noqa: ANN001
        if event.inaxes is not self.ax2d:
            return
        if event.button == 3:  # right-click finalizes, if possible
            if len(self._mask_click_points) >= 3:
                self._mask_polygon = list(self._mask_click_points)
                self._mask_click_points = []
                self._end_mask_mode()
                if self.last_volume is not None:
                    self.volume_text.set("Volume: (stale – recompute)")
                self._redraw()
            else:
                self._cancel_mask_mode()
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        self._mask_click_points.append((event.xdata, event.ydata))
        if event.dblclick and len(self._mask_click_points) >= 3:
            self._mask_polygon = list(self._mask_click_points)
            self._mask_click_points = []
            self._end_mask_mode()
            if self.last_volume is not None:
                self.volume_text.set("Volume: (stale – recompute)")
            self._redraw()
            return

        self.status.set(
            f"Mask mode: {len(self._mask_click_points)} vertex/vertices. "
            "Left-click to add more, right-click or double-click to close."
        )
        self._redraw_2d()

    @staticmethod
    def _section_label(index: int) -> str:
        """Return A, B, ..., Z, AA, AB, ... for the given 0-based index."""
        letters = ""
        n = index
        while True:
            letters = chr(ord("A") + (n % 26)) + letters
            n = n // 26 - 1
            if n < 0:
                break
        return letters

    # --------------------------------------------------------------- Render
    def _redraw(self) -> None:
        self._redraw_2d()
        self._redraw_3d()
        self._update_status()

    def _redraw_2d(self) -> None:
        self.ax2d.clear()
        self.ax2d.set_xlabel("Easting (Y)")
        self.ax2d.set_ylabel("Northing (X)")
        self.ax2d.grid(True, linestyle=":", alpha=0.5)

        if self.show_raster.get() and self._raster_layers:
            for layer in self._raster_layers:
                if not layer.visible.get():
                    continue
                self.ax2d.imshow(
                    layer.image,
                    extent=layer.extent,
                    origin=layer.origin,
                    alpha=float(layer.alpha.get()),
                    zorder=0,
                )

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            color = FILE_COLORS[idx % len(FILE_COLORS)]
            if isinstance(kof, KOFFile):
                labeled = False
                for block in kof.blocks:
                    if not block.points:
                        continue
                    xs = [p.y for p in block.points]
                    ys = [p.x for p in block.points]
                    label = kof.name if not labeled else None
                    labeled = True
                    if block.is_polyline and self.show_lines.get() and len(block.points) >= 2:
                        self.ax2d.plot(xs, ys, "-", color=color, linewidth=1.2, label=label)
                        if self.show_markers.get():
                            self.ax2d.plot(xs, ys, "o", color=color, markersize=3)
                    else:
                        marker = "o" if self.show_markers.get() else "."
                        self.ax2d.plot(xs, ys, marker, color=color, markersize=4,
                                       linestyle="None", label=label)

                    if self.show_labels.get():
                        for p in block.points:
                            self.ax2d.annotate(
                                p.pkt, (p.y, p.x), fontsize=7,
                                xytext=(3, 3), textcoords="offset points", color=color,
                            )
            else:
                if self.show_markers.get() and kof.ensure_triangulation() and kof._xy is not None:
                    self.ax2d.plot(
                        kof._xy[:, 0],
                        kof._xy[:, 1],
                        "o",
                        color=color,
                        markersize=2,
                        linestyle="None",
                        label=kof.name,
                    )

            draw_tin_faces = isinstance(kof, LandXMLSurface) and len(kof.faces) > 0 and self.show_lines.get()
            if (self.show_triangulation.get() or draw_tin_faces) and kof.ensure_triangulation():
                kept = kof.kept_simplices
                if kept is not None and len(kept) > 0:
                    if draw_tin_faces and self.show_tin_fill.get() and kof._xy is not None:
                        face_vals = np.zeros(len(kept), dtype=float)
                        self.ax2d.tripcolor(
                            kof._xy[:, 0],
                            kof._xy[:, 1],
                            face_vals,
                            triangles=kept,
                            shading="flat",
                            vmin=0.0,
                            vmax=1.0,
                            color=color,
                            alpha=float(self.tin_fill_alpha.get()),
                            zorder=1,
                        )
                    self.ax2d.triplot(
                        kof._xy[:, 0], kof._xy[:, 1], kept,
                        color=color, linewidth=0.45, alpha=0.75,
                    )

        for lidx, lx in enumerate(self.landxml_files):
            color = FILE_COLORS[(len(self.files) + lidx) % len(FILE_COLORS)]
            labeled = False
            for alignment in lx.alignments:
                if self.show_lines.get() and len(alignment.centerline) >= 2:
                    ys = [pt[0] for pt in alignment.centerline]
                    xs = [pt[1] for pt in alignment.centerline]
                    self.ax2d.plot(
                        ys,
                        xs,
                        "-",
                        color=color,
                        linewidth=1.6,
                        alpha=0.85,
                        label=f"{lx.name}::{alignment.name}" if not labeled else None,
                    )
                    labeled = True
                if self.show_stations.get():
                    for st in alignment.stations:
                        self.ax2d.plot([st.y], [st.x], marker="x", color=color, markersize=4)
                        self.ax2d.annotate(
                            st.label,
                            (st.y, st.x),
                            fontsize=7,
                            xytext=(3, 3),
                            textcoords="offset points",
                            color=color,
                        )

        if self.equal_aspect.get():
            self.ax2d.set_aspect("equal", adjustable="datalim")
        else:
            self.ax2d.set_aspect("auto")

        if self._view_extent_2d is not None:
            xmin, xmax, ymin, ymax = self._view_extent_2d
            self.ax2d.set_xlim(xmin, xmax)
            self.ax2d.set_ylim(ymin, ymax)

        # Persisted section lines (drawn on top of surface data).
        for i, ((y0, x0), (y1, x1)) in enumerate(self._sections):
            label = self._section_label(i)
            self.ax2d.plot([y0, y1], [x0, x1], "-", color="black", linewidth=1.6,
                           zorder=5)
            self.ax2d.plot([y0, y1], [x0, x1], "s", color="black", markersize=4,
                           zorder=6)
            # Label the two endpoints as A / A' (etc.)
            self.ax2d.annotate(
                label, (y0, x0), fontsize=9, fontweight="bold", color="black",
                xytext=(6, 6), textcoords="offset points", zorder=7,
            )
            self.ax2d.annotate(
                f"{label}'", (y1, x1), fontsize=9, fontweight="bold", color="black",
                xytext=(6, 6), textcoords="offset points", zorder=7,
            )

        # Active polygon mask.
        if len(self._mask_polygon) >= 3:
            arr = np.asarray(self._mask_polygon, dtype=float)
            closed = np.vstack([arr, arr[0]])
            self.ax2d.fill(closed[:, 0], closed[:, 1],
                           color="#ffbf00", alpha=0.15, zorder=8)
            self.ax2d.plot(closed[:, 0], closed[:, 1], "-", color="#cc8a00",
                           linewidth=1.8, zorder=9)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00",
                           markersize=4, zorder=10)

        # In-progress polygon while mask mode is active.
        if self._mask_mode and self._mask_click_points:
            arr = np.asarray(self._mask_click_points, dtype=float)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "--", color="#cc8a00",
                           linewidth=1.3, zorder=11)
            self.ax2d.plot(arr[:, 0], arr[:, 1], "o", color="#cc8a00",
                           markersize=4, zorder=12)
            if len(arr) >= 2:
                self.ax2d.plot([arr[-1, 0], arr[0, 0]], [arr[-1, 1], arr[0, 1]],
                               ":", color="#cc8a00", linewidth=1.0, zorder=11)

        if self.files or self.landxml_files:
            self.ax2d.legend(loc="best", fontsize=8)
        else:
            self.ax2d.text(
                0.5, 0.5, "Open a .kof or .xml file to begin",
                transform=self.ax2d.transAxes, ha="center", va="center", color="gray",
            )

        self.fig2d.tight_layout()
        self.canvas2d.draw_idle()

    def _redraw_3d(self) -> None:
        self.ax3d.clear()
        self.ax3d.set_xlabel("Easting (Y)")
        self.ax3d.set_ylabel("Northing (X)")
        self.ax3d.set_zlabel("Elevation (H)")

        for idx, kof in enumerate(self.files):
            if not self._is_layer_visible(idx):
                continue
            color = FILE_COLORS[idx % len(FILE_COLORS)]
            labeled = False
            if self.show_triangulation.get() and hasattr(kof, "ensure_triangulation") and kof.ensure_triangulation():
                kept = kof.kept_simplices
                if kept is not None and len(kept) > 0:
                    self.ax3d.plot_trisurf(
                        kof._xy[:, 0], kof._xy[:, 1], kof._z,
                        triangles=kept,
                        color=color, alpha=0.35, linewidth=0.2, edgecolor=color,
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
                    if block.is_polyline and self.show_lines.get() and len(block.points) >= 2:
                        self.ax3d.plot(xs, ys, zs, "-", color=color,
                                       linewidth=1.2, label=label)
                        if self.show_markers.get():
                            self.ax3d.scatter(xs, ys, zs, color=color, s=10)
                    else:
                        if self.show_markers.get():
                            self.ax3d.scatter(xs, ys, zs, color=color, s=12, label=label)

                    if self.show_labels.get():
                        for p in block.points:
                            self.ax3d.text(p.y, p.x, p.h, p.pkt, fontsize=7, color=color)
            else:
                if kof.ensure_triangulation() and kof._xy is not None and kof._z is not None and self.show_markers.get():
                    self.ax3d.scatter(kof._xy[:, 0], kof._xy[:, 1], kof._z, color=color, s=10, label=kof.name)

        if self.files:
            self.ax3d.legend(loc="best", fontsize=8)
            self._equalize_3d_aspect()
        else:
            self.ax3d.text2D(
                0.5, 0.5, "Open a surface-enabled file to begin",
                transform=self.ax3d.transAxes, ha="center", va="center", color="gray",
            )

        self.fig3d.tight_layout()
        self.canvas3d.draw_idle()

    def _equalize_3d_aspect(self) -> None:
        all_x: list[float] = []
        all_y: list[float] = []
        all_z: list[float] = []
        for kof in self.files:
            if isinstance(kof, KOFFile):
                for p in kof.all_points:
                    all_x.append(p.y)
                    all_y.append(p.x)
                    all_z.append(p.h)
            else:
                if kof.ensure_triangulation() and kof._xy is not None and kof._z is not None:
                    all_x.extend(kof._xy[:, 0].tolist())
                    all_y.extend(kof._xy[:, 1].tolist())
                    all_z.extend(kof._z.tolist())
        if not all_x:
            return
        rx = max(all_x) - min(all_x)
        ry = max(all_y) - min(all_y)
        rz = max(all_z) - min(all_z)
        r = max(rx, ry, rz, 1e-6)
        cx = (max(all_x) + min(all_x)) / 2
        cy = (max(all_y) + min(all_y)) / 2
        cz = (max(all_z) + min(all_z)) / 2
        self.ax3d.set_xlim(cx - r / 2, cx + r / 2)
        self.ax3d.set_ylim(cy - r / 2, cy + r / 2)
        self.ax3d.set_zlim(cz - r / 2, cz + r / 2)
        try:
            self.ax3d.set_box_aspect((1, 1, 1))
        except Exception:  # noqa: BLE001
            pass

    def _update_status(self) -> None:
        if self._section_mode:
            return
        if self._mask_mode:
            return
        if not self.files and not self.landxml_files:
            self.status.set("No file loaded.")
            return
        parts = []
        total_points = 0
        total_blocks = 0
        for kof in self.files:
            if isinstance(kof, KOFFile):
                n_pts = sum(len(b.points) for b in kof.blocks)
                n_pl = sum(1 for b in kof.blocks if b.is_polyline and b.points)
                total_points += n_pts
                total_blocks += n_pl
                parts.append(f"{kof.name}: {n_pts} pts / {n_pl} polylines")
            else:
                n_pts = len(kof.all_points)
                n_tri = len(kof.faces)
                total_points += n_pts
                total_blocks += n_tri
                parts.append(f"{kof.name}: {n_pts} pts / {n_tri} triangles")
        for lx in self.landxml_files:
            n_align = len(lx.alignments)
            n_sta = sum(len(a.stations) for a in lx.alignments)
            parts.append(f"{lx.name}: {n_align} alignments / {n_sta} stations")
        base = (
            f"{len(self.files)} surface layer(s), {total_points} points, "
            f"{total_blocks} primitives   |   " + "  ·  ".join(parts)
        )
        if len(self._mask_polygon) >= 3:
            base += f"   |   mask: {len(self._mask_polygon)} vertices"
        if self._raster_layers:
            state = "on" if self.show_raster.get() else "off"
            n_visible = sum(1 for r in self._raster_layers if r.visible.get())
            elev_name = "none"
            if self._elevation_raster_index is not None and 0 <= self._elevation_raster_index < len(self._raster_layers):
                elev_name = os.path.basename(self._raster_layers[self._elevation_raster_index].path)
            base += (
                f"   |   rasters: {len(self._raster_layers)} ({n_visible} visible, {state})"
                f"   |   elevation raster: {elev_name}"
            )
        self.status.set(base)


# ---------------------------------------------------------------------------
# Section window
# ---------------------------------------------------------------------------


class SectionWindow(tk.Toplevel):
    """Toplevel showing a vertical cross-section between two surfaces."""

    def __init__(
        self,
        master: tk.Misc,
        top: SurfaceLike,
        bottom: SurfaceLike,
        p0: tuple[float, float],
        p1: tuple[float, float],
        raster_surface: Optional[ElevationRasterSurface] = None,
        intermediate_surface: Optional[Any] = None,
        area_top_surface: Optional[Any] = None,
        area_bottom_surface: Optional[Any] = None,
        area_top_name: Optional[str] = None,
        area_bottom_name: Optional[str] = None,
        n_samples: int = 200,
        label: str = "",
    ) -> None:
        super().__init__(master)
        title_label = f" ({label}–{label}')" if label else ""
        raster_title = " + raster" if raster_surface is not None else ""
        intermediate_title = " + intermediate" if intermediate_surface is not None else ""
        self.title(
            f"Section{title_label}: {top.name} (top) vs {bottom.name} (bottom){raster_title}{intermediate_title}"
        )
        self.geometry("900x520")

        y0, x0 = p0
        y1, x1 = p1
        total_len = float(np.hypot(y1 - y0, x1 - x0))
        if total_len <= 0:
            ttk.Label(self, text="Zero-length section.").pack(padx=20, pady=20)
            return

        ts = np.linspace(0.0, 1.0, n_samples)
        ys = y0 + (y1 - y0) * ts
        xs = x0 + (x1 - x0) * ts
        dists = ts * total_len

        z_top = top.interpolate(ys, xs)
        z_bot = bottom.interpolate(ys, xs)
        z_raster = raster_surface.interpolate(ys, xs) if raster_surface is not None else None
        z_intermediate = (
            intermediate_surface.interpolate(ys, xs)
            if intermediate_surface is not None
            else None
        )
        z_area_top = area_top_surface.interpolate(ys, xs) if area_top_surface is not None else z_top
        z_area_bottom = area_bottom_surface.interpolate(ys, xs) if area_bottom_surface is not None else z_bot
        area_top_label = area_top_name or top.name
        area_bottom_label = area_bottom_name or bottom.name

        # Area between curves (m² of cross-section); split into cut (top above
        # bottom) and fill (top below bottom).
        diff = z_area_top - z_area_bottom
        mask = np.isfinite(diff)
        if mask.any():
            d_valid = dists[mask]
            diff_valid = diff[mask]
            # Positive and negative integrals via trapezoid rule.
            pos = np.clip(diff_valid, 0, None)
            neg = np.clip(diff_valid, None, 0)
            area_cut = float(np.trapezoid(pos, d_valid))
            area_fill = float(-np.trapezoid(neg, d_valid))
            area_net = float(np.trapezoid(diff_valid, d_valid))
        else:
            area_cut = area_fill = area_net = float("nan")

        fig = Figure(figsize=(8, 4.5), dpi=100)
        ax = fig.add_subplot(111)

        if np.any(np.isfinite(z_top)):
            ax.plot(dists, z_top, color="#1f77b4", linewidth=1.6,
                    label=f"Top: {top.name}")
        if np.any(np.isfinite(z_bot)):
            ax.plot(dists, z_bot, color="#d62728", linewidth=1.6,
                    label=f"Bottom: {bottom.name}")
        if z_raster is not None and np.any(np.isfinite(z_raster)):
            ax.plot(
                dists,
                z_raster,
                color="#2ca02c",
                linewidth=1.4,
                linestyle="--",
                label=f"Raster: {raster_surface.name}",
            )
        if z_intermediate is not None and np.any(np.isfinite(z_intermediate)):
            intermediate_name = getattr(intermediate_surface, "name", "Intermediate")
            ax.plot(
                dists,
                z_intermediate,
                color="#ff7f0e",
                linewidth=1.4,
                linestyle=":",
                label=f"Intermediate: {intermediate_name}",
            )

        # Shade split legs in different colors when intermediate is shown.
        if z_intermediate is not None and np.any(np.isfinite(z_intermediate)):
            both_upper = np.isfinite(z_top) & np.isfinite(z_intermediate)
            both_lower = np.isfinite(z_intermediate) & np.isfinite(z_bot)
            intermediate_name = getattr(intermediate_surface, "name", "Intermediate")
            if both_upper.any():
                ax.fill_between(
                    dists,
                    z_intermediate,
                    z_top,
                    where=both_upper,
                    color="#4c78a8",
                    alpha=0.22,
                    label=f"Upper leg ({top.name} - {intermediate_name})",
                )
            if both_lower.any():
                ax.fill_between(
                    dists,
                    z_bot,
                    z_intermediate,
                    where=both_lower,
                    color="#f58518",
                    alpha=0.22,
                    label=f"Lower leg ({intermediate_name} - {bottom.name})",
                )
        else:
            # Shade the area between surfaces where both are defined.
            both = np.isfinite(z_area_top) & np.isfinite(z_area_bottom)
            if both.any():
                cut_mask = both & (z_area_top >= z_area_bottom)
                fill_mask = both & (z_area_top < z_area_bottom)
                if cut_mask.any():
                    ax.fill_between(dists, z_area_bottom, z_area_top, where=cut_mask,
                                    color="#1f77b4", alpha=0.25,
                                    label=f"Cut ({area_top_label} ≥ {area_bottom_label})")
                if fill_mask.any():
                    ax.fill_between(dists, z_area_bottom, z_area_top, where=fill_mask,
                                    color="#d62728", alpha=0.25,
                                    label=f"Fill ({area_top_label} < {area_bottom_label})")

        ax.set_xlabel("Distance along section (m)")
        ax.set_ylabel("Elevation (m)")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(
            f"Length {total_len:,.2f} m   |   "
            f"Area ({area_top_label} - {area_bottom_label}) net {area_net:+,.2f} m²   "
            f"(cut {area_cut:,.2f} / fill {area_fill:,.2f})"
        )

        canvas = FigureCanvasTkAgg(fig, master=self)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(canvas, self)
        toolbar.update()

        info = ttk.Label(
            self,
            text=(
                f"From ({x0:,.2f} N, {y0:,.2f} E) "
                f"to ({x1:,.2f} N, {y1:,.2f} E)   "
                f"length={total_len:,.2f} m, {n_samples} samples"
            ),
            anchor=tk.W, padding=(8, 2),
        )
        info.pack(side=tk.BOTTOM, fill=tk.X)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone 2D/3D viewer for KOF files.")
    parser.add_argument("files", nargs="*", help="KOF files to preload")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    app = KOFViewer(initial_files=args.files)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
