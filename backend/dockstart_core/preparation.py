"""Preparation workflow status and prerequisites for DockStart projects."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters import meeko_adapter, rdkit_adapter, vina_adapter
from dockstart_core.persistence import atomic_write_bytes, atomic_write_json, atomic_write_text
from dockstart_core.project import (
    _error,
    _preparation_target_lock,
    _project_from_dict,
    _project_lock,
    _read_and_migrate_project_unlocked,
    _write_project_json_unlocked,
    load_project,
    save_project,
)
from dockstart_core.preparation_models import (
    ALLOWED_PREPARATION_TARGETS,
    PreparationTarget,
    default_preparation_result,
)
from dockstart_core.toolchain import get_resolved_python

SUPPORTED_LIGAND_PREPARATION_FORMATS = {".sdf", ".mol"}
SUPPORTED_RECEPTOR_PREPARATION_FORMATS = {".pdb", ".cif"}
LIGAND_PREPARATION_OUTPUT = "prepared/ligand.pdbqt"
RECEPTOR_PREPARATION_OUTPUT = "prepared/receptor.pdbqt"
LIGAND_PREPARATION_LOG_DIR = Path("prepared", "logs")
LIGAND_PREPARATION_STDOUT = Path("prepared", "logs", "ligand_stdout.txt")
LIGAND_PREPARATION_STDERR = Path("prepared", "logs", "ligand_stderr.txt")
LIGAND_PREPARATION_LOG = Path("prepared", "logs", "ligand_preparation_log.json")
RECEPTOR_PREPARATION_STDOUT = Path("prepared", "logs", "receptor_stdout.txt")
RECEPTOR_PREPARATION_STDERR = Path("prepared", "logs", "receptor_stderr.txt")
RECEPTOR_PREPARATION_LOG = Path("prepared", "logs", "receptor_preparation_log.json")
PREPARATION_RECORD_ROOT = Path("preparation")
PREPARATION_ID_PATTERN = re.compile(r"^(receptor|ligand)_(\d{3,})$")
PREPARATION_TOOLS_SNAPSHOT_ENV_VAR = "DOCKSTART_PREPARATION_TOOLS_JSON"


class PreparationPathError(RuntimeError):
    """Raised when a preparation path can escape through a reparse point."""


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _target_error(target: str) -> dict[str, Any]:
    return _error(
        "PREPARATION_TARGET_INVALID",
        "准备目标无效，只能是 receptor 或 ligand。",
        raw_error=str(target),
        suggestion="请选择 receptor 或 ligand。",
    )


def _normalize_target(target: str) -> PreparationTarget | None:
    normalized = str(target or "").strip().lower()
    if normalized in ALLOWED_PREPARATION_TARGETS:
        return normalized  # type: ignore[return-value]
    return None


def _load_project_model(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    return _project_from_dict(loaded["project"], Path(project_dir).expanduser()), None


def _file_status(project_path: Path, relative_file: str, key: str, name: str) -> dict[str, Any]:
    value = str(relative_file or "")
    if not value:
        return {
            "key": key,
            "name": name,
            "path": "",
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "missing",
            "message": f"{name} 尚未记录。",
        }

    try:
        path = _safe_project_path(project_path, Path(value))
    except PreparationPathError as exc:
        return {
            "key": key,
            "name": name,
            "path": value,
            "absolute_path": "",
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "error",
            "message": f"{name} 路径不安全：{exc}",
        }
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    non_empty = size > 0
    if not exists:
        status = "missing"
        message = f"{name} 文件不存在。"
    elif not is_file:
        status = "error"
        message = f"{name} 路径不是文件。"
    elif not non_empty:
        status = "empty"
        message = f"{name} 文件为空。"
    else:
        status = "ok"
        message = f"{name} 文件存在。"

    return {
        "key": key,
        "name": name,
        "path": value,
        "absolute_path": str(path.resolve()),
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "non_empty": non_empty,
        "status": status,
        "message": message,
    }


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0) or 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0),
    )


def _safe_project_path(project_path: Path, value: Path, *, allow_missing: bool = True) -> Path:
    """Return a lexical project-local path with no symlink/reparse components."""

    project_root = project_path.expanduser().resolve(strict=True)
    supplied = value.expanduser()
    candidate = supplied if supplied.is_absolute() else project_root / supplied
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(project_root)
    except ValueError as exc:
        raise PreparationPathError(f"路径越出项目目录：{value}") from exc

    current = project_root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _is_reparse_or_symlink(current):
            raise PreparationPathError(f"路径包含符号链接、junction 或 reparse point：{current}")

    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise PreparationPathError(f"路径重解析到项目目录外：{value}") from exc
    if not allow_missing and not lexical.exists():
        raise PreparationPathError(f"路径不存在：{lexical}")
    return lexical


def _project_file_path(project_path: Path, value: str) -> Path:
    return _safe_project_path(project_path, Path(str(value or "")))


def _relative_path(path: Path, project_path: Path) -> str:
    try:
        return path.resolve().relative_to(project_path.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    atomic_write_json(path, payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_if_readable(path: Path) -> tuple[str, str]:
    try:
        return (_sha256_file(path), "") if path.is_file() else ("", "")
    except OSError as exc:
        return "", str(exc)


def _file_size_if_readable(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _preparation_record_dir(project_path: Path, prep_id: str) -> Path:
    return _safe_project_path(project_path, PREPARATION_RECORD_ROOT / prep_id)


def _ensure_preparation_directories(project_path: Path) -> None:
    for relative in (PREPARATION_RECORD_ROOT, Path("prepared")):
        directory = _safe_project_path(project_path, relative)
        directory.mkdir(parents=True, exist_ok=True)
        checked = _safe_project_path(project_path, relative, allow_missing=False)
        if not checked.is_dir():
            raise PreparationPathError(f"preparation 路径不是普通目录：{checked}")


def _validate_prep_id_for_target(target: PreparationTarget, prep_id: str) -> dict[str, Any] | None:
    match = PREPARATION_ID_PATTERN.match(str(prep_id or ""))
    if not match or match.group(1) != target:
        return _error(
            "PREPARATION_ID_INVALID",
            "preparation 记录编号无效。",
            raw_error=str(prep_id),
            suggestion=f"请使用形如 {target}_001 的 preparation 记录编号。",
        )
    return None


def get_next_preparation_id(project_dir: str, target: str) -> str:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        raise ValueError("preparation target must be receptor or ligand")

    project_path = Path(project_dir).expanduser().resolve()
    _ensure_preparation_directories(project_path)
    root = _safe_project_path(project_path, PREPARATION_RECORD_ROOT, allow_missing=False)
    max_index = 0
    if root.is_dir():
        for child in root.iterdir():
            _safe_project_path(project_path, PREPARATION_RECORD_ROOT / child.name, allow_missing=False)
            if not child.is_dir():
                continue
            match = PREPARATION_ID_PATTERN.match(child.name)
            if match and match.group(1) == normalized_target:
                max_index = max(max_index, int(match.group(2)))
    return f"{normalized_target}_{max_index + 1:03d}"


def _make_preparation_record_paths(project_path: Path, prep_id: str) -> dict[str, Any]:
    record_dir = _preparation_record_dir(project_path, prep_id)
    return {
        "prep_id": prep_id,
        "record_dir": record_dir,
        "record_dir_relative": _relative_path(record_dir, project_path),
        "metadata_file": _relative_path(record_dir / "metadata.json", project_path),
        "stdout_file": _relative_path(record_dir / "stdout.txt", project_path),
        "stderr_file": _relative_path(record_dir / "stderr.txt", project_path),
        "command_file": _relative_path(record_dir / "command.json", project_path),
        "input_snapshot_file": _relative_path(record_dir / "input_snapshot.json", project_path),
        "output_check_file": _relative_path(record_dir / "output_check.json", project_path),
    }


def _file_snapshot(path: Path, project_path: Path) -> dict[str, Any]:
    path = _safe_project_path(project_path, path)
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    modified_at = (
        datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()
        if exists and is_file
        else ""
    )
    sha256, sha256_error = _sha256_if_readable(path)
    return {
        "path": _relative_path(path, project_path),
        "absolute_path": str(path.resolve()) if exists else str(path),
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "non_empty": size > 0,
        "sha256": sha256,
        "sha256_error": sha256_error,
        "modified_at": modified_at,
    }


def _raw_input_identity(path: Path, project_path: Path) -> dict[str, Any]:
    """Capture a stable project-relative identity for one raw preparation input."""

    safe_path = _safe_project_path(project_path, path, allow_missing=False)
    canonical_relative_path = _relative_path(safe_path, project_path)
    captured_at = _now_iso()
    try:
        before = safe_path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise OSError("raw 输入不是非空普通文件")
        sha256 = _sha256_file(safe_path)
        after = safe_path.stat()
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if not stable:
            raise OSError("计算 SHA256 期间 raw 输入发生变化")
        return {
            "ok": True,
            "canonical_relative_path": canonical_relative_path,
            "sha256": sha256,
            "size_bytes": int(after.st_size),
            "modified_at_ns": int(after.st_mtime_ns),
            "captured_at": captured_at,
            "error": "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "canonical_relative_path": canonical_relative_path,
            "sha256": "",
            "size_bytes": 0,
            "modified_at_ns": 0,
            "captured_at": captured_at,
            "error": str(exc),
        }


def _verify_current_raw_input(
    project_path: Path,
    project: Any,
    target: PreparationTarget,
    claimed_input: Mapping[str, Any],
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """Compare current project raw reference and bytes with the claimed input."""

    file_ref = getattr(project, target)
    recorded_raw_file = str(getattr(file_ref, "raw_file", "") or "")
    reasons: list[str] = []
    current_input: dict[str, Any]
    if not recorded_raw_file:
        current_input = {
            "ok": False,
            "canonical_relative_path": "",
            "sha256": "",
            "error": "project.json 当前未记录 raw_file",
        }
        reasons.append("raw 引用已被清空")
    else:
        try:
            current_path = _project_file_path(project_path, recorded_raw_file)
            current_input = _raw_input_identity(current_path, project_path)
        except (OSError, PreparationPathError) as exc:
            current_input = {
                "ok": False,
                "canonical_relative_path": "",
                "sha256": "",
                "error": str(exc),
            }
        if not current_input.get("ok"):
            reasons.append("当前 raw 输入不存在、不可读或在校验期间发生变化")

    claimed_path = str(claimed_input.get("canonical_relative_path") or "")
    claimed_sha256 = str(claimed_input.get("sha256") or "")
    current_path = str(current_input.get("canonical_relative_path") or "")
    current_sha256 = str(current_input.get("sha256") or "")
    if not claimed_path or not claimed_sha256:
        reasons.append("任务认领记录缺少规范 raw 路径或 SHA256")
    if current_path != claimed_path:
        reasons.append("project.json 的 raw 引用已变化")
    if current_sha256 != claimed_sha256:
        reasons.append("raw 文件内容 SHA256 已变化")

    verification = {
        "checked_at": _now_iso(),
        "recorded_raw_file": recorded_raw_file,
        "claimed": dict(claimed_input),
        "current": current_input,
        "matches": not reasons,
        "reasons": reasons,
    }
    if not reasons:
        return True, verification, None

    error = {
        "code": "PREPARATION_INPUT_STALE",
        "message": "分子准备期间原始输入发生变化，候选 PDBQT 未发布。",
        "raw_error": json.dumps(verification, ensure_ascii=False, sort_keys=True),
        "suggestion": "请确认当前 raw 文件与项目引用后重新启动格式转换；旧候选文件仅保留在 preparation 记录中。",
    }
    return False, verification, error


def _build_preparation_metadata(
    *,
    prep_id: str,
    target: PreparationTarget,
    status: str,
    method: str,
    created_at: str,
    started_at: str | None,
    finished_at: str | None,
    built: dict[str, Any],
    exit_code: int | None = None,
    warnings: list[str] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = built.get("tools", {})
    python_tool = tools.get("python", {}) if isinstance(tools, dict) else {}
    rdkit_tool = tools.get("rdkit", {}) if isinstance(tools, dict) else {}
    meeko_tool = tools.get("meeko", {}) if isinstance(tools, dict) else {}
    python_path = Path(str(python_tool.get("path") or "")).expanduser()
    python_snapshot = built.get("python_executable_snapshot")
    if not isinstance(python_snapshot, dict):
        python_exists = python_path.is_file()
        python_sha256, python_sha256_error = _sha256_if_readable(python_path)
        python_snapshot = {
            "path": str(python_path),
            "exists": python_exists,
            "size_bytes": _file_size_if_readable(python_path) if python_exists else 0,
            "sha256": python_sha256,
            "sha256_error": python_sha256_error,
            "captured_at": _now_iso(),
        }
        # Reuse the exact launch-time observation at finalization.  Rehashing
        # the path later could silently attribute replacement bytes to a run.
        built["python_executable_snapshot"] = python_snapshot
    return {
        "prep_id": prep_id,
        "target": target,
        "status": status,
        "method": method,
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "python_path": str(python_tool.get("path") or ""),
        "python_source": str(python_tool.get("source") or "unknown"),
        "python_sha256": str(python_snapshot.get("sha256") or ""),
        "python_sha256_error": str(python_snapshot.get("sha256_error") or ""),
        "python_size_bytes": int(python_snapshot.get("size_bytes") or 0),
        "python_executable_snapshot": dict(python_snapshot),
        "rdkit_version": str(rdkit_tool.get("version") or ""),
        "meeko_version": str(meeko_tool.get("version") or ""),
        "input_file": built.get("input_file", ""),
        "output_file": built.get("output_file", ""),
        "candidate_output_file": built.get("candidate_output_file", ""),
        "claimed_input": built.get("claimed_input", {}),
        "claim_verification": built.get("claim_verification", {}),
        "script_file": built.get("script_file", ""),
        "intermediate_input_file": built.get("intermediate_input_file", ""),
        "command": built.get("command", []),
        "executor_pid": built.get("executor_pid"),
        "executor_executable": built.get("executor_executable", ""),
        "executor_identity": built.get("executor_identity"),
        "exit_code": exit_code,
        "warnings": warnings or [],
        "error": error,
    }


def _attach_executor_identity(built: dict[str, Any]) -> None:
    """Record the backend process that owns a running preparation task."""

    executor_pid = os.getpid()
    identity = vina_adapter.get_process_identity(executor_pid)
    built["executor_pid"] = executor_pid
    built["executor_identity"] = identity
    built["executor_executable"] = str((identity or {}).get("executable_path") or "")


def _publish_candidate_output(candidate: Path, destination: Path, project_path: Path | None = None) -> None:
    """Validate and atomically publish a generated text PDBQT candidate."""

    if project_path is not None:
        candidate = _safe_project_path(project_path, candidate, allow_missing=False)
        destination = _safe_project_path(project_path, destination)

    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise RuntimeError("Meeko 没有生成非空的 PDBQT 候选文件。")
    text = candidate.read_text(encoding="utf-8", errors="strict")
    if not text.strip():
        raise RuntimeError("Meeko 生成的 PDBQT 候选文件只包含空白内容。")
    atomic_write_text(destination, text)


def _project_error_payload(
    project: Any,
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "preparation": project.preparation.to_dict(),
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
        "warnings": warnings or [],
        "message": message,
    }


def _preparation_busy_error(project: Any, target: PreparationTarget) -> dict[str, Any] | None:
    prep = getattr(project.preparation, target)
    if prep.status != "running":
        return None

    active = False
    detail = "项目仍记录为 running，等待恢复检查确认执行器状态。"
    prep_id = str(prep.prep_id or project.latest_preparation.get(target) or "")
    if PREPARATION_ID_PATTERN.match(prep_id):
        try:
            metadata_path = _safe_project_path(
                Path(project.project_dir),
                PREPARATION_RECORD_ROOT / prep_id / "metadata.json",
                allow_missing=False,
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict):
                pid = metadata.get("executor_pid")
                if isinstance(pid, int) and pid > 0:
                    verification = vina_adapter.verify_process_identity(
                        pid,
                        str(metadata.get("executor_executable") or ""),
                        metadata.get("executor_identity") if isinstance(metadata.get("executor_identity"), dict) else None,
                    )
                    active = bool(verification.get("ok"))
                    detail = str(verification.get("message") or detail)
        except (OSError, ValueError, json.JSONDecodeError, PreparationPathError):
            pass

    return _project_error_payload(
        project,
        "PREPARATION_ALREADY_RUNNING",
        f"{target} 已有准备任务正在运行，未启动重复任务。",
        raw_error=f"prep_id={prep_id or 'unknown'}; active={active}; {detail}",
        suggestion="请等待当前任务完成；若应用曾异常退出，请先触发项目恢复检查。",
    )


def _check_preparation_available(project_path: Path, target: PreparationTarget) -> dict[str, Any] | None:
    with _project_lock(project_path):
        data, _, _ = _read_and_migrate_project_unlocked(project_path, persist_migration=True)
        project = _project_from_dict(data, project_path)
        return _preparation_busy_error(project, target)


def _claim_preparation(
    project_path: Path,
    target: PreparationTarget,
    prep_id: str,
    built: dict[str, Any],
    *,
    method: str,
    created_at: str,
    started_at: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Claim one target while the caller holds its cross-process lock."""

    _ensure_preparation_directories(project_path)
    _attach_executor_identity(built)
    command_path = _safe_project_path(project_path, Path(built["command_file"]))
    input_snapshot_path = _safe_project_path(project_path, Path(built["input_snapshot_file"]))
    metadata_path = _safe_project_path(project_path, Path(built["metadata_file"]))
    input_path = _safe_project_path(project_path, Path(built["input_path"]), allow_missing=False)
    try:
        claimed_input = _raw_input_identity(input_path, project_path)
    except (OSError, PreparationPathError) as exc:
        claimed_input = {
            "ok": False,
            "canonical_relative_path": _relative_path(input_path, project_path),
            "sha256": "",
            "captured_at": _now_iso(),
            "error": str(exc),
        }
    built["claimed_input"] = claimed_input

    # Create the audit record before publishing the running claim.  A crash can
    # therefore never leave project.json pointing to a record that did not
    # exist at claim time.
    _write_json(command_path, {"prep_id": prep_id, "target": target, "command": built["command"]})
    warnings = list(built.get("warnings", []))
    input_snapshot_payload = {
        "prep_id": prep_id,
        "target": target,
        "input_file": built["input_file"],
        "canonical_input_file": str(claimed_input.get("canonical_relative_path") or ""),
        "input_sha256": str(claimed_input.get("sha256") or ""),
        "claimed_input": claimed_input,
        "input": _file_snapshot(input_path, project_path),
        "tools": built.get("tools", {}),
        "warnings": warnings,
    }
    _write_json(input_snapshot_path, input_snapshot_payload)
    if not claimed_input.get("ok") or not claimed_input.get("sha256"):
        snapshot_error = _error(
            "PREPARATION_INPUT_SNAPSHOT_FAILED",
            "无法冻结本次分子准备的 raw 输入，任务未启动。",
            raw_error=str(claimed_input.get("error") or "raw 输入 SHA256 不可用"),
            suggestion="请确认 raw 文件是项目内可读取的非空普通文件，然后重新启动格式转换。",
        )
        rejected = _build_preparation_metadata(
            prep_id=prep_id,
            target=target,
            status="failed",
            method=method,
            created_at=created_at,
            started_at=started_at,
            finished_at=_now_iso(),
            built=built,
            warnings=warnings,
            error=copy.deepcopy(snapshot_error.get("error")),
        )
        rejected["published"] = False
        rejected["claim_rejected"] = True
        _write_json(metadata_path, rejected)
        return None, snapshot_error
    _write_json(
        metadata_path,
        _build_preparation_metadata(
            prep_id=prep_id,
            target=target,
            status="running",
            method=method,
            created_at=created_at,
            started_at=started_at,
            finished_at=None,
            built=built,
            warnings=warnings,
        ),
    )

    with _project_lock(project_path):
        data, _, _ = _read_and_migrate_project_unlocked(project_path, persist_migration=True)
        project = _project_from_dict(data, project_path)
        busy = _preparation_busy_error(project, target)
        if busy:
            rejected = _build_preparation_metadata(
                prep_id=prep_id,
                target=target,
                status="interrupted",
                method=method,
                created_at=created_at,
                started_at=started_at,
                finished_at=_now_iso(),
                built=built,
                warnings=warnings,
                error=copy.deepcopy(busy.get("error")) if isinstance(busy.get("error"), dict) else None,
            )
            rejected["published"] = False
            rejected["claim_rejected"] = True
            _write_json(metadata_path, rejected)
            return None, busy

        input_matches, claim_verification, stale_error = _verify_current_raw_input(
            project_path,
            project,
            target,
            claimed_input,
        )
        built["claim_verification"] = claim_verification
        input_snapshot_payload["claim_verification"] = claim_verification
        _write_json(input_snapshot_path, input_snapshot_payload)
        if not input_matches:
            assert stale_error is not None
            rejected = _build_preparation_metadata(
                prep_id=prep_id,
                target=target,
                status="failed",
                method=method,
                created_at=created_at,
                started_at=started_at,
                finished_at=_now_iso(),
                built=built,
                warnings=warnings,
                error=copy.deepcopy(stale_error),
            )
            rejected["published"] = False
            rejected["claim_rejected"] = True
            _write_json(metadata_path, rejected)
            return None, _project_error_payload(
                project,
                str(stale_error.get("code") or "PREPARATION_INPUT_STALE"),
                str(stale_error.get("message") or "分子准备输入已变化。"),
                raw_error=str(stale_error.get("raw_error") or ""),
                suggestion=str(stale_error.get("suggestion") or ""),
                warnings=warnings,
            )

        _write_json(
            metadata_path,
            _build_preparation_metadata(
                prep_id=prep_id,
                target=target,
                status="running",
                method=method,
                created_at=created_at,
                started_at=started_at,
                finished_at=None,
                built=built,
                warnings=warnings,
            ),
        )

        prep = getattr(project.preparation, target)
        prep.prep_id = prep_id
        prep.status = "running"
        prep.method = method
        prep.input_file = str(built["input_file"])
        prep.output_file = f"prepared/{target}.pdbqt"
        prep.started_at = started_at
        prep.finished_at = None
        prep.python_path = str(built["tools"]["python"].get("path", ""))
        prep.python_source = str(built["tools"]["python"].get("source", "unknown"))
        prep.rdkit_available = target == "ligand" and built["tools"]["rdkit"].get("status") == "ok"
        prep.meeko_available = built["tools"]["meeko"].get("status") == "ok"
        prep.command = list(built["command"])
        prep.stdout_file = str(built["stdout_file"])
        prep.stderr_file = str(built["stderr_file"])
        prep.log_file = str(built["log_file"])
        prep.metadata_file = str(built["metadata_file"])
        prep.command_file = str(built["command_file"])
        prep.input_snapshot_file = str(built["input_snapshot_file"])
        prep.output_check_file = str(built["output_check_file"])
        prep.exit_code = None
        prep.error = None
        prep.warnings = warnings
        project.latest_preparation[target] = prep_id
        project.updated_at = _now_iso()
        project.revision += 1
        _write_project_json_unlocked(project_path, project)
        return project, None


