from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any


THEME_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg_root": "#111827",
        "bg_surface": "#1f2937",
        "bg_panel": "#243244",
        "bg_hover": "#2f4157",
        "bg_pressed": "#1e2f44",
        "bg_border": "#334155",
        "fg_primary": "#e5e7eb",
        "fg_muted": "#9ca3af",
        "accent": "#1f6aa5",
        "accent_hover": "#2b7fc2",
        "tooltip_bg": "#1f2937",
        "tooltip_fg": "#e5e7eb",
        "mpl_figure_bg": "#111827",
        "mpl_axes_bg": "#0f172a",
        "mpl_text": "#e5e7eb",
        "mpl_muted_text": "#9ca3af",
        "mpl_grid": "#334155",
        "mpl_spine": "#475569",
        "mpl_legend_bg": "#1f2937",
        "mpl_legend_edge": "#334155",
        "mpl_section_line": "#e2e8f0",
        "mpl_empty_state": "#94a3b8",
        "mpl_toolbar_bg": "#1f2937",
        "mpl_toolbar_button_bg": "#243244",
        "mpl_toolbar_button_fg": "#e5e7eb",
        "mpl_toolbar_button_active_bg": "#2f4157",
        "mpl_toolbar_button_active_fg": "#ffffff",
        "table_row_text": "#f8fafc",
        "table_total_bg": "#334155",
        "table_total_fg": "#f8fafc",
    },
    "light": {
        "bg_root": "#f3f4f6",
        "bg_surface": "#ffffff",
        "bg_panel": "#e5e7eb",
        "bg_hover": "#dbeafe",
        "bg_pressed": "#bfdbfe",
        "bg_border": "#cbd5e1",
        "fg_primary": "#111827",
        "fg_muted": "#4b5563",
        "accent": "#1f6aa5",
        "accent_hover": "#2b7fc2",
        "tooltip_bg": "#fffbea",
        "tooltip_fg": "#111827",
        "mpl_figure_bg": "#f8fafc",
        "mpl_axes_bg": "#ffffff",
        "mpl_text": "#111827",
        "mpl_muted_text": "#6b7280",
        "mpl_grid": "#d1d5db",
        "mpl_spine": "#94a3b8",
        "mpl_legend_bg": "#ffffff",
        "mpl_legend_edge": "#cbd5e1",
        "mpl_section_line": "#1f2937",
        "mpl_empty_state": "#6b7280",
        "mpl_toolbar_bg": "#e5e7eb",
        "mpl_toolbar_button_bg": "#f8fafc",
        "mpl_toolbar_button_fg": "#111827",
        "mpl_toolbar_button_active_bg": "#dbeafe",
        "mpl_toolbar_button_active_fg": "#111827",
        "table_row_text": "#111827",
        "table_total_bg": "#dbeafe",
        "table_total_fg": "#102030",
    },
}


DARK_COLORS = THEME_PALETTES["dark"]


def normalize_theme_name(theme_name: str | None) -> str:
    normalized = (theme_name or "dark").strip().lower()
    if normalized not in THEME_PALETTES:
        return "dark"
    return normalized


def get_theme_palette(theme_name: str | None) -> dict[str, str]:
    return THEME_PALETTES[normalize_theme_name(theme_name)]


def _apply_toolbar_widget_colors(widget: tk.Widget, palette: dict[str, str]) -> None:
    try:
        widget.configure(background=palette["mpl_toolbar_bg"])
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        try:
            child.configure(
                background=palette["mpl_toolbar_button_bg"],
                foreground=palette["mpl_toolbar_button_fg"],
                activebackground=palette["mpl_toolbar_button_active_bg"],
                activeforeground=palette["mpl_toolbar_button_active_fg"],
                highlightbackground=palette["mpl_toolbar_bg"],
                highlightcolor=palette["accent"],
                selectcolor=palette["bg_panel"],
            )
        except tk.TclError:
            try:
                child.configure(background=palette["mpl_toolbar_bg"])
            except tk.TclError:
                pass
        _apply_toolbar_widget_colors(child, palette)


def apply_matplotlib_toolbar_theme(toolbar: Any, palette: dict[str, str]) -> None:
    try:
        toolbar.configure(background=palette["mpl_toolbar_bg"], borderwidth=0)
    except Exception:  # noqa: BLE001
        pass
    _apply_toolbar_widget_colors(toolbar, palette)


def apply_matplotlib_axes_theme(ax: Any, palette: dict[str, str], *, is_3d: bool = False) -> None:
    ax.set_facecolor(palette["mpl_axes_bg"])
    ax.xaxis.label.set_color(palette["mpl_text"])
    ax.yaxis.label.set_color(palette["mpl_text"])
    ax.tick_params(axis="x", colors=palette["mpl_text"])
    ax.tick_params(axis="y", colors=palette["mpl_text"])
    for spine in ax.spines.values():
        spine.set_color(palette["mpl_spine"])
    ax.grid(True, linestyle=":", alpha=0.5, color=palette["mpl_grid"])

    if is_3d:
        try:
            ax.zaxis.label.set_color(palette["mpl_text"])
            ax.tick_params(axis="z", colors=palette["mpl_text"])
            for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
                axis.pane.set_facecolor(palette["mpl_axes_bg"])
                axis.pane.set_edgecolor(palette["mpl_spine"])
                axis._axinfo["grid"]["color"] = palette["mpl_grid"]
        except Exception:  # noqa: BLE001
            pass


