"""Detect RDKit import availability only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dockstart_core.models import ToolCheckResult


def detect(python_path: str = "", source: str = "current_environment") -> ToolCheckResult:
    python_executable = python_path.strip() or sys.executable
    if python_path.strip() and not Path(python_executable).exists():
        return ToolCheckResult(
            key="rdkit",
            name="RDKit",
            status="missing",
            path=python_executable,
            message="用户配置的 Python 路径不存在，无法检测 RDKit。",
            source="configured",
        )

    command = [
        python_executable,
        "-c",
        "import rdkit; print(getattr(rdkit, '__version__', ''))",
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
        return ToolCheckResult(
            key="rdkit",
            name="RDKit",
            status="error",
            path=python_executable,
            message="检测 RDKit 时发生错误。",
            raw_error=str(exc),
            source=source,
        )

    version = completed.stdout.strip()
    raw_error = completed.stderr.strip()

    if completed.returncode == 0:
        return ToolCheckResult(
            key="rdkit",
            name="RDKit",
            status="ok",
            version=version,
            path=python_executable,
            message="已检测到 RDKit Python 包。本轮只确认可导入，不进行分子读取或处理。",
            raw_error=raw_error,
            source=source,
        )

    status = "missing" if "ModuleNotFoundError" in raw_error or "No module named" in raw_error else "error"
    return ToolCheckResult(
        key="rdkit",
        name="RDKit",
        status=status,
        path=python_executable,
        message="未检测到 RDKit Python 包。本轮不会自动安装或进行分子处理。",
        raw_error=raw_error,
        source=source,
    )
