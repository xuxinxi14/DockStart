"""Project creation and PDBQT import helpers for DockStart."""

from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
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
from dockstart_core.persistence import atomic_write_text as _atomic_write_text
from dockstart_core.preparation_models import PreparationState, preparation_state_from_dict
from dockstart_core.settings import load_settings
from dockstart_core.structure_review import build_structure_review

PROJECT_DIRS = ("raw", "prepared", "configs", "runs", "results", "reports", "preparation")
PROJECT_NAME_PATTERN = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")
RUN_ID_PATTERN = re.compile(r"^run_(\d{3,})$")
VINA_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
VINA_SCORE_ROW_PATTERN = re.compile(
    rf"^\s*(\d+)\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})(?:\s|$)",
)
SCORES_CSV_FIELDS = ("mode", "affinity_kcal_mol", "rmsd_lb", "rmsd_ub")
DOCKING_SCORE_DISCLAIMER = "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
RUN_REPORT_FILE = "docking_report.md"
PROJECT_REPORT_FILE = Path("reports", "docking_report.md").as_posix()
CURRENT_PROJECT_SCHEMA_VERSION = 1


class ProjectSchemaError(ValueError):
    """Raised when a project document cannot be safely migrated."""

    def __init__(self, code: str, message: str, *, raw_error: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_error = raw_error


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
    num_modes: int = 9
    energy_range: float = 4
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

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
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
            num_modes=int(_value_or_default(vina, "num_modes", 9)),
            energy_range=float(_value_or_default(vina, "energy_range", 4)),
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


def load_project(project_dir: str) -> dict[str, Any]:
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
        with _project_lock(path):
            data, migrated, backup_path = _read_and_migrate_project_unlocked(
                path,
                persist_migration=True,
            )
        project = _project_from_dict(data, path)
        warnings = []
        message = "项目读取成功。"
        if migrated:
            message = "项目已迁移到当前数据格式并完成读取。"
            warnings.append(
                f"迁移前 project.json 已备份到 {backup_path.name if backup_path else '备份文件'}。",
            )
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


def _import_pdbqt(project_dir: str, source_path: str, role: str) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("PDBQT_ROLE_INVALID", "PDBQT 导入类型无效。")

    validation = validate_pdbqt_file(source_path)
    if not validation.get("ok"):
        return validation

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
            target_path = Path(project.project_dir).expanduser() / "prepared" / f"{role}.pdbqt"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(source_path).expanduser(), target_path)

            file_ref = getattr(project, role)
            file_ref.source = "local"
            file_ref.file = target_relative

            saved = save_project(project)
            if not saved.get("ok"):
                return saved

            label = "受体" if role == "receptor" else "配体"
            return _success(project, f"{label} PDBQT 已导入。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PDBQT_IMPORT_ERROR",
            "导入 PDBQT 文件时发生错误。",
            str(exc),
            "请确认项目目录可写，源文件未被其他程序锁定。",
        )


def import_receptor_pdbqt(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "receptor")


def import_ligand_pdbqt(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "ligand")


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
                suggestion="cpu 可以填写 0 表示自动，或填写一个正整数。",
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


