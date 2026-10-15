from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox

TOOL_NONE = "none"
TOOL_SECTION = "section"
TOOL_MASK = "mask"
TOOL_MASK_EDIT = "mask_edit"
TOOL_MEASURE = "measure"
TOOL_NOTE = "note"


@dataclass
class ToolDispatcher:
    """Lightweight active-tool state tracker for viewer interaction modes."""

    active_tool: str = TOOL_NONE

    def activate(self, tool_name: str) -> None:
        self.active_tool = tool_name

    def clear(self, tool_name: str | None = None) -> None:
        if tool_name is None or self.active_tool == tool_name:
            self.active_tool = TOOL_NONE

    def is_active(self, tool_name: str) -> bool:
        return self.active_tool == tool_name


class SectionTool:
    @staticmethod
    def toggle(viewer) -> None:  # noqa: ANN001
        if viewer._mask_mode:
            viewer._end_mask_mode()
        if viewer._measure_mode:
            viewer._end_measure_mode()
        if viewer._note_mode:
            viewer._end_note_mode()
        if viewer._section_mode:
            viewer._end_section_mode()
            return
        if len(viewer.files) < 2:
            messagebox.showinfo("KOF Viewer", "Load at least two files and select Top/Bottom first.")
            return
        if not viewer.top_var.get() or not viewer.bottom_var.get():
            messagebox.showinfo("KOF Viewer", "Pick a top and bottom surface before drawing a section.")
            return
        viewer._section_mode = True
        viewer._tool_dispatch.activate(TOOL_SECTION)
        viewer._section_click_points = []
        viewer.section_btn.configure(text="Cancel section")
        viewer.notebook.select(0)
        viewer.status.set("Section mode: click two points on the 2D plot (right-click or press Esc to cancel).")
        viewer._section_cid = viewer.canvas2d.mpl_connect("button_press_event", viewer._on_section_click)
        viewer.bind("<Escape>", lambda _e: viewer._end_section_mode())

    @staticmethod
    def end(viewer) -> None:  # noqa: ANN001
        viewer._section_mode = False
        viewer._tool_dispatch.clear(TOOL_SECTION)
        if viewer._section_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._section_cid)
            viewer._section_cid = None
        viewer.section_btn.configure(text="Draw section")
        viewer.unbind("<Escape>")
        viewer._section_click_points = []
        viewer._section_preview = None
        viewer._update_status()

    @staticmethod
    def on_click(viewer, event) -> None:  # noqa: ANN001
        if event.inaxes is not viewer.ax2d:
            return
        if event.button == 3:
            viewer._end_section_mode()
            return
        if event.xdata is None or event.ydata is None:
            return
        viewer._section_click_points.append((event.xdata, event.ydata))

        if len(viewer._section_click_points) == 1:
            viewer.status.set("Section mode: click the second endpoint (right-click or Esc to cancel).")
            y0, x0 = viewer._section_click_points[0]
            viewer.ax2d.plot(
                [y0],
                [x0],
                marker="x",
                color=viewer._theme.get("mpl_section_line", "black"),
                markersize=8,
            )
            viewer.canvas2d.draw_idle()
            return

        (y0, x0), (y1, x1) = viewer._section_click_points
        viewer._sections.append(((y0, x0), (y1, x1)))
        viewer._active_section_index = len(viewer._sections) - 1
        viewer._refresh_section_controls()

        top = viewer._find_file(viewer.top_var.get())
        bot = viewer._find_file(viewer.bottom_var.get())
        viewer._end_section_mode()
        viewer._redraw_2d()
        if top is not None and bot is not None:
            viewer._open_section_window_at_index(len(viewer._sections) - 1, top=top, bottom=bot)


