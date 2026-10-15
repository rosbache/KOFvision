from __future__ import annotations

import numpy as np

from kofvision.report_service import cumulative_profile_volumes, integrate_profile_areas_to_volume


def test_constant_area_integrates_to_expected_volume() -> None:
    stations = [0.0, 10.0, 20.0, 30.0]
    areas = [2.0, 2.0, 2.0, 2.0]
    vol = integrate_profile_areas_to_volume(stations, areas)
    assert np.isclose(vol, 60.0)


def test_cumulative_volume_is_monotonic_for_positive_areas() -> None:
    rng = np.random.default_rng(42)
    stations = np.sort(rng.uniform(0.0, 200.0, size=25)).tolist()
    areas = rng.uniform(0.1, 12.0, size=25).tolist()
    cumulative = cumulative_profile_volumes(stations, areas)
    finite = [v for v in cumulative if np.isfinite(v)]
    assert finite
    assert all(finite[i] <= finite[i + 1] for i in range(len(finite) - 1))
