"""Project-scoped, topology-preserving Meeko result export."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dockstart_core.advanced_protocols import (
    ProtocolRunner,
    ProtocolValidationError,
    execute_mk_export,
    inspect_meeko_ligand_pdbqt,
)
from dockstart_core.project import RUN_ID_PATTERN, _update_run_metadata_transaction, load_run_metadata
from dockstart_core.toolchain import get_resolved_python


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error(code: str, message: str, *, raw_error: str = "", suggestion: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_context(project_dir: str, run_id: str) -> tuple[Path, dict[str, Any], Path] | dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(str(run_id or "")):
        return _error("RUN_ID_INVALID", "run_id 格式无效。")
    root = Path(project_dir).expanduser().resolve()
    loaded = load_run_metadata(str(root), run_id)
    if not loaded.get("ok"):
        return loaded
    metadata = loaded.get("metadata") if isinstance(loaded.get("metadata"), dict) else {}
    if metadata.get("status") != "finished":
        return _error(
            "RUN_NOT_FINISHED",
            "只有 finished 状态的对接结果可以导出 SDF。",
            suggestion="请先完成 Vina 运行与结果解析。",
        )
    output_file = str(metadata.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())
    relative = Path(output_file)
    if relative.is_absolute():
        return _error("RESULT_PATH_UNSAFE", "对接结果必须使用项目内相对路径。")
    result_path = (root / relative).resolve(strict=False)
    try:
        result_path.relative_to(root / "runs" / run_id)
    except ValueError:
        return _error("RESULT_PATH_UNSAFE", "对接结果越过了当前 run 目录边界。")
    if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size <= 0:
        return _error("RESULT_PDBQT_MISSING", "没有找到非空的对接结果 PDBQT。")
    return root, metadata, result_path


def _allocate_export_dir(root: Path, run_id: str) -> tuple[str, Path]:
    """Atomically reserve a new audit directory across UI and CLI processes."""

    export_root = root / "runs" / run_id / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1000):
        export_id = f"sdf_{index:03d}"
        export_dir = export_root / export_id
        try:
            export_dir.mkdir()
        except FileExistsError:
            continue
        return export_id, export_dir
    raise RuntimeError("无法分配新的 SDF 导出编号。")


def _protocol_export_error(metadata: dict[str, Any]) -> dict[str, Any] | None:
    protocol = metadata.get("docking_protocol") if isinstance(metadata.get("docking_protocol"), dict) else {}
    if protocol.get("mode") == "flexible":
        return _error(
            "FLEXIBLE_RESULT_SDF_UNSUPPORTED",
            "当前版本尚不能安全恢复有限柔性侧链对接结果的拓扑 SDF。",
            suggestion="请保留 PDBQT 结果；待 receptor JSON 与柔性残基映射接入导出链路后再导出 SDF。",
        )
    return None


def get_result_export_status(project_dir: str, run_id: str) -> dict[str, Any]:
    context = _run_context(project_dir, run_id)
    if isinstance(context, dict):
        return context
    root, metadata, result_path = context
    protocol_error = _protocol_export_error(metadata)
    if protocol_error:
        return protocol_error
    try:
        inspection = inspect_meeko_ligand_pdbqt(result_path)
    except ProtocolValidationError as exc:
        return _error(exc.code, exc.message, raw_error=exc.detail, suggestion=exc.suggestion)
    exports = metadata.get("result_exports") if isinstance(metadata.get("result_exports"), list) else []
    return {
        "ok": True,
        "project_dir": str(root),
        "run_id": run_id,
        "ready": bool(inspection.get("embedded_topology")),
        "inspection": inspection,
        "exports": exports,
        "message": (
            "当前结果保留 Meeko 原始拓扑，可安全导出 SDF。"
            if inspection.get("embedded_topology")
            else "当前结果缺少原始拓扑映射，禁止猜测键级导出 SDF。"
        ),
        "error": None,
    }


def export_result_sdf(
    project_dir: str,
    run_id: str,
    *,
    runner: ProtocolRunner | None = None,
) -> dict[str, Any]:
    context = _run_context(project_dir, run_id)
    if isinstance(context, dict):
        return context
    root, metadata, result_path = context
    protocol_error = _protocol_export_error(metadata)
    if protocol_error:
        return protocol_error
    python = get_resolved_python()
    if python.status != "ok" or not python.path:
        return _error(
            "MEEKO_PYTHON_UNAVAILABLE",
            "没有找到可执行 Meeko mk_export 的 Python。",
            raw_error=python.raw_error,
            suggestion="Assisted 版请先修复内置工具链；Basic 版需配置兼容的 Python/Meeko。",
        )
    try:
        export_id, export_dir = _allocate_export_dir(root, run_id)
        output_sdf = export_dir / "poses.sdf"
        execution = execute_mk_export(
            python.path,
            result_path,
            output_sdf,
            record_dir=export_dir / "record",
            runner=runner,
            cwd=export_dir,
        )
        if not output_sdf.is_file() or output_sdf.stat().st_size <= 0:
            return _error("SDF_EXPORT_OUTPUT_MISSING", "Meeko 未发布非空的 poses.sdf。")
        relative_output = output_sdf.relative_to(root).as_posix()
        record = {
            "export_id": export_id,
            "created_at": _now_iso(),
            "protocol": "meeko_result_export",
            "input_file": result_path.relative_to(root).as_posix(),
            "input_sha256": _sha256(result_path),
            "output_file": relative_output,
            "output_sha256": _sha256(output_sdf),
            "python_path": python.path,
            "python_source": python.source,
            "execution_record": (export_dir / "record" / "command_result.json").relative_to(root).as_posix(),
            "topology_evidence": execution.get("topology_evidence", {}),
        }

        def append_export(current: dict[str, Any]) -> dict[str, Any]:
            entries = current.get("result_exports") if isinstance(current.get("result_exports"), list) else []
            current["result_exports"] = [*entries, record]
            return current

        _, transaction_error = _update_run_metadata_transaction(str(root), run_id, append_export)
        if transaction_error:
            return transaction_error
        return {
            "ok": True,
            "project_dir": str(root),
            "run_id": run_id,
            "export": record,
            "message": "已使用 Meeko 原始拓扑导出 SDF；未根据原子距离猜测键级。",
            "error": None,
        }
    except ProtocolValidationError as exc:
        return _error(exc.code, exc.message, raw_error=exc.detail, suggestion=exc.suggestion)
    except Exception as exc:  # noqa: BLE001 - stable CLI boundary.
        return _error(
            "RESULT_SDF_EXPORT_FAILED",
            "导出 SDF 时发生错误，原始对接结果未被修改。",
            raw_error=str(exc),
            suggestion="请查看本次 exports/sdf_NNN/record 记录。",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart Meeko 拓扑 SDF 导出")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "export"):
        item = sub.add_parser(command)
        item.add_argument("--project", required=True)
        item.add_argument("--run", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = (
        get_result_export_status(args.project, args.run)
        if args.command == "status"
        else export_result_sdf(args.project, args.run)
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
