from __future__ import annotations

from types import SimpleNamespace

from kofvision.viewer_state import clear_cached_mask_volume_results


class _DummyViewer(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__()
        self._last_volume_per_mask_rows = ["row"]
        self._last_mask_volume_results = [{"mask": "A"}]
        self.refresh_called = 0

    def _refresh_mask_result_actions(self) -> None:
        self.refresh_called += 1


def test_clear_cached_mask_volume_results_clears_lists_and_refreshes() -> None:
    viewer = _DummyViewer()

    clear_cached_mask_volume_results(viewer)

    assert viewer._last_volume_per_mask_rows == []
    assert viewer._last_mask_volume_results == []
    assert viewer.refresh_called == 1
