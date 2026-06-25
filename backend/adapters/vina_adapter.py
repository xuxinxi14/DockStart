"""Detect AutoDock Vina without running a docking task."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from dockstart_core.models import ToolCheckResult

_VINA_CANDIDATES = ("vina", "vina.exe")


def _find_vina() -> str:
    for candidate in _VINA_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return ""


def _parse_version(output: str) -> str:
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"(\d+(?:\.\d+)+)", first_line)
    return match.group(1) if match else first_line


def detect(configured_path: str = "") -> ToolCheckResult:
    configured_path = configured_path.strip()
    source = "configured" if configured_path else "auto"
    path = configured_path or _find_vina()

    if not path:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            message="未在 PATH 中检测到 vina 或 vina.exe。请先安装 AutoDock Vina，或在后续设置页配置工具路径。",
            source=source,
        )

    if configured_path and not Path(configured_path).exists():
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            path=configured_path,
            message="用户配置的 vina.exe 路径不存在，请检查设置页中的 AutoDock Vina 路径。",
            source="configured",
        )

    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError as exc:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            path=path,
            message="检测到的 Vina 路径无法执行，请检查安装是否完整。",
            raw_error=str(exc),
            source=source,
        )
    except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="error",
            path=path,
            message="检测 AutoDock Vina 时发生错误。",
            raw_error=str(exc),
            source=source,
        )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    raw_output = "\n".join(part for part in (stdout, stderr) if part)
    version = _parse_version(raw_output)

    if completed.returncode == 0:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version=version,
            path=path,
            message="已检测到 AutoDock Vina 命令行工具。",
            raw_error=stderr,
            source=source,
        )

    return ToolCheckResult(
        key="vina",
        name="AutoDock Vina",
        status="error",
        version=version,
        path=path,
        message="已找到 AutoDock Vina，但运行版本检测命令失败。",
        raw_error=raw_output,
        source=source,
    )
