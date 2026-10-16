from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kofvision.ditch import (
    DitchLayerCatalog,
    DitchRule,
    apply_ditch_rules_to_profile,
    parse_ditch_rules,
)
from kofvision.section_report import ProfileSample


@dataclass
class _FlatSurface:
    z: float

    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:  # noqa: ARG002
        return np.full_like(np.asarray(y, dtype=float), self.z)


def _make_sample() -> ProfileSample:
    dists = np.linspace(0.0, 10.0, 11)
    z_top = np.full_like(dists, 10.0)
    z_bottom = np.full_like(dists, 0.0)
    return ProfileSample(
        profile_id="P0001",
        station_m=0.0,
        center=(5.0, 0.0),
        start=(0.0, 0.0),
        end=(10.0, 0.0),
        length_m=10.0,
        dists_m=dists,
        z_top=z_top,
        z_bottom=z_bottom,
        z_reference=None,
        reference_name=None,
        z_secondary_reference=None,
        secondary_reference_name=None,
        z_area_top=z_top.copy(),
        z_area_bottom=z_bottom.copy(),
        area_top_name="top",
        area_bottom_name="bottom",
        area_net_m2=100.0,
        area_cut_m2=100.0,
        area_fill_m2=0.0,
        area_upper_leg_m2=None,
        area_upper_leg_cut_m2=None,
        area_upper_leg_fill_m2=None,
        area_lower_leg_m2=None,
        area_lower_leg_cut_m2=None,
        area_lower_leg_fill_m2=None,
        valid_fraction=1.0,
    )


def test_parse_ditch_rules_filters_invalid_entries() -> None:
    rules = parse_ditch_rules(
        [
            {"code_label": "1.1", "distance_m": 2.0, "slope": -1.0, "profile_start": 1, "profile_end": 10},
            {"code_label": "", "distance_m": 2.0, "slope": -1.0},
            {"code_label": "bad", "distance_m": -5.0, "slope": -1.0},
        ]
    )
    assert len(rules) == 1
    assert rules[0].code_label == "1.1"


def test_profile_range_controls_rule_application() -> None:
    sample = _make_sample()
    rules = [
        DitchRule(
            code_label="1.1",
            side=1,
            distance_m=4.0,
            slope=-1.0,
            profile_start=2,
            profile_end=4,
        )
    ]
    catalog = DitchLayerCatalog()

    outside = apply_ditch_rules_to_profile(sample, 1, rules, catalog, {})
    inside = apply_ditch_rules_to_profile(sample, 3, rules, catalog, {})

    assert np.isclose(outside.area_net_m2, 100.0)
    assert inside.area_net_m2 < outside.area_net_m2
    assert inside.applied_rule_codes == ["1.1"]


def test_layer_stop_prevents_crossing_stop_surface() -> None:
    sample = _make_sample()
    rules = [
        DitchRule(
            code_label="-2.1",
            side=-1,
            distance_m=4.0,
            slope=-2.0,
            profile_start=1,
            profile_end=10,
            stop_material="rock",
            stop_slot=0,
        )
    ]
    catalog = DitchLayerCatalog()
    catalog.slots["rock"][0] = "rock_0"

    no_stop = apply_ditch_rules_to_profile(sample, 1, rules, catalog, {})
    with_stop = apply_ditch_rules_to_profile(sample, 1, rules, catalog, {"rock_0": _FlatSurface(8.0)})

    assert with_stop.area_net_m2 > no_stop.area_net_m2
    assert with_stop.hit_stop_layers


def test_profile_range_switch_between_rule_sets() -> None:
    sample = _make_sample()
    rules = [
        DitchRule(
            code_label="1.1",
            side=1,
            distance_m=2.0,
            slope=-1.0,
            profile_start=1,
            profile_end=10,
        ),
        DitchRule(
            code_label="2.1",
            side=1,
            distance_m=4.0,
            slope=-1.0,
            profile_start=11,
            profile_end=20,
        ),
    ]
    catalog = DitchLayerCatalog()

    first_range = apply_ditch_rules_to_profile(sample, 5, rules, catalog, {})
    second_range = apply_ditch_rules_to_profile(sample, 15, rules, catalog, {})

    assert first_range.applied_rule_codes == ["1.1"]
    assert second_range.applied_rule_codes == ["2.1"]
    assert second_range.area_net_m2 < first_range.area_net_m2


def test_stop_layer_freezes_extension_after_hit() -> None:
    sample = _make_sample()
    rules = [
        DitchRule(
            code_label="1.1",
            side=1,
            distance_m=4.0,
            slope=-2.0,
            profile_start=1,
            profile_end=10,
            stop_material="rock",
            stop_slot=0,
        ),
        DitchRule(
            code_label="2.1",
            side=1,
            distance_m=2.0,
            slope=-3.0,
            profile_start=1,
            profile_end=10,
            stop_material="rock",
            stop_slot=0,
        ),
    ]
    catalog = DitchLayerCatalog()
    catalog.slots["rock"][0] = "rock_0"

    with_stop = apply_ditch_rules_to_profile(sample, 1, rules, catalog, {"rock_0": _FlatSurface(8.0)})
    first_rule_only = apply_ditch_rules_to_profile(sample, 1, rules[:1], catalog, {"rock_0": _FlatSurface(8.0)})

    assert with_stop.applied_rule_codes == ["1.1", "2.1"]
    assert with_stop.hit_stop_layers
    assert np.isclose(with_stop.area_net_m2, first_rule_only.area_net_m2)
