"""Preparation workflow status and prerequisites for DockStart projects."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters import meeko_adapter, rdkit_adapter
from dockstart_core.project import _error, _project_from_dict, load_project, save_project
from dockstart_core.preparation_models import (
    ALLOWED_PREPARATION_TARGETS,
    PreparationTarget,
    default_preparation_result,
)
from dockstart_core.toolchain import get_resolved_python

SUPPORTED_LIGAND_PREPARATION_FORMATS = {".sdf", ".mol"}
LIGAND_PREPARATION_OUTPUT = "prepared/ligand.pdbqt"
LIGAND_PREPARATION_LOG_DIR = Path("prepared", "logs")
LIGAND_PREPARATION_STDOUT = Path("prepared", "logs", "ligand_stdout.txt")
LIGAND_PREPARATION_STDERR = Path("prepared", "logs", "ligand_stderr.txt")
LIGAND_PREPARATION_LOG = Path("prepared", "logs", "ligand_preparation_log.json")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _target_error(target: str) -> dict[str, Any]:
    return _error(
        "PREPARATION_TARGET_INVALID",
        "准备目标无效，只能是 receptor 或 ligand。",
        raw_error=str(target),
        suggestion="请选择 receptor 或 ligand。",
    )


def _normalize_target(target: str) -> PreparationTarget | None:
    normalized = str(target or "").strip().lower()
    if normalized in ALLOWED_PREPARATION_TARGETS:
        return normalized  # type: ignore[return-value]
    return None


def _load_project_model(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    return _project_from_dict(loaded["project"], Path(project_dir).expanduser()), None


def _file_status(project_path: Path, relative_file: str, key: str, name: str) -> dict[str, Any]:
    value = str(relative_file or "")
    if not value:
        return {
            "key": key,
            "name": name,
            "path": "",
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "missing",
            "message": f"{name} 尚未记录。",
        }

    path = project_path / value
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    non_empty = size > 0
    if not exists:
        status = "missing"
        message = f"{name} 文件不存在。"
    elif not is_file:
        status = "error"
        message = f"{name} 路径不是文件。"
    elif not non_empty:
        status = "empty"
        message = f"{name} 文件为空。"
    else:
        status = "ok"
        message = f"{name} 文件存在。"

    return {
        "key": key,
        "name": name,
        "path": value,
        "absolute_path": str(path.resolve()),
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "non_empty": non_empty,
        "status": status,
        "message": message,
    }


def _project_file_path(project_path: Path, value: str) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else project_path / path


def _relative_path(path: Path, project_path: Path) -> str:
    try:
        return path.resolve().relative_to(project_path.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _project_error_payload(
    project: Any,
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "preparation": project.preparation.to_dict(),
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
        "warnings": warnings or [],
        "message": message,
    }


def _tool_status() -> dict[str, Any]:
    python_result = get_resolved_python()
    rdkit_result = rdkit_adapter.detect_rdkit_capabilities(python_result.path, python_result.source)
    meeko_result = meeko_adapter.detect_meeko_capabilities(python_result.path, python_result.source)
    return {
        "python": python_result.to_dict(),
        "rdkit": rdkit_result,
        "meeko": meeko_result,
    }


def get_preparation_tool_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    tools = _tool_status()
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "tools": tools,
        "python_path": tools["python"].get("path", ""),
        "python_source": tools["python"].get("source", "unknown"),
        "message": "自动准备工具能力检测已完成。本阶段只检测能力，不执行分子处理。",
        "error": None,
    }


def get_preparation_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    tools = _tool_status()
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "preparation": project.preparation.to_dict(),
        "tools": tools,
        "files": {
            "receptor_raw": _file_status(project_path, project.receptor.raw_file, "receptor_raw", "受体 raw 文件"),
            "ligand_raw": _file_status(project_path, project.ligand.raw_file, "ligand_raw", "配体 raw 文件"),
            "receptor_prepared": _file_status(project_path, project.receptor.file, "receptor_prepared", "受体 prepared PDBQT"),
            "ligand_prepared": _file_status(project_path, project.ligand.file, "ligand_prepared", "配体 prepared PDBQT"),
        },
        "message": "PDBQT 自动准备状态已读取。",
        "error": None,
    }


def validate_preparation_prerequisites(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    file_ref = getattr(project, normalized_target)
    raw_status = _file_status(
        project_path,
        file_ref.raw_file,
        f"{normalized_target}_raw",
        "受体 raw 文件" if normalized_target == "receptor" else "配体 raw 文件",
    )
    if raw_status["status"] != "ok":
        return _error(
            f"{normalized_target.upper()}_RAW_FILE_NOT_READY",
            "尚未找到可用于自动准备的 raw 文件。",
            raw_error=raw_status.get("path", ""),
            suggestion="请先在“下载原始结构文件”页面下载 raw 文件，或确认 project.json 中的 raw_file 记录。",
        )

    tools = _tool_status()
    python_ok = tools["python"]["status"] == "ok"
    rdkit_ok = tools["rdkit"]["status"] == "ok"
    meeko_ok = tools["meeko"]["status"] == "ok"
    missing: list[str] = []
    if not python_ok:
        missing.append("Python")
    if normalized_target == "ligand" and not rdkit_ok:
        missing.append("RDKit")
    if not meeko_ok:
        missing.append("Meeko")

    status = "ready" if not missing else "checking"
    preparation_result = getattr(project.preparation, normalized_target)
    preparation_result.status = status  # type: ignore[assignment]
    preparation_result.input_file = file_ref.raw_file
    preparation_result.output_file = f"prepared/{normalized_target}.pdbqt"
    preparation_result.python_path = tools["python"].get("path", "")
    preparation_result.python_source = tools["python"].get("source", "unknown")
    preparation_result.rdkit_available = rdkit_ok
    preparation_result.meeko_available = meeko_ok
    preparation_result.warnings = [
        "自动准备只能完成格式和工具链层面的处理，不能保证质子化、电荷、构象或受体结构选择一定科学正确。"
    ]
    preparation_result.error = None if not missing else {
        "code": "PREPARATION_TOOLS_NOT_READY",
        "message": "自动准备所需工具尚未全部可用。",
        "raw_error": ", ".join(missing),
        "suggestion": "请先在工具链状态页确认 Python、RDKit 和 Meeko。DockStart 不会自动安装这些包。",
    }

    save_result = save_project(project)
    if not save_result.get("ok"):
        return save_result

    payload = get_preparation_status(project.project_dir)
    payload["target"] = normalized_target
    payload["ready"] = not missing
    payload["missing_tools"] = missing
    payload["message"] = (
        "自动准备前置检查通过。"
        if not missing
        else "raw 文件已找到，但自动准备工具尚未全部可用。"
    )
    if missing:
        payload["ok"] = False
        payload["error"] = preparation_result.error
    return payload


def _ligand_preparation_script_text() -> str:
    return r'''
from __future__ import annotations

import json
import sys
from pathlib import Path

from rdkit import Chem
from meeko import MoleculePreparation

try:
    from meeko import PDBQTWriterLegacy as PDBQTWriter
except Exception:
    try:
        from meeko import PDBQTWriter
    except Exception as exc:
        raise RuntimeError("未找到可用的 Meeko PDBQT writer。") from exc


def read_ligand(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".sdf":
        supplier = Chem.SDMolSupplier(str(path), sanitize=True, removeHs=False)
        molecules = [mol for mol in supplier if mol is not None]
        if not molecules:
            raise RuntimeError("RDKit 未能从 SDF 中读取到有效分子。")
        return molecules[0]
    if suffix == ".mol":
        molecule = Chem.MolFromMolFile(str(path), sanitize=True, removeHs=False)
        if molecule is None:
            raise RuntimeError("RDKit 未能从 MOL 文件中读取到有效分子。")
        return molecule
    raise RuntimeError(f"暂不支持的配体输入格式：{suffix}")


def normalize_writer_result(result):
    if isinstance(result, tuple):
        if len(result) >= 2 and result[1] is False:
            raise RuntimeError(str(result[2]) if len(result) >= 3 else "Meeko 写出 PDBQT 失败。")
        return str(result[0])
    return str(result)


def main() -> int:
    if len(sys.argv) != 3:
        print("需要输入 raw ligand 路径和输出 PDBQT 路径。", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    molecule = read_ligand(input_path)
    preparator = MoleculePreparation()
    setups = preparator.prepare(molecule)
    if not setups:
        print("Meeko 未生成 ligand setup，无法写出 PDBQT。", file=sys.stderr)
        return 3

    result = PDBQTWriter.write_string(setups[0])
    pdbqt_text = normalize_writer_result(result)
    if not pdbqt_text.strip():
        print("Meeko 写出的 PDBQT 为空。", file=sys.stderr)
        return 4

    output_path.write_text(pdbqt_text, encoding="utf-8")
    print(json.dumps({"ok": True, "output_file": str(output_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def validate_ligand_preparation_input(project_dir: str, overwrite: bool = False) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    raw_file = str(project.ligand.raw_file or "")
    if not raw_file:
        return _error(
            "LIGAND_RAW_FILE_NOT_RECORDED",
            "尚未记录 ligand raw 文件，无法自动准备配体 PDBQT。",
            suggestion="请先在“下载原始结构文件”页面下载 ligand raw 文件，或手动导入 prepared ligand PDBQT。",
        )

    input_path = _project_file_path(project_path, raw_file)
    input_status = _file_status(project_path, raw_file, "ligand_raw", "配体 raw 文件")
    if input_status["status"] != "ok":
        return _error(
            "LIGAND_RAW_FILE_NOT_READY",
            "配体 raw 文件不存在或为空，无法自动准备 ligand PDBQT。",
            raw_error=input_status.get("absolute_path", input_status.get("path", "")),
            suggestion="请重新下载 ligand raw 文件，或检查 project.json 中的 ligand.raw_file 记录。",
        )

    suffix = input_path.suffix.lower()
    if suffix not in SUPPORTED_LIGAND_PREPARATION_FORMATS:
        return _error(
            "LIGAND_RAW_FORMAT_UNSUPPORTED",
            "当前版本暂不支持该配体 raw 文件格式自动准备 PDBQT。",
            raw_error=suffix,
            suggestion="V0.3.2 优先支持 SDF 和 MOL；MOL2/SMILES 暂不自动准备，请先使用外部工具准备 PDBQT。",
        )

    output_path = project_path / LIGAND_PREPARATION_OUTPUT
    if output_path.exists() and output_path.stat().st_size > 0 and not overwrite:
        return _error(
            "LIGAND_PREPARED_FILE_EXISTS",
            "prepared/ligand.pdbqt 已存在，默认不会覆盖。",
            raw_error=str(output_path),
            suggestion="如确认要重新生成，请开启 overwrite。",
        )

    tool_status = get_preparation_tool_status(project.project_dir)
    if not tool_status.get("ok"):
        return tool_status
    tools = tool_status["tools"]
    python_tool = tools["python"]
    rdkit_tool = tools["rdkit"]
    meeko_tool = tools["meeko"]
    ligand_capability = meeko_tool.get("capabilities", {}).get("ligand_preparation", {})

    missing: list[str] = []
    if python_tool.get("status") != "ok":
        missing.append("Python")
    if rdkit_tool.get("status") != "ok":
        missing.append("RDKit")
    if meeko_tool.get("status") != "ok":
        missing.append("Meeko")
    if ligand_capability.get("status") != "ok":
        missing.append("Meeko ligand preparation capability")

    if missing:
        return _error(
            "LIGAND_PREPARATION_TOOLS_NOT_READY",
            "配体 PDBQT 自动准备所需工具尚未全部可用。",
            raw_error=", ".join(missing),
            suggestion="请先在 PreparationPage 或工具链状态页确认 Python、RDKit、Meeko 以及 Meeko 配体准备能力。",
        )

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "input_file": raw_file,
        "input_path": str(input_path),
        "output_file": LIGAND_PREPARATION_OUTPUT,
        "output_path": str(output_path),
        "format": suffix,
        "tools": tools,
        "overwrite": overwrite,
        "warnings": [
            "自动生成 ligand PDBQT 不代表配体质子化、电荷、构象或互变异构状态一定科学正确，请人工检查。"
        ],
        "message": "配体 PDBQT 自动准备输入检查通过。",
        "error": None,
    }


def build_ligand_preparation_command_or_script(project_dir: str, overwrite: bool = False) -> dict[str, Any]:
    validation = validate_ligand_preparation_input(project_dir, overwrite=overwrite)
    if not validation.get("ok"):
        return validation

    project_path = Path(validation["project_dir"]).expanduser()
    logs_dir = project_path / LIGAND_PREPARATION_LOG_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    script_path = logs_dir / "prepare_ligand_rdkit_meeko.py"
    script_path.write_text(_ligand_preparation_script_text(), encoding="utf-8")

    command = [
        validation["tools"]["python"]["path"],
        str(script_path),
        validation["input_path"],
        validation["output_path"],
    ]
    return {
        **validation,
        "command": command,
        "script_file": _relative_path(script_path, project_path),
        "stdout_file": LIGAND_PREPARATION_STDOUT.as_posix(),
        "stderr_file": LIGAND_PREPARATION_STDERR.as_posix(),
        "log_file": LIGAND_PREPARATION_LOG.as_posix(),
    }


def prepare_ligand_pdbqt(project_dir: str, overwrite: bool = False, options: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = options or {}
    built = build_ligand_preparation_command_or_script(project_dir, overwrite=overwrite)
    if not built.get("ok"):
        return built

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    stdout_path = project_path / built["stdout_file"]
    stderr_path = project_path / built["stderr_file"]
    log_path = project_path / built["log_file"]
    output_path = Path(built["output_path"])
    stdout_path.parent.mkdir(parents=True, exist_ok=True)

    prep = project.preparation.ligand
    started_at = _now_iso()
    prep.status = "running"
    prep.method = "rdkit_meeko"
    prep.input_file = built["input_file"]
    prep.output_file = LIGAND_PREPARATION_OUTPUT
    prep.started_at = started_at
    prep.finished_at = None
    prep.python_path = built["tools"]["python"].get("path", "")
    prep.python_source = built["tools"]["python"].get("source", "unknown")
    prep.rdkit_available = built["tools"]["rdkit"].get("status") == "ok"
    prep.meeko_available = built["tools"]["meeko"].get("status") == "ok"
    prep.command = built["command"]
    prep.stdout_file = built["stdout_file"]
    prep.stderr_file = built["stderr_file"]
    prep.log_file = built["log_file"]
    prep.error = None
    prep.warnings = list(built.get("warnings", []))
    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    try:
        completed = meeko_adapter.run_preparation_command(built["command"], cwd=project_path)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        exit_code = int(completed.returncode)
    except Exception as exc:  # noqa: BLE001 - return structured preparation failure.
        stdout = ""
        stderr = str(exc)
        exit_code = -1

    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    finished_at = _now_iso()
    output_ok = output_path.is_file() and output_path.stat().st_size > 0
    success = exit_code == 0 and output_ok
    if success:
        prep.status = "finished"
        prep.error = None
        project.ligand.file = LIGAND_PREPARATION_OUTPUT
        message = "ligand PDBQT 自动准备完成。请继续人工检查配体质子化、电荷和构象合理性。"
        error = None
    else:
        prep.status = "failed"
        message = "ligand PDBQT 自动准备失败。"
        raw_error = stderr or stdout or ("输出 PDBQT 不存在或为空。" if exit_code == 0 else "")
        error = {
            "code": "LIGAND_PREPARATION_FAILED",
            "message": "ligand PDBQT 自动准备失败，请查看 stderr 和日志。",
            "raw_error": raw_error,
            "suggestion": "请确认 RDKit/Meeko 版本、输入文件格式、配体 3D 构象和 Meeko 配体准备能力。",
        }
        prep.error = error

    prep.finished_at = finished_at
    log_payload = {
        "target": "ligand",
        "status": prep.status,
        "method": prep.method,
        "started_at": started_at,
        "finished_at": finished_at,
        "input_file": built["input_file"],
        "output_file": LIGAND_PREPARATION_OUTPUT,
        "command": built["command"],
        "exit_code": exit_code,
        "stdout_file": built["stdout_file"],
        "stderr_file": built["stderr_file"],
        "output_exists": output_path.is_file(),
        "output_non_empty": output_ok,
        "warnings": prep.warnings,
        "error": error,
    }
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    payload = get_preparation_status(project.project_dir)
    payload.update(
        {
            "ok": success,
            "target": "ligand",
            "output_file": LIGAND_PREPARATION_OUTPUT,
            "stdout_file": built["stdout_file"],
            "stderr_file": built["stderr_file"],
            "log_file": built["log_file"],
            "exit_code": exit_code,
            "message": message,
            "error": error,
        }
    )
    return payload


def load_ligand_preparation_log(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    prep = project.preparation.ligand
    stdout_file = prep.stdout_file or LIGAND_PREPARATION_STDOUT.as_posix()
    stderr_file = prep.stderr_file or LIGAND_PREPARATION_STDERR.as_posix()
    log_file = prep.log_file or LIGAND_PREPARATION_LOG.as_posix()

    def read_optional(relative_file: str) -> str:
        path = _project_file_path(project_path, relative_file)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "target": "ligand",
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "log_file": log_file,
        "stdout": read_optional(stdout_file),
        "stderr": read_optional(stderr_file),
        "log": read_optional(log_file),
        "message": "ligand preparation 日志已读取。",
        "error": None,
    }


def reset_preparation_status(project_dir: str, target: str) -> dict[str, Any]:
    normalized_target = _normalize_target(target)
    if normalized_target is None:
        return _target_error(target)

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None

    setattr(project.preparation, normalized_target, default_preparation_result(normalized_target))
    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    payload = get_preparation_status(project.project_dir)
    payload["target"] = normalized_target
    payload["message"] = "准备状态已重置。prepared PDBQT 文件不会被删除。"
    return payload


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "status"

    if command == "status":
        if len(sys.argv) < 3:
            _print_json(_error("PREPARATION_STATUS_ARGS", "读取准备状态需要 project_dir 参数。"))
            return
        _print_json(get_preparation_status(sys.argv[2]))
        return

    if command == "validate":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_VALIDATE_ARGS", "准备前置检查需要 project_dir 和 target 参数。"))
            return
        _print_json(validate_preparation_prerequisites(sys.argv[2], sys.argv[3]))
        return

    if command == "tool-status":
        if len(sys.argv) < 3:
            _print_json(_error("PREPARATION_TOOL_STATUS_ARGS", "读取准备工具能力需要 project_dir 参数。"))
            return
        _print_json(get_preparation_tool_status(sys.argv[2]))
        return

    if command == "prepare-ligand":
        if len(sys.argv) < 3:
            _print_json(_error("LIGAND_PREPARATION_ARGS", "准备 ligand PDBQT 需要 project_dir 参数。"))
            return
        overwrite = len(sys.argv) >= 4 and sys.argv[3].strip().lower() in {"1", "true", "yes", "y"}
        _print_json(prepare_ligand_pdbqt(sys.argv[2], overwrite=overwrite))
        return

    if command == "ligand-log":
        if len(sys.argv) < 3:
            _print_json(_error("LIGAND_PREPARATION_LOG_ARGS", "读取 ligand preparation 日志需要 project_dir 参数。"))
            return
        _print_json(load_ligand_preparation_log(sys.argv[2]))
        return

    if command == "reset":
        if len(sys.argv) < 4:
            _print_json(_error("PREPARATION_RESET_ARGS", "重置准备状态需要 project_dir 和 target 参数。"))
            return
        _print_json(reset_preparation_status(sys.argv[2], sys.argv[3]))
        return

    _print_json(_error("PREPARATION_COMMAND_UNKNOWN", f"未知准备命令：{command}"))


if __name__ == "__main__":
    main()
