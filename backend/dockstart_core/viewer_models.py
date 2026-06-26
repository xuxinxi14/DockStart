"""Data models for DockStart structure viewer responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ViewerStructureResult:
    ok: bool
    file_kind: str
    relative_path: str = ""
    absolute_path: str = ""
    exists: bool = False
    format: str = "unknown"
    content: str = ""
    size_bytes: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DockingPoseSummary:
    mode: int
    relative_path: str
    size_bytes: int
    line_count: int
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
