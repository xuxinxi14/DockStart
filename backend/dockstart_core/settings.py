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
from pathlib import Path
from typing import Any, Iterator

from dockstart_core.persistence import atomic_write_text

SETTINGS_ENV_VAR = "DOCKSTART_SETTINGS_PATH"
_SETTINGS_PROCESS_LOCK = threading.RLock()


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
class DockStartSettings:
    tool_paths: ToolPaths = field(default_factory=ToolPaths)
    project: ProjectSettings = field(default_factory=ProjectSettings)

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
    )


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