class NoteTool:
    @staticmethod
    def pick_tolerance(viewer) -> float:  # noqa: ANN001
        xmin, xmax = viewer.ax2d.get_xlim()
        ymin, ymax = viewer.ax2d.get_ylim()
        span = max(abs(xmax - xmin), abs(ymax - ymin), 1e-9)
        return span * 0.02

    @staticmethod
    def pick_index(viewer, y: float, x: float):  # noqa: ANN001
        if not viewer._notes:
            return None
        tol = NoteTool.pick_tolerance(viewer)
        best_idx = None
        best_dist2 = float("inf")
        for idx, note in enumerate(viewer._notes):
            if not note.visible:
                continue
            dy = note.y - y
            dx = note.x - x
            dist2 = (dy * dy) + (dx * dx)
            if dist2 <= (tol * tol) and dist2 < best_dist2:
                best_dist2 = dist2
                best_idx = idx
        return best_idx

    @staticmethod
    def default_title(index: int) -> str:
        return f"Note {index + 1}"

    @staticmethod
    def edit_dialog(
        viewer,  # noqa: ANN001
        *,
        title_value: str,
        text_value: str,
        allow_delete: bool,
    ) -> tuple[str, str, str]:
        dialog = tk.Toplevel(viewer)
        dialog.title("Map note")
        dialog.transient(viewer)
        dialog.grab_set()
        dialog.resizable(True, True)

        container = ttk.Frame(dialog, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Title:").pack(anchor="w")
        title_var = tk.StringVar(value=title_value)
        title_entry = ttk.Entry(container, textvariable=title_var, width=48)
        title_entry.pack(fill=tk.X, pady=(2, 8))

        ttk.Label(container, text="Comment:").pack(anchor="w")
        text_widget = tk.Text(container, width=52, height=10, wrap="word")
        text_widget.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        text_widget.insert("1.0", text_value)

        result: dict[str, str] = {
            "action": "cancel",
            "title": title_value,
            "text": text_value,
        }

        def _save() -> None:
            result["action"] = "save"
            result["title"] = title_var.get().strip()
            result["text"] = text_widget.get("1.0", "end-1c").strip()
            dialog.destroy()

        def _delete() -> None:
            result["action"] = "delete"
            result["title"] = title_var.get().strip()
            result["text"] = text_widget.get("1.0", "end-1c").strip()
            dialog.destroy()

        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X)
        if allow_delete:
            ttk.Button(buttons, text="Delete", command=_delete).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=_save).pack(side=tk.RIGHT, padx=(0, 6))

        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        title_entry.focus_set()
        viewer.wait_window(dialog)
        return result["action"], result["title"], result["text"]

    @staticmethod
    def show_at_index(viewer, idx: int) -> None:  # noqa: ANN001
        if idx < 0 or idx >= len(viewer._notes):
            return
        viewer._active_note_index = idx
        note = viewer._notes[idx]
        body = note.text if note.text else "(no comment)"
        messagebox.showinfo(note.title, body)
        viewer._redraw_2d()

    @staticmethod
    def edit_at_index(viewer, idx: int) -> None:  # noqa: ANN001
        if idx < 0 or idx >= len(viewer._notes):
            return
        note = viewer._notes[idx]
        action, title, text = NoteTool.edit_dialog(
            viewer,
            title_value=note.title,
            text_value=note.text,
            allow_delete=True,
        )
        if action == "delete":
            if viewer._delete_note_at_index(idx, ask_confirm=True):
                viewer._redraw_2d()
                viewer.status.set("Note deleted.")
            return
        if action != "save":
            return
        if not title and not text:
            if messagebox.askyesno("Delete empty note", "The note is empty. Delete it?"):
                if viewer._delete_note_at_index(idx, ask_confirm=False):
                    viewer._redraw_2d()
                    viewer.status.set("Note deleted.")
            return
        note.title = title or NoteTool.default_title(idx)
        note.text = text
        viewer._active_note_index = idx
        viewer._refresh_layer_controls()
        viewer._redraw_2d()
        viewer.status.set(f"Updated note '{note.title}'.")

    @staticmethod
    def on_map_click(viewer, event) -> None:  # noqa: ANN001
        if event.inaxes is not viewer.ax2d:
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return
        if viewer._section_mode or viewer._mask_mode or viewer._mask_edit_mode or viewer._measure_mode or viewer._note_mode:
            return
        idx = NoteTool.pick_index(viewer, float(event.xdata), float(event.ydata))
        if idx is None:
            viewer._cancel_pending_note_single_click()
            return
        if event.dblclick:
            viewer._cancel_pending_note_single_click()
            NoteTool.edit_at_index(viewer, idx)
            return
        viewer._cancel_pending_note_single_click()
        viewer._pending_note_click_index = idx
        viewer._pending_note_click_after_id = viewer.after(250, viewer._run_pending_note_single_click)

    @staticmethod
    def toggle(viewer) -> None:  # noqa: ANN001
        if viewer._section_mode:
            viewer._end_section_mode()
        if viewer._mask_mode:
            viewer._end_mask_mode()
        if viewer._mask_edit_mode:
            viewer._end_mask_edit_mode()
        if viewer._measure_mode:
            viewer._end_measure_mode()
        if viewer._note_mode:
            viewer._end_note_mode()
            viewer._redraw_2d()
            return

        viewer._note_mode = True
        viewer._tool_dispatch.activate(TOOL_NOTE)
        viewer.note_btn.configure(text="Cancel note")
        viewer.notebook.select(0)
        viewer.status.set("Note mode: click a point on the 2D map to place a note. Right-click or Esc to cancel.")
        viewer._note_cid = viewer.canvas2d.mpl_connect("button_press_event", viewer._on_note_place_click)
        viewer.bind("<Escape>", lambda _e: viewer._cancel_note_mode())

    @staticmethod
    def cancel(viewer) -> None:  # noqa: ANN001
        viewer._end_note_mode()
        viewer._redraw_2d()

    @staticmethod
    def end(viewer) -> None:  # noqa: ANN001
        viewer._note_mode = False
        viewer._tool_dispatch.clear(TOOL_NOTE)
        viewer._cancel_pending_note_single_click()
        if viewer._note_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._note_cid)
            viewer._note_cid = None
        if hasattr(viewer, "note_btn"):
            viewer.note_btn.configure(text="Add note")
        viewer.unbind("<Escape>")
        viewer._update_status()

    @staticmethod
    def on_place_click(viewer, event) -> None:  # noqa: ANN001
        if event.inaxes is not viewer.ax2d:
            return
        if event.button == 3:
            viewer._cancel_note_mode()
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        action, title, text = NoteTool.edit_dialog(
            viewer,
            title_value="",
            text_value="",
            allow_delete=False,
        )
        if action != "save":
            return
        if not title and not text:
            messagebox.showinfo("Map note", "Note was empty and was not created.")
            return
        viewer._append_note(title=title, text=text, y=float(event.xdata), x=float(event.ydata), visible=True)
        viewer._end_note_mode()
        viewer._redraw_2d()
        note = viewer._notes[-1]
        viewer.status.set(f"Added note '{note.title}'.")


