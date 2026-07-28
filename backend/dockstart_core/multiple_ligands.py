"""Experimental simultaneous multi-ligand docking workflow.

This module models one AutoDock Vina search containing exactly two ligands.
It is deliberately separate from :mod:`dockstart_core.screening`, where each
ligand is executed as an independent Vina process.  A score produced here
belongs to the joint two-ligand pose and is never presented as a per-ligand
contribution.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import sys
import threading
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from adapters import vina_adapter
from dockstart_core import __version__
from dockstart_core.persistence import (
    atomic_write_bytes,
    atomic_write_text,
)
from dockstart_core.project import (
    RUN_ID_PATTERN,
    _error,
    _cancel_marker_path,
    _create_cancel_marker,
    _duration_seconds,
    _format_config_number,
    _hash_snapshot,
    _now_iso,
    _parse_pdbqt_stats,
    _project_from_dict,
    _project_grid_source,
    _project_receptor_mode,
    _project_run_mode,
    _project_scoring_protocol,
    _read_run_metadata,
    _safe_run_directory,
    _safe_runs_directory,
    _sha256_file,
    _validate_vina_grid_resource,
    _write_run_metadata,
    _update_run_metadata_transaction,
    get_next_run_id,
    load_project,
    parse_vina_log_text,
    save_project,
    update_project_run_summary,
    validate_box_params,
    validate_pdbqt_file,
    validate_vina_params,
    validate_vina_runtime_capabilities,
)
from dockstart_core.settings import load_settings
from dockstart_core.viewer import _viewer_content
from dockstart_core.viewer_models import ViewerStructureResult

PROTOCOL_ID = "simultaneous_multi_ligand"
PROTOCOL_NAME = "多配体共同对接（实验性）"
SCHEMA_VERSION = 1
MINIMUM_VINA_VERSION = "1.2.0"
MEMBER_COUNT = 2
RUN_OUTPUT_FILE = "out.pdbqt"
RUN_SCORES_FILE = "scores.csv"
RUN_JOINT_POSES_FILE = "joint_poses.json"
RUN_REPORT_FILE = "multi_ligand_report.md"
PROJECT_SCORES_FILE = Path(
    "results",
    "simultaneous_multi_ligand_scores.csv",
).as_posix()
PROJECT_REPORT_FILE = Path(
    "reports",
    "simultaneous_multi_ligand_report.md",
).as_posix()
JOINT_SCORE_DISCLAIMER = (
    "本协议的 affinity 与 RMSD 均属于两个配体组成的联合构象；"
    "DockStart 不提供单个配体的独立评分贡献。"
)
SCIENTIFIC_DISCLAIMER = (
    "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODEL_PATTERN = re.compile(r"^\s*MODEL\s+(\d+)\s*$", re.IGNORECASE)
ENDMDL_PATTERN = re.compile(r"^\s*ENDMDL\s*$", re.IGNORECASE)
VINA_RESULT_PATTERN = re.compile(
    r"^\s*REMARK\s+VINA\s+RESULT:\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))",
    re.IGNORECASE,
)


class _ProtocolPathError(RuntimeError):
    """Raised when a protocol artifact path is not an ordinary project path."""


def _protocol_error(
    code: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return _error(code, message, raw_error, suggestion)


def _clean_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        key: stats.get(key)
        for key in (
            "atom_count",
            "coordinate_count",
            "coordinate_bounds",
            "coordinate_center",
            "chains",
            "atom_types",
            "torsdof",
            "identity_sha256",
        )
    }


def _joint_atom_types(members: list[dict[str, Any]]) -> list[str]:
    atom_types: set[str] = set()
    for member in members:
        stats = (
            member.get("stats")
            if isinstance(member.get("stats"), dict)
            else member
        )
        if not isinstance(stats, dict):
            continue
        atom_types.update(
            str(value or "").strip().upper()
            for value in stats.get("atom_types") or []
            if str(value or "").strip()
        )
    return sorted(atom_types)


def _validate_joint_grid_resource(
    box: dict[str, Any],
    vina: dict[str, Any],
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        spacing = float(vina.get("spacing"))
        force_even_voxels = bool(vina.get("force_even_voxels", False))
    except (TypeError, ValueError) as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_GRID_CONFIGURATION_INVALID",
            "无法读取共同对接冻结的 Box 或网格 spacing。",
            raw_error=str(exc),
        )
    atom_types = _joint_atom_types(members)
    if not atom_types:
        return _protocol_error(
            "MULTIPLE_LIGAND_GRID_ATOM_TYPES_MISSING",
            "两个配体都没有可用于 Vina 网格估算的原子类型。",
            suggestion="请重新准备包含有效 AutoDock 原子类型的配体 PDBQT。",
        )
    validation = _validate_vina_grid_resource(
        box,
        spacing,
        atom_types,
        force_even_voxels,
    )
    if not validation.get("ok"):
        detail = validation.get("error") or {}
        return _protocol_error(
            "MULTIPLE_LIGAND_GRID_RESOURCE_LIMIT_EXCEEDED",
            (
                "两个配体原子类型并集所需的 Vina 网格预计超过 "
                "DockStart 的 2 GiB 保护上限。"
            ),
            raw_error=str(detail.get("raw_error") or ""),
            suggestion=str(
                detail.get("suggestion")
                or "请缩小对接箱体，或适当增大 spacing 后重试。"
            ),
        )
    return {
        "ok": True,
        "grid_estimate": dict(validation["grid_estimate"]),
        "warnings": list(validation.get("warnings") or []),
        "error": None,
    }


def _pdbqt_atom_serial(line: str) -> str:
    fixed = line[6:11].strip()
    if fixed:
        return fixed
    parts = line.split()
    return parts[1].strip() if len(parts) > 1 else ""


def _normalized_partial_charge(line: str) -> str:
    """Return a finite, representation-independent PDBQT partial charge."""

    parts = line.split()
    candidates = [
        parts[-2].strip() if len(parts) >= 2 else "",
        line[70:76].strip() if len(line) >= 76 else "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value = Decimal(candidate)
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    raise ValueError(
        "ATOM/HETATM 记录缺少可解析为有限数的 PDBQT partial charge。"
    )


def _normalized_branch_endpoint(
    value: str,
    serial_to_order: dict[str, int],
) -> int | str:
    stripped = str(value or "").strip()
    if stripped in serial_to_order:
        return serial_to_order[stripped]
    try:
        return int(stripped)
    except ValueError:
        return stripped.upper()


def _pdbqt_identity_sha256(lines: list[str]) -> str:
    """Hash ordered atom identities and torsion topology, never coordinates."""

    serial_to_order: dict[str, int] = {}
    atom_order = 0
    for line in lines:
        record = line[:6].strip().upper()
        if record not in {"ATOM", "HETATM"}:
            continue
        atom_order += 1
        serial = _pdbqt_atom_serial(line)
        if serial and serial not in serial_to_order:
            serial_to_order[serial] = atom_order

    identities: list[list[Any]] = []
    for line in lines:
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM"}:
            parts = line.split()
            identities.append(
                [
                    "ATOM_IDENTITY",
                    record,
                    line[12:16].strip(),
                    line[17:20].strip(),
                    line[21:22].strip(),
                    line[22:26].strip(),
                    line[26:27].strip(),
                    _normalized_partial_charge(line),
                    parts[-1].strip().upper() if parts else "",
                ]
            )
            continue
        tokens = line.split()
        keyword = tokens[0].upper() if tokens else ""
        if keyword in {"ROOT", "ENDROOT"}:
            identities.append([keyword])
        elif keyword in {"BRANCH", "ENDBRANCH"}:
            identities.append(
                [
                    keyword,
                    _normalized_branch_endpoint(
                        tokens[1] if len(tokens) > 1 else "",
                        serial_to_order,
                    ),
                    _normalized_branch_endpoint(
                        tokens[2] if len(tokens) > 2 else "",
                        serial_to_order,
                    ),
                ]
            )
        elif keyword == "TORSDOF":
            raw_value = tokens[1] if len(tokens) > 1 else ""
            try:
                normalized_torsdof: int | str = int(raw_value)
            except ValueError:
                normalized_torsdof = raw_value.upper()
            identities.append(["TORSDOF", normalized_torsdof])
    canonical = json.dumps(
        identities,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _pdbqt_file_identity_sha256(path: Path) -> str:
    return _pdbqt_identity_sha256(
        path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    )


def _relative_snapshot(
    path: Path,
    relative_path: str,
    *,
    ligand: bool,
) -> dict[str, Any]:
    stats = _parse_pdbqt_stats(
        path,
        relative_path,
        ligand=ligand,
    )
    if ligand:
        stats["identity_sha256"] = _pdbqt_file_identity_sha256(path)
    stats.pop("absolute_path", None)
    return stats


def _parse_version(value: str) -> tuple[int, int, int, bool] | None:
    match = re.search(
        r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?"
        r"(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?",
        str(value or ""),
    )
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
        match.group(4) is None,
    )


def _version_is_supported(version: str) -> bool:
    parsed = _parse_version(version)
    minimum = _parse_version(MINIMUM_VINA_VERSION)
    return parsed is not None and minimum is not None and parsed >= minimum


def _resolve_executable(path_value: str) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        discovered = shutil.which(path_value)
        if discovered:
            path = Path(discovered)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _vina_binary_snapshot(detection: Any) -> dict[str, Any] | None:
    resolved = _resolve_executable(str(getattr(detection, "path", "") or ""))
    if resolved is None:
        return None
    snapshot = _hash_snapshot(resolved)
    return {
        "path": str(resolved),
        "version": str(getattr(detection, "version", "") or ""),
        "source": str(getattr(detection, "source", "") or ""),
        "size_bytes": int(snapshot.get("size_bytes") or 0),
        "sha256": str(snapshot.get("sha256") or "").lower(),
    }


def _fixed_run_relative(run_id: str, *parts: str) -> str:
    return Path("runs", run_id, *parts).as_posix()


def _path_is_link_or_reparse(path: Path) -> bool:
    """Detect POSIX symlinks and Windows junction/reparse points."""

    try:
        details = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0) or 0)
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0
    )
    return stat.S_ISLNK(details.st_mode) or bool(
        reparse_flag and attributes & reparse_flag
    )


def _safe_project_artifact_path(
    project_root: Path,
    relative_path: str | Path,
    *,
    allow_missing: bool = True,
) -> Path:
    """Resolve a lexical project path while rejecting every linked component."""

    root = project_root.expanduser().resolve(strict=True)
    supplied = Path(relative_path)
    if supplied.is_absolute():
        raise _ProtocolPathError(f"协议产物路径必须是项目相对路径：{supplied}")
    lexical = Path(os.path.abspath(root / supplied))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise _ProtocolPathError(
            f"协议产物路径越出项目目录：{supplied}"
        ) from exc

    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _path_is_link_or_reparse(current):
            raise _ProtocolPathError(
                "协议产物路径包含符号链接、junction 或 reparse point："
                f"{current}"
            )

    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _ProtocolPathError(
            f"协议产物路径重解析到项目目录外：{supplied}"
        ) from exc
    if not allow_missing and not lexical.exists():
        raise _ProtocolPathError(f"协议产物路径不存在：{lexical}")
    return lexical


class _VerifiedArtifactParent:
    """A target whose parent hierarchy stays anchored during publication."""

    def __init__(
        self,
        target: Path,
        *,
        parent_fd: int | None = None,
    ) -> None:
        self.target = target
        self.parent_fd = parent_fd

    def write_bytes(self, payload: bytes) -> None:
        if self.parent_fd is None:
            atomic_write_bytes(self.target, payload)
            _windows_verify_published_file(self.target)
            return
        _posix_atomic_write_bytes(
            self.parent_fd,
            self.target.name,
            payload,
        )


def _windows_directory_handle(path: Path) -> tuple[int, tuple[int, int]]:
    """Open and identify a Windows directory without following reparse data."""

    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FileTime),
            ("ftLastAccessTime", _FileTime),
            ("ftLastWriteTime", _FileTime),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    file_read_attributes = 0x0080
    delete_access = 0x00010000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    open_existing = 3
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value

    handle = create_file(
        str(path),
        file_read_attributes | delete_access,
        file_share_read | file_share_write,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    handle_value = int(handle) if handle is not None else invalid_handle
    if handle_value == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())

    information = _ByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise error
    attributes = int(information.dwFileAttributes)
    if not attributes & file_attribute_directory:
        close_handle(handle)
        raise _ProtocolPathError(f"协议产物父路径不是目录：{path}")
    if attributes & file_attribute_reparse_point:
        close_handle(handle)
        raise _ProtocolPathError(
            f"协议产物父路径是 reparse point：{path}"
        )
    identity = (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32)
        | int(information.nFileIndexLow),
    )
    return handle_value, identity


def _windows_close_handle(handle: int) -> None:
    from ctypes import wintypes

    close_handle = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


def _windows_verify_published_file(path: Path) -> None:
    """Reject a target leaf that was replaced by a link during publication."""

    if not os.path.lexists(path):
        raise _ProtocolPathError(f"协议产物写入后不存在：{path}")
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0) or 0)
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0
    )
    if stat.S_ISLNK(details.st_mode) or bool(
        reparse_flag and attributes & reparse_flag
    ):
        raise _ProtocolPathError(
            f"协议产物写入后变成链接或 reparse point：{path}"
        )
    if not stat.S_ISREG(details.st_mode):
        raise _ProtocolPathError(f"协议产物写入后不是普通文件：{path}")


def _posix_atomic_write_bytes(
    parent_fd: int,
    target_name: str,
    payload: bytes,
) -> None:
    """Publish relative to an already verified directory descriptor."""

    no_follow = int(getattr(os, "O_NOFOLLOW", 0) or 0)
    close_on_exec = int(getattr(os, "O_CLOEXEC", 0) or 0)
    temporary_name = ""
    temporary_fd: int | None = None
    try:
        for attempt in range(100):
            candidate = (
                f".{target_name}.{os.getpid()}."
                f"{threading.get_ident()}.{time.time_ns()}.{attempt}.tmp"
            )
            try:
                temporary_fd = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | no_follow
                    | close_on_exec,
                    0o600,
                    dir_fd=parent_fd,
                )
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if temporary_fd is None:
            raise FileExistsError("无法分配安全的协议产物临时文件。")

        view = memoryview(payload)
        written = 0
        while written < len(view):
            written += os.write(temporary_fd, view[written:])
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = None
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_name = ""
        try:
            os.fsync(parent_fd)
        except OSError:
            pass

        published_fd = os.open(
            target_name,
            os.O_RDONLY | no_follow | close_on_exec,
            dir_fd=parent_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(published_fd).st_mode):
                raise _ProtocolPathError(
                    f"协议产物写入后不是普通文件：{target_name}"
                )
        finally:
            os.close(published_fd)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def _write_bytes_to_verified_parent(
    parent: _VerifiedArtifactParent,
    payload: bytes,
) -> None:
    """Inner publication entry kept separate for deterministic race tests."""

    parent.write_bytes(payload)


@contextmanager
def _verified_artifact_parent(
    project_root: Path,
    relative_path: str | Path,
) -> Iterator[_VerifiedArtifactParent]:
    """Hold every parent directory while a project artifact is published."""

    target = _safe_project_artifact_path(
        project_root,
        relative_path,
        allow_missing=True,
    )
    root = project_root.expanduser().resolve(strict=True)
    relative = target.relative_to(root)
    if not relative.parts or relative.name in {"", ".", ".."}:
        raise _ProtocolPathError(
            f"协议产物路径必须指向项目内文件：{relative_path}"
        )
    parent_parts = relative.parts[:-1]

    if os.name == "nt":
        handles: list[tuple[Path, int, tuple[int, int]]] = []
        current = root
        try:
            handle, identity = _windows_directory_handle(current)
            handles.append((current, handle, identity))
            for part in parent_parts:
                current = current / part
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                handle, identity = _windows_directory_handle(current)
                handles.append((current, handle, identity))

            for directory, _, expected_identity in handles:
                observed = os.stat(directory, follow_symlinks=False)
                if int(observed.st_ino) != expected_identity[1]:
                    raise _ProtocolPathError(
                        "协议产物父目录身份在写入前发生变化："
                        f"{directory}"
                    )
            if os.path.lexists(target) and _path_is_link_or_reparse(target):
                raise _ProtocolPathError(
                    f"协议产物目标是链接或 reparse point：{target}"
                )
            yield _VerifiedArtifactParent(target)
        finally:
            for _, handle, _ in reversed(handles):
                _windows_close_handle(handle)
        return

    directory_flags = (
        os.O_RDONLY
        | int(getattr(os, "O_DIRECTORY", 0) or 0)
        | int(getattr(os, "O_NOFOLLOW", 0) or 0)
        | int(getattr(os, "O_CLOEXEC", 0) or 0)
    )
    directory_fds: list[int] = []
    try:
        current_fd = os.open(root, directory_flags)
        directory_fds.append(current_fd)
        for part in parent_parts:
            try:
                child_fd = os.open(
                    part,
                    directory_flags,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(
                    part,
                    directory_flags,
                    dir_fd=current_fd,
                )
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                os.close(child_fd)
                raise _ProtocolPathError(
                    f"协议产物父路径不是目录：{part}"
                )
            directory_fds.append(child_fd)
            current_fd = child_fd

        try:
            target_details = os.stat(
                relative.name,
                dir_fd=current_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            target_details = None
        if target_details is not None and stat.S_ISLNK(target_details.st_mode):
            raise _ProtocolPathError(
                f"协议产物目标是符号链接：{relative_path}"
            )
        yield _VerifiedArtifactParent(
            target,
            parent_fd=current_fd,
        )
    finally:
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _safe_atomic_write_text(
    project_root: Path,
    relative_path: str | Path,
    payload: str,
) -> Path:
    with _verified_artifact_parent(
        project_root,
        relative_path,
    ) as parent:
        _write_bytes_to_verified_parent(parent, payload.encode("utf-8"))
        return _safe_project_artifact_path(
            project_root,
            relative_path,
            allow_missing=False,
        )


def _safe_atomic_write_json(
    project_root: Path,
    relative_path: str | Path,
    payload: dict[str, Any] | list[Any],
) -> Path:
    return _safe_atomic_write_text(
        project_root,
        relative_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _build_command(vina_path: str, run_id: str) -> list[str]:
    """Build the official multitoken ``--ligand`` command shape."""

    return [
        vina_path,
        "--config",
        _fixed_run_relative(run_id, "config_snapshot.txt"),
        "--ligand",
        _fixed_run_relative(run_id, "inputs", "ligand_001.pdbqt"),
        _fixed_run_relative(run_id, "inputs", "ligand_002.pdbqt"),
        "--out",
        _fixed_run_relative(run_id, RUN_OUTPUT_FILE),
    ]


def _build_config(project: Any, run_id: str) -> str:
    receptor = _fixed_run_relative(run_id, "inputs", "receptor.pdbqt")
    vina = project.vina
    box = project.box
    lines = [
        f"receptor = {receptor}",
        f"scoring = {vina.scoring}",
        "",
        f"center_x = {_format_config_number(box.center_x)}",
        f"center_y = {_format_config_number(box.center_y)}",
        f"center_z = {_format_config_number(box.center_z)}",
        "",
        f"size_x = {_format_config_number(box.size_x)}",
        f"size_y = {_format_config_number(box.size_y)}",
        f"size_z = {_format_config_number(box.size_z)}",
        "",
        f"exhaustiveness = {vina.exhaustiveness}",
        f"max_evals = {vina.max_evals}",
        f"num_modes = {vina.num_modes}",
        f"min_rmsd = {_format_config_number(vina.min_rmsd)}",
        f"energy_range = {_format_config_number(vina.energy_range)}",
        f"cpu = {vina.cpu}",
        f"spacing = {_format_config_number(vina.spacing)}",
    ]
    if vina.no_refine:
        lines.append("no_refine = true")
    if vina.force_even_voxels:
        lines.append("force_even_voxels = true")
    lines.append(f"verbosity = {vina.verbosity}")
    if vina.seed is not None:
        lines.append(f"seed = {vina.seed}")
    return "\n".join(lines) + "\n"


def _project_protocol_validation(project: Any) -> dict[str, Any] | None:
    protocol = (
        project.preserved_data.get("docking_protocol")
        if isinstance(project.preserved_data, dict)
        else {}
    )
    protocol = protocol if isinstance(protocol, dict) else {}
    if _project_scoring_protocol(project) != "vina":
        return _protocol_error(
            "MULTIPLE_LIGAND_SCORING_PROTOCOL_UNSUPPORTED",
            "多配体共同对接（实验性）首版只支持 Vina 或 Vinardo 评分。",
            suggestion="请关闭 AutoDock4/AD4Zn 协议后重新准备本次共同对接。",
        )
    if _project_grid_source(project) != "receptor":
        return _protocol_error(
            "MULTIPLE_LIGAND_PRECOMPUTED_MAPS_UNSUPPORTED",
            "多配体共同对接（实验性）首版不支持预计算 maps。",
            suggestion="请切换为直接从刚性受体建立网格的 Vina/Vinardo 全局搜索。",
        )
    if _project_receptor_mode(project) != "rigid":
        return _protocol_error(
            "MULTIPLE_LIGAND_RIGID_RECEPTOR_REQUIRED",
            "多配体共同对接（实验性）首版只支持刚性受体。",
            suggestion="请关闭有限柔性侧链后重新准备本次共同对接。",
        )
    if _project_run_mode(project) != "dock":
        return _protocol_error(
            "MULTIPLE_LIGAND_GLOBAL_SEARCH_REQUIRED",
            "多配体共同对接（实验性）首版只支持全局搜索。",
            suggestion="请关闭 score_only/local_only 后重新准备本次共同对接。",
        )
    if str(protocol.get("autobox") or "").strip().lower() in {"true", "1"}:
        return _protocol_error(
            "MULTIPLE_LIGAND_AUTOBOX_UNSUPPORTED",
            "多配体共同对接（实验性）必须使用项目中明确保存的 Box。",
            suggestion="请回到对接工作台设置 Box 中心和尺寸。",
        )
    return None


def _parse_branch_pair(
    tokens: list[str],
    *,
    line_number: int,
) -> tuple[tuple[int, int] | None, str | None]:
    if len(tokens) != 3:
        return None, f"第 {line_number} 行必须包含两个 BRANCH 端点。"
    try:
        pair = (int(tokens[1]), int(tokens[2]))
    except ValueError:
        return None, f"第 {line_number} 行的 BRANCH 端点不是整数。"
    if pair[0] <= 0 or pair[1] <= 0:
        return None, f"第 {line_number} 行的 BRANCH 端点必须是正整数。"
    return pair, None


def _validate_single_ligand_torsion_tree(
    lines: list[str],
    member_index: int,
) -> dict[str, Any]:
    """Validate one complete, non-MODE PDBQT ligand torsion tree."""

    records = [
        (line_number, line, line.split())
        for line_number, line in enumerate(lines, start=1)
        if line.strip()
    ]
    for line_number, _, tokens in records:
        keyword = tokens[0].upper()
        if keyword in {"MODEL", "ENDMDL"}:
            return _protocol_error(
                "MULTIPLE_LIGAND_INPUT_MODEL_FORBIDDEN",
                (
                    f"第 {member_index} 个配体包含 {keyword}；"
                    "输入必须是单一配体 PDBQT，而不是多 MODEL 文件。"
                ),
                raw_error=f"line={line_number}",
                suggestion="请拆分并选择单个已准备配体 PDBQT。",
            )

    root_records = [
        line_number
        for line_number, _, tokens in records
        if tokens[0].upper() == "ROOT"
    ]
    endroot_records = [
        line_number
        for line_number, _, tokens in records
        if tokens[0].upper() == "ENDROOT"
    ]
    if len(root_records) != 1 or len(endroot_records) != 1:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_ROOT_COUNT_INVALID",
            (
                f"第 {member_index} 个配体必须恰好包含一个 ROOT "
                "和一个 ENDROOT。"
            ),
            raw_error=(
                f"ROOT={root_records}; ENDROOT={endroot_records}"
            ),
            suggestion="请使用 Meeko 等工具重新准备完整的单配体 PDBQT。",
        )

    torsdof_records = [
        (line_number, tokens)
        for line_number, _, tokens in records
        if tokens[0].upper() == "TORSDOF"
    ]
    if len(torsdof_records) != 1:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            f"第 {member_index} 个配体必须恰好包含一条 TORSDOF。",
            raw_error=f"TORSDOF lines={[item[0] for item in torsdof_records]}",
            suggestion="请重新准备并保留完整 PDBQT 扭转树。",
        )
    torsdof_line, torsdof_tokens = torsdof_records[0]
    if (
        len(torsdof_tokens) != 2
        or re.fullmatch(r"[+-]?\d+", torsdof_tokens[1]) is None
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            f"第 {member_index} 个配体的 TORSDOF 必须是单个非负整数。",
            raw_error=f"line {torsdof_line}: {' '.join(torsdof_tokens)}",
        )
    torsdof = int(torsdof_tokens[1])
    if torsdof < 0:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            f"第 {member_index} 个配体的 TORSDOF 不能为负数。",
            raw_error=f"line {torsdof_line}: {torsdof}",
        )
    if torsdof_line != records[-1][0]:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_TORSDOF_NOT_TERMINAL",
            f"第 {member_index} 个配体的 TORSDOF 必须是最后一条非空记录。",
            raw_error=(
                f"TORSDOF line={torsdof_line}; "
                f"last line={records[-1][0]}"
            ),
            suggestion="请移除 TORSDOF 后的附加记录，或重新准备配体。",
        )

    atom_serials: set[int] = set()
    for line_number, line, tokens in records:
        if tokens[0].upper() not in {"ATOM", "HETATM"}:
            continue
        raw_serial = _pdbqt_atom_serial(line)
        try:
            serial = int(raw_serial)
        except ValueError:
            return _protocol_error(
                "MULTIPLE_LIGAND_INPUT_ATOM_SERIAL_INVALID",
                f"第 {member_index} 个配体包含非整数原子 serial。",
                raw_error=f"line={line_number}; serial={raw_serial}",
            )
        if serial <= 0 or serial in atom_serials:
            return _protocol_error(
                "MULTIPLE_LIGAND_INPUT_ATOM_SERIAL_INVALID",
                (
                    f"第 {member_index} 个配体的原子 serial "
                    "必须是唯一正整数。"
                ),
                raw_error=f"line={line_number}; serial={serial}",
            )
        atom_serials.add(serial)

    root_open = False
    root_closed = False
    root_atom_count = 0
    branch_stack: list[tuple[int, int]] = []
    branch_count = 0
    for line_number, _, tokens in records:
        keyword = tokens[0].upper()
        if keyword == "ROOT":
            if len(tokens) != 1 or root_open or root_closed or branch_stack:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_ROOT_ORDER_INVALID",
                    f"第 {member_index} 个配体的 ROOT/ENDROOT 顺序无效。",
                    raw_error=f"line={line_number}",
                )
            root_open = True
        elif keyword == "ENDROOT":
            if len(tokens) != 1 or not root_open or branch_stack:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_ROOT_ORDER_INVALID",
                    f"第 {member_index} 个配体的 ROOT/ENDROOT 顺序无效。",
                    raw_error=f"line={line_number}",
                )
            root_open = False
            root_closed = True
        elif keyword == "BRANCH":
            pair, pair_error = _parse_branch_pair(
                tokens,
                line_number=line_number,
            )
            if pair_error or not root_closed or root_open:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
                    f"第 {member_index} 个配体的 BRANCH 扭转树无效。",
                    raw_error=pair_error or f"line={line_number}",
                )
            assert pair is not None
            if pair[0] == pair[1]:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_ENDPOINT_INVALID",
                    (
                        f"第 {member_index} 个配体的 BRANCH 不能"
                        "把同一个原子同时作为父端点和子端点。"
                    ),
                    raw_error=f"line={line_number}; pair={pair}",
                    suggestion="请使用 Meeko 等工具重新准备完整的配体扭转树。",
                )
            if pair[0] not in atom_serials or pair[1] not in atom_serials:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_ENDPOINT_INVALID",
                    (
                        f"第 {member_index} 个配体的 BRANCH 端点"
                        "必须引用实际 ATOM/HETATM serial。"
                    ),
                    raw_error=f"line={line_number}; pair={pair}",
                )
            branch_stack.append(pair)
            branch_count += 1
        elif keyword == "ENDBRANCH":
            pair, pair_error = _parse_branch_pair(
                tokens,
                line_number=line_number,
            )
            if (
                pair_error
                or not branch_stack
                or pair != branch_stack[-1]
            ):
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
                    (
                        f"第 {member_index} 个配体的 ENDBRANCH "
                        "没有匹配最近打开的 BRANCH。"
                    ),
                    raw_error=(
                        pair_error
                        or (
                            f"line={line_number}; observed={pair}; "
                            f"expected={branch_stack[-1] if branch_stack else None}"
                        )
                    ),
                )
            assert pair is not None
            if pair[0] not in atom_serials or pair[1] not in atom_serials:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_ENDPOINT_INVALID",
                    (
                        f"第 {member_index} 个配体的 ENDBRANCH 端点"
                        "必须引用实际 ATOM/HETATM serial。"
                    ),
                    raw_error=f"line={line_number}; pair={pair}",
                )
            branch_stack.pop()
        elif keyword in {"ATOM", "HETATM"}:
            if root_open:
                root_atom_count += 1
            elif not branch_stack:
                return _protocol_error(
                    "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
                    (
                        f"第 {member_index} 个配体第 {line_number} 行的"
                        "原子不在 ROOT 或 BRANCH 中。"
                    ),
                )

    if root_open or not root_closed or root_atom_count <= 0:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_ROOT_ORDER_INVALID",
            f"第 {member_index} 个配体的 ROOT 块未闭合或不含原子。",
        )
    if branch_stack:
        return _protocol_error(
            "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
            f"第 {member_index} 个配体存在未闭合的 BRANCH。",
            raw_error=f"open branches={branch_stack}",
        )
    return {
        "ok": True,
        "torsdof": torsdof,
        "branch_count": branch_count,
        "root_atom_count": root_atom_count,
        "error": None,
    }


def _validate_source_ligand(
    path_value: str,
    member_index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(path_value, str) or not path_value.strip():
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_PATH_INVALID",
            f"第 {member_index} 个配体路径为空。",
            suggestion="请选择两个已经准备好的 PDBQT 配体。",
        )
    validation = validate_pdbqt_file(path_value)
    if not validation.get("ok"):
        error = dict(validation.get("error") or {})
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_INVALID",
            f"第 {member_index} 个配体 PDBQT 无法使用。",
            raw_error=str(error.get("raw_error") or path_value),
            suggestion=str(
                error.get("suggestion")
                or "请重新选择已经准备好的非空 PDBQT 配体。"
            ),
        )
    try:
        source = Path(path_value).expanduser().resolve(strict=True)
        source_text = source.read_text(
            encoding="utf-8",
            errors="strict",
        )
        tree_validation = _validate_single_ligand_torsion_tree(
            source_text.splitlines(),
            member_index,
        )
        if not tree_validation.get("ok"):
            return None, tree_validation
        stats = _parse_pdbqt_stats(
            source,
            source.name,
            ligand=True,
        )
        stats["identity_sha256"] = _pdbqt_identity_sha256(
            source_text.splitlines()
        )
    except ValueError as exc:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_PARTIAL_CHARGE_INVALID",
            f"第 {member_index} 个配体包含无效或缺失的 PDBQT 部分电荷。",
            raw_error=str(exc),
            suggestion="请重新准备该配体，并确认每个原子都有有限数值部分电荷。",
        )
    except Exception as exc:  # noqa: BLE001 - converted to a structured error.
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_READ_ERROR",
            f"读取第 {member_index} 个配体 PDBQT 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认文件没有被其他程序替换或锁定。",
        )
    if int(stats.get("atom_count") or 0) <= 0:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_NO_ATOMS",
            f"第 {member_index} 个配体没有可识别的 ATOM/HETATM 记录。",
            suggestion="请使用包含三维原子坐标的 Vina 配体 PDBQT。",
        )
    if stats.get("torsdof") is None:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_INPUT_TORSDOF_MISSING",
            f"第 {member_index} 个配体缺少有效 TORSDOF。",
            suggestion="请重新准备该配体 PDBQT，并保留完整的 Vina 扭转树。",
        )
    atom_types = {
        str(value or "").strip().upper()
        for value in stats.get("atom_types") or []
    }
    unsupported_atom_types = sorted(
        atom_type
        for atom_type in atom_types
        if atom_type == "W"
        or re.fullmatch(r"(?:G|CG)\d+", atom_type) is not None
    )
    if unsupported_atom_types:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_UNSUPPORTED_ATOM_TYPE",
            (
                f"第 {member_index} 个配体包含首版共同对接未验证的"
                "水合或大环伪原子类型。"
            ),
            raw_error=", ".join(unsupported_atom_types),
            suggestion=(
                "请改用不含 W、G* 或 CG* 伪原子的普通已准备配体；"
                "水合对接和大环协议需要独立基准与结果解释。"
            ),
        )
    return {
        "source": source,
        "source_name": source.name,
        "sha256": str(stats["sha256"]).lower(),
        "size_bytes": int(stats["size_bytes"]),
        "stats": _clean_stats(stats),
    }, None


def _source_display_name(
    project_root: Path,
    source: dict[str, Any],
) -> str:
    """Recover a user-facing staging label without trusting it as identity."""

    fallback = str(source.get("source_name") or "ligand.pdbqt")
    source_path = source.get("source")
    if not isinstance(source_path, Path):
        return fallback
    try:
        relative = source_path.relative_to(project_root).as_posix()
    except ValueError:
        return fallback
    if not re.fullmatch(
        r"screening/staging/[0-9a-f]{64}\.pdbqt",
        relative,
    ):
        return fallback
    index_path = project_root / "screening" / "staging" / "index.json"
    try:
        if (
            not index_path.is_file()
            or index_path.is_symlink()
            or index_path.resolve(strict=True) != index_path.absolute()
            or index_path.stat().st_size > 16 * 1024 * 1024
        ):
            return fallback
        index = json.loads(index_path.read_text(encoding="utf-8"))
        files = index.get("files") if isinstance(index, dict) else {}
        record = (
            files.get(str(source.get("sha256") or ""))
            if isinstance(files, dict)
            else {}
        )
        if (
            not isinstance(record, dict)
            or str(record.get("file") or "") != relative
            or str(record.get("sha256") or "").lower()
            != str(source.get("sha256") or "").lower()
            or int(record.get("size_bytes") or 0)
            != int(source.get("size_bytes") or 0)
        ):
            return fallback
        source_records = (
            record.get("source_records")
            if isinstance(record.get("source_records"), list)
            else []
        )
        first_source = (
            source_records[0]
            if source_records and isinstance(source_records[0], dict)
            else {}
        )
        return (
            str(record.get("source_record_name") or "").strip()
            or str(first_source.get("source_record_name") or "").strip()
            or str(record.get("original_name") or "").strip()
            or str(first_source.get("original_name") or "").strip()
            or fallback
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _expected_member_stat(
    member: dict[str, Any],
    key: str,
) -> Any:
    if key in member:
        return member.get(key)
    stats = member.get("stats")
    return stats.get(key) if isinstance(stats, dict) else None


def _member_block_stats(lines: list[str]) -> dict[str, Any]:
    atom_count = sum(
        1
        for line in lines
        if line[:6].strip().upper() in {"ATOM", "HETATM"}
    )
    torsdof: int | None = None
    for line in lines:
        if line.lstrip().upper().startswith("TORSDOF"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    torsdof = int(parts[1])
                except ValueError:
                    torsdof = None
    return {
        "atom_count": atom_count,
        "torsdof": torsdof,
        "identity_sha256": _pdbqt_identity_sha256(lines),
    }


def _parse_model(
    mode: int,
    lines: list[str],
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    score_matches = [
        VINA_RESULT_PATTERN.match(line)
        for line in lines
        if VINA_RESULT_PATTERN.match(line)
    ]
    if len(score_matches) != 1:
        return _protocol_error(
            "MULTIPLE_LIGAND_MODEL_SCORE_MISSING",
            f"联合构象 Mode {mode} 必须恰好包含一条 REMARK VINA RESULT。",
            raw_error=f"matches={len(score_matches)}",
            suggestion="请确认 out.pdbqt 来自本次多配体共同对接且没有被修改。",
        )
    score_match = score_matches[0]
    assert score_match is not None
    score_values = [float(score_match.group(index)) for index in (1, 2, 3)]
    if not all(math.isfinite(value) for value in score_values):
        return _protocol_error(
            "MULTIPLE_LIGAND_MODEL_SCORE_INVALID",
            f"联合构象 Mode {mode} 的评分或 RMSD 不是有限数字。",
        )

    torsdof_indices = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().upper().startswith("TORSDOF")
    ]
    if len(torsdof_indices) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_COUNT_INVALID",
            f"联合构象 Mode {mode} 必须恰好包含两个以 TORSDOF 结尾的成员块。",
            raw_error=f"TORSDOF blocks={len(torsdof_indices)}",
            suggestion="请确认 Vina 输出使用了一个 --ligand 后跟两个配体路径。",
        )

    parsed_members: list[dict[str, Any]] = []
    block_start = 0
    for offset, block_end in enumerate(torsdof_indices):
        segment = lines[block_start : block_end + 1]
        block_start = block_end + 1
        structural_start = next(
            (
                index
                for index, line in enumerate(segment)
                if line[:6].strip().upper() in {"ROOT", "ATOM", "HETATM"}
            ),
            None,
        )
        if structural_start is None:
            return _protocol_error(
                "MULTIPLE_LIGAND_MEMBER_BLOCK_INVALID",
                f"联合构象 Mode {mode} 的第 {offset + 1} 个成员没有结构记录。",
            )
        block_lines = segment[structural_start:]
        try:
            observed = _member_block_stats(block_lines)
        except ValueError as exc:
            return _protocol_error(
                "MULTIPLE_LIGAND_MEMBER_PARTIAL_CHARGE_INVALID",
                (
                    f"联合构象 Mode {mode} 的第 {offset + 1} 个成员"
                    "包含无效或缺失的 PDBQT 部分电荷。"
                ),
                raw_error=str(exc),
                suggestion="请确认 out.pdbqt 来自本次 Vina 运行且没有被修改。",
            )
        expected = members[offset]
        expected_atom_count = int(_expected_member_stat(expected, "atom_count") or 0)
        expected_torsdof = _expected_member_stat(expected, "torsdof")
        if (
            observed["atom_count"] != expected_atom_count
            or observed["torsdof"] != expected_torsdof
        ):
            return _protocol_error(
                "MULTIPLE_LIGAND_MEMBER_STATS_MISMATCH",
                (
                    f"联合构象 Mode {mode} 的第 {offset + 1} 个成员"
                    "与冻结输入的原子数或 TORSDOF 不一致。"
                ),
                raw_error=(
                    f"expected atoms={expected_atom_count}, TORSDOF={expected_torsdof}; "
                    f"observed atoms={observed['atom_count']}, "
                    f"TORSDOF={observed['torsdof']}"
                ),
                suggestion="请保留本次 run 供排查，并重新准备新的共同对接。",
            )
        expected_identity_sha256 = str(
            _expected_member_stat(expected, "identity_sha256") or ""
        ).lower()
        if (
            expected_identity_sha256
            and (
                not SHA256_PATTERN.fullmatch(expected_identity_sha256)
                or observed["identity_sha256"] != expected_identity_sha256
            )
        ):
            return _protocol_error(
                "MULTIPLE_LIGAND_MEMBER_IDENTITY_MISMATCH",
                (
                    f"联合构象 Mode {mode} 的第 {offset + 1} 个成员"
                    "原子身份序列与冻结输入不一致。"
                ),
                raw_error=(
                    f"expected={expected_identity_sha256}; "
                    f"observed={observed['identity_sha256']}"
                ),
                suggestion=(
                    "成员可能被交换、错误拆分或替换；请保留该 run "
                    "供审计，并重新准备新的共同对接。"
                ),
            )
        member_index = int(expected.get("member_index") or offset + 1)
        content_lines = [
            f"MODEL {mode}",
            f"REMARK DOCKSTART JOINT MODE {mode}; MEMBER {member_index} OF {MEMBER_COUNT}",
            "REMARK DOCKSTART JOINT SCORE ONLY; NO PER-MEMBER SCORE",
            *block_lines,
            "ENDMDL",
        ]
        parsed_members.append(
            {
                "member_index": member_index,
                "atom_count": observed["atom_count"],
                "torsdof": observed["torsdof"],
                "identity_sha256": observed["identity_sha256"],
                "content": "\n".join(content_lines) + "\n",
            }
        )

    if any(line.strip() for line in lines[block_start:]):
        trailing = [
            line
            for line in lines[block_start:]
            if line.strip()
            and not line.lstrip().upper().startswith(
                ("REMARK", "TER", "CONECT")
            )
        ]
        if trailing:
            return _protocol_error(
                "MULTIPLE_LIGAND_MODEL_TRAILING_STRUCTURE",
                f"联合构象 Mode {mode} 在第二个 TORSDOF 后仍有结构记录。",
                raw_error="\n".join(trailing[:8]),
            )

    return {
        "ok": True,
        "mode": mode,
        "joint_affinity_kcal_mol": score_values[0],
        "rmsd_lb": score_values[1],
        "rmsd_ub": score_values[2],
        "members": parsed_members,
    }


def parse_multiple_ligand_output_text(
    output_text: str,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Parse and validate a simultaneous two-ligand Vina PDBQT output."""

    if not isinstance(output_text, str) or not output_text.strip():
        return _protocol_error(
            "MULTIPLE_LIGAND_OUTPUT_EMPTY",
            "共同对接输出 out.pdbqt 为空。",
            suggestion="请查看本次 run 的 stdout.txt 和 stderr.txt。",
        )
    if not isinstance(members, list) or len(members) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_MANIFEST_INVALID",
            "成员清单必须恰好包含两个冻结配体。",
        )
    if [
        int(member.get("member_index") or 0)
        for member in members
        if isinstance(member, dict)
    ] != [1, 2]:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_ORDER_INVALID",
            "成员清单顺序无效；首版必须按 member 1、member 2 排列。",
        )

    models: list[dict[str, Any]] = []
    current_mode: int | None = None
    current_lines: list[str] = []
    observed_modes: set[int] = set()
    for line_number, line in enumerate(output_text.splitlines(), start=1):
        model_match = MODEL_PATTERN.match(line)
        if model_match:
            if current_mode is not None:
                return _protocol_error(
                    "MULTIPLE_LIGAND_MODEL_NESTED",
                    f"out.pdbqt 第 {line_number} 行出现嵌套 MODEL。",
                )
            current_mode = int(model_match.group(1))
            if current_mode <= 0 or current_mode in observed_modes:
                return _protocol_error(
                    "MULTIPLE_LIGAND_MODEL_ID_INVALID",
                    "out.pdbqt 包含无效或重复的 MODEL 编号。",
                    raw_error=str(current_mode),
                )
            expected_mode = len(models) + 1
            if current_mode != expected_mode:
                return _protocol_error(
                    "MULTIPLE_LIGAND_MODEL_SEQUENCE_INVALID",
                    (
                        "out.pdbqt 的 MODEL 必须从 1 开始连续递增，"
                        f"当前应为 {expected_mode}，实际为 {current_mode}。"
                    ),
                )
            current_lines = []
            continue
        if ENDMDL_PATTERN.match(line):
            if current_mode is None:
                return _protocol_error(
                    "MULTIPLE_LIGAND_ENDMDL_UNMATCHED",
                    f"out.pdbqt 第 {line_number} 行的 ENDMDL 没有对应 MODEL。",
                )
            parsed = _parse_model(current_mode, current_lines, members)
            if not parsed.get("ok"):
                return parsed
            models.append(
                {
                    key: value
                    for key, value in parsed.items()
                    if key != "ok"
                }
            )
            observed_modes.add(current_mode)
            current_mode = None
            current_lines = []
            continue
        if current_mode is not None:
            current_lines.append(line)
        elif line.strip():
            return _protocol_error(
                "MULTIPLE_LIGAND_OUTPUT_RECORD_OUTSIDE_MODEL",
                "out.pdbqt 在 MODEL/ENDMDL 外包含非空记录。",
                raw_error=f"line {line_number}: {line}",
            )
    if current_mode is not None:
        return _protocol_error(
            "MULTIPLE_LIGAND_MODEL_UNCLOSED",
            f"联合构象 Mode {current_mode} 缺少 ENDMDL。",
        )
    if not models:
        return _protocol_error(
            "MULTIPLE_LIGAND_MODELS_NOT_FOUND",
            "out.pdbqt 中没有找到任何联合构象 MODEL。",
        )
    return {
        "ok": True,
        "models": models,
        "available_modes": [int(model["mode"]) for model in models],
        "affinity_scope": "joint_two_ligand_pose",
        "rmsd_scope": "joint_pose_relative_to_best_mode",
        "per_member_scores_available": False,
        "message": f"已解析 {len(models)} 个两配体联合构象。",
        "error": None,
    }


