from __future__ import annotations

from typing import Any

import numpy as np


def integrate_profile_areas_to_volume(stations_m: list[float], areas_m2: list[float]) -> float:
    if len(stations_m) < 2 or len(areas_m2) < 2:
        return float("nan")
    s = np.asarray(stations_m, dtype=float)
    a = np.asarray(areas_m2, dtype=float)
    valid = np.isfinite(s) & np.isfinite(a)
    if np.count_nonzero(valid) < 2:
        return float("nan")
    s_valid = s[valid]
    a_valid = a[valid]
    order = np.argsort(s_valid)
    return float(np.trapezoid(a_valid[order], s_valid[order]))


def cumulative_profile_volumes(stations_m: list[float], areas_m2: list[float]) -> list[float]:
    n = min(len(stations_m), len(areas_m2))
    if n == 0:
        return []

    s = np.asarray(stations_m[:n], dtype=float)
    a = np.asarray(areas_m2[:n], dtype=float)
    order = np.argsort(s)
    s_sorted = s[order]
    a_sorted = a[order]

    cumulative_sorted = np.full(n, np.nan, dtype=float)
    cumulative_sorted[0] = 0.0
    for i in range(1, n):
        s0 = s_sorted[i - 1]
        s1 = s_sorted[i]
        a0 = a_sorted[i - 1]
        a1 = a_sorted[i]
        prev = cumulative_sorted[i - 1]
        if np.isfinite(prev) and np.isfinite(s0) and np.isfinite(s1) and np.isfinite(a0) and np.isfinite(a1):
            cumulative_sorted[i] = prev + (0.5 * (a0 + a1) * (s1 - s0))

    cumulative = np.full(n, np.nan, dtype=float)
    cumulative[order] = cumulative_sorted
    return [float(v) for v in cumulative]


def apply_cumulative_profile_volumes(profile_rows: list[dict[str, Any]]) -> None:
    stations = [float(r["station_m"]) for r in profile_rows]

    def _field(field: str) -> list[float]:
        out: list[float] = []
        for row in profile_rows:
            raw = row.get(field)
            out.append(float(raw) if raw not in (None, "") else float("nan"))
        return out

    volume_fields = {
        "area_net_m2": "volume_net_m3",
        "area_cut_m2": "volume_cut_m3",
        "area_fill_m2": "volume_fill_m3",
        "area_upper_leg_m2": "volume_upper_leg_m3",
        "area_upper_leg_cut_m2": "volume_upper_leg_cut_m3",
        "area_upper_leg_fill_m2": "volume_upper_leg_fill_m3",
        "area_lower_leg_m2": "volume_lower_leg_m3",
        "area_lower_leg_cut_m2": "volume_lower_leg_cut_m3",
        "area_lower_leg_fill_m2": "volume_lower_leg_fill_m3",
    }

    computed = {
        out_field: cumulative_profile_volumes(stations, _field(in_field))
        for in_field, out_field in volume_fields.items()
    }

    for i, row in enumerate(profile_rows):
        for out_field, values in computed.items():
            row[out_field] = values[i] if i < len(values) else float("nan")