class MeasureTool:
    @staticmethod
    def toggle(viewer) -> None:  # noqa: ANN001
        if viewer._section_mode:
            viewer._end_section_mode()
        if viewer._mask_mode:
            viewer._end_mask_mode()
        if viewer._mask_edit_mode:
            viewer._end_mask_edit_mode()
        if viewer._note_mode:
            viewer._end_note_mode()
        if viewer._measure_mode:
            viewer._end_measure_mode()
            viewer._redraw_2d()
            return

        viewer._measure_mode = True
        viewer._tool_dispatch.activate(TOOL_MEASURE)
        viewer._measure_click_points = []
        viewer.measure_btn.configure(text="Cancel measure")
        if hasattr(viewer, "measure_toggle_btn"):
            viewer.measure_toggle_btn.configure(text="Cancel measure")
        viewer.notebook.select(0)
        mode = viewer._current_measure_mode_key()
        if mode == "polygon":
            viewer.status.set("Measure polygon: left-click vertices, then right-click or double-click to close. Esc to cancel.")
        else:
            viewer.status.set("Measure line: click two points on the 2D plot. Right-click or Esc to cancel.")
        viewer._measure_cid = viewer.canvas2d.mpl_connect("button_press_event", viewer._on_measure_click)
        viewer.bind("<Escape>", lambda _e: viewer._cancel_measure_mode())

    @staticmethod
    def cancel(viewer) -> None:  # noqa: ANN001
        viewer._measure_click_points = []
        viewer._end_measure_mode()
        viewer._redraw_2d()

    @staticmethod
    def end(viewer) -> None:  # noqa: ANN001
        viewer._measure_mode = False
        viewer._tool_dispatch.clear(TOOL_MEASURE)
        if viewer._measure_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._measure_cid)
            viewer._measure_cid = None
        viewer.measure_btn.configure(text="Measure")
        if hasattr(viewer, "measure_toggle_btn"):
            viewer.measure_toggle_btn.configure(text="Start measure")
        viewer.unbind("<Escape>")
        viewer._measure_click_points = []
        viewer._update_status()

    @staticmethod
    def on_click(viewer, event) -> None:  # noqa: ANN001
        if event.inaxes is not viewer.ax2d:
            return
        mode = viewer._current_measure_mode_key()

        if event.button == 3:
            if mode == "polygon" and len(viewer._measure_click_points) >= 3:
                viewer._finalize_polygon_measure(viewer._measure_click_points)
            else:
                viewer._cancel_measure_mode()
            return

        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        viewer._measure_click_points.append((float(event.xdata), float(event.ydata)))

        if mode == "line":
            if len(viewer._measure_click_points) == 1:
                viewer.status.set("Measure line: click second point.")
                viewer._redraw_2d()
                return
            viewer._finalize_line_measure(viewer._measure_click_points[:2])
            return

        if event.dblclick and len(viewer._measure_click_points) >= 3:
            viewer._finalize_polygon_measure(viewer._measure_click_points)
            return

        viewer.status.set(
            f"Measure polygon: {len(viewer._measure_click_points)} vertex/vertices. "
            "Left-click to add more, right-click or double-click to close."
        )
        viewer._redraw_2d()


