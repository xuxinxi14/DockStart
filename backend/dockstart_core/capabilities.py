"""DockStart usage modes and capability profile helpers."""

from __future__ import annotations

import json
import sys
from typing import Any

from adapters import viewer_adapter
from dockstart_core import __version__
from dockstart_core.demo_projects import list_available_demo_projects
from dockstart_core.project import get_project_workflow_status
from dockstart_core.toolchain import get_toolchain_status

ModeName = str


def _ok_tool(tool: dict[str, Any] | None) -> bool:
    return bool(tool and tool.get("status") == "ok")


def _demo_project_candidates() -> list[dict[str, Any]]:
    response = list_available_demo_projects()
    demos = response.get("demos") if isinstance(response.get("demos"), list) else []
    return [demo for demo in demos if isinstance(demo, dict) and demo.get("exists")]


def _demo_available() -> bool:
    return bool(_demo_project_candidates())


def _build_mode_summary(toolchain: dict[str, Any]) -> dict[str, Any]:
    vina = toolchain.get("active_vina") if isinstance(toolchain.get("active_vina"), dict) else {}
    python = toolchain.get("resolved_python") if isinstance(toolchain.get("resolved_python"), dict) else {}
    rdkit = toolchain.get("rdkit_for_python") if isinstance(toolchain.get("rdkit_for_python"), dict) else {}
    meeko = toolchain.get("meeko_for_python") if isinstance(toolchain.get("meeko_for_python"), dict) else {}

    basic_available = _ok_tool(vina)
    assisted_available = basic_available and _ok_tool(python) and _ok_tool(rdkit) and _ok_tool(meeko)
    demo_available = _demo_available()

    blocking_items: list[dict[str, str]] = []
    if not basic_available:
        blocking_items.append(
            {
                "mode": "basic",
                "item": "AutoDock Vina",
                "message": "Basic Mode 需要可用的 AutoDock Vina。",
            }
        )
    if not assisted_available:
        for label, tool, mode_item in [
            ("AutoDock Vina", vina, "vina"),
            ("Python", python, "python"),
            ("RDKit", rdkit, "rdkit"),
            ("Meeko", meeko, "meeko"),
        ]:
            if not _ok_tool(tool):
                blocking_items.append(
                    {
                        "mode": "assisted",
                        "item": mode_item,
                        "message": f"Assisted Mode 需要 {label} 可用。",
                    }
                )
    if not demo_available:
        blocking_items.append(
            {
                "mode": "demo",
                "item": "demo_projects",
                "message": "Demo Mode 需要示例项目资源。",
            }
        )

    if assisted_available:
        recommended_mode = "assisted"
        next_action = "工具链完整，可从 raw PDB/SDF 自动准备 PDBQT，也可以直接导入已有 PDBQT。"
    elif basic_available:
        recommended_mode = "basic"
        next_action = "你当前可以使用 Basic Mode：导入已准备好的 PDBQT，设置 Box 后运行 Vina。"
    elif demo_available:
        recommended_mode = "demo"
        next_action = "可先打开示例项目了解流程；未检测到 Vina 时只能做非运行演示。"
    else:
        recommended_mode = "setup"
        next_action = "请先配置 AutoDock Vina，或补充示例项目资源后体验 Demo Mode。"

    return {
        "basic_mode_available": basic_available,
        "assisted_mode_available": assisted_available,
        "demo_mode_available": demo_available,
        "recommended_mode": recommended_mode,
        "blocking_items": blocking_items,
        "next_action": next_action,
    }


def get_app_capability_profile() -> dict[str, Any]:
    """Return a structured app-level Basic/Assisted/Demo capability profile."""

    toolchain = get_toolchain_status()
    viewer_status = viewer_adapter.detect().to_dict()
    mode_summary = _build_mode_summary(toolchain)

    return {
        "ok": True,
        "app_version": __version__,
        "vina_status": toolchain.get("active_vina"),
        "python_status": toolchain.get("resolved_python"),
        "rdkit_status": toolchain.get("rdkit_for_python"),
        "meeko_status": toolchain.get("meeko_for_python"),
        "viewer_status": viewer_status,
        "basic_mode_available": mode_summary["basic_mode_available"],
        "assisted_mode_available": mode_summary["assisted_mode_available"],
        "demo_mode_available": mode_summary["demo_mode_available"],
        "recommended_mode": mode_summary["recommended_mode"],
        "blocking_items": mode_summary["blocking_items"],
        "next_action": mode_summary["next_action"],
        "demo_projects": _demo_project_candidates(),
        "message": "DockStart 运行模式能力已读取。",
        "error": None,
    }


def _workflow_ok(workflow: dict[str, Any], section: str, target: str) -> bool:
    status = workflow.get(section)
    if not isinstance(status, dict):
        return False
    item = status.get(target)
    return isinstance(item, dict) and item.get("status") == "ok"


