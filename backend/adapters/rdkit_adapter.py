"""Detect RDKit import and preparation-related capabilities."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from dockstart_core.models import ToolCheckResult
from dockstart_core.process_utils import hidden_subprocess_kwargs

SUBPROCESS_TEXT_KWARGS = {"text": True, "encoding": "utf-8", "errors": "replace"}


def detect(python_path: str = "", source: str = "current_environment") -> ToolCheckResult:
    python_executable = python_path.strip() or sys.executable
    if python_path.strip() and not Path(python_executable).exists():
        return ToolCheckResult(
            key="rdkit",
            name="RDKit",
            status="missing",
            path=python_executable,
            message="所选 Python 路径不存在，无法检测 RDKit。",
            source=source,  # type: ignore[arg-type]
        )

    command = [
        python_executable,
        "-I",
        "-B",
        "-c",
        "import rdkit; print(getattr(rdkit, '__version__', ''))",
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            **SUBPROCESS_TEXT_KWARGS,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
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


def _missing_python_capability(python_executable: str, source: str) -> dict[str, Any]:
    return {
        "key": "rdkit",
        "name": "RDKit",
        "status": "missing",
        "version": "",
        "path": python_executable,
        "python_path": python_executable,
        "python_source": source,
        "source": source,
        "capabilities": {
            "import": {"status": "missing", "message": "所选 Python 路径不存在。"},
            "sdf_inline_read": {"status": "unknown", "message": "Python 不可用，无法检测 SDF 读取能力。"},
        },
        "message": "所选 Python 路径不存在，无法检测 RDKit 能力。",
        "raw_error": "",
    }


def detect_rdkit_capabilities(python_path: str = "", source: str = "current_environment") -> dict[str, Any]:
    """Return structured RDKit capability status without writing project files."""

    python_executable = python_path.strip() or sys.executable
    if python_path.strip() and not Path(python_executable).exists():
        return _missing_python_capability(python_executable, source)

    probe_script = r'''
import io
import json

payload = {
    "import_available": False,
    "version": "",
    "capabilities": {
        "import": {"status": "missing", "message": "未检测到 RDKit。"},
        "sdf_inline_read": {"status": "unknown", "message": "尚未完成 SDF 读取探测。"},
    },
}

try:
    import rdkit
    from rdkit import Chem

    payload["import_available"] = True
    payload["version"] = getattr(rdkit, "__version__", "")
    payload["capabilities"]["import"] = {"status": "ok", "message": "RDKit 可导入。"}
    sample = b"""DockStart
  DockStart

  1  0  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
M  END
$$$$
"""
    try:
        supplier = Chem.ForwardSDMolSupplier(io.BytesIO(sample), sanitize=False, removeHs=False)
        molecule = next(iter(supplier), None)
        payload["capabilities"]["sdf_inline_read"] = {
            "status": "ok" if molecule is not None else "unknown",
            "message": "RDKit 可读取内联 SDF 样本。" if molecule is not None else "RDKit 已导入，但内联 SDF 样本读取结果不可确认。",
        }
    except Exception as exc:
        payload["capabilities"]["sdf_inline_read"] = {
            "status": "unknown",
            "message": "RDKit 已导入，但 SDF 读取能力不可确认。",
            "raw_error": str(exc),
        }
except Exception as exc:
    payload["raw_error"] = str(exc)

print(json.dumps(payload, ensure_ascii=True))
'''

    command = [python_executable, "-I", "-B", "-c", probe_script]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            **SUBPROCESS_TEXT_KWARGS,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001 - detector errors are returned as structured status.
        return {
            "key": "rdkit",
            "name": "RDKit",
            "status": "error",
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": "error", "message": "检测 RDKit import 时发生错误。"},
                "sdf_inline_read": {"status": "unknown", "message": "RDKit import 失败，未检测 SDF 读取能力。"},
            },
            "message": "检测 RDKit 能力时发生错误。",
            "raw_error": str(exc),
        }

    stdout = completed.stdout or ""
    raw_error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        status = "missing" if "ModuleNotFoundError" in raw_error or "No module named" in raw_error else "error"
        return {
            "key": "rdkit",
            "name": "RDKit",
            "status": status,
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": status, "message": "未检测到 RDKit Python 包。"},
                "sdf_inline_read": {"status": "unknown", "message": "RDKit 不可导入，未检测 SDF 读取能力。"},
            },
            "message": "未检测到 RDKit 能力。DockStart 不会自动安装 RDKit。",
            "raw_error": raw_error or stdout.strip(),
        }

    try:
        payload = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        return {
            "key": "rdkit",
            "name": "RDKit",
            "status": "error",
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": "unknown", "message": "RDKit 探测输出无法解析。"},
                "sdf_inline_read": {"status": "unknown", "message": "RDKit 探测输出无法解析。"},
            },
            "message": "RDKit 能力检测输出不是有效 JSON。",
            "raw_error": str(exc),
        }

    capabilities = payload.get("capabilities") if isinstance(payload, dict) else {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    import_available = bool(payload.get("import_available")) if isinstance(payload, dict) else False

    return {
        "key": "rdkit",
        "name": "RDKit",
        "status": "ok" if import_available else "missing",
        "version": str(payload.get("version") or "") if isinstance(payload, dict) else "",
        "path": python_executable,
        "python_path": python_executable,
        "python_source": source,
        "source": source,
        "capabilities": capabilities,
        "message": "RDKit 可导入，基础 SDF 读取能力已探测。"
        if import_available
        else "未检测到 RDKit Python 包。",
        "raw_error": raw_error or str(payload.get("raw_error") or "") if isinstance(payload, dict) else raw_error,
    }


detect_capabilities = detect_rdkit_capabilities
