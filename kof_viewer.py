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
import csv
import json
import os
import tempfile
import sys
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
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
from kofvision.mask_io import (
    MaskDataError,
    MaskDefinition,
    load_masks_json,
    save_masks_json,
)
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
from kofvision.raster import ElevationRasterSurface
from kofvision.viewer_serialization import (
    ProjectPayloadError,
    apply_project_payload,
    build_project_payload,
    validate_project_payload,
)
from kofvision.viewer_state import clear_cached_mask_volume_results, reset_for_clear
from kofvision.viewer_ui import DARK_COLORS, configure_viewer_styles
from kofvision.viewer_tools import (
    MaskEditTool,
    MaskTool,
    MeasureTool,
    NoteTool,
    SectionTool,
    ToolDispatcher,
)
from kofvision.viewer_rendering import render_2d, render_3d
from kofvision.raster_management import RasterLayerManager

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

MASK_SCOPE_LABELS: dict[str, str] = {
    "selected": "Selected mask",
    "visible_union": "Visible masks (union)",
    "all_union": "All masks (union)",
}
MASK_LABEL_TO_SCOPE: dict[str, str] = {v: k for k, v in MASK_SCOPE_LABELS.items()}

MEASURE_MODE_LABELS: dict[str, str] = {
    "line": "Line distance",
    "polygon": "Polygon area",
}
MEASURE_LABEL_TO_MODE: dict[str, str] = {v: k for k, v in MEASURE_MODE_LABELS.items()}


@dataclass
class _RasterViewLayer:
    path: str
    image: np.ndarray
    extent: tuple[float, float, float, float]
    origin: str
    visible: tk.BooleanVar
    alpha: tk.DoubleVar
    elevation_surface: Optional[ElevationRasterSurface]