def _validated_project(
    project_dir: str,
) -> tuple[Any | None, Path | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, None, loaded
    project_root = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(loaded["project"], project_root)
    project.project_dir = str(project_root)
    protocol_error = _project_protocol_validation(project)
    if protocol_error:
        return None, None, protocol_error
    box_validation = validate_box_params(project.box.__dict__)
    if not box_validation.get("ok"):
        return None, None, box_validation
    vina_validation = validate_vina_params(project.vina.__dict__)
    if not vina_validation.get("ok"):
        return None, None, vina_validation
    return project, project_root, None


def _detect_supported_vina(
    project: Any,
    *,
    vina_options: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, Any] | None, dict[str, Any] | None]:
    settings = load_settings()
    detection = vina_adapter.detect(settings.tool_paths.vina)
    if detection.status != "ok" or not detection.path:
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_NOT_AVAILABLE",
            detection.message or "没有检测到可用的 AutoDock Vina。",
            raw_error=detection.raw_error,
            suggestion="请在设置页修复 Vina 路径后重试。",
        )
    if not _version_is_supported(detection.version):
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_VERSION_UNSUPPORTED",
            (
                "多配体共同对接至少需要 AutoDock Vina "
                f"{MINIMUM_VINA_VERSION}。"
            ),
            raw_error=f"detected={detection.version or 'unknown'}",
            suggestion="请安装或配置兼容版本的 AutoDock Vina。",
        )
    features = (
        detection.capabilities.get("features")
        if isinstance(detection.capabilities, dict)
        and isinstance(detection.capabilities.get("features"), dict)
        else {}
    )
    multiple_ligands_capability = (
        features.get("multiple_ligands")
        if isinstance(features.get("multiple_ligands"), dict)
        else {}
    )
    if multiple_ligands_capability.get("supported") is not True:
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_CAPABILITY_UNCONFIRMED",
            (
                "当前 Vina 能力探测没有明确确认 multitoken "
                "--ligand 共同对接支持。"
            ),
            raw_error=json.dumps(
                multiple_ligands_capability,
                ensure_ascii=False,
            ),
            suggestion=(
                "请使用能够由 DockStart 明确探测为支持 multiple_ligands "
                "的 AutoDock Vina。"
            ),
        )
    capability = validate_vina_runtime_capabilities(
        vina_options if vina_options is not None else project.vina.__dict__,
        "vina",
        detection.capabilities,
        run_mode="dock",
        receptor_mode="rigid",
        autobox=False,
    )
    if not capability.get("ok"):
        detail = capability.get("error") or {}
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_CAPABILITY_MISMATCH",
            "当前 Vina 不支持本次共同对接冻结的专家选项。",
            raw_error=str(detail.get("raw_error") or ""),
            suggestion="请关闭不受支持的专家选项，或配置兼容 Vina。",
        )
    binary = _vina_binary_snapshot(detection)
    if (
        binary is None
        or not SHA256_PATTERN.fullmatch(str(binary.get("sha256") or ""))
        or int(binary.get("size_bytes") or 0) <= 0
    ):
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_BINARY_UNVERIFIABLE",
            "无法读取并冻结当前 Vina 可执行文件的 SHA256。",
            raw_error=str(detection.path or ""),
            suggestion="请配置一个可读取的本地 Vina 可执行文件。",
        )
    return detection, binary, None


