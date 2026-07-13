"""Bundled demo project helpers for DockStart."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dockstart_core.persistence import atomic_write_text
from dockstart_core.project import CURRENT_PROJECT_SCHEMA_VERSION, load_project
from dockstart_core.toolchain_paths import get_resources_dir

DEMO_DISCLAIMER = "示例只用于学习 DockStart 操作流程，不用于药效判断或科研结论。"
EXPECTED_DEMO_IDS = ("basic_pdbqt", "assisted_raw", "viewer_result")

DEMO_FALLBACKS: dict[str, dict[str, Any]] = {
    "basic_pdbqt": {
        "title": "基础对接示例",
        "description": "已有 receptor.pdbqt 和 ligand.pdbqt，演示最小对接流程。",
        "mode": "basic",
        "requiredTools": ["vina"],
        "tags": ["推荐", "只需要 Vina"],
        "entryStep": "prepare",
        "buttonLabel": "复制并进入准备结构",
        "targetNamePrefix": "basic_demo",
    },
    "assisted_raw": {
        "title": "从原始结构开始示例",
        "description": "从 PDB / SDF 准备 Vina 输入文件，再继续对接。",
        "mode": "assisted",
        "requiredTools": ["python", "rdkit", "meeko"],
        "tags": ["需要工具链", "可使用参考文件"],
        "entryStep": "prepare",
        "buttonLabel": "复制并进入结构准备",
        "targetNamePrefix": "assisted_demo",
    },
    "viewer_result": {
        "title": "结果查看示例",
        "description": "打开已完成的对接结果，查看构象、分数和报告。",
        "mode": "viewer",
        "requiredTools": [],
        "tags": ["无需工具链", "仅查看"],
        "entryStep": "results",
        "buttonLabel": "复制并查看结果",
        "targetNamePrefix": "result_demo",
        "entryRunId": "run_001",
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
    return get_resources_dir() / "examples"


def _demo_dir(demo_id: str) -> Path:
    return _examples_root() / demo_id


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for file_path in path.rglob("*"):
        if file_path.is_file():
            total += file_path.stat().st_size
    return total


def _load_manifest(demo_id: str) -> tuple[dict[str, Any], list[str]]:
    manifest_path = _demo_dir(demo_id) / "manifest.json"
    if not manifest_path.is_file():
        fallback = DEMO_FALLBACKS.get(demo_id, {"title": demo_id, "description": "", "mode": ""})
        return dict(fallback), ["manifest.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - summary payload needs raw error.
        fallback = DEMO_FALLBACKS.get(demo_id, {"title": demo_id, "description": "", "mode": ""})
        payload = dict(fallback)
        payload["manifest_error"] = str(exc)
        return payload, ["manifest.json"]
    if not isinstance(manifest, dict):
        fallback = DEMO_FALLBACKS.get(demo_id, {"title": demo_id, "description": "", "mode": ""})
        payload = dict(fallback)
        payload["manifest_error"] = "manifest.json 不是 JSON 对象。"
        return payload, ["manifest.json"]
    return manifest, []


def _collect_file_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip().replace("\\", "/")
        return [text] if text else []
    if isinstance(value, list):
        refs: list[str] = []
        for item in value:
            refs.extend(_collect_file_refs(item))
        return refs
    if isinstance(value, dict):
        refs = []
        for item in value.values():
            refs.extend(_collect_file_refs(item))
        return refs
    return []


def _manifest_files(manifest: dict[str, Any]) -> list[str]:
    files = _collect_file_refs(manifest.get("files", {}))
    return list(dict.fromkeys(["project.json", *files]))


def _summary_from_manifest(demo_id: str) -> dict[str, Any]:
    template_dir = _demo_dir(demo_id)
    manifest, missing = _load_manifest(demo_id)
    fallback = DEMO_FALLBACKS.get(demo_id, {})
    declared_files = _manifest_files(manifest)
    for relative_path in declared_files:
        if not (template_dir / relative_path).is_file():
            missing.append(relative_path)
    missing = list(dict.fromkeys(missing))

    note = str(manifest.get("note") or fallback.get("note") or DEMO_DISCLAIMER)
    return {
        "demo_type": str(manifest.get("id") or demo_id),
        "id": str(manifest.get("id") or demo_id),
        "title": str(manifest.get("title") or fallback.get("title") or demo_id),
        "description": str(manifest.get("description") or fallback.get("description") or ""),
        "mode": str(manifest.get("mode") or fallback.get("mode") or ""),
        "required_tools": [str(item) for item in manifest.get("requiredTools", fallback.get("requiredTools", []))],
        "tags": [str(item) for item in manifest.get("tags", fallback.get("tags", []))],
        "entry_step": str(manifest.get("entryStep") or fallback.get("entryStep") or ""),
        "entry_run_id": str(manifest.get("entryRunId") or fallback.get("entryRunId") or ""),
        "button_label": str(manifest.get("buttonLabel") or fallback.get("buttonLabel") or "复制并打开"),
        "target_name_prefix": str(manifest.get("targetNamePrefix") or fallback.get("targetNamePrefix") or demo_id),
        "template_dir": str(template_dir),
        "manifest": str(template_dir / "manifest.json"),
        "project_json": str(template_dir / "project.json"),
        "exists": template_dir.is_dir() and not missing,
        "missing_files": missing,
        "size_bytes": _directory_size(template_dir),
        "readme": str(template_dir / "README.md"),
        "files": manifest.get("files", {}),
        "note": note,
        "disclaimer": DEMO_DISCLAIMER,
    }


def _available_demo_ids() -> list[str]:
    ids = list(EXPECTED_DEMO_IDS)
    root = _examples_root()
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if path.is_dir() and (path / "manifest.json").is_file() and path.name not in ids:
                ids.append(path.name)
    return ids


def list_available_demo_projects() -> dict[str, Any]:
    demos = [_summary_from_manifest(demo_id) for demo_id in _available_demo_ids()]
    return {
        "ok": True,
        "examples_root": str(_examples_root()),
        "demos": demos,
        "message": "示例项目列表已读取。",
        "error": None,
    }


def _entry_page(summary: dict[str, Any]) -> str:
    entry_step = str(summary.get("entry_step") or "")
    mode = str(summary.get("mode") or "")
    if entry_step == "results":
        return "result"
    if entry_step == "prepare" and mode == "assisted":
        return "preparation"
    if entry_step == "prepare":
        return "import-pdbqt"
    return "home"


def _next_available_dir(parent_dir: Path, prefix: str) -> Path:
    safe_prefix = prefix.strip() or "demo_project"
    for index in range(1, 1000):
        candidate = parent_dir / f"{safe_prefix}_{index:03d}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法为 {safe_prefix} 生成不冲突的示例目录名。")


def _update_project_json_for_copy(project_json: Path, target_dir: Path, summary: dict[str, Any]) -> None:
    data = json.loads(project_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("示例 project.json 不是 JSON 对象。")

    now = _now_iso()
    data["project_name"] = target_dir.name
    data["project_dir"] = str(target_dir)
    data["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    data["revision"] = 0
    data["updated_at"] = now
    data.setdefault("created_at", now)

    demo = data.get("demo") if isinstance(data.get("demo"), dict) else {}
    demo.update(
        {
            "type": summary["id"],
            "mode": summary.get("mode", ""),
            "title": summary.get("title", ""),
            "description": DEMO_DISCLAIMER,
            "entry_step": summary.get("entry_step", ""),
        },
    )
    data["demo"] = demo
    atomic_write_text(project_json, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def create_demo_project(destination_dir: str, demo_type: str) -> dict[str, Any]:
    demo_id = demo_type.strip()
    if demo_id not in _available_demo_ids():
        return _error(
            "DEMO_TYPE_INVALID",
            "未知示例项目类型。",
            raw_error=demo_type,
            suggestion="请从示例流程页面列出的内置示例中选择。",
        )
    if not destination_dir.strip():
        return _error("DEMO_DESTINATION_REQUIRED", "示例项目保存目录不能为空。", suggestion="请选择一个可写入的父目录。")

    summary = _summary_from_manifest(demo_id)
    template_dir = Path(summary["template_dir"])
    missing_files = summary.get("missing_files", [])
    if not summary.get("exists") or missing_files:
        return _error(
            "DEMO_TEMPLATE_MISSING",
            "示例资源未找到。",
            raw_error=json.dumps(missing_files, ensure_ascii=False),
            suggestion=f"请确认 {template_dir} 中包含 manifest.json 和关键示例文件。",
        )

    parent_dir = Path(destination_dir).expanduser()
    target_dir = _next_available_dir(parent_dir, str(summary.get("target_name_prefix") or demo_id))

    try:
        parent_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_dir, target_dir)
        _update_project_json_for_copy(target_dir / "project.json", target_dir, summary)
        loaded = load_project(str(target_dir))
        if not loaded.get("ok"):
            return loaded
        return {
            "ok": True,
            "project_dir": str(target_dir),
            "project": loaded.get("project"),
            "demo_type": summary["id"],
            "entry_step": summary.get("entry_step", ""),
            "entry_page": _entry_page(summary),
            "entry_run_id": summary.get("entry_run_id", ""),
            "target_name": target_dir.name,
            "disclaimer": DEMO_DISCLAIMER,
            "message": "示例项目已复制并打开。示例只用于学习 DockStart 操作流程，不用于药效判断或科研结论。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - CLI/UI must receive structured JSON.
        return _error(
            "DEMO_CREATE_ERROR",
            "创建示例项目时发生错误。",
            str(exc),
            "请确认目标目录可写，然后重新复制示例。",
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
    if demo_type not in _available_demo_ids():
        warnings.append("project.json 中没有识别到内置示例类型，可能不是 DockStart 当前版本示例。")
    if DEMO_DISCLAIMER not in str(demo.get("description") or ""):
        warnings.append("示例项目缺少“只用于学习操作流程”的说明。")

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