class MaskTool:
    @staticmethod
    def toggle(viewer) -> None:  # noqa: ANN001
        if viewer._section_mode:
            viewer._end_section_mode()
        if viewer._measure_mode:
            viewer._end_measure_mode()
        if viewer._mask_edit_mode:
            viewer._end_mask_edit_mode()
        if viewer._note_mode:
            viewer._end_note_mode()
        if viewer._mask_mode:
            viewer._end_mask_mode()
            viewer._redraw_2d()
            return
        viewer._mask_mode = True
        viewer._tool_dispatch.activate(TOOL_MASK)
        viewer._mask_click_points = []
        viewer.mask_btn.configure(text="Cancel mask")
        viewer.notebook.select(0)
        viewer.status.set(
            "Mask mode: left-click to add polygon vertices. "
            "Right-click (or double-click) to close. Esc to cancel."
        )
        viewer._mask_cid = viewer.canvas2d.mpl_connect("button_press_event", viewer._on_mask_click)
        viewer.bind("<Escape>", lambda _e: viewer._cancel_mask_mode())

    @staticmethod
    def cancel(viewer) -> None:  # noqa: ANN001
        viewer._mask_click_points = []
        viewer._end_mask_mode()
        viewer._redraw_2d()

    @staticmethod
    def end(viewer) -> None:  # noqa: ANN001
        viewer._mask_mode = False
        viewer._tool_dispatch.clear(TOOL_MASK)
        if viewer._mask_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._mask_cid)
            viewer._mask_cid = None
        viewer.mask_btn.configure(text="Draw mask")
        viewer.unbind("<Escape>")
        viewer._update_status()

    @staticmethod
    def on_click(viewer, event) -> None:  # noqa: ANN001
        if event.inaxes is not viewer.ax2d:
            return
        if event.button == 3:
            if len(viewer._mask_click_points) >= 3:
                viewer._append_mask(viewer._mask_click_points)
                viewer._mask_click_points = []
                viewer._end_mask_mode()
                if viewer.last_volume is not None:
                    viewer.volume_text.set("Volume: (stale – recompute)")
                viewer._refresh_mask_controls()
                viewer._redraw()
            else:
                viewer._cancel_mask_mode()
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return

        viewer._mask_click_points.append((event.xdata, event.ydata))
        if event.dblclick and len(viewer._mask_click_points) >= 3:
            viewer._append_mask(viewer._mask_click_points)
            viewer._mask_click_points = []
            viewer._end_mask_mode()
            if viewer.last_volume is not None:
                viewer.volume_text.set("Volume: (stale – recompute)")
            viewer._refresh_mask_controls()
            viewer._redraw()
            return

        viewer.status.set(
            f"Mask mode: {len(viewer._mask_click_points)} vertex/vertices. "
            "Left-click to add more, right-click or double-click to close."
        )
        viewer._redraw_2d()


