from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kofvision.volume import SplitVolumeResult, SurfaceLike, VolumeResult, compute_volume, compute_volume_with_intermediate


@dataclass
class VolumeComputationSummary:
    result: VolumeResult | SplitVolumeResult
    effective_method: str
    scope_label: str


def combine_volume_results(
    results: list[VolumeResult | SplitVolumeResult],
) -> Optional[VolumeResult | SplitVolumeResult]:
    if not results:
        return None

    def _combine_simple(items: list[VolumeResult]) -> VolumeResult:
        first = items[0]
        return VolumeResult(
            top_name=first.top_name,
            bottom_name=first.bottom_name,
            cell_size=first.cell_size,
            n_valid=int(sum(it.n_valid for it in items)),
            net=float(sum(it.net for it in items)),
            cut=float(sum(it.cut for it in items)),
            fill=float(sum(it.fill for it in items)),
            area=float(sum(it.area for it in items)),
            y_min=min(it.y_min for it in items),
            x_min=min(it.x_min for it in items),
            y_max=max(it.y_max for it in items),
            x_max=max(it.x_max for it in items),
        )

    if isinstance(results[0], SplitVolumeResult):
        split_items = [r for r in results if isinstance(r, SplitVolumeResult)]
        if len(split_items) != len(results):
            return None
        upper = _combine_simple([it.upper for it in split_items])
        lower = _combine_simple([it.lower for it in split_items])
        return SplitVolumeResult(upper=upper, lower=lower)

    simple_items = [r for r in results if isinstance(r, VolumeResult)]
    if len(simple_items) != len(results):
        return None
    return _combine_simple(simple_items)


def compute_volume_for_mask(
    top_surface: SurfaceLike,
    bottom_surface: SurfaceLike,
    intermediate_surface: Optional[SurfaceLike],
    *,
    mask_polygon: Optional[list[tuple[float, float]]],
    method: str,
) -> Optional[VolumeResult | SplitVolumeResult]:
    if intermediate_surface is None:
        return compute_volume(top_surface, bottom_surface, mask_polygon=mask_polygon, method=method)
    return compute_volume_with_intermediate(
        top_surface,
        intermediate_surface,
        bottom_surface,
        mask_polygon=mask_polygon,
        method=method,
    )
