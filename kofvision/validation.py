from __future__ import annotations

from typing import Iterable, TypeVar

import numpy as np

T = TypeVar("T")


def safe_float(
    raw: object,
    *,
    default: float,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    """Parse and clamp a float value, falling back to default."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if not np.isfinite(value):
        value = default
    if min_value is not None:
        value = max(value, float(min_value))
    if max_value is not None:
        value = min(value, float(max_value))
    return float(value)


def safe_choice(raw: object, *, allowed: Iterable[T], default: T) -> T:
    """Return raw when present in allowed, else default."""
    for item in allowed:
        if raw == item:
            return item
    return default
