from __future__ import annotations

from dataclasses import dataclass

from kofvision.viewer_serialization import (
    PROJECT_TYPE,
    PROJECT_VERSION,
    ProjectPayloadError,
    build_project_payload,
    validate_project_payload,
)


class _Var:
    def __init__(self, value):  # noqa: ANN001
        self._value = value

    def get(self):
        return self._value


@dataclass
class _DummyRuntime:
    volume_history: list[str]


class _DummyViewer:
    def __init__(self) -> None:
        self._surface_source_paths = []
        self._surface_owner_paths = []
        self.files = []
        self.layer_vars = []
        self._landxml_group_vars = {}
        self._raster_layers = []
        self._elevation_raster_index = None
        self.show_raster = _Var(False)
        self.top_var = _Var("")
        self.bottom_var = _Var("")
        self.volume_source_mode = _Var("kof_vs_kof")
        self.volume_method = _Var("raster")
        self.intermediate_type = _Var("none")
        self.intermediate_kof_var = _Var("")
        self.intermediate_raster_var = _Var("")
        self.intermediate_constant_z = _Var(0.0)
        self.mask_compute_scope = _Var("selected")
        self._sections = []
        self._ditch_centerlines = [((1.0, 2.0), (3.0, 4.0))]
        self._active_ditch_centerline_index = 0
        self._masks = []
        self.active_mask_var = _Var("")
        self.runtime_state = _DummyRuntime(volume_history=[])
        self.theme_mode = _Var("dark")
        self.station_interval_m = _Var(10.0)
        self.station_label_format = _Var("plain")
        self.show_labels = _Var(False)
        self.show_markers = _Var(True)
        self.show_lines = _Var(True)
        self.show_triangulation = _Var(False)
        self._notes = []
        self._ditch_rules = [
            type("_Rule", (), {
                "code_label": "1.1",
                "side": 1,
                "distance_m": 2.0,
                "slope": -1.0,
                "profile_start": 1,
                "profile_end": 10,
                "stop_material": "rock",
                "stop_slot": 0,
            })()
        ]
        self._ditch_layer_catalog = type("_Catalog", (), {
            "to_payload": staticmethod(lambda: {"rock": ["s0"] + [None] * 9, "moist": [None] * 10, "constructiontc": [None] * 10})
        })()


def test_validate_project_payload_accepts_valid_payload() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": PROJECT_VERSION,
        "source_files": [],
    }
    assert validate_project_payload(payload) is payload


def test_validate_project_payload_rejects_wrong_type() -> None:
    payload = {
        "type": "other.project",
        "version": PROJECT_VERSION,
    }
    try:
        validate_project_payload(payload)
    except ProjectPayloadError as exc:
        assert "Invalid project file type" in str(exc)
    else:
        raise AssertionError("Expected ProjectPayloadError")


def test_validate_project_payload_rejects_wrong_version() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": 999,
    }
    try:
        validate_project_payload(payload)
    except ProjectPayloadError as exc:
        assert "Unsupported project version" in str(exc)
    else:
        raise AssertionError("Expected ProjectPayloadError")


def test_validate_project_payload_migrates_v1_payload() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": 1,
        "source_files": [],
    }
    validated = validate_project_payload(payload)
    assert validated["version"] == PROJECT_VERSION
    assert "volume_history" in validated
    assert "saved_preferences" in validated


def test_build_project_payload_includes_ditch_data() -> None:
    payload = build_project_payload(_DummyViewer())
    assert payload["ditch_centerlines"]
    assert payload["active_ditch_centerline_index"] == 0
    assert payload["ditch_rules"][0]["code_label"] == "1.1"
    assert payload["ditch_layer_catalog"]["rock"][0] == "s0"
