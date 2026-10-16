from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

import numpy as np

from kofvision.section_report import ProfileSample


_MATERIALS = ("rock", "moist", "constructiontc")


class _SurfaceLike(Protocol):
    def interpolate(self, y: np.ndarray, x: np.ndarray) -> np.ndarray:
        ...


@dataclass
class DitchRule:
    code_label: str
    side: int
    distance_m: float
    slope: float
    profile_start: int
    profile_end: int
    stop_material: Optional[str] = None
    stop_slot: Optional[int] = None

    def applies_to_profile(self, profile_index: int) -> bool:
        return self.profile_start <= profile_index <= self.profile_end


@dataclass
class DitchLayerCatalog:
    slots: dict[str, list[Optional[str]]] = field(
        default_factory=lambda: {name: [None] * 10 for name in _MATERIALS}
    )

    @classmethod
    def from_payload(cls, payload: object) -> "DitchLayerCatalog":
        catalog = cls()
        if not isinstance(payload, dict):
            return catalog
        for material in _MATERIALS:
            raw = payload.get(material)
            if not isinstance(raw, list):
                continue
            values: list[Optional[str]] = [None] * 10
            for idx, item in enumerate(raw[:10]):
                text = str(item).strip() if item not in (None, "") else ""
                values[idx] = text or None
            catalog.slots[material] = values
        return catalog

    def to_payload(self) -> dict[str, list[Optional[str]]]:
        return {material: list(values[:10]) for material, values in self.slots.items()}

    def surface_name(self, material: str, slot: Optional[int]) -> Optional[str]:
        if material not in self.slots or slot is None:
            return None
        if slot < 0 or slot >= 10:
            return None
        return self.slots[material][slot]


@dataclass
class DitchProfileAdjustment:
    area_net_m2: float
    area_cut_m2: float
    area_fill_m2: float
    applied_rule_codes: list[str]
    hit_stop_layers: list[str]


_AREA_FIELDS = (
    "area_upper_leg_m2",
    "area_upper_leg_cut_m2",
    "area_upper_leg_fill_m2",
    "area_lower_leg_m2",
    "area_lower_leg_cut_m2",
    "area_lower_leg_fill_m2",
)


def parse_ditch_rules(payload: object) -> list[DitchRule]:
    if not isinstance(payload, list):
        return []
    parsed: list[DitchRule] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        code_label = str(item.get("code_label", "")).strip()
        if not code_label:
            continue
        side_raw = item.get("side", 1)
        try:
            side = int(side_raw)
        except (TypeError, ValueError):
            side = 1
        side = -1 if side < 0 else 1

        try:
            distance_m = float(item.get("distance_m", 0.0))
        except (TypeError, ValueError):
            continue
        try:
            slope = float(item.get("slope", 0.0))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(distance_m) or distance_m <= 0:
            continue
        if not np.isfinite(slope):
            continue

        try:
            profile_start = int(item.get("profile_start", 1))
            profile_end = int(item.get("profile_end", profile_start))
        except (TypeError, ValueError):
            continue
        profile_start = max(1, profile_start)
        profile_end = max(profile_start, profile_end)

        stop_material_raw = str(item.get("stop_material", "")).strip().lower()
        stop_material = stop_material_raw if stop_material_raw in _MATERIALS else None
        stop_slot_raw = item.get("stop_slot")
        stop_slot: Optional[int] = None
        if stop_slot_raw not in (None, ""):
            try:
                stop_slot = int(stop_slot_raw)
            except (TypeError, ValueError):
                stop_slot = None
            if stop_slot is not None and (stop_slot < 0 or stop_slot > 9):
                stop_slot = None

        parsed.append(
            DitchRule(
                code_label=code_label,
                side=side,
                distance_m=float(distance_m),
                slope=float(slope),
                profile_start=profile_start,
                profile_end=profile_end,
                stop_material=stop_material,
                stop_slot=stop_slot,
            )
        )
    return parsed


def ditch_rules_to_payload(rules: list[DitchRule]) -> list[dict[str, Any]]:
    return [
        {
            "code_label": rule.code_label,
            "side": int(rule.side),
            "distance_m": float(rule.distance_m),
            "slope": float(rule.slope),
            "profile_start": int(rule.profile_start),
            "profile_end": int(rule.profile_end),
            "stop_material": rule.stop_material,
            "stop_slot": rule.stop_slot,
        }
        for rule in rules
    ]


def _line_sample_points(sample: ProfileSample) -> tuple[np.ndarray, np.ndarray]:
    y0, x0 = sample.start
    y1, x1 = sample.end
    if sample.length_m <= 0:
        ts = np.zeros_like(sample.dists_m)
    else:
        ts = np.asarray(sample.dists_m, dtype=float) / float(sample.length_m)
    ys = y0 + (y1 - y0) * ts
    xs = x0 + (x1 - x0) * ts
    return ys, xs