@dataclass
class NoteDefinition:
    title: str
    text: str
    y: float
    x: float
    visible: bool = True


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
            background=DARK_COLORS["bg_surface"],
            foreground=DARK_COLORS["fg_primary"],
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
        self._surface_source_paths: list[Optional[str]] = []
        self._landxml_group_vars: dict[str, tk.BooleanVar] = {}
        self.last_volume: Optional[VolumeResult | SplitVolumeResult] = None
        self._latest_section_report_path: Optional[str] = None

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
        self.mask_compute_scope = tk.StringVar(value=MASK_SCOPE_LABELS["selected"])
        self.measure_mode = tk.StringVar(value=MEASURE_MODE_LABELS["line"])

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
        self._section_windows: list[tk.Toplevel] = []
        self._active_section_marker_index: Optional[int] = None
        self._active_section_marker_chainage_m: Optional[float] = None
        self._active_section_index: Optional[int] = None
        self.active_section_var = tk.StringVar(value="")

        # Polygon mask state for volume limitation in 2D.
        self._mask_mode = False
        self._mask_click_points: list[tuple[float, float]] = []
        self._mask_cid: Optional[int] = None
        self._masks: list[MaskDefinition] = []
        self._mask_visibility_vars: list[tk.BooleanVar] = []
        self._active_mask_index: Optional[int] = None
        self.active_mask_var = tk.StringVar(value="")
        self._mask_visibility_checks: list[tk.Widget] = []
        self._last_volume_per_mask_rows: list[str] = []
        self._last_mask_volume_results: list[dict[str, Any]] = []

        # Mask edit state (drag vertices for selected mask).
        self._mask_edit_mode = False
        self._mask_edit_press_cid: Optional[int] = None
        self._mask_edit_motion_cid: Optional[int] = None
        self._mask_edit_release_cid: Optional[int] = None
        self._mask_drag_vertex_index: Optional[int] = None
        self._mask_changed_since_last_compute = False

        # 2D measurement state.
        self._measure_mode = False
        self._measure_click_points: list[tuple[float, float]] = []
        self._measure_cid: Optional[int] = None
        self._measure_results: list[dict[str, Any]] = []

        # 2D map notes state.
        self._note_mode = False
        self._note_cid: Optional[int] = None
        self._note_pick_cid: Optional[int] = None
        self._notes: list[NoteDefinition] = []
        self._active_note_index: Optional[int] = None
        self._note_visibility_vars: list[tk.BooleanVar] = []
        self._pending_note_click_after_id: Optional[str] = None
        self._pending_note_click_index: Optional[int] = None
        self._RasterViewLayerClass = _RasterViewLayer
        self._tool_dispatch = ToolDispatcher()
        self._raster_manager = RasterLayerManager(self)

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
        configure_viewer_styles(self)

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _e: self._invoke_shortcut(self._on_open))
        self.bind_all("<Control-e>", lambda _e: self._invoke_shortcut(self._on_export_gpkg))
        self.bind_all("<Control-Shift-K>", lambda _e: self._invoke_shortcut(self._on_clear))
        self.bind_all("<Control-Shift-Z>", lambda _e: self._invoke_shortcut(self._on_zoom_selected_layers))
        self.bind_all("<Control-Shift-R>", lambda _e: self._invoke_shortcut(self._on_reset_view))
        self.bind_all("<Control-Shift-V>", lambda _e: self._invoke_shortcut(self._on_compute_volume))
        self.bind_all("<Control-Shift-S>", lambda _e: self._invoke_shortcut(self._on_toggle_section))
        self.bind_all("<Control-Shift-M>", lambda _e: self._invoke_shortcut(self._on_toggle_mask))
        self.bind_all("<Control-Shift-D>", lambda _e: self._invoke_shortcut(self._on_toggle_measure))
        self.bind_all("<Control-Shift-N>", lambda _e: self._invoke_shortcut(self._on_toggle_note_mode))

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

        self.measure_btn = self._make_toolbar_button(top, text="Measure", command=self._on_toggle_measure)
        self.measure_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(self.measure_btn, "Shortcut: Ctrl+Shift+D")

        self.note_btn = self._make_toolbar_button(top, text="Add note", command=self._on_toggle_note_mode)
        self.note_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(self.note_btn, "Shortcut: Ctrl+Shift+N")

        self.measure_mode_btn = ttk.Menubutton(top, text="Measure mode")
        measure_mode_menu = tk.Menu(self.measure_mode_btn, tearoff=False)
        for _mode_key, mode_label in MEASURE_MODE_LABELS.items():
            measure_mode_menu.add_radiobutton(
                label=mode_label,
                value=mode_label,
                variable=self.measure_mode,
                command=self._on_measure_mode_changed,
            )
        self.measure_mode_btn.configure(menu=measure_mode_menu)
        self.measure_mode_btn.pack(side=tk.LEFT, padx=3)
        _HoverTooltip(self.measure_mode_btn, "Choose how Measure behaves (line distance or polygon area).")

        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        save_project_btn = self._make_toolbar_button(top, text="Save project", command=self._on_save_project)
        save_project_btn.pack(side=tk.LEFT, padx=3)

        load_project_btn = self._make_toolbar_button(top, text="Load project", command=self._on_load_project)
        load_project_btn.pack(side=tk.LEFT, padx=3)

        self._on_measure_mode_changed()

        ttk.Label(
            top,
            text="Shortcuts: Ctrl+O open, Ctrl+E export, Ctrl+Shift+V compute, Ctrl+Shift+N note",
            foreground=DARK_COLORS["fg_muted"],
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
        mask_results_btn_row = ttk.Frame(volume_panel)
        mask_results_btn_row.pack(fill=tk.X, pady=(4, 0))
        self.mask_results_popup_btn = ttk.Button(
            mask_results_btn_row,
            text="Mask results table...",
            command=self._on_show_mask_results_table,
            state="disabled",
        )
        self.mask_results_popup_btn.pack(side=tk.LEFT)
        self.mask_results_export_btn = ttk.Button(
            mask_results_btn_row,
            text="Export mask CSV...",
            command=self._on_export_mask_results_csv,
            state="disabled",
        )
        self.mask_results_export_btn.pack(side=tk.LEFT, padx=(4, 0))

        sections_panel = self._add_collapsible_panel(parent, "Sections and mask", expanded=False)
        ttk.Button(sections_panel, text="Update sections", command=self._on_update_sections).pack(fill=tk.X, pady=(0, 2))
        ttk.Label(sections_panel, text="Selected section:").pack(anchor="w", pady=(2, 0))
        self.section_combo = ttk.Combobox(
            sections_panel,
            textvariable=self.active_section_var,
            values=[],
            state="readonly",
            width=26,
        )
        self.section_combo.pack(fill=tk.X, pady=(2, 2))
        self.section_combo.bind("<<ComboboxSelected>>", self._on_section_select)
        section_btn_row = ttk.Frame(sections_panel)
        section_btn_row.pack(fill=tk.X, pady=(0, 2))
        self.section_zoom_btn = ttk.Button(section_btn_row, text="Zoom selected", command=self._on_zoom_selected_section)
        self.section_zoom_btn.pack(side=tk.LEFT)
        self.section_delete_btn = ttk.Button(section_btn_row, text="Delete selected", command=self._on_delete_selected_section)
        self.section_delete_btn.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(sections_panel, text="Clear sections", command=self._on_clear_sections).pack(fill=tk.X, pady=(0, 2))
        ttk.Button(sections_panel, text="Save sections...", command=self._on_save_sections_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Load sections...", command=self._on_load_sections_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Export section report...", command=self._on_export_section_report).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Open section report in browser...", command=self._on_open_section_report_browser).pack(fill=tk.X, pady=2)
        ttk.Separator(sections_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Label(sections_panel, text="Active mask:").pack(anchor="w")
        self.mask_combo = ttk.Combobox(
            sections_panel,
            textvariable=self.active_mask_var,
            values=[],
            state="readonly",
            width=26,
        )
        self.mask_combo.pack(fill=tk.X, pady=(2, 4))
        self.mask_combo.bind("<<ComboboxSelected>>", self._on_mask_select)
        ttk.Label(sections_panel, text="Compute mask scope:").pack(anchor="w", pady=(2, 0))
        self.mask_scope_combo = ttk.Combobox(
            sections_panel,
            textvariable=self.mask_compute_scope,
            values=list(MASK_SCOPE_LABELS.values()),
            state="readonly",
            width=26,
        )
        self.mask_scope_combo.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(sections_panel, text="Mask visibility:").pack(anchor="w", pady=(2, 0))
        self.mask_visibility_frame = ttk.Frame(sections_panel)
        self.mask_visibility_frame.pack(fill=tk.X, pady=(2, 2))
        vis_btn_row = ttk.Frame(sections_panel)
        vis_btn_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(vis_btn_row, text="Show all", command=lambda: self._set_all_masks_visible(True)).pack(side=tk.LEFT)
        ttk.Button(vis_btn_row, text="Hide all", command=lambda: self._set_all_masks_visible(False)).pack(side=tk.LEFT, padx=(4, 0))
        self.mask_edit_btn = ttk.Button(sections_panel, text="Edit selected mask", command=self._on_toggle_mask_edit)
        self.mask_edit_btn.pack(fill=tk.X, pady=2)
        self.mask_buffer_btn = ttk.Button(sections_panel, text="Buffer selected mask...", command=self._on_buffer_selected_mask)
        self.mask_buffer_btn.pack(fill=tk.X, pady=2)
        self.mask_delete_btn = ttk.Button(sections_panel, text="Delete selected mask", command=self._on_delete_selected_mask)
        self.mask_delete_btn.pack(fill=tk.X, pady=2)
        self.mask_clear_btn = ttk.Button(sections_panel, text="Clear all masks", command=self._on_clear_mask)
        self.mask_clear_btn.pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Save masks...", command=self._on_save_mask_json).pack(fill=tk.X, pady=2)
        ttk.Button(sections_panel, text="Load masks...", command=self._on_load_mask_json).pack(fill=tk.X, pady=2)
        ttk.Separator(sections_panel, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        self.measure_toggle_btn = ttk.Button(sections_panel, text="Start measure", command=self._on_toggle_measure)
        self.measure_toggle_btn.pack(fill=tk.X, pady=2)
        self.measure_clear_btn = ttk.Button(sections_panel, text="Clear measurements", command=self._on_clear_measurements)
        self.measure_clear_btn.pack(fill=tk.X, pady=2)

        schema_panel = self._add_collapsible_panel(parent, "LandXML schema", expanded=False)
        ttk.Checkbutton(schema_panel, text="Strict XSD", variable=self.strict_xsd_validation).pack(anchor="w")
        ttk.Checkbutton(schema_panel, text="Allow net schema", variable=self.xsd_allow_network).pack(anchor="w")
        ttk.Button(schema_panel, text="XSD file...", command=self._on_pick_local_xsd).pack(anchor="w", pady=(4, 0))

        self._on_intermediate_type_change()
        self._refresh_mask_controls()
        self._refresh_section_controls()

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
        self._note_pick_cid = self.canvas2d.mpl_connect("button_press_event", self._on_note_map_click)
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
        self.controls_canvas.configure(background=DARK_COLORS["bg_surface"])
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
        reset_for_clear(self)
        self._refresh_mask_controls()
        self._refresh_section_controls()
        self._refresh_mask_result_actions()
        self._refresh_file_selectors()
        self._refresh_layer_controls()
        self._redraw()

    def _clear_cached_mask_volume_results(self) -> None:
        clear_cached_mask_volume_results(self)

    def _refresh_mask_result_actions(self) -> None:
        has_results = bool(self._last_mask_volume_results)
        state = "normal" if has_results else "disabled"
        if hasattr(self, "mask_results_popup_btn"):
            self.mask_results_popup_btn.configure(state=state)
        if hasattr(self, "mask_results_export_btn"):
            self.mask_results_export_btn.configure(state=state)

    def _on_show_mask_results_table(self) -> None:
        if not self._last_mask_volume_results:
            messagebox.showinfo("KOF Viewer", "No per-mask volume results available. Run Compute volume first.")
            return
        MaskVolumeResultsWindow(self, self._last_mask_volume_results)

    def _on_export_mask_results_csv(self) -> None:
        rows = self._last_mask_volume_results
        if not rows:
            messagebox.showinfo("KOF Viewer", "No per-mask volume results available. Run Compute volume first.")
            return

        out_path = filedialog.asksaveasfilename(
            title="Export mask volume summary CSV",
            defaultextension=".csv",
            initialfile="mask_volume_summary.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not out_path:
            return

        fieldnames = [
            "mask_name",
            "area_m2",
            "net_m3",
            "cut_m3",
            "fill_m3",
            "top_to_intermediate_m3",
            "top_to_intermediate_cut_m3",
            "top_to_intermediate_fill_m3",
            "intermediate_to_bottom_m3",
            "intermediate_to_bottom_cut_m3",
            "intermediate_to_bottom_fill_m3",
            "status",
        ]
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            "mask_name": row.get("mask_name", ""),
                            "area_m2": row.get("area_m2", ""),
                            "net_m3": row.get("net_m3", ""),
                            "cut_m3": row.get("cut_m3", ""),
                            "fill_m3": row.get("fill_m3", ""),
                            "top_to_intermediate_m3": row.get("top_to_intermediate_m3", ""),
                            "top_to_intermediate_cut_m3": row.get("top_to_intermediate_cut_m3", ""),
                            "top_to_intermediate_fill_m3": row.get("top_to_intermediate_fill_m3", ""),
                            "intermediate_to_bottom_m3": row.get("intermediate_to_bottom_m3", ""),
                            "intermediate_to_bottom_cut_m3": row.get("intermediate_to_bottom_cut_m3", ""),
                            "intermediate_to_bottom_fill_m3": row.get("intermediate_to_bottom_fill_m3", ""),
                            "status": row.get("status", ""),
                        }
                    )
        except OSError as exc:
            messagebox.showerror("KOF Viewer", f"Failed to export CSV:\n{exc}")
            return

        messagebox.showinfo("KOF Viewer", f"Exported {len(rows)} row(s) to:\n{out_path}")

    @staticmethod
    def _mask_base_name(index: int) -> str:
        return f"Mask {KOFViewer._section_label(index)}"

    def _next_mask_name(self) -> str:
        used = {m.name for m in self._masks}
        idx = 0
        while True:
            candidate = self._mask_base_name(idx)
            if candidate not in used:
                return candidate
            idx += 1

    def _dedupe_mask_name(self, name: str) -> str:
        candidate = (name or "").strip() or self._next_mask_name()
        used = {m.name for m in self._masks}
        if candidate not in used:
            return candidate
        suffix = 2
        while f"{candidate} ({suffix})" in used:
            suffix += 1
        return f"{candidate} ({suffix})"

    def _set_active_mask_index(self, index: Optional[int]) -> None:
        if index is None or index < 0 or index >= len(self._masks):
            self._active_mask_index = None
            self.active_mask_var.set("")
            return
        self._active_mask_index = index
        self.active_mask_var.set(self._masks[index].name)

    def _active_mask(self) -> Optional[MaskDefinition]:
        idx = self._active_mask_index
        if idx is None or idx < 0 or idx >= len(self._masks):
            return None
        return self._masks[idx]

    def _mask_is_visible(self, index: int) -> bool:
        if index < 0 or index >= len(self._masks):
            return False
        if index >= len(self._mask_visibility_vars):
            return bool(self._masks[index].visible)
        return bool(self._mask_visibility_vars[index].get())

    def _sync_mask_visible_flag(self, index: int) -> None:
        if index < 0 or index >= len(self._masks):
            return
        self._masks[index].visible = self._mask_is_visible(index)

    def _on_toggle_mask_visibility(self, index: int) -> None:
        self._sync_mask_visible_flag(index)
        self._clear_cached_mask_volume_results()
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw_2d()

    def _set_all_masks_visible(self, visible: bool) -> None:
        if not self._masks:
            return
        for idx, var in enumerate(self._mask_visibility_vars):
            var.set(bool(visible))
            if idx < len(self._masks):
                self._masks[idx].visible = bool(visible)
        self._clear_cached_mask_volume_results()
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._redraw_2d()

    def _active_mask_polygon(self) -> Optional[list[tuple[float, float]]]:
        mask = self._active_mask()
        if mask is None or len(mask.vertices) < 3:
            return None
        return list(mask.vertices)

    def _append_mask(
        self,
        vertices: list[tuple[float, float]],
        name: Optional[str] = None,
        *,
        visible: bool = True,
    ) -> None:
        if len(vertices) < 3:
            return
        mask_name = self._dedupe_mask_name(name or self._next_mask_name())
        self._masks.append(MaskDefinition(name=mask_name, vertices=list(vertices), visible=bool(visible)))
        self._mask_visibility_vars.append(tk.BooleanVar(value=bool(visible)))
        self._clear_cached_mask_volume_results()
        self._set_active_mask_index(len(self._masks) - 1)

    def _refresh_mask_controls(self) -> None:
        if hasattr(self, "mask_combo"):
            names = [m.name for m in self._masks]
            self.mask_combo.configure(values=names)
            if names and self._active_mask_index is not None and 0 <= self._active_mask_index < len(names):
                self.active_mask_var.set(names[self._active_mask_index])
            else:
                self.active_mask_var.set("")
        while len(self._mask_visibility_vars) > len(self._masks):
            self._mask_visibility_vars.pop()
        while len(self._mask_visibility_vars) < len(self._masks):
            idx = len(self._mask_visibility_vars)
            self._mask_visibility_vars.append(tk.BooleanVar(value=bool(self._masks[idx].visible)))
        for idx, mask in enumerate(self._masks):
            self._mask_visibility_vars[idx].set(bool(mask.visible))
        if hasattr(self, "mask_visibility_frame"):
            for widget in getattr(self, "_mask_visibility_checks", []):
                widget.destroy()
            self._mask_visibility_checks = []
            for idx, mask in enumerate(self._masks):
                chk = ttk.Checkbutton(
                    self.mask_visibility_frame,
                    text=mask.name,
                    variable=self._mask_visibility_vars[idx],
                    command=lambda i=idx: self._on_toggle_mask_visibility(i),
                )
                chk.pack(anchor="w")
                self._mask_visibility_checks.append(chk)
        has_active = self._active_mask() is not None
        has_any = bool(self._masks)
        edit_state = "normal" if has_active else "disabled"
        any_state = "normal" if has_any else "disabled"
        if hasattr(self, "mask_edit_btn"):
            self.mask_edit_btn.configure(state=edit_state)
        if hasattr(self, "mask_buffer_btn"):
            self.mask_buffer_btn.configure(state=edit_state)
        if hasattr(self, "mask_delete_btn"):
            self.mask_delete_btn.configure(state=edit_state)
        if hasattr(self, "mask_clear_btn"):
            self.mask_clear_btn.configure(state=any_state)
        self._refresh_layer_controls()

    def _on_mask_select(self, _event=None) -> None:  # noqa: ANN001
        selected = self.active_mask_var.get().strip()
        for idx, mask in enumerate(self._masks):
            if mask.name == selected:
                self._set_active_mask_index(idx)
                self._redraw_2d()
                return

    def _on_save_mask_json(self) -> None:
        if not self._masks:
            messagebox.showinfo(
                "KOF Viewer",
                "No mask polygons to save. Draw at least one mask first.",
            )
            return

        default_name = "masks.json"
        out_path = filedialog.asksaveasfilename(
            title="Save masks",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out_path:
            return

        try:
            save_masks_json(
                out_path,
                self._masks,
                metadata={
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "mask_count": len(self._masks),
                },
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to save mask JSON:\n{exc}")
            return

        messagebox.showinfo(
            "KOF Viewer",
            f"Saved {len(self._masks)} mask(s) to:\n{out_path}",
        )

    def _on_load_mask_json(self) -> None:
        in_path = filedialog.askopenfilename(
            title="Load masks",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not in_path:
            return

        try:
            masks = load_masks_json(in_path)
        except (MaskDataError, OSError, ValueError) as exc:
            messagebox.showerror("KOF Viewer", f"Failed to load mask JSON:\n{exc}")
            return

        if not masks:
            messagebox.showinfo("KOF Viewer", "Mask file did not contain any valid closed polygons.")
            return

        replace = messagebox.askyesnocancel(
            "KOF Viewer",
            "Replace existing masks?\n\nYes: replace\nNo: append",
        )
        if replace is None:
            return

        if replace:
            self._masks.clear()
            self._mask_visibility_vars.clear()
            self._active_mask_index = None

        loaded_names: list[str] = []
        for mask in masks:
            name = self._dedupe_mask_name(mask.name)
            self._append_mask(mask.vertices, name=name, visible=mask.visible)
            loaded_names.append(name)

        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        if self._mask_edit_mode:
            self._end_mask_edit_mode()
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self.notebook.select(0)
        active_poly = self._active_mask_polygon()
        if active_poly is not None:
            self._zoom_to_bounds(self._polygon_bounds(active_poly))
        self._refresh_mask_controls()
        self._redraw()

        messagebox.showinfo(
            "KOF Viewer",
            f"Loaded {len(loaded_names)} mask(s). Active: {self.active_mask_var.get() or 'none'}",
        )

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

        self._close_section_windows()
        self._sections = list(sections)
        self._clear_active_section_marker(redraw=False)
        self._active_section_index = 0 if self._sections else None
        self._refresh_section_controls()
        self._redraw()

    def _on_save_project(self) -> None:
        out_path = filedialog.asksaveasfilename(
            title="Save project",
            defaultextension=".json",
            initialfile="kofvision_project.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not out_path:
            return

        payload = build_project_payload(self)

        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to save project:\n{exc}")
            return

        messagebox.showinfo("KOF Viewer", f"Project saved:\n{out_path}")

    def _on_load_project(self) -> None:
        in_path = filedialog.askopenfilename(
            title="Load project",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not in_path:
            return

        try:
            with open(in_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("KOF Viewer", f"Failed to read project file:\n{exc}")
            return

        try:
            project_payload = validate_project_payload(payload)
        except ProjectPayloadError as exc:
            messagebox.showerror("KOF Viewer", str(exc))
            return
        missing_sources = apply_project_payload(
            self,
            project_payload,
            allowed_volume_modes=set(VOLUME_LABEL_TO_MODE) | set(VOLUME_MODE_LABELS.values()),
            allowed_intermediate_types=set(INTERMEDIATE_LABEL_TO_TYPE) | set(INTERMEDIATE_TYPE_LABELS.values()),
            allowed_mask_scopes=set(MASK_LABEL_TO_SCOPE) | set(MASK_SCOPE_LABELS.values()),
        )

        msg = f"Project loaded:\n{in_path}"
        if missing_sources:
            msg += "\n\nMissing source files:\n" + "\n".join(missing_sources)
        messagebox.showinfo("KOF Viewer", msg)

    @staticmethod
    def _integrate_profile_areas_to_volume(stations_m: list[float], areas_m2: list[float]) -> float:
        if len(stations_m) < 2 or len(areas_m2) < 2:
            return float("nan")
        s = np.asarray(stations_m, dtype=float)
        a = np.asarray(areas_m2, dtype=float)
        valid = np.isfinite(s) & np.isfinite(a)
        if np.count_nonzero(valid) < 2:
            return float("nan")
        s_valid = s[valid]
        a_valid = a[valid]
        order = np.argsort(s_valid)
        return float(np.trapezoid(a_valid[order], s_valid[order]))

    @staticmethod
    def _cumulative_profile_volumes(stations_m: list[float], areas_m2: list[float]) -> list[float]:
        n = min(len(stations_m), len(areas_m2))
        if n == 0:
            return []

        s = np.asarray(stations_m[:n], dtype=float)
        a = np.asarray(areas_m2[:n], dtype=float)
        order = np.argsort(s)
        s_sorted = s[order]
        a_sorted = a[order]

        cumulative_sorted = np.full(n, np.nan, dtype=float)
        cumulative_sorted[0] = 0.0
        for i in range(1, n):
            s0 = s_sorted[i - 1]
            s1 = s_sorted[i]
            a0 = a_sorted[i - 1]
            a1 = a_sorted[i]
            prev = cumulative_sorted[i - 1]
            if np.isfinite(prev) and np.isfinite(s0) and np.isfinite(s1) and np.isfinite(a0) and np.isfinite(a1):
                dv = 0.5 * (a0 + a1) * (s1 - s0)
                cumulative_sorted[i] = prev + dv

        cumulative = np.full(n, np.nan, dtype=float)
        cumulative[order] = cumulative_sorted
        return [float(v) for v in cumulative]

    @staticmethod
    def _open_path_in_browser(path: str) -> bool:
        try:
            return bool(webbrowser.open_new_tab(Path(path).resolve().as_uri()))
        except Exception:  # noqa: BLE001
            return False

    def _ask_section_report_parameters(self) -> Optional[tuple[float, float, int]]:
        spacing_m = simpledialog.askfloat(
            "Section report",
            "Profile spacing along baseline (m):",
            initialvalue=1.0,
            minvalue=0.01,
        )
        if spacing_m is None:
            return None

        section_length_m = simpledialog.askfloat(
            "Section report",
            "Cross-section length (m):",
            initialvalue=20.0,
            minvalue=0.1,
        )
        if section_length_m is None:
            return None

        n_samples = simpledialog.askinteger(
            "Section report",
            "Samples per profile:",
            initialvalue=200,
            minvalue=25,
            maxvalue=2000,
        )
        if n_samples is None:
            return None
        return float(spacing_m), float(section_length_m), int(n_samples)

    def _generate_section_report(self, out_root: str, *, open_in_browser: bool) -> Optional[str]:
        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        if top is None or bot is None:
            messagebox.showwarning("KOF Viewer", "Select both top and bottom surfaces first.")
            return None
        if top is bot:
            messagebox.showwarning("KOF Viewer", "Top and bottom must be different files.")
            return None
        if not self._sections:
            messagebox.showinfo(
                "KOF Viewer",
                "Draw at least one section line first. The latest line is used as baseline.",
            )
            return None

        params = self._ask_section_report_parameters()
        if params is None:
            return None
        spacing_m, section_length_m, n_samples = params

        baseline = self._sections[-1]
        (b0, b1) = baseline
        baseline_len = float(np.hypot(b1[0] - b0[0], b1[1] - b0[1]))
        if baseline_len <= 0:
            messagebox.showerror("KOF Viewer", "Baseline section has zero length.")
            return None

        stations = station_values(baseline_len, spacing_m)
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
                    "area_upper_leg_m2": sample.area_upper_leg_m2,
                    "area_upper_leg_cut_m2": sample.area_upper_leg_cut_m2,
                    "area_upper_leg_fill_m2": sample.area_upper_leg_fill_m2,
                    "area_lower_leg_m2": sample.area_lower_leg_m2,
                    "area_lower_leg_cut_m2": sample.area_lower_leg_cut_m2,
                    "area_lower_leg_fill_m2": sample.area_lower_leg_fill_m2,
                    "image_rel": image_rel.replace("\\", "/"),
                }
            )
            plan_points.append((profile_id, center))

        stations_list = [float(r["station_m"]) for r in profile_rows]
        area_net_list = [float(r["area_net_m2"]) for r in profile_rows]
        area_cut_list = [float(r["area_cut_m2"]) for r in profile_rows]
        area_fill_list = [float(r["area_fill_m2"]) for r in profile_rows]
        net_volume_m3 = self._integrate_profile_areas_to_volume(stations_list, area_net_list)
        cut_volume_m3 = self._integrate_profile_areas_to_volume(stations_list, area_cut_list)
        fill_volume_m3 = self._integrate_profile_areas_to_volume(stations_list, area_fill_list)

        upper_leg_values = [r.get("area_upper_leg_m2") for r in profile_rows]
        lower_leg_values = [r.get("area_lower_leg_m2") for r in profile_rows]
        upper_leg_cut_values = [r.get("area_upper_leg_cut_m2") for r in profile_rows]
        upper_leg_fill_values = [r.get("area_upper_leg_fill_m2") for r in profile_rows]
        lower_leg_cut_values = [r.get("area_lower_leg_cut_m2") for r in profile_rows]
        lower_leg_fill_values = [r.get("area_lower_leg_fill_m2") for r in profile_rows]
        upper_leg_volume_m3 = float("nan")
        lower_leg_volume_m3 = float("nan")
        upper_leg_cut_volume_m3 = float("nan")
        upper_leg_fill_volume_m3 = float("nan")
        lower_leg_cut_volume_m3 = float("nan")
        lower_leg_fill_volume_m3 = float("nan")
        if any(v not in (None, "") for v in upper_leg_values):
            upper_leg_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in upper_leg_values],
            )
        if any(v not in (None, "") for v in lower_leg_values):
            lower_leg_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in lower_leg_values],
            )
        if any(v not in (None, "") for v in upper_leg_cut_values):
            upper_leg_cut_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in upper_leg_cut_values],
            )
        if any(v not in (None, "") for v in upper_leg_fill_values):
            upper_leg_fill_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in upper_leg_fill_values],
            )
        if any(v not in (None, "") for v in lower_leg_cut_values):
            lower_leg_cut_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in lower_leg_cut_values],
            )
        if any(v not in (None, "") for v in lower_leg_fill_values):
            lower_leg_fill_volume_m3 = self._integrate_profile_areas_to_volume(
                stations_list,
                [float(v) if v not in (None, "") else float("nan") for v in lower_leg_fill_values],
            )

        upper_leg_area_list = [float(v) if v not in (None, "") else float("nan") for v in upper_leg_values]
        lower_leg_area_list = [float(v) if v not in (None, "") else float("nan") for v in lower_leg_values]
        upper_leg_cut_area_list = [float(v) if v not in (None, "") else float("nan") for v in upper_leg_cut_values]
        upper_leg_fill_area_list = [float(v) if v not in (None, "") else float("nan") for v in upper_leg_fill_values]
        lower_leg_cut_area_list = [float(v) if v not in (None, "") else float("nan") for v in lower_leg_cut_values]
        lower_leg_fill_area_list = [float(v) if v not in (None, "") else float("nan") for v in lower_leg_fill_values]

        cumulative_net_m3 = self._cumulative_profile_volumes(stations_list, area_net_list)
        cumulative_cut_m3 = self._cumulative_profile_volumes(stations_list, area_cut_list)
        cumulative_fill_m3 = self._cumulative_profile_volumes(stations_list, area_fill_list)
        cumulative_upper_leg_m3 = self._cumulative_profile_volumes(stations_list, upper_leg_area_list)
        cumulative_lower_leg_m3 = self._cumulative_profile_volumes(stations_list, lower_leg_area_list)
        cumulative_upper_leg_cut_m3 = self._cumulative_profile_volumes(stations_list, upper_leg_cut_area_list)
        cumulative_upper_leg_fill_m3 = self._cumulative_profile_volumes(stations_list, upper_leg_fill_area_list)
        cumulative_lower_leg_cut_m3 = self._cumulative_profile_volumes(stations_list, lower_leg_cut_area_list)
        cumulative_lower_leg_fill_m3 = self._cumulative_profile_volumes(stations_list, lower_leg_fill_area_list)

        for i, row in enumerate(profile_rows):
            row["volume_net_m3"] = cumulative_net_m3[i] if i < len(cumulative_net_m3) else float("nan")
            row["volume_cut_m3"] = cumulative_cut_m3[i] if i < len(cumulative_cut_m3) else float("nan")
            row["volume_fill_m3"] = cumulative_fill_m3[i] if i < len(cumulative_fill_m3) else float("nan")
            row["volume_upper_leg_m3"] = cumulative_upper_leg_m3[i] if i < len(cumulative_upper_leg_m3) else float("nan")
            row["volume_upper_leg_cut_m3"] = cumulative_upper_leg_cut_m3[i] if i < len(cumulative_upper_leg_cut_m3) else float("nan")
            row["volume_upper_leg_fill_m3"] = cumulative_upper_leg_fill_m3[i] if i < len(cumulative_upper_leg_fill_m3) else float("nan")
            row["volume_lower_leg_m3"] = cumulative_lower_leg_m3[i] if i < len(cumulative_lower_leg_m3) else float("nan")
            row["volume_lower_leg_cut_m3"] = cumulative_lower_leg_cut_m3[i] if i < len(cumulative_lower_leg_cut_m3) else float("nan")
            row["volume_lower_leg_fill_m3"] = cumulative_lower_leg_fill_m3[i] if i < len(cumulative_lower_leg_fill_m3) else float("nan")

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
        mask_report_text = " | ".join(self._last_volume_per_mask_rows) if self._last_volume_per_mask_rows else "(not computed)"
        metadata = {
            "Top surface": top.name,
            "Bottom surface": bot.name,
            "Section area pair": f"{area_top_name} - {area_bottom_name}",
            "Input files": ", ".join(k.name for k in self.files),
            "Spacing (m)": f"{spacing_m:.2f}",
            "Cross-section length (m)": f"{section_length_m:.2f}",
            "Samples per profile": n_samples,
            "Profile count": len(profile_rows),
            "Baseline length (m)": f"{baseline_len:.2f}",
            "Section orientation": "Left -> Right (looking toward higher station)",
            "Volume net (m3)": f"{net_volume_m3:+,.2f}",
            "Volume cut (m3)": f"{cut_volume_m3:,.2f}",
            "Volume fill (m3)": f"{fill_volume_m3:,.2f}",
            "Latest volume per-mask": mask_report_text,
        }
        if np.isfinite(upper_leg_volume_m3):
            upper_name = section_intermediate.name if section_intermediate is not None else "Intermediate"
            metadata[f"Volume {top.name} - {upper_name} (m3)"] = f"{upper_leg_volume_m3:+,.2f}"
            if np.isfinite(upper_leg_cut_volume_m3):
                metadata[f"Volume {top.name} - {upper_name} cut (m3)"] = f"{upper_leg_cut_volume_m3:,.2f}"
            if np.isfinite(upper_leg_fill_volume_m3):
                metadata[f"Volume {top.name} - {upper_name} fill (m3)"] = f"{upper_leg_fill_volume_m3:,.2f}"
        if np.isfinite(lower_leg_volume_m3):
            lower_name = section_intermediate.name if section_intermediate is not None else "Intermediate"
            metadata[f"Volume {lower_name} - {bot.name} (m3)"] = f"{lower_leg_volume_m3:+,.2f}"
            if np.isfinite(lower_leg_cut_volume_m3):
                metadata[f"Volume {lower_name} - {bot.name} cut (m3)"] = f"{lower_leg_cut_volume_m3:,.2f}"
            if np.isfinite(lower_leg_fill_volume_m3):
                metadata[f"Volume {lower_name} - {bot.name} fill (m3)"] = f"{lower_leg_fill_volume_m3:,.2f}"

        write_html_report(
            report_path,
            title="KOF Section Report",
            metadata=metadata,
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
                    "latest_volume_per_mask": self._last_volume_per_mask_rows,
                    "input_files": [k.name for k in self.files],
                    "spacing_m": spacing_m,
                    "section_length_m": section_length_m,
                    "samples_per_profile": n_samples,
                    "baseline": {
                        "start": [float(b0[0]), float(b0[1])],
                        "end": [float(b1[0]), float(b1[1])],
                        "length_m": baseline_len,
                    },
                    "orientation": {
                        "section_axis": "left_to_right",
                        "view_direction": "toward_higher_station",
                    },
                    "volume_summary_m3": {
                        "net": net_volume_m3,
                        "cut": cut_volume_m3,
                        "fill": fill_volume_m3,
                        "top_to_intermediate": upper_leg_volume_m3,
                        "intermediate_to_bottom": lower_leg_volume_m3,
                    },
                    "profiles": profile_rows,
                },
                fh,
                indent=2,
            )

        self._latest_section_report_path = report_path
        if open_in_browser:
            opened = self._open_path_in_browser(report_path)
            if not opened:
                messagebox.showwarning(
                    "KOF Viewer",
                    "Report exported, but failed to open web browser automatically.",
                )

        return run_dir

    def _on_export_section_report(self) -> None:
        out_root = filedialog.askdirectory(title="Choose report output folder")
        if not out_root:
            return
        run_dir = self._generate_section_report(out_root, open_in_browser=True)
        if run_dir is None:
            return

        messagebox.showinfo(
            "KOF Viewer",
            "Section report export completed.\n\n"
            f"Output folder:\n{run_dir}\n\n"
            f"Report opened in browser: {'yes' if self._latest_section_report_path else 'no'}\n"
            "Baseline: latest drawn section line\n"
            "Orientation: left -> right looking toward higher station",
        )

    def _on_open_section_report_browser(self) -> None:
        out_root = tempfile.mkdtemp(prefix="kofvision_section_report_")
        run_dir = self._generate_section_report(out_root, open_in_browser=True)
        if run_dir is None:
            return
        messagebox.showinfo(
            "KOF Viewer",
            "Section report generated and opened in browser.\n\n"
            f"Temporary folder:\n{run_dir}\n\n"
            "Tip: use Export section report... to choose a permanent output folder.",
        )

    def _on_open_raster(self) -> None:
        self._raster_manager.on_open_raster()

    def _raster_display_name(self, idx: int) -> str:
        return self._raster_manager.raster_display_name(idx)

    def _refresh_raster_selector(self) -> None:
        self._raster_manager.refresh_raster_selector()

    def _set_active_raster_index(self, idx: Optional[int]) -> None:
        self._raster_manager.set_active_raster_index(idx)

    def _on_select_active_raster(self) -> None:
        self._raster_manager.on_select_active_raster()

    def _sync_alpha_from_active(self) -> None:
        self._raster_manager.sync_alpha_from_active()

    def _get_active_raster_layer(self) -> Optional[_RasterViewLayer]:
        return self._raster_manager.get_active_raster_layer()

    def _top_visible_raster_layer(self) -> Optional[_RasterViewLayer]:
        return self._raster_manager.top_visible_raster_layer()

    def _on_move_raster_up(self) -> None:
        self._raster_manager.on_move_raster_up()

    def _on_move_raster_down(self) -> None:
        self._raster_manager.on_move_raster_down()

    def _on_set_elevation_raster(self) -> None:
        self._raster_manager.on_set_elevation_raster()

    def _on_clear_raster(self) -> None:
        self._raster_manager.on_clear_raster()

    def _on_raster_alpha_change(self) -> None:
        self._raster_manager.on_raster_alpha_change()

    def _on_tin_fill_alpha_change(self) -> None:
        self._raster_manager.on_tin_fill_alpha_change()

    def _on_tin_fill_alpha_scale(self, _value: str) -> None:
        self._raster_manager.on_tin_fill_alpha_scale(_value)

    def _load_raster(self, path: str) -> None:
        self._raster_manager.load_raster(path)

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

    def _zoom_to_point(self, y: float, x: float) -> None:
        bounds = self._selected_layers_2d_bounds()
        if bounds is None:
            pad = 10.0
        else:
            xmin, xmax, ymin, ymax = bounds
            span = max(xmax - xmin, ymax - ymin)
            pad = max(3.0, span * 0.05)
        self._zoom_to_bounds((y - pad, y + pad, x - pad, x + pad))

    def _on_layer_mask_item_click(self, index: int) -> None:
        if index < 0 or index >= len(self._masks):
            return
        self._set_active_mask_index(index)
        poly = list(self._masks[index].vertices)
        if len(poly) >= 3:
            self._zoom_to_bounds(self._polygon_bounds(poly))
        self.notebook.select(0)
        self._refresh_mask_controls()
        self._redraw_2d()

    def _on_layer_note_item_click(self, index: int) -> None:
        if index < 0 or index >= len(self._notes):
            return
        self._active_note_index = index
        note = self._notes[index]
        self._zoom_to_point(note.y, note.x)
        self.notebook.select(0)
        self._refresh_layer_controls()
        self._redraw_2d()

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
        self._close_section_windows()
        self._sections.clear()
        self._clear_active_section_marker(redraw=False)
        self._active_section_index = None
        self._refresh_section_controls()
        self._redraw()

    def _clear_active_section_marker(self, *, redraw: bool = True) -> None:
        self._active_section_marker_index = None
        self._active_section_marker_chainage_m = None
        if redraw:
            self._redraw_2d()

    def _set_active_section_marker(self, section_index: int, chainage_m: float) -> None:
        if section_index < 0 or section_index >= len(self._sections):
            self._clear_active_section_marker(redraw=True)
            return

        (y0, x0), (y1, x1) = self._sections[section_index]
        length = float(np.hypot(y1 - y0, x1 - x0))
        if length <= 0:
            self._clear_active_section_marker(redraw=True)
            return

        chainage = min(max(float(chainage_m), 0.0), length)
        self._active_section_index = section_index
        self._refresh_section_controls()
        self._active_section_marker_index = section_index
        self._active_section_marker_chainage_m = chainage
        self._redraw_2d()

    def _on_section_window_chainage(self, section_index: int, chainage_m: float) -> None:
        self._set_active_section_marker(section_index, chainage_m)

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
                    self._surface_source_paths.append(path)
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
                self._surface_source_paths.append(path)
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
        self._raster_manager.refresh_intermediate_raster_selector()

    def _refresh_layer_controls(self) -> None:
        for child in self.layers_inner.winfo_children():
            child.destroy()

        has_raster_layer = bool(self._raster_layers)

        if not self.files and not has_raster_layer and not self.landxml_files and not self._masks and not self._notes:
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

        if self._masks:
            ttk.Separator(self.layers_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(2, 4))
            ttk.Label(self.layers_inner, text="Masks").pack(anchor="w")
            while len(self._mask_visibility_vars) < len(self._masks):
                idx = len(self._mask_visibility_vars)
                self._mask_visibility_vars.append(tk.BooleanVar(value=bool(self._masks[idx].visible)))
            for idx, mask in enumerate(self._masks):
                self._mask_visibility_vars[idx].set(bool(mask.visible))
                row = ttk.Frame(self.layers_inner)
                row.pack(fill=tk.X, pady=1)
                ttk.Checkbutton(
                    row,
                    variable=self._mask_visibility_vars[idx],
                    command=lambda i=idx: self._on_toggle_mask_visibility(i),
                ).pack(side=tk.LEFT)
                is_active = idx == self._active_mask_index
                mask_label = ttk.Label(
                    row,
                    text=mask.name,
                    foreground="#204080" if is_active else "",
                    font=("TkDefaultFont", 9, "bold" if is_active else "normal"),
                    cursor="hand2",
                )
                mask_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                mask_label.bind("<Button-1>", lambda _e, i=idx: self._on_layer_mask_item_click(i))

        if self._notes:
            ttk.Separator(self.layers_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(2, 4))
            ttk.Label(self.layers_inner, text="Notes").pack(anchor="w")
            while len(self._note_visibility_vars) > len(self._notes):
                self._note_visibility_vars.pop()
            while len(self._note_visibility_vars) < len(self._notes):
                idx = len(self._note_visibility_vars)
                self._note_visibility_vars.append(tk.BooleanVar(value=bool(self._notes[idx].visible)))
            for idx, note in enumerate(self._notes):
                self._note_visibility_vars[idx].set(bool(note.visible))
                label = note.title if note.title else self._note_default_title(idx)
                row = ttk.Frame(self.layers_inner)
                row.pack(fill=tk.X, pady=1)
                ttk.Checkbutton(
                    row,
                    variable=self._note_visibility_vars[idx],
                    command=lambda i=idx: self._on_toggle_note_visibility(i),
                ).pack(side=tk.LEFT)
                is_active = idx == self._active_note_index
                note_label = ttk.Label(
                    row,
                    text=label,
                    foreground="#204080" if is_active else "",
                    font=("TkDefaultFont", 9, "bold" if is_active else "normal"),
                    cursor="hand2",
                )
                note_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                note_label.bind("<Button-1>", lambda _e, i=idx: self._on_layer_note_item_click(i))

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
        # Keep section raster previews aligned with raster-based sampling mode,
        # including KOF-KOF where the raster is shown as reference only.
        if self.volume_method.get().strip().lower() != "raster":
            return None
        return layer.elevation_surface

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

    def _current_mask_scope_key(self) -> str:
        label = self.mask_compute_scope.get().strip()
        return MASK_LABEL_TO_SCOPE.get(label, "selected")

    def _mask_indices_for_compute(self, scope: str) -> list[int]:
        if not self._masks:
            return []
        if scope == "selected":
            idx = self._active_mask_index
            if idx is None or idx < 0 or idx >= len(self._masks):
                return []
            return [idx]
        if scope == "visible_union":
            return [idx for idx in range(len(self._masks)) if self._mask_is_visible(idx)]
        if scope == "all_union":
            return list(range(len(self._masks)))
        return []

    @staticmethod
    def _polygon_from_geom(geom) -> list[list[tuple[float, float]]]:  # noqa: ANN001
        if geom.is_empty:
            return []
        out: list[list[tuple[float, float]]] = []
        if geom.geom_type == "Polygon":
            out.append([(float(y), float(x)) for y, x in geom.exterior.coords[:-1]])
            return out
        if geom.geom_type == "MultiPolygon":
            for part in geom.geoms:
                out.extend(KOFViewer._polygon_from_geom(part))
            return out
        return []

    @staticmethod
    def _combine_volume_results(
        results: list[VolumeResult | SplitVolumeResult],
    ) -> Optional[VolumeResult | SplitVolumeResult]:
        if not results:
            return None

        def _combine_simple(items: list[VolumeResult]) -> VolumeResult:
            first = items[0]
            return VolumeResult(
                top_name=first.top_name,
                bottom_name=first.bottom_name,
                cell_size=first.cell_size,
                n_valid=int(sum(it.n_valid for it in items)),
                net=float(sum(it.net for it in items)),
                cut=float(sum(it.cut for it in items)),
                fill=float(sum(it.fill for it in items)),
                area=float(sum(it.area for it in items)),
                y_min=min(it.y_min for it in items),
                x_min=min(it.x_min for it in items),
                y_max=max(it.y_max for it in items),
                x_max=max(it.x_max for it in items),
            )

        if isinstance(results[0], SplitVolumeResult):
            split_items = [r for r in results if isinstance(r, SplitVolumeResult)]
            if len(split_items) != len(results):
                return None
            upper = _combine_simple([it.upper for it in split_items])
            lower = _combine_simple([it.lower for it in split_items])
            return SplitVolumeResult(upper=upper, lower=lower)

        simple_items = [r for r in results if isinstance(r, VolumeResult)]
        if len(simple_items) != len(results):
            return None
        return _combine_simple(simple_items)

    def _compute_volume_for_mask(
        self,
        top_surface: SurfaceLike,
        bottom_surface: SurfaceLike,
        intermediate_surface: Optional[SurfaceLike],
        *,
        mask_polygon: Optional[list[tuple[float, float]]],
        method: str,
    ) -> Optional[VolumeResult | SplitVolumeResult]:
        if intermediate_surface is None:
            return compute_volume(top_surface, bottom_surface, mask_polygon=mask_polygon, method=method)
        return compute_volume_with_intermediate(
            top_surface,
            intermediate_surface,
            bottom_surface,
            mask_polygon=mask_polygon,
            method=method,
        )

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

        method = self.volume_method.get().strip().lower()
        scope = self._current_mask_scope_key()
        scope_label = MASK_SCOPE_LABELS.get(scope, MASK_SCOPE_LABELS["selected"])

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

        mask_indices = self._mask_indices_for_compute(scope)
        per_mask_rows: list[str] = []
        per_mask_result_rows: list[dict[str, Any]] = []
        effective_method = method

        union_polygons: list[list[tuple[float, float]]] = []
        if scope == "selected":
            union_polygons = [self._active_mask_polygon()] if self._active_mask_polygon() is not None else []
        elif mask_indices:
            try:
                from shapely.geometry import Polygon
                from shapely.ops import unary_union
            except Exception:  # noqa: BLE001
                messagebox.showerror(
                    "KOF Viewer",
                    "Union mask scope requires shapely. Install it with:\n  pip install shapely",
                )
                return

            geoms = []
            for idx in mask_indices:
                vertices = self._masks[idx].vertices
                if len(vertices) < 3:
                    continue
                poly = Polygon(vertices)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty:
                    geoms.append(poly)
            if geoms:
                union_geom = unary_union(geoms)
                union_polygons = self._polygon_from_geom(union_geom)
            if method == "triangles" and union_polygons:
                effective_method = "raster"
                messagebox.showwarning(
                    "KOF Viewer",
                    "Mask union currently runs with raster method; switched from triangles for this computation.",
                )

        mask_compute_polygons = [p for p in union_polygons if p is not None and len(p) >= 3]
        if scope != "selected" and mask_indices and not mask_compute_polygons:
            messagebox.showerror(
                "KOF Viewer",
                "Could not build a valid union polygon from selected masks.",
            )
            return

        try:
            if not mask_compute_polygons:
                result = self._compute_volume_for_mask(
                    top_surface,
                    bottom_surface,
                    intermediate_surface,
                    mask_polygon=None,
                    method=effective_method,
                )
            else:
                partial_results: list[VolumeResult | SplitVolumeResult] = []
                for poly in mask_compute_polygons:
                    partial = self._compute_volume_for_mask(
                        top_surface,
                        bottom_surface,
                        intermediate_surface,
                        mask_polygon=poly,
                        method=effective_method,
                    )
                    if partial is not None:
                        partial_results.append(partial)
                result = self._combine_volume_results(partial_results)

            if mask_indices:
                for idx in mask_indices:
                    mask = self._masks[idx]
                    mask_result = self._compute_volume_for_mask(
                        top_surface,
                        bottom_surface,
                        intermediate_surface,
                        mask_polygon=list(mask.vertices),
                        method=effective_method,
                    )
                    if mask_result is None:
                        per_mask_rows.append(f"- {mask.name}: no overlap")
                        per_mask_result_rows.append(
                            {
                                "mask_name": mask.name,
                                "area_m2": "",
                                "net_m3": "",
                                "cut_m3": "",
                                "fill_m3": "",
                                "top_to_intermediate_m3": "",
                                "top_to_intermediate_cut_m3": "",
                                "top_to_intermediate_fill_m3": "",
                                "intermediate_to_bottom_m3": "",
                                "intermediate_to_bottom_cut_m3": "",
                                "intermediate_to_bottom_fill_m3": "",
                                "status": "no overlap",
                            }
                        )
                        continue
                    if isinstance(mask_result, SplitVolumeResult):
                        area_m2 = float(mask_result.upper.area)
                        net_m3 = float(mask_result.net)
                        cut_m3 = float(mask_result.cut)
                        fill_m3 = float(mask_result.fill)
                        upper_net_m3 = float(mask_result.upper.net)
                        upper_cut_m3 = float(mask_result.upper.cut)
                        upper_fill_m3 = float(mask_result.upper.fill)
                        lower_net_m3 = float(mask_result.lower.net)
                        lower_cut_m3 = float(mask_result.lower.cut)
                        lower_fill_m3 = float(mask_result.lower.fill)
                        per_mask_rows.append(
                            f"- {mask.name}: area {area_m2:,.1f} m2 | "
                            f"net {net_m3:+,.2f} m3 | "
                            f"top-intermediate {upper_net_m3:+,.2f} | "
                            f"intermediate-bottom {lower_net_m3:+,.2f}"
                        )
                        per_mask_result_rows.append(
                            {
                                "mask_name": mask.name,
                                "area_m2": f"{area_m2:.6f}",
                                "net_m3": f"{net_m3:.6f}",
                                "cut_m3": f"{cut_m3:.6f}",
                                "fill_m3": f"{fill_m3:.6f}",
                                "top_to_intermediate_m3": f"{upper_net_m3:.6f}",
                                "top_to_intermediate_cut_m3": f"{upper_cut_m3:.6f}",
                                "top_to_intermediate_fill_m3": f"{upper_fill_m3:.6f}",
                                "intermediate_to_bottom_m3": f"{lower_net_m3:.6f}",
                                "intermediate_to_bottom_cut_m3": f"{lower_cut_m3:.6f}",
                                "intermediate_to_bottom_fill_m3": f"{lower_fill_m3:.6f}",
                                "status": "ok",
                            }
                        )
                    else:
                        area_m2 = float(mask_result.area)
                        net_m3 = float(mask_result.net)
                        cut_m3 = float(mask_result.cut)
                        fill_m3 = float(mask_result.fill)
                        per_mask_rows.append(
                            f"- {mask.name}: area {area_m2:,.1f} m2 | "
                            f"net {net_m3:+,.2f} m3 | cut {cut_m3:,.2f} | fill {fill_m3:,.2f}"
                        )
                        per_mask_result_rows.append(
                            {
                                "mask_name": mask.name,
                                "area_m2": f"{area_m2:.6f}",
                                "net_m3": f"{net_m3:.6f}",
                                "cut_m3": f"{cut_m3:.6f}",
                                "fill_m3": f"{fill_m3:.6f}",
                                "top_to_intermediate_m3": "",
                                "top_to_intermediate_cut_m3": "",
                                "top_to_intermediate_fill_m3": "",
                                "intermediate_to_bottom_m3": "",
                                "intermediate_to_bottom_cut_m3": "",
                                "intermediate_to_bottom_fill_m3": "",
                                "status": "ok",
                            }
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
        self._last_volume_per_mask_rows = list(per_mask_rows)
        if not per_mask_result_rows:
            if isinstance(result, SplitVolumeResult):
                per_mask_result_rows.append(
                    {
                        "mask_name": "(no mask)",
                        "area_m2": f"{float(result.upper.area):.6f}",
                        "net_m3": f"{float(result.net):.6f}",
                        "cut_m3": f"{float(result.cut):.6f}",
                        "fill_m3": f"{float(result.fill):.6f}",
                        "top_to_intermediate_m3": f"{float(result.upper.net):.6f}",
                        "top_to_intermediate_cut_m3": f"{float(result.upper.cut):.6f}",
                        "top_to_intermediate_fill_m3": f"{float(result.upper.fill):.6f}",
                        "intermediate_to_bottom_m3": f"{float(result.lower.net):.6f}",
                        "intermediate_to_bottom_cut_m3": f"{float(result.lower.cut):.6f}",
                        "intermediate_to_bottom_fill_m3": f"{float(result.lower.fill):.6f}",
                        "status": "overall",
                    }
                )
            else:
                per_mask_result_rows.append(
                    {
                        "mask_name": "(no mask)",
                        "area_m2": f"{float(result.area):.6f}",
                        "net_m3": f"{float(result.net):.6f}",
                        "cut_m3": f"{float(result.cut):.6f}",
                        "fill_m3": f"{float(result.fill):.6f}",
                        "top_to_intermediate_m3": "",
                        "top_to_intermediate_cut_m3": "",
                        "top_to_intermediate_fill_m3": "",
                        "intermediate_to_bottom_m3": "",
                        "intermediate_to_bottom_cut_m3": "",
                        "intermediate_to_bottom_fill_m3": "",
                        "status": "overall",
                    }
                )
        self._last_mask_volume_results = list(per_mask_result_rows)
        self._refresh_mask_result_actions()
        mask_suffix = ""
        if scope == "selected":
            active = self._active_mask()
            if active is not None:
                mask_suffix = f" (mask: {active.name})"
        elif mask_indices:
            mask_suffix = f" (scope: {scope_label}, masks: {len(mask_indices)})"
        if isinstance(result, SplitVolumeResult):
            upper = result.upper
            lower = result.lower
            summary = (
                f"{mode}/{effective_method} split: {upper.top_name}-{upper.bottom_name} "
                f"Net {upper.net:+,.2f} m³; {lower.top_name}-{lower.bottom_name} "
                f"Net {lower.net:+,.2f} m³; Total {result.net:+,.2f} m³{mask_suffix}"
            )
        else:
            summary = (
                f"{mode}/{effective_method}: Net {result.net:+,.2f} m³   (Cut {result.cut:,.2f} / "
                f"Fill {result.fill:,.2f})   over {result.area:,.1f} m²{mask_suffix}"
            )
        if per_mask_rows:
            summary = summary + "\n" + "\n".join(per_mask_rows)
        self.volume_text.set(summary)
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

    def _cleanup_section_windows(self) -> None:
        self._section_windows = [w for w in self._section_windows if w.winfo_exists()]

    def _close_section_windows(self) -> None:
        self._cleanup_section_windows()
        for win in self._section_windows:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self._section_windows.clear()

    def _open_section_window_at_index(
        self,
        index: int,
        *,
        top: SurfaceLike,
        bottom: SurfaceLike,
    ) -> None:
        if index < 0 or index >= len(self._sections):
            return
        (y0, x0), (y1, x1) = self._sections[index]
        section_intermediate = self._section_intermediate_surface()
        area_top_surface, area_bottom_surface, area_top_name, area_bottom_name = self._section_area_pair(top, bottom)
        window = SectionWindow(
            self,
            top,
            bottom,
            (y0, x0),
            (y1, x1),
            raster_surface=self._section_reference_raster(),
            intermediate_surface=section_intermediate,
            area_top_surface=area_top_surface,
            area_bottom_surface=area_bottom_surface,
            area_top_name=area_top_name,
            area_bottom_name=area_bottom_name,
            label=self._section_label(index),
            section_index=index,
            on_chainage_change=self._on_section_window_chainage,
        )
        self._section_windows.append(window)

    def _on_update_sections(self) -> None:
        if not self._sections:
            messagebox.showinfo("KOF Viewer", "No section lines available to update.")
            return

        top = self._find_file(self.top_var.get())
        bot = self._find_file(self.bottom_var.get())
        if top is None or bot is None:
            messagebox.showwarning("KOF Viewer", "Select both top and bottom surfaces first.")
            return
        if top is bot:
            messagebox.showwarning("KOF Viewer", "Top and bottom must be different files.")
            return

        self._close_section_windows()
        for idx in range(len(self._sections)):
            self._open_section_window_at_index(idx, top=top, bottom=bot)

        messagebox.showinfo(
            "KOF Viewer",
            f"Updated {len(self._sections)} section window(s) with current settings.",
        )

    # --------------------------------------------------------- Section tool
    def _on_toggle_section(self) -> None:
        SectionTool.toggle(self)

    def _end_section_mode(self) -> None:
        SectionTool.end(self)

    def _on_section_click(self, event) -> None:  # noqa: ANN001
        SectionTool.on_click(self, event)

    # -------------------------------------------------------------- Map notes
    def _on_toggle_note_mode(self) -> None:
        NoteTool.toggle(self)

    def _cancel_note_mode(self) -> None:
        NoteTool.cancel(self)

    def _end_note_mode(self) -> None:
        NoteTool.end(self)

    def _note_pick_tolerance(self) -> float:
        return NoteTool.pick_tolerance(self)

    def _pick_note_index(self, y: float, x: float) -> Optional[int]:
        return NoteTool.pick_index(self, y, x)

    @staticmethod
    def _note_default_title(index: int) -> str:
        return NoteTool.default_title(index)

    def _append_note(self, title: str, text: str, y: float, x: float, visible: bool = True) -> None:
        clean_title = title.strip()
        clean_text = text.strip()
        if not clean_title and not clean_text:
            return
        if not clean_title:
            clean_title = self._note_default_title(len(self._notes))
        self._notes.append(NoteDefinition(title=clean_title, text=clean_text, y=float(y), x=float(x), visible=visible))
        self._note_visibility_vars.append(tk.BooleanVar(value=bool(visible)))
        self._active_note_index = len(self._notes) - 1
        self._refresh_layer_controls()

    def _delete_note_at_index(self, idx: int, *, ask_confirm: bool = True) -> bool:
        if idx < 0 or idx >= len(self._notes):
            return False
        note = self._notes[idx]
        if ask_confirm:
            if not messagebox.askyesno("Delete note", f"Delete note '{note.title}'?"):
                return False
        del self._notes[idx]
        if idx < len(self._note_visibility_vars):
            del self._note_visibility_vars[idx]
        if not self._notes:
            self._active_note_index = None
        elif self._active_note_index is None:
            self._active_note_index = min(idx, len(self._notes) - 1)
        elif self._active_note_index == idx:
            self._active_note_index = min(idx, len(self._notes) - 1)
        elif self._active_note_index > idx:
            self._active_note_index -= 1
        self._refresh_layer_controls()
        return True

    def _note_is_visible(self, index: int) -> bool:
        if index < 0 or index >= len(self._notes):
            return False
        if index >= len(self._note_visibility_vars):
            return bool(self._notes[index].visible)
        return bool(self._note_visibility_vars[index].get())

    def _sync_note_visible_flag(self, index: int) -> None:
        if index < 0 or index >= len(self._notes):
            return
        self._notes[index].visible = self._note_is_visible(index)

    def _on_toggle_note_visibility(self, index: int) -> None:
        self._sync_note_visible_flag(index)
        self._redraw_2d()

    def _cancel_pending_note_single_click(self) -> None:
        after_id = self._pending_note_click_after_id
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self._pending_note_click_after_id = None
        self._pending_note_click_index = None

    def _run_pending_note_single_click(self) -> None:
        idx = self._pending_note_click_index
        self._pending_note_click_after_id = None
        self._pending_note_click_index = None
        if idx is None:
            return
        self._show_note_at_index(idx)

    def _edit_note_dialog(
        self,
        *,
        title_value: str,
        text_value: str,
        allow_delete: bool,
    ) -> tuple[str, str, str]:
        return NoteTool.edit_dialog(
            self,
            title_value=title_value,
            text_value=text_value,
            allow_delete=allow_delete,
        )

    def _on_note_place_click(self, event) -> None:  # noqa: ANN001
        NoteTool.on_place_click(self, event)

    def _show_note_at_index(self, idx: int) -> None:
        NoteTool.show_at_index(self, idx)

    def _edit_note_at_index(self, idx: int) -> None:
        NoteTool.edit_at_index(self, idx)

    def _on_note_map_click(self, event) -> None:  # noqa: ANN001
        NoteTool.on_map_click(self, event)

    # ------------------------------------------------------------ Measure tool
    def _current_measure_mode_key(self) -> str:
        raw = self.measure_mode.get().strip()
        if raw in MEASURE_LABEL_TO_MODE:
            return MEASURE_LABEL_TO_MODE[raw]
        key = raw.lower()
        if key in MEASURE_MODE_LABELS:
            return key
        return "line"

    def _on_measure_mode_changed(self) -> None:
        mode = self._current_measure_mode_key()
        mode_label = MEASURE_MODE_LABELS.get(mode, MEASURE_MODE_LABELS["line"])
        if hasattr(self, "measure_mode_btn"):
            self.measure_mode_btn.configure(text=f"Measure mode: {mode_label}")
        if not self._measure_mode:
            return
        self._measure_click_points = []
        if mode == "polygon":
            self.status.set(
                "Measure polygon: left-click vertices, then right-click or double-click to close. Esc to cancel."
            )
        else:
            self.status.set(
                "Measure line: click two points on the 2D plot. Right-click or Esc to cancel."
            )
        self._redraw_2d()

    @staticmethod
    def _measure_line_length(p0: tuple[float, float], p1: tuple[float, float]) -> float:
        return float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))

    @staticmethod
    def _measure_polygon_perimeter(points: list[tuple[float, float]]) -> float:
        if len(points) < 2:
            return 0.0
        arr = np.asarray(points, dtype=float)
        closed = np.vstack([arr, arr[0]])
        seg = closed[1:] - closed[:-1]
        return float(np.hypot(seg[:, 0], seg[:, 1]).sum())

    @staticmethod
    def _measure_polygon_area(points: list[tuple[float, float]]) -> float:
        if len(points) < 3:
            return 0.0
        arr = np.asarray(points, dtype=float)
        ys = arr[:, 0]
        xs = arr[:, 1]
        return float(abs(np.dot(ys, np.roll(xs, -1)) - np.dot(xs, np.roll(ys, -1))) * 0.5)

    @staticmethod
    def _measurement_label(index: int) -> str:
        return f"Meas {index + 1}"

    def _on_toggle_measure(self) -> None:
        MeasureTool.toggle(self)

    def _cancel_measure_mode(self) -> None:
        MeasureTool.cancel(self)

    def _end_measure_mode(self) -> None:
        MeasureTool.end(self)

    def _finalize_line_measure(self, points: list[tuple[float, float]]) -> None:
        if len(points) != 2:
            return
        length_m = self._measure_line_length(points[0], points[1])
        self._measure_results.append(
            {
                "kind": "line",
                "points": [points[0], points[1]],
                "length_m": length_m,
            }
        )
        label = self._measurement_label(len(self._measure_results) - 1)
        self._end_measure_mode()
        self._redraw_2d()
        self.status.set(f"{label}: distance {length_m:,.2f} m")

    def _finalize_polygon_measure(self, points: list[tuple[float, float]]) -> None:
        if len(points) < 3:
            return
        area_m2 = self._measure_polygon_area(points)
        perimeter_m = self._measure_polygon_perimeter(points)
        self._measure_results.append(
            {
                "kind": "polygon",
                "points": list(points),
                "area_m2": area_m2,
                "perimeter_m": perimeter_m,
            }
        )
        label = self._measurement_label(len(self._measure_results) - 1)
        self._end_measure_mode()
        self._redraw_2d()
        self.status.set(f"{label}: area {area_m2:,.2f} m2, perimeter {perimeter_m:,.2f} m")

    def _on_measure_click(self, event) -> None:  # noqa: ANN001
        MeasureTool.on_click(self, event)

    def _on_clear_measurements(self) -> None:
        had_data = bool(self._measure_results)
        self._measure_results.clear()
        self._measure_click_points.clear()
        if self._measure_mode:
            self._end_measure_mode()
        if had_data:
            self._redraw_2d()
        else:
            self._update_status()

    # ----------------------------------------------------------- Polygon mask
    def _on_toggle_mask(self) -> None:
        MaskTool.toggle(self)

    def _cancel_mask_mode(self) -> None:
        MaskTool.cancel(self)

    def _end_mask_mode(self) -> None:
        MaskTool.end(self)

    def _on_clear_mask(self) -> None:
        had_mask = bool(self._masks)
        self._masks.clear()
        self._mask_visibility_vars.clear()
        self._clear_cached_mask_volume_results()
        self._active_mask_index = None
        self.active_mask_var.set("")
        self._mask_click_points.clear()
        if self._mask_mode:
            self._end_mask_mode()
        if self._mask_edit_mode:
            self._end_mask_edit_mode()
        if had_mask and self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._refresh_mask_controls()
        self._redraw()

    def _on_mask_click(self, event) -> None:  # noqa: ANN001
        MaskTool.on_click(self, event)

    def _mask_pick_tolerance(self) -> float:
        xmin, xmax = self.ax2d.get_xlim()
        ymin, ymax = self.ax2d.get_ylim()
        span = max(abs(xmax - xmin), abs(ymax - ymin), 1e-9)
        return span * 0.015

    def _pick_vertex_index(
        self,
        vertices: list[tuple[float, float]],
        point: tuple[float, float],
    ) -> Optional[int]:
        if not vertices:
            return None
        py, px = point
        arr = np.asarray(vertices, dtype=float)
        d2 = (arr[:, 0] - py) ** 2 + (arr[:, 1] - px) ** 2
        idx = int(np.argmin(d2))
        tol = self._mask_pick_tolerance()
        if float(d2[idx]) <= tol * tol:
            return idx
        return None

    def _on_toggle_mask_edit(self) -> None:
        MaskEditTool.toggle(self)

    def _end_mask_edit_mode(self) -> None:
        MaskEditTool.end(self)

    def _on_mask_edit_press(self, event) -> None:  # noqa: ANN001
        MaskEditTool.on_press(self, event)

    def _on_mask_edit_motion(self, event) -> None:  # noqa: ANN001
        MaskEditTool.on_motion(self, event)

    def _on_mask_edit_release(self, event) -> None:  # noqa: ANN001
        MaskEditTool.on_release(self, event)

    def _on_delete_selected_mask(self) -> None:
        idx = self._active_mask_index
        if idx is None or idx < 0 or idx >= len(self._masks):
            return
        if self._mask_mode:
            self._end_mask_mode()
        del self._masks[idx]
        if idx < len(self._mask_visibility_vars):
            del self._mask_visibility_vars[idx]
        self._clear_cached_mask_volume_results()
        if self._mask_edit_mode:
            self._end_mask_edit_mode()
        if not self._masks:
            self._set_active_mask_index(None)
        else:
            self._set_active_mask_index(min(idx, len(self._masks) - 1))
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._refresh_mask_controls()
        self._redraw()

    def _on_buffer_selected_mask(self) -> None:
        active = self._active_mask()
        if active is None or len(active.vertices) < 3:
            messagebox.showinfo("KOF Viewer", "Select a closed mask first.")
            return

        distance = simpledialog.askfloat(
            "Buffer mask",
            "Buffer distance in meters (positive expands, negative shrinks):",
            initialvalue=1.0,
        )
        if distance is None:
            return
        if not np.isfinite(distance) or abs(distance) < 1e-12:
            messagebox.showwarning("KOF Viewer", "Enter a non-zero finite buffer distance.")
            return

        try:
            from shapely.geometry import Polygon
        except Exception:  # noqa: BLE001
            messagebox.showerror(
                "KOF Viewer",
                "Mask buffering requires shapely. Install it with:\n  pip install shapely",
            )
            return

        polygon = Polygon(active.vertices)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            messagebox.showerror("KOF Viewer", "Selected mask polygon is invalid and could not be fixed.")
            return

        buffered = polygon.buffer(float(distance))
        if buffered.is_empty:
            messagebox.showerror("KOF Viewer", "Buffer distance removed the mask entirely.")
            return

        if buffered.geom_type == "MultiPolygon":
            buffered = max(buffered.geoms, key=lambda g: g.area)

        coords = [(float(y), float(x)) for y, x in buffered.exterior.coords[:-1]]
        if len(coords) < 3:
            messagebox.showerror("KOF Viewer", "Buffered mask is not a valid polygon.")
            return

        self._append_mask(coords, name=f"{active.name} (buffer {distance:+.2f} m)")
        if self.last_volume is not None:
            self.volume_text.set("Volume: (stale – recompute)")
        self._refresh_mask_controls()
        self._redraw()

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

    def _refresh_section_controls(self) -> None:
        if hasattr(self, "section_combo"):
            names = [f"{self._section_label(i)}" for i in range(len(self._sections))]
            self.section_combo.configure(values=names)
            if (
                self._active_section_index is not None
                and 0 <= self._active_section_index < len(names)
            ):
                self.active_section_var.set(names[self._active_section_index])
            else:
                self.active_section_var.set("")
        has_active = (
            self._active_section_index is not None
            and 0 <= self._active_section_index < len(self._sections)
        )
        if hasattr(self, "section_zoom_btn"):
            self.section_zoom_btn.configure(state="normal" if has_active else "disabled")
        if hasattr(self, "section_delete_btn"):
            self.section_delete_btn.configure(state="normal" if has_active else "disabled")

    def _on_section_select(self, _event=None) -> None:  # noqa: ANN001
        selected = self.active_section_var.get().strip()
        for idx in range(len(self._sections)):
            if self._section_label(idx) == selected:
                self._active_section_index = idx
                self._clear_active_section_marker(redraw=False)
                self._redraw_2d()
                self._refresh_section_controls()
                return

    def _on_zoom_selected_section(self) -> None:
        idx = self._active_section_index
        if idx is None or idx < 0 or idx >= len(self._sections):
            return
        (y0, x0), (y1, x1) = self._sections[idx]
        self._zoom_to_bounds((min(y0, y1), max(y0, y1), min(x0, x1), max(x0, x1)))
        self.notebook.select(0)
        self._redraw_2d()

    def _on_delete_selected_section(self) -> None:
        idx = self._active_section_index
        if idx is None or idx < 0 or idx >= len(self._sections):
            return
        label = self._section_label(idx)
        if not messagebox.askyesno("Delete section", f"Delete section {label}?"):
            return
        self._close_section_windows()
        del self._sections[idx]
        self._clear_active_section_marker(redraw=False)
        if not self._sections:
            self._active_section_index = None
        else:
            self._active_section_index = min(idx, len(self._sections) - 1)
        self._refresh_section_controls()
        self._redraw_2d()

    # --------------------------------------------------------------- Render
    def _redraw(self) -> None:
        self._redraw_2d()
        self._redraw_3d()
        self._update_status()

    def _redraw_2d(self) -> None:
        render_2d(self, FILE_COLORS)

    def _redraw_3d(self) -> None:
        render_3d(self, FILE_COLORS)

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
        if self._mask_edit_mode:
            return
        if self._measure_mode:
            return
        if self._note_mode:
            return
        if not self.files and not self.landxml_files:
            if self._notes:
                self.status.set(f"No file loaded.   |   notes: {len(self._notes)}")
            else:
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
        if self._masks:
            active = self._active_mask()
            n_visible_masks = sum(1 for i in range(len(self._masks)) if self._mask_is_visible(i))
            if active is not None:
                base += (
                    f"   |   masks: {len(self._masks)} ({n_visible_masks} visible, "
                    f"active: {active.name}, {len(active.vertices)} vertices)"
                )
            else:
                base += f"   |   masks: {len(self._masks)} ({n_visible_masks} visible)"
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
        if self._measure_results:
            n_line = sum(1 for m in self._measure_results if m.get("kind") == "line")
            n_poly = sum(1 for m in self._measure_results if m.get("kind") == "polygon")
            base += f"   |   measurements: {len(self._measure_results)} ({n_line} line, {n_poly} polygon)"
        if self._notes:
            n_with_text = sum(1 for n in self._notes if bool(n.text.strip()))
            base += f"   |   notes: {len(self._notes)} ({n_with_text} with comment)"
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
        section_index: Optional[int] = None,
        on_chainage_change: Optional[Any] = None,
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
        self._section_index = section_index
        self._on_chainage_change = on_chainage_change
        self._section_total_len = total_len
        self._chainage_updating = False

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
        self._section_ax = ax

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

        center_dist = 0.5 * total_len
        self._chainage_indicator = ax.axvline(
            center_dist,
            color="#444444",
            linestyle="--",
            linewidth=1.1,
            alpha=0.85,
            label="Centerline",
        )

        ax.set_xlabel("Distance along section (m) [Left -> Right, looking toward higher station]")
        ax.set_ylabel("Elevation (m)")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        ax.set_title(
            f"Length {total_len:,.2f} m   |   "
            f"Area ({area_top_label} - {area_bottom_label}) net {area_net:+,.2f} m²   "
            f"(cut {area_cut:,.2f} / fill {area_fill:,.2f})"
        )

        canvas = FigureCanvasTkAgg(fig, master=self)
        self._section_canvas = canvas
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        toolbar = NavigationToolbar2Tk(canvas, self)
        toolbar.update()

        slider_row = ttk.Frame(self, padding=(8, 4, 8, 0))
        slider_row.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(slider_row, text="Chainage").pack(side=tk.LEFT)
        self._chainage_var = tk.DoubleVar(value=center_dist)
        self._chainage_value_text = tk.StringVar()
        self._chainage_scale = ttk.Scale(
            slider_row,
            from_=0.0,
            to=total_len,
            orient=tk.HORIZONTAL,
            variable=self._chainage_var,
            command=self._on_chainage_slider,
        )
        self._chainage_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        ttk.Label(slider_row, textvariable=self._chainage_value_text, width=20, anchor=tk.E).pack(side=tk.LEFT)

        info = ttk.Label(
            self,
            text=(
                f"From ({x0:,.2f} N, {y0:,.2f} E) "
                f"to ({x1:,.2f} N, {y1:,.2f} E)   "
                f"length={total_len:,.2f} m, {n_samples} samples   "
                "(axis: left -> right when facing higher station)"
            ),
            anchor=tk.W, padding=(8, 2),
        )
        info.pack(side=tk.BOTTOM, fill=tk.X)

        self._set_chainage(center_dist, notify=True, update_slider=False)

    def _set_chainage(self, chainage_m: float, *, notify: bool, update_slider: bool) -> None:
        if self._section_total_len <= 0:
            return

        chainage = min(max(float(chainage_m), 0.0), self._section_total_len)
        if update_slider:
            self._chainage_updating = True
            try:
                self._chainage_var.set(chainage)
            finally:
                self._chainage_updating = False

        self._chainage_indicator.set_xdata([chainage, chainage])
        self._chainage_value_text.set(f"{chainage:,.2f} / {self._section_total_len:,.2f} m")
        self._section_canvas.draw_idle()

        if notify and self._section_index is not None and callable(self._on_chainage_change):
            self._on_chainage_change(self._section_index, chainage)

    def _on_chainage_slider(self, _value: str) -> None:
        if self._chainage_updating:
            return
        try:
            chainage = float(self._chainage_var.get())
        except (TypeError, ValueError, tk.TclError):
            return
        self._set_chainage(chainage, notify=True, update_slider=False)


