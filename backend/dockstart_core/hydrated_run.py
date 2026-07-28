"""Project-scoped run orchestration for experimental hydrated AD4 docking."""

from __future__ import annotations

import copy
import json
import math
import re
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from adapters import vina_adapter
from dockstart_core import __version__
from dockstart_core import hydrated_maps as hydrated_map_core
from dockstart_core.persistence import atomic_write_json, atomic_write_text
from dockstart_core.project import (
    HYDRATED_PROTOCOL_ID,
    HYDRATED_RETAINED_OUTPUT_NAME,
    HYDRATED_WATER_FREE_OUTPUT_NAME,
    HYDRATED_WATERS_MANIFEST_NAME,
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
    _run_output_file,
    _safe_run_directory,
    _system_snapshot,
    _tool_hash_snapshot,
    _validate_ad4_maps_post_run_integrity,
    _with_artifact_hashes,
    _write_run_metadata,
    analyze_vina_run_results,
    get_next_run_id,
    get_run_files_status,
    load_project,
    load_run_metadata,
    load_scores_csv,
    save_project,
    validate_box_params,
    validate_vina_params,
)
from dockstart_core.settings import load_settings

MINIMUM_VINA_VERSION = "1.2.0"
PROTOCOL_STABILITY = "experimental"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


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
    if (
        str(after_source.get("sha256") or "").lower() != expected
        or str(target.get("sha256") or "").lower() != expected
        or int(target.get("size_bytes") or -1) != int(before.get("size_bytes") or -2)
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
    if not hydrated_status.get("maps_ready"):
        return _error(
            "HYDRATED_MAPS_NOT_READY",
            "水合 affinity maps 尚未准备完成或已失效。",
            raw_error="；".join(str(item) for item in hydrated_status.get("maps_issues") or []),
            suggestion="请重新生成与当前受体、Box 和水合配体一致的 maps。",
            project_dir=str(root),
        )

    ligand_manifest = _mapping(hydrated_status.get("manifest"))
    maps_manifest = _mapping(hydrated_status.get("maps_manifest"))
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
    hydrated_ligand_types = [
        str(item)
        for item in maps.get("ligand_atom_types")
        if isinstance(item, str)
    ] if isinstance(maps.get("ligand_atom_types"), list) else []
    autogrid_ligand_types = [
        str(item)
        for item in maps.get("autogrid_ligand_atom_types")
        if isinstance(item, str)
    ] if isinstance(maps.get("autogrid_ligand_atom_types"), list) else []
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
        set(required_files) != set(records_by_name)
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

    box_validation = validate_box_params(asdict(project.box))
    if not box_validation.get("ok"):
        return box_validation
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
        "vina_detection": detection,
        "vina_binary": vina_binary,
        "box": box_validation.get("box") or asdict(project.box),
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
                "ad4_maps": {
                    "manifest": maps_manifest_snapshot,
                    "prefix": maps_prefix,
                    "files": maps_file_snapshots,
                    "source_manifest": prerequisites["maps_manifest_file"],
                },
                "hydrated": {
                    "ligand_manifest": ligand_manifest_snapshot,
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
                "maps_manifest": maps_manifest_snapshot["sha256"],
                "maps": {
                    str(item.get("name") or ""): str(item.get("sha256") or "")
                    for item in maps_file_snapshots
                },
                "hydrated_ligand_manifest": ligand_manifest_snapshot["sha256"],
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
                "ligand_atom_types": copy.deepcopy(
                    maps.get("ligand_atom_types") or []
                ),
            },
            "hydrated": {
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "ligand_preparation_manifest": prerequisites[
                    "ligand_manifest_file"
                ],
                "ligand_preparation_manifest_snapshot": ligand_manifest_relative,
                "maps_manifest": prerequisites["maps_manifest_file"],
                "maps_manifest_snapshot": maps_manifest_relative,
                "water_map": copy.deepcopy(maps.get("water_map") or {}),
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
        or sorted(modes) != list(range(1, len(modes) + 1))
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
    frozen_input_integrity = _validate_ad4_maps_post_run_integrity(
        str(root),
        run_id,
        metadata,
    )
    if not frozen_input_integrity.get("ok"):
        return frozen_input_integrity
    waters_manifest, waters_error = _load_waters_manifest(root, run_id, metadata)
    if waters_error:
        return waters_error
    assert waters_manifest is not None
    scores_payload = load_scores_csv(str(root), run_id)
    if not scores_payload.get("ok"):
        analyzed = analyze_vina_run_results(str(root), run_id)
        if not analyzed.get("ok"):
            return analyzed
        scores = _list_of_mappings(analyzed.get("scores"))
        metadata = _mapping(analyzed.get("metadata"))
    else:
        scores = _list_of_mappings(scores_payload.get("scores"))
    pose_records = {
        int(item.get("mode") or 0): item
        for item in _list_of_mappings(waters_manifest.get("poses"))
        if int(item.get("mode") or 0) > 0
    }
    score_modes = [
        int(item.get("mode") or 0)
        for item in scores
        if isinstance(item.get("mode"), int)
        and not isinstance(item.get("mode"), bool)
    ]
    if (
        len(score_modes) != len(scores)
        or len(set(score_modes)) != len(score_modes)
        or set(score_modes) != set(pose_records)
    ):
        return _error(
            "HYDRATED_SCORE_MODE_BINDING_MISMATCH",
            "评分表与水分子分类记录的构象编号不一致。",
            raw_error=json.dumps(
                {
                    "score_modes": score_modes,
                    "water_modes": sorted(pose_records),
                },
                ensure_ascii=False,
            ),
            project_dir=str(root),
            run_id=run_id,
        )

    def values_match(left: Any, right: Any) -> bool:
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
                abs_tol=0.00051,
            )
        )

    modes: list[dict[str, Any]] = []
    for score in scores:
        mode = int(score.get("mode") or 0)
        water = copy.deepcopy(pose_records.get(mode) or {})
        if not all(
            (
                values_match(
                    score.get("affinity_kcal_mol"),
                    water.get("raw_affinity"),
                ),
                values_match(score.get("rmsd_lb"), water.get("rmsd_lb")),
                values_match(score.get("rmsd_ub"), water.get("rmsd_ub")),
            )
        ):
            return _error(
                "HYDRATED_SCORE_RECORD_MISMATCH",
                f"Mode {mode} 的 raw affinity 或 RMSD 与水分子分类记录不一致。",
                raw_error=json.dumps(
                    {"score": score, "water_pose": water},
                    ensure_ascii=False,
                ),
                project_dir=str(root),
                run_id=run_id,
            )
        modes.append(
            {
                **score,
                "raw_affinity_kcal_mol": score.get("affinity_kcal_mol"),
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
        "scores": scores,
        "modes": modes,
        "water_summary": copy.deepcopy(waters_manifest.get("summary") or {}),
        "waters_manifest": waters_manifest,
        "raw_output_file": str(metadata.get("output_file") or ""),
        "retained_output_file": str(metadata.get("pose_file") or ""),
        "water_free_output_file": str(
            _mapping(metadata.get("hydrated_postprocess")).get(
                "water_free_output_file"
            )
            or ""
        ),
        "files": copy.deepcopy(files.get("files") or []),
        "message": "已读取 raw AD4 affinity 与逐构象水分子分类；未计算处理后 affinity。",
        "error": None,
    }


def build_hydrated_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    results = load_hydrated_results(project_dir, run_id)
    if not results.get("ok"):
        return results
    metadata = _mapping(results.get("metadata"))
    summary = _mapping(results.get("water_summary"))
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
    report_lines = [
        "# DockStart 实验性水合 AD4 对接报告",
        "",
        f"- Run：`{run_id}`",
        f"- 协议：`{HYDRATED_PROTOCOL_ID}`（Experimental）",
        f"- Vina：{metadata.get('vina_version') or '未记录'}",
        f"- Raw 输出：`{results.get('raw_output_file') or ''}`",
        f"- 保留水输出：`{results.get('retained_output_file') or ''}`",
        f"- 去水配体输出：`{results.get('water_free_output_file') or ''}`",
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
