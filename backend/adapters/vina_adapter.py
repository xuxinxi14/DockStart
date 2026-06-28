"""Detect AutoDock Vina without running a docking task."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from dockstart_core.models import ToolCheckResult
from dockstart_core.process_utils import hidden_subprocess_kwargs
from dockstart_core.toolchain_paths import get_existing_bundled_vina_path

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


def _run_version_check(path: str, source: str, bundled_path: str = "") -> ToolCheckResult:
    is_bundled = source == "bundled"

    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
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
            bundled_path=bundled_path,
            is_bundled=is_bundled,
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
            bundled_path=bundled_path,
            is_bundled=is_bundled,
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
            bundled_path=bundled_path,
            is_bundled=is_bundled,
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
        bundled_path=bundled_path,
        is_bundled=is_bundled,
    )


def detect(configured_path: str = "", bundled_path: str = "") -> ToolCheckResult:
    configured_path = configured_path.strip()
    resolved_bundled_path = (
        str(Path(bundled_path).expanduser())
        if bundled_path
        else str(get_existing_bundled_vina_path())
    )

    if Path(resolved_bundled_path).expanduser().is_file():
        return _run_version_check(
            resolved_bundled_path,
            "bundled",
            bundled_path=resolved_bundled_path,
        )

    if configured_path:
        configured = Path(configured_path).expanduser()
        if not configured.exists():
            return ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="missing",
                path=configured_path,
                message="用户配置的 vina.exe 路径不存在，请检查设置页中的 AutoDock Vina 路径。",
                source="configured",
                bundled_path=resolved_bundled_path,
                is_bundled=False,
            )
        return _run_version_check(
            str(configured),
            "configured",
            bundled_path=resolved_bundled_path,
        )

    path = _find_vina()
    if not path:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            message=(
                "未检测到内置 Vina，也未在 PATH 中检测到 vina 或 vina.exe。"
                "请放置 resources/tools/vina/vina.exe、在设置页配置路径，或将 Vina 加入 PATH。"
            ),
            source="missing",
            bundled_path=resolved_bundled_path,
            is_bundled=False,
        )

    return _run_version_check(
        path,
        "auto",
        bundled_path=resolved_bundled_path,
    )
