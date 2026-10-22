from __future__ import annotations

from types import SimpleNamespace

from kof_viewer import KOFViewer
from kofvision.ditch import DitchLayerCatalog
from kofvision.pit import PitPolygon, PitSettings


class _Var:
    def __init__(self, value):  # noqa: ANN001
        self._value = value

    def get(self):  # noqa: ANN201
        return self._value

    def set(self, value):  # noqa: ANN001
        self._value = value


class _Combo:
    def __init__(self) -> None:
        self.values = []

    def configure(self, **kwargs) -> None:  # noqa: ANN003
        if "values" in kwargs:
            self.values = list(kwargs["values"])


class _Listbox:
    def __init__(self) -> None:
        self._items: list[str] = []
        self._selected: tuple[int, ...] = ()

    def delete(self, start, end=None) -> None:  # noqa: ANN001, ANN202
        self._items.clear()
        self._selected = ()

    def insert(self, _idx, value: str) -> None:  # noqa: ANN001
        self._items.append(value)

    def size(self) -> int:
        return len(self._items)

    def curselection(self):  # noqa: ANN201
        return self._selected

    def get(self, index: int) -> str:
        return self._items[index]

    def select(self, index: int) -> None:
        self._selected = (index,)


class _DummyViewer:
    _volume_surface_choices = KOFViewer._volume_surface_choices
    _refresh_volume_stop_mapping_controls = KOFViewer._refresh_volume_stop_mapping_controls
    _parse_volume_stop_mapping_selection = KOFViewer._parse_volume_stop_mapping_selection
    _on_volume_stop_mapping_select = KOFViewer._on_volume_stop_mapping_select
    _on_assign_volume_stop_mapping = KOFViewer._on_assign_volume_stop_mapping
    _on_clear_selected_volume_stop_mapping = KOFViewer._on_clear_selected_volume_stop_mapping
    _on_pit_settings_change = KOFViewer._on_pit_settings_change

    def __init__(self) -> None:
        self.files = [SimpleNamespace(name="surface_a"), SimpleNamespace(name="surface_b")]
        self._raster_layers = [SimpleNamespace(path=r"C:\dem\elev.tif", elevation_surface=SimpleNamespace(name="elev"))]
        self._ditch_layer_catalog = DitchLayerCatalog()
        self._ditch_layer_catalog.slots["rock"][0] = "surface_a"
        self.volume_stop_surface_combo = _Combo()
        self.volume_stop_mapping_list = _Listbox()
        self.volume_stop_surface_var = _Var("")
        self.volume_stop_material_var = _Var("rock")
        self.volume_stop_slot_var = _Var("0")
        self.last_volume = object()
        self.volume_text = _Var("Volume: ok")
        self.pit_edge_slope_var = _Var(2.0)
        self.pit_vertical_wall_var = _Var(False)
        self.pit_stop_material_var = _Var("rock")
        self.pit_stop_slot_var = _Var("0")
        self._pit_settings = PitSettings(edge_slope=1.0, vertical_wall=False, stop_material=None, stop_slot=None)
        self.refresh_calls = 0
        self.redraw_calls = 0

    def _refresh_pit_controls(self) -> None:
        self.refresh_calls += 1

    def _redraw_2d(self) -> None:
        self.redraw_calls += 1


class _DummyPitSelectionViewer:
    _resolve_active_pit_index = KOFViewer._resolve_active_pit_index
    _active_pit = KOFViewer._active_pit
    _pit_indices_for_compute = KOFViewer._pit_indices_for_compute

    def __init__(self) -> None:
        self._pit_polygons = [
            PitPolygon(name="Pit A", vertices=[(0.0, 0.0), (0.0, 1.0), (1.0, 1.0)], target_z=10.0, visible=True),
            PitPolygon(name="Pit B", vertices=[(2.0, 2.0), (2.0, 3.0), (3.0, 3.0)], target_z=11.0, visible=True),
        ]
        self._active_pit_index = None
        self.active_pit_var = _Var("Pit B")
        self._pit_visibility_vars = [_Var(True), _Var(True)]

    def _pit_is_visible(self, index: int) -> bool:
        return bool(self._pit_visibility_vars[index].get())


def test_refresh_volume_stop_mapping_controls_populates_choices_and_rows() -> None:
    viewer = _DummyViewer()
    viewer._refresh_volume_stop_mapping_controls()
    assert viewer.volume_stop_surface_combo.values == ["", "surface_a", "surface_b", r"Raster: C:\dem\elev.tif"]
    assert viewer.volume_stop_mapping_list.size() == 1
    assert viewer.volume_stop_mapping_list.get(0) == "rock[0] -> surface_a"


def test_assign_and_clear_volume_stop_mapping() -> None:
    viewer = _DummyViewer()
    viewer._refresh_volume_stop_mapping_controls()
    viewer.volume_stop_material_var.set("moist")
    viewer.volume_stop_slot_var.set("2")
    viewer.volume_stop_surface_var.set(r"Raster: C:\dem\elev.tif")
    viewer._on_assign_volume_stop_mapping()
    assert viewer._ditch_layer_catalog.slots["moist"][2] == r"Raster: C:\dem\elev.tif"

    viewer._refresh_volume_stop_mapping_controls()
    viewer.volume_stop_mapping_list.select(1)
    viewer._on_clear_selected_volume_stop_mapping()
    assert viewer._ditch_layer_catalog.slots["moist"][2] is None


def test_pit_settings_change_marks_volume_stale_and_redraws() -> None:
    viewer = _DummyViewer()
    viewer._on_pit_settings_change()
    assert viewer._pit_settings.edge_slope == 2.0
    assert viewer._pit_settings.stop_material == "rock"
    assert viewer._pit_settings.stop_slot == 0
    assert viewer.volume_text.get() == "Volume: (stale – recompute)"
    assert viewer.refresh_calls == 1
    assert viewer.redraw_calls == 1


def test_selected_pit_name_recovers_active_pit_index() -> None:
    viewer = _DummyPitSelectionViewer()
    assert viewer._active_pit() is not None
    assert viewer._active_pit().name == "Pit B"
    assert viewer._active_pit_index == 1
    assert viewer._pit_indices_for_compute("selected") == [1]
