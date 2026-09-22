"""Project creation and PDBQT import helpers for DockStart."""

from __future__ import annotations

import csv
import copy
import errno
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess  # Compatibility surface for existing no-execution regression tests.
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from adapters import vina_adapter
from dockstart_core import __version__
from dockstart_core.flexible_movement import (
    analyze_flexible_movement,
    canonical_flexible_movement_json,
)
from dockstart_core.persistence import atomic_write_bytes as _atomic_write_bytes
from dockstart_core.persistence import atomic_write_text as _atomic_write_text
from dockstart_core.pose_comparison import compare_local_only_poses
from dockstart_core.preparation_models import (
    PreparationState,
    default_preparation_result,
    preparation_state_from_dict,
)
from dockstart_core.settings import docking_default_overrides, load_settings
from dockstart_core.structure_review import build_structure_review

PROJECT_DIRS = ("raw", "prepared", "configs", "runs", "results", "reports", "preparation", "maps")
PROJECT_NAME_PATTERN = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")
RUN_ID_PATTERN = re.compile(r"^run_(\d{3,})$")
VINA_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
VINA_SCORE_ROW_PATTERN = re.compile(
    rf"^\s*(\d+)\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})(?:\s|$)",
)
VINA_EVALUATION_LINE_PATTERN = re.compile(
    rf"^\s*(?:\((\d+)\)\s*)?([^:]+?)\s*:\s*({VINA_NUMBER_PATTERN})\s*\(kcal/mol\)",
    re.IGNORECASE,
)
SCORES_CSV_FIELDS = ("mode", "affinity_kcal_mol", "rmsd_lb", "rmsd_ub")
MULTIPLE_LIGAND_SCORES_CSV_FIELDS = (
    "mode",
    "joint_affinity_kcal_mol",
    "rmsd_lb",
    "rmsd_ub",
    "pose_available",
    "score_scope",
)
DOCKING_SCORE_DISCLAIMER = "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
VINA_RUN_MODES = {"dock", "score_only", "local_only"}
POSE_INPUT_ATTESTATION_VERSION = 1
POSE_INPUT_ATTESTATION_CLAIM = "same_receptor_coordinate_frame"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RUN_REPORT_FILE = "docking_report.md"
PROJECT_REPORT_FILE = Path("reports", "docking_report.md").as_posix()
CURRENT_PROJECT_SCHEMA_VERSION = 1
LOCAL_ONLY_EXECUTION_PLAN_SCHEMA_VERSION = 2
LOCAL_ONLY_EXECUTION_PLAN_KIND = "local_only_with_baseline"
VINA_GRID_MEMORY_WARNING_BYTES = 512 * 1024 * 1024
VINA_GRID_MEMORY_HARD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
PDBQT_SOURCE_LABEL_MAX_CHARS = 240
AD4ZN_PROTOCOL_ID = "ad4zn_beta"
HYDRATED_PROTOCOL_ID = "hydrated_ad4_experimental"
HYDRATED_RETAINED_OUTPUT_NAME = "hydrated_retained.pdbqt"
HYDRATED_WATER_FREE_OUTPUT_NAME = "ligand_water_free.pdbqt"
HYDRATED_WATERS_MANIFEST_NAME = "waters_manifest.json"
VINA_OUTPUT_NORMALIZATION_SCHEMA_VERSION = 1
VINA_OUTPUT_NORMALIZATION_METHOD = "torsdof_endmdl_nul_padding_v1"
MULTIPLE_LIGAND_PROTOCOL_ID = "simultaneous_multi_ligand"
FLEXIBLE_RECEPTOR_PROTOCOL_ID = "flexible_single"
FLEXIBLE_MOVEMENT_SCHEMA_ID = "dockstart.flexible_movement.v1"
FLEXIBLE_MOVEMENT_METHOD = (
    "same_receptor_frame_heavy_atom_displacement_no_alignment"
)
FLEXIBLE_MOVEMENT_FILENAME = "flexible_movement.json"
FLEXIBLE_MOVEMENT_MAX_BYTES = 64 * 1024 * 1024
MACROCYCLE_CONTRACT_SCHEMA_VERSION = 2
MACROCYCLE_ANALYSIS_VERSION = "2.0"
MACROCYCLE_MEEKO_API_PROFILE = "meeko_0_7_explicit_ring_break_topology_v2"
MACROCYCLE_HYDROGEN_POLICY = "rdkit_add_hs_preserve_source_indices_v1"
AD4ZN_GPF_REQUIRED_LINES = (
    "dielectric -0.1465",
    "nbp_r_eps 0.25 23.2135 12 6 NA TZ",
    "nbp_r_eps 2.1 3.8453 12 6 OA Zn",
    "nbp_r_eps 2.25 7.5914 12 6 SA Zn",
    "nbp_r_eps 1.0 0.0 12 6 HD Zn",
    "nbp_r_eps 2.0 0.0060 12 6 NA Zn",
    "nbp_r_eps 2.0 0.2966 12 6 N Zn",
    "parameter_file inputs/AD4Zn.dat",
    "receptor inputs/receptor_tz.pdbqt",
)


def _metadata_scoring_protocol(metadata: dict[str, Any]) -> str:
    return "ad4_maps" if str(metadata.get("scoring_protocol") or "") == "ad4_maps" else "vina"


def _metadata_protocol_id(metadata: dict[str, Any]) -> str:
    protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    protocol_id = str(
        metadata.get("protocol_id")
        or protocol.get("protocol_id")
        or "",
    ).strip().lower()
    if (
        _metadata_scoring_protocol(metadata) == "ad4_maps"
        and protocol_id in {AD4ZN_PROTOCOL_ID, HYDRATED_PROTOCOL_ID}
    ):
        return protocol_id
    if _metadata_scoring_protocol(metadata) == "ad4_maps":
        return "ad4_maps"
    return protocol_id or "rigid_single"


def _normalize_run_mode(value: Any) -> str:
    candidate = str(value or "dock").strip().lower()
    return candidate if candidate in VINA_RUN_MODES else "dock"


def _metadata_run_mode(metadata: dict[str, Any]) -> str:
    return _normalize_run_mode(metadata.get("run_mode"))


def _metadata_grid_source(metadata: dict[str, Any]) -> str:
    protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    return (
        "precomputed_maps"
        if _metadata_scoring_protocol(metadata) == "vina"
        and str(protocol.get("grid_source") or "").strip().lower()
        == "precomputed_maps"
        and str(protocol.get("protocol_id") or "").strip().lower()
        == "vina_maps"
        else "receptor"
    )


def _run_output_file(run_id: str, run_mode: str) -> str:
    normalized = _normalize_run_mode(run_mode)
    if normalized == "score_only":
        return ""
    filename = "optimized.pdbqt" if normalized == "local_only" else "out.pdbqt"
    return Path("runs", run_id, filename).as_posix()


def _run_pose_file(run_id: str, run_mode: str) -> str:
    normalized = _normalize_run_mode(run_mode)
    if normalized == "score_only":
        return Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
    return _run_output_file(run_id, normalized)


def _project_scores_file(metadata: dict[str, Any]) -> str:
    filename = (
        "simultaneous_multi_ligand_scores.csv"
        if _metadata_protocol_id(metadata) == MULTIPLE_LIGAND_PROTOCOL_ID
        else "hydrated_ad4_scores.csv"
        if _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
        else "ad4zn_scores.csv"
        if _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
        else "ad4_scores.csv"
        if _metadata_scoring_protocol(metadata) == "ad4_maps"
        else "scores.csv"
    )
    return Path("results", filename).as_posix()


def _project_report_file(metadata: dict[str, Any]) -> str:
    run_mode = _metadata_run_mode(metadata)
    if run_mode != "dock":
        protocol_prefix = (
            "ad4zn_"
            if _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
            else "ad4_"
            if _metadata_scoring_protocol(metadata) == "ad4_maps"
            else ""
        )
        return Path("reports", f"{protocol_prefix}{run_mode}_report.md").as_posix()
    filename = (
        "simultaneous_multi_ligand_report.md"
        if _metadata_protocol_id(metadata) == MULTIPLE_LIGAND_PROTOCOL_ID
        else "hydrated_ad4_docking_report.md"
        if _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
        else "ad4zn_docking_report.md"
        if _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
        else "ad4_docking_report.md"
        if _metadata_scoring_protocol(metadata) == "ad4_maps"
        else "docking_report.md"
    )
    return Path("reports", filename).as_posix()


def _run_report_filename(metadata: dict[str, Any]) -> str:
    if _metadata_run_mode(metadata) != "dock":
        return "evaluation_report.md"
    if _metadata_protocol_id(metadata) == MULTIPLE_LIGAND_PROTOCOL_ID:
        return "multi_ligand_report.md"
    if _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID:
        return "hydrated_docking_report.md"
    return RUN_REPORT_FILE


class ProjectSchemaError(ValueError):
    """Raised when a project document cannot be safely migrated."""

    def __init__(self, code: str, message: str, *, raw_error: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_error = raw_error


class ProjectFileSafetyError(RuntimeError):
    """Raised when a project file path cannot be accessed without following links."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _FilePublication:
    """Rollback evidence for one atomically published project file."""

    path: Path
    published_bytes: bytes
    previous_existed: bool
    previous_bytes: bytes
    parent_identity: tuple[int, int]


@dataclass
class ProjectFileRef:
    source: str = ""
    source_id: str = ""
    query_type: str = ""
    downloaded_at: str = ""
    raw_file: str = ""
    file: str = ""


@dataclass
class BoxSettings:
    center_x: float = 0
    center_y: float = 0
    center_z: float = 0
    size_x: float = 20
    size_y: float = 20
    size_z: float = 20


@dataclass
class VinaSettings:
    scoring: str = "vina"
    exhaustiveness: int = 8
    max_evals: int = 0
    num_modes: int = 9
    min_rmsd: float = 1
    energy_range: float = 4
    spacing: float = 0.375
    unbound_energy: float | None = None
    no_refine: bool = False
    force_even_voxels: bool = False
    verbosity: int = 1
    cpu: int = 0
    seed: int | None = None


@dataclass
class ConfigSettings:
    vina_config_file: str = ""
    generated_at: str = ""


@dataclass
class DockStartProject:
    project_name: str
    created_at: str
    updated_at: str
    project_dir: str
    schema_version: int = CURRENT_PROJECT_SCHEMA_VERSION
    revision: int = 0
    receptor: ProjectFileRef = field(default_factory=ProjectFileRef)
    ligand: ProjectFileRef = field(default_factory=ProjectFileRef)
    box: BoxSettings = field(default_factory=BoxSettings)
    vina: VinaSettings = field(default_factory=VinaSettings)
    config: ConfigSettings = field(default_factory=ConfigSettings)
    preparation: PreparationState = field(default_factory=PreparationState)
    latest_preparation: dict[str, str] = field(default_factory=lambda: {"receptor": "", "ligand": ""})
    runs: list[dict[str, Any]] = field(default_factory=list)
    preserved_data: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.preserved_data)
        known = {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "project_name": self.project_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_dir": self.project_dir,
            "receptor": asdict(self.receptor),
            "ligand": asdict(self.ligand),
            "box": asdict(self.box),
            "vina": asdict(self.vina),
            "config": asdict(self.config),
            "preparation": self.preparation.to_dict(),
            "latest_preparation": copy.deepcopy(self.latest_preparation),
            "runs": copy.deepcopy(self.runs),
        }
        return _deep_overlay(payload, known)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error(code: str, message: str, raw_error: str = "", suggestion: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "project": None,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _success(project: DockStartProject, message: str = "", warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "message": message,
        "warnings": warnings or [],
    }


def _project_json_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def _project_lock_path(project_dir: str | Path) -> Path:
    project_root = Path(project_dir).expanduser().resolve()
    return project_root / ".project.lock"


def _acquire_windows_byte_lock(handle: Any) -> None:
    """Block until the one-byte Windows lock is available.

    ``msvcrt.LK_LOCK`` only retries a contended lock a finite number of times
    before raising ``EDEADLK``.  Long scientific jobs can legitimately hold a
    project-local lock longer than that retry window, so use the non-blocking
    primitive in an explicit loop to match POSIX ``flock(LOCK_EX)`` semantics.
    Unexpected filesystem or descriptor errors are never swallowed.
    """

    import msvcrt

    retryable = {
        errno.EACCES,
        errno.EAGAIN,
        errno.EDEADLK,
    }
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in retryable:
                raise
            time.sleep(0.05)


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Hold an advisory one-byte lock across processes.

    The lock file itself must be a regular path, not a symlink/reparse target.
    This keeps an untrusted project from redirecting DockStart's coordination
    writes outside the directory that the user opened.
    """

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink() or lock_path.resolve(strict=False) != lock_path.absolute():
        raise RuntimeError(f"锁文件路径不安全：{lock_path}")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            _acquire_windows_byte_lock(handle)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _project_lock(project_dir: str | Path) -> Iterator[None]:
    with _exclusive_file_lock(_project_lock_path(project_dir)):
        yield


def _preparation_target_lock_path(project_dir: str | Path, target: str) -> Path:
    """Return the project-local cross-process lock for one preparation target."""

    normalized = str(target or "").strip().lower()
    if normalized not in {"receptor", "ligand"}:
        raise RuntimeError(f"preparation target 无效：{target}")
    project_root = Path(project_dir).expanduser().resolve()
    return project_root / f".preparation-{normalized}.lock"


@contextmanager
def _preparation_target_lock(project_dir: str | Path, target: str) -> Iterator[None]:
    """Serialize claim/finalize/recovery transactions for one target."""

    with _exclusive_file_lock(_preparation_target_lock_path(project_dir, target)):
        yield


def _is_reparse_or_symlink(path: Path) -> bool:
    """Inspect one lexical component without following it."""

    try:
        details = os.lstat(path)
    except OSError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _directory_identity(path: Path) -> tuple[int, int]:
    """Return a no-follow directory identity suitable for a short transaction."""

    details = os.lstat(path)
    if _is_reparse_or_symlink(path) or not stat.S_ISDIR(details.st_mode):
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_PATH_UNSAFE",
            f"项目存储目录不是普通目录，或属于符号链接、junction/reparse point：{path}",
        )
    return int(details.st_dev), int(details.st_ino)


def _safe_project_storage_file(
    project_path: Path,
    relative_path: str | Path,
    storage_root: str,
) -> tuple[Path, tuple[int, int]]:
    """Resolve one fixed project file without following any lexical component.

    The caller receives the identity of the immediate parent directory and must
    keep checking it around publication/deletion.  This prevents a project
    ``raw``/``prepared`` directory from being silently redirected through a
    symlink, Windows junction, or another reparse point.
    """

    project_root = project_path.expanduser().resolve(strict=True)
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != storage_root
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_PATH_OUTSIDE_ROOT",
            f"项目文件必须位于 {storage_root}/ 目录：{relative_path}",
        )

    candidate = Path(os.path.abspath(project_root / relative))
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_PATH_OUTSIDE_ROOT",
            f"项目文件路径越出项目目录：{relative_path}",
        ) from exc

    current = project_root
    for index, part in enumerate(relative.parts):
        current = current / part
        if not os.path.lexists(current):
            if index < len(relative.parts) - 1:
                raise ProjectFileSafetyError(
                    "PROJECT_STORAGE_PATH_MISSING",
                    f"项目存储目录不存在：{current}",
                )
            continue
        details = os.lstat(current)
        if _is_reparse_or_symlink(current):
            raise ProjectFileSafetyError(
                "PROJECT_STORAGE_PATH_UNSAFE",
                f"项目路径包含符号链接、junction 或 reparse point：{current}",
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(details.st_mode):
            raise ProjectFileSafetyError(
                "PROJECT_STORAGE_PATH_UNSAFE",
                f"项目路径中的目录组件不是普通目录：{current}",
            )

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_PATH_OUTSIDE_ROOT",
            f"项目文件重解析到项目目录之外：{relative_path}",
        ) from exc
    if resolved != candidate.absolute():
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_PATH_UNSAFE",
            f"项目文件路径被重解析到其他位置：{relative_path}",
        )

    parent_identity = _directory_identity(candidate.parent)
    return candidate, parent_identity


def _assert_directory_identity(path: Path, expected: tuple[int, int]) -> None:
    observed = _directory_identity(path)
    if observed != expected:
        raise ProjectFileSafetyError(
            "PROJECT_STORAGE_DIRECTORY_CHANGED",
            f"项目存储目录在文件操作期间发生替换：{path}",
        )


def _existing_file_snapshot_for_publication(path: Path) -> tuple[bool, bytes]:
    """Read an existing target while rejecting links and shared hardlinks."""

    if not os.path.lexists(path):
        return False, b""
    details = os.lstat(path)
    if _is_reparse_or_symlink(path) or not stat.S_ISREG(details.st_mode):
        raise ProjectFileSafetyError(
            "PROJECT_FILE_TARGET_UNSAFE",
            f"目标路径不是普通文件，或属于符号链接、junction/reparse point：{path}",
        )
    if int(getattr(details, "st_nlink", 1) or 1) > 1:
        raise ProjectFileSafetyError(
            "PROJECT_FILE_TARGET_HARDLINKED",
            f"目标文件存在多个硬链接，DockStart 不会替换或删除它：{path}",
        )
    snapshot = _read_file_snapshot_no_follow(path)
    after = os.lstat(path)
    if (
        _is_reparse_or_symlink(path)
        or not stat.S_ISREG(after.st_mode)
        or int(getattr(after, "st_nlink", 1) or 1) > 1
        or (details.st_dev, details.st_ino) != (after.st_dev, after.st_ino)
    ):
        raise ProjectFileSafetyError(
            "PROJECT_FILE_TARGET_CHANGED",
            f"目标文件在安全检查期间发生变化：{path}",
        )
    return True, snapshot


def _read_external_file_snapshot_no_follow(path: Path) -> bytes:
    """Read a user-selected file while rejecting linked path components."""

    lexical = Path(os.path.abspath(path.expanduser()))
    anchor = Path(lexical.anchor)
    current = anchor
    parent_identities: list[tuple[Path, tuple[int, int]]] = []
    relative_parts = lexical.parts[1:] if lexical.anchor else lexical.parts
    for index, part in enumerate(relative_parts):
        current = current / part
        details = os.lstat(current)
        if _is_reparse_or_symlink(current):
            raise OSError(f"源路径包含符号链接、junction 或 reparse point：{current}")
        if index < len(relative_parts) - 1:
            if not stat.S_ISDIR(details.st_mode):
                raise OSError(f"源路径目录组件不是普通目录：{current}")
            parent_identities.append(
                (current, (int(details.st_dev), int(details.st_ino)))
            )

    snapshot = _read_file_snapshot_no_follow(lexical)
    for parent, expected in parent_identities:
        details = os.lstat(parent)
        if (
            _is_reparse_or_symlink(parent)
            or not stat.S_ISDIR(details.st_mode)
            or (int(details.st_dev), int(details.st_ino)) != expected
        ):
            raise OSError(f"源路径目录在读取过程中发生替换：{parent}")
    return snapshot


def _atomic_create_bytes_no_replace(
    path: Path,
    payload: bytes,
    parent_identity: tuple[int, int],
) -> None:
    """Publish sibling-temporary bytes only if the destination is still absent."""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        _assert_directory_identity(path.parent, parent_identity)
        # Linking a fully flushed sibling temporary file is an atomic
        # create-if-absent operation on the same volume.  Unlike an existence
        # check followed by os.replace, it cannot overwrite a concurrently
        # created target.
        os.link(temporary_path, path, follow_symlinks=False)
        temporary_path.unlink()
        temporary_path = None
        _assert_directory_identity(path.parent, parent_identity)
    finally:
        if temporary_path is not None and os.path.lexists(temporary_path):
            temporary_path.unlink(missing_ok=True)


def _publish_project_file_bytes(
    path: Path,
    payload: bytes,
    *,
    overwrite: bool,
    parent_identity: tuple[int, int],
) -> _FilePublication:
    """Atomically publish bytes and retain enough evidence for safe rollback."""

    _assert_directory_identity(path.parent, parent_identity)
    previous_existed, previous_bytes = _existing_file_snapshot_for_publication(path)
    if previous_existed and not overwrite:
        raise FileExistsError(str(path))

    if overwrite:
        _atomic_write_bytes(path, payload)
    else:
        _atomic_create_bytes_no_replace(path, payload, parent_identity)

    _assert_directory_identity(path.parent, parent_identity)
    published = _read_file_snapshot_no_follow(path)
    details = os.lstat(path)
    if published != payload:
        raise ProjectFileSafetyError(
            "PROJECT_FILE_PUBLICATION_MISMATCH",
            f"项目文件发布后的字节与输入快照不一致：{path}",
        )
    if int(getattr(details, "st_nlink", 1) or 1) > 1:
        raise ProjectFileSafetyError(
            "PROJECT_FILE_TARGET_HARDLINKED",
            f"项目文件发布后出现多个硬链接，已拒绝继续提交：{path}",
        )
    return _FilePublication(
        path=path,
        published_bytes=payload,
        previous_existed=previous_existed,
        previous_bytes=previous_bytes,
        parent_identity=parent_identity,
    )


def _rollback_file_publication(publication: _FilePublication) -> str:
    """Restore a file only while it still contains this transaction's bytes."""

    try:
        _assert_directory_identity(publication.path.parent, publication.parent_identity)
        exists, current_bytes = _existing_file_snapshot_for_publication(publication.path)
        if not exists or current_bytes != publication.published_bytes:
            raise ProjectFileSafetyError(
                "PROJECT_FILE_ROLLBACK_CONFLICT",
                f"目标文件已被其他操作修改，DockStart 不会用旧内容覆盖它：{publication.path}",
            )
        if publication.previous_existed:
            _atomic_write_bytes(publication.path, publication.previous_bytes)
            restored = _read_file_snapshot_no_follow(publication.path)
            if restored != publication.previous_bytes:
                raise OSError("恢复后的文件内容与事务前快照不一致")
        else:
            publication.path.unlink()
            if os.path.lexists(publication.path):
                raise OSError("新建文件回滚后仍然存在")
        _assert_directory_identity(publication.path.parent, publication.parent_identity)
        return ""
    except Exception as exc:  # noqa: BLE001 - append rollback evidence to caller error.
        return str(exc)


def _deep_overlay(base: Any, updates: Any) -> Any:
    """Overlay known fields while retaining unknown nested project fields."""

    if isinstance(base, dict) and isinstance(updates, dict):
        merged = copy.deepcopy(base)
        for key, value in updates.items():
            merged[key] = _deep_overlay(merged.get(key), value)
        return merged
    return copy.deepcopy(updates)


def _coerce_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ProjectSchemaError(
            "PROJECT_SCHEMA_INVALID",
            f"project.json 的 {field_name} 必须是非负整数。",
            raw_error=repr(value),
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectSchemaError(
            "PROJECT_SCHEMA_INVALID",
            f"project.json 的 {field_name} 必须是非负整数。",
            raw_error=str(exc),
        ) from exc
    if parsed < 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise ProjectSchemaError(
            "PROJECT_SCHEMA_INVALID",
            f"project.json 的 {field_name} 必须是非负整数。",
            raw_error=repr(value),
        )
    return parsed


def migrate_project_data(data: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    """Migrate a project document to the current schema without data loss.

    Returns ``(migrated, changed, source_version)``.  Unknown keys are retained
    verbatim; known fields are normalized later by :func:`_project_from_dict`.
    """

    migrated = copy.deepcopy(data)
    raw_version = migrated.get("schema_version")
    source_version = 0 if raw_version in (None, "") else _coerce_non_negative_int(
        raw_version,
        field_name="schema_version",
    )
    if source_version > CURRENT_PROJECT_SCHEMA_VERSION:
        raise ProjectSchemaError(
            "PROJECT_SCHEMA_VERSION_UNSUPPORTED",
            "project.json 来自更高版本的 DockStart，当前版本不会改写该项目。",
            raw_error=(
                f"project schema_version={source_version}; "
                f"supported={CURRENT_PROJECT_SCHEMA_VERSION}"
            ),
        )

    changed = source_version != CURRENT_PROJECT_SCHEMA_VERSION
    if source_version == 0:
        migrated.setdefault("revision", 0)
        migrated.setdefault("preparation", {})
        migrated.setdefault("latest_preparation", {"receptor": "", "ligand": ""})
        migrated.setdefault("config", {})
        migrated.setdefault("runs", [])
        migrated["schema_version"] = 1

    revision = _coerce_non_negative_int(migrated.get("revision", 0), field_name="revision")
    if migrated.get("revision") != revision:
        migrated["revision"] = revision
        changed = True
    return migrated, changed, source_version


def _migration_backup_path(project_root: Path, source_version: int) -> Path:
    base = project_root / f"project.json.schema-v{source_version}.bak"
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = base.with_name(f"{base.name}.{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法为 project.json 迁移备份分配安全文件名。")


def _read_and_migrate_project_unlocked(
    project_root: Path,
    *,
    persist_migration: bool,
) -> tuple[dict[str, Any], bool, Path | None]:
    project_json = _project_json_path(project_root)
    original_text = project_json.read_text(encoding="utf-8")
    data = json.loads(original_text)
    if not isinstance(data, dict):
        raise ProjectSchemaError("PROJECT_JSON_INVALID", "project.json 格式不是 JSON 对象。")
    migrated, changed, source_version = migrate_project_data(data)
    # Migration must not persist a partially understood document.  Parse every
    # known field first so malformed numeric/settings data leaves the original
    # project.json byte-for-byte untouched.
    _project_from_dict(migrated, project_root)
    backup_path: Path | None = None
    if changed and persist_migration:
        backup_path = _migration_backup_path(project_root, source_version)
        _atomic_write_text(backup_path, original_text)
        _atomic_write_text(
            project_json,
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
        )
    return migrated, changed, backup_path


def _write_project_json_unlocked(project_root: Path, project: DockStartProject) -> None:
    project_json = _project_json_path(project_root)
    if project_json.is_symlink():
        raise RuntimeError("project.json 不能是符号链接。")
    _atomic_write_text(
        project_json,
        json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )


def _sanitize_project_name(project_name: str) -> str:
    return project_name.strip()


def _validate_project_name(project_name: str) -> str | None:
    if not project_name:
        return "项目名称不能为空。"
    if project_name in {".", ".."}:
        return "项目名称不能是 . 或 ..。"
    if not PROJECT_NAME_PATTERN.match(project_name):
        return "项目名称不能包含路径分隔符或 Windows 文件名保留字符。"
    return None


def _value_or_default(data: dict[str, Any], key: str, default: Any) -> Any:
    value = data.get(key, default)
    return default if value is None or value == "" else value


def _bool_value_or_default(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if value is None or value == "":
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是 JSON 布尔值。")
    return value


def _optional_finite_float_value(
    data: dict[str, Any],
    key: str,
) -> float | None:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} 必须是有限数字或 null。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是有限数字或 null。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} 不能是 NaN 或 Infinity。")
    return number


def _project_from_dict(data: dict[str, Any], fallback_dir: Path) -> DockStartProject:
    data, _, _ = migrate_project_data(data)
    receptor = data.get("receptor") if isinstance(data.get("receptor"), dict) else {}
    ligand = data.get("ligand") if isinstance(data.get("ligand"), dict) else {}
    box = data.get("box") if isinstance(data.get("box"), dict) else {}
    vina = data.get("vina") if isinstance(data.get("vina"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    preparation = data.get("preparation") if isinstance(data.get("preparation"), dict) else {}
    latest_preparation = data.get("latest_preparation") if isinstance(data.get("latest_preparation"), dict) else {}
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    # The directory explicitly opened by the caller is authoritative.  The
    # stored value is historical display data only and may point to another
    # machine, drive letter, or the pre-copy location of this project.
    project_dir = str(fallback_dir.expanduser().resolve())

    return DockStartProject(
        project_name=str(data.get("project_name", fallback_dir.name) or fallback_dir.name),
        created_at=str(data.get("created_at", "") or _now_iso()),
        updated_at=str(data.get("updated_at", "") or _now_iso()),
        project_dir=project_dir,
        schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
        revision=_coerce_non_negative_int(data.get("revision", 0), field_name="revision"),
        receptor=ProjectFileRef(
            source=str(receptor.get("source", "") or ""),
            source_id=str(receptor.get("source_id", "") or ""),
            query_type=str(receptor.get("query_type", "") or ""),
            downloaded_at=str(receptor.get("downloaded_at", "") or ""),
            raw_file=str(receptor.get("raw_file", "") or ""),
            file=str(receptor.get("file", "") or ""),
        ),
        ligand=ProjectFileRef(
            source=str(ligand.get("source", "") or ""),
            source_id=str(ligand.get("source_id", "") or ""),
            query_type=str(ligand.get("query_type", "") or ""),
            downloaded_at=str(ligand.get("downloaded_at", "") or ""),
            raw_file=str(ligand.get("raw_file", "") or ""),
            file=str(ligand.get("file", "") or ""),
        ),
        box=BoxSettings(
            center_x=float(_value_or_default(box, "center_x", 0)),
            center_y=float(_value_or_default(box, "center_y", 0)),
            center_z=float(_value_or_default(box, "center_z", 0)),
            size_x=float(_value_or_default(box, "size_x", 20)),
            size_y=float(_value_or_default(box, "size_y", 20)),
            size_z=float(_value_or_default(box, "size_z", 20)),
        ),
        vina=VinaSettings(
            scoring=str(_value_or_default(vina, "scoring", "vina") or "vina").strip().lower(),
            exhaustiveness=int(_value_or_default(vina, "exhaustiveness", 8)),
            max_evals=int(_value_or_default(vina, "max_evals", 0)),
            num_modes=int(_value_or_default(vina, "num_modes", 9)),
            min_rmsd=float(_value_or_default(vina, "min_rmsd", 1)),
            energy_range=float(_value_or_default(vina, "energy_range", 4)),
            spacing=float(_value_or_default(vina, "spacing", 0.375)),
            unbound_energy=_optional_finite_float_value(vina, "unbound_energy"),
            no_refine=_bool_value_or_default(vina, "no_refine", False),
            force_even_voxels=_bool_value_or_default(vina, "force_even_voxels", False),
            verbosity=int(_value_or_default(vina, "verbosity", 1)),
            cpu=int(_value_or_default(vina, "cpu", 0)),
            seed=None if vina.get("seed", None) in ("", None) else int(vina.get("seed")),
        ),
        config=ConfigSettings(
            vina_config_file=str(config.get("vina_config_file", "") or ""),
            generated_at=str(config.get("generated_at", "") or ""),
        ),
        preparation=preparation_state_from_dict(preparation),
        latest_preparation={
            "receptor": str(latest_preparation.get("receptor", "") or ""),
            "ligand": str(latest_preparation.get("ligand", "") or ""),
        },
        runs=runs,
        preserved_data=copy.deepcopy(data),
    )


def ensure_project_structure(project_dir: str | Path) -> dict[str, Any]:
    try:
        path = Path(project_dir).expanduser()
        for directory in PROJECT_DIRS:
            (path / directory).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "project_dir": str(path), "error": None}
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_STRUCTURE_ERROR",
            "创建项目目录结构时发生错误。",
            str(exc),
            "请确认项目保存目录存在且有写入权限。",
        )


def save_project(project: DockStartProject) -> dict[str, Any]:
    try:
        project_dir = Path(project.project_dir).expanduser().resolve()
        structure = ensure_project_structure(project_dir)
        if not structure.get("ok"):
            return structure
        with _project_lock(project_dir):
            project_json = _project_json_path(project_dir)
            if project_json.exists():
                if project_json.is_symlink() or project_json.resolve(strict=True) != project_json.absolute():
                    return _error(
                        "PROJECT_JSON_PATH_UNSAFE",
                        "project.json 不能是符号链接或重解析到其他位置。",
                        raw_error=str(project_json),
                    )
                current_data, _, _ = _read_and_migrate_project_unlocked(
                    project_dir,
                    persist_migration=True,
                )
                current_revision = _coerce_non_negative_int(
                    current_data.get("revision", 0),
                    field_name="revision",
                )
                if current_revision != project.revision:
                    return _error(
                        "PROJECT_SAVE_CONFLICT",
                        "project.json 已被其他操作更新，本次保存已拒绝以避免覆盖新数据。",
                        raw_error=f"expected revision={project.revision}; current revision={current_revision}",
                        suggestion="请重新读取项目后再提交本次修改。",
                    )
            else:
                current_revision = 0
                if project.revision not in {0, current_revision}:
                    return _error(
                        "PROJECT_SAVE_CONFLICT",
                        "project.json 尚不存在，但内存项目 revision 不是初始值，已拒绝写入。",
                        raw_error=f"expected revision=0; project revision={project.revision}",
                    )
            project.updated_at = _now_iso()
            project.schema_version = CURRENT_PROJECT_SCHEMA_VERSION
            project.revision = current_revision + 1
            _write_project_json_unlocked(project_dir, project)
            project.preserved_data = copy.deepcopy(project.to_dict())
        return _success(project, "项目已保存。")
    except ProjectSchemaError as exc:
        return _error(
            exc.code,
            exc.message,
            exc.raw_error,
            "请使用创建该项目的 DockStart 版本打开，或先复制项目后再迁移。",
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_SAVE_ERROR",
            "保存 project.json 时发生错误。",
            str(exc),
            "请确认项目目录可写。",
        )


def _new_project_vina_settings() -> VinaSettings:
    """Seed a brand new project from the user's global docking defaults.

    The global defaults are strictly optional.  A missing or damaged settings
    file, or an unexpected key, must never block project creation, so every
    failure falls back to the documented dataclass defaults.  Once a project
    exists it stores its own copy, which is why project-level values always win
    over these global defaults.
    """

    defaults = VinaSettings()
    try:
        overrides = docking_default_overrides()
    except Exception:  # noqa: BLE001 - settings must never break project creation.
        return defaults
    if not isinstance(overrides, dict):
        return defaults

    fallbacks: dict[str, Any] = {
        "scoring": defaults.scoring,
        "exhaustiveness": defaults.exhaustiveness,
        "num_modes": defaults.num_modes,
        "energy_range": defaults.energy_range,
        "cpu": defaults.cpu,
        "seed": defaults.seed,
    }
    try:
        return VinaSettings(
            **{key: overrides.get(key, fallback) for key, fallback in fallbacks.items()}
        )
    except Exception:  # noqa: BLE001 - never fail project creation on settings.
        return defaults


def create_project(project_name: str, base_dir: str) -> dict[str, Any]:
    safe_name = _sanitize_project_name(project_name)
    name_error = _validate_project_name(safe_name)
    if name_error:
        return _error("INVALID_PROJECT_NAME", name_error, suggestion="请使用普通文件夹名称作为项目名。")

    if not base_dir.strip():
        return _error("BASE_DIR_REQUIRED", "项目保存目录不能为空。", suggestion="请输入一个可写入的父目录。")

    base_path = Path(base_dir).expanduser()
    project_dir = base_path / safe_name

    if project_dir.exists():
        return _error(
            "PROJECT_DIR_EXISTS",
            "项目目录已存在，DockStart 不会覆盖已有项目。",
            suggestion="请更换项目名称，或选择另一个保存目录。",
        )

    try:
        project_dir.mkdir(parents=True, exist_ok=False)
        structure = ensure_project_structure(project_dir)
        if not structure.get("ok"):
            return structure

        created_at = _now_iso()
        project = DockStartProject(
            project_name=safe_name,
            created_at=created_at,
            updated_at=created_at,
            project_dir=str(project_dir),
            vina=_new_project_vina_settings(),
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "项目创建成功。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CREATE_ERROR",
            "创建项目时发生错误。",
            str(exc),
            "请确认保存目录存在且有写入权限。",
        )


def load_project(
    project_dir: str,
    *,
    persist_migration: bool = True,
) -> dict[str, Any]:
    """Load a project, optionally migrating only the in-memory representation.

    Normal application opens keep the historical behaviour and persist a safe
    schema migration with a backup.  Read-only callers such as the headless
    structure review pass ``persist_migration=False``; that path deliberately
    avoids the project lock as well, because acquiring it can create a lock
    file.  Atomic project writes mean a lock-free reader still observes either
    the complete old document or the complete new document.
    """

    try:
        path = Path(project_dir).expanduser().resolve()
        project_json = _project_json_path(path)
        if not project_json.exists():
            return _error(
                "PROJECT_JSON_NOT_FOUND",
                "没有找到 project.json，无法读取 DockStart 项目。",
                suggestion="请确认选择的是 DockStart 项目目录。",
            )

        if project_json.is_symlink() or project_json.resolve(strict=True) != project_json.absolute():
            return _error(
                "PROJECT_JSON_PATH_UNSAFE",
                "project.json 不能是符号链接或重解析到其他位置。",
                raw_error=str(project_json),
                suggestion="请恢复项目根目录中的普通 project.json 文件。",
            )
        if persist_migration:
            with _project_lock(path):
                data, migrated, backup_path = _read_and_migrate_project_unlocked(
                    path,
                    persist_migration=True,
                )
        else:
            data, migrated, backup_path = _read_and_migrate_project_unlocked(
                path,
                persist_migration=False,
            )
        project = _project_from_dict(data, path)
        warnings = []
        message = "项目读取成功。"
        if migrated:
            if persist_migration:
                message = "项目已迁移到当前数据格式并完成读取。"
                warnings.append(
                    f"迁移前 project.json 已备份到 {backup_path.name if backup_path else '备份文件'}。",
                )
            else:
                message = "项目已按当前数据格式只读解析；project.json 未被改写。"
                warnings.append("旧项目字段仅在内存中迁移，本次读取不会创建迁移备份或写回项目。")
        return _success(project, message, warnings)
    except ProjectSchemaError as exc:
        return _error(
            exc.code,
            exc.message,
            exc.raw_error,
            "请使用兼容版本打开该项目；DockStart 未改写原文件。",
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_LOAD_ERROR",
            "读取项目时发生错误。",
            str(exc),
            "请检查 project.json 是否完整。",
        )


def validate_pdbqt_file(path: str) -> dict[str, Any]:
    file_path = Path(path).expanduser()

    if not file_path.exists():
        return _error(
            "PDBQT_FILE_NOT_FOUND",
            "没有找到 PDBQT 文件，请检查输入路径。",
            suggestion="请确认文件路径正确，或重新选择 .pdbqt 文件。",
        )
    if not file_path.is_file():
        return _error("PDBQT_PATH_NOT_FILE", "PDBQT 路径不是一个文件。")
    if file_path.suffix.lower() != ".pdbqt":
        return _error(
            "PDBQT_EXTENSION_INVALID",
            "文件扩展名不是 .pdbqt。",
            suggestion="第一版只支持已经准备好的 receptor.pdbqt 和 ligand.pdbqt。",
        )
    if file_path.stat().st_size == 0:
        return _error(
            "PDBQT_FILE_EMPTY",
            "PDBQT 文件为空，无法导入。",
            suggestion="请确认该文件是 AutoDock Vina 可用的 PDBQT 文件。",
        )

    return {"ok": True, "path": str(file_path), "error": None}


def _normalize_pdbqt_source_label(source_label: str, fallback: str) -> str:
    """Normalize a human-facing PDBQT identity without path semantics."""

    def normalize(value: str) -> str:
        # Preserve punctuation and Unicode verbatim. C0 whitespace becomes a
        # regular separator; other C0 controls (including NUL) are discarded.
        without_controls = "".join(
            " " if character.isspace() else character
            for character in str(value or "")
            if ord(character) >= 0x20 or character.isspace()
        )
        compact = " ".join(without_controls.split())
        return compact[:PDBQT_SOURCE_LABEL_MAX_CHARS].rstrip()

    return normalize(source_label) or normalize(fallback)


def _import_pdbqt(
    project_dir: str,
    source_path: str,
    role: str,
    source_label: str = "",
) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("PDBQT_ROLE_INVALID", "PDBQT 导入类型无效。")

    validation = validate_pdbqt_file(source_path)
    if not validation.get("ok"):
        return validation

    source = Path(str(validation["path"])).expanduser()
    try:
        source_payload = _read_external_file_snapshot_no_follow(source)
    except OSError as exc:
        return _error(
            "PDBQT_SOURCE_UNSAFE",
            "PDBQT 源文件不是稳定的普通文件，已拒绝导入。",
            raw_error=str(exc),
            suggestion="请选择不经过符号链接、junction 或 reparse point 的普通 PDBQT 文件。",
        )
    if not source_payload:
        return _error(
            "PDBQT_FILE_EMPTY",
            "PDBQT 文件为空，无法导入。",
            raw_error=str(source),
            suggestion="请确认该文件是 AutoDock Vina 可用的非空 PDBQT 文件。",
        )

    try:
        # Use the same target lock as preparation claim/finalize. If an import
        # races final publication, either preparation observes the imported
        # output and rejects its candidate, or the later user import wins after
        # publication. The older task can never overwrite the later import.
        with _preparation_target_lock(project_dir, role):
            loaded = load_project(project_dir)
            if not loaded.get("ok"):
                return loaded
            project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
            target_relative = f"prepared/{role}.pdbqt"
            project_path = Path(project.project_dir).expanduser().resolve(strict=True)
            target_path, parent_identity = _safe_project_storage_file(
                project_path,
                target_relative,
                "prepared",
            )
            publication = _publish_project_file_bytes(
                target_path,
                source_payload,
                overwrite=True,
                parent_identity=parent_identity,
            )
            rollback_required = True
            try:
                file_ref = getattr(project, role)
                human_label = _normalize_pdbqt_source_label(source_label, source.name)
                file_ref.source = "local"
                file_ref.source_id = human_label
                file_ref.query_type = "local_file"
                file_ref.downloaded_at = _now_iso()
                file_ref.raw_file = ""
                file_ref.file = target_relative

                # A manually imported PDBQT is a new active structure. Keep old
                # preparation records on disk for audit, but detach their current
                # project pointers so their method/evidence cannot be attributed
                # to the newly imported bytes. A running preparation keeps its
                # ownership until finalization so it can record the imported bytes
                # as an output conflict and preserve the full verification audit.
                active_preparation = getattr(project.preparation, role)
                has_running_preparation = (
                    active_preparation.status == "running"
                    and bool(active_preparation.prep_id)
                    and project.latest_preparation.get(role) == active_preparation.prep_id
                )
                if not has_running_preparation:
                    setattr(project.preparation, role, default_preparation_result(role))
                    project.latest_preparation[role] = ""

                saved = save_project(project)
                if not saved.get("ok"):
                    rollback_error = _rollback_file_publication(publication)
                    rollback_required = False
                    if rollback_error:
                        failed = copy.deepcopy(saved)
                        error = failed.get("error") if isinstance(failed.get("error"), dict) else {}
                        raw_error = str(error.get("raw_error") or "")
                        error["raw_error"] = (
                            f"{raw_error}; PDBQT rollback failed: {rollback_error}"
                            if raw_error
                            else f"PDBQT rollback failed: {rollback_error}"
                        )
                        error["suggestion"] = (
                            "project.json 提交失败且 prepared 文件无法自动恢复；"
                            "请停止运行并人工核对 project.json 与 prepared/ 内容。"
                        )
                        failed["error"] = error
                        return failed
                    return saved

                rollback_required = False
                label = "受体" if role == "receptor" else "配体"
                return _success(project, f"{label} PDBQT 已导入。")
            finally:
                if rollback_required:
                    rollback_error = _rollback_file_publication(publication)
                    if rollback_error:
                        raise RuntimeError(
                            f"PDBQT 导入异常且 prepared 文件回滚失败：{rollback_error}"
                        )
    except ProjectFileSafetyError as exc:
        return _error(
            exc.code,
            "prepared PDBQT 目标路径不安全，已拒绝导入。",
            str(exc),
            "请恢复项目内普通的 prepared/ 目录和文件，移除符号链接、junction 或硬链接后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PDBQT_IMPORT_ERROR",
            "导入 PDBQT 文件时发生错误。",
            str(exc),
            "请确认项目目录可写，源文件未被其他程序锁定。",
        )


def import_receptor_pdbqt(
    project_dir: str,
    source_path: str,
    source_label: str = "",
) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "receptor", source_label)


def import_ligand_pdbqt(
    project_dir: str,
    source_path: str,
    source_label: str = "",
) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "ligand", source_label)


def _parse_box_number(box: dict[str, Any], key: str) -> tuple[float | None, dict[str, Any] | None]:
    value = box.get(key)
    if value is None:
        return None, _error(
            "BOX_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的对接箱体参数。",
        )
    if isinstance(value, str) and not value.strip():
        return None, _error(
            "BOX_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的对接箱体参数。",
        )
    if isinstance(value, bool):
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 必须是数字。",
            suggestion="请输入普通数字，例如 0、-12.5 或 20。",
        )

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 必须是数字。",
            str(exc),
            "请输入普通数字，例如 0、-12.5 或 20。",
        )

    if not math.isfinite(number):
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            suggestion="请输入有限数字。",
        )
    return number, None


def validate_box_params(box: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(box, dict):
        return _error(
            "BOX_PARAMS_INVALID",
            "Box 参数格式无效。",
            suggestion="请提交包含 center_x/y/z 和 size_x/y/z 的对象。",
        )

    parsed: dict[str, float] = {}
    for key in ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z"):
        value, error = _parse_box_number(box, key)
        if error:
            return error
        parsed[key] = value if value is not None else 0

    for key in ("size_x", "size_y", "size_z"):
        if parsed[key] <= 0:
            return _error(
                "BOX_SIZE_NOT_POSITIVE",
                f"{key} 必须大于 0。",
                suggestion="对接箱体尺寸必须是正数，单位为 Å。",
            )

    warnings: list[str] = []
    if any(parsed[key] > 60 for key in ("size_x", "size_y", "size_z")):
        warnings.append("对接箱体尺寸较大，可能导致搜索变慢或结果不稳定，请确认是否覆盖了合理结合区域。")

    return {
        "ok": True,
        "box": parsed,
        "warnings": warnings,
        "error": None,
    }


def get_box_params(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "box": project.box.to_dict() if hasattr(project.box, "to_dict") else asdict(project.box),
        "warnings": [],
        "message": "Box 参数读取成功。",
    }


def update_box_params(project_dir: str, box: dict[str, Any]) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    validation = validate_box_params(box)
    if not validation.get("ok"):
        return validation

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        parsed_box = validation["box"]
        project.box = BoxSettings(
            center_x=parsed_box["center_x"],
            center_y=parsed_box["center_y"],
            center_z=parsed_box["center_z"],
            size_x=parsed_box["size_x"],
            size_y=parsed_box["size_y"],
            size_z=parsed_box["size_z"],
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "Box 参数已保存。", validation.get("warnings", []))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "BOX_UPDATE_ERROR",
            "保存 Box 参数时发生错误。",
            str(exc),
            "请确认 project.json 可写。",
        )


def _parse_vina_int(
    vina: dict[str, Any],
    key: str,
    *,
    allow_zero: bool = False,
) -> tuple[int | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, _error(
            "VINA_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的 Vina 参数；seed 可以留空。",
        )
    if isinstance(value, bool):
        return None, _error(
            "VINA_PARAM_INTEGER_REQUIRED",
            f"{key} 必须是整数。",
            suggestion="请输入整数，例如 8、9 或 0。",
        )

    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None, _error(
                "VINA_PARAM_INVALID",
                f"{key} 不能是 NaN 或 Infinity。",
                suggestion="请输入有限数字。",
            )
        if not value.is_integer():
            return None, _error(
                "VINA_PARAM_INTEGER_REQUIRED",
                f"{key} 必须是整数。",
                suggestion="请输入整数，不要输入小数。",
            )
        number = int(value)
    else:
        try:
            number = int(str(value).strip(), 10)
        except (TypeError, ValueError) as exc:
            return None, _error(
                "VINA_PARAM_INTEGER_REQUIRED",
                f"{key} 必须是整数。",
                str(exc),
                "请输入整数，例如 8、9 或 0。",
            )

    if allow_zero:
        if number < 0:
            return None, _error(
                "VINA_PARAM_NON_NEGATIVE_REQUIRED",
                f"{key} 必须是非负整数。",
                suggestion=(
                    "cpu 可以填写 0 表示自动，或填写一个正整数。"
                    if key == "cpu"
                    else f"{key} 可以填写 0 表示使用 Vina 默认策略，或填写一个正整数。"
                ),
            )
    elif number <= 0:
        return None, _error(
            "VINA_PARAM_POSITIVE_REQUIRED",
            f"{key} 必须是正整数。",
            suggestion="请输入大于 0 的整数。",
        )

    return number, None


def _parse_vina_positive_float(
    vina: dict[str, Any],
    key: str,
) -> tuple[float | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, _error(
            "VINA_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的 Vina 参数；seed 可以留空。",
        )
    if isinstance(value, bool):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            suggestion="请输入正数，例如 3、4 或 7.5。",
        )

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            str(exc),
            "请输入正数，例如 3、4 或 7.5。",
        )

    if not math.isfinite(number):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            suggestion="请输入有限数字。",
        )
    if number <= 0:
        return None, _error(
            "VINA_PARAM_POSITIVE_REQUIRED",
            f"{key} 必须是正数。",
            suggestion="energy_range 需要大于 0，单位为 kcal/mol。",
        )

    return number, None


def _parse_vina_non_negative_float(
    vina: dict[str, Any],
    key: str,
) -> tuple[float | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, _error(
            "VINA_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请输入大于或等于 0 的有限数字。",
        )
    if isinstance(value, bool):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            suggestion="请输入大于或等于 0 的有限数字。",
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            str(exc),
            "请输入大于或等于 0 的有限数字。",
        )
    if not math.isfinite(number):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            suggestion="请输入有限数字。",
        )
    if number < 0:
        return None, _error(
            "VINA_PARAM_NON_NEGATIVE_REQUIRED",
            f"{key} 必须大于或等于 0。",
            suggestion="请输入 0 或正数。",
        )
    return number, None


def _parse_vina_optional_finite_float(
    vina: dict[str, Any],
    key: str,
) -> tuple[float | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, _error(
            "VINA_UNBOUND_ENERGY_INVALID",
            f"{key} 必须是有限数字，或留空。",
            raw_error=f"{key}={value!r}",
            suggestion="留空表示由 Vina 计算未结合态参考能量；也可填写有限的 kcal/mol 数值。",
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_UNBOUND_ENERGY_INVALID",
            f"{key} 必须是有限数字，或留空。",
            raw_error=str(exc),
            suggestion="留空表示由 Vina 计算未结合态参考能量；也可填写有限的 kcal/mol 数值。",
        )
    if not math.isfinite(number):
        return None, _error(
            "VINA_UNBOUND_ENERGY_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            raw_error=f"{key}={value!r}",
            suggestion="请留空，或填写有限的 kcal/mol 数值。",
        )
    return number, None


def _parse_vina_seed(vina: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    value = vina.get("seed")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, _error(
            "VINA_SEED_INTEGER_REQUIRED",
            "seed 必须是整数，或留空。",
            suggestion="留空表示随机；填写整数可提高复现性。",
        )

    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, _error(
                "VINA_PARAM_INVALID",
                "seed 不能是 NaN 或 Infinity。",
                suggestion="seed 可以留空，或填写一个整数。",
            )
        if not value.is_integer():
            return None, _error(
                "VINA_SEED_INTEGER_REQUIRED",
                "seed 必须是整数，或留空。",
                suggestion="留空表示随机；填写整数可提高复现性。",
            )
        return int(value), None

    try:
        return int(str(value).strip(), 10), None
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_SEED_INTEGER_REQUIRED",
            "seed 必须是整数，或留空。",
            str(exc),
            "留空表示随机；填写整数可提高复现性。",
        )


def _parse_vina_bool(
    vina: dict[str, Any],
    key: str,
) -> tuple[bool | None, dict[str, Any] | None]:
    value = vina.get(key)
    if not isinstance(value, bool):
        return None, _error(
            "VINA_BOOLEAN_REQUIRED",
            f"{key} 必须是 JSON 布尔值 true 或 false。",
            raw_error=f"{key}={value!r}",
            suggestion="请使用开关设置该选项，不要提交字符串或数字。",
        )
    return value, None


def validate_vina_params(vina: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(vina, dict):
        return _error(
            "VINA_PARAMS_INVALID",
            "Vina 参数格式无效。",
            suggestion="请提交包含 exhaustiveness、num_modes、energy_range、cpu 和 seed 的对象。",
        )

    normalized = {
        "max_evals": 0,
        "min_rmsd": 1,
        "spacing": 0.375,
        "unbound_energy": None,
        "no_refine": False,
        "force_even_voxels": False,
        "verbosity": 1,
        **vina,
    }
    scoring = str(normalized.get("scoring") or "vina").strip().lower()
    if scoring not in {"vina", "vinardo"}:
        return _error(
            "VINA_SCORING_INVALID",
            "评分函数仅支持 Vina 或 Vinardo。",
            suggestion="请选择 Vina 或 Vinardo；AutoDock4 需要预先生成 affinity maps，当前标准流程不开放。",
        )

    exhaustiveness, error = _parse_vina_int(normalized, "exhaustiveness")
    if error:
        return error
    max_evals, error = _parse_vina_int(normalized, "max_evals", allow_zero=True)
    if error:
        return error
    num_modes, error = _parse_vina_int(normalized, "num_modes")
    if error:
        return error
    min_rmsd, error = _parse_vina_non_negative_float(normalized, "min_rmsd")
    if error:
        return error
    energy_range, error = _parse_vina_positive_float(normalized, "energy_range")
    if error:
        return error
    spacing, error = _parse_vina_positive_float(normalized, "spacing")
    if error:
        return error
    unbound_energy, error = _parse_vina_optional_finite_float(
        normalized,
        "unbound_energy",
    )
    if error:
        return error
    no_refine, error = _parse_vina_bool(normalized, "no_refine")
    if error:
        return error
    force_even_voxels, error = _parse_vina_bool(normalized, "force_even_voxels")
    if error:
        return error
    verbosity, error = _parse_vina_int(normalized, "verbosity", allow_zero=True)
    if error:
        return error
    cpu, error = _parse_vina_int(normalized, "cpu", allow_zero=True)
    if error:
        return error
    seed, error = _parse_vina_seed(normalized)
    if error:
        return error

    if max_evals is not None and max_evals > 2_147_483_647:
        return _error(
            "VINA_MAX_EVALS_TOO_LARGE",
            "max_evals 超出 Vina 可安全表示的整数范围。",
            suggestion="请输入 0 到 2147483647；0 表示由 Vina 按启发式规则决定。",
        )
    if min_rmsd is not None and min_rmsd > 100:
        return _error(
            "VINA_MIN_RMSD_TOO_LARGE",
            "min_rmsd 不能大于 100 Å。",
            suggestion="常用值为 1 Å；0 表示不以正距离阈值分隔输出姿势。",
        )
    if spacing is not None and not 0.1 <= spacing <= 2:
        return _error(
            "VINA_SPACING_OUT_OF_RANGE",
            "spacing 必须在 0.1 到 2.0 Å 之间。",
            suggestion="Vina 默认值为 0.375 Å；过小会显著增加网格内存。",
        )
    if verbosity not in {1, 2}:
        return _error(
            "VINA_VERBOSITY_UNSUPPORTED",
            "DockStart 只允许 verbosity 为 1 或 2。",
            suggestion="选择 1（标准日志）或 2（详细日志）；0 会使结果日志不足以解析。",
        )

    parsed = {
        "scoring": scoring,
        "exhaustiveness": exhaustiveness,
        "max_evals": max_evals,
        "num_modes": num_modes,
        "min_rmsd": min_rmsd,
        "energy_range": energy_range,
        "spacing": spacing,
        "unbound_energy": unbound_energy,
        "no_refine": no_refine,
        "force_even_voxels": force_even_voxels,
        "verbosity": verbosity,
        "cpu": cpu,
        "seed": seed,
    }

    warnings: list[str] = []
    if exhaustiveness is not None and exhaustiveness > 64:
        warnings.append("exhaustiveness 较高，可能显著增加运行时间；新手建议从 8 开始。")
    if max_evals is not None and max_evals > 1_000_000:
        warnings.append("max_evals 较高，可能显著增加每条 Monte Carlo 搜索链的运行时间。")
    if num_modes is not None and num_modes > 50:
        warnings.append("num_modes 较多，结果文件和解析成本可能增加；请确认确实需要这么多构象。")
    if energy_range is not None and energy_range > 10:
        warnings.append("energy_range 较大，可能保留较多高能构象；新手建议 3 或 4。")
    if spacing is not None and spacing < 0.25:
        warnings.append("spacing 小于 0.25 Å，网格内存和计算量可能显著增加。")
    if spacing is not None and spacing > 0.75:
        warnings.append("spacing 大于 0.75 Å，网格较粗；请确认这符合研究目的。")
    if unbound_energy is not None:
        warnings.append(
            "已设置显式未结合态参考能量；该值仅用于 score_only，并会改变总评分参考。"
        )
    if no_refine:
        warnings.append("已关闭显式受体原子精修；结果只应与相同设置的运行比较。")
    if force_even_voxels:
        warnings.append("网格体素数将取偶数，实际网格边界可能随体素取整调整。")
    if verbosity == 2:
        warnings.append("verbosity 为 2 将保存更详细的 Vina 日志。")
    if cpu == 0:
        warnings.append("cpu 设置为 0，将由 Vina 自动决定或使用默认 CPU 设置。")

    return {
        "ok": True,
        "vina": parsed,
        "warnings": warnings,
        "error": None,
    }


def _validate_vina_grid_resource(
    box: dict[str, Any],
    spacing: float,
    atom_types: list[str],
    force_even_voxels: bool = False,
) -> dict[str, Any]:
    """Estimate Vina grid cells and reject configurations likely to exhaust memory."""

    axis_intervals = {
        axis: max(1, math.ceil(float(box[f"size_{axis}"]) / spacing))
        for axis in ("x", "y", "z")
    }
    adjusted_axes: list[str] = []
    if force_even_voxels:
        for axis, intervals in axis_intervals.items():
            if intervals % 2:
                axis_intervals[axis] = intervals + 1
                adjusted_axes.append(axis)
    axis_points = {axis: intervals + 1 for axis, intervals in axis_intervals.items()}
    total_points = math.prod(axis_points.values())
    unique_atom_types = sorted({str(value).strip() for value in atom_types if str(value).strip()})
    map_count = len(unique_atom_types) if unique_atom_types else 4
    estimated_map_bytes = total_points * map_count * 8
    estimate = {
        "spacing_angstrom": spacing,
        "force_even_voxels": force_even_voxels,
        "axis_intervals": axis_intervals,
        "axis_points": axis_points,
        "adjusted_axes": adjusted_axes,
        "total_points": total_points,
        "atom_types": unique_atom_types,
        "map_count": map_count,
        "estimated_map_bytes": estimated_map_bytes,
        "warning_bytes": VINA_GRID_MEMORY_WARNING_BYTES,
        "hard_limit_bytes": VINA_GRID_MEMORY_HARD_LIMIT_BYTES,
    }
    if estimated_map_bytes > VINA_GRID_MEMORY_HARD_LIMIT_BYTES:
        return _error(
            "VINA_GRID_RESOURCE_LIMIT_EXCEEDED",
            (
                "当前对接箱体、spacing 与可移动原子类型预计需要约 "
                f"{estimated_map_bytes / (1024 ** 3):.2f} GiB 网格数据，"
                "超过 DockStart 的资源保护上限。"
            ),
            raw_error=json.dumps(estimate, ensure_ascii=False),
            suggestion="请缩小对接箱体，或适当增大 spacing 后再运行。",
        )

    warnings: list[str] = []
    if adjusted_axes:
        warnings.append(
            "网格体素取偶数后，"
            + "、".join(axis.upper() for axis in adjusted_axes)
            + " 轴的实际网格边界将扩大一个 spacing。"
        )
    if estimated_map_bytes > VINA_GRID_MEMORY_WARNING_BYTES:
        warnings.append(
            "当前 Box、spacing 与可移动原子类型预计需要约 "
            f"{estimated_map_bytes / (1024 ** 2):.0f} MiB 网格数据，Vina 可能占用较多内存；"
            "如非必要，建议缩小 Box 或增大 spacing。"
        )
    return {
        "ok": True,
        "grid_estimate": estimate,
        "warnings": warnings,
        "error": None,
    }


def validate_vina_runtime_capabilities(
    vina: dict[str, Any],
    scoring_protocol: str,
    capabilities: dict[str, Any] | None,
    *,
    run_mode: str = "dock",
    receptor_mode: str = "rigid",
    autobox: bool = False,
) -> dict[str, Any]:
    """Fail closed when an enabled expert option is not verified by the active Vina."""

    normalized_protocol = str(scoring_protocol or "vina").strip().lower()
    normalized_mode = _normalize_run_mode(run_mode)
    flexible_receptor = str(receptor_mode or "rigid").strip().lower() == "flexible"
    required = (
        []
        if normalized_protocol == "ad4_maps"
        else [
            key
            for key in ("no_refine", "force_even_voxels")
            if vina.get(key) is True
        ]
        + (
            ["unbound_energy"]
            if normalized_mode == "score_only"
            and not flexible_receptor
            and vina.get("unbound_energy") is not None
            else []
        )
        + (
            ["autobox"]
            if normalized_mode != "dock" and autobox
            else []
        )
    )
    profile = capabilities if isinstance(capabilities, dict) else {}
    features = profile.get("features") if isinstance(profile.get("features"), dict) else {}
    unsupported: list[dict[str, str]] = []
    for key in required:
        feature = features.get(key) if isinstance(features.get(key), dict) else {}
        if feature.get("status") == "supported" and feature.get("supported") is True:
            continue
        unsupported.append(
            {
                "key": key,
                "status": str(feature.get("status") or "unknown"),
                "message": str(feature.get("message") or "当前 Vina 未返回该选项的可靠能力证据。"),
            }
        )

    if not unsupported:
        return {
            "ok": True,
            "required": required,
            "capabilities": copy.deepcopy(profile),
            "message": (
                "当前 Vina 已确认支持本次启用的专家选项。"
                if required
                else "本次运行未启用需要能力门禁的专家选项。"
            ),
            "error": None,
        }

    first = unsupported[0]
    labels = {
        "autobox": "按输入配体自动建立评价范围",
        "no_refine": "关闭显式受体原子精修",
        "force_even_voxels": "网格体素数取偶数",
        "unbound_energy": "显式未结合态参考能量",
    }
    code = {
        "autobox": "VINA_AUTOBOX_UNSUPPORTED",
        "no_refine": "VINA_NO_REFINE_UNSUPPORTED",
        "force_even_voxels": "VINA_FORCE_EVEN_VOXELS_UNSUPPORTED",
        "unbound_energy": "VINA_UNBOUND_ENERGY_UNSUPPORTED",
    }.get(first["key"], "VINA_ADVANCED_FEATURE_UNSUPPORTED")
    return _error(
        code,
        f"当前 AutoDock Vina 尚未确认支持“{labels.get(first['key'], first['key'])}”。",
        raw_error=json.dumps(
            {
                "required": required,
                "unsupported": unsupported,
                "capabilities": profile,
            },
            ensure_ascii=False,
        ),
        suggestion=(
            "请关闭该选项，或改用能在 --help_advanced 中声明该参数的兼容 Vina。"
            + (
                (
                    " 该选项还要求 Vina 1.2.3 或更高版本。"
                    if first["key"] == "autobox"
                    else " 该选项还要求 Vina 1.2.4 或更高版本。"
                )
                if first["key"] in {"autobox", "no_refine", "unbound_energy"}
                else ""
            )
        ),
    )


def get_vina_params(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "vina": asdict(project.vina),
        "warnings": [],
        "message": "Vina 参数读取成功。",
    }


def update_vina_params(project_dir: str, vina: dict[str, Any]) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CONFIG_INVALID",
            "项目配置格式不完整，无法保存 Vina 参数。",
            str(exc),
            "请检查 project.json 中的 vina 字段。",
        )

    merged_vina = {**asdict(project.vina), **vina} if isinstance(vina, dict) else vina
    validation = validate_vina_params(merged_vina)
    if not validation.get("ok"):
        return validation

    try:
        parsed_vina = validation["vina"]
        project.vina = VinaSettings(
            scoring=parsed_vina["scoring"],
            exhaustiveness=parsed_vina["exhaustiveness"],
            max_evals=parsed_vina["max_evals"],
            num_modes=parsed_vina["num_modes"],
            min_rmsd=parsed_vina["min_rmsd"],
            energy_range=parsed_vina["energy_range"],
            spacing=parsed_vina["spacing"],
            unbound_energy=parsed_vina["unbound_energy"],
            no_refine=parsed_vina["no_refine"],
            force_even_voxels=parsed_vina["force_even_voxels"],
            verbosity=parsed_vina["verbosity"],
            cpu=parsed_vina["cpu"],
            seed=parsed_vina["seed"],
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "Vina 参数已保存。", validation.get("warnings", []))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_UPDATE_ERROR",
            "保存 Vina 参数时发生错误。",
            str(exc),
            "请确认 project.json 可写。",
        )


def _format_config_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)


def _parse_run_config_bool(
    config_text: str,
    key: str,
) -> tuple[bool | None, dict[str, Any] | None]:
    matches = re.findall(
        rf"(?mi)^\s*{re.escape(key)}\s*=\s*([^#\r\n]*?)\s*(?:#.*)?$",
        config_text,
    )
    if not matches:
        return False, None
    if len(matches) != 1 or matches[0].strip().lower() not in {"true", "false"}:
        return None, _error(
            "RUN_CONFIG_ADVANCED_OPTION_INVALID",
            f"运行配置中的 {key} 必须只出现一次，并使用 true 或 false。",
            raw_error=f"{key}={matches!r}",
            suggestion="请重新准备新的 run；不要手动修改配置快照。",
        )
    return matches[0].strip().lower() == "true", None


def _parse_run_config_optional_float(
    config_text: str,
    key: str,
) -> tuple[float | None, dict[str, Any] | None]:
    matches = re.findall(
        rf"(?mi)^\s*{re.escape(key)}\s*=\s*([^#\r\n]*?)\s*(?:#.*)?$",
        config_text,
    )
    if not matches:
        return None, None
    if len(matches) != 1 or not matches[0].strip():
        return None, _error(
            "RUN_CONFIG_UNBOUND_ENERGY_INVALID",
            f"运行配置中的 {key} 必须只出现一次，并填写有限数字。",
            raw_error=f"{key}={matches!r}",
            suggestion="请重新准备新的 run；不要手动修改配置快照。",
        )
    raw_value = matches[0].strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        return None, _error(
            "RUN_CONFIG_UNBOUND_ENERGY_INVALID",
            f"运行配置中的 {key} 不是有效数字。",
            raw_error=str(exc),
            suggestion="请重新准备新的 run；不要手动修改配置快照。",
        )
    if not math.isfinite(value):
        return None, _error(
            "RUN_CONFIG_UNBOUND_ENERGY_INVALID",
            f"运行配置中的 {key} 不能是 NaN 或 Infinity。",
            raw_error=f"{key}={raw_value!r}",
            suggestion="请重新准备新的 run；不要手动修改配置快照。",
        )
    return value, None


def _project_relative_file(project_dir: Path, relative_path: str, role_label: str) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{role_label.upper()}_FILE_NOT_SET",
            f"{role_label} PDBQT 文件尚未导入，无法生成 Vina 配置文件。",
            suggestion=f"请先回到 PDBQT 导入页，导入 {role_label}.pdbqt。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{role_label.upper()}_FILE_PATH_NOT_RELATIVE",
            f"{role_label} 文件路径必须是项目内相对路径。",
            suggestion="请重新导入 PDBQT 文件，让 DockStart 使用 prepared 目录中的副本。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{role_label.upper()}_FILE_OUTSIDE_PROJECT",
            f"{role_label} 文件路径指向项目目录外，无法写入配置文件。",
            suggestion="请重新导入 PDBQT 文件，让 DockStart 使用项目目录内的 prepared 文件。",
        )
    if not file_path.exists():
        return None, _error(
            f"{role_label.upper()}_FILE_NOT_FOUND",
            f"没有找到准备后的 {role_label}.pdbqt 文件。",
            raw_error=str(file_path),
            suggestion=f"请确认 {relative_path} 是否存在，或重新导入 {role_label}.pdbqt。",
        )
    if not file_path.is_file():
        return None, _error(
            f"{role_label.upper()}_PATH_NOT_FILE",
            f"{role_label} PDBQT 路径不是一个文件。",
            raw_error=str(file_path),
            suggestion="请重新导入 PDBQT 文件。",
        )

    return file_path, None


def _project_file_exists_non_empty(project_dir: Path, relative_path: str) -> bool:
    if not relative_path:
        return False
    path = Path(relative_path)
    if path.is_absolute():
        return path.is_file() and path.stat().st_size > 0
    resolved = (project_dir.resolve() / path).resolve()
    try:
        resolved.relative_to(project_dir.resolve())
    except ValueError:
        return False
    return resolved.is_file() and resolved.stat().st_size > 0


def _prepared_input_hint(project: DockStartProject, project_dir: Path, target: str, fallback_error: dict[str, Any]) -> dict[str, Any]:
    target_label = "受体" if target == "receptor" else "配体"
    file_ref = getattr(project, target)
    preparation_result = getattr(project.preparation, target)
    raw_file = str(file_ref.raw_file or "")
    fallback_code = fallback_error.get("error", {}).get("code", f"{target.upper()}_FILE_NOT_READY")

    if preparation_result.status == "failed":
        prep_error = preparation_result.error if isinstance(preparation_result.error, dict) else {}
        return _error(
            f"{target.upper()}_PREPARATION_FAILED",
            f"{target_label} PDBQT 自动准备上次失败，请先查看 preparation 日志。",
            raw_error=str(prep_error.get("raw_error") or preparation_result.log_file or raw_file),
            suggestion=f"请回到自动准备页面查看 {target} preparation 日志，修复后重新准备或手动导入 prepared/{target}.pdbqt。",
        )

    if raw_file and fallback_code in {f"{target.upper()}_FILE_NOT_SET", f"{target.upper()}_FILE_NOT_FOUND"}:
        if _project_file_exists_non_empty(project_dir, raw_file):
            return _error(
                f"{target.upper()}_PDBQT_NOT_PREPARED",
                f"已下载 raw {target}，但尚未准备 prepared/{target}.pdbqt。",
                raw_error=raw_file,
                suggestion=f"请前往自动准备页面生成 prepared/{target}.pdbqt，或手动导入已经准备好的 {target}.pdbqt。",
            )

    return fallback_error


def _project_scoring_protocol(project: DockStartProject) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    if isinstance(protocol, dict) and str(protocol.get("engine") or "").strip().lower() == "ad4_maps":
        return "ad4_maps"
    return "vina"


def _project_protocol_id(project: DockStartProject) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    protocol_id = (
        str(protocol.get("protocol_id") or "").strip().lower()
        if isinstance(protocol, dict)
        else ""
    )
    if (
        _project_scoring_protocol(project) == "ad4_maps"
        and protocol_id == AD4ZN_PROTOCOL_ID
    ):
        return AD4ZN_PROTOCOL_ID
    if _project_scoring_protocol(project) == "ad4_maps":
        return "ad4_maps"
    return protocol_id or "rigid_single"


def _project_grid_source(project: DockStartProject) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    return (
        "precomputed_maps"
        if _project_scoring_protocol(project) == "vina"
        and isinstance(protocol, dict)
        and str(protocol.get("grid_source") or "").strip().lower()
        == "precomputed_maps"
        and str(protocol.get("protocol_id") or "").strip().lower()
        == "vina_maps"
        else "receptor"
    )


def _project_run_mode(project: DockStartProject) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    return _normalize_run_mode(protocol.get("run_mode")) if isinstance(protocol, dict) else "dock"


def _project_receptor_mode(project: DockStartProject) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    configured_mode = ""
    if isinstance(protocol, dict):
        configured_mode = protocol.get("receptor_mode") or protocol.get("mode") or ""
    return (
        "flexible"
        if str(configured_mode or "").strip().lower() == "flexible"
        else "rigid"
    )


def _project_autobox(project: DockStartProject) -> bool:
    protocol = project.preserved_data.get("docking_protocol")
    run_mode = _project_run_mode(project)
    return (
        protocol.get("autobox") is True
        if isinstance(protocol, dict)
        and run_mode != "dock"
        and _project_scoring_protocol(project) != "ad4_maps"
        else False
    )


def _pose_input_attestation_result(
    *,
    required: bool,
    valid: bool,
    status: str,
    message: str,
    attestation: dict[str, Any] | None = None,
    current_receptor_sha256: str = "",
    current_ligand_sha256: str = "",
    current_flex_sha256: str = "",
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recorded = attestation if isinstance(attestation, dict) else {}
    return {
        "required": required,
        "valid": valid,
        "status": status,
        "confirmed_at": str(recorded.get("confirmed_at") or ""),
        "receptor_sha256": str(recorded.get("receptor_sha256") or ""),
        "ligand_sha256": str(recorded.get("ligand_sha256") or ""),
        "flex_sha256": str(recorded.get("flex_sha256") or ""),
        "claim": str(recorded.get("claim") or ""),
        "current_receptor_sha256": current_receptor_sha256,
        "current_ligand_sha256": current_ligand_sha256,
        "current_flex_sha256": current_flex_sha256,
        "message": message,
        "attestation": copy.deepcopy(recorded) if recorded else None,
        "error": copy.deepcopy(error),
    }


def _validate_current_pose_input_attestation(
    project_path: Path,
    project: DockStartProject,
    *,
    receptor_file: str | None = None,
) -> dict[str, Any]:
    """Validate that a user's pose-context claim still binds the current files.

    This validates record structure and file hashes only. It does not establish
    that the receptor and ligand actually share a scientifically valid
    coordinate frame.
    """

    protocol = project.preserved_data.get("docking_protocol")
    attestation = (
        protocol.get("pose_input_attestation")
        if isinstance(protocol, dict)
        and isinstance(protocol.get("pose_input_attestation"), dict)
        else None
    )
    run_mode = _project_run_mode(project)
    if run_mode == "dock":
        return _pose_input_attestation_result(
            required=False,
            valid=True,
            status="not_required",
            message="全局对接不要求输入姿势坐标系确认。",
            attestation=attestation,
        )

    if attestation is None:
        error = {
            "code": "POSE_INPUT_ATTESTATION_REQUIRED",
            "message": "尚未记录用户对受体与配体输入姿势坐标系的确认。",
            "raw_error": "",
            "suggestion": "请核对当前配体姿势与受体使用同一受体坐标系，然后明确确认；DockStart 不会自动证明这一科学前提。",
        }
        return _pose_input_attestation_result(
            required=True,
            valid=False,
            status="missing",
            message=error["message"],
            error=error,
        )

    receptor_inputs = _active_receptor_inputs(project_path, project)
    if not receptor_inputs.get("ok"):
        receptor_error = receptor_inputs.get("error") or {}
        error = {
            "code": "POSE_INPUT_ATTESTATION_INPUT_UNAVAILABLE",
            "message": "当前运行受体 PDBQT 不可用，无法核对用户确认记录。",
            "raw_error": str(
                receptor_error.get("raw_error")
                or receptor_error.get("message")
                or ""
            ),
            "suggestion": str(
                receptor_error.get("suggestion")
                or "请先恢复当前项目的运行受体输入，然后重新确认输入姿势坐标系。"
            ),
        }
        return _pose_input_attestation_result(
            required=True,
            valid=False,
            status="invalid",
            message=error["message"],
            attestation=attestation,
            error=error,
        )
    effective_receptor_file = str(
        receptor_file or receptor_inputs.get("receptor_file") or ""
    )
    effective_flex_file = str(receptor_inputs.get("flex_file") or "")

    confirmed_at = attestation.get("confirmed_at")
    confirmed_at_valid = False
    if isinstance(confirmed_at, str) and confirmed_at.strip():
        try:
            parsed_confirmed_at = datetime.fromisoformat(
                confirmed_at.strip().replace("Z", "+00:00")
            )
            confirmed_at_valid = parsed_confirmed_at.tzinfo is not None
        except ValueError:
            confirmed_at_valid = False
    receptor_sha256 = attestation.get("receptor_sha256")
    ligand_sha256 = attestation.get("ligand_sha256")
    flex_sha256 = attestation.get("flex_sha256")
    schema_valid = (
        type(attestation.get("version")) is int
        and attestation.get("version") == POSE_INPUT_ATTESTATION_VERSION
        and attestation.get("claim") == POSE_INPUT_ATTESTATION_CLAIM
        and confirmed_at_valid
        and isinstance(receptor_sha256, str)
        and SHA256_PATTERN.fullmatch(receptor_sha256) is not None
        and isinstance(ligand_sha256, str)
        and SHA256_PATTERN.fullmatch(ligand_sha256) is not None
        and (
            (
                isinstance(flex_sha256, str)
                and SHA256_PATTERN.fullmatch(flex_sha256) is not None
            )
            if effective_flex_file
            else flex_sha256 is None or flex_sha256 == ""
        )
    )
    if not schema_valid:
        error = {
            "code": "POSE_INPUT_ATTESTATION_INVALID",
            "message": "输入姿势坐标系确认记录格式无效。",
            "raw_error": "确认记录的版本、时间、声明或 SHA256 字段不符合当前格式。",
            "suggestion": "请重新核对当前受体与配体的坐标系并再次确认。",
        }
        return _pose_input_attestation_result(
            required=True,
            valid=False,
            status="invalid",
            message=error["message"],
            attestation=attestation,
            error=error,
        )

    receptor_path, receptor_error = _project_relative_file(
        project_path,
        effective_receptor_file,
        "receptor",
    )
    ligand_path, ligand_error = _project_relative_file(
        project_path,
        project.ligand.file,
        "ligand",
    )
    flex_path: Path | None = None
    flex_error: dict[str, Any] | None = None
    if effective_flex_file:
        flex_path, flex_error = _project_relative_file(
            project_path,
            effective_flex_file,
            "flex",
        )
    if (
        receptor_error
        or ligand_error
        or flex_error
        or receptor_path is None
        or ligand_path is None
        or (effective_flex_file and flex_path is None)
        or receptor_path.stat().st_size <= 0
        or ligand_path.stat().st_size <= 0
        or (flex_path is not None and flex_path.stat().st_size <= 0)
    ):
        source_error = (
            receptor_error or ligand_error or flex_error or {}
        ).get("error") or {}
        error = {
            "code": "POSE_INPUT_ATTESTATION_INPUT_UNAVAILABLE",
            "message": "当前运行受体、柔性侧链或配体 PDBQT 不可用，无法核对用户确认记录。",
            "raw_error": str(
                source_error.get("raw_error")
                or source_error.get("message")
                or "运行受体、柔性侧链或配体 PDBQT 为空。"
            ),
            "suggestion": "请先恢复当前项目的运行受体、柔性侧链与配体 PDBQT，然后重新确认输入姿势坐标系。",
        }
        return _pose_input_attestation_result(
            required=True,
            valid=False,
            status="invalid",
            message=error["message"],
            attestation=attestation,
            error=error,
        )

    current_receptor_sha256 = _sha256_file(receptor_path)
    current_ligand_sha256 = _sha256_file(ligand_path)
    current_flex_sha256 = _sha256_file(flex_path) if flex_path is not None else ""
    if (
        receptor_sha256 != current_receptor_sha256
        or ligand_sha256 != current_ligand_sha256
        or str(flex_sha256 or "") != current_flex_sha256
    ):
        changed_inputs = [
            label
            for label, recorded_hash, current_hash in (
                ("受体", receptor_sha256, current_receptor_sha256),
                ("配体", ligand_sha256, current_ligand_sha256),
                ("柔性侧链", str(flex_sha256 or ""), current_flex_sha256),
            )
            if recorded_hash != current_hash
        ]
        error = {
            "code": "POSE_INPUT_ATTESTATION_STALE",
            "message": f"{'、'.join(changed_inputs)} PDBQT 已在用户确认后发生变化，原确认已失效。",
            "raw_error": "确认记录中的 SHA256 与当前项目文件不一致。",
            "suggestion": "请重新核对当前受体与配体是否使用同一受体坐标系，然后再次确认。",
        }
        return _pose_input_attestation_result(
            required=True,
            valid=False,
            status="stale",
            message=error["message"],
            attestation=attestation,
            current_receptor_sha256=current_receptor_sha256,
            current_ligand_sha256=current_ligand_sha256,
            current_flex_sha256=current_flex_sha256,
            error=error,
        )

    return _pose_input_attestation_result(
        required=True,
        valid=True,
        status="confirmed",
        message=(
            "用户确认记录与当前运行受体、柔性侧链和配体 PDBQT 的 SHA256 一致。"
            if current_flex_sha256
            else "用户确认记录与当前受体、配体 PDBQT 的 SHA256 一致。"
        ),
        attestation=attestation,
        current_receptor_sha256=current_receptor_sha256,
        current_ligand_sha256=current_ligand_sha256,
        current_flex_sha256=current_flex_sha256,
    )


def validate_pose_input_attestation(project_dir: str) -> dict[str, Any]:
    """Return the current user-attestation status for one project."""

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    try:
        project_path = Path(project_dir).expanduser().resolve()
        project = _project_from_dict(loaded["project"], project_path)
        return {
            "ok": True,
            "project_dir": str(project_path),
            "pose_input_attestation": _validate_current_pose_input_attestation(
                project_path,
                project,
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "POSE_INPUT_ATTESTATION_CHECK_ERROR",
            "核对输入姿势坐标系确认记录时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 project.json 与当前受体、配体 PDBQT 可读取。",
        )


def _validate_frozen_pose_input_attestation(
    protocol: dict[str, Any],
    run_mode: str,
    expected_hashes: dict[str, str],
) -> dict[str, Any] | None:
    """Validate the user claim frozen into a prepared evaluation run.

    Finished historical runs are not routed through this check. It protects
    only a not-yet-executed score/local run and binds the recorded claim to the
    immutable rigid receptor, optional flexible side-chain, and ligand snapshots
    that Vina will actually consume.
    """

    if run_mode == "dock":
        return None

    attestation = protocol.get("pose_input_attestation")
    if not isinstance(attestation, dict):
        return _error(
            "RUN_POSE_INPUT_ATTESTATION_MISSING",
            "本次姿势评价 run 未冻结用户的输入姿势坐标系确认，拒绝执行。",
            suggestion="请保留该 run 作为审计记录，回到运行工作台重新核对姿势并准备新的 run。",
        )

    confirmed_at = attestation.get("confirmed_at")
    confirmed_at_valid = False
    if isinstance(confirmed_at, str) and confirmed_at.strip():
        try:
            parsed_confirmed_at = datetime.fromisoformat(
                confirmed_at.strip().replace("Z", "+00:00")
            )
            confirmed_at_valid = parsed_confirmed_at.tzinfo is not None
        except ValueError:
            confirmed_at_valid = False

    receptor_sha256 = attestation.get("receptor_sha256")
    ligand_sha256 = attestation.get("ligand_sha256")
    flex_sha256 = attestation.get("flex_sha256")
    expected_flex_sha256 = expected_hashes.get("flex")
    if not (
        type(attestation.get("version")) is int
        and attestation.get("version") == POSE_INPUT_ATTESTATION_VERSION
        and attestation.get("claim") == POSE_INPUT_ATTESTATION_CLAIM
        and confirmed_at_valid
        and isinstance(receptor_sha256, str)
        and SHA256_PATTERN.fullmatch(receptor_sha256) is not None
        and isinstance(ligand_sha256, str)
        and SHA256_PATTERN.fullmatch(ligand_sha256) is not None
        and (
            (
                isinstance(flex_sha256, str)
                and SHA256_PATTERN.fullmatch(flex_sha256) is not None
            )
            if expected_flex_sha256 is not None
            else flex_sha256 is None or flex_sha256 == ""
        )
    ):
        return _error(
            "RUN_POSE_INPUT_ATTESTATION_INVALID",
            "本次姿势评价 run 的输入姿势坐标系确认记录格式无效，拒绝执行。",
            raw_error="确认版本、时间、声明或 SHA256 字段不符合当前格式。",
            suggestion="请保留该 run 作为审计记录，重新核对姿势并准备新的 run。",
        )

    mismatched = [
        label
        for label, recorded, expected in (
            ("受体", receptor_sha256, expected_hashes.get("receptor", "")),
            ("配体", ligand_sha256, expected_hashes.get("ligand", "")),
            *(
                [("柔性侧链", str(flex_sha256 or ""), expected_flex_sha256)]
                if expected_flex_sha256 is not None
                else []
            ),
        )
        if recorded.lower() != expected.lower()
    ]
    if mismatched:
        return _error(
            "RUN_POSE_INPUT_ATTESTATION_HASH_MISMATCH",
            f"本次 run 的{'、'.join(mismatched)}输入快照与用户确认记录不一致，拒绝执行。",
            raw_error="确认记录中的 SHA256 未绑定到本次 immutable 输入快照。",
            suggestion="请保留该 run 作为审计记录，重新核对姿势并准备新的 run。",
        )
    return None


def _apply_vina_run_protocol(
    project_path: Path,
    project: DockStartProject,
    run_mode: str,
    autobox: bool,
    confirm_pose_context: bool = False,
) -> dict[str, Any]:
    normalized_mode = str(run_mode or "").strip().lower()
    if normalized_mode not in VINA_RUN_MODES:
        return _error(
            "VINA_RUN_MODE_INVALID",
            "运行任务类型无效。",
            raw_error=str(run_mode),
            suggestion="请选择全局对接、仅评分或局部优化。",
        )
    if not isinstance(autobox, bool):
        return _error(
            "VINA_AUTOBOX_INVALID",
            "自动评价范围必须是布尔值。",
            raw_error=repr(autobox),
        )
    if not isinstance(confirm_pose_context, bool):
        return _error(
            "POSE_INPUT_CONFIRMATION_INVALID",
            "输入姿势坐标系确认必须是布尔值。",
            raw_error=repr(confirm_pose_context),
        )

    if (
        _project_grid_source(project) == "precomputed_maps"
        and normalized_mode != "dock"
    ):
        return _error(
            "VINA_MAPS_RUN_MODE_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 当前只支持全局对接。",
            raw_error=f"run_mode={normalized_mode}",
            suggestion="请先将网格来源切换回受体，再选择仅评分或局部优化。",
        )
    protocol = project.preserved_data.get("docking_protocol")
    next_protocol = copy.deepcopy(protocol) if isinstance(protocol, dict) else {}
    next_protocol["run_mode"] = normalized_mode
    next_protocol["autobox"] = (
        bool(autobox)
        if normalized_mode != "dock"
        and _project_scoring_protocol(project) != "ad4_maps"
        else False
    )
    project.preserved_data["docking_protocol"] = next_protocol
    if normalized_mode != "dock" and confirm_pose_context:
        receptor_inputs = _active_receptor_inputs(
            project_path,
            project,
        )
        if not receptor_inputs.get("ok"):
            return receptor_inputs
        receptor_path, receptor_error = _project_relative_file(
            project_path,
            str(receptor_inputs.get("receptor_file") or ""),
            "receptor",
        )
        ligand_path, ligand_error = _project_relative_file(
            project_path,
            project.ligand.file,
            "ligand",
        )
        flex_file = str(receptor_inputs.get("flex_file") or "")
        flex_path: Path | None = None
        flex_error: dict[str, Any] | None = None
        if flex_file:
            flex_path, flex_error = _project_relative_file(
                project_path,
                flex_file,
                "flex",
            )
        if receptor_error:
            return receptor_error
        if ligand_error:
            return ligand_error
        if flex_error:
            return flex_error
        if (
            receptor_path is None
            or ligand_path is None
            or (flex_file and flex_path is None)
            or receptor_path.stat().st_size <= 0
            or ligand_path.stat().st_size <= 0
            or (flex_path is not None and flex_path.stat().st_size <= 0)
        ):
            return _error(
                "POSE_INPUT_ATTESTATION_INPUT_EMPTY",
                "运行受体、柔性侧链或配体 PDBQT 为空，不能记录输入姿势坐标系确认。",
                suggestion="请恢复非空的运行受体、柔性侧链与配体 PDBQT。",
            )
        pose_input_attestation = {
            "version": POSE_INPUT_ATTESTATION_VERSION,
            "confirmed_at": _now_iso(),
            "receptor_sha256": _sha256_file(receptor_path),
            "ligand_sha256": _sha256_file(ligand_path),
            "claim": POSE_INPUT_ATTESTATION_CLAIM,
        }
        if flex_path is not None:
            pose_input_attestation["flex_sha256"] = _sha256_file(flex_path)
        next_protocol["pose_input_attestation"] = pose_input_attestation
    return {
        "ok": True,
        "run_mode": normalized_mode,
        "autobox": next_protocol["autobox"],
        "pose_input_attestation": _validate_current_pose_input_attestation(
            project_path,
            project,
        ),
        "error": None,
    }


def update_vina_run_protocol(
    project_dir: str,
    run_mode: str,
    autobox: bool,
    confirm_pose_context: bool = False,
) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    try:
        project_path = Path(project_dir).expanduser().resolve()
        project = _project_from_dict(loaded["project"], project_path)
        applied = _apply_vina_run_protocol(
            project_path,
            project,
            run_mode,
            autobox,
            confirm_pose_context,
        )
        if not applied.get("ok"):
            return applied
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return {
            **saved,
            "run_mode": applied["run_mode"],
            "autobox": applied["autobox"],
            "pose_input_attestation": applied["pose_input_attestation"],
            "message": "运行任务类型已保存。",
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_RUN_PROTOCOL_UPDATE_ERROR",
            "保存运行任务类型时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 project.json 可写。",
        )


def update_run_settings(
    project_dir: str,
    box: dict[str, Any],
    vina: dict[str, Any],
    run_mode: str,
    autobox: bool,
    confirm_pose_context: bool = False,
) -> dict[str, Any]:
    """Atomically update Box, Vina parameters, and the run protocol.

    All validation is completed against one loaded project revision before the
    single :func:`save_project` call.  A validation error or revision conflict
    therefore cannot leave a subset of the three settings persisted.
    """

    box_validation = validate_box_params(box)
    if not box_validation.get("ok"):
        return box_validation

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    try:
        project_path = Path(project_dir).expanduser().resolve()
        project = _project_from_dict(loaded["project"], project_path)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CONFIG_INVALID",
            "项目配置格式不完整，无法保存运行设置。",
            raw_error=str(exc),
            suggestion="请检查 project.json 中的 box、vina 和 docking_protocol 字段。",
        )

    merged_vina = {**asdict(project.vina), **vina} if isinstance(vina, dict) else vina
    vina_validation = validate_vina_params(merged_vina)
    if not vina_validation.get("ok"):
        return vina_validation

    try:
        parsed_box = box_validation["box"]
        project.box = BoxSettings(
            center_x=parsed_box["center_x"],
            center_y=parsed_box["center_y"],
            center_z=parsed_box["center_z"],
            size_x=parsed_box["size_x"],
            size_y=parsed_box["size_y"],
            size_z=parsed_box["size_z"],
        )
        parsed_vina = vina_validation["vina"]
        project.vina = VinaSettings(
            scoring=parsed_vina["scoring"],
            exhaustiveness=parsed_vina["exhaustiveness"],
            max_evals=parsed_vina["max_evals"],
            num_modes=parsed_vina["num_modes"],
            min_rmsd=parsed_vina["min_rmsd"],
            energy_range=parsed_vina["energy_range"],
            spacing=parsed_vina["spacing"],
            unbound_energy=parsed_vina["unbound_energy"],
            no_refine=parsed_vina["no_refine"],
            force_even_voxels=parsed_vina["force_even_voxels"],
            verbosity=parsed_vina["verbosity"],
            cpu=parsed_vina["cpu"],
            seed=parsed_vina["seed"],
        )
        applied = _apply_vina_run_protocol(
            project_path,
            project,
            run_mode,
            autobox,
            confirm_pose_context,
        )
        if not applied.get("ok"):
            return applied

        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        warnings = list(
            dict.fromkeys(
                [
                    *box_validation.get("warnings", []),
                    *vina_validation.get("warnings", []),
                ]
            )
        )
        response = _success(project, "运行设置已原子保存。", warnings)
        response.update(
            {
                "box": asdict(project.box),
                "vina": asdict(project.vina),
                "run_mode": applied["run_mode"],
                "autobox": applied["autobox"],
                "pose_input_attestation": applied["pose_input_attestation"],
            }
        )
        return response
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_SETTINGS_UPDATE_ERROR",
            "保存运行设置时发生错误，未提交部分更新。",
            raw_error=str(exc),
            suggestion="请重新读取项目后再保存 Box、Vina 参数和运行任务类型。",
        )


def _active_ad4_maps(project_dir: str) -> dict[str, Any]:
    from dockstart_core.autogrid import validate_active_maps

    return validate_active_maps(project_dir)


def _active_vina_maps(
    project_dir: str,
    *,
    probe_ligand: bool = True,
) -> dict[str, Any]:
    from dockstart_core.vina_maps import validate_active_maps

    return validate_active_maps(project_dir, probe_ligand=probe_ligand)


def validate_config_prerequisites(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    try:
        project = _project_from_dict(loaded["project"], project_path)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CONFIG_INVALID",
            "项目配置格式不完整，无法生成 Vina 配置文件。",
            str(exc),
            "请检查 project.json 中 receptor、ligand、box 和 vina 字段是否完整。",
        )

    receptor_inputs = _active_receptor_inputs(project_path, project)
    if not receptor_inputs.get("ok"):
        return receptor_inputs
    scoring_protocol = _project_scoring_protocol(project)
    protocol_id = _project_protocol_id(project)
    grid_source = _project_grid_source(project)
    uses_vina_maps = scoring_protocol == "vina" and grid_source == "precomputed_maps"
    run_mode = _project_run_mode(project)
    autobox = _project_autobox(project)
    if protocol_id == AD4ZN_PROTOCOL_ID and run_mode != "dock":
        return _error(
            "AD4ZN_RUN_MODE_UNSUPPORTED",
            "AD4Zn beta 只支持单配体全局对接。",
            raw_error=f"run_mode={run_mode}",
            suggestion="请将任务类型切换为全局对接。",
        )
    if uses_vina_maps and receptor_inputs.get("mode") == "flexible":
        return _error(
            "MAPS_FLEXIBLE_RECEPTOR_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 运行只支持刚性受体。",
            suggestion="请切换回刚性受体，或改用 AutoDock4 maps 柔性对接流程。",
        )
    if uses_vina_maps and run_mode != "dock":
        return _error(
            "VINA_MAPS_RUN_MODE_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 当前只支持全局对接。",
            raw_error=f"run_mode={run_mode}",
            suggestion="请将任务类型切换为全局对接，或将网格来源切换回受体。",
        )
    receptor_file = str(receptor_inputs["receptor_file"])
    _, receptor_error = _project_relative_file(project_path, receptor_file, "receptor")
    if receptor_error:
        return _prepared_input_hint(project, project_path, "receptor", receptor_error)
    ligand_path, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        return _prepared_input_hint(project, project_path, "ligand", ligand_error)

    pose_input_attestation = _validate_current_pose_input_attestation(
        project_path,
        project,
        receptor_file=receptor_file,
    )
    if pose_input_attestation["required"] and not pose_input_attestation["valid"]:
        error = pose_input_attestation.get("error") or {}
        return _error(
            str(error.get("code") or "POSE_INPUT_ATTESTATION_INVALID"),
            str(
                error.get("message")
                or "当前输入姿势坐标系确认记录无效。"
            ),
            raw_error=str(error.get("raw_error") or ""),
            suggestion=str(
                error.get("suggestion")
                or "请重新核对受体与配体坐标系并再次确认。"
            ),
        )

    box_required = scoring_protocol == "ad4_maps" or uses_vina_maps or run_mode == "dock" or not autobox
    box_validation = validate_box_params(asdict(project.box))
    if box_required and not box_validation.get("ok"):
        return box_validation

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        return vina_validation
    grid_validation: dict[str, Any] | None = None
    if scoring_protocol != "ad4_maps" and not uses_vina_maps and box_required:
        movable_atom_types: set[str] = set()
        if ligand_path is not None:
            movable_atom_types.update(
                _parse_pdbqt_stats(
                    ligand_path,
                    project.ligand.file,
                    ligand=True,
                )["atom_types"]
            )
        flex_file = str(receptor_inputs.get("flex_file") or "")
        if flex_file:
            flex_path, flex_error = _project_relative_file(project_path, flex_file, "flex")
            if flex_error:
                return flex_error
            if flex_path is not None:
                movable_atom_types.update(
                    _parse_pdbqt_stats(flex_path, flex_file)["atom_types"]
                )
        grid_validation = _validate_vina_grid_resource(
            box_validation.get("box") or asdict(project.box),
            float(vina_validation["vina"]["spacing"]),
            sorted(movable_atom_types),
            bool(vina_validation["vina"]["force_even_voxels"]),
        )
        if not grid_validation.get("ok"):
            return grid_validation
    maps_status: dict[str, Any] | None = None
    vina_maps_status: dict[str, Any] | None = None
    if scoring_protocol == "ad4_maps":
        maps_status = _active_ad4_maps(str(project_path))
        if not maps_status.get("ok") or not maps_status.get("ready"):
            error = maps_status.get("error") or {}
            return _error(
                str(error.get("code") or "MAPS_VALIDATION_FAILED"),
                str(error.get("message") or "AutoDock4 affinity maps 未准备完成。"),
                str(error.get("raw_error") or "；".join(maps_status.get("issues") or [])),
                str(error.get("suggestion") or "请重新生成或导入与当前受体匹配的 maps。"),
            )
        if protocol_id == AD4ZN_PROTOCOL_ID:
            manifest = (
                maps_status.get("manifest")
                if isinstance(maps_status.get("manifest"), dict)
                else {}
            )
            if str(manifest.get("protocol_id") or "") != AD4ZN_PROTOCOL_ID:
                return _error(
                    "AD4ZN_MAPS_PROTOCOL_MISMATCH",
                    "活动 maps 不是当前受体的 AD4Zn beta 专用 maps。",
                    raw_error=str(manifest.get("protocol_id") or ""),
                    suggestion="请在 AD4Zn beta 面板重新生成专用 maps。",
                )
            receptor_record = (
                manifest.get("receptor")
                if isinstance(manifest.get("receptor"), dict)
                else {}
            )
            receptor_file = str(
                receptor_record.get("source_relative_path") or ""
            )
            _, ad4zn_receptor_error = _project_relative_file(
                project_path,
                receptor_file,
                "ad4zn_receptor",
            )
            if ad4zn_receptor_error:
                return ad4zn_receptor_error
    elif uses_vina_maps:
        vina_maps_status = _active_vina_maps(
            str(project_path),
            probe_ligand=True,
        )
        if not vina_maps_status.get("ok") or not vina_maps_status.get("ready"):
            error = vina_maps_status.get("error") or {}
            return _error(
                str(error.get("code") or "VINA_MAPS_VALIDATION_FAILED"),
                str(
                    error.get("message")
                    or "Vina/Vinardo 预计算 maps 未准备完成。"
                ),
                str(
                    error.get("raw_error")
                    or "；".join(vina_maps_status.get("issues") or [])
                ),
                str(
                    error.get("suggestion")
                    or "请重新生成或导入与当前受体、Box、评分函数和配体类型兼容的 maps。"
                ),
            )

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "box": box_validation.get("box") or asdict(project.box),
        "vina": vina_validation["vina"],
        "receptor_file": receptor_file,
        "flex_file": str(receptor_inputs.get("flex_file") or ""),
        "docking_protocol": {
            **copy.deepcopy(
                project.preserved_data.get("docking_protocol")
                if isinstance(
                    project.preserved_data.get("docking_protocol"), dict
                )
                else {}
            ),
            **receptor_inputs,
            **(
                {
                    "pose_input_attestation": copy.deepcopy(
                        pose_input_attestation["attestation"]
                    )
                }
                if pose_input_attestation["required"]
                and pose_input_attestation["valid"]
                else {}
            ),
        },
        "pose_input_attestation": pose_input_attestation,
        "scoring_protocol": scoring_protocol,
        "protocol_id": protocol_id,
        "grid_source": grid_source,
        "run_mode": run_mode,
        "autobox": autobox,
        "box_required": box_required,
        "ad4_maps": maps_status,
        "vina_maps": vina_maps_status,
        "grid_estimate": (grid_validation or {}).get("grid_estimate"),
        "warnings": (
            vina_validation.get("warnings", [])
            + (box_validation.get("warnings", []) if box_required else [])
            + ((grid_validation or {}).get("warnings") or [])
        ),
        "error": None,
    }


def build_vina_config_text(project_dir: str) -> dict[str, Any]:
    prerequisites = validate_config_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    project = _project_from_dict(prerequisites["project"], Path(project_dir).expanduser())
    box = prerequisites["box"]
    vina = prerequisites["vina"]
    scoring_protocol = str(prerequisites.get("scoring_protocol") or "vina")
    grid_source = str(prerequisites.get("grid_source") or "receptor")
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    active_maps_status = (
        prerequisites.get("ad4_maps")
        if scoring_protocol == "ad4_maps"
        else prerequisites.get("vina_maps")
        if uses_vina_maps
        else {}
    )
    if not isinstance(active_maps_status, dict):
        active_maps_status = {}
    run_mode = _normalize_run_mode(prerequisites.get("run_mode"))
    autobox = bool(prerequisites.get("autobox"))
    if scoring_protocol == "ad4_maps":
        lines = [
            f"ligand = {Path(project.ligand.file).as_posix()}",
            "scoring = ad4",
            "",
        ]
    elif uses_vina_maps:
        lines = [
            f"ligand = {Path(project.ligand.file).as_posix()}",
            f"scoring = {vina['scoring']}",
            "",
        ]
    else:
        lines = [
            f"receptor = {Path(prerequisites['receptor_file']).as_posix()}",
            f"ligand = {Path(project.ligand.file).as_posix()}",
            f"scoring = {vina['scoring']}",
            "",
        ]
        if run_mode == "dock" or not autobox:
            lines.extend(
                [
                    f"center_x = {_format_config_number(box['center_x'])}",
                    f"center_y = {_format_config_number(box['center_y'])}",
                    f"center_z = {_format_config_number(box['center_z'])}",
                    "",
                    f"size_x = {_format_config_number(box['size_x'])}",
                    f"size_y = {_format_config_number(box['size_y'])}",
                    f"size_z = {_format_config_number(box['size_z'])}",
                    "",
                ]
            )
    if run_mode == "dock":
        lines.extend(
            [
                f"exhaustiveness = {vina['exhaustiveness']}",
                f"max_evals = {vina['max_evals']}",
                f"num_modes = {vina['num_modes']}",
                f"min_rmsd = {_format_config_number(vina['min_rmsd'])}",
                f"energy_range = {_format_config_number(vina['energy_range'])}",
                f"cpu = {vina['cpu']}",
            ]
        )
    else:
        lines.append(f"cpu = {vina['cpu']}")
    if scoring_protocol != "ad4_maps" and not uses_vina_maps:
        lines.append(f"spacing = {_format_config_number(vina['spacing'])}")
        if (
            run_mode == "score_only"
            and str(
                (prerequisites.get("docking_protocol") or {}).get("mode")
                or "rigid"
            ).strip().lower()
            != "flexible"
            and vina["unbound_energy"] is not None
        ):
            lines.append(
                "unbound_energy = "
                f"{_format_config_number(vina['unbound_energy'])}"
            )
        if vina["no_refine"]:
            lines.append("no_refine = true")
        if vina["force_even_voxels"]:
            lines.append("force_even_voxels = true")
    lines.append(f"verbosity = {vina['verbosity']}")
    if run_mode == "dock" and vina["seed"] is not None:
        lines.append(f"seed = {vina['seed']}")

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "config_file": "configs/vina_config.txt",
        "config_text": "\n".join(lines) + "\n",
        "scoring_protocol": scoring_protocol,
        "grid_source": grid_source,
        "run_mode": run_mode,
        "autobox": autobox,
        "maps_prefix": str(active_maps_status.get("maps_prefix") or ""),
        "warnings": prerequisites.get("warnings", []),
        "message": "Vina 配置预览已生成。",
        "error": None,
    }


def get_vina_config_preview(project_dir: str) -> dict[str, Any]:
    return build_vina_config_text(project_dir)


def generate_vina_config(project_dir: str) -> dict[str, Any]:
    preview = build_vina_config_text(project_dir)
    if not preview.get("ok"):
        return preview

    try:
        project = _project_from_dict(preview["project"], Path(project_dir).expanduser())
        config_relative = "configs/vina_config.txt"
        config_path = Path(project.project_dir).expanduser() / "configs" / "vina_config.txt"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(config_path, preview["config_text"])

        generated_at = _now_iso()
        project.config.vina_config_file = config_relative
        project.config.generated_at = generated_at
        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "config_file": config_relative,
            "config_text": preview["config_text"],
            "warnings": preview.get("warnings", []),
            "message": "vina_config.txt 已生成。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_CONFIG_WRITE_ERROR",
            "写入 vina_config.txt 时发生错误。",
            str(exc),
            "请确认项目 configs 目录可写。",
        )


def _run_check(
    key: str,
    name: str,
    status: str,
    message: str,
    path: str = "",
    version: str = "",
    raw_error: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "status": status,
        "message": message,
        "path": path,
        "version": version,
        "raw_error": raw_error,
    }


def _run_error(
    code: str,
    message: str,
    checks: list[dict[str, Any]],
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    payload = _error(code, message, raw_error, suggestion)
    payload["checks"] = checks
    payload["warnings"] = []
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ad4zn_prepared_receptor_issue(path: Path) -> str:
    """Return a concise integrity issue for a frozen AD4Zn TZ receptor."""

    zinc_count = 0
    tz_count = 0
    nonzero_zinc_charges: list[str] = []
    nonzero_tz_charges: list[str] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(),
            start=1,
        ):
            if not line.startswith(("ATOM", "HETATM")):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            atom_type = fields[-1].upper()
            if atom_type == "TZ":
                tz_count += 1
                try:
                    charge = float(fields[-2])
                except (IndexError, ValueError):
                    nonzero_tz_charges.append(
                        f"line {line_number}: charge unreadable"
                    )
                    continue
                if not math.isfinite(charge) or abs(charge) > 1e-6:
                    nonzero_tz_charges.append(
                        f"line {line_number}: {charge}"
                    )
                continue
            if atom_type != "ZN":
                continue
            zinc_count += 1
            try:
                charge = float(fields[-2])
            except (IndexError, ValueError):
                nonzero_zinc_charges.append(
                    f"line {line_number}: charge unreadable"
                )
                continue
            if not math.isfinite(charge) or abs(charge) > 1e-6:
                nonzero_zinc_charges.append(f"line {line_number}: {charge}")
    except (OSError, UnicodeError) as exc:
        return f"TZ 受体无法读取：{exc}"
    if zinc_count != 1:
        return f"TZ 受体必须恰好包含一个 ZN；当前为 {zinc_count}。"
    if tz_count != 1:
        return (
            "TZ 受体必须恰好包含一个 TZ；"
            f"当前为 {tz_count}，AD4Zn 不允许回退为普通 AutoDock4。"
        )
    if nonzero_zinc_charges:
        return "TZ 受体中的 Zn 电荷不是 0.000：" + ", ".join(
            nonzero_zinc_charges
        )
    if nonzero_tz_charges:
        return "TZ 受体中的 TZ 电荷不是 0.000：" + ", ".join(
            nonzero_tz_charges
        )
    return ""


def _ad4zn_parameter_reference_issue(path: Path) -> str:
    """Require the frozen parameter asset to be the pinned official profile."""

    try:
        from dockstart_core.ad4zn import validate_parameter_file  # noqa: PLC0415

        validation = validate_parameter_file(path)
    except Exception as exc:  # noqa: BLE001 - converted to an audit issue.
        return f"无法复核冻结的 AD4Zn.dat：{exc}"
    if not validation.get("ok"):
        error = (
            validation.get("error")
            if isinstance(validation.get("error"), dict)
            else {}
        )
        return str(
            error.get("message")
            or error.get("title")
            or "冻结的 AD4Zn.dat 未通过官方参数身份校验。"
        )
    if (
        validation.get("matches_reference_sha256") is not True
        or str(validation.get("canonical_sha256") or "").lower()
        != str(validation.get("reference_sha256") or "").lower()
    ):
        return (
            "冻结的 AD4Zn.dat canonical LF SHA256 "
            "与固定的 AutoDock Vina v1.2.7 上游参考不一致。"
        )
    return ""


def _ad4zn_frozen_box_coverage(
    receptor_path: Path,
    frozen_manifest: Any,
) -> tuple[dict[str, Any] | None, str]:
    """Recompute the frozen ZN/TZ requested-Box and effective-grid contract."""

    if not isinstance(frozen_manifest, dict):
        return None, "AD4Zn maps manifest 不是 JSON 对象。"
    grid = (
        frozen_manifest.get("grid")
        if isinstance(frozen_manifest.get("grid"), dict)
        else {}
    )
    requested_box = (
        grid.get("requested_box")
        if isinstance(grid.get("requested_box"), dict)
        else {}
    )
    requested_center = (
        requested_box.get("center")
        if isinstance(requested_box.get("center"), dict)
        else {}
    )
    requested_size = (
        requested_box.get("size")
        if isinstance(requested_box.get("size"), dict)
        else {}
    )
    grid_center = (
        grid.get("center")
        if isinstance(grid.get("center"), dict)
        else {}
    )
    grid_points = (
        grid.get("grid_points")
        if isinstance(grid.get("grid_points"), dict)
        else {}
    )
    actual_size = (
        grid.get("actual_size")
        if isinstance(grid.get("actual_size"), dict)
        else {}
    )
    ad4zn = (
        frozen_manifest.get("ad4zn")
        if isinstance(frozen_manifest.get("ad4zn"), dict)
        else {}
    )
    stored_coverage = (
        ad4zn.get("box_coverage")
        if isinstance(ad4zn.get("box_coverage"), dict)
        else None
    )
    stored_coverage_sha256 = str(
        ad4zn.get("box_coverage_sha256") or ""
    ).lower()
    if stored_coverage is None or not SHA256_PATTERN.fullmatch(
        stored_coverage_sha256
    ):
        return (
            None,
            "AD4Zn maps manifest 缺少现代 ZN/TZ Box 覆盖记录；"
            "旧 maps 必须重新生成后才能运行。",
        )
    try:
        from dockstart_core.autogrid import (  # noqa: PLC0415
            GRID_GEOMETRY_TOLERANCE_ANGSTROM,
            compute_ad4zn_box_coverage,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an audit issue.
        return None, f"无法加载 AD4Zn Box 覆盖校验器：{exc}"
    try:
        box = {
            **{
                f"center_{axis}": float(requested_center[axis])
                for axis in ("x", "y", "z")
            },
            **{
                f"size_{axis}": float(requested_size[axis])
                for axis in ("x", "y", "z")
            },
        }
        points = [int(grid_points[axis]) for axis in ("x", "y", "z")]
        spacing = float(grid["spacing"])
        expected_actual_size = {
            axis: points[index] * spacing
            for index, axis in enumerate(("x", "y", "z"))
        }
        for axis in ("x", "y", "z"):
            if not math.isclose(
                float(grid_center[axis]),
                box[f"center_{axis}"],
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                return None, "AD4Zn 请求 Box 中心与实际网格中心不一致。"
            if not math.isclose(
                float(actual_size[axis]),
                expected_actual_size[axis],
                rel_tol=0.0,
                # AutoGrid manifests intentionally serialize actual_size at
                # six decimal places.  Use the same tolerance as active-map
                # validation so a frozen run neither rejects a valid manifest
                # nor silently applies a looser geometry contract.
                abs_tol=GRID_GEOMETRY_TOLERANCE_ANGSTROM,
            ):
                return None, "AD4Zn 实际网格尺寸与 npts × spacing 不一致。"
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"AD4Zn maps manifest 的 Box/网格几何无效：{exc}"

    try:
        recomputed = compute_ad4zn_box_coverage(
            receptor_path,
            box,
            points,
            spacing,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an audit issue.
        return None, f"无法重算冻结的 ZN/TZ Box 覆盖：{exc}"
    if not recomputed.get("ok"):
        error = (
            recomputed.get("error")
            if isinstance(recomputed.get("error"), dict)
            else {}
        )
        return None, str(
            error.get("message")
            or "冻结的 ZN/TZ 坐标或 Box/网格覆盖无效。"
        )
    coverage = recomputed.get("box_coverage")
    coverage_sha256 = str(
        recomputed.get("box_coverage_sha256") or ""
    ).lower()
    if (
        not isinstance(coverage, dict)
        or coverage != stored_coverage
        or coverage_sha256 != stored_coverage_sha256
    ):
        return None, "AD4Zn ZN/TZ Box 覆盖记录与冻结输入重算结果不一致。"
    if (
        coverage.get("all_zn_tz_inside_requested_box") is not True
        or coverage.get("all_zn_tz_inside_effective_grid") is not True
    ):
        return None, "ZN 与 TZ 未同时位于请求 Box 和实际 AutoGrid 网格内。"
    return copy.deepcopy(coverage), ""


def _hydrated_frozen_grid_coverage(
    frozen_manifest: Any,
    expected_box: Any = None,
    expected_grid: Any = None,
) -> tuple[dict[str, Any] | None, str]:
    """Recompute a frozen hydrated requested-Box/effective-grid contract."""

    if not isinstance(frozen_manifest, dict):
        return None, "水合 maps manifest 不是 JSON 对象。"
    manifest_box = (
        frozen_manifest.get("box")
        if isinstance(frozen_manifest.get("box"), dict)
        else {}
    )
    grid = (
        frozen_manifest.get("grid")
        if isinstance(frozen_manifest.get("grid"), dict)
        else {}
    )
    stored_coverage = (
        frozen_manifest.get("grid_coverage")
        if isinstance(frozen_manifest.get("grid_coverage"), dict)
        else None
    )
    stored_sha256 = str(
        frozen_manifest.get("grid_coverage_sha256") or ""
    ).lower()
    if (
        not manifest_box
        or stored_coverage is None
        or not SHA256_PATTERN.fullmatch(stored_sha256)
    ):
        return (
            None,
            "水合 maps manifest 缺少现代请求 Box/实际网格覆盖记录；"
            "旧 maps 必须重新生成后才能运行。",
        )
    try:
        from dockstart_core.autogrid import (  # noqa: PLC0415
            GRID_GEOMETRY_TOLERANCE_ANGSTROM,
            compute_requested_box_grid_coverage,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an audit issue.
        return None, f"无法加载水合 Box/网格覆盖校验器：{exc}"

    axes = ("x", "y", "z")

    def normalize_box(value: Any) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            raise TypeError("Box 记录必须是 JSON 对象。")
        normalized: dict[str, dict[str, float]] = {
            "center": {},
            "size": {},
        }
        for kind in ("center", "size"):
            nested = (
                value.get(kind)
                if isinstance(value.get(kind), dict)
                else {}
            )
            for axis in axes:
                raw = (
                    nested.get(axis)
                    if axis in nested
                    else value.get(f"{kind}_{axis}")
                )
                normalized[kind][axis] = float(raw)
        return normalized

    def same_number(left: Any, right: Any) -> bool:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=GRID_GEOMETRY_TOLERANCE_ANGSTROM,
        )

    try:
        frozen_box = normalize_box(manifest_box)
        required_box = (
            normalize_box(expected_box)
            if expected_box is not None
            else frozen_box
        )
        if any(
            not same_number(
                frozen_box[kind][axis],
                required_box[kind][axis],
            )
            for kind in ("center", "size")
            for axis in axes
        ):
            return None, "水合 maps 的请求 Box 与冻结运行 Box 不一致。"

        requested_box = normalize_box(
            grid.get("requested_box")
            if isinstance(grid.get("requested_box"), dict)
            else {}
        )
        grid_center = (
            grid.get("center")
            if isinstance(grid.get("center"), dict)
            else {}
        )
        grid_points = (
            grid.get("grid_points")
            if isinstance(grid.get("grid_points"), dict)
            else {}
        )
        actual_size = (
            grid.get("actual_size")
            if isinstance(grid.get("actual_size"), dict)
            else {}
        )
        if any(
            not same_number(
                requested_box[kind][axis],
                required_box[kind][axis],
            )
            for kind in ("center", "size")
            for axis in axes
        ):
            return (
                None,
                "水合 maps 的 grid.requested_box 与冻结运行 Box 不一致。",
            )
        if any(
            not same_number(
                grid_center[axis],
                required_box["center"][axis],
            )
            for axis in axes
        ):
            return None, "水合 maps 的实际网格中心与请求 Box 中心不一致。"
        points = [grid_points[axis] for axis in axes]
        spacing = grid["spacing"]

        if expected_grid is not None:
            if not isinstance(expected_grid, dict):
                return None, "冻结运行的水合网格记录格式无效。"
            expected_requested_box = normalize_box(
                expected_grid.get("requested_box")
                if isinstance(expected_grid.get("requested_box"), dict)
                else {}
            )
            expected_center = (
                expected_grid.get("center")
                if isinstance(expected_grid.get("center"), dict)
                else {}
            )
            expected_points = (
                expected_grid.get("grid_points")
                if isinstance(expected_grid.get("grid_points"), dict)
                else {}
            )
            expected_actual_size = (
                expected_grid.get("actual_size")
                if isinstance(expected_grid.get("actual_size"), dict)
                else {}
            )
            if (
                not same_number(spacing, expected_grid["spacing"])
                or any(
                    int(grid_points[axis])
                    != int(expected_points[axis])
                    for axis in axes
                )
                or any(
                    not same_number(
                        requested_box[kind][axis],
                        expected_requested_box[kind][axis],
                    )
                    for kind in ("center", "size")
                    for axis in axes
                )
                or any(
                    not same_number(
                        grid_center[axis],
                        expected_center[axis],
                    )
                    for axis in axes
                )
                or any(
                    not same_number(
                        actual_size[axis],
                        expected_actual_size[axis],
                    )
                    for axis in axes
                )
            ):
                return (
                    None,
                    "水合 maps 的 grid 与冻结运行网格快照不一致。",
                )
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"水合 maps manifest 的 Box/网格几何无效：{exc}"

    try:
        recomputed = compute_requested_box_grid_coverage(
            required_box,
            points,
            spacing,
        )
    except Exception as exc:  # noqa: BLE001 - converted to an audit issue.
        return None, f"无法重算冻结的水合 Box/网格覆盖：{exc}"
    if not recomputed.get("ok"):
        error = (
            recomputed.get("error")
            if isinstance(recomputed.get("error"), dict)
            else {}
        )
        return None, str(
            error.get("message")
            or "冻结的水合 Box/网格覆盖无效。"
        )
    coverage = recomputed.get("coverage")
    coverage_sha256 = str(
        recomputed.get("coverage_sha256") or ""
    ).lower()
    if (
        not isinstance(coverage, dict)
        or coverage != stored_coverage
        or coverage_sha256 != stored_sha256
    ):
        return None, "水合 Box/网格覆盖记录与冻结几何重算结果不一致。"
    if coverage.get("covers_requested_box") is not True:
        return None, "实际 AutoGrid 网格没有完整覆盖请求 Box。"
    try:
        effective_size = coverage["effective_grid_size_angstrom"]
        if any(
            not same_number(
                actual_size[axis],
                effective_size[axis],
            )
            for axis in axes
        ):
            return None, "水合实际网格尺寸与 npts × spacing 不一致。"
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"水合实际网格尺寸记录无效：{exc}"
    return copy.deepcopy(coverage), ""


def _ad4zn_gpf_missing_lines(path: Path) -> list[str]:
    try:
        normalized_lines = {
            " ".join(line.strip().split())
            for line in path.read_text(
                encoding="utf-8",
                errors="strict",
            ).splitlines()
            if line.strip()
        }
    except (OSError, UnicodeError):
        return list(AD4ZN_GPF_REQUIRED_LINES)
    return [
        required
        for required in AD4ZN_GPF_REQUIRED_LINES
        if " ".join(required.split()) not in normalized_lines
    ]


def _autogrid_version_supports_ad4zn(version: Any) -> bool:
    parts = [int(value) for value in re.findall(r"\d+", str(version or ""))]
    return len(parts) >= 3 and tuple(parts[:3]) >= (4, 2, 7)


def _hash_snapshot(path: Path, relative_path: str = "") -> dict[str, Any]:
    """Return an auditable file snapshot, including hashes for empty files."""

    hash_error = ""
    try:
        exists = path.is_file()
        size_bytes = path.stat().st_size if exists else 0
        sha256 = _sha256_file(path) if exists else ""
    except OSError as exc:
        exists = False
        size_bytes = 0
        sha256 = ""
        hash_error = str(exc)
    return {
        "relative_path": Path(relative_path).as_posix() if relative_path else "",
        "absolute_path": str(path),
        "exists": exists,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "hash_error": hash_error,
    }


def _vina_raw_output_relative_path(output_file: str) -> str:
    output_path = Path(output_file)
    return output_path.with_name(
        f"{output_path.stem}.vina_raw{output_path.suffix}",
    ).as_posix()


def _unexpected_pdbqt_control_bytes(payload: bytes) -> dict[int, int]:
    counts: dict[int, int] = {}
    for value in payload:
        if value == 0 or (value < 32 and value not in {9, 10, 13}) or value == 127:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _unexpected_pdbqt_control_codepoints(text: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for character in text:
        value = ord(character)
        if value == 0 or (value < 32 and value not in {9, 10, 13}) or (
            127 <= value <= 159
        ):
            counts[value] = counts.get(value, 0) + 1
    return counts


def _recognized_vina_nul_padding_blocks(
    payload: bytes,
    *,
    allow_multiple_ligand_member_boundary: bool = False,
    allow_flexible_residue_boundary: bool = False,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Classify NUL runs without treating arbitrary binary data as PDBQT.

    AutoDock Vina 1.2.7 on Windows has been observed to emit a contiguous NUL
    run after a model's ``TORSDOF`` line when its ligand input uses CRLF line
    endings.  Standard single-ligand output only accepts that placement before
    ``ENDMDL``.  The simultaneous multi-ligand caller may additionally accept
    the same boundary before the next member's optional ``REMARK`` records and
    ``ROOT``.  A flexible-receptor run may accept the boundary before the
    first syntactically valid ``BEGIN_RES`` record.  Arbitrary NUL bytes remain
    fail-closed.
    """

    recognized: list[tuple[int, int]] = []
    rejected: list[tuple[int, int]] = []
    for match in re.finditer(rb"\x00+", payload):
        prefix = payload[: match.start()]
        suffix = payload[match.end() :]
        follows_torsdof = re.search(
            rb"(?:^|[\r\n])TORSDOF[ \t]+\d+[ \t]*(?:\r\n|\n|\r)$",
            prefix,
        )
        precedes_endmdl = re.match(
            rb"(?:(?:\r\n|\n|\r))?ENDMDL[ \t]*(?:\r\n|\n|\r|$)",
            suffix,
        )
        precedes_next_member = (
            re.match(
                rb"(?:(?:\r\n|\n|\r))?"
                rb"(?:REMARK[^\r\n]*(?:\r\n|\n|\r))*"
                rb"ROOT[ \t]*(?:\r\n|\n|\r)",
                suffix,
            )
            if allow_multiple_ligand_member_boundary
            else None
        )
        precedes_flexible_residue = (
            re.match(
                rb"(?:(?:\r\n|\n|\r))?"
                rb"BEGIN_RES[ \t]+"
                rb"[A-Za-z0-9][A-Za-z0-9_+\-]{0,7}[ \t]+"
                rb"(?:(?:[A-Za-z0-9_.\-]{1,8})[ \t]+)?"
                rb"-?\d+[A-Za-z0-9]?[ \t]*"
                rb"(?:\r\n|\n|\r)",
                suffix,
            )
            if allow_flexible_residue_boundary
            else None
        )
        target = (
            recognized
            if follows_torsdof
            and (
                precedes_endmdl
                or precedes_next_member
                or precedes_flexible_residue
            )
            else rejected
        )
        target.append((match.start(), match.end()))
    return recognized, rejected


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _normalize_vina_pdbqt_output(
    output_path: Path,
    output_file: str,
    *,
    allow_multiple_ligand_member_boundary: bool = False,
    allow_flexible_residue_boundary: bool = False,
) -> dict[str, Any]:
    """Preserve Vina's bytes and publish a text-safe PDBQT when possible."""

    output_relative = Path(output_file).as_posix()
    normalization_method = (
        "torsdof_flexible_residue_or_endmdl_nul_padding_v3"
        if allow_flexible_residue_boundary
        else "torsdof_member_or_endmdl_nul_padding_v2"
        if allow_multiple_ligand_member_boundary
        else VINA_OUTPUT_NORMALIZATION_METHOD
    )
    try:
        source = output_path.read_bytes()
    except OSError as exc:
        error = {
            "code": "VINA_OUTPUT_READ_ERROR",
            "message": "无法读取 AutoDock Vina 生成的 PDBQT 输出。",
            "raw_error": str(exc),
            "suggestion": "请保留本次 run，并检查输出文件权限后重新准备新 run。",
        }
        return {
            "ok": False,
            "record": {
                "schema_version": VINA_OUTPUT_NORMALIZATION_SCHEMA_VERSION,
                "status": "failed",
                "method": normalization_method,
                "source_file": output_relative,
                "normalized_file": output_relative,
                "changed": False,
                "error": copy.deepcopy(error),
            },
            "artifacts": {},
            "warning": "",
            "error": error,
        }

    source_sha256 = hashlib.sha256(source).hexdigest()
    source_text = ""
    utf8_error = ""
    try:
        source_text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        utf8_error = str(exc)
    control_counts = _unexpected_pdbqt_control_bytes(source)
    control_codepoints = (
        _unexpected_pdbqt_control_codepoints(source_text)
        if not utf8_error
        else {}
    )
    nul_count = control_counts.get(0, 0)
    recognized, rejected = _recognized_vina_nul_padding_blocks(
        source,
        allow_multiple_ligand_member_boundary=(
            allow_multiple_ligand_member_boundary
        ),
        allow_flexible_residue_boundary=(
            allow_flexible_residue_boundary
        ),
    )
    unexpected_counts = dict(control_counts)
    unexpected_codepoints = dict(control_codepoints)
    if recognized and not rejected:
        unexpected_counts.pop(0, None)
        unexpected_codepoints.pop(0, None)

    base_record: dict[str, Any] = {
        "schema_version": VINA_OUTPUT_NORMALIZATION_SCHEMA_VERSION,
        "status": "not_required",
        "method": normalization_method,
        "source_file": output_relative,
        "raw_output_file": "",
        "normalized_file": output_relative,
        "changed": False,
        "source_size_bytes": len(source),
        "source_sha256": source_sha256,
        "normalized_size_bytes": len(source),
        "normalized_sha256": source_sha256,
        "utf8_valid": not utf8_error,
        "utf8_error": utf8_error,
        "nul_bytes_detected": nul_count,
        "nul_bytes_removed": 0,
        "recognized_padding_blocks": 0,
        "recognized_padding_lengths": [],
        "unexpected_control_bytes": [
            {"byte": value, "hex": f"0x{value:02x}", "count": count}
            for value, count in sorted(unexpected_counts.items())
        ],
        "unexpected_control_codepoints": [
            {"codepoint": value, "unicode": f"U+{value:04X}", "count": count}
            for value, count in sorted(unexpected_codepoints.items())
        ],
        "scientific_content_policy": (
            (
                "仅允许移除每个配体 TORSDOF 与下一成员 REMARK/ROOT "
                "或 ENDMDL 之间的连续 NUL 填充；"
                if allow_multiple_ligand_member_boundary
                else (
                    "仅允许移除配体 TORSDOF 与首个合法 BEGIN_RES "
                    "或 ENDMDL 之间的连续 NUL 填充；"
                    if allow_flexible_residue_boundary
                    else "仅允许移除 TORSDOF 与紧随其后的 ENDMDL 之间的连续 NUL 填充；"
                )
            )
            + "不改写坐标、原子、构象、评分备注或行尾。"
        ),
    }
    if not control_counts and not unexpected_codepoints and not utf8_error:
        return {
            "ok": True,
            "record": base_record,
            "artifacts": {},
            "warning": "",
            "error": None,
        }

    raw_relative = _vina_raw_output_relative_path(output_relative)
    raw_path = output_path.with_name(Path(raw_relative).name)
    base_record["source_file"] = raw_relative
    base_record["raw_output_file"] = raw_relative
    raw_created = False
    try:
        with raw_path.open("xb") as handle:
            raw_created = True
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        raw_snapshot = _hash_snapshot(raw_path, raw_relative)
        if (
            raw_snapshot.get("sha256") != source_sha256
            or raw_snapshot.get("size_bytes") != len(source)
        ):
            raise OSError("原始 Vina 输出副本与执行后读取的字节不一致。")
    except OSError as exc:
        if raw_created:
            try:
                raw_path.unlink(missing_ok=True)
            except OSError:
                pass
        error = {
            "code": "VINA_OUTPUT_RAW_ARCHIVE_ERROR",
            "message": "无法保留 AutoDock Vina 的原始 PDBQT 输出，已拒绝标准化。",
            "raw_error": str(exc),
            "suggestion": "请保留当前 out.pdbqt，排除目录占用或权限问题后重新准备新 run。",
        }
        base_record.update(
            {
                "status": "failed",
                "source_file": output_relative,
                "raw_output_file": "",
                "normalized_file": "",
                "normalized_size_bytes": 0,
                "normalized_sha256": "",
                "error": copy.deepcopy(error),
            }
        )
        return {
            "ok": False,
            "record": base_record,
            "artifacts": {},
            "warning": "",
            "error": error,
        }

    artifacts = {"out_vina_raw": raw_snapshot}
    if (
        unexpected_counts
        or unexpected_codepoints
        or rejected
        or nul_count == 0
        or utf8_error
    ):
        rejected_nul_count = sum(end - start for start, end in rejected)
        error = {
            "code": (
                "VINA_OUTPUT_TEXT_ENCODING_INVALID"
                if utf8_error
                else "VINA_OUTPUT_BINARY_CONTROL_CHARACTER"
            ),
            "message": (
                "Vina 输出不是有效的 UTF-8 文本，结果已拒绝。"
                if utf8_error
                else "Vina 输出包含无法安全解释的二进制控制字符，结果已拒绝。"
            ),
            "raw_error": (
                f"control_bytes={base_record['unexpected_control_bytes']}; "
                f"control_codepoints={base_record['unexpected_control_codepoints']}; "
                f"rejected_nul_bytes={rejected_nul_count}; "
                f"utf8_error={utf8_error}"
            ),
            "suggestion": (
                "原始输出已保留为 *.vina_raw.pdbqt。请检查 Vina 版本、输入文件和磁盘状态，"
                "不要把该文件作为标准 PDBQT 继续分析。"
            ),
        }
        base_record.update(
            {
                "status": "failed",
                "normalized_file": "",
                "normalized_size_bytes": 0,
                "normalized_sha256": "",
                "recognized_padding_blocks": len(recognized),
                "recognized_padding_lengths": [
                    end - start for start, end in recognized
                ],
                "error": copy.deepcopy(error),
            }
        )
        try:
            output_path.unlink(missing_ok=True)
        except OSError as exc:
            error["raw_error"] = (
                f"{error['raw_error']}; polluted_output_remove_error={exc}"
            )
            base_record["error"] = copy.deepcopy(error)
        return {
            "ok": False,
            "record": base_record,
            "artifacts": artifacts,
            "warning": "",
            "error": error,
        }

    normalized_parts: list[bytes] = []
    cursor = 0
    for start, end in recognized:
        normalized_parts.append(source[cursor:start])
        cursor = end
    normalized_parts.append(source[cursor:])
    normalized = b"".join(normalized_parts)
    try:
        normalized.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        error = {
            "code": "VINA_OUTPUT_TEXT_ENCODING_INVALID",
            "message": "移除已识别 NUL 填充后，Vina 输出仍不是有效的 UTF-8 文本。",
            "raw_error": str(exc),
            "suggestion": (
                "原始输出已保留为 *.vina_raw.pdbqt。请检查 Vina 版本和输入编码后重新准备新 run。"
            ),
        }
        base_record.update(
            {
                "status": "failed",
                "normalized_file": "",
                "normalized_size_bytes": 0,
                "normalized_sha256": "",
                "error": copy.deepcopy(error),
            }
        )
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "ok": False,
            "record": base_record,
            "artifacts": artifacts,
            "warning": "",
            "error": error,
        }
    normalized_sha256 = hashlib.sha256(normalized).hexdigest()
    try:
        _write_bytes_atomically(output_path, normalized)
        published = output_path.read_bytes()
        if (
            len(published) != len(normalized)
            or hashlib.sha256(published).hexdigest() != normalized_sha256
        ):
            raise OSError("标准化 PDBQT 写入后校验失败。")
    except OSError as exc:
        error = {
            "code": "VINA_OUTPUT_NORMALIZATION_WRITE_ERROR",
            "message": "原始 Vina 输出已保留，但无法发布标准化 PDBQT。",
            "raw_error": str(exc),
            "suggestion": (
                "请保留 *.vina_raw.pdbqt 作为执行证据，排除目录占用或权限问题后重新准备新 run。"
            ),
        }
        base_record.update(
            {
                "status": "failed",
                "normalized_file": "",
                "normalized_size_bytes": 0,
                "normalized_sha256": "",
                "error": copy.deepcopy(error),
            }
        )
        try:
            output_path.unlink(missing_ok=True)
        except OSError as remove_exc:
            error["raw_error"] = (
                f"{error['raw_error']}; unpublished_output_remove_error={remove_exc}"
            )
            base_record["error"] = copy.deepcopy(error)
        return {
            "ok": False,
            "record": base_record,
            "artifacts": artifacts,
            "warning": "",
            "error": error,
        }

    removed = sum(end - start for start, end in recognized)
    warning = (
        f"检测到并移除了 {removed} 个位于 TORSDOF 结构边界的 NUL 填充字节；"
        f"Vina 原始输出已保留在 {raw_relative}。"
    )
    base_record.update(
        {
            "status": "normalized",
            "changed": True,
            "normalized_size_bytes": len(normalized),
            "normalized_sha256": normalized_sha256,
            "nul_bytes_removed": removed,
            "recognized_padding_blocks": len(recognized),
            "recognized_padding_lengths": [
                end - start for start, end in recognized
            ],
        }
    )
    return {
        "ok": True,
        "record": base_record,
        "artifacts": artifacts,
        "warning": warning,
        "error": None,
    }


def _tool_hash_snapshot(path_value: str) -> dict[str, Any]:
    path = Path(path_value).expanduser() if path_value else Path()
    try:
        is_file = bool(path_value and path.is_file())
    except OSError:
        is_file = False
    if is_file:
        try:
            path = path.resolve(strict=True)
        except OSError:
            pass
        return _hash_snapshot(path)
    return {
        "relative_path": "",
        "absolute_path": str(path_value or ""),
        "exists": False,
        "size_bytes": 0,
        "sha256": "",
    }


def _with_artifact_hashes(
    metadata: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    artifacts = dict(metadata.get("artifacts") or {}) if isinstance(metadata.get("artifacts"), dict) else {}
    for key, snapshot in snapshots.items():
        existing = artifacts.get(key) if isinstance(artifacts.get(key), dict) else {}
        # A verified hash is historical provenance.  Never replace it during a
        # later recovery/analysis pass with bytes observed at a different time.
        if re.fullmatch(r"[0-9a-fA-F]{64}", str(existing.get("sha256") or "")):
            continue
        artifacts[key] = copy.deepcopy(snapshot)
    metadata["artifacts"] = artifacts
    metadata["artifact_sha256"] = {
        key: str(value.get("sha256") or "")
        for key, value in artifacts.items()
        if isinstance(value, dict)
    }
    return metadata


def _parse_pdbqt_stats(path: Path, relative_path: str, *, ligand: bool = False) -> dict[str, Any]:
    """Return lightweight, deterministic PDBQT facts without chemistry claims."""

    atom_count = 0
    coordinate_count = 0
    coordinate_min: list[float] | None = None
    coordinate_max: list[float] | None = None
    chains: set[str] = set()
    atom_types: set[str] = set()
    torsdof: int | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            record = line[:6].strip().upper()
            if record in {"ATOM", "HETATM"}:
                atom_count += 1
                chain = line[21:22].strip() if len(line) > 21 else ""
                if chain:
                    chains.add(chain)
                parts = line.split()
                if parts:
                    atom_type = parts[-1].strip()
                    if atom_type and len(atom_type) <= 4:
                        atom_types.add(atom_type)
                try:
                    coordinates = [float(line[30:38]), float(line[38:46]), float(line[46:54])]
                except (TypeError, ValueError):
                    coordinates = []
                if len(coordinates) == 3 and all(math.isfinite(value) for value in coordinates):
                    coordinate_count += 1
                    if coordinate_min is None or coordinate_max is None:
                        coordinate_min = coordinates.copy()
                        coordinate_max = coordinates.copy()
                    else:
                        coordinate_min = [min(current, value) for current, value in zip(coordinate_min, coordinates)]
                        coordinate_max = [max(current, value) for current, value in zip(coordinate_max, coordinates)]
            if ligand and line.lstrip().upper().startswith("TORSDOF"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        torsdof = int(parts[1])
                    except ValueError:
                        torsdof = None

    coordinate_bounds = None
    coordinate_center = None
    if coordinate_min is not None and coordinate_max is not None:
        coordinate_bounds = {
            "min": dict(zip(("x", "y", "z"), coordinate_min)),
            "max": dict(zip(("x", "y", "z"), coordinate_max)),
        }
        coordinate_center = {
            axis: round((minimum + maximum) / 2, 3)
            for axis, minimum, maximum in zip(("x", "y", "z"), coordinate_min, coordinate_max)
        }

    return {
        "relative_path": Path(relative_path).as_posix(),
        "absolute_path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "atom_count": atom_count,
        "coordinate_count": coordinate_count,
        "coordinate_bounds": coordinate_bounds,
        "coordinate_center": coordinate_center,
        "chains": sorted(chains),
        "atom_types": sorted(atom_types),
        "torsdof": torsdof if ligand else None,
    }


def _memory_bytes() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("avail_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.total_phys)
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        return int(page_size * page_count)
    except (AttributeError, OSError, ValueError):
        return None


def _system_snapshot() -> dict[str, Any]:
    cpu_count = os.cpu_count() or 1
    release = platform.release()
    if sys.platform == "win32":
        try:
            windows_version = sys.getwindowsversion()
            if windows_version.build >= 22000:
                release = "11"
        except (AttributeError, OSError):
            pass
    identity = "|".join(
        [platform.system(), release, platform.machine(), platform.processor(), str(cpu_count)],
    )
    return {
        "system": platform.system(),
        "release": release,
        "machine": platform.machine(),
        "cpu_count": cpu_count,
        "memory_bytes": _memory_bytes(),
        "fingerprint": hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:16],
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _duration_seconds(started_at: Any, finished_at: Any = None) -> float | None:
    started = _parse_iso_datetime(started_at)
    finished = _parse_iso_datetime(finished_at) if finished_at else datetime.now(UTC)
    if not started or not finished:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return max(0.0, round((finished - started).total_seconds(), 3))


def _verify_metadata_process(
    metadata: dict[str, Any],
    *,
    pid_key: str,
    executable_key: str,
    identity_key: str,
) -> dict[str, object]:
    pid = metadata.get(pid_key)
    executable = str(metadata.get(executable_key) or "")
    identity = metadata.get(identity_key) if isinstance(metadata.get(identity_key), dict) else None
    if not isinstance(pid, int) or pid <= 0 or not executable or identity is None:
        return {"ok": False, "running": False, "message": "缺少可验证的进程身份。"}
    return vina_adapter.verify_process_identity(pid, executable, identity)


def _collect_run_history(project_path: Path, project: DockStartProject) -> list[dict[str, Any]]:
    run_ids: set[str] = {
        str(item.get("run_id"))
        for item in project.runs
        if isinstance(item, dict) and RUN_ID_PATTERN.match(str(item.get("run_id", "")))
    }
    runs_dir = project_path / "runs"
    if runs_dir.is_dir():
        run_ids.update(child.name for child in runs_dir.iterdir() if child.is_dir() and RUN_ID_PATTERN.match(child.name))

    history: list[dict[str, Any]] = []
    project_summaries = {
        str(item.get("run_id")): item for item in project.runs if isinstance(item, dict) and item.get("run_id")
    }
    for run_id in sorted(run_ids, reverse=True):
        summary = dict(project_summaries.get(run_id, {}))
        metadata_path = runs_dir / run_id / "metadata.json"
        metadata: dict[str, Any] = {}
        try:
            candidate = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                metadata = candidate
        except (OSError, json.JSONDecodeError):
            pass
        combined = {**summary, **metadata}
        scoring_protocol = str(combined.get("scoring_protocol") or "")
        if not scoring_protocol:
            protocol_snapshot = combined.get("docking_protocol")
            scoring_protocol = (
                "ad4_maps"
                if isinstance(protocol_snapshot, dict)
                and str(protocol_snapshot.get("protocol_id") or "") == "ad4_maps"
                else "vina"
            )
        duration = combined.get("duration_seconds")
        if not isinstance(duration, (int, float)):
            duration = _duration_seconds(combined.get("started_at"), combined.get("finished_at"))
        history.append(
            {
                "run_id": run_id,
                "status": str(combined.get("status") or "unknown"),
                "created_at": combined.get("created_at"),
                "started_at": combined.get("started_at"),
                "finished_at": combined.get("finished_at"),
                "duration_seconds": duration,
                "best_affinity": combined.get("best_affinity"),
                "primary_score_kcal_mol": combined.get("primary_score_kcal_mol"),
                "stage": str(combined.get("stage") or combined.get("status") or "unknown"),
                "run_mode": _normalize_run_mode(combined.get("run_mode")),
                "scoring_protocol": scoring_protocol,
                "scoring_function": "ad4"
                if scoring_protocol == "ad4_maps"
                else str((combined.get("vina_snapshot") or {}).get("scoring") or "vina")
                if isinstance(combined.get("vina_snapshot"), dict)
                else "vina",
            },
        )
    return history


def _format_duration_range(seconds_low: float, seconds_high: float) -> str:
    if seconds_high < 60:
        return f"约 {max(1, round(seconds_low))}–{max(1, round(seconds_high))} 秒"
    return f"约 {max(1, round(seconds_low / 60))}–{max(1, round(seconds_high / 60))} 分钟"


def _runtime_estimate(
    run_history: list[dict[str, Any]],
    scoring_protocol: str = "vina",
    run_mode: str = "dock",
) -> dict[str, Any]:
    samples = sorted(
        float(item["duration_seconds"])
        for item in run_history
        if item.get("status") == "finished"
        and str(item.get("scoring_protocol") or "vina") == scoring_protocol
        and _normalize_run_mode(item.get("run_mode")) == _normalize_run_mode(run_mode)
        and isinstance(item.get("duration_seconds"), (int, float))
        and float(item["duration_seconds"]) > 0
    )
    if len(samples) < 3:
        return {
            "available": False,
            "sample_count": len(samples),
            "range_label": "",
            "message": "暂无足够的同项目成功运行历史，暂不提供耗时估计。",
        }
    low_index = max(0, int((len(samples) - 1) * 0.25))
    high_index = min(len(samples) - 1, int((len(samples) - 1) * 0.75 + 0.999))
    return {
        "available": True,
        "sample_count": len(samples),
        "range_label": _format_duration_range(samples[low_index], samples[high_index]),
        "message": "根据当前项目最近的成功运行历史估计；结构、Box 和参数变化会影响实际耗时。",
    }


def get_run_preflight(project_dir: str) -> dict[str, Any]:
    """Aggregate every run blocker and warning for the run cockpit."""

    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    input_stats: dict[str, dict[str, Any]] = {"receptor": {}, "ligand": {}}
    default_payload: dict[str, Any] = {
        "ok": False,
        "ready": False,
        "project": None,
        "project_dir": str(Path(project_dir).expanduser()),
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "input_stats": input_stats,
        "structure_review": {
            "scientific_validation": False,
            "receptor": {},
            "ligand": {},
            "provenance": {},
            "checks": [],
            "warning_count": 0,
            "unknown_count": 0,
        },
        "box": {},
        "vina_params": {},
        "tool": {
            "status": "unknown",
            "version": "",
            "path": "",
            "source": "unknown",
            "message": "",
            "capabilities": {},
        },
        "output": {"runs_dir": "", "writable": False, "free_bytes": None},
        "system": _system_snapshot(),
        "estimate": {"available": False, "sample_count": 0, "range_label": "", "message": "暂无运行历史。"},
        "next_run_id": "",
        "command_preview": "",
        "run_history": [],
        "scoring_protocol": "vina",
        "grid_source": "receptor",
        "run_mode": "dock",
        "autobox": False,
        "pose_input_attestation": _pose_input_attestation_result(
            required=False,
            valid=True,
            status="not_required",
            message="全局对接不要求输入姿势坐标系确认。",
        ),
        "ad4_maps": None,
        "vina_maps": None,
        "config": {"status": "missing", "relative_path": "", "absolute_path": "", "exists": False, "non_empty": False, "sha256": ""},
        "message": "运行前检查未完成。",
        "error": None,
    }

    def add_check(
        key: str,
        name: str,
        status: str,
        message: str,
        *,
        blocking: bool,
        detail: str = "",
        action_page: str = "",
        path: str = "",
        version: str = "",
    ) -> None:
        checks.append(
            {
                "key": key,
                "name": name,
                "status": status,
                "message": message,
                "detail": detail,
                "blocking": blocking,
                "action_page": action_page,
                "path": path,
                "version": version,
            },
        )
        if blocking and message not in blockers:
            blockers.append(message)

    loaded = recover_project_state(project_dir)
    if not loaded.get("ok"):
        error = loaded.get("error") or {}
        message = str(error.get("message") or "没有找到可读取的 DockStart 项目。")
        add_check("project", "项目文件", "missing", message, blocking=True, action_page="project", path=project_dir)
        default_payload["message"] = "项目不可用，无法完成运行前检查。"
        default_payload["error"] = error
        return default_payload

    project_path = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(loaded["project"], project_path)
    scoring_protocol = _project_scoring_protocol(project)
    grid_source = _project_grid_source(project)
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    run_mode = _project_run_mode(project)
    autobox = _project_autobox(project)
    receptor_mode = _project_receptor_mode(project)
    default_payload.update(
        {
            "ok": True,
            "project": project.to_dict(),
            "project_dir": str(project_path),
            "scoring_protocol": scoring_protocol,
            "grid_source": grid_source,
            "run_mode": run_mode,
            "autobox": autobox,
        }
    )
    add_check("project", "项目文件", "ok", "project.json 已读取。", blocking=False, path=str(project_path / "project.json"))
    receptor_inputs = _active_receptor_inputs(project_path, project)
    active_receptor_file = project.receptor.file
    active_flex_file = ""
    if receptor_inputs.get("ok"):
        active_receptor_file = str(
            receptor_inputs.get("receptor_file") or project.receptor.file
        )
        active_flex_file = str(receptor_inputs.get("flex_file") or "")
    else:
        receptor_input_error = receptor_inputs.get("error") or {}
        add_check(
            "flexible_receptor",
            "柔性侧链受体",
            "error",
            str(receptor_input_error.get("message") or "柔性侧链受体不可用。"),
            blocking=True,
            detail=str(receptor_input_error.get("raw_error") or ""),
            action_page="run-prepare",
        )
    if uses_vina_maps and run_mode != "dock":
        add_check(
            "vina_maps_mode",
            "预计算 maps 任务类型",
            "error",
            "Vina/Vinardo 预计算 maps 当前只支持全局对接。",
            blocking=True,
            detail=f"run_mode={run_mode}",
            action_page="run-prepare",
        )
    if uses_vina_maps and (
        receptor_mode == "flexible" or bool(active_flex_file)
    ):
        add_check(
            "vina_maps_receptor",
            "预计算 maps 受体模式",
            "error",
            "Vina/Vinardo 预计算 maps 当前只支持刚性受体。",
            blocking=True,
            action_page="run-prepare",
        )

    def inspect_input(role: str, relative_path: str) -> None:
        label = "受体 PDBQT" if role == "receptor" else "配体 PDBQT"
        if not relative_path:
            add_check(role, label, "missing", f"尚未设置{label}。", blocking=True, action_page="import")
            return
        relative = Path(relative_path)
        if relative.is_absolute():
            add_check(role, label, "error", f"{label}必须使用项目内相对路径。", blocking=True, action_page="import", path=relative_path)
            return
        resolved = (project_path / relative).resolve()
        try:
            resolved.relative_to(project_path)
        except ValueError:
            add_check(role, label, "error", f"{label}指向项目目录外。", blocking=True, action_page="import", path=str(resolved))
            return
        if not resolved.is_file():
            add_check(role, label, "missing", f"没有找到{label}。", blocking=True, action_page="import", path=str(resolved))
            return
        if resolved.stat().st_size <= 0:
            add_check(role, label, "error", f"{label}为空文件。", blocking=True, action_page="import", path=str(resolved))
            return
        try:
            stats = _parse_pdbqt_stats(resolved, relative_path, ligand=role == "ligand")
            input_stats[role] = stats
        except OSError as exc:
            add_check(role, label, "error", f"无法读取{label}。", blocking=True, detail=str(exc), action_page="import", path=str(resolved))
            return
        if stats["atom_count"] <= 0:
            add_check(
                role,
                label,
                "error",
                f"{label}中没有识别到 ATOM/HETATM 记录。",
                blocking=True,
                detail="请确认文件是准备后的 PDBQT，而不是只有备注的占位文件。",
                action_page="import",
                path=str(resolved),
            )
            return
        add_check(
            role,
            label,
            "ok",
            f"已读取 {stats['atom_count']} 个原子。",
            blocking=False,
            detail=f"atom types: {', '.join(stats['atom_types']) or '未标注'}",
            path=str(resolved),
        )

    inspect_input("receptor", active_receptor_file)
    inspect_input("ligand", project.ligand.file)
    if active_flex_file:
        flex_path, flex_error = _project_relative_file(
            project_path,
            active_flex_file,
            "flex",
        )
        if flex_error or flex_path is None:
            error = (flex_error or {}).get("error") or {}
            add_check(
                "flex",
                "柔性侧链 PDBQT",
                "error",
                str(error.get("message") or "柔性侧链 PDBQT 不可用。"),
                blocking=True,
                detail=str(error.get("raw_error") or ""),
                action_page="run-prepare",
                path=active_flex_file,
            )
        elif flex_path.stat().st_size <= 0:
            add_check(
                "flex",
                "柔性侧链 PDBQT",
                "error",
                "柔性侧链 PDBQT 为空文件。",
                blocking=True,
                action_page="run-prepare",
                path=str(flex_path),
            )
        else:
            flex_stats = _parse_pdbqt_stats(
                flex_path,
                active_flex_file,
            )
            add_check(
                "flex",
                "柔性侧链 PDBQT",
                "ok",
                f"已读取 {flex_stats['atom_count']} 个柔性侧链原子。",
                blocking=False,
                detail=f"atom types: {', '.join(flex_stats['atom_types']) or '未标注'}",
                path=str(flex_path),
            )

    pose_input_attestation = _validate_current_pose_input_attestation(
        project_path,
        project,
    )
    default_payload["pose_input_attestation"] = pose_input_attestation
    if pose_input_attestation["required"]:
        attestation_detail = (
            "此门禁仅记录用户确认并校验确认后文件是否变化；"
            "DockStart 不会自动证明受体与配体使用同一坐标系。"
        )
        if pose_input_attestation["valid"]:
            add_check(
                "pose_input_attestation",
                "输入姿势坐标系确认",
                "ok",
                str(pose_input_attestation["message"]),
                blocking=False,
                detail=attestation_detail,
                action_page="run-prepare",
            )
        else:
            attestation_error = pose_input_attestation.get("error") or {}
            add_check(
                "pose_input_attestation",
                "输入姿势坐标系确认",
                (
                    "missing"
                    if pose_input_attestation["status"] == "missing"
                    else "error"
                ),
                str(pose_input_attestation["message"]),
                blocking=True,
                detail=(
                    f"{attestation_detail} "
                    f"{str(attestation_error.get('suggestion') or '')}"
                ).strip(),
                action_page="run-prepare",
            )

    structure_review = build_structure_review(
        project_path,
        receptor_file=project.receptor.file,
        ligand_file=project.ligand.file,
        receptor_raw_file=project.receptor.raw_file,
        ligand_raw_file=project.ligand.raw_file,
        receptor_metadata_file=project.preparation.receptor.metadata_file,
        ligand_metadata_file=project.preparation.ligand.metadata_file,
    )
    default_payload["structure_review"] = structure_review
    for role, label in (("receptor", "受体结构审查"), ("ligand", "配体结构审查")):
        role_checks = [item for item in structure_review["checks"] if item.get("role") == role]
        concrete_warnings = [item for item in role_checks if item.get("status") == "warning"]
        unknown_checks = [item for item in role_checks if item.get("status") == "unknown"]
        if concrete_warnings:
            message = f"检测到 {len(concrete_warnings)} 项需要人工确认的结构事实。"
            detail = "；".join(str(item.get("message") or "") for item in concrete_warnings)
            warnings.append(f"{label}：{message}")
            add_check(
                f"{role}_structure_review",
                label,
                "warning",
                message,
                blocking=False,
                detail=detail,
                action_page="import-pdbqt",
            )
        elif unknown_checks:
            message = f"文件事实已汇总，但仍有 {len(unknown_checks)} 项不能自动判定。"
            detail = "；".join(str(item.get("message") or "") for item in unknown_checks)
            add_check(
                f"{role}_structure_review",
                label,
                "warning",
                message,
                blocking=False,
                detail=detail,
                action_page="import-pdbqt",
            )
        else:
            add_check(
                f"{role}_structure_review",
                label,
                "ok",
                "可观察的结构文件事实已汇总；仍需人工判断其科学适用性。",
                blocking=False,
                action_page="import-pdbqt",
            )

    box_data = asdict(project.box)
    box_validation = validate_box_params(box_data)
    volume = float(project.box.size_x * project.box.size_y * project.box.size_z)
    box_warnings: list[str] = []
    box_required = scoring_protocol == "ad4_maps" or run_mode == "dock" or not autobox
    if not box_required:
        add_check(
            "box",
            "评价范围",
            "ok",
            "将按当前配体坐标自动建立评价范围（autobox）。",
            blocking=False,
            action_page="box",
        )
    elif box_validation.get("ok"):
        if any(box_data[key] > 30 for key in ("size_x", "size_y", "size_z")) or volume > 27000:
            warning = "对接箱体较大（任一轴超过 30 Å 或体积超过 27,000 Å³），运行可能明显变慢。"
            box_warnings.append(warning)
            warnings.append(warning)
            add_check("box", "对接箱体", "warning", warning, blocking=False, action_page="box")
        else:
            add_check("box", "对接箱体", "ok", "中心、尺寸和体积有效。", blocking=False, action_page="box")
    else:
        error = box_validation.get("error") or {}
        add_check("box", "对接箱体", "error", str(error.get("message") or "Box 参数无效。"), blocking=True, detail=str(error.get("raw_error") or ""), action_page="box")
    default_payload["box"] = {**box_data, "volume_angstrom3": round(volume, 3), "warnings": box_warnings}

    vina_data = asdict(project.vina)
    vina_validation = validate_vina_params(vina_data)
    if vina_validation.get("ok"):
        add_check(
            "vina_params",
            "运行参数" if run_mode != "dock" else "搜索参数",
            "ok",
            (
                "AutoDock4 运行参数格式有效。"
                if scoring_protocol == "ad4_maps"
                else "姿势评价参数格式有效。"
                if run_mode != "dock"
                else "Vina 参数格式有效。"
            ),
            blocking=False,
            action_page="vina-param",
        )
        for warning in vina_validation.get("warnings", []):
            if warning not in warnings:
                warnings.append(warning)
    else:
        error = vina_validation.get("error") or {}
        add_check("vina_params", "Vina 参数", "error", str(error.get("message") or "Vina 参数无效。"), blocking=True, detail=str(error.get("raw_error") or ""), action_page="vina-param")
    cpu_count = int(default_payload["system"]["cpu_count"] or 1)
    configured_cpu = int(vina_data.get("cpu") or 0)
    if configured_cpu > cpu_count:
        warning = f"Vina CPU 设置为 {configured_cpu}，超过系统检测到的 {cpu_count} 个逻辑核心。"
        warnings.append(warning)
        add_check("cpu", "CPU 线程", "warning", warning, blocking=False, action_page="vina-param")
    else:
        add_check("cpu", "CPU 线程", "ok", "CPU 线程设置未超过系统逻辑核心数。", blocking=False, action_page="vina-param")
    default_payload["vina_params"] = vina_data

    maps_prefix = ""
    maps_scoring = str(project.vina.scoring or "vina")
    if scoring_protocol == "ad4_maps":
        maps_status = _active_ad4_maps(str(project_path))
        default_payload["ad4_maps"] = maps_status
        if maps_status.get("ok") and maps_status.get("ready"):
            maps_prefix = str(maps_status.get("maps_prefix") or "")
            add_check(
                "ad4_maps",
                "AutoDock4 affinity maps",
                "ok",
                "maps 完整，并与当前受体 SHA256、配体原子类型和 Box 一致。",
                blocking=False,
                path=str(maps_status.get("manifest_file") or ""),
            )
        else:
            error = maps_status.get("error") or {}
            add_check(
                "ad4_maps",
                "AutoDock4 affinity maps",
                "error",
                str(error.get("message") or maps_status.get("message") or "maps 未准备完成。"),
                blocking=True,
                detail=str(error.get("raw_error") or "；".join(maps_status.get("issues") or [])),
                action_page="run-prepare",
                path=str(maps_status.get("manifest_file") or ""),
            )
        from adapters import autogrid_adapter

        autogrid_detection = autogrid_adapter.detect(load_settings().tool_paths.autogrid4)
        if autogrid_detection.status == "ok":
            add_check(
                "autogrid4",
                "AutoGrid4",
                "ok",
                "外部 AutoGrid4 可用于重新生成 maps。",
                blocking=False,
                path=autogrid_detection.path,
                version=autogrid_detection.version,
            )
        elif maps_status.get("ok") and maps_status.get("ready"):
            warning = "当前 AutoGrid4 不可用；已有 maps 仍可运行，但无法在本机重新生成。"
            warnings.append(warning)
            add_check(
                "autogrid4",
                "AutoGrid4",
                "warning",
                warning,
                blocking=False,
                detail=autogrid_detection.raw_error,
                action_page="settings",
            )
        else:
            add_check(
                "autogrid4",
                "AutoGrid4",
                autogrid_detection.status,
                autogrid_detection.message or "未检测到 AutoGrid4。",
                blocking=True,
                detail=autogrid_detection.raw_error,
                action_page="settings",
            )
    elif uses_vina_maps:
        maps_status = _active_vina_maps(
            str(project_path),
            probe_ligand=True,
        )
        default_payload["vina_maps"] = maps_status
        manifest = (
            maps_status.get("manifest")
            if isinstance(maps_status.get("manifest"), dict)
            else {}
        )
        maps_scoring = str(
            manifest.get("scoring_function") or project.vina.scoring or "vina"
        )
        if maps_status.get("ok") and maps_status.get("ready"):
            maps_prefix = str(maps_status.get("maps_prefix") or "")
            add_check(
                "vina_maps",
                "Vina/Vinardo 预计算 maps",
                "ok",
                "maps 已通过文件完整性、受体、Box、评分函数和当前配体兼容性校验。",
                blocking=False,
                detail=str(
                    (maps_status.get("compatibility_probe") or {}).get(
                        "message"
                    )
                    or ""
                )
                if isinstance(maps_status.get("compatibility_probe"), dict)
                else "",
                path=str(maps_status.get("manifest_file") or ""),
            )
        else:
            error = maps_status.get("error") or {}
            add_check(
                "vina_maps",
                "Vina/Vinardo 预计算 maps",
                "error",
                str(
                    error.get("message")
                    or maps_status.get("message")
                    or "预计算 maps 未通过运行前校验。"
                ),
                blocking=True,
                detail=str(
                    error.get("raw_error")
                    or "；".join(maps_status.get("issues") or [])
                ),
                action_page="run-prepare",
                path=str(maps_status.get("manifest_file") or ""),
            )

    config_file = _config_relative_path(project)
    config_relative = Path(config_file)
    config_path = (project_path / config_relative).resolve() if not config_relative.is_absolute() else config_relative.resolve()
    config_contained = not config_relative.is_absolute()
    if config_contained:
        try:
            config_path.relative_to(project_path)
        except ValueError:
            config_contained = False
    config_status = {
        "status": "missing" if config_contained else "invalid",
        "relative_path": Path(config_file).as_posix(),
        "absolute_path": str(config_path),
        "exists": config_path.is_file() if config_contained else False,
        "non_empty": False,
        "sha256": "",
        "generated_at": project.config.generated_at,
    }
    if not config_contained:
        add_check(
            "config",
            "Vina 配置",
            "error",
            "Vina 配置路径指向项目目录外，已拒绝读取。",
            blocking=True,
            detail=str(config_path),
            action_page="vina-config",
            path=str(config_path),
        )
    elif config_path.is_file():
        try:
            config_status["non_empty"] = config_path.stat().st_size > 0
            if config_status["non_empty"]:
                config_status["sha256"] = _sha256_file(config_path)
                preview = build_vina_config_text(str(project_path))
                current_text = config_path.read_text(encoding="utf-8")
                expected_text = str(preview.get("config_text") or "") if preview.get("ok") else ""
                if expected_text and current_text.replace("\r\n", "\n") != expected_text.replace("\r\n", "\n"):
                    config_status["status"] = "stale"
                    warning = "Vina 配置与当前项目参数不一致，启动时将生成/刷新。"
                    warnings.append(warning)
                    add_check("config", "Vina 配置", "warning", warning, blocking=False, action_page="vina-config", path=str(config_path))
                else:
                    config_status["status"] = "ok"
                    add_check("config", "Vina 配置", "ok", "vina_config.txt 已生成且与当前参数一致。", blocking=False, action_page="vina-config", path=str(config_path))
            else:
                config_status["status"] = "stale"
                warning = "vina_config.txt 为空，启动时将生成/刷新。"
                warnings.append(warning)
                add_check("config", "Vina 配置", "warning", warning, blocking=False, action_page="vina-config", path=str(config_path))
        except OSError as exc:
            config_status["status"] = "stale"
            warning = "当前 vina_config.txt 无法读取，启动时将生成/刷新。"
            warnings.append(warning)
            add_check("config", "Vina 配置", "warning", warning, blocking=False, detail=str(exc), action_page="vina-config", path=str(config_path))
    else:
        warning = "尚未生成 vina_config.txt，启动时将生成/刷新。"
        warnings.append(warning)
        add_check("config", "Vina 配置", "warning", warning, blocking=False, action_page="vina-config", path=str(config_path))
    default_payload["config"] = config_status

    settings = load_settings()
    detection = vina_adapter.detect(settings.tool_paths.vina)
    tool = {
        "status": detection.status,
        "version": detection.version,
        "path": detection.path,
        "source": detection.source,
        "message": detection.message,
        "capabilities": copy.deepcopy(detection.capabilities),
    }
    default_payload["tool"] = tool
    if detection.status == "ok":
        add_check("tool", "AutoDock Vina", "ok", detection.message or "AutoDock Vina 可用。", blocking=False, path=detection.path, version=detection.version)
        runtime_vina_data = copy.deepcopy(vina_data)
        if uses_vina_maps:
            runtime_vina_data["no_refine"] = False
            runtime_vina_data["force_even_voxels"] = False
        capability_validation = validate_vina_runtime_capabilities(
            runtime_vina_data,
            scoring_protocol,
            detection.capabilities,
            run_mode=run_mode,
            receptor_mode=receptor_mode,
            autobox=autobox,
        )
        if not capability_validation.get("ok"):
            capability_error = capability_validation.get("error") or {}
            add_check(
                "vina_capabilities",
                "Vina 专家选项",
                "error",
                str(capability_error.get("message") or "当前 Vina 不支持已启用的专家选项。"),
                blocking=True,
                detail=str(capability_error.get("raw_error") or ""),
                action_page="vina-param",
                path=detection.path,
                version=detection.version,
            )
        else:
            features = (
                detection.capabilities.get("features")
                if isinstance(detection.capabilities, dict)
                and isinstance(detection.capabilities.get("features"), dict)
                else {}
            )
            applicable_feature_keys = [
                *([] if uses_vina_maps else ["no_refine", "force_even_voxels"]),
                *(
                    ["unbound_energy"]
                    if run_mode == "score_only" and receptor_mode != "flexible"
                    else []
                ),
                *(
                    ["autobox"]
                    if run_mode != "dock" and autobox
                    else []
                ),
            ]
            feature_statuses = {
                key: str((features.get(key) or {}).get("status") or "unknown")
                if isinstance(features.get(key), dict)
                else "unknown"
                for key in applicable_feature_keys
            }
            if scoring_protocol == "ad4_maps":
                add_check(
                    "vina_capabilities",
                    "Vina 专家选项",
                    "ok",
                    "AutoDock4 maps 不使用精修与网格体素专家选项。",
                    blocking=False,
                    path=detection.path,
                    version=detection.version,
                )
            elif uses_vina_maps:
                add_check(
                    "vina_capabilities",
                    "Vina 预计算 maps",
                    "ok",
                    "当前 maps 已由兼容性探针确认可供本次 Vina/Vinardo 全局对接读取。",
                    blocking=False,
                    path=detection.path,
                    version=detection.version,
                )
            elif all(status == "supported" for status in feature_statuses.values()):
                add_check(
                    "vina_capabilities",
                    "Vina 专家选项",
                    "ok",
                    "当前 Vina 已确认支持本任务适用的高级选项。",
                    blocking=False,
                    path=detection.path,
                    version=detection.version,
                )
            else:
                capability_message = "当前 Vina 的部分专家选项尚未确认；保持关闭不影响默认运行。"
                if capability_message not in warnings:
                    warnings.append(capability_message)
                add_check(
                    "vina_capabilities",
                    "Vina 专家选项",
                    "warning",
                    capability_message,
                    blocking=False,
                    detail=str(detection.capabilities.get("raw_error") or "")
                    if isinstance(detection.capabilities, dict)
                    else "",
                    path=detection.path,
                    version=detection.version,
                )
    else:
        add_check("tool", "AutoDock Vina", detection.status, detection.message or "AutoDock Vina 不可用。", blocking=True, detail=detection.raw_error, action_page="settings", path=detection.path, version=detection.version)

    runs_dir = project_path / "runs"
    writable = False
    output_error = ""
    try:
        runs_dir = _safe_runs_directory(project_path, create=True)
        with tempfile.NamedTemporaryFile(prefix=".dockstart-write-check-", dir=runs_dir, delete=False) as marker:
            marker_path = Path(marker.name)
        marker_path.unlink(missing_ok=True)
        writable = True
    except (OSError, RuntimeError) as exc:
        output_error = str(exc)
    try:
        free_bytes: int | None = shutil.disk_usage(project_path).free
    except OSError:
        free_bytes = None
    default_payload["output"] = {"runs_dir": str(runs_dir), "writable": writable, "free_bytes": free_bytes}
    if writable:
        add_check("output", "运行输出目录", "ok", "runs 目录可写。", blocking=False, path=str(runs_dir))
    else:
        add_check("output", "运行输出目录", "error", "runs 目录不可写。", blocking=True, detail=output_error, action_page="project", path=str(runs_dir))
    if free_bytes is not None and free_bytes < 256 * 1024 * 1024:
        warning = "项目磁盘剩余空间不足 256 MB，请清理空间后再运行。"
        warnings.append(warning)
        add_check("disk", "磁盘空间", "warning", warning, blocking=False, path=str(project_path))
    elif free_bytes is not None:
        add_check("disk", "磁盘空间", "ok", "项目磁盘剩余空间可用。", blocking=False, detail=f"{free_bytes} bytes", path=str(project_path))
    else:
        warning = "无法读取项目磁盘剩余空间。"
        warnings.append(warning)
        add_check("disk", "磁盘空间", "warning", warning, blocking=False, path=str(project_path))

    run_history = _collect_run_history(project_path, project)
    next_run_id = get_next_run_id(str(project_path))
    command = _build_vina_command(
        detection.path,
        config_file,
        next_run_id,
        active_flex_file,
        scoring_protocol=scoring_protocol,
        maps_prefix=maps_prefix,
        grid_source=grid_source,
        maps_scoring=maps_scoring,
        run_mode=run_mode,
        autobox=autobox,
    )
    default_payload.update(
        {
            "ready": not blockers,
            "estimate": _runtime_estimate(run_history, scoring_protocol, run_mode),
            "next_run_id": next_run_id,
            "command_preview": _format_command_preview(command),
            "run_history": run_history,
            "message": "全部运行前条件已满足。" if not blockers else f"发现 {len(blockers)} 个阻塞项，请修复后再运行。",
        },
    )
    return default_payload


def _config_relative_path(project: DockStartProject) -> str:
    return project.config.vina_config_file or "configs/vina_config.txt"


def _project_relative_existing_file(
    project_dir: Path,
    relative_path: str,
    error_prefix: str,
    display_name: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{error_prefix}_NOT_SET",
            f"{display_name} 尚未生成或记录在 project.json 中。",
            suggestion=f"请先完成对应步骤，确认项目中存在 {display_name}。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{error_prefix}_PATH_NOT_RELATIVE",
            f"{display_name} 路径必须是项目内相对路径。",
            suggestion="请重新生成配置文件，避免使用用户机器上的绝对路径。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{error_prefix}_OUTSIDE_PROJECT",
            f"{display_name} 路径指向项目目录外，无法用于可复现运行记录。",
            raw_error=str(file_path),
            suggestion="请重新生成配置文件，让路径保留在项目目录内。",
        )

    if not file_path.exists():
        return None, _error(
            f"{error_prefix}_NOT_FOUND",
            f"没有找到 {display_name}。",
            raw_error=str(file_path),
            suggestion=f"请先生成或恢复 {relative_path}。",
        )
    if not file_path.is_file():
        return None, _error(
            f"{error_prefix}_PATH_NOT_FILE",
            f"{display_name} 路径不是一个文件。",
            raw_error=str(file_path),
            suggestion="请检查项目文件结构。",
        )

    return file_path, None


def _status_from_error_code(code: str) -> str:
    return "missing" if code.endswith("_NOT_SET") or code.endswith("_NOT_FOUND") or code.endswith("_NOT_PREPARED") else "error"


def _active_receptor_inputs(project_path: Path, project: DockStartProject) -> dict[str, Any]:
    """Resolve a verified rigid/flexible receptor pair without changing legacy projects."""

    if _project_receptor_mode(project) != "flexible":
        return {
            "ok": True,
            "mode": "rigid",
            "receptor_file": Path(project.receptor.file).as_posix() if project.receptor.file else "",
            "flex_file": "",
            "selected_residues": [],
        }

    # Local import avoids a module cycle: flexible_receptor uses project
    # persistence helpers, while the run chain only consumes its verified status.
    from dockstart_core.flexible_receptor import get_flexible_receptor_status

    status = get_flexible_receptor_status(str(project_path))
    config = status.get("flexible_receptor") if isinstance(status.get("flexible_receptor"), dict) else {}
    if not status.get("ok") or status.get("effective_mode") != "flexible":
        issues = (status.get("integrity") or {}).get("issues") if isinstance(status.get("integrity"), dict) else []
        return _error(
            "FLEX_RECEPTOR_NOT_READY",
            "项目选择了柔性侧链模式，但 rigid/flex 受体三件套未通过来源与 SHA256 校验。",
            raw_error="；".join(str(item) for item in (issues or [])),
            suggestion="请重新准备柔性受体，或切换回刚性受体模式。",
        )
    return {
        "ok": True,
        "mode": "flexible",
        "receptor_file": Path(str(config.get("rigid_file") or "")).as_posix(),
        "flex_file": Path(str(config.get("flex_file") or "")).as_posix(),
        "receptor_json_file": Path(str(config.get("receptor_json_file") or "")).as_posix(),
        "selected_residues": copy.deepcopy(config.get("selected_residues") or []),
        "preparation_id": str(config.get("preparation_id") or ""),
        "source_raw_file": str(config.get("source_raw_file") or ""),
        "source_format": str(config.get("source_format") or "pdb"),
        "source_sha256": str(config.get("source_sha256") or ""),
        "resolved_altlocs": copy.deepcopy(config.get("resolved_altlocs") or {}),
        "receptor_controls": copy.deepcopy(
            config.get("receptor_controls") or {}
        ),
        "receptor_controls_sha256": str(
            config.get("receptor_controls_sha256") or ""
        ),
        "receptor_controls_fingerprint": copy.deepcopy(
            config.get("receptor_controls_fingerprint") or {}
        ),
        "atom_partition": copy.deepcopy(config.get("atom_partition") or {}),
        "identity": copy.deepcopy(config.get("identity") or {}),
        "sha256": copy.deepcopy(config.get("sha256") or {}),
    }


def _normalized_macrocycle_bonds(value: Any) -> tuple[list[list[int]], str]:
    """Return canonical zero-based atom pairs without accepting bools or duplicates."""

    if not isinstance(value, list):
        return [], "断环键不是数组。"
    normalized: list[list[int]] = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return [], "断环键必须由两个原子索引组成。"
        left, right = pair
        if (
            isinstance(left, bool)
            or isinstance(right, bool)
            or not isinstance(left, int)
            or not isinstance(right, int)
            or left < 0
            or right < 0
            or left == right
        ):
            return [], "断环键包含无效原子索引。"
        normalized.append([min(left, right), max(left, right)])
    if normalized != sorted(normalized) or len({tuple(pair) for pair in normalized}) != len(
        normalized
    ):
        return [], "断环键没有按规范顺序记录或包含重复项。"
    return normalized, ""


def _macrocycle_canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _macrocycle_relative_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return ""
    return path.as_posix()


def _macrocycle_snapshot_size(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("size")
    if value is None:
        value = snapshot.get("size_bytes")
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _load_macrocycle_json_record(
    path: Path,
    *,
    maximum_bytes: int = 8 * 1024 * 1024,
) -> tuple[dict[str, Any] | None, str]:
    try:
        size = path.stat().st_size
        if size <= 0 or size > maximum_bytes:
            return None, f"JSON 记录大小无效（{size} bytes）。"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, f"JSON 记录无法读取或解析：{exc}"
    if not isinstance(payload, dict):
        return None, "JSON 记录顶层不是对象。"
    return payload, ""


def _macrocycle_bond_summaries(
    contract: dict[str, Any],
    exact_bonds: list[list[int]],
) -> tuple[list[dict[str, Any]], str]:
    selected = contract.get("selected_bonds")
    if not isinstance(selected, list):
        return [], "大环合同缺少可读的已选断环键。"
    if not exact_bonds and selected:
        return [], "刚性大环合同不应包含已选断环键。"
    if len(selected) != len(exact_bonds):
        return [], "大环合同中的断环键编号与精确原子对数量不一致。"

    summaries: list[dict[str, Any]] = []
    for expected_pair, item in zip(exact_bonds, selected, strict=True):
        if not isinstance(item, dict):
            return [], "大环合同中的断环键说明格式无效。"
        zero_based, error = _normalized_macrocycle_bonds(
            [item.get("atom_indices_zero_based")]
        )
        if error or zero_based != [expected_pair]:
            return [], "断环键说明与合同中的精确原子对不一致。"
        one_based = item.get("atom_numbers_one_based")
        if (
            not isinstance(one_based, list)
            or len(one_based) != 2
            or one_based != [expected_pair[0] + 1, expected_pair[1] + 1]
        ):
            return [], "断环键的一基编号与零基原子索引不一致。"
        labels = item.get("atom_labels")
        if (
            not isinstance(labels, list)
            or len(labels) != 2
            or not all(str(label or "").strip() for label in labels)
        ):
            return [], "断环键缺少可读的原子标签。"
        clean_labels = [str(label).strip() for label in labels]
        summaries.append(
            {
                "atom_numbers_one_based": list(one_based),
                "atom_labels": clean_labels,
                "display": (
                    f"{clean_labels[0]}（原子 {one_based[0]}）"
                    f"—{clean_labels[1]}（原子 {one_based[1]}）"
                ),
            }
        )
    return summaries, ""


def _formal_macrocycle_preparation_match(
    project_path: Path,
    metadata: dict[str, Any],
    *,
    metadata_file: str,
    metadata_path: Path,
    ligand_file: str,
    ligand_path: Path,
    ligand_sha256: str,
) -> dict[str, Any]:
    """Require the complete review/contract/worker chain before formal attribution."""

    issues: list[str] = []

    def issue(message: str) -> None:
        if message and message not in issues:
            issues.append(message)

    if str(metadata.get("status") or "") != "finished":
        issue("准备 metadata 状态不是 finished。")
    if metadata.get("published") is not True or metadata.get("output_non_empty") is not True:
        issue("准备记录没有确认发布非空的 ligand.pdbqt。")

    protocol_evidence = (
        metadata.get("protocol_evidence")
        if isinstance(metadata.get("protocol_evidence"), dict)
        else {}
    )
    if protocol_evidence.get("ok") is not True:
        issue("协议证据没有通过准备阶段校验。")
    if str(protocol_evidence.get("mode") or "") != "reviewed":
        issue("协议证据模式不是 reviewed。")
    evidence_issues = protocol_evidence.get("issues")
    if not isinstance(evidence_issues, list) or evidence_issues:
        issue("协议证据仍包含问题，或缺少明确的空问题清单。")

    contract = (
        metadata.get("macrocycle_contract")
        if isinstance(metadata.get("macrocycle_contract"), dict)
        else {}
    )
    if contract.get("protocol_id") != "meeko_macrocycle":
        issue("大环合同缺少正确的 protocol_id。")
    if (
        isinstance(contract.get("schema_version"), bool)
        or contract.get("schema_version")
        != MACROCYCLE_CONTRACT_SCHEMA_VERSION
        or contract.get("analysis_version")
        != MACROCYCLE_ANALYSIS_VERSION
        or contract.get("meeko_api_profile")
        != MACROCYCLE_MEEKO_API_PROFILE
        or contract.get("hydrogen_policy")
        != MACROCYCLE_HYDROGEN_POLICY
    ):
        issue("大环合同不是当前正式协议版本。")

    contract_file = _macrocycle_relative_path(
        metadata.get("macrocycle_contract_file")
    )
    evidence_contract_file = _macrocycle_relative_path(
        protocol_evidence.get("contract_file")
    )
    if not contract_file:
        issue("大环合同路径缺失或不是项目内相对路径。")
    elif evidence_contract_file != contract_file:
        issue("metadata 与协议证据记录的大环合同路径不一致。")

    contract_sha256 = str(
        metadata.get("macrocycle_contract_sha256") or ""
    ).lower()
    recorded_contract_sha256 = str(
        protocol_evidence.get("contract_sha256") or ""
    ).lower()
    if not SHA256_PATTERN.fullmatch(contract_sha256):
        issue("大环合同 SHA256 缺失或格式无效。")
    elif recorded_contract_sha256 != contract_sha256:
        issue("metadata 与协议证据记录的大环合同 SHA256 不一致。")

    contract_path: Path | None = None
    if contract_file:
        contract_path, contract_path_error = _project_relative_existing_file(
            project_path,
            contract_file,
            "MACROCYCLE_CONTRACT",
            "大环准备合同",
        )
        if contract_path_error or contract_path is None:
            issue("没有找到可核验的大环准备合同。")
    if contract_path is not None:
        persisted_contract, contract_read_error = _load_macrocycle_json_record(
            contract_path
        )
        if contract_read_error:
            issue(f"大环合同无效：{contract_read_error}")
        elif persisted_contract != contract:
            issue("metadata 中的大环合同与合同文件内容不一致。")
        actual_contract_sha256 = _sha256_file(contract_path)
        if actual_contract_sha256 != contract_sha256:
            issue("大环合同文件 SHA256 与准备记录不一致。")
        contract_snapshot = (
            protocol_evidence.get("contract_snapshot")
            if isinstance(protocol_evidence.get("contract_snapshot"), dict)
            else {}
        )
        if (
            _macrocycle_relative_path(
                contract_snapshot.get("path")
                or contract_snapshot.get("relative_path")
            )
            != contract_file
            or str(contract_snapshot.get("sha256") or "").lower()
            != actual_contract_sha256
            or _macrocycle_snapshot_size(contract_snapshot)
            != contract_path.stat().st_size
        ):
            issue("协议证据中的合同文件快照与磁盘文件不一致。")

    evidence_file = _macrocycle_relative_path(
        metadata.get("macrocycle_evidence_file")
    )
    recorded_evidence_file = _macrocycle_relative_path(
        protocol_evidence.get("evidence_file")
    )
    if not evidence_file:
        issue("大环 worker 证据路径缺失或不是项目内相对路径。")
    elif recorded_evidence_file != evidence_file:
        issue("metadata 与协议证据记录的 worker 证据路径不一致。")

    evidence_path: Path | None = None
    worker_evidence: dict[str, Any] = {}
    evidence_sha256 = ""
    if evidence_file:
        evidence_path, evidence_path_error = _project_relative_existing_file(
            project_path,
            evidence_file,
            "MACROCYCLE_EVIDENCE",
            "大环 worker 证据",
        )
        if evidence_path_error or evidence_path is None:
            issue("没有找到可核验的大环 worker 证据。")
    if evidence_path is not None:
        persisted_evidence, evidence_read_error = _load_macrocycle_json_record(
            evidence_path
        )
        if evidence_read_error:
            issue(f"大环 worker 证据无效：{evidence_read_error}")
        else:
            worker_evidence = persisted_evidence or {}
            if worker_evidence != protocol_evidence.get("evidence"):
                issue("metadata 中的 worker 证据与证据文件内容不一致。")
        evidence_sha256 = _sha256_file(evidence_path)
        evidence_snapshot = (
            protocol_evidence.get("evidence_snapshot")
            if isinstance(protocol_evidence.get("evidence_snapshot"), dict)
            else {}
        )
        if (
            _macrocycle_relative_path(
                evidence_snapshot.get("path")
                or evidence_snapshot.get("relative_path")
            )
            != evidence_file
            or str(evidence_snapshot.get("sha256") or "").lower()
            != evidence_sha256
            or _macrocycle_snapshot_size(evidence_snapshot)
            != evidence_path.stat().st_size
        ):
            issue("worker 证据文件与准备阶段记录的 SHA256/大小不一致。")

    runtime_input = (
        contract.get("runtime_input")
        if isinstance(contract.get("runtime_input"), dict)
        else {}
    )
    frozen_input_file = _macrocycle_relative_path(
        metadata.get("macrocycle_input_file")
    )
    runtime_input_file = _macrocycle_relative_path(
        runtime_input.get("relative_path")
    )
    if not frozen_input_file or runtime_input_file != frozen_input_file:
        issue("大环合同没有绑定当前准备记录的冻结输入。")
    frozen_input_sha256 = str(runtime_input.get("sha256") or "").lower()
    try:
        frozen_input_size = int(runtime_input.get("size_bytes"))
    except (TypeError, ValueError):
        frozen_input_size = -1
    frozen_input_path: Path | None = None
    if frozen_input_file:
        frozen_input_path, frozen_input_error = _project_relative_existing_file(
            project_path,
            frozen_input_file,
            "MACROCYCLE_FROZEN_INPUT",
            "大环冻结输入",
        )
        if frozen_input_error or frozen_input_path is None:
            issue("没有找到大环准备使用的冻结输入。")
    if frozen_input_path is not None:
        actual_frozen_sha256 = _sha256_file(frozen_input_path)
        if (
            not SHA256_PATTERN.fullmatch(frozen_input_sha256)
            or actual_frozen_sha256 != frozen_input_sha256
            or frozen_input_path.stat().st_size != frozen_input_size
        ):
            issue("大环冻结输入的 SHA256 或大小与合同不一致。")
        input_snapshot = (
            protocol_evidence.get("input_snapshot")
            if isinstance(protocol_evidence.get("input_snapshot"), dict)
            else {}
        )
        if (
            _macrocycle_relative_path(
                input_snapshot.get("path")
                or input_snapshot.get("relative_path")
            )
            != frozen_input_file
            or str(input_snapshot.get("sha256") or "").lower()
            != actual_frozen_sha256
            or _macrocycle_snapshot_size(input_snapshot)
            != frozen_input_path.stat().st_size
        ):
            issue("协议证据中的冻结输入快照与磁盘文件不一致。")

    review_input = (
        contract.get("review_input")
        if isinstance(contract.get("review_input"), dict)
        else {}
    )
    review_input_file = _macrocycle_relative_path(
        review_input.get("relative_path")
    )
    if (
        not review_input_file
        or str(review_input.get("sha256") or "").lower() != frozen_input_sha256
        or _macrocycle_snapshot_size(review_input) != frozen_input_size
    ):
        issue("审查输入与准备阶段冻结输入的 SHA256/大小不一致。")
    else:
        review_input_path, review_input_error = _project_relative_existing_file(
            project_path,
            review_input_file,
            "MACROCYCLE_REVIEW_INPUT",
            "大环审查输入快照",
        )
        if review_input_error or review_input_path is None:
            issue("没有找到大环审查输入快照。")
        elif (
            _sha256_file(review_input_path) != frozen_input_sha256
            or review_input_path.stat().st_size != frozen_input_size
        ):
            issue("大环审查输入快照已变化。")

    output = metadata.get("output") if isinstance(metadata.get("output"), dict) else {}
    expected_output_path = _macrocycle_relative_path(output.get("path"))
    recorded_output_sha256 = str(output.get("sha256") or "").lower()
    try:
        recorded_output_size = int(output.get("size"))
    except (TypeError, ValueError):
        recorded_output_size = -1
    if expected_output_path != Path(ligand_file).as_posix():
        issue("准备记录中的发布输出路径不是当前 ligand.pdbqt。")
    if (
        recorded_output_sha256 != ligand_sha256
        or recorded_output_size != ligand_path.stat().st_size
    ):
        issue("发布输出的 SHA256 或大小与当前 ligand.pdbqt 不一致。")

    candidate_output = (
        protocol_evidence.get("candidate_output")
        if isinstance(protocol_evidence.get("candidate_output"), dict)
        else {}
    )
    if (
        str(candidate_output.get("sha256") or "").lower() != ligand_sha256
        or _macrocycle_snapshot_size(candidate_output) != ligand_path.stat().st_size
    ):
        issue("准备阶段候选输出快照与当前 ligand.pdbqt 不一致。")

    if worker_evidence.get("ok") is not True:
        issue("大环 worker 没有报告成功。")
    worker_output_sha256 = str(worker_evidence.get("output_sha256") or "").lower()
    try:
        worker_output_size = int(worker_evidence.get("output_size_bytes"))
    except (TypeError, ValueError):
        worker_output_size = -1
    if (
        worker_output_sha256 != ligand_sha256
        or worker_output_size != ligand_path.stat().st_size
    ):
        issue("worker 证据中的输出 SHA256 或大小与当前 ligand.pdbqt 不一致。")

    review_id = str(contract.get("review_id") or "")
    confirmation_sha256 = str(contract.get("confirmation_sha256") or "").lower()
    candidate_id = str(contract.get("candidate_id") or "")
    selection_mode = str(contract.get("selection_mode") or "")
    atom_table_sha256 = str(contract.get("atom_table_sha256") or "").lower()
    bond_topology_sha256 = str(
        contract.get("bond_topology_sha256") or ""
    ).lower()
    bond_topology = contract.get("bond_topology")
    atom_indexing = contract.get("atom_indexing")
    tool_versions = (
        contract.get("tool_versions")
        if isinstance(contract.get("tool_versions"), dict)
        else {}
    )
    if not review_id:
        issue("大环合同缺少 review_id。")
    if not SHA256_PATTERN.fullmatch(confirmation_sha256):
        issue("大环合同缺少有效的 confirmation_sha256。")
    if not SHA256_PATTERN.fullmatch(atom_table_sha256):
        issue("大环合同缺少有效的原子表 SHA256。")
    try:
        topology_matches = (
            isinstance(bond_topology, list)
            and _macrocycle_canonical_json_sha256(bond_topology)
            == bond_topology_sha256
        )
    except (TypeError, ValueError):
        topology_matches = False
    if (
        not SHA256_PATTERN.fullmatch(bond_topology_sha256)
        or not topology_matches
    ):
        issue("大环合同中的键拓扑或 SHA256 无效。")
    if not isinstance(atom_indexing, dict):
        issue("大环合同缺少显式氢与原始原子索引映射。")

    for key, expected_value in (
        ("review_id", review_id),
        ("confirmation_sha256", confirmation_sha256),
        ("candidate_id", candidate_id),
        ("selection_mode", selection_mode),
        ("atom_table_sha256", atom_table_sha256),
        ("bond_topology_sha256", bond_topology_sha256),
        ("hydrogen_policy", MACROCYCLE_HYDROGEN_POLICY),
    ):
        if worker_evidence.get(key) != expected_value:
            issue(f"worker 证据中的 {key} 与大环合同不一致。")
    if worker_evidence.get("atom_indexing") != atom_indexing:
        issue("worker 的显式氢/原始原子索引映射与大环合同不一致。")
    for key in ("rdkit", "meeko"):
        expected_version = str(tool_versions.get(key) or "")
        if (
            not expected_version
            or str(worker_evidence.get(f"{key}_version") or "")
            != expected_version
            or str(metadata.get(f"{key}_version") or "")
            != expected_version
        ):
            issue(f"准备 metadata、worker 与合同中的 {key} 版本不一致。")

    requested = metadata.get("options") if isinstance(metadata.get("options"), dict) else {}
    requested_macrocycle = (
        requested.get("macrocycle")
        if isinstance(requested.get("macrocycle"), dict)
        else {}
    )
    if (
        requested_macrocycle.get("mode") != "reviewed"
        or requested_macrocycle.get("review_id") != review_id
        or str(requested_macrocycle.get("confirmation_sha256") or "").lower()
        != confirmation_sha256
    ):
        issue("准备请求绑定的审查/确认标识与大环合同不一致。")

    expected_evidence = (
        metadata.get("macrocycle_expected_output_evidence")
        if isinstance(metadata.get("macrocycle_expected_output_evidence"), dict)
        else {}
    )
    for key, expected_value in (
        ("selection_mode", selection_mode),
        ("candidate_id", candidate_id),
        ("confirmation_sha256", confirmation_sha256),
        ("atom_table_sha256", atom_table_sha256),
        ("bond_topology_sha256", bond_topology_sha256),
    ):
        if expected_evidence.get(key) != expected_value:
            issue(f"准备计划中的 {key} 与大环合同不一致。")

    contract_bonds, contract_bonds_error = _normalized_macrocycle_bonds(
        contract.get("exact_bonds")
    )
    expected_bonds, expected_bonds_error = _normalized_macrocycle_bonds(
        expected_evidence.get("exact_bonds")
    )
    worker_expected_bonds, worker_expected_error = _normalized_macrocycle_bonds(
        worker_evidence.get("expected_bonds")
    )
    worker_actual_bonds, worker_actual_error = _normalized_macrocycle_bonds(
        worker_evidence.get("actual_bonds")
    )
    if any(
        (
            contract_bonds_error,
            expected_bonds_error,
            worker_expected_error,
            worker_actual_error,
        )
    ):
        issue("合同、准备计划或 worker 的断环键证据格式无效。")
    elif not (
        contract_bonds
        == expected_bonds
        == worker_expected_bonds
        == worker_actual_bonds
    ):
        issue("合同、准备计划、worker 预期与实际断环键不一致。")

    if selection_mode == "candidate":
        if not candidate_id or not contract_bonds:
            issue("候选断环模式缺少 candidate_id 或精确断环键。")
    elif selection_mode == "rigid":
        if candidate_id or contract_bonds:
            issue("刚性大环模式不应包含 candidate_id 或断环键。")
    else:
        issue("大环选择模式不是 candidate 或 rigid。")

    bond_summaries, bond_summary_error = _macrocycle_bond_summaries(
        contract,
        contract_bonds,
    )
    if bond_summary_error:
        issue(bond_summary_error)

    try:
        worker_glue_count = int(worker_evidence.get("glue_pseudo_atom_count"))
    except (TypeError, ValueError):
        worker_glue_count = -1
    recorded_inspection = (
        protocol_evidence.get("inspection")
        if isinstance(protocol_evidence.get("inspection"), dict)
        else {}
    )
    recorded_glue_atoms = recorded_inspection.get("glue_pseudo_atoms")
    recorded_glue_count = (
        len(recorded_glue_atoms) if isinstance(recorded_glue_atoms, list) else -1
    )
    actual_glue_count = -1
    actual_embedded_topology = False
    try:
        from dockstart_core.advanced_protocols import (  # noqa: PLC0415
            inspect_meeko_ligand_pdbqt,
        )

        actual_inspection = inspect_meeko_ligand_pdbqt(ligand_path)
        actual_glue_count = len(actual_inspection.get("glue_pseudo_atoms") or [])
        actual_embedded_topology = (
            actual_inspection.get("embedded_topology") is True
        )
    except Exception as exc:  # noqa: BLE001 - convert to an attribution issue.
        _ = exc
        issue("当前 ligand.pdbqt 无法复核 G* 证据。")
    expected_glue_count = 2 * len(contract_bonds)
    if not (
        worker_glue_count
        == recorded_glue_count
        == actual_glue_count
        == expected_glue_count
    ):
        issue("G* 胶合伪原子数量与正式断环键证据不一致。")
    if recorded_inspection.get("embedded_topology") is not True or not actual_embedded_topology:
        issue("当前 ligand.pdbqt 缺少可复核的 Meeko 拓扑映射。")

    if issues:
        return {
            "matched": False,
            "integrity": "rejected",
            "reason": "正式大环准备记录未通过完整性校验：" + "；".join(issues),
            "integrity_issues": issues,
        }

    meeko_version = str(
        worker_evidence.get("meeko_version")
        or metadata.get("meeko_version")
        or tool_versions.get("meeko")
        or ""
    )
    rdkit_version = str(
        worker_evidence.get("rdkit_version")
        or metadata.get("rdkit_version")
        or tool_versions.get("rdkit")
        or ""
    )
    return {
        "matched": True,
        "integrity": "formal_reviewed",
        "evidence_level": "formal",
        "formal_reviewed": True,
        "metadata_file": Path(metadata_file).as_posix(),
        "metadata_sha256": _sha256_file(metadata_path),
        "ligand_sha256": ligand_sha256,
        "prep_id": str(metadata.get("prep_id") or ""),
        "method": str(metadata.get("method") or "meeko_macrocycle"),
        "protocol": "meeko_macrocycle",
        "protocol_mode": "reviewed",
        "options": copy.deepcopy(metadata.get("options") or {}),
        "protocol_evidence": {"ok": True, "mode": "reviewed", "issues": []},
        "meeko_version": meeko_version,
        "rdkit_version": rdkit_version,
        "python_source": str(metadata.get("python_source") or "unknown"),
        "macrocycle_summary": {
            "status": "正式审查",
            "selection_mode": selection_mode,
            "review_id": review_id,
            "candidate_id": candidate_id,
            "break_bonds": bond_summaries,
            "exact_bonds_zero_based": contract_bonds,
            "glue_pseudo_atom_count": actual_glue_count,
            "embedded_topology": True,
            "hydrogen_policy": MACROCYCLE_HYDROGEN_POLICY,
            "bond_topology_sha256": bond_topology_sha256,
            "contract_sha256": contract_sha256,
            "evidence_sha256": evidence_sha256,
            "frozen_input_sha256": frozen_input_sha256,
            "frozen_input_size_bytes": frozen_input_size,
            "records": {
                "contract_file": contract_file,
                "evidence_file": evidence_file,
                "frozen_input_file": frozen_input_file,
            },
            "meeko_version": meeko_version,
            "rdkit_version": rdkit_version,
        },
    }


def _matching_ligand_preparation(
    project_path: Path,
    project: DockStartProject,
) -> dict[str, Any]:
    """Attribute the active ligand only when preparation evidence matches its bytes."""

    metadata_file = str(project.preparation.ligand.metadata_file or "")
    ligand_file = str(project.ligand.file or "")
    if not metadata_file or not ligand_file:
        return {"matched": False, "reason": "没有可匹配的配体准备记录。"}
    metadata_path, metadata_error = _project_relative_existing_file(
        project_path,
        metadata_file,
        "LIGAND_PREPARATION_METADATA",
        "配体准备 metadata.json",
    )
    ligand_path, ligand_error = _project_relative_existing_file(
        project_path,
        ligand_file,
        "LIGAND_FILE",
        "ligand.pdbqt",
    )
    if metadata_error or ligand_error or metadata_path is None or ligand_path is None:
        return {"matched": False, "reason": "配体准备记录或当前 PDBQT 不可读取。"}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"matched": False, "reason": "配体准备 metadata.json 无法解析。"}
    if not isinstance(metadata, dict):
        return {"matched": False, "reason": "配体准备 metadata.json 顶层不是对象。"}
    output = metadata.get("output") if isinstance(metadata.get("output"), dict) else {}
    expected = str(output.get("sha256") or "")
    actual = _sha256_file(ligand_path)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected) or expected.lower() != actual.lower():
        return {"matched": False, "reason": "当前 ligand.pdbqt 与准备记录 SHA256 不一致。"}

    protocol = str(metadata.get("protocol") or "standard")
    protocol_mode = str(metadata.get("protocol_mode") or "").strip().lower()
    if protocol == "meeko_macrocycle" and protocol_mode == "reviewed":
        return _formal_macrocycle_preparation_match(
            project_path,
            metadata,
            metadata_file=metadata_file,
            metadata_path=metadata_path,
            ligand_file=ligand_file,
            ligand_path=ligand_path,
            ligand_sha256=actual,
        )

    result = {
        "matched": True,
        "metadata_file": Path(metadata_file).as_posix(),
        "metadata_sha256": _sha256_file(metadata_path),
        "ligand_sha256": actual,
        "prep_id": str(metadata.get("prep_id") or ""),
        "method": str(metadata.get("method") or "external_manual"),
        "protocol": protocol,
        "options": copy.deepcopy(metadata.get("options") or {}),
        "protocol_evidence": copy.deepcopy(metadata.get("protocol_evidence") or {}),
        "meeko_version": str(metadata.get("meeko_version") or ""),
        "rdkit_version": str(metadata.get("rdkit_version") or ""),
        "python_source": str(metadata.get("python_source") or "unknown"),
    }
    if protocol == "meeko_macrocycle":
        legacy_evidence = (
            metadata.get("protocol_evidence")
            if isinstance(metadata.get("protocol_evidence"), dict)
            else {}
        )
        inspection = (
            legacy_evidence.get("inspection")
            if isinstance(legacy_evidence.get("inspection"), dict)
            else {}
        )
        glue_atoms = inspection.get("glue_pseudo_atoms")
        macrocycle_options = (
            (metadata.get("options") or {}).get("macrocycle")
            if isinstance(metadata.get("options"), dict)
            and isinstance((metadata.get("options") or {}).get("macrocycle"), dict)
            else {}
        )
        result.update(
            {
                "integrity": "legacy_partial",
                "evidence_level": "partial",
                "formal_reviewed": False,
                "protocol_mode": "legacy",
                "protocol_evidence": {
                    "ok": legacy_evidence.get("ok") is True,
                    "mode": "legacy",
                    "embedded_topology": inspection.get("embedded_topology")
                    is True,
                },
                "macrocycle_summary": {
                    "status": "旧版兼容（部分证据）",
                    "selection_mode": str(
                        macrocycle_options.get("mode") or "legacy"
                    ),
                    "review_id": "",
                    "candidate_id": "",
                    "break_bonds": [],
                    "exact_bonds_zero_based": [],
                    "glue_pseudo_atom_count": (
                        len(glue_atoms) if isinstance(glue_atoms, list) else None
                    ),
                    "embedded_topology": inspection.get("embedded_topology")
                    is True,
                    "contract_sha256": "",
                    "evidence_sha256": "",
                    "frozen_input_sha256": "",
                    "meeko_version": str(metadata.get("meeko_version") or ""),
                    "rdkit_version": str(metadata.get("rdkit_version") or ""),
                    "limitation": (
                        "旧版 auto/rigid 记录没有人工确认合同和精确断环键证据，"
                        "不能视为正式大环审查。"
                    ),
                },
            }
        )
    return result


def _build_vina_command(
    vina_path: str,
    config_file: str,
    run_id: str,
    flex_file: str = "",
    *,
    scoring_protocol: str = "vina",
    maps_prefix: str = "",
    grid_source: str = "receptor",
    maps_scoring: str = "vina",
    run_mode: str = "dock",
    autobox: bool = False,
) -> list[str]:
    normalized_mode = _normalize_run_mode(run_mode)
    command = [
        vina_path or "vina",
        "--config",
        Path(config_file).as_posix(),
    ]
    if scoring_protocol == "ad4_maps":
        command.extend(["--maps", Path(maps_prefix).as_posix(), "--scoring", "ad4"])
    elif grid_source == "precomputed_maps":
        command.extend(
            [
                "--maps",
                Path(maps_prefix).as_posix(),
                "--scoring",
                str(maps_scoring or "vina"),
            ]
        )
    if normalized_mode == "score_only":
        command.append("--score_only")
    elif normalized_mode == "local_only":
        command.append("--local_only")
    output_file = _run_output_file(run_id, normalized_mode)
    if output_file:
        command.extend(["--out", output_file])
    if normalized_mode != "dock" and autobox and scoring_protocol != "ad4_maps":
        command.append("--autobox")
    if flex_file:
        command.extend(["--flex", Path(flex_file).as_posix()])
    return command


def _local_only_baseline_files(run_id: str) -> dict[str, str]:
    return {
        "stdout_file": Path("runs", run_id, "baseline_stdout.txt").as_posix(),
        "stderr_file": Path("runs", run_id, "baseline_stderr.txt").as_posix(),
        "log_file": Path("runs", run_id, "baseline_log.txt").as_posix(),
    }


def _build_local_only_execution_plan(
    vina_path: str,
    config_file: str,
    run_id: str,
    flex_file: str = "",
    *,
    scoring_protocol: str = "vina",
    maps_prefix: str = "",
    grid_source: str = "receptor",
    maps_scoring: str = "vina",
    autobox: bool = False,
) -> dict[str, Any]:
    baseline_files = _local_only_baseline_files(run_id)
    baseline_command = _build_vina_command(
        vina_path,
        config_file,
        run_id,
        flex_file,
        scoring_protocol=scoring_protocol,
        maps_prefix=maps_prefix,
        grid_source=grid_source,
        maps_scoring=maps_scoring,
        run_mode="score_only",
        autobox=autobox,
    )
    local_command = _build_vina_command(
        vina_path,
        config_file,
        run_id,
        flex_file,
        scoring_protocol=scoring_protocol,
        maps_prefix=maps_prefix,
        grid_source=grid_source,
        maps_scoring=maps_scoring,
        run_mode="local_only",
        autobox=autobox,
    )
    return {
        "schema_version": LOCAL_ONLY_EXECUTION_PLAN_SCHEMA_VERSION,
        "kind": LOCAL_ONLY_EXECUTION_PLAN_KIND,
        "stages": [
            {
                "id": "input_score",
                "label": "输入姿势评分",
                "run_mode": "score_only",
                "command": baseline_command,
                **baseline_files,
                "output_file": "",
            },
            {
                "id": "local_optimization",
                "label": "局部优化",
                "run_mode": "local_only",
                "command": local_command,
                "stdout_file": Path("runs", run_id, "stdout.txt").as_posix(),
                "stderr_file": Path("runs", run_id, "stderr.txt").as_posix(),
                "log_file": Path("runs", run_id, "log.txt").as_posix(),
                "output_file": _run_output_file(run_id, "local_only"),
            },
        ],
        "comparison": {
            "score_change_definition": "optimized_minus_input",
            "same_config_snapshot_required": True,
            "same_input_snapshot_required": True,
        },
    }


def _local_only_execution_plan_enabled(metadata: dict[str, Any]) -> bool:
    plan = metadata.get("execution_plan")
    return bool(
        _metadata_run_mode(metadata) == "local_only"
        and isinstance(plan, dict)
        and plan.get("schema_version") == LOCAL_ONLY_EXECUTION_PLAN_SCHEMA_VERSION
        and plan.get("kind") == LOCAL_ONLY_EXECUTION_PLAN_KIND
    )


def _format_local_only_execution_plan_preview(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            continue
        command = stage.get("command")
        if not isinstance(command, list):
            continue
        label = str(stage.get("label") or stage.get("id") or f"阶段 {index}")
        lines.append(f"# 阶段 {index}：{label}")
        lines.append(_format_command_preview([str(item) for item in command]))
    return "\n".join(lines)


def _build_run_snapshot_config(
    project: DockStartProject,
    run_id: str,
    *,
    scoring_protocol: str = "vina",
    grid_source: str = "receptor",
    run_mode: str = "dock",
    autobox: bool = False,
) -> str:
    normalized_mode = _normalize_run_mode(run_mode)
    receptor = Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()
    ligand = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    if scoring_protocol == "ad4_maps":
        lines = [f"ligand = {ligand}", "scoring = ad4", ""]
    elif uses_vina_maps:
        lines = [
            f"ligand = {ligand}",
            f"scoring = {project.vina.scoring}",
            "",
        ]
    else:
        lines = [
            f"receptor = {receptor}",
            f"ligand = {ligand}",
            f"scoring = {project.vina.scoring}",
            "",
        ]
        if normalized_mode == "dock" or not autobox:
            lines.extend(
                [
                    f"center_x = {_format_config_number(project.box.center_x)}",
                    f"center_y = {_format_config_number(project.box.center_y)}",
                    f"center_z = {_format_config_number(project.box.center_z)}",
                    "",
                    f"size_x = {_format_config_number(project.box.size_x)}",
                    f"size_y = {_format_config_number(project.box.size_y)}",
                    f"size_z = {_format_config_number(project.box.size_z)}",
                    "",
                ]
            )
    if normalized_mode == "dock":
        lines.extend(
            [
                f"exhaustiveness = {project.vina.exhaustiveness}",
                f"max_evals = {project.vina.max_evals}",
                f"num_modes = {project.vina.num_modes}",
                f"min_rmsd = {_format_config_number(project.vina.min_rmsd)}",
                f"energy_range = {_format_config_number(project.vina.energy_range)}",
                f"cpu = {project.vina.cpu}",
            ]
        )
    else:
        lines.append(f"cpu = {project.vina.cpu}")
    if scoring_protocol != "ad4_maps" and not uses_vina_maps:
        lines.append(f"spacing = {_format_config_number(project.vina.spacing)}")
        if (
            normalized_mode == "score_only"
            and _project_receptor_mode(project) != "flexible"
            and project.vina.unbound_energy is not None
        ):
            lines.append(
                "unbound_energy = "
                f"{_format_config_number(project.vina.unbound_energy)}"
            )
        if project.vina.no_refine:
            lines.append("no_refine = true")
        if project.vina.force_even_voxels:
            lines.append("force_even_voxels = true")
    lines.append(f"verbosity = {project.vina.verbosity}")
    if normalized_mode == "dock" and project.vina.seed is not None:
        lines.append(f"seed = {project.vina.seed}")
    return "\n".join(lines) + "\n"


def _format_command_preview(command: list[str]) -> str:
    return " ".join(f'"{part}"' if any(char.isspace() for char in part) else part for part in command)


def validate_run_prerequisites(project_dir: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        error = loaded.get("error", {})
        checks.append(
            _run_check(
                "project_json",
                "project.json",
                "missing",
                error.get("message", "没有找到 project.json。"),
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(
            error.get("code", "PROJECT_JSON_NOT_FOUND"),
            error.get("message", "没有找到 project.json，无法准备运行记录。"),
            checks,
            error.get("raw_error", ""),
            error.get("suggestion", "请先创建或选择 DockStart 项目。"),
        )

    project_path = Path(project_dir).expanduser()
    project = _project_from_dict(loaded["project"], project_path)
    scoring_protocol = _project_scoring_protocol(project)
    protocol_id = _project_protocol_id(project)
    grid_source = _project_grid_source(project)
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    run_mode = _project_run_mode(project)
    autobox = _project_autobox(project)
    if protocol_id == AD4ZN_PROTOCOL_ID and run_mode != "dock":
        checks.append(
            _run_check(
                "ad4zn_protocol",
                "AD4Zn beta",
                "error",
                "AD4Zn beta 只支持单配体全局对接。",
            )
        )
        return _run_error(
            "AD4ZN_RUN_MODE_UNSUPPORTED",
            "AD4Zn beta 只支持单配体全局对接。",
            checks,
            raw_error=f"run_mode={run_mode}",
            suggestion="请将任务类型切换为全局对接。",
        )
    checks.append(_run_check("project_json", "project.json", "ok", "已读取项目配置。", "project.json"))

    receptor_inputs = _active_receptor_inputs(project_path, project)
    if not receptor_inputs.get("ok"):
        error = receptor_inputs.get("error", {})
        checks.append(
            _run_check(
                "flexible_receptor",
                "柔性侧链受体",
                "error",
                str(error.get("message") or "柔性侧链受体不可用。"),
                raw_error=str(error.get("raw_error") or ""),
            )
        )
        return _run_error(
            str(error.get("code") or "FLEX_RECEPTOR_NOT_READY"),
            str(error.get("message") or "柔性侧链受体不可用。"),
            checks,
            str(error.get("raw_error") or ""),
            str(error.get("suggestion") or ""),
        )
    receptor_file = str(receptor_inputs["receptor_file"])
    flex_file = str(receptor_inputs.get("flex_file") or "")
    if uses_vina_maps and flex_file:
        checks.append(
            _run_check(
                "ad4_maps",
                "AutoDock4 affinity maps",
                "error",
                "Vina/Vinardo 预计算 maps 运行只支持刚性受体。",
            )
        )
        return _run_error(
            "MAPS_FLEXIBLE_RECEPTOR_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 运行只支持刚性受体。",
            checks,
            suggestion="请切换回刚性受体，或改用 AutoDock4 maps 柔性对接流程。",
        )
    if uses_vina_maps and run_mode != "dock":
        checks.append(
            _run_check(
                "vina_maps",
                "Vina/Vinardo 预计算 maps",
                "error",
                "Vina/Vinardo 预计算 maps 当前只支持全局对接。",
            )
        )
        return _run_error(
            "VINA_MAPS_RUN_MODE_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 当前只支持全局对接。",
            checks,
            raw_error=f"run_mode={run_mode}",
            suggestion="请切换为全局对接，或将网格来源切换回受体。",
        )
    receptor_path, receptor_error = _project_relative_file(project_path, receptor_file, "receptor")
    if receptor_error:
        receptor_error = _prepared_input_hint(project, project_path, "receptor", receptor_error)
        error = receptor_error["error"]
        checks.append(
            _run_check(
                "receptor",
                "receptor.pdbqt",
                _status_from_error_code(error["code"]),
                error["message"],
                receptor_file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    if receptor_path and receptor_path.stat().st_size == 0:
        checks.append(
            _run_check(
                "receptor",
                "receptor.pdbqt",
                "error",
                "受体 PDBQT 文件为空，无法准备运行记录。",
                receptor_file,
                raw_error=str(receptor_path),
            ),
        )
        return _run_error(
            "RECEPTOR_FILE_EMPTY",
            "受体 PDBQT 文件为空，无法准备运行记录。",
            checks,
            str(receptor_path),
            "请重新导入非空的 receptor.pdbqt。",
        )
    checks.append(_run_check("receptor", "receptor.pdbqt", "ok", "已找到受体 PDBQT 文件。", receptor_file))
    if flex_file:
        flex_path, flex_error = _project_relative_file(project_path, flex_file, "flex")
        if flex_error or flex_path is None or flex_path.stat().st_size <= 0:
            error = (flex_error or _error("FLEX_RECEPTOR_FILE_EMPTY", "柔性侧链 PDBQT 为空。"))["error"]
            checks.append(
                _run_check(
                    "flexible_receptor",
                    "柔性侧链 PDBQT",
                    "error",
                    str(error.get("message") or "柔性侧链 PDBQT 不可用。"),
                    flex_file,
                    raw_error=str(error.get("raw_error") or ""),
                )
            )
            return _run_error(
                str(error.get("code") or "FLEX_RECEPTOR_FILE_NOT_READY"),
                str(error.get("message") or "柔性侧链 PDBQT 不可用。"),
                checks,
                str(error.get("raw_error") or ""),
                "请重新准备柔性受体，或切换回刚性模式。",
            )
        checks.append(
            _run_check(
                "flexible_receptor",
                "柔性侧链 PDBQT",
                "ok",
                "柔性侧链受体已通过来源与 SHA256 校验。",
                flex_file,
            )
        )

    ligand_path, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        ligand_error = _prepared_input_hint(project, project_path, "ligand", ligand_error)
        error = ligand_error["error"]
        checks.append(
            _run_check(
                "ligand",
                "ligand.pdbqt",
                _status_from_error_code(error["code"]),
                error["message"],
                project.ligand.file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    if ligand_path and ligand_path.stat().st_size == 0:
        checks.append(
            _run_check(
                "ligand",
                "ligand.pdbqt",
                "error",
                "配体 PDBQT 文件为空，无法准备运行记录。",
                project.ligand.file,
                raw_error=str(ligand_path),
            ),
        )
        return _run_error(
            "LIGAND_FILE_EMPTY",
            "配体 PDBQT 文件为空，无法准备运行记录。",
            checks,
            str(ligand_path),
            "请重新导入非空的 ligand.pdbqt。",
        )
    checks.append(_run_check("ligand", "ligand.pdbqt", "ok", "已找到配体 PDBQT 文件。", project.ligand.file))

    pose_input_attestation = _validate_current_pose_input_attestation(
        project_path,
        project,
        receptor_file=receptor_file,
    )
    if pose_input_attestation["required"]:
        if not pose_input_attestation["valid"]:
            error = pose_input_attestation.get("error") or {}
            checks.append(
                _run_check(
                    "pose_input_attestation",
                    "输入姿势坐标系确认",
                    (
                        "missing"
                        if pose_input_attestation["status"] == "missing"
                        else "error"
                    ),
                    str(
                        error.get("message")
                        or "当前输入姿势坐标系确认记录无效。"
                    ),
                    raw_error=str(error.get("raw_error") or ""),
                )
            )
            return _run_error(
                str(error.get("code") or "POSE_INPUT_ATTESTATION_INVALID"),
                str(
                    error.get("message")
                    or "当前输入姿势坐标系确认记录无效。"
                ),
                checks,
                str(error.get("raw_error") or ""),
                str(
                    error.get("suggestion")
                    or "请重新核对受体与配体坐标系并再次确认。"
                ),
            )
        checks.append(
            _run_check(
                "pose_input_attestation",
                "输入姿势坐标系确认",
                "ok",
                "用户确认记录与当前受体、配体 PDBQT 的 SHA256 一致。",
            )
        )

    config_file = _config_relative_path(project)
    config_path, config_error = _project_relative_existing_file(project_path, config_file, "VINA_CONFIG", "configs/vina_config.txt")
    if config_error:
        error = config_error["error"]
        checks.append(
            _run_check(
                "vina_config",
                "vina_config.txt",
                _status_from_error_code(error["code"]),
                error["message"],
                config_file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("vina_config", "vina_config.txt", "ok", "已找到 Vina 配置文件。", config_file))

    box_validation = validate_box_params(asdict(project.box))
    box_required = scoring_protocol == "ad4_maps" or uses_vina_maps or run_mode == "dock" or not autobox
    if box_required and not box_validation.get("ok"):
        error = box_validation["error"]
        checks.append(_run_check("box", "Box 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(
        _run_check(
            "box",
            "评价范围" if not box_required else "Box 参数",
            "ok",
            "将按当前配体坐标自动建立评价范围（autobox）。" if not box_required else "Box 参数格式有效。",
        )
    )

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        error = vina_validation["error"]
        checks.append(_run_check("vina_params", "Vina 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("vina_params", "Vina 参数", "ok", "Vina 参数格式有效。"))

    maps_status: dict[str, Any] | None = None
    maps_prefix = ""
    maps_scoring = str(project.vina.scoring or "vina")
    if scoring_protocol == "ad4_maps":
        maps_status = _active_ad4_maps(str(project_path))
        if not maps_status.get("ok") or not maps_status.get("ready"):
            error = maps_status.get("error") or {}
            checks.append(
                _run_check(
                    "ad4_maps",
                    "AutoDock4 affinity maps",
                    "error",
                    str(error.get("message") or "maps 未通过完整性校验。"),
                    str(maps_status.get("manifest_file") or ""),
                    raw_error=str(error.get("raw_error") or "；".join(maps_status.get("issues") or [])),
                )
            )
            return _run_error(
                str(error.get("code") or "MAPS_VALIDATION_FAILED"),
                str(error.get("message") or "AutoDock4 affinity maps 未通过完整性校验。"),
                checks,
                str(error.get("raw_error") or ""),
                str(error.get("suggestion") or "请重新生成或导入 maps。"),
            )
        maps_prefix = str(maps_status.get("maps_prefix") or "")
        if protocol_id == AD4ZN_PROTOCOL_ID:
            manifest = (
                maps_status.get("manifest")
                if isinstance(maps_status.get("manifest"), dict)
                else {}
            )
            if str(manifest.get("protocol_id") or "") != AD4ZN_PROTOCOL_ID:
                return _run_error(
                    "AD4ZN_MAPS_PROTOCOL_MISMATCH",
                    "活动 maps 不是 AD4Zn beta 专用 maps。",
                    checks,
                    raw_error=str(manifest.get("protocol_id") or ""),
                    suggestion="请重新生成 AD4Zn beta 专用 maps。",
                )
            receptor_record = (
                manifest.get("receptor")
                if isinstance(manifest.get("receptor"), dict)
                else {}
            )
            receptor_file = str(
                receptor_record.get("source_relative_path") or ""
            )
            receptor_path, ad4zn_receptor_error = _project_relative_file(
                project_path,
                receptor_file,
                "ad4zn_receptor",
            )
            if (
                ad4zn_receptor_error
                or receptor_path is None
                or receptor_path.stat().st_size <= 0
            ):
                error = (
                    ad4zn_receptor_error.get("error")
                    if isinstance(ad4zn_receptor_error, dict)
                    else {}
                )
                return _run_error(
                    str(error.get("code") or "AD4ZN_TZ_RECEPTOR_MISSING"),
                    str(error.get("message") or "AD4Zn TZ 受体不可读取。"),
                    checks,
                    raw_error=str(error.get("raw_error") or receptor_file),
                    suggestion="请重新生成 TZ 受体和专用 maps。",
                )
        checks.append(
            _run_check(
                (
                    "ad4zn_maps"
                    if protocol_id == AD4ZN_PROTOCOL_ID
                    else "ad4_maps"
                ),
                (
                    "AD4Zn beta 专用 maps"
                    if protocol_id == AD4ZN_PROTOCOL_ID
                    else "AutoDock4 affinity maps"
                ),
                "ok",
                (
                    "Zn/TZ、参数、maps 与 Box 的绑定记录完整。"
                    if protocol_id == AD4ZN_PROTOCOL_ID
                    else "maps 完整，并与当前受体、配体原子类型和 Box 一致。"
                ),
                str(maps_status.get("manifest_file") or ""),
            )
        )
    elif uses_vina_maps:
        maps_status = _active_vina_maps(
            str(project_path),
            probe_ligand=True,
        )
        if not maps_status.get("ok") or not maps_status.get("ready"):
            error = maps_status.get("error") or {}
            checks.append(
                _run_check(
                    "vina_maps",
                    "Vina/Vinardo 预计算 maps",
                    "error",
                    str(
                        error.get("message")
                        or "预计算 maps 未通过完整性与兼容性校验。"
                    ),
                    str(maps_status.get("manifest_file") or ""),
                    raw_error=str(
                        error.get("raw_error")
                        or "；".join(maps_status.get("issues") or [])
                    ),
                )
            )
            return _run_error(
                str(error.get("code") or "VINA_MAPS_VALIDATION_FAILED"),
                str(
                    error.get("message")
                    or "Vina/Vinardo 预计算 maps 未通过校验。"
                ),
                checks,
                str(error.get("raw_error") or ""),
                str(
                    error.get("suggestion")
                    or "请重新生成或导入与当前受体、Box、评分函数和配体兼容的 maps。"
                ),
            )
        manifest = (
            maps_status.get("manifest")
            if isinstance(maps_status.get("manifest"), dict)
            else {}
        )
        maps_prefix = str(maps_status.get("maps_prefix") or "")
        maps_scoring = str(
            manifest.get("scoring_function") or project.vina.scoring or "vina"
        )
        checks.append(
            _run_check(
                "vina_maps",
                "Vina/Vinardo 预计算 maps",
                "ok",
                "maps 已通过完整性、来源绑定和当前配体兼容性校验。",
                str(maps_status.get("manifest_file") or ""),
            )
        )

    settings = load_settings()
    vina_detection = vina_adapter.detect(settings.tool_paths.vina)
    vina_dict = vina_detection.to_dict()
    if vina_detection.status != "ok":
        checks.append(
            _run_check(
                "vina",
                "AutoDock Vina",
                vina_detection.status,
                vina_detection.message or "未检测到 AutoDock Vina，无法准备运行命令。",
                vina_detection.path,
                vina_detection.version,
                vina_detection.raw_error,
            ),
        )
        return _run_error(
            "VINA_NOT_AVAILABLE",
            vina_detection.message or "未检测到 AutoDock Vina，无法准备运行命令。",
            checks,
            vina_detection.raw_error,
            "请先在工具路径设置中配置 vina.exe，或确认 vina/vina.exe 已加入 PATH。",
        )
    checks.append(
        _run_check(
            "vina",
            "AutoDock Vina",
            "ok",
            vina_detection.message or "已检测到 AutoDock Vina。",
            vina_detection.path,
            vina_detection.version,
            vina_detection.raw_error,
        ),
    )
    runtime_vina = copy.deepcopy(vina_validation["vina"])
    if uses_vina_maps:
        runtime_vina["no_refine"] = False
        runtime_vina["force_even_voxels"] = False
    capability_validation = validate_vina_runtime_capabilities(
        runtime_vina,
        scoring_protocol,
        vina_detection.capabilities,
        run_mode=run_mode,
        receptor_mode=str(receptor_inputs.get("mode") or "rigid"),
        autobox=autobox,
    )
    if not capability_validation.get("ok"):
        error = capability_validation.get("error") or {}
        checks.append(
            _run_check(
                "vina_capabilities",
                "Vina 专家选项",
                "error",
                str(error.get("message") or "当前 Vina 不支持已启用的专家选项。"),
                vina_detection.path,
                vina_detection.version,
                str(error.get("raw_error") or ""),
            )
        )
        return _run_error(
            str(error.get("code") or "VINA_ADVANCED_FEATURE_UNSUPPORTED"),
            str(error.get("message") or "当前 Vina 不支持已启用的专家选项。"),
            checks,
            str(error.get("raw_error") or ""),
            str(error.get("suggestion") or "请关闭该选项或更换兼容的 Vina。"),
        )
    if capability_validation.get("required"):
        checks.append(
            _run_check(
                "vina_capabilities",
                "Vina 专家选项",
                "ok",
                "当前 Vina 已确认支持本次启用的专家选项。",
                vina_detection.path,
                vina_detection.version,
            )
        )

    next_run_id = get_next_run_id(project.project_dir)
    command = _build_vina_command(
        vina_detection.path,
        config_file,
        next_run_id,
        flex_file,
        scoring_protocol=scoring_protocol,
        maps_prefix=maps_prefix,
        grid_source=grid_source,
        maps_scoring=maps_scoring,
        run_mode=run_mode,
        autobox=autobox,
    )
    warnings = (box_validation.get("warnings", []) if box_required else []) + vina_validation.get("warnings", [])
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "checks": checks,
        "warnings": warnings,
        "config_file": Path(config_file).as_posix(),
        "config_path": str(config_path) if config_path else "",
        "next_run_id": next_run_id,
        "vina": vina_dict,
        "vina_path": vina_detection.path,
        "vina_version": vina_detection.version,
        "vina_capabilities": copy.deepcopy(vina_detection.capabilities),
        "receptor_file": receptor_file,
        "flex_file": flex_file,
        "docking_protocol": {
            **copy.deepcopy(
                project.preserved_data.get("docking_protocol")
                if isinstance(
                    project.preserved_data.get("docking_protocol"), dict
                )
                else {}
            ),
            **receptor_inputs,
            **(
                {
                    "pose_input_attestation": copy.deepcopy(
                        pose_input_attestation["attestation"]
                    )
                }
                if pose_input_attestation["required"]
                and pose_input_attestation["valid"]
                else {}
            ),
        },
        "pose_input_attestation": pose_input_attestation,
        "scoring_protocol": scoring_protocol,
        "protocol_id": protocol_id,
        "grid_source": grid_source,
        "run_mode": run_mode,
        "autobox": autobox,
        "box_required": box_required,
        "ad4_maps": maps_status,
        "vina_maps": maps_status if uses_vina_maps else None,
        "maps_prefix": maps_prefix,
        "maps_scoring": maps_scoring,
        "command": command,
        "command_preview": _format_command_preview(command),
        "message": "运行前检查通过，可以准备运行记录。",
        "error": None,
    }


def get_next_run_id(project_dir: str) -> str:
    project_path = Path(project_dir).expanduser()
    numbers: set[int] = set()
    runs_dir = project_path / "runs"
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            match = RUN_ID_PATTERN.match(child.name)
            if child.is_dir() and match:
                numbers.add(int(match.group(1)))

    project_json = _project_json_path(project_path)
    if project_json.exists():
        try:
            data = json.loads(project_json.read_text(encoding="utf-8"))
            for item in data.get("runs", []):
                if isinstance(item, dict):
                    match = RUN_ID_PATTERN.match(str(item.get("run_id", "")))
                    if match:
                        numbers.add(int(match.group(1)))
        except Exception:
            pass

    next_number = max(numbers, default=0) + 1
    return f"run_{next_number:03d}"


def build_vina_command_preview(project_dir: str, run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用 DockStart 自动生成的 run_id。",
        )

    prerequisites = validate_run_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    run_mode = _normalize_run_mode(prerequisites.get("run_mode"))
    command = _build_vina_command(
        prerequisites["vina_path"],
        prerequisites["config_file"],
        run_id,
        str(prerequisites.get("flex_file") or ""),
        scoring_protocol=str(prerequisites.get("scoring_protocol") or "vina"),
        maps_prefix=str(prerequisites.get("maps_prefix") or ""),
        grid_source=str(prerequisites.get("grid_source") or "receptor"),
        maps_scoring=str(prerequisites.get("maps_scoring") or "vina"),
        run_mode=run_mode,
        autobox=bool(prerequisites.get("autobox")),
    )
    execution_plan = (
        _build_local_only_execution_plan(
            prerequisites["vina_path"],
            prerequisites["config_file"],
            run_id,
            str(prerequisites.get("flex_file") or ""),
            scoring_protocol=str(prerequisites.get("scoring_protocol") or "vina"),
            maps_prefix=str(prerequisites.get("maps_prefix") or ""),
            grid_source=str(prerequisites.get("grid_source") or "receptor"),
            maps_scoring=str(prerequisites.get("maps_scoring") or "vina"),
            autobox=bool(prerequisites.get("autobox")),
        )
        if run_mode == "local_only"
        else None
    )
    command_preview = (
        _format_local_only_execution_plan_preview(execution_plan)
        if execution_plan is not None
        else _format_command_preview(command)
    )
    return {
        "ok": True,
        "project_dir": prerequisites["project_dir"],
        "project": prerequisites["project"],
        "run_id": run_id,
        "command": command,
        "commands": (
            [
                copy.deepcopy(stage.get("command") or [])
                for stage in execution_plan.get("stages", [])
                if isinstance(stage, dict)
            ]
            if execution_plan is not None
            else [command]
        ),
        "execution_plan": execution_plan,
        "command_preview": command_preview,
        "checks": prerequisites.get("checks", []),
        "warnings": prerequisites.get("warnings", []),
        "message": (
            "Vina 两阶段命令预览已生成；将先评分输入姿势，再执行局部优化。"
            if execution_plan is not None
            else "Vina 命令预览已生成；当前版本不会执行该命令。"
        ),
        "error": None,
    }


def prepare_vina_run(project_dir: str) -> dict[str, Any]:
    prerequisites = validate_run_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    project_root = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(prerequisites["project"], project_root)
    project.project_dir = str(project_root)
    run_id = prerequisites["next_run_id"]
    try:
        run_dir = _safe_run_directory(project_root, run_id, require_exists=False)
    except Exception as exc:  # noqa: BLE001 - reject symlinked/reparsed run roots.
        return _run_error(
            "RUN_PATH_UNSAFE",
            "runs 目录或待创建的 run 路径不安全，已拒绝准备运行。",
            prerequisites.get("checks", []),
            str(exc),
            "请恢复项目内普通的 runs 目录后重试。",
        )
    if run_dir.exists():
        return _run_error(
            "RUN_DIR_EXISTS",
            f"{run_id} 已存在，DockStart 不会覆盖已有运行目录。",
            prerequisites.get("checks", []),
            str(run_dir),
            "请重新检查 runs 目录，或保留已有运行记录后再次准备。",
        )

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=False)
        created_at = _now_iso()
        config_file = prerequisites["config_file"]

        metadata_file = Path("runs", run_id, "metadata.json").as_posix()
        command_preview_file = Path("runs", run_id, "command_preview.txt").as_posix()
        config_snapshot_file = Path("runs", run_id, "config_snapshot.txt").as_posix()
        receptor_snapshot_file = Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()
        ligand_snapshot_file = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
        flex_snapshot_file = (
            Path("runs", run_id, "inputs", "flex.pdbqt").as_posix()
            if prerequisites.get("flex_file")
            else ""
        )
        flexible_protocol_snapshot_file = (
            Path(
                "runs",
                run_id,
                "inputs",
                "flexible_receptor_protocol.json",
            ).as_posix()
            if flex_snapshot_file
            else ""
        )
        scoring_protocol = str(prerequisites.get("scoring_protocol") or "vina")
        protocol_id = str(
            prerequisites.get("protocol_id")
            or (
                prerequisites.get("docking_protocol")
                if isinstance(prerequisites.get("docking_protocol"), dict)
                else {}
            ).get("protocol_id")
            or ""
        ).strip().lower()
        is_ad4zn = (
            scoring_protocol == "ad4_maps"
            and protocol_id == AD4ZN_PROTOCOL_ID
        )
        grid_source = str(prerequisites.get("grid_source") or "receptor")
        uses_vina_maps = (
            scoring_protocol == "vina"
            and grid_source == "precomputed_maps"
        )
        run_mode = _normalize_run_mode(prerequisites.get("run_mode"))
        autobox = bool(prerequisites.get("autobox"))
        ad4_maps_status = prerequisites.get("ad4_maps") if isinstance(prerequisites.get("ad4_maps"), dict) else {}
        vina_maps_status = prerequisites.get("vina_maps") if isinstance(prerequisites.get("vina_maps"), dict) else {}
        active_maps_status = (
            ad4_maps_status
            if scoring_protocol == "ad4_maps"
            else vina_maps_status
            if uses_vina_maps
            else {}
        )
        maps_snapshot_kind = (
            "ad4_maps"
            if scoring_protocol == "ad4_maps"
            else "vina_maps"
            if uses_vina_maps
            else ""
        )
        maps_scoring = str(
            prerequisites.get("maps_scoring")
            or project.vina.scoring
            or "vina"
        )
        maps_snapshot_prefix = ""
        maps_manifest_snapshot_file = ""
        maps_file_snapshots: list[dict[str, Any]] = []
        ad4zn_snapshots: dict[str, Any] = {}
        if maps_snapshot_kind:
            source_manifest = active_maps_status.get("manifest")
            if not isinstance(source_manifest, dict):
                raise RuntimeError("maps manifest 未包含在运行前检查结果中。")
            source_maps = source_manifest.get("maps") if isinstance(source_manifest.get("maps"), dict) else {}
            source_files = source_maps.get("files") if isinstance(source_maps.get("files"), list) else []
            source_prefix = Path(str(source_maps.get("prefix") or "")).name
            if not source_prefix or not source_files:
                raise RuntimeError("maps manifest 缺少 prefix 或文件清单。")
            maps_snapshot_dir = inputs_dir / "maps"
            maps_snapshot_dir.mkdir(parents=True, exist_ok=False)
            for item in source_files:
                if not isinstance(item, dict):
                    raise RuntimeError("maps manifest 包含无效文件记录。")
                source_relative = str(item.get("relative_path") or "")
                source_path, source_error = _project_relative_existing_file(
                    project_root,
                    source_relative,
                    "MAP",
                    "预计算 map",
                )
                if source_error or source_path is None:
                    raise RuntimeError(str((source_error or {}).get("error") or "预计算 map 不可读取。"))
                target_path = maps_snapshot_dir / source_path.name
                shutil.copyfile(source_path, target_path)
                target_relative = Path("runs", run_id, "inputs", "maps", target_path.name).as_posix()
                maps_file_snapshots.append(
                    {
                        "name": target_path.name,
                        "source_relative_path": Path(source_relative).as_posix(),
                        **_hash_snapshot(target_path, target_relative),
                    }
                )
            maps_manifest_snapshot_file = Path(
                "runs",
                run_id,
                "inputs",
                "maps",
                "manifest.json",
            ).as_posix()
            _atomic_write_text(
                project_root / maps_manifest_snapshot_file,
                json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
            )
            maps_snapshot_prefix = Path(
                "runs",
                run_id,
                "inputs",
                "maps",
                source_prefix,
            ).as_posix()
            if is_ad4zn:
                ad4zn_dir = inputs_dir / "ad4zn"
                ad4zn_dir.mkdir(parents=True, exist_ok=False)
                source_receptor_record = (
                    source_manifest.get("receptor")
                    if isinstance(source_manifest.get("receptor"), dict)
                    else {}
                )
                source_parameter_record = (
                    source_manifest.get("parameter_file")
                    if isinstance(source_manifest.get("parameter_file"), dict)
                    else {}
                )
                source_gpf_record = (
                    source_manifest.get("gpf")
                    if isinstance(source_manifest.get("gpf"), dict)
                    else {}
                )
                evidence_specs = (
                    (
                        "original_receptor",
                        str(project.receptor.file or ""),
                        "original_receptor.pdbqt",
                        str(
                            source_receptor_record.get(
                                "original_source_sha256"
                            )
                            or ""
                        ),
                    ),
                    (
                        "parameter_file",
                        str(
                            source_parameter_record.get("relative_path")
                            or ""
                        ),
                        "AD4Zn.dat",
                        str(source_parameter_record.get("sha256") or ""),
                    ),
                    (
                        "gpf",
                        str(source_gpf_record.get("relative_path") or ""),
                        "receptor.gpf",
                        str(source_gpf_record.get("sha256") or ""),
                    ),
                )
                for key, source_relative, target_name, expected_hash in evidence_specs:
                    source_path, source_error = _project_relative_existing_file(
                        project_root,
                        source_relative,
                        f"AD4ZN_{key.upper()}",
                        f"AD4Zn {key}",
                    )
                    if source_error or source_path is None:
                        raise RuntimeError(
                            str(
                                (source_error or {}).get("error")
                                or f"AD4Zn {key} 不可读取。"
                            )
                        )
                    if (
                        not SHA256_PATTERN.fullmatch(expected_hash.lower())
                        or _sha256_file(source_path).lower()
                        != expected_hash.lower()
                    ):
                        raise RuntimeError(
                            f"AD4Zn {key} 与 maps manifest 的 SHA256 不一致。"
                        )
                    target_path = ad4zn_dir / target_name
                    shutil.copyfile(source_path, target_path)
                    target_relative = Path(
                        "runs",
                        run_id,
                        "inputs",
                        "ad4zn",
                        target_name,
                    ).as_posix()
                    ad4zn_snapshots[key] = {
                        "source_relative_path": Path(
                            source_relative
                        ).as_posix(),
                        **_hash_snapshot(target_path, target_relative),
                    }
                review_payload = copy.deepcopy(
                    source_manifest.get("ad4zn")
                    if isinstance(source_manifest.get("ad4zn"), dict)
                    else {}
                )
                review_relative = Path(
                    "runs",
                    run_id,
                    "inputs",
                    "ad4zn",
                    "protocol_record.json",
                ).as_posix()
                _atomic_write_text(
                    project_root / review_relative,
                    json.dumps(review_payload, ensure_ascii=False, indent=2)
                    + "\n",
                )
                ad4zn_snapshots["protocol_record"] = _hash_snapshot(
                    project_root / review_relative,
                    review_relative,
                )
        output_file = _run_output_file(run_id, run_mode)
        pose_file = _run_pose_file(run_id, run_mode)
        log_file = Path("runs", run_id, "log.txt").as_posix()
        command = _build_vina_command(
            prerequisites["vina_path"],
            config_snapshot_file,
            run_id,
            flex_snapshot_file,
            scoring_protocol=scoring_protocol,
            maps_prefix=maps_snapshot_prefix,
            grid_source=grid_source,
            maps_scoring=maps_scoring,
            run_mode=run_mode,
            autobox=autobox,
        )
        execution_plan = (
            _build_local_only_execution_plan(
                prerequisites["vina_path"],
                config_snapshot_file,
                run_id,
                flex_snapshot_file,
                scoring_protocol=scoring_protocol,
                maps_prefix=maps_snapshot_prefix,
                grid_source=grid_source,
                maps_scoring=maps_scoring,
                autobox=autobox,
            )
            if run_mode == "local_only"
            else None
        )
        receptor_source_file = str(prerequisites.get("receptor_file") or project.receptor.file)
        receptor_path = project_root / receptor_source_file
        ligand_path = project_root / project.ligand.file
        receptor_snapshot_path = project_root / receptor_snapshot_file
        ligand_snapshot_path = project_root / ligand_snapshot_file
        flex_snapshot_path = project_root / flex_snapshot_file if flex_snapshot_file else None
        config_snapshot_path = project_root / config_snapshot_file
        shutil.copyfile(receptor_path, receptor_snapshot_path)
        shutil.copyfile(ligand_path, ligand_snapshot_path)
        if flex_snapshot_path is not None:
            shutil.copyfile(project_root / str(prerequisites["flex_file"]), flex_snapshot_path)
        flexible_protocol_snapshot = None
        if flexible_protocol_snapshot_file:
            active_flexible_protocol = (
                prerequisites.get("docking_protocol")
                if isinstance(prerequisites.get("docking_protocol"), dict)
                else {}
            )
            flexible_protocol_payload = {
                "schema_version": 2,
                "protocol_id": FLEXIBLE_RECEPTOR_PROTOCOL_ID,
                "mode": "flexible",
                "preparation_id": str(
                    active_flexible_protocol.get("preparation_id") or ""
                ),
                "source_raw_file": str(
                    active_flexible_protocol.get("source_raw_file") or ""
                ),
                "source_format": str(
                    active_flexible_protocol.get("source_format") or ""
                ),
                "source_sha256": str(
                    active_flexible_protocol.get("source_sha256") or ""
                ),
                "selected_residues": copy.deepcopy(
                    active_flexible_protocol.get("selected_residues") or []
                ),
                "resolved_altlocs": copy.deepcopy(
                    active_flexible_protocol.get("resolved_altlocs") or {}
                ),
                "receptor_controls": copy.deepcopy(
                    active_flexible_protocol.get("receptor_controls") or {}
                ),
                "receptor_controls_sha256": str(
                    active_flexible_protocol.get(
                        "receptor_controls_sha256"
                    )
                    or ""
                ),
                "receptor_controls_fingerprint": copy.deepcopy(
                    active_flexible_protocol.get(
                        "receptor_controls_fingerprint"
                    )
                    or {}
                ),
                "atom_partition": copy.deepcopy(
                    active_flexible_protocol.get("atom_partition") or {}
                ),
                "identity": copy.deepcopy(
                    active_flexible_protocol.get("identity") or {}
                ),
                "prepared_output_sha256": copy.deepcopy(
                    active_flexible_protocol.get("sha256") or {}
                ),
                **(
                    {
                        "analysis_contracts": {
                            "flexible_movement": {
                                "schema_id": FLEXIBLE_MOVEMENT_SCHEMA_ID,
                                "method": FLEXIBLE_MOVEMENT_METHOD,
                                "artifact_file": (
                                    _flexible_movement_relative_path(
                                        run_id
                                    )
                                ),
                                "required_after_analysis": True,
                                "exclude_flexible_ca_root": True,
                            },
                        },
                    }
                    if run_mode == "dock"
                    else {}
                ),
            }
            flexible_protocol_snapshot_path = (
                project_root / flexible_protocol_snapshot_file
            )
            _atomic_write_text(
                flexible_protocol_snapshot_path,
                json.dumps(
                    flexible_protocol_payload,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            flexible_protocol_snapshot = _hash_snapshot(
                flexible_protocol_snapshot_path,
                flexible_protocol_snapshot_file,
            )
        _atomic_write_text(
            config_snapshot_path,
            _build_run_snapshot_config(
                project,
                run_id,
                scoring_protocol=scoring_protocol,
                grid_source=grid_source,
                run_mode=run_mode,
                autobox=autobox,
            ),
        )
        receptor_snapshot = _parse_pdbqt_stats(receptor_snapshot_path, receptor_snapshot_file)
        receptor_snapshot["source_relative_path"] = Path(receptor_source_file).as_posix()
        ligand_snapshot = _parse_pdbqt_stats(ligand_snapshot_path, ligand_snapshot_file, ligand=True)
        ligand_snapshot["source_relative_path"] = Path(project.ligand.file).as_posix()
        ligand_preparation = _matching_ligand_preparation(project_root, project)
        ligand_preparation_snapshot_file = ""
        ligand_preparation_snapshot = None
        if ligand_preparation.get("matched"):
            ligand_preparation_snapshot_file = Path(
                "runs", run_id, "inputs", "ligand_preparation.json"
            ).as_posix()
            ligand_preparation_snapshot_path = project_root / ligand_preparation_snapshot_file
            _atomic_write_text(
                ligand_preparation_snapshot_path,
                json.dumps(ligand_preparation, ensure_ascii=False, indent=2) + "\n",
            )
            ligand_preparation_snapshot = {
                "relative_path": ligand_preparation_snapshot_file,
                "sha256": _sha256_file(ligand_preparation_snapshot_path),
                "size_bytes": ligand_preparation_snapshot_path.stat().st_size,
            }
        flex_snapshot = None
        if flex_snapshot_path is not None:
            flex_snapshot = _parse_pdbqt_stats(flex_snapshot_path, flex_snapshot_file)
            flex_snapshot["source_relative_path"] = Path(str(prerequisites["flex_file"])).as_posix()
        config_sha256 = _sha256_file(config_snapshot_path)
        maps_manifest_snapshot = (
            _hash_snapshot(project_root / maps_manifest_snapshot_file, maps_manifest_snapshot_file)
            if maps_manifest_snapshot_file
            else None
        )
        system_snapshot = _system_snapshot()
        vina_source = str((prerequisites.get("vina") or {}).get("source") or "unknown")
        vina_binary = _tool_hash_snapshot(str(prerequisites.get("vina_path") or ""))

        metadata = {
            "run_id": run_id,
            "status": "prepared",
            "stage": "prepared",
            "progress": {"percent": 0, "message": "运行记录已准备。"},
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "pid": None,
            "vina_version": prerequisites.get("vina_version", ""),
            "vina_path": prerequisites.get("vina_path", ""),
            "vina_source": vina_source,
            "app_version": __version__,
            "app": {"name": "DockStart", "version": __version__},
            "vina_tool": {
                "version": prerequisites.get("vina_version", ""),
                "path": prerequisites.get("vina_path", ""),
                "source": vina_source,
                "sha256": vina_binary["sha256"],
                "size_bytes": vina_binary["size_bytes"],
                "capabilities": copy.deepcopy(prerequisites.get("vina_capabilities") or {}),
            },
            "vina_capabilities": copy.deepcopy(prerequisites.get("vina_capabilities") or {}),
            "vina_sha256": vina_binary["sha256"],
            "system": system_snapshot,
            "command": command,
            **(
                {
                    "execution_plan": execution_plan,
                    "execution_phases": {
                        "input_score": {"status": "pending"},
                        "local_optimization": {"status": "pending"},
                    },
                }
                if execution_plan is not None
                else {}
            ),
            "config_file": Path(config_file).as_posix(),
            "config_snapshot": config_snapshot_file,
            "snapshots": {
                "inputs": {
                    "receptor": receptor_snapshot,
                    "ligand": ligand_snapshot,
                    **({"flex": flex_snapshot} if flex_snapshot is not None else {}),
                },
                **(
                    {
                        "flexible_receptor_protocol": (
                            flexible_protocol_snapshot
                        )
                    }
                    if flexible_protocol_snapshot is not None
                    else {}
                ),
                "config": {
                    "source_relative_path": Path(config_file).as_posix(),
                    "relative_path": config_snapshot_file,
                    "snapshot_file": config_snapshot_file,
                    "sha256": config_sha256,
                    "size_bytes": config_snapshot_path.stat().st_size,
                },
                **(
                    {
                        maps_snapshot_kind: {
                            "manifest": maps_manifest_snapshot,
                            "prefix": maps_snapshot_prefix,
                            "files": maps_file_snapshots,
                            "source_manifest": str(
                                active_maps_status.get("manifest_file") or ""
                            ),
                        }
                    }
                    if maps_snapshot_kind
                    else {}
                ),
                **(
                    {"ad4zn": copy.deepcopy(ad4zn_snapshots)}
                    if is_ad4zn
                    else {}
                ),
                **(
                    {"ligand_preparation": ligand_preparation_snapshot}
                    if ligand_preparation_snapshot is not None
                    else {}
                ),
                "box": asdict(project.box),
                "vina": {
                    **asdict(project.vina),
                    "scoring": "ad4" if scoring_protocol == "ad4_maps" else project.vina.scoring,
                },
            },
            "input_sha256": {
                "receptor": receptor_snapshot["sha256"],
                "ligand": ligand_snapshot["sha256"],
                "config": config_sha256,
                **({"flex": flex_snapshot["sha256"]} if flex_snapshot is not None else {}),
                **(
                    {
                        "flexible_receptor_protocol": str(
                            (
                                flexible_protocol_snapshot or {}
                            ).get("sha256")
                            or ""
                        )
                    }
                    if flexible_protocol_snapshot is not None
                    else {}
                ),
                **(
                    {
                        "maps_manifest": str((maps_manifest_snapshot or {}).get("sha256") or ""),
                        "maps": {
                            str(item.get("name") or ""): str(item.get("sha256") or "")
                            for item in maps_file_snapshots
                        },
                    }
                    if maps_snapshot_kind
                    else {}
                ),
                **(
                    {
                        "ad4zn": {
                            key: str(value.get("sha256") or "")
                            for key, value in ad4zn_snapshots.items()
                            if isinstance(value, dict)
                        }
                    }
                    if is_ad4zn
                    else {}
                ),
            },
            "docking_protocol": {
                **copy.deepcopy(prerequisites.get("docking_protocol") or {"mode": "rigid"}),
                "run_mode": run_mode,
                "autobox": autobox,
                "protocol_id": (
                    AD4ZN_PROTOCOL_ID
                    if is_ad4zn
                    else "ad4_maps"
                    if scoring_protocol == "ad4_maps"
                    else "vina_maps"
                    if uses_vina_maps
                    else (
                        FLEXIBLE_RECEPTOR_PROTOCOL_ID
                        if str((prerequisites.get("docking_protocol") or {}).get("mode") or "").strip().lower()
                        == "flexible"
                        else "rigid_single"
                    )
                ),
                "engine": scoring_protocol,
                "grid_source": grid_source,
                **({"stability": "beta"} if is_ad4zn else {}),
            },
            **(
                {
                    "analysis_contracts": {
                        "flexible_movement": {
                            "schema_id": FLEXIBLE_MOVEMENT_SCHEMA_ID,
                            "method": FLEXIBLE_MOVEMENT_METHOD,
                            "artifact_file": Path(
                                "runs",
                                run_id,
                                FLEXIBLE_MOVEMENT_FILENAME,
                            ).as_posix(),
                            "required_after_analysis": True,
                            "exclude_flexible_ca_root": True,
                        },
                    },
                }
                if flex_snapshot is not None and run_mode == "dock"
                else {}
            ),
            "scoring_protocol": scoring_protocol,
            "scoring_function": "ad4" if scoring_protocol == "ad4_maps" else project.vina.scoring,
            "grid_source": grid_source,
            "run_mode": run_mode,
            "autobox": autobox,
            "ad4_maps": (
                {
                    "map_set_id": str(ad4_maps_status.get("map_set_id") or ""),
                    "source_manifest": str(ad4_maps_status.get("manifest_file") or ""),
                    "manifest_snapshot": maps_manifest_snapshot_file,
                    "prefix": maps_snapshot_prefix,
                    "files": maps_file_snapshots,
                    "grid": copy.deepcopy((ad4_maps_status.get("manifest") or {}).get("grid") or {}),
                    "ligand_atom_types": copy.deepcopy(
                        ((ad4_maps_status.get("manifest") or {}).get("maps") or {}).get("ligand_atom_types") or []
                    ),
                }
                if scoring_protocol == "ad4_maps"
                else None
            ),
            "ad4zn": (
                {
                    **copy.deepcopy(
                        (ad4_maps_status.get("manifest") or {}).get(
                            "ad4zn"
                        )
                        or {}
                    ),
                    "map_set_id": str(
                        ad4_maps_status.get("map_set_id") or ""
                    ),
                    "source_manifest": str(
                        ad4_maps_status.get("manifest_file") or ""
                    ),
                    "manifest_snapshot": maps_manifest_snapshot_file,
                    "prefix": maps_snapshot_prefix,
                    "snapshots": copy.deepcopy(ad4zn_snapshots),
                }
                if is_ad4zn
                else None
            ),
            "vina_maps": (
                {
                    "map_set_id": str(
                        vina_maps_status.get("map_set_id") or ""
                    ),
                    "source_manifest": str(
                        vina_maps_status.get("manifest_file") or ""
                    ),
                    "manifest_snapshot": maps_manifest_snapshot_file,
                    "prefix": maps_snapshot_prefix,
                    "files": maps_file_snapshots,
                    "scoring_function": maps_scoring,
                    "grid": copy.deepcopy(
                        (vina_maps_status.get("manifest") or {}).get("grid")
                        or {}
                    ),
                    "atom_types": copy.deepcopy(
                        (
                            (vina_maps_status.get("manifest") or {}).get(
                                "maps"
                            )
                            or {}
                        ).get("atom_types")
                        or []
                    ),
                    "semantics": copy.deepcopy(
                        (vina_maps_status.get("manifest") or {}).get(
                            "semantics"
                        )
                        or {
                            "grid_only": True,
                            "no_refine_equivalent": True,
                        }
                    ),
                    "compatibility_probe": copy.deepcopy(
                        vina_maps_status.get("compatibility_probe") or {}
                    ),
                }
                if uses_vina_maps
                else None
            ),
            "grid_execution": (
                {
                    "source": "precomputed_maps",
                    "grid_only": True,
                    "receptor_argument_used": False,
                    "no_refine_equivalent": True,
                    "message": (
                        "本次运行只使用冻结的预计算 affinity maps；"
                        "Vina 命令未传入 --receptor，因此最终优化与评分不使用显式受体原子。"
                    ),
                }
                if uses_vina_maps
                else {
                    "source": (
                        "ad4zn_precomputed_maps"
                        if is_ad4zn
                        else "ad4_precomputed_maps"
                    ),
                    "grid_only": True,
                    "receptor_argument_used": False,
                    "no_refine_equivalent": True,
                }
                if scoring_protocol == "ad4_maps"
                else {
                    "source": "receptor",
                    "grid_only": False,
                    "receptor_argument_used": True,
                    "no_refine_equivalent": False,
                }
            ),
            "ligand_preparation": ligand_preparation,
            "ligand_preparation_snapshot": ligand_preparation_snapshot_file,
            "box_snapshot": asdict(project.box),
            "vina_snapshot": {
                **asdict(project.vina),
                "scoring": "ad4" if scoring_protocol == "ad4_maps" else project.vina.scoring,
            },
            "output_file": output_file,
            "pose_file": pose_file,
            "log_file": log_file,
            "exit_code": None,
            "best_affinity": None,
            "primary_score_kcal_mol": None,
        }
        _with_artifact_hashes(metadata, {"vina_binary_prepared": vina_binary})

        command_preview = (
            _format_local_only_execution_plan_preview(execution_plan)
            if execution_plan is not None
            else _format_command_preview(command)
        )
        _atomic_write_text(run_dir / "command_preview.txt", command_preview + "\n")
        _write_run_metadata(project.project_dir, run_id, metadata)

        project.runs.append(
            {
                "run_id": run_id,
                "status": "prepared",
                "metadata_file": metadata_file,
                "created_at": created_at,
                "scoring_protocol": scoring_protocol,
                "scoring_function": "ad4" if scoring_protocol == "ad4_maps" else project.vina.scoring,
                "protocol_id": (
                    AD4ZN_PROTOCOL_ID
                    if is_ad4zn
                    else "ad4_maps"
                    if scoring_protocol == "ad4_maps"
                    else "vina_maps"
                    if uses_vina_maps
                    else "rigid_single"
                ),
                "grid_source": grid_source,
                "run_mode": run_mode,
                "autobox": autobox,
                "output_file": output_file,
                "pose_file": pose_file,
            },
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": metadata_file,
            "command_preview_file": command_preview_file,
            "config_snapshot_file": config_snapshot_file,
            "command": command,
            "commands": (
                [
                    copy.deepcopy(stage.get("command") or [])
                    for stage in execution_plan.get("stages", [])
                    if isinstance(stage, dict)
                ]
                if execution_plan is not None
                else [command]
            ),
            "execution_plan": execution_plan,
            "command_preview": command_preview,
            "checks": prerequisites.get("checks", []),
            "warnings": prerequisites.get("warnings", []),
            "message": "运行记录已准备完成，可以执行 AutoDock Vina。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _run_error(
            "RUN_PREPARE_ERROR",
            "准备运行记录时发生错误。",
            prerequisites.get("checks", []),
            str(exc),
            "请确认项目 runs 目录可写，并重新准备运行记录。",
        )


def _workflow_file_status(project_dir: Path, relative_path: str) -> dict[str, Any]:
    if not relative_path:
        return {"path": "", "exists": False, "non_empty": False, "status": "missing"}
    path = Path(relative_path)
    if not path.is_absolute():
        path = project_dir / path
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    if exists and is_file and size > 0:
        status = "ok"
    elif exists and is_file:
        status = "empty"
    else:
        status = "missing"
    return {
        "path": relative_path,
        "absolute_path": str(path),
        "exists": exists,
        "non_empty": size > 0,
        "size": size,
        "status": status,
    }


def _workflow_preparation_summary(project: DockStartProject, target: str) -> dict[str, Any]:
    prep = getattr(project.preparation, target)
    return {
        "status": prep.status,
        "method": prep.method,
        "input_file": prep.input_file,
        "output_file": prep.output_file,
        "log_file": prep.log_file,
        "error": prep.error,
    }


def _workflow_viewer_status(
    project_path: Path,
    project: DockStartProject,
    receptor_raw: dict[str, Any],
    ligand_raw: dict[str, Any],
    receptor_prepared: dict[str, Any],
    ligand_prepared: dict[str, Any],
) -> dict[str, Any]:
    available_runs: list[dict[str, Any]] = []
    for run in project.runs or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "")
        if not RUN_ID_PATTERN.match(run_id):
            continue
        run_mode = _normalize_run_mode(run.get("run_mode"))
        if run_mode == "score_only" and str(run.get("status") or "") != "finished":
            continue
        pose_file = str(run.get("pose_file") or _run_pose_file(run_id, run_mode))
        pose_status = _workflow_file_status(project_path, pose_file)
        if pose_status["status"] == "ok":
            available_runs.append(
                {
                    "run_id": run_id,
                    "status": run.get("status", ""),
                    "run_mode": run_mode,
                    "pose_file": pose_file,
                    "output_file": str(run.get("output_file") or _run_output_file(run_id, run_mode)),
                    "size": pose_status.get("size", 0),
                }
            )

    can_view_raw_receptor = receptor_raw["status"] == "ok"
    can_view_raw_ligand = ligand_raw["status"] == "ok"
    can_view_prepared_receptor = receptor_prepared["status"] == "ok"
    can_view_prepared_ligand = ligand_prepared["status"] == "ok"
    can_view_docking_output = bool(available_runs)

    if can_view_docking_output:
        available_modes = {str(item.get("run_mode") or "dock") for item in available_runs}
        if available_modes == {"score_only"}:
            recommended = "已有仅评分运行，可以打开 3D Viewer 查看本次评价的输入姿势。"
        elif available_modes <= {"score_only", "local_only"}:
            recommended = "已有姿势评价运行，可以打开 3D Viewer 查看输入或局部优化后的姿势。"
        else:
            recommended = "已有 Vina 运行结果，可以打开 3D Viewer 查看对接或姿势评价结果。"
    elif can_view_prepared_receptor or can_view_prepared_ligand:
        recommended = "已有 prepared PDBQT，可以打开 3D Viewer 查看结构和 Box。"
    elif can_view_raw_receptor or can_view_raw_ligand:
        recommended = "已有 raw 结构，可以打开 3D Viewer 预览；运行 Vina 前仍需准备 PDBQT。"
    else:
        recommended = "请先下载 raw 结构或准备 PDBQT 文件，再打开 3D Viewer。"

    return {
        "can_view_raw_receptor": can_view_raw_receptor,
        "can_view_raw_ligand": can_view_raw_ligand,
        "can_view_prepared_receptor": can_view_prepared_receptor,
        "can_view_prepared_ligand": can_view_prepared_ligand,
        "can_view_docking_output": can_view_docking_output,
        "available_runs": available_runs,
        "recommended_viewer_action": recommended,
    }


def get_project_workflow_status(project_dir: str) -> dict[str, Any]:
    loaded = recover_project_state(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    project = _project_from_dict(loaded["project"], project_path)
    scoring_protocol = _project_scoring_protocol(project)
    grid_source = _project_grid_source(project)
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    run_mode = _project_run_mode(project)
    autobox = _project_autobox(project)
    receptor_raw = _workflow_file_status(project_path, project.receptor.raw_file)
    ligand_raw = _workflow_file_status(project_path, project.ligand.raw_file)
    receptor_prepared = _workflow_file_status(project_path, project.receptor.file)
    ligand_prepared = _workflow_file_status(project_path, project.ligand.file)
    config_file = _config_relative_path(project)
    config_status = _workflow_file_status(project_path, config_file)
    box_validation = validate_box_params(asdict(project.box))
    vina_validation = validate_vina_params(asdict(project.vina))
    vina_maps_status = (
        _active_vina_maps(str(project_path), probe_ligand=False)
        if uses_vina_maps
        else None
    )
    latest_run = project.runs[-1] if project.runs else None
    latest_run_for_current_mode = next(
        (
            run
            for run in reversed(project.runs)
            if isinstance(run, dict)
            and _normalize_run_mode(run.get("run_mode")) == run_mode
        ),
        None,
    )

    if receptor_prepared["status"] != "ok":
        if project.preparation.receptor.status == "failed":
            next_action = "receptor PDBQT 自动准备失败，请查看 preparation 日志并重新准备或手动导入。"
        elif receptor_raw["status"] == "ok":
            next_action = "已下载 raw receptor，请准备 receptor PDBQT。"
        else:
            next_action = "请先下载 receptor raw 文件，或直接导入 prepared/receptor.pdbqt。"
    elif ligand_prepared["status"] != "ok":
        if project.preparation.ligand.status == "failed":
            next_action = "ligand PDBQT 自动准备失败，请查看 preparation 日志并重新准备或手动导入。"
        elif ligand_raw["status"] == "ok":
            next_action = "已下载 raw ligand，请准备 ligand PDBQT。"
        else:
            next_action = "请先下载 ligand raw 文件，或直接导入 prepared/ligand.pdbqt。"
    elif (scoring_protocol == "ad4_maps" or run_mode == "dock" or not autobox) and not box_validation.get("ok"):
        next_action = "请设置合法的 docking box 参数。"
    elif not vina_validation.get("ok"):
        next_action = "请设置合法的 Vina 参数。"
    elif uses_vina_maps and (
        not isinstance(vina_maps_status, dict)
        or not vina_maps_status.get("ok")
        or not vina_maps_status.get("ready")
    ):
        next_action = "请生成或导入与当前项目匹配的 Vina/Vinardo 预计算 maps。"
    elif config_status["status"] != "ok":
        next_action = "请生成 configs/vina_config.txt。"
    elif latest_run_for_current_mode is None:
        next_action = "输入文件和参数已就绪，可以准备并运行 Vina。"
    elif latest_run_for_current_mode.get("status") == "prepared":
        next_action = f"{latest_run_for_current_mode.get('run_id')} 已准备，可以执行 Vina。"
    elif latest_run_for_current_mode.get("status") == "finished":
        next_action = f"{latest_run_for_current_mode.get('run_id')} 已完成，可以查看结果或导出报告。"
    elif latest_run_for_current_mode.get("status") == "failed":
        next_action = f"{latest_run_for_current_mode.get('run_id')} 运行失败，请查看 stdout/stderr/log。"
    else:
        next_action = "请查看最新 run 状态，并按流程继续。"

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "raw": {
            "receptor": receptor_raw,
            "ligand": ligand_raw,
        },
        "prepared": {
            "receptor": receptor_prepared,
            "ligand": ligand_prepared,
        },
        "preparation": {
            "receptor": _workflow_preparation_summary(project, "receptor"),
            "ligand": _workflow_preparation_summary(project, "ligand"),
        },
        "box": {
            "status": "ok" if box_validation.get("ok") else "error",
            "warnings": box_validation.get("warnings", []),
            "error": box_validation.get("error"),
        },
        "vina": {
            "status": "ok" if vina_validation.get("ok") else "error",
            "warnings": vina_validation.get("warnings", []),
            "error": vina_validation.get("error"),
        },
        "grid_source": grid_source,
        "vina_maps": vina_maps_status,
        "config": config_status,
        "latest_run": latest_run,
        "latest_run_for_current_mode": latest_run_for_current_mode,
        "viewer": _workflow_viewer_status(
            project_path,
            project,
            receptor_raw,
            ligand_raw,
            receptor_prepared,
            ligand_prepared,
        ),
        "next_recommended_action": next_action,
        "message": "项目工作流状态已读取。",
        "error": None,
    }


def load_run_metadata(project_dir: str, run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    try:
        metadata, error = _read_run_metadata(project_dir, run_id)
        if error:
            return error
        assert metadata is not None
        return {
            "ok": True,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": _metadata_relative_path(run_id),
            "message": "运行元数据读取成功。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_READ_ERROR",
            "读取运行元数据时发生错误。",
            str(exc),
            "请确认 metadata.json 可以读取。",
        )


def _metadata_relative_path(run_id: str) -> str:
    return Path("runs", run_id, "metadata.json").as_posix()


def _safe_runs_directory(project_dir: str | Path, *, create: bool = False) -> Path:
    project_root = Path(project_dir).expanduser().resolve()
    runs_dir = project_root / "runs"
    if create:
        runs_dir.mkdir(parents=True, exist_ok=True)
    if runs_dir.exists():
        resolved = runs_dir.resolve(strict=True)
        if runs_dir.is_symlink() or resolved != runs_dir.absolute():
            raise RuntimeError(f"runs 目录不能是符号链接或重解析目录：{runs_dir}")
        if not resolved.is_dir():
            raise RuntimeError(f"runs 路径不是目录：{runs_dir}")
    return runs_dir


def _safe_run_directory(project_dir: str | Path, run_id: str, *, require_exists: bool = True) -> Path:
    if not RUN_ID_PATTERN.match(run_id):
        raise RuntimeError(f"run_id 格式无效：{run_id}")
    runs_dir = _safe_runs_directory(project_dir)
    run_dir = runs_dir / run_id
    if require_exists and not run_dir.is_dir():
        raise RuntimeError(f"run 目录不存在：{run_dir}")
    if run_dir.exists():
        resolved = run_dir.resolve(strict=True)
        if run_dir.is_symlink() or resolved != run_dir.absolute():
            raise RuntimeError(f"run 目录不能是符号链接或重解析目录：{run_dir}")
        if not resolved.is_dir():
            raise RuntimeError(f"run 路径不是目录：{run_dir}")
    return run_dir


def _run_metadata_path(project_dir: str | Path, run_id: str) -> Path:
    return _safe_run_directory(project_dir, run_id) / "metadata.json"


def _run_lock_path(project_dir: str | Path, run_id: str) -> Path:
    return _safe_run_directory(project_dir, run_id) / ".metadata.lock"


def _cancel_marker_path(project_dir: str | Path, run_id: str) -> Path:
    marker = _safe_run_directory(project_dir, run_id) / ".cancel_requested"
    if marker.is_symlink() or marker.resolve(strict=False) != marker.absolute():
        raise RuntimeError(f"取消标记路径不安全：{marker}")
    return marker


def _create_cancel_marker(project_dir: str | Path, run_id: str, requested_at: str) -> None:
    marker = _cancel_marker_path(project_dir, run_id)
    _atomic_write_text(marker, requested_at + "\n")


@contextmanager
def _run_metadata_lock(project_dir: str | Path, run_id: str) -> Iterator[None]:
    """Cross-process exclusive lock for one run's metadata transaction."""

    lock_path = _run_lock_path(project_dir, run_id)
    with _exclusive_file_lock(lock_path):
        yield


def _read_run_metadata_unlocked(project_dir: str | Path, run_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    metadata_path = _run_metadata_path(project_dir, run_id)
    if not metadata_path.exists():
        return None, _error(
            "RUN_METADATA_NOT_FOUND",
            "没有找到该 run 的 metadata.json，无法执行 Vina。",
            raw_error=str(metadata_path),
            suggestion="请先在运行准备页创建 run 记录。",
        )
    if metadata_path.is_symlink() or metadata_path.resolve(strict=True) != metadata_path.absolute():
        return None, _error(
            "RUN_METADATA_PATH_UNSAFE",
            "metadata.json 不能是符号链接或重解析到其他位置。",
            raw_error=str(metadata_path),
            suggestion="请恢复 run 目录中的普通 metadata.json 文件。",
        )
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return None, _error(
            "RUN_METADATA_READ_ERROR",
            "读取 metadata.json 时发生错误。",
            str(exc),
            "请检查 metadata.json 是否为 UTF-8 JSON 文件。",
        )
    if not isinstance(metadata, dict):
        return None, _error("RUN_METADATA_INVALID", "metadata.json 格式不是 JSON 对象。")
    return metadata, None


def _validate_run_status_transition(current: dict[str, Any] | None, updated: dict[str, Any]) -> None:
    if current is None:
        return
    before = str(current.get("status") or "")
    after = str(updated.get("status") or before)
    terminal = {"finished", "failed", "cancelled", "interrupted"}
    if before in terminal and after != before:
        raise RuntimeError(f"禁止将 run 终态从 {before} 改写为 {after}。")
    allowed = {
        "prepared": {"prepared", "running", "cancelled", "interrupted"},
        "running": {"running", "finished", "failed", "cancelled", "interrupted"},
    }
    if before in allowed and after not in allowed[before]:
        raise RuntimeError(f"不允许的 run 状态迁移：{before} -> {after}。")


def _write_run_metadata_unlocked(project_dir: str | Path, run_id: str, metadata: dict[str, Any]) -> None:
    metadata_path = _run_metadata_path(project_dir, run_id)
    if metadata_path.is_symlink():
        raise RuntimeError("metadata.json 不能是符号链接。")
    payload = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(metadata_path, payload)


def _write_run_metadata(project_dir: str | Path, run_id: str, metadata: dict[str, Any]) -> None:
    with _run_metadata_lock(project_dir, run_id):
        current, _ = _read_run_metadata_unlocked(project_dir, run_id)
        _validate_run_status_transition(current, metadata)
        _write_run_metadata_unlocked(project_dir, run_id, metadata)


def _read_run_metadata(project_dir: str, run_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not RUN_ID_PATTERN.match(run_id):
        return None, _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )
    try:
        run_dir = _safe_run_directory(project_dir, run_id, require_exists=False)
        if not run_dir.is_dir():
            metadata_path = run_dir / "metadata.json"
            return None, _error(
                "RUN_METADATA_NOT_FOUND",
                "没有找到该 run 的 metadata.json，无法执行 Vina。",
                raw_error=str(metadata_path),
                suggestion="请先在运行准备页创建 run 记录。",
            )
        with _run_metadata_lock(project_dir, run_id):
            return _read_run_metadata_unlocked(project_dir, run_id)
    except Exception as exc:  # noqa: BLE001 - unsafe paths become structured errors.
        return None, _error(
            "RUN_PATH_UNSAFE",
            "run 目录或运行元数据路径不安全，已拒绝访问。",
            raw_error=str(exc),
            suggestion="请恢复项目 runs 目录中的普通 run 文件夹和 metadata.json。",
        )


def _update_run_metadata_transaction(
    project_dir: str,
    run_id: str,
    updater: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        with _run_metadata_lock(project_dir, run_id):
            current, error = _read_run_metadata_unlocked(project_dir, run_id)
            if error or current is None:
                return None, error
            updated = updater(dict(current))
            if not isinstance(updated, dict):
                return None, _error("RUN_METADATA_UPDATE_INVALID", "run metadata 更新器没有返回 JSON 对象。")
            _validate_run_status_transition(current, updated)
            _write_run_metadata_unlocked(project_dir, run_id, updated)
            return updated, None
    except Exception as exc:  # noqa: BLE001 - unsafe paths become structured errors.
        return None, _error(
            "RUN_METADATA_TRANSACTION_ERROR",
            "更新 run metadata 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 run 目录是项目内普通目录且可写。",
        )


def _project_relative_path_for_run(
    project_dir: Path,
    relative_path: str,
    error_prefix: str,
    display_name: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{error_prefix}_NOT_SET",
            f"{display_name} 路径尚未设置。",
            suggestion=f"请回到运行准备页，重新生成包含 {display_name} 的 run metadata。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{error_prefix}_PATH_NOT_RELATIVE",
            f"{display_name} 路径必须是项目内相对路径。",
            suggestion="请重新准备运行记录，避免使用用户机器绝对路径。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{error_prefix}_OUTSIDE_PROJECT",
            f"{display_name} 路径指向项目目录外，DockStart 不会执行该 run。",
            raw_error=str(file_path),
            suggestion="请重新准备运行记录，让输出路径保留在项目目录内。",
        )

    return file_path, None


def _file_status(project_dir: Path, relative_path: str, key: str, name: str) -> dict[str, Any]:
    path, error = _project_relative_path_for_run(project_dir, relative_path, key.upper(), name)
    if error:
        return {
            "key": key,
            "name": name,
            "path": relative_path,
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "error",
            "message": error["error"]["message"],
            "raw_error": error["error"].get("raw_error", ""),
        }

    exists = bool(path and path.exists())
    is_file = bool(path and path.is_file())
    size = path.stat().st_size if path and is_file else 0
    if exists and is_file and size > 0:
        status = "ok"
        message = "文件存在且非空。"
    elif exists and is_file:
        status = "empty"
        message = "文件存在，但当前为空。"
    else:
        status = "missing"
        message = "文件尚未生成。"

    return {
        "key": key,
        "name": name,
        "path": relative_path,
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "non_empty": size > 0,
        "status": status,
        "message": message,
        "raw_error": "",
    }


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is False and isinstance(payload.get("error"), dict)


def _looks_like_score_row_candidate(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\b", line))


def parse_vina_log_text(log_text: str) -> list[dict[str, Any]] | dict[str, Any]:
    """Parse the score table from AutoDock Vina log text.

    This only extracts mode, affinity, and RMSD bounds. It does not interpret
    docking quality or pose geometry.
    """

    if not isinstance(log_text, str) or not log_text.strip():
        return _error(
            "VINA_LOG_EMPTY",
            "log.txt 为空，无法解析 Vina 结果表格。",
            suggestion="请确认该 run 已成功生成非空 log.txt，然后再解析结果。",
        )

    saw_header = False
    scores: list[dict[str, Any]] = []
    for line_number, line in enumerate(log_text.splitlines(), start=1):
        lower = line.lower()
        if not saw_header:
            if "mode" in lower and "affinity" in lower:
                saw_header = True
            continue

        stripped = line.strip()
        if not stripped:
            if scores:
                break
            continue
        if set(stripped) <= {"-", "+", "|", " "}:
            continue
        if "kcal" in lower or "rmsd" in lower or "mode" in lower or "affinity" in lower:
            continue

        match = VINA_SCORE_ROW_PATTERN.match(line)
        if match:
            mode_text, affinity_text, rmsd_lb_text, rmsd_ub_text = match.groups()
            scores.append(
                {
                    "mode": int(mode_text),
                    "affinity_kcal_mol": float(affinity_text),
                    "affinity_text": affinity_text,
                    "rmsd_lb": float(rmsd_lb_text),
                    "rmsd_lb_text": rmsd_lb_text,
                    "rmsd_ub": float(rmsd_ub_text),
                    "rmsd_ub_text": rmsd_ub_text,
                },
            )
            continue

        if _looks_like_score_row_candidate(line):
            return _error(
                "VINA_RESULT_ROW_PARSE_ERROR",
                f"Vina 结果表格第 {line_number} 行无法解析。",
                raw_error=line,
                suggestion="请确认 log.txt 中的结果行包含 mode、affinity、RMSD lower bound 和 RMSD upper bound 四列。",
            )
        if scores:
            break

    if not saw_header:
        return _error(
            "VINA_RESULT_TABLE_NOT_FOUND",
            "没有在 log.txt 中找到 Vina 结果表格。",
            suggestion="请确认 log.txt 来自 AutoDock Vina，并包含 mode / affinity / RMSD 表格。",
        )
    if not scores:
        return _error(
            "VINA_RESULT_ROWS_NOT_FOUND",
            "找到了 Vina 结果表头，但没有解析到任何结果行。",
            suggestion="请检查 log.txt 是否包含完整的 docking score 表格。",
        )
    return scores


def parse_vina_log_file(project_dir: str, run_id: str) -> list[dict[str, Any]] | dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    project_path = Path(project_dir).expanduser()
    log_file = Path("runs", run_id, "log.txt").as_posix()
    log_path, log_error = _project_relative_path_for_run(project_path, log_file, "VINA_LOG", "log.txt")
    if log_error:
        return log_error
    assert log_path is not None

    if not log_path.exists():
        return _error(
            "VINA_LOG_NOT_FOUND",
            "没有找到该 run 的 log.txt，无法解析 Vina 结果。",
            raw_error=str(log_path),
            suggestion="请先成功运行 Vina，或确认 runs/{run_id}/log.txt 是否存在。",
        )
    if not log_path.is_file():
        return _error(
            "VINA_LOG_NOT_FILE",
            "该 run 的 log.txt 路径不是文件。",
            raw_error=str(log_path),
            suggestion="请检查 run 目录结构是否完整。",
        )
    if log_path.stat().st_size == 0:
        return _error(
            "VINA_LOG_EMPTY",
            "该 run 的 log.txt 为空，无法解析 Vina 结果。",
            raw_error=str(log_path),
            suggestion="请先成功运行 Vina，生成非空 log.txt。",
        )

    try:
        return parse_vina_log_text(log_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return _error(
            "VINA_LOG_READ_ERROR",
            "读取 log.txt 时发生编码错误。",
            raw_error=str(exc),
            suggestion="请确认 log.txt 是可读取的文本文件。",
        )


def _evaluation_term_key(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.strip().lower())
    known = {
        "estimated free energy of binding": "estimated_free_energy_of_binding",
        "final intermolecular energy": "final_intermolecular_energy",
        "ligand - receptor": "ligand_receptor",
        "ligand - flex side chains": "ligand_flex_side_chains",
        "final total internal energy": "final_total_internal_energy",
        "ligand": "ligand_internal_energy",
        "flex - receptor": "flex_receptor_internal_energy",
        "flex - flex side chains": "flex_side_chains_internal_energy",
        "torsional free energy": "torsional_free_energy",
        "unbound system's energy": "unbound_system_energy",
        "unbound system’s energy": "unbound_system_energy",
    }
    if normalized in known:
        return known[normalized]
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "energy_term"


def parse_vina_evaluation_text(log_text: str, run_mode: str) -> dict[str, Any]:
    """Parse Vina score-only/local-only energy output without inventing poses."""

    normalized_mode = _normalize_run_mode(run_mode)
    if normalized_mode == "dock":
        return _error(
            "VINA_EVALUATION_MODE_INVALID",
            "全局对接结果应解析构象评分表，而不是姿势评价能量分解。",
        )
    if not isinstance(log_text, str) or not log_text.strip():
        return _error(
            "VINA_EVALUATION_LOG_EMPTY",
            "运行日志为空，无法解析姿势评价结果。",
            suggestion="请确认 score_only 或 local_only 运行已完成并生成非空 log.txt。",
        )

    scoring_match = re.search(r"^\s*Scoring function\s*:\s*(\S+)", log_text, re.IGNORECASE | re.MULTILINE)
    grid_center_match = re.search(r"^\s*Grid center\s*:\s*(.+)$", log_text, re.IGNORECASE | re.MULTILINE)
    grid_size_match = re.search(r"^\s*Grid size\s*:\s*(.+)$", log_text, re.IGNORECASE | re.MULTILINE)
    grid_space_match = re.search(rf"^\s*Grid space\s*:\s*({VINA_NUMBER_PATTERN})", log_text, re.IGNORECASE | re.MULTILINE)

    blocks: list[dict[str, Any]] = []
    current_block: dict[str, Any] | None = None
    for line_number, line in enumerate(log_text.splitlines(), start=1):
        match = VINA_EVALUATION_LINE_PATTERN.match(line)
        if not match:
            continue
        term_number, label, value_text = match.groups()
        key = _evaluation_term_key(label)
        if key == "estimated_free_energy_of_binding":
            if current_block is not None:
                blocks.append(current_block)
            current_block = {
                "terms": [],
                "seen_keys": set(),
                "start_line": line_number,
                "end_line": line_number,
            }
        elif current_block is None:
            continue

        assert current_block is not None
        if key in current_block["seen_keys"]:
            return _error(
                "VINA_EVALUATION_TERM_DUPLICATE",
                f"姿势评价日志中重复出现能量项：{label.strip()}。",
                raw_error=f"line {line_number}: {line}",
                suggestion="请确认 log.txt 来自单次完整的 Vina score_only/local_only 运行。",
            )
        current_block["seen_keys"].add(key)
        value = float(value_text)
        if not math.isfinite(value):
            return _error(
                "VINA_EVALUATION_VALUE_INVALID",
                f"姿势评价能量项 {label.strip()} 不是有限数值。",
                raw_error=line,
            )
        item = {
            "key": key,
            "label": label.strip(),
            "value_kcal_mol": value,
            "term_number": int(term_number) if term_number else None,
            "line_number": line_number,
        }
        current_block["terms"].append(item)
        current_block["end_line"] = line_number
    if current_block is not None:
        blocks.append(current_block)

    required_keys = {"estimated_free_energy_of_binding", "final_intermolecular_energy"}
    if not blocks:
        return _error(
            "VINA_EVALUATION_BLOCK_NOT_FOUND",
            "没有在运行日志中找到完整的 Vina 姿势评价能量块。",
            raw_error="missing: estimated_free_energy_of_binding, final_intermolecular_energy",
            suggestion="请确认运行使用 --score_only 或 --local_only，且 log.txt 未被截断。",
        )

    selected_block_index = 0
    selected_block_role = "evaluation"
    if len(blocks) > 1:
        before_marker = re.search(
            r"^\s*Before local optimization:\s*$",
            log_text,
            re.IGNORECASE | re.MULTILINE,
        )
        search_done_marker = re.search(
            r"^\s*Performing local search\b.*\bdone\.\s*$",
            log_text,
            re.IGNORECASE | re.MULTILINE,
        )
        before_marker_line = (
            log_text.count("\n", 0, before_marker.start()) + 1
            if before_marker
            else -1
        )
        search_done_line = (
            log_text.count("\n", 0, search_done_marker.start()) + 1
            if search_done_marker
            else -1
        )
        all_complete = all(required_keys <= block["seen_keys"] for block in blocks)
        if not (
            normalized_mode == "local_only"
            and len(blocks) == 2
            and before_marker
            and search_done_marker
            and before_marker_line < blocks[0]["start_line"]
            and blocks[0]["end_line"] < search_done_line
            and search_done_line < blocks[1]["start_line"]
            and all_complete
        ):
            duplicate_line = blocks[1]["terms"][0]
            return _error(
                "VINA_EVALUATION_TERM_DUPLICATE",
                "姿势评价日志中出现多个无法明确归属的能量块。",
                raw_error=(
                    f"line {duplicate_line['line_number']}: "
                    f"{duplicate_line['label']}"
                ),
                suggestion="请确认日志来自单次完整运行，且没有拼接或重复内容。",
            )
        selected_block_index = 1
        selected_block_role = "after_local_optimization"

    selected_block = blocks[selected_block_index]
    seen_keys = selected_block["seen_keys"]
    missing = sorted(required_keys - seen_keys)
    terms = selected_block["terms"]
    primary_term = next(
        (
            item
            for item in terms
            if item["key"] == "estimated_free_energy_of_binding"
        ),
        None,
    )
    if missing or primary_term is None:
        return _error(
            "VINA_EVALUATION_BLOCK_NOT_FOUND",
            "没有在运行日志中找到完整的 Vina 姿势评价能量块。",
            raw_error="missing: " + ", ".join(missing or ["estimated_free_energy_of_binding"]),
            suggestion="请确认运行使用 --score_only 或 --local_only，且 log.txt 未被截断。",
        )

    block_summaries = []
    for index, block in enumerate(blocks):
        score_item = next(
            (
                item
                for item in block["terms"]
                if item["key"] == "estimated_free_energy_of_binding"
            ),
            None,
        )
        block_summaries.append(
            {
                "index": index,
                "role": (
                    "before_local_optimization"
                    if len(blocks) == 2 and index == 0
                    else selected_block_role
                ),
                "primary_score_kcal_mol": (
                    score_item["value_kcal_mol"] if score_item else None
                ),
                "start_line": block["start_line"],
                "end_line": block["end_line"],
            }
        )

    return {
        "ok": True,
        "kind": normalized_mode,
        "run_mode": normalized_mode,
        "primary_score_kcal_mol": primary_term["value_kcal_mol"],
        "scoring_function": scoring_match.group(1).strip().lower() if scoring_match else "",
        "energy_terms": terms,
        "energy_blocks": block_summaries,
        "selected_energy_block_index": selected_block_index,
        "selected_energy_block_role": selected_block_role,
        "grid": {
            "mode": "autobox"
            if grid_center_match and "autobox" in grid_center_match.group(1).lower()
            else "project_box",
            "center": grid_center_match.group(1).strip() if grid_center_match else "",
            "size": grid_size_match.group(1).strip() if grid_size_match else "",
            "spacing_angstrom": float(grid_space_match.group(1)) if grid_space_match else None,
        },
        "output_pose_generated": normalized_mode == "local_only",
        "message": "Vina 姿势评价能量分解已解析。",
        "error": None,
    }


def parse_vina_evaluation_file(project_dir: str, run_id: str, run_mode: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error("RUN_ID_INVALID", "run_id 格式无效，应类似 run_001。")
    project_path = Path(project_dir).expanduser()
    log_file = Path("runs", run_id, "log.txt").as_posix()
    log_path, path_error = _project_relative_path_for_run(
        project_path,
        log_file,
        "VINA_EVALUATION_LOG",
        "log.txt",
    )
    if path_error:
        return path_error
    assert log_path is not None
    if not log_path.is_file() or log_path.stat().st_size <= 0:
        return _error(
            "VINA_EVALUATION_LOG_NOT_FOUND",
            "没有找到非空的姿势评价运行日志。",
            raw_error=str(log_path),
            suggestion="请先成功执行 score_only 或 local_only。",
        )
    try:
        return parse_vina_evaluation_text(log_path.read_text(encoding="utf-8"), run_mode)
    except UnicodeDecodeError as exc:
        return _error(
            "VINA_EVALUATION_LOG_READ_ERROR",
            "读取姿势评价运行日志时发生编码错误。",
            raw_error=str(exc),
        )


def export_scores_csv(project_dir: str, run_id: str, scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(scores, list) or not scores:
        return _error(
            "SCORES_EMPTY",
            "没有可导出的 Vina score 记录。",
            suggestion="请先解析包含结果表格的 log.txt。",
        )

    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
    run_scores_file = Path("runs", run_id, "scores.csv").as_posix()
    project_scores_file = _project_scores_file(metadata)

    run_scores_path, run_path_error = _project_relative_path_for_run(project_path, run_scores_file, "RUN_SCORES_CSV", "scores.csv")
    if run_path_error:
        return run_path_error
    project_scores_path, project_path_error = _project_relative_path_for_run(
        project_path,
        project_scores_file,
        "PROJECT_SCORES_CSV",
        "results/scores.csv",
    )
    if project_path_error:
        return project_path_error
    assert run_scores_path is not None
    assert project_scores_path is not None

    try:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=SCORES_CSV_FIELDS)
        writer.writeheader()
        for score in scores:
            writer.writerow({field: score[field] for field in SCORES_CSV_FIELDS})
        csv_payload = buffer.getvalue()
        for target_path in (run_scores_path, project_scores_path):
            _atomic_write_text(target_path, csv_payload)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "SCORES_CSV_WRITE_ERROR",
            "写入 scores.csv 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目 results 目录和 run 目录可以写入。",
        )

    return {
        "ok": True,
        "project_dir": str(project_path),
        "run_id": run_id,
        "scores": scores,
        "scores_file": run_scores_file,
        "project_scores_file": project_scores_file,
        "message": (
            "AutoDock4 scores.csv 已导出，并与 Vina/Vinardo 项目结果分开保存。"
            if _metadata_scoring_protocol(metadata) == "ad4_maps"
            else "scores.csv 已导出。"
        ),
        "error": None,
    }


def _parse_scores_csv_row(row: dict[str, str], line_number: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        return (
            {
                "mode": int(row["mode"]),
                "affinity_kcal_mol": float(row["affinity_kcal_mol"]),
                "rmsd_lb": float(row["rmsd_lb"]),
                "rmsd_ub": float(row["rmsd_ub"]),
            },
            None,
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return None, _error(
            "SCORES_CSV_ROW_INVALID",
            f"scores.csv 第 {line_number} 行无法解析。",
            raw_error=str(exc),
            suggestion="请重新从 Vina log 解析并导出 scores.csv。",
        )


def _parse_multiple_ligand_scores_csv_row(
    row: dict[str, str],
    line_number: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    pose_available_text = str(row.get("pose_available") or "").strip().lower()
    if pose_available_text not in {"true", "false"}:
        return None, _error(
            "MULTIPLE_LIGAND_SCORES_CSV_POSE_FLAG_INVALID",
            f"scores.csv 第 {line_number} 行的 pose_available 不是布尔值。",
            raw_error=pose_available_text,
            suggestion="请重新运行多配体共同对接，恢复完整的联合评分记录。",
        )
    if str(row.get("score_scope") or "").strip() != "joint_two_ligand_pose":
        return None, _error(
            "MULTIPLE_LIGAND_SCORES_CSV_SCOPE_INVALID",
            f"scores.csv 第 {line_number} 行没有声明联合评分范围。",
            raw_error=str(row.get("score_scope") or ""),
            suggestion="请勿将单配体评分表替换为共同对接 scores.csv。",
        )
    try:
        joint_affinity = float(row["joint_affinity_kcal_mol"])
        return (
            {
                "mode": int(row["mode"]),
                "affinity_kcal_mol": joint_affinity,
                "joint_affinity_kcal_mol": joint_affinity,
                "rmsd_lb": float(row["rmsd_lb"]),
                "rmsd_ub": float(row["rmsd_ub"]),
                "pose_available": pose_available_text == "true",
                "score_scope": "joint_two_ligand_pose",
            },
            None,
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return None, _error(
            "MULTIPLE_LIGAND_SCORES_CSV_ROW_INVALID",
            f"scores.csv 第 {line_number} 行无法解析。",
            raw_error=str(exc),
            suggestion="请重新运行多配体共同对接，恢复完整的联合评分记录。",
        )


def _read_file_snapshot_no_follow(path: Path) -> bytes:
    """Read one immutable byte snapshot while rejecting path replacement.

    ``O_NOFOLLOW`` is used where Python exposes it.  The lstat/fstat identity
    checks provide the corresponding guard on Windows and also detect a fixed
    result path being replaced while the handle is open.
    """

    before = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not stat.S_ISREG(before.st_mode) or (
        reparse_flag and getattr(before, "st_file_attributes", 0) & reparse_flag
    ):
        raise OSError("结果路径不是普通文件，或属于重解析点")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("打开的结果对象不是普通文件")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("结果文件在安全检查与打开之间被替换")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        snapshot = b"".join(chunks)

        opened_after = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino)
            != (opened_after.st_dev, opened_after.st_ino)
            or opened.st_size != opened_after.st_size
            or opened.st_mtime_ns != opened_after.st_mtime_ns
            or len(snapshot) != opened_after.st_size
        ):
            raise OSError("结果文件在读取过程中发生变化")

        after = path.lstat()
        if (
            not stat.S_ISREG(after.st_mode)
            or (
                reparse_flag
                and getattr(after, "st_file_attributes", 0) & reparse_flag
            )
            or (after.st_dev, after.st_ino)
            != (opened_after.st_dev, opened_after.st_ino)
        ):
            raise OSError("结果文件在读取过程中被替换")
        return snapshot
    finally:
        os.close(descriptor)


RUN_EXECUTION_ARTIFACT_KEYS = (
    "log",
    "stdout",
    "stderr",
    "out_vina_raw",
    "vina_binary_executed",
    "vina_binary_observed_after_execution",
)
RUN_SCORE_ARTIFACT_KEYS = (
    "scores",
    "project_scores",
    "flexible_movement",
)
RUN_REPORT_ARTIFACT_KEYS = ("report", "project_report")


def _recorded_artifact_contract_present(
    metadata: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    flat_hashes = (
        metadata.get("artifact_sha256")
        if isinstance(metadata.get("artifact_sha256"), dict)
        else {}
    )
    return any(key in artifacts or key in flat_hashes for key in keys)


def _read_verified_recorded_artifact(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
    *,
    artifact_key: str,
    expected_relative: str,
    recorded_relative: str,
    contract_keys: tuple[str, ...],
    error_stem: str,
    display_name: str,
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Read a modern run artifact from one immutable, hash-verified snapshot.

    Historical runs that predate the relevant artifact contract remain
    readable. Once any peer record from the same contract exists, however,
    the requested record is mandatory so deleting one entry cannot silently
    downgrade a partially modern run to an unchecked read.
    """

    if not _recorded_artifact_contract_present(metadata, contract_keys):
        return None, None

    expected_relative = Path(expected_relative).as_posix()
    recorded_relative = Path(recorded_relative).as_posix()
    if recorded_relative != expected_relative:
        return None, _error(
            f"{error_stem}_PATH_MISMATCH",
            f"{display_name} 不是本次 run 的固定证据路径，已拒绝读取。",
            raw_error=(
                f"expected={expected_relative}; actual={recorded_relative}"
            ),
            suggestion="请保留该 run 供审计，并从未修改的运行记录重新生成结果。",
        )

    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    artifact = (
        artifacts.get(artifact_key)
        if isinstance(artifacts.get(artifact_key), dict)
        else {}
    )
    expected_sha256 = str(artifact.get("sha256") or "").lower()
    expected_size = artifact.get("size_bytes")
    artifact_relative = Path(
        str(artifact.get("relative_path") or ""),
    ).as_posix()
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or artifact_relative != expected_relative
    ):
        return None, _error(
            f"{error_stem}_HASH_MISSING",
            f"metadata 中缺少可信且路径一致的 {display_name} SHA256 与文件大小。",
            raw_error=(
                f"relative_path={artifact_relative}; "
                f"sha256={expected_sha256}; size_bytes={expected_size!r}"
            ),
            suggestion="请保留该 run 供审计，并重新执行或重新解析新的运行记录。",
        )

    path, path_error = _project_relative_path_for_run(
        project_path,
        expected_relative,
        error_stem,
        display_name,
    )
    if path_error:
        return None, path_error
    assert path is not None

    try:
        snapshot = _read_file_snapshot_no_follow(path)
    except FileNotFoundError:
        return None, _error(
            f"{error_stem}_NOT_FOUND",
            f"没有找到完成时记录的 {display_name}。",
            raw_error=str(path),
            suggestion="请保留该 run 供审计，并重新执行新的运行记录。",
        )
    except OSError as exc:
        return None, _error(
            f"{error_stem}_READ_ERROR",
            f"读取 {display_name} 完整性信息时发生错误。",
            raw_error=str(exc),
            suggestion="请确认文件可读，且没有被符号链接或其他程序替换。",
        )

    actual_sha256 = hashlib.sha256(snapshot).hexdigest().lower()
    actual_size = len(snapshot)
    if actual_sha256 != expected_sha256 or actual_size != expected_size:
        return None, _error(
            f"{error_stem}_HASH_MISMATCH",
            f"{display_name} 与完成时记录的 SHA256 或文件大小不一致，已拒绝读取。",
            raw_error=(
                f"expected sha256={expected_sha256}, size={expected_size}; "
                f"actual sha256={actual_sha256}, size={actual_size}; "
                f"path={path}"
            ),
            suggestion="请保留该 run 供审计，并重新执行或重新解析新的运行记录。",
        )
    return snapshot, None


def _read_multiple_ligand_result_artifact_snapshot(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
    *,
    artifact_key: str,
    filename: str,
    metadata_path_key: str,
    error_stem: str,
    display_name: str,
) -> tuple[bytes | None, dict[str, Any] | None]:
    expected_relative = Path("runs", run_id, filename).as_posix()
    recorded_relative = str(metadata.get(metadata_path_key) or "")
    if recorded_relative != expected_relative:
        return None, _error(
            f"{error_stem}_FIXED_PATH_INVALID",
            f"{display_name} 不是本次多配体 run 的固定结果路径，已拒绝读取。",
            raw_error=(
                f"expected={expected_relative}; actual={recorded_relative}"
            ),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )

    path, path_error = _safe_fixed_run_artifact(
        project_path,
        run_id,
        filename,
    )
    if path is None or path_error:
        return None, _error(
            f"{error_stem}_PATH_UNSAFE",
            f"{display_name} 的固定路径不安全，已拒绝读取。",
            raw_error=path_error,
            suggestion="请恢复 run 目录中的普通结果文件，且不要使用符号链接。",
        )
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    artifact = (
        artifacts.get(artifact_key)
        if isinstance(artifacts.get(artifact_key), dict)
        else {}
    )
    expected_sha256 = str(artifact.get("sha256") or "").lower()
    expected_size = artifact.get("size_bytes")
    artifact_relative = str(artifact.get("relative_path") or "")
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or artifact_relative != expected_relative
    ):
        return None, _error(
            f"{error_stem}_ATTESTATION_INVALID",
            f"metadata 中缺少可信且路径一致的 {display_name} SHA256 与文件大小。",
            raw_error=(
                f"relative_path={artifact_relative}; "
                f"sha256={expected_sha256}; size_bytes={expected_size!r}"
            ),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )

    try:
        snapshot = _read_file_snapshot_no_follow(path)
        actual_sha256 = hashlib.sha256(snapshot).hexdigest().lower()
        actual_size = len(snapshot)
    except FileNotFoundError:
        return None, _error(
            f"{error_stem}_NOT_FOUND",
            f"{display_name} 缺失或为空，无法读取多配体共同对接结果。",
            raw_error=str(path),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    except OSError as exc:
        return None, _error(
            f"{error_stem}_READ_ERROR",
            f"读取 {display_name} 完整性信息时发生错误。",
            raw_error=str(exc),
            suggestion="请确认结果文件可读且没有被其他程序替换。",
        )
    if (
        actual_sha256 != expected_sha256
        or actual_size != expected_size
    ):
        return None, _error(
            f"{error_stem}_INTEGRITY_ERROR",
            f"{display_name} 与完成时记录的 SHA256 或文件大小不一致，已拒绝读取。",
            raw_error=(
                f"expected sha256={expected_sha256}, size={expected_size}; "
                f"actual sha256={actual_sha256}, size={actual_size}; "
                f"path={path}"
            ),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    return snapshot, None


def _load_multiple_ligand_joint_score_manifest(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    snapshot, error = _read_multiple_ligand_result_artifact_snapshot(
        project_path,
        run_id,
        metadata,
        artifact_key="joint_poses",
        filename="joint_poses.json",
        metadata_path_key="joint_poses_file",
        error_stem="MULTIPLE_LIGAND_JOINT_MANIFEST",
        display_name="joint_poses.json",
    )
    if error:
        return None, error
    assert snapshot is not None
    try:
        manifest = json.loads(snapshot.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return None, _error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_READ_ERROR",
            "joint_poses.json 无法读取。",
            raw_error=str(exc),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    if (
        not isinstance(manifest, dict)
        or manifest.get("protocol_id") != MULTIPLE_LIGAND_PROTOCOL_ID
        or manifest.get("run_id") != run_id
        or not isinstance(manifest.get("scores"), list)
    ):
        return None, _error(
            "MULTIPLE_LIGAND_JOINT_MANIFEST_INVALID",
            "joint_poses.json 的协议、run 身份或联合评分清单无效。",
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    return manifest, None


def _multiple_ligand_score_comparison_value(
    score: dict[str, Any],
) -> tuple[tuple[int, float, float, float, bool] | None, str]:
    mode = score.get("mode")
    pose_available = score.get("pose_available")
    if (
        isinstance(mode, bool)
        or not isinstance(mode, int)
        or mode <= 0
        or not isinstance(pose_available, bool)
    ):
        return None, "mode 或 pose_available 类型无效"
    values: list[float] = []
    for key in (
        "joint_affinity_kcal_mol",
        "rmsd_lb",
        "rmsd_ub",
    ):
        value = score.get(key)
        if isinstance(value, bool):
            return None, f"{key} 类型无效"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None, f"{key} 不是数字"
        if not math.isfinite(number):
            return None, f"{key} 不是有限数字"
        values.append(number)
    return (
        (
            mode,
            values[0],
            values[1],
            values[2],
            pose_available,
        ),
        "",
    )


def _cross_check_multiple_ligand_scores(
    csv_scores: list[dict[str, Any]],
    joint_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    manifest_scores = joint_manifest.get("scores")
    if not isinstance(manifest_scores, list):
        return _error(
            "MULTIPLE_LIGAND_SCORE_MANIFEST_MISMATCH",
            "joint_poses.json 缺少联合评分清单，已拒绝读取 scores.csv。",
        )

    def index_scores(
        values: list[Any],
        source: str,
    ) -> tuple[dict[int, tuple[int, float, float, float, bool]] | None, str]:
        indexed: dict[int, tuple[int, float, float, float, bool]] = {}
        for position, value in enumerate(values, start=1):
            if not isinstance(value, dict):
                return None, f"{source} 第 {position} 项不是对象"
            normalized, issue = _multiple_ligand_score_comparison_value(value)
            if normalized is None:
                return None, f"{source} 第 {position} 项：{issue}"
            mode = normalized[0]
            if mode in indexed:
                return None, f"{source} 包含重复 mode={mode}"
            indexed[mode] = normalized
        return indexed, ""

    csv_index, csv_issue = index_scores(csv_scores, "scores.csv")
    manifest_index, manifest_issue = index_scores(
        manifest_scores,
        "joint_poses.json",
    )
    if csv_index is None or manifest_index is None:
        return _error(
            "MULTIPLE_LIGAND_SCORE_MANIFEST_MISMATCH",
            "scores.csv 与 joint_poses.json 的联合评分记录无效。",
            raw_error=csv_issue or manifest_issue,
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    if csv_index.keys() != manifest_index.keys():
        return _error(
            "MULTIPLE_LIGAND_SCORE_MANIFEST_MISMATCH",
            "scores.csv 与 joint_poses.json 的 mode 集合不一致。",
            raw_error=(
                f"scores.csv modes={sorted(csv_index)}; "
                f"joint_poses.json modes={sorted(manifest_index)}"
            ),
            suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
        )
    for mode, csv_value in csv_index.items():
        manifest_value = manifest_index[mode]
        if csv_value != manifest_value:
            return _error(
                "MULTIPLE_LIGAND_SCORE_MANIFEST_MISMATCH",
                f"Mode {mode} 在 scores.csv 与 joint_poses.json 中的联合评分记录不一致。",
                raw_error=(
                    "fields=(mode,joint_affinity_kcal_mol,rmsd_lb,"
                    "rmsd_ub,pose_available); "
                    f"scores.csv={csv_value}; joint_poses.json={manifest_value}"
                ),
                suggestion="请保留该 run 供审计，并重新执行新的多配体共同对接。",
            )
    return None


def _flexible_movement_relative_path(run_id: str) -> str:
    return Path("runs", run_id, FLEXIBLE_MOVEMENT_FILENAME).as_posix()


def _is_flexible_movement_run(metadata: dict[str, Any]) -> bool:
    docking_protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    return (
        _metadata_run_mode(metadata) == "dock"
        and (
            bool(_flexible_movement_contract(metadata))
            or str(docking_protocol.get("mode") or "").strip().lower()
            == "flexible"
        )
    )


def _flexible_movement_contract(metadata: dict[str, Any]) -> dict[str, Any]:
    contracts = (
        metadata.get("analysis_contracts")
        if isinstance(metadata.get("analysis_contracts"), dict)
        else {}
    )
    contract = contracts.get("flexible_movement")
    return copy.deepcopy(contract) if isinstance(contract, dict) else {}


def _frozen_flexible_movement_contract(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    protocol_snapshot = (
        snapshots.get("flexible_receptor_protocol")
        if isinstance(
            snapshots.get("flexible_receptor_protocol"),
            dict,
        )
        else {}
    )
    recorded_file = str(protocol_snapshot.get("relative_path") or "")
    if not recorded_file:
        return {}, None
    expected_file = Path(
        "runs",
        run_id,
        "inputs",
        "flexible_receptor_protocol.json",
    ).as_posix()
    protocol_path, path_error = _fixed_run_file(
        project_path,
        expected_relative_path=expected_file,
        recorded_relative_path=recorded_file,
        error_stem="FLEX_MOVEMENT_PROTOCOL_SNAPSHOT",
        display_name="冻结柔性受体协议",
    )
    if path_error:
        return {}, path_error
    assert protocol_path is not None
    try:
        raw = protocol_path.read_bytes()
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise ValueError(f"size_bytes={len(raw)}")
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {}, _error(
            "FLEX_MOVEMENT_PROTOCOL_SNAPSHOT_INVALID",
            "冻结柔性受体协议不是有效的严格 UTF-8 JSON。",
            raw_error=str(exc),
        )
    if not isinstance(payload, dict):
        return {}, _error(
            "FLEX_MOVEMENT_PROTOCOL_SNAPSHOT_INVALID",
            "冻结柔性受体协议顶层必须是 JSON 对象。",
        )
    input_sha256 = (
        metadata.get("input_sha256")
        if isinstance(metadata.get("input_sha256"), dict)
        else {}
    )
    expected_sha256 = str(
        input_sha256.get("flexible_receptor_protocol") or ""
    ).lower()
    snapshot_sha256 = str(protocol_snapshot.get("sha256") or "").lower()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        not SHA256_PATTERN.fullmatch(expected_sha256)
        or snapshot_sha256 != expected_sha256
        or actual_sha256 != expected_sha256
    ):
        return {}, _error(
            "FLEX_MOVEMENT_PROTOCOL_SNAPSHOT_HASH_MISMATCH",
            "冻结柔性受体协议与运行时 SHA256 证据不一致。",
            raw_error=(
                f"input_sha256={expected_sha256 or '<empty>'}; "
                f"snapshot_sha256={snapshot_sha256 or '<empty>'}; "
                f"actual_sha256={actual_sha256}"
            ),
            suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
        )
    if payload.get("schema_version") != 2:
        return {}, None
    contracts = (
        payload.get("analysis_contracts")
        if isinstance(payload.get("analysis_contracts"), dict)
        else {}
    )
    contract = contracts.get("flexible_movement")
    if not isinstance(contract, dict):
        return {}, _error(
            "FLEX_MOVEMENT_FROZEN_CONTRACT_MISSING",
            "新版冻结柔性受体协议缺少运动分析合同。",
        )
    top_level_contract = _flexible_movement_contract(metadata)
    if top_level_contract != contract:
        return {}, _error(
            "FLEX_MOVEMENT_CONTRACT_MISMATCH",
            "metadata 中的运动分析合同与冻结柔性受体协议不一致。",
            raw_error=(
                f"metadata={top_level_contract}; frozen={contract}"
            ),
            suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
        )
    return copy.deepcopy(contract), None


def _fixed_run_file(
    project_path: Path,
    *,
    expected_relative_path: str,
    recorded_relative_path: str,
    error_stem: str,
    display_name: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    expected = Path(expected_relative_path).as_posix()
    recorded = (
        Path(recorded_relative_path).as_posix()
        if recorded_relative_path
        else ""
    )
    if recorded != expected:
        return None, _error(
            f"{error_stem}_PATH_INVALID",
            f"{display_name}没有绑定到本次 run 的固定路径，已拒绝读取。",
            raw_error=f"expected={expected}; recorded={recorded or '<empty>'}",
            suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
        )
    candidate = project_path / Path(expected)
    if candidate.is_symlink():
        return None, _error(
            f"{error_stem}_PATH_UNSAFE",
            f"{display_name}不能是符号链接。",
            raw_error=str(candidate),
        )
    try:
        resolved = candidate.resolve(strict=True)
        expected_absolute = candidate.absolute()
        resolved.relative_to(project_path)
    except (OSError, ValueError) as exc:
        return None, _error(
            f"{error_stem}_NOT_FOUND",
            f"没有找到本次 run 的{display_name}。",
            raw_error=f"{candidate}: {exc}",
            suggestion="请恢复冻结文件；无法恢复时重新准备、执行新的柔性对接。",
        )
    if resolved != expected_absolute or not resolved.is_file():
        return None, _error(
            f"{error_stem}_PATH_UNSAFE",
            f"{display_name}被重解析到固定路径之外或不是普通文件。",
            raw_error=f"expected={expected_absolute}; resolved={resolved}",
        )
    return resolved, None


def _metadata_output_sha256(metadata: dict[str, Any]) -> str:
    normalization = (
        metadata.get("output_normalization")
        if isinstance(metadata.get("output_normalization"), dict)
        else {}
    )
    normalized_sha256 = str(
        normalization.get("normalized_sha256") or ""
    ).lower()
    if SHA256_PATTERN.fullmatch(normalized_sha256):
        return normalized_sha256
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    output_artifact = (
        artifacts.get("out")
        if isinstance(artifacts.get("out"), dict)
        else {}
    )
    artifact_sha256 = str(output_artifact.get("sha256") or "").lower()
    if SHA256_PATTERN.fullmatch(artifact_sha256):
        return artifact_sha256
    output_sha256 = (
        metadata.get("output_sha256")
        if isinstance(metadata.get("output_sha256"), dict)
        else {}
    )
    legacy_sha256 = str(output_sha256.get("out") or "").lower()
    return legacy_sha256 if SHA256_PATTERN.fullmatch(legacy_sha256) else ""


def _flexible_movement_source_paths(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> tuple[dict[str, Path] | None, dict[str, Any] | None]:
    expected_ligand = Path(
        "runs",
        run_id,
        "inputs",
        "ligand.pdbqt",
    ).as_posix()
    expected_flex = Path(
        "runs",
        run_id,
        "inputs",
        "flex.pdbqt",
    ).as_posix()
    expected_output = Path("runs", run_id, "out.pdbqt").as_posix()
    paths: dict[str, Path] = {}
    for role, expected, recorded, stem, label in (
        (
            "ligand_input",
            expected_ligand,
            _run_input_snapshot_file(metadata, "ligand", ""),
            "FLEX_MOVEMENT_LIGAND_INPUT",
            "冻结配体 PDBQT",
        ),
        (
            "flex_input",
            expected_flex,
            _run_input_snapshot_file(metadata, "flex", ""),
            "FLEX_MOVEMENT_FLEX_INPUT",
            "冻结柔性侧链 PDBQT",
        ),
        (
            "vina_output",
            expected_output,
            str(metadata.get("output_file") or ""),
            "FLEX_MOVEMENT_OUTPUT",
            "Vina 柔性对接输出 PDBQT",
        ),
    ):
        path, error = _fixed_run_file(
            project_path,
            expected_relative_path=expected,
            recorded_relative_path=recorded,
            error_stem=stem,
            display_name=label,
        )
        if error:
            return None, error
        assert path is not None
        paths[role] = path
    return paths, None


def _validate_flexible_movement_source_hashes(
    metadata: dict[str, Any],
    movement: dict[str, Any],
    paths: dict[str, Path],
    *,
    require_recorded_hashes: bool,
) -> dict[str, Any] | None:
    input_sha256 = (
        metadata.get("input_sha256")
        if isinstance(metadata.get("input_sha256"), dict)
        else {}
    )
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    inputs = (
        snapshots.get("inputs")
        if isinstance(snapshots.get("inputs"), dict)
        else {}
    )
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    expected = {
        "ligand_input": str(input_sha256.get("ligand") or "").lower(),
        "flex_input": str(input_sha256.get("flex") or "").lower(),
        "vina_output": _metadata_output_sha256(metadata),
    }
    records = {
        "ligand_input": (
            inputs.get("ligand")
            if isinstance(inputs.get("ligand"), dict)
            else {}
        ),
        "flex_input": (
            inputs.get("flex")
            if isinstance(inputs.get("flex"), dict)
            else {}
        ),
        "vina_output": (
            artifacts.get("out")
            if isinstance(artifacts.get("out"), dict)
            else {}
        ),
    }
    evidence = (
        movement.get("source_evidence")
        if isinstance(movement.get("source_evidence"), dict)
        else {}
    )
    for role, label in (
        ("ligand_input", "冻结配体"),
        ("flex_input", "冻结柔性侧链"),
        ("vina_output", "Vina 输出"),
    ):
        recorded_sha256 = expected[role]
        observed_sha256 = _sha256_file(paths[role]).lower()
        item = (
            evidence.get(role)
            if isinstance(evidence.get(role), dict)
            else {}
        )
        evidence_sha256 = str(item.get("sha256") or "").lower()
        record = records[role]
        record_sha256 = str(record.get("sha256") or "").lower()
        try:
            evidence_size = int(item.get("size_bytes"))
        except (TypeError, ValueError):
            evidence_size = -1
        try:
            record_size = int(record.get("size_bytes"))
        except (TypeError, ValueError):
            record_size = -1
        if (
            require_recorded_hashes
            and (
                not SHA256_PATTERN.fullmatch(recorded_sha256)
                or record_sha256 != recorded_sha256
                or record_size != paths[role].stat().st_size
            )
        ):
            return _error(
                "FLEX_MOVEMENT_SOURCE_HASH_MISSING",
                f"run metadata 缺少可信的{label} SHA256 / 大小记录，无法发布运动分析。",
                raw_error=(
                    f"role={role}; input_or_output_sha256="
                    f"{recorded_sha256 or '<empty>'}; "
                    f"artifact_sha256={record_sha256 or '<empty>'}; "
                    f"artifact_size={record_size}; "
                    f"actual_size={paths[role].stat().st_size}"
                ),
                suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
            )
        if (
            SHA256_PATTERN.fullmatch(recorded_sha256)
            and observed_sha256 != recorded_sha256
        ):
            return _error(
                "FLEX_MOVEMENT_SOURCE_HASH_MISMATCH",
                f"{label}与运行时冻结证据不一致，已拒绝运动分析。",
                raw_error=(
                    f"role={role}; expected={recorded_sha256}; "
                    f"actual={observed_sha256}"
                ),
                suggestion="请勿覆盖已完成 run 的输入或输出；重新准备并执行新的 run。",
            )
        if (
            evidence_sha256 != observed_sha256
            or evidence_size != paths[role].stat().st_size
        ):
            return _error(
                "FLEX_MOVEMENT_SOURCE_EVIDENCE_MISMATCH",
                f"运动分析记录中的{label}字节证据与文件不一致。",
                raw_error=(
                    f"role={role}; evidence_sha256={evidence_sha256}; "
                    f"actual_sha256={observed_sha256}; "
                    f"evidence_size={evidence_size}; "
                    f"actual_size={paths[role].stat().st_size}"
                ),
            )
    return None


def _validate_flexible_static_run_snapshots(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
    *,
    require_recorded_hashes: bool,
) -> dict[str, Any] | None:
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    inputs = (
        snapshots.get("inputs")
        if isinstance(snapshots.get("inputs"), dict)
        else {}
    )
    config_record = (
        snapshots.get("config")
        if isinstance(snapshots.get("config"), dict)
        else {}
    )
    input_sha256 = (
        metadata.get("input_sha256")
        if isinstance(metadata.get("input_sha256"), dict)
        else {}
    )
    for key, expected_file, record, expected_hash, label in (
        (
            "receptor",
            Path(
                "runs",
                run_id,
                "inputs",
                "receptor.pdbqt",
            ).as_posix(),
            (
                inputs.get("receptor")
                if isinstance(inputs.get("receptor"), dict)
                else {}
            ),
            str(input_sha256.get("receptor") or "").lower(),
            "冻结刚性受体",
        ),
        (
            "config",
            Path(
                "runs",
                run_id,
                "config_snapshot.txt",
            ).as_posix(),
            config_record,
            str(input_sha256.get("config") or "").lower(),
            "冻结 Vina 配置",
        ),
    ):
        recorded_file = str(
            record.get("relative_path")
            or record.get("snapshot_file")
            or ""
        )
        path, path_error = _fixed_run_file(
            project_path,
            expected_relative_path=expected_file,
            recorded_relative_path=recorded_file,
            error_stem=f"FLEX_MOVEMENT_{key.upper()}_SNAPSHOT",
            display_name=label,
        )
        if path_error:
            return path_error
        assert path is not None
        record_sha256 = str(record.get("sha256") or "").lower()
        actual_sha256 = _sha256_file(path).lower()
        try:
            record_size = int(record.get("size_bytes"))
        except (TypeError, ValueError):
            record_size = -1
        if (
            require_recorded_hashes
            and (
                not SHA256_PATTERN.fullmatch(expected_hash)
                or record_sha256 != expected_hash
                or record_size != path.stat().st_size
            )
        ):
            return _error(
                "FLEX_MOVEMENT_STATIC_SNAPSHOT_RECORD_INVALID",
                f"{label}的路径、SHA256 或大小记录不完整。",
                raw_error=(
                    f"key={key}; input_sha256={expected_hash}; "
                    f"snapshot_sha256={record_sha256}; "
                    f"snapshot_size={record_size}; "
                    f"actual_size={path.stat().st_size}"
                ),
            )
        if (
            SHA256_PATTERN.fullmatch(expected_hash)
            and actual_sha256 != expected_hash
        ):
            return _error(
                "FLEX_MOVEMENT_STATIC_SNAPSHOT_HASH_MISMATCH",
                f"{label}在运行后与冻结 SHA256 不一致。",
                raw_error=(
                    f"key={key}; expected={expected_hash}; "
                    f"actual={actual_sha256}"
                ),
                suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
            )
    return None


def _compute_flexible_movement_for_run(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
    scores: list[dict[str, Any]],
    *,
    require_recorded_hashes: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    top_level_contract = _flexible_movement_contract(metadata)
    frozen_contract, frozen_contract_error = (
        _frozen_flexible_movement_contract(
            project_path,
            run_id,
            metadata,
        )
    )
    if frozen_contract_error:
        return None, frozen_contract_error
    if top_level_contract and not frozen_contract:
        return None, _error(
            "FLEX_MOVEMENT_CONTRACT_NOT_FROZEN",
            "运动分析合同只存在于可变 metadata，未冻结进柔性受体协议快照。",
            suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
        )
    contract = frozen_contract or top_level_contract
    if contract:
        expected_contract = {
            "schema_id": FLEXIBLE_MOVEMENT_SCHEMA_ID,
            "method": FLEXIBLE_MOVEMENT_METHOD,
            "artifact_file": _flexible_movement_relative_path(run_id),
            "required_after_analysis": True,
            "exclude_flexible_ca_root": True,
        }
        if any(
            contract.get(key) != value
            for key, value in expected_contract.items()
        ):
            return None, _error(
                "FLEX_MOVEMENT_CONTRACT_INVALID",
                "run metadata 中的柔性运动分析合同无效。",
                raw_error=(
                    f"expected={expected_contract}; recorded={contract}"
                ),
                suggestion="请保留该 run 供审计，并重新准备、执行新的柔性对接。",
            )
        require_recorded_hashes = True
    static_snapshot_error = _validate_flexible_static_run_snapshots(
        project_path,
        run_id,
        metadata,
        require_recorded_hashes=require_recorded_hashes,
    )
    if static_snapshot_error:
        return None, static_snapshot_error
    paths, path_error = _flexible_movement_source_paths(
        project_path,
        run_id,
        metadata,
    )
    if path_error:
        return None, path_error
    assert paths is not None
    movement = analyze_flexible_movement(
        paths["ligand_input"],
        paths["flex_input"],
        paths["vina_output"],
        exclude_flexible_ca=True,
    )
    if not movement.get("ok"):
        return None, movement
    if (
        movement.get("schema_id") != FLEXIBLE_MOVEMENT_SCHEMA_ID
        or movement.get("method") != FLEXIBLE_MOVEMENT_METHOD
        or movement.get("alignment_applied") is not False
    ):
        return None, _error(
            "FLEX_MOVEMENT_SCHEMA_INVALID",
            "柔性运动分析器返回了未知 schema、方法或对齐语义。",
            raw_error=(
                f"schema_id={movement.get('schema_id')}; "
                f"method={movement.get('method')}; "
                f"alignment_applied={movement.get('alignment_applied')}"
            ),
        )
    source_error = _validate_flexible_movement_source_hashes(
        metadata,
        movement,
        paths,
        require_recorded_hashes=require_recorded_hashes,
    )
    if source_error:
        return None, source_error
    movement_modes = [
        int(item.get("mode"))
        for item in movement.get("modes", [])
        if isinstance(item, dict) and isinstance(item.get("mode"), int)
    ]
    score_modes = [
        int(item.get("mode"))
        for item in scores
        if isinstance(item, dict) and isinstance(item.get("mode"), int)
    ]
    if movement_modes != score_modes:
        return None, _error(
            "FLEX_MOVEMENT_SCORE_MODE_MISMATCH",
            "柔性运动分析的构象编号与 scores.csv / Vina 日志不一致。",
            raw_error=(
                f"movement_modes={movement_modes}; score_modes={score_modes}"
            ),
            suggestion="请保留该 run 供审计，并重新执行新的柔性对接。",
        )
    return movement, None


def _movement_artifact_required(metadata: dict[str, Any]) -> bool:
    contract = _flexible_movement_contract(metadata)
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    return bool(
        contract.get("required_after_analysis") is True
        or metadata.get("flexible_movement_file")
        or isinstance(metadata.get("flexible_movement"), dict)
        or isinstance(artifacts.get("flexible_movement"), dict)
    )


def _load_flexible_movement_artifact(
    project_path: Path,
    run_id: str,
    metadata: dict[str, Any],
    scores: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _is_flexible_movement_run(metadata):
        return {
            "ok": True,
            "applicable": False,
            "available": False,
            "legacy_partial": False,
            "flexible_movement": None,
            "flexible_movement_file": "",
            "warning": "",
            "error": None,
        }

    expected_file = _flexible_movement_relative_path(run_id)
    frozen_contract, frozen_contract_error = (
        _frozen_flexible_movement_contract(
            project_path,
            run_id,
            metadata,
        )
    )
    if frozen_contract_error:
        return frozen_contract_error
    required = bool(frozen_contract) or _movement_artifact_required(metadata)
    recorded_file = str(metadata.get("flexible_movement_file") or "")
    if not recorded_file:
        if required:
            return _error(
                "FLEX_MOVEMENT_ARTIFACT_MISSING",
                "本次柔性对接要求运动分析证据，但 metadata 未记录 flexible_movement.json。",
                suggestion="请重新点击“解析结果”；若冻结文件已损坏，请重新执行新的 run。",
            )
        return {
            "ok": True,
            "applicable": True,
            "available": False,
            "legacy_partial": True,
            "flexible_movement": None,
            "flexible_movement_file": "",
            "warning": (
                "该柔性对接来自旧版运行记录，未保存配体与柔性侧链的分离运动分析；"
                "评分仍可读取，但几何证据不完整。"
            ),
            "error": None,
        }

    movement_path, path_error = _fixed_run_file(
        project_path,
        expected_relative_path=expected_file,
        recorded_relative_path=recorded_file,
        error_stem="FLEX_MOVEMENT_ARTIFACT",
        display_name=FLEXIBLE_MOVEMENT_FILENAME,
    )
    if path_error:
        return path_error
    assert movement_path is not None
    try:
        size_bytes = movement_path.stat().st_size
        if size_bytes <= 0 or size_bytes > FLEXIBLE_MOVEMENT_MAX_BYTES:
            raise ValueError(
                f"size_bytes={size_bytes}; max={FLEXIBLE_MOVEMENT_MAX_BYTES}"
            )
        raw = movement_path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        stored = json.loads(text)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_INVALID",
            "flexible_movement.json 为空、过大或不是有效的严格 UTF-8 JSON。",
            raw_error=str(exc),
            suggestion="请重新点击“解析结果”；若问题仍存在，请重新执行新的 run。",
        )
    if not isinstance(stored, dict):
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_INVALID",
            "flexible_movement.json 顶层必须是 JSON 对象。",
        )

    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    artifact = (
        artifacts.get("flexible_movement")
        if isinstance(artifacts.get("flexible_movement"), dict)
        else {}
    )
    expected_sha256 = str(artifact.get("sha256") or "").lower()
    expected_size = artifact.get("size_bytes")
    artifact_sha256 = (
        metadata.get("artifact_sha256")
        if isinstance(metadata.get("artifact_sha256"), dict)
        else {}
    )
    indexed_sha256 = str(
        artifact_sha256.get("flexible_movement") or ""
    ).lower()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        not SHA256_PATTERN.fullmatch(expected_sha256)
        or indexed_sha256 != expected_sha256
        or expected_size != len(raw)
        or actual_sha256 != expected_sha256
    ):
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_HASH_MISMATCH",
            "flexible_movement.json 与 metadata 中冻结的 SHA256 / 大小不一致。",
            raw_error=(
                f"expected_sha256={expected_sha256 or '<empty>'}; "
                f"indexed_sha256={indexed_sha256 or '<empty>'}; "
                f"actual_sha256={actual_sha256}; "
                f"expected_size={expected_size}; actual_size={len(raw)}"
            ),
            suggestion="请保留该 run 供审计；不要手工修改已发布的分析证据。",
        )
    try:
        canonical = canonical_flexible_movement_json(stored)
    except (TypeError, ValueError) as exc:
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_INVALID",
            "flexible_movement.json 含有不能规范化的数值或结构。",
            raw_error=str(exc),
        )
    if canonical != text:
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_NOT_CANONICAL",
            "flexible_movement.json 不是 DockStart 发布的规范 JSON 字节表示。",
            suggestion="请重新点击“解析结果”，不要手工重排或改写该证据文件。",
        )

    recomputed, compute_error = _compute_flexible_movement_for_run(
        project_path,
        run_id,
        metadata,
        scores,
        require_recorded_hashes=bool(_flexible_movement_contract(metadata)),
    )
    if compute_error:
        return compute_error
    assert recomputed is not None
    if canonical_flexible_movement_json(recomputed) != canonical:
        return _error(
            "FLEX_MOVEMENT_ARTIFACT_RECOMPUTE_MISMATCH",
            "flexible_movement.json 与冻结输入和输出重新计算的结果不一致。",
            suggestion="请保留该 run 供审计，并重新执行新的柔性对接。",
        )

    summary = (
        metadata.get("flexible_movement")
        if isinstance(metadata.get("flexible_movement"), dict)
        else {}
    )
    expected_summary = {
        "schema_id": stored.get("schema_id"),
        "method": stored.get("method"),
        "mode_count": stored.get("mode_count"),
        "alignment_applied": stored.get("alignment_applied"),
        "exclude_flexible_ca_root": stored.get(
            "exclude_flexible_ca_root"
        ),
    }
    if any(
        summary.get(key) != value
        for key, value in expected_summary.items()
    ):
        return _error(
            "FLEX_MOVEMENT_METADATA_MISMATCH",
            "metadata 中的柔性运动摘要与 flexible_movement.json 不一致。",
            raw_error=(
                f"metadata={summary}; artifact_summary={expected_summary}"
            ),
        )
    return {
        "ok": True,
        "applicable": True,
        "available": True,
        "legacy_partial": False,
        "flexible_movement": stored,
        "flexible_movement_file": expected_file,
        "warning": "",
        "error": None,
    }


def load_flexible_movement(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None
    if not _is_flexible_movement_run(metadata):
        return _error(
            "FLEX_MOVEMENT_NOT_APPLICABLE",
            "该 run 不是有限柔性侧链对接，不能读取柔性运动分析。",
        )
    scores_payload = load_scores_csv(project_dir, run_id)
    if not scores_payload.get("ok"):
        return scores_payload
    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "project": scores_payload.get("project"),
        "run_id": run_id,
        "metadata": scores_payload.get("metadata"),
        "flexible_movement": scores_payload.get("flexible_movement"),
        "flexible_movement_file": scores_payload.get(
            "flexible_movement_file",
            "",
        ),
        "available": scores_payload.get(
            "flexible_movement_available",
            False,
        ),
        "legacy_partial": scores_payload.get(
            "flexible_movement_legacy_partial",
            False,
        ),
        "warning": scores_payload.get("flexible_movement_warning", ""),
        "message": (
            "柔性运动分析已读取。"
            if scores_payload.get("flexible_movement_available")
            else "旧版柔性 run 没有保存运动分析，当前仅能读取评分。"
        ),
        "error": None,
    }


def load_scores_csv(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser().resolve()
    is_multiple_ligand = (
        _metadata_protocol_id(metadata) == MULTIPLE_LIGAND_PROTOCOL_ID
    )
    scores_file = str(metadata.get("scores_file") or Path("runs", run_id, "scores.csv").as_posix())
    scores_path, path_error = _project_relative_path_for_run(project_path, scores_file, "SCORES_CSV", "scores.csv")
    if path_error:
        return path_error
    assert scores_path is not None

    scores_snapshot: bytes | None = None
    if is_multiple_ligand:
        scores_snapshot, artifact_error = (
            _read_multiple_ligand_result_artifact_snapshot(
                project_path,
                run_id,
                metadata,
                artifact_key="scores",
                filename="scores.csv",
                metadata_path_key="scores_file",
                error_stem="MULTIPLE_LIGAND_SCORES",
                display_name="scores.csv",
            )
        )
        if artifact_error:
            return artifact_error
        assert scores_snapshot is not None
    elif _recorded_artifact_contract_present(
        metadata,
        RUN_SCORE_ARTIFACT_KEYS,
    ):
        expected_scores_file = Path(
            "runs",
            run_id,
            "scores.csv",
        ).as_posix()
        scores_snapshot, artifact_error = _read_verified_recorded_artifact(
            project_path,
            run_id,
            metadata,
            artifact_key="scores",
            expected_relative=expected_scores_file,
            recorded_relative=scores_file,
            contract_keys=RUN_SCORE_ARTIFACT_KEYS,
            error_stem="SCORES_CSV_ARTIFACT",
            display_name="scores.csv",
        )
        if artifact_error:
            return artifact_error
        assert scores_snapshot is not None
    else:
        if not scores_path.exists():
            return _error(
                "SCORES_CSV_NOT_FOUND",
                "没有找到该 run 的 scores.csv。",
                raw_error=str(scores_path),
                suggestion="请先点击“解析结果”，从 log.txt 导出 scores.csv。",
            )
        if not scores_path.is_file():
            return _error(
                "SCORES_CSV_NOT_FILE",
                "scores.csv 路径不是文件。",
                raw_error=str(scores_path),
                suggestion="请检查 run 目录结构是否完整。",
            )
        if scores_path.stat().st_size == 0:
            return _error(
                "SCORES_CSV_EMPTY",
                "scores.csv 为空，无法读取结果表格。",
                raw_error=str(scores_path),
                suggestion="请重新解析 Vina log 并导出 scores.csv。",
            )

    try:
        if scores_snapshot is not None:
            assert scores_snapshot is not None
            handle = io.StringIO(
                scores_snapshot.decode("utf-8"),
                newline="",
            )
        else:
            handle = scores_path.open("r", encoding="utf-8", newline="")
        with handle:
            reader = csv.DictReader(handle)
            expected_fields = (
                MULTIPLE_LIGAND_SCORES_CSV_FIELDS
                if is_multiple_ligand
                else SCORES_CSV_FIELDS
            )
            if tuple(reader.fieldnames or ()) != expected_fields:
                return _error(
                    "SCORES_CSV_HEADER_INVALID",
                    "scores.csv 表头不符合 DockStart 当前版本要求。",
                    raw_error=",".join(reader.fieldnames or []),
                    suggestion=(
                        "请重新运行多配体共同对接，恢复联合评分表。"
                        if is_multiple_ligand
                        else "请重新从 Vina log 解析并导出 scores.csv。"
                    ),
                )
            scores: list[dict[str, Any]] = []
            for line_number, row in enumerate(reader, start=2):
                parsed, row_error = (
                    _parse_multiple_ligand_scores_csv_row(row, line_number)
                    if is_multiple_ligand
                    else _parse_scores_csv_row(row, line_number)
                )
                if row_error:
                    return row_error
                assert parsed is not None
                scores.append(parsed)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "SCORES_CSV_READ_ERROR",
            "读取 scores.csv 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 scores.csv 是 UTF-8 文本文件。",
        )

    if not scores:
        return _error(
            "SCORES_CSV_NO_ROWS",
            "scores.csv 中没有结果记录。",
            suggestion="请重新解析包含 Vina 结果表格的 log.txt。",
        )

    if is_multiple_ligand:
        joint_manifest, manifest_error = (
            _load_multiple_ligand_joint_score_manifest(
                project_path,
                run_id,
                metadata,
            )
        )
        if manifest_error:
            return manifest_error
        assert joint_manifest is not None
        score_mismatch = _cross_check_multiple_ligand_scores(
            scores,
            joint_manifest,
        )
        if score_mismatch:
            return score_mismatch

    flexible_movement = _load_flexible_movement_artifact(
        project_path,
        run_id,
        metadata,
        scores,
    )
    if not flexible_movement.get("ok"):
        return flexible_movement

    loaded = load_project(project_dir)
    project = loaded.get("project") if loaded.get("ok") else None
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project,
        "run_id": run_id,
        "metadata": metadata,
        "scores": scores,
        "scores_file": scores_file,
        "project_scores_file": str(metadata.get("project_scores_file") or _project_scores_file(metadata)),
        "best_affinity": metadata.get("best_affinity", scores[0]["affinity_kcal_mol"]),
        "analyzed_at": metadata.get("analyzed_at", ""),
        "flexible_movement": flexible_movement.get("flexible_movement"),
        "flexible_movement_file": flexible_movement.get(
            "flexible_movement_file",
            "",
        ),
        "flexible_movement_available": flexible_movement.get(
            "available",
            False,
        ),
        "flexible_movement_legacy_partial": flexible_movement.get(
            "legacy_partial",
            False,
        ),
        "flexible_movement_warning": flexible_movement.get("warning", ""),
        "message": (
            "多配体共同对接联合评分表已读取。"
            if _metadata_protocol_id(metadata) == MULTIPLE_LIGAND_PROTOCOL_ID
            else "scores.csv 已读取。"
        ),
        "error": None,
    }


def _evaluation_relative_path(run_id: str) -> str:
    return Path("runs", run_id, "evaluation.json").as_posix()


def load_vina_evaluation(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None
    run_mode = _metadata_run_mode(metadata)
    if run_mode == "dock":
        return _error(
            "VINA_EVALUATION_NOT_APPLICABLE",
            "该 run 是全局对接，应读取 scores.csv。",
        )

    project_path = Path(project_dir).expanduser()
    expected_file = _evaluation_relative_path(run_id)
    evaluation_file = str(metadata.get("evaluation_file") or expected_file)
    if evaluation_file != expected_file:
        return _error(
            "VINA_EVALUATION_PATH_INVALID",
            "姿势评价结果路径不是本次 run 的固定路径，拒绝读取。",
            raw_error=evaluation_file,
        )
    evaluation_path, path_error = _project_relative_path_for_run(
        project_path,
        evaluation_file,
        "VINA_EVALUATION",
        "evaluation.json",
    )
    if path_error:
        return path_error
    assert evaluation_path is not None
    if not evaluation_path.is_file() or evaluation_path.stat().st_size <= 0:
        return _error(
            "VINA_EVALUATION_NOT_FOUND",
            "没有找到该 run 的 evaluation.json。",
            raw_error=str(evaluation_path),
            suggestion="请先解析该姿势评价运行。",
        )
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    evaluation_artifact = (
        artifacts.get("evaluation")
        if isinstance(artifacts.get("evaluation"), dict)
        else {}
    )
    if artifacts or isinstance(metadata.get("artifact_sha256"), dict):
        expected_sha256 = str(evaluation_artifact.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            return _error(
                "VINA_EVALUATION_ARTIFACT_HASH_MISSING",
                "metadata 中缺少可信的 evaluation.json SHA256，拒绝读取姿势评价结果。",
                suggestion="请保留该 run 作为审计记录，并重新解析或重新执行新的评价运行。",
            )
        actual_sha256 = _sha256_file(evaluation_path)
        if actual_sha256.lower() != expected_sha256.lower():
            return _error(
                "VINA_EVALUATION_ARTIFACT_HASH_MISMATCH",
                "evaluation.json 在结果解析后发生变化，拒绝读取或生成报告。",
                raw_error=(
                    f"expected={expected_sha256}; actual={actual_sha256}; "
                    f"path={evaluation_path}"
                ),
                suggestion="请保留该 run 作为审计记录，并重新解析未被修改的运行日志。",
            )
    try:
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _error(
            "VINA_EVALUATION_READ_ERROR",
            "读取 evaluation.json 时发生错误。",
            raw_error=str(exc),
        )
    if (
        not isinstance(evaluation, dict)
        or _normalize_run_mode(evaluation.get("run_mode")) != run_mode
        or isinstance(evaluation.get("primary_score_kcal_mol"), bool)
        or not isinstance(evaluation.get("primary_score_kcal_mol"), (int, float))
        or not math.isfinite(float(evaluation.get("primary_score_kcal_mol")))
        or not isinstance(evaluation.get("energy_terms"), list)
    ):
        return _error(
            "VINA_EVALUATION_SCHEMA_INVALID",
            "evaluation.json 的结构与本次运行模式不一致。",
            suggestion="请重新解析该 run。",
        )
    unbound_reference = evaluation.get("unbound_energy")
    if unbound_reference is not None:
        unbound_mode = (
            str(unbound_reference.get("mode") or "")
            if isinstance(unbound_reference, dict)
            else ""
        )
        unbound_value = (
            unbound_reference.get("value_kcal_mol")
            if isinstance(unbound_reference, dict)
            else None
        )
        valid_explicit = (
            run_mode == "score_only"
            and unbound_mode == "explicit"
            and not isinstance(unbound_value, bool)
            and isinstance(unbound_value, (int, float))
            and math.isfinite(float(unbound_value))
        )
        valid_default = (
            run_mode == "score_only"
            and unbound_mode == "vina_default"
            and unbound_value is None
        )
        if not (valid_explicit or valid_default):
            return _error(
                "VINA_EVALUATION_SCHEMA_INVALID",
                "evaluation.json 的未结合态参考能量记录无效。",
                suggestion="请重新解析该 run。",
            )
    loaded = load_project(project_dir)
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": loaded.get("project") if loaded.get("ok") else None,
        "run_id": run_id,
        "metadata": metadata,
        "evaluation": evaluation,
        "evaluation_file": evaluation_file,
        "primary_score_kcal_mol": evaluation["primary_score_kcal_mol"],
        "analyzed_at": str(evaluation.get("analyzed_at") or metadata.get("analyzed_at") or ""),
        "message": "姿势评价结果已读取。",
        "error": None,
    }


def _analyze_vina_evaluation(project_dir: str, run_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    run_mode = _metadata_run_mode(metadata)
    project_path = Path(project_dir).expanduser().resolve()
    output_file = str(metadata.get("output_file") or "")
    input_pose_file = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
    baseline_parsed: dict[str, Any] | None = None
    baseline_log_file = ""
    comparison_reason = ""
    pose_comparison: dict[str, Any] | None = None

    def require_recorded_hash(
        artifact_key: str,
        path: Path,
        label: str,
    ) -> dict[str, Any] | None:
        artifacts = (
            metadata.get("artifacts")
            if isinstance(metadata.get("artifacts"), dict)
            else {}
        )
        record = artifacts.get(artifact_key) if isinstance(artifacts.get(artifact_key), dict) else {}
        expected = str(record.get("sha256") or "")
        error_prefix = (
            "LOCAL_ONLY"
            if run_mode == "local_only"
            else "VINA_EVALUATION"
        )
        rejected_action = (
            "生成前后比较"
            if run_mode == "local_only"
            else "生成姿势评分结果"
        )
        rerun_label = "local_only run" if run_mode == "local_only" else "score_only run"
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return _error(
                f"{error_prefix}_ARTIFACT_HASH_MISSING",
                f"metadata 中缺少可信的{label} SHA256，拒绝{rejected_action}。",
                suggestion=f"请重新准备并执行新的 {rerun_label}。",
            )
        if not path.is_file() or path.stat().st_size <= 0:
            return _error(
                f"{error_prefix}_ARTIFACT_NOT_FOUND",
                f"没有找到非空的{label}。",
                raw_error=str(path),
            )
        actual = _sha256_file(path)
        if actual.lower() != expected.lower():
            return _error(
                f"{error_prefix}_ARTIFACT_HASH_MISMATCH",
                f"{label}在运行完成后发生变化，拒绝{rejected_action}。",
                raw_error=f"expected={expected}; actual={actual}; path={path}",
                suggestion=f"请保留该 run 作为审计记录，并重新准备新的 {rerun_label}。",
            )
        return None

    vina_snapshot_for_audit = (
        _run_snapshot_mapping(metadata, "vina")
        if run_mode == "score_only"
        else {}
    )
    docking_protocol_for_audit = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    receptor_mode_for_audit = str(
        docking_protocol_for_audit.get("receptor_mode")
        or docking_protocol_for_audit.get("mode")
        or "rigid"
    ).strip().lower()
    explicit_unbound_requires_audit = (
        run_mode == "score_only"
        and _metadata_scoring_protocol(metadata) != "ad4_maps"
        and receptor_mode_for_audit != "flexible"
        and vina_snapshot_for_audit.get("unbound_energy") is not None
    )
    if run_mode == "score_only" and (
        explicit_unbound_requires_audit
        or isinstance(metadata.get("execution_vina"), dict)
        or isinstance(metadata.get("artifacts"), dict)
    ):
        integrity_error = require_recorded_hash(
            "log",
            project_path / Path("runs", run_id, "log.txt"),
            "姿势评分日志",
        )
        if integrity_error:
            return integrity_error

    parsed = parse_vina_evaluation_file(project_dir, run_id, run_mode)
    if not parsed.get("ok"):
        return parsed

    if run_mode == "local_only":
        expected_output = _run_output_file(run_id, run_mode)
        if output_file != expected_output:
            return _error(
                "LOCAL_ONLY_OUTPUT_PATH_INVALID",
                "局部优化输出路径与固定运行路径不一致。",
                raw_error=f"metadata={output_file!r}; expected={expected_output!r}",
            )
        output_path = project_path / output_file
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            return _error(
                "LOCAL_ONLY_OUTPUT_NOT_FOUND",
                "局部优化未生成非空的 optimized.pdbqt。",
                raw_error=str(output_path),
                suggestion="请检查 Vina 日志并重新运行 local_only。",
            )
        input_pose_path = project_path / input_pose_file
        if _local_only_execution_plan_enabled(metadata):
            baseline_files = _local_only_baseline_files(run_id)
            baseline_log_file = baseline_files["log_file"]
            baseline_log_path = project_path / baseline_log_file
            for artifact_key, path, label in (
                ("baseline_log", baseline_log_path, "输入姿势评分日志"),
                ("log", project_path / Path("runs", run_id, "log.txt"), "局部优化日志"),
                ("out", output_path, "局部优化后姿势"),
            ):
                integrity_error = require_recorded_hash(artifact_key, path, label)
                if integrity_error:
                    return integrity_error
            expected_input_hash = str(
                (metadata.get("input_sha256") or {}).get("ligand")
                if isinstance(metadata.get("input_sha256"), dict)
                else ""
            )
            if (
                not re.fullmatch(r"[0-9a-fA-F]{64}", expected_input_hash)
                or not input_pose_path.is_file()
                or _sha256_file(input_pose_path).lower() != expected_input_hash.lower()
            ):
                return _error(
                    "LOCAL_ONLY_INPUT_INTEGRITY_MISMATCH",
                    "输入配体快照缺少可信哈希或已发生变化，拒绝生成前后比较。",
                    raw_error=str(input_pose_path),
                    suggestion="请重新准备并执行新的 local_only run。",
                )
            try:
                baseline_parsed = parse_vina_evaluation_text(
                    baseline_log_path.read_text(encoding="utf-8"),
                    "score_only",
                )
            except (OSError, UnicodeDecodeError) as exc:
                return _error(
                    "LOCAL_ONLY_BASELINE_LOG_READ_ERROR",
                    "无法读取输入姿势评分日志。",
                    raw_error=str(exc),
                )
            if not baseline_parsed.get("ok"):
                return baseline_parsed
            same_scoring = (
                str(baseline_parsed.get("scoring_function") or "")
                == str(parsed.get("scoring_function") or "")
            )
            baseline_grid = baseline_parsed.get("grid") if isinstance(baseline_parsed.get("grid"), dict) else {}
            optimized_grid = parsed.get("grid") if isinstance(parsed.get("grid"), dict) else {}
            same_grid = all(
                baseline_grid.get(key) == optimized_grid.get(key)
                for key in ("mode", "center", "size", "spacing_angstrom")
            )
            if not same_scoring or not same_grid:
                return _error(
                    "LOCAL_ONLY_COMPARISON_PROTOCOL_MISMATCH",
                    "输入评分与局部优化没有使用一致的评分函数或网格记录，拒绝计算差值。",
                    raw_error=json.dumps(
                        {
                            "baseline_scoring": baseline_parsed.get("scoring_function"),
                            "optimized_scoring": parsed.get("scoring_function"),
                            "baseline_grid": baseline_grid,
                            "optimized_grid": optimized_grid,
                        },
                        ensure_ascii=False,
                    ),
                )
        else:
            comparison_reason = "该历史运行未记录输入姿势 score_only 基线，无法计算评分差。"

        pose_comparison = compare_local_only_poses(input_pose_path, output_path)

    analyzed_at = _now_iso()
    evaluation_file = _evaluation_relative_path(run_id)
    input_score = (
        baseline_parsed.get("primary_score_kcal_mol")
        if isinstance(baseline_parsed, dict) and baseline_parsed.get("ok")
        else None
    )
    optimized_score = parsed["primary_score_kcal_mol"]
    score_change = (
        float(optimized_score) - float(input_score)
        if isinstance(input_score, (int, float))
        else None
    )
    phase_records = (
        copy.deepcopy(metadata.get("execution_phases"))
        if isinstance(metadata.get("execution_phases"), dict)
        else {}
    )
    stages = [
        {"id": stage_id, **copy.deepcopy(phase_records.get(stage_id) or {})}
        for stage_id in ("input_score", "local_optimization")
        if isinstance(phase_records.get(stage_id), dict)
    ]
    unbound_reference: dict[str, Any] | None = None
    if run_mode == "score_only":
        vina_snapshot = _run_snapshot_mapping(metadata, "vina")
        snapshot_value = vina_snapshot.get("unbound_energy")
        docking_protocol = (
            metadata.get("docking_protocol")
            if isinstance(metadata.get("docking_protocol"), dict)
            else {}
        )
        receptor_mode = str(
            docking_protocol.get("receptor_mode")
            or docking_protocol.get("mode")
            or "rigid"
        ).strip().lower()
        if (
            _metadata_scoring_protocol(metadata) != "ad4_maps"
            and receptor_mode != "flexible"
            and snapshot_value is not None
        ):
            if (
                isinstance(snapshot_value, bool)
                or not isinstance(snapshot_value, (int, float))
                or not math.isfinite(float(snapshot_value))
            ):
                return _error(
                    "VINA_EVALUATION_UNBOUND_REFERENCE_INVALID",
                    "运行快照中的未结合态参考能量不是有限数值。",
                    raw_error=repr(snapshot_value),
                    suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
                )
            explicit_value = float(snapshot_value)
            energy_terms = {
                str(item.get("key") or ""): item
                for item in parsed.get("energy_terms", [])
                if isinstance(item, dict) and item.get("key")
            }
            numbered_terms = {
                "final_intermolecular_energy": 1,
                "final_total_internal_energy": 2,
                "torsional_free_energy": 3,
                "unbound_system_energy": 4,
            }
            invalid_terms = [
                {
                    "key": key,
                    "expected_term_number": term_number,
                    "actual_term_number": (
                        energy_terms.get(key, {}).get("term_number")
                        if isinstance(energy_terms.get(key), dict)
                        else None
                    ),
                    "value_kcal_mol": (
                        energy_terms.get(key, {}).get("value_kcal_mol")
                        if isinstance(energy_terms.get(key), dict)
                        else None
                    ),
                }
                for key, term_number in numbered_terms.items()
                if (
                    not isinstance(energy_terms.get(key), dict)
                    or energy_terms[key].get("term_number") != term_number
                    or isinstance(energy_terms[key].get("value_kcal_mol"), bool)
                    or not isinstance(
                        energy_terms[key].get("value_kcal_mol"),
                        (int, float),
                    )
                    or not math.isfinite(
                        float(energy_terms[key].get("value_kcal_mol")),
                    )
                )
            ]
            estimated_term = energy_terms.get(
                "estimated_free_energy_of_binding",
            )
            if (
                not isinstance(estimated_term, dict)
                or isinstance(estimated_term.get("value_kcal_mol"), bool)
                or not isinstance(
                    estimated_term.get("value_kcal_mol"),
                    (int, float),
                )
                or not math.isfinite(
                    float(estimated_term.get("value_kcal_mol")),
                )
            ):
                invalid_terms.append(
                    {
                        "key": "estimated_free_energy_of_binding",
                        "expected_term_number": None,
                        "actual_term_number": (
                            estimated_term.get("term_number")
                            if isinstance(estimated_term, dict)
                            else None
                        ),
                        "value_kcal_mol": (
                            estimated_term.get("value_kcal_mol")
                            if isinstance(estimated_term, dict)
                            else None
                        ),
                    }
                )
            if invalid_terms:
                return _error(
                    "VINA_EVALUATION_UNBOUND_TERM_INVALID",
                    "Vina 日志中的显式未结合态能量分解不完整或项号不正确。",
                    raw_error=json.dumps(invalid_terms, ensure_ascii=False),
                    suggestion=(
                        "请保留该 run 作为审计记录，并确认日志来自单次完整的 "
                        "Vina/Vinardo score_only 运行。"
                    ),
                )

            logged_unbound_term = energy_terms["unbound_system_energy"]
            logged_unbound_value = (
                logged_unbound_term.get("value_kcal_mol")
                if isinstance(logged_unbound_term, dict)
                else None
            )
            if (
                isinstance(logged_unbound_value, bool)
                or not isinstance(logged_unbound_value, (int, float))
                or not math.isfinite(float(logged_unbound_value))
                or not math.isclose(
                    float(logged_unbound_value),
                    explicit_value,
                    rel_tol=0.0,
                    abs_tol=0.00051,
                )
            ):
                return _error(
                    "VINA_EVALUATION_UNBOUND_REFERENCE_MISMATCH",
                    "Vina 日志中的未结合体系能量与运行快照不一致，拒绝将该结果标记为显式参考评分。",
                    raw_error=(
                        f"snapshot={explicit_value!r}; "
                        f"log={logged_unbound_value!r}"
                    ),
                    suggestion=(
                        "请保留该 run 作为审计记录，并确认当前 Vina 支持 "
                        "--unbound_energy 后重新准备运行。"
                    ),
                )
            expected_score = (
                float(
                    energy_terms["final_intermolecular_energy"][
                        "value_kcal_mol"
                    ]
                )
                + float(
                    energy_terms["final_total_internal_energy"][
                        "value_kcal_mol"
                    ]
                )
                + float(
                    energy_terms["torsional_free_energy"][
                        "value_kcal_mol"
                    ]
                )
                - float(logged_unbound_value)
            )
            logged_score = float(estimated_term["value_kcal_mol"])
            if not math.isclose(
                logged_score,
                expected_score,
                rel_tol=0.0,
                abs_tol=0.0026,
            ):
                return _error(
                    "VINA_EVALUATION_ENERGY_BALANCE_MISMATCH",
                    "Vina 日志的总评分与第 (1)＋(2)＋(3)－(4) 项不一致，拒绝将结果标记为显式参考评分。",
                    raw_error=(
                        f"reported={logged_score!r}; "
                        f"terms_1_plus_2_plus_3_minus_4={expected_score!r}"
                    ),
                    suggestion=(
                        "请保留该 run 作为审计记录，并确认日志未被修改、"
                        "当前 Vina 确实应用了 --unbound_energy。"
                    ),
                )
            unbound_reference = {
                "mode": "explicit",
                "value_kcal_mol": explicit_value,
                "comparison_warning": (
                    "本次评分使用显式未结合态参考能量；"
                    "只与输入、结构准备、评分函数和参考能量相同的运行比较。"
                ),
            }
        else:
            unbound_reference = {
                "mode": "vina_default",
                "value_kcal_mol": None,
            }
    evaluation = {
        "schema_version": 2 if run_mode == "local_only" else 1,
        "run_id": run_id,
        "run_mode": run_mode,
        "kind": run_mode,
        "analyzed_at": analyzed_at,
        "scoring_protocol": _metadata_scoring_protocol(metadata),
        "scoring_function": parsed.get("scoring_function") or metadata.get("scoring_function") or "",
        "primary_score_kcal_mol": parsed["primary_score_kcal_mol"],
        "energy_terms": copy.deepcopy(parsed["energy_terms"]),
        "input_energy_terms": (
            copy.deepcopy(baseline_parsed.get("energy_terms") or [])
            if isinstance(baseline_parsed, dict) and baseline_parsed.get("ok")
            else []
        ),
        "grid": copy.deepcopy(parsed.get("grid") or {}),
        "autobox": bool(metadata.get("autobox")),
        "input_pose_file": input_pose_file,
        "output_pose_file": output_file if run_mode == "local_only" else "",
        "pose_file": str(metadata.get("pose_file") or _run_pose_file(run_id, run_mode)),
        "output_pose_generated": run_mode == "local_only",
        **(
            {"unbound_energy": unbound_reference}
            if unbound_reference is not None
            else {}
        ),
        **(
            {
                "comparison": {
                    "comparable": baseline_parsed is not None and baseline_parsed.get("ok") is True,
                    "reason": comparison_reason or None,
                    "input_score_kcal_mol": input_score,
                    "optimized_score_kcal_mol": optimized_score,
                    "delta_score_kcal_mol": score_change,
                    "delta_definition": "optimized_minus_input",
                    "baseline_log_file": baseline_log_file,
                    "optimized_log_file": str(metadata.get("log_file") or ""),
                    "protocol": {
                        "comparable": baseline_parsed is not None and baseline_parsed.get("ok") is True,
                        "same_config_snapshot": baseline_parsed is not None,
                        "same_input_snapshot": baseline_parsed is not None,
                        "scoring_protocol": _metadata_scoring_protocol(metadata),
                        "scoring_function": parsed.get("scoring_function")
                        or metadata.get("scoring_function")
                        or "",
                        "config_sha256": str(
                            (metadata.get("input_sha256") or {}).get("config")
                            if isinstance(metadata.get("input_sha256"), dict)
                            else ""
                        ),
                    },
                    "geometry": copy.deepcopy(pose_comparison),
                },
                "pose_comparison": copy.deepcopy(pose_comparison),
                "stages": stages,
            }
            if run_mode == "local_only"
            else {}
        ),
        "scientific_note": (
            "该数值是输入姿势的单点评分；未执行全局构象搜索，也没有生成新构象。"
            if run_mode == "score_only"
            else "该数值来自输入姿势附近的局部优化；不代表已完成全局构象搜索。"
        ),
    }
    evaluation_path = project_path / evaluation_file
    try:
        _atomic_write_text(evaluation_path, json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_EVALUATION_WRITE_ERROR",
            "写入 evaluation.json 时发生错误。",
            raw_error=str(exc),
        )
    evaluation_artifact = _hash_snapshot(evaluation_path, evaluation_file)

    def merge_analysis(current: dict[str, Any]) -> dict[str, Any]:
        current.update(
            {
                "evaluation_file": evaluation_file,
                "primary_score_kcal_mol": evaluation["primary_score_kcal_mol"],
                **(
                    {
                        "baseline_score_kcal_mol": input_score,
                        "score_change_kcal_mol": score_change,
                        "score_change_definition": "optimized_minus_input",
                        "comparison_available": baseline_parsed is not None
                        and baseline_parsed.get("ok") is True,
                    }
                    if run_mode == "local_only"
                    else {}
                ),
                "analyzed_at": analyzed_at,
            }
        )
        _with_artifact_hashes(current, {"evaluation": evaluation_artifact})
        return current

    updated_metadata, metadata_error = _update_run_metadata_transaction(
        project_dir,
        run_id,
        merge_analysis,
    )
    if metadata_error:
        return metadata_error
    assert updated_metadata is not None
    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "run_mode": run_mode,
            "primary_score_kcal_mol": evaluation["primary_score_kcal_mol"],
            **(
                {
                    "baseline_score_kcal_mol": input_score,
                    "score_change_kcal_mol": score_change,
                    "comparison_available": baseline_parsed is not None
                    and baseline_parsed.get("ok") is True,
                }
                if run_mode == "local_only"
                else {}
            ),
            "evaluation_file": evaluation_file,
            "analyzed_at": analyzed_at,
        },
    )
    if not project_update.get("ok"):
        return project_update
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project_update.get("project"),
        "run_id": run_id,
        "metadata": updated_metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "scores": [],
        "evaluation": evaluation,
        "evaluation_file": evaluation_file,
        "primary_score_kcal_mol": evaluation["primary_score_kcal_mol"],
        "analyzed_at": analyzed_at,
        "message": "当前姿势评分已解析。" if run_mode == "score_only" else "局部优化结果已解析。",
        "error": None,
    }


def analyze_vina_run_results(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    status = str(metadata.get("status") or "")
    if status != "finished":
        return _error(
            "RUN_STATUS_NOT_FINISHED",
            f"当前 run 状态为 {status or 'unknown'}，只有 finished 状态的 run 可以解析结果。",
            suggestion="请先成功运行 Vina，确认 run.status 为 finished 后再解析结果。",
        )

    if _metadata_run_mode(metadata) != "dock":
        return _analyze_vina_evaluation(project_dir, run_id, metadata)

    project_path = Path(project_dir).expanduser().resolve()
    expected_log_file = Path("runs", run_id, "log.txt").as_posix()
    if _recorded_artifact_contract_present(
        metadata,
        RUN_EXECUTION_ARTIFACT_KEYS,
    ):
        log_snapshot, log_integrity_error = (
            _read_verified_recorded_artifact(
                project_path,
                run_id,
                metadata,
                artifact_key="log",
                expected_relative=expected_log_file,
                recorded_relative=str(
                    metadata.get("log_file") or expected_log_file
                ),
                contract_keys=RUN_EXECUTION_ARTIFACT_KEYS,
                error_stem="RUN_LOG_ARTIFACT",
                display_name="log.txt",
            )
        )
        if log_integrity_error:
            return log_integrity_error
        assert log_snapshot is not None
        try:
            parsed_scores = parse_vina_log_text(
                log_snapshot.decode("utf-8"),
            )
        except UnicodeDecodeError as exc:
            return _error(
                "VINA_LOG_READ_ERROR",
                "读取 log.txt 时发生编码错误。",
                raw_error=str(exc),
                suggestion="请保留该 run 供审计，并重新执行新的运行记录。",
            )
    else:
        parsed_scores = parse_vina_log_file(project_dir, run_id)
    if _is_error_payload(parsed_scores):
        return parsed_scores
    assert isinstance(parsed_scores, list)

    hydrated_result_scores: list[dict[str, Any]] | None = None
    if _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID:
        # Import at runtime to avoid the project/hydrated_run module cycle.
        from dockstart_core.hydrated_run import load_hydrated_results

        hydrated_results = load_hydrated_results(project_dir, run_id)
        if not hydrated_results.get("ok"):
            return hydrated_results
        hydrated_result_scores = [
            copy.deepcopy(item)
            for item in hydrated_results.get("scores", [])
            if isinstance(item, dict)
        ]
        if not hydrated_result_scores:
            return _error(
                "HYDRATED_RESULT_SCORES_EMPTY",
                "水合对接结果没有可与实际 PDBQT 构象绑定的评分记录。",
                suggestion="请保留该 run 供审计，并重新执行新的水合对接。",
            )

    movement: dict[str, Any] | None = None
    movement_file = ""
    movement_text = ""
    movement_sha256 = ""
    movement_path: Path | None = None
    movement_snapshot: dict[str, Any] | None = None
    if _is_flexible_movement_run(metadata):
        movement, movement_error = _compute_flexible_movement_for_run(
            project_path,
            run_id,
            metadata,
            parsed_scores,
            require_recorded_hashes=bool(
                _flexible_movement_contract(metadata)
            ),
        )
        if movement_error:
            return movement_error
        assert movement is not None
        movement_file = _flexible_movement_relative_path(run_id)
        movement_text = canonical_flexible_movement_json(movement)
        movement_bytes = movement_text.encode("utf-8")
        if len(movement_bytes) > FLEXIBLE_MOVEMENT_MAX_BYTES:
            return _error(
                "FLEX_MOVEMENT_ARTIFACT_TOO_LARGE",
                "柔性运动分析超过 64 MB 安全上限，未发布结果。",
                raw_error=(
                    f"size_bytes={len(movement_bytes)}; "
                    f"max={FLEXIBLE_MOVEMENT_MAX_BYTES}"
                ),
                suggestion="请减少 num_modes 或柔性残基数量后创建新的 run。",
            )
        movement_sha256 = hashlib.sha256(movement_bytes).hexdigest()
        existing_artifacts = (
            metadata.get("artifacts")
            if isinstance(metadata.get("artifacts"), dict)
            else {}
        )
        existing_movement_artifact = (
            existing_artifacts.get("flexible_movement")
            if isinstance(
                existing_artifacts.get("flexible_movement"),
                dict,
            )
            else {}
        )
        existing_movement_sha256 = str(
            existing_movement_artifact.get("sha256") or ""
        ).lower()
        if (
            SHA256_PATTERN.fullmatch(existing_movement_sha256)
            and existing_movement_sha256 != movement_sha256
        ):
            return _error(
                "FLEX_MOVEMENT_REANALYSIS_CHANGED",
                "相同冻结输入与输出的柔性运动重算字节发生变化，已拒绝覆盖历史证据。",
                raw_error=(
                    f"recorded={existing_movement_sha256}; "
                    f"recomputed={movement_sha256}"
                ),
                suggestion="请保留该 run 供审计；算法升级应创建新的 run 或新 schema。",
            )
        movement_path, movement_path_issue = _safe_fixed_run_artifact(
            project_path,
            run_id,
            FLEXIBLE_MOVEMENT_FILENAME,
        )
        if movement_path_issue:
            return _error(
                "FLEX_MOVEMENT_ARTIFACT_PATH_UNSAFE",
                "无法在本次 run 的固定路径发布 flexible_movement.json。",
                raw_error=movement_path_issue,
            )
        assert movement_path is not None

    exported = export_scores_csv(project_dir, run_id, parsed_scores)
    if not exported.get("ok"):
        return exported

    analyzed_at = _now_iso()
    best_affinity = parsed_scores[0]["affinity_kcal_mol"]
    if movement is not None:
        assert movement_path is not None
        try:
            _atomic_write_text(movement_path, movement_text)
        except OSError as exc:
            return _error(
                "FLEX_MOVEMENT_ARTIFACT_WRITE_ERROR",
                "写入 flexible_movement.json 时发生错误。",
                raw_error=str(exc),
                suggestion="请确认 run 目录可写后重新解析结果。",
            )
        movement_snapshot = _hash_snapshot(
            movement_path,
            movement_file,
        )
        if (
            movement_snapshot.get("sha256") != movement_sha256
            or movement_snapshot.get("size_bytes")
            != len(movement_bytes)
        ):
            return _error(
                "FLEX_MOVEMENT_ARTIFACT_WRITE_VERIFY_ERROR",
                "flexible_movement.json 写入后的字节校验失败。",
                raw_error=str(movement_snapshot),
            )
    scores_artifacts = {
        "scores": _hash_snapshot(project_path / exported["scores_file"], exported["scores_file"]),
        "project_scores": _hash_snapshot(
            project_path / exported["project_scores_file"],
            exported["project_scores_file"],
        ),
        **(
            {"flexible_movement": movement_snapshot}
            if movement_snapshot is not None
            else {}
        ),
    }
    for artifact_key, artifact in scores_artifacts.items():
        if (
            artifact.get("exists") is not True
            or isinstance(artifact.get("size_bytes"), bool)
            or not isinstance(artifact.get("size_bytes"), int)
            or int(artifact.get("size_bytes") or 0) <= 0
            or SHA256_PATTERN.fullmatch(
                str(artifact.get("sha256") or "").lower()
            )
            is None
        ):
            return _error(
                "SCORES_ARTIFACT_WRITE_VERIFY_ERROR",
                "评分或柔性运动结果写入后未通过文件完整性校验。",
                raw_error=f"{artifact_key}={artifact}",
                suggestion="请确认项目目录可写后重新解析该 run。",
            )

    def merge_analysis(current: dict[str, Any]) -> dict[str, Any]:
        current.update(
            {
                "best_affinity": best_affinity,
                "scores_file": exported["scores_file"],
                "project_scores_file": exported["project_scores_file"],
                "analyzed_at": analyzed_at,
                **(
                    {
                        "flexible_movement_file": movement_file,
                        "flexible_movement": {
                            "schema_id": movement.get("schema_id"),
                            "method": movement.get("method"),
                            "mode_count": movement.get("mode_count"),
                            "alignment_applied": movement.get(
                                "alignment_applied"
                            ),
                            "exclude_flexible_ca_root": movement.get(
                                "exclude_flexible_ca_root"
                            ),
                        },
                    }
                    if movement is not None
                    else {}
                ),
            },
        )
        _with_artifact_hashes(current, scores_artifacts)
        return current

    metadata, metadata_error = _update_run_metadata_transaction(project_dir, run_id, merge_analysis)
    if metadata_error:
        return metadata_error
    assert metadata is not None

    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "best_affinity": best_affinity,
            "scores_file": exported["scores_file"],
            "analyzed_at": analyzed_at,
            **(
                {
                    "flexible_movement_file": movement_file,
                    "flexible_movement_mode_count": movement.get(
                        "mode_count"
                    ),
                }
                if movement is not None
                else {}
            ),
        },
    )
    if not project_update.get("ok"):
        return project_update

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": project_update.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "scores": (
            hydrated_result_scores
            if hydrated_result_scores is not None
            else parsed_scores
        ),
        "scores_file": exported["scores_file"],
        "project_scores_file": exported["project_scores_file"],
        "best_affinity": best_affinity,
        "analyzed_at": analyzed_at,
        "flexible_movement": movement,
        "flexible_movement_file": movement_file,
        "message": (
            "AutoDock4 结果已解析；该评分与 Vina/Vinardo 不可直接比较。"
            if _metadata_scoring_protocol(metadata) == "ad4_maps"
            else "Vina 结果已解析，scores.csv 已导出。"
        ),
        "error": None,
    }


def _markdown_cell(value: Any) -> str:
    if value is None or value == "":
        text = "未记录"
    elif isinstance(value, float):
        text = _format_config_number(value)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(_markdown_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _command_for_report(metadata: dict[str, Any]) -> list[str]:
    command = metadata.get("command")
    if isinstance(command, list):
        return [str(item) for item in command]
    return []


def _run_snapshot_mapping(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    direct = metadata.get(f"{key}_snapshot")
    if isinstance(direct, dict):
        return copy.deepcopy(direct)
    snapshots = metadata.get("snapshots")
    nested = snapshots.get(key) if isinstance(snapshots, dict) else None
    return copy.deepcopy(nested) if isinstance(nested, dict) else {}


def _run_input_snapshot_file(metadata: dict[str, Any], role: str, fallback: str) -> str:
    snapshots = metadata.get("snapshots")
    inputs = snapshots.get("inputs") if isinstance(snapshots, dict) else None
    item = inputs.get(role) if isinstance(inputs, dict) else None
    if isinstance(item, dict):
        for key in ("relative_path", "snapshot_file"):
            value = str(item.get(key) or "").strip()
            if value:
                return Path(value).as_posix()
    return Path(fallback).as_posix() if fallback else ""


def _load_report_context(project_dir: str, run_id: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    try:
        project = _project_from_dict(loaded["project"], project_path)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_REPORT_CONFIG_INVALID",
            "project.json 格式不完整，无法导出 Markdown 报告。",
            raw_error=str(exc),
            suggestion="请检查 project.json 中 receptor、ligand、box、vina 和 runs 字段是否完整。",
        )

    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None

    status = str(metadata.get("status") or "")
    if status != "finished":
        return _error(
            "RUN_STATUS_NOT_FINISHED",
            f"当前 run 状态为 {status or 'unknown'}，只有 finished 状态的 run 可以导出 Markdown 报告。",
            suggestion="请先成功运行 Vina，并确认已生成 scores.csv 后再生成分析报告。",
        )

    if not any(isinstance(item, dict) and item.get("run_id") == run_id for item in project.runs):
        return _error(
            "RUN_SUMMARY_NOT_FOUND",
            "project.json 的 runs 数组中没有找到对应 run，无法导出 Markdown 报告。",
            suggestion="请确认该 run 来自当前 DockStart 项目，或重新准备运行记录。",
        )

    ad4zn_integrity: dict[str, Any] | None = None
    if (
        _metadata_scoring_protocol(metadata) == "ad4_maps"
        and _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
    ):
        ad4zn_integrity = _validate_ad4_maps_post_run_integrity(
            project_dir,
            run_id,
            metadata,
        )
        if not ad4zn_integrity.get("ok"):
            return ad4zn_integrity

    receptor_file = _run_input_snapshot_file(metadata, "receptor", project.receptor.file)
    ligand_file = _run_input_snapshot_file(metadata, "ligand", project.ligand.file)
    flex_file = _run_input_snapshot_file(metadata, "flex", "")
    receptor_path, receptor_error = _project_relative_existing_file(
        project_path,
        receptor_file,
        "RECEPTOR_FILE",
        "receptor.pdbqt",
    )
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_relative_existing_file(
        project_path,
        ligand_file,
        "LIGAND_FILE",
        "ligand.pdbqt",
    )
    if ligand_error:
        return ligand_error

    config_file = str(metadata.get("config_snapshot") or metadata.get("config_file") or _config_relative_path(project))
    config_path, config_error = _project_relative_existing_file(project_path, config_file, "VINA_CONFIG", "vina_config.txt")
    if config_error:
        return config_error

    scores_payload = load_scores_csv(project_dir, run_id)
    if not scores_payload.get("ok"):
        return scores_payload

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project.to_dict(),
        "metadata": metadata,
        "run_id": run_id,
        "scores": scores_payload["scores"],
        "scores_file": scores_payload["scores_file"],
        "project_scores_file": scores_payload.get("project_scores_file", _project_scores_file(metadata)),
        "flexible_movement": scores_payload.get("flexible_movement"),
        "flexible_movement_file": scores_payload.get(
            "flexible_movement_file",
            "",
        ),
        "flexible_movement_available": scores_payload.get(
            "flexible_movement_available",
            False,
        ),
        "flexible_movement_legacy_partial": scores_payload.get(
            "flexible_movement_legacy_partial",
            False,
        ),
        "flexible_movement_warning": scores_payload.get(
            "flexible_movement_warning",
            "",
        ),
        "receptor_file": receptor_file,
        "receptor_path": str(receptor_path) if receptor_path else "",
        "ligand_file": ligand_file,
        "ligand_path": str(ligand_path) if ligand_path else "",
        "flex_file": flex_file,
        "config_file": config_file,
        "config_path": str(config_path) if config_path else "",
        "box_snapshot": _run_snapshot_mapping(metadata, "box") or asdict(project.box),
        "vina_snapshot": _run_snapshot_mapping(metadata, "vina") or asdict(project.vina),
        "ad4zn_box_coverage": (
            copy.deepcopy(ad4zn_integrity.get("ad4zn_box_coverage"))
            if isinstance(ad4zn_integrity, dict)
            and isinstance(
                ad4zn_integrity.get("ad4zn_box_coverage"),
                dict,
            )
            else None
        ),
        "error": None,
    }


def _build_vina_evaluation_report(project_dir: str, run_id: str) -> dict[str, Any]:
    evaluation_payload = load_vina_evaluation(project_dir, run_id)
    if not evaluation_payload.get("ok"):
        return evaluation_payload
    metadata = evaluation_payload["metadata"]
    evaluation = evaluation_payload["evaluation"]
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    run_mode = _metadata_run_mode(metadata)
    mode_label = "当前姿势评分" if run_mode == "score_only" else "当前姿势局部优化"
    output_normalization = (
        metadata.get("output_normalization")
        if isinstance(metadata.get("output_normalization"), dict)
        else {}
    )
    command = _command_for_report(metadata)
    command_record: Any = (
        copy.deepcopy(metadata.get("execution_plan"))
        if run_mode == "local_only" and isinstance(metadata.get("execution_plan"), dict)
        else command
    )
    input_sha256 = metadata.get("input_sha256") if isinstance(metadata.get("input_sha256"), dict) else {}
    docking_protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    pose_input_attestation = (
        docking_protocol.get("pose_input_attestation")
        if isinstance(docking_protocol.get("pose_input_attestation"), dict)
        else {}
    )
    flex_input_file = _run_input_snapshot_file(metadata, "flex", "")
    vina_snapshot = _run_snapshot_mapping(metadata, "vina")
    unbound_reference = (
        evaluation.get("unbound_energy")
        if isinstance(evaluation.get("unbound_energy"), dict)
        else {}
    )
    explicit_unbound = (
        run_mode == "score_only"
        and unbound_reference.get("mode") == "explicit"
        and not isinstance(unbound_reference.get("value_kcal_mol"), bool)
        and isinstance(unbound_reference.get("value_kcal_mol"), (int, float))
        and math.isfinite(float(unbound_reference.get("value_kcal_mol")))
    )
    unbound_reference_label = (
        f"{_format_config_number(float(unbound_reference['value_kcal_mol']))} kcal/mol（显式）"
        if explicit_unbound
        else "未显式指定（Vina 默认处理）"
        if run_mode == "score_only"
        else "不适用"
    )
    optimized_terms = {
        str(item.get("key") or ""): item
        for item in evaluation.get("energy_terms", [])
        if isinstance(item, dict) and item.get("key")
    }
    input_terms = {
        str(item.get("key") or ""): item
        for item in evaluation.get("input_energy_terms", [])
        if isinstance(item, dict) and item.get("key")
    }
    if run_mode == "local_only" and input_terms:
        ordered_energy_keys = list(optimized_terms)
        ordered_energy_keys.extend(key for key in input_terms if key not in optimized_terms)
        energy_rows = []
        for key in ordered_energy_keys:
            input_item = input_terms.get(key) or {}
            optimized_item = optimized_terms.get(key) or {}
            input_value = input_item.get("value_kcal_mol")
            optimized_value = optimized_item.get("value_kcal_mol")
            delta = (
                float(optimized_value) - float(input_value)
                if isinstance(input_value, (int, float))
                and isinstance(optimized_value, (int, float))
                else None
            )
            energy_rows.append(
                [
                    optimized_item.get("label") or input_item.get("label") or key,
                    input_value,
                    optimized_value,
                    delta,
                ]
            )
    else:
        energy_rows = [
            [
                item.get("label"),
                item.get("value_kcal_mol"),
                item.get("term_number") if item.get("term_number") is not None else "—",
            ]
            for item in evaluation.get("energy_terms", [])
            if isinstance(item, dict)
        ]
    file_rows = [
        ["受体快照", Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()],
        *(
            [["柔性侧链快照", flex_input_file]]
            if flex_input_file
            else []
        ),
        ["输入配体姿势", evaluation.get("input_pose_file")],
        *(
            [["局部优化后姿势", evaluation.get("output_pose_file")]]
            if run_mode == "local_only"
            else []
        ),
        *(
            [
                [
                    "Vina 原始输出（字节证据）",
                    output_normalization.get("raw_output_file"),
                ]
            ]
            if output_normalization.get("raw_output_file")
            else []
        ),
        ["配置快照", metadata.get("config_snapshot")],
        *(
            [["输入评分日志", (evaluation.get("comparison") or {}).get("baseline_log_file")]]
            if run_mode == "local_only"
            and isinstance(evaluation.get("comparison"), dict)
            and (evaluation.get("comparison") or {}).get("baseline_log_file")
            else []
        ),
        ["运行日志", metadata.get("log_file")],
        ["评价结果", evaluation_payload.get("evaluation_file")],
    ]
    grid = evaluation.get("grid") if isinstance(evaluation.get("grid"), dict) else {}
    grid_rows = [
        [
            "范围来源",
            "按输入配体自动建立（autobox）"
            if evaluation.get("autobox")
            else "项目 Box / 预计算 maps",
            "",
        ],
        ["中心记录", grid.get("center"), ""],
        ["尺寸记录", grid.get("size"), ""],
        ["网格间距", grid.get("spacing_angstrom"), "Å"],
    ]
    reproducibility_rows = [
        ["DockStart version", metadata.get("app_version")],
        ["Vina version", metadata.get("vina_version")],
        ["Vina binary SHA256", metadata.get("vina_sha256")],
        ["receptor SHA256", input_sha256.get("receptor")],
        *(
            [["flex SHA256", input_sha256.get("flex")]]
            if flex_input_file or input_sha256.get("flex")
            else []
        ),
        ["ligand SHA256", input_sha256.get("ligand")],
        ["config SHA256", input_sha256.get("config")],
        *(
            [
                [
                    "Vina 输出标准化",
                    (
                        f"{output_normalization.get('status') or '未记录'}；"
                        f"方法={output_normalization.get('method') or '未记录'}；"
                        f"移除 NUL={output_normalization.get('nul_bytes_removed') or 0}"
                    ),
                ],
                [
                    "Vina 原始输出 SHA256",
                    output_normalization.get("source_sha256"),
                ],
                [
                    "标准 PDBQT SHA256",
                    output_normalization.get("normalized_sha256"),
                ],
            ]
            if output_normalization
            else []
        ),
        [
            "姿势坐标系确认",
            (
                "用户已确认；DockStart 仅校验记录与输入快照哈希，不自动验证科学有效性"
                if pose_input_attestation
                else "历史运行未记录"
            ),
        ],
        ["确认时间", pose_input_attestation.get("confirmed_at")],
        ["确认声明", pose_input_attestation.get("claim")],
        ["确认 receptor SHA256", pose_input_attestation.get("receptor_sha256")],
        *(
            [["确认 flex SHA256", pose_input_attestation.get("flex_sha256")]]
            if pose_input_attestation.get("flex_sha256")
            else []
        ),
        ["确认 ligand SHA256", pose_input_attestation.get("ligand_sha256")],
        [
            "spacing",
            (
                "由预计算 maps 固定"
                if _metadata_scoring_protocol(metadata) == "ad4_maps"
                else vina_snapshot.get("spacing", 0.375)
            ),
        ],
        [
            "no_refine",
            (
                "不适用"
                if _metadata_scoring_protocol(metadata) == "ad4_maps"
                else "开启" if vina_snapshot.get("no_refine", False) else "关闭"
            ),
        ],
        [
            "force_even_voxels",
            (
                "不适用"
                if _metadata_scoring_protocol(metadata) == "ad4_maps"
                else "开启" if vina_snapshot.get("force_even_voxels", False) else "关闭"
            ),
        ],
        ["unbound_energy", unbound_reference_label],
        ["verbosity", vina_snapshot.get("verbosity", 1)],
        ["cpu", vina_snapshot.get("cpu", 0)],
        ["started_at", metadata.get("started_at")],
        ["finished_at", metadata.get("finished_at")],
        ["exit_code", metadata.get("exit_code")],
    ]
    scientific_lines = (
        [
            "- 本次只计算输入姿势的能量分解；",
            "- 未执行全局构象搜索；",
            "- 未生成新的配体构象文件；",
            "- 该分值不能作为一组候选构象中的“最佳结果”。",
            *(
                [
                    "- 本次显式指定未结合态参考能量；"
                    "只与输入、结构准备、评分函数和参考能量相同的运行比较。"
                ]
                if explicit_unbound
                else []
            ),
        ]
        if run_mode == "score_only"
        else [
            "- 本次只在输入姿势附近执行局部优化；",
            "- 已保存局部优化后的单个 PDBQT；",
            "- 未执行全局构象搜索，不能据此排除其他结合姿势；",
            "- 该结果不能作为全局对接排名。",
        ]
    )
    scientific_lines.append(
        "- 输入姿势坐标关系由用户确认；DockStart 记录确认并绑定文件哈希，但不会自动证明受体与配体属于科学上有效的同一坐标系。"
    )
    report_lines = [
            f"# DockStart {mode_label}报告",
            "",
            "## 1. 项目与任务",
            "",
            f"- 项目名称: {_markdown_cell(project.project_name)}",
            f"- run_id: {_markdown_cell(run_id)}",
            f"- 任务类型: {_markdown_cell(run_mode)}",
            f"- 评分函数: {_markdown_cell(evaluation.get('scoring_function'))}",
            f"- 主要评分: {_markdown_cell(evaluation.get('primary_score_kcal_mol'))} kcal/mol",
            f"- 未结合态参考能量: {_markdown_cell(unbound_reference_label)}",
            "",
            "## 2. 输入与输出文件",
            "",
            _markdown_table(["项目", "路径"], file_rows),
            "",
            "## 3. 评价范围",
            "",
            _markdown_table(["项目", "记录值", "单位"], grid_rows),
            "",
            "## 4. 能量分解",
            "",
    ]
    if run_mode == "local_only":
        comparison = (
            evaluation.get("comparison")
            if isinstance(evaluation.get("comparison"), dict)
            else {}
        )
        geometry = (
            comparison.get("geometry")
            if isinstance(comparison.get("geometry"), dict)
            else {}
        )
        comparison_rows = [
            ["输入姿势评分", comparison.get("input_score_kcal_mol"), "kcal/mol"],
            ["优化后评分", comparison.get("optimized_score_kcal_mol"), "kcal/mol"],
            [
                "评分变化（优化后－输入）",
                comparison.get("delta_score_kcal_mol"),
                "kcal/mol",
            ],
            [
                "可比较",
                "是" if comparison.get("comparable") else "否",
                comparison.get("reason") or "",
            ],
        ]
        geometry_rows = (
            [
                ["原子映射", geometry.get("mapping_method"), ""],
                ["匹配重原子", geometry.get("heavy_atom_count"), "个"],
                [
                    "未对齐重原子 RMSD",
                    geometry.get("heavy_atom_rmsd_no_alignment_angstrom"),
                    "Å",
                ],
                [
                    "平均重原子位移",
                    geometry.get("mean_heavy_atom_displacement_angstrom"),
                    "Å",
                ],
                [
                    "最大重原子位移",
                    geometry.get("max_heavy_atom_displacement_angstrom"),
                    "Å",
                ],
                [
                    "几何质心位移",
                    geometry.get("centroid_displacement_angstrom"),
                    "Å",
                ],
            ]
            if geometry.get("ok")
            else [
                [
                    "几何比较",
                    "不可用",
                    str((geometry.get("error") or {}).get("message") or "未记录"),
                ]
            ]
        )
        stage_labels = {
            "input_score": "输入姿势评分",
            "local_optimization": "局部优化",
        }
        stage_rows = [
            [
                stage_labels.get(str(stage.get("id") or ""), stage.get("id")),
                stage.get("status"),
                stage.get("started_at"),
                stage.get("finished_at"),
                stage.get("duration_seconds"),
                stage.get("exit_code"),
                stage.get("log_file"),
            ]
            for stage in evaluation.get("stages", [])
            if isinstance(stage, dict)
        ]
        report_lines.extend(
            [
                _markdown_table(["能量项", "输入", "优化后", "Δ（优化后－输入）"], energy_rows)
                if input_terms
                else "未记录输入姿势能量分解；仅保留优化后能量项。",
                "",
                "## 5. 优化前后评分比较",
                "",
                _markdown_table(["项目", "记录值", "单位 / 说明"], comparison_rows),
                "",
                "负的评分变化只表示在本次 Vina 评分协议下数值降低，不等同于真实结合自由能改善。",
                "",
                "## 6. 姿势位移",
                "",
                _markdown_table(["项目", "记录值", "单位 / 说明"], geometry_rows),
                "",
                str(geometry.get("scientific_note") or ""),
                "",
                "## 7. 分阶段运行记录",
                "",
                (
                    _markdown_table(
                        ["阶段", "状态", "开始", "结束", "耗时 (s)", "退出码", "日志"],
                        stage_rows,
                    )
                    if stage_rows
                    else "该历史运行未记录分阶段执行信息。"
                ),
                "",
                "## 8. 执行计划",
                "",
                "```json",
                json.dumps(command_record, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 9. 可复现记录",
                "",
                _markdown_table(["项目", "记录值"], reproducibility_rows),
                "",
                "## 10. 科学边界",
                "",
                *scientific_lines,
                "",
                DOCKING_SCORE_DISCLAIMER,
                "",
            ]
        )
    else:
        report_lines.extend(
            [
                _markdown_table(["能量项", "数值 (kcal/mol)", "Vina 项号"], energy_rows),
                "",
                "## 5. 执行命令",
                "",
                "```json",
                json.dumps(command_record, ensure_ascii=False, indent=2),
                "```",
                "",
                "## 6. 可复现记录",
                "",
                _markdown_table(["项目", "记录值"], reproducibility_rows),
                "",
                "## 7. 科学边界",
                "",
                *scientific_lines,
                "",
                DOCKING_SCORE_DISCLAIMER,
                "",
            ]
        )
    report_text = "\n".join(report_lines)
    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": project.to_dict(),
        "run_id": run_id,
        "metadata": metadata,
        "evaluation": evaluation,
        "evaluation_file": evaluation_payload.get("evaluation_file"),
        "report_text": report_text,
        "message": f"{mode_label}报告内容已生成。",
        "error": None,
    }


def build_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None
    protocol_id = _metadata_protocol_id(metadata)
    if protocol_id == MULTIPLE_LIGAND_PROTOCOL_ID:
        from dockstart_core.multiple_ligands import (  # noqa: PLC0415
            build_multiple_ligand_markdown_report,
        )

        return build_multiple_ligand_markdown_report(project_dir, run_id)
    if protocol_id == HYDRATED_PROTOCOL_ID:
        from dockstart_core.hydrated_run import (  # noqa: PLC0415
            build_hydrated_markdown_report,
        )

        return build_hydrated_markdown_report(project_dir, run_id)
    if _metadata_run_mode(metadata) != "dock":
        return _build_vina_evaluation_report(project_dir, run_id)

    context = _load_report_context(project_dir, run_id)
    if not context.get("ok"):
        return context

    project = _project_from_dict(context["project"], Path(project_dir).expanduser())
    metadata = context["metadata"]
    scores = context["scores"]
    scoring_protocol = _metadata_scoring_protocol(metadata)
    is_ad4_maps = scoring_protocol == "ad4_maps"
    is_ad4zn = (
        is_ad4_maps
        and _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
    )
    is_vina_maps = _metadata_grid_source(metadata) == "precomputed_maps"
    ad4_maps = metadata.get("ad4_maps") if isinstance(metadata.get("ad4_maps"), dict) else {}
    ad4zn = (
        metadata.get("ad4zn")
        if isinstance(metadata.get("ad4zn"), dict)
        else {}
    )
    ad4zn_box_coverage = (
        context.get("ad4zn_box_coverage")
        if isinstance(context.get("ad4zn_box_coverage"), dict)
        else {}
    )
    vina_maps = metadata.get("vina_maps") if isinstance(metadata.get("vina_maps"), dict) else {}
    command = _command_for_report(metadata)
    command_text = json.dumps(command, ensure_ascii=False, indent=2)
    vina_path = command[0] if command else str(metadata.get("vina_path") or "")
    output_normalization = (
        metadata.get("output_normalization")
        if isinstance(metadata.get("output_normalization"), dict)
        else {}
    )
    raw_vina_output_file = str(
        output_normalization.get("raw_output_file") or ""
    )
    flexible_movement = (
        context.get("flexible_movement")
        if isinstance(context.get("flexible_movement"), dict)
        else None
    )
    flexible_movement_file = str(
        context.get("flexible_movement_file") or ""
    )

    input_rows = [
        ["receptor 文件", context["receptor_file"]],
        ["ligand 文件", context["ligand_file"]],
        *([["flex 侧链文件", context["flex_file"]]] if context.get("flex_file") else []),
        ["vina_config.txt", context["config_file"]],
        ["log.txt", str(metadata.get("log_file") or Path("runs", run_id, "log.txt").as_posix())],
        ["out.pdbqt", str(metadata.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())],
        *(
            [["柔性运动分析", flexible_movement_file]]
            if flexible_movement_file
            else []
        ),
        *(
            [["Vina 原始输出（字节证据）", raw_vina_output_file]]
            if raw_vina_output_file
            else []
        ),
        ["stdout.txt", str(metadata.get("stdout_file") or Path("runs", run_id, "stdout.txt").as_posix())],
        ["stderr.txt", str(metadata.get("stderr_file") or Path("runs", run_id, "stderr.txt").as_posix())],
        *(
            [
                [
                    "AutoDock4Zn beta maps manifest"
                    if is_ad4zn
                    else "AutoDock4 maps manifest",
                    str(ad4_maps.get("manifest_snapshot") or ""),
                ],
                [
                    "AutoDock4Zn beta maps prefix"
                    if is_ad4zn
                    else "AutoDock4 maps prefix",
                    str(ad4_maps.get("prefix") or ""),
                ],
            ]
            if is_ad4_maps
            else []
        ),
        *(
            [
                ["Vina/Vinardo maps manifest", str(vina_maps.get("manifest_snapshot") or "")],
                ["Vina/Vinardo maps prefix", str(vina_maps.get("prefix") or "")],
            ]
            if is_vina_maps
            else []
        ),
    ]
    box_snapshot = {**asdict(project.box), **context["box_snapshot"]}
    recorded_vina_snapshot = _run_snapshot_mapping(metadata, "vina")
    vina_snapshot = {**asdict(project.vina), **context["vina_snapshot"]}
    box_rows = [
        ["center_x", box_snapshot["center_x"], "Å"],
        ["center_y", box_snapshot["center_y"], "Å"],
        ["center_z", box_snapshot["center_z"], "Å"],
        ["size_x", box_snapshot["size_x"], "Å"],
        ["size_y", box_snapshot["size_y"], "Å"],
        ["size_z", box_snapshot["size_z"], "Å"],
    ]
    vina_rows = [
        ["scoring", vina_snapshot.get("scoring") or "vina"],
        ["exhaustiveness", vina_snapshot["exhaustiveness"]],
        ["max_evals", vina_snapshot.get("max_evals", 0)],
        ["num_modes", vina_snapshot["num_modes"]],
        ["min_rmsd", vina_snapshot.get("min_rmsd", 1)],
        ["energy_range", vina_snapshot["energy_range"]],
        [
            "spacing",
            (
                "由预计算 maps 固定"
                if is_ad4_maps or is_vina_maps
                else vina_snapshot.get("spacing", 0.375)
            ),
        ],
        [
            "no_refine",
            (
                "grid-only（等价于 no-refine）"
                if is_vina_maps
                else "不适用"
                if is_ad4_maps
                else "开启" if recorded_vina_snapshot.get("no_refine", False) else "关闭"
            ),
        ],
        [
            "force_even_voxels",
            (
                "仅生成 maps 时适用"
                if is_vina_maps
                else "不适用"
                if is_ad4_maps
                else "开启" if recorded_vina_snapshot.get("force_even_voxels", False) else "关闭"
            ),
        ],
        ["unbound_energy", "不适用（仅用于 score_only）"],
        ["verbosity", vina_snapshot.get("verbosity", 1)],
        ["cpu", vina_snapshot["cpu"]],
        ["seed", vina_snapshot["seed"] if vina_snapshot.get("seed") is not None else "未设置"],
    ]
    score_rows = [
        [score["mode"], score["affinity_kcal_mol"], score["rmsd_lb"], score["rmsd_ub"]]
        for score in scores
    ]
    input_sha256 = metadata.get("input_sha256") if isinstance(metadata.get("input_sha256"), dict) else {}
    result_artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    flexible_movement_artifact = (
        result_artifacts.get("flexible_movement")
        if isinstance(result_artifacts.get("flexible_movement"), dict)
        else {}
    )
    vina_tool = metadata.get("vina_tool") if isinstance(metadata.get("vina_tool"), dict) else {}
    system = metadata.get("system") if isinstance(metadata.get("system"), dict) else {}
    app = metadata.get("app") if isinstance(metadata.get("app"), dict) else {}
    ad4zn_hashes = (
        input_sha256.get("ad4zn")
        if isinstance(input_sha256.get("ad4zn"), dict)
        else {}
    )
    ligand_preparation = (
        metadata.get("ligand_preparation")
        if isinstance(metadata.get("ligand_preparation"), dict)
        else {}
    )
    macrocycle_summary = (
        ligand_preparation.get("macrocycle_summary")
        if isinstance(ligand_preparation.get("macrocycle_summary"), dict)
        else {}
    )
    is_macrocycle_preparation = (
        ligand_preparation.get("matched") is True
        and ligand_preparation.get("protocol") == "meeko_macrocycle"
    )
    docking_protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    flexible_identity = (
        docking_protocol.get("identity")
        if isinstance(docking_protocol.get("identity"), dict)
        else {}
    )
    reproducibility_rows = [
        ["DockStart version", metadata.get("app_version") or app.get("version")],
        ["Vina binary SHA256", metadata.get("vina_sha256") or vina_tool.get("sha256")],
        ["receptor SHA256", input_sha256.get("receptor")],
        ["ligand SHA256", input_sha256.get("ligand")],
        *([["flex SHA256", input_sha256.get("flex")]] if input_sha256.get("flex") else []),
        *(
            [
                [
                    "柔性运动分析 SHA256",
                    flexible_movement_artifact.get("sha256"),
                ],
                [
                    "柔性运动分析方法",
                    flexible_movement.get("method")
                    if flexible_movement
                    else "旧版未记录",
                ],
            ]
            if docking_protocol.get("mode") == "flexible"
            else []
        ),
        *(
            [
                ["柔性受体原始结构 SHA256", docking_protocol.get("source_sha256")],
                [
                    "rigid/flex 原子划分 SHA256",
                    (
                        docking_protocol.get("atom_partition")
                        if isinstance(docking_protocol.get("atom_partition"), dict)
                        else {}
                    ).get("partition_sha256"),
                ],
            ]
            if docking_protocol.get("mode") == "flexible"
            else []
        ),
        *(
            [
                [
                    "mmCIF 身份合同 SHA256",
                    flexible_identity.get("identity_contract_sha256"),
                ],
                [
                    "柔性残基选择合同 SHA256",
                    flexible_identity.get("selection_sha256"),
                ],
                ["Gemmi 桥接 PDB SHA256", flexible_identity.get("bridge_sha256")],
                [
                    "Gemmi 桥接验证 SHA256",
                    flexible_identity.get("bridge_verification_sha256"),
                ],
            ]
            if flexible_identity
            else []
        ),
        ["config SHA256", input_sha256.get("config")],
        *(
            [
                [
                    "Vina 输出标准化",
                    (
                        f"{output_normalization.get('status') or '未记录'}；"
                        f"方法={output_normalization.get('method') or '未记录'}；"
                        f"移除 NUL={output_normalization.get('nul_bytes_removed') or 0}"
                    ),
                ],
                [
                    "Vina 原始输出 SHA256",
                    output_normalization.get("source_sha256"),
                ],
                [
                    "标准 PDBQT SHA256",
                    output_normalization.get("normalized_sha256"),
                ],
            ]
            if output_normalization
            else []
        ),
        *(
            [["maps manifest SHA256", input_sha256.get("maps_manifest")]]
            if is_ad4_maps or is_vina_maps
            else []
        ),
        *(
            [
                ["AD4Zn 原始受体 SHA256", ad4zn_hashes.get("original_receptor")],
                ["AD4Zn.dat SHA256", ad4zn_hashes.get("parameter_file")],
                ["AD4Zn GPF SHA256", ad4zn_hashes.get("gpf")],
                ["AD4Zn 协议记录 SHA256", ad4zn_hashes.get("protocol_record")],
            ]
            if is_ad4zn
            else []
        ),
        *(
            [
                ["大环合同 SHA256", macrocycle_summary.get("contract_sha256") or "旧版未记录"],
                ["大环 worker 证据 SHA256", macrocycle_summary.get("evidence_sha256") or "旧版未记录"],
                ["大环冻结输入 SHA256", macrocycle_summary.get("frozen_input_sha256") or "旧版未记录"],
                ["大环键拓扑 SHA256", macrocycle_summary.get("bond_topology_sha256") or "旧版未记录"],
            ]
            if is_macrocycle_preparation
            else []
        ),
        ["system fingerprint", system.get("fingerprint")],
    ]
    flexible_residue_details: list[str] = []
    for raw_residue in docking_protocol.get("selected_residues", []):
        if not isinstance(raw_residue, dict):
            flexible_residue_details.append(str(raw_residue))
            continue
        selector = str(raw_residue.get("selector") or "")
        author = (
            raw_residue.get("author")
            if isinstance(raw_residue.get("author"), dict)
            else {}
        )
        label_identity = (
            raw_residue.get("label")
            if isinstance(raw_residue.get("label"), dict)
            else {}
        )
        selected_altloc = str(raw_residue.get("selected_altloc") or "")
        insertion_code = str(
            author.get("insertion_code")
            or raw_residue.get("insertion_code")
            or ""
        )
        alternate = (
            raw_residue.get("alternate_locations")
            if isinstance(raw_residue.get("alternate_locations"), dict)
            else {}
        )
        occupancy = (
            alternate.get("occupancy")
            if isinstance(alternate.get("occupancy"), dict)
            else {}
        )
        occupancy_fact = (
            occupancy.get(selected_altloc or "shared")
            if isinstance(occupancy.get(selected_altloc or "shared"), dict)
            else {}
        )
        if author and label_identity:
            detail = (
                f"auth {selector} {author.get('component_id') or ''} → "
                f"label {label_identity.get('chain_id') or '?'}:"
                f"{label_identity.get('sequence_id') or '?'} "
                f"{label_identity.get('component_id') or ''}；"
                f"插入码={insertion_code or '无'}"
            ).strip()
            if selected_altloc:
                detail += (
                    f"；altloc={selected_altloc}；occupancy="
                    f"{occupancy_fact.get('minimum', '?')}–"
                    f"{occupancy_fact.get('maximum', '?')}"
                )
            else:
                detail += (
                    "；altloc=无；occupancy="
                    f"{occupancy_fact.get('minimum', '?')}–"
                    f"{occupancy_fact.get('maximum', '?')}"
                )
            flexible_residue_details.append(detail)
        else:
            flexible_residue_details.append(
                f"{selector or raw_residue}；"
                f"插入码={insertion_code or '无'}；"
                f"altloc={selected_altloc or '无'}；occupancy="
                f"{occupancy_fact.get('minimum', '?')}–"
                f"{occupancy_fact.get('maximum', '?')}"
            )

    protocol_rows = [
        [
            "评分协议",
            "AutoDock4Zn beta（TZ + AD4Zn.dat + 预计算 maps）"
            if is_ad4zn
            else "AutoDock4（预计算 maps）"
            if is_ad4_maps
            else "Vina / Vinardo（预计算 maps，grid-only）"
            if is_vina_maps
            else "Vina / Vinardo",
        ],
        ["评分函数", "ad4" if is_ad4_maps else vina_snapshot.get("scoring") or "vina"],
        ["受体模式", "有限柔性侧链" if docking_protocol.get("mode") == "flexible" else "刚性受体"],
        [
            "本次允许运动的对象",
            (
                "配体的平移、转动与可旋转键，以及已选受体侧链；"
                "受体主链和未选受体原子保持刚性"
                if docking_protocol.get("mode") == "flexible"
                else "配体的平移、转动与可旋转键；受体全部原子保持刚性"
            ),
        ],
        [
            "柔性残基",
            "；".join(flexible_residue_details) or "不适用",
        ],
        ["柔性准备记录", docking_protocol.get("preparation_id") or "不适用"],
        *(
            [
                [
                    "mmCIF 模型",
                    (
                        flexible_identity.get("model")
                        if isinstance(flexible_identity.get("model"), dict)
                        else {}
                    ).get("id")
                    or "未记录",
                ],
                [
                    "残基编号体系",
                    "点选/Meeko 使用 auth_asym_id + auth_seq_id + 插入码；"
                    "报告同时冻结 label_asym_id + label_seq_id",
                ],
            ]
            if flexible_identity
            else []
        ),
        ["配体准备协议", ligand_preparation.get("protocol") if ligand_preparation.get("matched") else "外部或未匹配"],
        ["配体准备记录", ligand_preparation.get("prep_id") or "不适用"],
        ["Meeko 版本", ligand_preparation.get("meeko_version") or "未记录"],
        ["RDKit 版本", ligand_preparation.get("rdkit_version") or "未记录"],
    ]
    if is_macrocycle_preparation:
        break_bond_labels = ", ".join(
            str(item.get("display") or "")
            for item in macrocycle_summary.get("break_bonds", [])
            if isinstance(item, dict) and item.get("display")
        )
        formal_reviewed = ligand_preparation.get("formal_reviewed") is True
        selection_mode = str(macrocycle_summary.get("selection_mode") or "")
        protocol_rows.extend(
            [
                [
                    "大环证据等级",
                    "正式审查（完整证据）"
                    if formal_reviewed
                    else "旧版兼容（部分证据，非正式审查）",
                ],
                [
                    "大环选择模式",
                    "人工确认断环候选"
                    if selection_mode == "candidate"
                    else "刚性大环"
                    if selection_mode == "rigid"
                    else f"旧版 {selection_mode or '未记录'}",
                ],
                [
                    "大环 review ID",
                    macrocycle_summary.get("review_id") or "旧版未记录",
                ],
                [
                    "断环候选 ID",
                    macrocycle_summary.get("candidate_id")
                    or (
                        "不适用（刚性大环）"
                        if selection_mode == "rigid"
                        else "旧版未记录"
                    ),
                ],
                [
                    "确认断环键（一基编号）",
                    break_bond_labels
                    or (
                        "不打开环键"
                        if selection_mode == "rigid"
                        else "旧版未冻结精确断环键"
                    ),
                ],
                [
                    "G* 胶合伪原子",
                    macrocycle_summary.get("glue_pseudo_atom_count")
                    if macrocycle_summary.get("glue_pseudo_atom_count") is not None
                    else "旧版未可靠记录",
                ],
                [
                    "显式氢策略",
                    macrocycle_summary.get("hydrogen_policy") or "旧版未记录",
                ],
            ]
        )
    if is_ad4_maps:
        grid = ad4_maps.get("grid") if isinstance(ad4_maps.get("grid"), dict) else {}
        grid_points = grid.get("grid_points") if isinstance(grid.get("grid_points"), dict) else {}
        protocol_rows.extend(
            [
                ["map set", ad4_maps.get("map_set_id") or "未记录"],
                [
                    "网格点数",
                    " × ".join(str(grid_points.get(axis)) for axis in ("x", "y", "z"))
                    if all(grid_points.get(axis) is not None for axis in ("x", "y", "z"))
                    else "未记录",
                ],
                ["网格间距", f"{grid.get('spacing')} Å" if grid.get("spacing") is not None else "未记录"],
                ["配体原子类型", ", ".join(str(value) for value in ad4_maps.get("ligand_atom_types", [])) or "未记录"],
            ]
        )
        if is_ad4zn:
            selected_site = (
                ad4zn.get("selected_site")
                if isinstance(ad4zn.get("selected_site"), dict)
                else {}
            )
            review = (
                ad4zn.get("review")
                if isinstance(ad4zn.get("review"), dict)
                else {}
            )
            prepared_receptor = (
                ad4zn.get("prepared_receptor")
                if isinstance(ad4zn.get("prepared_receptor"), dict)
                else {}
            )
            parameter_file = (
                ad4zn.get("parameter_file")
                if isinstance(ad4zn.get("parameter_file"), dict)
                else {}
            )
            tz_candidate = (
                selected_site.get("tz_candidate")
                if isinstance(selected_site.get("tz_candidate"), dict)
                else {}
            )
            coverage_markers = (
                ad4zn_box_coverage.get("markers")
                if isinstance(ad4zn_box_coverage.get("markers"), dict)
                else {}
            )
            coverage_zn = (
                coverage_markers.get("ZN")
                if isinstance(coverage_markers.get("ZN"), dict)
                else {}
            )
            coverage_tz = (
                coverage_markers.get("TZ")
                if isinstance(coverage_markers.get("TZ"), dict)
                else {}
            )
            coordination_atoms = (
                selected_site.get("coordination_atoms")
                if isinstance(
                    selected_site.get("coordination_atoms"),
                    list,
                )
                else selected_site.get("coordination_groups")
                if isinstance(
                    selected_site.get("coordination_groups"),
                    list,
                )
                else selected_site.get("coordination_sites")
                if isinstance(
                    selected_site.get("coordination_sites"),
                    list,
                )
                else []
            )
            zn_coordinates = (
                coverage_zn.get("coordinate_angstrom")
                or selected_site.get("zn_coordinate")
                or (
                    selected_site.get("zn").get("coordinate")
                    if isinstance(selected_site.get("zn"), dict)
                    else None
                )
                or "未记录"
            )
            tz_coordinates = (
                coverage_tz.get("coordinate_angstrom")
                or selected_site.get("tz_coordinates")
                or selected_site.get("tz")
                or tz_candidate.get("coordinate")
                or prepared_receptor.get("tz_coordinates")
                or prepared_receptor.get("tz")
                or "未记录"
            )
            protocol_rows.extend(
                [
                    [
                        "Zn 位点",
                        selected_site.get("site_id")
                        or selected_site.get("id")
                        or selected_site.get("selector")
                        or "未记录",
                    ],
                    ["受体配位点数量", len(coordination_atoms) or "未记录"],
                    [
                        "ZN 坐标",
                        json.dumps(zn_coordinates, ensure_ascii=False)
                        if isinstance(zn_coordinates, (dict, list))
                        else zn_coordinates,
                    ],
                    [
                        "TZ 坐标",
                        json.dumps(tz_coordinates, ensure_ascii=False)
                        if isinstance(tz_coordinates, (dict, list))
                        else tz_coordinates,
                    ],
                    [
                        "请求 Box 边界",
                        json.dumps(
                            ad4zn_box_coverage.get(
                                "requested_box_bounds_angstrom"
                            )
                            or "未记录",
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ],
                    [
                        "实际 AutoGrid 网格边界",
                        json.dumps(
                            ad4zn_box_coverage.get(
                                "effective_grid_bounds_angstrom"
                            )
                            or "未记录",
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ],
                    [
                        "ZN/TZ 位于请求 Box",
                        (
                            "通过"
                            if ad4zn_box_coverage.get(
                                "all_zn_tz_inside_requested_box"
                            )
                            is True
                            else "未通过"
                        ),
                    ],
                    [
                        "ZN/TZ 位于实际网格",
                        (
                            "通过"
                            if ad4zn_box_coverage.get(
                                "all_zn_tz_inside_effective_grid"
                            )
                            is True
                            else "未通过"
                        ),
                    ],
                    [
                        "TZ 受体",
                        prepared_receptor.get("relative_path")
                        or prepared_receptor.get("file")
                        or "未记录",
                    ],
                    [
                        "AD4Zn.dat 本机来源",
                        parameter_file.get("source_path")
                        or parameter_file.get("relative_path")
                        or "用户提供，来源未记录",
                    ],
                    [
                        "AD4Zn.dat 支持配置",
                        parameter_file.get("supported_profile_id")
                        or "未记录",
                    ],
                    [
                        "AD4Zn.dat 上游参考",
                        parameter_file.get("upstream_reference")
                        or "未记录",
                    ],
                    [
                        "与上游参考 SHA256 一致",
                        (
                            "是"
                            if parameter_file.get("matches_reference_sha256")
                            is True
                            else "否或未记录"
                        ),
                    ],
                    [
                        "AD4Zn.dat 许可证",
                        parameter_file.get("license_id")
                        or "GPL-2.0-or-later",
                    ],
                    [
                        "人工复核时间",
                        review.get("confirmed_at")
                        or review.get("reviewed_at")
                        or review.get("saved_at")
                        or "未记录",
                    ],
                    ["协议稳定性", "beta"],
                ]
            )
    elif is_vina_maps:
        grid = vina_maps.get("grid") if isinstance(vina_maps.get("grid"), dict) else {}
        nelements = (
            grid.get("nelements")
            if isinstance(grid.get("nelements"), dict)
            else {}
        )
        semantics = (
            vina_maps.get("semantics")
            if isinstance(vina_maps.get("semantics"), dict)
            else {}
        )
        protocol_rows.extend(
            [
                ["map set", vina_maps.get("map_set_id") or "未记录"],
                ["网格来源", "预计算 affinity maps（未传 --receptor）"],
                [
                    "网格点数",
                    " × ".join(str(nelements.get(axis)) for axis in ("x", "y", "z"))
                    if all(nelements.get(axis) is not None for axis in ("x", "y", "z"))
                    else "未记录",
                ],
                ["网格间距", f"{grid.get('spacing')} Å" if grid.get("spacing") is not None else "未记录"],
                ["map 原子类型", ", ".join(str(value) for value in vina_maps.get("atom_types", [])) or "未记录"],
                [
                    "最终精修语义",
                    "grid-only / no-refine 等价"
                    if semantics.get("grid_only") is True
                    and semantics.get("no_refine_equivalent") is True
                    else "记录不完整",
                ],
            ]
        )
    reference_rmsd = metadata.get("reference_rmsd") if isinstance(metadata.get("reference_rmsd"), dict) else {}
    if reference_rmsd:
        reproducibility_rows.extend(
            [
                ["reference ligand SHA256", reference_rmsd.get("reference_sha256")],
                ["reference ligand file", reference_rmsd.get("reference_file")],
            ]
        )
        reference_rmsd_text = "\n".join(
            [
                "### 共晶参考 RMSD",
                "",
                _markdown_table(
                    ["构象", "重原子 RMSD (Å)", "方法", "参考配体"],
                    [[
                        f"Mode {reference_rmsd.get('mode')}",
                        reference_rmsd.get("rmsd_angstrom"),
                        reference_rmsd.get("method"),
                        reference_rmsd.get("reference_source_name") or reference_rmsd.get("reference_file"),
                    ]],
                ),
                "",
                "该值是对接构象与所选参考配体的重原子、对称性修正 RMSD；不是 Vina 表格中相对 Mode 1 的 RMSD。",
            ]
        )
    else:
        reference_rmsd_text = "### 共晶参考 RMSD\n\n尚未选择共晶参考配体，因此未计算该项。"

    affinities = [float(score["affinity_kcal_mol"]) for score in scores]
    sorted_affinities = sorted(affinities)
    score_count = len(affinities)
    mean_affinity = sum(affinities) / score_count
    median_affinity = (
        sorted_affinities[score_count // 2]
        if score_count % 2
        else (sorted_affinities[score_count // 2 - 1] + sorted_affinities[score_count // 2]) / 2
    )
    score_std = math.sqrt(sum((value - mean_affinity) ** 2 for value in affinities) / score_count)
    best_affinity = affinities[0]
    second_gap = affinities[1] - best_affinity if score_count > 1 else None
    score_summary_rows = [
        ["输出构象数量", score_count, "个"],
        ["最佳评分", best_affinity, "kcal/mol"],
        ["第二名与最佳评分差", second_gap if second_gap is not None else "无第二构象", "kcal/mol" if second_gap is not None else "—"],
        ["评分均值", round(mean_affinity, 4), "kcal/mol"],
        ["评分中位数", round(median_affinity, 4), "kcal/mol"],
        ["评分标准差", round(score_std, 4), "kcal/mol"],
        ["评分跨度", round(max(affinities) - min(affinities), 4), "kcal/mol"],
        ["距最佳评分 1 kcal/mol 内", sum(value <= best_affinity + 1 for value in affinities), "个"],
        ["距最佳评分 2 kcal/mol 内", sum(value <= best_affinity + 2 for value in affinities), "个"],
    ]
    alternate_scores = [score for score in scores if int(score["mode"]) != int(scores[0]["mode"])]
    rmsd_lb_values = [float(score["rmsd_lb"]) for score in alternate_scores]
    rmsd_ub_values = [float(score["rmsd_ub"]) for score in alternate_scores]
    pose_dispersion_rows = [
        ["参考构象", f"Mode {scores[0]['mode']}", "Vina 输出中的最佳预测构象"],
        ["RMSD l.b. ≤ 2 Å", sum(value <= 2 for value in rmsd_lb_values), "只表示相对 Mode 1 的下界"],
        ["RMSD l.b. > 4 Å", sum(value > 4 for value in rmsd_lb_values), "提示输出中存在几何差异较大的构象"],
        ["最大 RMSD l.b.", max(rmsd_lb_values) if rmsd_lb_values else None, "Å"],
        ["最大 RMSD u.b.", max(rmsd_ub_values) if rmsd_ub_values else None, "Å"],
    ]
    flexible_movement_text = ""
    if docking_protocol.get("mode") == "flexible":
        if flexible_movement is not None:
            ligand_movement_rows: list[list[Any]] = []
            sidechain_movement_rows: list[list[Any]] = []
            for mode_item in flexible_movement.get("modes", []):
                if not isinstance(mode_item, dict):
                    continue
                mode_number = mode_item.get("mode")
                ligand_movement = (
                    mode_item.get("ligand")
                    if isinstance(mode_item.get("ligand"), dict)
                    else {}
                )
                ligand_movement_rows.append(
                    [
                        mode_number,
                        ligand_movement.get("heavy_atom_count"),
                        ligand_movement.get(
                            "heavy_atom_rmsd_no_alignment_angstrom"
                        ),
                        ligand_movement.get(
                            "mean_heavy_atom_displacement_angstrom"
                        ),
                        ligand_movement.get(
                            "max_heavy_atom_displacement_angstrom"
                        ),
                        ligand_movement.get(
                            "centroid_displacement_angstrom"
                        ),
                    ]
                )
                for residue_item in mode_item.get(
                    "flexible_residues",
                    [],
                ):
                    if not isinstance(residue_item, dict):
                        continue
                    residue = (
                        residue_item.get("residue")
                        if isinstance(residue_item.get("residue"), dict)
                        else {}
                    )
                    movement_item = (
                        residue_item.get("movement")
                        if isinstance(
                            residue_item.get("movement"),
                            dict,
                        )
                        else {}
                    )
                    residue_label = " ".join(
                        value
                        for value in (
                            str(residue.get("residue_name") or ""),
                            str(residue.get("canonical_id") or ""),
                        )
                        if value
                    )
                    sidechain_movement_rows.append(
                        [
                            mode_number,
                            residue_label,
                            movement_item.get("heavy_atom_count"),
                            movement_item.get(
                                "heavy_atom_rmsd_no_alignment_angstrom"
                            ),
                            movement_item.get(
                                "mean_heavy_atom_displacement_angstrom"
                            ),
                            movement_item.get(
                                "max_heavy_atom_displacement_angstrom"
                            ),
                            movement_item.get(
                                "centroid_displacement_angstrom"
                            ),
                        ]
                    )
            flexible_movement_text = "\n".join(
                [
                    "### 配体运动（与冻结输入分别比较）",
                    "",
                    _markdown_table(
                        [
                            "Mode",
                            "重原子数",
                            "直接 RMSD (Å)",
                            "平均位移 (Å)",
                            "最大位移 (Å)",
                            "质心位移 (Å)",
                        ],
                        ligand_movement_rows,
                    ),
                    "",
                    "### 柔性侧链运动（与各自冻结输入分别比较）",
                    "",
                    _markdown_table(
                        [
                            "Mode",
                            "柔性残基",
                            "侧链重原子数",
                            "直接 RMSD (Å)",
                            "平均位移 (Å)",
                            "最大位移 (Å)",
                            "质心位移 (Å)",
                        ],
                        sidechain_movement_rows,
                    ),
                    "",
                    (
                        "上述两组指标在同一受体坐标系中直接比较，没有做刚体对齐；"
                        "配体指标包含整体平移、旋转和内部构象变化，柔性残基指标单独计算，"
                        "并排除 H/HD/HS、非物理伪原子和 CA 根原子。"
                    ),
                    (
                        "这些“直接 RMSD / 位移”不是 Vina 表格中相对 Mode 1 的 "
                        "RMSD l.b./u.b.，也不是相对共晶配体的恢复 RMSD。"
                    ),
                    (
                        "运动大小与 docking score 只描述本次输入和协议下的计算结果，"
                        "不能单独证明真实结合、构象合理性或药效。"
                    ),
                ]
            )
        else:
            flexible_movement_text = "\n".join(
                [
                    "### 配体与柔性侧链运动",
                    "",
                    str(
                        context.get("flexible_movement_warning")
                        or (
                            "该旧版柔性 run 未保存配体与柔性侧链的分离运动分析，"
                            "本报告只能保留评分和运行证据。"
                        )
                    ),
                ]
            )

    structure_review = build_structure_review(
        project_dir,
        receptor_file=context["receptor_file"],
        ligand_file=context["ligand_file"],
    )
    receptor_facts = structure_review.get("receptor") if isinstance(structure_review.get("receptor"), dict) else {}
    ligand_review = structure_review.get("ligand") if isinstance(structure_review.get("ligand"), dict) else {}
    ligand_facts = ligand_review.get("pdbqt") if isinstance(ligand_review.get("pdbqt"), dict) else {}

    def yes_no_unknown(value: Any) -> str:
        if value is True:
            return "是"
        if value is False:
            return "否"
        return "无法可靠判定"

    structure_fact_rows = [
        ["受体重原子数", receptor_facts.get("heavy_atom_count"), context["receptor_file"]],
        ["受体三维坐标", yes_no_unknown(receptor_facts.get("has_3d_coordinates")), "由坐标记录判断"],
        ["配体连接组分", ligand_facts.get("fragment_count"), "优先读取 PDBQT REMARK SMILES / ROOT"],
        ["配体总形式电荷", ligand_facts.get("formal_charge"), ligand_facts.get("formal_charge_source")],
        ["配体重原子数", ligand_facts.get("heavy_atom_count"), context["ligand_file"]],
        ["配体是否包含盐/多片段", yes_no_unknown(ligand_facts.get("contains_salt")), "仅依据连接组分"],
        ["配体未定义立体信息", yes_no_unknown(ligand_facts.get("undefined_stereochemistry")), "PDBQT 通常不足以可靠判断"],
        ["配体三维坐标", yes_no_unknown(ligand_facts.get("has_3d_coordinates")), "由原子坐标记录判断"],
        ["PDBQT 活动扭转数量", ligand_facts.get("torsdof"), "TORSDOF"],
    ]
    review_rows = [
        [check.get("name"), check.get("status"), check.get("message"), check.get("evidence")]
        for check in structure_review.get("checks", [])
        if isinstance(check, dict)
    ]
    second_gap_text = _format_config_number(second_gap) if second_gap is not None else "无第二构象"
    interpretation_lines = [
        f"- 本次最佳预测为 Mode {scores[0]['mode']}，评分 {_format_config_number(best_affinity)} kcal/mol。",
        (
            f"- 第二名与最佳评分差为 {second_gap_text} kcal/mol；该差值只描述本次输出的内部排序，不代表结合概率或置信度。"
            if second_gap is not None
            else "- 本次只有一个输出构象，没有可计算的第二名评分差。"
        ),
        f"- {sum(value <= best_affinity + 1 for value in affinities)} / {score_count} 个构象位于最佳评分 1 kcal/mol 范围内。",
        "- Vina 表格中的 RMSD l.b./u.b. 是相对最佳预测构象的距离界限，不是相对共晶配体的验证 RMSD。",
        *(
            [
                "- 本次使用 AutoDock4Zn beta：TZ 是几何伪原子，Vina 通过 AD4Zn 专用 maps 以 `--scoring ad4` 运行；该评分不能与标准 AutoDock4、Vina 或 Vinardo 直接横向比较。"
            ]
            if is_ad4zn
            else ["- 本次使用 AutoDock4 maps 评分；该评分不能与 Vina 或 Vinardo 结果直接横向比较。"]
            if is_ad4_maps
            else []
        ),
        *(
            [
                "- 本次使用 Vina/Vinardo 预计算 maps；运行命令未传入 --receptor，最终优化和评分只使用网格，不使用显式受体原子。"
            ]
            if is_vina_maps
            else []
        ),
        *(
            [
                (
                    "- 本次配体使用经过人工确认、合同与 worker 证据交叉校验的"
                    " Meeko 大环准备；确认的断环键和 G* 数量只说明本次 PDBQT"
                    " 的闭环约束实现，不证明所选构象、质子化、电荷或结合模式正确。"
                ),
                (
                    "- 对接后的 PDBQT 不是原始闭环化学拓扑；用于 SDF 或后续分析前，"
                    "仍须使用受支持的 Meeko 拓扑重建流程并人工检查闭环与立体化学。"
                ),
            ]
            if is_macrocycle_preparation
            and ligand_preparation.get("formal_reviewed") is True
            else [
                (
                    "- 本次配体沿用旧版大环 auto/rigid 准备记录；该记录只有部分证据，"
                    "没有正式人工确认合同和精确断环键，不得视为正式大环审查，"
                    "也不得仅凭 G* 伪原子反推原始断环键。"
                )
            ]
            if is_macrocycle_preparation
            else []
        ),
    ]

    report_text = "\n".join(
        [
            "# DockStart Docking Report · 深度结果分析",
            "",
            "## 1. 项目信息",
            "",
            f"- 项目名称: {_markdown_cell(project.project_name)}",
            f"- 项目路径: {_markdown_cell(project.project_dir)}",
            f"- 创建时间: {_markdown_cell(project.created_at)}",
            f"- 更新时间: {_markdown_cell(project.updated_at)}",
            f"- run_id: {_markdown_cell(run_id)}",
            "",
            "## 2. 输入文件",
            "",
            _markdown_table(["项目", "路径"], input_rows),
            "",
            "## 3. Box / Grid 参数",
            "",
            _markdown_table(["参数", "值", "单位"], box_rows),
            "",
            "## 4. 运行参数",
            "",
            _markdown_table(["参数", "值"], vina_rows),
            "",
            "### 受体对接协议",
            "",
            _markdown_table(["项目", "记录值"], protocol_rows),
            "",
            "## 5. 运行信息",
            "",
            f"- Vina 路径: {_markdown_cell(vina_path)}",
            f"- Vina 版本: {_markdown_cell(metadata.get('vina_version'))}",
            f"- started_at: {_markdown_cell(metadata.get('started_at'))}",
            f"- finished_at: {_markdown_cell(metadata.get('finished_at'))}",
            f"- exit_code: {_markdown_cell(metadata.get('exit_code'))}",
            "",
            "命令数组:",
            "",
            "```json",
            command_text,
            "```",
            "",
            "## 6. 可复现记录",
            "",
            _markdown_table(["项目", "记录值"], reproducibility_rows),
            "",
            "## 7. AutoDock4Zn beta Score 结果"
            if is_ad4zn
            else "## 7. AutoDock4 Score 结果"
            if is_ad4_maps
            else "## 7. Docking Score 结果",
            "",
            _markdown_table(["Mode", "Affinity kcal/mol", "RMSD l.b.", "RMSD u.b."], score_rows),
            "",
            "## 8. 评分统计摘要",
            "",
            _markdown_table(["指标", "值", "单位/说明"], score_summary_rows),
            "",
            "### 受控解读",
            "",
            *interpretation_lines,
            "",
            "## 9. 构象离散度",
            "",
            _markdown_table(["指标", "值", "说明"], pose_dispersion_rows),
            "",
            *(
                [flexible_movement_text, ""]
                if flexible_movement_text
                else []
            ),
            "## 10. 输入结构事实",
            "",
            _markdown_table(["项目", "记录值", "依据"], structure_fact_rows),
            "",
            "## 11. 结构审查摘要",
            "",
            _markdown_table(["检查项", "状态", "说明", "证据文件"], review_rows),
            "",
            structure_review.get("disclaimer", ""),
            "",
            "## 12. 参考姿势验证",
            "",
            reference_rmsd_text,
            "",
            "## 13. 重要说明",
            "",
            DOCKING_SCORE_DISCLAIMER,
            "",
            *(
                [
                    "- AutoDock4Zn beta 仅适用于经人工确认的 Zn 位点；TZ 是几何伪原子，未显式描述极化、电荷转移或水介导配位；",
                    "- AutoDock4Zn beta、标准 AutoDock4、Vina 与 Vinardo 使用不同的评分协议，评分不可直接横向比较；",
                ]
                if is_ad4zn
                else [
                    "- AutoDock4 maps、Vina 与 Vinardo 使用不同的评分协议，评分不可直接横向比较；",
                ]
                if is_ad4_maps
                else []
            ),
            *(
                [
                    "- 本次为 grid-only 预计算 maps 运行，等价于 no-refine；受体 PDBQT 仅作为来源与 SHA256 溯源快照，不作为 Vina 命令参数；",
                ]
                if is_vina_maps
                else []
            ),
            "- 本报告不证明真实药效；",
            "- 本报告不包含相互作用分析；",
            "- 本报告不包含分子动力学验证；",
            "- 结果依赖输入结构、box、参数和 Vina 版本。",
            "",
        ],
    )

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": project.to_dict(),
        "run_id": run_id,
        "metadata": metadata,
        "scores": scores,
        "scores_file": context["scores_file"],
        "project_scores_file": context["project_scores_file"],
        "flexible_movement": flexible_movement,
        "flexible_movement_file": flexible_movement_file,
        "report_text": report_text,
        "message": "Markdown 报告内容已生成。",
        "error": None,
    }


def _report_file_statuses(project_dir: str, run_id: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    project_path = Path(project_dir).expanduser().resolve()
    run_mode = _metadata_run_mode(metadata)
    expected_analysis_file = (
        Path("runs", run_id, "scores.csv").as_posix()
        if run_mode == "dock"
        else _evaluation_relative_path(run_id)
    )
    analysis_file = (
        str(metadata.get("scores_file") or expected_analysis_file)
        if run_mode == "dock"
        else str(metadata.get("evaluation_file") or expected_analysis_file)
    )
    report_name = _run_report_filename(metadata)
    expected_report_file = Path(
        "runs",
        run_id,
        report_name,
    ).as_posix()
    expected_project_report_file = _project_report_file(metadata)
    report_file = str(
        metadata.get("report_file") or expected_report_file
    )
    project_report_file = str(
        metadata.get("project_report_file")
        or expected_project_report_file
    )
    statuses = [
        _file_status(
            project_path,
            analysis_file,
            "scores" if run_mode == "dock" else "evaluation",
            "scores.csv" if run_mode == "dock" else "evaluation.json",
        ),
        _file_status(project_path, report_file, "run_report", f"runs/{run_id}/{report_name}"),
        _file_status(project_path, project_report_file, "project_report", Path(project_report_file).name),
    ]
    if (
        _is_flexible_movement_run(metadata)
        and _movement_artifact_required(metadata)
    ):
        statuses.insert(
            1,
            _file_status(
                project_path,
                _flexible_movement_relative_path(run_id),
                "flexible_movement",
                FLEXIBLE_MOVEMENT_FILENAME,
            ),
        )

    integrity_specs = {
        "scores": (
            "scores",
            expected_analysis_file,
            RUN_SCORE_ARTIFACT_KEYS,
            "SCORES_CSV_ARTIFACT",
        ),
        "evaluation": (
            "evaluation",
            expected_analysis_file,
            ("evaluation",),
            "VINA_EVALUATION_ARTIFACT",
        ),
        "flexible_movement": (
            "flexible_movement",
            _flexible_movement_relative_path(run_id),
            ("flexible_movement",),
            "FLEX_MOVEMENT_ARTIFACT",
        ),
        "run_report": (
            "report",
            expected_report_file,
            RUN_REPORT_ARTIFACT_KEYS,
            "RUN_REPORT_ARTIFACT",
        ),
        "project_report": (
            "project_report",
            expected_project_report_file,
            RUN_REPORT_ARTIFACT_KEYS,
            "PROJECT_REPORT_ARTIFACT",
        ),
    }
    for status in statuses:
        spec = integrity_specs.get(str(status.get("key") or ""))
        if spec is None or status.get("status") != "ok":
            continue
        artifact_key, expected_relative, contract_keys, error_stem = spec
        snapshot, integrity_error = _read_verified_recorded_artifact(
            project_path,
            run_id,
            metadata,
            artifact_key=artifact_key,
            expected_relative=expected_relative,
            recorded_relative=str(status.get("path") or ""),
            contract_keys=contract_keys,
            error_stem=error_stem,
            display_name=str(status.get("name") or artifact_key),
        )
        if integrity_error:
            status["status"] = "integrity_error"
            status["message"] = str(
                integrity_error["error"].get("message") or ""
            )
            status["raw_error"] = str(
                integrity_error["error"].get("raw_error") or ""
            )
            status["integrity"] = "failed"
        elif snapshot is not None:
            status["integrity"] = "verified"
            status["sha256"] = hashlib.sha256(snapshot).hexdigest()
        else:
            status["integrity"] = "legacy_unverified"
    return statuses


def get_report_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    files = _report_file_statuses(project_dir, run_id, metadata)
    run_mode = _metadata_run_mode(metadata)
    analysis_status = next((item for item in files if item["key"] in {"scores", "evaluation"}), None)
    movement_status = next(
        (
            item
            for item in files
            if item["key"] == "flexible_movement"
        ),
        None,
    )
    report_files = [item for item in files if item["key"] in {"run_report", "project_report"}]
    reports_ready = all(item["status"] == "ok" for item in report_files)
    can_export = str(metadata.get("status") or "") == "finished" and bool(
        analysis_status and analysis_status["status"] == "ok"
        and (
            movement_status is None
            or movement_status["status"] == "ok"
        )
    )
    report_name = _run_report_filename(metadata)

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": loaded.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "files": files,
        "scores_status": analysis_status,
        "analysis_status": analysis_status,
        "report_status": "exported" if reports_ready else "missing",
        "can_export": can_export,
        "report_file": str(metadata.get("report_file") or Path("runs", run_id, report_name).as_posix()),
        "project_report_file": str(metadata.get("project_report_file") or _project_report_file(metadata)),
        "reported_at": str(metadata.get("reported_at") or ""),
        "message": "报告状态已读取。",
        "error": None,
    }


def export_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    built = build_markdown_report(project_dir, run_id)
    if not built.get("ok"):
        return built

    project_path = Path(project_dir).expanduser()
    run_report_name = _run_report_filename(built["metadata"])
    run_report_file = Path("runs", run_id, run_report_name).as_posix()
    project_report_file = _project_report_file(built["metadata"])
    run_report_path, run_report_error = _project_relative_path_for_run(
        project_path,
        run_report_file,
        "RUN_REPORT",
        f"runs/{run_id}/{run_report_name}",
    )
    if run_report_error:
        return run_report_error
    project_report_path, project_report_error = _project_relative_path_for_run(
        project_path,
        project_report_file,
        "PROJECT_REPORT",
        "reports/docking_report.md",
    )
    if project_report_error:
        return project_report_error
    assert run_report_path is not None
    assert project_report_path is not None

    try:
        for target_path in (project_report_path, run_report_path):
            _atomic_write_text(target_path, built["report_text"])
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "MARKDOWN_REPORT_WRITE_ERROR",
            "写入 Markdown 报告时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目 reports 目录和 run 目录可以写入。",
        )

    reported_at = _now_iso()
    report_artifacts = {
        "report": _hash_snapshot(run_report_path, run_report_file),
        "project_report": _hash_snapshot(project_report_path, project_report_file),
    }
    for artifact_key, artifact in report_artifacts.items():
        if (
            artifact.get("exists") is not True
            or isinstance(artifact.get("size_bytes"), bool)
            or not isinstance(artifact.get("size_bytes"), int)
            or int(artifact.get("size_bytes") or 0) <= 0
            or SHA256_PATTERN.fullmatch(
                str(artifact.get("sha256") or "").lower()
            )
            is None
        ):
            return _error(
                "MARKDOWN_REPORT_WRITE_VERIFY_ERROR",
                "Markdown 报告写入后未通过文件完整性校验。",
                raw_error=f"{artifact_key}={artifact}",
                suggestion="请确认项目目录可写后重新导出报告。",
            )

    def merge_report(current: dict[str, Any]) -> dict[str, Any]:
        current.update(
            {
                "report_file": run_report_file,
                "project_report_file": project_report_file,
                "reported_at": reported_at,
            },
        )
        _with_artifact_hashes(current, report_artifacts)
        return current

    metadata, metadata_error = _update_run_metadata_transaction(project_dir, run_id, merge_report)
    if metadata_error:
        return metadata_error
    assert metadata is not None

    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "report_file": run_report_file,
            "project_report_file": project_report_file,
            "reported_at": reported_at,
        },
    )
    if not project_update.get("ok"):
        return project_update

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project_update.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "report_file": run_report_file,
        "project_report_file": project_report_file,
        "reported_at": reported_at,
        "files": _report_file_statuses(project_dir, run_id, metadata),
        "message": "Markdown 报告已导出。",
        "error": None,
    }


def update_run_metadata(project_dir: str, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return _error("RUN_METADATA_PATCH_INVALID", "metadata patch 必须是 JSON 对象。")

    try:
        def apply_patch(current: dict[str, Any]) -> dict[str, Any]:
            current.update(patch)
            return current

        metadata, error = _update_run_metadata_transaction(project_dir, run_id, apply_patch)
        if error:
            return error
        assert metadata is not None
        return {
            "ok": True,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": _metadata_relative_path(run_id),
            "message": "运行元数据已更新。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_WRITE_ERROR",
            "写入 metadata.json 时发生错误。",
            str(exc),
            "请确认 run 目录可写。",
        )


def update_project_run_summary(project_dir: str, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return _error("RUN_SUMMARY_PATCH_INVALID", "run summary patch 必须是 JSON 对象。")

    try:
        project_root = Path(project_dir).expanduser().resolve()
        with _project_lock(project_root):
            project_json = _project_json_path(project_root)
            if project_json.is_symlink() or project_json.resolve(strict=True) != project_json.absolute():
                return _error(
                    "PROJECT_JSON_PATH_UNSAFE",
                    "project.json 不能是符号链接或重解析到其他位置。",
                    raw_error=str(project_json),
                )
            data, _, _ = _read_and_migrate_project_unlocked(
                project_root,
                persist_migration=True,
            )
            project = _project_from_dict(data, project_root)
            project.project_dir = str(project_root)
            matched = False
            for run_summary in project.runs:
                if isinstance(run_summary, dict) and run_summary.get("run_id") == run_id:
                    run_summary.update(patch)
                    matched = True
                    break

            if not matched:
                return _error(
                    "RUN_SUMMARY_NOT_FOUND",
                    "project.json 的 runs 数组中没有找到对应 run。",
                    suggestion="请回到运行准备页重新创建运行记录。",
                )

            project.updated_at = _now_iso()
            project.revision += 1
            _write_project_json_unlocked(project_root, project)
        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "run_id": run_id,
            "message": "project.json 中的 run 摘要已更新。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_SUMMARY_UPDATE_ERROR",
            "更新 project.json 中的 run 摘要时发生错误。",
            str(exc),
            "请确认 project.json 可以写入。",
        )


def _validate_execute_prerequisites(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    project_path = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(loaded["project"], project_path)
    scoring_protocol = _metadata_scoring_protocol(metadata)
    protocol_id = _metadata_protocol_id(metadata)
    is_ad4zn = (
        scoring_protocol == "ad4_maps"
        and protocol_id == AD4ZN_PROTOCOL_ID
    )
    is_hydrated = (
        scoring_protocol == "ad4_maps"
        and protocol_id == HYDRATED_PROTOCOL_ID
    )
    grid_source = _metadata_grid_source(metadata)
    uses_vina_maps = (
        scoring_protocol == "vina" and grid_source == "precomputed_maps"
    )
    run_mode = _metadata_run_mode(metadata)
    autobox = bool(metadata.get("autobox")) if run_mode != "dock" else False
    if not any(isinstance(item, dict) and item.get("run_id") == run_id for item in project.runs):
        return _error(
            "RUN_SUMMARY_NOT_FOUND",
            "project.json 的 runs 数组中没有找到对应 run。",
            suggestion="请回到运行准备页重新创建运行记录。",
        )

    fixed_relative_paths = {
        "receptor": Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix(),
        "ligand": Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix(),
        "config": Path("runs", run_id, "config_snapshot.txt").as_posix(),
        "log": Path("runs", run_id, "log.txt").as_posix(),
        "stdout": Path("runs", run_id, "stdout.txt").as_posix(),
        "stderr": Path("runs", run_id, "stderr.txt").as_posix(),
    }
    if is_hydrated:
        fixed_relative_paths["hydrated_ligand_manifest"] = Path(
            "runs",
            run_id,
            "inputs",
            "hydrated",
            "ligand_manifest.json",
        ).as_posix()
    composite_local_only = _local_only_execution_plan_enabled(metadata)
    if composite_local_only:
        baseline_files = _local_only_baseline_files(run_id)
        fixed_relative_paths.update(
            {
                "baseline_log": baseline_files["log_file"],
                "baseline_stdout": baseline_files["stdout_file"],
                "baseline_stderr": baseline_files["stderr_file"],
            }
        )
    output_relative = _run_output_file(run_id, run_mode)
    if output_relative:
        fixed_relative_paths["output"] = output_relative
    if str(metadata.get("output_file") or "") != output_relative:
        return _error(
            "RUN_OUTPUT_PATH_MISMATCH",
            "metadata 中的输出路径与运行任务类型不一致，拒绝执行。",
            raw_error=f"run_mode={run_mode}; metadata={metadata.get('output_file')!r}; expected={output_relative!r}",
            suggestion="请重新准备新的 run。",
        )
    protocol = metadata.get("docking_protocol") if isinstance(metadata.get("docking_protocol"), dict) else {}
    if is_ad4zn and run_mode != "dock":
        return _error(
            "RUN_AD4ZN_MODE_INVALID",
            "AutoDock4Zn beta 只能执行全局对接。",
            raw_error=f"run_mode={run_mode}",
            suggestion="请保留该 run 作为审计记录，并重新准备全局对接 run。",
        )
    if is_ad4zn and str(protocol.get("mode") or "rigid") == "flexible":
        return _error(
            "RUN_AD4ZN_FLEX_INVALID",
            "AutoDock4Zn beta 当前仅支持刚性受体。",
            suggestion="请以刚性受体重新准备 AD4Zn run。",
        )
    if is_hydrated and run_mode != "dock":
        return _error(
            "RUN_HYDRATED_MODE_INVALID",
            "实验性水合 AD4 协议只能执行全局对接。",
            raw_error=f"run_mode={run_mode}",
            suggestion="请保留该 run 作为审计记录，并重新准备水合全局对接 run。",
        )
    if is_hydrated and str(protocol.get("mode") or "rigid") == "flexible":
        return _error(
            "RUN_HYDRATED_FLEX_INVALID",
            "实验性水合 AD4 协议当前仅支持刚性受体。",
            suggestion="请以刚性受体重新准备水合对接 run。",
        )
    if uses_vina_maps and run_mode != "dock":
        return _error(
            "RUN_VINA_MAPS_MODE_INVALID",
            "预计算 maps run 只能执行全局对接。",
            raw_error=f"run_mode={run_mode}",
            suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
        )
    if uses_vina_maps and str(protocol.get("mode") or "rigid") == "flexible":
        return _error(
            "RUN_VINA_MAPS_FLEX_INVALID",
            "预计算 maps run 不能包含柔性侧链受体。",
            suggestion="请保留该 run 作为审计记录，并以刚性受体重新准备。",
        )
    if str(protocol.get("mode") or "rigid") == "flexible":
        fixed_relative_paths["flex"] = Path("runs", run_id, "inputs", "flex.pdbqt").as_posix()
        fixed_relative_paths["flexible_protocol"] = Path(
            "runs",
            run_id,
            "inputs",
            "flexible_receptor_protocol.json",
        ).as_posix()
    try:
        run_dir = _safe_run_directory(project_path, run_id)
    except Exception as exc:  # noqa: BLE001 - reject symlinked/reparsed run roots.
        return _error(
            "RUN_PATH_UNSAFE",
            "run 目录不是项目内普通目录，拒绝执行。",
            raw_error=str(exc),
            suggestion="请重新准备新的 run。",
        )
    lexical_paths = {key: project_path / relative for key, relative in fixed_relative_paths.items()}
    fixed_paths: dict[str, Path] = {}
    for key, lexical_path in lexical_paths.items():
        if lexical_path.is_symlink():
            return _error(
                "RUN_PATH_SYMLINK_UNSAFE",
                f"固定运行路径 {key} 不能是符号链接。",
                raw_error=str(lexical_path),
                suggestion="请删除该链接并重新准备新的 run。",
            )
        if key in {
            "output",
            "log",
            "stdout",
            "stderr",
            "baseline_log",
            "baseline_stdout",
            "baseline_stderr",
        } and lexical_path.exists():
            return _error(
                "RUN_OUTPUT_ALREADY_EXISTS",
                f"prepared run 的 {key} 输出路径已存在，拒绝覆盖。",
                raw_error=str(lexical_path),
                suggestion="请保留当前 run 作为审计记录，并重新准备新的 run。",
            )
        try:
            resolved = lexical_path.resolve(strict=False)
            resolved.relative_to(project_path)
        except (OSError, ValueError) as exc:
            return _error(
                "RUN_PATH_OUTSIDE_PROJECT",
                f"固定运行路径 {key} 越出项目目录，拒绝执行。",
                raw_error=f"{lexical_path}: {exc}",
            )
        expected_parent = (
            run_dir / "inputs" / "hydrated"
            if key == "hydrated_ligand_manifest"
            else run_dir / "inputs"
            if key in {
                "receptor",
                "ligand",
                "flex",
                "flexible_protocol",
            }
            else run_dir
        )
        if resolved.parent != expected_parent or resolved != lexical_path.absolute():
            return _error(
                "RUN_PATH_REPARSE_UNSAFE",
                f"固定运行路径 {key} 被重解析到本次 run 之外，拒绝执行。",
                raw_error=f"lexical={lexical_path}; resolved={resolved}",
                suggestion="请重新准备新的 run，且不要用链接替换运行文件。",
            )
        fixed_paths[key] = resolved

    required_inputs = ["receptor", "ligand", "config"]
    if "flex" in fixed_paths:
        required_inputs.append("flex")
        required_inputs.append("flexible_protocol")
    if is_hydrated:
        required_inputs.append("hydrated_ligand_manifest")
    for key in required_inputs:
        path = fixed_paths[key]
        if not path.is_file() or path.stat().st_size <= 0:
            return _error(
                f"RUN_{key.upper()}_SNAPSHOT_MISSING",
                f"本次 run 的 {key} 快照缺失或为空，拒绝执行。",
                raw_error=str(path),
                suggestion="请重新准备新的 run；DockStart 不会回退到可变的项目输入。",
            )

    snapshots = metadata.get("snapshots") if isinstance(metadata.get("snapshots"), dict) else {}
    inputs = snapshots.get("inputs") if isinstance(snapshots.get("inputs"), dict) else {}
    config_snapshot = snapshots.get("config") if isinstance(snapshots.get("config"), dict) else {}
    hydrated_snapshot = (
        snapshots.get("hydrated")
        if isinstance(snapshots.get("hydrated"), dict)
        else {}
    )
    expected_hashes = {
        "receptor": str((inputs.get("receptor") or {}).get("sha256") or "") if isinstance(inputs.get("receptor"), dict) else "",
        "ligand": str((inputs.get("ligand") or {}).get("sha256") or "") if isinstance(inputs.get("ligand"), dict) else "",
        "config": str(config_snapshot.get("sha256") or ""),
    }
    if is_hydrated:
        ligand_manifest_record = (
            hydrated_snapshot.get("ligand_manifest")
            if isinstance(hydrated_snapshot.get("ligand_manifest"), dict)
            else {}
        )
        expected_hashes["hydrated_ligand_manifest"] = str(
            ligand_manifest_record.get("sha256") or ""
        )
    if "flex" in fixed_paths:
        expected_hashes["flex"] = (
            str((inputs.get("flex") or {}).get("sha256") or "")
            if isinstance(inputs.get("flex"), dict)
            else ""
        )
        flexible_protocol_snapshot = (
            snapshots.get("flexible_receptor_protocol")
            if isinstance(
                snapshots.get("flexible_receptor_protocol"),
                dict,
            )
            else {}
        )
        expected_hashes["flexible_protocol"] = str(
            flexible_protocol_snapshot.get("sha256") or ""
        )
    for key, expected in expected_hashes.items():
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return _error(
                "RUN_SNAPSHOT_HASH_MISSING",
                f"metadata 中缺少可信的 {key} 快照 SHA256，拒绝执行。",
                suggestion="请重新准备新的 run。",
            )
        actual = _sha256_file(fixed_paths[key])
        if actual.lower() != expected.lower():
            return _error(
                "RUN_SNAPSHOT_HASH_MISMATCH",
                f"{key} 快照在准备后发生变化，拒绝执行。",
                raw_error=f"expected={expected}; actual={actual}; path={fixed_paths[key]}",
                suggestion="请重新准备 run，或恢复未被修改的快照。",
            )

    if "flexible_protocol" in fixed_paths:
        try:
            frozen_flexible_protocol = json.loads(
                fixed_paths["flexible_protocol"].read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _error(
                "RUN_FLEXIBLE_PROTOCOL_SNAPSHOT_INVALID",
                "柔性受体协议快照不是可验证的 JSON 对象。",
                raw_error=str(exc),
                suggestion="请保留该 run 用于审计，并重新准备新的 run。",
            )
        if not isinstance(frozen_flexible_protocol, dict):
            return _error(
                "RUN_FLEXIBLE_PROTOCOL_SNAPSHOT_INVALID",
                "柔性受体协议快照不是 JSON 对象。",
                suggestion="请重新准备新的 run。",
            )
        flexible_protocol_schema = frozen_flexible_protocol.get(
            "schema_version"
        )
        if flexible_protocol_schema not in {1, 2}:
            return _error(
                "RUN_FLEXIBLE_PROTOCOL_SNAPSHOT_INVALID",
                "柔性受体协议快照使用了未知 schema_version。",
                raw_error=str(flexible_protocol_schema),
                suggestion="请保留该 run 用于审计，并重新准备新的 run。",
            )
        expected_flexible_protocol = {
            "schema_version": flexible_protocol_schema,
            "protocol_id": FLEXIBLE_RECEPTOR_PROTOCOL_ID,
            "mode": "flexible",
            "preparation_id": str(protocol.get("preparation_id") or ""),
            "source_raw_file": str(protocol.get("source_raw_file") or ""),
            "source_format": str(protocol.get("source_format") or ""),
            "source_sha256": str(protocol.get("source_sha256") or ""),
            "selected_residues": copy.deepcopy(
                protocol.get("selected_residues") or []
            ),
            "resolved_altlocs": copy.deepcopy(
                protocol.get("resolved_altlocs") or {}
            ),
            "receptor_controls": copy.deepcopy(
                protocol.get("receptor_controls") or {}
            ),
            "receptor_controls_sha256": str(
                protocol.get("receptor_controls_sha256") or ""
            ),
            "receptor_controls_fingerprint": copy.deepcopy(
                protocol.get("receptor_controls_fingerprint") or {}
            ),
            "atom_partition": copy.deepcopy(
                protocol.get("atom_partition") or {}
            ),
            "identity": copy.deepcopy(protocol.get("identity") or {}),
            "prepared_output_sha256": copy.deepcopy(
                protocol.get("sha256") or {}
            ),
            **(
                {
                    "analysis_contracts": {
                        "flexible_movement": {
                            "schema_id": FLEXIBLE_MOVEMENT_SCHEMA_ID,
                            "method": FLEXIBLE_MOVEMENT_METHOD,
                            "artifact_file": (
                                _flexible_movement_relative_path(run_id)
                            ),
                            "required_after_analysis": True,
                            "exclude_flexible_ca_root": True,
                        },
                    },
                }
                if flexible_protocol_schema == 2
                and run_mode == "dock"
                else {}
            ),
        }
        if frozen_flexible_protocol != expected_flexible_protocol:
            return _error(
                "RUN_FLEXIBLE_PROTOCOL_SNAPSHOT_MISMATCH",
                "柔性受体协议快照与 metadata 冻结记录不一致。",
                suggestion="请保留该 run 用于审计，并重新准备新的 run。",
            )
        if flexible_protocol_schema == 2 and run_mode == "dock":
            expected_analysis_contract = expected_flexible_protocol[
                "analysis_contracts"
            ]
            if metadata.get("analysis_contracts") != (
                expected_analysis_contract
            ):
                return _error(
                    "RUN_FLEXIBLE_ANALYSIS_CONTRACT_MISMATCH",
                    "metadata 中的运动分析合同与冻结柔性受体协议不一致。",
                    suggestion="请保留该 run 用于审计，并重新准备新的 run。",
                )

    pose_attestation_error = _validate_frozen_pose_input_attestation(
        protocol,
        run_mode,
        expected_hashes,
    )
    if pose_attestation_error:
        return pose_attestation_error

    maps_prefix = ""
    maps_scoring = str(metadata.get("scoring_function") or "vina")
    maps_vina_sha256 = ""
    if scoring_protocol == "ad4_maps" or uses_vina_maps:
        maps_key = "ad4_maps" if scoring_protocol == "ad4_maps" else "vina_maps"
        map_label = (
            "AutoDock4Zn beta"
            if is_ad4zn
            else "实验性水合 AD4"
            if is_hydrated
            else "AutoDock4"
            if scoring_protocol == "ad4_maps"
            else "Vina/Vinardo"
        )
        maps_snapshot = snapshots.get(maps_key) if isinstance(snapshots.get(maps_key), dict) else {}
        maps_metadata = metadata.get(maps_key) if isinstance(metadata.get(maps_key), dict) else {}
        maps_files = maps_snapshot.get("files") if isinstance(maps_snapshot.get("files"), list) else []
        maps_prefix = str(maps_snapshot.get("prefix") or maps_metadata.get("prefix") or "")
        maps_scoring = (
            "ad4"
            if scoring_protocol == "ad4_maps"
            else str(maps_metadata.get("scoring_function") or maps_scoring)
        )
        expected_maps_dir = run_dir / "inputs" / "maps"
        prefix_name = Path(maps_prefix).name
        expected_prefix = Path("runs", run_id, "inputs", "maps", prefix_name).as_posix() if prefix_name else ""
        if not maps_files or maps_prefix != expected_prefix:
            return _error(
                "RUN_AD4_MAPS_SNAPSHOT_INVALID"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MAPS_SNAPSHOT_INVALID",
                f"{map_label} maps 快照缺少可信的文件清单或 prefix，拒绝执行。",
                suggestion=f"请重新准备新的 {map_label} maps run。",
            )

        observed_names: set[str] = set()
        observed_hashes: dict[str, str] = {}
        for item in maps_files:
            if not isinstance(item, dict):
                return _error(
                    "RUN_AD4_MAP_RECORD_INVALID"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_RECORD_INVALID",
                    f"{map_label} map 文件记录格式无效，拒绝执行。",
                )
            name = str(item.get("name") or "")
            relative_path = str(item.get("relative_path") or "")
            expected_relative = Path("runs", run_id, "inputs", "maps", name).as_posix()
            expected_hash = str(item.get("sha256") or "")
            if (
                not name
                or Path(name).name != name
                or relative_path != expected_relative
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash)
            ):
                return _error(
                    "RUN_AD4_MAP_RECORD_INVALID"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_RECORD_INVALID",
                    f"{map_label} map 文件记录不完整或路径不可信，拒绝执行。",
                    raw_error=json.dumps(item, ensure_ascii=False),
                    suggestion=f"请重新准备新的 {map_label} maps run。",
                )
            lexical_path = project_path / relative_path
            if lexical_path.is_symlink():
                return _error(
                    "RUN_AD4_MAP_SYMLINK_UNSAFE"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_SYMLINK_UNSAFE",
                    f"{map_label} map 快照不能是符号链接，拒绝执行。",
                    raw_error=str(lexical_path),
                )
            try:
                resolved = lexical_path.resolve(strict=True)
            except OSError as exc:
                return _error(
                    "RUN_AD4_MAP_SNAPSHOT_MISSING"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_SNAPSHOT_MISSING",
                    f"{map_label} map 快照缺失，拒绝执行。",
                    raw_error=f"{lexical_path}: {exc}",
                    suggestion=f"请重新准备新的 {map_label} maps run。",
                )
            if resolved.parent != expected_maps_dir or resolved != lexical_path.absolute():
                return _error(
                    "RUN_AD4_MAP_REPARSE_UNSAFE"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_REPARSE_UNSAFE",
                    f"{map_label} map 快照被重解析到本次 run 之外，拒绝执行。",
                    raw_error=str(lexical_path),
                )
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                return _error(
                    "RUN_AD4_MAP_SNAPSHOT_EMPTY"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_SNAPSHOT_EMPTY",
                    f"{map_label} map 快照缺失或为空，拒绝执行。",
                    raw_error=str(resolved),
                )
            actual_hash = _sha256_file(resolved)
            if actual_hash.lower() != expected_hash.lower():
                return _error(
                    "RUN_AD4_MAP_HASH_MISMATCH"
                    if scoring_protocol == "ad4_maps"
                    else "RUN_VINA_MAP_HASH_MISMATCH",
                    f"{map_label} map 快照在准备后发生变化，拒绝执行。",
                    raw_error=f"expected={expected_hash}; actual={actual_hash}; path={resolved}",
                    suggestion=f"请重新准备新的 {map_label} maps run。",
                )
            observed_names.add(name)
            observed_hashes[name] = actual_hash.lower()

        required_names = (
            {
                f"{prefix_name}.maps.fld",
                f"{prefix_name}.e.map",
                f"{prefix_name}.d.map",
            }
            if scoring_protocol == "ad4_maps"
            else set()
        )
        has_affinity_map = any(
            name.startswith(f"{prefix_name}.")
            and name.endswith(".map")
            and name not in required_names
            for name in observed_names
        )
        if (
            scoring_protocol == "ad4_maps"
            and not required_names.issubset(observed_names)
        ) or not has_affinity_map:
            return _error(
                "RUN_AD4_MAPS_INCOMPLETE"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MAPS_INCOMPLETE",
                f"{map_label} maps 快照不完整，拒绝执行。",
                raw_error=", ".join(sorted(observed_names)),
                suggestion="请重新生成或导入完整 maps 后准备新的 run。",
            )
        if is_hydrated and f"{prefix_name}.W.map" not in observed_names:
            return _error(
                "RUN_HYDRATED_W_MAP_MISSING",
                "水合对接 maps 快照缺少 W affinity map，拒绝执行。",
                raw_error=", ".join(sorted(observed_names)),
                suggestion="请重新生成完整的水合 maps 后准备新的 run。",
            )

        manifest_record = maps_snapshot.get("manifest") if isinstance(maps_snapshot.get("manifest"), dict) else {}
        manifest_relative = str(manifest_record.get("relative_path") or "")
        expected_manifest = Path("runs", run_id, "inputs", "maps", "manifest.json").as_posix()
        manifest_hash = str(manifest_record.get("sha256") or "")
        manifest_path = project_path / expected_manifest
        if manifest_relative != expected_manifest or not re.fullmatch(r"[0-9a-fA-F]{64}", manifest_hash):
            return _error(
                "RUN_AD4_MANIFEST_SNAPSHOT_INVALID"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MANIFEST_SNAPSHOT_INVALID",
                f"{map_label} maps manifest 快照记录无效，拒绝执行。",
            )
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.resolve(strict=True).parent != expected_maps_dir
            or _sha256_file(manifest_path).lower() != manifest_hash.lower()
        ):
            return _error(
                "RUN_AD4_MANIFEST_HASH_MISMATCH"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MANIFEST_HASH_MISMATCH",
                f"{map_label} maps manifest 快照缺失或已被修改，拒绝执行。",
                raw_error=str(manifest_path),
                suggestion=f"请重新准备新的 {map_label} maps run。",
            )
        try:
            frozen_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _error(
                "RUN_AD4_MANIFEST_INVALID"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MANIFEST_INVALID",
                f"{map_label} maps manifest 快照无法解析，拒绝执行。",
                raw_error=str(exc),
            )
        if not isinstance(frozen_manifest, dict):
            return _error(
                "RUN_AD4_MANIFEST_INVALID"
                if scoring_protocol == "ad4_maps"
                else "RUN_VINA_MANIFEST_INVALID",
                f"{map_label} maps manifest 快照必须是 JSON 对象。",
            )
        frozen_maps = (
            frozen_manifest.get("maps")
            if isinstance(frozen_manifest.get("maps"), dict)
            else {}
        )
        frozen_files = (
            frozen_maps.get("files")
            if isinstance(frozen_maps.get("files"), list)
            else []
        )
        frozen_receptor = (
            frozen_manifest.get("receptor")
            if isinstance(frozen_manifest.get("receptor"), dict)
            else {}
        )
        frozen_ligand = (
            frozen_manifest.get("ligand")
            if isinstance(frozen_manifest.get("ligand"), dict)
            else {}
        )
        frozen_prefix_name = Path(
            str(frozen_maps.get("prefix") or "")
        ).name
        frozen_hashes = {
            str(item.get("name") or ""): str(item.get("sha256") or "").lower()
            for item in frozen_files
            if isinstance(item, dict)
        }
        if uses_vina_maps:
            frozen_semantics = (
                frozen_manifest.get("semantics")
                if isinstance(frozen_manifest.get("semantics"), dict)
                else {}
            )
            frozen_vina = (
                frozen_manifest.get("vina")
                if isinstance(frozen_manifest.get("vina"), dict)
                else {}
            )
            prepared_vina = (
                metadata.get("vina_tool")
                if isinstance(metadata.get("vina_tool"), dict)
                else {}
            )
            maps_vina_sha256 = str(frozen_vina.get("sha256") or "").lower()
            if (
                str(frozen_manifest.get("protocol_id") or "") != "vina_maps"
                or str(frozen_manifest.get("scoring_function") or "")
                != maps_scoring
                or maps_scoring not in {"vina", "vinardo"}
                or frozen_prefix_name != prefix_name
                or str(frozen_receptor.get("source_sha256") or "").lower()
                != expected_hashes["receptor"].lower()
                or frozen_semantics.get("grid_only") is not True
                or frozen_semantics.get("no_refine_equivalent") is not True
                or frozen_hashes != observed_hashes
                or not SHA256_PATTERN.fullmatch(maps_vina_sha256)
                or maps_vina_sha256
                != str(prepared_vina.get("sha256") or "").lower()
            ):
                return _error(
                    "RUN_VINA_MANIFEST_BINDING_MISMATCH",
                    "Vina/Vinardo maps manifest 未完整绑定本次受体、配体、评分函数、map 文件与 grid-only 语义，拒绝执行。",
                    suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
                )
        else:
            expected_protocol_id = (
                AD4ZN_PROTOCOL_ID
                if is_ad4zn
                else HYDRATED_PROTOCOL_ID
                if is_hydrated
                else "ad4_maps"
            )
            if (
                str(frozen_manifest.get("protocol_id") or "")
                != expected_protocol_id
                or frozen_prefix_name != prefix_name
                or str(frozen_receptor.get("source_sha256") or "").lower()
                != expected_hashes["receptor"].lower()
                or str(frozen_ligand.get("source_sha256") or "").lower()
                != expected_hashes["ligand"].lower()
                or frozen_hashes != observed_hashes
            ):
                return _error(
                    "RUN_AD4ZN_MANIFEST_BINDING_MISMATCH"
                    if is_ad4zn
                    else "RUN_AD4_MANIFEST_BINDING_MISMATCH",
                    f"{map_label} maps manifest 未完整绑定本次受体、配体和 map 文件，拒绝执行。",
                    suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
                )
            if is_hydrated:
                from dockstart_core import autogrid as autogrid_core  # noqa: PLC0415
                from dockstart_core import hydrated_maps as hydrated_map_core  # noqa: PLC0415

                frozen_ligand_manifest = (
                    frozen_manifest.get("hydrated_ligand_manifest")
                    if isinstance(
                        frozen_manifest.get("hydrated_ligand_manifest"),
                        dict,
                    )
                    else {}
                )
                hydrated_metadata = (
                    metadata.get("hydrated")
                    if isinstance(metadata.get("hydrated"), dict)
                    else {}
                )
                input_sha256 = (
                    metadata.get("input_sha256")
                    if isinstance(metadata.get("input_sha256"), dict)
                    else {}
                )
                artifacts = (
                    metadata.get("artifacts")
                    if isinstance(metadata.get("artifacts"), dict)
                    else {}
                )
                ligand_manifest_artifact = (
                    artifacts.get("hydrated_ligand_manifest")
                    if isinstance(
                        artifacts.get("hydrated_ligand_manifest"),
                        dict,
                    )
                    else {}
                )
                expected_ligand_manifest_hash = expected_hashes[
                    "hydrated_ligand_manifest"
                ].lower()
                expected_ligand_manifest_snapshot = fixed_relative_paths[
                    "hydrated_ligand_manifest"
                ]
                expected_ligand_manifest_source = str(
                    hydrated_metadata.get(
                        "ligand_preparation_manifest",
                    )
                    or ""
                )
                if (
                    not expected_ligand_manifest_source
                    or str(
                        frozen_ligand_manifest.get("path") or ""
                    )
                    != expected_ligand_manifest_source
                    or str(
                        frozen_ligand_manifest.get("sha256") or ""
                    ).lower()
                    != expected_ligand_manifest_hash
                    or str(
                        hydrated_metadata.get(
                            "ligand_preparation_manifest_snapshot",
                        )
                        or ""
                    )
                    != expected_ligand_manifest_snapshot
                    or str(
                        input_sha256.get(
                            "hydrated_ligand_manifest",
                        )
                        or ""
                    ).lower()
                    != expected_ligand_manifest_hash
                    or str(
                        ligand_manifest_artifact.get(
                            "relative_path",
                        )
                        or ""
                    )
                    != expected_ligand_manifest_snapshot
                    or str(
                        ligand_manifest_artifact.get("sha256") or ""
                    ).lower()
                    != expected_ligand_manifest_hash
                ):
                    return _error(
                        "RUN_HYDRATED_LIGAND_MANIFEST_BINDING_MISMATCH",
                        "水合配体 preparation manifest 的来源、冻结快照或 SHA256 绑定无效，拒绝执行。",
                        suggestion=(
                            "请保留该 run 作为审计记录，并重新准备水合配体、"
                            "maps 和新的 run。"
                        ),
                    )
                frozen_water = (
                    frozen_maps.get("water_map")
                    if isinstance(frozen_maps.get("water_map"), dict)
                    else {}
                )
                frozen_water_parameters = (
                    frozen_water.get("parameters")
                    if isinstance(frozen_water.get("parameters"), dict)
                    else {}
                )
                frozen_water_sources = (
                    frozen_water.get("sources")
                    if isinstance(frozen_water.get("sources"), dict)
                    else {}
                )
                raw_hydrated_types = frozen_maps.get(
                    "ligand_atom_types"
                )
                raw_autogrid_types = frozen_maps.get(
                    "autogrid_ligand_atom_types"
                )
                hydrated_type_list = autogrid_core.canonical_atom_types(
                    raw_hydrated_types
                )
                autogrid_type_list = autogrid_core.canonical_atom_types(
                    raw_autogrid_types
                )
                atom_type_lists_valid = (
                    isinstance(raw_hydrated_types, list)
                    and isinstance(raw_autogrid_types, list)
                    and all(
                        isinstance(item, str) and bool(item.strip())
                        for item in raw_hydrated_types
                    )
                    and all(
                        isinstance(item, str) and bool(item.strip())
                        for item in raw_autogrid_types
                    )
                    and len(hydrated_type_list)
                    == len(raw_hydrated_types)
                    and len(autogrid_type_list)
                    == len(raw_autogrid_types)
                    and set(hydrated_type_list).issubset(
                        autogrid_core.STANDARD_NON_METAL_TYPES | {"W"}
                    )
                    and set(autogrid_type_list).issubset(
                        autogrid_core.STANDARD_NON_METAL_TYPES
                    )
                )
                hydrated_types = set(hydrated_type_list)
                autogrid_types = set(autogrid_type_list)
                water_name = f"{prefix_name}.W.map"
                oa_name = f"{prefix_name}.OA.map"
                hd_name = f"{prefix_name}.HD.map"
                frozen_records = {
                    str(item.get("name") or ""): item
                    for item in frozen_files
                    if isinstance(item, dict)
                }
                water_record = (
                    frozen_records.get(water_name)
                    if isinstance(frozen_records.get(water_name), dict)
                    else {}
                )
                oa_record = (
                    frozen_records.get(oa_name)
                    if isinstance(frozen_records.get(oa_name), dict)
                    else {}
                )
                hd_record = (
                    frozen_records.get(hd_name)
                    if isinstance(frozen_records.get(hd_name), dict)
                    else {}
                )
                expected_water_parameters = {
                    "mode": "BEST",
                    "weight": hydrated_map_core.BEST_WEIGHT,
                    "entropy": hydrated_map_core.DISPLACEMENT_ENTROPY,
                    "oa_weight": hydrated_map_core.OA_WEIGHT,
                    "hd_weight": hydrated_map_core.HD_WEIGHT,
                    "output_decimals": hydrated_map_core.OUTPUT_DECIMALS,
                    "positive_value_rule": (
                        "entropy_if_oa_gt_0_or_hd_gt_0"
                    ),
                }
                if (
                    not atom_type_lists_valid
                    or "W" not in hydrated_types
                    or "W" in autogrid_types
                    or not {"OA", "HD"}.issubset(autogrid_types)
                    or not (hydrated_types - {"W"}).issubset(
                        autogrid_types
                    )
                    or str(frozen_water.get("name") or "")
                    != water_name
                    or str(frozen_water.get("method") or "")
                    != "hydrated_ad4_best_v1"
                    or frozen_water_parameters
                    != expected_water_parameters
                    or str(frozen_water.get("relative_path") or "")
                    != str(water_record.get("relative_path") or "")
                    or str(frozen_water.get("sha256") or "").lower()
                    != str(water_record.get("sha256") or "").lower()
                    or str(
                        (
                            frozen_water_sources.get("oa")
                            if isinstance(
                                frozen_water_sources.get("oa"),
                                dict,
                            )
                            else {}
                        ).get("sha256")
                        or ""
                    ).lower()
                    != str(oa_record.get("sha256") or "").lower()
                    or str(
                        (
                            frozen_water_sources.get("hd")
                            if isinstance(
                                frozen_water_sources.get("hd"),
                                dict,
                            )
                            else {}
                        ).get("sha256")
                        or ""
                    ).lower()
                    != str(hd_record.get("sha256") or "").lower()
                    or (
                        frozen_water.get("geometry")
                        if isinstance(
                            frozen_water.get("geometry"),
                            dict,
                        )
                        else {}
                    )
                    != (
                        frozen_maps.get("geometry")
                        if isinstance(
                            frozen_maps.get("geometry"),
                            dict,
                        )
                        else {}
                    )
                ):
                    return _error(
                        "RUN_HYDRATED_W_MAP_BINDING_MISMATCH",
                        "水合 maps 的原子类型、BEST 参数或 OA/HD/W 绑定记录无效，拒绝执行。",
                        suggestion=(
                            "请保留该 run 作为审计记录，并重新生成水合 maps、"
                            "准备新的 run。"
                        ),
                    )
                hydrated_coverage, hydrated_coverage_issue = (
                    _hydrated_frozen_grid_coverage(
                        frozen_manifest,
                        expected_box=metadata.get("box_snapshot"),
                        expected_grid=maps_metadata.get("grid"),
                    )
                )
                if (
                    hydrated_coverage_issue
                    or hydrated_coverage is None
                ):
                    return _error(
                        "RUN_HYDRATED_GRID_COVERAGE_INVALID",
                        "冻结的水合 maps 未通过请求 Box/实际网格覆盖复核，拒绝执行。",
                        raw_error=(
                            hydrated_coverage_issue
                            or "缺少可复核的覆盖记录。"
                        ),
                        suggestion=(
                            "请保留该 run 作为审计记录，重新生成水合 maps "
                            "并准备新的 run。"
                        ),
                    )
                frozen_coverage_sha256 = str(
                    frozen_manifest.get("grid_coverage_sha256") or ""
                ).lower()
                for coverage_record in (
                    maps_snapshot,
                    maps_metadata,
                    hydrated_metadata,
                ):
                    recorded_coverage = (
                        coverage_record.get("grid_coverage")
                        if isinstance(
                            coverage_record.get("grid_coverage"),
                            dict,
                        )
                        else None
                    )
                    recorded_sha256 = str(
                        coverage_record.get(
                            "grid_coverage_sha256"
                        )
                        or ""
                    ).lower()
                    if (
                        recorded_coverage != hydrated_coverage
                        or recorded_sha256 != frozen_coverage_sha256
                    ):
                        return _error(
                            "RUN_HYDRATED_GRID_COVERAGE_INVALID",
                            "冻结运行记录与水合 maps manifest 的覆盖证据不一致，拒绝执行。",
                            suggestion=(
                                "请保留该 run 作为审计记录，重新生成水合 "
                                "maps 并准备新的 run。"
                            ),
                        )
            if is_ad4zn:
                ad4zn_snapshot = (
                    snapshots.get("ad4zn")
                    if isinstance(snapshots.get("ad4zn"), dict)
                    else {}
                )
                ad4zn_dir = run_dir / "inputs" / "ad4zn"
                evidence_names = {
                    "original_receptor": "original_receptor.pdbqt",
                    "parameter_file": "AD4Zn.dat",
                    "gpf": "receptor.gpf",
                    "protocol_record": "protocol_record.json",
                }
                resolved_evidence: dict[str, Path] = {}
                for evidence_key, filename in evidence_names.items():
                    record = (
                        ad4zn_snapshot.get(evidence_key)
                        if isinstance(
                            ad4zn_snapshot.get(evidence_key),
                            dict,
                        )
                        else {}
                    )
                    relative_path = str(record.get("relative_path") or "")
                    expected_relative = Path(
                        "runs",
                        run_id,
                        "inputs",
                        "ad4zn",
                        filename,
                    ).as_posix()
                    expected_hash = str(record.get("sha256") or "").lower()
                    evidence_path = project_path / expected_relative
                    if (
                        relative_path != expected_relative
                        or not SHA256_PATTERN.fullmatch(expected_hash)
                        or evidence_path.is_symlink()
                        or not evidence_path.is_file()
                        or evidence_path.stat().st_size <= 0
                        or evidence_path.resolve(strict=True).parent
                        != ad4zn_dir
                        or _sha256_file(evidence_path).lower()
                        != expected_hash
                    ):
                        return _error(
                            "RUN_AD4ZN_EVIDENCE_INVALID",
                            f"AD4Zn {evidence_key} 冻结证据缺失、路径不可信或已被修改，拒绝执行。",
                            raw_error=str(evidence_path),
                            suggestion="请保留该 run 作为审计记录，并重新准备新的 AD4Zn run。",
                        )
                    resolved_evidence[evidence_key] = evidence_path
                frozen_parameter = (
                    frozen_manifest.get("parameter_file")
                    if isinstance(
                        frozen_manifest.get("parameter_file"),
                        dict,
                    )
                    else {}
                )
                frozen_gpf = (
                    frozen_manifest.get("gpf")
                    if isinstance(frozen_manifest.get("gpf"), dict)
                    else {}
                )
                frozen_ad4zn = (
                    frozen_manifest.get("ad4zn")
                    if isinstance(frozen_manifest.get("ad4zn"), dict)
                    else {}
                )
                frozen_autogrid = (
                    frozen_manifest.get("autogrid")
                    if isinstance(frozen_manifest.get("autogrid"), dict)
                    else {}
                )
                frozen_log_summary = (
                    frozen_autogrid.get("log_summary")
                    if isinstance(
                        frozen_autogrid.get("log_summary"),
                        dict,
                    )
                    else {}
                )
                try:
                    protocol_record = json.loads(
                        resolved_evidence["protocol_record"].read_text(
                            encoding="utf-8"
                        )
                    )
                except (
                    OSError,
                    UnicodeError,
                    json.JSONDecodeError,
                ) as exc:
                    return _error(
                        "RUN_AD4ZN_PROTOCOL_RECORD_INVALID",
                        "AD4Zn 冻结协议记录无法解析，拒绝执行。",
                        raw_error=str(exc),
                    )
                prepared_issue = _ad4zn_prepared_receptor_issue(
                    fixed_paths["receptor"]
                )
                parameter_issue = _ad4zn_parameter_reference_issue(
                    resolved_evidence["parameter_file"]
                )
                _box_coverage, box_coverage_issue = (
                    _ad4zn_frozen_box_coverage(
                        fixed_paths["receptor"],
                        frozen_manifest,
                    )
                )
                missing_gpf_lines = _ad4zn_gpf_missing_lines(
                    resolved_evidence["gpf"]
                )
                if (
                    str(frozen_ad4zn.get("protocol_id") or "")
                    != AD4ZN_PROTOCOL_ID
                    or protocol_record != frozen_ad4zn
                    or str(frozen_receptor.get("original_source_sha256") or "").lower()
                    != _sha256_file(
                        resolved_evidence["original_receptor"]
                    ).lower()
                    or str(frozen_parameter.get("sha256") or "").lower()
                    != _sha256_file(
                        resolved_evidence["parameter_file"]
                    ).lower()
                    or str(frozen_gpf.get("sha256") or "").lower()
                    != _sha256_file(resolved_evidence["gpf"]).lower()
                    or not _autogrid_version_supports_ad4zn(
                        frozen_autogrid.get("version")
                    )
                    or frozen_log_summary.get("successful_completion")
                    is not True
                    or prepared_issue
                    or parameter_issue
                    or box_coverage_issue
                    or missing_gpf_lines
                ):
                    details = [
                        *([prepared_issue] if prepared_issue else []),
                        *([parameter_issue] if parameter_issue else []),
                        *(
                            [box_coverage_issue]
                            if box_coverage_issue
                            else []
                        ),
                        *(
                            [
                                "GPF 缺少：" + ", ".join(missing_gpf_lines)
                            ]
                            if missing_gpf_lines
                            else []
                        ),
                    ]
                    return _error(
                        "RUN_AD4ZN_PROTOCOL_BINDING_MISMATCH",
                        "AD4Zn maps、ZN/TZ Box 覆盖、官方参数文件、GPF、AutoGrid 版本或人工复核记录不一致，拒绝执行。",
                        raw_error="；".join(details),
                        suggestion="请保留该 run 作为审计记录，并从 AD4Zn 复核步骤重新准备。",
                    )

    config_text = fixed_paths["config"].read_text(encoding="utf-8", errors="strict")
    expected_receptor_line = f"receptor = {fixed_relative_paths['receptor']}"
    expected_ligand_line = f"ligand = {fixed_relative_paths['ligand']}"
    config_lines = config_text.splitlines()
    if scoring_protocol == "ad4_maps":
        config_inputs_match = (
            expected_ligand_line in config_lines
            and "scoring = ad4" in config_lines
            and expected_receptor_line not in config_lines
        )
    elif uses_vina_maps:
        config_inputs_match = (
            expected_ligand_line in config_lines
            and f"scoring = {maps_scoring}" in config_lines
            and expected_receptor_line not in config_lines
        )
    else:
        config_inputs_match = (
            expected_receptor_line in config_lines
            and expected_ligand_line in config_lines
        )
    if not config_inputs_match:
        return _error(
            "RUN_CONFIG_SNAPSHOT_INPUT_MISMATCH",
            "运行配置快照没有引用本次 run 的 immutable 输入快照。",
            suggestion="请重新准备新的 run。",
        )
    if uses_vina_maps:
        forbidden_grid_keys = {
            "receptor",
            "center_x",
            "center_y",
            "center_z",
            "size_x",
            "size_y",
            "size_z",
            "spacing",
            "no_refine",
            "force_even_voxels",
        }
        observed_forbidden = sorted(
            {
                line.split("=", 1)[0].strip()
                for line in config_lines
                if "=" in line
                and line.split("=", 1)[0].strip() in forbidden_grid_keys
            }
        )
        if observed_forbidden:
            return _error(
                "RUN_VINA_MAPS_CONFIG_GRID_CONFLICT",
                "预计算 maps 配置快照仍包含受体或实时网格选项，拒绝执行。",
                raw_error=", ".join(observed_forbidden),
                suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
            )
    effective_vina_options = {
        "no_refine": False,
        "force_even_voxels": False,
        "unbound_energy": None,
    }
    if scoring_protocol != "ad4_maps" and not uses_vina_maps:
        for key in ("no_refine", "force_even_voxels"):
            value, option_error = _parse_run_config_bool(config_text, key)
            if option_error:
                return option_error
            effective_vina_options[key] = bool(value)
    unbound_energy, unbound_error = _parse_run_config_optional_float(
        config_text,
        "unbound_energy",
    )
    if unbound_error:
        return unbound_error
    if unbound_energy is not None and (
        scoring_protocol == "ad4_maps"
        or uses_vina_maps
        or run_mode != "score_only"
        or str(protocol.get("mode") or "rigid").strip().lower() == "flexible"
    ):
        return _error(
            "RUN_CONFIG_UNBOUND_ENERGY_NOT_APPLICABLE",
            "本次运行类型不能使用显式未结合态参考能量。",
            raw_error=(
                f"scoring_protocol={scoring_protocol}; "
                f"run_mode={run_mode}; unbound_energy={unbound_energy}"
            ),
            suggestion="请保留该 run 作为审计记录，并重新准备新的 run。",
        )
    effective_vina_options["unbound_energy"] = unbound_energy

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project.to_dict(),
        "config_file": fixed_relative_paths["config"],
        "config_path": str(fixed_paths["config"]),
        "receptor_file": fixed_relative_paths["receptor"],
        "ligand_file": fixed_relative_paths["ligand"],
        "flex_file": fixed_relative_paths.get("flex", ""),
        "scoring_protocol": scoring_protocol,
        "grid_source": grid_source,
        "run_mode": run_mode,
        "autobox": autobox,
        "effective_vina_options": effective_vina_options,
        "maps_prefix": maps_prefix,
        "maps_scoring": maps_scoring,
        "maps_vina_sha256": maps_vina_sha256,
        "output_file": fixed_relative_paths.get("output", ""),
        "output_path": str(fixed_paths["output"]) if "output" in fixed_paths else "",
        "log_file": fixed_relative_paths["log"],
        "log_path": str(fixed_paths["log"]),
        "stdout_file": fixed_relative_paths["stdout"],
        "stdout_path": str(fixed_paths["stdout"]),
        "stderr_file": fixed_relative_paths["stderr"],
        "stderr_path": str(fixed_paths["stderr"]),
        "composite_local_only": composite_local_only,
        "baseline_log_file": fixed_relative_paths.get("baseline_log", ""),
        "baseline_log_path": str(fixed_paths["baseline_log"]) if "baseline_log" in fixed_paths else "",
        "baseline_stdout_file": fixed_relative_paths.get("baseline_stdout", ""),
        "baseline_stdout_path": (
            str(fixed_paths["baseline_stdout"]) if "baseline_stdout" in fixed_paths else ""
        ),
        "baseline_stderr_file": fixed_relative_paths.get("baseline_stderr", ""),
        "baseline_stderr_path": (
            str(fixed_paths["baseline_stderr"]) if "baseline_stderr" in fixed_paths else ""
        ),
        "error": None,
    }


def _validate_vina_maps_post_run_integrity(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Recheck immutable Vina-map inputs without inspecting mutable outputs."""

    if _metadata_grid_source(metadata) != "precomputed_maps":
        return {
            "ok": True,
            "status": "not_applicable",
            "checked_at": _now_iso(),
            "verified": [],
            "error": None,
        }
    project_path = Path(project_dir).expanduser().resolve()
    try:
        run_dir = _safe_run_directory(project_path, run_id)
    except Exception as exc:  # noqa: BLE001 - convert to structured error.
        return _error(
            "RUN_VINA_MAPS_POST_PATH_UNSAFE",
            "运行结束后无法安全定位本次 run，结果已拒绝。",
            raw_error=str(exc),
        )
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    inputs = (
        snapshots.get("inputs")
        if isinstance(snapshots.get("inputs"), dict)
        else {}
    )
    config_record = (
        snapshots.get("config")
        if isinstance(snapshots.get("config"), dict)
        else {}
    )
    maps_record = (
        snapshots.get("vina_maps")
        if isinstance(snapshots.get("vina_maps"), dict)
        else {}
    )
    expected_records: list[tuple[str, str, str, Path]] = []
    for key in ("receptor", "ligand"):
        record = inputs.get(key) if isinstance(inputs.get(key), dict) else {}
        expected_records.append(
            (
                key,
                str(record.get("relative_path") or ""),
                str(record.get("sha256") or ""),
                run_dir / "inputs",
            )
        )
    expected_records.append(
        (
            "config",
            str(config_record.get("relative_path") or ""),
            str(config_record.get("sha256") or ""),
            run_dir,
        )
    )
    map_files = (
        maps_record.get("files")
        if isinstance(maps_record.get("files"), list)
        else []
    )
    for index, record in enumerate(map_files):
        if not isinstance(record, dict):
            return _error(
                "RUN_VINA_MAPS_POST_RECORD_INVALID",
                "运行结束后发现 map 快照记录格式无效，结果已拒绝。",
                raw_error=f"index={index}",
            )
        expected_records.append(
            (
                f"map:{record.get('name') or index}",
                str(record.get("relative_path") or ""),
                str(record.get("sha256") or ""),
                run_dir / "inputs" / "maps",
            )
        )
    manifest_record = (
        maps_record.get("manifest")
        if isinstance(maps_record.get("manifest"), dict)
        else {}
    )
    expected_records.append(
        (
            "maps_manifest",
            str(manifest_record.get("relative_path") or ""),
            str(manifest_record.get("sha256") or ""),
            run_dir / "inputs" / "maps",
        )
    )
    verified: list[dict[str, Any]] = []
    resolved_by_key: dict[str, Path] = {}
    for key, relative_path, expected_hash, expected_parent in expected_records:
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or not SHA256_PATTERN.fullmatch(expected_hash.lower())
        ):
            return _error(
                "RUN_VINA_MAPS_POST_RECORD_INVALID",
                f"{key} 的冻结路径或 SHA256 记录无效，结果已拒绝。",
                raw_error=f"path={relative_path}; sha256={expected_hash}",
            )
        lexical_path = project_path / relative_path
        if lexical_path.is_symlink():
            return _error(
                "RUN_VINA_MAPS_POST_SYMLINK_UNSAFE",
                f"{key} 在运行期间被替换为符号链接，结果已拒绝。",
                raw_error=str(lexical_path),
            )
        try:
            resolved = lexical_path.resolve(strict=True)
        except OSError as exc:
            return _error(
                "RUN_VINA_MAPS_POST_INPUT_MISSING",
                f"{key} 在运行期间丢失，结果已拒绝。",
                raw_error=f"{lexical_path}: {exc}",
            )
        if (
            resolved != lexical_path.absolute()
            or resolved.parent != expected_parent
            or not resolved.is_file()
            or resolved.stat().st_size <= 0
        ):
            return _error(
                "RUN_VINA_MAPS_POST_PATH_MISMATCH",
                f"{key} 的冻结路径在运行期间发生变化，结果已拒绝。",
                raw_error=str(resolved),
            )
        actual_hash = _sha256_file(resolved)
        if actual_hash.lower() != expected_hash.lower():
            return _error(
                "RUN_VINA_MAPS_POST_HASH_MISMATCH",
                f"{key} 在 Vina 运行期间发生变化，结果已拒绝。",
                raw_error=(
                    f"expected={expected_hash}; actual={actual_hash}; "
                    f"path={resolved}"
                ),
            )
        verified.append(
            {
                "key": key,
                "relative_path": Path(relative_path).as_posix(),
                "sha256": actual_hash,
                "size_bytes": resolved.stat().st_size,
            }
        )
        resolved_by_key[key] = resolved

    manifest_path = resolved_by_key.get("maps_manifest")
    assert manifest_path is not None
    try:
        frozen_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _error(
            "RUN_VINA_MAPS_POST_MANIFEST_INVALID",
            "运行结束后无法解析冻结的 maps manifest，结果已拒绝。",
            raw_error=str(exc),
        )
    frozen_maps = (
        frozen_manifest.get("maps")
        if isinstance(frozen_manifest, dict)
        and isinstance(frozen_manifest.get("maps"), dict)
        else {}
    )
    frozen_receptor = (
        frozen_manifest.get("receptor")
        if isinstance(frozen_manifest, dict)
        and isinstance(frozen_manifest.get("receptor"), dict)
        else {}
    )
    frozen_semantics = (
        frozen_manifest.get("semantics")
        if isinstance(frozen_manifest, dict)
        and isinstance(frozen_manifest.get("semantics"), dict)
        else {}
    )
    frozen_files = (
        frozen_maps.get("files")
        if isinstance(frozen_maps.get("files"), list)
        else []
    )
    frozen_hashes = {
        str(item.get("name") or ""): str(item.get("sha256") or "").lower()
        for item in frozen_files
        if isinstance(item, dict)
    }
    observed_hashes = {
        str(record.get("name") or ""): str(record.get("sha256") or "").lower()
        for record in map_files
        if isinstance(record, dict)
    }
    receptor_record = (
        inputs.get("receptor")
        if isinstance(inputs.get("receptor"), dict)
        else {}
    )
    if (
        not isinstance(frozen_manifest, dict)
        or frozen_manifest.get("protocol_id") != "vina_maps"
        or str(frozen_manifest.get("scoring_function") or "")
        != str(metadata.get("scoring_function") or "")
        or str(frozen_receptor.get("source_sha256") or "").lower()
        != str(receptor_record.get("sha256") or "").lower()
        or frozen_semantics.get("grid_only") is not True
        or frozen_semantics.get("no_refine_equivalent") is not True
        or frozen_hashes != observed_hashes
    ):
        return _error(
            "RUN_VINA_MAPS_POST_MANIFEST_BINDING_MISMATCH",
            "运行结束后 maps manifest 与冻结受体、评分函数、map 文件或 grid-only 语义不一致，结果已拒绝。",
        )
    return {
        "ok": True,
        "status": "verified",
        "checked_at": _now_iso(),
        "verified": verified,
        "error": None,
    }


def _validate_ad4_maps_post_run_integrity(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Recheck frozen AutoDock4/AutoDock4Zn inputs after Vina exits."""

    if _metadata_scoring_protocol(metadata) != "ad4_maps":
        return {
            "ok": True,
            "status": "not_applicable",
            "checked_at": _now_iso(),
            "verified": [],
            "error": None,
        }
    is_ad4zn = _metadata_protocol_id(metadata) == AD4ZN_PROTOCOL_ID
    is_hydrated = _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
    verified_ad4zn_box_coverage: dict[str, Any] | None = None
    verified_hydrated_grid_coverage: dict[str, Any] | None = None
    code_prefix = (
        "RUN_AD4ZN_POST"
        if is_ad4zn
        else "RUN_HYDRATED_POST"
        if is_hydrated
        else "RUN_AD4_MAPS_POST"
    )
    label = (
        "AutoDock4Zn beta"
        if is_ad4zn
        else "实验性水合 AD4"
        if is_hydrated
        else "AutoDock4"
    )
    project_path = Path(project_dir).expanduser().resolve()
    try:
        run_dir = _safe_run_directory(project_path, run_id)
    except Exception as exc:  # noqa: BLE001
        return _error(
            f"{code_prefix}_PATH_UNSAFE",
            f"运行结束后无法安全定位本次 {label} run，结果已拒绝。",
            raw_error=str(exc),
        )

    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    inputs = (
        snapshots.get("inputs")
        if isinstance(snapshots.get("inputs"), dict)
        else {}
    )
    config_record = (
        snapshots.get("config")
        if isinstance(snapshots.get("config"), dict)
        else {}
    )
    maps_record = (
        snapshots.get("ad4_maps")
        if isinstance(snapshots.get("ad4_maps"), dict)
        else {}
    )
    hydrated_records = (
        snapshots.get("hydrated")
        if is_hydrated and isinstance(snapshots.get("hydrated"), dict)
        else {}
    )
    expected_records: list[tuple[str, str, str, Path]] = []
    for key in ("receptor", "ligand"):
        record = inputs.get(key) if isinstance(inputs.get(key), dict) else {}
        expected_records.append(
            (
                key,
                str(record.get("relative_path") or ""),
                str(record.get("sha256") or ""),
                run_dir / "inputs",
            )
        )
    expected_records.append(
        (
            "config",
            str(config_record.get("relative_path") or ""),
            str(config_record.get("sha256") or ""),
            run_dir,
        )
    )
    if is_hydrated:
        ligand_manifest_record = (
            hydrated_records.get("ligand_manifest")
            if isinstance(hydrated_records.get("ligand_manifest"), dict)
            else {}
        )
        expected_records.append(
            (
                "hydrated:ligand_manifest",
                str(ligand_manifest_record.get("relative_path") or ""),
                str(ligand_manifest_record.get("sha256") or ""),
                run_dir / "inputs" / "hydrated",
            )
        )
    map_files = (
        maps_record.get("files")
        if isinstance(maps_record.get("files"), list)
        else []
    )
    for index, record in enumerate(map_files):
        if not isinstance(record, dict):
            return _error(
                f"{code_prefix}_RECORD_INVALID",
                f"{label} map 快照记录格式无效，结果已拒绝。",
                raw_error=f"index={index}",
            )
        expected_records.append(
            (
                f"map:{record.get('name') or index}",
                str(record.get("relative_path") or ""),
                str(record.get("sha256") or ""),
                run_dir / "inputs" / "maps",
            )
        )
    manifest_record = (
        maps_record.get("manifest")
        if isinstance(maps_record.get("manifest"), dict)
        else {}
    )
    expected_records.append(
        (
            "maps_manifest",
            str(manifest_record.get("relative_path") or ""),
            str(manifest_record.get("sha256") or ""),
            run_dir / "inputs" / "maps",
        )
    )
    ad4zn_records = (
        snapshots.get("ad4zn")
        if is_ad4zn and isinstance(snapshots.get("ad4zn"), dict)
        else {}
    )
    if is_ad4zn:
        for key in (
            "original_receptor",
            "parameter_file",
            "gpf",
            "protocol_record",
        ):
            record = (
                ad4zn_records.get(key)
                if isinstance(ad4zn_records.get(key), dict)
                else {}
            )
            expected_records.append(
                (
                    f"ad4zn:{key}",
                    str(record.get("relative_path") or ""),
                    str(record.get("sha256") or ""),
                    run_dir / "inputs" / "ad4zn",
                )
            )

    verified: list[dict[str, Any]] = []
    resolved_by_key: dict[str, Path] = {}
    for key, relative_path, expected_hash, expected_parent in expected_records:
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or not SHA256_PATTERN.fullmatch(expected_hash.lower())
        ):
            return _error(
                f"{code_prefix}_RECORD_INVALID",
                f"{key} 的冻结路径或 SHA256 记录无效，结果已拒绝。",
                raw_error=f"path={relative_path}; sha256={expected_hash}",
            )
        lexical_path = project_path / relative_path
        if lexical_path.is_symlink():
            return _error(
                f"{code_prefix}_SYMLINK_UNSAFE",
                f"{key} 在运行期间被替换为符号链接，结果已拒绝。",
                raw_error=str(lexical_path),
            )
        try:
            resolved = lexical_path.resolve(strict=True)
        except OSError as exc:
            return _error(
                f"{code_prefix}_INPUT_MISSING",
                f"{key} 在运行期间丢失，结果已拒绝。",
                raw_error=f"{lexical_path}: {exc}",
            )
        if (
            resolved != lexical_path.absolute()
            or resolved.parent != expected_parent
            or not resolved.is_file()
            or resolved.stat().st_size <= 0
        ):
            return _error(
                f"{code_prefix}_PATH_MISMATCH",
                f"{key} 的冻结路径在运行期间发生变化，结果已拒绝。",
                raw_error=str(resolved),
            )
        actual_hash = _sha256_file(resolved)
        if actual_hash.lower() != expected_hash.lower():
            return _error(
                f"{code_prefix}_HASH_MISMATCH",
                f"{key} 在 Vina 运行期间发生变化，结果已拒绝。",
                raw_error=(
                    f"expected={expected_hash}; actual={actual_hash}; "
                    f"path={resolved}"
                ),
            )
        verified.append(
            {
                "key": key,
                "relative_path": Path(relative_path).as_posix(),
                "sha256": actual_hash,
                "size_bytes": resolved.stat().st_size,
            }
        )
        resolved_by_key[key] = resolved

    manifest_path = resolved_by_key.get("maps_manifest")
    assert manifest_path is not None
    try:
        frozen_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _error(
            f"{code_prefix}_MANIFEST_INVALID",
            f"运行结束后无法解析冻结的 {label} maps manifest，结果已拒绝。",
            raw_error=str(exc),
        )
    if not isinstance(frozen_manifest, dict):
        return _error(
            f"{code_prefix}_MANIFEST_INVALID",
            f"冻结的 {label} maps manifest 不是 JSON 对象，结果已拒绝。",
        )
    frozen_maps = (
        frozen_manifest.get("maps")
        if isinstance(frozen_manifest.get("maps"), dict)
        else {}
    )
    frozen_receptor = (
        frozen_manifest.get("receptor")
        if isinstance(frozen_manifest.get("receptor"), dict)
        else {}
    )
    frozen_ligand = (
        frozen_manifest.get("ligand")
        if isinstance(frozen_manifest.get("ligand"), dict)
        else {}
    )
    frozen_files = (
        frozen_maps.get("files")
        if isinstance(frozen_maps.get("files"), list)
        else []
    )
    frozen_hashes = {
        str(item.get("name") or ""): str(item.get("sha256") or "").lower()
        for item in frozen_files
        if isinstance(item, dict)
    }
    observed_hashes = {
        str(record.get("name") or ""): str(record.get("sha256") or "").lower()
        for record in map_files
        if isinstance(record, dict)
    }
    receptor_record = (
        inputs.get("receptor")
        if isinstance(inputs.get("receptor"), dict)
        else {}
    )
    ligand_record = (
        inputs.get("ligand")
        if isinstance(inputs.get("ligand"), dict)
        else {}
    )
    expected_protocol = (
        AD4ZN_PROTOCOL_ID
        if is_ad4zn
        else HYDRATED_PROTOCOL_ID
        if is_hydrated
        else "ad4_maps"
    )
    frozen_prefix = Path(str(frozen_maps.get("prefix") or "")).name
    snapshot_prefix = Path(str(maps_record.get("prefix") or "")).name
    if (
        str(frozen_manifest.get("protocol_id") or "") != expected_protocol
        or not frozen_prefix
        or frozen_prefix != snapshot_prefix
        or str(frozen_receptor.get("source_sha256") or "").lower()
        != str(receptor_record.get("sha256") or "").lower()
        or str(frozen_ligand.get("source_sha256") or "").lower()
        != str(ligand_record.get("sha256") or "").lower()
        or frozen_hashes != observed_hashes
    ):
        return _error(
            f"{code_prefix}_MANIFEST_BINDING_MISMATCH",
            f"运行结束后 {label} maps manifest 与冻结受体、配体或 map 文件不一致，结果已拒绝。",
        )

    if is_hydrated:
        frozen_ligand_manifest = (
            frozen_manifest.get("hydrated_ligand_manifest")
            if isinstance(
                frozen_manifest.get("hydrated_ligand_manifest"),
                dict,
            )
            else {}
        )
        ligand_manifest_record = (
            hydrated_records.get("ligand_manifest")
            if isinstance(hydrated_records.get("ligand_manifest"), dict)
            else {}
        )
        hydrated_metadata = (
            metadata.get("hydrated")
            if isinstance(metadata.get("hydrated"), dict)
            else {}
        )
        input_sha256 = (
            metadata.get("input_sha256")
            if isinstance(metadata.get("input_sha256"), dict)
            else {}
        )
        artifacts = (
            metadata.get("artifacts")
            if isinstance(metadata.get("artifacts"), dict)
            else {}
        )
        ligand_manifest_artifact = (
            artifacts.get("hydrated_ligand_manifest")
            if isinstance(
                artifacts.get("hydrated_ligand_manifest"),
                dict,
            )
            else {}
        )
        expected_ligand_manifest_relative = Path(
            "runs",
            run_id,
            "inputs",
            "hydrated",
            "ligand_manifest.json",
        ).as_posix()
        expected_ligand_manifest_hash = str(
            ligand_manifest_record.get("sha256") or ""
        ).lower()
        expected_ligand_manifest_source = str(
            hydrated_metadata.get("ligand_preparation_manifest") or ""
        )
        if (
            str(ligand_manifest_record.get("relative_path") or "")
            != expected_ligand_manifest_relative
            or not SHA256_PATTERN.fullmatch(
                expected_ligand_manifest_hash,
            )
            or not expected_ligand_manifest_source
            or str(frozen_ligand_manifest.get("path") or "")
            != expected_ligand_manifest_source
            or str(
                frozen_ligand_manifest.get("sha256") or ""
            ).lower()
            != expected_ligand_manifest_hash
            or str(
                hydrated_metadata.get(
                    "ligand_preparation_manifest_snapshot",
                )
                or ""
            )
            != expected_ligand_manifest_relative
            or str(
                input_sha256.get("hydrated_ligand_manifest") or ""
            ).lower()
            != expected_ligand_manifest_hash
            or str(
                ligand_manifest_artifact.get("relative_path") or ""
            )
            != expected_ligand_manifest_relative
            or str(
                ligand_manifest_artifact.get("sha256") or ""
            ).lower()
            != expected_ligand_manifest_hash
        ):
            return _error(
                f"{code_prefix}_LIGAND_MANIFEST_BINDING_MISMATCH",
                "运行结束后水合配体 preparation manifest 的来源、冻结快照或 SHA256 绑定不一致，结果已拒绝。",
            )
        water_map_name = f"{frozen_prefix}.W.map"
        if water_map_name not in frozen_hashes:
            return _error(
                f"{code_prefix}_W_MAP_MISSING",
                "运行结束后冻结的水合 maps 缺少 W affinity map，结果已拒绝。",
                raw_error=", ".join(sorted(frozen_hashes)),
            )
        hydrated_maps_metadata = (
            metadata.get("ad4_maps")
            if isinstance(metadata.get("ad4_maps"), dict)
            else {}
        )
        (
            verified_hydrated_grid_coverage,
            hydrated_coverage_issue,
        ) = _hydrated_frozen_grid_coverage(
            frozen_manifest,
            expected_box=metadata.get("box_snapshot"),
            expected_grid=hydrated_maps_metadata.get("grid"),
        )
        frozen_coverage_sha256 = str(
            frozen_manifest.get("grid_coverage_sha256") or ""
        ).lower()
        if (
            hydrated_coverage_issue
            or verified_hydrated_grid_coverage is None
        ):
            return _error(
                f"{code_prefix}_GRID_COVERAGE_INVALID",
                "运行结束后冻结的水合请求 Box/实际网格覆盖证据不一致，结果已拒绝。",
                raw_error=(
                    hydrated_coverage_issue
                    or "metadata 与冻结 maps manifest 的覆盖记录不一致。"
                ),
                suggestion="请重新生成水合 maps 并准备新的 run。",
            )
        for coverage_record in (
            maps_record,
            hydrated_maps_metadata,
            hydrated_metadata,
        ):
            recorded_coverage = (
                coverage_record.get("grid_coverage")
                if isinstance(
                    coverage_record.get("grid_coverage"),
                    dict,
                )
                else None
            )
            recorded_coverage_sha256 = str(
                coverage_record.get("grid_coverage_sha256") or ""
            ).lower()
            if (
                recorded_coverage
                != verified_hydrated_grid_coverage
                or recorded_coverage_sha256
                != frozen_coverage_sha256
            ):
                return _error(
                    f"{code_prefix}_GRID_COVERAGE_INVALID",
                    "运行结束后 metadata 与冻结水合 maps manifest 的覆盖证据不一致，结果已拒绝。",
                    suggestion="请重新生成水合 maps 并准备新的 run。",
                )

    if is_ad4zn:
        frozen_parameter = (
            frozen_manifest.get("parameter_file")
            if isinstance(frozen_manifest.get("parameter_file"), dict)
            else {}
        )
        frozen_gpf = (
            frozen_manifest.get("gpf")
            if isinstance(frozen_manifest.get("gpf"), dict)
            else {}
        )
        frozen_ad4zn = (
            frozen_manifest.get("ad4zn")
            if isinstance(frozen_manifest.get("ad4zn"), dict)
            else {}
        )
        frozen_autogrid = (
            frozen_manifest.get("autogrid")
            if isinstance(frozen_manifest.get("autogrid"), dict)
            else {}
        )
        frozen_log_summary = (
            frozen_autogrid.get("log_summary")
            if isinstance(frozen_autogrid.get("log_summary"), dict)
            else {}
        )
        protocol_path = resolved_by_key.get("ad4zn:protocol_record")
        original_path = resolved_by_key.get("ad4zn:original_receptor")
        parameter_path = resolved_by_key.get("ad4zn:parameter_file")
        gpf_path = resolved_by_key.get("ad4zn:gpf")
        prepared_path = resolved_by_key.get("receptor")
        assert all(
            path is not None
            for path in (
                protocol_path,
                original_path,
                parameter_path,
                gpf_path,
                prepared_path,
            )
        )
        try:
            protocol_record = json.loads(
                protocol_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _error(
                f"{code_prefix}_PROTOCOL_RECORD_INVALID",
                "运行结束后无法解析冻结的 AD4Zn 协议记录，结果已拒绝。",
                raw_error=str(exc),
            )
        prepared_issue = _ad4zn_prepared_receptor_issue(prepared_path)
        parameter_issue = _ad4zn_parameter_reference_issue(parameter_path)
        (
            verified_ad4zn_box_coverage,
            box_coverage_issue,
        ) = _ad4zn_frozen_box_coverage(
            prepared_path,
            frozen_manifest,
        )
        missing_gpf_lines = _ad4zn_gpf_missing_lines(gpf_path)
        if (
            str(frozen_ad4zn.get("protocol_id") or "")
            != AD4ZN_PROTOCOL_ID
            or protocol_record != frozen_ad4zn
            or str(
                frozen_receptor.get("original_source_sha256") or ""
            ).lower()
            != _sha256_file(original_path).lower()
            or str(frozen_parameter.get("sha256") or "").lower()
            != _sha256_file(parameter_path).lower()
            or str(frozen_gpf.get("sha256") or "").lower()
            != _sha256_file(gpf_path).lower()
            or not _autogrid_version_supports_ad4zn(
                frozen_autogrid.get("version")
            )
            or frozen_log_summary.get("successful_completion") is not True
            or prepared_issue
            or parameter_issue
            or box_coverage_issue
            or missing_gpf_lines
        ):
            details = [
                *([prepared_issue] if prepared_issue else []),
                *([parameter_issue] if parameter_issue else []),
                *([box_coverage_issue] if box_coverage_issue else []),
                *(
                    ["GPF 缺少：" + ", ".join(missing_gpf_lines)]
                    if missing_gpf_lines
                    else []
                ),
            ]
            return _error(
                f"{code_prefix}_PROTOCOL_BINDING_MISMATCH",
                "运行结束后 AD4Zn 的 ZN/TZ Box 覆盖、官方参数文件、GPF、AutoGrid 证据或人工复核记录不一致，结果已拒绝。",
                raw_error="；".join(details),
            )
    return {
        "ok": True,
        "status": "verified",
        "checked_at": _now_iso(),
        "verified": verified,
        **(
            {
                "ad4zn_box_coverage": copy.deepcopy(
                    verified_ad4zn_box_coverage
                )
            }
            if verified_ad4zn_box_coverage is not None
            else {}
        ),
        **(
            {
                "hydrated_grid_coverage": copy.deepcopy(
                    verified_hydrated_grid_coverage
                )
            }
            if verified_hydrated_grid_coverage is not None
            else {}
        ),
        "error": None,
    }


def _validate_maps_post_run_integrity(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    if _metadata_scoring_protocol(metadata) == "ad4_maps":
        return _validate_ad4_maps_post_run_integrity(
            project_dir,
            run_id,
            metadata,
        )
    return _validate_vina_maps_post_run_integrity(
        project_dir,
        run_id,
        metadata,
    )


def get_run_files_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
    run_mode = _metadata_run_mode(metadata)
    protocol_id = _metadata_protocol_id(metadata)
    files = [
        _file_status(project_path, _metadata_relative_path(run_id), "metadata", "metadata.json"),
        _file_status(
            project_path,
            Path("runs", run_id, "config_snapshot.txt").as_posix(),
            "config_snapshot",
            "config_snapshot.txt",
        ),
        _file_status(
            project_path,
            Path("runs", run_id, "stdout.txt").as_posix(),
            "stdout",
            "stdout.txt",
        ),
        _file_status(
            project_path,
            Path("runs", run_id, "stderr.txt").as_posix(),
            "stderr",
            "stderr.txt",
        ),
        _file_status(project_path, Path("runs", run_id, "log.txt").as_posix(), "log", "log.txt"),
    ]
    if _local_only_execution_plan_enabled(metadata):
        baseline_files = _local_only_baseline_files(run_id)
        files.extend(
            [
                _file_status(
                    project_path,
                    baseline_files["stdout_file"],
                    "baseline_stdout",
                    "baseline_stdout.txt",
                ),
                _file_status(
                    project_path,
                    baseline_files["stderr_file"],
                    "baseline_stderr",
                    "baseline_stderr.txt",
                ),
                _file_status(
                    project_path,
                    baseline_files["log_file"],
                    "baseline_log",
                    "baseline_log.txt",
                ),
            ]
        )
    output_file = _run_output_file(run_id, run_mode)
    if output_file:
        output_name = "optimized.pdbqt" if run_mode == "local_only" else "out.pdbqt"
        files.append(_file_status(project_path, output_file, "out", output_name))
        output_normalization = (
            metadata.get("output_normalization")
            if isinstance(metadata.get("output_normalization"), dict)
            else {}
        )
        raw_output_file = _vina_raw_output_relative_path(output_file)
        if (
            str(output_normalization.get("raw_output_file") or "")
            == raw_output_file
        ):
            files.append(
                _file_status(
                    project_path,
                    raw_output_file,
                    "out_vina_raw",
                    Path(raw_output_file).name,
                )
            )
    if _is_flexible_movement_run(metadata):
        files.append(
            _file_status(
                project_path,
                _flexible_movement_relative_path(run_id),
                "flexible_movement",
                FLEXIBLE_MOVEMENT_FILENAME,
            )
        )
    if run_mode != "dock":
        files.append(
            _file_status(
                project_path,
                _evaluation_relative_path(run_id),
                "evaluation",
                "evaluation.json",
            )
        )
    if _metadata_scoring_protocol(metadata) == "ad4_maps":
        files.append(
            _file_status(
                project_path,
                Path("runs", run_id, "inputs", "maps", "manifest.json").as_posix(),
                (
                    "ad4zn_maps_manifest"
                    if protocol_id == AD4ZN_PROTOCOL_ID
                    else "hydrated_maps_manifest"
                    if protocol_id == HYDRATED_PROTOCOL_ID
                    else "ad4_maps_manifest"
                ),
                (
                    "AutoDock4Zn beta maps manifest.json"
                    if protocol_id == AD4ZN_PROTOCOL_ID
                    else "水合 AD4 maps manifest.json"
                    if protocol_id == HYDRATED_PROTOCOL_ID
                    else "AutoDock4 maps manifest.json"
                ),
            )
        )
        if protocol_id == AD4ZN_PROTOCOL_ID:
            for key, filename, label in (
                (
                    "ad4zn_original_receptor",
                    "original_receptor.pdbqt",
                    "AD4Zn 原始受体",
                ),
                ("ad4zn_parameter_file", "AD4Zn.dat", "AD4Zn.dat"),
                ("ad4zn_gpf", "receptor.gpf", "AD4Zn GPF"),
                (
                    "ad4zn_protocol_record",
                    "protocol_record.json",
                    "AD4Zn 协议记录",
                ),
            ):
                files.append(
                    _file_status(
                        project_path,
                        Path(
                            "runs",
                            run_id,
                            "inputs",
                            "ad4zn",
                            filename,
                        ).as_posix(),
                        key,
                        label,
                    )
                )
        elif protocol_id == HYDRATED_PROTOCOL_ID:
            for key, filename, label in (
                (
                    "hydrated_retained",
                    HYDRATED_RETAINED_OUTPUT_NAME,
                    "保留强/弱水的构象",
                ),
                (
                    "hydrated_water_free",
                    HYDRATED_WATER_FREE_OUTPUT_NAME,
                    "完全去水配体构象",
                ),
                (
                    "hydrated_waters_manifest",
                    HYDRATED_WATERS_MANIFEST_NAME,
                    "水分子分类记录",
                ),
            ):
                files.append(
                    _file_status(
                        project_path,
                        Path("runs", run_id, filename).as_posix(),
                        key,
                        label,
                    )
                )
    elif _metadata_grid_source(metadata) == "precomputed_maps":
        files.append(
            _file_status(
                project_path,
                Path(
                    "runs",
                    run_id,
                    "inputs",
                    "maps",
                    "manifest.json",
                ).as_posix(),
                "vina_maps_manifest",
                "Vina/Vinardo maps manifest.json",
            )
        )

    loaded = load_project(project_dir)
    project = loaded.get("project") if loaded.get("ok") else None
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project,
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "files": files,
        "message": "运行文件状态已读取。",
        "error": None,
    }


def _tail_text(path: Path, *, max_bytes: int = 65536, max_lines: int = 80) -> str:
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            content = handle.read().decode("utf-8", errors="replace")
        return "\n".join(content.splitlines()[-max_lines:])
    except OSError:
        return ""


def _safe_fixed_run_artifact(project_path: Path, run_id: str, filename: str) -> tuple[Path | None, str]:
    try:
        run_dir = _safe_run_directory(project_path, run_id)
    except Exception as exc:  # noqa: BLE001 - return a safe empty tail.
        return None, f"run 目录不安全：{exc}"
    candidate = run_dir / filename
    if candidate.is_symlink():
        return None, f"{filename} 不能是符号链接"
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(project_path)
    except (OSError, ValueError) as exc:
        return None, f"{filename} 固定路径越出项目目录：{exc}"
    if resolved.parent != run_dir or resolved != candidate.absolute():
        return None, f"{filename} 被重解析到本次 run 之外"
    return resolved, ""


def get_run_runtime_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser().resolve()
    status = str(metadata.get("status") or "unknown")
    stage = str(metadata.get("stage") or status)
    process_active = False
    identity_error = ""
    summary_sync_error: dict[str, Any] | None = None
    elapsed = metadata.get("duration_seconds")
    if not isinstance(elapsed, (int, float)):
        elapsed = _duration_seconds(metadata.get("started_at"), metadata.get("finished_at"))

    executor_active = False
    if status == "running":
        pid = metadata.get("pid")
        launch_grace = (
            stage in {"starting", "baseline_starting", "local_starting"}
            and not isinstance(pid, int)
            and isinstance(elapsed, (int, float))
            and elapsed < 10
        )
        verification = _verify_metadata_process(
            metadata,
            pid_key="pid",
            executable_key="trusted_executable",
            identity_key="process_identity",
        )
        executor_verification = _verify_metadata_process(
            metadata,
            pid_key="executor_pid",
            executable_key="executor_executable",
            identity_key="executor_identity",
        )
        process_active = bool(verification.get("ok"))
        executor_active = bool(executor_verification.get("ok"))
        between_composite_stages = bool(
            _local_only_execution_plan_enabled(metadata)
            and stage in {"baseline_scoring", "baseline_recorded", "local_starting"}
            and executor_active
        )
        hydrated_postprocessing = bool(
            _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
            and stage == "postprocessing"
            and executor_active
        )

        if process_active and metadata.get("process_missing_since"):
            observed_probe = metadata.get("process_missing_since")

            def clear_missing_probe(current: dict[str, Any]) -> dict[str, Any]:
                if (
                    current.get("status") == "running"
                    and current.get("process_missing_since") == observed_probe
                    and _verify_metadata_process(
                        current,
                        pid_key="pid",
                        executable_key="trusted_executable",
                        identity_key="process_identity",
                    ).get("ok")
                ):
                    current.pop("process_missing_since", None)
                return current

            cleared, clear_error = _update_run_metadata_transaction(project_dir, run_id, clear_missing_probe)
            if clear_error:
                return clear_error
            assert cleared is not None
            metadata = cleared

        if (
            not process_active
            and not launch_grace
            and not between_composite_stages
            and not hydrated_postprocessing
        ):
            identity_error = str(verification.get("message") or "没有找到可验证的 Vina 进程身份。")
            observed_probe = metadata.get("process_missing_since")
            if not observed_probe:
                detected_at = _now_iso()

                def record_missing_probe(current: dict[str, Any]) -> dict[str, Any]:
                    if current.get("status") != "running" or current.get("process_missing_since"):
                        return current
                    child_now = _verify_metadata_process(
                        current,
                        pid_key="pid",
                        executable_key="trusted_executable",
                        identity_key="process_identity",
                    )
                    if not child_now.get("ok"):
                        current["process_missing_since"] = detected_at
                    return current

                probed, probe_error = _update_run_metadata_transaction(project_dir, run_id, record_missing_probe)
                if probe_error:
                    return probe_error
                assert probed is not None
                metadata = probed
            else:
                marker_exists = _cancel_marker_path(project_path, run_id).exists()
                terminal_status = "cancelled" if marker_exists else "interrupted"
                finished_at = _now_iso()

                def converge(current: dict[str, Any]) -> dict[str, Any]:
                    if (
                        current.get("status") != "running"
                        or current.get("process_missing_since") != observed_probe
                    ):
                        return current
                    child_now = _verify_metadata_process(
                        current,
                        pid_key="pid",
                        executable_key="trusted_executable",
                        identity_key="process_identity",
                    )
                    executor_now = _verify_metadata_process(
                        current,
                        pid_key="executor_pid",
                        executable_key="executor_executable",
                        identity_key="executor_identity",
                    )
                    if child_now.get("ok"):
                        current.pop("process_missing_since", None)
                        return current
                    if executor_now.get("ok"):
                        return current
                    current.update(
                        {
                            "status": terminal_status,
                            "stage": terminal_status,
                            "finished_at": finished_at,
                            "duration_seconds": _duration_seconds(current.get("started_at"), finished_at),
                            "progress": {
                                "percent": int((current.get("progress") or {}).get("percent") or 0),
                                "message": "运行已取消。" if terminal_status == "cancelled" else "Vina 进程意外中断。",
                            },
                        },
                    )
                    current.pop("process_missing_since", None)
                    if terminal_status == "interrupted":
                        current["error_message"] = identity_error
                    else:
                        current.pop("error_message", None)
                    return current

                converged, transaction_error = _update_run_metadata_transaction(project_dir, run_id, converge)
                if transaction_error:
                    return transaction_error
                assert converged is not None
                metadata = converged

            status = str(metadata.get("status") or status)
            stage = str(metadata.get("stage") or status)
            elapsed = metadata.get("duration_seconds")
            process_active = bool(
                _verify_metadata_process(
                    metadata,
                    pid_key="pid",
                    executable_key="trusted_executable",
                    identity_key="process_identity",
                ).get("ok")
            ) if status == "running" else False
            executor_active = bool(
                _verify_metadata_process(
                    metadata,
                    pid_key="executor_pid",
                    executable_key="executor_executable",
                    identity_key="executor_identity",
                ).get("ok")
            ) if status == "running" else False

            if status != "running":
                summary_update = update_project_run_summary(
                    project_dir,
                    run_id,
                    {
                        "status": status,
                        "stage": stage,
                        "finished_at": metadata.get("finished_at"),
                        "duration_seconds": elapsed,
                        "exit_code": metadata.get("exit_code"),
                    },
                )
                if not summary_update.get("ok"):
                    summary_sync_error = summary_update.get("error") or {"message": "project.json run 摘要同步失败。"}

    progress = metadata.get("progress") if isinstance(metadata.get("progress"), dict) else {}
    if status == "running" and metadata.get("process_missing_since"):
        progress = {
            "percent": int(progress.get("percent") or 0),
            "message": "Vina 进程已退出，正在等待运行记录收尾。",
        }
    if not progress and status == "finished":
        progress = {"percent": 100, "message": "AutoDock Vina 已成功完成。"}
    message_by_status = {
        "prepared": "运行记录已准备，尚未启动 Vina。",
        "running": "AutoDock Vina 正在运行。",
        "finished": "AutoDock Vina 已成功完成。",
        "failed": "AutoDock Vina 运行失败，请查看日志。",
        "cancelled": "AutoDock Vina 运行已取消。",
        "interrupted": "Vina 进程已中断，请检查日志后重新准备运行。",
    }
    tail_paths: dict[str, Path | None] = {}
    tail_path_errors: list[str] = []
    baseline_tail = bool(
        _local_only_execution_plan_enabled(metadata)
        and stage in {
            "baseline_starting",
            "baseline_scoring",
            "baseline_recorded",
        }
    )
    tail_filenames = (
        (
            ("stdout", "baseline_stdout.txt"),
            ("stderr", "baseline_stderr.txt"),
            ("log", "baseline_log.txt"),
        )
        if baseline_tail
        else (("stdout", "stdout.txt"), ("stderr", "stderr.txt"), ("log", "log.txt"))
    )
    for key, filename in tail_filenames:
        path, path_error = _safe_fixed_run_artifact(project_path, run_id, filename)
        tail_paths[key] = path
        if path_error:
            tail_path_errors.append(path_error)
    if tail_path_errors and summary_sync_error is None:
        summary_sync_error = {
            "code": "RUN_LOG_PATH_UNSAFE",
            "message": "运行日志固定路径不安全，已拒绝读取。",
            "raw_error": "; ".join(tail_path_errors),
        }

    loaded = load_project(project_dir)
    return {
        "ok": summary_sync_error is None,
        "project": loaded.get("project") if loaded.get("ok") else None,
        "project_dir": str(project_path),
        "run_id": run_id,
        "metadata": metadata,
        "progress": {
            "percent": int(progress.get("percent") or 0),
            "message": str(progress.get("message") or message_by_status.get(status, "运行状态已读取。")),
        },
        "stage": stage,
        "elapsed_seconds": elapsed,
        "process_active": process_active,
        "executor_active": executor_active,
        "stdout_tail": _tail_text(tail_paths["stdout"]) if tail_paths["stdout"] is not None else "",
        "stderr_tail": _tail_text(tail_paths["stderr"]) if tail_paths["stderr"] is not None else "",
        "log_tail": _tail_text(tail_paths["log"]) if tail_paths["log"] is not None else "",
        "message": message_by_status.get(status, "运行状态已读取。"),
        "error": summary_sync_error,
    }


_RUN_SUMMARY_KEYS = (
    "status",
    "stage",
    "protocol_id",
    "protocol_name",
    "member_count",
    "members",
    "run_mode",
    "autobox",
    "scoring_protocol",
    "scoring_function",
    "created_at",
    "started_at",
    "vina_finished_at",
    "finished_at",
    "duration_seconds",
    "exit_code",
    "best_affinity",
    "primary_score_kcal_mol",
    "baseline_score_kcal_mol",
    "score_change_kcal_mol",
    "score_change_definition",
    "comparison_available",
    "output_file",
    "pose_file",
    "hydrated_postprocess",
    "log_file",
    "scores_file",
    "evaluation_file",
    "analyzed_at",
    "report_file",
    "project_report_file",
    "reported_at",
)


def _run_summary_from_metadata(run_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "run_id": run_id,
        "metadata_file": _metadata_relative_path(run_id),
    }
    for key in _RUN_SUMMARY_KEYS:
        if key in metadata:
            summary[key] = copy.deepcopy(metadata[key])
    return summary


def _recover_run_metadata(project_root: Path, run_id: str) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
    metadata, error = _read_run_metadata(str(project_root), run_id)
    if error or metadata is None:
        return None, False, error
    changed = False
    if str(metadata.get("status") or "") == "running":
        elapsed = _duration_seconds(metadata.get("started_at"))
        launch_grace = (
            str(metadata.get("stage") or "")
            in {"starting", "baseline_starting", "local_starting"}
            and not isinstance(metadata.get("pid"), int)
            and isinstance(elapsed, (int, float))
            and elapsed < 10
        )
        first_child = _verify_metadata_process(
            metadata,
            pid_key="pid",
            executable_key="trusted_executable",
            identity_key="process_identity",
        )
        first_executor = _verify_metadata_process(
            metadata,
            pid_key="executor_pid",
            executable_key="executor_executable",
            identity_key="executor_identity",
        )
        is_dead_hydrated_postprocess = bool(
            _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
            and str(metadata.get("stage") or "") == "postprocessing"
            and not first_child.get("ok")
            and not first_executor.get("ok")
        )
        if is_dead_hydrated_postprocess:
            finished_at = _now_iso()

            def fail_interrupted_hydrated_postprocess(
                current: dict[str, Any],
            ) -> dict[str, Any]:
                if (
                    current.get("status") != "running"
                    or str(current.get("stage") or "") != "postprocessing"
                ):
                    return current
                child_now = _verify_metadata_process(
                    current,
                    pid_key="pid",
                    executable_key="trusted_executable",
                    identity_key="process_identity",
                )
                executor_now = _verify_metadata_process(
                    current,
                    pid_key="executor_pid",
                    executable_key="executor_executable",
                    identity_key="executor_identity",
                )
                if child_now.get("ok") or executor_now.get("ok"):
                    return current
                postprocess_error = {
                    "code": "HYDRATED_POSTPROCESS_INTERRUPTED",
                    "message": "水合结果后处理执行器意外退出，原始 Vina 输出已保留。",
                    "raw_error": (
                        "恢复检查确认 Vina 与 DockStart 执行器均已退出，"
                        "无法证明派生结果完整。"
                    ),
                    "suggestion": (
                        "请保留本次 run 作为审计记录，并从已验证的水合输入"
                        "重新准备新的 run。"
                    ),
                }
                current.update(
                    {
                        "status": "failed",
                        "stage": "postprocess_failed",
                        "finished_at": finished_at,
                        "duration_seconds": _duration_seconds(
                            current.get("started_at"),
                            finished_at,
                        ),
                        "progress": {
                            "percent": 100,
                            "message": postprocess_error["message"],
                        },
                        "hydrated_postprocess": {
                            "status": "failed",
                            "finished_at": finished_at,
                            "raw_output_file": str(
                                current.get("output_file")
                                or Path(
                                    "runs",
                                    run_id,
                                    "out.pdbqt",
                                ).as_posix()
                            ),
                            "error": copy.deepcopy(postprocess_error),
                        },
                        "error_message": postprocess_error["message"],
                    },
                )
                current.pop("process_missing_since", None)
                return current

            recovered, transaction_error = _update_run_metadata_transaction(
                str(project_root),
                run_id,
                fail_interrupted_hydrated_postprocess,
            )
            if transaction_error:
                return metadata, False, transaction_error
            assert recovered is not None
            changed = recovered != metadata
            metadata = recovered
        elif (
            not launch_grace
            and not first_child.get("ok")
            and not first_executor.get("ok")
        ):
            finished_at = _now_iso()

            def interrupt_dead_run(current: dict[str, Any]) -> dict[str, Any]:
                if current.get("status") != "running":
                    return current
                child_now = _verify_metadata_process(
                    current,
                    pid_key="pid",
                    executable_key="trusted_executable",
                    identity_key="process_identity",
                )
                executor_now = _verify_metadata_process(
                    current,
                    pid_key="executor_pid",
                    executable_key="executor_executable",
                    identity_key="executor_identity",
                )
                if child_now.get("ok") or executor_now.get("ok"):
                    return current
                phases = (
                    copy.deepcopy(current.get("execution_phases"))
                    if isinstance(current.get("execution_phases"), dict)
                    else {}
                )
                for phase_id, phase_value in list(phases.items()):
                    if not isinstance(phase_value, dict):
                        continue
                    if str(phase_value.get("status") or "") in {"starting", "running"}:
                        phases[phase_id] = {
                            **phase_value,
                            "status": "interrupted",
                            "finished_at": finished_at,
                            "reason": "应用或运行进程异常退出。",
                        }
                current.update(
                    {
                        "status": "interrupted",
                        "stage": "interrupted",
                        "finished_at": finished_at,
                        "duration_seconds": _duration_seconds(current.get("started_at"), finished_at),
                        "progress": {
                            "percent": int((current.get("progress") or {}).get("percent") or 0),
                            "message": "DockStart 恢复检查确认 Vina 与执行器均已退出。",
                        },
                        "error_message": "应用或运行进程异常退出，本次 run 已标记为 interrupted。",
                        **({"execution_phases": phases} if phases else {}),
                    },
                )
                current.pop("process_missing_since", None)
                return current

            recovered, transaction_error = _update_run_metadata_transaction(
                str(project_root),
                run_id,
                interrupt_dead_run,
            )
            if transaction_error:
                return metadata, False, transaction_error
            assert recovered is not None
            changed = recovered != metadata
            metadata = recovered

    if str(metadata.get("status") or "") in {"finished", "failed", "cancelled", "interrupted"}:
        run_mode = _metadata_run_mode(metadata)
        is_multiple_ligand = (
            _metadata_protocol_id(metadata)
            == MULTIPLE_LIGAND_PROTOCOL_ID
        )
        is_hydrated = (
            _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
        )
        existing_artifacts = (
            metadata.get("artifacts")
            if isinstance(metadata.get("artifacts"), dict)
            else {}
        )
        output_artifact_key = "out"
        if is_multiple_ligand:
            output_artifact_key = (
                "output"
                if "output" in existing_artifacts
                else "out"
                if "out" in existing_artifacts
                else "output"
            )
        artifact_paths = {
            "log": ("log.txt", Path("runs", run_id, "log.txt").as_posix()),
            "stdout": ("stdout.txt", Path("runs", run_id, "stdout.txt").as_posix()),
            "stderr": ("stderr.txt", Path("runs", run_id, "stderr.txt").as_posix()),
            **(
                {
                    output_artifact_key: (
                        "optimized.pdbqt" if run_mode == "local_only" else "out.pdbqt",
                        _run_output_file(run_id, run_mode),
                    )
                }
                if _run_output_file(run_id, run_mode)
                else {}
            ),
            **(
                {"evaluation": ("evaluation.json", _evaluation_relative_path(run_id))}
                if run_mode != "dock"
                else {}
            ),
            **(
                {
                    "baseline_log": (
                        "baseline_log.txt",
                        _local_only_baseline_files(run_id)["log_file"],
                    ),
                    "baseline_stdout": (
                        "baseline_stdout.txt",
                        _local_only_baseline_files(run_id)["stdout_file"],
                    ),
                    "baseline_stderr": (
                        "baseline_stderr.txt",
                        _local_only_baseline_files(run_id)["stderr_file"],
                    ),
                }
                if _local_only_execution_plan_enabled(metadata)
                else {}
            ),
            **(
                {
                    "hydrated_retained": (
                        HYDRATED_RETAINED_OUTPUT_NAME,
                        Path(
                            "runs",
                            run_id,
                            HYDRATED_RETAINED_OUTPUT_NAME,
                        ).as_posix(),
                    ),
                    "hydrated_water_free": (
                        HYDRATED_WATER_FREE_OUTPUT_NAME,
                        Path(
                            "runs",
                            run_id,
                            HYDRATED_WATER_FREE_OUTPUT_NAME,
                        ).as_posix(),
                    ),
                    "hydrated_waters_manifest": (
                        HYDRATED_WATERS_MANIFEST_NAME,
                        Path(
                            "runs",
                            run_id,
                            HYDRATED_WATERS_MANIFEST_NAME,
                        ).as_posix(),
                    ),
                }
                if is_hydrated
                else {}
            ),
        }
        output_normalization = (
            metadata.get("output_normalization")
            if isinstance(metadata.get("output_normalization"), dict)
            else {}
        )
        normalized_output_file = _run_output_file(run_id, run_mode)
        expected_raw_output_file = (
            _vina_raw_output_relative_path(normalized_output_file)
            if normalized_output_file
            else ""
        )
        if (
            expected_raw_output_file
            and str(output_normalization.get("raw_output_file") or "")
            == expected_raw_output_file
        ):
            artifact_paths["out_vina_raw"] = (
                Path(expected_raw_output_file).name,
                expected_raw_output_file,
            )
        snapshots: dict[str, dict[str, Any]] = {}
        for key, (filename, relative) in artifact_paths.items():
            existing = existing_artifacts.get(key) if isinstance(existing_artifacts.get(key), dict) else {}
            if re.fullmatch(r"[0-9a-fA-F]{64}", str(existing.get("sha256") or "")):
                continue
            path, path_error = _safe_fixed_run_artifact(project_root, run_id, filename)
            if path is not None and not path_error:
                snapshots[key] = _hash_snapshot(path, relative)
        vina_path = str(
            (metadata.get("execution_vina") or {}).get("path")
            if isinstance(metadata.get("execution_vina"), dict)
            else metadata.get("vina_path") or ""
        )
        existing_vina = (
            existing_artifacts.get("vina_binary_executed")
            if isinstance(existing_artifacts.get("vina_binary_executed"), dict)
            else {}
        )
        if vina_path and not re.fullmatch(r"[0-9a-fA-F]{64}", str(existing_vina.get("sha256") or "")):
            execution_vina = (
                metadata.get("execution_vina")
                if isinstance(metadata.get("execution_vina"), dict)
                else {}
            )
            execution_sha256 = str(
                execution_vina.get("sha256") or ""
            ).lower()
            execution_size = execution_vina.get("size_bytes")
            if (
                is_multiple_ligand
                and SHA256_PATTERN.fullmatch(execution_sha256) is not None
                and not isinstance(execution_size, bool)
                and isinstance(execution_size, int)
                and execution_size > 0
            ):
                snapshots["vina_binary_executed"] = {
                    **copy.deepcopy(existing_vina),
                    "relative_path": "",
                    "absolute_path": str(
                        existing_vina.get("absolute_path")
                        or execution_vina.get("path")
                        or vina_path
                    ),
                    "exists": True,
                    "size_bytes": execution_size,
                    "sha256": execution_sha256,
                    "hash_error": "",
                    "verification_status": "verified",
                    "recorded_at_execution": True,
                    "version": str(execution_vina.get("version") or ""),
                    "source": str(execution_vina.get("source") or ""),
                }
            else:
                # Hashing the executable currently found at this path cannot
                # prove which bytes executed for a historical run. Record the
                # gap explicitly instead of manufacturing false provenance.
                snapshots["vina_binary_executed"] = {
                    **copy.deepcopy(existing_vina),
                    "absolute_path": str(existing_vina.get("absolute_path") or vina_path),
                    "sha256": "",
                    "verification_status": "unknown",
                    "backfilled_unverified": True,
                    "message": "历史 run 未记录执行时 Vina 哈希，当前文件不能用于回填。",
                }

        if not snapshots:
            return metadata, changed, None

        def enrich_artifacts(current: dict[str, Any]) -> dict[str, Any]:
            _with_artifact_hashes(current, snapshots)
            return current

        enriched, transaction_error = _update_run_metadata_transaction(
            str(project_root),
            run_id,
            enrich_artifacts,
        )
        if transaction_error:
            return metadata, changed, transaction_error
        assert enriched is not None
        changed = changed or enriched != metadata
        metadata = enriched
    return metadata, changed, None


def _safe_preparation_metadata_path(
    project_root: Path,
    prep_id: str,
    *,
    create_record: bool = False,
) -> Path | None:
    """Resolve a preparation metadata path without following reparse dirs."""

    if not PREPARATION_ID_PATTERN_COMPAT.match(prep_id):
        return None
    preparation_root = project_root / "preparation"
    record_root = preparation_root / prep_id
    metadata_path = record_root / "metadata.json"
    try:
        if create_record:
            preparation_root.mkdir(exist_ok=True)
            record_root.mkdir(exist_ok=True)
        for candidate, expect_dir in ((preparation_root, True), (record_root, True)):
            if not candidate.exists():
                return None
            resolved = candidate.resolve(strict=True)
            if candidate.is_symlink() or resolved != candidate.absolute():
                return None
            if expect_dir and not resolved.is_dir():
                return None
        if metadata_path.exists():
            if not metadata_path.is_file():
                return None
            if metadata_path.is_symlink() or metadata_path.resolve(strict=True) != metadata_path.absolute():
                return None
        elif not create_record:
            return None
    except OSError:
        return None
    return metadata_path


def _recover_preparation_state(
    project_root: Path,
    project: DockStartProject,
) -> tuple[list[str], bool]:
    """Recover preparation state with target serialization and a miss grace."""

    recovered: list[str] = []
    project_changed = False
    for target in ("receptor", "ligand"):
        with _preparation_target_lock(project_root, target):
            # Re-read after acquiring the same target lock used by claim and
            # final publication.  The caller's earlier project snapshot may be
            # stale by the time recovery reaches this target.
            with _project_lock(project_root):
                data, _, _ = _read_and_migrate_project_unlocked(project_root, persist_migration=True)
                authoritative = _project_from_dict(data, project_root)
            prep = getattr(authoritative.preparation, target)
            if prep.status != "running":
                continue

            prep_id = str(prep.prep_id or authoritative.latest_preparation.get(target) or "")
            metadata_path = _safe_preparation_metadata_path(project_root, prep_id, create_record=True)
            metadata: dict[str, Any] = {}
            if metadata_path is not None:
                try:
                    candidate = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(candidate, dict):
                        metadata = candidate
                except (OSError, json.JSONDecodeError):
                    metadata = {}

            metadata_status = str(metadata.get("status") or "")
            if metadata_status in {"finished", "failed", "interrupted"}:
                safe_terminal = metadata_status != "finished"
                if metadata_status == "finished":
                    output_file = str(metadata.get("output_file") or f"prepared/{target}.pdbqt")
                    relative_output = Path(output_file)
                    expected_output = metadata.get("output") if isinstance(metadata.get("output"), dict) else {}
                    expected_hash = str(expected_output.get("sha256") or "")
                    try:
                        if relative_output.is_absolute():
                            raise ValueError("prepared output 必须是项目内相对路径")
                        output_path = (project_root / relative_output).absolute()
                        output_path.relative_to(project_root)
                        safe_terminal = (
                            output_path.is_file()
                            and not output_path.is_symlink()
                            and output_path.resolve(strict=True) == output_path
                            and output_path.stat().st_size > 0
                            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash))
                            and _sha256_file(output_path).lower() == expected_hash.lower()
                        )
                    except (OSError, ValueError):
                        safe_terminal = False
                if safe_terminal:
                    prep.status = metadata_status  # type: ignore[assignment]
                    prep.finished_at = str(metadata.get("finished_at") or "") or None
                    prep.exit_code = metadata.get("exit_code") if isinstance(metadata.get("exit_code"), int) else None
                    prep.error = copy.deepcopy(metadata.get("error")) if isinstance(metadata.get("error"), dict) else None
                    setattr(project.preparation, target, copy.deepcopy(prep))
                    project.latest_preparation[target] = authoritative.latest_preparation.get(target, prep_id)
                    if metadata_status == "finished":
                        getattr(project, target).file = str(
                            metadata.get("output_file") or f"prepared/{target}.pdbqt",
                        )
                    project_changed = True
                    recovered.append(prep_id or target)
                    continue

            verification = _verify_metadata_process(
                metadata,
                pid_key="executor_pid",
                executable_key="executor_executable",
                identity_key="executor_identity",
            )
            if verification.get("ok"):
                if metadata_path is not None and metadata.get("process_missing_since"):
                    metadata.pop("process_missing_since", None)
                    _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
                continue

            missing_since = str(metadata.get("process_missing_since") or "")
            if not missing_since:
                if metadata_path is not None:
                    if not metadata:
                        metadata = {
                            "prep_id": prep_id,
                            "target": target,
                            "status": "running",
                            "output_file": f"prepared/{target}.pdbqt",
                        }
                    metadata["process_missing_since"] = _now_iso()
                    metadata["process_missing_probe"] = str(verification.get("message") or "执行器身份不可验证。")
                    _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
                # A single missing observation is not enough to kill a task.
                continue

            missing_seconds = _duration_seconds(missing_since)
            if not isinstance(missing_seconds, (int, float)) or missing_seconds < 2.0:
                continue
            verification_again = _verify_metadata_process(
                metadata,
                pid_key="executor_pid",
                executable_key="executor_executable",
                identity_key="executor_identity",
            )
            if verification_again.get("ok"):
                if metadata_path is not None:
                    metadata.pop("process_missing_since", None)
                    metadata.pop("process_missing_probe", None)
                    _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
                continue

            finished_at = _now_iso()
            prep.status = "interrupted"
            prep.finished_at = finished_at
            prep.exit_code = None
            prep.error = {
                "code": "PREPARATION_INTERRUPTED",
                "message": f"{target} 自动准备执行器连续不可验证，本次记录已恢复为 interrupted。",
                "raw_error": str(verification_again.get("message") or "缺少可验证的执行器进程。"),
                "suggestion": "请查看 preparation 记录后重新准备；DockStart 未采用候选输出。",
            }
            setattr(project.preparation, target, copy.deepcopy(prep))
            project.latest_preparation[target] = authoritative.latest_preparation.get(target, prep_id)
            project_changed = True
            recovered.append(prep_id or target)
            if metadata_path is not None and metadata:
                metadata.update(
                    {
                        "status": "interrupted",
                        "finished_at": finished_at,
                        "exit_code": None,
                        "error": copy.deepcopy(prep.error),
                        "published": False,
                    },
                )
                metadata.pop("process_missing_since", None)
                metadata.pop("process_missing_probe", None)
                _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return recovered, project_changed


# Kept local to avoid a circular import from preparation.py.
PREPARATION_ID_PATTERN_COMPAT = re.compile(r"^(receptor|ligand)_(\d{3,})$")


def recover_project_state(project_dir: str) -> dict[str, Any]:
    """Reconcile crash leftovers with authoritative run/preparation metadata."""

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    project_root = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(loaded["project"], project_root)
    recovered_runs: list[str] = []
    recovery_errors: list[dict[str, Any]] = []
    metadata_by_run: dict[str, dict[str, Any]] = {}

    run_ids: set[str] = {
        str(item.get("run_id"))
        for item in project.runs
        if isinstance(item, dict) and RUN_ID_PATTERN.match(str(item.get("run_id") or ""))
    }
    runs_dir = project_root / "runs"
    if runs_dir.is_dir() and not runs_dir.is_symlink():
        run_ids.update(
            child.name
            for child in runs_dir.iterdir()
            if child.is_dir() and not child.is_symlink() and RUN_ID_PATTERN.match(child.name)
        )
    for run_id in sorted(run_ids):
        metadata, changed, error = _recover_run_metadata(project_root, run_id)
        if error:
            error_detail = error.get("error") or {"message": f"{run_id} 恢复失败。"}
            if str(error_detail.get("code") or "") != "RUN_METADATA_NOT_FOUND":
                recovery_errors.append(error_detail)
            continue
        if metadata is None:
            continue
        metadata_by_run[run_id] = metadata
        if changed:
            recovered_runs.append(run_id)

    recovered_preparations, preparation_changed = _recover_preparation_state(project_root, project)

    try:
        with _project_lock(project_root):
            data, _, _ = _read_and_migrate_project_unlocked(project_root, persist_migration=True)
            latest = _project_from_dict(data, project_root)
            summaries = {
                str(item.get("run_id")): item
                for item in latest.runs
                if isinstance(item, dict) and item.get("run_id")
            }
            project_changed = False
            for run_id, metadata in metadata_by_run.items():
                authoritative = _run_summary_from_metadata(run_id, metadata)
                summary = summaries.get(run_id)
                if summary is None:
                    latest.runs.append(authoritative)
                    summaries[run_id] = authoritative
                    project_changed = True
                else:
                    for key, value in authoritative.items():
                        if summary.get(key) != value:
                            summary[key] = copy.deepcopy(value)
                            project_changed = True

            if preparation_changed:
                for target in ("receptor", "ligand"):
                    recovered_prep = getattr(project.preparation, target)
                    current_prep = getattr(latest.preparation, target)
                    if (
                        recovered_prep.status in {"finished", "failed", "interrupted"}
                        and current_prep.status == "running"
                        and current_prep.prep_id == recovered_prep.prep_id
                    ):
                        setattr(latest.preparation, target, recovered_prep)
                        if recovered_prep.status == "finished":
                            getattr(latest, target).file = getattr(project, target).file
                        project_changed = True

            if project_changed:
                latest.updated_at = _now_iso()
                latest.revision += 1
                _write_project_json_unlocked(project_root, latest)
            project = latest
    except Exception as exc:  # noqa: BLE001 - recovery must remain structured.
        return _error(
            "PROJECT_RECOVERY_WRITE_ERROR",
            "恢复运行状态时无法更新 project.json。",
            raw_error=str(exc),
            suggestion="请确认项目目录可写，并保留现有 run/preparation 审计文件。",
        )

    return {
        "ok": not recovery_errors,
        "project_dir": str(project_root),
        "project": project.to_dict(),
        "recovered_runs": sorted(set(recovered_runs)),
        "recovered_preparations": recovered_preparations,
        "recovery_errors": recovery_errors,
        "message": (
            "项目恢复检查完成，已收敛中断记录。"
            if recovered_runs or recovered_preparations
            else "项目恢复检查完成，未发现需要收敛的记录。"
        ),
        "error": recovery_errors[0] if recovery_errors else None,
    }


def cancel_vina_run(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None
    if str(metadata.get("status") or "") != "running":
        return _error(
            "RUN_NOT_RUNNING",
            f"当前 run 状态为 {metadata.get('status') or 'unknown'}，没有可取消的 Vina 进程。",
            suggestion="只能取消 running 状态的运行。",
        )
    if (
        _metadata_protocol_id(metadata) == HYDRATED_PROTOCOL_ID
        and str(metadata.get("stage") or "") == "postprocessing"
    ):
        loaded = load_project(project_dir)
        return {
            "ok": True,
            "accepted": False,
            "cancelled": False,
            "project": loaded.get("project") if loaded.get("ok") else None,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": metadata,
            "stage": "postprocessing",
            "message": (
                "Vina 已完成，DockStart 正在生成水分子分类与派生结构；"
                "该确定性收尾阶段不再终止进程。"
            ),
            "error": None,
        }

    pid = metadata.get("pid")
    requested_at = _now_iso()
    if not isinstance(pid, int) or pid <= 0:
        if (
            _local_only_execution_plan_enabled(metadata)
            and str(metadata.get("stage") or "")
            not in {
                "baseline_starting",
                "baseline_scoring",
                "baseline_recorded",
                "local_starting",
                "cancel_pending",
            }
        ):
            loaded = load_project(project_dir)
            return {
                "ok": True,
                "accepted": False,
                "cancelled": False,
                "project": loaded.get("project") if loaded.get("ok") else None,
                "project_dir": str(Path(project_dir).expanduser()),
                "run_id": run_id,
                "metadata": metadata,
                "stage": metadata.get("stage") or "running",
                "message": "Vina 阶段已结束，DockStart 正在收尾；本次取消未终止任何 PID。",
                "error": None,
            }
        _create_cancel_marker(project_dir, run_id, requested_at)

        def request_pending(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                return current
            progress = current.get("progress") if isinstance(current.get("progress"), dict) else {}
            current.update(
                {
                    "stage": "cancel_pending",
                    "cancel_requested_at": requested_at,
                    "progress": {
                        "percent": int(progress.get("percent") or 0),
                        "message": "取消请求已登记，等待 Vina 进程身份可用。",
                    },
                },
            )
            return current

        pending, transaction_error = _update_run_metadata_transaction(project_dir, run_id, request_pending)
        if transaction_error:
            return transaction_error
        assert pending is not None
        if pending.get("status") != "running":
            runtime = get_run_runtime_status(project_dir, run_id)
            runtime.update(
                {
                    "accepted": False,
                    "cancelled": pending.get("status") == "cancelled",
                    "message": f"运行已进入 {pending.get('status')} 状态，无需继续取消。",
                }
            )
            return runtime
        pending_summary = update_project_run_summary(
            project_dir,
            run_id,
            {
                "status": "running",
                "stage": pending.get("stage") or "cancel_pending",
                "cancel_requested_at": pending.get("cancel_requested_at") or requested_at,
            },
        )
        return {
            "ok": bool(pending_summary.get("ok")),
            "accepted": True,
            "cancelled": False,
            "project": pending_summary.get("project") if pending_summary.get("ok") else None,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": pending,
            "stage": "cancel_pending",
            "message": "取消请求已登记；Vina PID 尚未可验证，正在等待安全终止。",
            "error": None if pending_summary.get("ok") else pending_summary.get("error"),
        }

    trusted_executable = str(metadata.get("trusted_executable") or "")
    recorded_identity = metadata.get("process_identity") if isinstance(metadata.get("process_identity"), dict) else None
    verification = _verify_metadata_process(
        metadata,
        pid_key="pid",
        executable_key="trusted_executable",
        identity_key="process_identity",
    )
    if not verification.get("ok"):
        detected_at = str(metadata.get("process_missing_since") or _now_iso())

        def note_process_exit(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                return current
            child_now = _verify_metadata_process(
                current,
                pid_key="pid",
                executable_key="trusted_executable",
                identity_key="process_identity",
            )
            if child_now.get("ok"):
                current.pop("process_missing_since", None)
                return current
            current.setdefault("process_missing_since", detected_at)
            return current

        settling, transaction_error = _update_run_metadata_transaction(project_dir, run_id, note_process_exit)
        if transaction_error:
            return transaction_error
        assert settling is not None
        if settling.get("status") != "running":
            runtime = get_run_runtime_status(project_dir, run_id)
            runtime.update(
                {
                    "accepted": False,
                    "cancelled": settling.get("status") == "cancelled",
                    "message": f"运行已进入 {settling.get('status')} 状态，无需继续取消。",
                }
            )
            return runtime

        metadata = settling
        pid = metadata.get("pid")
        trusted_executable = str(metadata.get("trusted_executable") or "")
        recorded_identity = metadata.get("process_identity") if isinstance(metadata.get("process_identity"), dict) else None
        verification = _verify_metadata_process(
            metadata,
            pid_key="pid",
            executable_key="trusted_executable",
            identity_key="process_identity",
        )
        if verification.get("ok"):
            # The first verification raced a still-starting process identity;
            # continue through the normal, verified cancellation path.
            pass
        elif _verify_metadata_process(
            metadata,
            pid_key="executor_pid",
            executable_key="executor_executable",
            identity_key="executor_identity",
        ).get("ok"):
            if (
                _local_only_execution_plan_enabled(metadata)
                and str(metadata.get("stage") or "")
                in {
                    "baseline_starting",
                    "baseline_scoring",
                    "baseline_recorded",
                    "local_starting",
                }
            ):
                _create_cancel_marker(project_dir, run_id, requested_at)

                def request_between_stages(current: dict[str, Any]) -> dict[str, Any]:
                    if current.get("status") != "running":
                        return current
                    progress = (
                        current.get("progress")
                        if isinstance(current.get("progress"), dict)
                        else {}
                    )
                    current.update(
                        {
                            "stage": "cancel_pending",
                            "cancel_requested_at": requested_at,
                            "progress": {
                                "percent": int(progress.get("percent") or 0),
                                "message": "取消请求已登记，不再启动局部优化。",
                            },
                        }
                    )
                    current.pop("process_missing_since", None)
                    return current

                pending, pending_error = _update_run_metadata_transaction(
                    project_dir,
                    run_id,
                    request_between_stages,
                )
                if pending_error:
                    return pending_error
                pending_summary = update_project_run_summary(
                    project_dir,
                    run_id,
                    {
                        "status": "running",
                        "stage": "cancel_pending",
                        "cancel_requested_at": requested_at,
                    },
                )
                return {
                    "ok": bool(pending_summary.get("ok")),
                    "accepted": True,
                    "cancelled": False,
                    "project": (
                        pending_summary.get("project")
                        if pending_summary.get("ok")
                        else None
                    ),
                    "project_dir": str(Path(project_dir).expanduser()),
                    "run_id": run_id,
                    "metadata": pending,
                    "stage": "cancel_pending",
                    "message": "取消请求已登记；局部优化阶段不会启动。",
                    "error": (
                        None
                        if pending_summary.get("ok")
                        else pending_summary.get("error")
                    ),
                }
            loaded = load_project(project_dir)
            return {
                "ok": True,
                "accepted": False,
                "cancelled": False,
                "project": loaded.get("project") if loaded.get("ok") else None,
                "project_dir": str(Path(project_dir).expanduser()),
                "run_id": run_id,
                "metadata": metadata,
                "stage": metadata.get("stage") or "running",
                "message": "Vina 进程已退出，DockStart 正在收尾；本次取消未再终止任何 PID。",
                "error": None,
            }
        else:
            payload = _error(
                "VINA_CANCEL_IDENTITY_MISMATCH",
                str(verification.get("message") or "Vina 进程已经退出，无法再执行终止。"),
                suggestion="未终止任何 PID；请刷新运行状态，DockStart 将在再次确认后收敛该 run。",
            )
            payload.update(
                {
                    "project_dir": str(Path(project_dir).expanduser()),
                    "run_id": run_id,
                    "metadata": metadata,
                }
            )
            return payload

    _create_cancel_marker(project_dir, run_id, requested_at)

    def mark_cancelling(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "running":
            return current
        progress = current.get("progress") if isinstance(current.get("progress"), dict) else {}
        current.update(
            {
                "stage": "cancelling",
                "cancel_requested_at": requested_at,
                "progress": {"percent": int(progress.get("percent") or 0), "message": "正在取消 AutoDock Vina。"},
            },
        )
        return current

    cancelling, transaction_error = _update_run_metadata_transaction(project_dir, run_id, mark_cancelling)
    if transaction_error:
        return transaction_error
    termination = vina_adapter.terminate_process(
        pid,
        expected_executable=trusted_executable,
        recorded_identity=recorded_identity,
    )
    if not termination.get("ok"):
        return _error(
            "VINA_CANCEL_FAILED",
            str(termination.get("message") or "无法安全终止 Vina 进程。"),
            str(termination.get("raw_error") or ""),
            "运行未被报告为已取消；请重新检查 runtime 状态。",
        )

    cancelled_at = _now_iso()

    def finalize_cancel(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "running":
            return current
        progress = current.get("progress") if isinstance(current.get("progress"), dict) else {}
        current.update(
            {
                "status": "cancelled",
                "stage": "cancelled",
                "finished_at": cancelled_at,
                "duration_seconds": _duration_seconds(current.get("started_at"), cancelled_at),
                "progress": {"percent": int(progress.get("percent") or 0), "message": "用户已取消运行。"},
            },
        )
        current.pop("error_message", None)
        return current

    cancelled, transaction_error = _update_run_metadata_transaction(project_dir, run_id, finalize_cancel)
    if transaction_error:
        return transaction_error
    assert cancelled is not None
    if cancelled.get("status") != "cancelled":
        return _error(
            "RUN_CANCEL_RACE_TERMINAL",
            f"运行在取消过程中已进入 {cancelled.get('status')} 状态，未覆盖该终态。",
        )
    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "status": "cancelled",
            "stage": "cancelled",
            "finished_at": cancelled.get("finished_at"),
            "duration_seconds": cancelled.get("duration_seconds"),
        },
    )
    runtime = get_run_runtime_status(project_dir, run_id)
    runtime["termination"] = termination
    runtime["message"] = "已取消 AutoDock Vina 运行。"
    if not project_update.get("ok"):
        runtime["ok"] = False
        runtime["error"] = project_update.get("error") or {"message": "project.json run 摘要同步失败。"}
    else:
        runtime["project"] = project_update.get("project")
    return runtime


def _execute_local_only_with_baseline(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
    prerequisites: dict[str, Any],
    detection: Any,
    execution_vina_binary: dict[str, Any],
) -> dict[str, Any]:
    """Execute a new-schema local_only run as score-only + local optimization."""

    project_path = Path(project_dir).expanduser().resolve()
    config_file = str(prerequisites["config_file"])
    output_file = str(prerequisites["output_file"])
    actual_plan = _build_local_only_execution_plan(
        detection.path,
        config_file,
        run_id,
        str(prerequisites.get("flex_file") or ""),
        scoring_protocol=str(prerequisites.get("scoring_protocol") or "vina"),
        maps_prefix=str(prerequisites.get("maps_prefix") or ""),
        autobox=bool(prerequisites.get("autobox")),
    )
    prepared_plan = metadata.get("execution_plan")
    if not isinstance(prepared_plan, dict):
        return _error(
            "LOCAL_ONLY_EXECUTION_PLAN_MISSING",
            "local_only 双阶段执行计划缺失，拒绝改变已准备 run 的语义。",
            suggestion="请重新准备新的 local_only run。",
        )
    prepared_stages = prepared_plan.get("stages")
    actual_stages = actual_plan.get("stages")
    if not isinstance(prepared_stages, list) or not isinstance(actual_stages, list):
        return _error(
            "LOCAL_ONLY_EXECUTION_PLAN_INVALID",
            "local_only 双阶段执行计划结构无效。",
            suggestion="请重新准备新的 local_only run。",
        )
    prepared_by_id = {
        str(stage.get("id") or ""): stage
        for stage in prepared_stages
        if isinstance(stage, dict)
    }
    actual_by_id = {
        str(stage.get("id") or ""): stage
        for stage in actual_stages
        if isinstance(stage, dict)
    }
    if set(prepared_by_id) != {"input_score", "local_optimization"}:
        return _error(
            "LOCAL_ONLY_EXECUTION_PLAN_INVALID",
            "local_only 执行计划必须只包含输入评分和局部优化两个阶段。",
            suggestion="请重新准备新的 local_only run。",
        )
    for stage_id, actual_stage in actual_by_id.items():
        prepared_stage = prepared_by_id.get(stage_id) or {}
        prepared_command = prepared_stage.get("command")
        actual_command = actual_stage.get("command")
        if (
            not isinstance(prepared_command, list)
            or not isinstance(actual_command, list)
            or [str(item) for item in prepared_command[1:]]
            != [str(item) for item in actual_command[1:]]
            or any(
                str(prepared_stage.get(key) or "") != str(actual_stage.get(key) or "")
                for key in ("run_mode", "stdout_file", "stderr_file", "log_file", "output_file")
            )
        ):
            return _error(
                "LOCAL_ONLY_EXECUTION_PLAN_MISMATCH",
                f"local_only 的 {stage_id} 阶段与已冻结执行计划不一致。",
                suggestion="请重新准备新的 local_only run。",
            )

    stage_specs = [
        {
            **copy.deepcopy(actual_by_id["input_score"]),
            "stdout_path": Path(str(prerequisites["baseline_stdout_path"])),
            "stderr_path": Path(str(prerequisites["baseline_stderr_path"])),
            "log_path": Path(str(prerequisites["baseline_log_path"])),
            "output_path": None,
            "starting_stage": "baseline_starting",
            "running_stage": "baseline_scoring",
            "recorded_stage": "baseline_recorded",
            "start_percent": 8,
            "end_percent": 45,
            "artifact_prefix": "baseline",
        },
        {
            **copy.deepcopy(actual_by_id["local_optimization"]),
            "stdout_path": Path(str(prerequisites["stdout_path"])),
            "stderr_path": Path(str(prerequisites["stderr_path"])),
            "log_path": Path(str(prerequisites["log_path"])),
            "output_path": Path(str(prerequisites["output_path"])),
            "starting_stage": "local_starting",
            "running_stage": "local_optimizing",
            "recorded_stage": "local_recorded",
            "start_percent": 55,
            "end_percent": 95,
            "artifact_prefix": "local",
        },
    ]

    started_at = _now_iso()
    launch_token = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    executor_pid = os.getpid()
    executor_identity = vina_adapter.get_process_identity(executor_pid)
    executor_executable = str((executor_identity or {}).get("executable_path") or "")
    if executor_identity is None or not executor_executable:
        return _error(
            "RUN_EXECUTOR_IDENTITY_UNAVAILABLE",
            "无法记录 DockStart 运行执行器的进程身份，已拒绝启动 Vina。",
            suggestion="请重新检查 Python 运行环境后再试；该 run 仍保持 prepared 状态。",
        )

    def mark_running(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "prepared":
            return current
        phases = (
            copy.deepcopy(current.get("execution_phases"))
            if isinstance(current.get("execution_phases"), dict)
            else {}
        )
        for stage_id in ("input_score", "local_optimization"):
            phase = phases.get(stage_id) if isinstance(phases.get(stage_id), dict) else {}
            phases[stage_id] = {**phase, "status": "pending"}
        phases["input_score"]["status"] = "starting"
        current.update(
            {
                "status": "running",
                "stage": "baseline_starting",
                "progress": {"percent": 5, "message": "正在启动输入姿势评分。"},
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
                "trusted_executable": detection.path,
                "executed_command": copy.deepcopy(actual_by_id["local_optimization"]["command"]),
                "executed_commands": [
                    copy.deepcopy(actual_by_id["input_score"]["command"]),
                    copy.deepcopy(actual_by_id["local_optimization"]["command"]),
                ],
                "execution_phases": phases,
                "execution_vina": {
                    "path": detection.path,
                    "version": detection.version,
                    "source": detection.source,
                    "sha256": execution_vina_binary["sha256"],
                    "size_bytes": execution_vina_binary["size_bytes"],
                    "capabilities": copy.deepcopy(detection.capabilities),
                },
                "stdout_file": str(prerequisites["stdout_file"]),
                "stderr_file": str(prerequisites["stderr_file"]),
                "output_file": output_file,
                "log_file": str(prerequisites["log_file"]),
                "baseline_stdout_file": str(prerequisites["baseline_stdout_file"]),
                "baseline_stderr_file": str(prerequisites["baseline_stderr_file"]),
                "baseline_log_file": str(prerequisites["baseline_log_file"]),
                "config_snapshot": config_file,
                "exit_code": None,
                "best_affinity": None,
                "comparison_available": False,
            }
        )
        current.pop("cancel_requested_at", None)
        current.pop("process_missing_since", None)
        current.pop("error_message", None)
        return current

    running_metadata, transaction_error = _update_run_metadata_transaction(
        project_dir,
        run_id,
        mark_running,
    )
    if transaction_error:
        return transaction_error
    assert running_metadata is not None
    if (
        running_metadata.get("status") != "running"
        or running_metadata.get("launch_token") != launch_token
    ):
        return _error("RUN_START_RACE", "run 状态在启动前发生变化，未执行 Vina。")
    running_project_update = update_project_run_summary(
        project_dir,
        run_id,
        {"status": "running", "stage": "baseline_starting", "started_at": started_at},
    )
    if not running_project_update.get("ok"):
        interrupted_at = _now_iso()

        def interrupt_before_spawn(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") == "running":
                current.update(
                    {
                        "status": "interrupted",
                        "stage": "interrupted",
                        "finished_at": interrupted_at,
                        "duration_seconds": _duration_seconds(started_at, interrupted_at),
                        "progress": {
                            "percent": 0,
                            "message": "project.json 摘要同步失败，未启动 Vina。",
                        },
                        "error_message": "project.json run 摘要同步失败。",
                    }
                )
            return current

        _update_run_metadata_transaction(project_dir, run_id, interrupt_before_spawn)
        return running_project_update

    callback_lock = threading.Lock()
    progress_state = {"stdout_chunks": 0, "last_update": 0.0}

    def execute_stage(spec: dict[str, Any]) -> dict[str, Any]:
        stage_id = str(spec["id"])
        run_mode = str(spec["run_mode"])
        stage_started_at = _now_iso()

        def mark_stage_starting(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                return current
            phases = (
                copy.deepcopy(current.get("execution_phases"))
                if isinstance(current.get("execution_phases"), dict)
                else {}
            )
            phase = phases.get(stage_id) if isinstance(phases.get(stage_id), dict) else {}
            phases[stage_id] = {
                **phase,
                "status": "starting",
                "started_at": stage_started_at,
                "command": copy.deepcopy(spec["command"]),
                "run_mode": run_mode,
                "stdout_file": str(spec["stdout_file"]),
                "stderr_file": str(spec["stderr_file"]),
                "log_file": str(spec["log_file"]),
                "output_file": str(spec.get("output_file") or ""),
            }
            current.update(
                {
                    "stage": str(spec["starting_stage"]),
                    "progress": {
                        "percent": int(spec["start_percent"]),
                        "message": (
                            "正在启动输入姿势评分。"
                            if stage_id == "input_score"
                            else "正在启动局部优化。"
                        ),
                    },
                    "pid": None,
                    "process_identity": None,
                    "process_started_at": None,
                    "execution_phases": phases,
                }
            )
            current.pop("process_missing_since", None)
            return current

        _, start_error = _update_run_metadata_transaction(
            project_dir,
            run_id,
            mark_stage_starting,
        )
        if start_error:
            return {
                "ok": False,
                "cancelled": False,
                "error_message": str(start_error.get("error") or start_error),
                "run_result": None,
                "parsed": None,
            }
        if _cancel_marker_path(project_dir, run_id).exists():
            return {
                "ok": False,
                "cancelled": True,
                "error_message": "",
                "run_result": None,
                "parsed": None,
            }

        stage_vina_start = _tool_hash_snapshot(detection.path)

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
                cancel_requested = _cancel_marker_path(project_dir, run_id).exists()
                phases = (
                    copy.deepcopy(current.get("execution_phases"))
                    if isinstance(current.get("execution_phases"), dict)
                    else {}
                )
                phase = phases.get(stage_id) if isinstance(phases.get(stage_id), dict) else {}
                phases[stage_id] = {
                    **phase,
                    "status": "running",
                    "pid": pid,
                    "process_identity": identity,
                    "process_started_at": process_started_at,
                }
                current.update(
                    {
                        "pid": pid,
                        "process_identity": identity,
                        "process_started_at": process_started_at,
                        "stage": "cancelling" if cancel_requested else str(spec["running_stage"]),
                        "progress": {
                            "percent": int(spec["start_percent"]) + 3,
                            "message": (
                                "检测到取消请求，正在终止 Vina。"
                                if cancel_requested
                                else (
                                    "正在计算输入姿势评分。"
                                    if stage_id == "input_score"
                                    else "正在局部优化输入姿势。"
                                )
                            ),
                        },
                        "execution_phases": phases,
                    }
                )
                current.pop("process_missing_since", None)
                return current

            _, callback_error = _update_run_metadata_transaction(
                project_dir,
                run_id,
                record_process,
            )
            if callback_error:
                raise RuntimeError(str(callback_error.get("error") or callback_error))
            if _cancel_marker_path(project_dir, run_id).exists():
                termination = vina_adapter.terminate_process(
                    pid,
                    expected_executable=detection.path,
                    recorded_identity=identity,
                )
                if not termination.get("ok"):
                    raise RuntimeError(
                        str(termination.get("message") or "取消已启动的 Vina 失败。")
                    )

        def on_output(stream_name: str, _chunk: str) -> None:
            if stream_name != "stdout" or _cancel_marker_path(project_dir, run_id).exists():
                return
            with callback_lock:
                progress_state["stdout_chunks"] += 1
                now = time.monotonic()
                if now - progress_state["last_update"] < 0.5:
                    return
                progress_state["last_update"] = now
                span = max(1, int(spec["end_percent"]) - int(spec["start_percent"]) - 5)
                percent = min(
                    int(spec["end_percent"]) - 1,
                    int(spec["start_percent"])
                    + 5
                    + min(span, int(progress_state["stdout_chunks"] / 25)),
                )

                def update_progress(current: dict[str, Any]) -> dict[str, Any]:
                    if (
                        current.get("status") != "running"
                        or _cancel_marker_path(project_dir, run_id).exists()
                    ):
                        return current
                    current.update(
                        {
                            "stage": str(spec["running_stage"]),
                            "progress": {
                                "percent": percent,
                                "message": (
                                    "正在计算输入姿势评分。"
                                    if stage_id == "input_score"
                                    else "正在局部优化输入姿势。"
                                ),
                            },
                        }
                    )
                    return current

                _, progress_error = _update_run_metadata_transaction(
                    project_dir,
                    run_id,
                    update_progress,
                )
                if progress_error:
                    raise RuntimeError(str(progress_error.get("error") or progress_error))

        run_result = vina_adapter.run_managed(
            [str(item) for item in spec["command"]],
            project_path,
            spec["stdout_path"],
            spec["stderr_path"],
            spec["log_path"],
            on_started=on_started,
            on_output=on_output,
        )
        if run_result.error:
            spec["stderr_path"].parent.mkdir(parents=True, exist_ok=True)
            existing_size = (
                spec["stderr_path"].stat().st_size
                if spec["stderr_path"].exists()
                else 0
            )
            with spec["stderr_path"].open("a", encoding="utf-8") as handle:
                if existing_size:
                    handle.write("\n")
                handle.write(run_result.error)

        stage_finished_at = _now_iso()
        log_ok = spec["log_path"].is_file() and spec["log_path"].stat().st_size > 0
        output_path = spec.get("output_path")
        output_exists = bool(
            output_path is not None
            and output_path.is_file()
            and output_path.stat().st_size > 0
        )
        output_normalization: dict[str, Any] | None = None
        output_normalization_error: dict[str, Any] | None = None
        output_normalization_warning = ""
        output_normalization_artifacts: dict[str, dict[str, Any]] = {}
        if output_exists and output_path is not None:
            normalized_output = _normalize_vina_pdbqt_output(
                output_path,
                str(spec["output_file"]),
                allow_flexible_residue_boundary=(
                    _is_flexible_movement_run(metadata)
                ),
            )
            output_normalization = copy.deepcopy(
                normalized_output.get("record") or {}
            )
            output_normalization_error = (
                copy.deepcopy(normalized_output.get("error"))
                if isinstance(normalized_output.get("error"), dict)
                else None
            )
            output_normalization_warning = str(
                normalized_output.get("warning") or ""
            )
            output_normalization_artifacts = copy.deepcopy(
                normalized_output.get("artifacts") or {}
            )
            output_exists = bool(
                normalized_output.get("ok")
                and output_path.is_file()
                and output_path.stat().st_size > 0
            )
        output_ok = bool(output_path is None or output_exists)
        parsed: dict[str, Any] | None = None
        parse_error = ""
        if log_ok:
            parsed = parse_vina_evaluation_text(
                spec["log_path"].read_text(encoding="utf-8", errors="replace"),
                run_mode,
            )
            if not parsed.get("ok"):
                parse_error = str(
                    (parsed.get("error") or {}).get("message")
                    or "能量分解无法解析。"
                )
        stage_vina_end = _tool_hash_snapshot(detection.path)
        initial_hash = str(execution_vina_binary.get("sha256") or "")
        start_hash = str(stage_vina_start.get("sha256") or "")
        end_hash = str(stage_vina_end.get("sha256") or "")
        vina_integrity_ok = bool(
            re.fullmatch(r"[0-9a-fA-F]{64}", initial_hash)
            and re.fullmatch(r"[0-9a-fA-F]{64}", start_hash)
            and re.fullmatch(r"[0-9a-fA-F]{64}", end_hash)
            and initial_hash.lower() == start_hash.lower() == end_hash.lower()
        )
        cancelled = _cancel_marker_path(project_dir, run_id).exists()
        succeeded = bool(
            not cancelled
            and run_result.exit_code == 0
            and not run_result.error
            and log_ok
            and output_ok
            and not parse_error
            and vina_integrity_ok
        )
        if cancelled:
            phase_status = "cancelled"
            error_message = ""
        elif succeeded:
            phase_status = "finished"
            error_message = ""
        elif not vina_integrity_ok:
            phase_status = "failed"
            error_message = (
                "Vina 可执行文件在两阶段比较期间发生变化，已拒绝发布比较结果。"
            )
        elif output_normalization_error is not None:
            phase_status = "failed"
            error_message = str(
                output_normalization_error.get("message")
                or "Vina 输出 PDBQT 未通过文本完整性检查。"
            )
        elif parse_error:
            phase_status = "failed"
            error_message = parse_error
        elif run_result.exit_code == 0 and not log_ok and not run_result.error:
            phase_status = "failed"
            error_message = f"{spec['label']}没有生成非空运行日志。"
        elif run_result.exit_code == 0 and not output_ok and not run_result.error:
            phase_status = "failed"
            error_message = "局部优化没有生成非空 optimized.pdbqt。"
        else:
            phase_status = "failed"
            error_message = f"{spec['label']}执行失败，请查看对应 stderr 和 log。"

        artifact_prefix = str(spec["artifact_prefix"])
        artifacts = {
            (
                "baseline_stdout" if stage_id == "input_score" else "stdout"
            ): _hash_snapshot(spec["stdout_path"], str(spec["stdout_file"])),
            (
                "baseline_stderr" if stage_id == "input_score" else "stderr"
            ): _hash_snapshot(spec["stderr_path"], str(spec["stderr_file"])),
            (
                "baseline_log" if stage_id == "input_score" else "log"
            ): _hash_snapshot(spec["log_path"], str(spec["log_file"])),
            f"vina_{artifact_prefix}_start": stage_vina_start,
            f"vina_{artifact_prefix}_end": stage_vina_end,
            **(
                {"out": _hash_snapshot(output_path, str(spec["output_file"]))}
                if output_path is not None
                else {}
            ),
            **output_normalization_artifacts,
        }

        def record_stage(current: dict[str, Any]) -> dict[str, Any]:
            phases = (
                copy.deepcopy(current.get("execution_phases"))
                if isinstance(current.get("execution_phases"), dict)
                else {}
            )
            phase = phases.get(stage_id) if isinstance(phases.get(stage_id), dict) else {}
            phases[stage_id] = {
                **phase,
                "status": phase_status,
                "started_at": stage_started_at,
                "finished_at": stage_finished_at,
                "duration_seconds": _duration_seconds(stage_started_at, stage_finished_at),
                "exit_code": run_result.exit_code,
                "pid": run_result.pid,
                "primary_score_kcal_mol": (
                    parsed.get("primary_score_kcal_mol")
                    if isinstance(parsed, dict) and parsed.get("ok")
                    else None
                ),
                "energy_terms": (
                    copy.deepcopy(parsed.get("energy_terms") or [])
                    if isinstance(parsed, dict) and parsed.get("ok")
                    else []
                ),
                "vina_binary_integrity": {
                    "initial_sha256": initial_hash,
                    "start_sha256": start_hash,
                    "end_sha256": end_hash,
                    "match": vina_integrity_ok,
                },
                **(
                    {
                        "output_normalization": copy.deepcopy(
                            output_normalization
                        )
                    }
                    if output_normalization is not None
                    else {}
                ),
                **({"error_message": error_message} if error_message else {}),
            }
            current["execution_phases"] = phases
            if output_normalization is not None:
                current["output_normalization"] = copy.deepcopy(
                    output_normalization
                )
            if output_normalization_warning:
                warnings = (
                    list(current.get("warnings") or [])
                    if isinstance(current.get("warnings"), list)
                    else []
                )
                if output_normalization_warning not in warnings:
                    warnings.append(output_normalization_warning)
                current["warnings"] = warnings
            if current.get("status") == "running":
                current.update(
                    {
                        "stage": (
                            str(spec["recorded_stage"])
                            if succeeded
                            else "cancelling"
                            if cancelled
                            else f"{stage_id}_failed"
                        ),
                        "progress": {
                            "percent": int(spec["end_percent"]),
                            "message": (
                                f"{spec['label']}已记录。"
                                if succeeded
                                else "用户已取消运行。"
                                if cancelled
                                else error_message
                            ),
                        },
                        "pid": None,
                        "process_identity": None,
                        "process_started_at": None,
                    }
                )
            current.pop("process_missing_since", None)
            _with_artifact_hashes(current, artifacts)
            return current

        recorded, record_error = _update_run_metadata_transaction(
            project_dir,
            run_id,
            record_stage,
        )
        return {
            "ok": succeeded,
            "cancelled": cancelled,
            "error_message": error_message,
            "structured_error": copy.deepcopy(output_normalization_error),
            "run_result": run_result,
            "parsed": parsed if isinstance(parsed, dict) and parsed.get("ok") else None,
            "metadata": recorded,
            "transaction_error": record_error,
            "vina_start": stage_vina_start,
            "vina_end": stage_vina_end,
        }

    baseline_result = execute_stage(stage_specs[0])
    if baseline_result.get("transaction_error"):
        return baseline_result["transaction_error"]

    if baseline_result.get("ok") and not baseline_result.get("cancelled"):
        def mark_local_starting(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                return current
            phases = (
                copy.deepcopy(current.get("execution_phases"))
                if isinstance(current.get("execution_phases"), dict)
                else {}
            )
            phase = (
                phases.get("local_optimization")
                if isinstance(phases.get("local_optimization"), dict)
                else {}
            )
            phases["local_optimization"] = {**phase, "status": "starting"}
            current.update(
                {
                    "stage": "local_starting",
                    "progress": {
                        "percent": 50,
                        "message": "输入姿势评分已记录，正在准备局部优化。",
                    },
                    "pid": None,
                    "process_identity": None,
                    "process_started_at": None,
                    "execution_phases": phases,
                }
            )
            current.pop("process_missing_since", None)
            return current

        _, transition_error = _update_run_metadata_transaction(
            project_dir,
            run_id,
            mark_local_starting,
        )
        if transition_error:
            return transition_error

    local_result: dict[str, Any] = {
        "ok": False,
        "cancelled": False,
        "error_message": "",
        "run_result": None,
        "parsed": None,
    }
    if (
        baseline_result.get("ok")
        and not baseline_result.get("cancelled")
        and not _cancel_marker_path(project_dir, run_id).exists()
    ):
        local_result = execute_stage(stage_specs[1])
        if local_result.get("transaction_error"):
            return local_result["transaction_error"]

    finished_at = _now_iso()
    latest_metadata, latest_error = _read_run_metadata(project_dir, run_id)
    if latest_error:
        return latest_error
    assert latest_metadata is not None
    already_terminal = str(latest_metadata.get("status") or "") in {
        "finished",
        "failed",
        "cancelled",
        "interrupted",
    }
    cancel_requested = bool(
        baseline_result.get("cancelled")
        or local_result.get("cancelled")
        or _cancel_marker_path(project_dir, run_id).exists()
        or latest_metadata.get("status") == "cancelled"
    )
    succeeded = bool(baseline_result.get("ok") and local_result.get("ok"))
    baseline_score = (
        (baseline_result.get("parsed") or {}).get("primary_score_kcal_mol")
        if isinstance(baseline_result.get("parsed"), dict)
        else None
    )
    optimized_score = (
        (local_result.get("parsed") or {}).get("primary_score_kcal_mol")
        if isinstance(local_result.get("parsed"), dict)
        else None
    )
    score_change = (
        float(optimized_score) - float(baseline_score)
        if isinstance(baseline_score, (int, float))
        and isinstance(optimized_score, (int, float))
        else None
    )
    baseline_run_result = baseline_result.get("run_result")
    local_run_result = local_result.get("run_result")
    exit_code = (
        local_run_result.exit_code
        if local_run_result is not None
        else baseline_run_result.exit_code
        if baseline_run_result is not None
        else None
    )
    failure_message = str(
        local_result.get("error_message")
        or baseline_result.get("error_message")
        or "local_only 双阶段运行失败。"
    )

    def finalize(current: dict[str, Any]) -> dict[str, Any]:
        phases = (
            copy.deepcopy(current.get("execution_phases"))
            if isinstance(current.get("execution_phases"), dict)
            else {}
        )
        if not local_result.get("run_result"):
            local_phase = (
                phases.get("local_optimization")
                if isinstance(phases.get("local_optimization"), dict)
                else {}
            )
            if str(local_phase.get("status") or "") in {"pending", "starting"}:
                phases["local_optimization"] = {
                    **local_phase,
                    "status": "skipped",
                    "reason": (
                        "用户在局部优化开始前取消运行。"
                        if cancel_requested
                        else "输入姿势评分阶段未成功，未启动局部优化。"
                    ),
                }
        if not already_terminal and current.get("status") == "running":
            if cancel_requested:
                final_status = "cancelled"
                message = "用户已取消运行。"
                error_message = ""
                percent = int((current.get("progress") or {}).get("percent") or 0)
            elif succeeded:
                final_status = "finished"
                message = "输入姿势评分与局部优化均已完成。"
                error_message = ""
                percent = 100
            else:
                final_status = "failed"
                message = "local_only 双阶段运行失败。"
                error_message = failure_message
                percent = 100
            current.update(
                {
                    "status": final_status,
                    "stage": final_status,
                    "progress": {"percent": percent, "message": message},
                    "finished_at": finished_at,
                    "duration_seconds": _duration_seconds(
                        current.get("started_at"),
                        finished_at,
                    ),
                    "pid": (
                        local_run_result.pid
                        if local_run_result is not None
                        else baseline_run_result.pid
                        if baseline_run_result is not None
                        else current.get("pid")
                    ),
                    "exit_code": exit_code,
                    "baseline_score_kcal_mol": baseline_score,
                    "primary_score_kcal_mol": optimized_score if succeeded else None,
                    "score_change_kcal_mol": score_change if succeeded else None,
                    "score_change_definition": "optimized_minus_input",
                    "comparison_available": succeeded,
                    "execution_phases": phases,
                }
            )
            if error_message:
                current["error_message"] = error_message
            else:
                current.pop("error_message", None)
        else:
            current["execution_phases"] = phases
        current.pop("process_missing_since", None)
        artifacts = current.get("artifacts") if isinstance(current.get("artifacts"), dict) else {}
        current["output_sha256"] = {
            key: str(snapshot.get("sha256") or "")
            for key, snapshot in artifacts.items()
            if isinstance(snapshot, dict) and not key.startswith("vina_")
        }
        return current

    final_metadata, transaction_error = _update_run_metadata_transaction(
        project_dir,
        run_id,
        finalize,
    )
    if transaction_error:
        return transaction_error
    assert final_metadata is not None
    final_status = str(final_metadata.get("status") or "unknown")
    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "status": final_status,
            "stage": final_metadata.get("stage"),
            "finished_at": final_metadata.get("finished_at"),
            "duration_seconds": final_metadata.get("duration_seconds"),
            "exit_code": final_metadata.get("exit_code"),
            "baseline_score_kcal_mol": final_metadata.get("baseline_score_kcal_mol"),
            "primary_score_kcal_mol": final_metadata.get("primary_score_kcal_mol"),
            "score_change_kcal_mol": final_metadata.get("score_change_kcal_mol"),
            "comparison_available": final_metadata.get("comparison_available"),
        },
    )
    files_status = get_run_files_status(project_dir, run_id)
    message = {
        "finished": "输入姿势评分与局部优化均已完成。",
        "cancelled": "Vina 运行已取消。",
        "failed": str(final_metadata.get("error_message") or "Vina 运行失败。"),
        "interrupted": str(final_metadata.get("error_message") or "Vina 运行已中断。"),
    }.get(final_status, "Vina 运行状态已更新。")
    payload = {
        "ok": final_status in {"finished", "cancelled"} and project_update.get("ok", False),
        "project_dir": str(project_path),
        "project": project_update.get("project") if project_update.get("ok") else None,
        "run_id": run_id,
        "metadata": final_metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "stdout_file": str(prerequisites["stdout_file"]),
        "stderr_file": str(prerequisites["stderr_file"]),
        "output_file": output_file,
        "log_file": str(prerequisites["log_file"]),
        "baseline_stdout_file": str(prerequisites["baseline_stdout_file"]),
        "baseline_stderr_file": str(prerequisites["baseline_stderr_file"]),
        "baseline_log_file": str(prerequisites["baseline_log_file"]),
        "files": files_status.get("files", []),
        "message": message,
        "error": None,
    }
    if not project_update.get("ok"):
        payload["error"] = project_update.get("error") or {
            "code": "RUN_SUMMARY_SYNC_FAILED",
            "message": "project.json run 摘要同步失败。",
        }
    elif final_status in {"failed", "interrupted"}:
        structured_error = (
            local_result.get("structured_error")
            if isinstance(local_result.get("structured_error"), dict)
            else baseline_result.get("structured_error")
            if isinstance(baseline_result.get("structured_error"), dict)
            else None
        )
        stderr_path = (
            Path(str(prerequisites["stderr_path"]))
            if local_run_result is not None
            else Path(str(prerequisites["baseline_stderr_path"]))
        )
        payload["error"] = {
            "code": (
                str(structured_error.get("code") or "")
                if structured_error
                else "VINA_RUN_FAILED"
                if final_status == "failed"
                else "VINA_RUN_INTERRUPTED"
            ),
            "message": message,
            "raw_error": (
                str(structured_error.get("raw_error") or "")
                if structured_error
                else str(local_run_result.error)
                if local_run_result is not None and local_run_result.error
                else str(baseline_run_result.error)
                if baseline_run_result is not None and baseline_run_result.error
                else _tail_text(stderr_path)
            ),
            "suggestion": (
                str(structured_error.get("suggestion") or "")
                if structured_error
                else "请查看 baseline_* 与 local_only 的 stderr/log 文件后重新准备运行。"
            ),
        }
    return payload


def execute_prepared_vina_run(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None
    protocol_id = _metadata_protocol_id(metadata)
    is_hydrated = protocol_id == HYDRATED_PROTOCOL_ID
    status = str(metadata.get("status") or "")
    if status != "prepared":
        return _error(
            "RUN_STATUS_NOT_EXECUTABLE",
            f"当前 run 状态为 {status or 'unknown'}，只能执行 prepared 状态的 run。",
            suggestion="请不要重复执行 running、finished、failed、interrupted 或 cancelled 的 run。",
        )
    if _cancel_marker_path(project_dir, run_id).exists():
        return _error(
            "RUN_CANCEL_MARKER_PRESENT",
            "该 run 已存在取消标记，拒绝启动 Vina。",
            suggestion="请重新准备新的 run。",
        )

    prerequisites = _validate_execute_prerequisites(project_dir, run_id, metadata)
    if not prerequisites.get("ok"):
        return prerequisites

    settings = load_settings()
    detection = vina_adapter.detect(settings.tool_paths.vina)
    if detection.status != "ok" or not detection.path:
        return _error(
            "VINA_NOT_AVAILABLE",
            detection.message or "执行前未检测到可信的 AutoDock Vina。",
            detection.raw_error,
            "请在设置页修复 Vina 路径后重新执行。",
        )
    execution_capability_validation = validate_vina_runtime_capabilities(
        prerequisites.get("effective_vina_options") or {},
        str(prerequisites.get("scoring_protocol") or "vina"),
        detection.capabilities,
        run_mode=str(prerequisites.get("run_mode") or "dock"),
        receptor_mode=(
            "flexible"
            if prerequisites.get("flex_file")
            else "rigid"
        ),
        autobox=bool(prerequisites.get("autobox")),
    )
    if not execution_capability_validation.get("ok"):
        capability_error = execution_capability_validation.get("error") or {}
        return _error(
            "RUN_VINA_CAPABILITY_MISMATCH",
            "当前执行用 AutoDock Vina 不支持本次 run 已冻结的专家选项。",
            raw_error=str(capability_error.get("raw_error") or ""),
            suggestion=(
                "请恢复准备该 run 时使用的兼容 Vina，或保留本次记录并重新准备新 run。"
            ),
        )
    execution_vina_binary = _tool_hash_snapshot(detection.path)
    expected_maps_vina_sha256 = str(
        prerequisites.get("maps_vina_sha256") or ""
    ).lower()
    if expected_maps_vina_sha256 and str(
        execution_vina_binary.get("sha256") or ""
    ).lower() != expected_maps_vina_sha256:
        return _error(
            "RUN_VINA_MAPS_BINARY_MISMATCH",
            "当前 Vina 可执行文件与生成/验证该 maps 快照的二进制不一致，拒绝执行。",
            raw_error=(
                f"expected={expected_maps_vina_sha256}; "
                f"actual={execution_vina_binary.get('sha256') or ''}"
            ),
            suggestion="请恢复生成 maps 时记录的 Vina，或重新生成 maps 并准备新的 run。",
        )
    if _local_only_execution_plan_enabled(metadata):
        return _execute_local_only_with_baseline(
            project_dir,
            run_id,
            metadata,
            prerequisites,
            detection,
            execution_vina_binary,
        )

    project_path = Path(project_dir).expanduser().resolve()
    stdout_file = str(prerequisites["stdout_file"])
    stderr_file = str(prerequisites["stderr_file"])
    log_file = str(prerequisites["log_file"])
    output_file = str(prerequisites["output_file"])
    config_file = str(prerequisites["config_file"])
    run_mode = _normalize_run_mode(prerequisites.get("run_mode"))
    autobox = bool(prerequisites.get("autobox"))
    stdout_path = Path(prerequisites["stdout_path"])
    stderr_path = Path(prerequisites["stderr_path"])
    log_path = Path(prerequisites["log_path"])
    output_path = Path(prerequisites["output_path"]) if prerequisites.get("output_path") else None
    command = _build_vina_command(
        detection.path,
        config_file,
        run_id,
        str(prerequisites.get("flex_file") or ""),
        scoring_protocol=str(prerequisites.get("scoring_protocol") or "vina"),
        maps_prefix=str(prerequisites.get("maps_prefix") or ""),
        grid_source=str(prerequisites.get("grid_source") or "receptor"),
        maps_scoring=str(prerequisites.get("maps_scoring") or "vina"),
        run_mode=run_mode,
        autobox=autobox,
    )
    started_at = _now_iso()
    launch_token = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    executor_pid = os.getpid()
    executor_identity = vina_adapter.get_process_identity(executor_pid)
    executor_executable = str((executor_identity or {}).get("executable_path") or "")
    if executor_identity is None or not executor_executable:
        return _error(
            "RUN_EXECUTOR_IDENTITY_UNAVAILABLE",
            "无法记录 DockStart 运行执行器的进程身份，已拒绝启动 Vina。",
            suggestion="请重新检查 Python 运行环境后再试；该 run 仍保持 prepared 状态。",
        )

    def mark_running(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") != "prepared":
            return current
        current.update(
            {
                "status": "running",
                "stage": "starting",
                "progress": {"percent": 5, "message": "正在启动 AutoDock Vina。"},
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
                "trusted_executable": detection.path,
                "executed_command": command,
                "execution_vina": {
                    "path": detection.path,
                    "version": detection.version,
                    "source": detection.source,
                    "sha256": execution_vina_binary["sha256"],
                    "size_bytes": execution_vina_binary["size_bytes"],
                    "capabilities": copy.deepcopy(detection.capabilities),
                },
                "stdout_file": stdout_file,
                "stderr_file": stderr_file,
                "output_file": output_file,
                "log_file": log_file,
                "config_snapshot": config_file,
                "exit_code": None,
                "best_affinity": None,
            },
        )
        current.pop("cancel_requested_at", None)
        current.pop("process_missing_since", None)
        current.pop("error_message", None)
        return current

    running_metadata, transaction_error = _update_run_metadata_transaction(project_dir, run_id, mark_running)
    if transaction_error:
        return transaction_error
    assert running_metadata is not None
    if running_metadata.get("status") != "running" or running_metadata.get("launch_token") != launch_token:
        return _error("RUN_START_RACE", "run 状态在启动前发生变化，未执行 Vina。")
    running_project_update = update_project_run_summary(
        project_dir,
        run_id,
        {"status": "running", "stage": "starting", "started_at": started_at},
    )
    if not running_project_update.get("ok"):
        interrupted_at = _now_iso()

        def interrupt_before_spawn(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") == "running":
                current.update(
                    {
                        "status": "interrupted",
                        "stage": "interrupted",
                        "finished_at": interrupted_at,
                        "duration_seconds": _duration_seconds(started_at, interrupted_at),
                        "progress": {"percent": 0, "message": "project.json 摘要同步失败，未启动 Vina。"},
                        "error_message": "project.json run 摘要同步失败。",
                    },
                )
            return current

        _update_run_metadata_transaction(project_dir, run_id, interrupt_before_spawn)
        return running_project_update

    callback_lock = threading.Lock()
    progress_state = {"stdout_chunks": 0, "last_update": 0.0}

    def on_started(pid: int) -> None:
        identity = vina_adapter.get_process_identity(pid)
        if identity is None:
            raise RuntimeError("Vina 已启动，但无法记录可验证的进程身份。")
        process_started_at = _now_iso()

        def record_process(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") != "running":
                raise RuntimeError(f"run 已进入 {current.get('status')} 状态，拒绝登记新进程。")
            cancel_requested = _cancel_marker_path(project_dir, run_id).exists()
            current.update(
                {
                    "pid": pid,
                    "process_identity": identity,
                    "process_started_at": process_started_at,
                    "stage": "cancelling" if cancel_requested else "running",
                    "progress": {
                        "percent": 10,
                        "message": "检测到取消请求，正在终止 Vina。" if cancel_requested else "AutoDock Vina 已启动，正在计算。",
                    },
                },
            )
            current.pop("process_missing_since", None)
            return current

        updated, callback_error = _update_run_metadata_transaction(project_dir, run_id, record_process)
        if callback_error:
            raise RuntimeError(str(callback_error.get("error") or callback_error))
        if _cancel_marker_path(project_dir, run_id).exists():
            termination = vina_adapter.terminate_process(
                pid,
                expected_executable=detection.path,
                recorded_identity=identity,
            )
            if not termination.get("ok"):
                raise RuntimeError(str(termination.get("message") or "取消已启动的 Vina 失败。"))

    def on_output(stream_name: str, _chunk: str) -> None:
        if stream_name != "stdout" or _cancel_marker_path(project_dir, run_id).exists():
            return
        with callback_lock:
            progress_state["stdout_chunks"] += 1
            now = time.monotonic()
            if now - progress_state["last_update"] < 0.5:
                return
            progress_state["last_update"] = now
            percent = min(90, 15 + int(progress_state["stdout_chunks"] / 25))

            def update_progress(current: dict[str, Any]) -> dict[str, Any]:
                if current.get("status") != "running" or _cancel_marker_path(project_dir, run_id).exists():
                    return current
                current.update(
                    {
                        "stage": "running",
                        "progress": {"percent": percent, "message": "AutoDock Vina 正在计算并写入实时日志。"},
                    },
                )
                return current

            _, progress_error = _update_run_metadata_transaction(project_dir, run_id, update_progress)
            if progress_error:
                raise RuntimeError(str(progress_error.get("error") or progress_error))

    run_result = vina_adapter.run_managed(
        command,
        project_path,
        stdout_path,
        stderr_path,
        log_path,
        on_started=on_started,
        on_output=on_output,
    )
    if run_result.error:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        existing_size = stderr_path.stat().st_size if stderr_path.exists() else 0
        with stderr_path.open("a", encoding="utf-8") as handle:
            if existing_size:
                handle.write("\n")
            handle.write(run_result.error)

    vina_finished_at = _now_iso()
    output_required = output_path is not None
    output_exists = bool(
        output_path is not None
        and output_path.is_file()
        and output_path.stat().st_size > 0
    )
    output_normalization: dict[str, Any] | None = None
    output_normalization_error: dict[str, Any] | None = None
    output_normalization_warning = ""
    output_normalization_artifacts: dict[str, dict[str, Any]] = {}
    if output_exists and output_path is not None:
        normalized_output = _normalize_vina_pdbqt_output(
            output_path,
            output_file,
            allow_flexible_residue_boundary=(
                _is_flexible_movement_run(metadata)
            ),
        )
        output_normalization = copy.deepcopy(
            normalized_output.get("record") or {}
        )
        output_normalization_error = (
            copy.deepcopy(normalized_output.get("error"))
            if isinstance(normalized_output.get("error"), dict)
            else None
        )
        output_normalization_warning = str(
            normalized_output.get("warning") or ""
        )
        output_normalization_artifacts = copy.deepcopy(
            normalized_output.get("artifacts") or {}
        )
        output_exists = bool(
            normalized_output.get("ok")
            and output_path.is_file()
            and output_path.stat().st_size > 0
        )
    output_ok = not output_required or output_exists
    log_ok = log_path.is_file() and log_path.stat().st_size > 0
    evaluation_parse_error = ""
    if run_mode != "dock" and log_ok:
        parsed_evaluation = parse_vina_evaluation_text(log_path.read_text(encoding="utf-8", errors="replace"), run_mode)
        if not parsed_evaluation.get("ok"):
            evaluation_parse_error = str((parsed_evaluation.get("error") or {}).get("message") or "能量分解无法解析。")
    cancel_requested = _cancel_marker_path(project_dir, run_id).exists()
    execution_vina_binary_after = _tool_hash_snapshot(detection.path)
    start_vina_hash = str(execution_vina_binary.get("sha256") or "")
    end_vina_hash = str(execution_vina_binary_after.get("sha256") or "")
    vina_hashes_comparable = bool(
        re.fullmatch(r"[0-9a-fA-F]{64}", start_vina_hash)
        and re.fullmatch(r"[0-9a-fA-F]{64}", end_vina_hash)
    )
    vina_hash_match: bool | None = (
        start_vina_hash.lower() == end_vina_hash.lower()
        if vina_hashes_comparable
        else None
    )
    vina_integrity_warning = ""
    if vina_hash_match is False:
        vina_integrity_warning = "Vina 可执行文件在本次运行期间发生变化；本次结果需要人工复核。"
    elif vina_hash_match is None:
        vina_integrity_warning = "无法在运行结束时重新验证 Vina 可执行文件哈希；本次工具溯源不完整。"
    post_run_input_integrity = _validate_maps_post_run_integrity(
        project_dir,
        run_id,
        metadata,
    )
    if not post_run_input_integrity.get("ok"):
        post_run_input_integrity.setdefault("status", "failed")
        post_run_input_integrity.setdefault("checked_at", _now_iso())
    post_run_integrity_error = (
        post_run_input_integrity.get("error")
        if not post_run_input_integrity.get("ok")
        and isinstance(post_run_input_integrity.get("error"), dict)
        else None
    )
    strict_maps_integrity = (
        _metadata_grid_source(metadata) == "precomputed_maps"
        or _metadata_scoring_protocol(metadata) == "ad4_maps"
    )
    hydrated_postprocess_record: dict[str, Any] | None = None
    hydrated_postprocess_error: dict[str, Any] | None = None
    hydrated_artifacts: dict[str, dict[str, Any]] = {}
    if (
        is_hydrated
        and run_result.exit_code == 0
        and output_ok
        and log_ok
        and not run_result.error
        and not cancel_requested
        and post_run_input_integrity.get("ok")
        and vina_hash_match is not False
    ):
        retained_relative = Path(
            "runs",
            run_id,
            HYDRATED_RETAINED_OUTPUT_NAME,
        ).as_posix()
        water_free_relative = Path(
            "runs",
            run_id,
            HYDRATED_WATER_FREE_OUTPUT_NAME,
        ).as_posix()
        waters_manifest_relative = Path(
            "runs",
            run_id,
            HYDRATED_WATERS_MANIFEST_NAME,
        ).as_posix()
        try:
            if output_path is None:
                raise RuntimeError("水合对接缺少原始 out.pdbqt 路径。")
            raw_output_snapshot = _hash_snapshot(output_path, output_file)

            def mark_hydrated_postprocessing(
                current: dict[str, Any],
            ) -> dict[str, Any]:
                if current.get("status") != "running":
                    return current
                current.update(
                    {
                        "stage": "postprocessing",
                        "progress": {
                            "percent": 95,
                            "message": "Vina 已完成，正在分类水分子并生成派生结构。",
                        },
                        "vina_finished_at": vina_finished_at,
                        "exit_code": run_result.exit_code,
                        "pid": None,
                        "process_identity": None,
                    }
                )
                _with_artifact_hashes(current, {"out": raw_output_snapshot})
                output_sha256 = (
                    copy.deepcopy(current.get("output_sha256"))
                    if isinstance(current.get("output_sha256"), dict)
                    else {}
                )
                output_sha256["out"] = str(
                    raw_output_snapshot.get("sha256") or ""
                )
                current["output_sha256"] = output_sha256
                current.pop("process_missing_since", None)
                return current

            postprocessing_metadata, checkpoint_error = (
                _update_run_metadata_transaction(
                    project_dir,
                    run_id,
                    mark_hydrated_postprocessing,
                )
            )
            if checkpoint_error:
                raise RuntimeError(
                    "无法写入水合后处理 checkpoint："
                    + str(checkpoint_error.get("error") or checkpoint_error)
                )
            if (
                postprocessing_metadata is None
                or postprocessing_metadata.get("status") != "running"
                or postprocessing_metadata.get("stage") != "postprocessing"
            ):
                raise RuntimeError("run 状态已变化，拒绝启动水合结果后处理。")
            checkpoint_summary = update_project_run_summary(
                project_dir,
                run_id,
                {
                    "status": "running",
                    "stage": "postprocessing",
                    "vina_finished_at": vina_finished_at,
                    "exit_code": run_result.exit_code,
                },
            )
            if not checkpoint_summary.get("ok"):
                raise RuntimeError(
                    "水合后处理 checkpoint 无法同步到 project.json："
                    + str(
                        checkpoint_summary.get("error")
                        or checkpoint_summary
                    )
                )

            from dockstart_core.hydrated_postprocess import (
                HydratedPostprocessError,
                postprocess_hydrated_output,
            )

            maps_prefix_name = Path(
                str(prerequisites.get("maps_prefix") or "")
            ).name
            if not maps_prefix_name:
                raise RuntimeError("水合对接 maps prefix 无效。")
            water_map_relative = Path(
                "runs",
                run_id,
                "inputs",
                "maps",
                f"{maps_prefix_name}.W.map",
            ).as_posix()
            receptor_relative = str(prerequisites.get("receptor_file") or "")
            water_map_path = project_path / water_map_relative
            receptor_path = project_path / receptor_relative
            retained_path = project_path / retained_relative
            water_free_path = project_path / water_free_relative
            waters_manifest_path = project_path / waters_manifest_relative

            postprocessed = postprocess_hydrated_output(
                output_path,
                receptor_path,
                water_map_path,
                retained_path,
                water_free_path,
            )
            manifest = copy.deepcopy(postprocessed.get("manifest") or {})
            if not isinstance(manifest, dict):
                raise RuntimeError("水合后处理没有返回有效 manifest。")
            sources = (
                manifest.get("sources")
                if isinstance(manifest.get("sources"), dict)
                else {}
            )
            outputs = (
                manifest.get("outputs")
                if isinstance(manifest.get("outputs"), dict)
                else {}
            )
            source_paths = {
                "hydrated_output": output_file,
                "receptor": receptor_relative,
                "water_map": water_map_relative,
            }
            for key, relative_path in source_paths.items():
                record = sources.get(key)
                if not isinstance(record, dict):
                    raise RuntimeError(f"水合后处理 manifest 缺少 {key} 来源记录。")
                record["path"] = relative_path
            output_paths = {
                "retained_water_annotated": retained_relative,
                "water_free_ligand": water_free_relative,
            }
            for key, relative_path in output_paths.items():
                record = outputs.get(key)
                if not isinstance(record, dict):
                    raise RuntimeError(f"水合后处理 manifest 缺少 {key} 输出记录。")
                record["path"] = relative_path
            postprocess_finished_at = _now_iso()
            manifest.update(
                {
                    "protocol_id": HYDRATED_PROTOCOL_ID,
                    "run_id": run_id,
                    "created_at": postprocess_finished_at,
                    "manifest_file": waters_manifest_relative,
                    "sources": sources,
                    "outputs": outputs,
                }
            )
            _atomic_write_text(
                waters_manifest_path,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            hydrated_artifacts = {
                "hydrated_retained": _hash_snapshot(
                    retained_path,
                    retained_relative,
                ),
                "hydrated_water_free": _hash_snapshot(
                    water_free_path,
                    water_free_relative,
                ),
                "hydrated_waters_manifest": _hash_snapshot(
                    waters_manifest_path,
                    waters_manifest_relative,
                ),
            }
            for key, output_key in (
                ("hydrated_retained", "retained_water_annotated"),
                ("hydrated_water_free", "water_free_ligand"),
            ):
                recorded_hash = str(
                    (outputs.get(output_key) or {}).get("sha256") or ""
                ).lower()
                actual_hash = str(
                    hydrated_artifacts[key].get("sha256") or ""
                ).lower()
                if (
                    not SHA256_PATTERN.fullmatch(recorded_hash)
                    or recorded_hash != actual_hash
                ):
                    raise RuntimeError(
                        f"水合后处理 {output_key} 输出哈希与 manifest 不一致。"
                    )
            hydrated_postprocess_record = {
                "status": "finished",
                "method": str(manifest.get("method") or ""),
                "started_at": vina_finished_at,
                "finished_at": postprocess_finished_at,
                "manifest_file": waters_manifest_relative,
                "manifest_sha256": str(
                    hydrated_artifacts["hydrated_waters_manifest"].get(
                        "sha256"
                    )
                    or ""
                ),
                "raw_output_file": output_file,
                "retained_output_file": retained_relative,
                "water_free_output_file": water_free_relative,
                "summary": copy.deepcopy(manifest.get("summary") or {}),
                "semantics": copy.deepcopy(manifest.get("semantics") or {}),
                "outputs": copy.deepcopy(outputs),
            }
        except Exception as exc:  # noqa: BLE001 - fail closed after Vina.
            postprocess_finished_at = _now_iso()
            code = (
                str(exc.code)
                if "HydratedPostprocessError" in locals()
                and isinstance(exc, HydratedPostprocessError)
                else "HYDRATED_POSTPROCESS_ERROR"
            )
            hydrated_postprocess_error = {
                "code": code,
                "message": "Vina 已生成原始水合结果，但水分子后处理失败。",
                "raw_error": str(exc),
                "suggestion": (
                    "请保留本次 raw out.pdbqt、日志和冻结 maps，检查输入完整性后重新准备新 run。"
                ),
            }
            hydrated_postprocess_record = {
                "status": "failed",
                "started_at": vina_finished_at,
                "finished_at": postprocess_finished_at,
                "raw_output_file": output_file,
                "error": copy.deepcopy(hydrated_postprocess_error),
            }
    finished_at = _now_iso()
    run_artifacts = {
        "vina_binary_executed": execution_vina_binary,
        "vina_binary_observed_after_execution": execution_vina_binary_after,
        "log": _hash_snapshot(log_path, log_file),
        "stdout": _hash_snapshot(stdout_path, stdout_file),
        "stderr": _hash_snapshot(stderr_path, stderr_file),
        **({"out": _hash_snapshot(output_path, output_file)} if output_path is not None else {}),
        **output_normalization_artifacts,
        **hydrated_artifacts,
    }

    def finalize(current: dict[str, Any]) -> dict[str, Any]:
        current["input_snapshot_integrity"] = copy.deepcopy(
            post_run_input_integrity
        )
        if output_normalization is not None:
            current["output_normalization"] = copy.deepcopy(
                output_normalization
            )
        if output_normalization_warning:
            warnings = (
                list(current.get("warnings") or [])
                if isinstance(current.get("warnings"), list)
                else []
            )
            if output_normalization_warning not in warnings:
                warnings.append(output_normalization_warning)
            current["warnings"] = warnings
        if is_hydrated:
            current["hydrated_postprocess"] = copy.deepcopy(
                hydrated_postprocess_record
                or {
                    "status": "not_run",
                    "raw_output_file": output_file,
                }
            )
        current["vina_binary_integrity"] = {
            "start_sha256": start_vina_hash,
            "end_sha256": end_vina_hash,
            "match": vina_hash_match,
            "checked_at": finished_at,
        }
        if vina_integrity_warning:
            warnings = list(current.get("warnings") or []) if isinstance(current.get("warnings"), list) else []
            if vina_integrity_warning not in warnings:
                warnings.append(vina_integrity_warning)
            current["warnings"] = warnings
        if str(current.get("status") or "") in {"finished", "failed", "cancelled", "interrupted"}:
            _with_artifact_hashes(current, run_artifacts)
            current["output_sha256"] = {
                key: str(snapshot.get("sha256") or "")
                for key, snapshot in run_artifacts.items()
                if key not in {"vina_binary_executed", "vina_binary_observed_after_execution"}
            }
            return current
        if current.get("status") != "running":
            return current
        if post_run_integrity_error is not None:
            final_status = "failed"
            message = str(
                post_run_integrity_error.get("message")
                or "运行期间 immutable 输入发生变化，结果已拒绝。"
            )
            error_message = message
            percent = 100
        elif strict_maps_integrity and vina_hash_match is False:
            final_status = "failed"
            message = vina_integrity_warning
            error_message = vina_integrity_warning
            percent = 100
        elif hydrated_postprocess_error is not None:
            final_status = "failed"
            message = str(
                hydrated_postprocess_error.get("message")
                or "水合结果后处理失败。"
            )
            error_message = message
            percent = 100
        elif cancel_requested:
            final_status = "cancelled"
            message = "用户已取消运行。"
            error_message = ""
            percent = int((current.get("progress") or {}).get("percent") or 0)
        elif output_normalization_error is not None:
            final_status = "failed"
            message = str(
                output_normalization_error.get("message")
                or "Vina 输出 PDBQT 未通过文本完整性检查。"
            )
            error_message = message
            percent = 100
        elif (
            run_result.exit_code == 0
            and output_ok
            and log_ok
            and not evaluation_parse_error
            and not run_result.error
        ):
            final_status = "finished"
            message = (
                "当前姿势评分完成。"
                if run_mode == "score_only"
                else "局部优化完成。"
                if run_mode == "local_only"
                else "实验性水合 AD4 对接及水分子后处理完成。"
                if is_hydrated
                else "AutoDock Vina 运行完成。"
            )
            error_message = ""
            percent = 100
        else:
            final_status = "failed"
            message = "AutoDock Vina 运行失败。"
            error_message = (
                evaluation_parse_error
                if evaluation_parse_error
                else "AutoDock Vina 结束码为 0，但没有生成非空运行日志。"
                if run_result.exit_code == 0 and not log_ok and not run_result.error
                else (
                    "AutoDock Vina 结束码为 0，但没有生成局部优化后的 PDBQT。"
                    if run_mode == "local_only"
                    else "AutoDock Vina 结束码为 0，但没有生成非空 out.pdbqt。"
                )
                if run_result.exit_code == 0 and not output_ok and not run_result.error
                else "AutoDock Vina 执行失败，请查看 stderr.txt 和 log.txt。"
            )
            percent = 100
        current.update(
            {
                "status": final_status,
                "stage": (
                    "postprocess_failed"
                    if hydrated_postprocess_error is not None
                    and final_status == "failed"
                    else final_status
                ),
                "progress": {"percent": percent, "message": message},
                "finished_at": finished_at,
                "duration_seconds": _duration_seconds(current.get("started_at"), finished_at),
                "pid": run_result.pid if run_result.pid is not None else current.get("pid"),
                "exit_code": run_result.exit_code,
                "stdout_file": stdout_file,
                "stderr_file": stderr_file,
                "output_file": output_file,
                **(
                    {
                        "pose_file": str(
                            (
                                hydrated_postprocess_record
                                or {}
                            ).get("retained_output_file")
                            or output_file
                        )
                    }
                    if is_hydrated
                    else {}
                ),
                "log_file": log_file,
                "best_affinity": None,
            },
        )
        current.pop("process_missing_since", None)
        if error_message:
            current["error_message"] = error_message
        else:
            current.pop("error_message", None)
        _with_artifact_hashes(current, run_artifacts)
        current["output_sha256"] = {
            key: str(snapshot.get("sha256") or "")
            for key, snapshot in run_artifacts.items()
            if key not in {"vina_binary_executed", "vina_binary_observed_after_execution"}
        }
        return current

    final_metadata, transaction_error = _update_run_metadata_transaction(project_dir, run_id, finalize)
    if transaction_error:
        return transaction_error
    assert final_metadata is not None
    final_status = str(final_metadata.get("status") or "unknown")
    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "status": final_status,
            "stage": final_metadata.get("stage"),
            "finished_at": final_metadata.get("finished_at"),
            "duration_seconds": final_metadata.get("duration_seconds"),
            "exit_code": final_metadata.get("exit_code"),
            **(
                {
                    "pose_file": str(
                        final_metadata.get("pose_file")
                        or final_metadata.get("output_file")
                        or ""
                    ),
                    "hydrated_postprocess": copy.deepcopy(
                        final_metadata.get("hydrated_postprocess") or {}
                    ),
                }
                if is_hydrated
                else {}
            ),
        },
    )
    files_status = get_run_files_status(project_dir, run_id)
    message = {
        "finished": (
            "实验性水合 AD4 对接完成。"
            if is_hydrated
            else "Vina 运行完成。"
        ),
        "cancelled": "Vina 运行已取消。",
        "failed": str(final_metadata.get("error_message") or "Vina 运行失败。"),
        "interrupted": str(final_metadata.get("error_message") or "Vina 运行已中断。"),
    }.get(final_status, "Vina 运行状态已更新。")
    payload = {
        "ok": final_status in {"finished", "cancelled"} and project_update.get("ok", False),
        "project_dir": str(project_path),
        "project": project_update.get("project") if project_update.get("ok") else None,
        "run_id": run_id,
        "metadata": final_metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "output_file": output_file,
        "log_file": log_file,
        "files": files_status.get("files", []),
        "message": message,
        "error": None,
    }
    if not project_update.get("ok"):
        payload["error"] = project_update.get("error") or {"code": "RUN_SUMMARY_SYNC_FAILED", "message": "project.json run 摘要同步失败。"}
    elif final_status in {"failed", "interrupted"}:
        integrity_error = (
            final_metadata.get("input_snapshot_integrity", {}).get("error")
            if isinstance(
                final_metadata.get("input_snapshot_integrity"),
                dict,
            )
            and isinstance(
                final_metadata.get("input_snapshot_integrity", {}).get(
                    "error"
                ),
                dict,
            )
            else None
        )
        binary_integrity = (
            final_metadata.get("vina_binary_integrity")
            if isinstance(
                final_metadata.get("vina_binary_integrity"),
                dict,
            )
            else {}
        )
        binary_integrity_failed = (
            strict_maps_integrity
            and binary_integrity.get("match") is False
        )
        payload["error"] = {
            "code": (
                str(integrity_error.get("code") or "")
                if integrity_error
                else "RUN_VINA_BINARY_CHANGED"
                if binary_integrity_failed
                else str(hydrated_postprocess_error.get("code") or "")
                if hydrated_postprocess_error
                else str(output_normalization_error.get("code") or "")
                if output_normalization_error
                else "VINA_RUN_FAILED"
                if final_status == "failed"
                else "VINA_RUN_INTERRUPTED"
            ),
            "message": message,
            "raw_error": (
                str(integrity_error.get("raw_error") or "")
                if integrity_error
                else (
                    f"start={binary_integrity.get('start_sha256') or ''}; "
                    f"end={binary_integrity.get('end_sha256') or ''}"
                )
                if binary_integrity_failed
                else str(
                    hydrated_postprocess_error.get("raw_error") or ""
                )
                if hydrated_postprocess_error
                else str(
                    output_normalization_error.get("raw_error") or ""
                )
                if output_normalization_error
                else run_result.error or _tail_text(stderr_path)
            ),
            "suggestion": (
                str(integrity_error.get("suggestion") or "")
                if integrity_error
                else "请恢复执行前的 Vina binary，并重新生成 maps、准备新 run。"
                if binary_integrity_failed
                else str(
                    hydrated_postprocess_error.get("suggestion") or ""
                )
                if hydrated_postprocess_error
                else str(
                    output_normalization_error.get("suggestion") or ""
                )
                if output_normalization_error
                else "请查看 stderr.txt、stdout.txt 和 log.txt 后重新准备运行。"
            ),
        }
    return payload


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    if command == "create":
        if len(sys.argv) < 4:
            _print_json(_error("PROJECT_CREATE_ARGS", "创建项目需要 project_name 和 base_dir 参数。"))
            return
        _print_json(create_project(sys.argv[2], sys.argv[3]))
        return

    if command == "load":
        if len(sys.argv) < 3:
            _print_json(_error("PROJECT_LOAD_ARGS", "读取项目需要 project_dir 参数。"))
            return
        _print_json(recover_project_state(sys.argv[2]))
        return

    if command == "recover-project":
        if len(sys.argv) < 3:
            _print_json(_error("PROJECT_RECOVERY_ARGS", "恢复项目状态需要 project_dir 参数。"))
            return
        _print_json(recover_project_state(sys.argv[2]))
        return

    if command == "import-receptor":
        if len(sys.argv) < 4:
            _print_json(_error("PDBQT_IMPORT_ARGS", "导入受体需要 project_dir 和 source_path 参数。"))
            return
        _print_json(
            import_receptor_pdbqt(
                sys.argv[2],
                sys.argv[3],
                sys.argv[4] if len(sys.argv) >= 5 else "",
            )
        )
        return

    if command == "import-ligand":
        if len(sys.argv) < 4:
            _print_json(_error("PDBQT_IMPORT_ARGS", "导入配体需要 project_dir 和 source_path 参数。"))
            return
        _print_json(
            import_ligand_pdbqt(
                sys.argv[2],
                sys.argv[3],
                sys.argv[4] if len(sys.argv) >= 5 else "",
            )
        )
        return

    if command == "get-box":
        if len(sys.argv) < 3:
            _print_json(_error("BOX_GET_ARGS", "读取 Box 参数需要 project_dir 参数。"))
            return
        _print_json(get_box_params(sys.argv[2]))
        return

    if command == "update-box":
        if len(sys.argv) < 4:
            _print_json(_error("BOX_UPDATE_ARGS", "保存 Box 参数需要 project_dir 和 box JSON 参数。"))
            return
        try:
            box = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(_error("BOX_JSON_INVALID", "Box 参数不是有效 JSON。", str(exc)))
            return
        _print_json(update_box_params(sys.argv[2], box))
        return

    if command == "get-vina":
        if len(sys.argv) < 3:
            _print_json(_error("VINA_GET_ARGS", "读取 Vina 参数需要 project_dir 参数。"))
            return
        _print_json(get_vina_params(sys.argv[2]))
        return

    if command == "update-vina":
        if len(sys.argv) < 4:
            _print_json(_error("VINA_UPDATE_ARGS", "保存 Vina 参数需要 project_dir 和 vina JSON 参数。"))
            return
        try:
            vina = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(_error("VINA_JSON_INVALID", "Vina 参数不是有效 JSON。", str(exc)))
            return
        _print_json(update_vina_params(sys.argv[2], vina))
        return

    if command == "update-run-settings":
        if len(sys.argv) < 7:
            _print_json(
                _error(
                    "RUN_SETTINGS_ARGS",
                    "保存运行设置需要 project_dir、box JSON、vina JSON、run_mode 和 autobox 参数。",
                )
            )
            return
        try:
            box = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(_error("BOX_JSON_INVALID", "Box 参数不是有效 JSON。", str(exc)))
            return
        try:
            vina = json.loads(sys.argv[4])
        except json.JSONDecodeError as exc:
            _print_json(_error("VINA_JSON_INVALID", "Vina 参数不是有效 JSON。", str(exc)))
            return
        try:
            autobox = json.loads(sys.argv[6])
        except json.JSONDecodeError as exc:
            _print_json(_error("VINA_AUTOBOX_JSON_INVALID", "autobox 不是有效 JSON。", str(exc)))
            return
        confirm_pose_context: Any = False
        if len(sys.argv) >= 8:
            try:
                confirm_pose_context = json.loads(sys.argv[7])
            except json.JSONDecodeError as exc:
                _print_json(
                    _error(
                        "POSE_INPUT_CONFIRMATION_JSON_INVALID",
                        "confirm_pose_context 不是有效 JSON。",
                        str(exc),
                    )
                )
                return
        _print_json(
            update_run_settings(
                sys.argv[2],
                box,
                vina,
                sys.argv[5],
                autobox,
                confirm_pose_context,
            )
        )
        return

    if command == "update-run-protocol":
        if len(sys.argv) < 5:
            _print_json(
                _error(
                    "VINA_RUN_PROTOCOL_ARGS",
                    "保存运行任务类型需要 project_dir、run_mode 和 autobox 参数。",
                )
            )
            return
        try:
            autobox = json.loads(sys.argv[4])
        except json.JSONDecodeError as exc:
            _print_json(_error("VINA_AUTOBOX_JSON_INVALID", "autobox 不是有效 JSON。", str(exc)))
            return
        confirm_pose_context: Any = False
        if len(sys.argv) >= 6:
            try:
                confirm_pose_context = json.loads(sys.argv[5])
            except json.JSONDecodeError as exc:
                _print_json(
                    _error(
                        "POSE_INPUT_CONFIRMATION_JSON_INVALID",
                        "confirm_pose_context 不是有效 JSON。",
                        str(exc),
                    )
                )
                return
        _print_json(
            update_vina_run_protocol(
                sys.argv[2],
                sys.argv[3],
                autobox,
                confirm_pose_context,
            )
        )
        return

    if command == "preview-config":
        if len(sys.argv) < 3:
            _print_json(_error("CONFIG_PREVIEW_ARGS", "预览 Vina 配置需要 project_dir 参数。"))
            return
        _print_json(get_vina_config_preview(sys.argv[2]))
        return

    if command == "generate-config":
        if len(sys.argv) < 3:
            _print_json(_error("CONFIG_GENERATE_ARGS", "生成 Vina 配置需要 project_dir 参数。"))
            return
        _print_json(generate_vina_config(sys.argv[2]))
        return

    if command == "validate-run":
        if len(sys.argv) < 3:
            _print_json(_error("RUN_VALIDATE_ARGS", "运行前检查需要 project_dir 参数。"))
            return
        _print_json(validate_run_prerequisites(sys.argv[2]))
        return

    if command == "run-preflight":
        if len(sys.argv) < 3:
            _print_json(_error("RUN_PREFLIGHT_ARGS", "运行驾驶舱检查需要 project_dir 参数。"))
            return
        _print_json(get_run_preflight(sys.argv[2]))
        return

    if command == "prepare-run":
        if len(sys.argv) < 3:
            _print_json(_error("RUN_PREPARE_ARGS", "准备运行记录需要 project_dir 参数。"))
            return
        _print_json(prepare_vina_run(sys.argv[2]))
        return

    if command == "workflow-status":
        if len(sys.argv) < 3:
            _print_json(_error("WORKFLOW_STATUS_ARGS", "读取项目工作流状态需要 project_dir 参数。"))
            return
        _print_json(get_project_workflow_status(sys.argv[2]))
        return

    if command == "load-run-metadata":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_METADATA_ARGS", "读取运行元数据需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_run_metadata(sys.argv[2], sys.argv[3]))
        return

    if command == "execute-run":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_EXECUTE_ARGS", "执行 prepared run 需要 project_dir 和 run_id 参数。"))
            return
        _print_json(execute_prepared_vina_run(sys.argv[2], sys.argv[3]))
        return

    if command == "run-runtime-status":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_RUNTIME_STATUS_ARGS", "读取运行状态需要 project_dir 和 run_id 参数。"))
            return
        _print_json(get_run_runtime_status(sys.argv[2], sys.argv[3]))
        return

    if command == "cancel-run":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_CANCEL_ARGS", "取消运行需要 project_dir 和 run_id 参数。"))
            return
        _print_json(cancel_vina_run(sys.argv[2], sys.argv[3]))
        return

    if command == "run-files-status":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_FILES_STATUS_ARGS", "读取运行文件状态需要 project_dir 和 run_id 参数。"))
            return
        _print_json(get_run_files_status(sys.argv[2], sys.argv[3]))
        return

    if command == "analyze-results":
        if len(sys.argv) < 4:
            _print_json(_error("RESULT_ANALYZE_ARGS", "解析 Vina 结果需要 project_dir 和 run_id 参数。"))
            return
        _print_json(analyze_vina_run_results(sys.argv[2], sys.argv[3]))
        return

    if command == "load-scores":
        if len(sys.argv) < 4:
            _print_json(_error("SCORES_LOAD_ARGS", "读取 scores.csv 需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_scores_csv(sys.argv[2], sys.argv[3]))
        return

    if command == "load-evaluation":
        if len(sys.argv) < 4:
            _print_json(_error("VINA_EVALUATION_LOAD_ARGS", "读取姿势评价需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_vina_evaluation(sys.argv[2], sys.argv[3]))
        return

    if command == "export-report":
        if len(sys.argv) < 4:
            _print_json(_error("REPORT_EXPORT_ARGS", "导出 Markdown 报告需要 project_dir 和 run_id 参数。"))
            return
        _print_json(export_markdown_report(sys.argv[2], sys.argv[3]))
        return

    if command == "report-status":
        if len(sys.argv) < 4:
            _print_json(_error("REPORT_STATUS_ARGS", "读取报告状态需要 project_dir 和 run_id 参数。"))
            return
        _print_json(get_report_status(sys.argv[2], sys.argv[3]))
        return

    _print_json(_error("PROJECT_COMMAND_UNKNOWN", f"未知项目命令：{command}"))


if __name__ == "__main__":
    main()
