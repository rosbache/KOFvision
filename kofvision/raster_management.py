from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any

from kofvision.raster import load_elevation_surface, load_raster_layer


class RasterLayerManager:
    """Encapsulates raster-layer state transitions and related UI updates."""

    def __init__(self, viewer: Any) -> None:
        self.viewer = viewer

    def on_open_raster(self) -> None:
        path = filedialog.askopenfilename(
            title="Open raster background",
            filetypes=[
                ("Raster images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.load_raster(path)

    def raster_display_name(self, idx: int) -> str:
        layer = self.viewer._raster_layers[idx]
        name = os.path.basename(layer.path)
        marks: list[str] = []
        if idx == self.viewer._elevation_raster_index:
            marks.append("elev")
        if idx == len(self.viewer._raster_layers) - 1:
            marks.append("top")
        if marks:
            return f"{name} ({', '.join(marks)})"
        return name

    def refresh_raster_selector(self) -> None:
        names = [self.raster_display_name(i) for i in range(len(self.viewer._raster_layers))]
        self.viewer.active_raster_combo["values"] = names
        if not names:
            self.viewer._active_raster_index = None
            self.viewer.active_raster_name.set("")
            self.refresh_intermediate_raster_selector()
            return
        if self.viewer._active_raster_index is None or self.viewer._active_raster_index >= len(names):
            self.viewer._active_raster_index = len(names) - 1
        self.viewer.active_raster_name.set(names[self.viewer._active_raster_index])
        self.sync_alpha_from_active()
        self.refresh_intermediate_raster_selector()

    def set_active_raster_index(self, idx: int | None) -> None:
        if idx is None or idx < 0 or idx >= len(self.viewer._raster_layers):
            self.viewer._active_raster_index = None
            self.viewer.active_raster_name.set("")
            return
        self.viewer._active_raster_index = idx
        self.refresh_raster_selector()

    def on_select_active_raster(self) -> None:
        idx = self.viewer.active_raster_combo.current()
        if idx < 0 or idx >= len(self.viewer._raster_layers):
            return
        self.viewer._active_raster_index = idx
        self.sync_alpha_from_active()

    def sync_alpha_from_active(self) -> None:
        layer = self.get_active_raster_layer()
        if layer is None:
            return
        self.viewer.raster_alpha.set(float(layer.alpha.get()))

    def get_active_raster_layer(self):
        idx = self.viewer._active_raster_index
        if idx is None or idx < 0 or idx >= len(self.viewer._raster_layers):
            return None
        return self.viewer._raster_layers[idx]

    def top_visible_raster_layer(self):
        if not self.viewer.show_raster.get():
            return None
        for layer in reversed(self.viewer._raster_layers):
            if layer.visible.get():
                return layer
        return None

    def on_move_raster_up(self) -> None:
        idx = self.viewer._active_raster_index
        if idx is None or idx >= len(self.viewer._raster_layers) - 1:
            return
        layers = self.viewer._raster_layers
        layers[idx], layers[idx + 1] = layers[idx + 1], layers[idx]
        if self.viewer._elevation_raster_index == idx:
            self.viewer._elevation_raster_index = idx + 1
        elif self.viewer._elevation_raster_index == idx + 1:
            self.viewer._elevation_raster_index = idx
        self.viewer._active_raster_index = idx + 1
        self.refresh_raster_selector()
        self.viewer._refresh_layer_controls()
        self.viewer._redraw()

    def on_move_raster_down(self) -> None:
        idx = self.viewer._active_raster_index
        if idx is None or idx <= 0:
            return
        layers = self.viewer._raster_layers
        layers[idx], layers[idx - 1] = layers[idx - 1], layers[idx]
        if self.viewer._elevation_raster_index == idx:
            self.viewer._elevation_raster_index = idx - 1
        elif self.viewer._elevation_raster_index == idx - 1:
            self.viewer._elevation_raster_index = idx
        self.viewer._active_raster_index = idx - 1
        self.refresh_raster_selector()
        self.viewer._refresh_layer_controls()
        self.viewer._redraw()

    def on_set_elevation_raster(self) -> None:
        idx = self.viewer._active_raster_index
        if idx is None:
            messagebox.showinfo("KOF Viewer", "No active raster selected.")
            return
        layer = self.viewer._raster_layers[idx]
        if layer.elevation_surface is None:
            messagebox.showwarning(
                "KOF Viewer",
                "Selected raster is not a supported single-band elevation raster.",
            )
            return
        self.viewer._elevation_raster_index = idx
        self.refresh_raster_selector()
        self.viewer._refresh_layer_controls()
        self.viewer._redraw_2d()

    def on_clear_raster(self) -> None:
        idx = self.viewer._active_raster_index
        if idx is None or idx < 0 or idx >= len(self.viewer._raster_layers):
            return
        self.viewer._raster_layers.pop(idx)
        if self.viewer._elevation_raster_index is not None:
            if self.viewer._elevation_raster_index == idx:
                self.viewer._elevation_raster_index = None
            elif self.viewer._elevation_raster_index > idx:
                self.viewer._elevation_raster_index -= 1
        if not self.viewer._raster_layers:
            self.viewer.show_raster.set(False)
            self.viewer._active_raster_index = None
        else:
            self.viewer._active_raster_index = min(idx, len(self.viewer._raster_layers) - 1)
        self.refresh_raster_selector()
        self.viewer._refresh_layer_controls()
        self.viewer._redraw()

    def on_raster_alpha_change(self) -> None:
        try:
            alpha = float(self.viewer.raster_alpha.get())
        except (tk.TclError, ValueError):
            return
        alpha = min(max(alpha, 0.05), 1.0)
        self.viewer.raster_alpha.set(alpha)
        layer = self.get_active_raster_layer()
        if layer is not None:
            layer.alpha.set(alpha)
        if self.viewer._raster_layers and self.viewer.show_raster.get():
            self.viewer._redraw_2d()

    def on_tin_fill_alpha_change(self) -> None:
        try:
            alpha = float(self.viewer.tin_fill_alpha.get())
        except (tk.TclError, ValueError):
            alpha = 0.14
        alpha = min(max(alpha, 0.0), 1.0)
        self.viewer.tin_fill_alpha.set(alpha)
        self.viewer._redraw_2d()

    def on_tin_fill_alpha_scale(self, _value: str) -> None:
        self.on_tin_fill_alpha_change()

    def load_raster(self, path: str) -> None:
        clip_bounds = self.viewer._all_files_bounds()
        clip_buffer_m = self.viewer._safe_clip_buffer_m()
        try:
            layer = load_raster_layer(
                path,
                clip_bounds=clip_bounds,
                clip_buffer_m=clip_buffer_m,
                prefer_clip=bool(self.viewer.raster_clip_to_kof.get()),
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

        elevation_surface = None
        try:
            elevation_surface = load_elevation_surface(
                path,
                clip_bounds=clip_bounds,
                clip_buffer_m=clip_buffer_m,
                prefer_clip=bool(self.viewer.raster_clip_to_kof.get()),
            )
        except Exception:
            elevation_surface = None

        self.viewer._raster_layers.append(
            self.viewer._RasterViewLayerClass(
                path=path,
                image=layer.image,
                extent=layer.extent,
                origin=layer.origin,
                visible=tk.BooleanVar(value=True),
                alpha=tk.DoubleVar(value=float(self.viewer.raster_alpha.get())),
                elevation_surface=elevation_surface,
            )
        )
        self.viewer._active_raster_index = len(self.viewer._raster_layers) - 1
        if self.viewer._elevation_raster_index is None and elevation_surface is not None:
            self.viewer._elevation_raster_index = self.viewer._active_raster_index
        self.viewer.show_raster.set(True)
        self.refresh_raster_selector()
        self.viewer._refresh_layer_controls()
        self.viewer._redraw()

        if elevation_surface is None:
            messagebox.showinfo(
                "KOF Viewer",
                "Raster loaded for display, but it is not a supported single-band elevation raster.\n"
                "Volume modes using raster elevation require a single-band DEM.",
            )

    def refresh_intermediate_raster_selector(self) -> None:
        self.viewer._intermediate_raster_index_map.clear()
        names: list[str] = []
        for idx, layer in enumerate(self.viewer._raster_layers):
            if layer.elevation_surface is None:
                continue
            label = self.raster_display_name(idx)
            names.append(label)
            self.viewer._intermediate_raster_index_map[label] = idx
        self.viewer.intermediate_raster_combo["values"] = names
        if names and self.viewer.intermediate_raster_var.get() not in self.viewer._intermediate_raster_index_map:
            self.viewer.intermediate_raster_var.set(names[0])
        if not names:
            self.viewer.intermediate_raster_var.set("")