def _create_run_directory(project_root: Path) -> tuple[str, Path]:
    _safe_runs_directory(project_root, create=True)
    for _ in range(100):
        run_id = get_next_run_id(str(project_root))
        run_dir = _safe_run_directory(
            project_root,
            run_id,
            require_exists=False,
        )
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
            return run_id, run_dir
        except FileExistsError:
            continue
    raise RuntimeError("连续 100 次无法分配唯一 run_id。")


def prepare_multiple_ligand_run(
    project_dir: str,
    ligand_files: list[str],
) -> dict[str, Any]:
    """Freeze exactly two prepared PDBQT ligands into one run."""

    if not isinstance(ligand_files, list) or len(ligand_files) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_EXACTLY_TWO_REQUIRED",
            "多配体共同对接（实验性）首版必须恰好选择两个配体。",
            suggestion="请选择两个唯一、已准备好的 PDBQT 配体。",
        )
    project, project_root, project_error = _validated_project(project_dir)
    if project_error:
        return project_error
    assert project is not None and project_root is not None

    source_members: list[dict[str, Any]] = []
    for member_index, path_value in enumerate(ligand_files, start=1):
        normalized_path = (
            str(project_root / Path(path_value))
            if isinstance(path_value, str)
            and path_value.strip()
            and not Path(path_value).expanduser().is_absolute()
            else path_value
        )
        source, source_error = _validate_source_ligand(
            normalized_path,
            member_index,
        )
        if source_error:
            return source_error
        assert source is not None
        source["display_name"] = _source_display_name(
            project_root,
            source,
        )
        source_members.append(source)
    member_hashes = [str(member["sha256"]) for member in source_members]
    if len(set(member_hashes)) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_DUPLICATE_INPUT",
            "两个配体的文件内容相同，不能组成共同对接输入。",
            raw_error=", ".join(member_hashes),
            suggestion="请选择两个内容不同的已准备 PDBQT 配体。",
        )
    grid_validation = _validate_joint_grid_resource(
        dict(project.box.__dict__),
        dict(project.vina.__dict__),
        source_members,
    )
    if not grid_validation.get("ok"):
        return grid_validation
    grid_estimate = dict(grid_validation["grid_estimate"])
    grid_warnings = list(grid_validation.get("warnings") or [])

    receptor_relative = str(project.receptor.file or "")
    if not receptor_relative:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_NOT_SET",
            "项目尚未导入刚性受体 PDBQT。",
            suggestion="请先导入受体 PDBQT，再准备共同对接。",
        )
    receptor_path = (project_root / receptor_relative).resolve(strict=False)
    try:
        receptor_path.relative_to(project_root)
    except ValueError:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_OUTSIDE_PROJECT",
            "受体路径指向项目目录外，已拒绝准备。",
            raw_error=receptor_relative,
        )
    if not receptor_path.is_file() or receptor_path.stat().st_size <= 0:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_MISSING",
            "没有找到项目中的刚性受体 PDBQT。",
            raw_error=str(receptor_path),
            suggestion="请重新导入受体 PDBQT。",
        )
    receptor_stats = _parse_pdbqt_stats(
        receptor_path,
        receptor_relative,
        ligand=False,
    )
    if int(receptor_stats.get("atom_count") or 0) <= 0:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_NO_ATOMS",
            "刚性受体 PDBQT 没有可识别的 ATOM/HETATM 记录。",
            suggestion="请重新准备并导入受体 PDBQT。",
        )

    detection, binary, detection_error = _detect_supported_vina(project)
    if detection_error:
        return detection_error
    assert detection is not None and binary is not None
    detected_features = (
        detection.capabilities.get("features")
        if isinstance(detection.capabilities, dict)
        and isinstance(detection.capabilities.get("features"), dict)
        else {}
    )
    multiple_ligands_capability = (
        dict(detected_features.get("multiple_ligands") or {})
        if isinstance(detected_features.get("multiple_ligands"), dict)
        else {}
    )

    run_id = ""
    try:
        run_id, run_dir = _create_run_directory(project_root)
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=False, exist_ok=False)

        receptor_snapshot_relative = _fixed_run_relative(
            run_id,
            "inputs",
            "receptor.pdbqt",
        )
        receptor_snapshot_path = inputs_dir / "receptor.pdbqt"
        atomic_write_bytes(
            receptor_snapshot_path,
            receptor_path.read_bytes(),
        )
        receptor_snapshot = _relative_snapshot(
            receptor_snapshot_path,
            receptor_snapshot_relative,
            ligand=False,
        )

        members: list[dict[str, Any]] = []
        for member_index, source in enumerate(source_members, start=1):
            member_id = f"member_{member_index:03d}"
            filename = f"ligand_{member_index:03d}.pdbqt"
            relative_path = _fixed_run_relative(
                run_id,
                "inputs",
                filename,
            )
            destination = inputs_dir / filename
            atomic_write_bytes(destination, Path(source["source"]).read_bytes())
            snapshot = _relative_snapshot(
                destination,
                relative_path,
                ligand=True,
            )
            if str(snapshot.get("sha256") or "").lower() != source["sha256"]:
                raise RuntimeError(
                    f"{member_id} 复制后的 SHA256 与源文件不一致。"
                )
            members.append(
                {
                    "member_index": member_index,
                    "member_id": member_id,
                    "display_name": str(source["display_name"]),
                    "source_name": str(source["source_name"]),
                    "source_sha256": str(source["sha256"]),
                    "file": relative_path,
                    "sha256": str(snapshot["sha256"]).lower(),
                    "size_bytes": int(snapshot["size_bytes"]),
                    "stats": _clean_stats(snapshot),
                }
            )

        config_relative = _fixed_run_relative(run_id, "config_snapshot.txt")
        config_path = run_dir / "config_snapshot.txt"
        config_text = _build_config(project, run_id)
        atomic_write_text(config_path, config_text)
        config_snapshot = _hash_snapshot(config_path, config_relative)
        config_snapshot.pop("absolute_path", None)

        command = _build_command(str(binary["path"]), run_id)
        created_at = _now_iso()
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "app_version": __version__,
            "app": {"name": "DockStart", "version": __version__},
            "run_id": run_id,
            "protocol_id": PROTOCOL_ID,
            "protocol_name": PROTOCOL_NAME,
            "experimental": True,
            "status": "prepared",
            "stage": "prepared",
            "progress": {
                "percent": 0,
                "message": "共同对接运行记录已准备。",
            },
            "created_at": created_at,
            "started_at": "",
            "finished_at": "",
            "run_mode": "dock",
            "scoring_protocol": "vina",
            "scoring_function": project.vina.scoring,
            "receptor_mode": "rigid",
            "grid_source": "receptor",
            "member_count": MEMBER_COUNT,
            "members": members,
            "box": dict(project.box.__dict__),
            "vina": dict(project.vina.__dict__),
            "grid_estimate": grid_estimate,
            "command": command,
            "command_preview": " ".join(
                f'"{part}"' if any(char.isspace() for char in part) else part
                for part in command
            ),
            "metadata_file": _fixed_run_relative(run_id, "metadata.json"),
            "config_file": config_relative,
            "receptor_file": receptor_snapshot_relative,
            "output_file": _fixed_run_relative(run_id, RUN_OUTPUT_FILE),
            "stdout_file": _fixed_run_relative(run_id, "stdout.txt"),
            "stderr_file": _fixed_run_relative(run_id, "stderr.txt"),
            "log_file": _fixed_run_relative(run_id, "log.txt"),
            "scores_file": _fixed_run_relative(run_id, RUN_SCORES_FILE),
            "joint_poses_file": _fixed_run_relative(
                run_id,
                RUN_JOINT_POSES_FILE,
            ),
            "report_file": _fixed_run_relative(run_id, RUN_REPORT_FILE),
            "project_scores_file": PROJECT_SCORES_FILE,
            "snapshots": {
                "receptor": receptor_snapshot,
                "members": [
                    {
                        "member_index": member["member_index"],
                        "member_id": member["member_id"],
                        "relative_path": member["file"],
                        "size_bytes": member["size_bytes"],
                        "sha256": member["sha256"],
                        "stats": member["stats"],
                    }
                    for member in members
                ],
                "config": config_snapshot,
            },
            "prepared_vina": binary,
            "multiple_ligands_capability": multiple_ligands_capability,
            "execution_vina": None,
            "available_modes": [],
            "scores": [],
            "pose_count": 0,
            "per_member_scores_available": False,
            "score_scope": "joint_two_ligand_pose",
            "rmsd_scope": "joint_pose_relative_to_best_mode",
            "warnings": [
                "这是实验性共同搜索协议，不是串行批量筛选。",
                JOINT_SCORE_DISCLAIMER,
                *grid_warnings,
            ],
            "error": None,
        }
        _write_run_metadata(project_root, run_id, metadata)

        latest = load_project(str(project_root))
        if not latest.get("ok"):
            return latest
        current = _project_from_dict(latest["project"], project_root)
        current.project_dir = str(project_root)
        current.runs.append(
            {
                "run_id": run_id,
                "status": "prepared",
                "stage": "prepared",
                "created_at": created_at,
                "started_at": "",
                "finished_at": "",
                "protocol_id": PROTOCOL_ID,
                "protocol_name": PROTOCOL_NAME,
                "run_mode": "dock",
                "scoring_protocol": "vina",
                "scoring_function": project.vina.scoring,
                "member_count": MEMBER_COUNT,
                "members": [
                    {
                        "member_index": member["member_index"],
                        "member_id": member["member_id"],
                        "display_name": member["display_name"],
                        "sha256": member["sha256"],
                    }
                    for member in members
                ],
                "output_file": metadata["output_file"],
                "scores_file": metadata["scores_file"],
                "report_file": metadata["report_file"],
                "best_affinity": None,
            }
        )
        saved = save_project(current)
        if not saved.get("ok"):
            return saved
        return {
            "ok": True,
            "project_dir": str(project_root),
            "run_id": run_id,
            "status": "prepared",
            "metadata_file": metadata["metadata_file"],
            "metadata": metadata,
            "members": members,
            "command": command,
            "message": (
                "多配体共同对接（实验性）运行记录已准备；"
                "两个配体将进入同一次 Vina 全局搜索。"
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - preparation failures are data.
        return _protocol_error(
            "MULTIPLE_LIGAND_PREPARE_ERROR",
            "准备多配体共同对接运行记录时发生错误。",
            raw_error=f"run_id={run_id}; {exc}",
            suggestion="请确认项目 runs 目录可写且没有被链接或其他进程占用。",
        )


def _load_protocol_metadata(
    project_dir: str,
    run_id: str,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, Any] | None]:
    if not RUN_ID_PATTERN.fullmatch(str(run_id or "")):
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
        )
    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return None, None, metadata_error
    assert metadata is not None
    if str(metadata.get("protocol_id") or "") != PROTOCOL_ID:
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_PROTOCOL_MISMATCH",
            "该 run 不是多配体共同对接记录。",
            raw_error=str(metadata.get("protocol_id") or ""),
            suggestion="请选择协议标记为“多配体共同对接（实验性）”的 run。",
        )
    try:
        project_root = Path(project_dir).expanduser().resolve()
        _safe_run_directory(project_root, run_id)
    except Exception as exc:  # noqa: BLE001
        return None, None, _protocol_error(
            "MULTIPLE_LIGAND_RUN_PATH_UNSAFE",
            "run 目录不是项目内普通目录，已拒绝访问。",
            raw_error=str(exc),
        )
    return metadata, project_root, None


