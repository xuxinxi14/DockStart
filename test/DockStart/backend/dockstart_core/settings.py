"""Persistent DockStart settings."""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from dockstart_core.persistence import atomic_write_text

SETTINGS_ENV_VAR = "DOCKSTART_SETTINGS_PATH"
_SETTINGS_PROCESS_LOCK = threading.RLock()

#: Scoring functions accepted by the global docking defaults.
#: The defaults seed a brand new project's ``VinaSettings.scoring``, which is
#: guarded by ``project.validate_vina_params`` and only accepts ``vina`` and
#: ``vinardo``.  ``ad4`` is deliberately excluded: AutoDock4 scoring needs
#: pre-computed affinity maps and is only reachable through the batch screening
#: workflow's own ``ad4_maps`` protocol, never as a plain project-level default.
DOCKING_DEFAULT_SCORING_FUNCTIONS = ("vina", "vinardo")

#: Inclusive numeric bounds for the global docking defaults.  They stay inside
#: the hard limits enforced further down the workflow -- ``ScreeningResourceLimits``
#: (max_exhaustiveness 128, max_num_modes 50, max_cpu 64) for the batch path and
#: ``project.validate_vina_params`` for the single-project path -- so a value
#: accepted here can never be rejected later during a run.
DOCKING_DEFAULT_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "exhaustiveness": (1, 128),
    "num_modes": (1, 50),
    "cpu": (0, 64),
}

#: ``energy_range`` is capped at the batch screening hard limit (20).
ENERGY_RANGE_BOUNDS = (0.0, 20.0)
SEED_BOUNDS = (0, 2_147_483_647)


