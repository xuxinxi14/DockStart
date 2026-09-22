"""AutoGrid4 adapter used by the AutoDock4 maps workflow."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dockstart_core.models import ToolCheckResult

AUTOGRID_VERSION_PATTERN = re.compile(
    r"(?:AutoGrid|autogrid)(?:\s+Version)?[^0-9]{0,24}(\d+(?:\.\d+){1,3})",
    re.IGNORECASE,
)


def _configured_candidate(configured_path: str) -> Path | None:
    value = str(configured_path or "").strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_dir():
        for name in ("autogrid4.exe", "autogrid4"):
            candidate = path / name
            if candidate.is_file():
                return candidate.resolve()
    return path.resolve(strict=False)


def _auto_candidate() -> Path | None:
    located = shutil.which("autogrid4") or shutil.which("autogrid4.exe")
    return Path(located).resolve() if located else None


def _version_from_output(output: str) -> str:
    match = AUTOGRID_VERSION_PATTERN.search(output)
    return match.group(1) if match else ""


def detect(configured_path: str = "") -> ToolCheckResult:
    """Detect an external AutoGrid4 executable without bundling it."""

    candidate = _configured_candidate(configured_path)
    source = "configured" if candidate is not None else "auto"
    if candidate is None:
        candidate = _auto_candidate()
    if candidate is None:
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="missing",
            message="未检测到 AutoGrid4；仅 AutoDock4 (maps) 协议需要该外部 GPL 工具。",
            source="missing",
        )
    if not candidate.is_file():
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="error",
            path=str(candidate),
            message="配置的 AutoGrid4 路径不是可读取文件。",
            raw_error=str(candidate),
            source=source,
        )

    attempts: list[str] = []
    for flag in ("--version", "-h"):
        try:
            completed = subprocess.run(
                [str(candidate), flag],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            attempts.append(f"{flag}: {exc}")
            continue
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        version = _version_from_output(output)
        if version or "autogrid" in output.lower():
            return ToolCheckResult(
                key="autogrid4",
                name="AutoGrid4",
                status="ok",
                version=version,
                path=str(candidate),
                message="已检测到外部 AutoGrid4，可用于生成 AutoDock4 affinity maps。",
                raw_error="" if completed.returncode == 0 else output,
                source=source,
            )
        attempts.append(f"{flag}: exit={completed.returncode}; output={output[:500]}")

    return ToolCheckResult(
        key="autogrid4",
        name="AutoGrid4",
        status="error",
        path=str(candidate),
        message="AutoGrid4 文件存在，但无法确认其命令行身份。",
        raw_error="\n".join(attempts),
        source=source,
    )


def run(
    executable: str,
    gpf_file: str,
    log_file: str,
    working_directory: str | Path,
    *,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """Run AutoGrid4 with an argument array and capture both output streams."""

    working_path = Path(working_directory).expanduser().resolve()
    executable_path = Path(executable).expanduser().resolve()
    if not executable_path.is_file():
        return {
            "ok": False,
            "command": [str(executable_path), "-p", gpf_file, "-l", log_file],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"AutoGrid4 可执行文件不存在：{executable_path}",
        }
    if not working_path.is_dir():
        return {
            "ok": False,
            "command": [str(executable_path), "-p", gpf_file, "-l", log_file],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": f"AutoGrid4 工作目录不存在：{working_path}",
        }

    command = [str(executable_path), "-p", gpf_file, "-l", log_file]
    try:
        completed = subprocess.run(
            command,
            cwd=working_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": command,
            "exit_code": None,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
            "error": f"AutoGrid4 运行超过 {timeout_seconds} 秒，已停止等待。",
        }
    except OSError as exc:
        return {
            "ok": False,
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error": str(exc),
        }

    return {
        "ok": completed.returncode == 0,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "error": "" if completed.returncode == 0 else f"AutoGrid4 退出码为 {completed.returncode}。",
    }