def _fixed_path(
    project_root: Path,
    run_id: str,
    relative_path: str,
    expected_relative: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if relative_path != expected_relative:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_FIXED_PATH_INVALID",
            "metadata 中的固定运行路径不可信。",
            raw_error=f"expected={expected_relative}; actual={relative_path}",
        )
    lexical = project_root / expected_relative
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(project_root)
        run_dir = _safe_run_directory(project_root, run_id)
        if (
            lexical.is_symlink()
            or resolved != lexical.absolute()
            or run_dir not in resolved.parents
        ):
            raise RuntimeError(f"unsafe resolved path: {resolved}")
    except Exception as exc:  # noqa: BLE001
        return None, _protocol_error(
            "MULTIPLE_LIGAND_FIXED_PATH_UNSAFE",
            "运行快照路径被链接或重解析到固定 run 之外。",
            raw_error=f"{lexical}: {exc}",
        )
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_SNAPSHOT_MISSING",
            "运行快照缺失或为空。",
            raw_error=str(resolved),
        )
    return resolved, None


def _verify_snapshots(
    project_root: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    records: list[tuple[str, dict[str, Any], str]] = []
    receptor = (
        snapshots.get("receptor")
        if isinstance(snapshots.get("receptor"), dict)
        else {}
    )
    records.append(
        (
            "receptor",
            receptor,
            _fixed_run_relative(run_id, "inputs", "receptor.pdbqt"),
        )
    )
    config = (
        snapshots.get("config")
        if isinstance(snapshots.get("config"), dict)
        else {}
    )
    records.append(
        (
            "config",
            config,
            _fixed_run_relative(run_id, "config_snapshot.txt"),
        )
    )
    member_records = (
        snapshots.get("members")
        if isinstance(snapshots.get("members"), list)
        else []
    )
    if len(member_records) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_MANIFEST_INVALID",
            "metadata 中的成员快照清单必须恰好包含两个配体。",
        )
    for index, record in enumerate(member_records, start=1):
        if not isinstance(record, dict) or int(record.get("member_index") or 0) != index:
            return _protocol_error(
                "MULTIPLE_LIGAND_MEMBER_ORDER_INVALID",
                "metadata 中的成员快照顺序无效。",
            )
        records.append(
            (
                f"member_{index}",
                record,
                _fixed_run_relative(
                    run_id,
                    "inputs",
                    f"ligand_{index:03d}.pdbqt",
                ),
            )
        )

    verified: dict[str, Any] = {}
    verified_member_stats: list[dict[str, Any]] = []
    for key, record, expected_relative in records:
        recorded_relative = str(
            record.get("relative_path")
            or (
                metadata.get("config_file")
                if key == "config"
                else metadata.get("receptor_file")
                if key == "receptor"
                else ""
            )
            or ""
        )
        path, path_error = _fixed_path(
            project_root,
            run_id,
            recorded_relative,
            expected_relative,
        )
        if path_error:
            return path_error
        assert path is not None
        expected_sha256 = str(record.get("sha256") or "").lower()
        expected_size = int(record.get("size_bytes") or 0)
        if not SHA256_PATTERN.fullmatch(expected_sha256) or expected_size <= 0:
            return _protocol_error(
                "MULTIPLE_LIGAND_SNAPSHOT_ATTESTATION_INVALID",
                f"{key} 快照缺少可信 SHA256 或文件大小。",
            )
        try:
            actual_sha256 = _sha256_file(path).lower()
            actual_size = path.stat().st_size
        except OSError as exc:
            return _protocol_error(
                "MULTIPLE_LIGAND_SNAPSHOT_READ_ERROR",
                f"读取 {key} 快照完整性信息时发生错误。",
                raw_error=str(exc),
            )
        if actual_sha256 != expected_sha256 or actual_size != expected_size:
            return _protocol_error(
                "MULTIPLE_LIGAND_SNAPSHOT_HASH_MISMATCH",
                f"{key} 快照在准备后发生变化，已拒绝执行。",
                raw_error=(
                    f"expected sha256={expected_sha256}, size={expected_size}; "
                    f"actual sha256={actual_sha256}, size={actual_size}; path={path}"
                ),
                suggestion="请保留该 run 供审计，并重新准备新的共同对接。",
            )
        if key.startswith("member_"):
            frozen_stats = (
                record.get("stats")
                if isinstance(record.get("stats"), dict)
                else {}
            )
            expected_identity = str(
                frozen_stats.get("identity_sha256") or ""
            ).lower()
            try:
                observed_stats = _parse_pdbqt_stats(
                    path,
                    expected_relative,
                    ligand=True,
                )
                observed_stats["identity_sha256"] = (
                    _pdbqt_file_identity_sha256(path)
                )
            except (OSError, UnicodeError, ValueError) as exc:
                return _protocol_error(
                    "MULTIPLE_LIGAND_MEMBER_PARTIAL_CHARGE_INVALID",
                    f"{key} 包含无效或缺失的 PDBQT 部分电荷。",
                    raw_error=str(exc),
                    suggestion="请保留该 run 供审计，并重新准备新的共同对接。",
                )
            actual_identity = str(
                observed_stats["identity_sha256"]
            ).lower()
            member_index = int(key.rsplit("_", 1)[-1])
            metadata_member = (
                metadata.get("members", [])[member_index - 1]
                if isinstance(metadata.get("members"), list)
                and len(metadata["members"]) >= member_index
                and isinstance(metadata["members"][member_index - 1], dict)
                else {}
            )
            metadata_stats = (
                metadata_member.get("stats")
                if isinstance(metadata_member.get("stats"), dict)
                else {}
            )
            if (
                not SHA256_PATTERN.fullmatch(expected_identity)
                or actual_identity != expected_identity
                or str(
                    metadata_stats.get("identity_sha256") or ""
                ).lower()
                != expected_identity
            ):
                return _protocol_error(
                    "MULTIPLE_LIGAND_MEMBER_IDENTITY_ATTESTATION_INVALID",
                    f"{key} 缺少可信或一致的原子身份序列指纹。",
                    raw_error=(
                        f"snapshot={expected_identity}; "
                        f"metadata={metadata_stats.get('identity_sha256') or ''}; "
                        f"actual={actual_identity}"
                    ),
                    suggestion="请保留该 run 供审计，并重新准备新的共同对接。",
                )
            verified_member_stats.append(
                {"stats": _clean_stats(observed_stats)}
            )
        verified[key] = {
            "path": path,
            "relative_path": expected_relative,
            "sha256": actual_sha256,
            "size_bytes": actual_size,
        }
    grid_validation = _validate_joint_grid_resource(
        (
            metadata.get("box")
            if isinstance(metadata.get("box"), dict)
            else {}
        ),
        (
            metadata.get("vina")
            if isinstance(metadata.get("vina"), dict)
            else {}
        ),
        verified_member_stats,
    )
    if not grid_validation.get("ok"):
        return grid_validation
    frozen_grid_estimate = (
        metadata.get("grid_estimate")
        if isinstance(metadata.get("grid_estimate"), dict)
        else {}
    )
    observed_grid_estimate = grid_validation["grid_estimate"]
    if json.dumps(
        frozen_grid_estimate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) != json.dumps(
        observed_grid_estimate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_GRID_ESTIMATE_ATTESTATION_INVALID",
            "共同对接冻结的联合网格估算与两个配体原子类型并集不一致。",
            raw_error=json.dumps(
                {
                    "frozen": frozen_grid_estimate,
                    "observed": observed_grid_estimate,
                },
                ensure_ascii=False,
            ),
            suggestion="请保留该 run 供审计，并重新准备新的共同对接。",
        )
    verified["grid_estimate"] = observed_grid_estimate
    return {"ok": True, "files": verified, "error": None}


