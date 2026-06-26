"""Data models for DockStart PDBQT preparation workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

PreparationStatus = Literal["not_started", "checking", "ready", "running", "finished", "failed"]
PreparationTarget = Literal["receptor", "ligand"]
PreparationMethod = Literal["meeko", "rdkit_meeko", "external_manual"]

ALLOWED_PREPARATION_STATUSES: set[str] = {
    "not_started",
    "checking",
    "ready",
    "running",
    "finished",
    "failed",
}
ALLOWED_PREPARATION_TARGETS: set[str] = {"receptor", "ligand"}
ALLOWED_PREPARATION_METHODS: set[str] = {"meeko", "rdkit_meeko", "external_manual"}


@dataclass
class PreparationResult:
    target: PreparationTarget
    prep_id: str = ""
    status: PreparationStatus = "not_started"
    method: PreparationMethod | None = None
    input_file: str | None = None
    output_file: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    python_path: str = ""
    python_source: str = "unknown"
    rdkit_available: bool = False
    meeko_available: bool = False
    command: list[str] = field(default_factory=list)
    stdout_file: str = ""
    stderr_file: str = ""
    log_file: str = ""
    metadata_file: str = ""
    command_file: str = ""
    input_snapshot_file: str = ""
    output_check_file: str = ""
    exit_code: int | None = None
    error: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparationState:
    receptor: PreparationResult = field(
        default_factory=lambda: PreparationResult(
            target="receptor",
            output_file="prepared/receptor.pdbqt",
        ),
    )
    ligand: PreparationResult = field(
        default_factory=lambda: PreparationResult(
            target="ligand",
            output_file="prepared/ligand.pdbqt",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_preparation_result(target: PreparationTarget) -> PreparationResult:
    return PreparationResult(target=target, output_file=f"prepared/{target}.pdbqt")


def preparation_result_from_dict(target: PreparationTarget, data: Any) -> PreparationResult:
    source = data if isinstance(data, dict) else {}
    status = str(source.get("status") or "not_started")
    if status not in ALLOWED_PREPARATION_STATUSES:
        status = "not_started"

    method_value = source.get("method")
    method = str(method_value) if method_value else None
    if method is not None and method not in ALLOWED_PREPARATION_METHODS:
        method = None

    command = source.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        command = []

    warnings = source.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(item) for item in warnings]

    error = source.get("error")
    if error is not None and not isinstance(error, dict):
        error = {"message": str(error)}

    exit_code_value = source.get("exit_code")
    try:
        exit_code = int(exit_code_value) if exit_code_value not in ("", None) else None
    except (TypeError, ValueError):
        exit_code = None

    return PreparationResult(
        target=target,
        prep_id=str(source.get("prep_id") or ""),
        status=status,  # type: ignore[arg-type]
        method=method,  # type: ignore[arg-type]
        input_file=str(source.get("input_file")) if source.get("input_file") else None,
        output_file=str(source.get("output_file") or f"prepared/{target}.pdbqt"),
        started_at=str(source.get("started_at")) if source.get("started_at") else None,
        finished_at=str(source.get("finished_at")) if source.get("finished_at") else None,
        python_path=str(source.get("python_path") or ""),
        python_source=str(source.get("python_source") or "unknown"),
        rdkit_available=bool(source.get("rdkit_available", False)),
        meeko_available=bool(source.get("meeko_available", False)),
        command=command,
        stdout_file=str(source.get("stdout_file") or ""),
        stderr_file=str(source.get("stderr_file") or ""),
        log_file=str(source.get("log_file") or ""),
        metadata_file=str(source.get("metadata_file") or ""),
        command_file=str(source.get("command_file") or ""),
        input_snapshot_file=str(source.get("input_snapshot_file") or ""),
        output_check_file=str(source.get("output_check_file") or ""),
        exit_code=exit_code,
        error=error,
        warnings=warnings,
    )


def preparation_state_from_dict(data: Any) -> PreparationState:
    source = data if isinstance(data, dict) else {}
    return PreparationState(
        receptor=preparation_result_from_dict("receptor", source.get("receptor")),
        ligand=preparation_result_from_dict("ligand", source.get("ligand")),
    )
