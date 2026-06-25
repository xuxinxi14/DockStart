"""Persistent DockStart settings."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SETTINGS_ENV_VAR = "DOCKSTART_SETTINGS_PATH"


@dataclass
class ToolPaths:
    vina: str = ""
    python: str = ""


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


def _settings_from_dict(data: dict[str, Any]) -> DockStartSettings:
    tool_paths = data.get("tool_paths") if isinstance(data.get("tool_paths"), dict) else {}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}

    return DockStartSettings(
        tool_paths=ToolPaths(
            vina=str(tool_paths.get("vina", "") or ""),
            python=str(tool_paths.get("python", "") or ""),
        ),
        project=ProjectSettings(
            default_project_dir=str(project.get("default_project_dir", "") or ""),
        ),
    )


def load_settings() -> DockStartSettings:
    settings_path = get_settings_path()
    if not settings_path.exists():
        return DockStartSettings()

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return DockStartSettings()

    if not isinstance(data, dict):
        return DockStartSettings()
    return _settings_from_dict(data)


def save_settings(settings: DockStartSettings) -> DockStartSettings:
    settings_path = get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return settings


def update_tool_path(tool_key: str, path: str) -> DockStartSettings:
    if tool_key not in {"vina", "python"}:
        raise ValueError("tool_key 只支持 vina 或 python。")

    settings = load_settings()
    setattr(settings.tool_paths, tool_key, path.strip())
    return save_settings(settings)


def _response(settings: DockStartSettings) -> dict[str, Any]:
    return {
        "ok": True,
        "settings_path": str(get_settings_path()),
        "settings": settings.to_dict(),
    }


def _error_response(message: str, raw_error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "settings_path": str(get_settings_path()),
        "error": {
            "message": message,
            "raw_error": raw_error,
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
        _print_json(_error_response("保存或读取 DockStart 设置时发生错误。", str(exc)))


if __name__ == "__main__":
    main()
