"""Shared models for DockStart backend checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal

ToolStatus = Literal["ok", "missing", "error", "unknown"]
ToolSource = Literal["configured", "auto", "current_environment", "frontend_dependency", "unknown"]


@dataclass(frozen=True)
class ToolCheckResult:
    """Structured result returned by every tool detector."""

    key: str
    name: str
    status: ToolStatus
    version: str = ""
    path: str = ""
    message: str = ""
    raw_error: str = ""
    source: ToolSource = "unknown"

    ALLOWED_STATUSES: ClassVar[set[str]] = {"ok", "missing", "error", "unknown"}
    ALLOWED_SOURCES: ClassVar[set[str]] = {
        "configured",
        "auto",
        "current_environment",
        "frontend_dependency",
        "unknown",
    }

    def __post_init__(self) -> None:
        if self.status not in self.ALLOWED_STATUSES:
            allowed = ", ".join(sorted(self.ALLOWED_STATUSES))
            raise ValueError(f"Unsupported tool status: {self.status}. Allowed: {allowed}")
        if self.source not in self.ALLOWED_SOURCES:
            allowed = ", ".join(sorted(self.ALLOWED_SOURCES))
            raise ValueError(f"Unsupported tool source: {self.source}. Allowed: {allowed}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
