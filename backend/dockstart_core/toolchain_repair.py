"""Structured toolchain repair guidance for DockStart."""

from __future__ import annotations

import json
import sys
from typing import Any

from dockstart_core.toolchain import get_toolchain_status

DOC_LINK = "docs/toolchain_repair_guide.md"


def _tool_status(tool: dict[str, Any] | None) -> str:
    if not isinstance(tool, dict):
        return "unknown"
    return str(tool.get("status", "unknown") or "unknown")


def _tool_path(tool: dict[str, Any] | None) -> str:
    if not isinstance(tool, dict):
        return ""
    return str(tool.get("path", "") or "")


def _is_microsoft_store_python(path: str) -> bool:
    normalized = path.replace("/", "\\").lower()
    return "windowsapps" in normalized or "pythonsoftwarefoundation" in normalized


def _suggestion(
    issue: str,
    severity: str,
    affected_mode: str,
    explanation: str,
    recommended_fix: str,
    manual_steps: list[str],
    copyable_commands: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "issue": issue,
        "severity": severity,
        "affected_mode": affected_mode,
        "explanation": explanation,
        "recommended_fix": recommended_fix,
        "documentation_link": DOC_LINK,
        "copyable_commands": copyable_commands or [],
        "manual_steps": manual_steps,
    }


def get_vina_setup_suggestion(toolchain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return a Vina repair suggestion when Basic Mode is blocked."""

    status = toolchain or get_toolchain_status()
    active_vina = status.get("active_vina") if isinstance(status.get("active_vina"), dict) else None
    if _tool_status(active_vina) == "ok":
        return None
    return _suggestion(
        issue="vina_missing",
        severity="error",
        affected_mode="Basic Mode / Assisted Mode",
        explanation="DockStart 没有检测到可用 AutoDock Vina，因此无法执行真实 docking。",
        recommended_fix="安装或准备 AutoDock Vina，并在设置页填写 vina.exe 路径；如果使用打包资源，可先运行 bundled Vina 装配脚本。",
        manual_steps=[
            "确认本机已有 AutoDock Vina。",
            "在命令行运行 vina --version 或 vina.exe --version 验证。",
            "打开 DockStart 设置页，填写 vina.exe 的完整路径。",
            "回到工具链页点击“重新检测”。",
        ],
        copyable_commands=["vina --version"],
    )


def get_python_toolchain_setup_suggestion(toolchain: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return a Python/RDKit/Meeko repair suggestion when Assisted Mode is blocked."""

    status = toolchain or get_toolchain_status()
    python = status.get("resolved_python") if isinstance(status.get("resolved_python"), dict) else None
    rdkit = status.get("rdkit_for_python") if isinstance(status.get("rdkit_for_python"), dict) else None
    meeko = status.get("meeko_for_python") if isinstance(status.get("meeko_for_python"), dict) else None

    python_ok = _tool_status(python) == "ok"
    rdkit_ok = _tool_status(rdkit) == "ok"
    meeko_ok = _tool_status(meeko) == "ok"
    if python_ok and rdkit_ok and meeko_ok:
        return None

    missing_parts = []
    if not python_ok:
        missing_parts.append("Python")
    if not rdkit_ok:
        missing_parts.append("RDKit")
    if not meeko_ok:
        missing_parts.append("Meeko")
    missing_text = "、".join(missing_parts) if missing_parts else "Python 工具链"

    return _suggestion(
        issue="python_rdkit_meeko_incomplete",
        severity="warning",
        affected_mode="Assisted Mode",
        explanation=f"自动准备 PDBQT 需要可用 Python + RDKit + Meeko；当前缺少或未检测通过：{missing_text}。",
        recommended_fix="推荐创建独立 conda/mamba 环境 dockstart-rdkit-meeko，然后在 DockStart 设置页配置该环境的 python.exe。",
        manual_steps=[
            "安装 Miniconda、Miniforge、mamba 或 micromamba。",
            "创建独立环境，优先使用 Python 3.11 和 conda-forge。",
            "确认 rdkit 和 meeko 可以 import。",
            "在 DockStart 设置页填写该环境的 python.exe 路径。",
            "回到工具链页点击“重新检测”。",
        ],
        copyable_commands=[
            "conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy",
            "conda run -n dockstart-rdkit-meeko python -c \"import rdkit, meeko; print('RDKit/Meeko ok')\"",
        ],
    )


def _microsoft_store_python_suggestion(toolchain: dict[str, Any]) -> dict[str, Any] | None:
    python = toolchain.get("resolved_python") if isinstance(toolchain.get("resolved_python"), dict) else None
    python_path = _tool_path(python)
    if not python_path or not _is_microsoft_store_python(python_path):
        return None
    return _suggestion(
        issue="microsoft_store_python_not_recommended",
        severity="warning",
        affected_mode="Assisted Mode",
        explanation="当前 Python 看起来来自 Microsoft Store。该环境不适合作为 RDKit/Meeko 工具链，包管理和路径行为容易不稳定。",
        recommended_fix="创建独立 conda/mamba 环境，并在 DockStart 设置页配置该环境的 python.exe。",
        manual_steps=[
            "不要把 RDKit/Meeko 安装进 Microsoft Store Python。",
            "创建 dockstart-rdkit-meeko 独立环境。",
            "把新环境的 python.exe 填入 DockStart 设置页。",
        ],
        copyable_commands=[
            "conda create -n dockstart-rdkit-meeko -c conda-forge python=3.11 rdkit meeko numpy scipy",
        ],
    )


def get_toolchain_repair_suggestions() -> dict[str, Any]:
    """Return structured, offline repair suggestions for current toolchain state."""

    toolchain = get_toolchain_status()
    suggestions = [
        suggestion
        for suggestion in [
            get_vina_setup_suggestion(toolchain),
            get_python_toolchain_setup_suggestion(toolchain),
            _microsoft_store_python_suggestion(toolchain),
        ]
        if suggestion is not None
    ]
    return {
        "ok": True,
        "suggestions": suggestions,
        "message": "工具链修复建议已生成。" if suggestions else "当前没有需要修复的关键工具链问题。",
        "error": None,
    }


def _error_response(message: str, raw_error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "suggestions": [],
        "message": message,
        "error": {
            "code": "TOOLCHAIN_REPAIR_SUGGESTION_ERROR",
            "message": message,
            "raw_error": raw_error,
            "suggestion": "请先确认 Python 后端可以读取工具链状态。",
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    try:
        _print_json(get_toolchain_repair_suggestions())
    except Exception as exc:  # noqa: BLE001 - CLI must return structured JSON.
        _print_json(_error_response("生成工具链修复建议时发生错误。", str(exc)))


if __name__ == "__main__":
    main()
