"""Project-scoped flexible-receptor preparation workflow.

This module is the narrow project boundary around :mod:`advanced_protocols`.
It never accepts a raw receptor or output path from the caller: the source is
always ``project.receptor.raw_file`` and every snapshot, record and generated
file stays below the opened project directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from dockstart_core.advanced_protocols import (
    ProtocolRunner,
    ProtocolValidationError,
    execute_meeko_receptor_flex,
    validate_flexible_residues,
)
from dockstart_core.persistence import atomic_write_bytes, atomic_write_json
from dockstart_core.project import (
    _exclusive_file_lock,
    _project_from_dict,
    load_project,
    save_project,
)
from dockstart_core.toolchain import get_resolved_python


PROTOCOL_KEY = "docking_protocol"
FLEX_PROTOCOL_VERSION = 1
FLEX_RECORD_ROOT = Path("preparation", "flexible_receptor")
FLEX_OUTPUT_ROOT = Path("prepared", "flexible_receptor")
EXPECTED_OUTPUT_KEYS = ("rigid_pdbqt", "flex_pdbqt", "receptor_json")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error(
    code: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
    title: str = "柔性受体准备未完成",
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "title": title,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _protocol_error(exc: ProtocolValidationError) -> dict[str, Any]:
    detail = exc.to_dict()
    return _error(
        detail["code"],
        detail["message"],
        raw_error=detail.get("detail", ""),
        suggestion=detail.get("suggestion", ""),
        title=detail.get("title", "柔性受体准备未完成"),
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_project(project_root: Path, relative: str, *, required: bool) -> Path:
    text = str(relative or "").strip()
    if not text:
        raise ValueError("项目未记录 receptor.raw_file。")
    supplied = Path(text)
    if supplied.is_absolute():
        raise ValueError("项目文件记录必须使用项目内相对路径。")
    candidate = (project_root / supplied).resolve(strict=False)
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("项目文件路径越过了项目目录边界。") from exc
    if required:
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("项目记录的受体原始结构不存在，或不是普通文件。")
        # A junction/symlink in a parent may resolve outside even when the leaf
        # itself is not reported as a symlink; the relative_to check above is
        # therefore authoritative.
    return candidate


def _load_project_payload(project_dir: str) -> tuple[Path, dict[str, Any]] | dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    project_root = Path(str(loaded["project_dir"])).resolve()
    payload = loaded.get("project")
    if not isinstance(payload, dict):
        return _error("PROJECT_PAYLOAD_INVALID", "项目数据不是有效对象。")
    return project_root, payload


def _normalized_protocol(payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    raw = payload.get(PROTOCOL_KEY)
    legacy = not isinstance(raw, Mapping)
    protocol = dict(raw) if isinstance(raw, Mapping) else {}
    mode = str(protocol.get("receptor_mode") or "rigid").strip().lower()
    if mode not in {"rigid", "flexible"}:
        mode = "rigid"
    protocol["schema_version"] = FLEX_PROTOCOL_VERSION
    protocol["receptor_mode"] = mode
    return protocol, legacy


def _flex_config_integrity(
    project_root: Path,
    project_payload: Mapping[str, Any],
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        return {"ready": False, "issues": ["项目尚未生成柔性受体三件套。"], "files": {}}

    issues: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    hashes = config.get("sha256") if isinstance(config.get("sha256"), Mapping) else {}
    file_fields = {
        "rigid_pdbqt": "rigid_file",
        "flex_pdbqt": "flex_file",
        "receptor_json": "receptor_json_file",
    }
    for key, field_name in file_fields.items():
        relative = str(config.get(field_name) or "")
        try:
            path = _inside_project(project_root, relative, required=True)
            actual_hash = _sha256_file(path)
            expected_hash = str(hashes.get(key) or "")
            if not expected_hash or actual_hash != expected_hash:
                issues.append(f"{field_name} 的 SHA256 与准备记录不一致。")
            files[key] = {
                "file": relative,
                "exists": True,
                "sha256": actual_hash,
                "sha256_matches": bool(expected_hash and actual_hash == expected_hash),
            }
        except (OSError, ValueError) as exc:
            issues.append(f"{field_name} 不可用：{exc}")
            files[key] = {"file": relative, "exists": False, "sha256": "", "sha256_matches": False}

    receptor = project_payload.get("receptor") if isinstance(project_payload.get("receptor"), Mapping) else {}
    current_raw_file = str(receptor.get("raw_file") or "")
    expected_raw_file = str(config.get("source_raw_file") or "")
    if not expected_raw_file or current_raw_file != expected_raw_file:
        issues.append("当前 receptor.raw_file 与柔性受体准备来源不一致。")
    else:
        try:
            raw_path = _inside_project(project_root, current_raw_file, required=True)
            if _sha256_file(raw_path) != str(config.get("source_sha256") or ""):
                issues.append("当前 receptor.raw_file 内容已在准备后发生变化。")
        except (OSError, ValueError) as exc:
            issues.append(f"当前 receptor.raw_file 不可用：{exc}")

    return {"ready": not issues, "issues": issues, "files": files}


def get_flexible_receptor_status(project_dir: str) -> dict[str, Any]:
    """Return the configured and effective receptor mode.

    A project without ``docking_protocol`` is deliberately treated as a rigid
    receptor project, preserving all historical projects without migration.
    """

    loaded = _load_project_payload(project_dir)
    if isinstance(loaded, dict):
        return loaded
    project_root, payload = loaded
    protocol, legacy = _normalized_protocol(payload)
    config = protocol.get("flexible_receptor")
    integrity = _flex_config_integrity(project_root, payload, config if isinstance(config, Mapping) else None)
    configured_mode = protocol["receptor_mode"]
    effective_mode = "flexible" if configured_mode == "flexible" and integrity["ready"] else "rigid"
    return {
        "ok": True,
        "project_dir": str(project_root),
        "mode": configured_mode,
        "effective_mode": effective_mode,
        "legacy_default": legacy,
        "flexible_ready": integrity["ready"],
        "integrity": integrity,
        "flexible_receptor": dict(config) if isinstance(config, Mapping) else None,
        "message": (
            "当前使用经过校验的柔性侧链受体。"
            if effective_mode == "flexible"
            else "当前使用刚性受体。"
        ),
        "error": None,
    }


def validate_flexible_receptor_preparation(
    project_dir: str,
    selections: Iterable[str],
    *,
    resolved_altlocs: Mapping[str, str] | None = None,
    max_residues: int = 8,
) -> dict[str, Any]:
    """Validate project raw input and residue selections without writing files."""

    loaded = _load_project_payload(project_dir)
    if isinstance(loaded, dict):
        return loaded
    project_root, payload = loaded
    receptor = payload.get("receptor") if isinstance(payload.get("receptor"), Mapping) else {}
    raw_file = str(receptor.get("raw_file") or "")
    try:
        raw_path = _inside_project(project_root, raw_file, required=True)
    except ValueError as exc:
        return _error(
            "RECEPTOR_RAW_FILE_UNAVAILABLE",
            "柔性侧链准备只能使用项目中已记录的 receptor.raw_file。",
            raw_error=str(exc),
            suggestion="请先导入或下载原始受体 PDB，并确认项目记录有效。",
        )
    suffix = raw_path.suffix.lower()
    if suffix in {".cif", ".mmcif"}:
        return _error(
            "FLEX_RECEPTOR_CIF_BRIDGE_UNAVAILABLE",
            "当前项目级柔性受体流程尚未接入经过审计的 mmCIF→PDB 桥接，因此拒绝直接准备 CIF。",
            raw_error=raw_file,
            suggestion="请优先使用保留目标链、残基编号和替代构象信息的规范 PDB；不要手工改用 PDBQT 推断残基。",
        )
    if suffix != ".pdb":
        return _error(
            "FLEX_RECEPTOR_RAW_FORMAT_UNSUPPORTED",
            "柔性侧链准备当前只接受项目内原始 PDB。",
            raw_error=suffix or "无扩展名",
            suggestion="请将受体原始结构以规范 PDB 写入项目 raw 目录后重试。",
        )
    try:
        review = validate_flexible_residues(
            raw_path,
            list(selections),
            resolved_altlocs=resolved_altlocs,
            max_residues=max_residues,
        )
    except ProtocolValidationError as exc:
        return _protocol_error(exc)
    return {
        "ok": True,
        "project_dir": str(project_root),
        "source_raw_file": raw_file,
        "source_path": str(raw_path),
        "source_sha256": _sha256_file(raw_path),
        "validation": review,
        "message": "原始 PDB 与柔性残基选择检查通过。",
        "error": None,
    }


def _next_preparation_id(project_root: Path) -> str:
    record_root = project_root / FLEX_RECORD_ROOT
    output_root = project_root / FLEX_OUTPUT_ROOT
    record_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = f"flex_{index:03d}"
        if not (record_root / candidate).exists() and not (output_root / candidate).exists():
            return candidate
    raise RuntimeError("无法分配新的柔性受体准备编号。")


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root).as_posix()


def _write_wrapper_result(record_dir: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_json(record_dir / "wrapper_result.json", dict(payload))


def _save_protocol(project_root: Path, payload: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    model = _project_from_dict(dict(payload), project_root)
    model.preserved_data[PROTOCOL_KEY] = dict(protocol)
    return save_project(model)


def prepare_flexible_receptor(
    project_dir: str,
    selections: Iterable[str],
    *,
    resolved_altlocs: Mapping[str, str] | None = None,
    max_residues: int = 8,
    runner: ProtocolRunner | None = None,
) -> dict[str, Any]:
    """Prepare, verify and atomically activate a flexible receptor protocol."""

    selection_values = list(selections)
    project_root = Path(project_dir).expanduser().resolve()
    lock_path = project_root / ".flexible-receptor.lock"
    try:
        with _exclusive_file_lock(lock_path):
            validation = validate_flexible_receptor_preparation(
                str(project_root),
                selection_values,
                resolved_altlocs=resolved_altlocs,
                max_residues=max_residues,
            )
            if not validation.get("ok"):
                return validation

            python_tool = get_resolved_python()
            if python_tool.status != "ok" or not python_tool.path:
                return _error(
                    "FLEX_RECEPTOR_PYTHON_UNAVAILABLE",
                    "没有找到可用于 Meeko 柔性受体准备的 Python。",
                    raw_error=python_tool.raw_error,
                    suggestion="请在工具链设置中配置 Assisted Python。",
                )

            preparation_id = _next_preparation_id(project_root)
            record_dir = project_root / FLEX_RECORD_ROOT / preparation_id
            output_dir = project_root / FLEX_OUTPUT_ROOT / preparation_id
            record_dir.mkdir(parents=True, exist_ok=False)
            output_dir.mkdir(parents=True, exist_ok=False)

            raw_path = Path(str(validation["source_path"]))
            raw_bytes = raw_path.read_bytes()
            source_sha256 = _sha256_bytes(raw_bytes)
            if source_sha256 != validation["source_sha256"]:
                failure = _error(
                    "FLEX_RECEPTOR_RAW_CHANGED",
                    "受体原始结构在验证与快照之间发生变化，已拒绝执行。",
                    suggestion="请确认没有其他程序正在改写 raw 文件后重试。",
                )
                _write_wrapper_result(record_dir, {**failure, "preparation_id": preparation_id})
                return failure

            snapshot_path = record_dir / f"input_snapshot{raw_path.suffix.lower()}"
            atomic_write_bytes(snapshot_path, raw_bytes)
            if _sha256_file(snapshot_path) != source_sha256:
                failure = _error("FLEX_RECEPTOR_SNAPSHOT_MISMATCH", "受体项目内快照校验失败。")
                _write_wrapper_result(record_dir, {**failure, "preparation_id": preparation_id})
                return failure

            output_basename = output_dir / "receptor"
            execution = execute_meeko_receptor_flex(
                python_tool.path,
                snapshot_path,
                output_basename,
                selection_values,
                record_dir=record_dir,
                resolved_altlocs=resolved_altlocs,
                max_residues=max_residues,
                runner=runner,
                cwd=record_dir,
            )
            published = execution.get("published_outputs")
            if not isinstance(published, Mapping) or set(published) != set(EXPECTED_OUTPUT_KEYS):
                failure = _error(
                    "FLEX_RECEPTOR_OUTPUT_SET_INVALID",
                    "Meeko 未返回完整的 rigid PDBQT、flex PDBQT 与 receptor JSON 三件套。",
                )
                _write_wrapper_result(record_dir, {**failure, "preparation_id": preparation_id})
                return failure

            output_hashes: dict[str, str] = {}
            relative_outputs: dict[str, str] = {}
            for key in EXPECTED_OUTPUT_KEYS:
                path = Path(str(published[key])).resolve()
                path.relative_to(project_root)
                if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
                    raise RuntimeError(f"已声明输出不可用：{key}")
                output_hashes[key] = _sha256_file(path)
                relative_outputs[key] = _relative(project_root, path)

            reloaded = _load_project_payload(str(project_root))
            if isinstance(reloaded, dict):
                failure = reloaded
                _write_wrapper_result(record_dir, {**failure, "preparation_id": preparation_id})
                return failure
            _, latest_payload = reloaded
            latest_receptor = (
                latest_payload.get("receptor")
                if isinstance(latest_payload.get("receptor"), Mapping)
                else {}
            )
            latest_raw_file = str(latest_receptor.get("raw_file") or "")
            try:
                latest_raw_path = _inside_project(project_root, latest_raw_file, required=True)
                latest_sha256 = _sha256_file(latest_raw_path)
            except (OSError, ValueError):
                latest_sha256 = ""
            if latest_raw_file != validation["source_raw_file"] or latest_sha256 != source_sha256:
                failure = _error(
                    "FLEX_RECEPTOR_RAW_CHANGED",
                    "受体原始结构在准备期间发生变化；三件套已保留用于审计，但项目仍维持原受体模式。",
                    suggestion="请检查 raw 文件来源，确认稳定后重新准备。",
                )
                _write_wrapper_result(
                    record_dir,
                    {**failure, "preparation_id": preparation_id, "sha256": output_hashes},
                )
                return failure

            protocol, _ = _normalized_protocol(latest_payload)
            protocol.update(
                {
                    "schema_version": FLEX_PROTOCOL_VERSION,
                    "receptor_mode": "flexible",
                    "updated_at": _now_iso(),
                    "flexible_receptor": {
                        "status": "ready",
                        "preparation_id": preparation_id,
                        "prepared_at": _now_iso(),
                        "source_raw_file": validation["source_raw_file"],
                        "source_snapshot_file": _relative(project_root, snapshot_path),
                        "source_sha256": source_sha256,
                        "selected_residues": execution.get("selected_residues", validation["validation"]["residues"]),
                        "resolved_altlocs": dict(resolved_altlocs or {}),
                        "max_residues": max_residues,
                        "rigid_file": relative_outputs["rigid_pdbqt"],
                        "flex_file": relative_outputs["flex_pdbqt"],
                        "receptor_json_file": relative_outputs["receptor_json"],
                        "sha256": output_hashes,
                        "execution_record_file": _relative(project_root, record_dir / "command_result.json"),
                    },
                }
            )
            saved = _save_protocol(project_root, latest_payload, protocol)
            if not saved.get("ok"):
                failure = _error(
                    "FLEX_RECEPTOR_ACTIVATION_FAILED",
                    "柔性受体三件套已验证，但 project.json 原子激活失败；项目仍维持原模式。",
                    raw_error=json.dumps(saved.get("error"), ensure_ascii=False),
                    suggestion="请重新读取项目并检查是否有并发修改。",
                )
                _write_wrapper_result(record_dir, {**failure, "preparation_id": preparation_id})
                return failure

            success = {
                "ok": True,
                "project_dir": str(project_root),
                "preparation_id": preparation_id,
                "mode": "flexible",
                "source_raw_file": validation["source_raw_file"],
                "source_sha256": source_sha256,
                "outputs": relative_outputs,
                "sha256": output_hashes,
                "project": saved.get("project"),
                "message": "柔性受体三件套已验证并激活。",
                "error": None,
            }
            _write_wrapper_result(record_dir, success)
            return success
    except ProtocolValidationError as exc:
        return _protocol_error(exc)
    except Exception as exc:  # noqa: BLE001 - return a stable project API error.
        return _error(
            "FLEX_RECEPTOR_PREPARATION_ERROR",
            "项目级柔性受体准备发生错误，project.json 未切换到新模式。",
            raw_error=str(exc),
            suggestion="请查看 preparation/flexible_receptor 中的执行记录。",
        )


def set_receptor_docking_mode(project_dir: str, mode: str) -> dict[str, Any]:
    """Atomically switch between the rigid and already-verified flexible modes."""

    normalized = str(mode or "").strip().lower()
    if normalized not in {"rigid", "flexible"}:
        return _error(
            "RECEPTOR_MODE_INVALID",
            "受体模式只能是 rigid 或 flexible。",
            raw_error=str(mode),
        )
    loaded = _load_project_payload(project_dir)
    if isinstance(loaded, dict):
        return loaded
    project_root, payload = loaded
    protocol, _ = _normalized_protocol(payload)
    if normalized == "flexible":
        config = protocol.get("flexible_receptor")
        integrity = _flex_config_integrity(project_root, payload, config if isinstance(config, Mapping) else None)
        if not integrity["ready"]:
            return _error(
                "FLEX_RECEPTOR_NOT_READY",
                "现有柔性受体三件套未通过来源与 SHA256 校验，不能激活。",
                raw_error="; ".join(integrity["issues"]),
                suggestion="请重新执行柔性受体准备。",
            )
    protocol["receptor_mode"] = normalized
    protocol["updated_at"] = _now_iso()
    saved = _save_protocol(project_root, payload, protocol)
    if not saved.get("ok"):
        return _error(
            "RECEPTOR_MODE_SAVE_FAILED",
            "保存受体模式失败，project.json 未被覆盖。",
            raw_error=json.dumps(saved.get("error"), ensure_ascii=False),
        )
    return {
        "ok": True,
        "project_dir": str(project_root),
        "mode": normalized,
        "project": saved.get("project"),
        "message": "已切换为柔性侧链受体。" if normalized == "flexible" else "已切换为刚性受体。",
        "error": None,
    }


# Short API aliases used by adapters that expose status/validate/prepare/set-mode.
status = get_flexible_receptor_status
validate = validate_flexible_receptor_preparation
prepare = prepare_flexible_receptor
set_mode = set_receptor_docking_mode


def _parse_altloc(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        selector, separator, altloc = value.rpartition("=")
        if not separator or not selector or not altloc:
            raise ValueError(f"无法解析替代构象参数：{value}")
        result[selector] = altloc
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart 项目级柔性受体准备")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "validate", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name != "status":
            command.add_argument("--residue", action="append", required=True)
            command.add_argument("--resolved-altloc", action="append", default=[])
            command.add_argument("--max-residues", type=int, default=8)
    set_mode_parser = commands.add_parser("set-mode")
    set_mode_parser.add_argument("--project", required=True)
    set_mode_parser.add_argument("--mode", choices=("rigid", "flexible"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "status":
            result = get_flexible_receptor_status(args.project)
        elif args.command == "set-mode":
            result = set_receptor_docking_mode(args.project, args.mode)
        else:
            altlocs = _parse_altloc(args.resolved_altloc)
            function = validate_flexible_receptor_preparation if args.command == "validate" else prepare_flexible_receptor
            result = function(
                args.project,
                args.residue,
                resolved_altlocs=altlocs,
                max_residues=args.max_residues,
            )
    except Exception as exc:  # noqa: BLE001 - CLI always emits one JSON response.
        result = _error("FLEX_RECEPTOR_CLI_ERROR", "柔性受体命令参数无效。", raw_error=str(exc))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
