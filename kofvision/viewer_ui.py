from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any


def configure_viewer_styles(viewer: Any) -> None:
    """Apply the default ttk style setup used by the viewer."""
    style = ttk.Style(viewer)
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
