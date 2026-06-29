"""Small bundled demo project helpers for DockStart."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dockstart_core.project import load_project
from dockstart_core.toolchain_paths import get_project_root

DEMO_DISCLAIMER = "仅用于软件流程演示，不用于科研结论。"

DEMO_TEMPLATES: dict[str, dict[str, str]] = {
    "basic_pdbqt": {
        "directory": "demo_basic_project",
        "title": "Basic Mode 示例",
        "description": "已有 receptor.pdbqt 和 ligand.pdbqt 的最低依赖流程示例。",
    },
    "assisted_raw": {
        "directory": "demo_assisted_project",
        "title": "Assisted Mode 示例",
        "description": "已有 raw PDB/SDF，等待 RDKit/Meeko 自动准备 PDBQT 的流程示例。",
    },
    "viewer_only": {
        "directory": "demo_basic_project",
        "title": "3D Viewer 示例",
        "description": "复用 Basic 示例的玩具结构，用于查看 viewer 和 Box 流程。",
    },
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error(code: str, message: str, raw_error: str = "", suggestion: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "demo": None,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _examples_root() -> Path:
    return get_project_root() / "examples"


def _template_dir(demo_type: str) -> Path:
    template = DEMO_TEMPLATES.get(demo_type)
    if not template:
        raise ValueError(f"未知 demo_type：{demo_type}")
    return _examples_root() / template["directory"]


def _directory_size(path: Path) -> int:
    total = 0
    for file_path in path.rglob("*"):
        if file_path.is_file():
            total += file_path.stat().st_size
    return total


def list_available_demo_projects() -> dict[str, Any]:
    demos: list[dict[str, Any]] = []
    for demo_type, meta in DEMO_TEMPLATES.items():
        template_dir = _examples_root() / meta["directory"]
        project_json = template_dir / "project.json"
        readme = template_dir / "README.md"
        demos.append(
            {
                "demo_type": demo_type,
                "title": meta["title"],
                "description": meta["description"],
                "template_dir": str(template_dir),
                "project_json": str(project_json),
                "exists": template_dir.is_dir() and project_json.is_file(),
                "size_bytes": _directory_size(template_dir) if template_dir.is_dir() else 0,
                "readme": str(readme),
                "disclaimer": DEMO_DISCLAIMER,
            }
        )
    return {
        "ok": True,
        "examples_root": str(_examples_root()),
        "demos": demos,
        "message": "示例项目列表已读取。",
        "error": None,
    }


def _update_project_json_for_copy(project_json: Path, target_dir: Path, demo_type: str) -> None:
    data = json.loads(project_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("示例 project.json 不是 JSON 对象。")
    data["project_name"] = target_dir.name
    data["project_dir"] = str(target_dir)
    data["updated_at"] = _now_iso()
    demo = data.get("demo") if isinstance(data.get("demo"), dict) else {}
    demo["type"] = demo_type
    demo["description"] = DEMO_DISCLAIMER
    data["demo"] = demo
    project_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_demo_project(destination_dir: str, demo_type: str) -> dict[str, Any]:
    if demo_type not in DEMO_TEMPLATES:
        return _error(
            "DEMO_TYPE_INVALID",
            "未知示例项目类型。",
            raw_error=demo_type,
            suggestion="demo_type 只能是 basic_pdbqt、assisted_raw 或 viewer_only。",
        )
    if not destination_dir.strip():
        return _error("DEMO_DESTINATION_REQUIRED", "示例项目保存目录不能为空。", suggestion="请选择一个可写入的父目录。")

    template_dir = _template_dir(demo_type)
    if not template_dir.is_dir() or not (template_dir / "project.json").is_file():
        return _error(
            "DEMO_TEMPLATE_MISSING",
            "没有找到内置示例项目模板。",
            raw_error=str(template_dir),
            suggestion="请确认 examples/demo_basic_project 或 examples/demo_assisted_project 随应用分发。",
        )

    target_name = "demo_viewer_project" if demo_type == "viewer_only" else DEMO_TEMPLATES[demo_type]["directory"]
    target_dir = Path(destination_dir).expanduser() / target_name
    if target_dir.exists():
        return _error(
            "DEMO_PROJECT_EXISTS",
            "目标目录已存在，DockStart 不会覆盖已有示例项目。",
            raw_error=str(target_dir),
            suggestion="请选择其他保存目录，或先手动移走已有目录。",
        )

    try:
        shutil.copytree(template_dir, target_dir)
        _update_project_json_for_copy(target_dir / "project.json", target_dir, demo_type)
        loaded = load_project(str(target_dir))
        if not loaded.get("ok"):
            return loaded
        return {
            "ok": True,
            "project_dir": str(target_dir),
            "project": loaded.get("project"),
            "demo_type": demo_type,
            "disclaimer": DEMO_DISCLAIMER,
            "message": "示例项目已创建。示例仅用于软件流程演示，不用于科研结论。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - CLI/UI must receive structured JSON.
        return _error(
            "DEMO_CREATE_ERROR",
            "创建示例项目时发生错误。",
            str(exc),
            "请确认目标目录可写，且没有同名示例项目目录。",
        )


def validate_demo_project(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    try:
        raw_project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _error(
            "DEMO_PROJECT_JSON_READ_ERROR",
            "读取示例 project.json 时发生错误。",
            str(exc),
            "请确认示例项目目录中存在可读取的 project.json。",
        )
    project = raw_project if isinstance(raw_project, dict) else {}
    demo = project.get("demo") if isinstance(project.get("demo"), dict) else {}
    demo_type = str(demo.get("type") or "")
    warnings: list[str] = []
    if demo_type not in DEMO_TEMPLATES:
        warnings.append("project.json 中没有识别到 demo.type，可能不是 DockStart 内置示例。")
    if DEMO_DISCLAIMER not in str(demo.get("description") or ""):
        warnings.append("示例项目缺少“仅用于软件流程演示”的说明。")

    checks = []
    for label, relative_path in [
        ("receptor.raw_file", project.get("receptor", {}).get("raw_file", "")),
        ("ligand.raw_file", project.get("ligand", {}).get("raw_file", "")),
        ("receptor.file", project.get("receptor", {}).get("file", "")),
        ("ligand.file", project.get("ligand", {}).get("file", "")),
    ]:
        if not relative_path:
            continue
        file_path = project_path / str(relative_path)
        checks.append(
            {
                "key": label,
                "path": str(relative_path),
                "exists": file_path.is_file(),
                "size_bytes": file_path.stat().st_size if file_path.is_file() else 0,
            }
        )

    ok = all(item["exists"] and item["size_bytes"] > 0 for item in checks)
    return {
        "ok": ok,
        "project_dir": str(project_path),
        "project": project,
        "demo_type": demo_type,
        "checks": checks,
        "warnings": warnings,
        "message": "示例项目校验完成。" if ok else "示例项目文件不完整。",
        "error": None
        if ok
        else {
            "code": "DEMO_PROJECT_INCOMPLETE",
            "message": "示例项目文件不完整。",
            "raw_error": json.dumps(checks, ensure_ascii=False),
            "suggestion": "请重新创建示例项目，或检查示例资源是否随应用分发。",
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    try:
        if command == "list":
            _print_json(list_available_demo_projects())
            return
        if command == "create":
            if len(sys.argv) < 4:
                raise ValueError("create 需要 destination_dir 和 demo_type 参数。")
            _print_json(create_demo_project(sys.argv[2], sys.argv[3]))
            return
        if command == "validate":
            if len(sys.argv) < 3:
                raise ValueError("validate 需要 project_dir 参数。")
            _print_json(validate_demo_project(sys.argv[2]))
            return
        raise ValueError(f"未知 demo_projects 命令：{command}")
    except Exception as exc:  # noqa: BLE001
        _print_json(_error("DEMO_PROJECT_COMMAND_ERROR", "示例项目命令执行失败。", str(exc)))


if __name__ == "__main__":
    main()