def apply_matplotlib_figure_theme(fig: Any, palette: dict[str, str]) -> None:
    fig.patch.set_facecolor(palette["mpl_figure_bg"])


def apply_matplotlib_legend_theme(ax: Any, palette: dict[str, str]) -> None:
    legend = ax.get_legend()
    if legend is None:
        return
    frame = legend.get_frame()
    frame.set_facecolor(palette["mpl_legend_bg"])
    frame.set_edgecolor(palette["mpl_legend_edge"])
    frame.set_alpha(0.9)
    for txt in legend.get_texts():
        txt.set_color(palette["mpl_text"])


def configure_viewer_styles(viewer: Any, theme_name: str = "dark") -> dict[str, str]:
    """Apply ttk and Tk styles for the selected theme and return its palette."""
    palette = get_theme_palette(theme_name)


    style = ttk.Style(viewer)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    viewer.configure(background=palette["bg_root"])
    viewer.tk_setPalette(
        background=palette["bg_root"],
        foreground=palette["fg_primary"],
        activeBackground=palette["bg_hover"],
        activeForeground=palette["fg_primary"],
        highlightColor=palette["accent"],
        selectColor=palette["accent"],
    )

    # Cover non-ttk widgets that inherit colors from Tk option database.
    viewer.option_add("*Menu.tearOff", False)
    viewer.option_add("*Menu.background", palette["bg_surface"])
    viewer.option_add("*Menu.foreground", palette["fg_primary"])
    viewer.option_add("*Menu.activeBackground", palette["accent"])
    viewer.option_add("*Menu.activeForeground", "#ffffff")
    viewer.option_add("*Canvas.background", palette["bg_surface"])

    base_font = tkfont.nametofont("TkDefaultFont")
    header_font = base_font.copy()
    header_font.configure(weight="bold", size=max(base_font.cget("size"), 10))

    style.configure(
        ".",
        background=palette["bg_root"],
        foreground=palette["fg_primary"],
        bordercolor=palette["bg_border"],
        lightcolor=palette["bg_border"],
        darkcolor=palette["bg_border"],
    )
    style.map(".", foreground=[("disabled", palette["fg_muted"])])

    style.configure("TFrame", background=palette["bg_root"])
    style.configure("TopBar.TFrame", padding=4, background=palette["bg_surface"])

    style.configure("TLabel", background=palette["bg_root"], foreground=palette["fg_primary"])
    style.configure("PanelHeader.TLabel", font=header_font, background=palette["bg_root"])
    style.configure("Muted.TLabel", background=palette["bg_root"], foreground=palette["fg_muted"])

    style.configure(
        "TLabelframe",
        background=palette["bg_root"],
        bordercolor=palette["bg_border"],
        relief="groove",
        borderwidth=1,
    )
    style.configure("TLabelframe.Label", background=palette["bg_root"], foreground=palette["fg_primary"])

    style.configure(
        "PanelBody.TFrame",
        relief="groove",
        borderwidth=1,
        background=palette["bg_surface"],
        bordercolor=palette["bg_border"],
    )

    style.configure(
        "TButton",
        padding=(10, 6),
        background=palette["bg_panel"],
        foreground=palette["fg_primary"],
        bordercolor=palette["bg_border"],
    )
    style.map(
        "TButton",
        background=[("active", palette["bg_hover"]), ("pressed", palette["bg_pressed"])],
        foreground=[("disabled", palette["fg_muted"])],
    )

    style.configure(
        "Primary.TButton",
        padding=(10, 6),
        background=palette["accent"],
        foreground="#ffffff",
        bordercolor=palette["accent"],
    )
    style.map(
        "Primary.TButton",
        background=[("active", palette["accent_hover"]), ("pressed", palette["accent"])],
    )

    style.configure("TCheckbutton", background=palette["bg_root"], foreground=palette["fg_primary"])
    style.configure("TRadiobutton", background=palette["bg_root"], foreground=palette["fg_primary"])
    style.configure(
        "TMenubutton",
        background=palette["bg_panel"],
        foreground=palette["fg_primary"],
        bordercolor=palette["bg_border"],
    )

    style.configure(
        "TEntry",
        fieldbackground=palette["bg_panel"],
        foreground=palette["fg_primary"],
        insertcolor=palette["fg_primary"],
    )
    style.configure(
        "TCombobox",
        fieldbackground=palette["bg_panel"],
        background=palette["bg_panel"],
        foreground=palette["fg_primary"],
        arrowcolor=palette["fg_primary"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette["bg_panel"])],
        foreground=[("readonly", palette["fg_primary"])],
    )

    style.configure("TNotebook", background=palette["bg_root"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=palette["bg_panel"],
        foreground=palette["fg_muted"],
        padding=(10, 5),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", palette["bg_surface"]), ("active", palette["bg_hover"])],
        foreground=[("selected", palette["fg_primary"])],
    )

    style.configure("TScrollbar", background=palette["bg_panel"], troughcolor=palette["bg_surface"])
    style.configure("TSeparator", background=palette["bg_border"])

    return palette
