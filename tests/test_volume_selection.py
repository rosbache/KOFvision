from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from kof_viewer import KOFViewer, VOLUME_MODE_LABELS
from kofvision.raster import ElevationRasterSurface
from kofvision.raster_management import RasterLayerManager
from kofvision.volume import ConstantElevationSurface, SplitVolumeResult
from kofvision.volume_controller import combine_volume_results, compute_volume_for_mask
from kofvision import viewer_serialization


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Executor:
    def submit(self, *, task, on_success, on_error):
        on_success(task())


class _Viewer:
    _volume_surface_choices = KOFViewer._volume_surface_choices
    _find_volume_surface = KOFViewer._find_volume_surface
    _find_file = KOFViewer._find_file
    _refresh_file_selectors = KOFViewer._refresh_file_selectors
    _current_volume_mode_key = KOFViewer._current_volume_mode_key
    _current_intermediate_type_key = KOFViewer._current_intermediate_type_key
    _resolve_intermediate_surface = KOFViewer._resolve_intermediate_surface
    _resolve_intermediate_surface_with_feedback = KOFViewer._resolve_intermediate_surface_with_feedback
    _section_reference_raster = KOFViewer._section_reference_raster
    _section_intermediate_surface = KOFViewer._section_intermediate_surface
    _on_compute_volume = KOFViewer._on_compute_volume

    def __init__(self, method="triangles"):
        self.files = [ConstantElevationSurface("vector", 10.0, (0.0, 0.0, 10.0, 4.0))]
        self._raster_layers = [
            SimpleNamespace(
                path=path,
                elevation_surface=ElevationRasterSurface(
                    path, np.full((3, 3), height), (0.0, 10.0, 0.0, 4.0), "upper"
                ),
            )
            for path, height in [(r"C:\dem\top.tif", 12.0), (r"C:\dem\bottom.tif", 7.0)]
        ]
        self._raster_layers.append(SimpleNamespace(path=r"C:\ortho.tif", elevation_surface=None))
        self._elevation_raster_index = 0
        self._active_raster_index = 0
        self.top_var = _Var("vector")
        self.bottom_var = _Var(r"Raster: C:\dem\bottom.tif")
        self.intermediate_kof_var = _Var()
        self.intermediate_raster_var = _Var()
        self.top_combo = {}
        self.bottom_combo = {}
        self.intermediate_kof_combo = {}
        self.intermediate_raster_combo = {}
        self._intermediate_raster_index_map = {}
        self.volume_source_mode = _Var(VOLUME_MODE_LABELS["surfaces"])
        self.volume_method = _Var(method)
        self.intermediate_type = _Var("none")
        self._volume_task_running = False
        self.status = _Var()
        self._task_executor = _Executor()
        self.result = None

    def _current_mask_scope_key(self):
        return "selected"

    def _mask_indices_for_compute(self, scope):
        return []

    def _active_mask_polygon(self):
        return None

    def _compute_volume_payload(self, **kwargs):
        self.effective_method = kwargs["effective_method"]
        results = [
            compute_volume_for_mask(
                kwargs["top_surface"],
                kwargs["bottom_surface"],
                kwargs["intermediate_surface"],
                mask_polygon=polygon,
                method=kwargs["effective_method"],
            )
            for polygon in (kwargs["mask_compute_polygons"] or [None])
        ]
        return {
            "result": combine_volume_results([result for result in results if result is not None])
        }

    def _apply_volume_compute_payload(self, *, payload, **kwargs):
        self.result = payload["result"]

    def _update_status(self):
        pass


def test_selectors_include_dems_but_not_orthophotos_and_keep_selection():
    viewer = _Viewer()
    viewer._refresh_file_selectors()
    assert list(viewer.top_combo["values"]) == [
        "vector", r"Raster: C:\dem\top.tif", r"Raster: C:\dem\bottom.tif"
    ]
    assert viewer.bottom_var.get() == r"Raster: C:\dem\bottom.tif"
    assert viewer._find_volume_surface(viewer.bottom_var.get()) is viewer._raster_layers[1].elevation_surface
    assert viewer._find_file(viewer.bottom_var.get()) is None
    viewer._raster_layers.reverse()
    viewer._refresh_file_selectors()
    assert viewer.bottom_var.get() == r"Raster: C:\dem\bottom.tif"
    assert viewer._find_volume_surface(viewer.bottom_var.get()) is viewer._raster_layers[1].elevation_surface
    viewer._raster_layers.pop(1)
    viewer._refresh_file_selectors()
    assert viewer.bottom_var.get() in viewer.bottom_combo["values"]


def test_dem_only_selectors_and_raster_manager_refresh():
    viewer = _Viewer()
    viewer.files.clear()
    manager = RasterLayerManager(viewer)
    manager.refresh_intermediate_raster_selector()
    assert len(viewer.top_combo["values"]) == 2
    assert viewer.top_var.get() == r"Raster: C:\dem\top.tif"
    assert viewer.bottom_var.get() == r"Raster: C:\dem\bottom.tif"
    assert viewer.intermediate_kof_var.get() == ""