def validate_vina_params(vina: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(vina, dict):
        return _error(
            "VINA_PARAMS_INVALID",
            "Vina 参数格式无效。",
            suggestion="请提交包含 exhaustiveness、num_modes、energy_range、cpu 和 seed 的对象。",
        )

    scoring = str(vina.get("scoring") or "vina").strip().lower()
    if scoring not in {"vina", "vinardo"}:
        return _error(
            "VINA_SCORING_INVALID",
            "评分函数仅支持 Vina 或 Vinardo。",
            suggestion="请选择 Vina 或 Vinardo；AutoDock4 需要预先生成 affinity maps，当前标准流程不开放。",
        )

    exhaustiveness, error = _parse_vina_int(vina, "exhaustiveness")
    if error:
        return error
    num_modes, error = _parse_vina_int(vina, "num_modes")
    if error:
        return error
    energy_range, error = _parse_vina_positive_float(vina, "energy_range")
    if error:
        return error
    cpu, error = _parse_vina_int(vina, "cpu", allow_zero=True)
    if error:
        return error
    seed, error = _parse_vina_seed(vina)
    if error:
        return error

    parsed = {
        "scoring": scoring,
        "exhaustiveness": exhaustiveness,
        "num_modes": num_modes,
        "energy_range": energy_range,
        "cpu": cpu,
        "seed": seed,
    }

    warnings: list[str] = []
    if exhaustiveness is not None and exhaustiveness > 64:
        warnings.append("exhaustiveness 较高，可能显著增加运行时间；新手建议从 8 开始。")
    if num_modes is not None and num_modes > 50:
        warnings.append("num_modes 较多，结果文件和解析成本可能增加；请确认确实需要这么多构象。")
    if energy_range is not None and energy_range > 10:
        warnings.append("energy_range 较大，可能保留较多高能构象；新手建议 3 或 4。")
    if cpu == 0:
        warnings.append("cpu 设置为 0，将由 Vina 自动决定或使用默认 CPU 设置。")

    return {
        "ok": True,
        "vina": parsed,
        "warnings": warnings,
        "error": None,
    }


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

    validation = validate_vina_params(vina)
    if not validation.get("ok"):
        return validation

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        parsed_vina = validation["vina"]
        project.vina = VinaSettings(
            scoring=parsed_vina["scoring"],
            exhaustiveness=parsed_vina["exhaustiveness"],
            num_modes=parsed_vina["num_modes"],
            energy_range=parsed_vina["energy_range"],
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

    _, receptor_error = _project_relative_file(project_path, project.receptor.file, "receptor")
    if receptor_error:
        return _prepared_input_hint(project, project_path, "receptor", receptor_error)
    _, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        return _prepared_input_hint(project, project_path, "ligand", ligand_error)

    box_validation = validate_box_params(asdict(project.box))
    if not box_validation.get("ok"):
        return box_validation

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        return vina_validation

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "box": box_validation["box"],
        "vina": vina_validation["vina"],
        "warnings": vina_validation.get("warnings", []) + box_validation.get("warnings", []),
        "error": None,
    }


def build_vina_config_text(project_dir: str) -> dict[str, Any]:
    prerequisites = validate_config_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    project = _project_from_dict(prerequisites["project"], Path(project_dir).expanduser())
    box = prerequisites["box"]
    vina = prerequisites["vina"]
    lines = [
        f"receptor = {Path(project.receptor.file).as_posix()}",
        f"ligand = {Path(project.ligand.file).as_posix()}",
        f"scoring = {vina['scoring']}",
        "",
        f"center_x = {_format_config_number(box['center_x'])}",
        f"center_y = {_format_config_number(box['center_y'])}",
        f"center_z = {_format_config_number(box['center_z'])}",
        "",
        f"size_x = {_format_config_number(box['size_x'])}",
        f"size_y = {_format_config_number(box['size_y'])}",
        f"size_z = {_format_config_number(box['size_z'])}",
        "",
        f"exhaustiveness = {vina['exhaustiveness']}",
        f"num_modes = {vina['num_modes']}",
        f"energy_range = {_format_config_number(vina['energy_range'])}",
        f"cpu = {vina['cpu']}",
    ]
    if vina["seed"] is not None:
        lines.append(f"seed = {vina['seed']}")

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "config_file": "configs/vina_config.txt",
        "config_text": "\n".join(lines) + "\n",
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
                "stage": str(combined.get("stage") or combined.get("status") or "unknown"),
            },
        )
    return history


def _format_duration_range(seconds_low: float, seconds_high: float) -> str:
    if seconds_high < 60:
        return f"约 {max(1, round(seconds_low))}–{max(1, round(seconds_high))} 秒"
    return f"约 {max(1, round(seconds_low / 60))}–{max(1, round(seconds_high / 60))} 分钟"


