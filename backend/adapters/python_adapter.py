"""Detect the Python runtime used by DockStart."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from dockstart_core.models import ToolCheckResult


def _parse_version(output: str) -> str:
    return output.strip().splitlines()[0] if output.strip() else ""


def detect(configured_path: str = "") -> ToolCheckResult:
    configured_path = configured_path.strip()
    if configured_path:
        path = Path(configured_path)
        if not path.exists():
            return ToolCheckResult(
                key="python",
                name="Python",
                status="missing",
                path=configured_path,
                message="用户配置的 python.exe 路径不存在，请检查设置页中的 Python 路径。",
                source="configured",
            )

        try:
            completed = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
            return ToolCheckResult(
                key="python",
                name="Python",
                status="error",
                path=str(path),
                message="用户配置的 Python 路径无法执行。",
                raw_error=str(exc),
                source="configured",
            )

        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        if completed.returncode == 0:
            return ToolCheckResult(
                key="python",
                name="Python",
                status="ok",
                version=_parse_version(output),
                path=str(path),
                message="已使用用户配置的 Python 路径完成检测。",
                raw_error=completed.stderr.strip(),
                source="configured",
            )

        return ToolCheckResult(
            key="python",
            name="Python",
            status="error",
            version=_parse_version(output),
            path=str(path),
            message="用户配置的 Python 路径存在，但版本检测命令失败。",
            raw_error=output,
            source="configured",
        )

    return ToolCheckResult(
        key="python",
        name="Python",
        status="ok",
        version=platform.python_version(),
        path=sys.executable,
        message="已检测到当前 Python 运行环境。",
        source="current_environment",
    )