def _surface_for_rule(
    rule: DitchRule,
    catalog: DitchLayerCatalog,
    surface_lookup: Mapping[str, _SurfaceLike],
) -> Optional[_SurfaceLike]:
    if rule.stop_material is None:
        return None
    surface_name = catalog.surface_name(rule.stop_material, rule.stop_slot)
    if not surface_name:
        return None
    return surface_lookup.get(surface_name)


def _apply_side_rules(
    current_z: np.ndarray,
    sample: ProfileSample,
    side_rules: list[DitchRule],
    catalog: DitchLayerCatalog,
    surface_lookup: Mapping[str, _SurfaceLike],
    hit_stop_layers: list[str],
) -> np.ndarray:
    if not side_rules:
        return current_z

    center_dist = float(sample.length_m) * 0.5
    rel = np.asarray(sample.dists_m, dtype=float) - center_dist
    ys, xs = _line_sample_points(sample)
    z_next = np.array(current_z, dtype=float, copy=True)

    running_offset = 0.0
    center_idx = int(np.argmin(np.abs(rel))) if len(rel) else 0
    running_elevation = float(z_next[center_idx]) if len(z_next) else float("nan")

    for rule in side_rules:
        if not np.isfinite(running_elevation):
            break
        end_offset = running_offset + float(rule.distance_m)
        side_rel = rel * float(rule.side)
        mask = (side_rel >= running_offset) & (side_rel <= end_offset)
        if not np.any(mask):
            running_offset = end_offset
            running_elevation = running_elevation + (rule.slope * rule.distance_m)
            continue

        local_u = np.clip(side_rel[mask] - running_offset, 0.0, rule.distance_m)
        target = running_elevation + rule.slope * local_u

        stop_surface = _surface_for_rule(rule, catalog, surface_lookup)
        if stop_surface is not None:
            z_stop = stop_surface.interpolate(ys[mask], xs[mask])
            stop_hit = np.zeros_like(target, dtype=bool)
            if rule.slope < 0:
                stop_hit = np.isfinite(z_stop) & (target < z_stop)
            elif rule.slope > 0:
                stop_hit = np.isfinite(z_stop) & (target > z_stop)
            if np.any(stop_hit):
                target = np.where(stop_hit, z_stop, target)
                hit_stop_layers.append(f"{rule.stop_material}:{int(rule.stop_slot or 0)}")
                first_stop_idx = int(np.argmax(stop_hit))
                frozen = float(target[first_stop_idx])
                freeze_mask = side_rel > side_rel[mask][first_stop_idx]
                z_next[freeze_mask] = frozen
                z_next[mask] = target
                running_offset = end_offset
                running_elevation = frozen
                continue

        z_next[mask] = target
        running_offset = end_offset
        running_elevation = running_elevation + (rule.slope * rule.distance_m)

    return z_next


def apply_ditch_rules_to_profile(
    sample: ProfileSample,
    profile_index: int,
    rules: list[DitchRule],
    catalog: DitchLayerCatalog,
    surface_lookup: Mapping[str, _SurfaceLike],
) -> DitchProfileAdjustment:
    applicable = [r for r in rules if r.applies_to_profile(profile_index)]
    if not applicable:
        return DitchProfileAdjustment(
            area_net_m2=float(sample.area_net_m2),
            area_cut_m2=float(sample.area_cut_m2),
            area_fill_m2=float(sample.area_fill_m2),
            applied_rule_codes=[],
            hit_stop_layers=[],
        )

    z_top_adj = np.array(sample.z_area_top, dtype=float, copy=True)
    hit_stop_layers: list[str] = []
    for side in (-1, 1):
        side_rules = [r for r in applicable if int(np.sign(r.side)) == side]
        if side_rules:
            z_top_adj = _apply_side_rules(
                z_top_adj,
                sample,
                side_rules,
                catalog,
                surface_lookup,
                hit_stop_layers,
            )

    diff = z_top_adj - np.asarray(sample.z_area_bottom, dtype=float)
    valid = np.isfinite(diff)
    if not valid.any():
        return DitchProfileAdjustment(
            area_net_m2=float("nan"),
            area_cut_m2=float("nan"),
            area_fill_m2=float("nan"),
            applied_rule_codes=[r.code_label for r in applicable],
            hit_stop_layers=hit_stop_layers,
        )

    d_valid = np.asarray(sample.dists_m, dtype=float)[valid]
    diff_valid = diff[valid]
    area_net = float(np.trapezoid(diff_valid, d_valid))
    area_cut = float(np.trapezoid(np.clip(diff_valid, 0.0, None), d_valid))
    area_fill = float(-np.trapezoid(np.clip(diff_valid, None, 0.0), d_valid))

    return DitchProfileAdjustment(
        area_net_m2=area_net,
        area_cut_m2=area_cut,
        area_fill_m2=area_fill,
        applied_rule_codes=[r.code_label for r in applicable],
        hit_stop_layers=hit_stop_layers,
    )


def clear_split_leg_area_fields(row: dict[str, Any]) -> None:
    # Ditch adjustments currently apply to the main area pair only.
    for field in _AREA_FIELDS:
        row[field] = None