def _verify_execution_binary(
    detection: Any,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        observed = _vina_binary_snapshot(detection)
    except OSError as exc:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_BINARY_READ_ERROR",
            "读取当前 Vina 二进制完整性信息时发生错误。",
            raw_error=str(exc),
        )
    expected = (
        metadata.get("prepared_vina")
        if isinstance(metadata.get("prepared_vina"), dict)
        else {}
    )
    if observed is None:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_BINARY_UNVERIFIABLE",
            "执行前无法读取当前 Vina 可执行文件。",
        )
    expected_sha256 = str(expected.get("sha256") or "").lower()
    expected_size = int(expected.get("size_bytes") or 0)
    if (
        not SHA256_PATTERN.fullmatch(expected_sha256)
        or observed["sha256"] != expected_sha256
        or int(observed["size_bytes"]) != expected_size
    ):
        return None, _protocol_error(
            "MULTIPLE_LIGAND_VINA_BINARY_MISMATCH",
            "当前 Vina 二进制与准备本次 run 时冻结的文件不一致。",
            raw_error=(
                f"expected sha256={expected_sha256}, size={expected_size}; "
                f"actual sha256={observed.get('sha256')}, "
                f"size={observed.get('size_bytes')}"
            ),
            suggestion="请恢复原 Vina，或保留该 run 并重新准备新的运行记录。",
        )
    return observed, None


def _scores_csv_text(scores: list[dict[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "mode",
            "joint_affinity_kcal_mol",
            "rmsd_lb",
            "rmsd_ub",
            "pose_available",
            "score_scope",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for score in scores:
        writer.writerow(
            {
                "mode": score["mode"],
                "joint_affinity_kcal_mol": score[
                    "joint_affinity_kcal_mol"
                ],
                "rmsd_lb": score["rmsd_lb"],
                "rmsd_ub": score["rmsd_ub"],
                "pose_available": str(bool(score["pose_available"])).lower(),
                "score_scope": "joint_two_ligand_pose",
            }
        )
    return buffer.getvalue()


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _report_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_report(
    metadata: dict[str, Any],
    joint_manifest: dict[str, Any],
) -> str:
    execution_vina = (
        metadata.get("execution_vina")
        if isinstance(metadata.get("execution_vina"), dict)
        else {}
    )
    frozen_vina = (
        metadata.get("vina")
        if isinstance(metadata.get("vina"), dict)
        else {}
    )
    box = (
        metadata.get("box")
        if isinstance(metadata.get("box"), dict)
        else {}
    )
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    receptor_snapshot = (
        snapshots.get("receptor")
        if isinstance(snapshots.get("receptor"), dict)
        else {}
    )
    config_snapshot = (
        snapshots.get("config")
        if isinstance(snapshots.get("config"), dict)
        else {}
    )
    member_snapshots = (
        snapshots.get("members")
        if isinstance(snapshots.get("members"), list)
        else []
    )
    output_snapshot = (
        artifacts.get("output")
        if isinstance(artifacts.get("output"), dict)
        else {}
    )
    capability = (
        metadata.get("multiple_ligands_capability")
        if isinstance(
            metadata.get("multiple_ligands_capability"),
            dict,
        )
        else {}
    )
    command = (
        metadata.get("executed_command")
        if isinstance(metadata.get("executed_command"), list)
        else metadata.get("command")
        if isinstance(metadata.get("command"), list)
        else []
    )
    lines = [
        f"# {PROTOCOL_NAME}报告",
        "",
        f"- Run：`{_report_value(metadata.get('run_id'))}`",
        f"- 状态：{_report_value(metadata.get('status'))}",
        f"- 评分函数：{_report_value(metadata.get('scoring_function'))}",
        f"- Vina 版本：{_report_value(execution_vina.get('version'))}",
        f"- 联合构象数量：{len(joint_manifest.get('available_modes') or [])}",
        "",
        "## 时间",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| 创建时间 | {_markdown_cell(_report_value(metadata.get('created_at')))} |",
        f"| 开始时间 | {_markdown_cell(_report_value(metadata.get('started_at')))} |",
        f"| 结束时间 | {_markdown_cell(_report_value(metadata.get('finished_at')))} |",
        f"| 用时（秒） | {_report_value(metadata.get('duration_seconds'))} |",
        "",
        "## 协议边界",
        "",
        "本报告来自一次 Vina 搜索中的两个配体共同优化，不是串行批量筛选。",
        JOINT_SCORE_DISCLAIMER,
        "当前实验版本仅支持两个唯一 PDBQT 配体、刚性受体、Vina/Vinardo 评分和全局搜索。",
        "",
        "## 对接箱体",
        "",
        "| 参数 | 冻结值 |",
        "|---|---:|",
    ]
    for key, value in box.items():
        lines.append(
            f"| {_markdown_cell(key)} | {_markdown_cell(_report_value(value))} |"
        )
    lines.extend(
        [
            "",
            "## 完整冻结 Vina 参数",
            "",
            "| 参数 | 冻结值 |",
            "|---|---|",
        ]
    )
    for key, value in frozen_vina.items():
        lines.append(
            f"| {_markdown_cell(key)} | {_markdown_cell(_report_value(value))} |"
        )
    lines.extend(
        [
            "",
            "## 实际命令",
            "",
            "```json",
            json.dumps(command, ensure_ascii=False),
            "```",
            "",
            "## multiple_ligands 能力证据",
            "",
            "```json",
            json.dumps(
                capability,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "```",
            "",
            "## 冻结成员",
            "",
            "| 顺序 | 名称 | 原子数 | TORSDOF | SHA256 |",
            "|---:|---|---:|---:|---|",
        ]
    )
    for member in metadata.get("members") or []:
        stats = member.get("stats") if isinstance(member.get("stats"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _report_value(member.get("member_index")),
                    _markdown_cell(_report_value(member.get("display_name"))),
                    _report_value(stats.get("atom_count")),
                    _report_value(stats.get("torsdof")),
                    f"`{_report_value(member.get('sha256'))}`",
                ]
            )
            + " |"
        )
    hash_rows: list[tuple[str, str, Any, Any]] = [
        (
            "受体输入",
            _report_value(metadata.get("receptor_file")),
            receptor_snapshot.get("sha256"),
            receptor_snapshot.get("size_bytes"),
        ),
        (
            "配置快照",
            _report_value(metadata.get("config_file")),
            config_snapshot.get("sha256"),
            config_snapshot.get("size_bytes"),
        ),
    ]
    for index, snapshot in enumerate(member_snapshots, start=1):
        if not isinstance(snapshot, dict):
            continue
        hash_rows.append(
            (
                f"配体成员 {index}",
                _report_value(snapshot.get("relative_path")),
                snapshot.get("sha256"),
                snapshot.get("size_bytes"),
            )
        )
    hash_rows.extend(
        [
            (
                "联合输出",
                _report_value(metadata.get("output_file")),
                output_snapshot.get("sha256"),
                output_snapshot.get("size_bytes"),
            ),
            (
                "Vina 二进制",
                _report_value(execution_vina.get("path")),
                execution_vina.get("sha256"),
                execution_vina.get("size_bytes"),
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## 完整性证据",
            "",
            "| 产物 | 路径 | SHA256 | 字节数 |",
            "|---|---|---|---:|",
        ]
    )
    for label, path, sha256, size_bytes in hash_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(label),
                    f"`{_markdown_cell(path)}`",
                    f"`{_markdown_cell(_report_value(sha256))}`",
                    _report_value(size_bytes),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 联合评分",
            "",
            "| Mode | 联合 affinity (kcal/mol) | RMSD l.b. | RMSD u.b. | 构象可加载 |",
            "|---:|---:|---:|---:|---|",
        ]
    )
    for score in joint_manifest.get("scores") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _report_value(score.get("mode")),
                    _report_value(score.get("joint_affinity_kcal_mol")),
                    _report_value(score.get("rmsd_lb")),
                    _report_value(score.get("rmsd_ub")),
                    "是" if score.get("pose_available") else "否",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            (
                "stdout 评分表可能包含因 energy_range 过滤而未写入 out.pdbqt 的模式；"
                "“构象可加载”只以 out.pdbqt 中实际存在且通过成员校验的 MODEL 为准。"
            ),
            "RMSD 表示整个两配体联合构象相对最佳联合模式的差异，不是任一成员的单独 RMSD。",
            "",
            "## 科学说明",
            "",
            SCIENTIFIC_DISCLAIMER,
            "共同对接结果不能直接证明协同结合、同时占位、药效、安全性或临床价值。",
            "",
        ]
    )
    return "\n".join(lines)


def _artifact_snapshot(
    project_root: Path,
    relative_path: str,
) -> dict[str, Any]:
    path = _safe_project_artifact_path(
        project_root,
        relative_path,
        allow_missing=False,
    )
    snapshot = _hash_snapshot(path, relative_path)
    snapshot.pop("absolute_path", None)
    return snapshot


def _finalize_cancelled(
    project_root: Path,
    run_id: str,
    *,
    message: str = "用户已取消多配体共同对接。",
) -> dict[str, Any]:
    cancelled_at = _now_iso()

    def finalize(current: dict[str, Any]) -> dict[str, Any]:
        status = str(current.get("status") or "")
        if status in {"finished", "failed", "cancelled", "interrupted"}:
            return current
        progress = (
            current.get("progress")
            if isinstance(current.get("progress"), dict)
            else {}
        )
        current.update(
            {
                "status": "cancelled",
                "stage": "cancelled",
                "finished_at": cancelled_at,
                "duration_seconds": _duration_seconds(
                    current.get("started_at"),
                    cancelled_at,
                ),
                "progress": {
                    "percent": int(progress.get("percent") or 0),
                    "message": message,
                },
                "error": None,
            }
        )
        current.pop("error_message", None)
        current.pop("process_missing_since", None)
        return current

    cancelled, transaction_error = _update_run_metadata_transaction(
        str(project_root),
        run_id,
        finalize,
    )
    if transaction_error:
        return transaction_error
    assert cancelled is not None
    status = str(cancelled.get("status") or "")
    if status != "cancelled":
        try:
            _cancel_marker_path(project_root, run_id).unlink(
                missing_ok=True
            )
        except OSError:
            pass
        return {
            "ok": True,
            "accepted": False,
            "cancelled": False,
            "project_dir": str(project_root),
            "run_id": run_id,
            "protocol_id": PROTOCOL_ID,
            "status": status,
            "stage": str(cancelled.get("stage") or status),
            "metadata": cancelled,
            "message": (
                f"run 已先进入 {status or 'unknown'} 终态，"
                "迟到的取消请求未改变结果。"
            ),
            "error": None,
        }
    try:
        _cancel_marker_path(project_root, run_id).unlink(missing_ok=True)
    except OSError:
        pass
    project_update = update_project_run_summary(
        str(project_root),
        run_id,
        {
            "status": "cancelled",
            "stage": "cancelled",
            "finished_at": cancelled.get("finished_at"),
            "duration_seconds": cancelled.get("duration_seconds"),
        },
    )
    return {
        "ok": bool(project_update.get("ok")),
        "accepted": True,
        "cancelled": True,
        "project": (
            project_update.get("project")
            if project_update.get("ok")
            else None
        ),
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "status": "cancelled",
        "stage": "cancelled",
        "metadata": cancelled,
        "message": message,
        "error": (
            None
            if project_update.get("ok")
            else project_update.get("error")
        ),
    }


