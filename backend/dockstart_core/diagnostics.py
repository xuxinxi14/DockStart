"""Post-install diagnostics for DockStart."""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dockstart_core import __version__
from dockstart_core.capabilities import get_app_capability_profile
from dockstart_core.demo_projects import list_available_demo_projects
from dockstart_core.persistence import atomic_write_text
from dockstart_core.settings import get_settings_path
from dockstart_core.toolchain import get_toolchain_status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_tool_summary(tool: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {
            "status": "unknown",
            "source": "unknown",
            "version": "",
            "path": "",
            "message": "未读取到工具状态。",
        }
    return {
        "status": str(tool.get("status", "unknown") or "unknown"),
        "source": str(tool.get("source", "unknown") or "unknown"),
        "version": str(tool.get("version", "") or ""),
        "path": str(tool.get("path", "") or ""),
        "message": str(tool.get("message", "") or ""),
    }


def _default_report_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / "DockStart" / "diagnostics"
    return Path.home() / ".dockstart" / "diagnostics"


def _demo_status(demo_response: dict[str, Any]) -> dict[str, Any]:
    demos = demo_response.get("demos") if isinstance(demo_response.get("demos"), list) else []
    available = [demo for demo in demos if isinstance(demo, dict) and demo.get("exists")]
    return {
        "ok": bool(demo_response.get("ok")),
        "available": bool(available),
        "count": len(available),
        "demos": [
            {
                "demo_type": str(demo.get("demo_type", "")),
                "title": str(demo.get("title", "")),
                "exists": bool(demo.get("exists")),
            }
            for demo in demos
            if isinstance(demo, dict)
        ],
    }


def run_post_install_check() -> dict[str, Any]:
    """Return a compact post-install diagnostic profile."""

    generated_at = _now_iso()
    toolchain = get_toolchain_status()
    capability = get_app_capability_profile()
    demo_projects = list_available_demo_projects()
    demo_status = _demo_status(demo_projects)

    vina = _safe_tool_summary(toolchain.get("active_vina") if isinstance(toolchain, dict) else None)
    python = _safe_tool_summary(toolchain.get("resolved_python") if isinstance(toolchain, dict) else None)
    rdkit = _safe_tool_summary(toolchain.get("rdkit_for_python") if isinstance(toolchain, dict) else None)
    meeko = _safe_tool_summary(toolchain.get("meeko_for_python") if isinstance(toolchain, dict) else None)
    viewer = _safe_tool_summary(capability.get("viewer_status") if isinstance(capability, dict) else None)

    issues: list[str] = []
    if vina["status"] != "ok":
        issues.append("未检测到可用 AutoDock Vina，Basic Mode 无法真实运行 docking。")
    if rdkit["status"] != "ok" or meeko["status"] != "ok":
        issues.append("RDKit/Meeko 未全部可用，Assisted Mode 自动准备 PDBQT 会受限。")
    if not demo_status["available"]:
        issues.append("未检测到可用示例项目，Demo Mode 会受限。")

    return {
        "ok": True,
        "generated_at": generated_at,
        "app_version": __version__,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "runtime_mode": str(toolchain.get("runtime_mode", "unknown") if isinstance(toolchain, dict) else "unknown"),
        "release_build_mode": str(toolchain.get("runtime_mode", "unknown") if isinstance(toolchain, dict) else "unknown"),
        "paths": {
            "settings_path": str(get_settings_path()),
            "resource_dir": str(toolchain.get("resource_dir", "") if isinstance(toolchain, dict) else ""),
            "toolchain_root": str(toolchain.get("toolchain_root", "") if isinstance(toolchain, dict) else ""),
        },
        "tools": {
            "vina": vina,
            "python": python,
            "rdkit": rdkit,
            "meeko": meeko,
            "viewer": viewer,
        },
        "demo_projects": demo_status,
        "modes": {
            "basic_mode_available": bool(capability.get("basic_mode_available")),
            "assisted_mode_available": bool(capability.get("assisted_mode_available")),
            "demo_mode_available": bool(capability.get("demo_mode_available")),
            "recommended_mode": str(capability.get("recommended_mode", "setup") or "setup"),
            "next_action": str(capability.get("next_action", "") or ""),
        },
        "issues": issues,
        "privacy_note": "诊断报告只用于本地排查，不会上传网络；报告可能包含本机工具路径，分享前可自行脱敏。",
        "message": "DockStart 安装后自检已完成。",
        "error": None,
    }


def _markdown_report(check: dict[str, Any]) -> str:
    tools = check.get("tools", {}) if isinstance(check.get("tools"), dict) else {}
    modes = check.get("modes", {}) if isinstance(check.get("modes"), dict) else {}
    paths = check.get("paths", {}) if isinstance(check.get("paths"), dict) else {}
    demo = check.get("demo_projects", {}) if isinstance(check.get("demo_projects"), dict) else {}
    os_info = check.get("os", {}) if isinstance(check.get("os"), dict) else {}

    def tool_line(key: str, label: str) -> str:
        item = tools.get(key) if isinstance(tools.get(key), dict) else {}
        return (
            f"| {label} | {item.get('status', 'unknown')} | {item.get('source', 'unknown')} | "
            f"{item.get('version', '')} | `{item.get('path', '')}` |"
        )

    issues = check.get("issues") if isinstance(check.get("issues"), list) else []
    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- 当前未发现关键阻塞项。"

    return "\n".join(
        [
            "# DockStart 诊断报告",
            "",
            f"- 生成时间：{check.get('generated_at', '')}",
            f"- DockStart 版本：{check.get('app_version', '')}",
            f"- 运行模式：{check.get('runtime_mode', 'unknown')}",
            f"- 系统：{os_info.get('system', '')} {os_info.get('release', '')} {os_info.get('machine', '')}",
            "",
            "## 使用模式",
            "",
            f"- Basic Mode：{'可用' if modes.get('basic_mode_available') else '不可用'}",
            f"- Assisted Mode：{'可用' if modes.get('assisted_mode_available') else '不可用'}",
            f"- Demo Mode：{'可用' if modes.get('demo_mode_available') else '不可用'}",
            f"- 推荐模式：{modes.get('recommended_mode', 'setup')}",
            f"- 下一步建议：{modes.get('next_action', '')}",
            "",
            "## 工具状态",
            "",
            "| 工具 | 状态 | 来源 | 版本 | 路径 |",
            "| --- | --- | --- | --- | --- |",
            tool_line("vina", "AutoDock Vina"),
            tool_line("python", "Python"),
            tool_line("rdkit", "RDKit"),
            tool_line("meeko", "Meeko"),
            tool_line("viewer", "3D Viewer"),
            "",
            "## 路径",
            "",
            f"- settings：`{paths.get('settings_path', '')}`",
            f"- resource_dir：`{paths.get('resource_dir', '')}`",
            f"- toolchain_root：`{paths.get('toolchain_root', '')}`",
            "",
            "## 示例项目",
            "",
            f"- 可用：{'是' if demo.get('available') else '否'}",
            f"- 数量：{demo.get('count', 0)}",
            "",
            "## 需要关注的问题",
            "",
            issue_text,
            "",
            "## 隐私说明",
            "",
            str(check.get("privacy_note", "")),
            "",
        ]
    )


def export_diagnostic_report(output_dir: str | None = None) -> dict[str, Any]:
    """Write a local Markdown diagnostic report and return its path."""

    check = run_post_install_check()
    target_dir = Path(output_dir).expanduser() if output_dir and output_dir.strip() else _default_report_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = target_dir / f"dockstart_diagnostic_{timestamp}.md"
    atomic_write_text(report_path, _markdown_report(check))
    return {
        "ok": True,
        "report_file": str(report_path),
        "generated_at": check["generated_at"],
        "check": check,
        "message": "诊断报告已导出。",
        "error": None,
    }


def _error_response(message: str, raw_error: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "message": message,
        "error": {
            "code": "DIAGNOSTIC_ERROR",
            "message": message,
            "raw_error": raw_error,
            "suggestion": "请确认 Python 后端、工具链状态和输出目录可访问。",
        },
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    try:
        if command == "check":
            _print_json(run_post_install_check())
            return
        if command == "export":
            output_dir = sys.argv[2] if len(sys.argv) > 2 else None
            _print_json(export_diagnostic_report(output_dir))
            return
        raise ValueError(f"未知 diagnostics 命令：{command}")
    except Exception as exc:  # noqa: BLE001 - CLI must return structured JSON.
        _print_json(_error_response("运行安装后自检时发生错误。", str(exc)))


if __name__ == "__main__":
    main()
