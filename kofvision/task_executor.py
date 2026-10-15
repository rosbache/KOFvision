from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")


class TaskExecutor:
    """UI-friendly background executor for long-running viewer tasks."""

    def __init__(self, tk_root, *, max_workers: int = 2) -> None:  # noqa: ANN001
        self._root = tk_root
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kofvision")

    def submit(
        self,
        *,
        task: Callable[[], T],
        on_success: Callable[[T], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        future = self._pool.submit(task)

        def _deliver() -> None:
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                on_error(exc)
                return
            on_success(result)

        future.add_done_callback(lambda _f: self._root.after(0, _deliver))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
