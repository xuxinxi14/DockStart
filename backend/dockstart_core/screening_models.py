"""Data models for deterministic, resumable virtual-screening jobs.

The screening state intentionally lives outside ``project.json``.  Older
DockStart projects therefore remain readable and are not migrated merely by
creating or inspecting a screening job.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


SCREENING_SCHEMA_VERSION = 1

ScreeningStatus = Literal[
    "ready",
    "running",
    "cancel_requested",
    "canceled",
    "completed",
    "completed_with_failures",
    "interrupted",
]
ScreeningItemStatus = Literal["pending", "running", "succeeded", "failed", "interrupted"]


@dataclass(frozen=True)
class ScreeningResourceLimits:
    """Hard limits applied before any external process is started."""

    max_ligands: int = 500
    max_retries: int = 3
    max_cpu: int = 64
    max_exhaustiveness: int = 128
    max_num_modes: int = 50
    max_box_edge_angstrom: float = 126.0
    max_ligand_bytes: int = 16 * 1024 * 1024
    max_staged_file_bytes: int = 128 * 1024 * 1024
    max_total_input_bytes: int = 512 * 1024 * 1024

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ScreeningResourceLimits":
        if not value:
            return cls()
        allowed = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value[key] for key in allowed if key in value})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScreeningToolSnapshot:
    """Auditable identity of the Vina binary selected for a screening job."""

    path: str
    version: str
    source: str
    sha256: str
    detection_status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScreeningStagedInput:
    """A content-addressed PDBQT copied into the project staging area."""

    file: str
    original_name: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScreeningItem:
    """One ligand in the stable screening queue."""

    item_id: str
    order: int
    ligand_file: str
    source_file: str
    sha256: str
    size_bytes: int
    status: ScreeningItemStatus = "pending"
    attempt_count: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    best_affinity_kcal_mol: float | None = None
    best_output_file: str = ""
    last_error: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScreeningItem":
        return cls(
            item_id=str(value["item_id"]),
            order=int(value["order"]),
            ligand_file=str(value["ligand_file"]),
            source_file=str(value.get("source_file") or ""),
            sha256=str(value.get("sha256") or ""),
            size_bytes=int(value.get("size_bytes") or 0),
            status=str(value.get("status") or "pending"),  # type: ignore[arg-type]
            attempt_count=int(value.get("attempt_count") or 0),
            attempts=list(value.get("attempts") or []),
            best_affinity_kcal_mol=(
                float(value["best_affinity_kcal_mol"])
                if value.get("best_affinity_kcal_mol") is not None
                else None
            ),
            best_output_file=str(value.get("best_output_file") or ""),
            last_error=str(value.get("last_error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
