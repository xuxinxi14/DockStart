"""Preparation workflow status and prerequisites for DockStart projects."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from adapters import meeko_adapter, rdkit_adapter
from dockstart_core.project import _error, _project_from_dict, load_project, save_project
from dockstart_core.preparation_models import (
    ALLOWED_PREPARATION_TARGETS,
    PreparationTarget,
    default_preparation_result,
)
from dockstart_core.toolchain import get_resolved_python


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

    path = project_path / value
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


def _tool_status() -> dict[str, Any]:
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


def get_preparation_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    tools = _tool_status()
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

    payload = get_preparation_status(project.project_dir)
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


def reset_preparation_status(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    setattr(project.preparation, normalized_target, default_preparation_result(normalized_target))
    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    payload = get_preparation_status(project.project_dir)
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

    if command == "reset":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_RESET_ARGS", "重置准备状态需要 project_dir 和 target 参数。"))
            return
        _print_json(reset_preparation_status(sys.argv[2], sys.argv[3]))
        return

    _print_json(_error("PREPARATION_COMMAND_UNKNOWN", f"未知准备命令：{command}"))


if __name__ == "__main__":
    main()
