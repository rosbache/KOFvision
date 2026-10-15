from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ViewerRuntimeState:
    """Structured runtime values that are not persisted as project data."""

    volume_history: list[str] = field(default_factory=list)
    last_render_2d_signature: tuple[object, ...] | None = None
    last_render_3d_signature: tuple[object, ...] | None = None