class MaskVolumeResultsWindow(tk.Toplevel):
    """Toplevel table view for per-mask volume summary rows."""

    @staticmethod
    def _fmt_num(raw: Any, *, signed: bool = False) -> str:
        if raw in (None, ""):
            return ""
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return str(raw)
        if signed:
            return f"{val:+,.2f}"
        return f"{val:,.2f}"

    @staticmethod
    def _as_float(raw: Any) -> Optional[float]:
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _soften_hex_color(hex_color: str, *, strength: float = 0.88) -> str:
        """Blend a hex color toward white so it works as a table row background."""
        h = (hex_color or "").strip().lstrip("#")
        if len(h) != 6:
            return "#f5f5f5"
        try:
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
        except ValueError:
            return "#f5f5f5"

        t = min(max(float(strength), 0.0), 1.0)
        rr = int(round(r + (255 - r) * t))
        gg = int(round(g + (255 - g) * t))
        bb = int(round(b + (255 - b) * t))
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    def __init__(self, master: tk.Misc, rows: list[dict[str, Any]]) -> None:
        super().__init__(master)
        self.title("Per-mask volume summary")
        self.geometry("1560x420")

        frame = ttk.Frame(self, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)

        columns = (
            "mask",
            "area",
            "net",
            "cut",
            "fill",
            "top_intermediate",
            "top_intermediate_cut",
            "top_intermediate_fill",
            "intermediate_bottom",
            "intermediate_bottom_cut",
            "intermediate_bottom_fill",
            "status",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        tree.heading("mask", text="Mask")
        tree.heading("area", text="Area (m2)")
        tree.heading("net", text="Net (m3)")
        tree.heading("cut", text="Cut (m3)")
        tree.heading("fill", text="Fill (m3)")
        tree.heading("top_intermediate", text="Top-Intermediate (m3)")
        tree.heading("top_intermediate_cut", text="Top-Intermediate Cut (m3)")
        tree.heading("top_intermediate_fill", text="Top-Intermediate Fill (m3)")
        tree.heading("intermediate_bottom", text="Intermediate-Bottom (m3)")
        tree.heading("intermediate_bottom_cut", text="Intermediate-Bottom Cut (m3)")
        tree.heading("intermediate_bottom_fill", text="Intermediate-Bottom Fill (m3)")
        tree.heading("status", text="Status")

        tree.column("mask", width=220, anchor=tk.W)
        tree.column("area", width=110, anchor=tk.E)
        tree.column("net", width=110, anchor=tk.E)
        tree.column("cut", width=110, anchor=tk.E)
        tree.column("fill", width=110, anchor=tk.E)
        tree.column("top_intermediate", width=160, anchor=tk.E)
        tree.column("top_intermediate_cut", width=180, anchor=tk.E)
        tree.column("top_intermediate_fill", width=180, anchor=tk.E)
        tree.column("intermediate_bottom", width=180, anchor=tk.E)
        tree.column("intermediate_bottom_cut", width=200, anchor=tk.E)
        tree.column("intermediate_bottom_fill", width=200, anchor=tk.E)
        tree.column("status", width=120, anchor=tk.W)

        y_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=y_scroll.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        totals = {
            "area_m2": 0.0,
            "net_m3": 0.0,
            "cut_m3": 0.0,
            "fill_m3": 0.0,
            "top_to_intermediate_m3": 0.0,
            "top_to_intermediate_cut_m3": 0.0,
            "top_to_intermediate_fill_m3": 0.0,
            "intermediate_to_bottom_m3": 0.0,
            "intermediate_to_bottom_cut_m3": 0.0,
            "intermediate_to_bottom_fill_m3": 0.0,
        }
        has_numeric_values = False

        for idx, row in enumerate(rows):
            base_color = FILE_COLORS[idx % len(FILE_COLORS)]
            tag_name = f"mask_row_{idx}"
            tree.tag_configure(tag_name, background=self._soften_hex_color(base_color), foreground="#202020")

            area_raw = row.get("area_m2", "")
            net_raw = row.get("net_m3", "")
            cut_raw = row.get("cut_m3", "")
            fill_raw = row.get("fill_m3", "")
            upper_raw = row.get("top_to_intermediate_m3", "")
            upper_cut_raw = row.get("top_to_intermediate_cut_m3", "")
            upper_fill_raw = row.get("top_to_intermediate_fill_m3", "")
            lower_raw = row.get("intermediate_to_bottom_m3", "")
            lower_cut_raw = row.get("intermediate_to_bottom_cut_m3", "")
            lower_fill_raw = row.get("intermediate_to_bottom_fill_m3", "")

            area_val = self._as_float(area_raw)
            net_val = self._as_float(net_raw)
            cut_val = self._as_float(cut_raw)
            fill_val = self._as_float(fill_raw)
            upper_val = self._as_float(upper_raw)
            upper_cut_val = self._as_float(upper_cut_raw)
            upper_fill_val = self._as_float(upper_fill_raw)
            lower_val = self._as_float(lower_raw)
            lower_cut_val = self._as_float(lower_cut_raw)
            lower_fill_val = self._as_float(lower_fill_raw)

            if area_val is not None:
                totals["area_m2"] += area_val
                has_numeric_values = True
            if net_val is not None:
                totals["net_m3"] += net_val
                has_numeric_values = True
            if cut_val is not None:
                totals["cut_m3"] += cut_val
                has_numeric_values = True
            if fill_val is not None:
                totals["fill_m3"] += fill_val
                has_numeric_values = True
            if upper_val is not None:
                totals["top_to_intermediate_m3"] += upper_val
                has_numeric_values = True
            if upper_cut_val is not None:
                totals["top_to_intermediate_cut_m3"] += upper_cut_val
                has_numeric_values = True
            if upper_fill_val is not None:
                totals["top_to_intermediate_fill_m3"] += upper_fill_val
                has_numeric_values = True
            if lower_val is not None:
                totals["intermediate_to_bottom_m3"] += lower_val
                has_numeric_values = True
            if lower_cut_val is not None:
                totals["intermediate_to_bottom_cut_m3"] += lower_cut_val
                has_numeric_values = True
            if lower_fill_val is not None:
                totals["intermediate_to_bottom_fill_m3"] += lower_fill_val
                has_numeric_values = True

            tree.insert(
                "",
                tk.END,
                values=(
                    row.get("mask_name", ""),
                    self._fmt_num(area_raw),
                    self._fmt_num(net_raw, signed=True),
                    self._fmt_num(cut_raw),
                    self._fmt_num(fill_raw),
                    self._fmt_num(upper_raw, signed=True),
                    self._fmt_num(upper_cut_raw),
                    self._fmt_num(upper_fill_raw),
                    self._fmt_num(lower_raw, signed=True),
                    self._fmt_num(lower_cut_raw),
                    self._fmt_num(lower_fill_raw),
                    row.get("status", ""),
                ),
                tags=(tag_name,),
            )

        if has_numeric_values:
            total_font = tkfont.nametofont("TkDefaultFont").copy()
            total_font.configure(weight="bold")
            tree.tag_configure("grand_total", background="#dfeaf5", foreground="#102030", font=total_font)
            tree.insert(
                "",
                tk.END,
                values=(
                    "Grand total",
                    self._fmt_num(totals["area_m2"]),
                    self._fmt_num(totals["net_m3"], signed=True),
                    self._fmt_num(totals["cut_m3"]),
                    self._fmt_num(totals["fill_m3"]),
                    self._fmt_num(totals["top_to_intermediate_m3"], signed=True),
                    self._fmt_num(totals["top_to_intermediate_cut_m3"]),
                    self._fmt_num(totals["top_to_intermediate_fill_m3"]),
                    self._fmt_num(totals["intermediate_to_bottom_m3"], signed=True),
                    self._fmt_num(totals["intermediate_to_bottom_cut_m3"]),
                    self._fmt_num(totals["intermediate_to_bottom_fill_m3"]),
                    "sum",
                ),
                tags=("grand_total",),
            )

        footer = ttk.Label(
            self,
            text=f"Rows: {len(rows)} (+ total)",
            anchor=tk.W,
            padding=(8, 2),
        )
        footer.pack(side=tk.BOTTOM, fill=tk.X)


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
