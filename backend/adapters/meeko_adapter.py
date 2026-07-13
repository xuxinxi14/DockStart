"""Detect Meeko import and preparation-related capabilities."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from dockstart_core.models import ToolCheckResult
from dockstart_core.process_utils import hidden_subprocess_kwargs

SUBPROCESS_TEXT_KWARGS = {"text": True, "encoding": "utf-8", "errors": "replace"}
MEEKO_LIGAND_MODULE = "meeko.cli.mk_prepare_ligand"
MEEKO_RECEPTOR_MODULE = "meeko.cli.mk_prepare_receptor"
ALLOWED_MEEKO_MODULES = frozenset({MEEKO_LIGAND_MODULE, MEEKO_RECEPTOR_MODULE})


def _python_subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    return environment


def detect(python_path: str = "", source: str = "current_environment") -> ToolCheckResult:
    python_executable = python_path.strip() or sys.executable
    if python_path.strip() and not Path(python_executable).exists():
        return ToolCheckResult(
            key="meeko",
            name="Meeko",
            status="missing",
            path=python_executable,
            message="所选 Python 路径不存在，无法检测 Meeko。",
            source=source,  # type: ignore[arg-type]
        )

    command = [
        python_executable,
        "-I",
        "-B",
        "-c",
        "import meeko; print(getattr(meeko, '__version__', ''))",
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            env=_python_subprocess_environment(),
            **SUBPROCESS_TEXT_KWARGS,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
        return ToolCheckResult(
            key="meeko",
            name="Meeko",
            status="error",
            path=python_executable,
            message="检测 Meeko 时发生错误。",
            raw_error=str(exc),
            source=source,
        )

    version = completed.stdout.strip()
    raw_error = completed.stderr.strip()

    if completed.returncode == 0:
        return ToolCheckResult(
            key="meeko",
            name="Meeko",
            status="ok",
            version=version,
            path=python_executable,
            message="已检测到 Meeko Python 包。本轮只确认可导入，不执行受体或配体准备。",
            raw_error=raw_error,
            source=source,
        )

    status = "missing" if "ModuleNotFoundError" in raw_error or "No module named" in raw_error else "error"
    return ToolCheckResult(
        key="meeko",
        name="Meeko",
        status=status,
        path=python_executable,
        message="未检测到 Meeko Python 包。本轮不会自动安装或执行分子准备。",
        raw_error=raw_error,
        source=source,
    )


def _missing_python_capability(python_executable: str, source: str) -> dict[str, Any]:
    return {
        "key": "meeko",
        "name": "Meeko",
        "status": "missing",
        "version": "",
        "path": python_executable,
        "python_path": python_executable,
        "python_source": source,
        "source": source,
        "capabilities": {
            "import": {"status": "missing", "message": "所选 Python 路径不存在。"},
            "ligand_preparation": {"status": "unknown", "message": "Python 不可用，无法检测配体准备能力。"},
            "receptor_preparation": {"status": "unknown", "message": "Python 不可用，无法检测受体准备能力。"},
        },
        "message": "所选 Python 路径不存在，无法检测 Meeko 能力。",
        "raw_error": "",
    }


def detect_meeko_capabilities(python_path: str = "", source: str = "current_environment") -> dict[str, Any]:
    """Return structured Meeko capability status without preparing molecules."""

    python_executable = python_path.strip() or sys.executable
    if python_path.strip() and not Path(python_executable).exists():
        return _missing_python_capability(python_executable, source)

    probe_script = r"""
import json
import importlib
import shutil
import sys
from pathlib import Path

LIGAND_API_CANDIDATES = [
    "MoleculePreparation",
    "RDKitMoleculeSetup",
    "PDBQTWriterLegacy",
    "PDBQTWriter",
]
RECEPTOR_API_CANDIDATES = [
    "PDBQTReceptor",
    "Polymer",
    "ResidueChemTemplates",
]
LIGAND_CLI_CANDIDATES = ["mk_prepare_ligand.py", "mk_prepare_ligand", "mk_prepare_ligand.exe"]
RECEPTOR_CLI_CANDIDATES = ["mk_prepare_receptor.py", "mk_prepare_receptor", "mk_prepare_receptor.exe"]
MODULE_CANDIDATES = {
    "ligand": "meeko.cli.mk_prepare_ligand",
    "receptor": "meeko.cli.mk_prepare_receptor",
}