def _restore_previous_output(destination: Path, previous: bytes | None) -> str:
    try:
        if previous is None:
            destination.unlink(missing_ok=True)
        else:
            atomic_write_bytes(destination, previous)
        return ""
    except OSError as exc:
        return str(exc)


def _finalize_preparation(
    project_path: Path,
    target: PreparationTarget,
    prep_id: str,
    built: dict[str, Any],
    *,
    method: str,
    created_at: str,
    started_at: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Finalize only if ``prep_id`` still owns the target claim."""

    stdout_path = _safe_project_path(project_path, Path(built["stdout_file"]))
    stderr_path = _safe_project_path(project_path, Path(built["stderr_file"]))
    metadata_path = _safe_project_path(project_path, Path(built["metadata_file"]))
    output_check_path = _safe_project_path(project_path, Path(built["output_check_file"]))
    candidate_output_path = _safe_project_path(project_path, Path(built["candidate_output_path"]))
    output_path = _safe_project_path(project_path, Path(built["output_path"]))
    atomic_write_text(stdout_path, stdout)
    atomic_write_text(stderr_path, stderr)

    finished_at = _now_iso()
    candidate_ok = candidate_output_path.is_file() and candidate_output_path.stat().st_size > 0
    publication_error = ""
    restore_error = ""
    published = False
    ownership_lost = False
    current_project: Any | None = None
    error: dict[str, Any] | None = None
    input_verification: dict[str, Any] = {}

    with _preparation_target_lock(project_path, target):
        with _project_lock(project_path):
            data, _, _ = _read_and_migrate_project_unlocked(project_path, persist_migration=True)
            current_project = _project_from_dict(data, project_path)
            current_prep = getattr(current_project.preparation, target)
            owns_target = (
                current_project.latest_preparation.get(target) == prep_id
                and current_prep.prep_id == prep_id
                and current_prep.status == "running"
            )
            if not owns_target:
                ownership_lost = True
                error = {
                    "code": "PREPARATION_OWNERSHIP_LOST",
                    "message": f"{target} 准备任务已被更新的任务取代，候选输出未发布。",
                    "raw_error": (
                        f"prep_id={prep_id}; latest={current_project.latest_preparation.get(target)}; "
                        f"current={current_prep.prep_id}:{current_prep.status}"
                    ),
                    "suggestion": "请查看最新 preparation 记录；旧任务的候选文件仅保留用于审计。",
                }
            else:
                input_matches, input_verification, stale_error = _verify_current_raw_input(
                    project_path,
                    current_project,
                    target,
                    built.get("claimed_input", {}),
                )
                if not input_matches:
                    error = stale_error
                previous_output = output_path.read_bytes() if output_path.is_file() else None
                if error is None and exit_code == 0 and candidate_ok:
                    try:
                        _publish_candidate_output(candidate_output_path, output_path, project_path)
                        published = True
                    except Exception as exc:  # noqa: BLE001 - preserve previous output below.
                        publication_error = str(exc)

                output_ok = published and output_path.is_file() and output_path.stat().st_size > 0
                success = error is None and exit_code == 0 and candidate_ok and output_ok
                if success:
                    current_prep.status = "finished"
                    current_prep.error = None
                    getattr(current_project, target).file = f"prepared/{target}.pdbqt"
                else:
                    current_prep.status = "failed"
                    if error is None:
                        raw_error = publication_error or stderr or stdout or (
                            "输出 PDBQT 不存在或为空。" if exit_code == 0 else ""
                        )
                        error = {
                            "code": f"{target.upper()}_PREPARATION_FAILED",
                            "message": f"{target} PDBQT 自动准备失败，请查看 stderr 和日志。",
                            "raw_error": raw_error,
                            "suggestion": (
                                "请确认 RDKit/Meeko 版本、输入结构和配体准备能力。"
                                if target == "ligand"
                                else "请确认 Meeko receptor 模块、输入结构完整性和准备选项。"
                            ),
                        }
                    current_prep.error = error
                current_prep.finished_at = finished_at
                current_prep.exit_code = exit_code
                current_project.updated_at = _now_iso()
                current_project.revision += 1
                try:
                    _write_project_json_unlocked(project_path, current_project)
                except Exception as exc:  # noqa: BLE001 - rollback published bytes.
                    if published:
                        restore_error = _restore_previous_output(output_path, previous_output)
                        published = False
                    publication_error = str(exc)
                    if restore_error:
                        publication_error += f"; rollback failed: {restore_error}"
                    error = {
                        "code": "PREPARATION_PROJECT_COMMIT_FAILED",
                        "message": "准备输出已拒绝提交，因为 project.json 更新失败。",
                        "raw_error": publication_error,
                        "suggestion": "请确认项目目录可写；旧 prepared 文件已尽力恢复。",
                    }

        final_status = "interrupted" if ownership_lost else (
            "finished" if published and not error else "failed"
        )
        output_ok = published and output_path.is_file() and output_path.stat().st_size > 0
        output_check = {
            "prep_id": prep_id,
            "target": target,
            "output_file": f"prepared/{target}.pdbqt",
            "candidate_output": _file_snapshot(candidate_output_path, project_path),
            "output": _file_snapshot(output_path, project_path),
            "exit_code": exit_code,
            "published": published,
            "ownership_lost": ownership_lost,
            "input_verification": input_verification,
            "publication_error": publication_error,
            "restore_error": restore_error,
            "success": final_status == "finished",
        }
        _write_json(output_check_path, output_check)
        metadata_payload = _build_preparation_metadata(
            prep_id=prep_id,
            target=target,
            status=final_status,
            method=method,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            built=built,
            exit_code=exit_code,
            warnings=list(built.get("warnings", [])),
            error=error,
        )
        metadata_payload.update(
            {
                "stdout_file": built["stdout_file"],
                "stderr_file": built["stderr_file"],
                "command_file": built["command_file"],
                "input_snapshot_file": built["input_snapshot_file"],
                "output_check_file": built["output_check_file"],
                "output_exists": output_path.is_file(),
                "output_non_empty": output_ok,
                "published": published,
                "ownership_lost": ownership_lost,
                "input_verification": input_verification,
                "candidate_output": _file_snapshot(candidate_output_path, project_path),
                "output": _file_snapshot(output_path, project_path),
                "stdout": _file_snapshot(stdout_path, project_path),
                "stderr": _file_snapshot(stderr_path, project_path),
            },
        )
        _write_json(metadata_path, metadata_payload)

    return {
        "success": final_status == "finished",
        "status": final_status,
        "project": current_project,
        "error": error,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "published": published,
        "ownership_lost": ownership_lost,
        "input_verification": input_verification,
    }


def _prepare_target_pdbqt(
    project_dir: str,
    target: PreparationTarget,
    *,
    overwrite: bool,
    method: str,
    builder: Any,
) -> dict[str, Any]:
    project_path = Path(project_dir).expanduser().resolve()
    try:
        with _preparation_target_lock(project_path, target):
            _ensure_preparation_directories(project_path)
            busy = _check_preparation_available(project_path, target)
            if busy:
                return busy
            prep_id = get_next_preparation_id(str(project_path), target)
            built = builder(str(project_path), overwrite=overwrite, prep_id=prep_id)
            if not built.get("ok"):
                return built
            created_at = _now_iso()
            started_at = _now_iso()
            claimed, claim_error = _claim_preparation(
                project_path,
                target,
                prep_id,
                built,
                method=method,
                created_at=created_at,
                started_at=started_at,
            )
            if claim_error:
                return claim_error
            assert claimed is not None

        try:
            completed = meeko_adapter.run_preparation_command(built["command"], cwd=project_path)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            exit_code = int(completed.returncode)
        except Exception as exc:  # noqa: BLE001 - structured preparation failure.
            stdout = ""
            stderr = str(exc)
            exit_code = -1

        finalized = _finalize_preparation(
            project_path,
            target,
            prep_id,
            built,
            method=method,
            created_at=created_at,
            started_at=started_at,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        tools_snapshot = built.get("tools")
        payload = get_preparation_status(
            str(project_path),
            tools_snapshot=tools_snapshot if isinstance(tools_snapshot, Mapping) else None,
        )
        success = bool(finalized["success"])
        final_error = finalized.get("error") if isinstance(finalized.get("error"), dict) else {}
        if finalized["ownership_lost"]:
            message = f"{target} 准备任务已失去所有权，候选输出未发布。"
        elif final_error.get("code") == "PREPARATION_INPUT_STALE":
            message = f"{target} 准备期间 raw 输入发生变化，候选输出未发布。"
        elif success:
            message = (
                "ligand PDBQT 自动准备完成。请继续人工检查配体质子化、电荷和构象合理性。"
                if target == "ligand"
                else "receptor PDBQT 自动准备完成。请继续人工检查受体结构、金属离子、水分子、辅因子和质子化状态。"
            )
        else:
            message = f"{target} PDBQT 自动准备失败。"
        payload.update(
            {
                "ok": success,
                "target": target,
                "prep_id": prep_id,
                "metadata_file": built["metadata_file"],
                "output_file": f"prepared/{target}.pdbqt",
                "stdout_file": built["stdout_file"],
                "stderr_file": built["stderr_file"],
                "log_file": built["log_file"],
                "exit_code": exit_code,
                "message": message,
                "error": finalized["error"],
            },
        )
        return payload
    except PreparationPathError as exc:
        return _error(
            "PREPARATION_PATH_UNSAFE",
            "准备任务路径包含符号链接、junction、reparse point 或越出项目目录，已拒绝访问。",
            raw_error=str(exc),
            suggestion="请恢复项目中的普通 raw/preparation/prepared 目录和文件后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - keep the boundary structured.
        return _error(
            "PREPARATION_START_ERROR",
            "启动分子准备任务时发生错误。",
            raw_error=str(exc),
            suggestion="请检查项目目录、工具链和 preparation 审计目录后重试。",
        )


def _tool_status() -> dict[str, Any]:
    cached_payload = os.environ.get(PREPARATION_TOOLS_SNAPSHOT_ENV_VAR, "").strip()
    if cached_payload:
        try:
            cached_tools = json.loads(cached_payload)
        except (TypeError, ValueError):
            cached_tools = None
        if (
            isinstance(cached_tools, dict)
            and isinstance(cached_tools.get("python"), dict)
            and isinstance(cached_tools.get("rdkit"), dict)
            and isinstance(cached_tools.get("meeko"), dict)
        ):
            # The desktop host keys this snapshot by its runtime fingerprint
            # and injects it only into the preparation subprocess.  Reusing it
            # avoids launching the same RDKit/Meeko capability probes for the
            # receptor and ligand conversions of one session.
            return copy.deepcopy(cached_tools)

    python_result = get_resolved_python()
    rdkit_result = rdkit_adapter.detect_rdkit_capabilities(python_result.path, python_result.source)
    meeko_result = meeko_adapter.detect_meeko_capabilities(python_result.path, python_result.source)
    return {
        "python": python_result.to_dict(),
        "rdkit": rdkit_result,
        "meeko": meeko_result,
    }


def get_preparation_tool_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    tools = _tool_status()
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "tools": tools,
        "python_path": tools["python"].get("path", ""),
        "python_source": tools["python"].get("source", "unknown"),
        "message": "自动准备工具能力检测已完成。本阶段只检测能力，不执行分子处理。",
        "error": None,
    }


def get_preparation_status(
    project_dir: str,
    *,
    tools_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    # A validation/preparation command has already paid for the Python/RDKit/
    # Meeko probes.  Reuse that immutable snapshot instead of starting the
    # detection scripts a second time merely to assemble the response payload.
    tools = copy.deepcopy(dict(tools_snapshot)) if tools_snapshot is not None else _tool_status()
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "preparation": project.preparation.to_dict(),
        "tools": tools,
        "files": {
            "receptor_raw": _file_status(project_path, project.receptor.raw_file, "receptor_raw", "受体 raw 文件"),
            "ligand_raw": _file_status(project_path, project.ligand.raw_file, "ligand_raw", "配体 raw 文件"),
            "receptor_prepared": _file_status(project_path, project.receptor.file, "receptor_prepared", "受体 prepared PDBQT"),
            "ligand_prepared": _file_status(project_path, project.ligand.file, "ligand_prepared", "配体 prepared PDBQT"),
        },
        "message": "PDBQT 自动准备状态已读取。",
        "error": None,
    }


def validate_preparation_prerequisites(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None
    busy = _preparation_busy_error(project, normalized_target)
    if busy:
        return busy

    project_path = Path(project.project_dir).expanduser()
    file_ref = getattr(project, normalized_target)
    raw_status = _file_status(
        project_path,
        file_ref.raw_file,
        f"{normalized_target}_raw",
        "受体 raw 文件" if normalized_target == "receptor" else "配体 raw 文件",
    )
    if raw_status["status"] != "ok":
        return _error(
            f"{normalized_target.upper()}_RAW_FILE_NOT_READY",
            "尚未找到可用于自动准备的 raw 文件。",
            raw_error=raw_status.get("path", ""),
            suggestion="请先在“下载原始结构文件”页面下载 raw 文件，或确认 project.json 中的 raw_file 记录。",
        )

    tools = _tool_status()
    python_ok = tools["python"]["status"] == "ok"
    rdkit_ok = tools["rdkit"]["status"] == "ok"
    meeko_ok = tools["meeko"]["status"] == "ok"
    missing: list[str] = []
    if not python_ok:
        missing.append("Python")
    if normalized_target == "ligand" and not rdkit_ok:
        missing.append("RDKit")
    if not meeko_ok:
        missing.append("Meeko")

    status = "ready" if not missing else "checking"
    preparation_result = getattr(project.preparation, normalized_target)
    preparation_result.status = status  # type: ignore[assignment]
    preparation_result.input_file = file_ref.raw_file
    preparation_result.output_file = f"prepared/{normalized_target}.pdbqt"
    preparation_result.python_path = tools["python"].get("path", "")
    preparation_result.python_source = tools["python"].get("source", "unknown")
    preparation_result.rdkit_available = rdkit_ok
    preparation_result.meeko_available = meeko_ok
    preparation_result.warnings = [
        "自动准备只能完成格式和工具链层面的处理，不能保证质子化、电荷、构象或受体结构选择一定科学正确。"
    ]
    preparation_result.error = None if not missing else {
        "code": "PREPARATION_TOOLS_NOT_READY",
        "message": "自动准备所需工具尚未全部可用。",
        "raw_error": ", ".join(missing),
        "suggestion": "请先在工具链状态页确认 Python、RDKit 和 Meeko。DockStart 不会自动安装这些包。",
    }

    save_result = save_project(project)
    if not save_result.get("ok"):
        return save_result

    payload = get_preparation_status(project.project_dir, tools_snapshot=tools)
    payload["target"] = normalized_target
    payload["ready"] = not missing
    payload["missing_tools"] = missing
    payload["message"] = (
        "自动准备前置检查通过。"
        if not missing
        else "raw 文件已找到，但自动准备工具尚未全部可用。"
    )
    if missing:
        payload["ok"] = False
        payload["error"] = preparation_result.error
    return payload


def _ligand_preparation_script_text() -> str:
    return r'''
from __future__ import annotations

import json
import io
import sys
from pathlib import Path

from rdkit import Chem
from meeko import MoleculePreparation

try:
    from meeko import PDBQTWriterLegacy as PDBQTWriter
except Exception:
    try:
        from meeko import PDBQTWriter
    except Exception as exc:
        raise RuntimeError("未找到可用的 Meeko PDBQT writer。") from exc


def read_ligand(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".sdf":
        # Let Python open the path so Windows Unicode/space handling does not
        # depend on RDKit's C++ filename conversion.
        supplier = Chem.ForwardSDMolSupplier(
            io.BytesIO(path.read_bytes()),
            sanitize=True,
            removeHs=False,
        )
        molecules = [mol for mol in supplier if mol is not None]
        if not molecules:
            raise RuntimeError("RDKit 未能从 SDF 中读取到有效分子。")
        return prepare_ligand_for_meeko(molecules[0])
    if suffix == ".mol":
        molecule = Chem.MolFromMolBlock(
            path.read_text(encoding="utf-8", errors="replace"),
            sanitize=True,
            removeHs=False,
        )
        if molecule is None:
            raise RuntimeError("RDKit 未能从 MOL 文件中读取到有效分子。")
        return prepare_ligand_for_meeko(molecule)
    raise RuntimeError(f"暂不支持的配体输入格式：{suffix}")


def prepare_ligand_for_meeko(molecule):
    try:
        molecule = Chem.AddHs(molecule, addCoords=True)
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        raise RuntimeError("RDKit failed to add explicit hydrogens before Meeko ligand preparation.") from exc
    return molecule


def normalize_writer_result(result):
    if isinstance(result, tuple):
        if len(result) >= 2 and result[1] is False:
            raise RuntimeError(str(result[2]) if len(result) >= 3 else "Meeko 写出 PDBQT 失败。")
        return str(result[0])
    return str(result)


def main() -> int:
    if len(sys.argv) != 3:
        print("需要输入 raw ligand 路径和输出 PDBQT 路径。", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    molecule = read_ligand(input_path)
    preparator = MoleculePreparation()
    setups = preparator.prepare(molecule)
    if not setups:
        print("Meeko 未生成 ligand setup，无法写出 PDBQT。", file=sys.stderr)
        return 3

    result = PDBQTWriter.write_string(setups[0])
    pdbqt_text = normalize_writer_result(result)
    if not pdbqt_text.strip():
        print("Meeko 写出的 PDBQT 为空。", file=sys.stderr)
        return 4

    output_path.write_text(pdbqt_text, encoding="utf-8")
    print(json.dumps({"ok": True, "output_file": str(output_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def validate_ligand_preparation_input(project_dir: str, overwrite: bool = False) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    raw_file = str(project.ligand.raw_file or "")
    if not raw_file:
        return _error(
            "LIGAND_RAW_FILE_NOT_RECORDED",
            "尚未记录 ligand raw 文件，无法自动准备配体 PDBQT。",
            suggestion="请先在“下载原始结构文件”页面下载 ligand raw 文件，或手动导入 prepared ligand PDBQT。",
        )

    try:
        input_path = _project_file_path(project_path, raw_file)
        output_path = _safe_project_path(project_path, Path(LIGAND_PREPARATION_OUTPUT))
    except PreparationPathError as exc:
        return _error(
            "PREPARATION_PATH_UNSAFE",
            "配体准备路径不安全，已拒绝访问。",
            raw_error=str(exc),
            suggestion="请使用项目内普通 raw/preparation/prepared 目录，移除符号链接或 junction。",
        )
    input_status = _file_status(project_path, raw_file, "ligand_raw", "配体 raw 文件")
    if input_status["status"] != "ok":
        return _error(
            "LIGAND_RAW_FILE_NOT_READY",
            "配体 raw 文件不存在或为空，无法自动准备 ligand PDBQT。",
            raw_error=input_status.get("absolute_path", input_status.get("path", "")),
            suggestion="请重新下载 ligand raw 文件，或检查 project.json 中的 ligand.raw_file 记录。",
        )

    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_LIGAND_PREPARATION_FORMATS:
        return _error(
            "LIGAND_RAW_FORMAT_UNSUPPORTED",
            "当前版本暂不支持该配体 raw 文件格式自动准备 PDBQT。",
            raw_error=suffix,
            suggestion="V0.3.2 优先支持 SDF 和 MOL；MOL2/SMILES 暂不自动准备，请先使用外部工具准备 PDBQT。",
        )

    if output_path.exists() and output_path.stat().st_size > 0 and not overwrite:
        return _error(
            "LIGAND_PREPARED_FILE_EXISTS",
            "prepared/ligand.pdbqt 已存在，默认不会覆盖。",
            raw_error=str(output_path),
            suggestion="如确认要重新生成，请开启 overwrite。",
        )

    tool_status = get_preparation_tool_status(project.project_dir)
    if not tool_status.get("ok"):
        return tool_status
    tools = tool_status["tools"]
    python_tool = tools["python"]
    rdkit_tool = tools["rdkit"]
    meeko_tool = tools["meeko"]
    ligand_capability = meeko_tool.get("capabilities", {}).get("ligand_preparation", {})

    missing: list[str] = []
    if python_tool.get("status") != "ok":
        missing.append("Python")
    if rdkit_tool.get("status") != "ok":
        missing.append("RDKit")
    if meeko_tool.get("status") != "ok":
        missing.append("Meeko")
    if ligand_capability.get("status") != "ok":
        missing.append("Meeko ligand preparation capability")

    if missing:
        return _error(
            "LIGAND_PREPARATION_TOOLS_NOT_READY",
            "配体 PDBQT 自动准备所需工具尚未全部可用。",
            raw_error=", ".join(missing),
            suggestion="请先在 PreparationPage 或工具链状态页确认 Python、RDKit、Meeko 以及 Meeko 配体准备能力。",
        )

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "input_file": raw_file,
        "input_path": str(input_path),
        "output_file": LIGAND_PREPARATION_OUTPUT,
        "output_path": str(output_path),
        "format": suffix,
        "tools": tools,
        "overwrite": overwrite,
        "warnings": [
            "自动生成 ligand PDBQT 不代表配体质子化、电荷、构象或互变异构状态一定科学正确，请人工检查。"
        ],
        "message": "配体 PDBQT 自动准备输入检查通过。",
        "error": None,
    }


def build_ligand_preparation_command_or_script(
    project_dir: str,
    overwrite: bool = False,
    prep_id: str | None = None,
) -> dict[str, Any]:
    validation = validate_ligand_preparation_input(project_dir, overwrite=overwrite)
    if not validation.get("ok"):
        return validation

    project_path = Path(validation["project_dir"]).expanduser().resolve()
    _ensure_preparation_directories(project_path)
    selected_prep_id = prep_id or get_next_preparation_id(project_dir, "ligand")
    paths = _make_preparation_record_paths(project_path, selected_prep_id)
    record_dir = paths["record_dir"]
    record_dir.mkdir(exist_ok=False)
    _safe_project_path(project_path, PREPARATION_RECORD_ROOT / selected_prep_id, allow_missing=False)
    script_path = record_dir / "prepare_ligand_rdkit_meeko.py"
    atomic_write_text(script_path, _ligand_preparation_script_text())
    candidate_output_path = record_dir / "candidate_ligand.pdbqt"

    command = [
        validation["tools"]["python"]["path"],
        "-I",
        "-B",
        str(script_path),
        validation["input_path"],
        str(candidate_output_path),
    ]
    return {
        **validation,
        "prep_id": selected_prep_id,
        "record_dir": paths["record_dir_relative"],
        "command": command,
        "script_file": _relative_path(script_path, project_path),
        "candidate_output_file": _relative_path(candidate_output_path, project_path),
        "candidate_output_path": str(candidate_output_path),
        "stdout_file": paths["stdout_file"],
        "stderr_file": paths["stderr_file"],
        "log_file": paths["metadata_file"],
        "metadata_file": paths["metadata_file"],
        "command_file": paths["command_file"],
        "input_snapshot_file": paths["input_snapshot_file"],
        "output_check_file": paths["output_check_file"],
    }


def prepare_ligand_pdbqt(project_dir: str, overwrite: bool = False, options: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = options or {}
    return _prepare_target_pdbqt(
        project_dir,
        "ligand",
        overwrite=overwrite,
        method="rdkit_meeko",
        builder=build_ligand_preparation_command_or_script,
    )


def load_ligand_preparation_log(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    prep = project.preparation.ligand
    stdout_file = prep.stdout_file or LIGAND_PREPARATION_STDOUT.as_posix()
    stderr_file = prep.stderr_file or LIGAND_PREPARATION_STDERR.as_posix()
    log_file = prep.log_file or LIGAND_PREPARATION_LOG.as_posix()

    def read_optional(relative_file: str) -> str:
        path = _project_file_path(project_path, relative_file)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    try:
        stdout = read_optional(stdout_file)
        stderr = read_optional(stderr_file)
        log = read_optional(log_file)
    except PreparationPathError as exc:
        return _error("PREPARATION_PATH_UNSAFE", "ligand preparation 日志路径不安全。", raw_error=str(exc))

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "target": "ligand",
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "log_file": log_file,
        "stdout": stdout,
        "stderr": stderr,
        "log": log,
        "message": "ligand preparation 日志已读取。",
        "error": None,
    }


def validate_receptor_preparation_input(project_dir: str, overwrite: bool = False) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    raw_file = str(project.receptor.raw_file or "")
    if not raw_file:
        return _error(
            "RECEPTOR_RAW_FILE_NOT_RECORDED",
            "尚未记录 receptor raw 文件，无法自动准备受体 PDBQT。",
            suggestion="请先在“下载原始结构文件”页面下载 receptor raw 文件，或手动导入 prepared receptor PDBQT。",
        )

    try:
        input_path = _project_file_path(project_path, raw_file)
        output_path = _safe_project_path(project_path, Path(RECEPTOR_PREPARATION_OUTPUT))
    except PreparationPathError as exc:
        return _error(
            "PREPARATION_PATH_UNSAFE",
            "受体准备路径不安全，已拒绝访问。",
            raw_error=str(exc),
            suggestion="请使用项目内普通 raw/preparation/prepared 目录，移除符号链接或 junction。",
        )
    input_status = _file_status(project_path, raw_file, "receptor_raw", "受体 raw 文件")
    if input_status["status"] != "ok":
        return _error(
            "RECEPTOR_RAW_FILE_NOT_READY",
            "受体 raw 文件不存在或为空，无法自动准备 receptor PDBQT。",
            raw_error=input_status.get("absolute_path", input_status.get("path", "")),
            suggestion="请重新下载 receptor raw 文件，或检查 project.json 中的 receptor.raw_file 记录。",
        )

    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_RECEPTOR_PREPARATION_FORMATS:
        return _error(
            "RECEPTOR_RAW_FORMAT_UNSUPPORTED",
            "当前版本暂不支持该受体 raw 文件格式自动准备 PDBQT。",
            raw_error=suffix,
            suggestion="受体自动准备支持 PDB 和 CIF；其他格式请先使用外部工具准备 PDBQT。",
        )

    if output_path.exists() and output_path.stat().st_size > 0 and not overwrite:
        return _error(
            "RECEPTOR_PREPARED_FILE_EXISTS",
            "prepared/receptor.pdbqt 已存在，默认不会覆盖。",
            raw_error=str(output_path),
            suggestion="如确认要重新生成，请开启 overwrite。",
        )

    tool_status = get_preparation_tool_status(project.project_dir)
    if not tool_status.get("ok"):
        return tool_status
    tools = tool_status["tools"]
    python_tool = tools["python"]
    meeko_tool = tools["meeko"]
    receptor_capability = meeko_tool.get("capabilities", {}).get("receptor_preparation", {})
    missing: list[str] = []
    if python_tool.get("status") != "ok":
        missing.append("Python")
    if meeko_tool.get("status") != "ok":
        missing.append("Meeko")
    if receptor_capability.get("status") != "ok":
        missing.append("Meeko receptor preparation capability (meeko.cli.mk_prepare_receptor)")
    if suffix == ".cif" and receptor_capability.get("cif_input_available") is not True:
        missing.append("Gemmi CIF parser")

    if missing:
        return _error(
            "RECEPTOR_PREPARATION_TOOLS_NOT_READY",
            "受体 PDBQT 自动准备所需工具尚未全部可用或不可确认。",
            raw_error=", ".join(missing),
            suggestion=(
                "CIF 转换还需要同一 Python 中可导入 Gemmi；请检查 Assisted 工具链，或改用 PDB。"
                if suffix == ".cif"
                else "请确认 Meeko 已安装且可导入 receptor preparation 模块。当前版本不使用 MGLTools/Open Babel 兜底。"
            ),
        )

    warnings = [
        "受体自动准备使用保守默认设置，不能保证缺失残基、金属离子、水分子、辅因子或质子化状态处理一定适合当前体系。",
        "请在运行 Vina 前人工检查 receptor PDBQT。",
    ]
    if suffix == ".cif":
        warnings.append(
            "CIF 将先由 Gemmi 转换为 preparation 审计目录中的中间 PDB，再交给 Meeko；"
            "无法无损表示为传统 PDB 的多模型、长链 ID 或超大结构会被明确拒绝。"
        )

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "input_file": raw_file,
        "input_path": str(input_path),
        "output_file": RECEPTOR_PREPARATION_OUTPUT,
        "output_path": str(output_path),
        "format": suffix,
        "tools": tools,
        "receptor_module": "meeko.cli.mk_prepare_receptor",
        "overwrite": overwrite,
        "warnings": warnings,
        "message": "受体 PDBQT 自动准备输入检查通过。",
        "error": None,
    }


def build_receptor_preparation_command_or_script(
    project_dir: str,
    overwrite: bool = False,
    prep_id: str | None = None,
) -> dict[str, Any]:
    validation = validate_receptor_preparation_input(project_dir, overwrite=overwrite)
    if not validation.get("ok"):
        return validation

    project_path = Path(validation["project_dir"]).expanduser().resolve()
    _ensure_preparation_directories(project_path)
    selected_prep_id = prep_id or get_next_preparation_id(project_dir, "receptor")
    paths = _make_preparation_record_paths(project_path, selected_prep_id)
    paths["record_dir"].mkdir(exist_ok=False)
    _safe_project_path(project_path, PREPARATION_RECORD_ROOT / selected_prep_id, allow_missing=False)
    candidate_output_path = paths["record_dir"] / "candidate_receptor.pdbqt"
    output_stem = str(candidate_output_path.with_suffix(""))
    python_path = validation["tools"]["python"]["path"]
    is_cif = str(validation.get("format") or "").lower() == ".cif"
    script_file = ""
    intermediate_input_file = ""
    if is_cif:
        script_path = paths["record_dir"] / "prepare_receptor_cif_gemmi_meeko.py"
        intermediate_path = paths["record_dir"] / "receptor_from_cif.pdb"
        atomic_write_text(script_path, meeko_adapter.receptor_cif_bridge_script_text())
        command = [
            python_path,
            "-I",
            "-B",
            str(script_path),
            validation["input_path"],
            str(intermediate_path),
            output_stem,
        ]
        script_file = _relative_path(script_path, project_path)
        intermediate_input_file = _relative_path(intermediate_path, project_path)
    else:
        command = [
            python_path,
            "-I",
            "-B",
            "-m",
            str(validation["receptor_module"]),
            "--read_pdb",
            validation["input_path"],
            "-o",
            output_stem,
            "-p",
        ]

    return {
        **validation,
        "prep_id": selected_prep_id,
        "record_dir": paths["record_dir_relative"],
        "command": command,
        "script_file": script_file,
        "intermediate_input_file": intermediate_input_file,
        "candidate_output_file": _relative_path(candidate_output_path, project_path),
        "candidate_output_path": str(candidate_output_path),
        "stdout_file": paths["stdout_file"],
        "stderr_file": paths["stderr_file"],
        "log_file": paths["metadata_file"],
        "metadata_file": paths["metadata_file"],
        "command_file": paths["command_file"],
        "input_snapshot_file": paths["input_snapshot_file"],
        "output_check_file": paths["output_check_file"],
    }


def prepare_receptor_pdbqt(project_dir: str, overwrite: bool = False, options: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = options or {}
    return _prepare_target_pdbqt(
        project_dir,
        "receptor",
        overwrite=overwrite,
        method="meeko",
        builder=build_receptor_preparation_command_or_script,
    )


def load_receptor_preparation_log(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    prep = project.preparation.receptor
    stdout_file = prep.stdout_file or RECEPTOR_PREPARATION_STDOUT.as_posix()
    stderr_file = prep.stderr_file or RECEPTOR_PREPARATION_STDERR.as_posix()
    log_file = prep.log_file or RECEPTOR_PREPARATION_LOG.as_posix()

    def read_optional(relative_file: str) -> str:
        path = _project_file_path(project_path, relative_file)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    try:
        stdout = read_optional(stdout_file)
        stderr = read_optional(stderr_file)
        log = read_optional(log_file)
    except PreparationPathError as exc:
        return _error("PREPARATION_PATH_UNSAFE", "receptor preparation 日志路径不安全。", raw_error=str(exc))

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "target": "receptor",
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "log_file": log_file,
        "stdout": stdout,
        "stderr": stderr,
        "log": log,
        "message": "receptor preparation 日志已读取。",
        "error": None,
    }


def list_preparation_runs(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser().resolve()
    try:
        root = _safe_project_path(project_path, PREPARATION_RECORD_ROOT)
    except PreparationPathError as exc:
        return _error("PREPARATION_PATH_UNSAFE", "preparation 记录目录不安全。", raw_error=str(exc))
    runs: list[dict[str, Any]] = []
    if root.is_dir():
        for child in root.iterdir():
            try:
                child = _safe_project_path(project_path, PREPARATION_RECORD_ROOT / child.name, allow_missing=False)
            except PreparationPathError as exc:
                return _error("PREPARATION_PATH_UNSAFE", "preparation 记录路径不安全。", raw_error=str(exc))
            if not child.is_dir():
                continue
            match = PREPARATION_ID_PATTERN.match(child.name)
            if not match or match.group(1) != normalized_target:
                continue
            try:
                metadata_file = _safe_project_path(
                    project_path,
                    PREPARATION_RECORD_ROOT / child.name / "metadata.json",
                )
            except PreparationPathError as exc:
                return _error("PREPARATION_PATH_UNSAFE", "preparation metadata 路径不安全。", raw_error=str(exc))
            metadata: dict[str, Any] = {}
            if metadata_file.is_file():
                try:
                    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    metadata = {"metadata_error": str(exc)}
            runs.append(
                {
                    "prep_id": child.name,
                    "target": normalized_target,
                    "record_dir": _relative_path(child, project_path),
                    "metadata_file": _relative_path(metadata_file, project_path),
                    "metadata_exists": metadata_file.is_file(),
                    "status": metadata.get("status", "unknown"),
                    "created_at": metadata.get("created_at", ""),
                    "finished_at": metadata.get("finished_at", ""),
                    "metadata": metadata,
                }
            )

    runs.sort(key=lambda item: int(PREPARATION_ID_PATTERN.match(item["prep_id"]).group(2)))  # type: ignore[union-attr]
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "target": normalized_target,
        "runs": runs,
        "message": "preparation 记录列表已读取。",
        "error": None,
    }


def load_preparation_metadata(project_dir: str, target: str, prep_id: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)
    prep_id_error = _validate_prep_id_for_target(normalized_target, prep_id)
    if prep_id_error:
        return prep_id_error

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser().resolve()
    try:
        metadata_path = _safe_project_path(
            project_path,
            PREPARATION_RECORD_ROOT / prep_id / "metadata.json",
        )
    except PreparationPathError as exc:
        return _error("PREPARATION_PATH_UNSAFE", "preparation metadata 路径不安全。", raw_error=str(exc))
    if not metadata_path.is_file():
        return _error(
            "PREPARATION_METADATA_NOT_FOUND",
            "没有找到 preparation metadata.json。",
            raw_error=str(metadata_path),
            suggestion="请先执行一次自动准备，或确认 prep_id 是否正确。",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error(
            "PREPARATION_METADATA_INVALID",
            "preparation metadata.json 不是有效 JSON。",
            raw_error=str(exc),
            suggestion="请检查该 preparation 记录是否被手动修改。",
        )

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "target": normalized_target,
        "prep_id": prep_id,
        "metadata_file": _relative_path(metadata_path, project_path),
        "metadata": metadata,
        "message": "preparation metadata 已读取。",
        "error": None,
    }


def get_latest_preparation(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    latest = project.latest_preparation.get(normalized_target, "")
    if latest:
        loaded = load_preparation_metadata(project.project_dir, normalized_target, latest)
        if loaded.get("ok"):
            loaded["latest"] = True
            return loaded

    runs = list_preparation_runs(project.project_dir, normalized_target)
    if not runs.get("ok"):
        return runs
    run_items = runs.get("runs", [])
    if not run_items:
        return {
            "ok": True,
            "project_dir": project.project_dir,
            "target": normalized_target,
            "prep_id": "",
            "metadata": None,
            "latest": False,
            "message": "当前还没有 preparation 记录。",
            "error": None,
        }

    latest_item = run_items[-1]
    loaded = load_preparation_metadata(project.project_dir, normalized_target, latest_item["prep_id"])
    if loaded.get("ok"):
        loaded["latest"] = True
    return loaded


def reset_preparation_status(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project_path = Path(project_dir).expanduser().resolve()
    try:
        with _preparation_target_lock(project_path, normalized_target):
            with _project_lock(project_path):
                data, _, _ = _read_and_migrate_project_unlocked(project_path, persist_migration=True)
                project = _project_from_dict(data, project_path)
                busy = _preparation_busy_error(project, normalized_target)
                if busy:
                    return busy
                setattr(project.preparation, normalized_target, default_preparation_result(normalized_target))
                project.updated_at = _now_iso()
                project.revision += 1
                _write_project_json_unlocked(project_path, project)
    except Exception as exc:  # noqa: BLE001 - return a structured reset error.
        return _error(
            "PREPARATION_RESET_ERROR",
            "重置 preparation 状态时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目目录可写且没有正在运行的同类型准备任务。",
        )

    # Reset changes only the persisted task state. It must not relaunch the
    # Python/RDKit/Meeko capability probes merely to rebuild the UI response.
    payload = get_preparation_status(str(project_path), tools_snapshot={})
    payload["tools"] = None
    payload["target"] = normalized_target
    payload["message"] = "准备状态已重置。prepared PDBQT 文件不会被删除。"
    return payload


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"

    if command == "status":
        if len(sys.argv) < 3:
            _print_json(_error("PREPARATION_STATUS_ARGS", "读取准备状态需要 project_dir 参数。"))
            return
        _print_json(get_preparation_status(sys.argv[2]))
        return

    if command == "validate":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_VALIDATE_ARGS", "准备前置检查需要 project_dir 和 target 参数。"))
            return
        _print_json(validate_preparation_prerequisites(sys.argv[2], sys.argv[3]))
        return

    if command == "tool-status":
        if len(sys.argv) < 3:
            _print_json(_error("PREPARATION_TOOL_STATUS_ARGS", "读取准备工具能力需要 project_dir 参数。"))
            return
        _print_json(get_preparation_tool_status(sys.argv[2]))
        return

    if command == "prepare-ligand":
        if len(sys.argv) < 3:
            _print_json(_error("LIGAND_PREPARATION_ARGS", "准备 ligand PDBQT 需要 project_dir 参数。"))
            return
        overwrite = len(sys.argv) >= 4 and sys.argv[3].strip().lower() in {"1", "true", "yes", "y"}
        _print_json(prepare_ligand_pdbqt(sys.argv[2], overwrite=overwrite))
        return

    if command == "ligand-log":
        if len(sys.argv) < 3:
            _print_json(_error("LIGAND_PREPARATION_LOG_ARGS", "读取 ligand preparation 日志需要 project_dir 参数。"))
            return
        _print_json(load_ligand_preparation_log(sys.argv[2]))
        return

    if command == "prepare-receptor":
        if len(sys.argv) < 3:
            _print_json(_error("RECEPTOR_PREPARATION_ARGS", "准备 receptor PDBQT 需要 project_dir 参数。"))
            return
        overwrite = len(sys.argv) >= 4 and sys.argv[3].strip().lower() in {"1", "true", "yes", "y"}
        _print_json(prepare_receptor_pdbqt(sys.argv[2], overwrite=overwrite))
        return

    if command == "receptor-log":
        if len(sys.argv) < 3:
            _print_json(_error("RECEPTOR_PREPARATION_LOG_ARGS", "读取 receptor preparation 日志需要 project_dir 参数。"))
            return
        _print_json(load_receptor_preparation_log(sys.argv[2]))
        return

    if command == "list-runs":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_LIST_ARGS", "列出 preparation 记录需要 project_dir 和 target 参数。"))
            return
        _print_json(list_preparation_runs(sys.argv[2], sys.argv[3]))
        return

    if command == "metadata":
        if len(sys.argv) < 5:
            _print_json(_error("PREPARATION_METADATA_ARGS", "读取 preparation metadata 需要 project_dir、target 和 prep_id 参数。"))
            return
        _print_json(load_preparation_metadata(sys.argv[2], sys.argv[3], sys.argv[4]))
        return

    if command == "latest":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_LATEST_ARGS", "读取 latest preparation 需要 project_dir 和 target 参数。"))
            return
        _print_json(get_latest_preparation(sys.argv[2], sys.argv[3]))
        return

    if command == "reset":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_RESET_ARGS", "重置准备状态需要 project_dir 和 target 参数。"))
            return
        _print_json(reset_preparation_status(sys.argv[2], sys.argv[3]))
        return

    _print_json(_error("PREPARATION_COMMAND_UNKNOWN", f"未知准备命令：{command}"))


if __name__ == "__main__":
    main()
