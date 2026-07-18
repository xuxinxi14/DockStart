"""Reference-ligand RMSD workflow for completed DockStart runs."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from dockstart_core.persistence import atomic_write_bytes
from dockstart_core.project import (
    _error,
    _now_iso,
    _read_run_metadata,
    _safe_run_directory,
    _sha256_file,
    _update_run_metadata_transaction,
)
from dockstart_core.toolchain import get_resolved_python

SUPPORTED_REFERENCE_FORMATS = {".sdf", ".mol", ".pdb", ".pdbqt"}
MAX_REFERENCE_BYTES = 25 * 1024 * 1024


def _worker_path() -> Path:
    return Path(__file__).resolve().parents[1] / "adapters" / "reference_rmsd_worker.py"


def _decode_worker_payload(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def calculate_reference_rmsd(
    project_dir: str,
    run_id: str,
    mode: int,
    reference_path: str,
) -> dict[str, Any]:
    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None
    if str(metadata.get("status") or "") != "finished":
        return _error(
            "REFERENCE_RMSD_RUN_NOT_FINISHED",
            "只有已完成的对接运行才能计算共晶参考 RMSD。",
            suggestion="请先完成 Vina 运行并生成 out.pdbqt。",
        )
    try:
        selected_mode = int(mode)
    except (TypeError, ValueError):
        selected_mode = 0
    if selected_mode < 1:
        return _error("REFERENCE_RMSD_MODE_INVALID", "构象编号必须大于或等于 1。")

    source = Path(reference_path).expanduser()
    if source.suffix.lower() not in SUPPORTED_REFERENCE_FORMATS:
        return _error(
            "REFERENCE_FORMAT_UNSUPPORTED",
            "参考配体仅支持 SDF、MOL、PDB 或 PDBQT。",
            raw_error=source.suffix,
        )
    if not source.is_file():
        return _error("REFERENCE_FILE_NOT_FOUND", "没有找到参考配体文件。", raw_error=str(source))
    source_size = source.stat().st_size
    if source_size <= 0 or source_size > MAX_REFERENCE_BYTES:
        return _error(
            "REFERENCE_FILE_SIZE_INVALID",
            "参考配体文件为空或超过 25 MB，已拒绝读取。",
            raw_error=f"size={source_size}",
        )

    try:
        run_dir = _safe_run_directory(project_dir, run_id)
        output_relative = str(metadata.get("output_file") or f"runs/{run_id}/out.pdbqt")
        output_path = (Path(project_dir).expanduser().resolve() / output_relative).resolve(strict=True)
        output_path.relative_to(Path(project_dir).expanduser().resolve())
        if not output_path.is_file():
            raise FileNotFoundError(output_path)
        source_bytes = source.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        validation_dir = run_dir / "validation"
        reference_file = validation_dir / f"reference_{source_sha256[:12]}{source.suffix.lower()}"
        atomic_write_bytes(reference_file, source_bytes)
    except Exception as exc:  # noqa: BLE001 - converted to a user-facing error.
        return _error(
            "REFERENCE_RMSD_FILE_ERROR",
            "准备共晶 RMSD 输入文件时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 run 输出与参考配体文件可读，项目目录可写。",
        )

    python_tool = get_resolved_python()
    if python_tool.status != "ok" or not python_tool.path:
        return _error(
            "REFERENCE_RMSD_RDKIT_UNAVAILABLE",
            "当前工具链没有可用的 RDKit Python，无法计算对称性修正 RMSD。",
            raw_error=python_tool.raw_error,
            suggestion="请使用 Assisted 版，或在工具链设置中配置包含 RDKit 的 Python。",
        )
    worker = _worker_path()
    command = [
        python_tool.path,
        str(worker),
        "--pdbqt",
        str(output_path),
        "--reference",
        str(reference_file),
        "--mode",
        str(selected_mode),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(
            "REFERENCE_RMSD_WORKER_ERROR",
            "启动 RDKit 共晶 RMSD 计算时发生错误。",
            raw_error=str(exc),
        )
    worker_payload = _decode_worker_payload(completed.stdout)
    if completed.returncode != 0 or not worker_payload:
        return _error(
            "REFERENCE_RMSD_WORKER_ERROR",
            "RDKit 共晶 RMSD 计算未返回有效结果。",
            raw_error=(completed.stderr or completed.stdout)[-4000:],
        )
    if not worker_payload.get("ok"):
        worker_error = worker_payload.get("error") if isinstance(worker_payload.get("error"), dict) else {}
        worker_code = str(worker_error.get("code") or "REFERENCE_RMSD_FAILED")
        return _error(
            worker_code,
            str(worker_error.get("message") or "共晶参考 RMSD 计算失败。"),
            raw_error=str(worker_error.get("detail") or completed.stderr),
            suggestion=(
                "请使用 Assisted 版，或配置包含 RDKit 的 Python。"
                if worker_code == "REFERENCE_RMSD_RDKIT_UNAVAILABLE"
                else "请确认参考配体与对接配体为同一化学实体，且文件包含三维坐标。"
            ),
        )

    relative_reference = reference_file.relative_to(Path(project_dir).expanduser().resolve()).as_posix()
    result = {
        **worker_payload,
        "calculated_at": _now_iso(),
        "reference_file": relative_reference,
        "reference_source_name": source.name,
        "reference_sha256": source_sha256,
        "output_sha256": _sha256_file(output_path),
        "python_source": python_tool.source,
        "command": ["<configured-python>", worker.name, "--mode", str(selected_mode)],
    }

    def update(current: dict[str, Any]) -> dict[str, Any]:
        current["reference_rmsd"] = result
        artifacts = current.setdefault("artifacts", {})
        if isinstance(artifacts, dict):
            artifacts["reference_ligand"] = {
                "relative_path": relative_reference,
                "sha256": source_sha256,
                "size_bytes": source_size,
            }
        return current

    updated, update_error = _update_run_metadata_transaction(project_dir, run_id, update)
    if update_error:
        return update_error
    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "run_id": run_id,
        "metadata": updated,
        "reference_rmsd": result,
        "message": f"Mode {selected_mode} 的共晶参考 RMSD 已计算。",
        "error": None,
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) != 6 or sys.argv[1] != "calculate":
        _print_json(_error("REFERENCE_RMSD_ARGS", "计算共晶 RMSD 需要 project_dir、run_id、mode 和参考配体路径。"))
        return
    _print_json(calculate_reference_rmsd(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]))


if __name__ == "__main__":
    main()
