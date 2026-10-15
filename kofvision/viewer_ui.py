from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any


DARK_COLORS = {
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
}


def configure_viewer_styles(viewer: Any) -> None:
    """Apply the default ttk style setup used by the viewer."""
    style = ttk.Style(viewer)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    viewer.configure(background=DARK_COLORS["bg_root"])
    viewer.tk_setPalette(
        background=DARK_COLORS["bg_root"],
        foreground=DARK_COLORS["fg_primary"],
        activeBackground=DARK_COLORS["bg_hover"],
        activeForeground=DARK_COLORS["fg_primary"],
        highlightColor=DARK_COLORS["accent"],
        selectColor=DARK_COLORS["accent"],
    )

    # Cover non-ttk widgets that inherit colors from Tk option database.
    viewer.option_add("*Menu.tearOff", False)
    viewer.option_add("*Menu.background", DARK_COLORS["bg_surface"])
    viewer.option_add("*Menu.foreground", DARK_COLORS["fg_primary"])
    viewer.option_add("*Menu.activeBackground", DARK_COLORS["accent"])
    viewer.option_add("*Menu.activeForeground", "#ffffff")
    viewer.option_add("*Canvas.background", DARK_COLORS["bg_surface"])

    base_font = tkfont.nametofont("TkDefaultFont")
    header_font = base_font.copy()
    header_font.configure(weight="bold", size=max(base_font.cget("size"), 10))

    style.configure(
        ".",
        background=DARK_COLORS["bg_root"],
        foreground=DARK_COLORS["fg_primary"],
        bordercolor=DARK_COLORS["bg_border"],
        lightcolor=DARK_COLORS["bg_border"],
        darkcolor=DARK_COLORS["bg_border"],
    )
    style.map(".", foreground=[("disabled", DARK_COLORS["fg_muted"])])

    style.configure("TFrame", background=DARK_COLORS["bg_root"])
    style.configure("TopBar.TFrame", padding=4, background=DARK_COLORS["bg_surface"])

    style.configure("TLabel", background=DARK_COLORS["bg_root"], foreground=DARK_COLORS["fg_primary"])
    style.configure("PanelHeader.TLabel", font=header_font, background=DARK_COLORS["bg_root"])

    style.configure(
        "TLabelframe",
        background=DARK_COLORS["bg_root"],
        bordercolor=DARK_COLORS["bg_border"],
        relief="groove",
        borderwidth=1,
    )
    style.configure("TLabelframe.Label", background=DARK_COLORS["bg_root"], foreground=DARK_COLORS["fg_primary"])

    style.configure(
        "PanelBody.TFrame",
        relief="groove",
        borderwidth=1,
        background=DARK_COLORS["bg_surface"],
        bordercolor=DARK_COLORS["bg_border"],
    )

    style.configure(
        "TButton",
        padding=(10, 6),
        background=DARK_COLORS["bg_panel"],
        foreground=DARK_COLORS["fg_primary"],
        bordercolor=DARK_COLORS["bg_border"],
    )
    style.map(
        "TButton",
        background=[("active", DARK_COLORS["bg_hover"]), ("pressed", DARK_COLORS["bg_pressed"])],
        foreground=[("disabled", DARK_COLORS["fg_muted"])],
    )

    style.configure(
        "Primary.TButton",
        padding=(10, 6),
        background=DARK_COLORS["accent"],
        foreground="#ffffff",
        bordercolor=DARK_COLORS["accent"],
    )
    style.map(
        "Primary.TButton",
        background=[("active", DARK_COLORS["accent_hover"]), ("pressed", DARK_COLORS["accent"])],
    )

    style.configure("TCheckbutton", background=DARK_COLORS["bg_root"], foreground=DARK_COLORS["fg_primary"])
    style.configure("TRadiobutton", background=DARK_COLORS["bg_root"], foreground=DARK_COLORS["fg_primary"])
    style.configure(
        "TMenubutton",
        background=DARK_COLORS["bg_panel"],
        foreground=DARK_COLORS["fg_primary"],
        bordercolor=DARK_COLORS["bg_border"],
    )

    style.configure(
        "TEntry",
        fieldbackground=DARK_COLORS["bg_panel"],
        foreground=DARK_COLORS["fg_primary"],
        insertcolor=DARK_COLORS["fg_primary"],
    )
    style.configure(
        "TCombobox",
        fieldbackground=DARK_COLORS["bg_panel"],
        background=DARK_COLORS["bg_panel"],
        foreground=DARK_COLORS["fg_primary"],
        arrowcolor=DARK_COLORS["fg_primary"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", DARK_COLORS["bg_panel"])],
        foreground=[("readonly", DARK_COLORS["fg_primary"])],
    )

    style.configure("TNotebook", background=DARK_COLORS["bg_root"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=DARK_COLORS["bg_panel"],
        foreground=DARK_COLORS["fg_muted"],
        padding=(10, 5),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", DARK_COLORS["bg_surface"]), ("active", DARK_COLORS["bg_hover"])],
        foreground=[("selected", DARK_COLORS["fg_primary"])],
    )

    style.configure("TScrollbar", background=DARK_COLORS["bg_panel"], troughcolor=DARK_COLORS["bg_surface"])
    style.configure("TSeparator", background=DARK_COLORS["bg_border"])