def cancel_multiple_ligand_run(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    """Cancel without ever signaling an unverified or reused PID."""

    metadata, project_root, metadata_error = _load_protocol_metadata(
        project_dir,
        run_id,
    )
    if metadata_error:
        return metadata_error
    assert metadata is not None and project_root is not None
    status = str(metadata.get("status") or "")
    requested_at = _now_iso()
    if status in {"finished", "failed", "cancelled", "interrupted"}:
        return {
            "ok": True,
            "accepted": False,
            "cancelled": status == "cancelled",
            "project_dir": str(project_root),
            "run_id": run_id,
            "protocol_id": PROTOCOL_ID,
            "status": status,
            "stage": str(metadata.get("stage") or status),
            "metadata": metadata,
            "message": (
                f"run 已进入 {status} 终态，取消请求未改变运行结果。"
            ),
            "error": None,
        }
    if status in {"prepared", "queued"}:
        _create_cancel_marker(project_root, run_id, requested_at)
        return _finalize_cancelled(project_root, run_id)
    if status != "running":
        return _protocol_error(
            "MULTIPLE_LIGAND_RUN_NOT_CANCELLABLE",
            f"当前 run 状态为 {status or 'unknown'}，没有可取消的共同对接。",
            suggestion="只能取消 prepared、queued 或 running 状态的 run。",
        )

    pid = metadata.get("pid")
    trusted_executable = str(metadata.get("trusted_executable") or "")
    process_identity = (
        metadata.get("process_identity")
        if isinstance(metadata.get("process_identity"), dict)
        else None
    )
    process_verification = (
        vina_adapter.verify_process_identity(
            pid,
            trusted_executable,
            process_identity,
        )
        if isinstance(pid, int)
        and pid > 0
        and trusted_executable
        and process_identity is not None
        else {"ok": False, "running": False}
    )
    executor_pid = metadata.get("executor_pid")
    executor_executable = str(metadata.get("executor_executable") or "")
    executor_identity = (
        metadata.get("executor_identity")
        if isinstance(metadata.get("executor_identity"), dict)
        else None
    )
    executor_verification = (
        vina_adapter.verify_process_identity(
            executor_pid,
            executor_executable,
            executor_identity,
        )
        if isinstance(executor_pid, int)
        and executor_pid > 0
        and executor_executable
        and executor_identity is not None
        else {"ok": False, "running": False}
    )

    if (
        not process_verification.get("ok")
        and not executor_verification.get("ok")
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_CANCEL_IDENTITY_MISMATCH",
            "无法验证 Vina 或 DockStart 执行器进程身份，已拒绝终止任何 PID。",
            raw_error=json.dumps(
                {
                    "process": process_verification,
                    "executor": executor_verification,
                },
                ensure_ascii=False,
            ),
            suggestion="请刷新状态；不要手工复用 metadata 中的 PID。",
        )

    _create_cancel_marker(project_root, run_id, requested_at)

    def mark_request(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "running":
            return current
        progress = (
            current.get("progress")
            if isinstance(current.get("progress"), dict)
            else {}
        )
        current.update(
            {
                "stage": (
                    "cancelling"
                    if process_verification.get("ok")
                    else "cancel_pending"
                ),
                "cancel_requested_at": requested_at,
                "progress": {
                    "percent": int(progress.get("percent") or 0),
                    "message": (
                        "正在安全终止 AutoDock Vina。"
                        if process_verification.get("ok")
                        else "取消请求已登记，等待运行执行器收敛。"
                    ),
                },
            }
        )
        return current

    pending, transaction_error = _update_run_metadata_transaction(
        str(project_root),
        run_id,
        mark_request,
    )
    if transaction_error:
        return transaction_error
    assert pending is not None
    if pending.get("status") != "running":
        if pending.get("status") == "finished":
            try:
                _cancel_marker_path(project_root, run_id).unlink(
                    missing_ok=True
                )
            except OSError:
                pass
        return {
            "ok": True,
            "accepted": False,
            "cancelled": pending.get("status") == "cancelled",
            "project_dir": str(project_root),
            "run_id": run_id,
            "protocol_id": PROTOCOL_ID,
            "status": str(pending.get("status") or ""),
            "metadata": pending,
            "message": f"run 已进入 {pending.get('status')} 状态。",
            "error": None,
        }

    if process_verification.get("ok"):
        termination = vina_adapter.terminate_process(
            int(pid),
            expected_executable=trusted_executable,
            recorded_identity=process_identity,
        )
        if not termination.get("ok"):
            return _protocol_error(
                "MULTIPLE_LIGAND_CANCEL_FAILED",
                str(
                    termination.get("message")
                    or "无法安全终止共同对接 Vina 进程。"
                ),
                raw_error=str(termination.get("raw_error") or ""),
                suggestion="未报告为已取消；请刷新运行状态后重试。",
            )
        cancelled = _finalize_cancelled(project_root, run_id)
        cancelled["termination"] = termination
        return cancelled

    if executor_verification.get("ok"):
        project_update = update_project_run_summary(
            str(project_root),
            run_id,
            {
                "status": "running",
                "stage": "cancel_pending",
                "cancel_requested_at": requested_at,
            },
        )
        return {
            "ok": bool(project_update.get("ok")),
            "accepted": True,
            "cancelled": False,
            "project": (
                project_update.get("project")
                if project_update.get("ok")
                else None
            ),
            "project_dir": str(project_root),
            "run_id": run_id,
            "protocol_id": PROTOCOL_ID,
            "status": "running",
            "stage": "cancel_pending",
            "metadata": pending,
            "message": "取消请求已登记；Vina 已退出或尚未登记，等待执行器安全收敛。",
            "error": (
                None
                if project_update.get("ok")
                else project_update.get("error")
            ),
        }

    raise AssertionError("verified cancel path did not return")


def _mark_failed(
    project_root: Path,
    run_id: str,
    metadata: dict[str, Any],
    *,
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    if _cancel_marker_path(project_root, run_id).exists():
        return _finalize_cancelled(project_root, run_id)
    finished_at = _now_iso()
    failure = {
        "code": code,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }

    def mark_failure(current: dict[str, Any]) -> dict[str, Any]:
        if _cancel_marker_path(project_root, run_id).exists():
            return current
        if str(current.get("status") or "") in {
            "finished",
            "failed",
            "cancelled",
            "interrupted",
        }:
            return current
        current.update(
            {
                "status": "failed",
                "stage": "failed",
                "finished_at": finished_at,
                "duration_seconds": _duration_seconds(
                    current.get("started_at"),
                    finished_at,
                ),
                "progress": {"percent": 100, "message": message},
                "error": failure,
                "error_message": message,
            }
        )
        return current

    failed, transaction_error = _update_run_metadata_transaction(
        str(project_root),
        run_id,
        mark_failure,
    )
    if transaction_error:
        return transaction_error
    assert failed is not None
    if _cancel_marker_path(project_root, run_id).exists():
        return _finalize_cancelled(project_root, run_id)
    update_project_run_summary(
        str(project_root),
        run_id,
        {
            "status": str(failed.get("status") or ""),
            "stage": str(failed.get("stage") or ""),
            "finished_at": failed.get("finished_at"),
            "error": failed.get("error"),
        },
    )
    if failed.get("status") != "failed":
        terminal_status = str(failed.get("status") or "")
        return {
            "ok": terminal_status in {"finished", "cancelled"},
            "project_dir": str(project_root),
            "run_id": run_id,
            "status": terminal_status,
            "metadata": failed,
            "message": f"run 已进入 {terminal_status} 状态。",
            "error": None,
        }
    payload = _protocol_error(
        code,
        message,
        raw_error=raw_error,
        suggestion=suggestion,
    )
    payload.update(
        {
            "project_dir": str(project_root),
            "run_id": run_id,
            "status": "failed",
            "metadata": failed,
        }
    )
    return payload


def _execute_multiple_ligand_run_impl(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    """Execute a prepared simultaneous two-ligand run through VinaAdapter."""

    metadata, project_root, metadata_error = _load_protocol_metadata(
        project_dir,
        run_id,
    )
    if metadata_error:
        return metadata_error
    assert metadata is not None and project_root is not None
    if str(metadata.get("status") or "") != "prepared":
        return _protocol_error(
            "MULTIPLE_LIGAND_RUN_NOT_EXECUTABLE",
            (
                f"当前 run 状态为 {metadata.get('status') or 'unknown'}，"
                "只能执行 prepared 状态的共同对接。"
            ),
            suggestion="请不要重复执行已经启动或进入终态的 run。",
        )
    if (
        metadata.get("run_mode") != "dock"
        or metadata.get("receptor_mode") != "rigid"
        or metadata.get("grid_source") != "receptor"
        or metadata.get("scoring_protocol") != "vina"
        or metadata.get("scoring_function") not in {"vina", "vinardo"}
        or int(metadata.get("member_count") or 0) != MEMBER_COUNT
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_FROZEN_PROTOCOL_INVALID",
            "metadata 中冻结的共同对接协议超出首版支持边界。",
        )
    members = metadata.get("members")
    if not isinstance(members, list) or len(members) != MEMBER_COUNT:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_MANIFEST_INVALID",
            "metadata 中的成员清单无效。",
        )

    snapshots = _verify_snapshots(project_root, run_id, metadata)
    if not snapshots.get("ok"):
        return snapshots

    loaded = load_project(str(project_root))
    if not loaded.get("ok"):
        return loaded
    project = _project_from_dict(loaded["project"], project_root)
    project.project_dir = str(project_root)
    frozen_vina = (
        metadata.get("vina")
        if isinstance(metadata.get("vina"), dict)
        else {}
    )
    detection, _, detection_error = _detect_supported_vina(
        project,
        vina_options=frozen_vina,
    )
    if detection_error:
        return detection_error
    assert detection is not None
    execution_binary, binary_error = _verify_execution_binary(
        detection,
        metadata,
    )
    if binary_error:
        return binary_error
    assert execution_binary is not None

    prepared_vina = metadata.get("prepared_vina")
    prepared_path = (
        str(prepared_vina.get("path") or "")
        if isinstance(prepared_vina, dict)
        else ""
    )
    expected_prepared_command = _build_command(prepared_path, run_id)
    if metadata.get("command") != expected_prepared_command:
        return _protocol_error(
            "MULTIPLE_LIGAND_COMMAND_SNAPSHOT_INVALID",
            "metadata 中冻结的 Vina 命令被修改，已拒绝执行。",
            raw_error=json.dumps(metadata.get("command"), ensure_ascii=False),
        )
    command = _build_command(str(execution_binary["path"]), run_id)
    run_dir = _safe_run_directory(project_root, run_id)
    fixed_output_relatives = {
        "output": _fixed_run_relative(run_id, RUN_OUTPUT_FILE),
        "stdout": _fixed_run_relative(run_id, "stdout.txt"),
        "stderr": _fixed_run_relative(run_id, "stderr.txt"),
        "log": _fixed_run_relative(run_id, "log.txt"),
        "scores": _fixed_run_relative(run_id, RUN_SCORES_FILE),
        "joint_poses": _fixed_run_relative(
            run_id,
            RUN_JOINT_POSES_FILE,
        ),
        "report": _fixed_run_relative(run_id, RUN_REPORT_FILE),
    }
    fixed_outputs = {
        key: project_root / relative
        for key, relative in fixed_output_relatives.items()
    }
    try:
        for relative in (
            fixed_output_relatives["scores"],
            fixed_output_relatives["joint_poses"],
            fixed_output_relatives["report"],
            _fixed_run_relative(run_id, "poses"),
            PROJECT_SCORES_FILE,
            PROJECT_REPORT_FILE,
        ):
            _safe_project_artifact_path(
                project_root,
                relative,
                allow_missing=True,
            )
    except (OSError, _ProtocolPathError) as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_RESULT_PATH_UNSAFE",
            "多配体结果路径包含链接或可能越出项目，已拒绝启动。",
            raw_error=str(exc),
            suggestion="请移除 results、reports 或本次 run/poses 下的链接后重试。",
        )
    for key, path in fixed_outputs.items():
        if os.path.lexists(path):
            return _protocol_error(
                "MULTIPLE_LIGAND_OUTPUT_ALREADY_EXISTS",
                f"prepared run 的 {key} 路径已存在，拒绝覆盖。",
                raw_error=str(path),
                suggestion="请保留当前 run，并重新准备新的运行记录。",
            )

    started_at = _now_iso()
    launch_token = (
        f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    )
    executor_pid = os.getpid()
    executor_identity = vina_adapter.get_process_identity(executor_pid)
    executor_executable = str(
        (executor_identity or {}).get("executable_path") or ""
    )
    if executor_identity is None or not executor_executable:
        return _protocol_error(
            "MULTIPLE_LIGAND_EXECUTOR_IDENTITY_UNAVAILABLE",
            "无法记录 DockStart 执行器进程身份，已拒绝启动 Vina。",
            suggestion="请重新检查 Python 运行环境；该 run 仍保持 prepared。",
        )

    def mark_running(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "prepared":
            return current
        current.update(
            {
                "status": "running",
                "stage": "starting",
                "progress": {
                    "percent": 5,
                    "message": "正在启动多配体共同对接。",
                },
                "started_at": started_at,
                "finished_at": None,
                "duration_seconds": None,
                "pid": None,
                "process_identity": None,
                "process_started_at": None,
                "launch_token": launch_token,
                "executor_pid": executor_pid,
                "executor_executable": executor_executable,
                "executor_identity": executor_identity,
                "trusted_executable": str(execution_binary["path"]),
                "execution_vina": {
                    **execution_binary,
                    "capabilities": detection.capabilities,
                },
                "executed_command": command,
                "exit_code": None,
                "error": None,
            }
        )
        current.pop("cancel_requested_at", None)
        current.pop("process_missing_since", None)
        current.pop("error_message", None)
        return current

    running, transaction_error = _update_run_metadata_transaction(
        str(project_root),
        run_id,
        mark_running,
    )
    if transaction_error:
        return transaction_error
    assert running is not None
    if (
        running.get("status") != "running"
        or running.get("launch_token") != launch_token
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_START_RACE",
            "run 状态在启动前发生变化，未执行 Vina。",
        )
    summary = update_project_run_summary(
        str(project_root),
        run_id,
        {
            "status": "running",
            "stage": "starting",
            "started_at": started_at,
        },
    )
    if not summary.get("ok"):
        interrupted_at = _now_iso()

        def interrupt_before_spawn(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") == "running":
                current.update(
                    {
                        "status": "interrupted",
                        "stage": "interrupted",
                        "finished_at": interrupted_at,
                        "duration_seconds": _duration_seconds(
                            started_at,
                            interrupted_at,
                        ),
                        "progress": {
                            "percent": 0,
                            "message": "project.json 摘要同步失败，未启动 Vina。",
                        },
                        "error_message": "project.json run 摘要同步失败。",
                    }
                )
            return current

        _update_run_metadata_transaction(
            str(project_root),
            run_id,
            interrupt_before_spawn,
        )
        return summary

    def on_started(pid: int) -> None:
        identity = vina_adapter.get_process_identity(pid)
        if identity is None:
            raise RuntimeError("Vina 已启动，但无法记录可验证的进程身份。")
        process_started_at = _now_iso()

        def record_process(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                raise RuntimeError(
                    f"run 已进入 {current.get('status')} 状态，拒绝登记新进程。"
                )
            cancel_requested = _cancel_marker_path(
                project_root,
                run_id,
            ).exists()
            current.update(
                {
                    "pid": pid,
                    "process_identity": identity,
                    "process_started_at": process_started_at,
                    "stage": (
                        "cancelling"
                        if cancel_requested
                        else "running"
                    ),
                    "progress": {
                        "percent": 10,
                        "message": (
                            "检测到取消请求，正在终止 Vina。"
                            if cancel_requested
                            else "AutoDock Vina 正在进行共同搜索。"
                        ),
                    },
                }
            )
            current.pop("process_missing_since", None)
            return current

        updated, callback_error = _update_run_metadata_transaction(
            str(project_root),
            run_id,
            record_process,
        )
        if callback_error:
            raise RuntimeError(
                str(callback_error.get("error") or callback_error)
            )
        assert updated is not None
        update_project_run_summary(
            str(project_root),
            run_id,
            {
                "status": "running",
                "stage": str(updated.get("stage") or "running"),
                "pid": pid,
            },
        )
        if _cancel_marker_path(project_root, run_id).exists():
            termination = vina_adapter.terminate_process(
                pid,
                expected_executable=str(execution_binary["path"]),
                recorded_identity=identity,
            )
            if not termination.get("ok"):
                raise RuntimeError(
                    str(
                        termination.get("message")
                        or "取消已启动的 Vina 失败。"
                    )
                )

    run_result = vina_adapter.run_managed(
        command,
        project_root,
        fixed_outputs["stdout"],
        fixed_outputs["stderr"],
        fixed_outputs["log"],
        on_started=on_started,
    )
    if run_result.error and fixed_outputs["stderr"].is_file():
        with fixed_outputs["stderr"].open(
            "a",
            encoding="utf-8",
        ) as handle:
            if fixed_outputs["stderr"].stat().st_size:
                handle.write("\n")
            handle.write(run_result.error)
    if _cancel_marker_path(project_root, run_id).exists():
        return _finalize_cancelled(project_root, run_id)
    if run_result.exit_code != 0 or run_result.error:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_VINA_FAILED",
            message="AutoDock Vina 多配体共同对接执行失败。",
            raw_error=str(
                run_result.error
                or (
                    fixed_outputs["stderr"].read_text(
                        encoding="utf-8",
                        errors="replace",
                    )[-8192:]
                    if fixed_outputs["stderr"].is_file()
                    else ""
                )
            ),
            suggestion="请查看该 run 的 stdout.txt、stderr.txt 和 log.txt。",
        )

    post_snapshots = _verify_snapshots(project_root, run_id, metadata)
    post_binary, post_binary_error = _verify_execution_binary(
        detection,
        metadata,
    )
    if not post_snapshots.get("ok") or post_binary_error:
        detail = (
            post_snapshots.get("error")
            if not post_snapshots.get("ok")
            else post_binary_error.get("error")
            if post_binary_error
            else {}
        )
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_POST_RUN_INTEGRITY_FAILED",
            message="Vina 返回后，输入、配置或 Vina 二进制完整性复核失败。",
            raw_error=json.dumps(detail or {}, ensure_ascii=False),
            suggestion="请保留该 run 供审计，并从未修改输入重新准备。",
        )
    assert post_binary is not None

    if not fixed_outputs["output"].is_file() or fixed_outputs["output"].stat().st_size <= 0:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_OUTPUT_MISSING",
            message="Vina 成功退出，但没有生成非空 out.pdbqt。",
            suggestion="请查看该 run 的 stdout.txt 和 stderr.txt。",
        )
    try:
        output_text = fixed_outputs["output"].read_text(
            encoding="utf-8",
            errors="strict",
        )
        log_text = fixed_outputs["log"].read_text(
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, UnicodeError) as exc:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_OUTPUT_READ_ERROR",
            message="读取共同对接输出时发生错误。",
            raw_error=str(exc),
        )

    parsed_output = parse_multiple_ligand_output_text(
        output_text,
        members,
    )
    if not parsed_output.get("ok"):
        detail = parsed_output.get("error") or {}
        return _mark_failed(
            project_root,
            run_id,
            running,
            code=str(
                detail.get("code")
                or "MULTIPLE_LIGAND_OUTPUT_PARSE_ERROR"
            ),
            message=str(
                detail.get("message")
                or "共同对接 out.pdbqt 无法解析。"
            ),
            raw_error=str(detail.get("raw_error") or ""),
            suggestion=str(detail.get("suggestion") or ""),
        )
    parsed_scores = parse_vina_log_text(log_text)
    if isinstance(parsed_scores, dict):
        detail = parsed_scores.get("error") or {}
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_SCORE_TABLE_INVALID",
            message="共同对接日志中的联合评分表无法解析。",
            raw_error=json.dumps(detail, ensure_ascii=False),
            suggestion="请确认 log.txt 来自本次 Vina 运行。",
        )
    score_modes = [int(score.get("mode") or 0) for score in parsed_scores]
    expected_score_modes = list(range(1, len(score_modes) + 1))
    frozen_num_modes = int(frozen_vina.get("num_modes") or 0)
    if (
        score_modes != expected_score_modes
        or any(mode <= 0 for mode in score_modes)
        or len(set(score_modes)) != len(score_modes)
        or any(mode > frozen_num_modes for mode in score_modes)
    ):
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_SCORE_MODE_SEQUENCE_INVALID",
            message=(
                "共同对接日志的 Mode 必须是从 1 开始的连续唯一正整数，"
                "且不能超过冻结的 num_modes。"
            ),
            raw_error=(
                f"observed={score_modes}; "
                f"num_modes={frozen_num_modes}"
            ),
            suggestion="请确认 log.txt 来自本次运行且未被拼接或修改。",
        )

    output_by_mode = {
        int(model["mode"]): model
        for model in parsed_output["models"]
    }
    scores: list[dict[str, Any]] = []
    for score in parsed_scores:
        mode = int(score["mode"])
        model = output_by_mode.get(mode)
        if model is not None:
            for key, model_key in (
                ("affinity_kcal_mol", "joint_affinity_kcal_mol"),
                ("rmsd_lb", "rmsd_lb"),
                ("rmsd_ub", "rmsd_ub"),
            ):
                if abs(float(score[key]) - float(model[model_key])) > 0.002:
                    return _mark_failed(
                        project_root,
                        run_id,
                        running,
                        code="MULTIPLE_LIGAND_SCORE_OUTPUT_MISMATCH",
                        message=(
                            f"Mode {mode} 的日志评分与 out.pdbqt "
                            "REMARK VINA RESULT 不一致。"
                        ),
                        raw_error=(
                            f"{key}: log={score[key]}; "
                            f"output={model[model_key]}"
                        ),
                    )
        scores.append(
            {
                "mode": mode,
                "joint_affinity_kcal_mol": float(
                    score["affinity_kcal_mol"]
                ),
                "rmsd_lb": float(score["rmsd_lb"]),
                "rmsd_ub": float(score["rmsd_ub"]),
                "pose_available": model is not None,
                "score_is_joint": True,
                "per_member_scores_available": False,
            }
        )
    available_score_modes = {int(score["mode"]) for score in scores}
    missing_score_modes = sorted(
        set(output_by_mode) - available_score_modes
    )
    if missing_score_modes:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_OUTPUT_SCORE_ROW_MISSING",
            message="out.pdbqt 中的部分联合构象没有对应日志评分行。",
            raw_error=", ".join(map(str, missing_score_modes)),
        )
    if _cancel_marker_path(project_root, run_id).exists():
        return _finalize_cancelled(project_root, run_id)

    manifest_models: list[dict[str, Any]] = []
    try:
        for model in parsed_output["models"]:
            mode = int(model["mode"])
            model_members: list[dict[str, Any]] = []
            for parsed_member in model["members"]:
                member_index = int(parsed_member["member_index"])
                relative = _fixed_run_relative(
                    run_id,
                    "poses",
                    f"mode_{mode:03d}",
                    f"member_{member_index:03d}.pdbqt",
                )
                _safe_atomic_write_text(
                    project_root,
                    relative,
                    str(parsed_member["content"]),
                )
                snapshot = _artifact_snapshot(project_root, relative)
                model_members.append(
                    {
                        "member_index": member_index,
                        "member_id": members[member_index - 1][
                            "member_id"
                        ],
                        "file": relative,
                        "sha256": snapshot["sha256"],
                        "size_bytes": snapshot["size_bytes"],
                        "atom_count": parsed_member["atom_count"],
                        "torsdof": parsed_member["torsdof"],
                        "identity_sha256": parsed_member[
                            "identity_sha256"
                        ],
                    }
                )
            manifest_models.append(
                {
                    "mode": mode,
                    "joint_affinity_kcal_mol": model[
                        "joint_affinity_kcal_mol"
                    ],
                    "rmsd_lb": model["rmsd_lb"],
                    "rmsd_ub": model["rmsd_ub"],
                    "members": model_members,
                }
            )
    except (OSError, _ProtocolPathError) as exc:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_RESULT_PATH_UNSAFE",
            message="保存联合构象成员文件时发现不安全的结果路径。",
            raw_error=str(exc),
            suggestion="请移除本次 run/poses 下的链接并重新准备运行。",
        )

    joint_manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "run_id": run_id,
        "member_count": MEMBER_COUNT,
        "members": [
            {
                "member_index": member["member_index"],
                "member_id": member["member_id"],
                "display_name": member["display_name"],
                "sha256": member["sha256"],
                "stats": member["stats"],
            }
            for member in members
        ],
        "models": manifest_models,
        "available_modes": parsed_output["available_modes"],
        "scores": scores,
        "affinity_scope": "joint_two_ligand_pose",
        "rmsd_scope": "joint_pose_relative_to_best_mode",
        "per_member_scores_available": False,
        "output_model_is_pose_authority": True,
    }
    scores_text = _scores_csv_text(scores)
    try:
        _safe_atomic_write_json(
            project_root,
            fixed_output_relatives["joint_poses"],
            joint_manifest,
        )
        _safe_atomic_write_text(
            project_root,
            fixed_output_relatives["scores"],
            scores_text,
        )
    except (OSError, _ProtocolPathError) as exc:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_RESULT_PATH_UNSAFE",
            message="保存共同对接运行结果时发现不安全的结果路径。",
            raw_error=str(exc),
            suggestion="请移除本次 run 结果路径中的链接并重新准备运行。",
        )

    latest_running, latest_error = _read_run_metadata(
        str(project_root),
        run_id,
    )
    if latest_error:
        return latest_error
    assert latest_running is not None
    if _cancel_marker_path(project_root, run_id).exists():
        return _finalize_cancelled(project_root, run_id)
    execution_features = (
        detection.capabilities.get("features")
        if isinstance(detection.capabilities, dict)
        and isinstance(detection.capabilities.get("features"), dict)
        else {}
    )
    execution_multiple_ligands_capability = (
        dict(execution_features.get("multiple_ligands") or {})
        if isinstance(
            execution_features.get("multiple_ligands"),
            dict,
        )
        else {}
    )
    finished = dict(latest_running)
    finished_at = _now_iso()
    finished.update(
        {
            "status": "finished",
            "stage": "finished",
            "progress": {
                "percent": 100,
                "message": "多配体共同对接已完成。",
            },
            "finished_at": finished_at,
            "duration_seconds": _duration_seconds(
                finished.get("started_at"),
                finished_at,
            ),
            "pid": (
                run_result.pid
                if run_result.pid is not None
                else finished.get("pid")
            ),
            "exit_code": 0,
            "execution_vina": {
                **post_binary,
                "capabilities": detection.capabilities,
            },
            "multiple_ligands_capability": (
                execution_multiple_ligands_capability
            ),
            "available_modes": parsed_output["available_modes"],
            "pose_count": len(parsed_output["available_modes"]),
            "scores": scores,
            "best_affinity": (
                min(
                    float(score["joint_affinity_kcal_mol"])
                    for score in scores
                )
                if scores
                else None
            ),
            "per_member_scores_available": False,
            "error": None,
        }
    )
    try:
        artifacts = {
            "output": _artifact_snapshot(
                project_root,
                fixed_output_relatives["output"],
            ),
            "stdout": _artifact_snapshot(
                project_root,
                fixed_output_relatives["stdout"],
            ),
            "stderr": _artifact_snapshot(
                project_root,
                fixed_output_relatives["stderr"],
            ),
            "log": _artifact_snapshot(
                project_root,
                fixed_output_relatives["log"],
            ),
            "scores": _artifact_snapshot(
                project_root,
                fixed_output_relatives["scores"],
            ),
            "joint_poses": _artifact_snapshot(
                project_root,
                fixed_output_relatives["joint_poses"],
            ),
        }
        finished["artifacts"] = artifacts
        report_text = _build_report(finished, joint_manifest)
        _safe_atomic_write_text(
            project_root,
            fixed_output_relatives["report"],
            report_text,
        )
        artifacts["report"] = _artifact_snapshot(
                project_root,
                fixed_output_relatives["report"],
        )
    except (OSError, _ProtocolPathError) as exc:
        return _mark_failed(
            project_root,
            run_id,
            running,
            code="MULTIPLE_LIGAND_RESULT_PATH_UNSAFE",
            message="提交共同对接运行产物前发现不安全的结果路径。",
            raw_error=str(exc),
            suggestion="请移除结果路径中的链接并重新准备运行。",
        )
    finished["artifacts"] = artifacts

    def finalize_success(current: dict[str, Any]) -> dict[str, Any]:
        if (
            current.get("status") != "running"
            or _cancel_marker_path(project_root, run_id).exists()
        ):
            return current
        current.update(finished)
        return current

    finalized, finalize_error = _update_run_metadata_transaction(
        str(project_root),
        run_id,
        finalize_success,
    )
    if finalize_error:
        return finalize_error
    assert finalized is not None
    if finalized.get("status") == "cancelled":
        return _finalize_cancelled(project_root, run_id)
    if (
        finalized.get("status") == "running"
        and _cancel_marker_path(project_root, run_id).exists()
    ):
        return _finalize_cancelled(project_root, run_id)
    if finalized.get("status") != "finished":
        return _protocol_error(
            "MULTIPLE_LIGAND_FINALIZE_RACE",
            f"结果收敛时 run 已进入 {finalized.get('status')} 状态。",
        )
    finished = finalized
    try:
        _cancel_marker_path(project_root, run_id).unlink(missing_ok=True)
    except OSError:
        pass

    warnings = list(finished.get("warnings") or [])
    project_outputs_published = False
    project_scores_snapshot: dict[str, Any] | None = None
    project_report_snapshot: dict[str, Any] | None = None
    try:
        _safe_atomic_write_text(
            project_root,
            PROJECT_SCORES_FILE,
            scores_text,
        )
        _safe_atomic_write_text(
            project_root,
            PROJECT_REPORT_FILE,
            report_text,
        )
        project_scores_snapshot = _artifact_snapshot(
            project_root,
            PROJECT_SCORES_FILE,
        )
        project_report_snapshot = _artifact_snapshot(
            project_root,
            PROJECT_REPORT_FILE,
        )
        project_outputs_published = True
    except (OSError, _ProtocolPathError) as exc:
        warnings.append(
            "run 已完成，但项目级汇总文件未发布；"
            f"请使用本次 run 内的 scores.csv 和报告。原因：{exc}"
        )
    if (
        project_outputs_published
        and project_scores_snapshot is not None
        and project_report_snapshot is not None
    ):
        def record_project_outputs(
            current: dict[str, Any],
        ) -> dict[str, Any]:
            if current.get("status") != "finished":
                return current
            current_artifacts = (
                dict(current.get("artifacts") or {})
                if isinstance(current.get("artifacts"), dict)
                else {}
            )
            current_artifacts.update(
                {
                    "project_scores": project_scores_snapshot,
                    "project_report": project_report_snapshot,
                }
            )
            current["artifacts"] = current_artifacts
            current["project_scores_file"] = PROJECT_SCORES_FILE
            current["project_report_file"] = PROJECT_REPORT_FILE
            current["reported_at"] = current.get("finished_at")
            return current

        try:
            published_metadata, publication_error = (
                _update_run_metadata_transaction(
                    str(project_root),
                    run_id,
                    record_project_outputs,
                )
            )
            if publication_error:
                warnings.append(
                    "项目级汇总已发布，但 metadata 证据更新失败。"
                )
            elif published_metadata is not None:
                finished = published_metadata
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                "项目级汇总已发布，但 metadata 证据更新失败："
                f"{exc}"
            )

    summary_fields = {
        "status": "finished",
        "started_at": finished["started_at"],
        "finished_at": finished["finished_at"],
        "best_affinity": finished["best_affinity"],
        "pose_count": finished["pose_count"],
        "available_modes": finished["available_modes"],
        "output_file": finished["output_file"],
        "scores_file": finished["scores_file"],
        "report_file": finished["report_file"],
    }
    if project_outputs_published:
        summary_fields.update(
            {
                "project_scores_file": PROJECT_SCORES_FILE,
                "project_report_file": PROJECT_REPORT_FILE,
            }
        )
    summary = update_project_run_summary(
        str(project_root),
        run_id,
        summary_fields,
    )
    if not summary.get("ok"):
        warnings.append(
            "run 已完成，但 project.json 的运行摘要更新失败；metadata.json 仍保留完整结果。"
        )
    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "status": "finished",
        "metadata": finished,
        "members": members,
        "available_modes": finished["available_modes"],
        "pose_count": finished["pose_count"],
        "best_affinity": finished["best_affinity"],
        "scores": scores,
        "joint_poses_file": finished["joint_poses_file"],
        "scores_file": finished["scores_file"],
        "report_file": finished["report_file"],
        "project_outputs_published": project_outputs_published,
        "warnings": warnings,
        "message": (
            f"共同对接已完成，out.pdbqt 中有 {finished['pose_count']} "
            "个通过成员校验的联合构象。"
        ),
        "error": None,
    }