def get_minimum_requirements_status(project_dir: str) -> dict[str, Any]:
    """Return project-level minimum requirements for Basic and Assisted paths."""

    workflow = get_project_workflow_status(project_dir)
    if not workflow.get("ok"):
        return workflow

    profile = get_app_capability_profile()
    receptor_prepared = _workflow_ok(workflow, "prepared", "receptor")
    ligand_prepared = _workflow_ok(workflow, "prepared", "ligand")
    receptor_raw = _workflow_ok(workflow, "raw", "receptor")
    ligand_raw = _workflow_ok(workflow, "raw", "ligand")
    basic_files_ready = receptor_prepared and ligand_prepared
    assisted_inputs_ready = receptor_raw and ligand_raw

    basic_ready = bool(profile["basic_mode_available"] and basic_files_ready)
    assisted_ready = bool(profile["assisted_mode_available"] and assisted_inputs_ready)

    missing: list[dict[str, str]] = []
    if not profile["basic_mode_available"]:
        missing.append({"mode": "basic", "item": "vina", "message": "缺少可用 AutoDock Vina。"})
    if not receptor_prepared:
        missing.append({"mode": "basic", "item": "receptor_pdbqt", "message": "缺少 prepared/receptor.pdbqt。"})
    if not ligand_prepared:
        missing.append({"mode": "basic", "item": "ligand_pdbqt", "message": "缺少 prepared/ligand.pdbqt。"})
    if not assisted_inputs_ready:
        missing.append({"mode": "assisted", "item": "raw_files", "message": "缺少 receptor 或 ligand 原始结构文件。"})
    if not profile["assisted_mode_available"]:
        missing.extend(
            item for item in profile["blocking_items"] if item.get("mode") == "assisted"
        )

    if basic_ready:
        next_action = "Basic Mode 最低依赖已满足，可以设置 Box、生成配置并运行 Vina。"
    elif profile["basic_mode_available"]:
        next_action = "Vina 可用，请先导入 receptor.pdbqt 和 ligand.pdbqt。"
    elif assisted_ready:
        next_action = "Assisted Mode 输入和工具链已满足，可以准备 PDBQT。"
    else:
        next_action = "请根据缺失项配置工具链或补齐项目文件。"

    return {
        "ok": True,
        "project_dir": workflow.get("project_dir", project_dir),
        "project": workflow.get("project"),
        "basic_mode": {
            "available": bool(profile["basic_mode_available"]),
            "files_ready": basic_files_ready,
            "ready": basic_ready,
        },
        "assisted_mode": {
            "available": bool(profile["assisted_mode_available"]),
            "raw_inputs_ready": assisted_inputs_ready,
            "ready": assisted_ready,
        },
        "demo_mode": {
            "available": bool(profile["demo_mode_available"]),
            "projects": profile.get("demo_projects", []),
        },
        "missing_items": missing,
        "next_action": next_action,
        "workflow": workflow,
        "message": "项目最低依赖状态已读取。",
        "error": None,
    }


def get_project_mode_recommendation(project_dir: str) -> dict[str, Any]:
    """Recommend Basic, Assisted, or Demo mode for a specific project."""

    requirements = get_minimum_requirements_status(project_dir)
    if not requirements.get("ok"):
        return requirements

    workflow = requirements.get("workflow") if isinstance(requirements.get("workflow"), dict) else {}
    receptor_prepared = _workflow_ok(workflow, "prepared", "receptor")
    ligand_prepared = _workflow_ok(workflow, "prepared", "ligand")
    receptor_raw = _workflow_ok(workflow, "raw", "receptor")
    ligand_raw = _workflow_ok(workflow, "raw", "ligand")

    if requirements["basic_mode"]["ready"]:
        recommended_mode: ModeName = "basic"
        reason = "项目已有受体和配体 PDBQT，最低依赖路径最短。"
        next_action = "继续设置 Box、生成运行配置并运行 Vina。"
    elif requirements["assisted_mode"]["ready"]:
        recommended_mode = "assisted"
        reason = "项目已有 raw receptor / ligand，且 RDKit/Meeko 工具链可用。"
        next_action = "先准备 Vina 输入文件，再进入 Box 与 Vina 流程。"
    elif receptor_prepared or ligand_prepared:
        recommended_mode = "basic"
        reason = "项目已有部分 PDBQT，可以继续补齐另一个 Vina 输入文件。"
        next_action = "补齐 receptor.pdbqt 和 ligand.pdbqt。"
    elif receptor_raw or ligand_raw:
        recommended_mode = "assisted" if requirements["assisted_mode"]["available"] else "basic"
        reason = "项目已有 raw 文件，但还没有完整 Vina 输入文件。"
        next_action = (
            "使用 RDKit/Meeko 自动准备 PDBQT。"
            if requirements["assisted_mode"]["available"]
            else "配置 RDKit/Meeko Python，或手动准备并导入 PDBQT。"
        )
    elif requirements["demo_mode"]["available"]:
        recommended_mode = "demo"
        reason = "当前项目还没有结构文件，可以先用示例理解完整流程。"
        next_action = "打开示例项目，或直接导入已有 PDBQT。"
    else:
        recommended_mode = "basic"
        reason = "Basic Mode 是最低依赖路径。"
        next_action = "先创建项目并导入 receptor.pdbqt / ligand.pdbqt。"

    return {
        "ok": True,
        "project_dir": requirements.get("project_dir", project_dir),
        "project": requirements.get("project"),
        "recommended_mode": recommended_mode,
        "reason": reason,
        "next_action": next_action,
        "minimum_requirements": requirements,
        "message": "项目运行模式建议已生成。",
        "error": None,
    }


def _error_response(message: str, raw_error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "CAPABILITY_PROFILE_ERROR",
            "message": message,
            "raw_error": raw_error,
            "suggestion": "请确认 Python 后端和项目目录可访问。",
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "profile"
    try:
        if command == "profile":
            _print_json(get_app_capability_profile())
            return
        if command == "project-recommendation":
            if len(sys.argv) < 3:
                raise ValueError("project-recommendation 需要 project_dir 参数。")
            _print_json(get_project_mode_recommendation(sys.argv[2]))
            return
        if command == "minimum-requirements":
            if len(sys.argv) < 3:
                raise ValueError("minimum-requirements 需要 project_dir 参数。")
            _print_json(get_minimum_requirements_status(sys.argv[2]))
            return
        raise ValueError(f"未知 capabilities 命令：{command}")
    except Exception as exc:  # noqa: BLE001 - CLI must return structured JSON.
        _print_json(_error_response("读取 DockStart 运行模式能力时发生错误。", str(exc)))


if __name__ == "__main__":
    main()