class SettingsFileError(RuntimeError):
    """A settings file cannot be read safely and must not be overwritten."""

    def __init__(self, code: str, message: str, raw_error: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.raw_error = raw_error


@dataclass
class ToolPaths:
    vina: str = ""
    python: str = ""
    autogrid4: str = ""


@dataclass
class ProjectSettings:
    default_project_dir: str = ""


@dataclass
class DockingDefaults:
    """Global docking defaults used to seed **new** projects.

    A project keeps its own copy in ``project.json`` once it is created, so a
    project-level value always wins over these defaults.  The literal defaults
    mirror ``dockstart_core.project.VinaSettings``; ``backend/tests/test_settings.py``
    asserts that the two never drift apart.
    """

    scoring: str = "vina"
    exhaustiveness: int = 8
    num_modes: int = 9
    energy_range: float = 4
    cpu: int = 0
    seed: int | None = None

    def vina_overrides(self) -> dict[str, Any]:
        """Return keyword arguments accepted by ``VinaSettings``."""

        return {
            "scoring": self.scoring,
            "exhaustiveness": self.exhaustiveness,
            "num_modes": self.num_modes,
            "energy_range": self.energy_range,
            "cpu": self.cpu,
            "seed": self.seed,
        }


@dataclass
class DockStartSettings:
    tool_paths: ToolPaths = field(default_factory=ToolPaths)
    project: ProjectSettings = field(default_factory=ProjectSettings)
    docking_defaults: DockingDefaults = field(default_factory=DockingDefaults)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_settings_path() -> Path:
    configured_path = os.environ.get(SETTINGS_ENV_VAR, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return _project_root() / "dockstart_settings.json"


def _settings_lock_path(settings_path: Path) -> Path:
    return settings_path.with_name(f".{settings_path.name}.lock")


def _acquire_windows_byte_lock(handle: Any) -> None:
    import msvcrt

    retryable = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
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
def _settings_file_lock(settings_path: Path | None = None) -> Iterator[None]:
    """Serialize settings mutations in this process and across processes."""

    path = settings_path or get_settings_path()
    lock_path = _settings_lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise SettingsFileError(
            "SETTINGS_LOCK_PATH_UNSAFE",
            "DockStart 设置锁文件不能是符号链接。",
            str(lock_path),
        )
    with _SETTINGS_PROCESS_LOCK, lock_path.open("a+b") as handle:
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


def _coerce_bounded_int(source: dict[str, Any], key: str, default: int) -> int:
    """Return a clamped int, falling back to ``default`` for unusable input.

    Settings are user-editable text, so a hand-edited file must degrade to the
    documented default instead of raising during application start-up.  A
    non-integral number is rejected rather than silently rounded, because these
    values are docking parameters where a quiet change of meaning is worse than
    a documented fallback.
    """

    raw = source.get(key, None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not value.is_integer():
        return default
    low, high = DOCKING_DEFAULT_INT_BOUNDS[key]
    return max(low, min(high, int(value)))


def _coerce_bounded_float(source: dict[str, Any], key: str, default: float) -> float:
    raw = source.get(key, None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value != value or value in (float("inf"), float("-inf")):
        return default
    low, high = ENERGY_RANGE_BOUNDS
    return max(low, min(high, value))


def _coerce_optional_seed(source: dict[str, Any], default: int | None) -> int | None:
    raw = source.get("seed", None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not value.is_integer():
        return default
    low, high = SEED_BOUNDS
    return max(low, min(high, int(value)))


def _coerce_scoring(source: dict[str, Any], default: str) -> str:
    raw = source.get("scoring", None)
    if not isinstance(raw, str):
        return default
    value = raw.strip().lower()
    if value not in DOCKING_DEFAULT_SCORING_FUNCTIONS:
        return default
    return value


def _docking_defaults_from_dict(data: Any) -> DockingDefaults:
    source = data if isinstance(data, dict) else {}
    fallback = DockingDefaults()
    return DockingDefaults(
        scoring=_coerce_scoring(source, fallback.scoring),
        exhaustiveness=_coerce_bounded_int(source, "exhaustiveness", fallback.exhaustiveness),
        num_modes=_coerce_bounded_int(source, "num_modes", fallback.num_modes),
        energy_range=_coerce_bounded_float(source, "energy_range", fallback.energy_range),
        cpu=_coerce_bounded_int(source, "cpu", fallback.cpu),
        seed=_coerce_optional_seed(source, fallback.seed),
    )


def _settings_from_dict(data: dict[str, Any]) -> DockStartSettings:
    tool_paths = data.get("tool_paths") if isinstance(data.get("tool_paths"), dict) else {}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}

    return DockStartSettings(
        tool_paths=ToolPaths(
            vina=str(tool_paths.get("vina", "") or ""),
            python=str(tool_paths.get("python", "") or ""),
            autogrid4=str(tool_paths.get("autogrid4", "") or ""),
        ),
        project=ProjectSettings(
            default_project_dir=str(project.get("default_project_dir", "") or ""),
        ),
        docking_defaults=_docking_defaults_from_dict(data.get("docking_defaults")),
    )


def docking_default_overrides() -> dict[str, Any]:
    """Return the global docking defaults as ``VinaSettings`` keyword arguments.

    This is the single integration point between the Settings page and the
    docking workflow.  Any failure to read the settings file falls back to the
    documented defaults so that project creation never depends on a valid
    settings file.
    """

    try:
        settings = load_settings()
    except Exception:  # noqa: BLE001 - a damaged settings file must not block work.
        return DockingDefaults().vina_overrides()
    return settings.docking_defaults.vina_overrides()


def _load_settings_unlocked(settings_path: Path) -> DockStartSettings:
    if not settings_path.exists():
        return DockStartSettings()

    try:
        text = settings_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise SettingsFileError(
            "SETTINGS_READ_ERROR",
            "无法读取 DockStart 设置文件，已拒绝使用默认值覆盖原文件。",
            str(exc),
        ) from exc
    try:
        data = json.loads(text)
    except Exception as exc:
        raise SettingsFileError(
            "SETTINGS_JSON_INVALID",
            "DockStart 设置文件不是有效 JSON，已保留原文件且不会自动覆盖。",
            str(exc),
        ) from exc

    if not isinstance(data, dict):
        raise SettingsFileError(
            "SETTINGS_JSON_INVALID",
            "DockStart 设置文件必须是 JSON 对象，已保留原文件且不会自动覆盖。",
            f"actual_type={type(data).__name__}",
        )
    return _settings_from_dict(data)


def load_settings() -> DockStartSettings:
    settings_path = get_settings_path()
    return _load_settings_unlocked(settings_path)


def _save_settings_unlocked(
    settings_path: Path,
    settings: DockStartSettings,
) -> DockStartSettings:
    atomic_write_text(
        settings_path,
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    return settings


def save_settings(settings: DockStartSettings) -> DockStartSettings:
    settings_path = get_settings_path()
    with _settings_file_lock(settings_path):
        # A full save is still fail-closed when an existing file is damaged.
        # Otherwise a UI save could silently destroy the only recoverable copy.
        if settings_path.exists():
            _load_settings_unlocked(settings_path)
        return _save_settings_unlocked(settings_path, settings)


def update_tool_path(tool_key: str, path: str) -> DockStartSettings:
    if tool_key not in {"vina", "python", "autogrid4"}:
        raise ValueError("tool_key 只支持 vina、python 或 autogrid4。")

    settings_path = get_settings_path()
    with _settings_file_lock(settings_path):
        # Re-read only after acquiring the cross-process lock.  This keeps two
        # independent tool-path updates from replacing one another.
        settings = _load_settings_unlocked(settings_path)
        setattr(settings.tool_paths, tool_key, path.strip())
        return _save_settings_unlocked(settings_path, settings)


def _probe_write_access(directory: Path) -> dict[str, Any]:
    """Really write and remove a probe file; never report a synthetic result."""

    probe_path = directory / f".dockstart-settings-probe-{os.getpid()}-{int(time.time() * 1000)}.tmp"
    try:
        with probe_path.open("w", encoding="utf-8") as handle:
            handle.write("dockstart settings write probe\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001 - diagnostics must report, not raise.
        return {
            "ok": False,
            "message": "设置目录不可写，保存设置会失败。",
            "raw_error": f"{type(exc).__name__}: {exc}",
            "suggestion": "请检查设置目录的写入权限，或以有权限的账号重新运行 DockStart。",
        }
    finally:
        try:
            probe_path.unlink()
        except OSError:
            pass
    return {
        "ok": True,
        "message": "设置目录可写，保存设置会立即生效。",
        "raw_error": "",
        "suggestion": "",
    }


def _file_stat_report(settings_path: Path) -> tuple[int, str]:
    try:
        stat_result = settings_path.stat()
    except OSError:
        return 0, ""
    modified_at = datetime.fromtimestamp(stat_result.st_mtime, UTC).astimezone().isoformat()
    return int(stat_result.st_size), modified_at


def diagnose_settings() -> dict[str, Any]:
    """Report the real state of the settings store, including a write probe."""

    settings_path = get_settings_path()
    directory = settings_path.parent
    file_exists = settings_path.is_file()
    size_bytes, modified_at = _file_stat_report(settings_path) if file_exists else (0, "")

    load_report: dict[str, Any]
    current_settings: dict[str, Any] | None = None
    try:
        current_settings = load_settings().to_dict()
        load_report = {
            "ok": True,
            "message": "设置文件可正常读取。",
            "raw_error": "",
            "suggestion": "",
        }
    except SettingsFileError as exc:
        load_report = {
            "ok": False,
            "message": str(exc),
            "raw_error": exc.raw_error,
            "suggestion": "请先备份并修复或移除损坏的设置文件；DockStart 不会自动覆盖它。",
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must report, not raise.
        load_report = {
            "ok": False,
            "message": "读取设置文件时发生未知错误。",
            "raw_error": f"{type(exc).__name__}: {exc}",
            "suggestion": "请检查设置文件的读取权限后重试。",
        }

    if directory.is_dir():
        write_report = _probe_write_access(directory)
    else:
        write_report = {
            "ok": False,
            "message": "设置目录不存在，无法保存设置。",
            "raw_error": f"missing_directory={directory}",
            "suggestion": "请创建该目录，或删除 DOCKSTART_SETTINGS_PATH 环境变量以使用默认位置。",
        }

    return {
        "ok": True,
        "settings_path": str(settings_path),
        "diagnostics": {
            "settings_path": str(settings_path),
            "settings_dir": str(directory),
            "dir_exists": directory.is_dir(),
            "file_exists": file_exists,
            "file_size_bytes": size_bytes,
            "file_modified_at": modified_at,
            "lock_file": str(_settings_lock_path(settings_path)),
            "env_override": os.environ.get(SETTINGS_ENV_VAR, "").strip(),
            "readable": bool(load_report["ok"]),
            "writable": bool(write_report["ok"]),
            "load": load_report,
            "write_probe": write_report,
            "current_settings": current_settings,
        },
    }


def _response(settings: DockStartSettings) -> dict[str, Any]:
    return {
        "ok": True,
        "settings_path": str(get_settings_path()),
        "settings": settings.to_dict(),
    }


def _error_response(
    message: str,
    raw_error: str = "",
    *,
    code: str = "SETTINGS_ERROR",
    suggestion: str = "请检查设置文件权限和 JSON 内容后重试。",
) -> dict[str, Any]:
    return {
        "ok": False,
        "settings_path": str(get_settings_path()),
        "error": {
            "code": code,
            "title": message,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "get"

    try:
        if command == "get":
            _print_json(_response(load_settings()))
            return

        if command == "save-json":
            if len(sys.argv) < 3:
                raise ValueError("save-json 需要 JSON 参数。")
            data = json.loads(sys.argv[2])
            if not isinstance(data, dict):
                raise ValueError("settings JSON 必须是对象。")
            _print_json(_response(save_settings(_settings_from_dict(data))))
            return

        if command == "update-tool-path":
            if len(sys.argv) < 4:
                raise ValueError("update-tool-path 需要 tool_key 和 path 参数。")
            _print_json(_response(update_tool_path(sys.argv[2], sys.argv[3])))
            return

        if command == "diagnose":
            _print_json(diagnose_settings())
            return

        raise ValueError(f"未知 settings 命令：{command}")
    except Exception as exc:  # noqa: BLE001 - CLI must return structured JSON.
        if isinstance(exc, SettingsFileError):
            _print_json(
                _error_response(
                    str(exc),
                    exc.raw_error,
                    code=exc.code,
                    suggestion=(
                        "请先备份并修复或移除损坏的设置文件；DockStart 不会自动覆盖它。"
                    ),
                )
            )
            return
        _print_json(_error_response("保存或读取 DockStart 设置时发生错误。", str(exc)))


if __name__ == "__main__":
    main()