def find_cli(candidates):
    search_dirs = [
        Path(sys.executable).parent,
        Path(sys.executable).parent / "Scripts",
        Path(sys.executable).parent.parent / "Scripts",
    ]
    found = []
    for name in candidates:
        located = shutil.which(name)
        if located:
            found.append(located)
            continue
        for directory in search_dirs:
            candidate = directory / name
            if candidate.exists():
                found.append(str(candidate))
                break
    return found


payload = {
    "import_available": False,
    "version": "",
    "capabilities": {
        "import": {"status": "missing", "message": "未检测到 Meeko。"},
        "ligand_preparation": {"status": "unknown", "message": "尚未确认 Meeko 配体准备能力。"},
        "receptor_preparation": {"status": "unknown", "message": "尚未确认 Meeko 受体准备能力。"},
    },
}

try:
    import meeko

    names = set(dir(meeko))
    ligand_api = sorted(name for name in LIGAND_API_CANDIDATES if name in names)
    receptor_api = sorted(name for name in RECEPTOR_API_CANDIDATES if name in names)
    ligand_cli = find_cli(LIGAND_CLI_CANDIDATES)
    receptor_cli = find_cli(RECEPTOR_CLI_CANDIDATES)
    ligand_errors = []
    ligand_writer = None
    try:
        molecule_preparation = getattr(meeko, "MoleculePreparation")
        if not callable(molecule_preparation):
            raise TypeError("MoleculePreparation is not callable")
        for writer_name in ("PDBQTWriterLegacy", "PDBQTWriter"):
            candidate = getattr(meeko, writer_name, None)
            if candidate is not None and callable(getattr(candidate, "write_string", None)):
                ligand_writer = writer_name
                break
        if ligand_writer is None:
            raise AttributeError("no PDBQT writer exposes write_string")
    except Exception as exc:
        ligand_errors.append(str(exc))

    receptor_errors = []
    receptor_module = None
    try:
        imported_receptor = importlib.import_module(MODULE_CANDIDATES["receptor"])
        if not callable(getattr(imported_receptor, "main", None)):
            raise AttributeError("receptor module has no callable main")
        receptor_module = MODULE_CANDIDATES["receptor"]
    except Exception as exc:
        receptor_errors.append(str(exc))

    ligand_modules = []
    receptor_modules = [receptor_module] if receptor_module else []
    ligand_ready = not ligand_errors and ligand_writer is not None
    receptor_ready = not receptor_errors and receptor_module is not None

    payload["import_available"] = True
    payload["version"] = getattr(meeko, "__version__", "")
    payload["capabilities"]["import"] = {"status": "ok", "message": "Meeko 可导入。"}
    payload["capabilities"]["ligand_preparation"] = {
        "status": "ok" if ligand_ready else "unknown",
        "message": "MoleculePreparation 与 PDBQT writer.write_string 接口均可用。" if ligand_ready else "Meeko 可导入，但实际配体准备 API 不完整。",
        "api_candidates_found": ligand_api,
        "cli_candidates_found": ligand_cli,
        "module_candidates_found": ligand_modules,
        "molecule_preparation_callable": ligand_ready,
        "writer_interface": ligand_writer or "",
        "probe_errors": ligand_errors,
    }
    payload["capabilities"]["receptor_preparation"] = {
        "status": "ok" if receptor_ready else "unknown",
        "message": "meeko.cli.mk_prepare_receptor 已实际导入且 main 可调用。" if receptor_ready else "Meeko 可导入，但受体准备模块不可执行。",
        "api_candidates_found": receptor_api,
        "cli_candidates_found": receptor_cli,
        "module_candidates_found": receptor_modules,
        "module_imported": receptor_ready,
        "main_callable": receptor_ready,
        "probe_errors": receptor_errors,
    }
except Exception as exc:
    payload["raw_error"] = str(exc)

