from __future__ import annotations

from dataclasses import dataclass
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
            viewer.ax2d.plot([y0], [x0], marker="x", color="black", markersize=8)
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

        action, title, text = viewer._edit_note_dialog(title_value="", text_value="", allow_delete=False)
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