def _runtime_estimate(run_history: list[dict[str, Any]]) -> dict[str, Any]:
    samples = sorted(
        float(item["duration_seconds"])
        for item in run_history
        if item.get("status") == "finished"
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
        "tool": {"status": "unknown", "version": "", "path": "", "source": "unknown", "message": ""},
        "output": {"runs_dir": "", "writable": False, "free_bytes": None},
        "system": _system_snapshot(),
        "estimate": {"available": False, "sample_count": 0, "range_label": "", "message": "暂无运行历史。"},
        "next_run_id": "",
        "command_preview": "",
        "run_history": [],
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
    default_payload.update({"ok": True, "project": project.to_dict(), "project_dir": str(project_path)})
    add_check("project", "项目文件", "ok", "project.json 已读取。", blocking=False, path=str(project_path / "project.json"))

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

    inspect_input("receptor", project.receptor.file)
    inspect_input("ligand", project.ligand.file)

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
    if box_validation.get("ok"):
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
        add_check("vina_params", "Vina 参数", "ok", "Vina 参数格式有效。", blocking=False, action_page="vina-param")
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
    }
    default_payload["tool"] = tool
    if detection.status == "ok":
        add_check("tool", "AutoDock Vina", "ok", detection.message or "AutoDock Vina 可用。", blocking=False, path=detection.path, version=detection.version)
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
    command = _build_vina_command(detection.path, config_file, next_run_id)
    default_payload.update(
        {
            "ready": not blockers,
            "estimate": _runtime_estimate(run_history),
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


def _build_vina_command(
    vina_path: str,
    config_file: str,
    run_id: str,
) -> list[str]:
    return [
        vina_path or "vina",
        "--config",
        Path(config_file).as_posix(),
        "--out",
        Path("runs", run_id, "out.pdbqt").as_posix(),
    ]


def _build_run_snapshot_config(project: DockStartProject, run_id: str) -> str:
    receptor = Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()
    ligand = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
    lines = [
        f"receptor = {receptor}",
        f"ligand = {ligand}",
        f"scoring = {project.vina.scoring}",
        "",
        f"center_x = {_format_config_number(project.box.center_x)}",
        f"center_y = {_format_config_number(project.box.center_y)}",
        f"center_z = {_format_config_number(project.box.center_z)}",
        "",
        f"size_x = {_format_config_number(project.box.size_x)}",
        f"size_y = {_format_config_number(project.box.size_y)}",
        f"size_z = {_format_config_number(project.box.size_z)}",
        "",
        f"exhaustiveness = {project.vina.exhaustiveness}",
        f"num_modes = {project.vina.num_modes}",
        f"energy_range = {_format_config_number(project.vina.energy_range)}",
        f"cpu = {project.vina.cpu}",
    ]
    if project.vina.seed is not None:
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
    checks.append(_run_check("project_json", "project.json", "ok", "已读取项目配置。", "project.json"))

    receptor_path, receptor_error = _project_relative_file(project_path, project.receptor.file, "receptor")
    if receptor_error:
        receptor_error = _prepared_input_hint(project, project_path, "receptor", receptor_error)
        error = receptor_error["error"]
        checks.append(
            _run_check(
                "receptor",
                "receptor.pdbqt",
                _status_from_error_code(error["code"]),
                error["message"],
                project.receptor.file,
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
                project.receptor.file,
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
    checks.append(_run_check("receptor", "receptor.pdbqt", "ok", "已找到受体 PDBQT 文件。", project.receptor.file))

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
    if not box_validation.get("ok"):
        error = box_validation["error"]
        checks.append(_run_check("box", "Box 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("box", "Box 参数", "ok", "Box 参数格式有效。"))

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        error = vina_validation["error"]
        checks.append(_run_check("vina_params", "Vina 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("vina_params", "Vina 参数", "ok", "Vina 参数格式有效。"))

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

    next_run_id = get_next_run_id(project.project_dir)
    command = _build_vina_command(vina_detection.path, config_file, next_run_id)
    warnings = box_validation.get("warnings", []) + vina_validation.get("warnings", [])
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

    command = _build_vina_command(prerequisites["vina_path"], prerequisites["config_file"], run_id)
    return {
        "ok": True,
        "project_dir": prerequisites["project_dir"],
        "project": prerequisites["project"],
        "run_id": run_id,
        "command": command,
        "command_preview": _format_command_preview(command),
        "checks": prerequisites.get("checks", []),
        "warnings": prerequisites.get("warnings", []),
        "message": "Vina 命令预览已生成；当前版本不会执行该命令。",
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
        output_file = Path("runs", run_id, "out.pdbqt").as_posix()
        log_file = Path("runs", run_id, "log.txt").as_posix()
        command = _build_vina_command(prerequisites["vina_path"], config_snapshot_file, run_id)
        receptor_path = project_root / project.receptor.file
        ligand_path = project_root / project.ligand.file
        receptor_snapshot_path = project_root / receptor_snapshot_file
        ligand_snapshot_path = project_root / ligand_snapshot_file
        config_snapshot_path = project_root / config_snapshot_file
        shutil.copyfile(receptor_path, receptor_snapshot_path)
        shutil.copyfile(ligand_path, ligand_snapshot_path)
        _atomic_write_text(config_snapshot_path, _build_run_snapshot_config(project, run_id))
        receptor_snapshot = _parse_pdbqt_stats(receptor_snapshot_path, receptor_snapshot_file)
        receptor_snapshot["source_relative_path"] = Path(project.receptor.file).as_posix()
        ligand_snapshot = _parse_pdbqt_stats(ligand_snapshot_path, ligand_snapshot_file, ligand=True)
        ligand_snapshot["source_relative_path"] = Path(project.ligand.file).as_posix()
        config_sha256 = _sha256_file(config_snapshot_path)
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
            },
            "vina_sha256": vina_binary["sha256"],
            "system": system_snapshot,
            "command": command,
            "config_file": Path(config_file).as_posix(),
            "config_snapshot": config_snapshot_file,
            "snapshots": {
                "inputs": {"receptor": receptor_snapshot, "ligand": ligand_snapshot},
                "config": {
                    "source_relative_path": Path(config_file).as_posix(),
                    "relative_path": config_snapshot_file,
                    "snapshot_file": config_snapshot_file,
                    "sha256": config_sha256,
                    "size_bytes": config_snapshot_path.stat().st_size,
                },
                "box": asdict(project.box),
                "vina": asdict(project.vina),
            },
            "input_sha256": {
                "receptor": receptor_snapshot["sha256"],
                "ligand": ligand_snapshot["sha256"],
                "config": config_sha256,
            },
            "box_snapshot": asdict(project.box),
            "vina_snapshot": asdict(project.vina),
            "output_file": output_file,
            "log_file": log_file,
            "exit_code": None,
            "best_affinity": None,
        }
        _with_artifact_hashes(metadata, {"vina_binary_prepared": vina_binary})

        _atomic_write_text(run_dir / "command_preview.txt", _format_command_preview(command) + "\n")
        _write_run_metadata(project.project_dir, run_id, metadata)

        project.runs.append(
            {
                "run_id": run_id,
                "status": "prepared",
                "metadata_file": metadata_file,
                "created_at": created_at,
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
            "command_preview": _format_command_preview(command),
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
        output_file = str(run.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())
        output_status = _workflow_file_status(project_path, output_file)
        if output_status["status"] == "ok":
            available_runs.append(
                {
                    "run_id": run_id,
                    "status": run.get("status", ""),
                    "output_file": output_file,
                    "size": output_status.get("size", 0),
                }
            )

    can_view_raw_receptor = receptor_raw["status"] == "ok"
    can_view_raw_ligand = ligand_raw["status"] == "ok"
    can_view_prepared_receptor = receptor_prepared["status"] == "ok"
    can_view_prepared_ligand = ligand_prepared["status"] == "ok"
    can_view_docking_output = bool(available_runs)

    if can_view_docking_output:
        recommended = "已有 Vina out.pdbqt，可以打开 3D Viewer 查看 docking pose。"
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
    receptor_raw = _workflow_file_status(project_path, project.receptor.raw_file)
    ligand_raw = _workflow_file_status(project_path, project.ligand.raw_file)
    receptor_prepared = _workflow_file_status(project_path, project.receptor.file)
    ligand_prepared = _workflow_file_status(project_path, project.ligand.file)
    config_file = _config_relative_path(project)
    config_status = _workflow_file_status(project_path, config_file)
    box_validation = validate_box_params(asdict(project.box))
    vina_validation = validate_vina_params(asdict(project.vina))
    latest_run = project.runs[-1] if project.runs else None

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
    elif not box_validation.get("ok"):
        next_action = "请设置合法的 docking box 参数。"
    elif not vina_validation.get("ok"):
        next_action = "请设置合法的 Vina 参数。"
    elif config_status["status"] != "ok":
        next_action = "请生成 configs/vina_config.txt。"
    elif not latest_run:
        next_action = "输入文件和参数已就绪，可以准备并运行 Vina。"
    elif latest_run.get("status") == "prepared":
        next_action = f"{latest_run.get('run_id')} 已准备，可以执行 Vina。"
    elif latest_run.get("status") == "finished":
        next_action = f"{latest_run.get('run_id')} 已完成，可以查看结果或导出报告。"
    elif latest_run.get("status") == "failed":
        next_action = f"{latest_run.get('run_id')} 运行失败，请查看 stdout/stderr/log。"
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
        "config": config_status,
        "latest_run": latest_run,
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
                    "rmsd_lb": float(rmsd_lb_text),
                    "rmsd_ub": float(rmsd_ub_text),
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


def export_scores_csv(project_dir: str, run_id: str, scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(scores, list) or not scores:
        return _error(
            "SCORES_EMPTY",
            "没有可导出的 Vina score 记录。",
            suggestion="请先解析包含结果表格的 log.txt。",
        )

    project_path = Path(project_dir).expanduser()
    run_scores_file = Path("runs", run_id, "scores.csv").as_posix()
    project_scores_file = Path("results", "scores.csv").as_posix()

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
        "message": "scores.csv 已导出。",
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


def load_scores_csv(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
    scores_file = str(metadata.get("scores_file") or Path("runs", run_id, "scores.csv").as_posix())
    scores_path, path_error = _project_relative_path_for_run(project_path, scores_file, "SCORES_CSV", "scores.csv")
    if path_error:
        return path_error
    assert scores_path is not None

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
        with scores_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != SCORES_CSV_FIELDS:
                return _error(
                    "SCORES_CSV_HEADER_INVALID",
                    "scores.csv 表头不符合 DockStart 当前版本要求。",
                    raw_error=",".join(reader.fieldnames or []),
                    suggestion="请重新从 Vina log 解析并导出 scores.csv。",
                )
            scores: list[dict[str, Any]] = []
            for line_number, row in enumerate(reader, start=2):
                parsed, row_error = _parse_scores_csv_row(row, line_number)
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
        "project_scores_file": str(metadata.get("project_scores_file") or Path("results", "scores.csv").as_posix()),
        "best_affinity": metadata.get("best_affinity", scores[0]["affinity_kcal_mol"]),
        "analyzed_at": metadata.get("analyzed_at", ""),
        "message": "scores.csv 已读取。",
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

    parsed_scores = parse_vina_log_file(project_dir, run_id)
    if _is_error_payload(parsed_scores):
        return parsed_scores
    assert isinstance(parsed_scores, list)

    exported = export_scores_csv(project_dir, run_id, parsed_scores)
    if not exported.get("ok"):
        return exported

    analyzed_at = _now_iso()
    best_affinity = parsed_scores[0]["affinity_kcal_mol"]
    project_path = Path(project_dir).expanduser().resolve()
    scores_artifacts = {
        "scores": _hash_snapshot(project_path / exported["scores_file"], exported["scores_file"]),
        "project_scores": _hash_snapshot(
            project_path / exported["project_scores_file"],
            exported["project_scores_file"],
        ),
    }
    def merge_analysis(current: dict[str, Any]) -> dict[str, Any]:
        current.update(
            {
                "best_affinity": best_affinity,
                "scores_file": exported["scores_file"],
                "project_scores_file": exported["project_scores_file"],
                "analyzed_at": analyzed_at,
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
        "scores": parsed_scores,
        "scores_file": exported["scores_file"],
        "project_scores_file": exported["project_scores_file"],
        "best_affinity": best_affinity,
        "analyzed_at": analyzed_at,
        "message": "Vina 结果已解析，scores.csv 已导出。",
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

    receptor_file = _run_input_snapshot_file(metadata, "receptor", project.receptor.file)
    ligand_file = _run_input_snapshot_file(metadata, "ligand", project.ligand.file)
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
        "project_scores_file": scores_payload.get("project_scores_file", Path("results", "scores.csv").as_posix()),
        "receptor_file": receptor_file,
        "receptor_path": str(receptor_path) if receptor_path else "",
        "ligand_file": ligand_file,
        "ligand_path": str(ligand_path) if ligand_path else "",
        "config_file": config_file,
        "config_path": str(config_path) if config_path else "",
        "box_snapshot": _run_snapshot_mapping(metadata, "box") or asdict(project.box),
        "vina_snapshot": _run_snapshot_mapping(metadata, "vina") or asdict(project.vina),
        "error": None,
    }


def build_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    context = _load_report_context(project_dir, run_id)
    if not context.get("ok"):
        return context

    project = _project_from_dict(context["project"], Path(project_dir).expanduser())
    metadata = context["metadata"]
    scores = context["scores"]
    command = _command_for_report(metadata)
    command_text = json.dumps(command, ensure_ascii=False, indent=2)
    vina_path = command[0] if command else str(metadata.get("vina_path") or "")

    input_rows = [
        ["receptor 文件", context["receptor_file"]],
        ["ligand 文件", context["ligand_file"]],
        ["vina_config.txt", context["config_file"]],
        ["log.txt", str(metadata.get("log_file") or Path("runs", run_id, "log.txt").as_posix())],
        ["out.pdbqt", str(metadata.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())],
        ["stdout.txt", str(metadata.get("stdout_file") or Path("runs", run_id, "stdout.txt").as_posix())],
        ["stderr.txt", str(metadata.get("stderr_file") or Path("runs", run_id, "stderr.txt").as_posix())],
    ]
    box_snapshot = {**asdict(project.box), **context["box_snapshot"]}
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
        ["num_modes", vina_snapshot["num_modes"]],
        ["energy_range", vina_snapshot["energy_range"]],
        ["cpu", vina_snapshot["cpu"]],
        ["seed", vina_snapshot["seed"] if vina_snapshot.get("seed") is not None else "未设置"],
    ]
    score_rows = [
        [score["mode"], score["affinity_kcal_mol"], score["rmsd_lb"], score["rmsd_ub"]]
        for score in scores
    ]
    input_sha256 = metadata.get("input_sha256") if isinstance(metadata.get("input_sha256"), dict) else {}
    vina_tool = metadata.get("vina_tool") if isinstance(metadata.get("vina_tool"), dict) else {}
    system = metadata.get("system") if isinstance(metadata.get("system"), dict) else {}
    app = metadata.get("app") if isinstance(metadata.get("app"), dict) else {}
    reproducibility_rows = [
        ["DockStart version", metadata.get("app_version") or app.get("version")],
        ["Vina binary SHA256", metadata.get("vina_sha256") or vina_tool.get("sha256")],
        ["receptor SHA256", input_sha256.get("receptor")],
        ["ligand SHA256", input_sha256.get("ligand")],
        ["config SHA256", input_sha256.get("config")],
        ["system fingerprint", system.get("fingerprint")],
    ]
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
        ["第二名与最佳评分差", second_gap, "kcal/mol"],
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
        f"- 第二名与最佳评分差为 {second_gap_text} kcal/mol；该差值只描述本次输出的内部排序，不代表结合概率或置信度。",
        f"- {sum(value <= best_affinity + 1 for value in affinities)} / {score_count} 个构象位于最佳评分 1 kcal/mol 范围内。",
        "- Vina 表格中的 RMSD l.b./u.b. 是相对最佳预测构象的距离界限，不是相对共晶配体的验证 RMSD。",
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
            "## 3. Box 参数",
            "",
            _markdown_table(["参数", "值", "单位"], box_rows),
            "",
            "## 4. Vina 参数",
            "",
            _markdown_table(["参数", "值"], vina_rows),
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
            "## 7. Docking Score 结果",
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
        "report_text": report_text,
        "message": "Markdown 报告内容已生成。",
        "error": None,
    }


def _report_file_statuses(project_dir: str, run_id: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    project_path = Path(project_dir).expanduser()
    scores_file = str(metadata.get("scores_file") or Path("runs", run_id, "scores.csv").as_posix())
    report_file = str(metadata.get("report_file") or Path("runs", run_id, RUN_REPORT_FILE).as_posix())
    project_report_file = str(metadata.get("project_report_file") or PROJECT_REPORT_FILE)
    return [
        _file_status(project_path, scores_file, "scores", "scores.csv"),
        _file_status(project_path, report_file, "run_report", f"runs/{run_id}/docking_report.md"),
        _file_status(project_path, project_report_file, "project_report", "reports/docking_report.md"),
    ]


def get_report_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    files = _report_file_statuses(project_dir, run_id, metadata)
    scores_status = next((item for item in files if item["key"] == "scores"), None)
    report_files = [item for item in files if item["key"] in {"run_report", "project_report"}]
    reports_ready = all(item["status"] == "ok" for item in report_files)
    can_export = str(metadata.get("status") or "") == "finished" and bool(scores_status and scores_status["status"] == "ok")

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": loaded.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "files": files,
        "scores_status": scores_status,
        "report_status": "exported" if reports_ready else "missing",
        "can_export": can_export,
        "report_file": str(metadata.get("report_file") or Path("runs", run_id, RUN_REPORT_FILE).as_posix()),
        "project_report_file": str(metadata.get("project_report_file") or PROJECT_REPORT_FILE),
        "reported_at": str(metadata.get("reported_at") or ""),
        "message": "报告状态已读取。",
        "error": None,
    }


def export_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    built = build_markdown_report(project_dir, run_id)
    if not built.get("ok"):
        return built

    project_path = Path(project_dir).expanduser()
    run_report_file = Path("runs", run_id, RUN_REPORT_FILE).as_posix()
    project_report_file = PROJECT_REPORT_FILE
    run_report_path, run_report_error = _project_relative_path_for_run(
        project_path,
        run_report_file,
        "RUN_REPORT",
        f"runs/{run_id}/docking_report.md",
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
        "output": Path("runs", run_id, "out.pdbqt").as_posix(),
        "log": Path("runs", run_id, "log.txt").as_posix(),
        "stdout": Path("runs", run_id, "stdout.txt").as_posix(),
        "stderr": Path("runs", run_id, "stderr.txt").as_posix(),
    }
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
        if key in {"output", "log", "stdout", "stderr"} and lexical_path.exists():
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
        expected_parent = run_dir / "inputs" if key in {"receptor", "ligand"} else run_dir
        if resolved.parent != expected_parent or resolved != lexical_path.absolute():
            return _error(
                "RUN_PATH_REPARSE_UNSAFE",
                f"固定运行路径 {key} 被重解析到本次 run 之外，拒绝执行。",
                raw_error=f"lexical={lexical_path}; resolved={resolved}",
                suggestion="请重新准备新的 run，且不要用链接替换运行文件。",
            )
        fixed_paths[key] = resolved

    for key in ("receptor", "ligand", "config"):
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
    expected_hashes = {
        "receptor": str((inputs.get("receptor") or {}).get("sha256") or "") if isinstance(inputs.get("receptor"), dict) else "",
        "ligand": str((inputs.get("ligand") or {}).get("sha256") or "") if isinstance(inputs.get("ligand"), dict) else "",
        "config": str(config_snapshot.get("sha256") or ""),
    }
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

    config_text = fixed_paths["config"].read_text(encoding="utf-8", errors="strict")
    expected_receptor_line = f"receptor = {fixed_relative_paths['receptor']}"
    expected_ligand_line = f"ligand = {fixed_relative_paths['ligand']}"
    if expected_receptor_line not in config_text.splitlines() or expected_ligand_line not in config_text.splitlines():
        return _error(
            "RUN_CONFIG_SNAPSHOT_INPUT_MISMATCH",
            "运行配置快照没有引用本次 run 的 immutable 输入快照。",
            suggestion="请重新准备新的 run。",
        )

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project.to_dict(),
        "config_file": fixed_relative_paths["config"],
        "config_path": str(fixed_paths["config"]),
        "receptor_file": fixed_relative_paths["receptor"],
        "ligand_file": fixed_relative_paths["ligand"],
        "output_file": fixed_relative_paths["output"],
        "output_path": str(fixed_paths["output"]),
        "log_file": fixed_relative_paths["log"],
        "log_path": str(fixed_paths["log"]),
        "stdout_file": fixed_relative_paths["stdout"],
        "stdout_path": str(fixed_paths["stdout"]),
        "stderr_file": fixed_relative_paths["stderr"],
        "stderr_path": str(fixed_paths["stderr"]),
        "error": None,
    }


def get_run_files_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
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
        _file_status(project_path, Path("runs", run_id, "out.pdbqt").as_posix(), "out", "out.pdbqt"),
    ]

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
            stage == "starting"
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

        if not process_active and not launch_grace:
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
    for key, filename in (("stdout", "stdout.txt"), ("stderr", "stderr.txt"), ("log", "log.txt")):
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
    "created_at",
    "started_at",
    "finished_at",
    "duration_seconds",
    "exit_code",
    "best_affinity",
    "output_file",
    "log_file",
    "scores_file",
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
            str(metadata.get("stage") or "") == "starting"
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
        if not launch_grace and not first_child.get("ok") and not first_executor.get("ok"):
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
        artifact_paths = {
            "out": ("out.pdbqt", Path("runs", run_id, "out.pdbqt").as_posix()),
            "log": ("log.txt", Path("runs", run_id, "log.txt").as_posix()),
            "stdout": ("stdout.txt", Path("runs", run_id, "stdout.txt").as_posix()),
            "stderr": ("stderr.txt", Path("runs", run_id, "stderr.txt").as_posix()),
        }
        snapshots: dict[str, dict[str, Any]] = {}
        existing_artifacts = metadata.get("artifacts") if isinstance(metadata.get("artifacts"), dict) else {}
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
            # Hashing the executable currently found at this path cannot prove
            # which bytes executed for a historical run.  Record the gap
            # explicitly instead of manufacturing false provenance.
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

    pid = metadata.get("pid")
    requested_at = _now_iso()
    if not isinstance(pid, int) or pid <= 0:
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


def execute_prepared_vina_run(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None
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
    execution_vina_binary = _tool_hash_snapshot(detection.path)

    project_path = Path(project_dir).expanduser().resolve()
    stdout_file = str(prerequisites["stdout_file"])
    stderr_file = str(prerequisites["stderr_file"])
    log_file = str(prerequisites["log_file"])
    output_file = str(prerequisites["output_file"])
    config_file = str(prerequisites["config_file"])
    stdout_path = Path(prerequisites["stdout_path"])
    stderr_path = Path(prerequisites["stderr_path"])
    log_path = Path(prerequisites["log_path"])
    output_path = Path(prerequisites["output_path"])
    command = _build_vina_command(detection.path, config_file, run_id)
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

    finished_at = _now_iso()
    output_ok = output_path.is_file() and output_path.stat().st_size > 0
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
    run_artifacts = {
        "vina_binary_executed": execution_vina_binary,
        "vina_binary_observed_after_execution": execution_vina_binary_after,
        "out": _hash_snapshot(output_path, output_file),
        "log": _hash_snapshot(log_path, log_file),
        "stdout": _hash_snapshot(stdout_path, stdout_file),
        "stderr": _hash_snapshot(stderr_path, stderr_file),
    }

    def finalize(current: dict[str, Any]) -> dict[str, Any]:
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
        if cancel_requested:
            final_status = "cancelled"
            message = "用户已取消运行。"
            error_message = ""
            percent = int((current.get("progress") or {}).get("percent") or 0)
        elif run_result.exit_code == 0 and output_ok and not run_result.error:
            final_status = "finished"
            message = "AutoDock Vina 运行完成。"
            error_message = ""
            percent = 100
        else:
            final_status = "failed"
            message = "AutoDock Vina 运行失败。"
            error_message = (
                "AutoDock Vina 结束码为 0，但没有生成非空 out.pdbqt。"
                if run_result.exit_code == 0 and not output_ok and not run_result.error
                else "AutoDock Vina 执行失败，请查看 stderr.txt 和 log.txt。"
            )
            percent = 100
        current.update(
            {
                "status": final_status,
                "stage": final_status,
                "progress": {"percent": percent, "message": message},
                "finished_at": finished_at,
                "duration_seconds": _duration_seconds(current.get("started_at"), finished_at),
                "pid": run_result.pid if run_result.pid is not None else current.get("pid"),
                "exit_code": run_result.exit_code,
                "stdout_file": stdout_file,
                "stderr_file": stderr_file,
                "output_file": output_file,
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
        },
    )
    files_status = get_run_files_status(project_dir, run_id)
    message = {
        "finished": "Vina 运行完成。",
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
        payload["error"] = {
            "code": "VINA_RUN_FAILED" if final_status == "failed" else "VINA_RUN_INTERRUPTED",
            "message": message,
            "raw_error": run_result.error or _tail_text(stderr_path),
            "suggestion": "请查看 stderr.txt、stdout.txt 和 log.txt 后重新准备运行。",
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
        _print_json(import_receptor_pdbqt(sys.argv[2], sys.argv[3]))
        return

    if command == "import-ligand":
        if len(sys.argv) < 4:
            _print_json(_error("PDBQT_IMPORT_ARGS", "导入配体需要 project_dir 和 source_path 参数。"))
            return
        _print_json(import_ligand_pdbqt(sys.argv[2], sys.argv[3]))
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