print(json.dumps(payload, ensure_ascii=True))
"""

    command = [python_executable, "-I", "-B", "-c", probe_script]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            env=_python_subprocess_environment(),
            **SUBPROCESS_TEXT_KWARGS,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except Exception as exc:  # noqa: BLE001 - detector errors are returned as structured status.
        return {
            "key": "meeko",
            "name": "Meeko",
            "status": "error",
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": "error", "message": "检测 Meeko import 时发生错误。"},
                "ligand_preparation": {"status": "unknown", "message": "Meeko import 失败，未检测配体准备能力。"},
                "receptor_preparation": {"status": "unknown", "message": "Meeko import 失败，未检测受体准备能力。"},
            },
            "message": "检测 Meeko 能力时发生错误。",
            "raw_error": str(exc),
        }

    stdout = completed.stdout or ""
    raw_error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        status = "missing" if "ModuleNotFoundError" in raw_error or "No module named" in raw_error else "error"
        return {
            "key": "meeko",
            "name": "Meeko",
            "status": status,
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": status, "message": "未检测到 Meeko Python 包。"},
                "ligand_preparation": {"status": "unknown", "message": "Meeko 不可导入，未检测配体准备能力。"},
                "receptor_preparation": {"status": "unknown", "message": "Meeko 不可导入，未检测受体准备能力。"},
            },
            "message": "未检测到 Meeko 能力。DockStart 不会自动安装 Meeko。",
            "raw_error": raw_error or stdout.strip(),
        }

    try:
        payload = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        return {
            "key": "meeko",
            "name": "Meeko",
            "status": "error",
            "version": "",
            "path": python_executable,
            "python_path": python_executable,
            "python_source": source,
            "source": source,
            "capabilities": {
                "import": {"status": "unknown", "message": "Meeko 探测输出无法解析。"},
                "ligand_preparation": {"status": "unknown", "message": "Meeko 探测输出无法解析。"},
                "receptor_preparation": {"status": "unknown", "message": "Meeko 探测输出无法解析。"},
            },
            "message": "Meeko 能力检测输出不是有效 JSON。",
            "raw_error": str(exc),
        }

    capabilities = payload.get("capabilities") if isinstance(payload, dict) else {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    import_available = bool(payload.get("import_available")) if isinstance(payload, dict) else False

    return {
        "key": "meeko",
        "name": "Meeko",
        "status": "ok" if import_available else "missing",
        "version": str(payload.get("version") or "") if isinstance(payload, dict) else "",
        "path": python_executable,
        "python_path": python_executable,
        "python_source": source,
        "source": source,
        "capabilities": capabilities,
        "message": "Meeko 可导入，受体/配体准备能力已探测。"
        if import_available
        else "未检测到 Meeko Python 包。",
        "raw_error": raw_error or str(payload.get("raw_error") or "") if isinstance(payload, dict) else raw_error,
    }


detect_capabilities = detect_meeko_capabilities


def build_module_command(python_executable: str, module: str, arguments: list[str]) -> list[str]:
    """Build the only supported Meeko module command shape.

    The allowlist avoids accepting a module name from project data. `-I` keeps
    user-site/PYTHONPATH packages out of a bundled invocation and `-B` prevents
    bytecode writes in read-only installation directories.
    """

    selected_module = module.strip()
    if selected_module not in ALLOWED_MEEKO_MODULES:
        raise ValueError(f"不支持的 Meeko 模块入口：{selected_module}")
    executable = python_executable.strip()
    if not executable:
        raise ValueError("Meeko 模块调用缺少 Python 可执行文件路径。")
    return [executable, "-I", "-B", "-m", selected_module, *[str(item) for item in arguments]]


def _with_isolated_python_flags(command: list[str]) -> list[str]:
    """Add isolation flags to legacy Python-script command arrays."""

    normalized = [str(item) for item in command]
    if not normalized:
        raise ValueError("准备命令不能为空。")
    executable_name = Path(normalized[0]).name.lower()
    if executable_name in {"python", "python.exe", "python3", "python3.exe"}:
        remainder = normalized[1:]
        prefix: list[str] = [normalized[0]]
        if "-I" not in remainder:
            prefix.append("-I")
        if "-B" not in remainder:
            prefix.append("-B")
        return [*prefix, *remainder]
    return normalized


def run_preparation_command(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Execute a Meeko/RDKit preparation helper through a safe argument array."""

    return subprocess.run(
        _with_isolated_python_flags(command),
        cwd=str(cwd),
        capture_output=True,
        env=_python_subprocess_environment(),
        **SUBPROCESS_TEXT_KWARGS,
        timeout=timeout,
        check=False,
        **hidden_subprocess_kwargs(),
    )
