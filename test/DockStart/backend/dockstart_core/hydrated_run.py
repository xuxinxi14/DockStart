"""Project-scoped run orchestration for experimental hydrated AD4 docking."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from adapters import vina_adapter
from dockstart_core import __version__
from dockstart_core import autogrid as autogrid_core
from dockstart_core import hydrated_maps as hydrated_map_core
from dockstart_core.persistence import atomic_write_json, atomic_write_text
from dockstart_core.project import (
    HYDRATED_PROTOCOL_ID,
    HYDRATED_RETAINED_OUTPUT_NAME,
    HYDRATED_WATER_FREE_OUTPUT_NAME,
    HYDRATED_WATERS_MANIFEST_NAME,
    RUN_SCORE_ARTIFACT_KEYS,
    SHA256_PATTERN,
    _build_run_snapshot_config,
    _build_vina_command,
    _format_command_preview,
    _hash_snapshot,
    _metadata_relative_path,
    _now_iso,
    _parse_pdbqt_stats,
    _project_from_dict,
    _project_relative_existing_file,
    _recorded_artifact_contract_present,
    _run_output_file,
    _safe_run_directory,
    _system_snapshot,
    _tool_hash_snapshot,
    _validate_ad4_maps_post_run_integrity,
    _hydrated_frozen_grid_coverage,
    _with_artifact_hashes,
    _write_run_metadata,
    get_next_run_id,
    get_run_files_status,
    load_project,
    load_run_metadata,
    load_scores_csv,
    parse_vina_log_text,
    save_project,
    validate_box_params,
    validate_vina_params,
)
from dockstart_core.settings import load_settings

MINIMUM_VINA_VERSION = "1.2.0"
PROTOCOL_STABILITY = "experimental"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
HYDRATED_PROVENANCE_SCHEMA_VERSION = 1
HYDRATED_PROVENANCE_CONTRACT_ID = (
    "dockstart_hydrated_frozen_provenance_v1"
)
# Vina 1.2.x writes the score table with std::setprecision(4) in default-float
# mode, while REMARK VINA RESULT in the PDBQT retains three fixed decimals for
# affinity and both RMSD fields.  A preceding verbose score block leaves the
# table stream in fixed mode.  Reconciliation must reproduce those two
# serialization paths, not use broad numeric tolerances.
VINA_SCORE_TABLE_SIGNIFICANT_DIGITS = 4
VINA_VERBOSE_SCORE_TABLE_FIXED_DECIMALS = 4
PDBQT_RESULT_SERIALIZATION_HALF_QUANTUM = 0.0005

_HYDRATED_POSTPROCESS_SEMANTICS = {
    "affinity": "original_ad4_hydrated_affinity_unchanged",
    "pose_order": "original_model_order_unchanged",
    "retained_output": "strong_and_weak_waters_only",
    "water_free_output": "all_final_type_W_atoms_removed",
}


def _error(
    code: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
    project_dir: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "protocol_id": HYDRATED_PROTOCOL_ID,
        "stability": PROTOCOL_STABILITY,
        "project_dir": project_dir,
        "run_id": run_id,
        "message": message,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    if not match:
        return None
    return tuple(int(match.group(index)) for index in range(1, 4))


def _project_file(
    root: Path,
    relative_path: str,
    *,
    label: str,
    expected_sha256: str = "",
) -> tuple[Path | None, dict[str, Any] | None]:
    path, error = _project_relative_existing_file(
        root,
        relative_path,
        f"HYDRATED_{label.upper()}",
        label,
    )
    if error or path is None:
        details = error.get("error") if isinstance(error, dict) else {}
        return None, _error(
            str((details or {}).get("code") or f"HYDRATED_{label.upper()}_MISSING"),
            str((details or {}).get("message") or f"{label}不可读取。"),
            raw_error=str((details or {}).get("raw_error") or relative_path),
            suggestion=str((details or {}).get("suggestion") or "请重新准备水合协议输入。"),
            project_dir=str(root),
        )
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        return None, _error(
            f"HYDRATED_{label.upper()}_INVALID",
            f"{label}不是非空普通文件。",
            raw_error=str(path),
            suggestion="请重新准备水合协议输入。",
            project_dir=str(root),
        )
    if expected_sha256:
        actual = str(_hash_snapshot(path, relative_path).get("sha256") or "").lower()
        if (
            not SHA256_PATTERN.fullmatch(expected_sha256.lower())
            or actual != expected_sha256.lower()
        ):
            return None, _error(
                f"HYDRATED_{label.upper()}_HASH_MISMATCH",
                f"{label}与准备记录的 SHA256 不一致。",
                raw_error=f"expected={expected_sha256}; actual={actual}; path={path}",
                suggestion="请重新准备水合配体或 affinity maps。",
                project_dir=str(root),
            )
    return path, None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _snapshot_size(record: Mapping[str, Any]) -> int:
    value = record.get("size_bytes")
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value


def vina_score_table_serialization_matches(
    log_value_text: Any,
    pdbqt_value: Any,
    *,
    verbosity: int,
) -> bool:
    if (
        not isinstance(log_value_text, str)
        or isinstance(pdbqt_value, bool)
        or not isinstance(pdbqt_value, (int, float))
    ):
        return False
    try:
        log_value = float(log_value_text)
        serialized_pdbqt_value = float(pdbqt_value)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(log_value) or not math.isfinite(
        serialized_pdbqt_value
    ):
        return False
    if not vina_score_table_token_is_canonical(
        log_value_text,
        verbosity=verbosity,
    ):
        return False
    format_specification = _vina_score_table_format_specification(verbosity)
    pdbqt_half_quantum = PDBQT_RESULT_SERIALIZATION_HALF_QUANTUM
    numerical_slack = 1e-12
    lower_unrounded = (
        serialized_pdbqt_value - pdbqt_half_quantum + numerical_slack
    )
    upper_unrounded = (
        serialized_pdbqt_value + pdbqt_half_quantum - numerical_slack
    )
    reachable_bounds = (
        float(format(lower_unrounded, format_specification)),
        float(format(upper_unrounded, format_specification)),
    )
    return (
        min(reachable_bounds) - numerical_slack
        <= log_value
        <= max(reachable_bounds) + numerical_slack
    )


def _vina_score_table_format_specification(verbosity: int) -> str:
    if verbosity > 1:
        return f".{VINA_VERBOSE_SCORE_TABLE_FIXED_DECIMALS}f"
    return f".{VINA_SCORE_TABLE_SIGNIFICANT_DIGITS}g"


def vina_score_table_token_is_canonical(
    log_value_text: Any,
    *,
    verbosity: int,
) -> bool:
    """Require the exact token emitted by Vina's score-table stream state."""

    if (
        not isinstance(log_value_text, str)
        or isinstance(verbosity, bool)
        or verbosity not in {1, 2}
    ):
        return False
    try:
        value = float(log_value_text)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(value):
        return False
    return log_value_text == format(
        value,
        _vina_score_table_format_specification(verbosity),
    )


def _vina_score_table_numeric_interval(
    log_value_text: str,
    *,
    verbosity: int,
) -> tuple[float, float] | None:
    """Return a conservative pre-serialization interval for one Vina token."""

    if not vina_score_table_token_is_canonical(
        log_value_text,
        verbosity=verbosity,
    ):
        return None
    value = float(log_value_text)
    if verbosity > 1:
        quantum = 10.0 ** (-VINA_VERBOSE_SCORE_TABLE_FIXED_DECIMALS)
        half_quantum = quantum / 2.0
        return value - half_quantum, value + half_quantum
    elif value == 0.0:
        return 0.0, 0.0

    lower_probe = math.nextafter(value, -math.inf)
    upper_probe = math.nextafter(value, math.inf)

    def decimal_exponent(probe: float) -> int:
        magnitude = abs(probe)
        exponent = math.floor(math.log10(magnitude))
        if magnitude < 10.0**exponent:
            exponent -= 1
        elif magnitude >= 10.0 ** (exponent + 1):
            exponent += 1
        return exponent

    lower_quantum = 10.0 ** (
        decimal_exponent(lower_probe)
        - VINA_SCORE_TABLE_SIGNIFICANT_DIGITS
        + 1
    )
    upper_quantum = 10.0 ** (
        decimal_exponent(upper_probe)
        - VINA_SCORE_TABLE_SIGNIFICANT_DIGITS
        + 1
    )
    return (
        value - lower_quantum / 2.0,
        value + upper_quantum / 2.0,
    )


def _vina_joint_serialization_interval(
    log_value_text: str,
    pdbqt_value: Any,
    *,
    verbosity: int,
) -> tuple[float, float] | None:
    if (
        isinstance(pdbqt_value, bool)
        or not isinstance(pdbqt_value, (int, float))
        or not math.isfinite(float(pdbqt_value))
    ):
        return None
    log_interval = _vina_score_table_numeric_interval(
        log_value_text,
        verbosity=verbosity,
    )
    if log_interval is None:
        return None
    pdbqt_interval = (
        float(pdbqt_value) - PDBQT_RESULT_SERIALIZATION_HALF_QUANTUM,
        float(pdbqt_value) + PDBQT_RESULT_SERIALIZATION_HALF_QUANTUM,
    )
    lower = max(log_interval[0], pdbqt_interval[0])
    upper = min(log_interval[1], pdbqt_interval[1])
    if lower > upper:
        return None
    return lower, upper


def vina_affinity_serialization_matches(
    log_affinity_text: Any,
    pdbqt_affinity: Any,
    *,
    verbosity: int,
) -> bool:
    return vina_score_table_serialization_matches(
        log_affinity_text,
        pdbqt_affinity,
        verbosity=verbosity,
    )


def vina_rmsd_serialization_matches(
    log_rmsd_text: Any,
    pdbqt_rmsd: Any,
    *,
    verbosity: int,
) -> bool:
    return vina_score_table_serialization_matches(
        log_rmsd_text,
        pdbqt_rmsd,
        verbosity=verbosity,
    )


