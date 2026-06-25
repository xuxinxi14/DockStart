"""Detect the Python runtime used by DockStart."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from dockstart_core.models import ToolCheckResult
from dockstart_core.toolchain_paths import get_bundled_python_path


def _parse_version(output: str) -> str:
    return output.strip().splitlines()[0] if output.strip() else ""


def _run_version_check(path: Path, source: str, bundled_path: str = "") -> ToolCheckResult:
    source_label = "内置 Python" if source == "bundled" else "用户配置的 Python"
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
            message=f"{source_label} 路径存在，但无法执行。",
            raw_error=str(exc),
            source=source,  # type: ignore[arg-type]
            bundled_path=bundled_path,
            is_bundled=source == "bundled",
        )

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    if completed.returncode == 0:
        return ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            version=_parse_version(output),
            path=str(path),
            message=f"已使用{source_label}完成检测。",
            raw_error=completed.stderr.strip(),
            source=source,  # type: ignore[arg-type]
            bundled_path=bundled_path,
            is_bundled=source == "bundled",
        )

    return ToolCheckResult(
        key="python",
        name="Python",
        status="error",
        version=_parse_version(output),
        path=str(path),
        message=f"{source_label} 路径存在，但版本检测命令失败。",
        raw_error=output,
        source=source,  # type: ignore[arg-type]
        bundled_path=bundled_path,
        is_bundled=source == "bundled",
    )


def detect(configured_path: str = "", bundled_path: str = "") -> ToolCheckResult:
    configured_path = configured_path.strip()
    resolved_bundled_path = Path(bundled_path).expanduser().resolve() if bundled_path.strip() else get_bundled_python_path()
    bundled_path_text = str(resolved_bundled_path)

    if resolved_bundled_path.is_file():
        return _run_version_check(resolved_bundled_path, "bundled", bundled_path_text)

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
                bundled_path=bundled_path_text,
            )

        return _run_version_check(path, "configured", bundled_path_text)

    return ToolCheckResult(
        key="python",
        name="Python",
        status="ok",
        version=platform.python_version(),
        path=sys.executable,
        message="已检测到当前 Python 运行环境。",
        source="current_environment",
        bundled_path=bundled_path_text,
    )