def execute_multiple_ligand_run(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    """Execute and always converge unexpected I/O failures out of running."""

    try:
        return _execute_multiple_ligand_run_impl(project_dir, run_id)
    except Exception as exc:  # noqa: BLE001 - public fail-closed boundary.
        try:
            metadata, project_root, metadata_error = (
                _load_protocol_metadata(project_dir, run_id)
            )
            if (
                metadata_error is None
                and metadata is not None
                and project_root is not None
            ):
                status = str(metadata.get("status") or "")
                if status == "running":
                    return _mark_failed(
                        project_root,
                        run_id,
                        metadata,
                        code="MULTIPLE_LIGAND_EXECUTION_IO_ERROR",
                        message=(
                            "多配体共同对接执行或后处理发生未预期的 I/O 错误。"
                        ),
                        raw_error=repr(exc),
                        suggestion=(
                            "请查看该 run 的 stdout.txt、stderr.txt 和 log.txt；"
                            "运行已收敛为 failed。"
                        ),
                    )
                if status == "finished":
                    return {
                        "ok": True,
                        "project_dir": str(project_root),
                        "run_id": run_id,
                        "protocol_id": PROTOCOL_ID,
                        "status": "finished",
                        "metadata": metadata,
                        "warnings": [
                            "run 已完成，但完成后的附加 I/O 操作失败："
                            f"{exc}"
                        ],
                        "message": "共同对接结果已提交；附加发布未完全完成。",
                        "error": None,
                    }
        except Exception:
            pass
        return _protocol_error(
            "MULTIPLE_LIGAND_EXECUTION_IO_ERROR",
            "多配体共同对接执行或后处理发生未预期的 I/O 错误。",
            raw_error=repr(exc),
            suggestion="请刷新运行状态并查看 run 目录中的日志文件。",
        ) | {
            "project_dir": str(
                Path(project_dir).expanduser().resolve(strict=False)
            ),
            "run_id": run_id,
        }


def get_multiple_ligand_run_status(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    metadata, project_root, metadata_error = _load_protocol_metadata(
        project_dir,
        run_id,
    )
    if metadata_error:
        return metadata_error
    assert metadata is not None and project_root is not None
    available_modes = [
        int(value)
        for value in (
            metadata.get("available_modes")
            if isinstance(metadata.get("available_modes"), list)
            else []
        )
    ]
    files = {}
    for key, filename in (
        ("metadata", "metadata.json"),
        ("config", "config_snapshot.txt"),
        ("output", RUN_OUTPUT_FILE),
        ("scores", RUN_SCORES_FILE),
        ("joint_poses", RUN_JOINT_POSES_FILE),
        ("report", RUN_REPORT_FILE),
        ("stdout", "stdout.txt"),
        ("stderr", "stderr.txt"),
        ("log", "log.txt"),
    ):
        relative = _fixed_run_relative(run_id, filename)
        try:
            path = _safe_project_artifact_path(
                project_root,
                relative,
                allow_missing=True,
            )
            is_file = path.is_file()
            files[key] = {
                "relative_path": relative,
                "exists": is_file,
                "size_bytes": path.stat().st_size if is_file else 0,
                "safe": True,
            }
        except (OSError, _ProtocolPathError) as exc:
            files[key] = {
                "relative_path": relative,
                "exists": False,
                "size_bytes": 0,
                "safe": False,
                "error": str(exc),
            }
    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "status": str(metadata.get("status") or ""),
        "metadata": metadata,
        "members": list(metadata.get("members") or []),
        "available_modes": available_modes,
        "best_affinity": metadata.get("best_affinity"),
        "scores": list(metadata.get("scores") or []),
        "files": files,
        "warnings": list(metadata.get("warnings") or []),
        "message": "多配体共同对接运行状态已读取。",
        "error": None,
    }


def _load_joint_manifest(
    project_root: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    relative = _fixed_run_relative(run_id, RUN_JOINT_POSES_FILE)
    try:
        path = _safe_project_artifact_path(
            project_root,
            relative,
            allow_missing=False,
        )
    except (OSError, _ProtocolPathError) as exc:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_PATH_UNSAFE",
            "joint_poses.json 路径包含链接或越出项目。",
            raw_error=str(exc),
        )
    artifact = (
        (metadata.get("artifacts") or {}).get("joint_poses")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    if not isinstance(artifact, dict):
        artifact = {}
    expected_sha256 = str(artifact.get("sha256") or "").lower()
    try:
        integrity_ok = (
            path.is_file()
            and SHA256_PATTERN.fullmatch(expected_sha256) is not None
            and _sha256_file(path).lower() == expected_sha256
        )
    except OSError as exc:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_READ_ERROR",
            "读取 joint_poses.json 完整性信息时发生错误。",
            raw_error=str(exc),
        )
    if not integrity_ok:
        return None, _protocol_error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_INTEGRITY_ERROR",
            "joint_poses.json 缺失或与完成时 SHA256 不一致。",
            raw_error=str(path),
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, _protocol_error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_READ_ERROR",
            "joint_poses.json 无法读取。",
            raw_error=str(exc),
        )
    if (
        not isinstance(value, dict)
        or value.get("protocol_id") != PROTOCOL_ID
        or value.get("run_id") != run_id
    ):
        return None, _protocol_error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_INVALID",
            "joint_poses.json 的协议或 run 身份不匹配。",
        )
    return value, None