def test_same_raster_filename_in_different_folders_is_unambiguous():
    viewer = _Viewer()
    viewer._raster_layers[0].path = r"C:\survey_a\dem.tif"
    viewer._raster_layers[1].path = r"C:\survey_b\dem.tif"
    viewer._refresh_file_selectors()
    assert viewer._find_volume_surface(r"Raster: C:\survey_a\dem.tif") is viewer._raster_layers[0].elevation_surface
    assert viewer._find_volume_surface(r"Raster: C:\survey_b\dem.tif") is viewer._raster_layers[1].elevation_surface


@pytest.mark.parametrize("method", ["raster", "triangles"])
@pytest.mark.parametrize("dem_only", [False, True])
def test_compute_handler_uses_selected_surfaces_independent_of_method(method, dem_only):
    viewer = _Viewer(method)
    if dem_only:
        viewer.top_var.set(r"Raster: C:\dem\top.tif")
        viewer.files.clear()
    viewer._on_compute_volume()
    assert viewer.result is not None
    assert viewer.result.area == pytest.approx(40.0)
    assert viewer.result.net == pytest.approx(200.0 if dem_only else 120.0)
    assert not viewer._volume_task_running


def test_triangle_handler_allows_raster_intermediate_and_section_preview():
    viewer = _Viewer()
    viewer.top_var.set(r"Raster: C:\dem\top.tif")
    viewer.intermediate_type.set("kof")
    viewer.intermediate_kof_var.set("vector")
    viewer._on_compute_volume()
    assert isinstance(viewer.result, SplitVolumeResult)
    assert viewer.result.upper.net == pytest.approx(80.0)
    assert viewer.result.lower.net == pytest.approx(120.0)
    assert viewer._section_intermediate_surface() is viewer.files[0]
    assert viewer._section_reference_raster() is viewer._raster_layers[0].elevation_surface

    viewer.top_var.set("vector")
    viewer.bottom_var.set(r"Raster: C:\dem\top.tif")
    viewer.intermediate_type.set("raster")
    viewer.intermediate_raster_var.set("middle")
    viewer._intermediate_raster_index_map["middle"] = 1
    viewer._on_compute_volume()
    assert isinstance(viewer.result, SplitVolumeResult)
    assert viewer.result.upper.net == pytest.approx(120.0)
    assert viewer.result.lower.net == pytest.approx(-200.0)
    assert viewer._section_intermediate_surface() is viewer._raster_layers[1].elevation_surface


@pytest.mark.parametrize("mode", ["kof_vs_kof", "top_minus_raster", "raster_minus_bottom", "raster_minus_top"])
def test_legacy_modes_still_resolve_with_triangles(mode):
    viewer = _Viewer()
    viewer.volume_source_mode.set(VOLUME_MODE_LABELS[mode])
    viewer._on_compute_volume()
    assert viewer.result is not None
    expected = {"kof_vs_kof": 120.0, "top_minus_raster": -80.0, "raster_minus_bottom": 200.0, "raster_minus_top": 80.0}
    assert viewer.result.net == pytest.approx(expected[mode])


def test_union_mask_keeps_selected_triangle_method():
    viewer = _Viewer()
    viewer._current_mask_scope_key = lambda: "all_union"
    viewer._mask_indices_for_compute = lambda scope: [0, 1]
    viewer._polygon_from_geom = KOFViewer._polygon_from_geom
    viewer._masks = [
        SimpleNamespace(vertices=[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]),
        SimpleNamespace(vertices=[(2.0, 0.0), (6.0, 0.0), (6.0, 4.0), (2.0, 4.0)]),
    ]
    viewer._on_compute_volume()
    assert viewer.effective_method == "triangles"
    assert viewer.result is not None
    assert viewer.result.area == pytest.approx(24.0)
    assert viewer.result.net == pytest.approx(72.0)


def test_project_load_restores_dem_selections_and_triangle_split(monkeypatch):
    viewer = _Viewer()
    monkeypatch.setattr(viewer_serialization, "reset_for_project_load", lambda _viewer: None)
    for name in (
        "_refresh_intermediate_raster_selector", "_refresh_raster_selector", "_refresh_mask_controls",
        "_refresh_section_controls", "_refresh_layer_controls", "_on_intermediate_type_change", "_redraw",
    ):
        setattr(viewer, name, lambda: None)
    viewer.show_raster = _Var(True)
    viewer._sections = []
    viewer._ditch_centerlines = []
    viewer._masks = []
    viewer._pit_polygons = []
    viewer.intermediate_constant_z = _Var(0.0)
    viewer_serialization.apply_project_payload(
        viewer,
        {
            "top_surface": r"Raster: C:\dem\top.tif",
            "bottom_surface": r"Raster: C:\dem\bottom.tif",
            "volume_source_mode": VOLUME_MODE_LABELS["surfaces"],
            "volume_method": "triangles",
            "intermediate_type": "kof",
            "intermediate_kof": "vector",
        },
        allowed_volume_modes=set(VOLUME_MODE_LABELS.values()),
        allowed_intermediate_types={"kof"},
        allowed_mask_scopes={"selected"},
    )
    assert viewer.top_var.get() == r"Raster: C:\dem\top.tif"
    assert viewer.bottom_var.get() == r"Raster: C:\dem\bottom.tif"
    viewer._on_compute_volume()
    assert isinstance(viewer.result, SplitVolumeResult)
    assert viewer.result.net == pytest.approx(200.0)