class MaskEditTool:
    @staticmethod
    def toggle(viewer) -> None:  # noqa: ANN001
        if viewer._section_mode:
            viewer._end_section_mode()
        if viewer._measure_mode:
            viewer._end_measure_mode()
        if viewer._mask_mode:
            viewer._end_mask_mode()
        if viewer._note_mode:
            viewer._end_note_mode()
        if viewer._mask_edit_mode:
            viewer._end_mask_edit_mode()
            viewer._redraw_2d()
            return

        active = viewer._active_mask()
        if active is None or len(active.vertices) < 3:
            messagebox.showinfo("KOF Viewer", "Select a closed mask before editing.")
            return

        viewer._mask_edit_mode = True
        viewer._tool_dispatch.activate(TOOL_MASK_EDIT)
        viewer._mask_drag_vertex_index = None
        viewer._mask_changed_since_last_compute = False
        viewer.mask_edit_btn.configure(text="Finish mask edit")
        viewer.notebook.select(0)
        viewer.status.set("Mask edit: drag vertices on selected mask. Right-click or Esc to finish.")
        viewer._mask_edit_press_cid = viewer.canvas2d.mpl_connect("button_press_event", viewer._on_mask_edit_press)
        viewer._mask_edit_motion_cid = viewer.canvas2d.mpl_connect("motion_notify_event", viewer._on_mask_edit_motion)
        viewer._mask_edit_release_cid = viewer.canvas2d.mpl_connect("button_release_event", viewer._on_mask_edit_release)
        viewer.bind("<Escape>", lambda _e: viewer._end_mask_edit_mode())

    @staticmethod
    def end(viewer) -> None:  # noqa: ANN001
        viewer._mask_edit_mode = False
        viewer._tool_dispatch.clear(TOOL_MASK_EDIT)
        if viewer._mask_edit_press_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._mask_edit_press_cid)
            viewer._mask_edit_press_cid = None
        if viewer._mask_edit_motion_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._mask_edit_motion_cid)
            viewer._mask_edit_motion_cid = None
        if viewer._mask_edit_release_cid is not None:
            viewer.canvas2d.mpl_disconnect(viewer._mask_edit_release_cid)
            viewer._mask_edit_release_cid = None
        viewer._mask_drag_vertex_index = None
        viewer.mask_edit_btn.configure(text="Edit selected mask")
        viewer.unbind("<Escape>")
        if viewer._mask_changed_since_last_compute and viewer.last_volume is not None:
            viewer.volume_text.set("Volume: (stale – recompute)")
            viewer._clear_cached_mask_volume_results()
        viewer._mask_changed_since_last_compute = False
        viewer._refresh_mask_controls()
        viewer._update_status()

    @staticmethod
    def on_press(viewer, event) -> None:  # noqa: ANN001
        if not viewer._mask_edit_mode or event.inaxes is not viewer.ax2d:
            return
        if event.button == 3:
            viewer._end_mask_edit_mode()
            viewer._redraw_2d()
            return
        if event.button != 1:
            return
        if event.xdata is None or event.ydata is None:
            return
        active = viewer._active_mask()
        if active is None:
            return
        idx = viewer._pick_vertex_index(active.vertices, (event.xdata, event.ydata))
        if idx is not None:
            viewer._mask_drag_vertex_index = idx

    @staticmethod
    def on_motion(viewer, event) -> None:  # noqa: ANN001
        if not viewer._mask_edit_mode:
            return
        idx = viewer._mask_drag_vertex_index
        if idx is None:
            return
        if event.inaxes is not viewer.ax2d or event.xdata is None or event.ydata is None:
            return
        active = viewer._active_mask()
        if active is None or idx < 0 or idx >= len(active.vertices):
            return
        active.vertices[idx] = (float(event.xdata), float(event.ydata))
        viewer._clear_cached_mask_volume_results()
        viewer._mask_changed_since_last_compute = True
        viewer._redraw_2d()

    @staticmethod
    def on_release(viewer, event) -> None:  # noqa: ANN001
        if not viewer._mask_edit_mode:
            return
        if event.button == 1:
            viewer._mask_drag_vertex_index = None