def load_multiple_ligand_pose(
    project_dir: str,
    run_id: str,
    mode: int,
    member_index: int,
) -> dict[str, Any]:
    metadata, project_root, metadata_error = _load_protocol_metadata(
        project_dir,
        run_id,
    )
    if metadata_error:
        return metadata_error
    assert metadata is not None and project_root is not None
    if str(metadata.get("status") or "") != "finished":
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_NOT_READY",
            "只有 finished 状态的共同对接 run 可以加载构象。",
        )
    if isinstance(mode, bool) or not isinstance(mode, int) or mode <= 0:
        return _protocol_error(
            "MULTIPLE_LIGAND_MODE_INVALID",
            "mode 必须是正整数。",
        )
    if member_index not in {1, 2}:
        return _protocol_error(
            "MULTIPLE_LIGAND_MEMBER_INDEX_INVALID",
            "member_index 只能是 1 或 2。",
        )
    manifest, manifest_error = _load_joint_manifest(
        project_root,
        run_id,
        metadata,
    )
    if manifest_error:
        return manifest_error
    assert manifest is not None
    model = next(
        (
            candidate
            for candidate in manifest.get("models") or []
            if isinstance(candidate, dict)
            and int(candidate.get("mode") or 0) == mode
        ),
        None,
    )
    if model is None:
        return _protocol_error(
            "MULTIPLE_LIGAND_MODE_NOT_AVAILABLE",
            f"out.pdbqt 中没有可加载的联合构象 Mode {mode}。",
            suggestion="请选择 available_modes 中的模式。",
        )
    pose_member = next(
        (
            candidate
            for candidate in model.get("members") or []
            if isinstance(candidate, dict)
            and int(candidate.get("member_index") or 0) == member_index
        ),
        None,
    )
    if pose_member is None:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_MEMBER_NOT_FOUND",
            f"Mode {mode} 缺少 member {member_index} 构象。",
        )
    expected_relative = _fixed_run_relative(
        run_id,
        "poses",
        f"mode_{mode:03d}",
        f"member_{member_index:03d}.pdbqt",
    )
    if pose_member.get("file") != expected_relative:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_PATH_INVALID",
            "成员构象路径与固定 run 路径不一致。",
        )
    try:
        pose_path = _safe_project_artifact_path(
            project_root,
            expected_relative,
            allow_missing=False,
        )
    except (OSError, _ProtocolPathError) as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_PATH_UNSAFE",
            "成员构象路径包含链接或越出项目。",
            raw_error=str(exc),
        )
    expected_pose_sha256 = str(pose_member.get("sha256") or "").lower()
    try:
        pose_integrity_ok = (
            pose_path.is_file()
            and SHA256_PATTERN.fullmatch(expected_pose_sha256) is not None
            and _sha256_file(pose_path).lower()
            == expected_pose_sha256
        )
    except OSError as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_READ_ERROR",
            "读取成员构象完整性信息时发生错误。",
            raw_error=str(exc),
        )
    if not pose_integrity_ok:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_HASH_MISMATCH",
            "成员构象文件缺失或与完成时 SHA256 不一致。",
            raw_error=str(pose_path),
        )
    receptor_relative = _fixed_run_relative(
        run_id,
        "inputs",
        "receptor.pdbqt",
    )
    try:
        receptor_path = _safe_project_artifact_path(
            project_root,
            receptor_relative,
            allow_missing=False,
        )
    except (OSError, _ProtocolPathError) as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_PATH_UNSAFE",
            "冻结受体路径包含链接或越出项目。",
            raw_error=str(exc),
        )
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    receptor_snapshot = (
        snapshots.get("receptor")
        if isinstance(snapshots.get("receptor"), dict)
        else {}
    )
    try:
        receptor_integrity_ok = (
            receptor_path.is_file()
            and _sha256_file(receptor_path).lower()
            == str(receptor_snapshot.get("sha256") or "").lower()
        )
    except OSError as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_READ_ERROR",
            "读取冻结受体完整性信息时发生错误。",
            raw_error=str(exc),
        )
    if not receptor_integrity_ok:
        return _protocol_error(
            "MULTIPLE_LIGAND_RECEPTOR_HASH_MISMATCH",
            "冻结受体文件缺失或与准备时 SHA256 不一致。",
            raw_error=str(receptor_path),
        )
    try:
        receptor_content = receptor_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        pose_content = pose_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_POSE_READ_ERROR",
            "读取受体或成员构象文件时发生错误。",
            raw_error=str(exc),
        )
    receptor_view, receptor_format, receptor_warnings = _viewer_content(
        receptor_content,
        "pdbqt",
    )
    pose_view, pose_format, pose_warnings = _viewer_content(
        pose_content,
        "pdbqt",
    )
    receptor_result = ViewerStructureResult(
        ok=True,
        file_kind="simultaneous_multi_ligand_receptor",
        relative_path=receptor_relative,
        absolute_path=str(receptor_path),
        exists=True,
        format=receptor_format,
        content=receptor_view,
        size_bytes=len(receptor_view.encode("utf-8")),
        message="已读取本次共同对接冻结的刚性受体。",
        warnings=receptor_warnings,
        error=None,
    ).to_dict()
    pose_result = ViewerStructureResult(
        ok=True,
        file_kind="simultaneous_multi_ligand_member_pose",
        relative_path=expected_relative,
        absolute_path=str(pose_path),
        exists=True,
        format=pose_format,
        content=pose_view,
        size_bytes=len(pose_view.encode("utf-8")),
        message=f"已读取联合 Mode {mode} 的 member {member_index}。",
        warnings=pose_warnings,
        error=None,
    ).to_dict()
    source_member = metadata["members"][member_index - 1]
    available_modes = [
        int(value)
        for value in manifest.get("available_modes") or []
    ]
    warnings = [
        JOINT_SCORE_DISCLAIMER,
        *receptor_warnings,
        *pose_warnings,
    ]
    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "mode": mode,
        "member_index": member_index,
        "member": {
            "member_index": member_index,
            "member_id": source_member.get("member_id"),
            "display_name": source_member.get("display_name"),
            "source_name": source_member.get("source_name"),
            "source_sha256": source_member.get("source_sha256"),
        },
        "receptor": receptor_result,
        "pose": pose_result,
        "content": pose_content,
        "format": "pdbqt",
        "available_modes": available_modes,
        "joint_affinity_kcal_mol": model[
            "joint_affinity_kcal_mol"
        ],
        "joint_score": {
            "joint_affinity_kcal_mol": model[
                "joint_affinity_kcal_mol"
            ],
            "rmsd_lb": model["rmsd_lb"],
            "rmsd_ub": model["rmsd_ub"],
        },
        "per_member_score_available": False,
        "warnings": warnings,
        "message": (
            f"已加载联合 Mode {mode} 的 member {member_index}；"
            "显示的 affinity 属于完整两配体联合构象。"
        ),
        "error": None,
    }


def build_multiple_ligand_markdown_report(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    """Build the dedicated report without writing or mutating any file."""

    metadata, project_root, metadata_error = _load_protocol_metadata(
        project_dir,
        run_id,
    )
    if metadata_error:
        return metadata_error
    assert metadata is not None and project_root is not None
    if str(metadata.get("status") or "") != "finished":
        return _protocol_error(
            "MULTIPLE_LIGAND_REPORT_NOT_READY",
            "只有 finished 状态的共同对接 run 可以生成报告。",
        )
    manifest, manifest_error = _load_joint_manifest(
        project_root,
        run_id,
        metadata,
    )
    if manifest_error:
        return manifest_error
    assert manifest is not None
    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "metadata": metadata,
        "joint_poses": manifest,
        "report_text": _build_report(metadata, manifest),
        "report_file": _fixed_run_relative(run_id, RUN_REPORT_FILE),
        "project_report_file": PROJECT_REPORT_FILE,
        "message": "多配体共同对接（实验性）报告内容已生成。",
        "error": None,
    }


def export_multiple_ligand_markdown_report(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    built = build_multiple_ligand_markdown_report(
        project_dir,
        run_id,
    )
    if not built.get("ok"):
        return built
    project_root = Path(str(built["project_dir"])).resolve()
    report_text = str(built["report_text"])
    relative = str(built["report_file"])
    reported_at = _now_iso()
    try:
        _safe_atomic_write_text(project_root, relative, report_text)
        report_snapshot = _artifact_snapshot(project_root, relative)
    except (OSError, _ProtocolPathError) as exc:
        return _protocol_error(
            "MULTIPLE_LIGAND_REPORT_PATH_UNSAFE",
            "本次 run 的报告路径包含链接、越出项目或无法安全写入。",
            raw_error=str(exc),
            suggestion="请移除本次 run 报告路径中的链接后重试。",
        )

    def record_report_artifact(current: dict[str, Any]) -> dict[str, Any]:
        if (
            current.get("protocol_id") != PROTOCOL_ID
            or current.get("status") != "finished"
        ):
            return current
        artifacts = (
            dict(current.get("artifacts") or {})
            if isinstance(current.get("artifacts"), dict)
            else {}
        )
        artifacts["report"] = report_snapshot
        current["artifacts"] = artifacts
        current["report_file"] = relative
        current["reported_at"] = reported_at
        return current

    try:
        updated, transaction_error = _update_run_metadata_transaction(
            str(project_root),
            run_id,
            record_report_artifact,
        )
    except Exception as exc:  # noqa: BLE001 - converted to API error.
        return _protocol_error(
            "MULTIPLE_LIGAND_REPORT_METADATA_UPDATE_FAILED",
            "run 内报告已写入，但更新 metadata.json 时发生错误。",
            raw_error=str(exc),
            suggestion="请保留 run 内报告并刷新运行状态后重试。",
        )
    if transaction_error:
        return transaction_error
    assert updated is not None
    updated_report = (
        (updated.get("artifacts") or {}).get("report")
        if isinstance(updated.get("artifacts"), dict)
        else {}
    )
    if (
        not isinstance(updated_report, dict)
        or updated_report.get("sha256") != report_snapshot.get("sha256")
    ):
        return _protocol_error(
            "MULTIPLE_LIGAND_REPORT_METADATA_RACE",
            "报告写入后 run 状态发生变化，未覆盖当前 metadata。",
            suggestion="请刷新状态后重试；run 内报告文件已保留。",
        )

    warnings: list[str] = []
    project_report_published = False
    project_report_snapshot: dict[str, Any] | None = None
    try:
        _safe_atomic_write_text(
            project_root,
            PROJECT_REPORT_FILE,
            report_text,
        )
        project_report_snapshot = _artifact_snapshot(
            project_root,
            PROJECT_REPORT_FILE,
        )
        project_report_published = True
    except (OSError, _ProtocolPathError) as exc:
        warnings.append(
            "run 内报告已生成，但项目级报告未发布；"
            f"请使用 {relative}。原因：{exc}"
        )

    if project_report_published and project_report_snapshot is not None:
        def record_project_report(
            current: dict[str, Any],
        ) -> dict[str, Any]:
            if (
                current.get("protocol_id") != PROTOCOL_ID
                or current.get("status") != "finished"
            ):
                return current
            artifacts = (
                dict(current.get("artifacts") or {})
                if isinstance(current.get("artifacts"), dict)
                else {}
            )
            artifacts["project_report"] = project_report_snapshot
            current["artifacts"] = artifacts
            current["project_report_file"] = PROJECT_REPORT_FILE
            return current

        try:
            project_metadata, project_metadata_error = (
                _update_run_metadata_transaction(
                    str(project_root),
                    run_id,
                    record_project_report,
                )
            )
            if project_metadata_error:
                warnings.append(
                    "项目级报告已写入，但其 metadata 证据更新失败。"
                )
            elif project_metadata is not None:
                updated = project_metadata
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                "项目级报告已写入，但其 metadata 证据更新失败："
                f"{exc}"
            )

    summary_fields = {"report_file": relative}
    if project_report_published:
        summary_fields["project_report_file"] = PROJECT_REPORT_FILE
    project_update = update_project_run_summary(
        str(project_root),
        run_id,
        summary_fields,
    )
    if not project_update.get("ok"):
        warnings.append(
            "报告已生成，但 project.json 的运行摘要更新失败。"
        )
    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "protocol_id": PROTOCOL_ID,
        "report_file": relative,
        "project_report_file": PROJECT_REPORT_FILE,
        "project_report_published": project_report_published,
        "reported_at": reported_at,
        "metadata": updated,
        "content": report_text,
        "warnings": warnings,
        "message": "多配体共同对接（实验性）Markdown 报告已生成。",
        "error": None,
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command == "prepare":
        if len(sys.argv) < 4:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_PREPARE_ARGS",
                    "prepare 需要 PROJECT_DIR 和 LIGAND_FILES_JSON。",
                )
            )
            return
        try:
            ligand_files = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_FILES_JSON_INVALID",
                    "LIGAND_FILES_JSON 不是有效 JSON。",
                    raw_error=str(exc),
                )
            )
            return
        _print_json(
            prepare_multiple_ligand_run(
                sys.argv[2],
                ligand_files,
            )
        )
        return
    if command == "run":
        if len(sys.argv) < 4:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_RUN_ARGS",
                    "run 需要 PROJECT_DIR 和 RUN_ID。",
                )
            )
            return
        _print_json(execute_multiple_ligand_run(sys.argv[2], sys.argv[3]))
        return
    if command == "status":
        if len(sys.argv) < 4:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_STATUS_ARGS",
                    "status 需要 PROJECT_DIR 和 RUN_ID。",
                )
            )
            return
        _print_json(
            get_multiple_ligand_run_status(sys.argv[2], sys.argv[3])
        )
        return
    if command == "cancel":
        if len(sys.argv) < 4:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_CANCEL_ARGS",
                    "cancel 需要 PROJECT_DIR 和 RUN_ID。",
                )
            )
            return
        _print_json(
            cancel_multiple_ligand_run(sys.argv[2], sys.argv[3])
        )
        return
    if command == "load-pose":
        if len(sys.argv) < 6:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_LOAD_POSE_ARGS",
                    (
                        "load-pose 需要 PROJECT_DIR、RUN_ID、MODE "
                        "和 MEMBER_INDEX。"
                    ),
                )
            )
            return
        try:
            mode = int(sys.argv[4])
            member_index = int(sys.argv[5])
        except ValueError as exc:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_LOAD_POSE_INDEX_INVALID",
                    "MODE 和 MEMBER_INDEX 必须是整数。",
                    raw_error=str(exc),
                )
            )
            return
        _print_json(
            load_multiple_ligand_pose(
                sys.argv[2],
                sys.argv[3],
                mode,
                member_index,
            )
        )
        return
    if command == "report":
        if len(sys.argv) < 4:
            _print_json(
                _protocol_error(
                    "MULTIPLE_LIGAND_REPORT_ARGS",
                    "report 需要 PROJECT_DIR 和 RUN_ID。",
                )
            )
            return
        _print_json(
            export_multiple_ligand_markdown_report(
                sys.argv[2],
                sys.argv[3],
            )
        )
        return
    _print_json(
        _protocol_error(
            "MULTIPLE_LIGAND_COMMAND_UNKNOWN",
            f"未知命令：{command}",
            suggestion=(
                "可用命令：prepare、run、status、cancel、load-pose、report。"
            ),
        )
    )


if __name__ == "__main__":
    main()
