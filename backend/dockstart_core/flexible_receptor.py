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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from dockstart_core.advanced_protocols import (
    ProtocolRunner,
    ProtocolValidationError,
    execute_meeko_receptor_flex,
    normalize_meeko_receptor_controls,
    parse_flexible_residue,
    validate_meeko_receptor_atom_partition,
    validate_flexible_residues,
)
from adapters.meeko_adapter import (
    receptor_cif_bridge_only_script_text,
    run_preparation_command,
)
from dockstart_core.mmcif_identity import (
    MmcifIdentityError,
    audit_mmcif_residue_identities,
    resolve_mmcif_flexible_selections,
    resolve_mmcif_receptor_preparation_controls,
    validate_mmcif_receptor_preparation_controls,
    verify_mmcif_pdb_bridge,
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
FLEX_IDENTITY_ROOT = FLEX_RECORD_ROOT / "identity"
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
    result = _error(
        detail["code"],
        detail["message"],
        raw_error=detail.get("detail", ""),
        suggestion=detail.get("suggestion", ""),
        title=detail.get("title", "柔性受体准备未完成"),
    )
    if detail["code"] in {
        "FLEX_BAD_RESIDUES_REVIEW_REQUIRED",
        "FLEX_BAD_RESIDUES_CHANGED",
    }:
        try:
            review_payload = json.loads(detail.get("detail", "") or "{}")
        except json.JSONDecodeError:
            review_payload = {}
        if isinstance(review_payload, dict):
            bad_residues = review_payload.get("bad_residues")
            if isinstance(bad_residues, list):
                result["review"] = {
                    "allow_bad_res": False,
                    "bad_residues": [str(value) for value in bad_residues if str(value)],
                    "acknowledged_bad_residues": [
                        str(value)
                        for value in review_payload.get("acknowledged_bad_residues", [])
                        if str(value)
                    ],
                }
    elif detail["code"] == "UNRESOLVED_ALTERNATE_LOCATION":
        try:
            review_payload = json.loads(detail.get("detail", "") or "{}")
        except json.JSONDecodeError:
            review_payload = {}
        if isinstance(review_payload, dict):
            result["review"] = {
                "selector": str(review_payload.get("selector") or ""),
                "altlocs": [
                    str(value)
                    for value in review_payload.get("altlocs", [])
                    if str(value)
                ],
            }
    return result


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _meeko_controls_from_identity_contract(
    controls: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the identity-bound mmCIF contract onto the closed Meeko schema."""

    alternate_locations = controls.get("alternate_locations")
    template_assignments = controls.get("template_assignments")
    deleted_residues = controls.get("deleted_residues")
    if (
        not isinstance(alternate_locations, list)
        or not isinstance(template_assignments, list)
        or not isinstance(deleted_residues, list)
    ):
        raise MmcifIdentityError(
            "MMCIF_PREPARATION_CONTROLS_INVALID",
            "mmCIF 受体准备控制合同缺少规范化控制列表。",
        )
    return {
        "schema_version": 1,
        "allow_bad_res": False,
        "alternate_locations": {
            str(item.get("selector") or ""): str(
                item.get("selected_altloc") or ""
            )
            for item in alternate_locations
            if isinstance(item, Mapping)
        },
        "template_assignments": {
            str(item.get("selector") or ""): str(
                item.get("assigned_template") or ""
            )
            for item in template_assignments
            if isinstance(item, Mapping)
        },
        "deleted_residues": [
            {
                "selector": str(item.get("selector") or ""),
                "expected_component_id": str(
                    item.get("expected_component_id") or ""
                ),
                "reason": str(item.get("reason") or ""),
            }
            for item in deleted_residues
            if isinstance(item, Mapping)
        ],
    }


def _selected_altlocs_from_controls(
    selections: Iterable[str],
    controls: Mapping[str, Any],
) -> dict[str, str]:
    selected = {
        parse_flexible_residue(value).canonical for value in selections
    }
    choices = (
        controls.get("alternate_locations")
        if isinstance(controls.get("alternate_locations"), Mapping)
        else {}
    )
    return {
        str(selector): str(altloc)
        for selector, altloc in choices.items()
        if str(selector) in selected
    }


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


def _identity_error(exc: MmcifIdentityError) -> dict[str, Any]:
    detail = exc.to_dict()
    return _error(
        detail["code"],
        detail["message"],
        raw_error=detail.get("detail", ""),
        suggestion=detail.get("suggestion", ""),
        title="mmCIF 残基身份未通过校验",
    )


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MmcifIdentityError(
            "MMCIF_IDENTITY_RECORD_INVALID",
            f"{label} 无法作为 UTF-8 JSON 读取。",
            suggestion="请删除损坏的身份审查目录后，从未变化的原始 mmCIF 重新审查。",
            detail=str(exc),
        ) from exc
    if not isinstance(payload, dict):
        raise MmcifIdentityError(
            "MMCIF_IDENTITY_RECORD_INVALID",
            f"{label} 不是 JSON 对象。",
            suggestion="请从未变化的原始 mmCIF 重新生成身份审查记录。",
        )
    return payload


def _bridge_verification_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    bridge = (
        payload.get("bridge")
        if isinstance(payload.get("bridge"), Mapping)
        else {}
    )
    return {
        "identity_contract_sha256": str(
            payload.get("identity_contract_sha256") or ""
        ),
        "bridge_sha256": str(bridge.get("sha256") or ""),
        "bridge_model_count": bridge.get("model_count"),
        "bridge_model_id": str(bridge.get("model_id") or ""),
        "bridge_coordinate_atom_count": bridge.get("coordinate_atom_count"),
        "bridge_polymer_atom_count": bridge.get("polymer_atom_count"),
        "bridge_nonpolymer_atom_count": bridge.get("nonpolymer_atom_count"),
        "verified_residue_count": payload.get("verified_residue_count"),
        "verified_atom_count": payload.get("verified_atom_count"),
        "verified_nonpolymer_residue_count": payload.get(
            "verified_nonpolymer_residue_count"
        ),
        "verified_nonpolymer_atom_count": payload.get(
            "verified_nonpolymer_atom_count"
        ),
        "coordinate_tolerance_angstrom": payload.get(
            "coordinate_tolerance_angstrom"
        ),
        "occupancy_tolerance": payload.get("occupancy_tolerance"),
        "verification_sha256": str(payload.get("verification_sha256") or ""),
    }


def _identity_residue_summaries(
    contract: Mapping[str, Any],
    *,
    field: str = "residues",
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    raw_residues = contract.get(field)
    for residue in raw_residues if isinstance(raw_residues, list) else []:
        if not isinstance(residue, Mapping):
            continue
        summaries.append(
            {
                "record_type": str(residue.get("record_type") or ""),
                "selector": str(residue.get("selector") or ""),
                "meeko_id": str(residue.get("meeko_id") or ""),
                "author": dict(residue.get("author") or {}),
                "label": dict(residue.get("label") or {}),
                "bridge": dict(residue.get("bridge") or {}),
                "atom_count": int(residue.get("atom_count") or 0),
                "atom_identity_sha256": str(
                    residue.get("atom_identity_sha256") or ""
                ),
                "alternate_locations": dict(
                    residue.get("alternate_locations") or {}
                ),
            }
        )
    return summaries


def _viewer_structure_payload(
    project_root: Path,
    path: Path,
    *,
    file_kind: str,
    source_format: str,
) -> dict[str, Any]:
    relative = _relative(project_root, path)
    return {
        "ok": True,
        "file_kind": file_kind,
        "relative_path": relative,
        "absolute_path": str(path),
        "exists": True,
        "format": "pdb",
        "content": path.read_text(encoding="utf-8", errors="strict"),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "source_format": source_format,
        "message": "已加载用于柔性残基点选的身份绑定结构。",
        "warnings": [],
        "error": None,
    }


def _mmcif_identity_review(
    project_root: Path,
    raw_path: Path,
    raw_file: str,
    python_executable: str,
) -> dict[str, Any]:
    contract = audit_mmcif_residue_identities(raw_path)
    source = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    source_sha256 = str(source.get("sha256") or "")
    if not source_sha256:
        raise MmcifIdentityError(
            "MMCIF_IDENTITY_SOURCE_HASH_MISSING",
            "mmCIF 身份合同没有冻结源文件 SHA256。",
        )
    source["file"] = raw_file
    contract["source"] = source
    review_dir = project_root / FLEX_IDENTITY_ROOT / source_sha256
    review_dir.mkdir(parents=True, exist_ok=True)
    contract_path = review_dir / "identity_contract.json"
    bridge_path = review_dir / "gemmi_bridge.pdb"
    verification_path = review_dir / "bridge_verification.json"
    helper_path = review_dir / "gemmi_bridge.py"
    command_path = review_dir / "bridge_command_result.json"

    existing = [path.exists() for path in (contract_path, bridge_path, verification_path)]
    if any(existing) and not all(existing):
        raise MmcifIdentityError(
            "MMCIF_IDENTITY_REVIEW_INCOMPLETE",
            "现有 mmCIF 身份审查目录不完整，已拒绝复用或覆盖。",
            suggestion="请保留该目录用于诊断，并在确认无用后手工移除，再重新审查原始 mmCIF。",
            detail=str(review_dir),
        )
    if all(existing):
        persisted_contract = _load_json_object(contract_path, "mmCIF 身份合同")
        if (
            str(persisted_contract.get("identity_sha256") or "")
            != str(contract.get("identity_sha256") or "")
            or str(
                (
                    persisted_contract.get("source")
                    if isinstance(persisted_contract.get("source"), Mapping)
                    else {}
                ).get("sha256")
                or ""
            )
            != source_sha256
        ):
            raise MmcifIdentityError(
                "MMCIF_IDENTITY_REVIEW_CHANGED",
                "现有身份审查与当前原始 mmCIF 不一致。",
                suggestion="请不要覆盖旧审查；确认原始文件来源后创建新的项目记录。",
                detail=str(review_dir),
            )
        verification = verify_mmcif_pdb_bridge(persisted_contract, bridge_path)
        recorded_verification = _load_json_object(
            verification_path,
            "mmCIF 桥接验证记录",
        )
        if _bridge_verification_evidence(
            recorded_verification
        ) != _bridge_verification_evidence(verification):
            raise MmcifIdentityError(
                "MMCIF_BRIDGE_VERIFICATION_CHANGED",
                "现有桥接 PDB 或验证记录已经变化。",
                suggestion="请保留现有目录用于诊断，不要继续使用该桥接。",
            )
        contract = persisted_contract
    else:
        helper_bytes = receptor_cif_bridge_only_script_text().encode("utf-8")
        if helper_path.exists():
            if helper_path.read_bytes() != helper_bytes:
                raise MmcifIdentityError(
                    "MMCIF_BRIDGE_HELPER_CHANGED",
                    "现有 Gemmi 桥接脚本与当前受审计版本不一致。",
                    suggestion="请保留现有审查目录用于诊断，并创建新的审查记录。",
                )
        else:
            atomic_write_bytes(helper_path, helper_bytes)
        pending_bridge = review_dir / f".gemmi-bridge-{uuid.uuid4().hex}.pdb"
        command = [
            python_executable,
            "-I",
            "-B",
            str(helper_path),
            str(raw_path),
            str(pending_bridge),
        ]
        try:
            completed = run_preparation_command(command, review_dir, timeout=300)
            command_record = {
                "schema_version": 1,
                "command": command,
                "cwd": str(review_dir),
                "exit_code": int(completed.returncode),
                "stdout": str(completed.stdout or ""),
                "stderr": str(completed.stderr or ""),
                "helper_sha256": _sha256_file(helper_path),
                "source_sha256": source_sha256,
            }
            atomic_write_json(command_path, command_record)
            if completed.returncode != 0:
                raise MmcifIdentityError(
                    "MMCIF_GEMMI_BRIDGE_FAILED",
                    "Gemmi 未能生成待验证的 mmCIF→PDB 桥接。",
                    suggestion="请查看 bridge_command_result.json，并确认 Assisted Python 含 Gemmi。",
                    detail=str(completed.stderr or completed.stdout or ""),
                )
            verification = verify_mmcif_pdb_bridge(contract, pending_bridge)
            if bridge_path.exists():
                raise MmcifIdentityError(
                    "MMCIF_BRIDGE_OUTPUT_COLLISION",
                    "身份审查期间桥接输出路径被其他进程占用。",
                    suggestion="请重新读取项目状态后重试。",
                )
            pending_bridge.replace(bridge_path)
            verification["bridge"]["file"] = _relative(project_root, bridge_path)
            atomic_write_json(contract_path, contract)
            atomic_write_json(verification_path, verification)
        finally:
            if pending_bridge.exists():
                pending_bridge.unlink(missing_ok=True)

    verification = verify_mmcif_pdb_bridge(contract, bridge_path)
    recorded_verification = _load_json_object(
        verification_path,
        "mmCIF 桥接验证记录",
    )
    if _bridge_verification_evidence(
        recorded_verification
    ) != _bridge_verification_evidence(verification):
        raise MmcifIdentityError(
            "MMCIF_BRIDGE_VERIFICATION_CHANGED",
            "桥接验证记录与当前 PDB 不一致。",
        )
    return {
        "ok": True,
        "project_dir": str(project_root),
        "source_format": "mmcif",
        "source_raw_file": raw_file,
        "source_sha256": source_sha256,
        "selection_context_sha256": str(contract.get("identity_sha256") or ""),
        "identity_contract_sha256": str(contract.get("identity_sha256") or ""),
        "coordinate_identity_sha256": str(
            contract.get("coordinate_identity_sha256") or ""
        ),
        "identity_contract_file": _relative(project_root, contract_path),
        "bridge_file": _relative(project_root, bridge_path),
        "bridge_sha256": str(verification["bridge"]["sha256"]),
        "bridge_verification_sha256": str(
            verification.get("verification_sha256") or ""
        ),
        "bridge_verification_file": _relative(project_root, verification_path),
        "model": dict(contract.get("model") or {}),
        "polymer_residue_count": int(contract.get("residue_count") or 0),
        "nonpolymer_residue_count": int(
            contract.get("nonpolymer_residue_count") or 0
        ),
        "coordinate_atom_count": int(
            contract.get("coordinate_atom_count") or 0
        ),
        "residues": _identity_residue_summaries(contract),
        "nonpolymer_residues": _identity_residue_summaries(
            contract,
            field="nonpolymer_residues",
        ),
        "viewer": _viewer_structure_payload(
            project_root,
            bridge_path,
            file_kind="receptor_identity_bridge",
            source_format="mmcif",
        ),
        "message": "mmCIF 作者/标签身份合同与 Gemmi PDB 桥接已验证。",
        "error": None,
    }


def get_flexible_receptor_identity_context(project_dir: str) -> dict[str, Any]:
    """Return the exact raw/bridge structure used for residue selection."""

    loaded = _load_project_payload(project_dir)
    if isinstance(loaded, dict):
        return loaded
    project_root, payload = loaded
    receptor = (
        payload.get("receptor")
        if isinstance(payload.get("receptor"), Mapping)
        else {}
    )
    raw_file = str(receptor.get("raw_file") or "")
    try:
        raw_path = _inside_project(project_root, raw_file, required=True)
    except ValueError as exc:
        return _error(
            "RECEPTOR_RAW_FILE_UNAVAILABLE",
            "柔性残基身份审查需要项目中已记录的 receptor.raw_file。",
            raw_error=str(exc),
            suggestion="请先导入原始 PDB 或 mmCIF 受体。",
        )
    suffix = raw_path.suffix.lower()
    if suffix == ".pdb":
        source_sha256 = _sha256_file(raw_path)
        return {
            "ok": True,
            "project_dir": str(project_root),
            "source_format": "pdb",
            "source_raw_file": raw_file,
            "source_sha256": source_sha256,
            "selection_context_sha256": source_sha256,
            "identity_contract_sha256": "",
            "model": {
                "id": "legacy_pdb",
                "count": 1,
                "selection_policy": "pdb_source_identity",
            },
            "residues": [],
            "viewer": _viewer_structure_payload(
                project_root,
                raw_path,
                file_kind="receptor_raw_identity",
                source_format="pdb",
            ),
            "message": "已加载项目原始 PDB 作为柔性残基点选依据。",
            "error": None,
        }
    if suffix not in {".cif", ".mmcif"}:
        return _error(
            "FLEX_RECEPTOR_RAW_FORMAT_UNSUPPORTED",
            "柔性残基身份审查只接受项目内原始 PDB 或 mmCIF。",
            raw_error=suffix or "无扩展名",
        )
    python_tool = get_resolved_python()
    if python_tool.status != "ok" or not python_tool.path:
        return _error(
            "FLEX_RECEPTOR_PYTHON_UNAVAILABLE",
            "没有找到可用于 Gemmi 身份桥接的 Assisted Python。",
            raw_error=python_tool.raw_error,
            suggestion="请先在工具链设置中恢复 Assisted Python 与 Gemmi。",
        )
    try:
        lock_path = project_root / ".flexible-receptor-identity.lock"
        with _exclusive_file_lock(lock_path):
            return _mmcif_identity_review(
                project_root,
                raw_path,
                raw_file,
                python_tool.path,
            )
    except MmcifIdentityError as exc:
        return _identity_error(exc)
    except (OSError, ValueError) as exc:
        return _error(
            "MMCIF_IDENTITY_REVIEW_ERROR",
            "mmCIF 身份审查未能安全完成。",
            raw_error=str(exc),
            suggestion="请查看 preparation/flexible_receptor/identity 中的记录。",
        )


def _normalized_protocol(payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    raw = payload.get(PROTOCOL_KEY)
    legacy = not isinstance(raw, Mapping)
    protocol = dict(raw) if isinstance(raw, Mapping) else {}
    mode = str(
        protocol.get("receptor_mode")
        or protocol.get("mode")
        or "rigid"
    ).strip().lower()
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

    source_format = str(config.get("source_format") or "")
    recorded_receptor_controls = (
        config.get("receptor_controls")
        if isinstance(config.get("receptor_controls"), Mapping)
        else None
    )
    if recorded_receptor_controls is not None:
        try:
            normalized_receptor_controls = normalize_meeko_receptor_controls(
                recorded_receptor_controls
            )
            recorded_controls_sha256 = str(
                config.get("receptor_controls_sha256") or ""
            )
            actual_controls_sha256 = _canonical_json_sha256(
                normalized_receptor_controls
            )
            if (
                normalized_receptor_controls
                != dict(recorded_receptor_controls)
                or not recorded_controls_sha256
                or recorded_controls_sha256 != actual_controls_sha256
            ):
                issues.append("结构化 receptor_controls 与冻结哈希不一致。")
        except ProtocolValidationError as exc:
            issues.append(f"结构化 receptor_controls 不可验证：{exc}")
    elif source_format == "mmcif":
        issues.append("mmCIF 柔性受体缺少结构化 receptor_controls。")

    if source_format:
        recorded_partition = (
            config.get("atom_partition")
            if isinstance(config.get("atom_partition"), Mapping)
            else {}
        )
        try:
            actual_partition = validate_meeko_receptor_atom_partition(
                _inside_project(
                    project_root,
                    str(config.get("rigid_file") or ""),
                    required=True,
                ),
                _inside_project(
                    project_root,
                    str(config.get("flex_file") or ""),
                    required=True,
                ),
                _inside_project(
                    project_root,
                    str(config.get("receptor_json_file") or ""),
                    required=True,
                ),
            )
            if (
                not str(recorded_partition.get("partition_sha256") or "")
                or str(recorded_partition.get("partition_sha256") or "")
                != str(actual_partition.get("partition_sha256") or "")
            ):
                issues.append("rigid/flex/receptor JSON 原子划分证据与准备记录不一致。")
        except (OSError, ValueError, ProtocolValidationError) as exc:
            issues.append(f"rigid/flex/receptor JSON 原子划分不可验证：{exc}")

    if source_format == "mmcif":
        identity = (
            config.get("identity")
            if isinstance(config.get("identity"), Mapping)
            else {}
        )
        artifact_hashes = (
            identity.get("artifact_sha256")
            if isinstance(identity.get("artifact_sha256"), Mapping)
            else {}
        )
        identity_files = {
            "identity_contract": "identity_contract_file",
            "selection_contract": "selection_contract_file",
            "preparation_controls": "preparation_controls_file",
            "bridge": "bridge_file",
            "bridge_verification": "bridge_verification_file",
        }
        resolved_identity_files: dict[str, Path] = {}
        for key, field in identity_files.items():
            try:
                path = _inside_project(
                    project_root,
                    str(identity.get(field) or ""),
                    required=True,
                )
                resolved_identity_files[key] = path
                if _sha256_file(path) != str(artifact_hashes.get(key) or ""):
                    issues.append(f"{field} 的 SHA256 与身份准备记录不一致。")
            except (OSError, ValueError) as exc:
                issues.append(f"{field} 不可用：{exc}")
        if set(resolved_identity_files) == set(identity_files):
            try:
                contract = _load_json_object(
                    resolved_identity_files["identity_contract"],
                    "mmCIF 身份合同",
                )
                selection = _load_json_object(
                    resolved_identity_files["selection_contract"],
                    "mmCIF 选择合同",
                )
                preparation_controls = _load_json_object(
                    resolved_identity_files["preparation_controls"],
                    "mmCIF 受体准备控制合同",
                )
                recorded_verification = _load_json_object(
                    resolved_identity_files["bridge_verification"],
                    "mmCIF 桥接验证记录",
                )
                verification = verify_mmcif_pdb_bridge(
                    contract,
                    resolved_identity_files["bridge"],
                )
                validate_mmcif_receptor_preparation_controls(
                    contract,
                    preparation_controls,
                )
                rebound_meeko_controls = (
                    _meeko_controls_from_identity_contract(
                        preparation_controls
                    )
                )
                normalized_rebound_controls = (
                    normalize_meeko_receptor_controls(
                        rebound_meeko_controls,
                        structure_path=resolved_identity_files["bridge"],
                        flexible_selections=[
                            str(item.get("selector") or "")
                            for item in config.get(
                                "selected_residues",
                                [],
                            )
                            if isinstance(item, Mapping)
                        ],
                    )
                )
                raw_selected = (
                    config.get("selected_residues")
                    if isinstance(config.get("selected_residues"), list)
                    else []
                )
                selectors = [
                    str(item.get("selector") or "")
                    if isinstance(item, Mapping)
                    else str(item)
                    for item in raw_selected
                ]
                selectors = [value for value in selectors if value]
                resolved_altlocs = (
                    config.get("resolved_altlocs")
                    if isinstance(config.get("resolved_altlocs"), Mapping)
                    else {}
                )
                recomputed_selection = resolve_mmcif_flexible_selections(
                    contract,
                    selectors,
                    resolved_altlocs={
                        str(key): str(value)
                        for key, value in resolved_altlocs.items()
                    },
                )
                if (
                    str(contract.get("identity_sha256") or "")
                    != str(identity.get("identity_contract_sha256") or "")
                ):
                    issues.append("mmCIF 身份合同哈希与项目记录不一致。")
                if (
                    str(contract.get("coordinate_identity_sha256") or "")
                    != str(
                        identity.get("coordinate_identity_sha256") or ""
                    )
                ):
                    issues.append("mmCIF 完整坐标身份哈希与项目记录不一致。")
                if (
                    str(preparation_controls.get("control_sha256") or "")
                    != str(
                        identity.get("preparation_controls_sha256") or ""
                    )
                    or normalized_rebound_controls
                    != dict(recorded_receptor_controls or {})
                ):
                    issues.append(
                        "mmCIF 受体准备控制合同与项目 receptor_controls 不一致。"
                    )
                if (
                    str(selection.get("selection_sha256") or "")
                    != str(identity.get("selection_sha256") or "")
                    or str(selection.get("selection_sha256") or "")
                    != str(recomputed_selection.get("selection_sha256") or "")
                    or str(selection.get("identity_contract_sha256") or "")
                    != str(contract.get("identity_sha256") or "")
                    or selection.get("selected_residues")
                    != recomputed_selection.get("selected_residues")
                    or selection.get("meeko_flexres")
                    != recomputed_selection.get("meeko_flexres")
                    or selection.get("wanted_altlocs")
                    != recomputed_selection.get("wanted_altlocs")
                ):
                    issues.append("mmCIF 残基选择合同与身份合同不一致。")
                if (
                    str(verification["bridge"].get("sha256") or "")
                    != str(identity.get("bridge_sha256") or "")
                    or str(verification.get("verification_sha256") or "")
                    != str(identity.get("bridge_verification_sha256") or "")
                ):
                    issues.append("mmCIF Gemmi 桥接验证哈希与项目记录不一致。")
                if _bridge_verification_evidence(
                    recorded_verification
                ) != _bridge_verification_evidence(verification):
                    issues.append("mmCIF 桥接验证文件与重新计算结果不一致。")
                if dict(identity.get("model") or {}) != dict(
                    contract.get("model") or {}
                ):
                    issues.append("mmCIF 模型记录与身份合同不一致。")
            except (OSError, ValueError, MmcifIdentityError) as exc:
                issues.append(f"mmCIF 身份/选择/桥接合同不可验证：{exc}")

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
    receptor_controls: Mapping[str, Any] | None = None,
    max_residues: int = 8,
    expected_selection_context_sha256: str = "",
    require_selection_context: bool = False,
) -> dict[str, Any]:
    """Validate raw input and selections against the exact point-selection context."""

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
    if suffix not in {".pdb", ".cif", ".mmcif"}:
        return _error(
            "FLEX_RECEPTOR_RAW_FORMAT_UNSUPPORTED",
            "柔性侧链准备只接受项目内原始 PDB 或 mmCIF。",
            raw_error=suffix or "无扩展名",
            suggestion="请重新导入包含原子坐标的原始 PDB 或 PDBx/mmCIF。",
        )
    identity_context = get_flexible_receptor_identity_context(str(project_root))
    if not identity_context.get("ok"):
        return identity_context
    current_context_sha256 = str(
        identity_context.get("selection_context_sha256") or ""
    )
    expected_context_sha256 = str(
        expected_selection_context_sha256 or ""
    ).strip().lower()
    if expected_context_sha256 and expected_context_sha256 != current_context_sha256:
        return _error(
            "FLEX_SELECTION_CONTEXT_CHANGED",
            "柔性残基选择绑定的结构身份已经变化。",
            raw_error=(
                f"expected={expected_context_sha256}; "
                f"current={current_context_sha256}"
            ),
            suggestion="请重新打开 3D 点选或身份审查，再提交残基选择。",
        )
    if require_selection_context and suffix in {".cif", ".mmcif"} and not expected_context_sha256:
        return _error(
            "MMCIF_SELECTION_CONTEXT_CONFIRMATION_REQUIRED",
            "mmCIF 柔性残基准备必须绑定已验证的身份合同。",
            suggestion="请先刷新身份审查或进入 3D 点选，再重新准备。",
        )

    selection_values = list(selections)
    if suffix in {".cif", ".mmcif"}:
        try:
            contract_path = _inside_project(
                project_root,
                str(identity_context.get("identity_contract_file") or ""),
                required=True,
            )
            bridge_path = _inside_project(
                project_root,
                str(identity_context.get("bridge_file") or ""),
                required=True,
            )
            contract = _load_json_object(contract_path, "mmCIF 身份合同")
            if receptor_controls is not None and resolved_altlocs is not None:
                raise MmcifIdentityError(
                    "MMCIF_RECEPTOR_CONTROLS_AMBIGUOUS",
                    "不能同时提交 receptor_controls 与旧 resolved_altlocs。",
                    suggestion="请只保留完整的 receptor_controls；其中必须覆盖全局 altloc 决定。",
                )
            raw_controls: Mapping[str, Any] = (
                receptor_controls
                if receptor_controls is not None
                else {
                    "schema_version": 1,
                    "allow_bad_res": False,
                    "alternate_locations": dict(resolved_altlocs or {}),
                    "template_assignments": {},
                    "deleted_residues": [],
                }
            )
            normalized_controls = normalize_meeko_receptor_controls(
                raw_controls,
                structure_path=bridge_path,
                flexible_selections=selection_values,
            )
            identity_preparation_controls = (
                resolve_mmcif_receptor_preparation_controls(
                    contract,
                    alternate_locations=normalized_controls[
                        "alternate_locations"
                    ],
                    template_assignments=normalized_controls[
                        "template_assignments"
                    ],
                    deleted_residues=normalized_controls[
                        "deleted_residues"
                    ],
                )
            )
            validate_mmcif_receptor_preparation_controls(
                contract,
                identity_preparation_controls,
            )
            selection_altlocs = _selected_altlocs_from_controls(
                selection_values,
                normalized_controls,
            )
            selection_contract = resolve_mmcif_flexible_selections(
                contract,
                selection_values,
                resolved_altlocs=selection_altlocs,
            )
            bridge_review = validate_flexible_residues(
                bridge_path,
                selection_values,
                resolved_altlocs=selection_altlocs,
                max_residues=max_residues,
            )
        except MmcifIdentityError as exc:
            failure = _identity_error(exc)
            failure["identity_context"] = identity_context
            return failure
        except (OSError, ValueError) as exc:
            return _error(
                "MMCIF_IDENTITY_RECORD_INVALID",
                "mmCIF 身份合同或桥接文件不可用。",
                raw_error=str(exc),
                suggestion="请重新执行身份审查。",
            )
        bridge_by_selector = {
            str(item.get("selector") or ""): item
            for item in bridge_review.get("residues", [])
            if isinstance(item, Mapping)
        }
        selected_residues: list[dict[str, Any]] = []
        for item in selection_contract["selected_residues"]:
            selected = dict(item)
            bridge_item = bridge_by_selector.get(str(item.get("selector") or ""), {})
            selected.update(
                {
                    "chain_id": str(
                        (item.get("author") or {}).get("chain_id")
                        if isinstance(item.get("author"), Mapping)
                        else ""
                    ),
                    "residue_number": int(
                        (item.get("author") or {}).get("sequence_id")
                        if isinstance(item.get("author"), Mapping)
                        else 0
                    ),
                    "insertion_code": str(
                        (item.get("author") or {}).get("insertion_code")
                        if isinstance(item.get("author"), Mapping)
                        else ""
                    ),
                    "residue_name": str(
                        (item.get("author") or {}).get("component_id")
                        if isinstance(item.get("author"), Mapping)
                        else ""
                    ),
                    "altlocs": list(
                        (
                            item.get("alternate_locations")
                            if isinstance(item.get("alternate_locations"), Mapping)
                            else {}
                        ).get("ids", [])
                    ),
                    "source": "原始 mmCIF 身份合同",
                    "bridge_atom_count": int(
                        bridge_item.get("atom_count") or 0
                    ),
                }
            )
            selected_residues.append(selected)
        review = {
            "source_structure": str(raw_path),
            "validation_structure": str(bridge_path),
            "source_format": "mmcif",
            "residues": selected_residues,
            "meeko_flexres": list(selection_contract["meeko_flexres"]),
            "wanted_altlocs": list(selection_contract["wanted_altlocs"]),
            "max_residues": max_residues,
            "identity_contract_sha256": str(
                selection_contract.get("identity_contract_sha256") or ""
            ),
            "selection_sha256": str(
                selection_contract.get("selection_sha256") or ""
            ),
            "model_id": str(selection_contract.get("model_id") or ""),
        }
        return {
            "ok": True,
            "project_dir": str(project_root),
            "source_raw_file": raw_file,
            "source_path": str(raw_path),
            "source_sha256": _sha256_file(raw_path),
            "meeko_input_path": str(bridge_path),
            "validation": review,
            "identity_context": identity_context,
            "selection_contract": selection_contract,
            "receptor_controls": normalized_controls,
            "receptor_controls_sha256": _canonical_json_sha256(
                normalized_controls
            ),
            "identity_preparation_controls": identity_preparation_controls,
            "identity_preparation_controls_sha256": str(
                identity_preparation_controls.get("control_sha256") or ""
            ),
            "message": "mmCIF 身份、Gemmi 桥接与柔性残基选择检查通过。",
            "error": None,
        }
    try:
        if receptor_controls is not None and resolved_altlocs is not None:
            raise ProtocolValidationError(
                "MEEKO_RECEPTOR_CONTROLS_AMBIGUOUS",
                "受体控制来源不唯一",
                "不能同时提交 receptor_controls 与 resolved_altlocs。",
                suggestion="请只保留结构化 receptor_controls。",
            )
        normalized_controls = (
            normalize_meeko_receptor_controls(
                receptor_controls,
                structure_path=raw_path,
                flexible_selections=selection_values,
            )
            if receptor_controls is not None
            else None
        )
        review = validate_flexible_residues(
            raw_path,
            selection_values,
            resolved_altlocs=(
                _selected_altlocs_from_controls(
                    selection_values,
                    normalized_controls,
                )
                if normalized_controls is not None
                else resolved_altlocs
            ),
            max_residues=max_residues,
        )
    except ProtocolValidationError as exc:
        failure = _protocol_error(exc)
        review_payload = (
            failure.get("review")
            if isinstance(failure.get("review"), Mapping)
            else {}
        )
        if review_payload.get("selector") and review_payload.get("altlocs"):
            identity_context = dict(identity_context)
            identity_context["residues"] = [
                {
                    "selector": str(review_payload["selector"]),
                    "alternate_locations": {
                        "ids": list(review_payload["altlocs"]),
                        "requires_explicit_choice": True,
                        "occupancy": {},
                    },
                }
            ]
        failure["identity_context"] = identity_context
        return failure
    return {
        "ok": True,
        "project_dir": str(project_root),
        "source_raw_file": raw_file,
        "source_path": str(raw_path),
        "source_sha256": _sha256_file(raw_path),
        "meeko_input_path": str(raw_path),
        "validation": review,
        "identity_context": identity_context,
        **(
            {
                "receptor_controls": normalized_controls,
                "receptor_controls_sha256": _canonical_json_sha256(
                    normalized_controls
                ),
            }
            if normalized_controls is not None
            else {}
        ),
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
    receptor_controls: Mapping[str, Any] | None = None,
    max_residues: int = 8,
    allow_bad_res: bool = False,
    acknowledged_bad_residues: Iterable[str] | None = None,
    expected_selection_context_sha256: str = "",
    runner: ProtocolRunner | None = None,
) -> dict[str, Any]:
    """Prepare, verify and atomically activate a flexible receptor protocol."""

    selection_values = list(selections)
    acknowledged_values = list(acknowledged_bad_residues or ())
    project_root = Path(project_dir).expanduser().resolve()
    lock_path = project_root / ".flexible-receptor.lock"
    try:
        with _exclusive_file_lock(lock_path):
            validation = validate_flexible_receptor_preparation(
                str(project_root),
                selection_values,
                resolved_altlocs=resolved_altlocs,
                receptor_controls=receptor_controls,
                max_residues=max_residues,
                expected_selection_context_sha256=expected_selection_context_sha256,
                require_selection_context=True,
            )
            if not validation.get("ok"):
                return validation
            if isinstance(validation.get("receptor_controls"), Mapping) and (
                allow_bad_res or acknowledged_values
            ):
                return _error(
                    "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
                    "结构化受体控制合同禁止使用 allow_bad_res。",
                    suggestion=(
                        "请显式修复模板，或在 receptor_controls 中记录"
                        " template_assignments/deleted_residues。"
                    ),
                )

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

            meeko_input_path = snapshot_path
            identity_record: dict[str, Any] | None = None
            if str(validation["validation"].get("source_format") or "") == "mmcif":
                context = (
                    validation.get("identity_context")
                    if isinstance(validation.get("identity_context"), Mapping)
                    else {}
                )
                contract_source = _inside_project(
                    project_root,
                    str(context.get("identity_contract_file") or ""),
                    required=True,
                )
                bridge_source = _inside_project(
                    project_root,
                    str(context.get("bridge_file") or ""),
                    required=True,
                )
                contract_snapshot = record_dir / "identity_contract.json"
                selection_snapshot = record_dir / "selection_contract.json"
                controls_snapshot = record_dir / "preparation_controls.json"
                bridge_snapshot = record_dir / "verified_gemmi_bridge.pdb"
                verification_snapshot = record_dir / "bridge_verification.json"
                atomic_write_bytes(contract_snapshot, contract_source.read_bytes())
                atomic_write_json(
                    selection_snapshot,
                    dict(validation.get("selection_contract") or {}),
                )
                atomic_write_json(
                    controls_snapshot,
                    dict(
                        validation.get("identity_preparation_controls") or {}
                    ),
                )
                atomic_write_bytes(bridge_snapshot, bridge_source.read_bytes())
                frozen_contract = _load_json_object(
                    contract_snapshot,
                    "冻结的 mmCIF 身份合同",
                )
                normalized_controls = (
                    validation.get("receptor_controls")
                    if isinstance(
                        validation.get("receptor_controls"),
                        Mapping,
                    )
                    else {}
                )
                frozen_identity_controls = (
                    resolve_mmcif_receptor_preparation_controls(
                        frozen_contract,
                        alternate_locations=normalized_controls.get(
                            "alternate_locations",
                            {},
                        ),
                        template_assignments=normalized_controls.get(
                            "template_assignments",
                            {},
                        ),
                        deleted_residues=normalized_controls.get(
                            "deleted_residues",
                            [],
                        ),
                    )
                )
                validate_mmcif_receptor_preparation_controls(
                    frozen_contract,
                    frozen_identity_controls,
                )
                persisted_identity_controls = _load_json_object(
                    controls_snapshot,
                    "冻结的 mmCIF 受体准备控制合同",
                )
                if (
                    str(
                        frozen_identity_controls.get("control_sha256")
                        or ""
                    )
                    != str(
                        persisted_identity_controls.get("control_sha256")
                        or ""
                    )
                    or frozen_identity_controls
                    != persisted_identity_controls
                ):
                    raise MmcifIdentityError(
                        "MMCIF_PREPARATION_CONTROLS_CHANGED",
                        "mmCIF 受体准备控制合同在冻结期间发生变化。",
                    )
                frozen_selection = resolve_mmcif_flexible_selections(
                    frozen_contract,
                    selection_values,
                    resolved_altlocs=_selected_altlocs_from_controls(
                        selection_values,
                        normalized_controls,
                    ),
                )
                if (
                    str(frozen_selection.get("selection_sha256") or "")
                    != str(
                        (
                            validation.get("selection_contract")
                            if isinstance(validation.get("selection_contract"), Mapping)
                            else {}
                        ).get("selection_sha256")
                        or ""
                    )
                ):
                    raise MmcifIdentityError(
                        "MMCIF_SELECTION_CONTRACT_CHANGED",
                        "柔性残基选择合同在冻结期间发生变化。",
                    )
                frozen_verification = verify_mmcif_pdb_bridge(
                    frozen_contract,
                    bridge_snapshot,
                )
                frozen_verification["bridge"]["file"] = _relative(
                    project_root,
                    bridge_snapshot,
                )
                atomic_write_json(verification_snapshot, frozen_verification)
                meeko_input_path = bridge_snapshot
                identity_record = {
                    "schema_version": 2,
                    "source_format": "mmcif",
                    "model": dict(context.get("model") or {}),
                    "identity_basis": str(
                        frozen_contract.get("identity_basis") or ""
                    ),
                    "identity_contract_file": _relative(
                        project_root,
                        contract_snapshot,
                    ),
                    "selection_contract_file": _relative(
                        project_root,
                        selection_snapshot,
                    ),
                    "bridge_file": _relative(project_root, bridge_snapshot),
                    "bridge_verification_file": _relative(
                        project_root,
                        verification_snapshot,
                    ),
                    "identity_contract_sha256": str(
                        frozen_contract.get("identity_sha256") or ""
                    ),
                    "coordinate_identity_sha256": str(
                        frozen_contract.get("coordinate_identity_sha256")
                        or ""
                    ),
                    "selection_sha256": str(
                        frozen_selection.get("selection_sha256") or ""
                    ),
                    "preparation_controls_file": _relative(
                        project_root,
                        controls_snapshot,
                    ),
                    "preparation_controls_sha256": str(
                        frozen_identity_controls.get("control_sha256")
                        or ""
                    ),
                    "bridge_sha256": str(
                        frozen_verification["bridge"]["sha256"]
                    ),
                    "bridge_verification_sha256": str(
                        frozen_verification.get("verification_sha256") or ""
                    ),
                    "artifact_sha256": {
                        "identity_contract": _sha256_file(contract_snapshot),
                        "selection_contract": _sha256_file(selection_snapshot),
                        "preparation_controls": _sha256_file(
                            controls_snapshot
                        ),
                        "bridge": _sha256_file(bridge_snapshot),
                        "bridge_verification": _sha256_file(
                            verification_snapshot
                        ),
                    },
                }

            output_basename = output_dir / "receptor"
            structured_controls = (
                validation.get("receptor_controls")
                if isinstance(validation.get("receptor_controls"), Mapping)
                else None
            )
            execution = execute_meeko_receptor_flex(
                python_tool.path,
                meeko_input_path,
                output_basename,
                selection_values,
                record_dir=record_dir,
                resolved_altlocs=(
                    None if structured_controls is not None else resolved_altlocs
                ),
                receptor_controls=structured_controls,
                max_residues=max_residues,
                allow_bad_res=(
                    False if structured_controls is not None else allow_bad_res
                ),
                acknowledged_bad_residues=(
                    ()
                    if structured_controls is not None
                    else acknowledged_values
                ),
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
                        "source_format": str(
                            validation["validation"].get("source_format") or "pdb"
                        ),
                        "selected_residues": execution.get("selected_residues", validation["validation"]["residues"]),
                        "resolved_altlocs": (
                            _selected_altlocs_from_controls(
                                selection_values,
                                structured_controls,
                            )
                            if structured_controls is not None
                            else dict(resolved_altlocs or {})
                        ),
                        **(
                            {
                                "receptor_controls": execution.get(
                                    "receptor_controls"
                                ),
                                "receptor_controls_sha256": execution.get(
                                    "receptor_controls_sha256"
                                ),
                                "receptor_controls_fingerprint": execution.get(
                                    "receptor_controls_fingerprint"
                                ),
                            }
                            if structured_controls is not None
                            else {}
                        ),
                        "max_residues": max_residues,
                        "scientific_review": execution.get("scientific_review", {
                            "allow_bad_res": False,
                            "acknowledged_bad_residues": [],
                            "detected_bad_residues": [],
                        }),
                        "rigid_file": relative_outputs["rigid_pdbqt"],
                        "flex_file": relative_outputs["flex_pdbqt"],
                        "receptor_json_file": relative_outputs["receptor_json"],
                        "sha256": output_hashes,
                        "atom_partition": execution.get("atom_partition"),
                        **(
                            {"identity": identity_record}
                            if identity_record is not None
                            else {}
                        ),
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
                "scientific_review": execution.get("scientific_review", {}),
                "atom_partition": execution.get("atom_partition"),
                **(
                    {
                        "receptor_controls": execution.get(
                            "receptor_controls"
                        ),
                        "receptor_controls_sha256": execution.get(
                            "receptor_controls_sha256"
                        ),
                        "receptor_controls_fingerprint": execution.get(
                            "receptor_controls_fingerprint"
                        ),
                    }
                    if structured_controls is not None
                    else {}
                ),
                **(
                    {"identity": identity_record}
                    if identity_record is not None
                    else {}
                ),
                "project": saved.get("project"),
                "message": (
                    "柔性受体三件套已验证并激活；已记录用户确认后由 Meeko 忽略的坏残基。"
                    if allow_bad_res
                    else "柔性受体三件套已验证并激活。"
                ),
                "error": None,
            }
            _write_wrapper_result(record_dir, success)
            return success
    except ProtocolValidationError as exc:
        return _protocol_error(exc)
    except MmcifIdentityError as exc:
        return _identity_error(exc)
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
identity = get_flexible_receptor_identity_context
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
    for name in ("status", "identity", "validate", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name not in {"status", "identity"}:
            command.add_argument("--residue", action="append", required=True)
            command.add_argument("--resolved-altloc", action="append", default=[])
            command.add_argument("--max-residues", type=int, default=8)
            command.add_argument(
                "--expected-selection-context-sha256",
                default="",
            )
            if name == "prepare":
                command.add_argument("--allow-bad-res", action="store_true")
                command.add_argument("--acknowledge-bad-residue", action="append", default=[])
    set_mode_parser = commands.add_parser("set-mode")
    set_mode_parser.add_argument("--project", required=True)
    set_mode_parser.add_argument("--mode", choices=("rigid", "flexible"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "status":
            result = get_flexible_receptor_status(args.project)
        elif args.command == "identity":
            result = get_flexible_receptor_identity_context(args.project)
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
                expected_selection_context_sha256=(
                    args.expected_selection_context_sha256
                ),
                **(
                    {
                        "allow_bad_res": args.allow_bad_res,
                        "acknowledged_bad_residues": args.acknowledge_bad_residue,
                    }
                    if args.command == "prepare"
                    else {}
                ),
            )
    except Exception as exc:  # noqa: BLE001 - CLI always emits one JSON response.
        result = _error("FLEX_RECEPTOR_CLI_ERROR", "柔性受体命令参数无效。", raw_error=str(exc))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
