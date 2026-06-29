"""Detect whether the frontend has a planned 3Dmol.js dependency."""

from __future__ import annotations

import json
from pathlib import Path

from dockstart_core.models import ToolCheckResult
from dockstart_core.toolchain_paths import get_resource_dir


def _package_json_candidates() -> list[Path]:
    candidates = [Path(__file__).resolve().parents[2] / "apps" / "desktop" / "package.json"]
    resource_dir = get_resource_dir()
    if resource_dir is not None:
        candidates.insert(0, resource_dir / "frontend" / "package.json")
    return candidates


def detect() -> ToolCheckResult:
    package_json = next((candidate for candidate in _package_json_candidates() if candidate.exists()), None)
    if package_json is None:
        return ToolCheckResult(
            key="viewer_3dmol",
            name="3Dmol.js",
            status="unknown",
            message="尚未找到前端依赖元数据，暂时无法判断 3Dmol.js 是否已接入。",
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
                message="已在前端依赖中检测到 3Dmol.js，Viewer 使用本地打包依赖，不使用外部 CDN。",
                source="frontend_dependency",
            )

    return ToolCheckResult(
        key="viewer_3dmol",
        name="3Dmol.js",
        status="missing",
        path=str(package_json),
        message="当前前端依赖元数据中没有检测到 3Dmol.js，3D Viewer 可能不可用。",
        source="frontend_dependency",
    )