def _files_are_byte_identical(
    left: Path,
    right: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(chunk_size)
                right_chunk = right_handle.read(chunk_size)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _recorded_project_file(
    root: Path,
    record: Mapping[str, Any],
    *,
    path_key: str = "path",
    label: str,
    allow_empty: bool = False,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Resolve and hash-check a project-relative historical artifact record."""

    relative_text = str(record.get(path_key) or "")
    expected_hash = str(record.get("sha256") or "").lower()
    expected_size = _snapshot_size(record)
    relative = Path(relative_text)
    if (
        not relative_text
        or relative.is_absolute()
        or relative.drive
        or not SHA256_PATTERN.fullmatch(expected_hash)
        or expected_size < 0
        or (not allow_empty and expected_size <= 0)
    ):
        return None, _error(
            "HYDRATED_PROVENANCE_SOURCE_RECORD_INVALID",
            f"{label} 的历史文件记录缺少可信的相对路径、大小或 SHA256。",
            raw_error=json.dumps(dict(record), ensure_ascii=False),
            suggestion="请重新准备水合配体、maps 和新的 run。",
            project_dir=str(root),
        )
    lexical = root / relative
    try:
        if lexical.is_symlink():
            raise ValueError("路径是符号链接")
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        return None, _error(
            "HYDRATED_PROVENANCE_SOURCE_PATH_INVALID",
            f"{label} 不在当前项目的普通文件边界内。",
            raw_error=f"{lexical}: {exc}",
            suggestion="请保留当前记录用于审计，并重新准备新的 run。",
            project_dir=str(root),
        )
    if (
        resolved != lexical.absolute()
        or not resolved.is_file()
        or resolved.stat().st_size != expected_size
        or (not allow_empty and expected_size <= 0)
    ):
        return None, _error(
            "HYDRATED_PROVENANCE_SOURCE_SIZE_MISMATCH",
            f"{label} 的文件类型或大小与历史记录不一致。",
            raw_error=(
                f"recorded={expected_size}; "
                f"actual={resolved.stat().st_size if resolved.exists() else 'missing'}; "
                f"path={resolved}"
            ),
            suggestion="请保留当前记录用于审计，并重新准备新的 run。",
            project_dir=str(root),
        )
    actual_hash = str(
        _hash_snapshot(resolved, relative.as_posix()).get("sha256") or ""
    ).lower()
    if actual_hash != expected_hash:
        return None, _error(
            "HYDRATED_PROVENANCE_SOURCE_HASH_MISMATCH",
            f"{label} 已在 run 冻结前发生变化。",
            raw_error=(
                f"expected={expected_hash}; actual={actual_hash}; path={resolved}"
            ),
            suggestion="请保留当前记录用于审计，并重新准备新的 run。",
            project_dir=str(root),
        )
    return resolved, None


def _hydrated_provenance_sources(
    root: Path,
    ligand_manifest: Mapping[str, Any],
    maps_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any] | None]:
    """Resolve the immutable evidence that a modern hydrated run must carry."""

    ligand_source = _mapping(ligand_manifest.get("source"))
    ligand_outputs = _mapping(ligand_manifest.get("outputs"))
    ligand_script = _mapping(ligand_manifest.get("script"))
    ligand_execution = _mapping(ligand_manifest.get("execution"))
    base_manifest_record = _mapping(maps_manifest.get("base_maps_manifest"))
    audit_files = {
        str(item.get("name") or ""): item
        for item in _list_of_mappings(maps_manifest.get("audit_files"))
    }

    raw_suffix = Path(str(ligand_source.get("snapshot_file") or "")).suffix.lower()
    if raw_suffix not in {".sdf", ".mol"}:
        return None, _error(
            "HYDRATED_PROVENANCE_RAW_FORMAT_INVALID",
            "水合配体 preparation manifest 未记录可冻结的 SDF/MOL 原始输入。",
            raw_error=str(ligand_source.get("snapshot_file") or ""),
            suggestion="请重新准备水合配体和新的 run。",
            project_dir=str(root),
        )

    specifications = {
        "raw_ligand": {
            "record": {
                **ligand_source,
                "path": str(ligand_source.get("snapshot_file") or ""),
            },
            "target_name": f"raw_ligand{raw_suffix}",
            "target_group": "hydrated",
            "label": "原始配体快照",
            "allow_empty": False,
            "source_identity": str(ligand_source.get("path") or ""),
        },
        "added_h_ligand": {
            "record": _mapping(ligand_outputs.get("added_h_sdf")),
            "target_name": "ligand_added_h.sdf",
            "target_group": "hydrated",
            "label": "加氢配体 SDF",
            "allow_empty": False,
        },
        "preparation_script": {
            "record": ligand_script,
            "target_name": "prepare_hydrated_ligand.py",
            "target_group": "hydrated",
            "label": "水合配体准备脚本",
            "allow_empty": False,
        },
        "preparation_stdout": {
            "record": _mapping(ligand_execution.get("stdout")),
            "target_name": "preparation_stdout.txt",
            "target_group": "hydrated",
            "label": "水合配体准备 stdout",
            "allow_empty": True,
        },
        "preparation_stderr": {
            "record": _mapping(ligand_execution.get("stderr")),
            "target_name": "preparation_stderr.txt",
            "target_group": "hydrated",
            "label": "水合配体准备 stderr",
            "allow_empty": True,
        },
        "base_maps_manifest": {
            "record": base_manifest_record,
            "path_key": "relative_path",
            "target_name": "base_manifest.json",
            "target_group": "maps",
            "label": "基础 AutoGrid maps manifest",
            "allow_empty": False,
        },
        "gpf": {
            "record": _mapping(audit_files.get("receptor.gpf")),
            "path_key": "relative_path",
            "target_name": "receptor.gpf",
            "target_group": "maps",
            "label": "AutoGrid GPF",
            "allow_empty": False,
        },
        "autogrid_stdout": {
            "record": _mapping(audit_files.get("stdout.txt")),
            "path_key": "relative_path",
            "target_name": "autogrid_stdout.txt",
            "target_group": "maps",
            "label": "AutoGrid stdout",
            "allow_empty": True,
        },
        "autogrid_stderr": {
            "record": _mapping(audit_files.get("stderr.txt")),
            "path_key": "relative_path",
            "target_name": "autogrid_stderr.txt",
            "target_group": "maps",
            "label": "AutoGrid stderr",
            "allow_empty": True,
        },
        "autogrid_log": {
            "record": _mapping(audit_files.get("autogrid.glg")),
            "path_key": "relative_path",
            "target_name": "autogrid.glg",
            "target_group": "maps",
            "label": "AutoGrid GLG",
            "allow_empty": False,
        },
    }
    if "receptor.maps.xyz" in audit_files:
        specifications["base_maps_xyz"] = {
            "record": _mapping(audit_files.get("receptor.maps.xyz")),
            "path_key": "relative_path",
            "target_name": "receptor.maps.xyz",
            "target_group": "maps",
            "label": "AutoGrid maps.xyz",
            "allow_empty": False,
        }

    resolved: dict[str, dict[str, Any]] = {}
    for key, specification in specifications.items():
        record = _mapping(specification.get("record"))
        source, source_error = _recorded_project_file(
            root,
            record,
            path_key=str(specification.get("path_key") or "path"),
            label=str(specification["label"]),
            allow_empty=bool(specification.get("allow_empty")),
        )
        if source_error:
            return None, source_error
        assert source is not None
        resolved[key] = {
            **copy.deepcopy(specification),
            "source": source,
            "source_record": record,
        }
    return resolved, None


def _copy_verified(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    expected = str(expected_sha256 or "").lower()
    if not SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError(f"缺少可信的源文件 SHA256：{source}")
    before = _hash_snapshot(source, source.name)
    if str(before.get("sha256") or "").lower() != expected:
        raise RuntimeError(f"源文件在冻结前已变化：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    after_source = _hash_snapshot(source, source.name)
    target = _hash_snapshot(destination, destination.name)
    before_size = _snapshot_size(before)
    target_size = _snapshot_size(target)
    if (
        str(after_source.get("sha256") or "").lower() != expected
        or str(target.get("sha256") or "").lower() != expected
        or before_size < 0
        or target_size < 0
        or target_size != before_size
    ):
        raise RuntimeError(f"冻结期间源文件发生变化或副本校验失败：{source}")
    return target


def _hydrated_prerequisites(project_dir: str) -> dict[str, Any]:
    from dockstart_core.hydrated import get_status as get_hydrated_status

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded
    root = Path(project_dir).expanduser().resolve()
    project = _project_from_dict(loaded["project"], root)
    project.project_dir = str(root)
    hydrated_status = get_hydrated_status(str(root))
    if not hydrated_status.get("ok"):
        return hydrated_status
    if not hydrated_status.get("preparation_ready"):
        return _error(
            "HYDRATED_LIGAND_NOT_READY",
            "水合配体尚未准备完成或准备记录已失效。",
            raw_error="；".join(str(item) for item in hydrated_status.get("issues") or []),
            suggestion="请先在实验性水合对接页面重新准备水合配体。",
            project_dir=str(root),
        )
    ligand_manifest = _mapping(hydrated_status.get("manifest"))
    maps_manifest = _mapping(hydrated_status.get("maps_manifest"))
    recorded_autogrid = _mapping(maps_manifest.get("autogrid"))
    if maps_manifest and not autogrid_core.autogrid_version_supported(
        str(recorded_autogrid.get("version") or ""),
        HYDRATED_PROTOCOL_ID,
    ):
        minimum_version = ".".join(
            str(part)
            for part in autogrid_core.minimum_autogrid_version(
                HYDRATED_PROTOCOL_ID
            )
        )
        return _error(
            "HYDRATED_AUTOGRID_VERSION_UNSUPPORTED",
            (
                "水合 affinity maps 未绑定受支持的 "
                f"AutoGrid {minimum_version}+ 生成版本。"
            ),
            raw_error=str(recorded_autogrid.get("version") or "未识别"),
            suggestion="请使用受支持的 AutoGrid4 重新生成水合 maps。",
            project_dir=str(root),
        )
    if not hydrated_status.get("maps_ready"):
        return _error(
            "HYDRATED_MAPS_NOT_READY",
            "水合 affinity maps 尚未准备完成或已失效。",
            raw_error="；".join(str(item) for item in hydrated_status.get("maps_issues") or []),
            suggestion="请重新生成与当前受体、Box 和水合配体一致的 maps。",
            project_dir=str(root),
        )

    ligand_outputs = _mapping(ligand_manifest.get("outputs"))
    hydrated_ligand = _mapping(ligand_outputs.get("hydrated_pdbqt"))
    maps = _mapping(maps_manifest.get("maps"))
    receptor_record = _mapping(maps_manifest.get("receptor"))
    maps_ligand_record = _mapping(maps_manifest.get("ligand"))
    map_files = _list_of_mappings(maps.get("files"))
    required_files = [
        str(item)
        for item in maps.get("required_files")
        if isinstance(item, str)
    ] if isinstance(maps.get("required_files"), list) else []
    map_prefix = str(maps.get("prefix") or "")
    prefix_name = Path(map_prefix).name
    if (
        maps_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or maps_manifest.get("status") != "ready"
        or not map_files
        or not prefix_name
        or Path(prefix_name).name != prefix_name
    ):
        return _error(
            "HYDRATED_MAPS_MANIFEST_INVALID",
            "水合 maps manifest 缺少协议、状态、prefix 或文件清单。",
            suggestion="请重新生成水合 maps。",
            project_dir=str(root),
        )

    records_by_name = {
        str(item.get("name") or ""): item
        for item in map_files
        if str(item.get("name") or "")
    }
    raw_hydrated_ligand_types = maps.get("ligand_atom_types")
    raw_autogrid_ligand_types = maps.get("autogrid_ligand_atom_types")
    hydrated_ligand_types = autogrid_core.canonical_atom_types(
        raw_hydrated_ligand_types
    )
    autogrid_ligand_types = autogrid_core.canonical_atom_types(
        raw_autogrid_ligand_types
    )
    atom_type_lists_valid = (
        isinstance(raw_hydrated_ligand_types, list)
        and isinstance(raw_autogrid_ligand_types, list)
        and all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_hydrated_ligand_types
        )
        and all(
            isinstance(item, str) and bool(item.strip())
            for item in raw_autogrid_ligand_types
        )
        and len(hydrated_ligand_types) == len(raw_hydrated_ligand_types)
        and len(autogrid_ligand_types) == len(raw_autogrid_ligand_types)
        and set(hydrated_ligand_types).issubset(
            autogrid_core.STANDARD_NON_METAL_TYPES | {"W"}
        )
        and set(autogrid_ligand_types).issubset(
            autogrid_core.STANDARD_NON_METAL_TYPES
        )
    )
    water_map = _mapping(maps.get("water_map"))
    water_parameters = _mapping(water_map.get("parameters"))
    water_sources = _mapping(water_map.get("sources"))
    water_file_name = f"{prefix_name}.W.map"
    water_file_record = _mapping(records_by_name.get(water_file_name))
    oa_file_record = _mapping(records_by_name.get(f"{prefix_name}.OA.map"))
    hd_file_record = _mapping(records_by_name.get(f"{prefix_name}.HD.map"))
    expected_water_parameters = {
        "mode": "BEST",
        "weight": hydrated_map_core.BEST_WEIGHT,
        "entropy": hydrated_map_core.DISPLACEMENT_ENTROPY,
        "oa_weight": hydrated_map_core.OA_WEIGHT,
        "hd_weight": hydrated_map_core.HD_WEIGHT,
        "output_decimals": hydrated_map_core.OUTPUT_DECIMALS,
        "positive_value_rule": "entropy_if_oa_gt_0_or_hd_gt_0",
    }
    if (
        not atom_type_lists_valid
        or set(required_files) != set(records_by_name)
        or len(required_files) != len(records_by_name)
        or "W" not in hydrated_ligand_types
        or "W" in autogrid_ligand_types
        or not {"OA", "HD"}.issubset(set(autogrid_ligand_types))
        or not (set(hydrated_ligand_types) - {"W"}).issubset(
            set(autogrid_ligand_types)
        )
        or water_map.get("name") != water_file_name
        or water_map.get("method") != "hydrated_ad4_best_v1"
        or water_parameters != expected_water_parameters
        or str(water_map.get("relative_path") or "")
        != str(water_file_record.get("relative_path") or "")
        or str(water_map.get("sha256") or "").lower()
        != str(water_file_record.get("sha256") or "").lower()
        or _mapping(water_sources.get("oa")).get("relative_path")
        != oa_file_record.get("relative_path")
        or str(_mapping(water_sources.get("oa")).get("sha256") or "").lower()
        != str(oa_file_record.get("sha256") or "").lower()
        or _mapping(water_sources.get("hd")).get("relative_path")
        != hd_file_record.get("relative_path")
        or str(_mapping(water_sources.get("hd")).get("sha256") or "").lower()
        != str(hd_file_record.get("sha256") or "").lower()
        or _mapping(water_map.get("geometry"))
        != _mapping(maps.get("geometry"))
    ):
        return _error(
            "HYDRATED_WATER_MAP_BINDING_INVALID",
            "水合 maps 的原子类型、BEST 参数或 OA/HD/W 绑定记录无效。",
            suggestion="请重新生成水合 maps。",
            project_dir=str(root),
        )

    ligand_relative = str(hydrated_ligand.get("path") or "")
    ligand_sha256 = str(hydrated_ligand.get("sha256") or "").lower()
    receptor_relative = str(project.receptor.file or "")
    receptor_sha256 = str(receptor_record.get("source_sha256") or "").lower()
    if (
        str(receptor_record.get("source_relative_path") or "") != receptor_relative
        or str(maps_ligand_record.get("source_relative_path") or "") != ligand_relative
        or str(maps_ligand_record.get("source_sha256") or "").lower() != ligand_sha256
    ):
        return _error(
            "HYDRATED_MAPS_SOURCE_BINDING_MISMATCH",
            "水合 maps 未绑定当前刚性受体和 active 水合配体。",
            suggestion="请重新生成水合 maps。",
            project_dir=str(root),
        )

    receptor_path, receptor_error = _project_file(
        root,
        receptor_relative,
        label="receptor",
        expected_sha256=receptor_sha256,
    )
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_file(
        root,
        ligand_relative,
        label="hydrated_ligand",
        expected_sha256=ligand_sha256,
    )
    if ligand_error:
        return ligand_error
    assert receptor_path is not None and ligand_path is not None

    map_paths: list[tuple[dict[str, Any], Path]] = []
    observed_names: set[str] = set()
    for record in map_files:
        name = str(record.get("name") or "")
        relative_path = str(record.get("relative_path") or "")
        expected_hash = str(record.get("sha256") or "").lower()
        if (
            not name
            or Path(name).name != name
            or name in observed_names
            or Path(relative_path).name != name
        ):
            return _error(
                "HYDRATED_MAP_RECORD_INVALID",
                "水合 maps manifest 包含无效或重复的 map 文件记录。",
                raw_error=json.dumps(record, ensure_ascii=False),
                suggestion="请重新生成水合 maps。",
                project_dir=str(root),
            )
        path, map_error = _project_file(
            root,
            relative_path,
            label="map",
            expected_sha256=expected_hash,
        )
        if map_error:
            return map_error
        assert path is not None
        observed_names.add(name)
        map_paths.append((record, path))

    required = {
        f"{prefix_name}.maps.fld",
        f"{prefix_name}.e.map",
        f"{prefix_name}.d.map",
        f"{prefix_name}.W.map",
    }
    if not required.issubset(observed_names):
        return _error(
            "HYDRATED_MAPS_INCOMPLETE",
            "水合 maps 缺少 fld、电势、脱溶剂或 W map。",
            raw_error=", ".join(sorted(observed_names)),
            suggestion="请重新生成完整的水合 maps。",
            project_dir=str(root),
        )

    ligand_manifest_file = str(hydrated_status.get("active_ligand_manifest") or "")
    ligand_manifest_sha256 = str(hydrated_status.get("manifest_sha256") or "").lower()
    maps_manifest_file = str(hydrated_status.get("active_maps_manifest") or "")
    maps_manifest_sha256 = str(hydrated_status.get("maps_manifest_sha256") or "").lower()
    ligand_manifest_path, ligand_manifest_error = _project_file(
        root,
        ligand_manifest_file,
        label="ligand_manifest",
        expected_sha256=ligand_manifest_sha256,
    )
    if ligand_manifest_error:
        return ligand_manifest_error
    maps_manifest_path, maps_manifest_error = _project_file(
        root,
        maps_manifest_file,
        label="maps_manifest",
        expected_sha256=maps_manifest_sha256,
    )
    if maps_manifest_error:
        return maps_manifest_error
    assert ligand_manifest_path is not None and maps_manifest_path is not None
    if (
        ligand_manifest_path.stat().st_size > MAX_MANIFEST_BYTES
        or maps_manifest_path.stat().st_size > MAX_MANIFEST_BYTES
    ):
        return _error(
            "HYDRATED_MANIFEST_TOO_LARGE",
            "水合协议 manifest 超过安全读取上限。",
            suggestion="请重新生成水合协议记录。",
            project_dir=str(root),
        )

    provenance_sources, provenance_error = _hydrated_provenance_sources(
        root,
        ligand_manifest,
        maps_manifest,
    )
    if provenance_error:
        return provenance_error
    assert provenance_sources is not None

    box_validation = validate_box_params(asdict(project.box))
    if not box_validation.get("ok"):
        return box_validation
    grid_coverage, grid_coverage_issue = (
        _hydrated_frozen_grid_coverage(
            maps_manifest,
            expected_box=box_validation.get("box") or asdict(project.box),
            expected_grid=maps_manifest.get("grid"),
        )
    )
    if grid_coverage_issue or grid_coverage is None:
        return _error(
            "HYDRATED_GRID_COVERAGE_INVALID",
            "水合 maps 未通过请求 Box/实际网格覆盖复核。",
            raw_error=(
                grid_coverage_issue
                or "缺少可复核的覆盖记录。"
            ),
            suggestion="请重新生成与当前 Box 一致的水合 maps。",
            project_dir=str(root),
        )
    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        return vina_validation

    settings = load_settings()
    detection = vina_adapter.detect(settings.tool_paths.vina)
    if detection.status != "ok" or not detection.path:
        return _error(
            "HYDRATED_VINA_NOT_AVAILABLE",
            detection.message or "未检测到可用的 AutoDock Vina。",
            raw_error=detection.raw_error,
            suggestion="请在设置页修复 Vina 路径。",
            project_dir=str(root),
        )
    detected_version = _version_tuple(detection.version)
    if detected_version is None or detected_version < (1, 2, 0):
        return _error(
            "HYDRATED_VINA_VERSION_UNSUPPORTED",
            "实验性水合 AD4 对接要求 AutoDock Vina 1.2.0 或更高版本。",
            raw_error=str(detection.version or "未识别"),
            suggestion="请配置 Vina 1.2.0 或更高版本。",
            project_dir=str(root),
        )
    capabilities = _mapping(detection.capabilities)
    features = _mapping(capabilities.get("features"))
    maps_feature = _mapping(features.get("maps"))
    if (
        maps_feature.get("status") != "supported"
        or maps_feature.get("supported") is not True
    ):
        return _error(
            "HYDRATED_VINA_MAPS_CAPABILITY_MISSING",
            "当前 Vina 未通过 --maps 能力门禁。",
            raw_error=json.dumps(maps_feature, ensure_ascii=False),
            suggestion="请使用能明确声明 --maps 的 Vina 1.2.x。",
            project_dir=str(root),
        )
    vina_binary = _tool_hash_snapshot(detection.path)
    if not SHA256_PATTERN.fullmatch(str(vina_binary.get("sha256") or "").lower()):
        return _error(
            "HYDRATED_VINA_BINARY_UNVERIFIED",
            "无法记录 Vina 可执行文件的 SHA256。",
            raw_error=detection.path,
            suggestion="请重新配置可读取的 Vina 可执行文件。",
            project_dir=str(root),
        )

    return {
        "ok": True,
        "protocol_id": HYDRATED_PROTOCOL_ID,
        "stability": PROTOCOL_STABILITY,
        "project_dir": str(root),
        "project": project.to_dict(),
        "_project_model": project,
        "hydrated_status": hydrated_status,
        "ligand_manifest": ligand_manifest,
        "ligand_manifest_file": ligand_manifest_file,
        "ligand_manifest_sha256": ligand_manifest_sha256,
        "ligand_manifest_path": ligand_manifest_path,
        "maps_manifest": maps_manifest,
        "maps_manifest_file": maps_manifest_file,
        "maps_manifest_sha256": maps_manifest_sha256,
        "maps_manifest_path": maps_manifest_path,
        "receptor_file": receptor_relative,
        "receptor_path": receptor_path,
        "receptor_sha256": receptor_sha256,
        "ligand_file": ligand_relative,
        "ligand_path": ligand_path,
        "ligand_sha256": ligand_sha256,
        "map_prefix": map_prefix,
        "prefix_name": prefix_name,
        "map_files": map_paths,
        "provenance_sources": provenance_sources,
        "vina_detection": detection,
        "vina_binary": vina_binary,
        "box": box_validation.get("box") or asdict(project.box),
        "grid_coverage": copy.deepcopy(grid_coverage),
        "grid_coverage_sha256": str(
            maps_manifest.get("grid_coverage_sha256") or ""
        ),
        "vina": vina_validation.get("vina") or asdict(project.vina),
        "warnings": [
            *(box_validation.get("warnings") or []),
            *(vina_validation.get("warnings") or []),
            "水合对接为实验性协议；raw AD4 affinity 仅用于本次 run 内构象排序。",
        ],
        "message": "水合对接运行前检查通过。",
        "error": None,
    }


def get_hydrated_run_preflight(project_dir: str) -> dict[str, Any]:
    result = _hydrated_prerequisites(project_dir)
    if not result.get("ok"):
        return result
    return {
        key: copy.deepcopy(value)
        for key, value in result.items()
        if not key.startswith("_")
        and key
        not in {
            "ligand_manifest_path",
            "maps_manifest_path",
            "receptor_path",
            "ligand_path",
            "map_files",
            "provenance_sources",
            "vina_detection",
        }
    } | {
        "map_files": [
            copy.deepcopy(record)
            for record, _path in result["map_files"]
        ],
        "next_run_id": get_next_run_id(project_dir),
    }


def prepare_hydrated_run(project_dir: str) -> dict[str, Any]:
    from dockstart_core.hydrated import get_status as get_hydrated_status

    prerequisites = _hydrated_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites
    root = Path(prerequisites["project_dir"])
    project = prerequisites["_project_model"]
    run_id = get_next_run_id(str(root))
    try:
        run_dir = _safe_run_directory(root, run_id, require_exists=False)
        run_dir.mkdir(parents=True, exist_ok=False)
        inputs_dir = run_dir / "inputs"
        maps_dir = inputs_dir / "maps"
        hydrated_dir = inputs_dir / "hydrated"
        maps_dir.mkdir(parents=True, exist_ok=False)
        hydrated_dir.mkdir(parents=True, exist_ok=False)

        receptor_relative = Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()
        ligand_relative = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
        ligand_manifest_relative = Path(
            "runs",
            run_id,
            "inputs",
            "hydrated",
            "ligand_manifest.json",
        ).as_posix()
        maps_manifest_relative = Path(
            "runs",
            run_id,
            "inputs",
            "maps",
            "manifest.json",
        ).as_posix()
        config_relative = Path("runs", run_id, "config_snapshot.txt").as_posix()
        command_preview_relative = Path("runs", run_id, "command_preview.txt").as_posix()
        output_relative = _run_output_file(run_id, "dock")
        log_relative = Path("runs", run_id, "log.txt").as_posix()

        _copy_verified(
            prerequisites["receptor_path"],
            root / receptor_relative,
            expected_sha256=prerequisites["receptor_sha256"],
        )
        _copy_verified(
            prerequisites["ligand_path"],
            root / ligand_relative,
            expected_sha256=prerequisites["ligand_sha256"],
        )
        _copy_verified(
            prerequisites["ligand_manifest_path"],
            root / ligand_manifest_relative,
            expected_sha256=prerequisites["ligand_manifest_sha256"],
        )
        _copy_verified(
            prerequisites["maps_manifest_path"],
            root / maps_manifest_relative,
            expected_sha256=prerequisites["maps_manifest_sha256"],
        )

        provenance_snapshots: dict[str, dict[str, Any]] = {}
        for key, specification in prerequisites["provenance_sources"].items():
            target_group = str(specification["target_group"])
            target_name = str(specification["target_name"])
            target_relative = Path(
                "runs",
                run_id,
                "inputs",
                target_group,
                target_name,
            ).as_posix()
            source_record = _mapping(specification.get("source_record"))
            _copy_verified(
                specification["source"],
                root / target_relative,
                expected_sha256=str(source_record.get("sha256") or ""),
            )
            provenance_snapshots[key] = {
                **_hash_snapshot(root / target_relative, target_relative),
                "source_relative_path": str(
                    source_record.get(
                        str(specification.get("path_key") or "path")
                    )
                    or ""
                ),
                "source_sha256": str(source_record.get("sha256") or ""),
                **(
                    {
                        "source_identity": str(
                            specification.get("source_identity") or ""
                        )
                    }
                    if specification.get("source_identity")
                    else {}
                ),
            }

        maps_file_snapshots: list[dict[str, Any]] = []
        for record, source in prerequisites["map_files"]:
            name = str(record["name"])
            target_relative = Path(
                "runs",
                run_id,
                "inputs",
                "maps",
                name,
            ).as_posix()
            _copy_verified(
                source,
                root / target_relative,
                expected_sha256=str(record.get("sha256") or ""),
            )
            maps_file_snapshots.append(
                {
                    "name": name,
                    "source_relative_path": str(record.get("relative_path") or ""),
                    **_hash_snapshot(root / target_relative, target_relative),
                }
            )

        maps_prefix = Path(
            "runs",
            run_id,
            "inputs",
            "maps",
            prerequisites["prefix_name"],
        ).as_posix()
        config_text = _build_run_snapshot_config(
            project,
            run_id,
            scoring_protocol="ad4_maps",
            grid_source="receptor",
            run_mode="dock",
            autobox=False,
        )
        atomic_write_text(root / config_relative, config_text)
        config_snapshot = _hash_snapshot(root / config_relative, config_relative)
        receptor_snapshot = _parse_pdbqt_stats(
            root / receptor_relative,
            receptor_relative,
        )
        receptor_snapshot["source_relative_path"] = prerequisites["receptor_file"]
        ligand_snapshot = _parse_pdbqt_stats(
            root / ligand_relative,
            ligand_relative,
            ligand=True,
        )
        ligand_snapshot["source_relative_path"] = prerequisites["ligand_file"]
        ligand_manifest_snapshot = _hash_snapshot(
            root / ligand_manifest_relative,
            ligand_manifest_relative,
        )
        maps_manifest_snapshot = _hash_snapshot(
            root / maps_manifest_relative,
            maps_manifest_relative,
        )
        frozen_maps_manifest = json.loads(
            (root / maps_manifest_relative).read_text(encoding="utf-8")
        )
        frozen_coverage, frozen_coverage_issue = (
            _hydrated_frozen_grid_coverage(
                frozen_maps_manifest,
                expected_box=asdict(project.box),
                expected_grid=prerequisites["maps_manifest"].get("grid"),
            )
        )
        if (
            frozen_coverage_issue
            or frozen_coverage is None
            or frozen_coverage != prerequisites["grid_coverage"]
            or str(
                frozen_maps_manifest.get("grid_coverage_sha256") or ""
            ).lower()
            != str(prerequisites["grid_coverage_sha256"] or "").lower()
        ):
            raise RuntimeError(
                frozen_coverage_issue
                or "冻结的水合 maps 覆盖证据与运行前复核结果不一致。"
            )

        status_after = get_hydrated_status(str(root))
        if (
            not status_after.get("preparation_ready")
            or not status_after.get("maps_ready")
            or str(status_after.get("manifest_sha256") or "").lower()
            != prerequisites["ligand_manifest_sha256"]
            or str(status_after.get("maps_manifest_sha256") or "").lower()
            != prerequisites["maps_manifest_sha256"]
        ):
            raise RuntimeError("冻结运行输入期间 active 水合配体或 maps 发生变化。")
        vina_binary_after = _tool_hash_snapshot(
            prerequisites["vina_detection"].path
        )
        if (
            str(vina_binary_after.get("sha256") or "").lower()
            != str(prerequisites["vina_binary"].get("sha256") or "").lower()
        ):
            raise RuntimeError("冻结运行输入期间 Vina 可执行文件发生变化。")

        command = _build_vina_command(
            prerequisites["vina_detection"].path,
            config_relative,
            run_id,
            "",
            scoring_protocol="ad4_maps",
            maps_prefix=maps_prefix,
            grid_source="receptor",
            maps_scoring="ad4",
            run_mode="dock",
            autobox=False,
        )
        command_preview = _format_command_preview(command)
        atomic_write_text(root / command_preview_relative, command_preview + "\n")
        command_preview_snapshot = _hash_snapshot(
            root / command_preview_relative,
            command_preview_relative,
        )
        created_at = _now_iso()
        maps_manifest = prerequisites["maps_manifest"]
        maps = _mapping(maps_manifest.get("maps"))
        metadata: dict[str, Any] = {
            "run_id": run_id,
            "status": "prepared",
            "stage": "prepared",
            "progress": {"percent": 0, "message": "水合对接运行记录已准备。"},
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "pid": None,
            "app_version": __version__,
            "app": {"name": "DockStart", "version": __version__},
            "protocol_id": HYDRATED_PROTOCOL_ID,
            "stability": PROTOCOL_STABILITY,
            "scientific_scope": {
                "rigid_receptor": True,
                "single_ligand": True,
                "global_docking_only": True,
                "ad4_maps_only": True,
                "virtual_screening_supported": False,
                "cross_ligand_score_comparison_supported": False,
            },
            "vina_version": prerequisites["vina_detection"].version,
            "vina_path": prerequisites["vina_detection"].path,
            "vina_source": prerequisites["vina_detection"].source,
            "vina_tool": {
                "version": prerequisites["vina_detection"].version,
                "path": prerequisites["vina_detection"].path,
                "source": prerequisites["vina_detection"].source,
                "sha256": prerequisites["vina_binary"]["sha256"],
                "size_bytes": prerequisites["vina_binary"]["size_bytes"],
                "capabilities": copy.deepcopy(
                    prerequisites["vina_detection"].capabilities
                ),
            },
            "vina_capabilities": copy.deepcopy(
                prerequisites["vina_detection"].capabilities
            ),
            "vina_sha256": prerequisites["vina_binary"]["sha256"],
            "system": _system_snapshot(),
            "command": command,
            "config_file": config_relative,
            "config_snapshot": config_relative,
            "snapshots": {
                "inputs": {
                    "receptor": receptor_snapshot,
                    "ligand": ligand_snapshot,
                },
                "config": {
                    **config_snapshot,
                    "source_relative_path": "",
                    "snapshot_file": config_relative,
                },
                "command_preview": {
                    **command_preview_snapshot,
                    "source_relative_path": "",
                    "snapshot_file": command_preview_relative,
                },
                "ad4_maps": {
                    "manifest": maps_manifest_snapshot,
                    "base_manifest": copy.deepcopy(
                        provenance_snapshots["base_maps_manifest"]
                    ),
                    **(
                        {
                            "base_maps_xyz": copy.deepcopy(
                                provenance_snapshots["base_maps_xyz"]
                            )
                        }
                        if "base_maps_xyz" in provenance_snapshots
                        else {}
                    ),
                    "gpf": copy.deepcopy(provenance_snapshots["gpf"]),
                    "autogrid_stdout": copy.deepcopy(
                        provenance_snapshots["autogrid_stdout"]
                    ),
                    "autogrid_stderr": copy.deepcopy(
                        provenance_snapshots["autogrid_stderr"]
                    ),
                    "autogrid_log": copy.deepcopy(
                        provenance_snapshots["autogrid_log"]
                    ),
                    "prefix": maps_prefix,
                    "files": maps_file_snapshots,
                    "source_manifest": prerequisites["maps_manifest_file"],
                    "grid": copy.deepcopy(maps_manifest.get("grid") or {}),
                    "grid_coverage": copy.deepcopy(frozen_coverage),
                    "grid_coverage_sha256": prerequisites[
                        "grid_coverage_sha256"
                    ],
                },
                "hydrated": {
                    "provenance_schema_version": (
                        HYDRATED_PROVENANCE_SCHEMA_VERSION
                    ),
                    "provenance_contract_id": (
                        HYDRATED_PROVENANCE_CONTRACT_ID
                    ),
                    "ligand_manifest": ligand_manifest_snapshot,
                    "raw_ligand": copy.deepcopy(
                        provenance_snapshots["raw_ligand"]
                    ),
                    "added_h_ligand": copy.deepcopy(
                        provenance_snapshots["added_h_ligand"]
                    ),
                    "preparation_script": copy.deepcopy(
                        provenance_snapshots["preparation_script"]
                    ),
                    "preparation_stdout": copy.deepcopy(
                        provenance_snapshots["preparation_stdout"]
                    ),
                    "preparation_stderr": copy.deepcopy(
                        provenance_snapshots["preparation_stderr"]
                    ),
                },
                "box": asdict(project.box),
                "vina": {
                    **asdict(project.vina),
                    "scoring": "ad4",
                },
            },
            "input_sha256": {
                "receptor": receptor_snapshot["sha256"],
                "ligand": ligand_snapshot["sha256"],
                "config": config_snapshot["sha256"],
                "command_preview": command_preview_snapshot["sha256"],
                "maps_manifest": maps_manifest_snapshot["sha256"],
                "base_maps_manifest": provenance_snapshots[
                    "base_maps_manifest"
                ]["sha256"],
                **(
                    {
                        "base_maps_xyz": provenance_snapshots[
                            "base_maps_xyz"
                        ]["sha256"]
                    }
                    if "base_maps_xyz" in provenance_snapshots
                    else {}
                ),
                "gpf": provenance_snapshots["gpf"]["sha256"],
                "maps": {
                    str(item.get("name") or ""): str(item.get("sha256") or "")
                    for item in maps_file_snapshots
                },
                "hydrated_ligand_manifest": ligand_manifest_snapshot["sha256"],
                "raw_ligand": provenance_snapshots["raw_ligand"]["sha256"],
                "added_h_ligand": provenance_snapshots[
                    "added_h_ligand"
                ]["sha256"],
                "preparation_script": provenance_snapshots[
                    "preparation_script"
                ]["sha256"],
            },
            "docking_protocol": {
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "mode": "rigid",
                "engine": "ad4_maps",
                "grid_source": "precomputed_maps",
                "run_mode": "dock",
                "autobox": False,
                "stability": PROTOCOL_STABILITY,
            },
            "scoring_protocol": "ad4_maps",
            "scoring_function": "ad4",
            "score_semantics": {
                "primary_score": "raw_hydrated_ad4_affinity_kcal_mol",
                "ranking": "within_run_pose_ranking_only",
                "postprocessed_affinity": False,
                "cross_protocol_comparison_supported": False,
                "cross_ligand_comparison_supported": False,
            },
            "grid_source": "precomputed_maps",
            "run_mode": "dock",
            "autobox": False,
            "ad4_maps": {
                "map_set_id": str(maps_manifest.get("map_set_id") or ""),
                "source_manifest": prerequisites["maps_manifest_file"],
                "manifest_snapshot": maps_manifest_relative,
                "prefix": maps_prefix,
                "files": maps_file_snapshots,
                "grid": copy.deepcopy(maps_manifest.get("grid") or {}),
                "grid_coverage": copy.deepcopy(frozen_coverage),
                "grid_coverage_sha256": prerequisites[
                    "grid_coverage_sha256"
                ],
                "ligand_atom_types": copy.deepcopy(
                    maps.get("ligand_atom_types") or []
                ),
            },
            "hydrated": {
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "provenance_schema_version": (
                    HYDRATED_PROVENANCE_SCHEMA_VERSION
                ),
                "provenance_contract_id": HYDRATED_PROVENANCE_CONTRACT_ID,
                "ligand_preparation_manifest": prerequisites[
                    "ligand_manifest_file"
                ],
                "ligand_preparation_manifest_snapshot": ligand_manifest_relative,
                "maps_manifest": prerequisites["maps_manifest_file"],
                "maps_manifest_snapshot": maps_manifest_relative,
                "water_map": copy.deepcopy(maps.get("water_map") or {}),
                "grid_coverage": copy.deepcopy(frozen_coverage),
                "grid_coverage_sha256": prerequisites[
                    "grid_coverage_sha256"
                ],
                "postprocess_contract": {
                    "method": "dockstart_hydrated_water_filter_v1",
                    "semantics": copy.deepcopy(
                        _HYDRATED_POSTPROCESS_SEMANTICS
                    ),
                    "parameters": {
                        "overlap_distance_angstrom": 2.03,
                        "overlap_rule": "strictly_less_than",
                        "receptor_excluded_atom_type": "HD",
                        "map_sample_radius_angstrom": 1.0,
                        "map_sample_rule": (
                            "minimum_in_clipped_index_cube"
                        ),
                        "strong_threshold": -0.5,
                        "weak_threshold": -0.3,
                        "threshold_rule": "strictly_less_than",
                    },
                },
                "raw_output_file": output_relative,
                "retained_output_file": Path(
                    "runs",
                    run_id,
                    HYDRATED_RETAINED_OUTPUT_NAME,
                ).as_posix(),
                "water_free_output_file": Path(
                    "runs",
                    run_id,
                    HYDRATED_WATER_FREE_OUTPUT_NAME,
                ).as_posix(),
                "waters_manifest_file": Path(
                    "runs",
                    run_id,
                    HYDRATED_WATERS_MANIFEST_NAME,
                ).as_posix(),
            },
            "grid_execution": {
                "source": "hydrated_ad4_precomputed_maps",
                "grid_only": True,
                "receptor_argument_used": False,
                "no_refine_equivalent": True,
            },
            "box_snapshot": asdict(project.box),
            "vina_snapshot": {
                **asdict(project.vina),
                "scoring": "ad4",
            },
            "output_file": output_relative,
            "pose_file": output_relative,
            "log_file": log_relative,
            "exit_code": None,
            "best_affinity": None,
            "primary_score_kcal_mol": None,
            "warnings": copy.deepcopy(prerequisites.get("warnings") or []),
        }
        _with_artifact_hashes(
            metadata,
            {
                "vina_binary_prepared": prerequisites["vina_binary"],
                "hydrated_ligand_manifest": ligand_manifest_snapshot,
                "hydrated_maps_manifest": maps_manifest_snapshot,
                "hydrated_raw_ligand": provenance_snapshots["raw_ligand"],
                "hydrated_added_h_ligand": provenance_snapshots[
                    "added_h_ligand"
                ],
                "hydrated_preparation_script": provenance_snapshots[
                    "preparation_script"
                ],
                "hydrated_preparation_stdout": provenance_snapshots[
                    "preparation_stdout"
                ],
                "hydrated_preparation_stderr": provenance_snapshots[
                    "preparation_stderr"
                ],
                "hydrated_base_maps_manifest": provenance_snapshots[
                    "base_maps_manifest"
                ],
                **(
                    {
                        "hydrated_base_maps_xyz": provenance_snapshots[
                            "base_maps_xyz"
                        ]
                    }
                    if "base_maps_xyz" in provenance_snapshots
                    else {}
                ),
                "hydrated_gpf": provenance_snapshots["gpf"],
                "hydrated_autogrid_stdout": provenance_snapshots[
                    "autogrid_stdout"
                ],
                "hydrated_autogrid_stderr": provenance_snapshots[
                    "autogrid_stderr"
                ],
                "hydrated_autogrid_log": provenance_snapshots[
                    "autogrid_log"
                ],
                "hydrated_command_preview": command_preview_snapshot,
            },
        )
        _write_run_metadata(str(root), run_id, metadata)

        project.runs.append(
            {
                "run_id": run_id,
                "status": "prepared",
                "stage": "prepared",
                "metadata_file": _metadata_relative_path(run_id),
                "created_at": created_at,
                "scoring_protocol": "ad4_maps",
                "scoring_function": "ad4",
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "stability": PROTOCOL_STABILITY,
                "grid_source": "precomputed_maps",
                "run_mode": "dock",
                "autobox": False,
                "output_file": output_relative,
                "pose_file": output_relative,
            }
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved | {
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "run_id": run_id,
                "message": "run 已冻结，但 project.json 摘要同步失败，禁止执行。",
            }
        return {
            "ok": True,
            "protocol_id": HYDRATED_PROTOCOL_ID,
            "stability": PROTOCOL_STABILITY,
            "project_dir": str(root),
            "project": saved.get("project"),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": _metadata_relative_path(run_id),
            "command": command,
            "command_preview": command_preview,
            "command_preview_file": command_preview_relative,
            "config_snapshot_file": config_relative,
            "warnings": copy.deepcopy(prerequisites.get("warnings") or []),
            "message": "实验性水合 AD4 run 已冻结，可以启动运行。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - preserve partial audit directory.
        try:
            if "run_dir" in locals() and run_dir.is_dir():
                atomic_write_json(
                    run_dir / "prepare_error.json",
                    {
                        "protocol_id": HYDRATED_PROTOCOL_ID,
                        "run_id": run_id,
                        "status": "failed",
                        "stage": "prepare_failed",
                        "error": str(exc),
                    },
                )
        except Exception:
            pass
        return _error(
            "HYDRATED_RUN_PREPARE_FAILED",
            "准备实验性水合 AD4 run 时发生错误。",
            raw_error=str(exc),
            suggestion="请保留失败记录，重新核对水合配体、maps、Vina 和项目写入权限。",
            project_dir=str(root),
            run_id=run_id,
        )


def _load_waters_manifest(
    root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    postprocess = _mapping(metadata.get("hydrated_postprocess"))
    relative = str(postprocess.get("manifest_file") or "")
    expected_hash = str(postprocess.get("manifest_sha256") or "").lower()
    expected_relative = Path(
        "runs",
        run_id,
        HYDRATED_WATERS_MANIFEST_NAME,
    ).as_posix()
    if (
        postprocess.get("status") != "finished"
        or relative != expected_relative
        or not SHA256_PATTERN.fullmatch(expected_hash)
    ):
        return None, _error(
            "HYDRATED_POSTPROCESS_NOT_READY",
            "水合结果后处理记录尚未完成或不可信。",
            suggestion="请确认 run 已成功完成水分子后处理。",
            project_dir=str(root),
            run_id=run_id,
        )
    path, path_error = _project_file(
        root,
        relative,
        label="waters_manifest",
        expected_sha256=expected_hash,
    )
    if path_error:
        return None, path_error
    assert path is not None
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_TOO_LARGE",
            "水分子分类记录超过安全读取上限。",
            project_dir=str(root),
            run_id=run_id,
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_INVALID",
            "无法解析水分子分类记录。",
            raw_error=str(exc),
            project_dir=str(root),
            run_id=run_id,
        )
    if (
        not isinstance(manifest, dict)
        or manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or manifest.get("run_id") != run_id
    ):
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_BINDING_MISMATCH",
            "水分子分类记录未绑定当前水合 run。",
            project_dir=str(root),
            run_id=run_id,
        )
    semantics = _mapping(manifest.get("semantics"))
    if (
        manifest.get("method") != "dockstart_hydrated_water_filter_v1"
        or semantics.get("affinity")
        != "original_ad4_hydrated_affinity_unchanged"
    ):
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_SEMANTICS_INVALID",
            "水分子分类记录的方法或评分语义无效。",
            raw_error=json.dumps(
                {
                    "method": manifest.get("method"),
                    "semantics": semantics,
                },
                ensure_ascii=False,
            ),
            project_dir=str(root),
            run_id=run_id,
        )

    raw_relative = Path("runs", run_id, "out.pdbqt").as_posix()
    receptor_relative = Path(
        "runs",
        run_id,
        "inputs",
        "receptor.pdbqt",
    ).as_posix()
    maps_record = _mapping(metadata.get("ad4_maps"))
    prefix_name = Path(str(maps_record.get("prefix") or "")).name
    water_map_relative = (
        Path(
            "runs",
            run_id,
            "inputs",
            "maps",
            f"{prefix_name}.W.map",
        ).as_posix()
        if prefix_name
        else ""
    )
    retained_relative = Path(
        "runs",
        run_id,
        HYDRATED_RETAINED_OUTPUT_NAME,
    ).as_posix()
    water_free_relative = Path(
        "runs",
        run_id,
        HYDRATED_WATER_FREE_OUTPUT_NAME,
    ).as_posix()
    sources = _mapping(manifest.get("sources"))
    outputs = _mapping(manifest.get("outputs"))
    fixed_records = (
        ("hydrated_output", _mapping(sources.get("hydrated_output")), raw_relative),
        ("receptor", _mapping(sources.get("receptor")), receptor_relative),
        ("water_map", _mapping(sources.get("water_map")), water_map_relative),
        (
            "retained_water_annotated",
            _mapping(outputs.get("retained_water_annotated")),
            retained_relative,
        ),
        (
            "water_free_ligand",
            _mapping(outputs.get("water_free_ligand")),
            water_free_relative,
        ),
    )
    observed_hashes: dict[str, str] = {}
    for label, record, expected_relative in fixed_records:
        recorded_relative = str(record.get("path") or "")
        recorded_hash = str(record.get("sha256") or "").lower()
        recorded_size = record.get("size_bytes")
        if (
            not expected_relative
            or recorded_relative != expected_relative
            or not SHA256_PATTERN.fullmatch(recorded_hash)
            or isinstance(recorded_size, bool)
            or not isinstance(recorded_size, int)
            or recorded_size <= 0
        ):
            return None, _error(
                "HYDRATED_WATERS_MANIFEST_FILE_RECORD_INVALID",
                f"水分子分类记录中的 {label} 文件绑定无效。",
                raw_error=json.dumps(record, ensure_ascii=False),
                project_dir=str(root),
                run_id=run_id,
            )
        bound_path, bound_error = _project_file(
            root,
            expected_relative,
            label=label,
            expected_sha256=recorded_hash,
        )
        if bound_error:
            return None, bound_error
        assert bound_path is not None
        if bound_path.stat().st_size != recorded_size:
            return None, _error(
                "HYDRATED_WATERS_MANIFEST_FILE_SIZE_MISMATCH",
                f"{label} 的文件大小与水分子分类记录不一致。",
                raw_error=(
                    f"recorded={recorded_size}; "
                    f"actual={bound_path.stat().st_size}; path={bound_path}"
                ),
                project_dir=str(root),
                run_id=run_id,
            )
        observed_hashes[label] = recorded_hash

    artifacts = _mapping(metadata.get("artifacts"))
    artifact_bindings = (
        ("out", raw_relative, observed_hashes["hydrated_output"]),
        (
            "hydrated_retained",
            retained_relative,
            observed_hashes["retained_water_annotated"],
        ),
        (
            "hydrated_water_free",
            water_free_relative,
            observed_hashes["water_free_ligand"],
        ),
        ("hydrated_waters_manifest", relative, expected_hash),
    )
    for key, expected_relative, expected_artifact_hash in artifact_bindings:
        artifact = _mapping(artifacts.get(key))
        if (
            str(artifact.get("relative_path") or "") != expected_relative
            or str(artifact.get("sha256") or "").lower()
            != expected_artifact_hash
        ):
            return None, _error(
                "HYDRATED_RESULT_ARTIFACT_BINDING_MISMATCH",
                f"metadata 中的 {key} artifact 未绑定水合结果文件。",
                raw_error=json.dumps(artifact, ensure_ascii=False),
                project_dir=str(root),
                run_id=run_id,
            )

    if (
        str(metadata.get("output_file") or "") != raw_relative
        or str(metadata.get("pose_file") or "") != retained_relative
        or str(postprocess.get("raw_output_file") or "") != raw_relative
        or str(postprocess.get("retained_output_file") or "")
        != retained_relative
        or str(postprocess.get("water_free_output_file") or "")
        != water_free_relative
    ):
        return None, _error(
            "HYDRATED_RESULT_PATH_BINDING_MISMATCH",
            "水合结果路径未绑定固定的 raw、保留水和去水输出。",
            project_dir=str(root),
            run_id=run_id,
        )

    poses = _list_of_mappings(manifest.get("poses"))
    modes = [int(item.get("mode") or 0) for item in poses]
    if (
        not poses
        or any(mode <= 0 for mode in modes)
        or len(set(modes)) != len(modes)
        or modes != list(range(1, len(modes) + 1))
    ):
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_POSES_INVALID",
            "水分子分类记录中的构象编号不连续或重复。",
            raw_error=json.dumps(modes, ensure_ascii=False),
            project_dir=str(root),
            run_id=run_id,
        )
    count_keys = (
        "candidate_water_count",
        "retained_water_count",
        "strong_water_count",
        "weak_water_count",
        "displaced_water_count",
    )
    invalid_pose_counts = [
        item
        for item in poses
        if any(
            isinstance(item.get(key), bool)
            or not isinstance(item.get(key), int)
            or int(item.get(key)) < 0
            for key in count_keys
        )
        or int(item.get("retained_water_count"))
        != int(item.get("strong_water_count"))
        + int(item.get("weak_water_count"))
        or int(item.get("displaced_water_count"))
        != int(item.get("candidate_water_count"))
        - int(item.get("retained_water_count"))
        or len(_list_of_mappings(item.get("waters")))
        != int(item.get("candidate_water_count"))
    ]
    if invalid_pose_counts:
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_COUNTS_INVALID",
            "水分子分类记录中的逐构象计数无效。",
            raw_error=json.dumps(invalid_pose_counts, ensure_ascii=False),
            project_dir=str(root),
            run_id=run_id,
        )
    calculated_summary = {
        "pose_count": len(poses),
        "raw_water_count": sum(
            int(item.get("candidate_water_count") or 0)
            for item in poses
        ),
        "candidate_water_count": sum(
            int(item.get("candidate_water_count") or 0)
            for item in poses
        ),
        "retained_water_count": sum(
            int(item.get("retained_water_count") or 0)
            for item in poses
        ),
        "strong_water_count": sum(
            int(item.get("strong_water_count") or 0)
            for item in poses
        ),
        "weak_water_count": sum(
            int(item.get("weak_water_count") or 0)
            for item in poses
        ),
        "displaced_water_count": sum(
            int(item.get("displaced_water_count") or 0)
            for item in poses
        ),
    }
    if _mapping(manifest.get("summary")) != calculated_summary:
        return None, _error(
            "HYDRATED_WATERS_MANIFEST_SUMMARY_MISMATCH",
            "水分子分类汇总与逐构象记录不一致。",
            raw_error=json.dumps(
                {
                    "recorded": manifest.get("summary"),
                    "calculated": calculated_summary,
                },
                ensure_ascii=False,
            ),
            project_dir=str(root),
            run_id=run_id,
        )
    return manifest, None


def _hydrated_provenance_error(
    root: Path,
    run_id: str,
    code: str,
    message: str,
    *,
    raw_error: str = "",
) -> dict[str, Any]:
    return _error(
        f"HYDRATED_PROVENANCE_{code}",
        message,
        raw_error=raw_error,
        suggestion=(
            "请保留该 run 作为审计记录；不要手工修补证据，"
            "请从已验证的水合输入重新准备并运行。"
        ),
        project_dir=str(root),
        run_id=run_id,
    )


def _modern_hydrated_provenance_marker(
    metadata: Mapping[str, Any],
) -> tuple[bool, str]:
    snapshots = _mapping(metadata.get("snapshots"))
    snapshot_hydrated = _mapping(snapshots.get("hydrated"))
    hydrated = _mapping(metadata.get("hydrated"))
    values = (
        snapshot_hydrated.get("provenance_schema_version"),
        hydrated.get("provenance_schema_version"),
    )
    contracts = (
        str(snapshot_hydrated.get("provenance_contract_id") or ""),
        str(hydrated.get("provenance_contract_id") or ""),
    )
    if values == (None, None) and contracts == ("", ""):
        return False, ""
    if (
        values
        != (
            HYDRATED_PROVENANCE_SCHEMA_VERSION,
            HYDRATED_PROVENANCE_SCHEMA_VERSION,
        )
        or contracts
        != (
            HYDRATED_PROVENANCE_CONTRACT_ID,
            HYDRATED_PROVENANCE_CONTRACT_ID,
        )
    ):
        return False, (
            "metadata 中的水合冻结溯源 schema/contract 双重记录不一致。"
        )
    return True, ""


def _verify_frozen_run_record(
    root: Path,
    run_id: str,
    record: Mapping[str, Any],
    *,
    expected_relative: str,
    label: str,
    allow_empty: bool = False,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, Any] | None]:
    relative = str(record.get("relative_path") or "")
    expected_hash = str(record.get("sha256") or "").lower()
    expected_size = _snapshot_size(record)
    expected_path = root / expected_relative
    if (
        relative != expected_relative
        or Path(relative).is_absolute()
        or Path(relative).drive
        or not SHA256_PATTERN.fullmatch(expected_hash)
        or expected_size < 0
        or (not allow_empty and expected_size <= 0)
    ):
        return None, None, _hydrated_provenance_error(
            root,
            run_id,
            "RECORD_INVALID",
            f"{label} 的 run 冻结记录格式无效。",
            raw_error=json.dumps(dict(record), ensure_ascii=False),
        )
    try:
        if expected_path.is_symlink():
            raise ValueError("路径是符号链接")
        resolved = expected_path.resolve(strict=True)
        run_root = (root / "runs" / run_id).resolve(strict=True)
        resolved.relative_to(run_root)
    except (OSError, ValueError) as exc:
        return None, None, _hydrated_provenance_error(
            root,
            run_id,
            "PATH_INVALID",
            f"{label} 不在当前 run 的普通文件边界内。",
            raw_error=f"{expected_path}: {exc}",
        )
    if (
        resolved != expected_path.absolute()
        or not resolved.is_file()
        or resolved.stat().st_size != expected_size
    ):
        return None, None, _hydrated_provenance_error(
            root,
            run_id,
            "SIZE_MISMATCH",
            f"{label} 的类型或大小与冻结记录不一致。",
            raw_error=(
                f"recorded={expected_size}; actual={resolved.stat().st_size}; "
                f"path={resolved}"
            ),
        )
    actual_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        return None, None, _hydrated_provenance_error(
            root,
            run_id,
            "HASH_MISMATCH",
            f"{label} 已在 run 冻结后发生变化。",
            raw_error=(
                f"expected={expected_hash}; actual={actual_hash}; path={resolved}"
            ),
        )
    return {
        "relative_path": expected_relative,
        "size_bytes": expected_size,
        "sha256": actual_hash,
    }, resolved, None


def _json_from_frozen_file(
    root: Path,
    run_id: str,
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("文件超过 manifest 安全读取上限")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "JSON_INVALID",
            f"{label} 无法作为 JSON 对象读取。",
            raw_error=str(exc),
        )
    if not isinstance(payload, dict):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "JSON_INVALID",
            f"{label} 的顶层不是 JSON 对象。",
        )
    return payload, None


def _tool_identity(record: Mapping[str, Any]) -> dict[str, Any] | None:
    path = str(record.get("path") or record.get("absolute_path") or "")
    sha256 = str(record.get("sha256") or "").lower()
    size = _snapshot_size(record)
    if (
        not path
        or not SHA256_PATTERN.fullmatch(sha256)
        or size <= 0
    ):
        return None
    return {
        "path": path,
        "size_bytes": size,
        "sha256": sha256,
    }


def _same_tool_identity(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_identity = _tool_identity(left)
    right_identity = _tool_identity(right)
    return (
        left_identity is not None
        and right_identity is not None
        and left_identity == right_identity
    )


def _private_tool_path(path_value: Any) -> str:
    path = Path(str(path_value or ""))
    return (
        f"<本机路径已省略>/{path.name}"
        if str(path_value or "")
        else "未记录"
    )


def _parse_simple_config(text: str) -> tuple[dict[str, str] | None, str]:
    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            return None, f"第 {line_number} 行缺少 '='。"
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or not value or key in parsed:
            return None, f"第 {line_number} 行包含空值或重复键。"
        parsed[key] = value
    return parsed, ""


def _numbers_match(left: Any, right: Any, *, tolerance: float = 1e-9) -> bool:
    if (
        isinstance(left, bool)
        or isinstance(right, bool)
        or not isinstance(left, (int, float))
        or not isinstance(right, (int, float))
    ):
        return False
    return (
        math.isfinite(float(left))
        and math.isfinite(float(right))
        and math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    )


def _expected_postprocess_parameters(spacing: float) -> dict[str, Any]:
    return {
        "overlap_distance_angstrom": 2.03,
        "overlap_rule": "strictly_less_than",
        "receptor_excluded_atom_type": "HD",
        "map_sample_radius_angstrom": 1.0,
        "map_sample_radius_steps": round(1.0 / spacing),
        "map_sample_rule": "minimum_in_clipped_index_cube",
        "strong_threshold": -0.5,
        "weak_threshold": -0.3,
        "threshold_rule": "strictly_less_than",
    }


def _expected_water_map_parameters() -> dict[str, Any]:
    return {
        "mode": "BEST",
        "weight": hydrated_map_core.BEST_WEIGHT,
        "entropy": hydrated_map_core.DISPLACEMENT_ENTROPY,
        "oa_weight": hydrated_map_core.OA_WEIGHT,
        "hd_weight": hydrated_map_core.HD_WEIGHT,
        "output_decimals": hydrated_map_core.OUTPUT_DECIMALS,
        "positive_value_rule": "entropy_if_oa_gt_0_or_hd_gt_0",
    }


def _recomputed_best_statistics(
    oa_values: tuple[float, ...],
    hd_values: tuple[float, ...],
) -> tuple[list[float], dict[str, Any]]:
    values: list[float] = []
    selected_oa = 0
    selected_hd = 0
    entropy_points = 0
    for oa_value, hd_value in zip(oa_values, hd_values, strict=True):
        weighted_oa = oa_value * hydrated_map_core.OA_WEIGHT
        weighted_hd = hd_value * hydrated_map_core.HD_WEIGHT
        if weighted_oa > 0.0 or weighted_hd > 0.0:
            value = hydrated_map_core.DISPLACEMENT_ENTROPY
            entropy_points += 1
        elif weighted_oa <= weighted_hd:
            value = weighted_oa * hydrated_map_core.BEST_WEIGHT
            selected_oa += 1
        else:
            value = weighted_hd * hydrated_map_core.BEST_WEIGHT
            selected_hd += 1
        published = float(
            f"{value:.{hydrated_map_core.OUTPUT_DECIMALS}f}"
        )
        values.append(0.0 if published == 0.0 else published)
    count = len(values)
    return values, {
        "point_count": count,
        "minimum": min(values),
        "maximum": max(values),
        "selected_oa_points": selected_oa,
        "selected_hd_points": selected_hd,
        "entropy_points": entropy_points,
        "selected_oa_percent": round(selected_oa * 100.0 / count, 6),
        "selected_hd_percent": round(selected_hd * 100.0 / count, 6),
        "entropy_percent": round(entropy_points * 100.0 / count, 6),
    }


def _verify_metadata_artifact_binding(
    root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
    *,
    artifact_key: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    artifacts = _mapping(metadata.get("artifacts"))
    artifact_hashes = _mapping(metadata.get("artifact_sha256"))
    artifact = _mapping(artifacts.get(artifact_key))
    expected = {
        "relative_path": str(snapshot.get("relative_path") or ""),
        "size_bytes": _snapshot_size(snapshot),
        "sha256": str(snapshot.get("sha256") or "").lower(),
    }
    observed = {
        "relative_path": str(artifact.get("relative_path") or ""),
        "size_bytes": _snapshot_size(artifact),
        "sha256": str(artifact.get("sha256") or "").lower(),
    }
    if (
        observed != expected
        or str(artifact_hashes.get(artifact_key) or "").lower()
        != expected["sha256"]
    ):
        return _hydrated_provenance_error(
            root,
            run_id,
            "ARTIFACT_BINDING_MISMATCH",
            f"{artifact_key} 的 artifact、大小或 artifact_sha256 绑定不一致。",
            raw_error=json.dumps(
                {"expected": expected, "observed": observed},
                ensure_ascii=False,
            ),
        )
    return None


def _validate_legacy_hydrated_provenance(
    root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Verify recorded legacy snapshots without manufacturing absent evidence."""

    snapshots = _mapping(metadata.get("snapshots"))
    inputs = _mapping(snapshots.get("inputs"))
    maps = _mapping(snapshots.get("ad4_maps"))
    hydrated = _mapping(snapshots.get("hydrated"))
    required = {
        "receptor": (
            _mapping(inputs.get("receptor")),
            Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix(),
        ),
        "hydrated_ligand": (
            _mapping(inputs.get("ligand")),
            Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix(),
        ),
        "config": (
            _mapping(snapshots.get("config")),
            Path("runs", run_id, "config_snapshot.txt").as_posix(),
        ),
        "ligand_manifest": (
            _mapping(hydrated.get("ligand_manifest")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "ligand_manifest.json",
            ).as_posix(),
        ),
        "maps_manifest": (
            _mapping(maps.get("manifest")),
            Path("runs", run_id, "inputs", "maps", "manifest.json").as_posix(),
        ),
    }
    verified: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for key, (record, expected_relative) in required.items():
        snapshot, path, snapshot_error = _verify_frozen_run_record(
            root,
            run_id,
            record,
            expected_relative=expected_relative,
            label=f"旧版 {key}",
        )
        if snapshot_error:
            return None, snapshot_error
        assert snapshot is not None and path is not None
        verified[key] = snapshot
        paths[key] = path

    map_records = _list_of_mappings(maps.get("files"))
    if not map_records:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "LEGACY_MAPS_MISSING",
            "旧版水合 run 没有可复核的冻结 map 文件清单。",
        )
    for item in map_records:
        name = str(item.get("name") or "")
        if not name or Path(name).name != name:
            return None, _hydrated_provenance_error(
                root,
                run_id,
                "LEGACY_MAP_RECORD_INVALID",
                "旧版水合 run 包含无效的 map 文件名。",
            )
        snapshot, path, snapshot_error = _verify_frozen_run_record(
            root,
            run_id,
            item,
            expected_relative=Path(
                "runs", run_id, "inputs", "maps", name
            ).as_posix(),
            label=f"旧版 map {name}",
        )
        if snapshot_error:
            return None, snapshot_error
        assert snapshot is not None and path is not None
        verified[f"map:{name}"] = snapshot
        paths[f"map:{name}"] = path

    maps_manifest, manifest_error = _json_from_frozen_file(
        root,
        run_id,
        paths["maps_manifest"],
        label="旧版水合 maps manifest",
    )
    if manifest_error:
        return None, manifest_error
    assert maps_manifest is not None
    frozen_maps = _mapping(maps_manifest.get("maps"))
    frozen_files = {
        str(item.get("name") or ""): item
        for item in _list_of_mappings(frozen_maps.get("files"))
    }
    observed_hashes = {
        key.split(":", 1)[1]: value["sha256"]
        for key, value in verified.items()
        if key.startswith("map:")
    }
    if (
        maps_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or {
            name: str(record.get("sha256") or "").lower()
            for name, record in frozen_files.items()
        }
        != observed_hashes
        or not any(name.endswith(".W.map") for name in observed_hashes)
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "LEGACY_MANIFEST_BINDING_MISMATCH",
            "旧版水合 maps manifest 未与冻结 receptor、ligand 或 maps 保持一致。",
        )

    grid_coverage, coverage_issue = _hydrated_frozen_grid_coverage(
        maps_manifest,
        expected_box=metadata.get("box_snapshot"),
        expected_grid=_mapping(metadata.get("ad4_maps")).get("grid"),
    )
    missing = [
        "完整 raw/加氢配体 run 快照",
        "Python/RDKit/Meeko run 级工具链绑定",
        "基础 maps manifest、GPF 与 AutoGrid 审计文件 run 快照",
        "命令预览 run 快照",
        "冻结后处理参数合同",
    ]
    if coverage_issue or grid_coverage is None:
        missing.append("请求 Box/实际网格覆盖合同")
        grid_coverage = {}
    return {
        "status": "legacy_partial",
        "schema_version": 0,
        "contract_id": "",
        "read_only": True,
        "evidence_source": "recorded_run_snapshots_only",
        "verified_files": verified,
        "missing_evidence": missing,
        "grid_coverage": copy.deepcopy(grid_coverage),
        "warning": (
            "该历史 run 创建于完整冻结溯源合同之前；仅展示已记录并通过"
            "哈希复核的证据，缺失项不会由当前项目状态补写。"
        ),
    }, None


def _record_hash_and_size(record: Mapping[str, Any]) -> tuple[str, int]:
    return (
        str(record.get("sha256") or "").lower(),
        _snapshot_size(record),
    )


def _records_share_bytes(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_hash, left_size = _record_hash_and_size(left)
    right_hash, right_size = _record_hash_and_size(right)
    return (
        SHA256_PATTERN.fullmatch(left_hash) is not None
        and left_hash == right_hash
        and left_size >= 0
        and left_size == right_size
    )


def _parse_gpf_contract(text: str) -> tuple[dict[str, Any] | None, str]:
    single: dict[str, list[str]] = {}
    maps: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        key = fields[0].lower()
        values = fields[1:]
        if not values:
            return None, f"GPF 第 {line_number} 行缺少参数值。"
        if key == "map":
            maps.append(values[0])
            continue
        if key in single:
            return None, f"GPF 包含重复的 {key} 参数。"
        single[key] = values
    try:
        npts = tuple(int(item) for item in single["npts"])
        center = tuple(float(item) for item in single["gridcenter"])
        spacing = float(single["spacing"][0])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"GPF 网格参数无法解析：{exc}"
    if (
        len(npts) != 3
        or len(center) != 3
        or any(item <= 0 for item in npts)
        or not math.isfinite(spacing)
        or spacing <= 0.0
        or any(not math.isfinite(item) for item in center)
    ):
        return None, "GPF 的 npts、gridcenter 或 spacing 无效。"
    return {
        "npts": npts,
        "center": center,
        "spacing": spacing,
        "receptor": (single.get("receptor") or [""])[0],
        "ligand_types": single.get("ligand_types") or [],
        "maps": maps,
        "elecmap": (single.get("elecmap") or [""])[0],
        "dsolvmap": (single.get("dsolvmap") or [""])[0],
    }, ""


def _validated_toolchain(
    root: Path,
    run_id: str,
    preparation_manifest: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tools = _mapping(preparation_manifest.get("tools"))
    detected = _mapping(tools.get("detected"))
    probe = _mapping(tools.get("probe"))
    before = _mapping(tools.get("before"))
    integrity = _mapping(preparation_manifest.get("integrity"))
    after = _mapping(integrity.get("tools_after"))
    result = _mapping(_mapping(preparation_manifest.get("execution")).get("result"))

    identities: dict[str, dict[str, Any]] = {}
    versions: dict[str, str] = {}
    for key in ("python", "rdkit", "meeko"):
        before_record = _mapping(before.get(key))
        after_record = _mapping(after.get(key))
        if (
            not _same_tool_identity(before_record, after_record)
            or str(_mapping(detected.get(key)).get("status") or "") != "ok"
        ):
            return None, _hydrated_provenance_error(
                root,
                run_id,
                "PREPARATION_TOOL_BINDING_MISMATCH",
                f"水合配体准备记录中的 {key} 工具身份前后不一致。",
            )
        identity = _tool_identity(before_record)
        assert identity is not None
        version = (
            str(_mapping(detected.get(key)).get("version") or "")
            if key == "python"
            else str(probe.get(f"{key}_version") or "")
        )
        if not version:
            return None, _hydrated_provenance_error(
                root,
                run_id,
                "PREPARATION_TOOL_VERSION_MISSING",
                f"水合配体准备记录没有冻结 {key} 版本。",
            )
        identities[key] = {
            **identity,
            "version": version,
            "source": str(_mapping(detected.get(key)).get("source") or ""),
        }
        versions[key] = version

    if (
        integrity.get("source_unchanged") is not True
        or integrity.get("tools_unchanged") is not True
        or integrity.get("script_unchanged") is not True
        or probe.get("ok") is not True
        or probe.get("hydrate_supported") is not True
        or result.get("ok") is not True
        or str(tools.get("rdkit_version") or "") != versions["rdkit"]
        or str(tools.get("meeko_version") or "") != versions["meeko"]
        or str(result.get("rdkit_version") or "") != versions["rdkit"]
        or str(result.get("meeko_version") or "") != versions["meeko"]
        or str(probe.get("rdkit_module_file") or "")
        != identities["rdkit"]["path"]
        or str(probe.get("meeko_module_file") or "")
        != identities["meeko"]["path"]
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "PREPARATION_TOOL_SEMANTICS_MISMATCH",
            "Python、RDKit 或 Meeko 的版本、能力探针与执行结果未形成一致证据链。",
        )
    return identities, None


def _validate_modern_hydrated_provenance(
    root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
    waters_manifest: Mapping[str, Any],
    frozen_input_integrity: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate the complete run-local hydrated provenance contract."""

    snapshots = _mapping(metadata.get("snapshots"))
    inputs = _mapping(snapshots.get("inputs"))
    maps_snapshots = _mapping(snapshots.get("ad4_maps"))
    hydrated_snapshots = _mapping(snapshots.get("hydrated"))
    raw_record = _mapping(hydrated_snapshots.get("raw_ligand"))
    raw_name = Path(str(raw_record.get("relative_path") or "")).name
    if raw_name not in {"raw_ligand.sdf", "raw_ligand.mol"}:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "RAW_LIGAND_PATH_INVALID",
            "原始配体快照没有绑定固定的 run 内 SDF/MOL 路径。",
        )

    fixed_specs: dict[
        str,
        tuple[dict[str, Any], str, str, bool, str | None],
    ] = {
        "receptor": (
            _mapping(inputs.get("receptor")),
            Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix(),
            "刚性受体",
            False,
            None,
        ),
        "hydrated_ligand": (
            _mapping(inputs.get("ligand")),
            Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix(),
            "水合 PDBQT 配体",
            False,
            None,
        ),
        "config": (
            _mapping(snapshots.get("config")),
            Path("runs", run_id, "config_snapshot.txt").as_posix(),
            "Vina 配置",
            False,
            None,
        ),
        "command_preview": (
            _mapping(snapshots.get("command_preview")),
            Path("runs", run_id, "command_preview.txt").as_posix(),
            "Vina 命令预览",
            False,
            "hydrated_command_preview",
        ),
        "ligand_manifest": (
            _mapping(hydrated_snapshots.get("ligand_manifest")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "ligand_manifest.json",
            ).as_posix(),
            "水合配体 preparation manifest",
            False,
            "hydrated_ligand_manifest",
        ),
        "raw_ligand": (
            raw_record,
            Path("runs", run_id, "inputs", "hydrated", raw_name).as_posix(),
            "原始配体",
            False,
            "hydrated_raw_ligand",
        ),
        "added_h_ligand": (
            _mapping(hydrated_snapshots.get("added_h_ligand")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "ligand_added_h.sdf",
            ).as_posix(),
            "加氢配体",
            False,
            "hydrated_added_h_ligand",
        ),
        "preparation_script": (
            _mapping(hydrated_snapshots.get("preparation_script")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "prepare_hydrated_ligand.py",
            ).as_posix(),
            "水合配体准备脚本",
            False,
            "hydrated_preparation_script",
        ),
        "preparation_stdout": (
            _mapping(hydrated_snapshots.get("preparation_stdout")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "preparation_stdout.txt",
            ).as_posix(),
            "水合配体准备 stdout",
            True,
            "hydrated_preparation_stdout",
        ),
        "preparation_stderr": (
            _mapping(hydrated_snapshots.get("preparation_stderr")),
            Path(
                "runs",
                run_id,
                "inputs",
                "hydrated",
                "preparation_stderr.txt",
            ).as_posix(),
            "水合配体准备 stderr",
            True,
            "hydrated_preparation_stderr",
        ),
        "maps_manifest": (
            _mapping(maps_snapshots.get("manifest")),
            Path("runs", run_id, "inputs", "maps", "manifest.json").as_posix(),
            "水合 maps manifest",
            False,
            "hydrated_maps_manifest",
        ),
        "base_maps_manifest": (
            _mapping(maps_snapshots.get("base_manifest")),
            Path(
                "runs", run_id, "inputs", "maps", "base_manifest.json"
            ).as_posix(),
            "基础 maps manifest",
            False,
            "hydrated_base_maps_manifest",
        ),
        "gpf": (
            _mapping(maps_snapshots.get("gpf")),
            Path("runs", run_id, "inputs", "maps", "receptor.gpf").as_posix(),
            "AutoGrid GPF",
            False,
            "hydrated_gpf",
        ),
        "autogrid_stdout": (
            _mapping(maps_snapshots.get("autogrid_stdout")),
            Path(
                "runs", run_id, "inputs", "maps", "autogrid_stdout.txt"
            ).as_posix(),
            "AutoGrid stdout",
            True,
            "hydrated_autogrid_stdout",
        ),
        "autogrid_stderr": (
            _mapping(maps_snapshots.get("autogrid_stderr")),
            Path(
                "runs", run_id, "inputs", "maps", "autogrid_stderr.txt"
            ).as_posix(),
            "AutoGrid stderr",
            True,
            "hydrated_autogrid_stderr",
        ),
        "autogrid_log": (
            _mapping(maps_snapshots.get("autogrid_log")),
            Path("runs", run_id, "inputs", "maps", "autogrid.glg").as_posix(),
            "AutoGrid GLG",
            False,
            "hydrated_autogrid_log",
        ),
    }
    if maps_snapshots.get("base_maps_xyz") is not None:
        fixed_specs["base_maps_xyz"] = (
            _mapping(maps_snapshots.get("base_maps_xyz")),
            Path(
                "runs", run_id, "inputs", "maps", "receptor.maps.xyz"
            ).as_posix(),
            "AutoGrid maps.xyz",
            False,
            "hydrated_base_maps_xyz",
        )
    verified: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    for key, (record, relative, label, allow_empty, artifact_key) in (
        fixed_specs.items()
    ):
        snapshot, path, verify_error = _verify_frozen_run_record(
            root,
            run_id,
            record,
            expected_relative=relative,
            label=label,
            allow_empty=allow_empty,
        )
        if verify_error:
            return None, verify_error
        assert snapshot is not None and path is not None
        verified[key] = snapshot
        paths[key] = path
        if artifact_key:
            artifact_error = _verify_metadata_artifact_binding(
                root,
                run_id,
                metadata,
                artifact_key=artifact_key,
                snapshot=snapshot,
            )
            if artifact_error:
                return None, artifact_error

    map_snapshot_records = _list_of_mappings(maps_snapshots.get("files"))
    if not map_snapshot_records:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_SNAPSHOTS_MISSING",
            "完整溯源合同缺少 run 内 map 快照清单。",
        )
    map_names: set[str] = set()
    for item in map_snapshot_records:
        name = str(item.get("name") or "")
        if not name or Path(name).name != name or name in map_names:
            return None, _hydrated_provenance_error(
                root,
                run_id,
                "MAP_RECORD_INVALID",
                "run 内 map 快照清单包含空名称、路径名称或重复名称。",
            )
        map_names.add(name)
        snapshot, path, verify_error = _verify_frozen_run_record(
            root,
            run_id,
            item,
            expected_relative=Path(
                "runs", run_id, "inputs", "maps", name
            ).as_posix(),
            label=f"map {name}",
        )
        if verify_error:
            return None, verify_error
        assert snapshot is not None and path is not None
        verified[f"map:{name}"] = snapshot
        paths[f"map:{name}"] = path

    artifacts = _mapping(metadata.get("artifacts"))
    artifact_hashes = _mapping(metadata.get("artifact_sha256"))
    if {
        key: str(value.get("sha256") or "")
        for key, value in artifacts.items()
        if isinstance(value, Mapping)
    } != {
        str(key): str(value or "")
        for key, value in artifact_hashes.items()
    }:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "ARTIFACT_INDEX_MISMATCH",
            "metadata 的 artifacts 与 artifact_sha256 索引不一致。",
        )

    input_hashes = _mapping(metadata.get("input_sha256"))
    expected_input_hashes = {
        "receptor": verified["receptor"]["sha256"],
        "ligand": verified["hydrated_ligand"]["sha256"],
        "config": verified["config"]["sha256"],
        "command_preview": verified["command_preview"]["sha256"],
        "maps_manifest": verified["maps_manifest"]["sha256"],
        "base_maps_manifest": verified["base_maps_manifest"]["sha256"],
        **(
            {"base_maps_xyz": verified["base_maps_xyz"]["sha256"]}
            if "base_maps_xyz" in verified
            else {}
        ),
        "gpf": verified["gpf"]["sha256"],
        "hydrated_ligand_manifest": verified["ligand_manifest"]["sha256"],
        "raw_ligand": verified["raw_ligand"]["sha256"],
        "added_h_ligand": verified["added_h_ligand"]["sha256"],
        "preparation_script": verified["preparation_script"]["sha256"],
    }
    if any(
        str(input_hashes.get(key) or "").lower() != value
        for key, value in expected_input_hashes.items()
    ) or _mapping(input_hashes.get("maps")) != {
        name: verified[f"map:{name}"]["sha256"]
        for name in map_names
    }:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "INPUT_HASH_INDEX_MISMATCH",
            "metadata 的 input_sha256 未精确绑定全部冻结输入。",
        )

    preparation_manifest, manifest_error = _json_from_frozen_file(
        root,
        run_id,
        paths["ligand_manifest"],
        label="水合配体 preparation manifest",
    )
    if manifest_error:
        return None, manifest_error
    maps_manifest, manifest_error = _json_from_frozen_file(
        root,
        run_id,
        paths["maps_manifest"],
        label="水合 maps manifest",
    )
    if manifest_error:
        return None, manifest_error
    base_manifest, manifest_error = _json_from_frozen_file(
        root,
        run_id,
        paths["base_maps_manifest"],
        label="基础 maps manifest",
    )
    if manifest_error:
        return None, manifest_error
    assert (
        preparation_manifest is not None
        and maps_manifest is not None
        and base_manifest is not None
    )

    preparation_source = _mapping(preparation_manifest.get("source"))
    preparation_outputs = _mapping(preparation_manifest.get("outputs"))
    preparation_execution = _mapping(preparation_manifest.get("execution"))
    preparation_integrity = _mapping(preparation_manifest.get("integrity"))
    if (
        preparation_manifest.get("schema_version") != 1
        or preparation_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or preparation_manifest.get("status") != "finished"
        or str(preparation_source.get("path") or "")
        != str(raw_record.get("source_identity") or "")
        or str(preparation_source.get("snapshot_file") or "")
        != str(raw_record.get("source_relative_path") or "")
        or _record_hash_and_size(preparation_source)
        != _record_hash_and_size(verified["raw_ligand"])
        or not _records_share_bytes(
            _mapping(preparation_manifest.get("script")),
            verified["preparation_script"],
        )
        or not _records_share_bytes(
            _mapping(preparation_outputs.get("added_h_sdf")),
            verified["added_h_ligand"],
        )
        or not _records_share_bytes(
            _mapping(preparation_outputs.get("hydrated_pdbqt")),
            verified["hydrated_ligand"],
        )
        or not _records_share_bytes(
            _mapping(preparation_execution.get("stdout")),
            verified["preparation_stdout"],
        )
        or not _records_share_bytes(
            _mapping(preparation_execution.get("stderr")),
            verified["preparation_stderr"],
        )
        or preparation_execution.get("exit_code") != 0
        or not _records_share_bytes(
            _mapping(preparation_integrity.get("source_before")),
            _mapping(preparation_integrity.get("source_after")),
        )
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "LIGAND_PREPARATION_BINDING_MISMATCH",
            "原始、加氢和水合配体或 preparation 审计文件未形成一致证据链。",
        )
    preparation_command = preparation_manifest.get("command")
    preparation_tools_record = _mapping(preparation_manifest.get("tools"))
    preparation_before_tools = _mapping(
        preparation_tools_record.get("before")
    )
    python_before_record = _mapping(preparation_before_tools.get("python"))
    script_source_path = str(
        _mapping(preparation_manifest.get("script")).get("path") or ""
    )
    added_h_source_path = str(
        _mapping(preparation_outputs.get("added_h_sdf")).get("path") or ""
    )
    hydrated_source_path = str(
        _mapping(preparation_outputs.get("hydrated_pdbqt")).get("path") or ""
    )
    preparation_record_dir = str(
        preparation_manifest.get("record_dir") or ""
    )
    command_text = (
        [str(item) for item in preparation_command]
        if isinstance(preparation_command, list)
        else []
    )
    if (
        not isinstance(preparation_command, list)
        or len(command_text) != 8
        or command_text[0] != str(python_before_record.get("path") or "")
        or command_text[1:3] != ["-I", "-B"]
        or command_text[4] != "prepare"
        or not Path(command_text[3]).as_posix().endswith(script_source_path)
        or not Path(command_text[5]).as_posix().endswith(
            str(preparation_source.get("snapshot_file") or "")
        )
        or not Path(command_text[6]).as_posix().endswith(
            f"{preparation_record_dir}/candidate_ligand_added_h.sdf"
        )
        or not Path(command_text[7]).as_posix().endswith(
            f"{preparation_record_dir}/candidate_ligand_hydrated.pdbqt"
        )
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "LIGAND_PREPARATION_COMMAND_MISMATCH",
            "水合配体 preparation 命令未绑定冻结脚本、原始输入和 Python。",
            raw_error=json.dumps(
                {
                    "command": command_text,
                    "python": python_before_record.get("path"),
                    "script": script_source_path,
                    "source": preparation_source.get("snapshot_file"),
                    "added_h": added_h_source_path,
                    "hydrated": hydrated_source_path,
                    "record_dir": preparation_record_dir,
                },
                ensure_ascii=False,
            ),
        )
    toolchain, tool_error = _validated_toolchain(
        root,
        run_id,
        preparation_manifest,
    )
    if tool_error:
        return None, tool_error
    assert toolchain is not None

    active_maps = _mapping(maps_manifest.get("maps"))
    base_maps = _mapping(base_manifest.get("maps"))
    active_file_records = _list_of_mappings(active_maps.get("files"))
    base_file_records = _list_of_mappings(base_maps.get("files"))
    active_files = {
        str(item.get("name") or ""): item
        for item in active_file_records
    }
    base_files = {
        str(item.get("name") or ""): item
        for item in base_file_records
    }
    active_required_raw = active_maps.get("required_files")
    base_required_raw = base_maps.get("required_files")
    active_required = (
        [str(item) for item in active_required_raw]
        if isinstance(active_required_raw, list)
        and all(isinstance(item, str) for item in active_required_raw)
        else []
    )
    base_required = (
        [str(item) for item in base_required_raw]
        if isinstance(base_required_raw, list)
        and all(isinstance(item, str) for item in base_required_raw)
        else []
    )
    expected_base_files = map_names - {"receptor.W.map"}
    base_optional_files = set(base_files) - set(base_required)
    allowed_base_optional_files = {"receptor.maps.xyz"}
    base_xyz_record = _mapping(base_files.get("receptor.maps.xyz"))
    base_xyz_snapshot = _mapping(maps_snapshots.get("base_maps_xyz"))
    expected_base_xyz_relative = Path(
        "maps",
        str(base_manifest.get("map_set_id") or ""),
        "receptor.maps.xyz",
    ).as_posix()
    audit_files_raw = maps_manifest.get("audit_files")
    audit_file_records = _list_of_mappings(audit_files_raw)
    audit_by_name = {
        str(item.get("name") or ""): item
        for item in audit_file_records
    }
    base_xyz_audit_record = _mapping(
        audit_by_name.get("receptor.maps.xyz")
    )
    if (
        maps_manifest.get("schema_version") != 1
        or maps_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or maps_manifest.get("status") != "ready"
        or base_manifest.get("schema_version") != 1
        or base_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or base_manifest.get("status") != "ready"
        or maps_manifest.get("map_set_id") != base_manifest.get("map_set_id")
        or set(active_files) != map_names
        or len(active_file_records) != len(active_files)
        or len(base_file_records) != len(base_files)
        or len(active_required) != len(map_names)
        or set(active_required) != map_names
        or len(base_required) != len(expected_base_files)
        or set(base_required) != expected_base_files
        or not expected_base_files.issubset(base_files)
        or bool(base_optional_files - allowed_base_optional_files)
        or not isinstance(audit_files_raw, list)
        or len(audit_file_records) != len(audit_files_raw)
        or len(audit_by_name) != len(audit_file_records)
        or "" in audit_by_name
        or (
            ("receptor.maps.xyz" in base_optional_files)
            != bool(base_xyz_snapshot)
        )
        or (
            "receptor.maps.xyz" in base_optional_files
            and (
                str(base_xyz_record.get("relative_path") or "")
                != expected_base_xyz_relative
                or str(
                    base_xyz_audit_record.get("relative_path") or ""
                )
                != expected_base_xyz_relative
                or str(
                    base_xyz_snapshot.get("source_relative_path") or ""
                )
                != expected_base_xyz_relative
                or not _records_share_bytes(
                    base_xyz_record,
                    base_xyz_snapshot,
                )
                or not _records_share_bytes(
                    base_xyz_record,
                    base_xyz_audit_record,
                )
            )
        )
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_MANIFEST_CONTRACT_MISMATCH",
            "水合与基础 maps manifest 的协议、状态、编号或文件集合不一致。",
            raw_error=json.dumps(
                {
                    "hydrated_manifest": {
                        "schema_version": maps_manifest.get("schema_version"),
                        "protocol_id": maps_manifest.get("protocol_id"),
                        "status": maps_manifest.get("status"),
                        "map_set_id": maps_manifest.get("map_set_id"),
                        "files": sorted(active_files),
                        "required_files": sorted(active_required),
                    },
                    "base_manifest": {
                        "schema_version": base_manifest.get("schema_version"),
                        "protocol_id": base_manifest.get("protocol_id"),
                        "status": base_manifest.get("status"),
                        "map_set_id": base_manifest.get("map_set_id"),
                        "files": sorted(base_files),
                        "required_files": sorted(base_required),
                        "optional_files": sorted(base_optional_files),
                        "optional_xyz_relative_path": (
                            base_xyz_record.get("relative_path")
                        ),
                    },
                    "frozen_map_files": sorted(map_names),
                    "expected_base_files": sorted(expected_base_files),
                    "expected_base_xyz_relative_path": (
                        expected_base_xyz_relative
                    ),
                    "base_xyz_snapshot": {
                        "present": bool(base_xyz_snapshot),
                        "source_relative_path": (
                            base_xyz_snapshot.get("source_relative_path")
                        ),
                    },
                    "base_xyz_audit_relative_path": (
                        base_xyz_audit_record.get("relative_path")
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    if any(
        not _records_share_bytes(active_files[name], verified[f"map:{name}"])
        for name in map_names
    ) or any(
        not _records_share_bytes(base_files[name], verified[f"map:{name}"])
        for name in expected_base_files
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_MANIFEST_FILE_MISMATCH",
            "map 文件的 run 快照、活动 manifest 与基础 manifest 哈希不一致。",
        )

    active_base_record = _mapping(maps_manifest.get("base_maps_manifest"))
    base_gpf = _mapping(base_manifest.get("gpf"))
    audit_bindings = {
        "receptor.gpf": "gpf",
        "stdout.txt": "autogrid_stdout",
        "stderr.txt": "autogrid_stderr",
        "autogrid.glg": "autogrid_log",
    }
    if (
        not _records_share_bytes(
            active_base_record,
            verified["base_maps_manifest"],
        )
        or not _records_share_bytes(base_gpf, verified["gpf"])
        or any(
            not _records_share_bytes(
                _mapping(audit_by_name.get(name)),
                verified[key],
            )
            for name, key in audit_bindings.items()
        )
        or "successful completion"
        not in paths["autogrid_log"].read_text(
            encoding="utf-8",
            errors="replace",
        ).lower()
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "AUTOGRID_AUDIT_BINDING_MISMATCH",
            "基础 manifest、GPF 或 AutoGrid 审计输出未形成一致证据链。",
            raw_error=json.dumps(
                {
                    "base_manifest": _records_share_bytes(
                        active_base_record,
                        verified["base_maps_manifest"],
                    ),
                    "gpf": _records_share_bytes(
                        base_gpf,
                        verified["gpf"],
                    ),
                    "audit": {
                        name: _records_share_bytes(
                            _mapping(audit_by_name.get(name)),
                            verified[key],
                        )
                        for name, key in audit_bindings.items()
                    },
                    "log_success": (
                        "successful completion"
                        in paths["autogrid_log"].read_text(
                            encoding="utf-8",
                            errors="replace",
                        ).lower()
                    ),
                },
                ensure_ascii=False,
            ),
        )

    active_receptor = _mapping(maps_manifest.get("receptor"))
    active_ligand = _mapping(maps_manifest.get("ligand"))
    base_receptor = _mapping(base_manifest.get("receptor"))
    base_ligand = _mapping(base_manifest.get("ligand"))
    if (
        str(active_receptor.get("sha256") or "").lower()
        != verified["receptor"]["sha256"]
        or str(base_receptor.get("source_sha256") or "").lower()
        != verified["receptor"]["sha256"]
        or str(active_ligand.get("sha256") or "").lower()
        != verified["hydrated_ligand"]["sha256"]
        or str(base_ligand.get("source_sha256") or "").lower()
        != verified["hydrated_ligand"]["sha256"]
        or str(
            _mapping(maps_manifest.get("hydrated_ligand_manifest")).get(
                "sha256"
            )
            or ""
        ).lower()
        != verified["ligand_manifest"]["sha256"]
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_INPUT_IDENTITY_MISMATCH",
            "maps manifest 未绑定当前 run 的刚性受体、水合配体或 preparation manifest。",
        )

    grid_coverage = _mapping(
        frozen_input_integrity.get("hydrated_grid_coverage")
    )
    if (
        not grid_coverage
        or _mapping(maps_manifest.get("grid"))
        != _mapping(base_manifest.get("grid"))
        or _mapping(maps_manifest.get("grid"))
        != _mapping(maps_snapshots.get("grid"))
        or _mapping(maps_manifest.get("grid_coverage")) != grid_coverage
        or _mapping(base_manifest.get("grid_coverage")) != grid_coverage
        or str(maps_manifest.get("grid_coverage_sha256") or "").lower()
        != _canonical_json_sha256(grid_coverage)
        or str(base_manifest.get("grid_coverage_sha256") or "").lower()
        != _canonical_json_sha256(grid_coverage)
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "GRID_COVERAGE_BINDING_MISMATCH",
            "Box、npts、spacing 与实际 AutoGrid 覆盖合同不一致。",
        )
    grid = _mapping(maps_manifest.get("grid"))
    grid_points = _mapping(grid.get("grid_points"))
    center = _mapping(grid.get("center"))
    gpf, gpf_error = _parse_gpf_contract(
        paths["gpf"].read_text(encoding="utf-8", errors="strict")
    )
    if gpf_error or gpf is None:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "GPF_INVALID",
            "冻结的 AutoGrid GPF 无法按合同解析。",
            raw_error=gpf_error,
        )
    expected_npts = tuple(int(grid_points.get(axis) or 0) for axis in ("x", "y", "z"))
    expected_center = tuple(float(center.get(axis)) for axis in ("x", "y", "z"))
    expected_base_map_names = {
        Path(str(name)).name
        for name in base_files
        if str(name).endswith(".map")
    }
    gpf_map_names = {
        Path(str(name)).name
        for name in (
            list(gpf["maps"])
            + [str(gpf["elecmap"]), str(gpf["dsolvmap"])]
        )
        if str(name)
    }
    if (
        gpf["npts"] != expected_npts
        or not _numbers_match(gpf["spacing"], grid.get("spacing"))
        or any(
            not _numbers_match(left, right)
            for left, right in zip(
                gpf["center"],
                expected_center,
                strict=True,
            )
        )
        or Path(str(gpf["receptor"])).name != "receptor.pdbqt"
        or gpf_map_names != expected_base_map_names
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "GPF_GRID_MISMATCH",
            "GPF 的 npts、spacing、center、受体或 map 集合与冻结 manifest 不一致。",
        )

    active_autogrid = _mapping(maps_manifest.get("autogrid"))
    base_autogrid = _mapping(base_manifest.get("autogrid"))
    active_identity = _tool_identity(active_autogrid)
    base_command = base_autogrid.get("command")
    active_command = active_autogrid.get("command")
    if (
        active_identity is None
        or not autogrid_core.autogrid_version_supported(
            str(active_autogrid.get("version") or ""),
            HYDRATED_PROTOCOL_ID,
        )
        or any(
            str(active_autogrid.get(key) or "")
            != str(base_autogrid.get(key) or "")
            for key in ("path", "sha256", "version", "source")
        )
        or active_command != base_command
        or not isinstance(active_command, list)
        or len(active_command) < 5
        or str(active_command[0]) != str(active_autogrid.get("path") or "")
        or active_autogrid.get("exit_code") != 0
        or base_autogrid.get("exit_code") != 0
        or _mapping(base_autogrid.get("log_summary")).get(
            "successful_completion"
        )
        is not True
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "AUTOGRID_TOOL_BINDING_MISMATCH",
            "AutoGrid 路径、版本、二进制身份、命令或成功日志不一致。",
        )

    parsed_maps: dict[str, Any] = {}
    try:
        for name in sorted(map_names):
            if name.endswith(".map"):
                parsed_maps[name] = hydrated_map_core.parse_autogrid_map(
                    paths[f"map:{name}"]
                )
    except hydrated_map_core.HydratedMapError as exc:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_PARSE_FAILED",
            "冻结 AutoGrid map 不能安全解析。",
            raw_error=str(exc),
        )
    required_water_names = {
        "receptor.OA.map",
        "receptor.HD.map",
        "receptor.W.map",
    }
    if not required_water_names.issubset(parsed_maps):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "WATER_MAP_SOURCES_MISSING",
            "冻结 maps 缺少 OA、HD 或 W。",
        )
    reference_geometry = _mapping(active_maps.get("geometry"))
    if any(
        parsed.sha256 != verified[f"map:{name}"]["sha256"]
        or parsed.geometry.to_dict() != reference_geometry
        for name, parsed in parsed_maps.items()
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "MAP_GEOMETRY_MISMATCH",
            "冻结 maps 的解析哈希或几何不一致。",
        )

    water_record = _mapping(active_maps.get("water_map"))
    water_sources = _mapping(water_record.get("sources"))
    oa = parsed_maps["receptor.OA.map"]
    hd = parsed_maps["receptor.HD.map"]
    water = parsed_maps["receptor.W.map"]
    recomputed_values, recomputed_statistics = _recomputed_best_statistics(
        oa.values,
        hd.values,
    )
    if (
        str(water_record.get("name") or "") != "receptor.W.map"
        or water_record.get("method") != "hydrated_ad4_best_v1"
        or _mapping(water_record.get("parameters"))
        != _expected_water_map_parameters()
        or _mapping(water_record.get("statistics")) != recomputed_statistics
        or not _records_share_bytes(
            water_record,
            verified["map:receptor.W.map"],
        )
        or not _records_share_bytes(
            _mapping(water_sources.get("oa")),
            verified["map:receptor.OA.map"],
        )
        or not _records_share_bytes(
            _mapping(water_sources.get("hd")),
            verified["map:receptor.HD.map"],
        )
        or list(water.values) != recomputed_values
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "WATER_MAP_BEST_MISMATCH",
            "W map 不是由冻结 OA/HD maps 按固定 BEST 合同生成。",
        )

    vina_tool = _mapping(metadata.get("vina_tool"))
    vina_artifacts = [
        _mapping(artifacts.get(key))
        for key in (
            "vina_binary_prepared",
            "vina_binary_executed",
            "vina_binary_observed_after_execution",
        )
    ]
    if (
        _tool_identity(vina_tool) is None
        or any(not _same_tool_identity(vina_tool, item) for item in vina_artifacts)
        or str(metadata.get("vina_path") or "") != str(vina_tool.get("path") or "")
        or str(metadata.get("vina_version") or "")
        != str(vina_tool.get("version") or "")
        or str(metadata.get("vina_source") or "")
        != str(vina_tool.get("source") or "")
        or str(metadata.get("vina_sha256") or "").lower()
        != str(vina_tool.get("sha256") or "").lower()
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_TOOL_BINDING_MISMATCH",
            "Vina 的准备、执行和执行后身份记录不一致。",
            raw_error=json.dumps(
                {
                    "vina_tool": vina_tool,
                    "artifacts": vina_artifacts,
                    "metadata": {
                        "path": metadata.get("vina_path"),
                        "version": metadata.get("vina_version"),
                        "source": metadata.get("vina_source"),
                        "sha256": metadata.get("vina_sha256"),
                    },
                },
                ensure_ascii=False,
            ),
        )
    command = metadata.get("command")
    expected_config = verified["config"]["relative_path"]
    expected_prefix = Path(
        "runs", run_id, "inputs", "maps", "receptor"
    ).as_posix()
    expected_output = Path("runs", run_id, "out.pdbqt").as_posix()
    expected_command = [
        str(vina_tool.get("path") or ""),
        "--config",
        expected_config,
        "--maps",
        expected_prefix,
        "--scoring",
        "ad4",
        "--out",
        expected_output,
    ]
    if command != expected_command:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_COMMAND_MISMATCH",
            "Vina 命令未精确绑定冻结配置、maps、AD4 评分和输出。",
            raw_error=json.dumps(command, ensure_ascii=False),
        )
    command_preview_text = paths["command_preview"].read_text(
        encoding="utf-8"
    )
    if command_preview_text != _format_command_preview(expected_command) + "\n":
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "COMMAND_PREVIEW_MISMATCH",
            "命令预览内容与实际冻结命令不一致。",
        )

    config, config_error = _parse_simple_config(
        paths["config"].read_text(encoding="utf-8")
    )
    if config_error or config is None:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_CONFIG_INVALID",
            "冻结 Vina 配置无法解析。",
            raw_error=config_error,
        )
    vina_snapshot = _mapping(snapshots.get("vina"))
    expected_config_values = {
        "ligand": verified["hydrated_ligand"]["relative_path"],
        "scoring": "ad4",
        "exhaustiveness": str(vina_snapshot.get("exhaustiveness")),
        "max_evals": str(vina_snapshot.get("max_evals")),
        "num_modes": str(vina_snapshot.get("num_modes")),
        "min_rmsd": str(vina_snapshot.get("min_rmsd")),
        "energy_range": str(vina_snapshot.get("energy_range")),
        "cpu": str(vina_snapshot.get("cpu")),
        "verbosity": str(vina_snapshot.get("verbosity")),
    }
    seed = vina_snapshot.get("seed")
    if seed is not None:
        expected_config_values["seed"] = str(seed)
    numeric_config_keys = {
        "exhaustiveness",
        "max_evals",
        "num_modes",
        "min_rmsd",
        "energy_range",
        "cpu",
        "verbosity",
        *(["seed"] if seed is not None else []),
    }
    config_values_match = (
        set(config) == set(expected_config_values)
        and config.get("ligand") == expected_config_values["ligand"]
        and config.get("scoring") == "ad4"
    )
    if config_values_match:
        try:
            config_values_match = all(
                _numbers_match(
                    float(config[key]),
                    float(expected_config_values[key]),
                )
                for key in numeric_config_keys
            )
        except (KeyError, TypeError, ValueError):
            config_values_match = False
    if not config_values_match or "receptor" in config:
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_CONFIG_CONTRACT_MISMATCH",
            "冻结 Vina 配置与参数快照或 AD4 maps 执行合同不一致。",
            raw_error=json.dumps(
                {
                    "recorded": config,
                    "expected": expected_config_values,
                },
                ensure_ascii=False,
            ),
        )

    spacing = float(grid.get("spacing"))
    expected_postprocess = _expected_postprocess_parameters(spacing)
    hydrated_record = _mapping(metadata.get("hydrated"))
    postprocess_contract = _mapping(hydrated_record.get("postprocess_contract"))
    static_postprocess = copy.deepcopy(expected_postprocess)
    static_postprocess.pop("map_sample_radius_steps", None)
    if (
        postprocess_contract.get("method")
        != "dockstart_hydrated_water_filter_v1"
        or _mapping(postprocess_contract.get("semantics"))
        != _HYDRATED_POSTPROCESS_SEMANTICS
        or _mapping(postprocess_contract.get("parameters"))
        != static_postprocess
        or waters_manifest.get("method")
        != "dockstart_hydrated_water_filter_v1"
        or _mapping(waters_manifest.get("semantics"))
        != _HYDRATED_POSTPROCESS_SEMANTICS
        or _mapping(waters_manifest.get("parameters"))
        != expected_postprocess
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "POSTPROCESS_CONTRACT_MISMATCH",
            "水分子后处理未绑定固定算法、2.03 Å/±1 Å 采样和 -0.5/-0.3 阈值。",
        )

    from dockstart_core.hydrated_postprocess import postprocess_hydrated_output

    raw_output = root / Path("runs", run_id, "out.pdbqt")
    retained_output = root / Path(
        "runs", run_id, HYDRATED_RETAINED_OUTPUT_NAME
    )
    water_free_output = root / Path(
        "runs", run_id, HYDRATED_WATER_FREE_OUTPUT_NAME
    )
    try:
        with tempfile.TemporaryDirectory(prefix="dockstart-hydrated-audit-") as temporary:
            temporary_path = Path(temporary)
            reproduced = postprocess_hydrated_output(
                raw_output,
                paths["receptor"],
                paths["map:receptor.W.map"],
                temporary_path / HYDRATED_RETAINED_OUTPUT_NAME,
                temporary_path / HYDRATED_WATER_FREE_OUTPUT_NAME,
            )
    except Exception as exc:  # noqa: BLE001 - convert replay failure to fail-closed.
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "POSTPROCESS_REPLAY_FAILED",
            "无法使用冻结输入重放水分子后处理。",
            raw_error=str(exc),
        )
    reproduced_manifest = _mapping(reproduced.get("manifest"))
    reproduced_outputs = _mapping(reproduced_manifest.get("outputs"))
    recorded_outputs = _mapping(waters_manifest.get("outputs"))
    replay_output_pairs = (
        ("retained_water_annotated", retained_output),
        ("water_free_ligand", water_free_output),
    )
    replay_outputs_match = True
    for output_key, actual_path in replay_output_pairs:
        replay_record = _mapping(reproduced_outputs.get(output_key))
        recorded_record = _mapping(recorded_outputs.get(output_key))
        actual_snapshot = _hash_snapshot(actual_path, str(recorded_record.get("path") or ""))
        if (
            _record_hash_and_size(replay_record)
            != _record_hash_and_size(recorded_record)
            or _record_hash_and_size(actual_snapshot)
            != _record_hash_and_size(recorded_record)
        ):
            replay_outputs_match = False
            break
    if (
        reproduced_manifest.get("method") != waters_manifest.get("method")
        or _mapping(reproduced_manifest.get("semantics"))
        != _mapping(waters_manifest.get("semantics"))
        or _mapping(reproduced_manifest.get("parameters"))
        != _mapping(waters_manifest.get("parameters"))
        or _mapping(reproduced_manifest.get("summary"))
        != _mapping(waters_manifest.get("summary"))
        or _list_of_mappings(reproduced_manifest.get("poses"))
        != _list_of_mappings(waters_manifest.get("poses"))
        or not replay_outputs_match
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "POSTPROCESS_REPLAY_MISMATCH",
            "冻结输入重放得到的水分子分类或输出与保存结果不一致。",
        )

    hydrated_postprocess = _mapping(metadata.get("hydrated_postprocess"))
    if (
        hydrated_postprocess.get("method") != waters_manifest.get("method")
        or _mapping(hydrated_postprocess.get("semantics"))
        != _mapping(waters_manifest.get("semantics"))
        or _mapping(hydrated_postprocess.get("summary"))
        != _mapping(waters_manifest.get("summary"))
        or _mapping(hydrated_postprocess.get("outputs"))
        != _mapping(waters_manifest.get("outputs"))
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "POSTPROCESS_METADATA_MISMATCH",
            "metadata 的水合后处理摘要未精确绑定 waters manifest。",
        )

    verified_toolchain = {
        **toolchain,
        "autogrid": {
            **active_identity,
            "version": str(active_autogrid.get("version") or ""),
            "source": str(active_autogrid.get("source") or ""),
        },
        "vina": {
            **(_tool_identity(vina_tool) or {}),
            "version": str(vina_tool.get("version") or ""),
            "source": str(vina_tool.get("source") or ""),
        },
    }
    commands = {
        "ligand_preparation": [str(item) for item in preparation_command],
        "autogrid": [str(item) for item in active_command],
        "vina": [str(item) for item in expected_command],
    }
    return {
        "status": "verified",
        "schema_version": HYDRATED_PROVENANCE_SCHEMA_VERSION,
        "contract_id": HYDRATED_PROVENANCE_CONTRACT_ID,
        "read_only": True,
        "evidence_source": "recorded_run_snapshots_only",
        "verified_files": verified,
        "toolchain": verified_toolchain,
        "commands": commands,
        "command_sha256": {
            key: _canonical_json_sha256(value)
            for key, value in commands.items()
        },
        "grid": copy.deepcopy(grid),
        "grid_coverage": copy.deepcopy(grid_coverage),
        "water_map": {
            "method": water_record.get("method"),
            "parameters": copy.deepcopy(water_record.get("parameters") or {}),
            "statistics": copy.deepcopy(water_record.get("statistics") or {}),
            "sources": {
                "oa": copy.deepcopy(verified["map:receptor.OA.map"]),
                "hd": copy.deepcopy(verified["map:receptor.HD.map"]),
            },
            "output": copy.deepcopy(verified["map:receptor.W.map"]),
        },
        "postprocess": {
            "method": waters_manifest.get("method"),
            "semantics": copy.deepcopy(waters_manifest.get("semantics") or {}),
            "parameters": copy.deepcopy(waters_manifest.get("parameters") or {}),
            "replayed": True,
        },
        "manifest_hashes": {
            "ligand_preparation": verified["ligand_manifest"]["sha256"],
            "base_maps": verified["base_maps_manifest"]["sha256"],
            "hydrated_maps": verified["maps_manifest"]["sha256"],
            "waters": str(
                _mapping(artifacts.get("hydrated_waters_manifest")).get(
                    "sha256"
                )
                or ""
            ),
        },
        "config": copy.deepcopy(verified["config"]),
        "command_preview": copy.deepcopy(verified["command_preview"]),
        "artifact_sha256": copy.deepcopy(artifact_hashes),
    }, None


def _validate_hydrated_result_table(
    root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
    log_scores: list[dict[str, Any]],
    waters_manifest: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    frozen_vina = _mapping(_mapping(metadata.get("snapshots")).get("vina"))
    verbosity = frozen_vina.get("verbosity")
    num_modes = frozen_vina.get("num_modes")
    energy_range = frozen_vina.get("energy_range")
    if (
        isinstance(verbosity, bool)
        or not isinstance(verbosity, int)
        or verbosity not in {1, 2}
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_VERBOSITY_CONTEXT_INVALID",
            "冻结的 Vina verbosity 无法用于复核评分表序列化。",
            raw_error=str(verbosity),
        )
    if (
        isinstance(num_modes, bool)
        or not isinstance(num_modes, int)
        or num_modes <= 0
        or isinstance(energy_range, bool)
        or not isinstance(energy_range, (int, float))
        or not math.isfinite(float(energy_range))
        or float(energy_range) <= 0.0
    ):
        return None, _hydrated_provenance_error(
            root,
            run_id,
            "VINA_RESULT_LIMITS_INVALID",
            "冻结的 num_modes 或 energy_range 无法用于复核 Vina 结果边界。",
            raw_error=json.dumps(
                {
                    "num_modes": num_modes,
                    "energy_range": energy_range,
                },
                ensure_ascii=False,
            ),
        )

    log_modes = [
        int(item.get("mode") or 0)
        for item in log_scores
        if isinstance(item.get("mode"), int)
        and not isinstance(item.get("mode"), bool)
    ]
    if (
        not log_scores
        or len(log_modes) != len(log_scores)
        or log_modes != list(range(1, len(log_scores) + 1))
        or len(log_scores) > num_modes
    ):
        return None, _error(
            "HYDRATED_LOG_MODE_SEQUENCE_INVALID",
            "Vina 评分表的构象编号不连续，或超过冻结的 num_modes。",
            raw_error=json.dumps(
                {
                    "log_modes": log_modes,
                    "frozen_num_modes": num_modes,
                },
                ensure_ascii=False,
            ),
            project_dir=str(root),
            run_id=run_id,
        )

    invalid_rows: list[dict[str, Any]] = []
    previous_affinity: float | None = None
    for index, score in enumerate(log_scores):
        numeric_values: dict[str, float] = {}
        row_valid = True
        for numeric_key, text_key in (
            ("affinity_kcal_mol", "affinity_text"),
            ("rmsd_lb", "rmsd_lb_text"),
            ("rmsd_ub", "rmsd_ub_text"),
        ):
            value = score.get(numeric_key)
            token = score.get(text_key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not vina_score_table_token_is_canonical(
                    token,
                    verbosity=verbosity,
                )
                or float(value) != float(str(token))
            ):
                row_valid = False
                continue
            numeric_values[numeric_key] = float(value)
        if len(numeric_values) == 3:
            affinity = numeric_values["affinity_kcal_mol"]
            rmsd_lb = numeric_values["rmsd_lb"]
            rmsd_ub = numeric_values["rmsd_ub"]
            if (
                rmsd_lb < 0.0
                or rmsd_ub < 0.0
                or rmsd_lb > rmsd_ub
                or (index == 0 and (rmsd_lb != 0.0 or rmsd_ub != 0.0))
                or (
                    previous_affinity is not None
                    and affinity < previous_affinity
                )
            ):
                row_valid = False
            previous_affinity = affinity
        if not row_valid:
            invalid_rows.append(copy.deepcopy(score))
    if invalid_rows:
        return None, _error(
            "HYDRATED_LOG_SCORE_SEMANTICS_INVALID",
            "Vina 评分表的显示格式、排序或 RMSD 语义无效。",
            raw_error=json.dumps(invalid_rows, ensure_ascii=False),
            suggestion="请保留该 run 供审计，并重新执行新的水合对接。",
            project_dir=str(root),
            run_id=run_id,
        )

    water_poses = _list_of_mappings(waters_manifest.get("poses"))
    output_modes = [int(item.get("mode") or 0) for item in water_poses]
    if (
        not output_modes
        or len(output_modes) > len(log_modes)
        or output_modes != log_modes[: len(output_modes)]
    ):
        return None, _error(
            "HYDRATED_OUTPUT_MODE_PREFIX_INVALID",
            "水合 PDBQT 输出不是 Vina 评分表的连续前缀。",
            raw_error=json.dumps(
                {
                    "log_modes": log_modes,
                    "output_modes": output_modes,
                },
                ensure_ascii=False,
            ),
            suggestion="请保留该 run 供审计，并重新执行新的水合对接。",
            project_dir=str(root),
            run_id=run_id,
        )

    log_records = {
        int(item.get("mode") or 0): item for item in log_scores
    }
    pose_records = {
        int(item.get("mode") or 0): item for item in water_poses
    }
    invalid_pose_rows: list[dict[str, Any]] = []
    previous_raw_affinity: float | None = None
    for mode in output_modes:
        pose = pose_records[mode]
        raw_values: dict[str, float] = {}
        pose_valid = True
        for key in ("raw_affinity", "rmsd_lb", "rmsd_ub"):
            value = pose.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                pose_valid = False
                continue
            raw_values[key] = float(value)
        if len(raw_values) == 3:
            raw_affinity = raw_values["raw_affinity"]
            rmsd_lb = raw_values["rmsd_lb"]
            rmsd_ub = raw_values["rmsd_ub"]
            if (
                rmsd_lb < 0.0
                or rmsd_ub < 0.0
                or rmsd_lb > rmsd_ub
                or (mode == 1 and (rmsd_lb != 0.0 or rmsd_ub != 0.0))
                or (
                    previous_raw_affinity is not None
                    and raw_affinity < previous_raw_affinity
                )
            ):
                pose_valid = False
            previous_raw_affinity = raw_affinity
        if not pose_valid:
            invalid_pose_rows.append(copy.deepcopy(pose))
    if invalid_pose_rows:
        return None, _error(
            "HYDRATED_PDBQT_RESULT_SEMANTICS_INVALID",
            "水合 PDBQT 的 affinity 排序或 RMSD 语义无效。",
            raw_error=json.dumps(
                invalid_pose_rows,
                ensure_ascii=False,
            ),
            suggestion="请保留该 run 供审计，并重新执行新的水合对接。",
            project_dir=str(root),
            run_id=run_id,
        )

    best_interval = _vina_joint_serialization_interval(
        str(log_records[1].get("affinity_text") or ""),
        pose_records[1].get("raw_affinity"),
        verbosity=verbosity,
    )
    if best_interval is None:
        return None, _error(
            "HYDRATED_SCORE_RECORD_MISMATCH",
            "Mode 1 的 Vina affinity 与水合 PDBQT 记录不一致。",
            project_dir=str(root),
            run_id=run_id,
        )

    frozen_energy_range = float(energy_range)
    for mode in output_modes:
        interval = _vina_joint_serialization_interval(
            str(log_records[mode].get("affinity_text") or ""),
            pose_records[mode].get("raw_affinity"),
            verbosity=verbosity,
        )
        if (
            interval is None
            or interval[0] > best_interval[1] + frozen_energy_range
        ):
            return None, _error(
                "HYDRATED_OUTPUT_ENERGY_RANGE_INVALID",
                f"Mode {mode} 与冻结的 energy_range 不一致。",
                raw_error=json.dumps(
                    {
                        "mode_interval": interval,
                        "best_interval": best_interval,
                        "energy_range": frozen_energy_range,
                    },
                    ensure_ascii=False,
                ),
                project_dir=str(root),
                run_id=run_id,
            )

    energy_range_status = "not_truncated"
    first_omitted_interval: tuple[float, float] | None = None
    if len(output_modes) < len(log_modes):
        first_omitted = log_scores[len(output_modes)]
        first_omitted_interval = _vina_score_table_numeric_interval(
            str(first_omitted.get("affinity_text") or ""),
            verbosity=verbosity,
        )
        if (
            first_omitted_interval is None
            or first_omitted_interval[1]
            <= best_interval[0] + frozen_energy_range
        ):
            return None, _error(
                "HYDRATED_OUTPUT_TRUNCATION_INVALID",
                "PDBQT 缺失的首个构象不可能由冻结的 energy_range 截断。",
                raw_error=json.dumps(
                    {
                        "first_omitted_mode": first_omitted.get("mode"),
                        "omitted_interval": first_omitted_interval,
                        "best_interval": best_interval,
                        "energy_range": frozen_energy_range,
                    },
                    ensure_ascii=False,
                ),
                project_dir=str(root),
                run_id=run_id,
            )
        energy_range_status = (
            "proven_truncated"
            if first_omitted_interval[0]
            > best_interval[1] + frozen_energy_range
            else "serialization_boundary_consistent"
        )

    return {
        "verbosity": verbosity,
        "num_modes": num_modes,
        "energy_range": frozen_energy_range,
        "log_modes": log_modes,
        "output_modes": output_modes,
        "log_records": log_records,
        "pose_records": pose_records,
        "energy_range_validation": {
            "status": energy_range_status,
            "best_affinity_interval": list(best_interval),
            "first_omitted_affinity_interval": (
                list(first_omitted_interval)
                if first_omitted_interval is not None
                else None
            ),
        },
    }, None


def load_hydrated_results(project_dir: str, run_id: str) -> dict[str, Any]:
    loaded = load_run_metadata(project_dir, run_id)
    if not loaded.get("ok"):
        return loaded
    metadata = _mapping(loaded.get("metadata"))
    if (
        metadata.get("protocol_id") != HYDRATED_PROTOCOL_ID
        and _mapping(metadata.get("docking_protocol")).get("protocol_id")
        != HYDRATED_PROTOCOL_ID
    ):
        return _error(
            "HYDRATED_RUN_PROTOCOL_MISMATCH",
            "所选 run 不是实验性水合 AD4 对接记录。",
            project_dir=project_dir,
            run_id=run_id,
        )
    if metadata.get("status") != "finished":
        return _error(
            "HYDRATED_RUN_NOT_FINISHED",
            "只有完成水分子后处理的 finished 水合 run 可以读取结果。",
            raw_error=f"status={metadata.get('status')}; stage={metadata.get('stage')}",
            project_dir=project_dir,
            run_id=run_id,
        )

    root = Path(project_dir).expanduser().resolve()
    is_modern, marker_issue = _modern_hydrated_provenance_marker(metadata)
    if marker_issue:
        return _hydrated_provenance_error(
            root,
            run_id,
            "MARKER_MISMATCH",
            "水合 run 的完整溯源 schema/contract 标记不一致。",
            raw_error=marker_issue,
        )
    if is_modern:
        frozen_input_integrity = _validate_ad4_maps_post_run_integrity(
            str(root),
            run_id,
            metadata,
        )
        if not frozen_input_integrity.get("ok"):
            return frozen_input_integrity
        provenance: dict[str, Any] | None = None
    else:
        provenance, provenance_error = _validate_legacy_hydrated_provenance(
            root,
            run_id,
            metadata,
        )
        if provenance_error:
            return provenance_error
        assert provenance is not None
        frozen_input_integrity = {
            "ok": True,
            "hydrated_grid_coverage": copy.deepcopy(
                provenance.get("grid_coverage") or {}
            ),
        }
    waters_manifest, waters_error = _load_waters_manifest(root, run_id, metadata)
    if waters_error:
        return waters_error
    assert waters_manifest is not None
    if is_modern:
        provenance, provenance_error = _validate_modern_hydrated_provenance(
            root,
            run_id,
            metadata,
            waters_manifest,
            frozen_input_integrity,
        )
        if provenance_error:
            return provenance_error
        assert provenance is not None
    log_record = _mapping(_mapping(metadata.get("artifacts")).get("log"))
    log_snapshot, log_path, log_error = _verify_frozen_run_record(
        root,
        run_id,
        log_record,
        expected_relative=Path("runs", run_id, "log.txt").as_posix(),
        label="Vina log",
    )
    if log_error:
        return log_error
    assert log_snapshot is not None and log_path is not None
    if (
        str(
            _mapping(metadata.get("artifact_sha256")).get("log") or ""
        ).lower()
        != log_snapshot["sha256"]
    ):
        return _hydrated_provenance_error(
            root,
            run_id,
            "LOG_ARTIFACT_BINDING_MISMATCH",
            "Vina log 与 artifact_sha256 的冻结绑定不一致。",
        )
    if is_modern:
        expected_log_relative = Path("runs", run_id, "log.txt").as_posix()
        expected_stdout_relative = Path(
            "runs", run_id, "stdout.txt"
        ).as_posix()
        if (
            str(metadata.get("log_file") or "") != expected_log_relative
            or str(metadata.get("stdout_file") or "")
            != expected_stdout_relative
        ):
            return _hydrated_provenance_error(
                root,
                run_id,
                "VINA_OUTPUT_PATH_MISMATCH",
                "现代水合 run 的 Vina stdout/log 没有绑定固定的 run 内路径。",
                raw_error=json.dumps(
                    {
                        "log_file": metadata.get("log_file"),
                        "stdout_file": metadata.get("stdout_file"),
                    },
                    ensure_ascii=False,
                ),
            )
        stdout_record = _mapping(
            _mapping(metadata.get("artifacts")).get("stdout")
        )
        stdout_snapshot, stdout_path, stdout_error = (
            _verify_frozen_run_record(
                root,
                run_id,
                stdout_record,
                expected_relative=expected_stdout_relative,
                label="Vina stdout",
            )
        )
        if stdout_error:
            return stdout_error
        assert stdout_snapshot is not None and stdout_path is not None
        if (
            str(
                _mapping(metadata.get("artifact_sha256")).get("stdout")
                or ""
            ).lower()
            != stdout_snapshot["sha256"]
        ):
            return _hydrated_provenance_error(
                root,
                run_id,
                "STDOUT_ARTIFACT_BINDING_MISMATCH",
                "Vina stdout 与 artifact_sha256 的冻结绑定不一致。",
            )
        if (
            stdout_snapshot["size_bytes"] != log_snapshot["size_bytes"]
            or stdout_snapshot["sha256"] != log_snapshot["sha256"]
            or not _files_are_byte_identical(stdout_path, log_path)
        ):
            return _hydrated_provenance_error(
                root,
                run_id,
                "VINA_STDOUT_LOG_MISMATCH",
                "Vina stdout 与同次执行生成的 log.txt 不再逐字节一致。",
                raw_error=json.dumps(
                    {
                        "stdout": stdout_snapshot,
                        "log": log_snapshot,
                    },
                    ensure_ascii=False,
                ),
            )
        verified_files = _mapping(provenance.get("verified_files"))
        verified_files["vina_stdout"] = copy.deepcopy(stdout_snapshot)
        verified_files["vina_log"] = copy.deepcopy(log_snapshot)
        provenance["verified_files"] = verified_files
    parsed_log_scores = parse_vina_log_text(
        log_path.read_text(encoding="utf-8", errors="strict")
    )
    if isinstance(parsed_log_scores, dict):
        return parsed_log_scores
    log_scores = _list_of_mappings(parsed_log_scores)
    table_binding, table_error = _validate_hydrated_result_table(
        root,
        run_id,
        metadata,
        log_scores,
        waters_manifest,
    )
    if table_error:
        return table_error
    assert table_binding is not None
    provenance["result_table_validation"] = copy.deepcopy(
        table_binding["energy_range_validation"]
    )

    score_contract_present = _recorded_artifact_contract_present(
        metadata,
        RUN_SCORE_ARTIFACT_KEYS,
    )
    if is_modern and not score_contract_present:
        scores = copy.deepcopy(log_scores)
        score_source = "verified_frozen_log"
    else:
        scores_payload = load_scores_csv(str(root), run_id)
        if not scores_payload.get("ok"):
            score_error = _mapping(scores_payload.get("error"))
            if (
                is_modern
                or score_error.get("code") != "SCORES_CSV_NOT_FOUND"
            ):
                return scores_payload
            scores = copy.deepcopy(log_scores)
            score_source = "verified_frozen_log"
        else:
            scores = _list_of_mappings(scores_payload.get("scores"))
            score_source = "verified_scores_csv"

    frozen_verbosity = int(table_binding["verbosity"])
    pose_records = _mapping(table_binding["pose_records"])
    log_score_records = _mapping(table_binding["log_records"])
    log_modes = list(table_binding["log_modes"])
    output_modes = list(table_binding["output_modes"])
    score_modes = [
        int(item.get("mode") or 0)
        for item in scores
        if isinstance(item.get("mode"), int)
        and not isinstance(item.get("mode"), bool)
    ]
    if (
        len(score_modes) != len(scores)
        or len(set(score_modes)) != len(score_modes)
        or (
            score_modes != log_modes
            and (is_modern or score_modes != output_modes)
        )
        or len(log_modes) != len(log_scores)
        or len(log_score_records) != len(log_scores)
        or output_modes != log_modes[: len(output_modes)]
    ):
        return _error(
            "HYDRATED_SCORE_MODE_BINDING_MISMATCH",
            "评分表与水分子分类记录的构象编号不一致。",
            raw_error=json.dumps(
                {
                    "score_modes": score_modes,
                    "log_modes": log_modes,
                    "water_modes": output_modes,
                },
                ensure_ascii=False,
            ),
            project_dir=str(root),
            run_id=run_id,
        )

    def values_match(
        left: Any,
        right: Any,
        *,
        absolute_tolerance: float,
    ) -> bool:
        if left is None or right is None:
            return left is None and right is None
        if (
            isinstance(left, bool)
            or isinstance(right, bool)
            or not isinstance(left, (int, float))
            or not isinstance(right, (int, float))
        ):
            return False
        return (
            math.isfinite(float(left))
            and math.isfinite(float(right))
            and math.isclose(
                float(left),
                float(right),
                rel_tol=0.0,
                abs_tol=absolute_tolerance,
            )
        )

    for score in scores:
        mode = int(score.get("mode") or 0)
        log_score = _mapping(log_score_records.get(mode))
        if not all(
            (
                values_match(
                    score.get("affinity_kcal_mol"),
                    log_score.get("affinity_kcal_mol"),
                    absolute_tolerance=0.0,
                ),
                values_match(
                    score.get("rmsd_lb"),
                    log_score.get("rmsd_lb"),
                    absolute_tolerance=0.0,
                ),
                values_match(
                    score.get("rmsd_ub"),
                    log_score.get("rmsd_ub"),
                    absolute_tolerance=0.0,
                ),
            )
        ):
            return _error(
                "HYDRATED_SCORE_LOG_MISMATCH",
                f"scores.csv 中 Mode {mode} 的数值与冻结日志不一致。",
                raw_error=json.dumps(
                    {
                        "score": score,
                        "log_score": log_score,
                    },
                    ensure_ascii=False,
                ),
                project_dir=str(root),
                run_id=run_id,
            )

    scores_by_mode = {
        int(item.get("mode") or 0): item for item in scores
    }
    selected_scores = [
        copy.deepcopy(scores_by_mode.get(mode) or log_score_records[mode])
        for mode in output_modes
    ]
    modes: list[dict[str, Any]] = []
    for score in selected_scores:
        mode = int(score.get("mode") or 0)
        water = copy.deepcopy(pose_records.get(mode) or {})
        log_score = copy.deepcopy(log_score_records.get(mode) or {})
        if not all(
            (
                values_match(
                    score.get("affinity_kcal_mol"),
                    log_score.get("affinity_kcal_mol"),
                    absolute_tolerance=0.0,
                ),
                vina_affinity_serialization_matches(
                    log_score.get("affinity_text"),
                    water.get("raw_affinity"),
                    verbosity=frozen_verbosity,
                ),
                values_match(
                    score.get("rmsd_lb"),
                    log_score.get("rmsd_lb"),
                    absolute_tolerance=0.0,
                ),
                values_match(
                    score.get("rmsd_ub"),
                    log_score.get("rmsd_ub"),
                    absolute_tolerance=0.0,
                ),
                vina_rmsd_serialization_matches(
                    log_score.get("rmsd_lb_text"),
                    water.get("rmsd_lb"),
                    verbosity=frozen_verbosity,
                ),
                vina_rmsd_serialization_matches(
                    log_score.get("rmsd_ub_text"),
                    water.get("rmsd_ub"),
                    verbosity=frozen_verbosity,
                ),
            )
        ):
            return _error(
                "HYDRATED_SCORE_RECORD_MISMATCH",
                f"Mode {mode} 的 raw affinity 或 RMSD 与水分子分类记录不一致。",
                raw_error=json.dumps(
                    {
                        "score": score,
                        "log_score": log_score,
                        "water_pose": water,
                    },
                    ensure_ascii=False,
                ),
                project_dir=str(root),
                run_id=run_id,
            )
        modes.append(
            {
                **score,
                "raw_affinity_kcal_mol": water.get("raw_affinity"),
                "water_summary": {
                    key: water.get(key)
                    for key in (
                        "candidate_water_count",
                        "retained_water_count",
                        "strong_water_count",
                        "weak_water_count",
                        "displaced_water_count",
                    )
                },
                "waters": copy.deepcopy(water.get("waters") or []),
            }
        )
    files = get_run_files_status(str(root), run_id)
    return {
        "ok": True,
        "protocol_id": HYDRATED_PROTOCOL_ID,
        "stability": PROTOCOL_STABILITY,
        "project_dir": str(root),
        "run_id": run_id,
        "metadata": metadata,
        "score_semantics": copy.deepcopy(metadata.get("score_semantics") or {}),
        "scores": selected_scores,
        "score_source": score_source,
        "modes": modes,
        "water_summary": copy.deepcopy(waters_manifest.get("summary") or {}),
        "waters_manifest": waters_manifest,
        "provenance": copy.deepcopy(provenance),
        "grid_coverage": copy.deepcopy(
            provenance.get("grid_coverage")
            or frozen_input_integrity.get("hydrated_grid_coverage")
            or {}
        ),
        "raw_output_file": str(metadata.get("output_file") or ""),
        "retained_output_file": str(metadata.get("pose_file") or ""),
        "water_free_output_file": str(
            _mapping(metadata.get("hydrated_postprocess")).get(
                "water_free_output_file"
            )
            or ""
        ),
        "files": copy.deepcopy(files.get("files") or []),
        "warnings": (
            [str(provenance.get("warning") or "")]
            if provenance.get("status") == "legacy_partial"
            else []
        ),
        "message": (
            "已按完整冻结溯源合同读取 raw AD4 affinity 与逐构象水分子分类；"
            "未计算处理后 affinity。"
            if provenance.get("status") == "verified"
            else
            "已按旧版只读兼容模式读取可复核证据；缺失溯源未由当前项目状态补写。"
        ),
        "error": None,
    }


def _sanitized_command_for_report(command: Any) -> str:
    if not isinstance(command, list):
        return "未记录"
    sanitized: list[str] = []
    for item in command:
        text = str(item)
        path = Path(text)
        sanitized.append(
            _private_tool_path(text)
            if path.is_absolute() or bool(path.drive)
            else text
        )
    return json.dumps(sanitized, ensure_ascii=False)


def build_hydrated_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    results = load_hydrated_results(project_dir, run_id)
    if not results.get("ok"):
        return results
    metadata = _mapping(results.get("metadata"))
    provenance = _mapping(results.get("provenance"))
    summary = _mapping(results.get("water_summary"))
    grid_coverage = _mapping(results.get("grid_coverage"))
    requested_grid_size = _mapping(
        grid_coverage.get("requested_box_size_angstrom")
    )
    effective_grid_size = _mapping(
        grid_coverage.get("effective_grid_size_angstrom")
    )
    axis_coverage = _mapping(grid_coverage.get("axis_coverage"))
    output_normalization = _mapping(metadata.get("output_normalization"))
    score_rows = [
        "| 模式 | Raw AD4 affinity (kcal/mol) | 强水 | 弱水 | 置换水 |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in _list_of_mappings(results.get("modes")):
        water = _mapping(item.get("water_summary"))
        score_rows.append(
            "| {mode} | {score} | {strong} | {weak} | {displaced} |".format(
                mode=item.get("mode"),
                score=item.get("raw_affinity_kcal_mol"),
                strong=water.get("strong_water_count"),
                weak=water.get("weak_water_count"),
                displaced=water.get("displaced_water_count"),
            )
        )
    if provenance.get("status") == "verified":
        verified_files = _mapping(provenance.get("verified_files"))
        toolchain = _mapping(provenance.get("toolchain"))
        commands = _mapping(provenance.get("commands"))
        command_hashes = _mapping(provenance.get("command_sha256"))
        water_map = _mapping(provenance.get("water_map"))
        water_parameters = _mapping(water_map.get("parameters"))
        gpf_record = _mapping(verified_files.get("gpf"))
        postprocess = _mapping(provenance.get("postprocess"))
        postprocess_parameters = _mapping(postprocess.get("parameters"))
        manifest_hashes = _mapping(provenance.get("manifest_hashes"))
        artifact_hashes = _mapping(provenance.get("artifact_sha256"))
        identity_rows = [
            "| 对象 | run 内冻结文件 | 大小（bytes） | SHA256 |",
            "| --- | --- | ---: | --- |",
        ]
        for label, key in (
            ("原始配体", "raw_ligand"),
            ("加氢配体", "added_h_ligand"),
            ("水合 PDBQT 配体", "hydrated_ligand"),
            ("刚性受体", "receptor"),
        ):
            record = _mapping(verified_files.get(key))
            identity_rows.append(
                f"| {label} | `{record.get('relative_path') or ''}` | "
                f"{record.get('size_bytes')} | `{record.get('sha256') or ''}` |"
            )
        tool_rows = [
            "| 工具 | 版本 | 来源 | 路径 | 大小（bytes） | SHA256 |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
        for label, key in (
            ("Python", "python"),
            ("RDKit", "rdkit"),
            ("Meeko", "meeko"),
            ("AutoGrid4", "autogrid"),
            ("AutoDock Vina", "vina"),
        ):
            record = _mapping(toolchain.get(key))
            tool_rows.append(
                f"| {label} | {record.get('version') or '未记录'} | "
                f"{record.get('source') or '未记录'} | "
                f"`{_private_tool_path(record.get('path'))}` | "
                f"{record.get('size_bytes')} | `{record.get('sha256') or ''}` |"
            )
        map_rows = [
            "| Map | 角色 | 大小（bytes） | SHA256 |",
            "| --- | --- | ---: | --- |",
        ]
        for name, role, key in (
            ("OA", "W BEST 来源", "map:receptor.OA.map"),
            ("HD", "W BEST 来源", "map:receptor.HD.map"),
            ("W", "BEST 输出", "map:receptor.W.map"),
        ):
            record = _mapping(verified_files.get(key))
            map_rows.append(
                f"| {name} | {role} | {record.get('size_bytes')} | "
                f"`{record.get('sha256') or ''}` |"
            )
        manifest_rows = [
            "| 证据 | SHA256 |",
            "| --- | --- |",
            *[
                f"| {key} | `{value}` |"
                for key, value in sorted(manifest_hashes.items())
            ],
            (
                "| config_snapshot | "
                f"`{_mapping(provenance.get('config')).get('sha256') or ''}` |"
            ),
            (
                "| command_preview | "
                f"`{_mapping(provenance.get('command_preview')).get('sha256') or ''}` |"
            ),
            (
                "| AutoGrid GPF | "
                f"`{_mapping(verified_files.get('gpf')).get('sha256') or ''}` |"
            ),
            *[
                f"| command:{key} | `{value}` |"
                for key, value in sorted(command_hashes.items())
            ],
        ]
        artifact_rows = [
            "| Artifact key | SHA256 |",
            "| --- | --- |",
            *[
                f"| `{key}` | `{value}` |"
                for key, value in sorted(artifact_hashes.items())
            ],
        ]
        provenance_lines = [
            "## 冻结溯源复核",
            "",
            (
                "- 状态：完整并已验证（"
                f"`schema v{provenance.get('schema_version')}` / "
                f"`{provenance.get('contract_id')}`）"
            ),
            "- 证据来源：仅当前 run 的冻结快照、manifest 与 metadata；未读取活动项目输入补写历史。",
            "- 报告读取模式：只读；已重放水分子后处理并逐字节复核输出。",
            "",
            "### 分子输入身份",
            "",
            *identity_rows,
            "",
            "### 工具链身份",
            "",
            *tool_rows,
            "",
            "### OA / HD / W map",
            "",
            (
                "- GPF："
                f"`{gpf_record.get('relative_path') or ''}`，"
                f"{gpf_record.get('size_bytes')} bytes，"
                f"SHA256 `{gpf_record.get('sha256') or ''}`。"
            ),
            "",
            *map_rows,
            "",
            (
                "- W map 方法："
                f"`{water_map.get('method') or ''}`，"
                f"mode={water_parameters.get('mode')}，"
                f"weight={water_parameters.get('weight')}，"
                f"entropy={water_parameters.get('entropy')}，"
                f"OA weight={water_parameters.get('oa_weight')}，"
                f"HD weight={water_parameters.get('hd_weight')}。"
            ),
            "",
            "### 水分子后处理合同",
            "",
            (
                "- 算法："
                f"`{postprocess.get('method') or ''}`；"
                f"配体水与受体重叠距离严格小于 "
                f"{postprocess_parameters.get('overlap_distance_angstrom')} Å 时置换。"
            ),
            (
                "- W map 采样：以最近网格点为中心，按 "
                f"±{postprocess_parameters.get('map_sample_radius_angstrom')} Å "
                f"（{postprocess_parameters.get('map_sample_radius_steps')} 个网格步长）"
                "的裁剪索引立方体取最小值。"
            ),
            (
                "- 分类阈值：强水严格小于 "
                f"{postprocess_parameters.get('strong_threshold')}；弱水严格小于 "
                f"{postprocess_parameters.get('weak_threshold')}；其余置换。"
            ),
            "- affinity 与构象顺序保持 Vina 原始结果，不计算处理后 affinity。",
            "",
            "### 命令与配置",
            "",
            f"- 配体准备：`{_sanitized_command_for_report(commands.get('ligand_preparation'))}`",
            f"- AutoGrid4：`{_sanitized_command_for_report(commands.get('autogrid'))}`",
            f"- Vina：`{_sanitized_command_for_report(commands.get('vina'))}`",
            "",
            *manifest_rows,
            "",
            "### Artifact 哈希索引",
            "",
            *artifact_rows,
            "",
        ]
    else:
        missing = [
            str(item)
            for item in provenance.get("missing_evidence") or []
        ]
        provenance_lines = [
            "## 冻结溯源复核",
            "",
            "> **旧版/部分溯源，只读。** "
            "该历史 run 创建于完整冻结溯源合同之前；仅显示已记录且通过哈希复核的证据。",
            "",
            "- 缺失证据不会从当前 project.json、活动输入或当前工具环境推断或补写。",
            *[f"- 缺失：{item}" for item in missing],
            "",
        ]
    if grid_coverage:
        provenance_grid = _mapping(provenance.get("grid"))
        requested_box = _mapping(provenance_grid.get("requested_box"))
        requested_center = _mapping(
            requested_box.get("center") or provenance_grid.get("center")
        )
        coverage_lines = [
            (
                "- 请求 Box 中心（Å）："
                f"X={requested_center.get('x')}，"
                f"Y={requested_center.get('y')}，"
                f"Z={requested_center.get('z')}"
            ),
            (
                "- 请求 Box 尺寸（Å）："
                f"X={requested_grid_size.get('x')}，"
                f"Y={requested_grid_size.get('y')}，"
                f"Z={requested_grid_size.get('z')}"
            ),
            (
                "- 实际网格尺寸（Å）："
                f"X={effective_grid_size.get('x')}，"
                f"Y={effective_grid_size.get('y')}，"
                f"Z={effective_grid_size.get('z')}"
            ),
            (
                "- 各轴最小余量（Å）："
                f"X={_mapping(axis_coverage.get('x')).get('minimum_margin_angstrom')}，"
                f"Y={_mapping(axis_coverage.get('y')).get('minimum_margin_angstrom')}，"
                f"Z={_mapping(axis_coverage.get('z')).get('minimum_margin_angstrom')}"
            ),
            "- 闭区间覆盖复核：通过",
        ]
        grid = provenance_grid
        grid_points = _mapping(grid.get("grid_points"))
        if grid:
            coverage_lines.extend(
                [
                    (
                        "- AutoGrid npts："
                        f"X={grid_points.get('x')}，Y={grid_points.get('y')}，"
                        f"Z={grid_points.get('z')}"
                    ),
                    f"- AutoGrid spacing：{grid.get('spacing')} Å",
                ]
            )
    else:
        coverage_lines = [
            "- 该旧版 run 没有可完整复算的 Box/网格覆盖合同；不声明覆盖通过。",
        ]
    report_lines = [
        "# DockStart 实验性水合 AD4 对接报告",
        "",
        f"- Run：`{run_id}`",
        f"- 协议：`{HYDRATED_PROTOCOL_ID}`（Experimental）",
        f"- Vina：{metadata.get('vina_version') or '未记录'}",
        f"- Raw 输出：`{results.get('raw_output_file') or ''}`",
        *(
            [
                (
                    "- Vina 原始字节输出："
                    f"`{output_normalization.get('raw_output_file') or ''}`"
                ),
                (
                    "- 输出标准化："
                    f"{output_normalization.get('status') or '未记录'}；"
                    f"方法={output_normalization.get('method') or '未记录'}；"
                    f"移除 NUL={output_normalization.get('nul_bytes_removed') or 0}"
                ),
                (
                    "- Vina 原始输出 SHA256："
                    f"`{output_normalization.get('source_sha256') or ''}`"
                ),
                (
                    "- 标准 PDBQT SHA256："
                    f"`{output_normalization.get('normalized_sha256') or ''}`"
                ),
            ]
            if output_normalization
            else []
        ),
        f"- 保留水输出：`{results.get('retained_output_file') or ''}`",
        f"- 去水配体输出：`{results.get('water_free_output_file') or ''}`",
        "",
        *provenance_lines,
        "## 冻结 Box 与实际网格覆盖",
        "",
        *coverage_lines,
        "",
        "## 评分语义",
        "",
        "表中的 affinity 是 Vina 使用 AD4 maps 对含显式 W 原子的原始构象给出的原始评分。"
        "水分子过滤只用于结构解释，不会产生或替换 affinity。",
        "",
        "**处理后评分未计算。**",
        "",
        "该协议不用于虚拟筛选，也不支持跨配体、跨评分函数或跨协议直接比较分值。",
        "",
        "## 水分子汇总",
        "",
        f"- 原始候选水：{summary.get('raw_water_count', 0)}",
        f"- 保留水：{summary.get('retained_water_count', 0)}",
        f"- 强水：{summary.get('strong_water_count', 0)}",
        f"- 弱水：{summary.get('weak_water_count', 0)}",
        f"- 置换水：{summary.get('displaced_water_count', 0)}",
        "",
        "## 构象结果",
        "",
        *score_rows,
        "",
        "## 科学边界",
        "",
        "Docking score 仅供结构结合趋势参考，不能替代实验验证。",
        "水分子分类依赖当前受体、Box、AutoGrid W map 与固定阈值，只用于本次运行的结构解释。",
        "",
    ]
    return {
        "ok": True,
        "protocol_id": HYDRATED_PROTOCOL_ID,
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "run_id": run_id,
        "metadata": metadata,
        "report_text": "\n".join(report_lines),
        "message": "实验性水合 AD4 Markdown 报告内容已生成。",
        "error": None,
    }


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "preflight":
        _print_json(get_hydrated_run_preflight(sys.argv[2]))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "prepare":
        result = prepare_hydrated_run(sys.argv[2])
        _print_json(result)
        return 0 if result.get("ok") else 1
    if len(sys.argv) == 4 and sys.argv[1] == "results":
        result = load_hydrated_results(sys.argv[2], sys.argv[3])
        _print_json(result)
        return 0 if result.get("ok") else 1
    _print_json(
        _error(
            "HYDRATED_RUN_COMMAND_INVALID",
            "水合对接运行命令无效。",
            suggestion=(
                "使用 preflight PROJECT、prepare PROJECT 或 results PROJECT RUN_ID。"
            ),
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_hydrated_markdown_report",
    "get_hydrated_run_preflight",
    "load_hydrated_results",
    "prepare_hydrated_run",
]
