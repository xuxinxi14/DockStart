"""Detect whether the frontend has a planned 3Dmol.js dependency."""

from __future__ import annotations

import json
from pathlib import Path

from dockstart_core.models import ToolCheckResult


def detect() -> ToolCheckResult:
    package_json = Path(__file__).resolve().parents[2] / "apps" / "desktop" / "package.json"
    if not package_json.exists():
        return ToolCheckResult(
            key="viewer_3dmol",
            name="3Dmol.js",
            status="unknown",
            message="尚未找到前端 package.json，暂时无法判断 3Dmol.js 是否已接入。",
        )

    try:
        package_data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
        return ToolCheckResult(
            key="viewer_3dmol",
            name="3Dmol.js",
            status="error",
            path=str(package_json),
            message="读取前端依赖配置时发生错误。",
            raw_error=str(exc),
        )

    dependencies = {
        **package_data.get("dependencies", {}),
        **package_data.get("devDependencies", {}),
    }
    for package_name in ("3dmol", "3Dmol.js", "3dmol.js"):
        if package_name in dependencies:
            return ToolCheckResult(
                key="viewer_3dmol",
                name="3Dmol.js",
                status="ok",
                version=str(dependencies[package_name]),
                path=str(package_json),
                message="已在前端依赖中检测到 3Dmol.js。当前仅确认依赖存在，不实现 3D 查看器。",
                source="frontend_dependency",
            )

    return ToolCheckResult(
        key="viewer_3dmol",
        name="3Dmol.js",
        status="missing",
        path=str(package_json),
        message="当前前端尚未安装 3Dmol.js。本轮只记录可视化工具状态，不实现 3D 查看器。",
        source="frontend_dependency",
    )
