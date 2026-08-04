"""Deterministic single-receptor/multi-ligand virtual screening.

This module is deliberately self-contained and does not mutate ``project.json``.
Every job is stored below ``screening/`` and can therefore be removed without
changing the existing single-ligand DockStart workflow.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from adapters import vina_adapter
from dockstart_core.advanced_protocols import (
    ProtocolValidationError,
    execute_mk_export,
    inspect_meeko_ligand_pdbqt,
)
from dockstart_core.persistence import atomic_write_bytes, atomic_write_json, atomic_write_text
from dockstart_core.preparation import get_preparation_tool_status
from dockstart_core.project import (
    _exclusive_file_lock,
    _parse_pdbqt_stats,
    _project_from_dict,
    _project_protocol_id,
    _project_receptor_mode,
    _project_run_mode,
    _project_scoring_protocol,
    _validate_vina_grid_resource,
    load_project,
    validate_vina_params,
    validate_vina_runtime_capabilities,
)
from dockstart_core.screening_models import (
    SCREENING_SCHEMA_VERSION,
    ScreeningItem,
    ScreeningResourceLimits,
    ScreeningStagedInput,
    ScreeningToolSnapshot,
)
from dockstart_core.settings import load_settings


STATE_RELATIVE_PATH = Path("screening", "screening.json")
SCREENING_ROOT = Path("screening")
STAGING_RELATIVE_PATH = Path("screening", "staging")
STAGING_INDEX_RELATIVE_PATH = STAGING_RELATIVE_PATH / "index.json"
STAGING_IMPORTS_RELATIVE_PATH = STAGING_RELATIVE_PATH / "imports"
STAGING_MUTATION_LOCK_RELATIVE_PATH = Path(
    "screening",
    ".staging-mutation.lock",
)
ARCHIVE_RELATIVE_PATH = Path("screening", "archive")
ACTIVE_JOB_NAMES = ("inputs", "attempts", "results")
TERMINAL_SCREENING_STATUSES = frozenset({"completed", "completed_with_failures", "canceled"})
ARCHIVE_ID_PATTERN = re.compile(r"^screening_\d{3,}_\d{14}(?:_\d{2})?$")
SCREENING_ID_PATTERN = re.compile(r"^screening_\d{3,}$")
SCREENING_IMPORT_ID_PATTERN = re.compile(r"^import_\d{6}$")
SCREENING_RECORD_ID_PATTERN = re.compile(r"^record_[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_ARCHIVE_METADATA_BYTES = 32 * 1024 * 1024
SCREENING_EXPORT_MANIFEST_NAME = "dockstart_screening_export.json"
SCREENING_EXPORT_PAYLOAD_DIRECTORY = "payload"
MAX_SCREENING_EXPORT_FILES = 20_000
MAX_SCREENING_EXPORT_MEMBER_BYTES = 512 * 1024 * 1024
MAX_SCREENING_EXPORT_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_SCREENING_EXPORT_PATH_BYTES = 1024
MAX_SCREENING_EXPORT_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_SCREENING_EXPORT_SEMANTIC_JSON_BYTES = 4 * 1024 * 1024
SCREENING_EXPORT_CHUNK_BYTES = 1024 * 1024
SUMMARY_FIELDS = (
    "item_id",
    "ligand_file",
    "status",
    "attempts",
    "best_affinity_kcal_mol",
    "best_output_file",
    "best_output_sha256",
    "best_output_size_bytes",
    "error",
)
SCORE_ROW = re.compile(
    r"^\s*(\d+)\s+(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s+(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$",
)

Runner = Callable[..., Any]

SCREENING_VINA_DEFAULTS: dict[str, Any] = {
    "max_evals": 0,
    "min_rmsd": 1.0,
    "spacing": 0.375,
    "verbosity": 1,
    "no_refine": False,
    "force_even_voxels": False,
}
SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION = 2
SCREENING_LIBRARY_PREPARATION_PROFILE = (
    "dockstart_screening_rdkit_meeko_fail_closed_v2"
)
SCREENING_CHEMICAL_FACTS_SCHEMA_VERSION = 1
SCREENING_CHEMICAL_FACTS_PROFILE = (
    "rdkit_source_formal_charge_heavy_atoms_strict_rotatable_bonds_v1"
)
SCREENING_HYDROGEN_POLICY = "rdkit_add_hs_preserve_source_indices_v1"
SCREENING_RESULT_SDF_SCHEMA_VERSION = 1
SCREENING_RESULT_TOPOLOGY_VALIDATOR_SCHEMA_VERSION = 1
SCREENING_RESULT_SDF_TOOLCHAIN_PROFILE = (
    "meeko_export_rdkit_heavy_graph_validation_v1"
)


class _ArchiveValidationError(ValueError):
    """Internal archive validation failure with a stable public error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class _ScreeningIntegrityError(ValueError):
    """A frozen screening input or tool no longer matches its recorded identity."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {
        "code": code,
        "title": message,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }
    if details is not None:
        error["details"] = details
    return {
        "ok": False,
        "error": error,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("项目路径不是目录。")
    return root


def _serialized_staging_mutation(function: Callable[..., dict[str, Any]]):
    """Serialize staging/index mutations across GUI and CLI processes."""

    @wraps(function)
    def wrapped(
        project_dir: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            root = _project_root(project_dir)
            lock_path = root / STAGING_MUTATION_LOCK_RELATIVE_PATH
            with _exclusive_file_lock(lock_path):
                return function(str(root), *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - public workflow boundary.
            return _error(
                "SCREENING_STAGING_LOCK_ERROR",
                "批量配体 staging 协调失败。",
                str(exc),
                "请确认项目目录可写且没有异常的链接或重解析点后重试。",
            )

    return wrapped


def _project_file(root: Path, value: str | Path, *, label: str) -> tuple[Path, str]:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于项目目录内。") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} 不是文件。")
    if resolved.suffix.lower() != ".pdbqt":
        raise ValueError(f"{label} 必须是 PDBQT 文件。")
    if resolved.stat().st_size <= 0:
        raise ValueError(f"{label} 为空文件。")
    return resolved, relative.as_posix()


def _project_artifact_file(
    root: Path,
    value: str | Path,
    *,
    label: str,
) -> tuple[Path, str]:
    """Resolve a non-PDBQT project artifact without weakening containment."""

    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于项目目录内。") from exc
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ValueError(f"{label} 不是可读取的非空文件。")
    return resolved, relative.as_posix()


def _pdbqt_file(value: str | Path, *, label: str) -> Path:
    resolved = Path(value).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} 不是文件。")
    if resolved.suffix.lower() != ".pdbqt":
        raise ValueError(f"{label} 必须是 PDBQT 文件。")
    if resolved.stat().st_size <= 0:
        raise ValueError(f"{label} 为空文件。")
    return resolved


def _vina_executable(value: str) -> str:
    supplied = value.strip()
    if not supplied:
        raise ValueError("未提供 AutoDock Vina 可执行文件。")
    discovered = shutil.which(supplied) if not Path(supplied).is_absolute() else None
    candidate = Path(discovered or supplied).expanduser().resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("AutoDock Vina 路径不是文件。")
    return str(candidate)


def _unused_detection_path(parent: Path) -> str:
    """Return a definitely absent bundled-path override for explicit detection."""

    for index in range(1000):
        candidate = parent / f".dockstart-explicit-vina-{index}.disabled"
        if not candidate.exists():
            return str(candidate)
    raise RuntimeError("无法为显式 Vina 路径建立隔离检测。")


def _resolve_vina_tool(
    vina_path: str | None,
) -> tuple[ScreeningToolSnapshot, dict[str, Any]]:
    requested = str(vina_path or "").strip()
    if requested:
        executable = _vina_executable(requested)
        detection = vina_adapter.detect(
            executable,
            bundled_path=_unused_detection_path(Path(executable).parent),
        )
        source = "explicit"
    else:
        settings = load_settings()
        detection = vina_adapter.detect(settings.tool_paths.vina)
        if detection.status != "ok" or not detection.path:
            raise ValueError(detection.message or "未检测到可用的 AutoDock Vina。")
        executable = _vina_executable(detection.path)
        source = str(detection.source or "auto")

    if detection.status != "ok" or not detection.path:
        detail = detection.raw_error or detection.message or "AutoDock Vina 检测失败。"
        raise ValueError(detail)
    capabilities = (
        json.loads(json.dumps(detection.capabilities, ensure_ascii=False))
        if isinstance(getattr(detection, "capabilities", None), dict)
        else {}
    )
    return (
        ScreeningToolSnapshot(
            path=executable,
            version=str(detection.version or ""),
            source=source,
            sha256=_sha256(Path(executable)),
            detection_status=str(detection.status or "unknown"),
        ),
        capabilities,
    )


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数字，不能是布尔值。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字。")
    return number


def _strict_integer(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数，不能是布尔值或其他类型。")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间。")
    return value


def _validated_resource_limits(
    value: dict[str, Any] | ScreeningResourceLimits | None,
) -> ScreeningResourceLimits:
    """Parse all frozen limits against application hard ceilings."""

    defaults = ScreeningResourceLimits()
    if value is None:
        source: dict[str, Any] = {}
    elif isinstance(value, ScreeningResourceLimits):
        source = value.to_dict()
    elif isinstance(value, dict):
        source = dict(value)
    else:
        raise ValueError("resource_limits 必须是对象。")

    allowed = set(ScreeningResourceLimits.__dataclass_fields__)
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise ValueError(
            "resource_limits 包含未知字段：" + ", ".join(unknown) + "。",
        )

    normalized: dict[str, Any] = {}
    integer_minimums = {
        "max_ligands": 1,
        "max_retries": 0,
        "max_cpu": 1,
        "max_exhaustiveness": 1,
        "max_num_modes": 1,
        "max_ligand_bytes": 1,
        "max_staged_file_bytes": 1,
        "max_total_input_bytes": 1,
    }
    for name, minimum in integer_minimums.items():
        hard_maximum = int(getattr(defaults, name))
        normalized[name] = _strict_integer(
            source.get(name, hard_maximum),
            f"resource_limits.{name}",
            minimum=minimum,
            maximum=hard_maximum,
        )

    raw_box_edge = source.get(
        "max_box_edge_angstrom",
        defaults.max_box_edge_angstrom,
    )
    box_edge = _finite_number(
        raw_box_edge,
        "resource_limits.max_box_edge_angstrom",
    )
    if box_edge <= 0 or box_edge > defaults.max_box_edge_angstrom:
        raise ValueError(
            "resource_limits.max_box_edge_angstrom 必须大于 0 且不超过 "
            f"{defaults.max_box_edge_angstrom:g} Å。",
        )
    normalized["max_box_edge_angstrom"] = box_edge
    return ScreeningResourceLimits(**normalized)


def _resource_limits_sha256(limits: ScreeningResourceLimits) -> str:
    canonical = json.dumps(
        limits.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_frozen_resource_limits_hash(
    state: dict[str, Any],
    limits: ScreeningResourceLimits,
) -> str:
    if "resource_limits_sha256" not in state:
        return "legacy_unverified"
    recorded = state.get("resource_limits_sha256")
    if not isinstance(recorded, str) or SHA256_PATTERN.fullmatch(recorded) is None:
        raise ValueError("resource_limits_sha256 无效。")
    actual = _resource_limits_sha256(limits)
    if recorded.lower() != actual:
        raise ValueError("冻结 resource_limits 的 SHA256 与记录不一致。")
    return "verified"


def _validate_screening_resource_state(
    state: dict[str, Any],
    limits: ScreeningResourceLimits,
) -> dict[str, int]:
    items = state.get("items")
    if not isinstance(items, list):
        raise ValueError("screening.json 的 items 必须是数组。")
    item_count = len(items)
    if item_count < 1 or item_count > limits.max_ligands:
        raise ValueError(
            f"配体数量必须在 1 到资源上限 {limits.max_ligands} 之间。",
        )
    max_retries = _strict_integer(
        state.get("max_retries"),
        "max_retries",
        minimum=0,
        maximum=limits.max_retries,
    )
    top_n = _strict_integer(
        state.get("top_n"),
        "top_n",
        minimum=1,
        maximum=limits.max_ligands,
    )

    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    receptor = (
        inputs.get("receptor")
        if isinstance(inputs.get("receptor"), dict)
        else None
    )
    if receptor is None:
        raise ValueError("screening.json 缺少冻结受体记录。")
    receptor_size = _strict_integer(
        receptor.get("size_bytes"),
        "冻结受体 size_bytes",
        minimum=1,
        maximum=limits.max_total_input_bytes,
    )
    total_input_bytes = receptor_size
    ad4_maps = (
        inputs.get("ad4_maps")
        if isinstance(inputs.get("ad4_maps"), dict)
        else {}
    )
    map_files = (
        ad4_maps.get("files")
        if isinstance(ad4_maps.get("files"), list)
        else []
    )
    if str(state.get("scoring_protocol") or "vina") == "ad4_maps":
        if not map_files:
            raise ValueError("screening.json 缺少冻结 AutoDock4 maps 文件。")
        for index, record in enumerate(map_files, start=1):
            if not isinstance(record, dict):
                raise ValueError(f"冻结 AD4 map #{index} 记录无效。")
            total_input_bytes += _strict_integer(
                record.get("size_bytes"),
                f"冻结 AD4 map #{index} size_bytes",
                minimum=1,
                maximum=limits.max_total_input_bytes,
            )
            if total_input_bytes > limits.max_total_input_bytes:
                raise ValueError("冻结受体、maps 与配体总大小超过资源上限。")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"items[{index - 1}] 必须是对象。")
        item_id = str(item.get("item_id") or f"#{index}")
        ligand_size = _strict_integer(
            item.get("size_bytes"),
            f"冻结配体 {item_id} size_bytes",
            minimum=1,
            maximum=limits.max_ligand_bytes,
        )
        total_input_bytes += ligand_size
        if total_input_bytes > limits.max_total_input_bytes:
            raise ValueError(
                "冻结受体与配体总大小超过 "
                f"resource_limits.max_total_input_bytes={limits.max_total_input_bytes}。",
            )
    return {
        "max_retries": max_retries,
        "top_n": top_n,
        "item_count": item_count,
        "total_input_bytes": total_input_bytes,
    }


def _validate_settings(
    box: dict[str, Any],
    vina: dict[str, Any],
    limits: ScreeningResourceLimits,
    *,
    allow_legacy_energy_range_zero: bool = False,
) -> tuple[dict[str, float], dict[str, Any]]:
    normalized_box: dict[str, float] = {}
    for axis in "xyz":
        normalized_box[f"center_{axis}"] = _finite_number(box.get(f"center_{axis}"), f"center_{axis}")
        edge = _finite_number(box.get(f"size_{axis}"), f"size_{axis}")
        if edge <= 0 or edge > limits.max_box_edge_angstrom:
            raise ValueError(
                f"size_{axis} 必须大于 0 且不超过 {limits.max_box_edge_angstrom:g} Å。",
            )
        normalized_box[f"size_{axis}"] = edge

    def integer(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = vina.get(name, default)
        if isinstance(raw, bool):
            raise ValueError(f"{name} 必须是整数。")
        if isinstance(raw, int):
            parsed_value = raw
        elif isinstance(raw, float):
            if not math.isfinite(raw) or not raw.is_integer():
                raise ValueError(f"{name} 必须是整数，不能静默截断小数。")
            parsed_value = int(raw)
        else:
            try:
                parsed_value = int(str(raw).strip(), 10)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 必须是整数。") from exc
        if parsed_value < minimum or parsed_value > maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间。")
        return parsed_value

    scoring = str(vina.get("scoring") or "vina").strip().lower()
    if scoring not in {"vina", "vinardo", "ad4"}:
        raise ValueError("批量筛选仅支持 vina、vinardo 或标准 AD4 maps 评分。")
    exhaustiveness = integer(
        "exhaustiveness",
        8,
        1,
        limits.max_exhaustiveness,
    )
    num_modes = integer("num_modes", 9, 1, limits.max_num_modes)
    cpu = integer("cpu", 1, 1, limits.max_cpu)
    energy_range = _finite_number(vina.get("energy_range", 3), "energy_range")
    if energy_range <= 0 and not (
        allow_legacy_energy_range_zero and energy_range == 0
    ):
        raise ValueError("energy_range 必须大于 0。")
    if energy_range > 20:
        raise ValueError("energy_range 不能大于 20。")
    seed_value = vina.get("seed")
    seed = (
        None
        if seed_value in (None, "")
        else integer("seed", 0, -2_147_483_648, 2_147_483_647)
    )

    vina_validation = validate_vina_params(
        {
            **vina,
            "scoring": "vina" if scoring == "ad4" else scoring,
            "exhaustiveness": exhaustiveness,
            "num_modes": num_modes,
            "energy_range": energy_range if energy_range > 0 else 3,
            "cpu": cpu,
            "seed": seed,
            "unbound_energy": None,
        }
    )
    if not vina_validation.get("ok"):
        error = vina_validation.get("error") or {}
        message = str(error.get("message") or "Vina 参数无效。")
        code = str(error.get("code") or "")
        raise ValueError(f"{message}（{code}）" if code else message)
    parsed = vina_validation["vina"]

    normalized_vina = {
        "scoring": scoring,
        "exhaustiveness": exhaustiveness,
        "max_evals": int(parsed["max_evals"]),
        "num_modes": num_modes,
        "min_rmsd": float(parsed["min_rmsd"]),
        "energy_range": energy_range,
        "spacing": float(parsed["spacing"]),
        "verbosity": int(parsed["verbosity"]),
        "no_refine": bool(parsed["no_refine"]),
        "force_even_voxels": bool(parsed["force_even_voxels"]),
        "cpu": cpu,
        "seed": seed,
    }
    return normalized_box, normalized_vina


def _screening_vina_with_defaults(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {**SCREENING_VINA_DEFAULTS, **source}


def _screening_parameter_warnings(value: Any) -> list[str]:
    vina = value if isinstance(value, dict) else {}
    unbound_energy = vina.get("unbound_energy")
    if unbound_energy is None or (
        isinstance(unbound_energy, str) and not unbound_energy.strip()
    ):
        return []
    return [
        "显式未结合态参考能量仅适用于刚性单配体 score_only；"
        "本批量全局对接任务已明确忽略该值。"
    ]


def _valid_legacy_energy_range_zero_marker(state: dict[str, Any]) -> bool:
    compatibility = (
        state.get("compatibility")
        if isinstance(state.get("compatibility"), dict)
        else {}
    )
    marker = compatibility.get("legacy_energy_range_zero")
    if marker is True:
        # The first compatibility implementation used a boolean marker.
        return True
    return (
        isinstance(marker, dict)
        and marker.get("applied") is True
        and marker.get("reason") == "schema_v1_missing_all_advanced_vina_fields"
    )


def _allow_legacy_energy_range_zero(state: dict[str, Any]) -> bool:
    if _valid_legacy_energy_range_zero_marker(state):
        return True

    compatibility = (
        state.get("compatibility")
        if isinstance(state.get("compatibility"), dict)
        else {}
    )
    raw_vina = state.get("vina") if isinstance(state.get("vina"), dict) else {}
    energy_range = raw_vina.get("energy_range")
    missing_all_advanced = all(
        key not in raw_vina for key in SCREENING_VINA_DEFAULTS
    )
    is_legacy_zero = (
        state.get("schema_version") == SCREENING_SCHEMA_VERSION
        and missing_all_advanced
        and not isinstance(energy_range, bool)
        and isinstance(energy_range, (int, float))
        and math.isfinite(float(energy_range))
        and float(energy_range) == 0
    )
    if not is_legacy_zero:
        return False

    compatibility = dict(compatibility)
    compatibility["legacy_energy_range_zero"] = {
        "applied": True,
        "detected_at": _now_iso(),
        "reason": "schema_v1_missing_all_advanced_vina_fields",
    }
    state["compatibility"] = compatibility
    return True


def _screening_grid_resource(
    box: dict[str, float],
    vina: dict[str, Any],
    ligand_entries: list[tuple[Path, str]],
) -> dict[str, Any]:
    ligand_stats = [
        _parse_pdbqt_stats(path, relative, ligand=True)
        for path, relative in ligand_entries
    ]
    estimates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for stats in ligand_stats:
        validation = _validate_vina_grid_resource(
            box,
            float(vina["spacing"]),
            list(stats.get("atom_types") or []),
            bool(vina["force_even_voxels"]),
        )
        if not validation.get("ok"):
            return validation
        estimates.append((stats, validation))
    reference, validation = max(
        estimates,
        key=lambda entry: int(
            (entry[1].get("grid_estimate") or {}).get("estimated_map_bytes") or 0
        ),
    )
    return {
        "ok": True,
        "grid_resource": {
            "estimate": validation["grid_estimate"],
            "warnings": list(validation.get("warnings") or []),
            "reference_ligand": {
                "source_file": str(reference.get("relative_path") or ""),
                "sha256": str(reference.get("sha256") or ""),
                "atom_type_count": len(reference.get("atom_types") or []),
                "atom_types": list(reference.get("atom_types") or []),
            },
        },
        "error": None,
    }


def _verified_frozen_file_bytes(
    path: Path,
    record: dict[str, Any],
    *,
    code: str,
    label: str,
) -> tuple[bytes, str]:
    expected_size = record.get("size_bytes")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
        raise _ScreeningIntegrityError(
            code,
            f"{label}缺少有效的冻结文件大小。",
        )
    expected_sha256 = str(record.get("sha256") or "").lower()
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise _ScreeningIntegrityError(
            code,
            f"{label}缺少有效的冻结 SHA256。",
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise _ScreeningIntegrityError(
            code,
            f"无法读取{label}：{exc}",
        ) from exc
    if len(content) != expected_size:
        raise _ScreeningIntegrityError(
            code,
            f"{label}大小已变化：记录 {expected_size} bytes，实际 {len(content)} bytes。",
        )
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256.lower() != expected_sha256:
        raise _ScreeningIntegrityError(
            code,
            f"{label} SHA256 已变化。",
        )
    return content, actual_sha256


def _verified_attempt_vina(state: dict[str, Any]) -> tuple[Path, str, int]:
    try:
        vina_path = Path(str(state.get("vina_path") or "")).expanduser().resolve(
            strict=True
        )
    except (OSError, RuntimeError) as exc:
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            f"无法读取批量任务冻结的 AutoDock Vina：{exc}",
        ) from exc
    if not vina_path.is_file():
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            "批量任务冻结的 AutoDock Vina 路径不再是文件。",
        )
    tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
    frozen_tool = tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
    expected_sha256 = str(frozen_tool.get("sha256") or "").lower()
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            "screening.json 缺少有效的 AutoDock Vina SHA256。",
        )
    try:
        executable_bytes = vina_path.read_bytes()
    except OSError as exc:
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            f"无法读取批量任务冻结的 AutoDock Vina：{exc}",
        ) from exc
    actual_size = len(executable_bytes)
    actual_sha256 = hashlib.sha256(executable_bytes).hexdigest()
    expected_size = frozen_tool.get("size_bytes")
    if expected_size is not None and (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or actual_size != expected_size
    ):
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            "AutoDock Vina 可执行文件大小已与批量任务创建时不同。",
        )
    if actual_sha256.lower() != expected_sha256:
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            "AutoDock Vina 可执行文件已与批量任务创建时不同。",
        )
    return vina_path, actual_sha256, actual_size


def _verify_runtime_evidence_file(
    path: Path,
    record: Any,
    *,
    code: str,
    label: str,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise _ScreeningIntegrityError(code, f"{label}缺少完整的运行证据。")
    expected_sha256 = str(record.get("sha256") or "").strip().lower()
    expected_size = record.get("size_bytes")
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise _ScreeningIntegrityError(code, f"{label}缺少有效的运行后 SHA256。")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise _ScreeningIntegrityError(code, f"{label}缺少有效的运行后文件大小。")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise _ScreeningIntegrityError(code, f"无法复核运行后的{label}：{exc}") from exc
    actual_size = len(content)
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_size != expected_size:
        raise _ScreeningIntegrityError(
            code,
            f"{label}在 Vina 运行期间发生变化：记录 {expected_size} bytes，实际 {actual_size} bytes。",
        )
    if actual_sha256.lower() != expected_sha256:
        raise _ScreeningIntegrityError(
            code,
            f"{label}在 Vina 运行期间发生变化：SHA256 不一致。",
        )
    return {
        "sha256": actual_sha256,
        "size_bytes": actual_size,
    }


def _verify_attempt_runtime_evidence(
    attempt_dir: Path,
    attempt_record: dict[str, Any],
) -> dict[str, Any]:
    input_snapshots = (
        attempt_record.get("input_snapshots")
        if isinstance(attempt_record.get("input_snapshots"), dict)
        else {}
    )
    verified = {
        "receptor": _verify_runtime_evidence_file(
            attempt_dir / "receptor.pdbqt",
            input_snapshots.get("receptor"),
            code="SCREENING_ATTEMPT_RECEPTOR_CHANGED",
            label="attempt 受体快照",
        ),
        "ligand": _verify_runtime_evidence_file(
            attempt_dir / "ligand.pdbqt",
            input_snapshots.get("ligand"),
            code="SCREENING_ATTEMPT_LIGAND_CHANGED",
            label="attempt 配体快照",
        ),
        "config": _verify_runtime_evidence_file(
            attempt_dir / "config.txt",
            attempt_record.get("config_snapshot"),
            code="SCREENING_ATTEMPT_CONFIG_CHANGED",
            label="attempt Vina 配置",
        ),
    }
    topology_snapshot = input_snapshots.get("source_topology")
    if topology_snapshot is not None:
        if not isinstance(topology_snapshot, dict):
            raise _ScreeningIntegrityError(
                "SCREENING_ATTEMPT_TOPOLOGY_CHANGED",
                "attempt 原始拓扑证据无效。",
            )
        topology_name = str(topology_snapshot.get("snapshot_name") or "")
        if (
            not topology_name
            or Path(topology_name).name != topology_name
            or Path(topology_name).suffix.lower() not in {".sdf", ".mol"}
        ):
            raise _ScreeningIntegrityError(
                "SCREENING_ATTEMPT_TOPOLOGY_CHANGED",
                "attempt 原始拓扑快照名称无效。",
            )
        verified["source_topology"] = _verify_runtime_evidence_file(
            attempt_dir / topology_name,
            topology_snapshot,
            code="SCREENING_ATTEMPT_TOPOLOGY_CHANGED",
            label="attempt 原始拓扑快照",
        )
    vina_snapshot = (
        attempt_record.get("vina_snapshot")
        if isinstance(attempt_record.get("vina_snapshot"), dict)
        else {}
    )
    raw_vina_path = str(vina_snapshot.get("path") or "")
    try:
        vina_path = Path(raw_vina_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            f"无法复核运行后的 AutoDock Vina：{exc}",
        ) from exc
    if not vina_path.is_file():
        raise _ScreeningIntegrityError(
            "SCREENING_VINA_BINARY_CHANGED",
            "运行后的 AutoDock Vina 路径不再是文件。",
        )
    verified["vina"] = _verify_runtime_evidence_file(
        vina_path,
        vina_snapshot,
        code="SCREENING_VINA_BINARY_CHANGED",
        label="AutoDock Vina 可执行文件",
    )
    return {
        "status": "verified",
        "checked_at": _now_iso(),
        "files": verified,
    }


def _verified_frozen_item_topology(
    root: Path,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = str(item.get("item_id") or "")
    integrity = str(item.get("topology_integrity") or "not_available")
    relative = str(item.get("source_topology_file") or "")
    expected_sha256 = str(item.get("source_topology_sha256") or "").lower()
    expected_size = item.get("source_topology_size_bytes")
    has_v2_identity = any(
        str(item.get(field) or "")
        for field in (
            "source_import_id",
            "source_candidate_id",
            "source_record_id",
        )
    )
    if integrity == "not_available":
        if relative or expected_sha256 or int(expected_size or 0) != 0:
            raise ValueError(
                f"冻结配体 {item_id} 的原始拓扑不可用状态与文件身份冲突。"
            )
        facts = item.get("chemical_facts")
        if (
            has_v2_identity
            and (
                not isinstance(facts, dict)
                or facts.get("status") != "unavailable"
            )
        ):
            raise ValueError(
                f"冻结配体 {item_id} 的 PDBQT-only 化学事实状态无效。"
            )
        if has_v2_identity:
            _validated_stored_preparation_evidence(
                item,
                require_worker=False,
            )
        return None
    if integrity != "verified":
        raise ValueError(
            f"冻结配体 {item_id} 的原始拓扑完整性状态无效：{integrity}。"
        )
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
    ):
        raise ValueError(f"冻结配体 {item_id} 缺少有效的原始拓扑身份。")
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or ".." in logical.parts
        or len(logical.parts) != 4
        or tuple(logical.parts[:3])
        != ("screening", "inputs", "topology")
        or Path(logical.parts[3]).stem != item_id
        or Path(logical.parts[3]).suffix.lower() not in {".sdf", ".mol"}
    ):
        raise ValueError(f"冻结配体 {item_id} 的原始拓扑路径无效。")
    cursor = root
    for part in logical.parts:
        cursor = cursor / part
        if _path_is_link_or_reparse(cursor):
            raise ValueError(
                f"冻结配体 {item_id} 的原始拓扑路径包含链接或重解析点。"
            )
    topology_path = (root / Path(*logical.parts)).resolve(strict=True)
    try:
        topology_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"冻结配体 {item_id} 的原始拓扑越过项目目录。"
        ) from exc
    if (
        not topology_path.is_file()
        or topology_path.stat().st_size != expected_size
        or _sha256(topology_path).lower() != expected_sha256
    ):
        raise ValueError(f"冻结配体 {item_id} 的原始拓扑完整性校验失败。")
    facts = _validated_screening_chemical_facts(
        item.get("chemical_facts"),
        source_topology_sha256=expected_sha256,
    )
    evidence = _validated_stored_preparation_evidence(
        item,
        require_worker=True,
    )
    return {
        "file": relative,
        "sha256": expected_sha256,
        "size_bytes": expected_size,
        "chemical_facts": facts,
        "preparation_evidence": evidence,
    }


def _verified_library_import_manifest(
    root: Path,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    record = (
        inputs.get("library_import_manifest")
        if isinstance(inputs.get("library_import_manifest"), dict)
        else None
    )
    has_v2_items = any(
        isinstance(item, dict)
        and any(
            str(item.get(field) or "")
            for field in (
                "source_import_id",
                "source_candidate_id",
                "source_record_id",
            )
        )
        for item in state.get("items") or []
    )
    if record is None:
        if has_v2_items:
            raise ValueError("screening.json 缺少批量配体导入 manifest。")
        return None
    relative = str(record.get("file") or "")
    expected_sha256 = str(record.get("sha256") or "").lower()
    expected_size = record.get("size_bytes")
    if (
        relative != "screening/inputs/library_import_manifest.json"
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
    ):
        raise ValueError("批量配体导入 manifest 身份无效。")
    path = root / Path(*PurePosixPath(relative).parts)
    if (
        not path.is_file()
        or _path_is_link_or_reparse(path)
        or path.stat().st_size != expected_size
        or _sha256(path).lower() != expected_sha256
    ):
        raise ValueError("批量配体导入 manifest 完整性校验失败。")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema_version")
        != SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
        or not isinstance(loaded.get("items"), list)
    ):
        raise ValueError("批量配体导入 manifest 内容无效。")
    by_item: dict[str, dict[str, Any]] = {}
    for value in loaded["items"]:
        if not isinstance(value, dict):
            raise ValueError("批量配体导入 manifest 包含无效 item。")
        item_id = str(value.get("item_id") or "")
        if not item_id or item_id in by_item:
            raise ValueError("批量配体导入 manifest 包含重复 item_id。")
        by_item[item_id] = value
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        manifest_item = by_item.get(item_id)
        if manifest_item is None:
            raise ValueError(
                f"批量配体导入 manifest 缺少冻结配体 {item_id}。"
            )
        comparisons = {
            "candidate_id": str(item.get("source_candidate_id") or ""),
            "record_id": str(item.get("source_record_id") or ""),
            "import_id": str(item.get("source_import_id") or ""),
            "topology_file": str(item.get("source_topology_file") or ""),
            "topology_sha256": str(
                item.get("source_topology_sha256") or ""
            ).lower(),
        }
        if any(
            str(manifest_item.get(field) or "").lower()
            != expected.lower()
            for field, expected in comparisons.items()
        ):
            raise ValueError(
                f"批量配体导入 manifest 与冻结配体 {item_id} 身份不一致。"
            )
    return loaded


def _verified_frozen_ligand_entries(
    root: Path,
    state: dict[str, Any],
) -> list[tuple[Path, str]]:
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    receptor = (
        inputs.get("receptor") if isinstance(inputs.get("receptor"), dict) else {}
    )
    receptor_path, _ = _project_file(
        root,
        str(receptor.get("file") or ""),
        label="冻结受体",
    )
    receptor_size = int(receptor.get("size_bytes") or 0)
    receptor_sha256 = str(receptor.get("sha256") or "").lower()
    if receptor_path.stat().st_size != receptor_size:
        raise ValueError("冻结受体的文件大小与 screening.json 不一致。")
    if SHA256_PATTERN.fullmatch(receptor_sha256) is None:
        raise ValueError("screening.json 缺少有效的冻结受体 SHA256。")
    if _sha256(receptor_path).lower() != receptor_sha256:
        raise ValueError("冻结受体的 SHA256 与 screening.json 不一致。")
    _verify_screening_ad4_maps(root, state)

    entries: list[tuple[Path, str]] = []
    verified_topology_count = 0
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            raise ValueError("screening.json 包含无效的配体记录。")
        relative = str(item.get("ligand_file") or "")
        ligand_path, normalized_relative = _project_file(
            root,
            relative,
            label=f"冻结配体 {item.get('item_id') or ''}",
        )
        expected_size = int(item.get("size_bytes") or 0)
        expected_sha256 = str(item.get("sha256") or "").lower()
        if ligand_path.stat().st_size != expected_size:
            raise ValueError(f"冻结配体 {item.get('item_id')} 的文件大小不一致。")
        if SHA256_PATTERN.fullmatch(expected_sha256) is None:
            raise ValueError(
                f"screening.json 缺少冻结配体 {item.get('item_id')} 的有效 SHA256。"
            )
        if _sha256(ligand_path).lower() != expected_sha256:
            raise ValueError(f"冻结配体 {item.get('item_id')} 的 SHA256 不一致。")
        if _verified_frozen_item_topology(root, item) is not None:
            verified_topology_count += 1
        entries.append(
            (
                ligand_path,
                str(item.get("source_file") or normalized_relative),
            )
        )
    if not entries:
        raise ValueError("screening.json 没有可运行的冻结配体。")
    topology_summary = (
        inputs.get("topology")
        if isinstance(inputs.get("topology"), dict)
        else None
    )
    if topology_summary is not None:
        expected_status = (
            "complete"
            if verified_topology_count == len(entries)
            else "partial"
            if verified_topology_count
            else "unavailable"
        )
        if (
            topology_summary.get("status") != expected_status
            or int(topology_summary.get("available_count") or 0)
            != verified_topology_count
            or int(topology_summary.get("total_count") or 0)
            != len(entries)
        ):
            raise ValueError("screening.json 的原始拓扑覆盖摘要不一致。")
    _verified_library_import_manifest(root, state)
    return entries


def _validate_screening_execution_state(
    root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    try:
        limits = _validated_resource_limits(
            state.get("resource_limits")
            if "resource_limits" in state
            else None
        )
        _verify_frozen_resource_limits_hash(state, limits)
        resource_state = _validate_screening_resource_state(state, limits)
        state["resource_limits"] = limits.to_dict()
        state["max_retries"] = resource_state["max_retries"]
        state["top_n"] = resource_state["top_n"]
        raw_vina = state.get("vina") if isinstance(state.get("vina"), dict) else {}
        allow_legacy_zero = _allow_legacy_energy_range_zero(state)
        normalized_box, normalized_vina = _validate_settings(
            state.get("box") if isinstance(state.get("box"), dict) else {},
            _screening_vina_with_defaults(raw_vina),
            limits,
            allow_legacy_energy_range_zero=allow_legacy_zero,
        )
        state["box"] = normalized_box
        state["vina"] = normalized_vina
        warnings = list(
            state.get("parameter_warnings")
            if isinstance(state.get("parameter_warnings"), list)
            else []
        )
        for warning in _screening_parameter_warnings(raw_vina):
            if warning not in warnings:
                warnings.append(warning)
        state["parameter_warnings"] = warnings
        ligand_entries = _verified_frozen_ligand_entries(root, state)
        recorded_grid = (
            state.get("grid_resource")
            if isinstance(state.get("grid_resource"), dict)
            else {}
        )
        if str(state.get("scoring_protocol") or "vina") == "ad4_maps":
            _verify_screening_ad4_maps(root, state)
            revalidated_grid = {
                **recorded_grid,
                "source": "ad4_maps_manifest",
            }
        else:
            grid_validation = _screening_grid_resource(
                normalized_box,
                normalized_vina,
                ligand_entries,
            )
            if not grid_validation.get("ok"):
                return grid_validation
            revalidated_grid = grid_validation["grid_resource"]
        state["grid_resource"] = {
            **revalidated_grid,
            "execution_check": {
                "checked_at": _now_iso(),
                "matches_recorded": (
                    str(state.get("scoring_protocol") or "vina") == "ad4_maps"
                    or (
                        recorded_grid.get("estimate") == revalidated_grid["estimate"]
                        and recorded_grid.get("reference_ligand")
                        == revalidated_grid["reference_ligand"]
                    )
                ),
            },
        }

        vina_path = Path(str(state.get("vina_path") or "")).expanduser().resolve(
            strict=True
        )
        if not vina_path.is_file():
            raise ValueError("冻结的 AutoDock Vina 路径不是文件。")
        tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
        frozen_tool = (
            tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
        )
        expected_sha256 = str(frozen_tool.get("sha256") or "").lower()
        if SHA256_PATTERN.fullmatch(expected_sha256) is None:
            raise ValueError("screening.json 缺少有效的 AutoDock Vina SHA256。")
        actual_sha256 = _sha256(vina_path)
        if actual_sha256.lower() != expected_sha256:
            return _error(
                "SCREENING_VINA_BINARY_CHANGED",
                "AutoDock Vina 可执行文件已与批量任务创建时不同。",
                raw_error=json.dumps(
                    {
                        "path": str(vina_path),
                        "expected_sha256": expected_sha256,
                        "actual_sha256": actual_sha256,
                    },
                    ensure_ascii=False,
                ),
                suggestion="请恢复创建任务时使用的 Vina，或归档当前任务后重新创建批量筛选。",
            )

        if normalized_vina["no_refine"] or normalized_vina["force_even_voxels"]:
            current_tool, current_capabilities = _resolve_vina_tool(str(vina_path))
            if current_tool.sha256.lower() != expected_sha256:
                return _error(
                    "SCREENING_VINA_BINARY_CHANGED",
                    "能力复核使用的 AutoDock Vina 与批量任务冻结二进制不同。",
                    raw_error=json.dumps(
                        {
                            "expected_sha256": expected_sha256,
                            "actual_sha256": current_tool.sha256,
                        },
                        ensure_ascii=False,
                    ),
                    suggestion="请恢复创建任务时使用的 Vina，或重新创建批量筛选。",
                )
            capability_validation = validate_vina_runtime_capabilities(
                normalized_vina,
                str(state.get("scoring_protocol") or normalized_vina["scoring"]),
                current_capabilities,
                run_mode="dock",
                receptor_mode="rigid",
            )
            if not capability_validation.get("ok"):
                error = capability_validation.get("error") or {}
                return _error(
                    "SCREENING_VINA_CAPABILITY_MISMATCH",
                    "当前 AutoDock Vina 不支持批量任务冻结的专家选项。",
                    raw_error=str(error.get("raw_error") or error.get("message") or ""),
                    suggestion=str(
                        error.get("suggestion")
                        or "请恢复创建任务时使用的兼容 Vina，或重新创建批量筛选。"
                    ),
                )
        return {"ok": True, "error": None}
    except Exception as exc:  # noqa: BLE001 - workflow boundary returns structured errors.
        return _error(
            "SCREENING_EXECUTION_PREFLIGHT_ERROR",
            "批量筛选执行前复核失败。",
            str(exc),
            "请检查 screening.json、冻结的 Vina 路径与参数后再运行。",
        )


def _state_path(root: Path) -> Path:
    return root / STATE_RELATIVE_PATH


def _write_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    atomic_write_json(_state_path(root), state)


def _read_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.is_file():
        raise FileNotFoundError("没有找到 screening/screening.json。")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != SCREENING_SCHEMA_VERSION:
        raise ValueError("screening.json 的 schema 版本不受支持。")
    if not isinstance(state.get("items"), list) or not isinstance(state.get("queue"), list):
        raise ValueError("screening.json 缺少有效的 items 或 queue。")
    return state


def _copy_snapshot(source: Path, destination: Path) -> None:
    atomic_write_bytes(destination, source.read_bytes())


def _screening_protocol_context(
    root: Path,
    *,
    requested_scoring: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    loaded = load_project(str(root))
    if not loaded.get("ok"):
        # The screening backend predates project schema v2 and is also used by
        # the standalone/legacy API.  Keep that Vina-only entry point readable,
        # but never infer AD4 without a valid project and its bound maps.
        if str(requested_scoring or "vina").strip().lower() != "ad4":
            return {
                "project": None,
                "scoring_protocol": "vina",
                "protocol_id": "rigid_single",
                "maps_status": None,
            }, None
        return None, loaded
    project = _project_from_dict(loaded["project"], root)
    scoring_protocol = _project_scoring_protocol(project)
    protocol_id = _project_protocol_id(project)
    if (
        str(requested_scoring or "").strip().lower() == "ad4"
        and scoring_protocol != "ad4_maps"
    ):
        return None, _error(
            "SCREENING_AD4_PROTOCOL_REQUIRED",
            "批量 AD4 必须先在项目中启用标准 AutoDock4 maps 协议。",
            suggestion="请切换到标准 AD4，生成或导入 maps 后再创建批量任务。",
        )
    if _project_run_mode(project) != "dock":
        return None, _error(
            "SCREENING_GLOBAL_SEARCH_REQUIRED",
            "批量筛选只支持全局对接。",
            suggestion="请将任务类型切换为全局对接。",
        )
    if _project_receptor_mode(project) != "rigid":
        return None, _error(
            "SCREENING_RIGID_RECEPTOR_REQUIRED",
            "批量筛选只支持刚性受体。",
            suggestion="请切换回刚性受体；柔性受体请使用单配体运行。",
        )
    if scoring_protocol == "ad4_maps" and protocol_id != "ad4_maps":
        return None, _error(
            "SCREENING_AD4_SUBPROTOCOL_UNSUPPORTED",
            "批量 AD4 仅支持标准 AutoDock4 maps，不支持 AD4Zn 或水合 AD4。",
            suggestion="请切换到标准 AD4 maps 协议。",
        )
    maps_status: dict[str, Any] | None = None
    if scoring_protocol == "ad4_maps":
        from dockstart_core.autogrid import validate_active_maps

        maps_status = validate_active_maps(str(root))
        if not maps_status.get("ok") or not maps_status.get("ready"):
            return None, _error(
                "SCREENING_AD4_MAPS_NOT_READY",
                "标准 AutoDock4 maps 尚未通过完整性校验。",
                "；".join(str(item) for item in maps_status.get("issues") or []),
                "请先生成或导入与当前受体、Box 和配体原子类型匹配的 AD4 maps。",
            )
    return {
        "project": project,
        "scoring_protocol": scoring_protocol,
        "protocol_id": protocol_id,
        "maps_status": maps_status,
    }, None


def _freeze_screening_ad4_maps(
    root: Path,
    inputs_root: Path,
    maps_status: dict[str, Any],
) -> dict[str, Any]:
    manifest = (
        maps_status.get("manifest")
        if isinstance(maps_status.get("manifest"), dict)
        else {}
    )
    source_files = (
        (manifest.get("maps") or {}).get("files")
        if isinstance(manifest.get("maps"), dict)
        else []
    )
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("活动 AD4 maps manifest 没有文件清单。")
    target_root = inputs_root / "ad4_maps"
    target_root.mkdir(parents=False, exist_ok=False)
    snapshots: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in source_files:
        if not isinstance(item, dict):
            raise ValueError("活动 AD4 maps manifest 包含无效文件记录。")
        source_relative = str(item.get("relative_path") or "")
        source, _ = _project_artifact_file(
            root,
            source_relative,
            label="AD4 map",
        )
        name = source.name
        if name in seen_names:
            raise ValueError(f"活动 AD4 maps 包含重名文件：{name}")
        seen_names.add(name)
        destination = target_root / name
        _copy_snapshot(source, destination)
        snapshots.append(
            {
                "relative_path": Path(
                    "screening",
                    "inputs",
                    "ad4_maps",
                    name,
                ).as_posix(),
                "sha256": _sha256(destination),
                "size_bytes": destination.stat().st_size,
                "source_relative_path": source_relative,
            }
        )
    manifest_relative = str(maps_status.get("manifest_file") or "")
    manifest_source, _ = _project_artifact_file(
        root,
        manifest_relative,
        label="AD4 maps manifest",
    )
    manifest_destination = target_root / "source_manifest.json"
    _copy_snapshot(manifest_source, manifest_destination)
    prefix_name = Path(str(maps_status.get("maps_prefix") or "")).name
    if not prefix_name:
        raise ValueError("活动 AD4 maps 缺少 prefix。")
    return {
        "map_set_id": str(maps_status.get("map_set_id") or ""),
        "source_manifest": manifest_relative,
        "manifest": {
            "file": Path(
                "screening",
                "inputs",
                "ad4_maps",
                "source_manifest.json",
            ).as_posix(),
            "sha256": _sha256(manifest_destination),
            "size_bytes": manifest_destination.stat().st_size,
        },
        "prefix": Path(
            "screening",
            "inputs",
            "ad4_maps",
            prefix_name,
        ).as_posix(),
        "attempt_prefix": Path(
            "..",
            "..",
            "..",
            "inputs",
            "ad4_maps",
            prefix_name,
        ).as_posix(),
        "files": snapshots,
        "grid": json.loads(json.dumps(manifest.get("grid") or {})),
        "ligand_atom_types": list(
            (manifest.get("maps") or {}).get("ligand_atom_types") or []
        ),
    }


def _verify_screening_ad4_maps(
    root: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    if str(state.get("scoring_protocol") or "vina") != "ad4_maps":
        return {}
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    record = (
        inputs.get("ad4_maps")
        if isinstance(inputs.get("ad4_maps"), dict)
        else {}
    )
    manifest = (
        record.get("manifest")
        if isinstance(record.get("manifest"), dict)
        else {}
    )
    files = record.get("files") if isinstance(record.get("files"), list) else []
    if not manifest or not files:
        raise ValueError("screening.json 缺少冻结 AD4 maps 证据。")
    for label, item in [("AD4 maps manifest", manifest), *[
        (f"AD4 map #{index}", value)
        for index, value in enumerate(files, start=1)
        if isinstance(value, dict)
    ]]:
        path, _ = _project_artifact_file(
            root,
            str(item.get("file") or item.get("relative_path") or ""),
            label=label,
        )
        _verified_frozen_file_bytes(
            path,
            item,
            code="SCREENING_AD4_MAPS_SNAPSHOT_CHANGED",
            label=label,
        )
    prefix = str(record.get("prefix") or "")
    expected_prefix_root = Path("screening", "inputs", "ad4_maps").as_posix() + "/"
    if not prefix.startswith(expected_prefix_root) or Path(prefix).name in {"", ".", ".."}:
        raise ValueError("screening.json 中冻结 maps prefix 不安全。")
    return record


def _looks_like_pdbqt(path: Path) -> bool:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return any(line.startswith(("ATOM  ", "HETATM")) for line in handle)


def _screening_library_preparation_script_text() -> str:
    """Return the isolated RDKit/Meeko worker used only by library imports."""

    return r'''
from __future__ import annotations

import hashlib
import io
import json
import platform
import sys
from pathlib import Path

import meeko
from rdkit import Chem, rdBase
from rdkit.Chem import rdMolDescriptors
from meeko import MoleculePreparation

try:
    from meeko import PDBQTWriterLegacy as PDBQTWriter
except Exception:
    try:
        from meeko import PDBQTWriter
    except Exception as exc:
        raise RuntimeError("未找到可用的 Meeko PDBQT writer。") from exc


CHEMICAL_FACTS_PROFILE = "rdkit_source_formal_charge_heavy_atoms_strict_rotatable_bonds_v1"
HYDROGEN_POLICY = "rdkit_add_hs_preserve_source_indices_v1"


class MacrocycleReviewRequired(RuntimeError):
    def __init__(self, rings, removed_bonds):
        super().__init__("检测到大环配体，批量导入不会静默采用 Meeko 自动断环。")
        self.rings = rings
        self.removed_bonds = removed_bonds


def split_sdf_records(payload: bytes) -> list[bytes]:
    records = []
    current = []
    for line in payload.splitlines(keepends=True):
        current.append(line)
        if line.strip() == b"$$$$":
            block = b"".join(current)
            if block.strip():
                records.append(block)
            current = []
    trailing = b"".join(current)
    if trailing.strip():
        records.append(trailing)
    return records


def normalize_writer_result(result):
    if isinstance(result, tuple):
        if len(result) >= 2 and result[1] is False:
            raise RuntimeError(str(result[2]) if len(result) >= 3 else "Meeko 写出 PDBQT 失败。")
        return str(result[0])
    return str(result)


def toolkit_snapshot():
    return {
        "python_version": platform.python_version(),
        "rdkit_version": str(rdBase.rdkitVersion),
        "meeko_version": str(getattr(meeko, "__version__", "unknown")),
    }


def topology_fingerprints(molecule):
    atoms = [
        {
            "index": int(atom.GetIdx()),
            "atomic_number": int(atom.GetAtomicNum()),
            "formal_charge": int(atom.GetFormalCharge()),
            "isotope": int(atom.GetIsotope()),
            "chiral_tag": str(atom.GetChiralTag()),
            "aromatic": bool(atom.GetIsAromatic()),
        }
        for atom in molecule.GetAtoms()
    ]
    bonds = sorted(
        [
            {
                "begin": min(int(bond.GetBeginAtomIdx()), int(bond.GetEndAtomIdx())),
                "end": max(int(bond.GetBeginAtomIdx()), int(bond.GetEndAtomIdx())),
                "order": str(bond.GetBondType()),
                "aromatic": bool(bond.GetIsAromatic()),
                "conjugated": bool(bond.GetIsConjugated()),
                "stereo": str(bond.GetStereo()),
            }
            for bond in molecule.GetBonds()
        ],
        key=lambda item: (item["begin"], item["end"]),
    )
    payload = json.dumps(
        {"atoms": atoms, "bonds": bonds},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ordered_sha256 = hashlib.sha256(payload).hexdigest()
    heavy_graph = Chem.RemoveHs(Chem.Mol(molecule))
    canonical_smiles = Chem.MolToSmiles(
        heavy_graph,
        canonical=True,
        isomericSmiles=True,
        allBondsExplicit=True,
    )
    if not canonical_smiles:
        raise RuntimeError("RDKit 未能生成规范重原子图身份。")
    return {
        "atom_count": len(atoms),
        "bond_count": len(bonds),
        "ordered_sha256": ordered_sha256,
        "canonical_graph_sha256": hashlib.sha256(
            canonical_smiles.encode("utf-8")
        ).hexdigest(),
    }


def chemical_facts(molecule, source_sha256):
    rings = [list(map(int, ring)) for ring in Chem.GetSymmSSSR(molecule)]
    topology = topology_fingerprints(molecule)
    return {
        "schema_version": 1,
        "status": "verified",
        "source": "frozen_raw_topology",
        "calculation_profile": CHEMICAL_FACTS_PROFILE,
        "source_topology_sha256": source_sha256,
        "rdkit_version": str(rdBase.rdkitVersion),
        "formal_charge": int(sum(atom.GetFormalCharge() for atom in molecule.GetAtoms())),
        "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
        "rotatable_bond_count": int(
            rdMolDescriptors.CalcNumRotatableBonds(
                molecule,
                rdMolDescriptors.NumRotatableBondsOptions.Strict,
            )
        ),
        "fragment_count": int(len(Chem.GetMolFrags(molecule))),
        "max_ring_size": max((len(ring) for ring in rings), default=0),
        "has_macrocycle": any(len(ring) >= 7 for ring in rings),
        "canonical_topology_sha256": topology["canonical_graph_sha256"],
        "ordered_topology_sha256": topology["ordered_sha256"],
        "canonical_atom_count": topology["atom_count"],
        "canonical_bond_count": topology["bond_count"],
    }


def prepare_one(molecule, output_path: Path) -> None:
    molecule = Chem.AddHs(molecule, addCoords=True)
    Chem.SanitizeMol(molecule)
    perceived_rings = [
        [int(index) + 1 for index in ring]
        for ring in Chem.GetSymmSSSR(molecule)
        if len(ring) >= 7
    ]
    if perceived_rings:
        raise MacrocycleReviewRequired(perceived_rings, [])
    setups = MoleculePreparation().prepare(molecule)
    if not setups:
        raise RuntimeError("Meeko 未生成 ligand setup。")
    removed_bonds = sorted(
        {
            tuple(sorted((int(pair[0]), int(pair[1]))))
            for setup in setups
            for pair in getattr(
                getattr(setup, "ring_closure_info", None),
                "bonds_removed",
                [],
            )
        }
    )
    if removed_bonds:
        raise MacrocycleReviewRequired([], removed_bonds)
    pdbqt_text = normalize_writer_result(PDBQTWriter.write_string(setups[0]))
    if not pdbqt_text.strip():
        raise RuntimeError("Meeko 写出的 PDBQT 为空。")
    output_path.write_text(pdbqt_text, encoding="utf-8")


def molecule_name(molecule, fallback: str) -> str:
    if molecule is not None and molecule.HasProp("_Name"):
        value = " ".join(molecule.GetProp("_Name").split())
        if value:
            return value[:240]
    return fallback


def main() -> int:
    if len(sys.argv) != 5:
        print("需要输入源文件、输出目录、manifest 路径和记录上限。", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    manifest_path = Path(sys.argv[3])
    max_records = int(sys.argv[4])
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = input_path.suffix.lower()
    payload = input_path.read_bytes()

    if suffix == ".sdf":
        raw_records = split_sdf_records(payload)
        if len(raw_records) > max_records:
            manifest_path.write_text(json.dumps({
                "schema_version": 2,
                "preparation_profile": "dockstart_screening_rdkit_meeko_fail_closed_v2",
                "hydrogen_policy": HYDROGEN_POLICY,
                "toolchain": toolkit_snapshot(),
                "limit_exceeded": True,
                "record_count": len(raw_records),
                "records": [],
            }, ensure_ascii=False), encoding="utf-8")
            return 0
        supplier = Chem.ForwardSDMolSupplier(
            io.BytesIO(payload),
            sanitize=True,
            removeHs=False,
        )
        molecules = list(supplier)
        record_count = max(len(raw_records), len(molecules))
    elif suffix == ".mol":
        raw_records = [payload]
        record_count = 1
        molecules = [
            Chem.MolFromMolBlock(
                payload.decode("utf-8", errors="replace"),
                sanitize=True,
                removeHs=False,
            )
        ]
    else:
        print(f"暂不支持的配体输入格式：{suffix}", file=sys.stderr)
        return 3

    if record_count > max_records:
        manifest_path.write_text(json.dumps({
            "schema_version": 2,
            "preparation_profile": "dockstart_screening_rdkit_meeko_fail_closed_v2",
            "hydrogen_policy": HYDROGEN_POLICY,
            "toolchain": toolkit_snapshot(),
            "limit_exceeded": True,
            "record_count": record_count,
            "records": [],
        }, ensure_ascii=False), encoding="utf-8")
        return 0

    records = []
    for offset in range(record_count):
        index = offset + 1
        raw_record = raw_records[offset] if offset < len(raw_records) else b""
        molecule = molecules[offset] if offset < len(molecules) else None
        fallback = f"{input_path.stem} #{index}"
        base = {
            "source_record_index": index,
            "source_record_name": molecule_name(molecule, fallback),
            "source_record_sha256": hashlib.sha256(raw_record).hexdigest(),
            "source_record_size_bytes": len(raw_record),
            "preparation_toolchain": toolkit_snapshot(),
        }
        if molecule is None:
            records.append({
                **base,
                "status": "invalid",
                "error": {
                    "code": "RDKIT_RECORD_INVALID",
                    "message": "RDKit 未能读取该分子记录。",
                },
            })
            continue

        try:
            base["chemical_facts"] = chemical_facts(
                molecule,
                base["source_record_sha256"],
            )
        except Exception as exc:
            records.append({
                **base,
                "status": "invalid",
                "error": {
                    "code": "CHEMICAL_FACTS_FAILED",
                    "message": "无法从冻结原始拓扑计算可审计化学事实。",
                    "raw_error": str(exc)[:4000],
                },
            })
            continue

        output_name = f"record_{index:06d}.pdbqt"
        output_path = output_dir / output_name
        try:
            prepare_one(molecule, output_path)
        except MacrocycleReviewRequired as exc:
            output_path.unlink(missing_ok=True)
            records.append({
                **base,
                "status": "review_required",
                "macrocycle": {
                    "ring_atom_numbers_one_based": exc.rings,
                    "meeko_proposed_bonds_zero_based": [
                        list(pair) for pair in exc.removed_bonds
                    ],
                },
                "error": {
                    "code": "MACROCYCLE_REVIEW_REQUIRED",
                    "message": "检测到大环配体；批量准备不会静默采用 Meeko 自动断环。",
                    "suggestion": "请先使用单配体正式大环审查确认断环候选或刚性大环，再导入受审查的 PDBQT。",
                },
            })
            continue
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            records.append({
                **base,
                "status": "invalid",
                "error": {
                    "code": "LIGAND_PREPARATION_FAILED",
                    "message": "该分子记录无法准备为 PDBQT。",
                    "raw_error": str(exc)[:4000],
                },
            })
            continue
        records.append({
            **base,
            "status": "ready",
            "pdbqt_file": output_name,
        })

    manifest_path.write_text(json.dumps({
        "schema_version": 2,
        "preparation_profile": "dockstart_screening_rdkit_meeko_fail_closed_v2",
        "hydrogen_policy": HYDROGEN_POLICY,
        "toolchain": toolkit_snapshot(),
        "limit_exceeded": False,
        "record_count": record_count,
        "records": records,
    }, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    attributes = int(getattr(value, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return path.is_symlink() or bool(reparse_flag and attributes & reparse_flag)


def _expand_screening_input_files(
    values: list[str],
    limits: ScreeningResourceLimits,
) -> list[Path]:
    supported = {".pdbqt", ".sdf", ".mol"}
    expanded: list[Path] = []
    for value in values:
        supplied = Path(value).expanduser().resolve(strict=True)
        if supplied.is_file():
            if supplied.suffix.lower() not in supported:
                raise ValueError(
                    f"暂不支持的批量配体格式：{supplied.suffix.lower() or '无扩展名'}"
                )
            expanded.append(supplied)
            continue
        if not supplied.is_dir():
            raise ValueError(f"配体输入不是文件或目录：{supplied}")

        discovered: list[Path] = []
        for directory, directories, filenames in os.walk(supplied, followlinks=False):
            directory_path = Path(directory)
            directories[:] = sorted(
                (
                    name
                    for name in directories
                    if not _path_is_link_or_reparse(directory_path / name)
                ),
                key=lambda item: (item.casefold(), item),
            )
            for name in sorted(filenames, key=lambda item: (item.casefold(), item)):
                candidate = directory_path / name
                if (
                    candidate.suffix.lower() in supported
                    and not _path_is_link_or_reparse(candidate)
                    and candidate.is_file()
                ):
                    discovered.append(candidate.resolve(strict=True))
        expanded.extend(discovered)

    if not expanded:
        raise ValueError("所选位置没有可导入的 PDBQT、SDF 或 MOL 配体。")
    if len(expanded) > limits.max_ligands:
        raise ValueError(
            f"待扫描的配体文件数量超过资源上限 {limits.max_ligands}。"
        )
    return expanded


def _split_sdf_record_bytes(payload: bytes) -> list[bytes]:
    records: list[bytes] = []
    current: list[bytes] = []
    for line in payload.splitlines(keepends=True):
        current.append(line)
        if line.strip() == b"$$$$":
            block = b"".join(current)
            if block.strip():
                records.append(block)
            current = []
    trailing = b"".join(current)
    if trailing.strip():
        records.append(trailing)
    return records


def _screening_record_id(
    source_sha256: str,
    record_index: int,
    record_sha256: str,
) -> str:
    payload = (
        f"{source_sha256.lower()}:{int(record_index)}:{record_sha256.lower()}"
    ).encode("ascii")
    return f"record_{hashlib.sha256(payload).hexdigest()}"


def _next_screening_import_id(root: Path, index: dict[str, Any]) -> str:
    highest = 0
    imports = index.get("imports") if isinstance(index.get("imports"), dict) else {}
    for value in imports:
        match = re.fullmatch(r"import_(\d{6})", str(value))
        if match:
            highest = max(highest, int(match.group(1)))
    imports_root = root / STAGING_IMPORTS_RELATIVE_PATH
    if imports_root.is_dir():
        for path in imports_root.iterdir():
            match = re.fullmatch(r"\.?import_(\d{6})(?:\.tmp)?", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    if highest >= 999_999:
        raise ValueError("批量配体导入记录编号已经达到上限。")
    return f"import_{highest + 1:06d}"


def _freeze_screening_topology_record(
    root: Path,
    import_id: str,
    record_id: str,
    source_format: str,
    content: bytes,
    expected_sha256: str,
    expected_size_bytes: int,
) -> dict[str, Any]:
    if SCREENING_IMPORT_ID_PATTERN.fullmatch(import_id) is None:
        raise ValueError("批量配体 import_id 无效。")
    if SCREENING_RECORD_ID_PATTERN.fullmatch(record_id) is None:
        raise ValueError("批量配体 record_id 无效。")
    normalized_format = str(source_format or "").strip().lower()
    if normalized_format not in {"sdf", "mol"}:
        raise ValueError("原始拓扑快照只接受 SDF 或 MOL。")
    if len(content) != int(expected_size_bytes):
        raise ValueError("原始拓扑快照大小与 record 盘点不一致。")
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != str(expected_sha256).lower():
        raise ValueError("原始拓扑快照 SHA256 与 record 盘点不一致。")
    relative = (
        STAGING_IMPORTS_RELATIVE_PATH
        / import_id
        / "records"
        / f"{record_id}.{normalized_format}"
    )
    destination = root / relative
    if destination.exists():
        if (
            not destination.is_file()
            or destination.is_symlink()
            or destination.stat().st_size != len(content)
            or _sha256(destination) != actual_sha256
        ):
            raise ValueError(f"原始拓扑快照目标已存在但内容不一致：{relative}")
    else:
        atomic_write_bytes(destination, content)
    if (
        destination.stat().st_size != len(content)
        or _sha256(destination) != actual_sha256
    ):
        destination.unlink(missing_ok=True)
        raise ValueError("原始拓扑快照发布后完整性校验失败。")
    return {
        "source_topology_file": relative.as_posix(),
        "source_topology_sha256": actual_sha256,
        "source_topology_size_bytes": len(content),
        "topology_integrity": "verified",
    }


def _load_screening_staging_index(root: Path) -> dict[str, Any]:
    index_path = root / STAGING_INDEX_RELATIVE_PATH
    if not index_path.is_file():
        return {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "files": {},
            "records": {},
            "imports": {},
        }
    loaded = json.loads(index_path.read_text(encoding="utf-8"))
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema_version") != 1
        or not isinstance(loaded.get("files"), dict)
    ):
        raise ValueError("screening/staging/index.json 无效，已拒绝覆盖。")
    loaded.setdefault("records", {})
    loaded.setdefault("imports", {})
    if not isinstance(loaded["records"], dict) or not isinstance(
        loaded["imports"], dict
    ):
        raise ValueError("screening/staging/index.json 的导入审计字段无效。")
    return loaded


def _unavailable_screening_chemical_facts(
    reason: str,
    *,
    source: str,
    source_topology_sha256: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCREENING_CHEMICAL_FACTS_SCHEMA_VERSION,
        "status": "unavailable",
        "source": source,
        "reason": str(reason),
        "source_topology_sha256": str(source_topology_sha256).lower(),
    }


def _validated_screening_chemical_facts(
    value: Any,
    *,
    source_topology_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("批量配体记录缺少化学事实。")
    facts = dict(value)
    if facts.get("schema_version") != SCREENING_CHEMICAL_FACTS_SCHEMA_VERSION:
        raise ValueError("批量配体化学事实 schema 不受支持。")
    if facts.get("status") != "verified":
        raise ValueError("批量配体化学事实尚未通过验证。")
    if facts.get("source") != "frozen_raw_topology":
        raise ValueError("批量配体化学事实不是从冻结原始拓扑计算。")
    if facts.get("calculation_profile") != SCREENING_CHEMICAL_FACTS_PROFILE:
        raise ValueError("批量配体化学事实计算规范不受支持。")
    expected_topology_sha256 = str(source_topology_sha256).lower()
    if (
        SHA256_PATTERN.fullmatch(expected_topology_sha256) is None
        or str(facts.get("source_topology_sha256") or "").lower()
        != expected_topology_sha256
    ):
        raise ValueError("批量配体化学事实与冻结原始拓扑身份不一致。")
    if not str(facts.get("rdkit_version") or "").strip():
        raise ValueError("批量配体化学事实缺少 RDKit 版本。")

    integer_fields = (
        "formal_charge",
        "heavy_atom_count",
        "rotatable_bond_count",
        "fragment_count",
        "max_ring_size",
        "canonical_atom_count",
        "canonical_bond_count",
    )
    for field in integer_fields:
        raw = facts.get(field)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"批量配体化学事实 {field} 必须是整数。")
        if field != "formal_charge" and raw < 0:
            raise ValueError(f"批量配体化学事实 {field} 不能为负数。")
    if facts["canonical_atom_count"] <= 0:
        raise ValueError("批量配体化学事实的原子数必须大于 0。")
    if facts["heavy_atom_count"] > facts["canonical_atom_count"]:
        raise ValueError("批量配体化学事实的重原子数超过总原子数。")
    if facts["fragment_count"] <= 0:
        raise ValueError("批量配体化学事实的片段数必须大于 0。")
    if facts["max_ring_size"] > facts["canonical_atom_count"]:
        raise ValueError("批量配体化学事实的最大环尺寸超过总原子数。")
    if facts["rotatable_bond_count"] > facts["canonical_bond_count"]:
        raise ValueError("批量配体化学事实的可旋转键数超过总键数。")
    if not isinstance(facts.get("has_macrocycle"), bool):
        raise ValueError("批量配体化学事实 has_macrocycle 必须是布尔值。")
    if bool(facts["has_macrocycle"]) != (facts["max_ring_size"] >= 7):
        raise ValueError("批量配体化学事实的大环标记与环尺寸不一致。")
    canonical_sha256 = str(facts.get("canonical_topology_sha256") or "").lower()
    if SHA256_PATTERN.fullmatch(canonical_sha256) is None:
        raise ValueError("批量配体化学事实缺少有效的规范拓扑 SHA256。")
    facts["source_topology_sha256"] = expected_topology_sha256
    facts["canonical_topology_sha256"] = canonical_sha256
    return facts


def _screening_preparation_evidence(
    prepared: dict[str, Any],
    *,
    import_id: str,
    record_id: str,
    source_topology_sha256: str,
    chemical_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(prepared.get("status") or "invalid")
    if not source_topology_sha256:
        return {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "status": "not_required",
            "source": "supplied_pdbqt",
            "import_id": import_id,
            "record_id": record_id,
        }
    raw = (
        dict(prepared.get("_worker_evidence") or {})
        if isinstance(prepared.get("_worker_evidence"), dict)
        else {}
    )
    if raw.get("schema_version") != SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION:
        raise ValueError("批量配体准备证据 schema 无效。")
    if (
        raw.get("preparation_profile")
        != SCREENING_LIBRARY_PREPARATION_PROFILE
    ):
        raise ValueError("批量配体准备证据的准备规范不受支持。")
    if raw.get("hydrogen_policy") != SCREENING_HYDROGEN_POLICY:
        raise ValueError("批量配体准备证据的加氢策略不受支持。")
    toolchain = raw.get("toolchain")
    if not isinstance(toolchain, dict) or any(
        not str(toolchain.get(field) or "").strip()
        for field in ("python_version", "rdkit_version", "meeko_version")
    ):
        raise ValueError("批量配体准备证据缺少完整工具链版本。")
    if (
        isinstance(chemical_facts, dict)
        and chemical_facts.get("status") == "verified"
        and str(chemical_facts.get("rdkit_version") or "").strip()
        != str(toolchain.get("rdkit_version") or "").strip()
    ):
        raise ValueError("批量配体化学事实与准备证据的 RDKit 版本不一致。")
    for field in ("worker_script_sha256", "worker_manifest_sha256"):
        if SHA256_PATTERN.fullmatch(str(raw.get(field) or "").lower()) is None:
            raise ValueError(f"批量配体准备证据缺少有效的 {field}。")
    manifest_size = raw.get("worker_manifest_size_bytes")
    if (
        isinstance(manifest_size, bool)
        or not isinstance(manifest_size, int)
        or manifest_size <= 0
    ):
        raise ValueError("批量配体准备证据缺少有效的 worker manifest 大小。")
    error = prepared.get("error") if isinstance(prepared.get("error"), dict) else {}
    return {
        **raw,
        "status": status,
        "import_id": import_id,
        "record_id": record_id,
        "source_topology_sha256": source_topology_sha256.lower(),
        "error_code": str(error.get("code") or ""),
    }


def _screening_preparation_attempt_record(
    candidate: dict[str, Any],
    *,
    attempt: int,
    preparation_status: str,
) -> dict[str, Any]:
    error = (
        dict(candidate.get("error") or {})
        if isinstance(candidate.get("error"), dict)
        else {}
    )
    return {
        "attempt": int(attempt),
        "recorded_at": _now_iso(),
        "status": preparation_status,
        "source_topology_file": str(
            candidate.get("source_topology_file") or ""
        ),
        "source_topology_sha256": str(
            candidate.get("source_topology_sha256") or ""
        ),
        "source_topology_size_bytes": int(
            candidate.get("source_topology_size_bytes") or 0
        ),
        "chemical_facts": (
            dict(candidate.get("chemical_facts") or {})
            if isinstance(candidate.get("chemical_facts"), dict)
            else {}
        ),
        "preparation_evidence": (
            dict(candidate.get("preparation_evidence") or {})
            if isinstance(candidate.get("preparation_evidence"), dict)
            else {}
        ),
        "output_file": str(candidate.get("file") or ""),
        "output_sha256": str(candidate.get("sha256") or ""),
        "output_size_bytes": int(candidate.get("size_bytes") or 0),
        "error": error,
    }


def _screening_import_revision(import_preview: dict[str, Any]) -> str:
    canonical = json.loads(json.dumps(import_preview, ensure_ascii=False))
    canonical.pop("revision_sha256", None)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_screening_failure_manifest(
    root: Path,
    import_id: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    failures = [
        dict(candidate)
        for candidate in candidates
        if candidate.get("status") in {"invalid", "review_required"}
    ]
    import_relative = STAGING_IMPORTS_RELATIVE_PATH / import_id
    json_relative = import_relative / "preparation_failures.json"
    csv_relative = import_relative / "preparation_failures.csv"
    json_path = root / json_relative
    csv_path = root / csv_relative
    atomic_write_json(
        json_path,
        {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "import_id": import_id,
            "created_at": _now_iso(),
            "failure_count": len(failures),
            "records": failures,
        },
    )
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=(
            "candidate_id",
            "record_id",
            "status",
            "source_file",
            "source_record_index",
            "source_record_name",
            "source_topology_file",
            "source_topology_sha256",
            "error_code",
            "error_message",
            "retryable",
        ),
        lineterminator="\n",
    )
    writer.writeheader()
    for candidate in failures:
        error = (
            candidate.get("error")
            if isinstance(candidate.get("error"), dict)
            else {}
        )
        writer.writerow(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "record_id": candidate.get("record_id", ""),
                "status": candidate.get("status", ""),
                "source_file": candidate.get("source_file", ""),
                "source_record_index": candidate.get("source_record_index", ""),
                "source_record_name": candidate.get("source_record_name", ""),
                "source_topology_file": candidate.get("source_topology_file", ""),
                "source_topology_sha256": candidate.get(
                    "source_topology_sha256",
                    "",
                ),
                "error_code": error.get("code", ""),
                "error_message": error.get("message", ""),
                "retryable": candidate.get("retryable", False),
            }
        )
    atomic_write_text(csv_path, buffer.getvalue())
    return {
        "json_file": json_relative.as_posix(),
        "json_sha256": _sha256(json_path),
        "json_size_bytes": json_path.stat().st_size,
        "csv_file": csv_relative.as_posix(),
        "csv_sha256": _sha256(csv_path),
        "csv_size_bytes": csv_path.stat().st_size,
        "failure_count": len(failures),
    }


def _inventory_raw_screening_records(
    source: Path,
    limits: ScreeningResourceLimits,
) -> list[dict[str, Any]]:
    payload = source.read_bytes()
    records = (
        _split_sdf_record_bytes(payload)
        if source.suffix.lower() == ".sdf"
        else [payload]
    )
    if not records:
        records = [payload]
    inventory: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if len(record) > limits.max_ligand_bytes:
            raise ValueError(
                f"{source.name} 的第 {index} 条记录超过单配体资源上限 "
                f"{limits.max_ligand_bytes} B。"
            )
        first_line = record.splitlines()[0] if record.splitlines() else b""
        title = " ".join(first_line.decode("utf-8", errors="replace").split())
        inventory.append(
            {
                "source_record_index": index,
                "source_record_name": title[:240] or f"{source.stem} #{index}",
                "source_record_sha256": hashlib.sha256(record).hexdigest(),
                "source_record_size_bytes": len(record),
                "_source_record_bytes": record,
            }
        )
    return inventory


def _prepare_raw_screening_library(
    root: Path,
    source: Path,
    python_path: str,
    *,
    max_records: int,
    expected_sha256: str,
    expected_size_bytes: int,
    record_dir: Path | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Prepare every SDF/MOL record in one isolated worker invocation."""

    staging_root = root / STAGING_RELATIVE_PATH
    staging_root.mkdir(parents=True, exist_ok=True)
    script = staging_root / "prepare_ligand_library_rdkit_meeko.py"
    script_text = _screening_library_preparation_script_text()
    if not script.is_file() or script.read_text(encoding="utf-8") != script_text:
        atomic_write_text(script, script_text)

    temporary = tempfile.TemporaryDirectory(prefix=".library-import-", dir=staging_root)
    temporary_root = Path(temporary.name)
    source_snapshot = temporary_root / source.name
    _copy_snapshot(source, source_snapshot)
    if (
        source_snapshot.stat().st_size != expected_size_bytes
        or _sha256(source_snapshot) != expected_sha256
    ):
        temporary.cleanup()
        raise ValueError(f"{source.name} 在导入过程中发生变化。")
    manifest_path = temporary_root / "manifest.json"
    completed = subprocess.run(
        [
            python_path,
            "-I",
            "-B",
            str(script),
            str(source_snapshot),
            str(temporary_root),
            str(manifest_path),
            str(max_records),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if record_dir is not None:
        record_dir.mkdir(parents=True, exist_ok=False)
        atomic_write_text(record_dir / "stdout.txt", completed.stdout)
        atomic_write_text(record_dir / "stderr.txt", completed.stderr)
        atomic_write_json(
            record_dir / "command_result.json",
            {
                "schema_version": 1,
                "created_at": _now_iso(),
                "command": [
                    python_path,
                    "-I",
                    "-B",
                    str(script),
                    str(source_snapshot),
                    str(temporary_root),
                    str(manifest_path),
                    str(max_records),
                ],
                "cwd": str(root),
                "returncode": completed.returncode,
                "worker_script_sha256": hashlib.sha256(
                    script_text.encode("utf-8")
                ).hexdigest(),
                "source_sha256": expected_sha256,
                "source_size_bytes": expected_size_bytes,
            },
        )
    if completed.returncode != 0 or not manifest_path.is_file():
        detail = "\n".join(
            value
            for value in (completed.stderr.strip(), completed.stdout.strip())
            if value
        )
        temporary.cleanup()
        raise ValueError(
            f"{source.name} 配体库准备失败：{detail or '未生成导入清单'}"
        )
    if manifest_path.stat().st_size > 16 * 1024 * 1024:
        temporary.cleanup()
        raise ValueError(f"{source.name} 的导入清单超过大小上限。")
    manifest_bytes = manifest_path.read_bytes()
    if record_dir is not None:
        atomic_write_bytes(record_dir / "worker_manifest.json", manifest_bytes)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        temporary.cleanup()
        raise
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version")
        != SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
        or not isinstance(manifest.get("records"), list)
    ):
        temporary.cleanup()
        raise ValueError(f"{source.name} 的导入清单无效。")
    if manifest.get("limit_exceeded"):
        count = int(manifest.get("record_count") or max_records + 1)
        temporary.cleanup()
        raise ValueError(
            f"{source.name} 包含 {count} 条记录，超过本次剩余上限 {max_records}。"
        )

    normalized: list[dict[str, Any]] = []
    worker_evidence = {
        "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
        "preparation_profile": str(manifest.get("preparation_profile") or ""),
        "hydrogen_policy": str(manifest.get("hydrogen_policy") or ""),
        "toolchain": (
            dict(manifest.get("toolchain") or {})
            if isinstance(manifest.get("toolchain"), dict)
            else {}
        ),
        "worker_script_sha256": hashlib.sha256(
            script_text.encode("utf-8")
        ).hexdigest(),
        "worker_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "worker_manifest_size_bytes": len(manifest_bytes),
    }
    for value in manifest["records"]:
        if not isinstance(value, dict):
            temporary.cleanup()
            raise ValueError(f"{source.name} 的导入记录无效。")
        record = dict(value)
        record["_worker_evidence"] = worker_evidence
        if record.get("status") == "ready":
            output_name = str(record.get("pdbqt_file") or "")
            relative = Path(output_name)
            if (
                not output_name
                or relative.is_absolute()
                or len(relative.parts) != 1
                or relative.name != output_name
            ):
                temporary.cleanup()
                raise ValueError(f"{source.name} 的导入输出路径无效。")
            output_path = temporary_root / relative
            if not output_path.is_file() or not _looks_like_pdbqt(output_path):
                temporary.cleanup()
                raise ValueError(f"{source.name} 的导入输出不是有效 PDBQT。")
            record["_pdbqt_path"] = output_path
        elif record.get("status") not in {"invalid", "review_required"}:
            temporary.cleanup()
            raise ValueError(f"{source.name} 的导入记录状态无效。")
        normalized.append(record)
    return temporary, normalized


@_serialized_staging_mutation
def stage_screening_inputs(
    project_dir: str,
    files: list[str],
    *,
    resource_limits: dict[str, Any] | ScreeningResourceLimits | None = None,
) -> dict[str, Any]:
    """Import a ligand library into content-addressed, auditable staging."""

    temporary_directories: list[Any] = []
    root: Path | None = None
    import_id = ""
    import_root: Path | None = None
    import_started_at = ""
    try:
        root = _project_root(project_dir)
        limits = _validated_resource_limits(resource_limits)
        if not files:
            raise ValueError("至少需要一个待导入的配体文件。")
        originals = _expand_screening_input_files(files, limits)
        unique_originals: list[Path] = []
        seen_original_paths: set[str] = set()
        for original in originals:
            identity = str(original.resolve(strict=True)).casefold()
            if identity in seen_original_paths:
                continue
            seen_original_paths.add(identity)
            unique_originals.append(original)
        originals = unique_originals
        raw_values = [
            value for value in originals if value.suffix.lower() in {".sdf", ".mol"}
        ]
        python_path = ""

        source_bytes: dict[str, int] = {}
        source_identities: dict[Path, tuple[str, int]] = {}
        inventories: dict[Path, list[dict[str, Any]]] = {}
        for original in originals:
            size = original.stat().st_size
            if size <= 0:
                raise ValueError(f"配体文件不可用：{original}")
            if size > limits.max_staged_file_bytes:
                raise ValueError(
                    f"文件 {original.name} 超过 staging 单文件上限 "
                    f"{limits.max_staged_file_bytes} B。",
                )
            source_digest = _sha256(original)
            source_bytes.setdefault(source_digest, size)
            source_identities[original] = (source_digest, size)
            if original.suffix.lower() in {".sdf", ".mol"}:
                inventories[original] = _inventory_raw_screening_records(
                    original,
                    limits,
                )
        if sum(source_bytes.values()) > limits.max_total_input_bytes:
            raise ValueError("所选源文件总大小超过批量筛选资源上限。")

        logical_record_count = sum(
            len(inventories.get(original, [{}])) for original in originals
        )
        if logical_record_count > limits.max_ligands:
            raise ValueError(
                f"配体记录数量超过资源上限 {limits.max_ligands}。"
            )
        if raw_values:
            tool_status = get_preparation_tool_status(str(root))
            tools = (
                tool_status.get("tools")
                if isinstance(tool_status.get("tools"), dict)
                else {}
            )
            python_tool = (
                tools.get("python")
                if isinstance(tools.get("python"), dict)
                else {}
            )
            rdkit_tool = (
                tools.get("rdkit")
                if isinstance(tools.get("rdkit"), dict)
                else {}
            )
            meeko_tool = (
                tools.get("meeko")
                if isinstance(tools.get("meeko"), dict)
                else {}
            )
            if not tool_status.get("ok") or any(
                tool.get("status") != "ok"
                for tool in (python_tool, rdkit_tool, meeko_tool)
            ):
                raise ValueError(
                    "SDF/MOL 自动准备需要可用的内置 Python、RDKit 与 Meeko。"
                )
            python_path = str(python_tool.get("path") or "")

        index_path = root / STAGING_INDEX_RELATIVE_PATH
        index = _load_screening_staging_index(root)
        import_id = _next_screening_import_id(root, index)
        import_root = root / STAGING_IMPORTS_RELATIVE_PATH / import_id
        import_root.mkdir(parents=True, exist_ok=False)
        import_started_at = _now_iso()

        frozen_record_count = 0
        for original in raw_values:
            source_digest, _source_size = source_identities[original]
            for expected in inventories[original]:
                record_id = _screening_record_id(
                    source_digest,
                    int(expected["source_record_index"]),
                    str(expected["source_record_sha256"]),
                )
                topology = _freeze_screening_topology_record(
                    root,
                    import_id,
                    record_id,
                    original.suffix.lstrip("."),
                    bytes(expected["_source_record_bytes"]),
                    str(expected["source_record_sha256"]),
                    int(expected["source_record_size_bytes"]),
                )
                expected.update(topology)
                expected["record_id"] = record_id
                frozen_record_count += 1

        atomic_write_json(
            import_root / "import_manifest.json",
            {
                "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
                "import_id": import_id,
                "status": "preparing",
                "started_at": import_started_at,
                "source_files": [
                    {
                        "file": str(original),
                        "format": original.suffix.lower().lstrip("."),
                        "sha256": source_identities[original][0],
                        "size_bytes": source_identities[original][1],
                        "record_count": len(inventories.get(original, [{}])),
                    }
                    for original in originals
                ],
                "frozen_raw_record_count": frozen_record_count,
            },
        )

        prepared_records: list[dict[str, Any]] = []
        for source_number, original in enumerate(originals, start=1):
            suffix = original.suffix.lower()
            source_digest, source_size = source_identities[original]
            if (
                original.stat().st_size != source_size
                or _sha256(original) != source_digest
            ):
                raise ValueError(f"{original.name} 在导入过程中发生变化。")
            if suffix == ".pdbqt":
                source = _pdbqt_file(original, label="筛选输入")
                record_id = _screening_record_id(
                    source_digest,
                    1,
                    source_digest,
                )
                prepared_records.append(
                    {
                        "status": "ready",
                        "original": original,
                        "source": source,
                        "source_digest": source_digest,
                        "source_format": "pdbqt",
                        "source_record_index": 1,
                        "source_record_name": original.stem,
                        "source_record_sha256": source_digest,
                        "source_record_size_bytes": source.stat().st_size,
                        "prepared_during_import": False,
                        "record_id": record_id,
                        "source_topology_file": "",
                        "source_topology_sha256": "",
                        "source_topology_size_bytes": 0,
                        "topology_integrity": "not_available",
                        "chemical_facts": _unavailable_screening_chemical_facts(
                            "输入仅包含 PDBQT，无法可靠恢复原始键级和化学拓扑。",
                            source="supplied_pdbqt",
                        ),
                    }
                )
                continue

            temporary, worker_records = _prepare_raw_screening_library(
                root,
                original,
                python_path,
                max_records=limits.max_ligands,
                expected_sha256=source_digest,
                expected_size_bytes=source_size,
                record_dir=import_root / "workers" / f"source_{source_number:04d}",
            )
            temporary_directories.append(temporary)
            inventory = inventories[original]
            by_index: dict[int, dict[str, Any]] = {}
            worker_evidence: dict[str, Any] | None = None
            for value in worker_records:
                raw_index = value.get("source_record_index")
                if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                    raise ValueError(
                        f"{original.name} 的准备工具返回了无效 record index。"
                    )
                if raw_index < 1 or raw_index > len(inventory):
                    raise ValueError(
                        f"{original.name} 的准备工具返回了越界 record index：{raw_index}。"
                    )
                if raw_index in by_index:
                    raise ValueError(
                        f"{original.name} 的准备工具重复返回 record index：{raw_index}。"
                    )
                by_index[raw_index] = value
                if worker_evidence is None and isinstance(
                    value.get("_worker_evidence"),
                    dict,
                ):
                    worker_evidence = dict(value["_worker_evidence"])
            if inventory and worker_evidence is None:
                raise ValueError(f"{original.name} 的准备工具缺少审计证据。")
            for expected in inventory:
                record_index = int(expected["source_record_index"])
                worker = by_index.get(record_index)
                if worker is None:
                    worker = {
                        "status": "invalid",
                        "error": {
                            "code": "LIGAND_RECORD_NOT_RETURNED",
                                "message": "准备工具没有返回该分子记录。",
                            },
                        "_worker_evidence": worker_evidence,
                    }
                else:
                    returned_sha256 = str(
                        worker.get("source_record_sha256") or ""
                    ).lower()
                    returned_size = worker.get("source_record_size_bytes")
                    if (
                        returned_sha256
                        != str(expected["source_record_sha256"]).lower()
                        or isinstance(returned_size, bool)
                        or not isinstance(returned_size, int)
                        or returned_size
                        != int(expected["source_record_size_bytes"])
                    ):
                        raise ValueError(
                            f"{original.name} 的第 {record_index} 条准备结果"
                            "与冻结原始记录身份不一致。"
                        )
                prepared_records.append(
                    {
                        **worker,
                        "original": original,
                        "source": worker.get("_pdbqt_path"),
                        "source_digest": source_digest,
                        "source_format": suffix.lstrip("."),
                        "source_record_index": record_index,
                        "source_record_name": str(
                            worker.get("source_record_name")
                            or expected["source_record_name"]
                        ),
                        "source_record_sha256": expected[
                            "source_record_sha256"
                        ],
                        "source_record_size_bytes": expected[
                            "source_record_size_bytes"
                        ],
                        "prepared_during_import": True,
                        "record_id": expected["record_id"],
                        "source_topology_file": expected[
                            "source_topology_file"
                        ],
                        "source_topology_sha256": expected[
                            "source_topology_sha256"
                        ],
                        "source_topology_size_bytes": expected[
                            "source_topology_size_bytes"
                        ],
                        "topology_integrity": expected["topology_integrity"],
                    }
                )

        candidate_ids: set[str] = set()
        canonical_by_identity: dict[tuple[str, str], str] = {}
        staged_file_records: dict[str, dict[str, Any]] = {}
        import_records: dict[str, dict[str, Any]] = {}
        output_bytes: dict[str, int] = {}
        candidates: list[dict[str, Any]] = []
        staged: list[dict[str, Any]] = []
        for prepared in prepared_records:
            original = prepared["original"]
            record_index = int(prepared["source_record_index"])
            record_id = str(prepared["record_id"])
            candidate_base = (
                f"ligand_{prepared['source_digest'][:12]}_{record_index:04d}"
            )
            candidate_id = candidate_base
            collision = 2
            while candidate_id in candidate_ids:
                candidate_id = f"{candidate_base}_{collision:02d}"
                collision += 1
            candidate_ids.add(candidate_id)
            topology_sha256 = str(
                prepared.get("source_topology_sha256") or ""
            ).lower()
            raw_status = str(prepared.get("status") or "invalid")
            raw_error = (
                prepared.get("error")
                if isinstance(prepared.get("error"), dict)
                else {}
            )
            raw_error_code = str(raw_error.get("code") or "")
            if topology_sha256 and isinstance(
                prepared.get("chemical_facts"),
                dict,
            ):
                facts = _validated_screening_chemical_facts(
                    prepared.get("chemical_facts"),
                    source_topology_sha256=topology_sha256,
                )
            elif topology_sha256 and (
                raw_status in {"ready", "review_required"}
                or raw_error_code == "LIGAND_PREPARATION_FAILED"
            ):
                raise ValueError(
                    f"{original.name} 的第 {record_index} 条记录"
                    "缺少可审计化学事实。"
                )
            elif topology_sha256:
                facts = _unavailable_screening_chemical_facts(
                    "RDKit 未能从冻结原始记录生成完整化学事实。",
                    source="frozen_raw_topology",
                    source_topology_sha256=topology_sha256,
                )
            else:
                facts = (
                    dict(prepared.get("chemical_facts") or {})
                    if isinstance(prepared.get("chemical_facts"), dict)
                    else _unavailable_screening_chemical_facts(
                        "输入仅包含 PDBQT，无法可靠恢复原始键级和化学拓扑。",
                        source="supplied_pdbqt",
                    )
                )
            preparation_evidence = _screening_preparation_evidence(
                prepared,
                import_id=import_id,
                record_id=record_id,
                source_topology_sha256=topology_sha256,
                chemical_facts=facts,
            )
            candidate: dict[str, Any] = {
                "candidate_id": candidate_id,
                "import_id": import_id,
                "record_id": record_id,
                "status": str(prepared.get("status") or "invalid"),
                "source_file": str(original),
                "original_name": original.name,
                "source_format": str(prepared["source_format"]),
                "source_record_index": record_index,
                "source_record_name": str(prepared["source_record_name"]),
                "source_record_sha256": str(prepared["source_record_sha256"]),
                "source_record_size_bytes": int(
                    prepared["source_record_size_bytes"]
                ),
                "prepared_during_import": bool(
                    prepared["prepared_during_import"]
                ),
                "source_topology_file": str(
                    prepared.get("source_topology_file") or ""
                ),
                "source_topology_sha256": topology_sha256,
                "source_topology_size_bytes": int(
                    prepared.get("source_topology_size_bytes") or 0
                ),
                "topology_integrity": str(
                    prepared.get("topology_integrity") or "not_available"
                ),
                "chemical_facts": facts,
                "preparation_evidence": preparation_evidence,
                "preparation_attempts": [],
                "warnings": [],
            }
            if facts.get("has_macrocycle") is True and raw_status != "review_required":
                raw_status = "review_required"
                prepared["error"] = {
                    "code": "MACROCYCLE_REVIEW_REQUIRED",
                    "message": "检测到大环配体；批量准备不会静默采用自动断环。",
                    "suggestion": (
                        "请先使用单配体正式大环审查确认断环候选或刚性大环，"
                        "再导入受审查的 PDBQT。"
                    ),
                }
            if raw_status == "review_required" and facts.get(
                "has_macrocycle"
            ) is not True:
                raise ValueError(
                    f"{original.name} 的第 {record_index} 条宏环审查状态"
                    "与化学事实不一致。"
                )
            if raw_status != "ready":
                error = (
                    prepared.get("error")
                    if isinstance(prepared.get("error"), dict)
                    else {}
                )
                error_code = str(error.get("code") or "LIGAND_RECORD_INVALID")
                review_required = raw_status == "review_required"
                candidate["status"] = (
                    "review_required" if review_required else "invalid"
                )
                candidate["retryable"] = (
                    not review_required
                    and error_code
                    in {
                        "LIGAND_PREPARATION_FAILED",
                        "CHEMICAL_FACTS_FAILED",
                        "LIGAND_RECORD_NOT_RETURNED",
                    }
                )
                candidate["error"] = {
                    "code": error_code,
                    "message": str(
                        error.get("message")
                        or "该分子记录无法导入。"
                    ),
                    "raw_error": str(error.get("raw_error") or ""),
                    "suggestion": str(
                        error.get("suggestion")
                        or (
                            "请先在单配体流程完成正式大环审查，再导入受审查的 PDBQT。"
                            if review_required
                            else "请修复该条记录，或从失败清单重试准备。"
                        )
                    ),
                }
                candidate["preparation_attempts"] = [
                    _screening_preparation_attempt_record(
                        candidate,
                        attempt=1,
                        preparation_status=candidate["status"],
                    )
                ]
                candidates.append(candidate)
                import_records[record_id] = dict(candidate)
                continue

            source = prepared.get("source")
            if not isinstance(source, Path) or not source.is_file():
                raise ValueError(f"{original.name} 的准备输出不存在。")
            size_bytes = source.stat().st_size
            if size_bytes <= 0 or size_bytes > limits.max_ligand_bytes:
                raise ValueError(
                    f"{original.name} 的第 {record_index} 条 PDBQT 输出超过单配体资源上限。"
                )
            if not _looks_like_pdbqt(source):
                raise ValueError(
                    f"{original.name} 的第 {record_index} 条输出不是有效 PDBQT。"
                )
            digest = _sha256(source)
            output_bytes.setdefault(digest, size_bytes)
            relative = STAGING_RELATIVE_PATH / f"{digest}.pdbqt"
            destination = root / relative
            if destination.exists() and (
                not destination.is_file()
                or destination.stat().st_size != size_bytes
                or _sha256(destination) != digest
            ):
                raise ValueError(f"staging 目标已存在但内容校验失败：{relative.as_posix()}")
            if not destination.exists():
                _copy_snapshot(source, destination)
                if _sha256(destination) != digest:
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"导入后 SHA256 校验失败：{source.name}")

            record = ScreeningStagedInput(
                file=relative.as_posix(),
                original_name=original.name,
                sha256=digest,
                size_bytes=size_bytes,
                source_file=str(original),
                source_format=str(prepared["source_format"]),
                prepared_during_import=bool(
                    prepared["prepared_during_import"]
                ),
                source_record_index=record_index,
                source_record_name=str(prepared["source_record_name"]),
                source_record_sha256=str(prepared["source_record_sha256"]),
                source_topology_file=str(
                    prepared.get("source_topology_file") or ""
                ),
                source_topology_sha256=topology_sha256,
                source_topology_size_bytes=int(
                    prepared.get("source_topology_size_bytes") or 0
                ),
                topology_integrity=str(
                    prepared.get("topology_integrity") or "not_available"
                ),
                import_id=import_id,
                candidate_id=candidate_id,
                record_id=record_id,
                chemical_facts=facts,
                preparation_evidence=preparation_evidence,
            ).to_dict()
            record["staged_at"] = _now_iso()
            source_record = {
                key: candidate[key]
                for key in (
                    "candidate_id",
                    "import_id",
                    "record_id",
                    "source_file",
                    "original_name",
                    "source_format",
                    "source_record_index",
                    "source_record_name",
                    "source_record_sha256",
                    "source_record_size_bytes",
                    "prepared_during_import",
                    "source_topology_file",
                    "source_topology_sha256",
                    "source_topology_size_bytes",
                    "topology_integrity",
                    "chemical_facts",
                    "preparation_evidence",
                )
            }

            canonical_topology_sha256 = str(
                facts.get("canonical_topology_sha256") or ""
            ).lower()
            dedup_identity = (digest, canonical_topology_sha256)
            if dedup_identity in canonical_by_identity:
                candidate.update(
                    {
                        "status": "duplicate",
                        "file": relative.as_posix(),
                        "sha256": digest,
                        "size_bytes": size_bytes,
                        "duplicate_of": canonical_by_identity[dedup_identity],
                    }
                )
                candidate["preparation_attempts"] = [
                    _screening_preparation_attempt_record(
                        candidate,
                        attempt=1,
                        preparation_status="ready",
                    )
                ]
                candidates.append(candidate)
                import_records[record_id] = dict(candidate)
                physical = staged_file_records[digest]
                physical.setdefault("source_records", []).append(source_record)
                continue

            canonical_by_identity[dedup_identity] = candidate_id
            candidate.update(
                {
                    "status": "ready",
                    "file": relative.as_posix(),
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            )
            candidate["preparation_attempts"] = [
                _screening_preparation_attempt_record(
                    candidate,
                    attempt=1,
                    preparation_status="ready",
                )
            ]
            candidates.append(candidate)
            record["source_records"] = [source_record]
            if digest in staged_file_records:
                staged_file_records[digest].setdefault(
                    "source_records",
                    [],
                ).append(source_record)
            else:
                staged_file_records[digest] = dict(record)
            staged.append(record)
            import_records[record_id] = dict(candidate)

        if sum(output_bytes.values()) > limits.max_total_input_bytes:
            raise ValueError("唯一 PDBQT 快照总大小超过批量筛选资源上限。")

        existing_files = index["files"]
        for digest, record in staged_file_records.items():
            previous = existing_files.get(digest)
            if isinstance(previous, dict):
                combined: list[dict[str, Any]] = []
                seen_sources: set[tuple[str, int, str]] = set()
                for source_record in list(previous.get("source_records") or []) + list(
                    record.get("source_records") or []
                ):
                    if not isinstance(source_record, dict):
                        continue
                    key = (
                        str(source_record.get("source_record_sha256") or ""),
                        int(source_record.get("source_record_index") or 0),
                        str(source_record.get("source_file") or ""),
                    )
                    if key in seen_sources:
                        continue
                    seen_sources.add(key)
                    combined.append(source_record)
                record["source_records"] = combined
            existing_files[digest] = record

        index_records = index["records"]
        for record_id, record in import_records.items():
            index_records[record_id] = record

        counts = {
            "total": len(candidates),
            "ready": sum(item["status"] == "ready" for item in candidates),
            "duplicate": sum(
                item["status"] == "duplicate" for item in candidates
            ),
            "review_required": sum(
                item["status"] == "review_required" for item in candidates
            ),
            "invalid": sum(item["status"] == "invalid" for item in candidates),
        }
        failure_manifest = _write_screening_failure_manifest(
            root,
            import_id,
            candidates,
        )
        import_preview = {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "import_id": import_id,
            "created_at": import_started_at,
            "candidates": candidates,
            "summary": {
                **counts,
                "source_files": len(originals),
            },
            "resource_limits": limits.to_dict(),
            "resource_limits_sha256": _resource_limits_sha256(limits),
            "failure_manifest": failure_manifest,
        }
        import_preview["revision_sha256"] = _screening_import_revision(
            import_preview
        )

        index["updated_at"] = _now_iso()
        index["selected_files"] = list(
            dict.fromkeys(record["file"] for record in staged)
        )
        index["selected_candidate_ids"] = [
            candidate["candidate_id"]
            for candidate in candidates
            if candidate["status"] == "ready"
        ]
        index["last_import"] = import_preview
        final_manifest = {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "import_id": import_id,
            "status": "completed",
            "started_at": import_started_at,
            "finished_at": _now_iso(),
            "revision_sha256": import_preview["revision_sha256"],
            "summary": import_preview["summary"],
            "candidates": candidates,
            "failure_manifest": failure_manifest,
        }
        import_manifest_path = import_root / "import_manifest.json"
        atomic_write_json(import_manifest_path, final_manifest)
        index["imports"][import_id] = {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "status": "completed",
            "manifest_file": (
                STAGING_IMPORTS_RELATIVE_PATH
                / import_id
                / "import_manifest.json"
            ).as_posix(),
            "manifest_sha256": _sha256(import_manifest_path),
            "manifest_size_bytes": import_manifest_path.stat().st_size,
            "revision_sha256": import_preview["revision_sha256"],
            "summary": import_preview["summary"],
        }
        atomic_write_json(index_path, index)
        issue_summary = []
        if counts["duplicate"]:
            issue_summary.append(f"{counts['duplicate']} 条重复")
        if counts["review_required"]:
            issue_summary.append(f"{counts['review_required']} 条需正式审查")
        if counts["invalid"]:
            issue_summary.append(f"{counts['invalid']} 条失败")
        suffix = f"；另有{'、'.join(issue_summary)}" if issue_summary else ""
        return {
            "ok": True,
            "project_dir": str(root),
            "staged": staged,
            "import_preview": import_preview,
            "message": f"已导入 {len(staged)} 个可用配体{suffix}。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - workflow boundary returns structured errors.
        if (
            root is not None
            and import_root is not None
            and import_root.is_dir()
            and SCREENING_IMPORT_ID_PATTERN.fullmatch(import_id) is not None
        ):
            try:
                atomic_write_json(
                    import_root / "import_manifest.json",
                    {
                        "schema_version": (
                            SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
                        ),
                        "import_id": import_id,
                        "status": "failed",
                        "started_at": import_started_at,
                        "finished_at": _now_iso(),
                        "error": {
                            "code": "SCREENING_STAGE_ERROR",
                            "message": str(exc),
                        },
                    },
                )
            except Exception:
                pass
        return _error(
            "SCREENING_STAGE_ERROR",
            "导入批量筛选配体失败。",
            str(exc),
            "请检查文件格式、大小和项目目录写入权限。",
        )
    finally:
        for temporary in temporary_directories:
            temporary.cleanup()


def _preparation_retry_execution_evidence(
    root: Path,
    record_dir: Path,
    *,
    import_id: str,
    record_id: str,
    source_topology_sha256: str,
) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for name in ("stdout.txt", "stderr.txt", "command_result.json"):
        path = record_dir / name
        if path.is_file():
            files[name] = {
                "file": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
    return {
        "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
        "status": "execution_failed",
        "import_id": import_id,
        "record_id": record_id,
        "source_topology_sha256": source_topology_sha256,
        "files": files,
    }


@_serialized_staging_mutation
def retry_screening_preparation(
    project_dir: str,
    candidate_ids: list[str],
    *,
    expected_staging_revision_sha256: str,
) -> dict[str, Any]:
    """Retry selected failed preparations from immutable raw topology records."""

    temporary_directories: list[Any] = []
    try:
        root = _project_root(project_dir)
        if not candidate_ids:
            raise ValueError("至少需要选择一条可重试的失败记录。")
        preview, candidate_lookup = _last_screening_import_candidates(
            root,
            expected_revision_sha256=expected_staging_revision_sha256,
        )
        limits = _validated_resource_limits(
            preview.get("resource_limits")
            if isinstance(preview.get("resource_limits"), dict)
            else None
        )
        recorded_limits_sha256 = str(
            preview.get("resource_limits_sha256") or ""
        ).lower()
        if (
            recorded_limits_sha256
            and recorded_limits_sha256 != _resource_limits_sha256(limits)
        ):
            raise ValueError("当前导入记录的资源上限身份校验失败。")
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate_id in candidate_ids:
            if (
                not isinstance(candidate_id, str)
                or not candidate_id
                or candidate_id in seen
                or re.fullmatch(
                    r"ligand_[0-9a-f]{12}_\d{4}(?:_\d{2})?",
                    candidate_id,
                )
                is None
            ):
                raise ValueError("重试列表包含无效或重复的 candidate_id。")
            seen.add(candidate_id)
            candidate = candidate_lookup.get(candidate_id)
            if candidate is None:
                raise ValueError(f"没有找到失败记录：{candidate_id}")
            if (
                candidate.get("status") != "invalid"
                or candidate.get("retryable") is not True
            ):
                raise ValueError(
                    f"记录 {candidate_id} 当前不是可自动重试状态。"
                )
            topology = _verified_screening_topology_record(
                root,
                candidate,
                label=f"失败记录 {candidate_id}",
            )
            if topology is None:
                raise ValueError(
                    f"失败记录 {candidate_id} 没有可重试的冻结原始拓扑。"
                )
            selected.append(candidate)

        tool_status = get_preparation_tool_status(str(root))
        tools = (
            tool_status.get("tools")
            if isinstance(tool_status.get("tools"), dict)
            else {}
        )
        python_tool = (
            tools.get("python")
            if isinstance(tools.get("python"), dict)
            else {}
        )
        rdkit_tool = (
            tools.get("rdkit")
            if isinstance(tools.get("rdkit"), dict)
            else {}
        )
        meeko_tool = (
            tools.get("meeko")
            if isinstance(tools.get("meeko"), dict)
            else {}
        )
        if not tool_status.get("ok") or any(
            tool.get("status") != "ok"
            for tool in (python_tool, rdkit_tool, meeko_tool)
        ):
            raise ValueError(
                "重试配体准备需要可用的内置 Python、RDKit 与 Meeko。"
            )
        python_path = str(python_tool.get("path") or "")

        index = _load_screening_staging_index(root)
        import_id = str(preview.get("import_id") or "")
        if SCREENING_IMPORT_ID_PATTERN.fullmatch(import_id) is None:
            raise ValueError("当前导入记录缺少有效 import_id。")
        import_root = root / STAGING_IMPORTS_RELATIVE_PATH / import_id
        if not import_root.is_dir() or _path_is_link_or_reparse(import_root):
            raise ValueError("当前导入审计目录不存在或不安全。")

        updated_candidates = [
            dict(value)
            for value in preview["candidates"]
            if isinstance(value, dict)
        ]
        position_by_id = {
            str(value.get("candidate_id") or ""): index_value
            for index_value, value in enumerate(updated_candidates)
        }
        for original_candidate in selected:
            candidate_id = str(original_candidate["candidate_id"])
            candidate = json.loads(
                json.dumps(original_candidate, ensure_ascii=False)
            )
            attempts = [
                dict(value)
                for value in (
                    candidate.get("preparation_attempts")
                    if isinstance(candidate.get("preparation_attempts"), list)
                    else []
                )
                if isinstance(value, dict)
            ]
            highest_attempt = max(
                (
                    int(value.get("attempt") or 0)
                    for value in attempts
                    if not isinstance(value.get("attempt"), bool)
                ),
                default=0,
            )
            candidate_retry_root = (
                import_root / "retry_attempts" / candidate_id
            )
            if candidate_retry_root.is_dir():
                for path in candidate_retry_root.iterdir():
                    match = re.fullmatch(r"attempt_(\d{3,})", path.name)
                    if match:
                        highest_attempt = max(
                            highest_attempt,
                            int(match.group(1)),
                        )
            attempt_number = highest_attempt + 1
            record_dir = (
                candidate_retry_root / f"attempt_{attempt_number:03d}"
            )
            topology_path, _topology_relative, _old_facts = (
                _verified_screening_topology_record(
                    root,
                    candidate,
                    label=f"失败记录 {candidate_id}",
                )
                or (None, "", {})
            )
            assert topology_path is not None
            expected_sha256 = str(
                candidate.get("source_topology_sha256") or ""
            ).lower()
            expected_size = int(
                candidate.get("source_topology_size_bytes") or 0
            )
            try:
                temporary, worker_records = _prepare_raw_screening_library(
                    root,
                    topology_path,
                    python_path,
                    max_records=1,
                    expected_sha256=expected_sha256,
                    expected_size_bytes=expected_size,
                    record_dir=record_dir,
                )
                temporary_directories.append(temporary)
                if len(worker_records) != 1:
                    raise ValueError("重试准备工具必须且只能返回一条记录。")
                prepared = dict(worker_records[0])
                if (
                    int(prepared.get("source_record_index") or 0) != 1
                    or str(
                        prepared.get("source_record_sha256") or ""
                    ).lower()
                    != expected_sha256
                    or int(prepared.get("source_record_size_bytes") or 0)
                    != expected_size
                ):
                    raise ValueError("重试准备结果与冻结原始记录身份不一致。")
            except Exception as exc:  # noqa: BLE001 - isolate one failed retry.
                candidate["status"] = "invalid"
                candidate["retryable"] = True
                candidate["error"] = {
                    "code": "LIGAND_PREPARATION_RETRY_EXECUTION_FAILED",
                    "message": "重新运行配体准备工具失败。",
                    "raw_error": str(exc),
                    "suggestion": "请检查内置工具链后再次重试；冻结原始记录未被修改。",
                }
                candidate["preparation_evidence"] = (
                    _preparation_retry_execution_evidence(
                        root,
                        record_dir,
                        import_id=import_id,
                        record_id=str(candidate.get("record_id") or ""),
                        source_topology_sha256=expected_sha256,
                    )
                )
                attempts.append(
                    _screening_preparation_attempt_record(
                        candidate,
                        attempt=attempt_number,
                        preparation_status="invalid",
                    )
                )
                candidate["preparation_attempts"] = attempts
                updated_candidates[position_by_id[candidate_id]] = candidate
                continue

            raw_status = str(prepared.get("status") or "invalid")
            raw_error = (
                prepared.get("error")
                if isinstance(prepared.get("error"), dict)
                else {}
            )
            error_code = str(raw_error.get("code") or "")
            if isinstance(prepared.get("chemical_facts"), dict):
                facts = _validated_screening_chemical_facts(
                    prepared["chemical_facts"],
                    source_topology_sha256=expected_sha256,
                )
            elif raw_status in {"ready", "review_required"} or error_code == (
                "LIGAND_PREPARATION_FAILED"
            ):
                raise ValueError(
                    f"失败记录 {candidate_id} 的重试结果缺少化学事实。"
                )
            else:
                facts = _unavailable_screening_chemical_facts(
                    "RDKit 未能从冻结原始记录生成完整化学事实。",
                    source="frozen_raw_topology",
                    source_topology_sha256=expected_sha256,
                )
            if facts.get("has_macrocycle") is True:
                raw_status = "review_required"
                raw_error = {
                    "code": "MACROCYCLE_REVIEW_REQUIRED",
                    "message": "检测到大环配体；自动重试不会采用静默断环。",
                    "suggestion": (
                        "请先使用单配体正式大环审查确认断环候选或刚性大环，"
                        "再导入受审查的 PDBQT。"
                    ),
                }
                error_code = "MACROCYCLE_REVIEW_REQUIRED"
            preparation_evidence = _screening_preparation_evidence(
                prepared,
                import_id=import_id,
                record_id=str(candidate.get("record_id") or ""),
                source_topology_sha256=expected_sha256,
                chemical_facts=facts,
            )
            candidate["chemical_facts"] = facts
            candidate["preparation_evidence"] = preparation_evidence

            if raw_status != "ready":
                candidate["status"] = (
                    "review_required"
                    if raw_status == "review_required"
                    else "invalid"
                )
                candidate["retryable"] = (
                    raw_status != "review_required"
                    and error_code
                    in {
                        "LIGAND_PREPARATION_FAILED",
                        "CHEMICAL_FACTS_FAILED",
                        "LIGAND_RECORD_NOT_RETURNED",
                    }
                )
                candidate["error"] = {
                    "code": error_code or "LIGAND_RECORD_INVALID",
                    "message": str(
                        raw_error.get("message")
                        or "该分子记录仍无法准备为 PDBQT。"
                    ),
                    "raw_error": str(raw_error.get("raw_error") or ""),
                    "suggestion": str(
                        raw_error.get("suggestion")
                        or (
                            "请先完成正式大环审查。"
                            if raw_status == "review_required"
                            else "可再次重试，或检查原始结构和工具链日志。"
                        )
                    ),
                }
                candidate.pop("file", None)
                candidate.pop("sha256", None)
                candidate.pop("size_bytes", None)
                candidate.pop("duplicate_of", None)
                attempts.append(
                    _screening_preparation_attempt_record(
                        candidate,
                        attempt=attempt_number,
                        preparation_status=candidate["status"],
                    )
                )
                candidate["preparation_attempts"] = attempts
                updated_candidates[position_by_id[candidate_id]] = candidate
                continue

            source = prepared.get("_pdbqt_path")
            if not isinstance(source, Path) or not source.is_file():
                raise ValueError(
                    f"失败记录 {candidate_id} 的重试 PDBQT 不存在。"
                )
            if not _looks_like_pdbqt(source):
                raise ValueError(
                    f"失败记录 {candidate_id} 的重试输出不是有效 PDBQT。"
                )
            size_bytes = source.stat().st_size
            if size_bytes <= 0 or size_bytes > limits.max_ligand_bytes:
                raise ValueError(
                    f"失败记录 {candidate_id} 的重试 PDBQT 超过单配体资源上限。"
                )
            digest = _sha256(source)
            relative = STAGING_RELATIVE_PATH / f"{digest}.pdbqt"
            destination = root / relative
            if destination.exists():
                if (
                    not destination.is_file()
                    or destination.stat().st_size != size_bytes
                    or _sha256(destination) != digest
                ):
                    raise ValueError("重试输出的 staging 目标身份冲突。")
            else:
                _copy_snapshot(source, destination)
            duplicate_of = next(
                (
                    str(other.get("candidate_id") or "")
                    for other in updated_candidates
                    if str(other.get("candidate_id") or "") != candidate_id
                    and other.get("status") in {"ready", "duplicate"}
                    and str(other.get("sha256") or "").lower() == digest
                    and str(
                        (
                            other.get("chemical_facts")
                            if isinstance(other.get("chemical_facts"), dict)
                            else {}
                        ).get("canonical_topology_sha256")
                        or ""
                    ).lower()
                    == str(facts.get("canonical_topology_sha256") or "").lower()
                ),
                "",
            )
            candidate.update(
                {
                    "status": "duplicate" if duplicate_of else "ready",
                    "retryable": False,
                    "file": relative.as_posix(),
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            )
            candidate.pop("error", None)
            if duplicate_of:
                candidate["duplicate_of"] = duplicate_of
            else:
                candidate.pop("duplicate_of", None)
            attempts.append(
                _screening_preparation_attempt_record(
                    candidate,
                    attempt=attempt_number,
                    preparation_status="ready",
                )
            )
            candidate["preparation_attempts"] = attempts
            updated_candidates[position_by_id[candidate_id]] = candidate

            staged_record = ScreeningStagedInput(
                file=relative.as_posix(),
                original_name=str(candidate.get("original_name") or ""),
                sha256=digest,
                size_bytes=size_bytes,
                source_file=str(candidate.get("source_file") or ""),
                source_format=str(candidate.get("source_format") or ""),
                prepared_during_import=True,
                source_record_index=int(
                    candidate.get("source_record_index") or 1
                ),
                source_record_name=str(
                    candidate.get("source_record_name") or ""
                ),
                source_record_sha256=str(
                    candidate.get("source_record_sha256") or ""
                ),
                source_topology_file=str(
                    candidate.get("source_topology_file") or ""
                ),
                source_topology_sha256=expected_sha256,
                source_topology_size_bytes=expected_size,
                topology_integrity="verified",
                import_id=import_id,
                candidate_id=candidate_id,
                record_id=str(candidate.get("record_id") or ""),
                chemical_facts=facts,
                preparation_evidence=preparation_evidence,
                source_records=[dict(candidate)],
            ).to_dict()
            staged_record["staged_at"] = _now_iso()
            existing = index["files"].get(digest)
            if isinstance(existing, dict):
                sources = [
                    dict(value)
                    for value in (
                        existing.get("source_records")
                        if isinstance(existing.get("source_records"), list)
                        else []
                    )
                    if isinstance(value, dict)
                ]
                sources.append(dict(candidate))
                staged_record["source_records"] = sources
            index["files"][digest] = staged_record

        source_files = int(
            (
                preview.get("summary")
                if isinstance(preview.get("summary"), dict)
                else {}
            ).get("source_files")
            or 0
        )
        counts = {
            "total": len(updated_candidates),
            "ready": sum(
                item.get("status") == "ready" for item in updated_candidates
            ),
            "duplicate": sum(
                item.get("status") == "duplicate"
                for item in updated_candidates
            ),
            "review_required": sum(
                item.get("status") == "review_required"
                for item in updated_candidates
            ),
            "invalid": sum(
                item.get("status") == "invalid" for item in updated_candidates
            ),
            "source_files": source_files,
        }
        failure_manifest = _write_screening_failure_manifest(
            root,
            import_id,
            updated_candidates,
        )
        updated_preview = {
            **preview,
            "candidates": updated_candidates,
            "summary": counts,
            "failure_manifest": failure_manifest,
            "updated_at": _now_iso(),
        }
        updated_preview.pop("revision_sha256", None)
        updated_preview["revision_sha256"] = _screening_import_revision(
            updated_preview
        )
        index["last_import"] = updated_preview
        index["selected_candidate_ids"] = [
            str(item.get("candidate_id") or "")
            for item in updated_candidates
            if item.get("status") == "ready"
        ]
        index["selected_files"] = list(
            dict.fromkeys(
                str(item.get("file") or "")
                for item in updated_candidates
                if item.get("status") == "ready"
                and str(item.get("file") or "")
            )
        )
        for candidate in updated_candidates:
            record_id = str(candidate.get("record_id") or "")
            if SCREENING_RECORD_ID_PATTERN.fullmatch(record_id):
                index["records"][record_id] = dict(candidate)
        import_manifest_path = import_root / "import_manifest.json"
        atomic_write_json(
            import_manifest_path,
            {
                "schema_version": (
                    SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
                ),
                "import_id": import_id,
                "status": "completed",
                "started_at": str(preview.get("created_at") or ""),
                "finished_at": _now_iso(),
                "revision_sha256": updated_preview["revision_sha256"],
                "summary": counts,
                "candidates": updated_candidates,
                "failure_manifest": failure_manifest,
            },
        )
        index["imports"][import_id] = {
            "schema_version": SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION,
            "status": "completed",
            "manifest_file": (
                STAGING_IMPORTS_RELATIVE_PATH
                / import_id
                / "import_manifest.json"
            ).as_posix(),
            "manifest_sha256": _sha256(import_manifest_path),
            "manifest_size_bytes": import_manifest_path.stat().st_size,
            "revision_sha256": updated_preview["revision_sha256"],
            "summary": counts,
        }
        index["updated_at"] = _now_iso()
        atomic_write_json(root / STAGING_INDEX_RELATIVE_PATH, index)
        return {
            "ok": True,
            "project_dir": str(root),
            "import_preview": updated_preview,
            "staged": [
                dict(item)
                for item in updated_candidates
                if item.get("status") == "ready"
            ],
            "message": (
                f"已重试 {len(selected)} 条配体准备记录；"
                f"当前可入队 {counts['ready']} 条。"
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - workflow boundary.
        return _error(
            "SCREENING_PREPARATION_RETRY_ERROR",
            "重试批量配体准备失败。",
            str(exc),
            "请刷新导入预览，确认记录仍可重试且内置工具链可用。",
        )
    finally:
        for temporary in temporary_directories:
            temporary.cleanup()


def _active_job_artifacts(root: Path) -> list[Path]:
    screening_root = root / SCREENING_ROOT
    return [screening_root / name for name in ACTIVE_JOB_NAMES if (screening_root / name).exists()]


def _next_screening_id(root: Path) -> str:
    highest = 0
    archive_root = root / ARCHIVE_RELATIVE_PATH
    if archive_root.is_dir():
        for path in archive_root.iterdir():
            match = re.match(r"^screening_(\d+)(?:_|$)", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"screening_{highest + 1:03d}"


def _staging_records_by_file(root: Path) -> dict[str, dict[str, Any]]:
    index_path = root / STAGING_INDEX_RELATIVE_PATH
    if not index_path.is_file():
        return {}
    loaded = json.loads(index_path.read_text(encoding="utf-8"))
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema_version") != 1
        or not isinstance(loaded.get("files"), dict)
    ):
        raise ValueError("screening/staging/index.json 无效。")
    records: dict[str, dict[str, Any]] = {}
    for value in loaded["files"].values():
        if not isinstance(value, dict):
            continue
        relative = str(value.get("file") or "")
        if relative:
            records[relative] = value
    return records


def _verified_screening_topology_record(
    root: Path,
    record: dict[str, Any],
    *,
    label: str,
) -> tuple[Path, str, dict[str, Any]] | None:
    integrity = str(record.get("topology_integrity") or "not_available")
    relative = str(record.get("source_topology_file") or "")
    expected_sha256 = str(record.get("source_topology_sha256") or "").lower()
    expected_size = record.get("source_topology_size_bytes")
    if integrity == "not_available":
        if relative or expected_sha256 or int(expected_size or 0) != 0:
            raise ValueError(f"{label}的原始拓扑不可用状态与文件身份冲突。")
        facts = (
            dict(record.get("chemical_facts") or {})
            if isinstance(record.get("chemical_facts"), dict)
            else {}
        )
        if facts and facts.get("status") != "unavailable":
            raise ValueError(f"{label}的化学事实状态与 PDBQT-only 输入冲突。")
        return None
    if integrity != "verified":
        raise ValueError(f"{label}的原始拓扑完整性状态无效：{integrity}。")
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
    ):
        raise ValueError(f"{label}缺少有效的冻结原始拓扑身份。")
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or ".." in logical.parts
        or tuple(logical.parts[:3])
        != ("screening", "staging", "imports")
        or len(logical.parts) != 6
        or logical.parts[4] != "records"
    ):
        raise ValueError(f"{label}的冻结原始拓扑路径不在受控导入目录中。")
    import_id = logical.parts[3]
    record_file = logical.parts[5]
    recorded_import_id = str(
        record.get("import_id") or record.get("source_import_id") or ""
    )
    recorded_record_id = str(
        record.get("record_id") or record.get("source_record_id") or ""
    )
    if (
        SCREENING_IMPORT_ID_PATTERN.fullmatch(import_id) is None
        or (recorded_import_id and recorded_import_id != import_id)
        or (
            recorded_record_id
            and (
                SCREENING_RECORD_ID_PATTERN.fullmatch(recorded_record_id)
                is None
                or Path(record_file).stem != recorded_record_id
            )
        )
        or Path(record_file).suffix.lower() not in {".sdf", ".mol"}
    ):
        raise ValueError(f"{label}的冻结原始拓扑路径无效。")
    cursor = root
    for part in logical.parts:
        cursor = cursor / part
        if _path_is_link_or_reparse(cursor):
            raise ValueError(f"{label}的冻结原始拓扑路径包含链接或重解析点。")
    candidate = (root / Path(*logical.parts)).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label}的冻结原始拓扑越过项目目录。") from exc
    if (
        not candidate.is_file()
        or candidate.is_symlink()
        or candidate.stat().st_size != expected_size
        or _sha256(candidate).lower() != expected_sha256
    ):
        raise ValueError(f"{label}的冻结原始拓扑完整性校验失败。")
    facts = _validated_screening_chemical_facts(
        record.get("chemical_facts"),
        source_topology_sha256=expected_sha256,
    )
    return candidate, relative, facts


def _validated_stored_preparation_evidence(
    record: dict[str, Any],
    *,
    require_worker: bool,
) -> dict[str, Any]:
    evidence = (
        dict(record.get("preparation_evidence") or {})
        if isinstance(record.get("preparation_evidence"), dict)
        else {}
    )
    if not require_worker:
        if evidence.get("status") not in {"not_required", "ready"}:
            raise ValueError("PDBQT-only 配体缺少有效的导入准备证据。")
        return evidence
    if (
        evidence.get("schema_version")
        != SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
        or evidence.get("status") != "ready"
        or evidence.get("preparation_profile")
        != SCREENING_LIBRARY_PREPARATION_PROFILE
        or evidence.get("hydrogen_policy") != SCREENING_HYDROGEN_POLICY
    ):
        raise ValueError("冻结配体的准备证据不完整或状态不是 ready。")
    if (
        str(evidence.get("import_id") or "")
        != str(
            record.get("import_id")
            or record.get("source_import_id")
            or ""
        )
        or str(evidence.get("record_id") or "")
        != str(
            record.get("record_id")
            or record.get("source_record_id")
            or ""
        )
        or str(evidence.get("source_topology_sha256") or "").lower()
        != str(record.get("source_topology_sha256") or "").lower()
    ):
        raise ValueError("冻结配体的准备证据与候选身份不一致。")
    toolchain = evidence.get("toolchain")
    if not isinstance(toolchain, dict) or any(
        not str(toolchain.get(field) or "").strip()
        for field in ("python_version", "rdkit_version", "meeko_version")
    ):
        raise ValueError("冻结配体的准备证据缺少工具链快照。")
    facts = (
        record.get("chemical_facts")
        if isinstance(record.get("chemical_facts"), dict)
        else {}
    )
    if (
        facts.get("status") == "verified"
        and str(facts.get("rdkit_version") or "").strip()
        != str(toolchain.get("rdkit_version") or "").strip()
    ):
        raise ValueError("冻结配体化学事实与准备证据的 RDKit 版本不一致。")
    for field in ("worker_script_sha256", "worker_manifest_sha256"):
        if SHA256_PATTERN.fullmatch(str(evidence.get(field) or "").lower()) is None:
            raise ValueError(f"冻结配体的准备证据缺少有效的 {field}。")
    manifest_size = evidence.get("worker_manifest_size_bytes")
    if (
        isinstance(manifest_size, bool)
        or not isinstance(manifest_size, int)
        or manifest_size <= 0
    ):
        raise ValueError("冻结配体的准备证据缺少有效的 worker manifest 大小。")
    return evidence


def _last_screening_import_candidates(
    root: Path,
    *,
    expected_revision_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    index = _load_screening_staging_index(root)
    preview = (
        dict(index.get("last_import") or {})
        if isinstance(index.get("last_import"), dict)
        else {}
    )
    if (
        preview.get("schema_version")
        != SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
        or not isinstance(preview.get("candidates"), list)
    ):
        raise ValueError("当前 staging 没有可按候选身份创建的 v2 导入记录。")
    recorded_revision = str(preview.get("revision_sha256") or "").lower()
    calculated_revision = _screening_import_revision(preview)
    expected_revision = str(expected_revision_sha256 or "").lower()
    if (
        SHA256_PATTERN.fullmatch(recorded_revision) is None
        or recorded_revision != calculated_revision
    ):
        raise ValueError("当前批量配体导入 revision 校验失败。")
    if (
        SHA256_PATTERN.fullmatch(expected_revision) is None
        or expected_revision != recorded_revision
    ):
        raise ValueError("批量配体候选已经变化，请刷新导入预览后重新创建任务。")
    candidates: dict[str, dict[str, Any]] = {}
    for value in preview["candidates"]:
        if not isinstance(value, dict):
            raise ValueError("批量配体导入预览包含无效候选。")
        candidate_id = str(value.get("candidate_id") or "")
        if not candidate_id or candidate_id in candidates:
            raise ValueError("批量配体导入预览包含缺失或重复的 candidate_id。")
        candidates[candidate_id] = dict(value)
    return preview, candidates


def _legacy_staging_record_is_unambiguous(record: dict[str, Any]) -> bool:
    source_records = (
        record.get("source_records")
        if isinstance(record.get("source_records"), list)
        else []
    )
    canonical_topologies: set[str] = set()
    has_verified_topology = False
    for source_record in source_records:
        if not isinstance(source_record, dict):
            continue
        if source_record.get("topology_integrity") == "verified":
            has_verified_topology = True
            facts = (
                source_record.get("chemical_facts")
                if isinstance(source_record.get("chemical_facts"), dict)
                else {}
            )
            canonical = str(facts.get("canonical_topology_sha256") or "").lower()
            if SHA256_PATTERN.fullmatch(canonical) is None:
                return False
            canonical_topologies.add(canonical)
    return not has_verified_topology or len(canonical_topologies) <= 1


@_serialized_staging_mutation
def create_screening(
    project_dir: str,
    receptor_file: str,
    ligand_files: list[str],
    *,
    ligand_candidate_ids: list[str] | None = None,
    expected_staging_revision_sha256: str = "",
    vina_path: str | None = None,
    box: dict[str, Any],
    vina: dict[str, Any],
    max_retries: int = 1,
    top_n: int = 20,
    resource_limits: dict[str, Any] | ScreeningResourceLimits | None = None,
) -> dict[str, Any]:
    """Create a screening job without changing the legacy project model."""

    pending_inputs_root: Path | None = None
    published_inputs_root: Path | None = None
    screening_root_for_cleanup: Path | None = None
    try:
        root = _project_root(project_dir)
        protocol_context, protocol_error = _screening_protocol_context(
            root,
            requested_scoring=str(vina.get("scoring") or ""),
        )
        if protocol_error:
            return protocol_error
        assert protocol_context is not None
        scoring_protocol = str(
            protocol_context.get("scoring_protocol") or "vina"
        )
        ad4_maps_status = (
            protocol_context.get("maps_status")
            if isinstance(protocol_context.get("maps_status"), dict)
            else None
        )
        if _state_path(root).exists():
            return _error(
                "SCREENING_ALREADY_EXISTS",
                "当前项目已经存在批量筛选任务。",
                str(_state_path(root)),
                "请继续、恢复或归档现有任务后再创建新任务。",
            )
        orphaned = _active_job_artifacts(root)
        if orphaned:
            return _error(
                "SCREENING_ORPHANED_DATA",
                "检测到未归档的批量筛选文件，已拒绝覆盖。",
                ", ".join(str(path) for path in orphaned),
                "请先恢复原任务或人工核对并归档这些文件。",
            )
        limits = _validated_resource_limits(resource_limits)
        max_retries = _strict_integer(
            max_retries,
            "max_retries",
            minimum=0,
            maximum=limits.max_retries,
        )
        selected_candidate_ids = list(ligand_candidate_ids or [])
        candidate_mode = bool(selected_candidate_ids)
        input_count = (
            len(selected_candidate_ids) if candidate_mode else len(ligand_files)
        )
        if input_count == 0:
            raise ValueError("至少需要一个配体 PDBQT。")
        if input_count > limits.max_ligands:
            raise ValueError(f"配体数量超过资源上限 {limits.max_ligands}。")
        top_n = _strict_integer(
            top_n,
            "top_n",
            minimum=1,
            maximum=limits.max_ligands,
        )

        receptor_path, receptor_relative = _project_file(root, receptor_file, label="受体")
        ligand_inputs: list[tuple[Path, str, dict[str, Any]]] = []
        selected_import_preview: dict[str, Any] | None = None
        if candidate_mode:
            selected_import_preview, candidate_lookup = (
                _last_screening_import_candidates(
                    root,
                    expected_revision_sha256=(
                        expected_staging_revision_sha256
                    ),
                )
            )
            seen_candidates: set[str] = set()
            for candidate_id in selected_candidate_ids:
                if (
                    not isinstance(candidate_id, str)
                    or not candidate_id
                    or candidate_id in seen_candidates
                ):
                    raise ValueError("候选列表包含空值或重复 candidate_id。")
                seen_candidates.add(candidate_id)
                provenance = candidate_lookup.get(candidate_id)
                if provenance is None:
                    raise ValueError(f"没有找到配体候选：{candidate_id}")
                if provenance.get("status") != "ready":
                    raise ValueError(
                        f"配体候选 {candidate_id} 不是可入队的 ready 状态。"
                    )
                path, relative = _project_file(
                    root,
                    str(provenance.get("file") or ""),
                    label=f"配体候选 {candidate_id}",
                )
                if (
                    str(provenance.get("sha256") or "").lower()
                    != _sha256(path).lower()
                    or int(provenance.get("size_bytes") or 0)
                    != path.stat().st_size
                ):
                    raise ValueError(
                        f"配体候选 {candidate_id} 的 PDBQT 身份校验失败。"
                    )
                ligand_inputs.append((path, relative, dict(provenance)))
            if ligand_files:
                supplied_relatives = [
                    _project_file(root, ligand, label="配体")[1]
                    for ligand in ligand_files
                ]
                selected_relatives = [
                    relative for _path, relative, _record in ligand_inputs
                ]
                if supplied_relatives != selected_relatives:
                    raise ValueError(
                        "ligand_files 与 ligand_candidate_ids 指向的快照不一致。"
                    )
        else:
            staged_records = _staging_records_by_file(root)
            seen_files: set[str] = set()
            for ligand in ligand_files:
                path, relative = _project_file(root, ligand, label="配体")
                key = relative.casefold()
                if key in seen_files:
                    raise ValueError(f"配体列表包含重复文件：{relative}")
                seen_files.add(key)
                provenance = staged_records.get(relative, {})
                if provenance and not _legacy_staging_record_is_unambiguous(
                    provenance
                ):
                    raise ValueError(
                        f"staging 配体 {relative} 关联多个不同原始拓扑；"
                        "请刷新导入预览并按 candidate_id 创建任务。"
                    )
                ligand_inputs.append((path, relative, provenance))
            ligand_inputs.sort(
                key=lambda entry: (entry[1].casefold(), entry[1])
            )

        for path, relative, _provenance in ligand_inputs:
            if path.stat().st_size > limits.max_ligand_bytes:
                raise ValueError(f"配体文件超过单文件资源上限：{relative}")
        ligand_entries = [
            (path, relative) for path, relative, _record in ligand_inputs
        ]
        total_bytes = receptor_path.stat().st_size + sum(
            path.stat().st_size for path, _relative, _record in ligand_inputs
        )
        if ad4_maps_status is not None:
            ad4_manifest = (
                ad4_maps_status.get("manifest")
                if isinstance(ad4_maps_status.get("manifest"), dict)
                else {}
            )
            total_bytes += sum(
                int(item.get("size_bytes") or 0)
                for item in (
                    (ad4_manifest.get("maps") or {}).get("files") or []
                )
                if isinstance(item, dict)
            )
        if total_bytes > limits.max_total_input_bytes:
            raise ValueError("受体和配体输入总大小超过批量筛选资源上限。")

        requested_vina = dict(vina)
        if scoring_protocol == "ad4_maps":
            requested_vina.update(
                {
                    "scoring": "ad4",
                    "no_refine": False,
                    "force_even_voxels": False,
                }
            )
        parameter_warnings = _screening_parameter_warnings(requested_vina)
        normalized_box, normalized_vina = _validate_settings(
            box,
            requested_vina,
            limits,
        )
        if ad4_maps_status is not None:
            manifest = (
                ad4_maps_status.get("manifest")
                if isinstance(ad4_maps_status.get("manifest"), dict)
                else {}
            )
            available_types = {
                str(value or "").strip().upper()
                for value in (manifest.get("maps") or {}).get(
                    "ligand_atom_types",
                    [],
                )
            }
            required_types = {
                str(value or "").strip().upper()
                for ligand_path, relative in ligand_entries
                for value in (
                    _parse_pdbqt_stats(
                        ligand_path,
                        relative,
                        ligand=True,
                    ).get("atom_types")
                    or []
                )
            }
            missing_types = sorted(required_types - available_types)
            if missing_types:
                return _error(
                    "SCREENING_AD4_MAP_TYPES_MISSING",
                    "当前 AD4 maps 未覆盖批量配体库所需的全部原子类型。",
                    ", ".join(missing_types),
                    "请在 AutoGrid4 面板加入缺失的配体原子类型并重新生成 maps。",
                )
            grid_validation = {
                "ok": True,
                "grid_resource": {
                    "source": "ad4_maps_manifest",
                    "map_set_id": str(ad4_maps_status.get("map_set_id") or ""),
                    "grid": json.loads(json.dumps(manifest.get("grid") or {})),
                    "warnings": [],
                },
                "error": None,
            }
        else:
            grid_validation = _screening_grid_resource(
                normalized_box,
                normalized_vina,
                ligand_entries,
            )
            if not grid_validation.get("ok"):
                return grid_validation
        vina_tool, vina_capabilities = _resolve_vina_tool(vina_path)
        capability_validation = validate_vina_runtime_capabilities(
            normalized_vina,
            scoring_protocol,
            vina_capabilities,
            run_mode="dock",
            receptor_mode="rigid",
        )
        if not capability_validation.get("ok"):
            return capability_validation

        preflight: list[dict[str, Any]] = []
        for index, (
            ligand_path,
            source_relative,
            provenance,
        ) in enumerate(ligand_inputs, start=1):
            item_id = f"ligand_{index:04d}"
            if source_relative.startswith(
                f"{STAGING_RELATIVE_PATH.as_posix()}/"
            ):
                if not provenance:
                    raise ValueError(
                        f"staging 配体缺少来源索引记录：{source_relative}"
                    )
                if (
                    str(provenance.get("file") or "") != source_relative
                    or str(provenance.get("sha256") or "").lower()
                    != _sha256(ligand_path).lower()
                    or int(provenance.get("size_bytes") or 0)
                    != ligand_path.stat().st_size
                ):
                    raise ValueError(
                        f"staging 配体来源索引校验失败：{source_relative}"
                    )
            topology = (
                _verified_screening_topology_record(
                    root,
                    provenance,
                    label=f"配体 {item_id}",
                )
                if provenance
                else None
            )
            has_candidate_identity = bool(
                provenance.get("candidate_id")
                or provenance.get("record_id")
                or provenance.get("import_id")
            )
            preparation_evidence = (
                _validated_stored_preparation_evidence(
                    provenance,
                    require_worker=topology is not None,
                )
                if has_candidate_identity
                else {
                    "schema_version": (
                        SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
                    ),
                    "status": "not_recorded",
                    "source": "legacy_or_local_pdbqt",
                }
            )
            preflight.append(
                {
                    "item_id": item_id,
                    "topology": topology,
                    "preparation_evidence": preparation_evidence,
                }
            )

        screening_root = root / SCREENING_ROOT
        screening_root.mkdir(parents=True, exist_ok=True)
        screening_root_for_cleanup = screening_root
        pending_inputs_root = Path(
            tempfile.mkdtemp(prefix=".inputs-", dir=screening_root)
        )
        inputs_root = pending_inputs_root
        receptor_snapshot = inputs_root / "receptor.pdbqt"
        _copy_snapshot(receptor_path, receptor_snapshot)
        frozen_ad4_maps = (
            _freeze_screening_ad4_maps(root, inputs_root, ad4_maps_status)
            if ad4_maps_status is not None
            else None
        )
        items: list[ScreeningItem] = []
        topology_items: list[dict[str, Any]] = []
        library_manifest_records: list[dict[str, Any]] = []
        for index, (
            ligand_path,
            source_relative,
            provenance,
        ) in enumerate(ligand_inputs, start=1):
            item_id = f"ligand_{index:04d}"
            relative_snapshot = Path("screening", "inputs", "ligands", f"{item_id}.pdbqt")
            snapshot_path = inputs_root / "ligands" / f"{item_id}.pdbqt"
            _copy_snapshot(ligand_path, snapshot_path)
            topology = preflight[index - 1]["topology"]
            if topology is not None:
                topology_source, topology_origin_relative, chemical_facts = (
                    topology
                )
                topology_relative = Path(
                    "screening",
                    "inputs",
                    "topology",
                    f"{item_id}{topology_source.suffix.lower()}",
                )
                topology_snapshot = (
                    inputs_root
                    / "topology"
                    / f"{item_id}{topology_source.suffix.lower()}"
                )
                _copy_snapshot(topology_source, topology_snapshot)
                topology_sha256 = _sha256(topology_snapshot)
                topology_size_bytes = topology_snapshot.stat().st_size
                if (
                    topology_sha256
                    != str(provenance.get("source_topology_sha256") or "").lower()
                    or topology_size_bytes
                    != int(provenance.get("source_topology_size_bytes") or 0)
                ):
                    raise ValueError(
                        f"配体 {item_id} 的队列原始拓扑快照校验失败。"
                    )
                topology_integrity = "verified"
                topology_items.append(
                    {
                        "item_id": item_id,
                        "file": topology_relative.as_posix(),
                        "sha256": topology_sha256,
                        "size_bytes": topology_size_bytes,
                        "source_file": topology_origin_relative,
                    }
                )
            else:
                topology_relative = Path()
                topology_sha256 = ""
                topology_size_bytes = 0
                topology_integrity = "not_available"
                chemical_facts = (
                    dict(provenance.get("chemical_facts") or {})
                    if isinstance(provenance.get("chemical_facts"), dict)
                    else _unavailable_screening_chemical_facts(
                        "任务输入没有冻结原始拓扑。",
                        source="legacy_or_local_pdbqt",
                    )
                )

            preparation_evidence = preflight[index - 1][
                "preparation_evidence"
            ]

            if candidate_mode:
                source_records = [
                    {
                        key: provenance.get(key)
                        for key in (
                            "candidate_id",
                            "import_id",
                            "record_id",
                            "source_file",
                            "original_name",
                            "source_format",
                            "source_record_index",
                            "source_record_name",
                            "source_record_sha256",
                            "source_record_size_bytes",
                            "prepared_during_import",
                            "source_topology_file",
                            "source_topology_sha256",
                            "source_topology_size_bytes",
                            "topology_integrity",
                            "chemical_facts",
                            "preparation_evidence",
                        )
                    }
                ]
            else:
                source_records = [
                    dict(value)
                    for value in (
                        provenance.get("source_records")
                        if isinstance(provenance.get("source_records"), list)
                        else []
                    )
                    if isinstance(value, dict)
                ]
            source_record_name = str(
                provenance.get("source_record_name") or ""
            ).strip()
            display_label = _screening_source_label(
                provenance,
                fallback_file=source_relative,
                fallback=item_id,
            )
            items.append(
                ScreeningItem(
                    item_id=item_id,
                    order=index,
                    ligand_file=relative_snapshot.as_posix(),
                    source_file=source_relative,
                    sha256=_sha256(snapshot_path),
                    size_bytes=snapshot_path.stat().st_size,
                    source_original_file=str(
                        provenance.get("source_file") or ""
                    ),
                    source_format=str(provenance.get("source_format") or ""),
                    source_record_index=int(
                        provenance.get("source_record_index") or 1
                    ),
                    source_record_name=source_record_name,
                    source_record_sha256=str(
                        provenance.get("source_record_sha256") or ""
                    ),
                    source_topology_file=(
                        topology_relative.as_posix()
                        if topology is not None
                        else ""
                    ),
                    source_topology_sha256=topology_sha256,
                    source_topology_size_bytes=topology_size_bytes,
                    topology_integrity=topology_integrity,
                    source_import_id=str(
                        provenance.get("import_id") or ""
                    ),
                    source_candidate_id=str(
                        provenance.get("candidate_id") or ""
                    ),
                    source_record_id=str(
                        provenance.get("record_id") or ""
                    ),
                    chemical_facts=chemical_facts,
                    preparation_evidence=preparation_evidence,
                    source_records=source_records,
                    display_label=display_label,
                ),
            )
            library_manifest_records.append(
                {
                    "item_id": item_id,
                    "source_file": source_relative,
                    "pdbqt_file": relative_snapshot.as_posix(),
                    "pdbqt_sha256": _sha256(snapshot_path),
                    "pdbqt_size_bytes": snapshot_path.stat().st_size,
                    "import_id": str(provenance.get("import_id") or ""),
                    "candidate_id": str(
                        provenance.get("candidate_id") or ""
                    ),
                    "record_id": str(provenance.get("record_id") or ""),
                    "source_record_sha256": str(
                        provenance.get("source_record_sha256") or ""
                    ),
                    "topology_integrity": topology_integrity,
                    "topology_file": (
                        topology_relative.as_posix()
                        if topology is not None
                        else ""
                    ),
                    "topology_sha256": topology_sha256,
                    "topology_size_bytes": topology_size_bytes,
                    "chemical_facts": chemical_facts,
                    "preparation_evidence": preparation_evidence,
                }
            )

        topology_status = (
            "complete"
            if len(topology_items) == len(items)
            else "partial"
            if topology_items
            else "unavailable"
        )
        library_manifest_relative = Path(
            "screening",
            "inputs",
            "library_import_manifest.json",
        )
        library_manifest_path = inputs_root / "library_import_manifest.json"
        atomic_write_json(
            library_manifest_path,
            {
                "schema_version": (
                    SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
                ),
                "created_at": _now_iso(),
                "selection_mode": (
                    "candidate_ids" if candidate_mode else "legacy_files"
                ),
                "import_id": str(
                    (selected_import_preview or {}).get("import_id") or ""
                ),
                "staging_revision_sha256": str(
                    (selected_import_preview or {}).get("revision_sha256")
                    or ""
                ),
                "topology_status": topology_status,
                "topology_count": len(topology_items),
                "item_count": len(items),
                "items": library_manifest_records,
            },
        )
        library_manifest_identity = {
            "file": library_manifest_relative.as_posix(),
            "sha256": _sha256(library_manifest_path),
            "size_bytes": library_manifest_path.stat().st_size,
        }

        created_at = _now_iso()
        state: dict[str, Any] = {
            "schema_version": SCREENING_SCHEMA_VERSION,
            "screening_id": _next_screening_id(root),
            "status": "ready",
            "created_at": created_at,
            "updated_at": created_at,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "scoring_protocol": scoring_protocol,
            "scoring_function": (
                "ad4" if scoring_protocol == "ad4_maps" else normalized_vina["scoring"]
            ),
            "vina_path": vina_tool.path,
            "tools": {
                "vina": {
                    **vina_tool.to_dict(),
                    "size_bytes": Path(vina_tool.path).stat().st_size,
                    "capabilities": vina_capabilities,
                }
            },
            "inputs": {
                "receptor": {
                    "source_file": receptor_relative,
                    "file": Path("screening", "inputs", "receptor.pdbqt").as_posix(),
                    "sha256": _sha256(receptor_snapshot),
                    "size_bytes": receptor_snapshot.stat().st_size,
                },
                **(
                    {"ad4_maps": frozen_ad4_maps}
                    if frozen_ad4_maps is not None
                    else {}
                ),
                "raw_ligand_topology_available": (
                    topology_status == "complete"
                ),
                "topology": {
                    "status": topology_status,
                    "available_count": len(topology_items),
                    "total_count": len(items),
                    "items": topology_items,
                },
                "library_import_manifest": library_manifest_identity,
            },
            "box": normalized_box,
            "vina": normalized_vina,
            "parameter_warnings": parameter_warnings,
            "grid_resource": grid_validation["grid_resource"],
            "max_retries": int(max_retries),
            "top_n": int(top_n),
            "resource_limits": limits.to_dict(),
            "resource_limits_sha256": _resource_limits_sha256(limits),
            "queue": [item.item_id for item in items],
            "items": [item.to_dict() for item in items],
            "outputs": {
                "summary_csv": "",
                "summary_sha256": "",
                "summary_size_bytes": 0,
                "top_n_csv": "",
                "top_n_sha256": "",
                "top_n_size_bytes": 0,
                "report_md": "",
                "report_sha256": "",
                "report_size_bytes": 0,
                "reported_at": "",
                "sdf": {
                    "generated": False,
                    "status": (
                        "pending"
                        if topology_status == "complete"
                        else "pending_partial"
                        if topology_status == "partial"
                        else "unavailable"
                    ),
                    "file": "",
                    "reason": (
                        "筛选完成后将从冻结原始拓扑和成功构象生成汇总 SDF。"
                        if topology_status == "complete"
                        else (
                            "仅部分配体具有冻结原始拓扑；汇总 SDF 将明确标记覆盖范围。"
                            if topology_status == "partial"
                            else "未提供原始配体拓扑；PDBQT 不包含可靠键级，未生成 SDF。"
                        )
                    ),
                    "topology_coverage": {
                        "available_count": len(topology_items),
                        "total_count": len(items),
                    },
                },
            },
        }
        final_inputs_root = screening_root / "inputs"
        if final_inputs_root.exists():
            raise ValueError("screening/inputs 在任务发布前已经存在，已拒绝覆盖。")
        os.replace(pending_inputs_root, final_inputs_root)
        published_inputs_root = final_inputs_root
        pending_inputs_root = None
        _write_state(root, state)
        return {
            "ok": True,
            "project_dir": str(root),
            "state_file": STATE_RELATIVE_PATH.as_posix(),
            "screening": state,
            "message": f"批量筛选任务已创建，共 {len(items)} 个配体。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - CLI/workflow boundary returns structured errors.
        if (
            pending_inputs_root is not None
            and screening_root_for_cleanup is not None
            and pending_inputs_root.is_dir()
        ):
            try:
                _safe_rmtree(
                    pending_inputs_root,
                    expected_parent=screening_root_for_cleanup,
                )
            except Exception:
                pass
        if (
            published_inputs_root is not None
            and screening_root_for_cleanup is not None
            and published_inputs_root.is_dir()
            and not (screening_root_for_cleanup / "screening.json").exists()
        ):
            try:
                _safe_rmtree(
                    published_inputs_root,
                    expected_parent=screening_root_for_cleanup,
                )
            except Exception:
                pass
        return _error(
            "SCREENING_CREATE_ERROR",
            "创建批量筛选任务失败。",
            str(exc),
            "请检查输入文件、Vina 路径和资源上限。",
        )


def get_screening_status(project_dir: str) -> dict[str, Any]:
    try:
        root = _project_root(project_dir)
        index_path = root / STAGING_INDEX_RELATIVE_PATH
        staged: list[dict[str, Any]] = []
        import_preview: dict[str, Any] | None = None
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(index.get("last_import"), dict):
                import_preview = index["last_import"]
            if (
                isinstance(import_preview, dict)
                and import_preview.get("schema_version")
                == SCREENING_LIBRARY_PREPARATION_SCHEMA_VERSION
                and isinstance(import_preview.get("candidates"), list)
            ):
                selected_candidate_ids = index.get("selected_candidate_ids")
                selected = (
                    {
                        str(value)
                        for value in selected_candidate_ids
                        if str(value)
                    }
                    if isinstance(selected_candidate_ids, list)
                    else set()
                )
                staged = [
                    dict(item)
                    for item in import_preview["candidates"]
                    if isinstance(item, dict)
                    and item.get("status") == "ready"
                    and (
                        not selected
                        or str(item.get("candidate_id") or "") in selected
                    )
                ]
            files = index.get("files") if isinstance(index, dict) else None
            if isinstance(files, dict) and not staged:
                records = [
                    item for item in files.values() if isinstance(item, dict)
                ]
                selected_files = index.get("selected_files")
                if isinstance(selected_files, list):
                    selected = {
                        str(value) for value in selected_files if str(value)
                    }
                    staged = [
                        item
                        for item in records
                        if str(item.get("file") or "") in selected
                    ]
                else:
                    staged = records
        state = _read_state(root) if _state_path(root).is_file() else None
        return {
            "ok": True,
            "project_dir": str(root),
            "state_file": STATE_RELATIVE_PATH.as_posix() if state else "",
            "screening": state,
            "staged": staged,
            "import_preview": import_preview,
            "mode": "batch" if len(staged) > 1 or state else "single",
            "message": "批量筛选状态已读取。" if state else f"已读取 {len(staged)} 个待筛选配体快照。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_READ_ERROR", "读取批量筛选状态失败。", str(exc))


def _safe_rmtree(path: Path, *, expected_parent: Path) -> None:
    resolved_parent = path.parent.resolve(strict=True)
    if resolved_parent != expected_parent.resolve(strict=True):
        raise ValueError(f"拒绝删除 screening 目录之外的路径：{path}")
    if path.is_dir():
        shutil.rmtree(path)


def _read_archive_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_METADATA_MISSING",
            f"归档缺少 {label}：{path.name}",
        )
    size = path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_METADATA_BYTES:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_METADATA_SIZE_INVALID",
            f"{label} 大小无效：{size} bytes",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - converted to a stable archive error.
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_METADATA_INVALID",
            f"{label} 不是有效 JSON：{exc}",
        ) from exc
    if not isinstance(value, dict):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_METADATA_INVALID",
            f"{label} 顶层必须是 JSON 对象。",
        )
    return value


def _archive_root_directory(root: Path, *, required: bool) -> Path | None:
    archive_root = root / ARCHIVE_RELATIVE_PATH
    if not archive_root.exists():
        if required:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_NOT_FOUND",
                "当前项目还没有批量筛选归档。",
            )
        return None
    archive_root_stat = archive_root.lstat()
    if (
        archive_root.is_symlink()
        or _is_reparse_stat(archive_root_stat)
        or not stat.S_ISDIR(archive_root_stat.st_mode)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ROOT_INVALID",
            "screening/archive 必须是项目内的真实目录。",
        )
    resolved = archive_root.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ROOT_INVALID",
            "screening/archive 指向项目目录外，已拒绝读取。",
        ) from exc
    return resolved


def _resolve_archive_directory(root: Path, archive_id: str) -> Path:
    value = str(archive_id or "")
    if value != value.strip() or not ARCHIVE_ID_PATTERN.fullmatch(value):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ID_INVALID",
            "批量筛选归档编号无效。",
        )
    archive_root = _archive_root_directory(root, required=True)
    assert archive_root is not None
    candidate = archive_root / value
    if not candidate.exists():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_NOT_FOUND",
            f"没有找到批量筛选归档：{value}",
        )
    candidate_stat = candidate.lstat()
    if (
        candidate.is_symlink()
        or _is_reparse_stat(candidate_stat)
        or not stat.S_ISDIR(candidate_stat.st_mode)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_PATH_INVALID",
            f"归档不是可读取的真实目录：{value}",
        )
    resolved = candidate.resolve(strict=True)
    if resolved.parent != archive_root or resolved.name != value:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_PATH_INVALID",
            f"归档路径不是 screening/archive 的直接子目录：{value}",
        )
    return resolved


def _validate_archive_bundle(
    root: Path,
    archive_id: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    archive_dir = _resolve_archive_directory(root, archive_id)
    manifest = _read_archive_json(
        archive_dir / "archive_manifest.json",
        label="archive_manifest.json",
    )
    if manifest.get("schema_version") != 1:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_MANIFEST_UNSUPPORTED",
            f"归档 manifest schema 不受支持：{manifest.get('schema_version')}",
        )
    expected_state_sha256 = str(manifest.get("state_sha256") or "")
    if not SHA256_PATTERN.fullmatch(expected_state_sha256):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_STATE_HASH_INVALID",
            "归档 manifest 没有有效的 screening.json SHA256。",
        )
    state_path = archive_dir / "screening.json"
    if not state_path.is_file():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_METADATA_MISSING",
            "归档缺少 screening.json。",
        )
    actual_state_sha256 = _sha256(state_path)
    if actual_state_sha256.lower() != expected_state_sha256.lower():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
            "归档 screening.json SHA256 与 manifest 不一致。",
        )
    state = _read_archive_json(state_path, label="screening.json")
    if state.get("schema_version") != SCREENING_SCHEMA_VERSION:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_STATE_UNSUPPORTED",
            f"归档 screening schema 不受支持：{state.get('schema_version')}",
        )
    if not isinstance(state.get("items"), list) or not isinstance(state.get("queue"), list):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_STATE_INVALID",
            "归档 screening.json 缺少有效的 items 或 queue。",
        )
    screening_id = str(state.get("screening_id") or "")
    manifest_screening_id = str(manifest.get("screening_id") or "")
    if (
        not SCREENING_ID_PATTERN.fullmatch(screening_id)
        or screening_id != manifest_screening_id
        or not archive_id.startswith(f"{screening_id}_")
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ID_MISMATCH",
            "归档目录、manifest 与 screening.json 的筛选编号不一致。",
        )
    status = str(state.get("status") or "")
    if (
        status not in TERMINAL_SCREENING_STATUSES
        or status != str(manifest.get("status") or "")
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_STATUS_MISMATCH",
            "归档 manifest 与 screening.json 的终态不一致。",
        )
    item_ids: set[str] = set()
    for item in state["items"]:
        if not isinstance(item, dict):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_STATE_INVALID",
                "归档 items 中包含非对象记录。",
            )
        item_id = str(item.get("item_id") or "")
        if (
            not re.fullmatch(r"ligand_\d{4,}", item_id)
            or item_id in item_ids
            or not isinstance(item.get("attempts"), list)
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_STATE_INVALID",
                f"归档配体记录无效或重复：{item_id or 'missing'}",
            )
        item_ids.add(item_id)
    item_labels = manifest.get("item_labels")
    if item_labels is not None:
        if not isinstance(item_labels, dict) or any(
            not isinstance(key, str)
            or key not in item_ids
            or not isinstance(value, str)
            for key, value in item_labels.items()
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_LABELS_INVALID",
                "归档 manifest 的 item_labels 无效。",
            )
    return archive_dir, manifest, state


def _basename_label(value: Any) -> str:
    parts = [part for part in re.split(r"[\\/]+", str(value or "").strip()) if part]
    return parts[-1] if parts else ""


def _screening_source_label(
    record: dict[str, Any],
    *,
    fallback_file: Any = "",
    fallback: str = "",
) -> str:
    source_format = str(record.get("source_format") or "").strip().lower()
    record_name = str(record.get("source_record_name") or "").strip()
    original_name = _basename_label(
        record.get("original_name") or record.get("source_original_file")
    )
    if source_format in {"sdf", "mol"} and record_name:
        return record_name
    return (
        original_name
        or record_name
        or _basename_label(record.get("source_file"))
        or _basename_label(fallback_file)
        or fallback
    )


def _staging_label_lookup(root: Path) -> dict[str, str]:
    index_path = root / STAGING_INDEX_RELATIVE_PATH
    if not index_path.is_file():
        return {}
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    files = index.get("files") if isinstance(index, dict) else None
    if not isinstance(files, dict):
        return {}
    labels: dict[str, str] = {}
    for record in files.values():
        if not isinstance(record, dict):
            continue
        file = str(record.get("file") or "")
        label = _screening_source_label(
            record,
            fallback_file=file,
        )
        if file and label:
            labels[file] = label
    return labels


def _archive_item_labels(
    root: Path,
    state: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> dict[str, str]:
    frozen = (
        manifest.get("item_labels")
        if isinstance(manifest, dict) and isinstance(manifest.get("item_labels"), dict)
        else {}
    )
    staged = _staging_label_lookup(root)
    labels: dict[str, str] = {}
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        source_file = str(item.get("source_file") or "")
        label = (
            str(frozen.get(item_id) or "").strip()
            or str(item.get("display_label") or "").strip()
            or str(item.get("source_record_name") or "").strip()
            or staged.get(source_file, "")
            or _basename_label(source_file)
            or _basename_label(item.get("ligand_file"))
            or item_id
        )
        if item_id:
            labels[item_id] = label
    return labels


def _normalized_archived_state(
    state: dict[str, Any],
    item_labels: dict[str, str],
) -> dict[str, Any]:
    normalized = json.loads(json.dumps(state, ensure_ascii=False))
    vina = normalized.get("vina")
    if isinstance(vina, dict):
        vina.pop("unbound_energy", None)
        inferred_vina_fields = [
            key for key in SCREENING_VINA_DEFAULTS if key not in vina
        ]
        for key, default in SCREENING_VINA_DEFAULTS.items():
            vina.setdefault(key, default)
        compatibility = (
            normalized.get("compatibility")
            if isinstance(normalized.get("compatibility"), dict)
            else {}
        )
        compatibility = dict(compatibility)
        compatibility["inferred_vina_fields"] = inferred_vina_fields
        raw_energy_range = vina.get("energy_range")
        if (
            len(inferred_vina_fields) == len(SCREENING_VINA_DEFAULTS)
            and not isinstance(raw_energy_range, bool)
            and isinstance(raw_energy_range, (int, float))
            and math.isfinite(float(raw_energy_range))
            and float(raw_energy_range) == 0
        ):
            compatibility.setdefault(
                "legacy_energy_range_zero",
                {
                    "applied": True,
                    "reason": "schema_v1_missing_all_advanced_vina_fields",
                    "source": "archive_inference",
                },
            )
        normalized["compatibility"] = compatibility
    raw_resource_limits = normalized.get("resource_limits")
    resource_source = (
        raw_resource_limits
        if isinstance(raw_resource_limits, dict)
        else {}
    )
    default_resource_limits = ScreeningResourceLimits().to_dict()
    inferred_resource_fields = [
        key for key in default_resource_limits if key not in resource_source
    ]
    normalized["resource_limits"] = {
        **default_resource_limits,
        **resource_source,
    }
    if inferred_resource_fields:
        compatibility = (
            normalized.get("compatibility")
            if isinstance(normalized.get("compatibility"), dict)
            else {}
        )
        compatibility = dict(compatibility)
        compatibility["inferred_resource_limit_fields"] = inferred_resource_fields
        normalized["compatibility"] = compatibility
    outputs = normalized.setdefault("outputs", {})
    if not isinstance(outputs, dict):
        outputs = {}
        normalized["outputs"] = outputs
    outputs.setdefault("summary_csv", "")
    outputs.setdefault("summary_sha256", "")
    outputs.setdefault("summary_size_bytes", 0)
    outputs.setdefault("top_n_csv", "")
    outputs.setdefault("top_n_sha256", "")
    outputs.setdefault("top_n_size_bytes", 0)
    outputs.setdefault("report_md", "")
    outputs.setdefault("report_sha256", "")
    outputs.setdefault("report_size_bytes", 0)
    outputs.setdefault("reported_at", "")
    outputs.setdefault(
        "sdf",
        {
            "generated": False,
            "file": "",
            "reason": "历史归档没有记录 SDF 输出。",
        },
    )
    for item in normalized.get("items") or []:
        if not isinstance(item, dict):
            continue
        item.setdefault("source_file", "")
        item.setdefault("sha256", "")
        item.setdefault("size_bytes", 0)
        item.setdefault("attempt_count", len(item.get("attempts") or []))
        item.setdefault("best_affinity_kcal_mol", None)
        item.setdefault("best_output_file", "")
        item.setdefault("best_output_sha256", "")
        item.setdefault("best_output_size_bytes", 0)
        item.setdefault("last_error", "")
        item["display_label"] = item_labels.get(str(item.get("item_id") or ""), "")
        for attempt in item.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            attempt.setdefault("output_sha256", "")
            attempt.setdefault("output_size_bytes", 0)
    return normalized


def _relocate_archive_logical_file(
    archive_dir: Path,
    logical_path: Any,
    *,
    expected_tail: tuple[str, ...],
) -> Path:
    value = str(logical_path or "")
    if not value or "\\" in value or ":" in value:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_PATH_INVALID",
            f"归档记录了无效的项目逻辑路径：{value or 'missing'}",
        )
    logical = PurePosixPath(value)
    if logical.is_absolute() or ".." in logical.parts:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_PATH_INVALID",
            f"归档文件路径越过允许范围：{value}",
        )
    parts = logical.parts
    if not parts or parts[0] != "screening" or tuple(parts[1:]) != expected_tail:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_PATH_INVALID",
            f"归档文件路径与预期位置不一致：{value}",
        )
    candidate = archive_dir.joinpath(*expected_tail)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_MISSING",
            f"归档文件不存在：{value}",
        ) from exc
    try:
        resolved.relative_to(archive_dir)
    except ValueError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_PATH_INVALID",
            f"归档文件指向所选归档之外：{value}",
        ) from exc
    if not resolved.is_file():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_FILE_MISSING",
            f"归档路径不是文件：{value}",
        )
    return resolved


def _archived_ad4_map_records(
    state: dict[str, Any],
) -> list[tuple[str, dict[str, Any], str]]:
    """Return validated logical paths for the AD4 maps frozen with a queue."""

    if str(state.get("scoring_protocol") or "vina") != "ad4_maps":
        return []
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    ad4_maps = (
        inputs.get("ad4_maps")
        if isinstance(inputs.get("ad4_maps"), dict)
        else {}
    )
    manifest = (
        ad4_maps.get("manifest")
        if isinstance(ad4_maps.get("manifest"), dict)
        else {}
    )
    files = ad4_maps.get("files") if isinstance(ad4_maps.get("files"), list) else []
    prefix = str(ad4_maps.get("prefix") or "")
    expected_root = PurePosixPath("screening", "inputs", "ad4_maps")
    prefix_path = PurePosixPath(prefix)
    if (
        not manifest
        or not files
        or tuple(prefix_path.parts[:-1]) != expected_root.parts
        or not prefix_path.name
        or "\\" in prefix
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_AD4_MAPS_INVALID",
            "归档缺少完整、安全的 AutoDock4 maps 快照记录。",
        )

    records: list[tuple[str, dict[str, Any], str]] = []
    manifest_path = str(manifest.get("file") or "")
    expected_manifest = (expected_root / "source_manifest.json").as_posix()
    if manifest_path != expected_manifest:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_AD4_MAPS_INVALID",
            f"归档 AutoDock4 maps manifest 路径无效：{manifest_path or 'missing'}。",
        )
    records.append((manifest_path, manifest, "AutoDock4 maps manifest"))

    seen_names: set[str] = set()
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_AD4_MAPS_INVALID",
                f"归档 AutoDock4 map #{index} 记录无效。",
            )
        logical = str(item.get("relative_path") or "")
        logical_path = PurePosixPath(logical)
        if (
            tuple(logical_path.parts[:-1]) != expected_root.parts
            or not logical_path.name
            or logical_path.name in seen_names
            or "\\" in logical
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_AD4_MAPS_INVALID",
                f"归档 AutoDock4 map #{index} 路径无效或重名：{logical or 'missing'}。",
            )
        seen_names.add(logical_path.name)
        records.append((logical, item, f"AutoDock4 map {logical_path.name}"))
    return records


def _archive_export_snapshot_identity(
    source_records: dict[str, dict[str, Any]],
    relative: str,
    *,
    archive_id: str,
    label: str,
) -> tuple[str, int]:
    snapshot_record = source_records.get(relative)
    if snapshot_record is None:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"归档 {archive_id} 的{label}不在导出快照中。",
        )
    try:
        sha256 = str(snapshot_record["sha256"]).lower()
        size = int(snapshot_record["size_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"归档 {archive_id} 的{label}导出快照身份无效。",
        ) from exc
    if (
        SHA256_PATTERN.fullmatch(sha256) is None
        or size < 0
        or isinstance(snapshot_record.get("size_bytes"), bool)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"归档 {archive_id} 的{label}导出快照身份无效。",
        )
    return sha256, size


def _validate_archived_resource_state(
    archive_dir: Path,
    state: dict[str, Any],
    *,
    archive_id: str,
    error_code: str,
    verify_files: bool,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        limits = _validated_resource_limits(
            state.get("resource_limits")
            if "resource_limits" in state
            else None
        )
        resource_hash_status = _verify_frozen_resource_limits_hash(state, limits)
        resource_state = _validate_screening_resource_state(state, limits)
        raw_vina = state.get("vina") if isinstance(state.get("vina"), dict) else {}
        _validate_settings(
            state.get("box") if isinstance(state.get("box"), dict) else {},
            _screening_vina_with_defaults(raw_vina),
            limits,
            allow_legacy_energy_range_zero=_archive_allows_legacy_energy_range_zero(
                state
            ),
        )
        if verify_files:
            inputs = (
                state.get("inputs")
                if isinstance(state.get("inputs"), dict)
                else {}
            )
            receptor = (
                inputs.get("receptor")
                if isinstance(inputs.get("receptor"), dict)
                else {}
            )
            records: list[tuple[Path, dict[str, Any], str, str]] = [
                (
                    _relocate_archive_logical_file(
                        archive_dir,
                        receptor.get("file"),
                        expected_tail=("inputs", "receptor.pdbqt"),
                    ),
                    receptor,
                    "受体输入",
                    "inputs/receptor.pdbqt",
                )
            ]
            library_manifest = (
                inputs.get("library_import_manifest")
                if isinstance(inputs.get("library_import_manifest"), dict)
                else None
            )
            if library_manifest is not None:
                records.append(
                    (
                        _relocate_archive_logical_file(
                            archive_dir,
                            library_manifest.get("file"),
                            expected_tail=(
                                "inputs",
                                "library_import_manifest.json",
                            ),
                        ),
                        library_manifest,
                        "批量配体导入 manifest",
                        "inputs/library_import_manifest.json",
                    )
                )
            for logical, record, label in _archived_ad4_map_records(state):
                logical_path = PurePosixPath(logical)
                records.append(
                    (
                        _relocate_archive_logical_file(
                            archive_dir,
                            logical,
                            expected_tail=tuple(logical_path.parts[1:]),
                        ),
                        record,
                        label,
                        PurePosixPath(*logical_path.parts[1:]).as_posix(),
                    )
                )
            for item in state.get("items") or []:
                item_id = str(item.get("item_id") or "")
                records.append(
                    (
                        _relocate_archive_logical_file(
                            archive_dir,
                            item.get("ligand_file"),
                            expected_tail=(
                                "inputs",
                                "ligands",
                                f"{item_id}.pdbqt",
                            ),
                        ),
                        item,
                        f"配体 {item_id} 输入",
                        f"inputs/ligands/{item_id}.pdbqt",
                    )
                )
                topology_file = str(item.get("source_topology_file") or "")
                if topology_file:
                    topology_name = PurePosixPath(topology_file).name
                    records.append(
                        (
                            _relocate_archive_logical_file(
                                archive_dir,
                                topology_file,
                                expected_tail=(
                                    "inputs",
                                    "topology",
                                    topology_name,
                                ),
                            ),
                            {
                                "sha256": item.get(
                                    "source_topology_sha256"
                                ),
                                "size_bytes": item.get(
                                    "source_topology_size_bytes"
                                ),
                            },
                            f"配体 {item_id} 原始拓扑",
                            f"inputs/topology/{topology_name}",
                        )
                    )
            outputs = (
                state.get("outputs")
                if isinstance(state.get("outputs"), dict)
                else {}
            )
            sdf_output = (
                outputs.get("sdf")
                if isinstance(outputs.get("sdf"), dict)
                else {}
            )
            for artifact in (
                sdf_output.get("artifacts")
                if isinstance(sdf_output.get("artifacts"), list)
                else []
            ):
                if not isinstance(artifact, dict):
                    raise ValueError("批量结果 SDF artifact 记录无效。")
                logical = PurePosixPath(str(artifact.get("file") or ""))
                if (
                    len(logical.parts) < 3
                    or tuple(logical.parts[:2])
                    != ("screening", "results")
                ):
                    raise ValueError("批量结果 SDF artifact 路径无效。")
                relative_tail = tuple(logical.parts[1:])
                records.append(
                    (
                        _relocate_archive_logical_file(
                            archive_dir,
                            logical.as_posix(),
                            expected_tail=relative_tail,
                        ),
                        artifact,
                        f"批量结果 SDF artifact {logical.name}",
                        PurePosixPath(*relative_tail).as_posix(),
                    )
                )
            for path, record, label, relative in records:
                expected_sha256 = str(record.get("sha256") or "").strip().lower()
                expected_size = record.get("size_bytes")
                allow_empty = label.startswith("批量结果 SDF artifact ")
                if SHA256_PATTERN.fullmatch(expected_sha256) is None:
                    raise ValueError(f"{label}缺少有效 SHA256。")
                if (
                    isinstance(expected_size, bool)
                    or not isinstance(expected_size, int)
                    or expected_size < (0 if allow_empty else 1)
                ):
                    raise ValueError(f"{label}缺少有效文件大小。")
                if source_records is None:
                    actual_sha256, actual_size, _identity = _hash_export_source(
                        path
                    )
                else:
                    actual_sha256, actual_size = (
                        _archive_export_snapshot_identity(
                            source_records,
                            relative,
                            archive_id=archive_id,
                            label=label,
                        )
                    )
                if actual_size != expected_size:
                    raise ValueError(
                        f"{label}大小不一致：记录 {expected_size} bytes，"
                        f"实际 {actual_size} bytes。",
                    )
                if actual_sha256 != expected_sha256:
                    raise ValueError(f"{label} SHA256 与 screening.json 不一致。")
        return {
            "status": (
                "verified"
                if resource_hash_status == "verified"
                else "legacy_unverified"
            ),
            "limits_hash": resource_hash_status,
            "limits": limits.to_dict(),
            **resource_state,
        }
    except _ArchiveValidationError:
        raise
    except Exception as exc:
        raise _ArchiveValidationError(
            error_code,
            f"归档 {archive_id} 的冻结资源策略无效：{exc}",
        ) from exc


def _succeeded_output_attempt(item: dict[str, Any]) -> dict[str, Any]:
    item_id = str(item.get("item_id") or "")
    output_file = str(item.get("best_output_file") or "")
    attempt = next(
        (
            record
            for record in reversed(item.get("attempts") or [])
            if isinstance(record, dict)
            and record.get("status") == "succeeded"
            and str(record.get("output_file") or "") == output_file
        ),
        None,
    )
    if not output_file or attempt is None:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_OUTPUT_RECORD_INVALID",
            f"{item_id} 缺少一致的成功输出记录。",
        )
    attempt_number = int(attempt.get("attempt") or 0)
    expected = (
        "attempts",
        item_id,
        f"attempt_{attempt_number:03d}",
        "out.pdbqt",
    )
    logical = PurePosixPath(output_file)
    if attempt_number < 1 or tuple(logical.parts[1:]) != expected:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_OUTPUT_RECORD_INVALID",
            f"{item_id} 的最佳输出路径与尝试编号不一致。",
        )
    return attempt


def _archive_attempt_evidence_file(
    archive_dir: Path,
    record: Any,
    *,
    expected_tail: tuple[str, ...],
    archive_id: str,
    label: str,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verify present pre-run fields and return the archive file's identity."""

    if record is None:
        evidence: dict[str, Any] = {}
    elif not isinstance(record, dict):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
            f"归档 {archive_id} 的{label}证据必须是对象。",
        )
    else:
        evidence = record

    raw_logical_file = evidence.get("file")
    if raw_logical_file is not None and not isinstance(raw_logical_file, str):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
            f"归档 {archive_id} 的{label}文件路径证据无效。",
        )
    logical_file = str(raw_logical_file or "")
    resolution_file = logical_file or PurePosixPath(
        "screening",
        *expected_tail,
    ).as_posix()
    path = _relocate_archive_logical_file(
        archive_dir,
        resolution_file,
        expected_tail=expected_tail,
    )
    if source_records is None:
        actual_sha256, actual_size, _identity = _hash_export_source(path)
    else:
        actual_sha256, actual_size = _archive_export_snapshot_identity(
            source_records,
            PurePosixPath(*expected_tail).as_posix(),
            archive_id=archive_id,
            label=label,
        )

    sha_present = "sha256" in evidence and evidence.get("sha256") not in (None, "")
    expected_sha256 = ""
    if sha_present:
        raw_sha256 = evidence.get("sha256")
        if not isinstance(raw_sha256, str):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                f"归档 {archive_id} 的{label} SHA256 必须是字符串。",
            )
        expected_sha256 = raw_sha256.strip().lower()
        if SHA256_PATTERN.fullmatch(expected_sha256) is None:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                f"归档 {archive_id} 的{label} SHA256 无效。",
            )
        if actual_sha256 != expected_sha256:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_HASH_MISMATCH",
                f"归档 {archive_id} 的{label} SHA256 与 attempt 证据不一致。",
            )

    size_present = (
        "size_bytes" in evidence and evidence.get("size_bytes") is not None
    )
    expected_size: int | None = None
    if size_present:
        raw_size = evidence.get("size_bytes")
        if (
            isinstance(raw_size, bool)
            or not isinstance(raw_size, int)
            or raw_size < 0
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                f"归档 {archive_id} 的{label}文件大小证据无效。",
            )
        expected_size = raw_size
        if actual_size != expected_size:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_SIZE_MISMATCH",
                (
                    f"归档 {archive_id} 的{label}大小与 attempt 证据不一致："
                    f"记录 {expected_size} bytes，实际 {actual_size} bytes。"
                ),
            )
    return (
        bool(logical_file) and sha_present and size_present,
        {
            "actual_sha256": actual_sha256,
            "actual_size_bytes": actual_size,
            "recorded_sha256": expected_sha256 if sha_present else None,
            "recorded_size_bytes": expected_size,
        },
    )


def _archive_post_run_identity(
    record: Any,
    reference: dict[str, Any],
    *,
    archive_id: str,
    label: str,
) -> bool:
    """Validate present post-run identity fields against all earlier evidence."""

    if record is None:
        return False
    if not isinstance(record, dict):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
            f"归档 {archive_id} 的运行后{label}证据必须是对象。",
        )
    complete = True
    raw_sha256 = record.get("sha256")
    if raw_sha256 in (None, ""):
        complete = False
    elif not isinstance(raw_sha256, str):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
            f"归档 {archive_id} 的运行后{label} SHA256 必须是字符串。",
        )
    else:
        sha256 = raw_sha256.strip().lower()
        if SHA256_PATTERN.fullmatch(sha256) is None:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                f"归档 {archive_id} 的运行后{label} SHA256 无效。",
            )
        expected_hashes = {
            str(reference.get(key) or "").lower()
            for key in ("actual_sha256", "recorded_sha256")
            if reference.get(key)
        }
        if any(sha256 != expected for expected in expected_hashes):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_POSTRUN_MISMATCH",
                f"归档 {archive_id} 的运行后{label} SHA256 与运行前证据不一致。",
            )

    raw_size = record.get("size_bytes")
    if raw_size is None:
        complete = False
    elif (
        isinstance(raw_size, bool)
        or not isinstance(raw_size, int)
        or raw_size < 0
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
            f"归档 {archive_id} 的运行后{label}大小证据无效。",
        )
    else:
        expected_sizes = {
            int(reference[key])
            for key in ("actual_size_bytes", "recorded_size_bytes")
            if reference.get(key) is not None
        }
        if any(raw_size != expected for expected in expected_sizes):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_ATTEMPT_POSTRUN_MISMATCH",
                f"归档 {archive_id} 的运行后{label}大小与运行前证据不一致。",
            )
    return complete


def _archive_attempt_integrity(
    archive_dir: Path,
    state: dict[str, Any],
    *,
    archive_id: str,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate post-run attempt evidence while keeping old records readable."""

    tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
    vina_tool = tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
    raw_frozen_vina_sha256 = vina_tool.get("sha256")
    frozen_vina_sha256 = (
        raw_frozen_vina_sha256.strip().lower()
        if isinstance(raw_frozen_vina_sha256, str)
        else ""
    )
    frozen_vina_size = vina_tool.get("size_bytes")
    counts = {
        "total": 0,
        "verified": 0,
        "partially_verified": 0,
        "legacy_unverified": 0,
        "attempt_json_missing": 0,
    }
    warnings: list[str] = []

    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        for attempt in item.get("attempts") or []:
            if not isinstance(attempt, dict):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的 {item_id} 包含非对象 attempt 记录。",
                )
            counts["total"] += 1
            attempt_number = attempt.get("attempt")
            if (
                isinstance(attempt_number, bool)
                or not isinstance(attempt_number, int)
                or attempt_number < 1
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的 {item_id} attempt 编号无效。",
                )
            attempt_tail = (
                "attempts",
                item_id,
                f"attempt_{attempt_number:03d}",
            )
            attempt_json_complete = True
            if source_records is not None:
                attempt_json_relative = PurePosixPath(
                    *attempt_tail,
                    "attempt.json",
                ).as_posix()
                attempt_json_record = source_records.get(attempt_json_relative)
                if attempt_json_record is None:
                    attempt_json_complete = False
                    counts["attempt_json_missing"] += 1
                else:
                    semantic_sha256 = str(
                        attempt_json_record.get("_semantic_sha256") or ""
                    ).lower()
                    if SHA256_PATTERN.fullmatch(semantic_sha256) is None:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_JSON_INVALID",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt "
                                f"{attempt_number} JSON 未形成有效语义快照。"
                            ),
                        )
                    try:
                        state_attempt_canonical = _canonical_json_bytes(attempt)
                    except (
                        TypeError,
                        ValueError,
                        UnicodeError,
                        RecursionError,
                    ) as exc:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_JSON_INVALID",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt "
                                f"{attempt_number} 状态无法规范化。"
                            ),
                        ) from exc
                    if (
                        len(state_attempt_canonical)
                        > MAX_SCREENING_EXPORT_SEMANTIC_JSON_BYTES
                    ):
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt "
                                f"{attempt_number} 语义对象超过上限。"
                            ),
                        )
                    state_attempt_sha256 = hashlib.sha256(
                        state_attempt_canonical
                    ).hexdigest()
                    if semantic_sha256 != state_attempt_sha256:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_JSON_MISMATCH",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt "
                                f"{attempt_number} JSON 与 screening.json 矛盾。"
                            ),
                        )
            evidence_keys = (
                "input_snapshots",
                "config_snapshot",
                "vina_snapshot",
                "integrity",
            )
            if not any(key in attempt for key in evidence_keys):
                counts["legacy_unverified"] += 1
                continue
            if (
                raw_frozen_vina_sha256 not in (None, "")
                and (
                    not isinstance(raw_frozen_vina_sha256, str)
                    or SHA256_PATTERN.fullmatch(frozen_vina_sha256) is None
                )
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的冻结 Vina SHA256 无效。",
                )
            if frozen_vina_size is not None and (
                isinstance(frozen_vina_size, bool)
                or not isinstance(frozen_vina_size, int)
                or frozen_vina_size <= 0
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的冻结 Vina 大小证据无效。",
                )

            complete = attempt_json_complete
            input_snapshots = attempt.get("input_snapshots")
            if input_snapshots is None:
                complete = False
                input_snapshots = {}
            elif not isinstance(input_snapshots, dict):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的 {item_id} attempt 输入证据无效。",
                )
            receptor_complete, receptor_identity = _archive_attempt_evidence_file(
                archive_dir,
                input_snapshots.get("receptor"),
                expected_tail=(*attempt_tail, "receptor.pdbqt"),
                archive_id=archive_id,
                label=f"{item_id} attempt {attempt_number} 受体",
                source_records=source_records,
            )
            complete = receptor_complete and complete
            ligand_complete, ligand_identity = _archive_attempt_evidence_file(
                archive_dir,
                input_snapshots.get("ligand"),
                expected_tail=(*attempt_tail, "ligand.pdbqt"),
                archive_id=archive_id,
                label=f"{item_id} attempt {attempt_number} 配体",
                source_records=source_records,
            )
            complete = ligand_complete and complete
            config_complete, config_identity = _archive_attempt_evidence_file(
                archive_dir,
                attempt.get("config_snapshot"),
                expected_tail=(*attempt_tail, "config.txt"),
                archive_id=archive_id,
                label=f"{item_id} attempt {attempt_number} 配置",
                source_records=source_records,
            )
            complete = config_complete and complete

            vina_snapshot = attempt.get("vina_snapshot")
            vina_reference = {
                "actual_sha256": frozen_vina_sha256 or None,
                "actual_size_bytes": frozen_vina_size,
                "recorded_sha256": None,
                "recorded_size_bytes": None,
            }
            if vina_snapshot is None:
                complete = False
            elif not isinstance(vina_snapshot, dict):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的 {item_id} attempt Vina 证据无效。",
                )
            else:
                raw_vina_sha256 = vina_snapshot.get("sha256")
                vina_sha256 = ""
                vina_size = vina_snapshot.get("size_bytes")
                if raw_vina_sha256 in (None, ""):
                    complete = False
                elif not isinstance(raw_vina_sha256, str):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt Vina SHA256 必须是字符串。",
                    )
                else:
                    vina_sha256 = raw_vina_sha256.strip().lower()
                    vina_reference["recorded_sha256"] = vina_sha256
                if not vina_sha256:
                    pass
                elif SHA256_PATTERN.fullmatch(vina_sha256) is None:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt Vina SHA256 无效。",
                    )
                else:
                    if not frozen_vina_sha256:
                        complete = False
                    elif vina_sha256 != frozen_vina_sha256:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_VINA_MISMATCH",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt Vina SHA256 "
                                "与任务冻结工具不一致。"
                            ),
                        )
                if vina_size is None:
                    complete = False
                elif (
                    isinstance(vina_size, bool)
                    or not isinstance(vina_size, int)
                    or vina_size <= 0
                ):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt Vina 大小证据无效。",
                    )
                elif frozen_vina_size is not None:
                    if (
                        isinstance(frozen_vina_size, bool)
                        or not isinstance(frozen_vina_size, int)
                        or frozen_vina_size <= 0
                    ):
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                            f"归档 {archive_id} 的冻结 Vina 大小证据无效。",
                        )
                    if vina_size != frozen_vina_size:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_VINA_MISMATCH",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt Vina 大小"
                                "与任务冻结工具不一致。"
                            ),
                        )
                if vina_size is not None:
                    vina_reference["recorded_size_bytes"] = vina_size
                if SHA256_PATTERN.fullmatch(frozen_vina_sha256) is None:
                    complete = False
                if frozen_vina_size is None:
                    complete = False

            integrity = attempt.get("integrity")
            integrity_files: dict[str, Any] = {}
            integrity_status = ""
            if integrity is None:
                complete = False
            elif not isinstance(integrity, dict):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                    f"归档 {archive_id} 的 {item_id} attempt 完成态证据无效。",
                )
            else:
                raw_integrity_status = integrity.get("status")
                if raw_integrity_status in (None, ""):
                    complete = False
                elif not isinstance(raw_integrity_status, str):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt integrity.status 无效。",
                    )
                else:
                    integrity_status = raw_integrity_status
                    if integrity_status not in {
                        "verified",
                        "failed",
                        "not_checked",
                        "pending",
                    }:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                            (
                                f"归档 {archive_id} 的 {item_id} attempt "
                                f"integrity.status 不受支持：{integrity_status}"
                            ),
                        )
                    if integrity_status != "verified":
                        complete = False
                checked_at = integrity.get("checked_at")
                if checked_at in (None, ""):
                    complete = False
                elif not isinstance(checked_at, str):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt integrity.checked_at 无效。",
                    )
                raw_integrity_files = integrity.get("files")
                if raw_integrity_files is None:
                    complete = False
                elif not isinstance(raw_integrity_files, dict):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_ATTEMPT_EVIDENCE_INVALID",
                        f"归档 {archive_id} 的 {item_id} attempt integrity.files 无效。",
                    )
                else:
                    integrity_files = raw_integrity_files

            post_run_references = {
                "receptor": receptor_identity,
                "ligand": ligand_identity,
                "config": config_identity,
                "vina": vina_reference,
            }
            for evidence_name, reference in post_run_references.items():
                post_run_complete = _archive_post_run_identity(
                    integrity_files.get(evidence_name),
                    reference,
                    archive_id=archive_id,
                    label=(
                        f"{item_id} attempt {attempt_number} {evidence_name}"
                    ),
                )
                complete = post_run_complete and complete

            if complete:
                counts["verified"] += 1
            else:
                counts["partially_verified"] += 1

    if counts["total"] == 0:
        status = "not_applicable"
    elif counts["partially_verified"] > 0:
        status = "partially_verified"
    elif counts["legacy_unverified"] == counts["total"]:
        status = "legacy_unverified"
    elif counts["legacy_unverified"] > 0:
        status = "partially_verified"
    else:
        status = "verified"
    if counts["legacy_unverified"]:
        warnings.append(
            f"{counts['legacy_unverified']} 个历史 attempt 没有运行后完整性证据。"
        )
    if counts["partially_verified"]:
        warnings.append(
            f"{counts['partially_verified']} 个 attempt 仅有部分证据，未标记为完全验证。"
        )
    if counts["attempt_json_missing"]:
        warnings.append(
            (
                f"{counts['attempt_json_missing']} 个历史 attempt 缺少 attempt.json，"
                "无法与 screening.json 逐项核对。"
            )
        )
    return {
        "status": status,
        "counts": counts,
        "warnings": warnings,
        "vina_binary_archived": False,
        "vina_evidence": (
            "attempt 记录已与冻结工具身份交叉核对；归档不复制 Vina 二进制。"
        ),
    }


def _archive_files(
    root: Path,
    archive_dir: Path,
    state: dict[str, Any],
    *,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive_relative = archive_dir.relative_to(root).as_posix()

    def project_relative(path: Path) -> str:
        return path.relative_to(root).as_posix()

    receptor_record = (
        state.get("inputs", {}).get("receptor", {})
        if isinstance(state.get("inputs"), dict)
        and isinstance(state.get("inputs", {}).get("receptor"), dict)
        else {}
    )
    receptor = _relocate_archive_logical_file(
        archive_dir,
        receptor_record.get("file"),
        expected_tail=("inputs", "receptor.pdbqt"),
    )
    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    fixed_outputs = {
        "summary_csv": ("results", "screening_summary.csv"),
        "top_n_csv": ("results", "screening_top_n.csv"),
        "report_md": ("results", "screening_report.md"),
    }
    relocated_outputs: dict[str, str] = {}
    for key, tail in fixed_outputs.items():
        logical = str(outputs.get(key) or "")
        if not logical:
            relocated_outputs[key] = ""
            continue
        relocated_outputs[key] = project_relative(
            _relocate_archive_logical_file(
                archive_dir,
                logical,
                expected_tail=tail,
            )
        )
    if relocated_outputs["report_md"] and outputs.get("report_sha256"):
        report_path = root / relocated_outputs["report_md"]
        if source_records is None:
            report_sha256, _report_size, _identity = _hash_export_source(
                report_path
            )
        else:
            report_sha256, _report_size = _archive_export_snapshot_identity(
                source_records,
                "results/screening_report.md",
                archive_id=archive_dir.name,
                label="实验记录",
            )
        if report_sha256 != str(outputs["report_sha256"]).lower():
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_FILE_HASH_MISMATCH",
                "归档实验记录 SHA256 与 screening.json 不一致。",
            )

    item_files: dict[str, dict[str, str]] = {}
    for item in state.get("items") or []:
        item_id = str(item.get("item_id") or "")
        ligand = _relocate_archive_logical_file(
            archive_dir,
            item.get("ligand_file"),
            expected_tail=("inputs", "ligands", f"{item_id}.pdbqt"),
        )
        best_output = ""
        if item.get("status") == "succeeded":
            attempt = _succeeded_output_attempt(item)
            attempt_number = int(attempt["attempt"])
            output = _relocate_archive_logical_file(
                archive_dir,
                item.get("best_output_file"),
                expected_tail=(
                    "attempts",
                    item_id,
                    f"attempt_{attempt_number:03d}",
                    "out.pdbqt",
                ),
            )
            best_output = project_relative(output)
        item_files[item_id] = {
            "ligand_input": project_relative(ligand),
            "best_output": best_output,
        }
    return {
        "directory": archive_relative,
        "manifest": f"{archive_relative}/archive_manifest.json",
        "state": f"{archive_relative}/screening.json",
        "receptor": project_relative(receptor),
        **relocated_outputs,
        "items": item_files,
    }


def _archive_summary(
    archive_id: str,
    archive_dir: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
    attempt_integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = [item for item in state.get("items") or [] if isinstance(item, dict)]
    successful_scores = [
        float(item["best_affinity_kcal_mol"])
        for item in items
        if item.get("status") == "succeeded"
        and isinstance(item.get("best_affinity_kcal_mol"), (int, float))
        and math.isfinite(float(item["best_affinity_kcal_mol"]))
    ]
    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    report_path = archive_dir / "results" / "screening_report.md"
    report_recorded = (
        str(outputs.get("report_md") or "")
        == "screening/results/screening_report.md"
    )
    return {
        "archive_id": archive_id,
        "valid": True,
        "integrity": "state_verified",
        "screening_id": str(state.get("screening_id") or ""),
        "status": str(state.get("status") or ""),
        "created_at": state.get("created_at"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "archived_at": manifest.get("archived_at"),
        "counts": {
            "total": len(items),
            "succeeded": sum(item.get("status") == "succeeded" for item in items),
            "failed": sum(item.get("status") == "failed" for item in items),
            "unfinished": sum(
                item.get("status") in {"pending", "running", "interrupted"}
                for item in items
            ),
        },
        "best_affinity_kcal_mol": min(successful_scores) if successful_scores else None,
        "top_n": int(state.get("top_n") or 0),
        "report_available": report_recorded and report_path.is_file(),
        "attempt_integrity": (
            str(attempt_integrity.get("status") or "unknown")
            if isinstance(attempt_integrity, dict)
            else "not_checked"
        ),
        "error": None,
    }


def list_screening_archives(project_dir: str) -> dict[str, Any]:
    """List every archive candidate without hiding damaged entries."""

    try:
        root = _project_root(project_dir)
        archive_root = _archive_root_directory(root, required=False)
        if archive_root is None:
            return {
                "ok": True,
                "project_dir": str(root),
                "archives": [],
                "message": "当前项目还没有批量筛选归档。",
                "error": None,
            }
        entries: list[dict[str, Any]] = []
        candidates = sorted(
            (
                path
                for path in archive_root.iterdir()
                if not path.name.startswith(".") and path.name.startswith("screening_")
            ),
            key=lambda path: path.name,
        )
        for candidate in candidates:
            archive_id = candidate.name
            try:
                archive_dir, manifest, state = _validate_archive_bundle(root, archive_id)
                entries.append(
                    _archive_summary(
                        archive_id,
                        archive_dir,
                        manifest,
                        state,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - preserve damaged entries.
                code = (
                    exc.code
                    if isinstance(exc, _ArchiveValidationError)
                    else "SCREENING_ARCHIVE_INVALID"
                )
                entries.append(
                    {
                        "archive_id": archive_id,
                        "valid": False,
                        "integrity": "invalid",
                        "screening_id": "",
                        "status": "",
                        "archived_at": None,
                        "counts": {
                            "total": 0,
                            "succeeded": 0,
                            "failed": 0,
                            "unfinished": 0,
                        },
                        "best_affinity_kcal_mol": None,
                        "top_n": 0,
                        "report_available": False,
                        "error": {
                            "code": code,
                            "message": str(exc),
                        },
                    }
                )
        entries.sort(
            key=lambda entry: (
                str(entry.get("archived_at") or ""),
                str(entry.get("archive_id") or ""),
            ),
            reverse=True,
        )
        return {
            "ok": True,
            "project_dir": str(root),
            "archives": entries,
            "message": f"已读取 {len(entries)} 个批量筛选归档条目。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - public read boundary.
        code = (
            exc.code
            if isinstance(exc, _ArchiveValidationError)
            else "SCREENING_ARCHIVE_LIST_ERROR"
        )
        return _error(code, "读取批量筛选归档列表失败。", str(exc))


def get_screening_archive(project_dir: str, archive_id: str) -> dict[str, Any]:
    """Read one verified archive without modifying legacy state on disk."""

    try:
        root = _project_root(project_dir)
        archive_dir, manifest, state = _validate_archive_bundle(root, archive_id)
        resource_integrity = _validate_archived_resource_state(
            archive_dir,
            state,
            archive_id=archive_id,
            error_code="SCREENING_ARCHIVE_RESOURCE_POLICY_INVALID",
            verify_files=True,
        )
        item_labels = _archive_item_labels(root, state, manifest)
        normalized = _normalized_archived_state(state, item_labels)
        files = _archive_files(root, archive_dir, normalized)
        attempt_integrity = _archive_attempt_integrity(
            archive_dir,
            state,
            archive_id=archive_id,
        )
        staged = [
            {
                "item_id": str(item.get("item_id") or ""),
                "file": str(item.get("source_file") or item.get("ligand_file") or ""),
                "source_file": str(item.get("source_file") or ""),
                "original_name": item_labels.get(str(item.get("item_id") or ""), ""),
            }
            for item in normalized.get("items") or []
            if isinstance(item, dict)
        ]
        staged_labels = {
            record["file"]: record["original_name"]
            for record in staged
            if record["file"] and record["original_name"]
        }
        return {
            "ok": True,
            "project_dir": str(root),
            "archive": _archive_summary(
                archive_id,
                archive_dir,
                manifest,
                normalized,
                attempt_integrity,
            ),
            "manifest": manifest,
            "screening": normalized,
            "files": files,
            "item_labels": item_labels,
            "staged": staged,
            "staged_labels": staged_labels,
            "attempt_integrity": attempt_integrity,
            "resource_integrity": resource_integrity,
            "message": "批量筛选历史归档已通过状态校验并读取。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - public read boundary.
        code = (
            exc.code
            if isinstance(exc, _ArchiveValidationError)
            else "SCREENING_ARCHIVE_READ_ERROR"
        )
        return _error(
            code,
            "读取批量筛选历史归档失败。",
            str(exc),
            "请从归档列表选择有效记录；不要手工修改归档文件。",
        )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_object_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate_pairs(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"包含重复键：{key}")
            value[key] = item
        return value

    def reject_nonfinite(value: str) -> Any:
        raise ValueError(f"包含非标准数值：{value}")

    try:
        parsed = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonfinite,
        )
    except Exception as exc:  # noqa: BLE001 - stable archive error.
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            f"{label}不是无歧义的严格 JSON：{exc}",
        ) from exc
    if not isinstance(parsed, dict):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            f"{label}顶层必须是 JSON 对象。",
        )
    try:
        # Also rejects overflowed floats such as 1e9999, lone Unicode
        # surrogates and any value that cannot enter the canonical hash.
        _canonical_json_bytes(parsed)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            f"{label}包含不可规范化的 JSON 值：{exc}",
        ) from exc
    return parsed


def _is_reparse_stat(value: os.stat_result) -> bool:
    attributes = int(getattr(value, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _export_file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def _validate_export_regular_file(
    path: Path,
    *,
    code: str,
    label: str,
) -> os.stat_result:
    try:
        value = path.lstat()
    except FileNotFoundError as exc:
        raise _ArchiveValidationError(code, f"{label}不存在：{path.name}") from exc
    if path.is_symlink() or _is_reparse_stat(value):
        raise _ArchiveValidationError(code, f"{label}不能是链接或重解析点：{path.name}")
    if not stat.S_ISREG(value.st_mode):
        raise _ArchiveValidationError(code, f"{label}必须是普通文件：{path.name}")
    if int(value.st_nlink) > 1:
        raise _ArchiveValidationError(code, f"{label}不能是硬链接文件：{path.name}")
    return value


def _archive_export_allowlist(
    archive_dir: Path,
    state: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Derive the only file and directory names an archive export may contain."""

    allowed_files = {
        "archive_manifest.json",
        "screening.json",
        "inputs/receptor.pdbqt",
    }
    allowed_directories: set[str] = set()

    def add_file(relative: str) -> None:
        logical = PurePosixPath(relative)
        if logical.is_absolute() or ".." in logical.parts or "\\" in relative:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                f"导出文件路径无效：{relative}",
            )
        allowed_files.add(logical.as_posix())
        for parent in logical.parents:
            parent_value = parent.as_posix()
            if parent_value not in {"", "."}:
                allowed_directories.add(parent_value)

    for fixed in tuple(allowed_files):
        add_file(fixed)

    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    for logical, _record, _label in _archived_ad4_map_records(state):
        logical_path = PurePosixPath(logical)
        add_file(PurePosixPath(*logical_path.parts[1:]).as_posix())
    library_manifest = (
        inputs.get("library_import_manifest")
        if isinstance(inputs.get("library_import_manifest"), dict)
        else None
    )
    if library_manifest is not None:
        recorded = str(library_manifest.get("file") or "")
        if recorded != "screening/inputs/library_import_manifest.json":
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                f"批量配体导入 manifest 路径不符合导出协议：{recorded}",
            )
        add_file("inputs/library_import_manifest.json")

    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    output_paths = {
        "summary_csv": (
            "screening/results/screening_summary.csv",
            "results/screening_summary.csv",
        ),
        "top_n_csv": (
            "screening/results/screening_top_n.csv",
            "results/screening_top_n.csv",
        ),
        "report_md": (
            "screening/results/screening_report.md",
            "results/screening_report.md",
        ),
    }
    for key, (logical_expected, relative) in output_paths.items():
        recorded = str(outputs.get(key) or "")
        if recorded:
            if recorded != logical_expected:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"归档 {key} 路径不符合导出协议：{recorded}",
                )
            add_file(relative)

    sdf_output = (
        outputs.get("sdf") if isinstance(outputs.get("sdf"), dict) else {}
    )
    sdf_fixed_paths = {
        "file": "screening/results/screening_poses.sdf",
        "manifest_file": "screening/results/screening_pose_manifest.json",
        "pose_map_file": "screening/results/screening_pose_map.csv",
    }
    for key, expected in sdf_fixed_paths.items():
        recorded = str(sdf_output.get(key) or "")
        if recorded:
            if recorded != expected:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"批量结果 SDF 的 {key} 路径不符合导出协议：{recorded}",
                )
            add_file(PurePosixPath(*PurePosixPath(recorded).parts[1:]).as_posix())
    artifacts = (
        sdf_output.get("artifacts")
        if isinstance(sdf_output.get("artifacts"), list)
        else []
    )
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                "批量结果 SDF artifacts 包含无效记录。",
            )
        recorded = str(artifact.get("file") or "")
        logical = PurePosixPath(recorded)
        if (
            logical.is_absolute()
            or ".." in logical.parts
            or "\\" in recorded
            or len(logical.parts) < 3
            or tuple(logical.parts[:2]) != ("screening", "results")
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                f"批量结果 SDF artifact 路径无效：{recorded}",
            )
        add_file(PurePosixPath(*logical.parts[1:]).as_posix())

    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        add_file(f"inputs/ligands/{item_id}.pdbqt")
        topology_file = str(item.get("source_topology_file") or "")
        if topology_file:
            logical_topology = PurePosixPath(topology_file)
            expected_topology_parent = (
                "screening",
                "inputs",
                "topology",
            )
            if (
                len(logical_topology.parts) != 4
                or tuple(logical_topology.parts[:3])
                != expected_topology_parent
                or Path(logical_topology.name).stem != item_id
                or Path(logical_topology.name).suffix.lower()
                not in {".sdf", ".mol"}
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"配体 {item_id} 的原始拓扑路径不符合导出协议。",
                )
            add_file(f"inputs/topology/{logical_topology.name}")
        seen_attempts: set[int] = set()
        for attempt in item.get("attempts") or []:
            if not isinstance(attempt, dict):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"配体 {item_id} 包含无效 attempt 记录。",
                )
            attempt_number = attempt.get("attempt")
            if (
                isinstance(attempt_number, bool)
                or not isinstance(attempt_number, int)
                or attempt_number < 1
                or attempt_number in seen_attempts
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"配体 {item_id} 的 attempt 编号无效或重复。",
                )
            seen_attempts.add(attempt_number)
            attempt_tail = (
                "attempts",
                item_id,
                f"attempt_{attempt_number:03d}",
            )
            expected_directory = PurePosixPath("screening", *attempt_tail).as_posix()
            recorded_directory = str(attempt.get("directory") or "")
            if recorded_directory and recorded_directory != expected_directory:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    (
                        f"配体 {item_id} attempt {attempt_number} 的目录记录"
                        "与归档位置不一致。"
                    ),
                )
            relative_directory = PurePosixPath(*attempt_tail).as_posix()
            allowed_directories.add(relative_directory)
            allowed_directories.add(
                PurePosixPath("attempts", item_id).as_posix()
            )
            allowed_directories.add("attempts")

            # Older readable archives may not have every modern audit file.  Known
            # fixed-role files are included only when they physically exist.
            for filename in (
                "attempt.json",
                "receptor.pdbqt",
                "ligand.pdbqt",
                "config.txt",
                "stdout.txt",
                "stderr.txt",
                "log.txt",
            ):
                relative = f"{relative_directory}/{filename}"
                if os.path.lexists(archive_dir / Path(*PurePosixPath(relative).parts)):
                    add_file(relative)

            input_snapshots = (
                attempt.get("input_snapshots")
                if isinstance(attempt.get("input_snapshots"), dict)
                else {}
            )
            topology_snapshot = (
                input_snapshots.get("source_topology")
                if isinstance(input_snapshots.get("source_topology"), dict)
                else None
            )
            if topology_snapshot is not None:
                snapshot_name = str(
                    topology_snapshot.get("snapshot_name") or ""
                )
                expected_topology_file = PurePosixPath(
                    "screening",
                    *attempt_tail,
                    snapshot_name,
                ).as_posix()
                if (
                    not snapshot_name
                    or Path(snapshot_name).name != snapshot_name
                    or Path(snapshot_name).suffix.lower()
                    not in {".sdf", ".mol"}
                    or str(topology_snapshot.get("file") or "")
                    != expected_topology_file
                ):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                        (
                            f"配体 {item_id} attempt {attempt_number} 的"
                            "原始拓扑快照路径不符合导出协议。"
                        ),
                    )
                add_file(f"{relative_directory}/{snapshot_name}")

            output_file = str(attempt.get("output_file") or "")
            expected_output = PurePosixPath(
                "screening",
                *attempt_tail,
                "out.pdbqt",
            ).as_posix()
            if output_file:
                if output_file != expected_output:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                        (
                            f"配体 {item_id} attempt {attempt_number} 的输出路径"
                            "与归档位置不一致。"
                        ),
                    )
                add_file(f"{relative_directory}/out.pdbqt")

    return allowed_files, allowed_directories


def _scan_archive_export_tree(
    archive_dir: Path,
    allowed_files: set[str],
    allowed_directories: set[str],
) -> list[str]:
    """Reject every filesystem entry not derived from the frozen archive state."""

    seen_files: set[str] = set()
    seen_directories: set[str] = set()
    casefolded: dict[str, str] = {}
    stack: list[tuple[Path, PurePosixPath]] = [(archive_dir, PurePosixPath())]
    enumerated_entries = 0

    while stack:
        directory, relative_parent = stack.pop()
        try:
            entries: list[os.DirEntry[str]] = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    enumerated_entries += 1
                    if enumerated_entries > MAX_SCREENING_EXPORT_FILES:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                            (
                                "归档目录条目数超过导出上限 "
                                f"{MAX_SCREENING_EXPORT_FILES}。"
                            ),
                        )
                    entries.append(entry)
            entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        except _ArchiveValidationError:
            raise
        except OSError as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                f"无法枚举归档目录：{exc}",
            ) from exc
        for entry in entries:
            name = entry.name
            if not name or name in {".", ".."} or "\x00" in name:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    "归档包含无效文件名。",
                )
            relative = (relative_parent / name).as_posix()
            if len(relative.encode("utf-8")) > MAX_SCREENING_EXPORT_PATH_BYTES:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                    f"归档成员路径超过上限：{relative}",
                )
            duplicate = casefolded.get(relative.casefold())
            if duplicate is not None:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"归档包含大小写重复路径：{duplicate} / {relative}",
                )
            casefolded[relative.casefold()] = relative
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"无法读取归档成员：{relative}：{exc}",
                ) from exc
            if entry.is_symlink() or _is_reparse_stat(entry_stat):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"归档不能包含链接或重解析点：{relative}",
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                if relative not in allowed_directories:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_UNKNOWN_ENTRY",
                        f"归档包含未声明目录：{relative}",
                    )
                seen_directories.add(relative)
                stack.append((Path(entry.path), PurePosixPath(relative)))
            elif stat.S_ISREG(entry_stat.st_mode):
                if int(entry_stat.st_nlink) > 1:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                        f"归档不能包含硬链接文件：{relative}",
                    )
                if relative not in allowed_files:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_UNKNOWN_ENTRY",
                        f"归档包含未声明文件：{relative}",
                    )
                seen_files.add(relative)
            else:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
                    f"归档包含特殊文件：{relative}",
                )

    missing_files = sorted(allowed_files - seen_files)
    if missing_files:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            "归档缺少导出所需文件：" + ", ".join(missing_files),
        )
    if not seen_directories.issubset(allowed_directories):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            "归档目录树与导出协议不一致。",
        )
    return sorted(seen_files, key=lambda value: (value.casefold(), value))


def _hash_export_source(
    path: Path,
    *,
    expected_identity: tuple[int, ...] | None = None,
    capture: bytearray | None = None,
    capture_limit: int | None = None,
) -> tuple[str, int, tuple[int, ...]]:
    before = _validate_export_regular_file(
        path,
        code="SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
        label="归档源文件",
    )
    before_identity = _export_file_identity(before)
    if expected_identity is not None and before_identity != expected_identity:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"导出期间归档源文件发生变化：{path.name}",
        )
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= int(os.O_NOFOLLOW)
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"无法安全打开归档源文件 {path.name}：{exc}",
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if _export_file_identity(opened) != before_identity:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                f"打开归档源文件时身份发生变化：{path.name}",
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(SCREENING_EXPORT_CHUNK_BYTES), b""):
                digest.update(chunk)
                size += len(chunk)
                if capture is not None:
                    if capture_limit is not None and size > capture_limit:
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                            (
                                f"归档语义 JSON 超过单文件上限 "
                                f"{capture_limit} bytes：{path.name}"
                            ),
                        )
                    capture.extend(chunk)
        after_open = os.fstat(descriptor)
        if _export_file_identity(after_open) != before_identity:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                f"读取归档源文件时内容发生变化：{path.name}",
            )
    finally:
        os.close(descriptor)
    after_path = _validate_export_regular_file(
        path,
        code="SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
        label="归档源文件",
    )
    if _export_file_identity(after_path) != before_identity:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            f"读取后归档源文件身份发生变化：{path.name}",
        )
    return digest.hexdigest(), size, before_identity


def _snapshot_archive_export_sources(
    archive_dir: Path,
    relative_files: list[str],
) -> tuple[list[dict[str, Any]], int]:
    if len(relative_files) > MAX_SCREENING_EXPORT_FILES:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
            f"归档文件数超过导出上限 {MAX_SCREENING_EXPORT_FILES}。",
        )
    records: list[dict[str, Any]] = []
    total_size = 0
    for relative in relative_files:
        zip_path = PurePosixPath(
            SCREENING_EXPORT_PAYLOAD_DIRECTORY,
            archive_dir.name,
            *PurePosixPath(relative).parts,
        ).as_posix()
        if len(zip_path.encode("utf-8")) > MAX_SCREENING_EXPORT_PATH_BYTES:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                f"ZIP 成员路径超过导出上限：{zip_path}",
            )
        source = archive_dir.joinpath(*PurePosixPath(relative).parts)
        relative_parts = PurePosixPath(relative).parts
        is_root_metadata = relative in {
            "archive_manifest.json",
            "screening.json",
        }
        is_attempt_metadata = (
            len(relative_parts) == 4
            and relative_parts[0] == "attempts"
            and relative_parts[2].startswith("attempt_")
            and relative_parts[3] == "attempt.json"
        )
        captured = (
            bytearray()
            if is_root_metadata or is_attempt_metadata
            else None
        )
        capture_limit = (
            MAX_ARCHIVE_METADATA_BYTES
            if is_root_metadata
            else (
                MAX_SCREENING_EXPORT_SEMANTIC_JSON_BYTES
                if is_attempt_metadata
                else None
            )
        )
        digest, size, identity = _hash_export_source(
            source,
            capture=captured,
            capture_limit=capture_limit,
        )
        if size > MAX_SCREENING_EXPORT_MEMBER_BYTES:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                f"归档成员超过单文件上限：{relative}",
            )
        total_size += size
        if total_size > MAX_SCREENING_EXPORT_TOTAL_BYTES:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                "归档未压缩总大小超过导出上限。",
            )
        record = {
            "path": relative,
            "zip_path": zip_path,
            "sha256": digest,
            "size_bytes": size,
            "_identity": identity,
        }
        if captured is not None:
            assert capture_limit is not None
            if size <= 0 or size > capture_limit:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
                    f"归档元数据快照大小无效：{relative}",
                )
            captured_json = _strict_json_object_bytes(
                bytes(captured),
                label=f"归档元数据 {relative}",
            )
            semantic_sha256 = hashlib.sha256(
                _canonical_json_bytes(captured_json)
            ).hexdigest()
            record["_semantic_sha256"] = semantic_sha256
            if is_root_metadata:
                record["_json_value"] = captured_json
        records.append(record)
    return records, total_size


def _screening_export_warnings(
    normalized_state: dict[str, Any],
    resource_integrity: dict[str, Any],
    attempt_integrity: dict[str, Any],
    output_integrity: dict[str, Any],
) -> list[str]:
    warnings = [
        str(value)
        for value in attempt_integrity.get("warnings") or []
        if str(value).strip()
    ]
    attempt_status = str(attempt_integrity.get("status") or "unknown")
    if attempt_status not in {"verified", "not_applicable"}:
        warnings.append(
            f"逐次运行证据状态为 {attempt_status}；ZIP 保留原归档的兼容性标记。"
        )
    resource_status = str(resource_integrity.get("status") or "unknown")
    if resource_status != "verified":
        warnings.append(
            f"资源策略证据状态为 {resource_status}；该状态已写入导出清单。"
        )
    warnings.extend(
        str(value)
        for value in output_integrity.get("warnings") or []
        if str(value).strip()
    )
    compatibility = (
        normalized_state.get("compatibility")
        if isinstance(normalized_state.get("compatibility"), dict)
        else {}
    )
    inferred_vina = compatibility.get("inferred_vina_fields")
    if isinstance(inferred_vina, list) and inferred_vina:
        warnings.append("旧归档缺少部分高级 Vina 字段，读取时使用兼容默认值。")
    inferred_limits = compatibility.get("inferred_resource_limit_fields")
    if isinstance(inferred_limits, list) and inferred_limits:
        warnings.append("旧归档缺少部分资源限制字段，读取时使用兼容默认值。")
    warnings.extend(
        (
            (
                "导出包保留原始 screening.json 与 attempt 记录，可能包含本机"
                "绝对路径或原始文件名；分享前请检查并脱敏。"
            ),
            (
                "此 ZIP 未匿名化、未进行数字签名，且不能用于恢复活动筛选"
                "队列。"
            ),
        )
    )
    return list(dict.fromkeys(warnings))


def _archive_export_recorded_output_identity(
    record: dict[str, Any],
    path: Path,
    *,
    archive_id: str,
    label: str,
    snapshot_record: dict[str, Any] | None = None,
) -> tuple[bool, str, int]:
    if snapshot_record is None:
        actual_sha256, actual_size, _identity = _hash_export_source(path)
    else:
        try:
            actual_sha256 = str(snapshot_record["sha256"]).lower()
            actual_size = int(snapshot_record["size_bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                f"归档 {archive_id} 的{label}输出未绑定有效导出快照。",
            ) from exc
        if (
            SHA256_PATTERN.fullmatch(actual_sha256) is None
            or actual_size < 0
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                f"归档 {archive_id} 的{label}输出快照身份无效。",
            )

    raw_sha256 = record.get("output_sha256")
    hash_present = raw_sha256 not in (None, "")
    if hash_present:
        if not isinstance(raw_sha256, str):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_IDENTITY_INVALID",
                f"归档 {archive_id} 的{label}输出 SHA256 必须是字符串。",
            )
        recorded_sha256 = raw_sha256.strip().lower()
        if SHA256_PATTERN.fullmatch(recorded_sha256) is None:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_IDENTITY_INVALID",
                f"归档 {archive_id} 的{label}输出 SHA256 无效。",
            )
        if recorded_sha256 != actual_sha256:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_OUTPUT_HASH_MISMATCH",
                f"归档 {archive_id} 的{label}输出 SHA256 与文件不一致。",
            )

    raw_size = record.get("output_size_bytes")
    size_present = raw_size is not None and (
        hash_present or raw_size != 0 or actual_size == 0
    )
    if raw_size is not None and (
        isinstance(raw_size, bool)
        or not isinstance(raw_size, int)
        or raw_size < 0
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_OUTPUT_IDENTITY_INVALID",
            f"归档 {archive_id} 的{label}输出大小证据无效。",
        )
    if size_present and int(raw_size) != actual_size:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_OUTPUT_SIZE_MISMATCH",
            f"归档 {archive_id} 的{label}输出大小与文件不一致。",
        )
    return hash_present and size_present, actual_sha256, actual_size


def _validate_archive_export_output_identities(
    archive_dir: Path,
    state: dict[str, Any],
    *,
    archive_id: str,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counts = {
        "total": 0,
        "verified": 0,
        "legacy_unverified": 0,
        "result_files_total": 0,
        "result_files_verified": 0,
        "result_files_legacy_unverified": 0,
    }
    warnings: list[str] = []
    legacy_result_labels: list[str] = []
    for item in state.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        best_attempt: dict[str, Any] | None = None
        best_identity: tuple[str, int] | None = None
        if item.get("status") == "succeeded":
            best_attempt = _succeeded_output_attempt(item)
        for attempt in item.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            output_file = str(attempt.get("output_file") or "")
            if not output_file:
                continue
            attempt_number = int(attempt.get("attempt") or 0)
            output_path = _relocate_archive_logical_file(
                archive_dir,
                output_file,
                expected_tail=(
                    "attempts",
                    item_id,
                    f"attempt_{attempt_number:03d}",
                    "out.pdbqt",
                ),
            )
            relative_output = PurePosixPath(
                "attempts",
                item_id,
                f"attempt_{attempt_number:03d}",
                "out.pdbqt",
            ).as_posix()
            snapshot_record = None
            if source_records is not None:
                snapshot_record = source_records.get(relative_output)
                if snapshot_record is None:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                        (
                            f"归档 {archive_id} 的 {item_id} attempt "
                            f"{attempt_number} 输出不在导出快照中。"
                        ),
                    )
            counts["total"] += 1
            complete, actual_sha256, actual_size = (
                _archive_export_recorded_output_identity(
                    attempt,
                    output_path,
                    archive_id=archive_id,
                    label=f"{item_id} attempt {attempt_number}",
                    snapshot_record=snapshot_record,
                )
            )
            if complete:
                counts["verified"] += 1
            else:
                counts["legacy_unverified"] += 1
            if attempt is best_attempt:
                best_identity = (actual_sha256, actual_size)

        if best_attempt is not None:
            if best_identity is None:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_OUTPUT_IDENTITY_INVALID",
                    f"归档 {archive_id} 的 {item_id} 最佳输出无法定位。",
                )
            best_path = _relocate_archive_logical_file(
                archive_dir,
                item.get("best_output_file"),
                expected_tail=(
                    "attempts",
                    item_id,
                    f"attempt_{int(best_attempt.get('attempt') or 0):03d}",
                    "out.pdbqt",
                ),
            )
            item_record = {
                "output_sha256": item.get("best_output_sha256"),
                "output_size_bytes": item.get("best_output_size_bytes"),
            }
            complete, item_sha256, item_size = (
                _archive_export_recorded_output_identity(
                    item_record,
                    best_path,
                    archive_id=archive_id,
                    label=f"{item_id} 最佳构象",
                    snapshot_record=(
                        source_records.get(
                            PurePosixPath(
                                "attempts",
                                item_id,
                                (
                                    "attempt_"
                                    f"{int(best_attempt.get('attempt') or 0):03d}"
                                ),
                                "out.pdbqt",
                            ).as_posix()
                        )
                        if source_records is not None
                        else None
                    ),
                )
            )
            if (item_sha256, item_size) != best_identity:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_OUTPUT_IDENTITY_INVALID",
                    f"归档 {archive_id} 的 {item_id} 最佳输出证据互相矛盾。",
                )
            counts["total"] += 1
            if complete:
                counts["verified"] += 1
            else:
                counts["legacy_unverified"] += 1

    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    result_specs = (
        (
            "summary_csv",
            "summary_sha256",
            "summary_size_bytes",
            ("results", "screening_summary.csv"),
            "完整汇总 CSV",
        ),
        (
            "top_n_csv",
            "top_n_sha256",
            "top_n_size_bytes",
            ("results", "screening_top_n.csv"),
            "Top N 汇总 CSV",
        ),
        (
            "report_md",
            "report_sha256",
            "report_size_bytes",
            ("results", "screening_report.md"),
            "Markdown 实验记录",
        ),
    )
    for path_key, sha_key, size_key, expected_tail, label in result_specs:
        logical_file = str(outputs.get(path_key) or "")
        if not logical_file:
            continue
        result_path = _relocate_archive_logical_file(
            archive_dir,
            logical_file,
            expected_tail=expected_tail,
        )
        relative_result = PurePosixPath(*expected_tail).as_posix()
        snapshot_record = None
        if source_records is not None:
            snapshot_record = source_records.get(relative_result)
            if snapshot_record is None:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                    f"归档 {archive_id} 的{label}不在导出快照中。",
                )
        evidence_record = {
            "output_sha256": outputs.get(sha_key),
            "output_size_bytes": outputs.get(size_key),
        }
        complete, _actual_sha256, _actual_size = (
            _archive_export_recorded_output_identity(
                evidence_record,
                result_path,
                archive_id=archive_id,
                label=label,
                snapshot_record=snapshot_record,
            )
        )
        counts["total"] += 1
        counts["result_files_total"] += 1
        if complete:
            counts["verified"] += 1
            counts["result_files_verified"] += 1
        else:
            counts["legacy_unverified"] += 1
            counts["result_files_legacy_unverified"] += 1
            legacy_result_labels.append(label)

    if counts["total"] == 0:
        status = "not_applicable"
    elif counts["legacy_unverified"] == 0:
        status = "verified"
    elif counts["verified"] == 0:
        status = "legacy_unverified"
    else:
        status = "partially_verified"
    if counts["legacy_unverified"]:
        warnings.append(
            f"{counts['legacy_unverified']} 条历史输出身份缺少完整 SHA256 或大小证据。"
        )
    if legacy_result_labels:
        warnings.append(
            "以下历史结果产物缺少完整身份凭据："
            + "、".join(dict.fromkeys(legacy_result_labels))
            + "。"
        )
    return {
        "status": status,
        "counts": counts,
        "warnings": warnings,
    }


def _validated_screening_export_source(
    root: Path,
    archive_id: str,
    *,
    source_records: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    archive_dir, source_manifest, state = _validate_archive_bundle(root, archive_id)
    if source_records is not None:
        manifest_record = source_records.get("archive_manifest.json")
        state_record = source_records.get("screening.json")
        if (
            manifest_record is None
            or state_record is None
            or not isinstance(manifest_record.get("_json_value"), dict)
            or not isinstance(state_record.get("_json_value"), dict)
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "归档 manifest 或 screening 状态没有有效语义快照。",
            )
        try:
            parsed_manifest_semantic = hashlib.sha256(
                _canonical_json_bytes(source_manifest)
            ).hexdigest()
            parsed_state_semantic = hashlib.sha256(
                _canonical_json_bytes(state)
            ).hexdigest()
        except (
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "路径复核得到的归档元数据无法规范化。",
            ) from exc
        if (
            parsed_manifest_semantic
            != str(manifest_record.get("_semantic_sha256") or "")
            or parsed_state_semantic
            != str(state_record.get("_semantic_sha256") or "")
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "路径复核得到的归档元数据与安全快照语义不一致。",
            )
        # Every downstream integrity decision must consume the exact objects
        # parsed from the source_records byte snapshot.
        source_manifest = manifest_record["_json_value"]
        state = state_record["_json_value"]
    resource_integrity = _validate_archived_resource_state(
        archive_dir,
        state,
        archive_id=archive_id,
        error_code="SCREENING_ARCHIVE_RESOURCE_POLICY_INVALID",
        verify_files=True,
        source_records=source_records,
    )
    item_labels = _archive_item_labels(root, state, source_manifest)
    normalized_state = _normalized_archived_state(state, item_labels)
    _archive_files(
        root,
        archive_dir,
        normalized_state,
        source_records=source_records,
    )
    attempt_integrity = _archive_attempt_integrity(
        archive_dir,
        state,
        archive_id=archive_id,
        source_records=source_records,
    )
    output_integrity = _validate_archive_export_output_identities(
        archive_dir,
        state,
        archive_id=archive_id,
        source_records=source_records,
    )
    return {
        "archive_dir": archive_dir,
        "source_manifest": source_manifest,
        "state": state,
        "normalized_state": normalized_state,
        "resource_integrity": resource_integrity,
        "attempt_integrity": attempt_integrity,
        "output_integrity": output_integrity,
    }


def _screening_export_manifest(
    *,
    archive_id: str,
    source_manifest: dict[str, Any],
    state: dict[str, Any],
    records: list[dict[str, Any]],
    total_size: int,
    source_integrity: dict[str, str],
    warnings: list[str],
) -> tuple[dict[str, Any], bytes, str]:
    public_records = [
        {
            "path": str(record["path"]),
            "zip_path": str(record["zip_path"]),
            "sha256": str(record["sha256"]),
            "size_bytes": int(record["size_bytes"]),
        }
        for record in records
    ]
    tree_payload = _canonical_json_bytes(public_records)
    payload_tree_sha256 = hashlib.sha256(tree_payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "artifact_type": "dockstart_screening_archive_zip",
        "archive_id": archive_id,
        "screening_id": str(state.get("screening_id") or ""),
        "status": str(state.get("status") or ""),
        "source_archived_at": (
            source_manifest.get("archived_at")
            if isinstance(source_manifest.get("archived_at"), str)
            else None
        ),
        "payload": {
            "root": PurePosixPath(
                SCREENING_EXPORT_PAYLOAD_DIRECTORY,
                archive_id,
            ).as_posix(),
            "file_count": len(public_records),
            "uncompressed_bytes": total_size,
            "tree_sha256": payload_tree_sha256,
            "tree_hash_format": "sha256(canonical-json(files))",
            "files": public_records,
        },
        "source_integrity": source_integrity,
        "warnings": warnings,
        "excluded": [
            "AutoDock Vina binary",
            "project.json",
            "raw/",
            "screening/staging/",
        ],
    }
    manifest_bytes = _canonical_json_bytes(manifest) + b"\n"
    if len(manifest_bytes) > MAX_SCREENING_EXPORT_MANIFEST_BYTES:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_LIMIT_EXCEEDED",
            "导出清单超过大小上限。",
        )
    return manifest, manifest_bytes, payload_tree_sha256


def _screening_export_zip_info(name: str) -> zipfile.ZipInfo:
    if (
        not name
        or "\\" in name
        or PurePosixPath(name).is_absolute()
        or ".." in PurePosixPath(name).parts
        or len(name.encode("utf-8")) > MAX_SCREENING_EXPORT_PATH_BYTES
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_CONTENT_INVALID",
            f"ZIP 成员名称无效：{name or 'missing'}",
        )
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.flag_bits |= 0x800
    info.extra = b""
    info.comment = b""
    return info


def _write_screening_export_zip(
    temporary_handle: Any,
    archive_dir: Path,
    records: list[dict[str, Any]],
    manifest_bytes: bytes,
) -> None:
    temporary_handle.seek(0)
    temporary_handle.truncate(0)
    with zipfile.ZipFile(
        temporary_handle,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as bundle:
        manifest_info = _screening_export_zip_info(SCREENING_EXPORT_MANIFEST_NAME)
        with bundle.open(manifest_info, mode="w", force_zip64=True) as target:
            target.write(manifest_bytes)
        for record in records:
            source = archive_dir.joinpath(*PurePosixPath(record["path"]).parts)
            before = _validate_export_regular_file(
                source,
                code="SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                label="归档源文件",
            )
            if _export_file_identity(before) != record["_identity"]:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                    f"写入 ZIP 前源文件发生变化：{record['path']}",
                )
            digest = hashlib.sha256()
            written = 0
            flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
            if hasattr(os, "O_NOFOLLOW"):
                flags |= int(os.O_NOFOLLOW)
            descriptor = os.open(source, flags)
            try:
                opened = os.fstat(descriptor)
                if _export_file_identity(opened) != record["_identity"]:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                        f"打开源文件时身份发生变化：{record['path']}",
                    )
                with (
                    os.fdopen(descriptor, "rb", closefd=False) as source_handle,
                    bundle.open(
                        _screening_export_zip_info(str(record["zip_path"])),
                        mode="w",
                        force_zip64=True,
                    ) as target,
                ):
                    for chunk in iter(
                        lambda: source_handle.read(SCREENING_EXPORT_CHUNK_BYTES),
                        b"",
                    ):
                        target.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                after_open = os.fstat(descriptor)
                if _export_file_identity(after_open) != record["_identity"]:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                        f"写入 ZIP 时源文件发生变化：{record['path']}",
                    )
            finally:
                os.close(descriptor)
            if (
                written != int(record["size_bytes"])
                or digest.hexdigest() != str(record["sha256"])
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                    f"写入 ZIP 时源文件内容发生变化：{record['path']}",
                )
            after = _validate_export_regular_file(
                source,
                code="SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                label="归档源文件",
            )
            if _export_file_identity(after) != record["_identity"]:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                    f"写入 ZIP 后源文件身份发生变化：{record['path']}",
                )
    temporary_handle.flush()
    os.fsync(temporary_handle.fileno())


def _verify_screening_export_zip(
    zip_handle: Any,
    records: list[dict[str, Any]],
    manifest_bytes: bytes,
) -> None:
    expected_names = [SCREENING_EXPORT_MANIFEST_NAME] + [
        str(record["zip_path"]) for record in records
    ]
    expected_by_name = {
        str(record["zip_path"]): record
        for record in records
    }
    try:
        zip_handle.seek(0)
        with zipfile.ZipFile(zip_handle, mode="r", allowZip64=True) as bundle:
            infos = bundle.infolist()
            actual_names = [info.filename for info in infos]
            if actual_names != expected_names:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                    "ZIP 条目顺序或集合与导出清单不一致。",
                )
            if len({name.casefold() for name in actual_names}) != len(actual_names):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                    "ZIP 包含大小写重复条目。",
                )
            bad_member = bundle.testzip()
            if bad_member is not None:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                    f"ZIP CRC 校验失败：{bad_member}",
                )
            for info in infos:
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if (
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or info.create_system != 3
                    or stat.S_IFMT(unix_mode) != stat.S_IFREG
                    or stat.S_IMODE(unix_mode) != 0o644
                    or info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.compress_type != zipfile.ZIP_DEFLATED
                ):
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                        f"ZIP 条目类型或固定元数据无效：{info.filename}",
                    )
                digest = hashlib.sha256()
                actual_size = 0
                with bundle.open(info, mode="r") as source:
                    for chunk in iter(
                        lambda: source.read(SCREENING_EXPORT_CHUNK_BYTES),
                        b"",
                    ):
                        digest.update(chunk)
                        actual_size += len(chunk)
                if actual_size != info.file_size:
                    raise _ArchiveValidationError(
                        "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                        f"ZIP 条目大小校验失败：{info.filename}",
                    )
                if info.filename == SCREENING_EXPORT_MANIFEST_NAME:
                    if (
                        actual_size != len(manifest_bytes)
                        or digest.hexdigest()
                        != hashlib.sha256(manifest_bytes).hexdigest()
                    ):
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                            "ZIP 外层清单内容校验失败。",
                        )
                else:
                    record = expected_by_name[info.filename]
                    if (
                        actual_size != int(record["size_bytes"])
                        or digest.hexdigest() != str(record["sha256"])
                    ):
                        raise _ArchiveValidationError(
                            "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
                            f"ZIP payload 校验失败：{info.filename}",
                        )
    except _ArchiveValidationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_ZIP_INVALID",
            f"无法重开并核对导出的 ZIP：{exc}",
        ) from exc


def _hash_screening_export_handle(handle: Any) -> tuple[str, int]:
    handle.flush()
    handle.seek(0)
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(SCREENING_EXPORT_CHUNK_BYTES), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _temporary_export_object_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
    )


def _validate_temporary_export_path(
    path: Path,
    expected_object_identity: tuple[int, ...],
) -> os.stat_result:
    value = _validate_export_regular_file(
        path,
        code="SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
        label="导出临时文件",
    )
    if _temporary_export_object_identity(value) != expected_object_identity:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
            "导出临时路径已被替换，已拒绝发布。",
        )
    return value


def _cleanup_temporary_export_path(
    path: Path | None,
    expected_object_identity: tuple[int, ...] | None,
) -> None:
    if path is None or expected_object_identity is None:
        return
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    if (
        not path.is_symlink()
        and not _is_reparse_stat(value)
        and stat.S_ISREG(value.st_mode)
        and _temporary_export_object_identity(value) == expected_object_identity
    ):
        path.unlink(missing_ok=True)


def _validate_screening_export_file_content(
    path: Path,
    *,
    expected_object_identity: tuple[int, ...],
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> tuple[str, int]:
    value = _validate_temporary_export_path(path, expected_object_identity)
    digest, size, identity = _hash_export_source(path)
    if (
        tuple(identity[:3]) != expected_object_identity
        or size != expected_size
        or digest != expected_sha256
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
            f"{label}内容或身份与写后校验结果不一致。",
        )
    # The path check after hashing closes replacement races during the read.
    after = _validate_temporary_export_path(path, expected_object_identity)
    if (
        int(after.st_size) != expected_size
        or _temporary_export_object_identity(value)
        != _temporary_export_object_identity(after)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
            f"{label}在内容复核期间发生变化。",
        )
    return digest, size


def _publish_screening_export_zip(
    temporary_file: Path,
    destination: Path,
    *,
    overwritten: bool,
    expected_object_identity: tuple[int, ...],
    expected_sha256: str,
    expected_size: int,
) -> tuple[str, int]:
    _validate_screening_export_file_content(
        temporary_file,
        expected_object_identity=expected_object_identity,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        label="待发布 ZIP",
    )
    if overwritten:
        os.replace(temporary_file, destination)
    elif os.name == "nt":
        try:
            os.rename(temporary_file, destination)
        except FileExistsError as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_DESTINATION_CHANGED",
                "发布导出文件时目标路径已被占用，未覆盖现有文件。",
            ) from exc
    else:
        try:
            os.link(temporary_file, destination)
        except FileExistsError as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_DESTINATION_CHANGED",
                "发布导出文件时目标路径已被占用，未覆盖现有文件。",
            ) from exc
        except OSError as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_ATOMIC_PUBLISH_UNSUPPORTED",
                "当前文件系统不支持默认不覆盖的原子发布。",
            ) from exc
        destination_stat = destination.lstat()
        destination_is_known_temporary = (
            not destination.is_symlink()
            and not _is_reparse_stat(destination_stat)
            and stat.S_ISREG(destination_stat.st_mode)
            and _temporary_export_object_identity(destination_stat)
            == expected_object_identity
        )
        if (
            not destination_is_known_temporary
            or int(destination_stat.st_nlink) != 2
        ):
            if destination_is_known_temporary:
                _cleanup_temporary_export_path(
                    destination,
                    expected_object_identity,
                )
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
                (
                    "导出临时文件在发布时发生变化；已清理本次导出目标。"
                    if destination_is_known_temporary
                    else "导出临时文件在发布时发生变化；未删除身份未知的目标路径。"
                ),
            )
        temporary_file.unlink()

    try:
        return _validate_screening_export_file_content(
            destination,
            expected_object_identity=expected_object_identity,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label="已发布 ZIP",
        )
    except Exception:
        # Only unlink when the destination still names the exact temporary
        # object created by this export.  An attacker-replaced path is preserved.
        _cleanup_temporary_export_path(destination, expected_object_identity)
        raise


def _verify_archive_export_sources_unchanged(
    archive_dir: Path,
    allowed_files: set[str],
    allowed_directories: set[str],
    records: list[dict[str, Any]],
) -> None:
    rescanned = _scan_archive_export_tree(
        archive_dir,
        allowed_files,
        allowed_directories,
    )
    if rescanned != [str(record["path"]) for record in records]:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
            "导出期间归档目录树发生变化。",
        )
    for record in records:
        source = archive_dir.joinpath(*PurePosixPath(record["path"]).parts)
        digest, size, identity = _hash_export_source(
            source,
            expected_identity=record["_identity"],
        )
        if digest != record["sha256"] or size != record["size_bytes"] or identity != record["_identity"]:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                f"导出期间归档源文件发生变化：{record['path']}",
            )


def _screening_export_destination_snapshot(
    destination: Path,
    *,
    expected_identity: tuple[int, ...] | None = None,
) -> tuple[dict[str, Any], tuple[int, ...], str, int]:
    try:
        digest, size, identity = _hash_export_source(
            destination,
            expected_identity=expected_identity,
        )
    except _ArchiveValidationError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_CHANGED",
            "无法稳定读取现有导出目标的身份。",
            details={"destination_file": str(destination)},
        ) from exc
    details = {
        "destination_file": str(destination),
        "destination_sha256": digest,
        "destination_size_bytes": size,
    }
    return details, identity, digest, size


def _resolve_screening_export_destination(
    root: Path,
    archive_dir: Path,
    destination_file: str | Path,
    *,
    overwrite: bool,
) -> tuple[Path, bool, tuple[int, ...] | None]:
    supplied = Path(destination_file).expanduser()
    if supplied.suffix.lower() != ".zip" or supplied.name in {"", ".", ".."}:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出目标必须是以 .zip 结尾的文件路径。",
        )
    lexical_parent = Path(os.path.abspath(str(supplied.parent)))
    try:
        parent = supplied.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出目标的父目录不存在。",
        ) from exc
    if not parent.is_dir():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出目标的父路径不是目录。",
        )
    parent_stat = parent.lstat()
    if parent.is_symlink() or _is_reparse_stat(parent_stat):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出目标父目录不能是链接或重解析点。",
        )
    if os.path.normcase(str(lexical_parent)) != os.path.normcase(str(parent)):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出目标父路径不能通过链接或重解析路径访问。",
        )
    destination = parent / supplied.name
    archive_root = _archive_root_directory(root, required=True)
    assert archive_root is not None
    try:
        destination.relative_to(archive_root)
    except ValueError:
        pass
    else:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出 ZIP 必须位于 screening/archive 目录之外。",
        )
    try:
        destination.relative_to(archive_dir)
    except ValueError:
        pass
    else:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            "导出 ZIP 不能写入源归档目录。",
        )

    exists = os.path.lexists(destination)
    identity: tuple[int, ...] | None = None
    if exists:
        destination_stat = _validate_export_regular_file(
            destination,
            code="SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
            label="现有导出目标",
        )
        identity = _export_file_identity(destination_stat)
        if not overwrite:
            details, identity, _, _ = _screening_export_destination_snapshot(
                destination,
                expected_identity=identity,
            )
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_EXISTS",
                "导出目标已经存在；如需替换，请显式启用 overwrite。",
                details=details,
            )
    return destination, exists, identity


def export_screening_archive_zip(
    project_dir: str,
    archive_id: str,
    destination_file: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export one validated read-only screening archive as a deterministic ZIP."""

    temporary_file: Path | None = None
    temporary_handle: Any | None = None
    temporary_object_identity: tuple[int, ...] | None = None
    try:
        if not isinstance(overwrite, bool):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
                "overwrite 必须是布尔值。",
            )
        root = _project_root(project_dir)
        initial = _validated_screening_export_source(
            root,
            archive_id,
        )
        archive_dir = initial["archive_dir"]
        allowed_files, allowed_directories = _archive_export_allowlist(
            archive_dir,
            initial["state"],
        )
        relative_files = _scan_archive_export_tree(
            archive_dir,
            allowed_files,
            allowed_directories,
        )
        records, total_size = _snapshot_archive_export_sources(
            archive_dir,
            relative_files,
        )
        records_by_path = {
            str(record["path"]): record
            for record in records
        }
        snapshot_manifest = records_by_path["archive_manifest.json"].get(
            "_json_value"
        )
        snapshot_state_sha256 = str(
            records_by_path["screening.json"]["sha256"]
        ).lower()
        snapshot_recorded_state_sha256 = (
            str(snapshot_manifest.get("state_sha256") or "").lower()
            if isinstance(snapshot_manifest, dict)
            else ""
        )
        if (
            SHA256_PATTERN.fullmatch(snapshot_recorded_state_sha256) is None
            or snapshot_recorded_state_sha256 != snapshot_state_sha256
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_STATE_HASH_MISMATCH",
                "归档 manifest 记录的 screening.json SHA256 与安全快照不一致。",
            )
        try:
            initial_manifest_semantic = hashlib.sha256(
                _canonical_json_bytes(initial["source_manifest"])
            ).hexdigest()
            initial_state_semantic = hashlib.sha256(
                _canonical_json_bytes(initial["state"])
            ).hexdigest()
        except (
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "初始归档元数据无法规范化。",
            ) from exc
        if (
            initial_manifest_semantic
            != records_by_path["archive_manifest.json"].get("_semantic_sha256")
            or initial_state_semantic
            != records_by_path["screening.json"].get("_semantic_sha256")
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "归档元数据在初始校验与安全快照之间发生变化。",
            )
        # Re-run every detail-level semantic and integrity check after the
        # byte/identity snapshot.  The following full source verification binds
        # those checks to the exact bytes later written into the ZIP.
        validated = _validated_screening_export_source(
            root,
            archive_id,
            source_records=records_by_path,
        )
        if validated["archive_dir"] != archive_dir:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "导出期间所选归档目录发生变化。",
            )
        try:
            validated_manifest_semantic = hashlib.sha256(
                _canonical_json_bytes(validated["source_manifest"])
            ).hexdigest()
            validated_state_semantic = hashlib.sha256(
                _canonical_json_bytes(validated["state"])
            ).hexdigest()
        except (
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "复核后的归档元数据无法规范化。",
            ) from exc
        if (
            validated_manifest_semantic
            != records_by_path["archive_manifest.json"].get("_semantic_sha256")
            or validated_state_semantic
            != records_by_path["screening.json"].get("_semantic_sha256")
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "归档元数据语义校验结果与安全快照不一致。",
            )
        rebound_files, rebound_directories = _archive_export_allowlist(
            archive_dir,
            validated["state"],
        )
        if (
            rebound_files != allowed_files
            or rebound_directories != allowed_directories
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_SOURCE_CHANGED",
                "导出期间归档声明的文件树发生变化。",
            )
        _verify_archive_export_sources_unchanged(
            archive_dir,
            allowed_files,
            allowed_directories,
            records,
        )
        source_manifest = validated["source_manifest"]
        state = validated["state"]
        normalized_state = validated["normalized_state"]
        resource_integrity = validated["resource_integrity"]
        attempt_integrity = validated["attempt_integrity"]
        output_integrity = validated["output_integrity"]
        source_integrity = {
            "bundle": "verified",
            "resource": str(resource_integrity.get("status") or "unknown"),
            "attempt": str(attempt_integrity.get("status") or "unknown"),
            "output": str(output_integrity.get("status") or "unknown"),
        }
        warnings = _screening_export_warnings(
            normalized_state,
            resource_integrity,
            attempt_integrity,
            output_integrity,
        )
        destination, overwritten, destination_identity = (
            _resolve_screening_export_destination(
                root,
                archive_dir,
                destination_file,
                overwrite=overwrite,
            )
        )
        if destination_identity is not None and any(
            destination_identity == record["_identity"] for record in records
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_DESTINATION_INVALID",
                "导出目标不能是归档源文件的硬链接。",
            )
        _manifest, manifest_bytes, payload_tree_sha256 = (
            _screening_export_manifest(
                archive_id=archive_id,
                source_manifest=source_manifest,
                state=state,
                records=records,
                total_size=total_size,
                source_integrity=source_integrity,
                warnings=warnings,
            )
        )
        temporary_handle = tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary_file = Path(temporary_handle.name)
        temporary_stat = os.fstat(temporary_handle.fileno())
        if (
            _is_reparse_stat(temporary_stat)
            or not stat.S_ISREG(temporary_stat.st_mode)
            or int(temporary_stat.st_nlink) != 1
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
                "无法建立安全的普通临时文件。",
            )
        temporary_object_identity = _temporary_export_object_identity(
            temporary_stat,
        )
        path_stat = _validate_temporary_export_path(
            temporary_file,
            temporary_object_identity,
        )
        if _temporary_export_object_identity(path_stat) != temporary_object_identity:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_TEMP_INVALID",
                "导出临时文件描述符与路径身份不一致。",
            )
        _write_screening_export_zip(
            temporary_handle,
            archive_dir,
            records,
            manifest_bytes,
        )
        _verify_screening_export_zip(
            temporary_handle,
            records,
            manifest_bytes,
        )
        _verify_archive_export_sources_unchanged(
            archive_dir,
            allowed_files,
            allowed_directories,
            records,
        )

        if not overwritten and os.path.lexists(destination):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_EXPORT_DESTINATION_CHANGED",
                "导出期间目标路径被其他文件占用，已拒绝覆盖。",
            )

        zip_sha256, zip_size = _hash_screening_export_handle(temporary_handle)
        temporary_handle.close()
        temporary_handle = None
        _validate_temporary_export_path(
            temporary_file,
            temporary_object_identity,
        )
        published_sha256, published_size = _publish_screening_export_zip(
            temporary_file,
            destination,
            overwritten=overwritten,
            expected_object_identity=temporary_object_identity,
            expected_sha256=zip_sha256,
            expected_size=zip_size,
        )
        temporary_file = None
        exported_at = _now_iso()
        return {
            "ok": True,
            "project_dir": str(root),
            "archive_id": archive_id,
            "zip_file": str(destination),
            "zip_sha256": published_sha256,
            "size_bytes": published_size,
            "entry_count": len(records) + 1,
            "payload_uncompressed_bytes": total_size,
            "payload_tree_sha256": payload_tree_sha256,
            "source_integrity": source_integrity,
            "warnings": warnings,
            "overwritten": overwritten,
            "exported_at": exported_at,
            "message": "批量筛选归档 ZIP 已通过写后校验并导出。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - public export boundary.
        if temporary_handle is not None:
            try:
                temporary_handle.close()
            except Exception:
                pass
        _cleanup_temporary_export_path(
            temporary_file,
            temporary_object_identity,
        )
        code = (
            exc.code
            if isinstance(exc, _ArchiveValidationError)
            else "SCREENING_ARCHIVE_EXPORT_ERROR"
        )
        return _error(
            code,
            "导出批量筛选归档 ZIP 失败。",
            str(exc),
            "请检查归档完整性、导出路径和文件大小后重试。",
            details=(
                exc.details
                if isinstance(exc, _ArchiveValidationError)
                else None
            ),
        )


def _comparison_recorded_sha256(
    value: Any,
    *,
    archive_id: str,
    label: str,
) -> str:
    recorded = str(value or "").strip().lower()
    if not recorded:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_HASH_MISSING",
            f"归档 {archive_id} 的{label}缺少 SHA256，不能进行可靠比较。",
        )
    if not SHA256_PATTERN.fullmatch(recorded):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_HASH_INVALID",
            f"归档 {archive_id} 的{label} SHA256 无效。",
        )
    return recorded


def _comparison_recorded_size(
    value: Any,
    *,
    archive_id: str,
    label: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_SIZE_INVALID",
            f"归档 {archive_id} 的{label}缺少有效文件大小。",
        )
    return value


def _verify_comparison_input(
    path: Path,
    record: dict[str, Any],
    *,
    archive_id: str,
    label: str,
) -> str:
    expected_sha256 = _comparison_recorded_sha256(
        record.get("sha256"),
        archive_id=archive_id,
        label=label,
    )
    expected_size = _comparison_recorded_size(
        record.get("size_bytes"),
        archive_id=archive_id,
        label=label,
    )
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_INPUT_SIZE_MISMATCH",
            (
                f"归档 {archive_id} 的{label}大小与 screening.json 不一致："
                f"记录 {expected_size} bytes，实际 {actual_size} bytes。"
            ),
        )
    actual_sha256 = _sha256(path)
    if actual_sha256.lower() != expected_sha256:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_INPUT_HASH_MISMATCH",
            f"归档 {archive_id} 的{label} SHA256 与 screening.json 不一致。",
        )
    return actual_sha256.lower()


def _archive_allows_legacy_energy_range_zero(state: dict[str, Any]) -> bool:
    raw_vina = state.get("vina") if isinstance(state.get("vina"), dict) else {}
    energy_range = raw_vina.get("energy_range")
    is_zero = (
        not isinstance(energy_range, bool)
        and isinstance(energy_range, (int, float))
        and math.isfinite(float(energy_range))
        and float(energy_range) == 0
    )
    if not is_zero:
        return False
    return _valid_legacy_energy_range_zero_marker(state) or all(
        key not in raw_vina for key in SCREENING_VINA_DEFAULTS
    )


def _comparison_resource_bounds(
    state: dict[str, Any],
    *,
    archive_id: str,
) -> dict[str, int | float]:
    try:
        limits = _validated_resource_limits(
            state.get("resource_limits")
            if "resource_limits" in state
            else None
        )
    except ValueError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 resource_limits 无效：{exc}",
        ) from exc
    return limits.to_dict()


def _comparison_protocol_fingerprint(
    state: dict[str, Any],
    *,
    archive_id: str,
    receptor_sha256: str,
    raw_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    box = state.get("box") if isinstance(state.get("box"), dict) else {}
    vina = _screening_vina_with_defaults(state.get("vina"))
    tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
    vina_tool = tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
    bounds = _comparison_resource_bounds(
        raw_state if isinstance(raw_state, dict) else state,
        archive_id=archive_id,
    )
    allow_legacy_energy_zero = _archive_allows_legacy_energy_range_zero(
        raw_state if isinstance(raw_state, dict) else state
    )

    required_box = (
        "center_x",
        "center_y",
        "center_z",
        "size_x",
        "size_y",
        "size_z",
    )
    required_vina = (
        "scoring",
        "exhaustiveness",
        "num_modes",
        "energy_range",
        "cpu",
        "seed",
        "max_evals",
        "min_rmsd",
        "spacing",
        "verbosity",
        "no_refine",
        "force_even_voxels",
    )
    missing = [
        f"box.{key}" for key in required_box if key not in box
    ] + [
        f"vina.{key}" for key in required_vina if key not in vina
    ]
    vina_version_value = vina_tool.get("version")
    if vina_version_value in (None, ""):
        missing.append("tools.vina.version")
    if missing:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INCOMPLETE",
            f"归档 {archive_id} 缺少协议字段：{', '.join(missing)}。",
        )
    if not isinstance(vina_version_value, str) or not vina_version_value.strip():
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 AutoDock Vina 版本必须是非空字符串。",
        )
    vina_binary_sha256 = _comparison_recorded_sha256(
        vina_tool.get("sha256"),
        archive_id=archive_id,
        label="AutoDock Vina 二进制",
    )

    normalized_box: dict[str, float] = {}
    for key in required_box:
        if isinstance(box[key], bool) or not isinstance(box[key], (int, float)):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须是数字。",
            )
        try:
            value = float(box[key])
        except (TypeError, ValueError) as exc:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 不是有效数字。",
            ) from exc
        if not math.isfinite(value):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 不是有限数字。",
            )
        if key.startswith("size_"):
            max_box_edge = float(bounds["max_box_edge_angstrom"])
            if value <= 0 or value > max_box_edge:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                    (
                        f"归档 {archive_id} 的 {key} 必须大于 0 且不超过 "
                        f"{max_box_edge:g} Å。"
                    ),
                )
        normalized_box[key] = value

    scoring = str(vina["scoring"] or "").strip().lower()
    if scoring not in {"vina", "vinardo", "ad4"}:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的评分函数不受支持：{scoring or 'missing'}。",
        )

    normalized_integers: dict[str, int] = {}
    integer_maximums = {
        "exhaustiveness": int(bounds["max_exhaustiveness"]),
        "num_modes": int(bounds["max_num_modes"]),
        "cpu": int(bounds["max_cpu"]),
    }
    for key in ("exhaustiveness", "num_modes", "cpu"):
        value = vina[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须是整数。",
            )
        maximum = integer_maximums[key]
        if value < 1 or value > maximum:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须在 1 到 {maximum} 之间。",
            )
        normalized_integers[key] = value

    energy_value = vina["energy_range"]
    if isinstance(energy_value, bool) or not isinstance(energy_value, (int, float)):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 energy_range 必须是数字。",
        )
    try:
        energy_range = float(energy_value)
    except (TypeError, ValueError) as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 energy_range 必须是数字。",
        ) from exc
    if (
        not math.isfinite(energy_range)
        or energy_range > 20
        or energy_range < 0
        or (energy_range == 0 and not allow_legacy_energy_zero)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            (
                f"归档 {archive_id} 的 energy_range 必须大于 0 且不超过 20；"
                "仅真实历史 schema v1 记录可保留 0。"
            ),
        )

    seed_value = vina["seed"]
    if seed_value is not None and (
        isinstance(seed_value, bool) or not isinstance(seed_value, int)
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 seed 必须是整数或 null。",
        )
    if seed_value is not None and not (-2_147_483_648 <= seed_value <= 2_147_483_647):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            f"归档 {archive_id} 的 seed 超出 32 位有符号整数范围。",
        )

    for key in ("max_evals", "verbosity"):
        value = vina[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须是整数。",
            )
    for key in ("min_rmsd", "spacing"):
        value = vina[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须是数字。",
            )
    for key in ("no_refine", "force_even_voxels"):
        if not isinstance(vina[key], bool):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                f"归档 {archive_id} 的 {key} 必须是布尔值。",
            )
    advanced_validation = validate_vina_params(
        {
            **vina,
            "scoring": "vina" if scoring == "ad4" else scoring,
            "energy_range": energy_range if energy_range > 0 else 3,
            "unbound_energy": None,
        }
    )
    if not advanced_validation.get("ok"):
        error = advanced_validation.get("error") or {}
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
            (
                f"归档 {archive_id} 的高级 Vina 参数无效："
                f"{error.get('message') or error.get('code') or 'unknown'}"
            ),
        )
    parsed_vina = advanced_validation["vina"]

    fingerprint: dict[str, Any] = {
        "receptor_sha256": receptor_sha256,
        "scoring_protocol": str(state.get("scoring_protocol") or "vina"),
        "scoring": scoring,
        "box": normalized_box,
        "vina_version": vina_version_value.strip(),
        "vina_binary_sha256": vina_binary_sha256,
        "resource_limits": dict(bounds),
        "exhaustiveness": normalized_integers["exhaustiveness"],
        "num_modes": normalized_integers["num_modes"],
        "energy_range": energy_range,
        "cpu": normalized_integers["cpu"],
        "seed": seed_value,
        "max_evals": int(parsed_vina["max_evals"]),
        "min_rmsd": float(parsed_vina["min_rmsd"]),
        "spacing": float(parsed_vina["spacing"]),
        "verbosity": int(parsed_vina["verbosity"]),
        "no_refine": bool(parsed_vina["no_refine"]),
        "force_even_voxels": bool(parsed_vina["force_even_voxels"]),
    }
    if scoring == "ad4":
        map_records = _archived_ad4_map_records(state)
        map_identity = []
        for logical, record, label in map_records:
            sha256 = _comparison_recorded_sha256(
                record.get("sha256"),
                archive_id=archive_id,
                label=label,
            )
            size_bytes = record.get("size_bytes")
            if (
                isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or size_bytes < 1
            ):
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
                    f"归档 {archive_id} 的 {label} 文件大小无效。",
                )
            map_identity.append(
                {
                    "file": logical,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                }
            )
        inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
        ad4_maps = (
            inputs.get("ad4_maps")
            if isinstance(inputs.get("ad4_maps"), dict)
            else {}
        )
        fingerprint["ad4_maps"] = {
            "map_set_id": str(ad4_maps.get("map_set_id") or ""),
            "prefix": str(ad4_maps.get("prefix") or ""),
            "files": map_identity,
        }
    canonical = json.dumps(
        fingerprint,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint["fingerprint_sha256"] = hashlib.sha256(canonical).hexdigest()
    return fingerprint


def _comparison_protocol_differences(
    baseline: dict[str, Any],
    comparison: dict[str, Any],
) -> list[dict[str, Any]]:
    fields = (
        "receptor_sha256",
        "scoring_protocol",
        "scoring",
        "ad4_maps",
        "box.center_x",
        "box.center_y",
        "box.center_z",
        "box.size_x",
        "box.size_y",
        "box.size_z",
        "vina_version",
        "vina_binary_sha256",
        "resource_limits.max_ligands",
        "resource_limits.max_retries",
        "resource_limits.max_cpu",
        "resource_limits.max_exhaustiveness",
        "resource_limits.max_num_modes",
        "resource_limits.max_box_edge_angstrom",
        "resource_limits.max_ligand_bytes",
        "resource_limits.max_staged_file_bytes",
        "resource_limits.max_total_input_bytes",
        "exhaustiveness",
        "num_modes",
        "energy_range",
        "cpu",
        "seed",
        "max_evals",
        "min_rmsd",
        "spacing",
        "verbosity",
        "no_refine",
        "force_even_voxels",
    )

    def field_value(value: dict[str, Any], field: str) -> Any:
        if field.startswith("box."):
            box = value.get("box") if isinstance(value.get("box"), dict) else {}
            return box.get(field.split(".", 1)[1])
        if field.startswith("resource_limits."):
            limits = (
                value.get("resource_limits")
                if isinstance(value.get("resource_limits"), dict)
                else {}
            )
            return limits.get(field.split(".", 1)[1])
        return value.get(field)

    differences: list[dict[str, Any]] = []
    for field in fields:
        baseline_value = field_value(baseline, field)
        comparison_value = field_value(comparison, field)
        if baseline_value != comparison_value:
            differences.append(
                {
                    "field": field,
                    "baseline": baseline_value,
                    "comparison": comparison_value,
                }
            )
    return differences


def _comparison_ranks(state: dict[str, Any]) -> dict[str, int]:
    ranked = sorted(
        (
            item
            for item in state.get("items") or []
            if isinstance(item, dict)
            and item.get("status") == "succeeded"
            and isinstance(item.get("best_affinity_kcal_mol"), (int, float))
            and not isinstance(item.get("best_affinity_kcal_mol"), bool)
            and math.isfinite(float(item["best_affinity_kcal_mol"]))
        ),
        key=lambda item: (
            float(item["best_affinity_kcal_mol"]),
            int(item.get("order") or 0),
            str(item.get("item_id") or ""),
        ),
    )
    return {
        str(item.get("item_id") or ""): rank
        for rank, item in enumerate(ranked, start=1)
    }


def _comparison_item_summary(
    item: dict[str, Any],
    ranks: dict[str, int],
    identity: dict[str, str],
) -> dict[str, Any]:
    item_id = str(item.get("item_id") or "")
    score = item.get("best_affinity_kcal_mol")
    valid_score = (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and math.isfinite(float(score))
    )
    return {
        "item_id": item_id,
        "order": int(item.get("order") or 0),
        "display_label": str(item.get("display_label") or ""),
        "source_file": str(item.get("source_file") or ""),
        "status": str(item.get("status") or ""),
        "best_affinity_kcal_mol": float(score) if valid_score else None,
        "rank": ranks.get(item_id),
        "identity_basis": identity["basis"],
        "pdbqt_sha256": identity["pdbqt_sha256"],
        "canonical_topology_sha256": identity[
            "canonical_topology_sha256"
        ],
        "source_topology_sha256": identity["source_topology_sha256"],
        "source_import_id": str(item.get("source_import_id") or ""),
        "source_candidate_id": str(item.get("source_candidate_id") or ""),
        "source_record_id": str(item.get("source_record_id") or ""),
    }


def _comparison_candidate_provenance(
    item: dict[str, Any],
    *,
    archive_id: str,
) -> dict[str, str]:
    item_id = str(item.get("item_id") or "")
    provenance = {
        "import_id": str(item.get("source_import_id") or ""),
        "candidate_id": str(item.get("source_candidate_id") or ""),
        "record_id": str(item.get("source_record_id") or ""),
    }
    if not any(provenance.values()):
        return {}
    if (
        SCREENING_IMPORT_ID_PATTERN.fullmatch(provenance["import_id"]) is None
        or SCREENING_RECORD_ID_PATTERN.fullmatch(provenance["record_id"]) is None
        or re.fullmatch(
            r"ligand_[0-9a-f]{12}_\d{4}(?:_\d{2,})?",
            provenance["candidate_id"],
        )
        is None
    ):
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            (
                f"归档 {archive_id} 的配体 {item_id} 缺少完整、有效的"
                " import/candidate/record 身份。"
            ),
        )
    return provenance


def _comparison_ligand_identity(
    archive_dir: Path,
    item: dict[str, Any],
    *,
    archive_id: str,
    pdbqt_sha256: str,
) -> dict[str, str]:
    """Build a fail-closed comparison identity from verified frozen evidence."""

    item_id = str(item.get("item_id") or "")
    raw_integrity = item.get("topology_integrity")
    topology_integrity = str(raw_integrity or "")
    topology_fields_present = bool(
        str(item.get("source_topology_file") or "")
        or str(item.get("source_topology_sha256") or "")
        or item.get("source_topology_size_bytes") not in (None, "", 0)
    )
    facts = (
        item.get("chemical_facts")
        if isinstance(item.get("chemical_facts"), dict)
        else {}
    )
    provenance = _comparison_candidate_provenance(
        item,
        archive_id=archive_id,
    )

    if not topology_integrity:
        if (
            topology_fields_present
            or provenance
            or facts.get("status") == "verified"
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
                (
                    f"归档 {archive_id} 的配体 {item_id} 含现代身份字段，"
                    "但缺少 topology_integrity。"
                ),
            )
        return {
            "identity_sha256": pdbqt_sha256,
            "basis": "pdbqt_sha256",
            "pdbqt_sha256": pdbqt_sha256,
            "canonical_topology_sha256": "",
            "source_topology_sha256": "",
        }

    if topology_integrity == "not_available":
        if topology_fields_present or facts.get("status") == "verified":
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
                (
                    f"归档 {archive_id} 的配体 {item_id} 将拓扑标记为"
                    "不可用，却同时记录了可用拓扑证据。"
                ),
            )
        if provenance:
            try:
                _validated_stored_preparation_evidence(
                    item,
                    require_worker=False,
                )
            except ValueError as exc:
                raise _ArchiveValidationError(
                    "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
                    (
                        f"归档 {archive_id} 的配体 {item_id} PDBQT-only "
                        f"候选身份无效：{exc}"
                    ),
                ) from exc
        return {
            "identity_sha256": pdbqt_sha256,
            "basis": "pdbqt_sha256",
            "pdbqt_sha256": pdbqt_sha256,
            "canonical_topology_sha256": "",
            "source_topology_sha256": "",
        }

    if topology_integrity != "verified":
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            (
                f"归档 {archive_id} 的配体 {item_id} topology_integrity "
                f"不受支持：{topology_integrity}。"
            ),
        )
    if not provenance:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            (
                f"归档 {archive_id} 的配体 {item_id} 已记录冻结拓扑，"
                "但缺少候选来源身份。"
            ),
        )

    topology_relative = str(item.get("source_topology_file") or "")
    topology_logical = PurePosixPath(topology_relative)
    topology_suffix = Path(topology_logical.name).suffix.lower()
    if topology_suffix not in {".sdf", ".mol"}:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            f"归档 {archive_id} 的配体 {item_id} 原始拓扑格式无效。",
        )
    topology_path = _relocate_archive_logical_file(
        archive_dir,
        topology_relative,
        expected_tail=(
            "inputs",
            "topology",
            f"{item_id}{topology_suffix}",
        ),
    )
    source_topology_sha256 = _verify_comparison_input(
        topology_path,
        {
            "sha256": item.get("source_topology_sha256"),
            "size_bytes": item.get("source_topology_size_bytes"),
        },
        archive_id=archive_id,
        label=f"配体 {item_id} 原始拓扑",
    )
    source_record_sha256 = _comparison_recorded_sha256(
        item.get("source_record_sha256"),
        archive_id=archive_id,
        label=f"配体 {item_id} 原始 record",
    )
    if source_record_sha256 != source_topology_sha256:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            (
                f"归档 {archive_id} 的配体 {item_id} 原始 record 与"
                "冻结拓扑 SHA256 不一致。"
            ),
        )
    try:
        validated_facts = _validated_screening_chemical_facts(
            item.get("chemical_facts"),
            source_topology_sha256=source_topology_sha256,
        )
        _validated_stored_preparation_evidence(
            item,
            require_worker=True,
        )
    except ValueError as exc:
        raise _ArchiveValidationError(
            "SCREENING_ARCHIVE_COMPARE_LIGAND_IDENTITY_INVALID",
            (
                f"归档 {archive_id} 的配体 {item_id} 规范拓扑证据无效："
                f"{exc}"
            ),
        ) from exc
    canonical_topology_sha256 = str(
        validated_facts["canonical_topology_sha256"]
    ).lower()
    identity_payload = json.dumps(
        {
            "schema_version": 1,
            "pdbqt_sha256": pdbqt_sha256,
            "canonical_topology_sha256": canonical_topology_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "identity_sha256": hashlib.sha256(identity_payload).hexdigest(),
        "basis": "pdbqt_sha256+canonical_topology_sha256",
        "pdbqt_sha256": pdbqt_sha256,
        "canonical_topology_sha256": canonical_topology_sha256,
        "source_topology_sha256": source_topology_sha256,
    }


def _load_comparison_archive(
    root: Path,
    archive_id: str,
) -> dict[str, Any]:
    archive_dir, manifest, state = _validate_archive_bundle(root, archive_id)
    resource_integrity = _validate_archived_resource_state(
        archive_dir,
        state,
        archive_id=archive_id,
        error_code="SCREENING_ARCHIVE_COMPARE_PROTOCOL_INVALID",
        verify_files=False,
    )
    labels = _archive_item_labels(root, state, manifest)
    normalized = _normalized_archived_state(state, labels)
    files = _archive_files(root, archive_dir, normalized)
    attempt_integrity = _archive_attempt_integrity(
        archive_dir,
        state,
        archive_id=archive_id,
    )

    inputs = normalized.get("inputs") if isinstance(normalized.get("inputs"), dict) else {}
    receptor_record = (
        inputs.get("receptor")
        if isinstance(inputs.get("receptor"), dict)
        else {}
    )
    receptor_path = (root / str(files["receptor"])).resolve(strict=True)
    receptor_sha256 = _verify_comparison_input(
        receptor_path,
        receptor_record,
        archive_id=archive_id,
        label="受体输入",
    )

    items_by_identity: dict[str, list[dict[str, Any]]] = {}
    ranks = _comparison_ranks(normalized)
    for item in normalized.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        item_file = files.get("items", {}).get(item_id, {})
        ligand_relative = (
            item_file.get("ligand_input")
            if isinstance(item_file, dict)
            else ""
        )
        ligand_path = (root / str(ligand_relative or "")).resolve(strict=True)
        ligand_sha256 = _verify_comparison_input(
            ligand_path,
            item,
            archive_id=archive_id,
            label=f"配体 {item_id} 输入",
        )
        identity = _comparison_ligand_identity(
            archive_dir,
            item,
            archive_id=archive_id,
            pdbqt_sha256=ligand_sha256,
        )
        items_by_identity.setdefault(identity["identity_sha256"], []).append(
            _comparison_item_summary(item, ranks, identity)
        )

    protocol_fingerprint = _comparison_protocol_fingerprint(
        normalized,
        archive_id=archive_id,
        receptor_sha256=receptor_sha256,
        raw_state=state,
    )
    summary = _archive_summary(
        archive_id,
        archive_dir,
        manifest,
        normalized,
        attempt_integrity,
    )
    summary["input_integrity"] = "verified"
    summary["resource_integrity"] = resource_integrity["status"]
    summary["protocol_fingerprint"] = protocol_fingerprint
    return {
        "summary": summary,
        "state": normalized,
        "items_by_identity": items_by_identity,
        "protocol_fingerprint": protocol_fingerprint,
        "attempt_integrity": attempt_integrity,
        "resource_integrity": resource_integrity,
    }


def compare_screening_archives(
    project_dir: str,
    archive_ids: list[str],
) -> dict[str, Any]:
    """Compare exactly two verified archives without writing project data."""

    try:
        if isinstance(archive_ids, (str, bytes)) or not isinstance(
            archive_ids,
            (list, tuple),
        ):
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_SELECTION_INVALID",
                "归档比较必须提供两个归档编号。",
            )
        selected = [str(value or "") for value in archive_ids]
        if len(selected) != 2:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_SELECTION_INVALID",
                "归档比较必须且只能提供两个归档编号。",
            )
        if selected[0] == selected[1]:
            raise _ArchiveValidationError(
                "SCREENING_ARCHIVE_COMPARE_SELECTION_INVALID",
                "基线归档和对照归档必须是两条不同记录。",
            )

        root = _project_root(project_dir)
        baseline = _load_comparison_archive(root, selected[0])
        comparison = _load_comparison_archive(root, selected[1])
        differences = _comparison_protocol_differences(
            baseline["protocol_fingerprint"],
            comparison["protocol_fingerprint"],
        )
        direct_score_comparison = not differences

        baseline_groups = baseline["items_by_identity"]
        comparison_groups = comparison["items_by_identity"]
        identities = sorted(
            set(baseline_groups) | set(comparison_groups),
            key=lambda sha256: (
                min(
                    (
                        int(item.get("order") or 0)
                        for item in baseline_groups.get(sha256, [])
                    ),
                    default=1_000_000_000,
                ),
                min(
                    (
                        int(item.get("order") or 0)
                        for item in comparison_groups.get(sha256, [])
                    ),
                    default=1_000_000_000,
                ),
                sha256,
            ),
        )
        rows: list[dict[str, Any]] = []
        counts = {
            "baseline_total": sum(len(items) for items in baseline_groups.values()),
            "comparison_total": sum(len(items) for items in comparison_groups.values()),
            "matched": 0,
            "baseline_only": 0,
            "comparison_only": 0,
            "ambiguous": 0,
            "delta_rows": 0,
            "rows": 0,
        }
        for identity_sha256 in identities:
            baseline_items = baseline_groups.get(identity_sha256, [])
            comparison_items = comparison_groups.get(identity_sha256, [])
            if len(baseline_items) > 1 or len(comparison_items) > 1:
                match_status = "ambiguous"
            elif baseline_items and comparison_items:
                match_status = "matched"
            elif baseline_items:
                match_status = "baseline_only"
            else:
                match_status = "comparison_only"
            counts[match_status] += 1
            row: dict[str, Any] = {
                "match_status": match_status,
                "identity_sha256": identity_sha256,
                "baseline_items": baseline_items,
                "comparison_items": comparison_items,
            }
            if (
                direct_score_comparison
                and match_status == "matched"
                and baseline_items[0].get("rank") is not None
                and comparison_items[0].get("rank") is not None
                and baseline_items[0].get("best_affinity_kcal_mol") is not None
                and comparison_items[0].get("best_affinity_kcal_mol") is not None
            ):
                row["score_delta_kcal_mol"] = (
                    float(comparison_items[0]["best_affinity_kcal_mol"])
                    - float(baseline_items[0]["best_affinity_kcal_mol"])
                )
                row["rank_delta"] = (
                    int(comparison_items[0]["rank"])
                    - int(baseline_items[0]["rank"])
                )
                counts["delta_rows"] += 1
            rows.append(row)
        counts["rows"] = len(rows)

        comparability = {
            "status": "comparable" if direct_score_comparison else "protocol_mismatch",
            "direct_score_comparison": direct_score_comparison,
            "differences": differences,
        }
        return {
            "ok": True,
            "project_dir": str(root),
            "baseline_archive": baseline["summary"],
            "comparison_archive": comparison["summary"],
            "direct_score_comparison": direct_score_comparison,
            "comparability": comparability,
            "counts": counts,
            "rows": rows,
            "message": (
                "两个归档的协议指纹一致，可直接比较共有配体的评分与排名。"
                if direct_score_comparison
                else "两个归档的协议指纹不同；仅列出配体对应关系，不计算评分或排名差值。"
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - public read-only boundary.
        code = (
            exc.code
            if isinstance(exc, _ArchiveValidationError)
            else "SCREENING_ARCHIVE_COMPARE_ERROR"
        )
        return _error(
            code,
            "比较批量筛选历史归档失败。",
            str(exc),
            "请选择两个不同且完整的归档；旧归档必须具有可核对的输入和工具 SHA256。",
        )


def archive_screening(project_dir: str) -> dict[str, Any]:
    """Archive a terminal job and clear only its active workspace."""

    temporary_archive: Path | None = None
    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state.get("status") not in TERMINAL_SCREENING_STATUSES:
            return _error(
                "SCREENING_NOT_TERMINAL",
                "只有已完成或已取消的批量筛选任务可以归档。",
                f"status={state.get('status')}",
                "请先完成任务，或请求取消并等待当前配体结束。",
            )
        report = export_screening_markdown_report(str(root))
        if not report.get("ok") or not isinstance(report.get("screening"), dict):
            detail = (
                report.get("error", {}).get("raw_error")
                or report.get("error", {}).get("message")
                or "未生成批量筛选实验记录。"
            )
            raise ValueError(f"归档前生成实验记录失败：{detail}")
        state = report["screening"]
        item_labels = _archive_item_labels(root, state)

        archive_root = root / ARCHIVE_RELATIVE_PATH
        archive_root.mkdir(parents=True, exist_ok=True)
        compact_time = re.sub(r"[^0-9]", "", _now_iso())[:14]
        base_name = f"{state['screening_id']}_{compact_time}"
        final_archive = archive_root / base_name
        suffix = 1
        while final_archive.exists():
            final_archive = archive_root / f"{base_name}_{suffix:02d}"
            suffix += 1
        temporary_archive = archive_root / f".{final_archive.name}.tmp"
        if temporary_archive.exists():
            raise ValueError(f"临时归档目录已存在，已拒绝覆盖：{temporary_archive}")
        temporary_archive.mkdir()

        shutil.copy2(_state_path(root), temporary_archive / "screening.json")
        screening_root = root / SCREENING_ROOT
        for name in ACTIVE_JOB_NAMES:
            source = screening_root / name
            if source.is_dir():
                shutil.copytree(source, temporary_archive / name)
        atomic_write_json(
            temporary_archive / "archive_manifest.json",
            {
                "schema_version": 1,
                "screening_id": state["screening_id"],
                "status": state["status"],
                "archived_at": _now_iso(),
                "state_sha256": _sha256(temporary_archive / "screening.json"),
                "item_labels": item_labels,
            },
        )
        os.replace(temporary_archive, final_archive)
        temporary_archive = None

        for name in ACTIVE_JOB_NAMES:
            _safe_rmtree(screening_root / name, expected_parent=screening_root)
        _state_path(root).unlink()
        relative_archive = final_archive.relative_to(root).as_posix()
        return {
            "ok": True,
            "project_dir": str(root),
            "archive": relative_archive,
            "archive_id": final_archive.name,
            "screening": state,
            "message": "批量筛选任务已归档，可以创建下一任务。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        if temporary_archive is not None and temporary_archive.is_dir():
            try:
                _safe_rmtree(temporary_archive, expected_parent=temporary_archive.parent)
            except Exception:
                pass
        return _error(
            "SCREENING_ARCHIVE_ERROR",
            "归档批量筛选任务失败。",
            str(exc),
            "原任务不会被新任务覆盖；请检查目录权限后重试。",
        )


def request_screening_cancel(project_dir: str) -> dict[str, Any]:
    """Request cancellation; the active ligand is allowed to finish safely."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] in {"completed", "completed_with_failures", "canceled"}:
            return {
                "ok": True,
                "screening": state,
                "message": "任务已经处于终止状态，无需取消。",
                "error": None,
            }
        state["cancel_requested"] = True
        if state["status"] in {"ready", "interrupted"}:
            state["status"] = "canceled"
            state["finished_at"] = _now_iso()
            message = "批量筛选尚未运行，已立即取消。"
        else:
            state["status"] = "cancel_requested"
            message = "已请求取消；当前配体完成后停止队列。"
        _write_state(root, state)
        return {
            "ok": True,
            "screening": state,
            "message": message,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_CANCEL_ERROR", "请求取消批量筛选失败。", str(exc))


def resume_screening(project_dir: str) -> dict[str, Any]:
    """Recover canceled or interrupted items into their original stable order."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] in {"completed", "completed_with_failures"}:
            return _error("SCREENING_ALREADY_FINISHED", "批量筛选已经完成，不能恢复。")
        limits = _validated_resource_limits(
            state.get("resource_limits")
            if "resource_limits" in state
            else None
        )
        _verify_frozen_resource_limits_hash(state, limits)
        resource_state = _validate_screening_resource_state(state, limits)
        state["resource_limits"] = limits.to_dict()
        state["max_retries"] = resource_state["max_retries"]
        state["top_n"] = resource_state["top_n"]
        for item in state["items"]:
            if item.get("status") != "running":
                continue
            running_attempt = next(
                (
                    attempt
                    for attempt in reversed(item.get("attempts") or [])
                    if attempt.get("status") == "running"
                ),
                None,
            )
            try:
                pid = int((running_attempt or {}).get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0 and vina_adapter.is_process_running(pid):
                return _error(
                    "SCREENING_PROCESS_ACTIVE",
                    "检测到原批量筛选 Vina 进程仍在运行，已拒绝重复启动。",
                    f"item={item.get('item_id')}, pid={pid}",
                    "请等待当前配体结束后再恢复；不要同时启动第二个筛选进程。",
                )
        max_attempts = state["max_retries"] + 1
        recoverable: list[dict[str, Any]] = []
        for item in state["items"]:
            if item["status"] == "running":
                item["status"] = "interrupted"
                item["last_error"] = "上次运行在状态持久化前中断。"
            if item["status"] in {"pending", "interrupted"}:
                if int(item.get("attempt_count") or 0) < max_attempts:
                    item["status"] = "pending"
                    recoverable.append(item)
                else:
                    item["status"] = "failed"
        state["queue"] = [item["item_id"] for item in sorted(recoverable, key=lambda row: int(row["order"]))]
        state["cancel_requested"] = False
        state["status"] = "ready"
        state["finished_at"] = None
        _write_state(root, state)
        return {
            "ok": True,
            "screening": state,
            "message": f"批量筛选已恢复，队列中有 {len(state['queue'])} 个配体。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_RESUME_ERROR", "恢复批量筛选失败。", str(exc))


def _config_text(state: dict[str, Any]) -> str:
    box = state["box"]
    vina = _screening_vina_with_defaults(state["vina"])
    is_ad4 = str(state.get("scoring_protocol") or "vina") == "ad4_maps"
    if is_ad4:
        lines = ["ligand = ligand.pdbqt", "scoring = ad4", ""]
    else:
        lines = [
            "receptor = receptor.pdbqt",
            "ligand = ligand.pdbqt",
            f"scoring = {vina['scoring']}",
            "",
            f"center_x = {box['center_x']:g}",
            f"center_y = {box['center_y']:g}",
            f"center_z = {box['center_z']:g}",
            f"size_x = {box['size_x']:g}",
            f"size_y = {box['size_y']:g}",
            f"size_z = {box['size_z']:g}",
            "",
        ]
    lines.extend([
        f"exhaustiveness = {vina['exhaustiveness']}",
        f"max_evals = {vina['max_evals']}",
        f"num_modes = {vina['num_modes']}",
        f"min_rmsd = {float(vina['min_rmsd']):g}",
        f"energy_range = {vina['energy_range']:g}",
        f"cpu = {vina['cpu']}",
    ])
    if not is_ad4:
        lines.append(f"spacing = {float(vina['spacing']):g}")
        if vina["no_refine"]:
            lines.append("no_refine = true")
        if vina["force_even_voxels"]:
            lines.append("force_even_voxels = true")
    lines.append(f"verbosity = {vina['verbosity']}")
    if vina.get("seed") is not None:
        lines.append(f"seed = {vina['seed']}")
    return "\n".join(lines) + "\n"


def _default_runner(**kwargs: Any) -> dict[str, Any]:
    result = vina_adapter.run_managed(
        kwargs["command"],
        kwargs["cwd"],
        kwargs["stdout_path"],
        kwargs["stderr_path"],
        kwargs["log_path"],
        on_started=kwargs.get("on_started"),
    )
    return {"pid": result.pid, "exit_code": result.exit_code, "error": result.error}


def _runner_result(value: Any) -> tuple[int | None, str, int | None]:
    if isinstance(value, int):
        return value, "", None
    if isinstance(value, dict):
        exit_code = value.get("exit_code")
        return (
            int(exit_code) if exit_code is not None else None,
            str(value.get("error") or ""),
            int(value["pid"]) if value.get("pid") is not None else None,
        )
    exit_code = getattr(value, "exit_code", None)
    return (
        int(exit_code) if exit_code is not None else None,
        str(getattr(value, "error", "") or ""),
        int(getattr(value, "pid")) if getattr(value, "pid", None) is not None else None,
    )


def _best_affinity(log_path: Path) -> float | None:
    if not log_path.is_file():
        return None
    scores: list[float] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SCORE_ROW.match(line)
        if match:
            scores.append(float(match.group(2)))
    return min(scores) if scores else None


def _refresh_cancel(root: Path, state: dict[str, Any]) -> None:
    try:
        current = _read_state(root)
    except Exception:
        return
    if current.get("cancel_requested"):
        state["cancel_requested"] = True


def _attempt_item(root: Path, state: dict[str, Any], item: dict[str, Any], runner: Runner) -> bool:
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    receptor_record = (
        inputs.get("receptor") if isinstance(inputs.get("receptor"), dict) else {}
    )
    try:
        receptor_path, _ = _project_file(
            root,
            str(receptor_record.get("file") or ""),
            label="冻结受体",
        )
    except Exception as exc:
        raise _ScreeningIntegrityError(
            "SCREENING_RECEPTOR_SNAPSHOT_CHANGED",
            f"无法读取冻结受体：{exc}",
        ) from exc
    try:
        ligand_path, _ = _project_file(
            root,
            str(item.get("ligand_file") or ""),
            label=f"冻结配体 {item.get('item_id') or ''}",
        )
    except Exception as exc:
        raise _ScreeningIntegrityError(
            "SCREENING_LIGAND_SNAPSHOT_CHANGED",
            f"无法读取冻结配体 {item.get('item_id') or ''}：{exc}",
        ) from exc
    receptor_bytes, receptor_sha256 = _verified_frozen_file_bytes(
        receptor_path,
        receptor_record,
        code="SCREENING_RECEPTOR_SNAPSHOT_CHANGED",
        label="冻结受体",
    )
    ligand_bytes, ligand_sha256 = _verified_frozen_file_bytes(
        ligand_path,
        item,
        code="SCREENING_LIGAND_SNAPSHOT_CHANGED",
        label=f"冻结配体 {item.get('item_id') or ''}",
    )
    topology_record = _verified_frozen_item_topology(root, item)
    topology_bytes = b""
    topology_snapshot_name = ""
    if topology_record is not None:
        topology_path = root / Path(
            *PurePosixPath(str(topology_record["file"])).parts
        )
        topology_bytes = topology_path.read_bytes()
        topology_snapshot_name = (
            f"ligand_source{topology_path.suffix.lower()}"
        )
    vina_path, vina_sha256, vina_size_bytes = _verified_attempt_vina(state)
    config_bytes = _config_text(state).encode("utf-8")
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()

    item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
    attempt_number = item["attempt_count"]
    attempt_relative = Path("screening", "attempts", item["item_id"], f"attempt_{attempt_number:03d}")
    attempt_dir = root / attempt_relative
    attempt_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_bytes(attempt_dir / "receptor.pdbqt", receptor_bytes)
    atomic_write_bytes(attempt_dir / "ligand.pdbqt", ligand_bytes)
    atomic_write_bytes(attempt_dir / "config.txt", config_bytes)
    if topology_snapshot_name:
        atomic_write_bytes(
            attempt_dir / topology_snapshot_name,
            topology_bytes,
        )

    output_path = attempt_dir / "out.pdbqt"
    stdout_path = attempt_dir / "stdout.txt"
    stderr_path = attempt_dir / "stderr.txt"
    log_path = attempt_dir / "log.txt"
    command = [str(vina_path), "--config", "config.txt"]
    if str(state.get("scoring_protocol") or "vina") == "ad4_maps":
        ad4_maps = _verify_screening_ad4_maps(root, state)
        command.extend(
            [
                "--maps",
                str(ad4_maps.get("attempt_prefix") or ""),
                "--scoring",
                "ad4",
            ]
        )
    command.extend(["--out", "out.pdbqt"])
    started_at = _now_iso()
    tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
    vina_tool = tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
    attempt_record: dict[str, Any] = {
        "attempt": attempt_number,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "directory": attempt_relative.as_posix(),
        "command": command,
        "input_snapshots": {
            "receptor": {
                "source_file": str(receptor_record.get("file") or ""),
                "file": (attempt_relative / "receptor.pdbqt").as_posix(),
                "sha256": receptor_sha256,
                "size_bytes": len(receptor_bytes),
            },
            "ligand": {
                "source_file": str(item.get("ligand_file") or ""),
                "file": (attempt_relative / "ligand.pdbqt").as_posix(),
                "sha256": ligand_sha256,
                "size_bytes": len(ligand_bytes),
                "display_label": str(item.get("display_label") or ""),
                "source_original_file": str(
                    item.get("source_original_file") or ""
                ),
                "source_format": str(item.get("source_format") or ""),
                "source_record_index": int(
                    item.get("source_record_index") or 1
                ),
                "source_record_name": str(
                    item.get("source_record_name") or ""
                ),
                "source_record_sha256": str(
                    item.get("source_record_sha256") or ""
                ),
                "source_import_id": str(
                    item.get("source_import_id") or ""
                ),
                "source_candidate_id": str(
                    item.get("source_candidate_id") or ""
                ),
                "source_record_id": str(
                    item.get("source_record_id") or ""
                ),
                "source_topology_file": str(
                    item.get("source_topology_file") or ""
                ),
                "source_topology_sha256": str(
                    item.get("source_topology_sha256") or ""
                ),
                "source_topology_size_bytes": int(
                    item.get("source_topology_size_bytes") or 0
                ),
                "topology_integrity": str(
                    item.get("topology_integrity") or "not_available"
                ),
                "chemical_facts": (
                    dict(item.get("chemical_facts") or {})
                    if isinstance(item.get("chemical_facts"), dict)
                    else {}
                ),
                "preparation_evidence": (
                    dict(item.get("preparation_evidence") or {})
                    if isinstance(item.get("preparation_evidence"), dict)
                    else {}
                ),
                "source_records": [
                    dict(value)
                    for value in (
                        item.get("source_records")
                        if isinstance(item.get("source_records"), list)
                        else []
                    )
                    if isinstance(value, dict)
                ],
            },
            **(
                {
                    "source_topology": {
                        "source_file": str(
                            item.get("source_topology_file") or ""
                        ),
                        "file": (
                            attempt_relative / topology_snapshot_name
                        ).as_posix(),
                        "snapshot_name": topology_snapshot_name,
                        "sha256": str(topology_record["sha256"]),
                        "size_bytes": len(topology_bytes),
                        "chemical_facts": dict(
                            topology_record["chemical_facts"]
                        ),
                    }
                }
                if topology_record is not None
                else {}
            ),
        },
        "config_snapshot": {
            "file": (attempt_relative / "config.txt").as_posix(),
            "sha256": config_sha256,
            "size_bytes": len(config_bytes),
        },
        "vina_snapshot": {
            "path": str(vina_path),
            "version": str(vina_tool.get("version") or ""),
            "sha256": vina_sha256,
            "size_bytes": vina_size_bytes,
        },
        "integrity": {
            "status": "pending",
            "checked_at": None,
        },
        "pid": None,
        "exit_code": None,
        "best_affinity_kcal_mol": None,
        "output_file": "",
        "output_sha256": "",
        "output_size_bytes": 0,
        "error": "",
    }
    item["status"] = "running"
    item.setdefault("attempts", []).append(attempt_record)
    atomic_write_json(attempt_dir / "attempt.json", attempt_record)
    _write_state(root, state)

    def on_started(pid: int) -> None:
        attempt_record["pid"] = int(pid)
        atomic_write_json(attempt_dir / "attempt.json", attempt_record)
        _refresh_cancel(root, state)
        if state.get("cancel_requested"):
            state["status"] = "cancel_requested"
        _write_state(root, state)

    try:
        returned = runner(
            command=command,
            cwd=attempt_dir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            log_path=log_path,
            output_path=output_path,
            item=dict(item),
            attempt=attempt_number,
            on_started=on_started,
        )
        exit_code, run_error, pid = _runner_result(returned)
    except Exception as exc:  # noqa: BLE001 - external runner failures become item failures.
        exit_code, run_error, pid = None, str(exc), None

    for path in (stdout_path, stderr_path, log_path):
        if not path.exists():
            atomic_write_text(path, "")
    try:
        attempt_record["integrity"] = _verify_attempt_runtime_evidence(
            attempt_dir,
            attempt_record,
        )
    except _ScreeningIntegrityError as exc:
        finished_at = _now_iso()
        attempt_record.update(
            {
                "status": "interrupted",
                "finished_at": finished_at,
                "pid": pid if pid is not None else attempt_record.get("pid"),
                "exit_code": exit_code,
                "error": str(exc),
                "integrity": {
                    "status": "failed",
                    "checked_at": finished_at,
                    "code": exc.code,
                    "message": str(exc),
                },
            }
        )
        item["status"] = "interrupted"
        item["last_error"] = str(exc)
        atomic_write_json(attempt_dir / "attempt.json", attempt_record)
        _write_state(root, state)
        raise
    affinity = _best_affinity(log_path)
    success = exit_code == 0 and output_path.is_file() and output_path.stat().st_size > 0 and affinity is not None
    if not success and not run_error:
        if exit_code not in (0, None):
            run_error = f"AutoDock Vina 退出码为 {exit_code}。"
        elif not output_path.is_file() or output_path.stat().st_size == 0:
            run_error = "Vina 未生成非空 out.pdbqt。"
        elif affinity is None:
            run_error = "Vina 日志中没有可解析的 score。"
        else:
            run_error = "Vina 运行失败。"

    finished_at = _now_iso()
    output_relative = (
        (attempt_relative / "out.pdbqt").as_posix() if output_path.is_file() else ""
    )
    output_sha256 = _sha256(output_path) if output_path.is_file() else ""
    output_size_bytes = output_path.stat().st_size if output_path.is_file() else 0
    attempt_record.update(
        {
            "status": "succeeded" if success else "failed",
            "finished_at": finished_at,
            "pid": pid if pid is not None else attempt_record.get("pid"),
            "exit_code": exit_code,
            "best_affinity_kcal_mol": affinity,
            "output_file": output_relative,
            "output_sha256": output_sha256,
            "output_size_bytes": output_size_bytes,
            "error": run_error,
        },
    )
    if success:
        item["status"] = "succeeded"
        item["best_affinity_kcal_mol"] = affinity
        item["best_output_file"] = attempt_record["output_file"]
        item["best_output_sha256"] = output_sha256
        item["best_output_size_bytes"] = output_size_bytes
        item["last_error"] = ""
    else:
        item["status"] = "failed"
        item["last_error"] = run_error
    atomic_write_json(attempt_dir / "attempt.json", attempt_record)
    _refresh_cancel(root, state)
    return success


def _screening_result_topology_validator_script_text() -> str:
    return r'''from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from rdkit import Chem, rdBase


def graph_sha256(molecule):
    heavy_graph = Chem.RemoveHs(Chem.Mol(molecule))
    smiles = Chem.MolToSmiles(
        heavy_graph,
        canonical=True,
        isomericSmiles=True,
        allBondsExplicit=True,
    )
    if not smiles:
        raise RuntimeError("RDKit 未能生成规范重原子图身份。")
    return hashlib.sha256(smiles.encode("utf-8")).hexdigest()


def source_molecule(path):
    payload = path.read_bytes()
    if path.suffix.lower() == ".sdf":
        molecules = [
            molecule
            for molecule in Chem.ForwardSDMolSupplier(
                io.BytesIO(payload),
                sanitize=True,
                removeHs=False,
            )
            if molecule is not None
        ]
        if len(molecules) != 1:
            raise RuntimeError("冻结原始 SDF 必须且只能包含一条可解析记录。")
        return molecules[0]
    if path.suffix.lower() == ".mol":
        molecule = Chem.MolFromMolBlock(
            payload.decode("utf-8", errors="replace"),
            sanitize=True,
            removeHs=False,
        )
        if molecule is None:
            raise RuntimeError("RDKit 无法解析冻结原始 MOL。")
        return molecule
    raise RuntimeError("冻结原始拓扑必须是 SDF 或 MOL。")


def main():
    if len(sys.argv) != 5:
        print("需要 source、exported SDF、manifest 与预期图身份。", file=sys.stderr)
        return 2
    source_path = Path(sys.argv[1])
    exported_path = Path(sys.argv[2])
    manifest_path = Path(sys.argv[3])
    expected = sys.argv[4].lower()
    source_graph = graph_sha256(source_molecule(source_path))
    molecules = list(
        Chem.ForwardSDMolSupplier(
            io.BytesIO(exported_path.read_bytes()),
            sanitize=True,
            removeHs=False,
        )
    )
    if not molecules or any(molecule is None for molecule in molecules):
        raise RuntimeError("Meeko 导出的 SDF 包含空记录或无法解析的构象。")
    output_graphs = [graph_sha256(molecule) for molecule in molecules]
    manifest = {
        "schema_version": 1,
        "status": "verified",
        "rdkit_version": str(rdBase.rdkitVersion),
        "source_topology_sha256": hashlib.sha256(
            source_path.read_bytes()
        ).hexdigest(),
        "source_graph_sha256": source_graph,
        "expected_graph_sha256": expected,
        "output_sdf_sha256": hashlib.sha256(
            exported_path.read_bytes()
        ).hexdigest(),
        "pose_count": len(molecules),
        "pose_graph_sha256": output_graphs,
        "all_poses_match": (
            source_graph == expected
            and all(value == expected for value in output_graphs)
        ),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0 if manifest["all_poses_match"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _validate_screening_result_topology(
    root: Path,
    python_path: str,
    source_topology: Path,
    exported_sdf: Path,
    expected_graph_sha256: str,
    *,
    record_dir: Path,
) -> dict[str, Any]:
    if SHA256_PATTERN.fullmatch(expected_graph_sha256) is None:
        raise ValueError("结果 SDF 拓扑验证缺少有效的预期图身份。")
    record_dir.mkdir(parents=True, exist_ok=False)
    script = (
        root
        / STAGING_RELATIVE_PATH
        / "validate_screening_result_topology_rdkit.py"
    )
    script_text = _screening_result_topology_validator_script_text()
    if not script.is_file() or script.read_text(encoding="utf-8") != script_text:
        atomic_write_text(script, script_text)
    manifest_path = record_dir / "validation_manifest.json"
    command = [
        python_path,
        "-I",
        "-B",
        str(script),
        str(source_topology),
        str(exported_sdf),
        str(manifest_path),
        expected_graph_sha256,
    ]
    completed = subprocess.run(
        command,
        cwd=record_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    atomic_write_text(record_dir / "stdout.txt", completed.stdout)
    atomic_write_text(record_dir / "stderr.txt", completed.stderr)
    atomic_write_json(
        record_dir / "command_result.json",
        {
            "schema_version": (
                SCREENING_RESULT_TOPOLOGY_VALIDATOR_SCHEMA_VERSION
            ),
            "created_at": _now_iso(),
            "command": command,
            "returncode": completed.returncode,
            "validator_script_sha256": hashlib.sha256(
                script_text.encode("utf-8")
            ).hexdigest(),
            "source_topology_sha256": _sha256(source_topology),
            "exported_sdf_sha256": _sha256(exported_sdf),
            "expected_graph_sha256": expected_graph_sha256,
        },
    )
    if completed.returncode != 0 or not manifest_path.is_file():
        detail = "\n".join(
            value
            for value in (completed.stderr.strip(), completed.stdout.strip())
            if value
        )
        raise ValueError(
            "结果 SDF 与冻结原始拓扑不一致："
            f"{detail or f'validator exit={completed.returncode}'}"
        )
    if manifest_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("结果 SDF 拓扑验证 manifest 超过大小上限。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version")
        != SCREENING_RESULT_TOPOLOGY_VALIDATOR_SCHEMA_VERSION
        or manifest.get("status") != "verified"
        or manifest.get("all_poses_match") is not True
        or str(manifest.get("expected_graph_sha256") or "").lower()
        != expected_graph_sha256
        or str(manifest.get("source_topology_sha256") or "").lower()
        != _sha256(source_topology).lower()
        or str(manifest.get("output_sdf_sha256") or "").lower()
        != _sha256(exported_sdf).lower()
        or isinstance(manifest.get("pose_count"), bool)
        or not isinstance(manifest.get("pose_count"), int)
        or int(manifest["pose_count"]) <= 0
    ):
        raise ValueError("结果 SDF 拓扑验证 manifest 内容无效。")
    return manifest


def _annotated_screening_sdf_records(
    payload: bytes,
    annotations: dict[str, Any],
) -> tuple[bytes, int]:
    records = _split_sdf_record_bytes(payload)
    if not records:
        raise ValueError("Meeko 导出的 SDF 没有分子记录。")
    output: list[bytes] = []
    for pose_index, record in enumerate(records, start=1):
        marker = record.rfind(b"$$$$")
        if marker < 0:
            raise ValueError("Meeko 导出的 SDF 记录缺少 $$$$ 分隔符。")
        # SD properties are separated by one blank line.  Meeko already
        # writes properties of its own, so preserve a complete separator
        # before appending DockStart metadata instead of merging our first
        # header into the preceding value.
        prefix = record[:marker].rstrip(b"\r\n") + b"\n\n"
        properties = {
            **annotations,
            "DOCKSTART_POSE_INDEX": pose_index,
        }
        property_bytes = bytearray()
        for key, raw_value in properties.items():
            value = (
                ""
                if raw_value is None
                else " ".join(str(raw_value).split())
            )
            property_bytes.extend(
                f">  <{key}>\n{value}\n\n".encode("utf-8")
            )
        output.append(prefix + bytes(property_bytes) + b"$$$$\n")
    return b"".join(output), len(records)


def _verified_screening_best_output(
    root: Path,
    item: dict[str, Any],
) -> Path:
    item_id = str(item.get("item_id") or "")
    relative = str(item.get("best_output_file") or "")
    logical = PurePosixPath(relative)
    if (
        item.get("status") != "succeeded"
        or logical.is_absolute()
        or ".." in logical.parts
        or "\\" in relative
        or len(logical.parts) != 5
        or tuple(logical.parts[:3])
        != ("screening", "attempts", item_id)
        or not re.fullmatch(r"attempt_\d{3,}", logical.parts[3])
        or logical.parts[4] != "out.pdbqt"
    ):
        raise ValueError(f"配体 {item_id} 的最佳输出路径无效。")
    path = root / Path(*logical.parts)
    expected_sha256 = str(item.get("best_output_sha256") or "").lower()
    expected_size = item.get("best_output_size_bytes")
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or not path.is_file()
        or _path_is_link_or_reparse(path)
        or path.stat().st_size != expected_size
        or _sha256(path).lower() != expected_sha256
    ):
        raise ValueError(f"配体 {item_id} 的最佳输出完整性校验失败。")
    return path


def _screening_sdf_artifacts(
    root: Path,
    result_root: Path,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(
        (value for value in result_root.rglob("*") if value.is_file()),
        key=lambda value: value.relative_to(root).as_posix(),
    ):
        if _path_is_link_or_reparse(path):
            raise ValueError("结果 SDF 审计目录包含链接或重解析点。")
        size = path.stat().st_size
        total_bytes += size
        if (
            len(artifacts) >= 10_000
            or size > MAX_SCREENING_EXPORT_MEMBER_BYTES
            or total_bytes > MAX_SCREENING_EXPORT_TOTAL_BYTES
        ):
            raise ValueError("结果 SDF 审计文件超过资源上限。")
        artifacts.append(
            {
                "file": path.relative_to(root).as_posix(),
                "sha256": _sha256(path),
                "size_bytes": size,
            }
        )
    return artifacts


def _screening_result_sdf_toolchain_snapshot(
    python_tool: dict[str, Any],
    rdkit_tool: dict[str, Any],
    meeko_tool: dict[str, Any],
) -> dict[str, Any]:
    python_path = Path(str(python_tool.get("path") or "")).expanduser().resolve(
        strict=True
    )
    if (
        not python_path.is_file()
        or _path_is_link_or_reparse(python_path)
        or python_path.stat().st_size <= 0
    ):
        raise ValueError("结果 SDF 工具链的 Python 可执行文件无效。")
    versions = {
        "python": str(python_tool.get("version") or "").strip(),
        "rdkit": str(rdkit_tool.get("version") or "").strip(),
        "meeko": str(meeko_tool.get("version") or "").strip(),
    }
    if any(not value for value in versions.values()):
        raise ValueError("结果 SDF 工具链缺少 Python、RDKit 或 Meeko 版本。")
    snapshot: dict[str, Any] = {
        "profile": SCREENING_RESULT_SDF_TOOLCHAIN_PROFILE,
        "python": {
            "path": str(python_path),
            "source": str(python_tool.get("source") or "unknown"),
            "version": versions["python"],
            "sha256": _sha256(python_path),
            "size_bytes": python_path.stat().st_size,
        },
        "rdkit": {
            "version": versions["rdkit"],
            "source": str(
                rdkit_tool.get("source")
                or rdkit_tool.get("python_source")
                or "unknown"
            ),
        },
        "meeko": {
            "version": versions["meeko"],
            "source": str(
                meeko_tool.get("source")
                or meeko_tool.get("python_source")
                or "unknown"
            ),
        },
    }
    snapshot["snapshot_sha256"] = hashlib.sha256(
        _canonical_json_bytes(snapshot)
    ).hexdigest()
    return snapshot


def _verify_screening_result_sdf_python(
    snapshot: dict[str, Any],
) -> str:
    python_record = (
        snapshot.get("python")
        if isinstance(snapshot.get("python"), dict)
        else {}
    )
    path = Path(str(python_record.get("path") or "")).expanduser().resolve(
        strict=True
    )
    expected_sha256 = str(python_record.get("sha256") or "").lower()
    expected_size = python_record.get("size_bytes")
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or not path.is_file()
        or _path_is_link_or_reparse(path)
        or path.stat().st_size != expected_size
        or _sha256(path).lower() != expected_sha256
    ):
        raise ValueError("结果 SDF 工具链的 Python 在导出期间发生变化。")
    return str(path)


def _verified_screening_result_artifact(
    root: Path,
    record: dict[str, Any],
    *,
    label: str,
    expected_relative: str | None = None,
) -> tuple[Path, str]:
    relative = str(record.get("file") or "")
    logical = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or logical.is_absolute()
        or ".." in logical.parts
        or logical.as_posix() != relative
        or (
            expected_relative is not None
            and relative != expected_relative
        )
        or (
            expected_relative is None
            and tuple(logical.parts[:3])
            != ("screening", "results", "sdf_exports")
        )
    ):
        raise ValueError(f"{label}路径无效。")
    expected_sha256 = str(record.get("sha256") or "").lower()
    expected_size = record.get("size_bytes")
    if (
        SHA256_PATTERN.fullmatch(expected_sha256) is None
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise ValueError(f"{label}身份记录无效。")
    cursor = root
    for part in logical.parts:
        cursor = cursor / part
        if _path_is_link_or_reparse(cursor):
            raise ValueError(f"{label}路径包含链接或重解析点。")
    path = (root / Path(*logical.parts)).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label}越过项目目录。") from exc
    if (
        not path.is_file()
        or path.stat().st_size != expected_size
        or _sha256(path).lower() != expected_sha256
    ):
        raise ValueError(f"{label}完整性校验失败。")
    return path, relative


def _verified_existing_screening_result_sdf(
    root: Path,
    existing: dict[str, Any],
) -> dict[str, Any]:
    if (
        existing.get("schema_version") != SCREENING_RESULT_SDF_SCHEMA_VERSION
        or existing.get("generated") is not True
        or existing.get("status") not in {"complete", "partial"}
    ):
        raise ValueError("已记录的批量结果 SDF 状态无效。")
    fixed = {
        "aggregate": (
            "file",
            "sha256",
            "size_bytes",
            "screening/results/screening_poses.sdf",
        ),
        "manifest": (
            "manifest_file",
            "manifest_sha256",
            "manifest_size_bytes",
            "screening/results/screening_pose_manifest.json",
        ),
        "pose map": (
            "pose_map_file",
            "pose_map_sha256",
            "pose_map_size_bytes",
            "screening/results/screening_pose_map.csv",
        ),
    }
    verified: dict[str, Path] = {}
    fixed_relatives: set[str] = set()
    for label, (file_key, sha_key, size_key, expected_relative) in fixed.items():
        path, relative = _verified_screening_result_artifact(
            root,
            {
                "file": existing.get(file_key),
                "sha256": existing.get(sha_key),
                "size_bytes": existing.get(size_key),
            },
            label=f"批量结果 SDF {label}",
            expected_relative=expected_relative,
        )
        verified[label] = path
        fixed_relatives.add(relative)

    artifacts = (
        existing.get("artifacts")
        if isinstance(existing.get("artifacts"), list)
        else []
    )
    if not artifacts or len(artifacts) > MAX_SCREENING_EXPORT_FILES:
        raise ValueError("批量结果 SDF 审计文件清单无效。")
    seen: set[str] = set()
    total_size = 0
    for value in artifacts:
        if not isinstance(value, dict):
            raise ValueError("批量结果 SDF 审计文件记录无效。")
        relative = str(value.get("file") or "")
        if relative in seen:
            raise ValueError("批量结果 SDF 审计文件清单包含重复路径。")
        seen.add(relative)
        _, verified_relative = _verified_screening_result_artifact(
            root,
            value,
            label="批量结果 SDF 审计文件",
            expected_relative=(
                relative if relative in fixed_relatives else None
            ),
        )
        seen.add(verified_relative)
        total_size += int(value.get("size_bytes") or 0)
        if total_size > MAX_SCREENING_EXPORT_TOTAL_BYTES:
            raise ValueError("批量结果 SDF 审计文件总大小超过上限。")
    if not fixed_relatives.issubset(seen):
        raise ValueError("批量结果 SDF 审计文件清单缺少固定产物。")

    toolchain = (
        existing.get("toolchain")
        if isinstance(existing.get("toolchain"), dict)
        else {}
    )
    recorded_toolchain_sha256 = str(
        toolchain.get("snapshot_sha256") or ""
    ).lower()
    unsigned_toolchain = dict(toolchain)
    unsigned_toolchain.pop("snapshot_sha256", None)
    if (
        toolchain.get("profile") != SCREENING_RESULT_SDF_TOOLCHAIN_PROFILE
        or SHA256_PATTERN.fullmatch(recorded_toolchain_sha256) is None
        or hashlib.sha256(
            _canonical_json_bytes(unsigned_toolchain)
        ).hexdigest()
        != recorded_toolchain_sha256
    ):
        raise ValueError("批量结果 SDF 工具链快照校验失败。")

    manifest = _strict_json_object_bytes(
        verified["manifest"].read_bytes(),
        label="批量结果 SDF 拓扑验证清单",
    )
    coverage = (
        existing.get("topology_coverage")
        if isinstance(existing.get("topology_coverage"), dict)
        else {}
    )
    pose_count = coverage.get("pose_count")
    if (
        manifest.get("schema_version") != SCREENING_RESULT_SDF_SCHEMA_VERSION
        or manifest.get("status") != existing.get("status")
        or manifest.get("aggregate_file") != existing.get("file")
        or manifest.get("aggregate_sha256") != existing.get("sha256")
        or manifest.get("aggregate_size_bytes")
        != existing.get("size_bytes")
        or manifest.get("pose_map_file") != existing.get("pose_map_file")
        or manifest.get("pose_map_sha256")
        != existing.get("pose_map_sha256")
        or manifest.get("pose_map_size_bytes")
        != existing.get("pose_map_size_bytes")
        or manifest.get("coverage") != coverage
        or manifest.get("toolchain") != toolchain
        or isinstance(pose_count, bool)
        or not isinstance(pose_count, int)
        or pose_count <= 0
        or len(manifest.get("poses") or []) != pose_count
        or len(_split_sdf_record_bytes(verified["aggregate"].read_bytes()))
        != pose_count
    ):
        raise ValueError("批量结果 SDF 拓扑验证清单与状态记录不一致。")
    with verified["pose map"].open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        if sum(1 for _ in csv.DictReader(handle)) != pose_count:
            raise ValueError("批量结果 SDF pose map 行数与状态记录不一致。")
    return existing


def _generate_screening_result_sdf(
    root: Path,
    state: dict[str, Any],
    *,
    mk_export_executor: Callable[..., dict[str, Any]] | None = None,
    topology_validator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    outputs = state.setdefault("outputs", {})
    existing = outputs.get("sdf") if isinstance(outputs.get("sdf"), dict) else {}
    if existing.get("generated") is True:
        return _verified_existing_screening_result_sdf(root, existing)

    succeeded = [
        item
        for item in state.get("items") or []
        if isinstance(item, dict) and item.get("status") == "succeeded"
    ]
    topology_ready = [
        item
        for item in succeeded
        if item.get("topology_integrity") == "verified"
    ]
    coverage = {
        "total_items": len(state.get("items") or []),
        "succeeded_items": len(succeeded),
        "succeeded_with_topology": len(topology_ready),
        "exported_items": 0,
        "pose_count": 0,
    }
    if not succeeded:
        result = {
            "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
            "generated": False,
            "status": "unavailable",
            "file": "",
            "reason": "本次批量筛选没有成功构象，未生成结果 SDF。",
            "topology_coverage": coverage,
            "failures": [],
            "artifacts": [],
        }
        outputs["sdf"] = result
        return result
    if not topology_ready:
        result = {
            "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
            "generated": False,
            "status": "unavailable",
            "file": "",
            "reason": "成功构象均缺少冻结原始拓扑；未从 PDBQT 猜测键级。",
            "topology_coverage": coverage,
            "failures": [
                {
                    "item_id": str(item.get("item_id") or ""),
                    "code": "RAW_TOPOLOGY_UNAVAILABLE",
                    "message": "该成功构象没有冻结原始拓扑。",
                }
                for item in succeeded
            ],
            "artifacts": [],
        }
        outputs["sdf"] = result
        return result

    tool_status = get_preparation_tool_status(str(root))
    tools = (
        tool_status.get("tools")
        if isinstance(tool_status.get("tools"), dict)
        else {}
    )
    python_tool = (
        tools.get("python")
        if isinstance(tools.get("python"), dict)
        else {}
    )
    rdkit_tool = (
        tools.get("rdkit")
        if isinstance(tools.get("rdkit"), dict)
        else {}
    )
    meeko_tool = (
        tools.get("meeko")
        if isinstance(tools.get("meeko"), dict)
        else {}
    )
    if not tool_status.get("ok") or any(
        tool.get("status") != "ok"
        for tool in (python_tool, rdkit_tool, meeko_tool)
    ):
        result = {
            "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
            "generated": False,
            "status": "failed",
            "file": "",
            "reason": "结果 SDF 需要可用的 Python、RDKit 与 Meeko 工具链。",
            "topology_coverage": coverage,
            "failures": [
                {
                    "item_id": "",
                    "code": "RESULT_SDF_TOOLCHAIN_UNAVAILABLE",
                    "message": "无法验证并导出批量结果 SDF。",
                }
            ],
            "artifacts": [],
        }
        outputs["sdf"] = result
        return result
    toolchain_snapshot = _screening_result_sdf_toolchain_snapshot(
        python_tool,
        rdkit_tool,
        meeko_tool,
    )
    python_path = _verify_screening_result_sdf_python(toolchain_snapshot)
    active_mk_export = mk_export_executor or execute_mk_export
    active_validator = topology_validator or _validate_screening_result_topology
    result_audit_root = root / "screening" / "results" / "sdf_exports"
    result_audit_root.mkdir(parents=True, exist_ok=True)
    result_root: Path | None = None
    generation_id = ""
    for generation_number in range(1, 1000):
        generation_id = f"export_{generation_number:03d}"
        candidate_root = result_audit_root / generation_id
        try:
            candidate_root.mkdir()
        except FileExistsError:
            continue
        result_root = candidate_root
        break
    if result_root is None:
        raise ValueError("无法分配新的批量结果 SDF 审计编号。")

    aggregate_parts: list[bytes] = []
    pose_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in succeeded:
        item_id = str(item.get("item_id") or "")
        if item.get("topology_integrity") != "verified":
            failures.append(
                {
                    "item_id": item_id,
                    "code": "RAW_TOPOLOGY_UNAVAILABLE",
                    "message": "该成功构象没有冻结原始拓扑。",
                }
            )
            continue
        item_dir = result_root / item_id
        item_dir.mkdir()
        try:
            python_path = _verify_screening_result_sdf_python(
                toolchain_snapshot
            )
            topology_record = _verified_frozen_item_topology(root, item)
            if topology_record is None:
                raise ValueError("冻结原始拓扑不可用。")
            topology_path = root / Path(
                *PurePosixPath(str(topology_record["file"])).parts
            )
            output_pdbqt = _verified_screening_best_output(root, item)
            inspection = inspect_meeko_ligand_pdbqt(output_pdbqt)
            if inspection.get("embedded_topology") is not True:
                raise ValueError(
                    "成功构象缺少 Meeko SMILES/SMILES IDX 拓扑映射。"
                )
            exported_sdf = item_dir / "poses.sdf"
            active_mk_export(
                python_path,
                output_pdbqt,
                exported_sdf,
                record_dir=item_dir / "mk_export_record",
                cwd=item_dir,
            )
            if (
                not exported_sdf.is_file()
                or exported_sdf.stat().st_size <= 0
            ):
                raise ValueError("Meeko 未生成非空 poses.sdf。")
            facts = topology_record["chemical_facts"]
            expected_graph_sha256 = str(
                facts.get("canonical_topology_sha256") or ""
            ).lower()
            validation = active_validator(
                root,
                python_path,
                topology_path,
                exported_sdf,
                expected_graph_sha256,
                record_dir=item_dir / "topology_validation",
            )
            if (
                str(validation.get("rdkit_version") or "").strip()
                != str(
                    toolchain_snapshot["rdkit"].get("version") or ""
                ).strip()
            ):
                raise ValueError(
                    "结果 SDF 拓扑验证使用的 RDKit 版本与工具链快照不一致。"
                )
            _verify_screening_result_sdf_python(toolchain_snapshot)
            annotated, pose_count = _annotated_screening_sdf_records(
                exported_sdf.read_bytes(),
                {
                    "DOCKSTART_ITEM_ID": item_id,
                    "DOCKSTART_SOURCE_CANDIDATE_ID": str(
                        item.get("source_candidate_id") or ""
                    ),
                    "DOCKSTART_SOURCE_RECORD_ID": str(
                        item.get("source_record_id") or ""
                    ),
                    "DOCKSTART_SOURCE_TOPOLOGY_SHA256": str(
                        item.get("source_topology_sha256") or ""
                    ),
                    "DOCKSTART_RESULT_PDBQT_SHA256": str(
                        item.get("best_output_sha256") or ""
                    ),
                    "DOCKSTART_BEST_AFFINITY_KCAL_MOL": (
                        item.get("best_affinity_kcal_mol")
                    ),
                },
            )
            if pose_count != int(validation.get("pose_count") or 0):
                raise ValueError("SDF 记录数与拓扑验证 manifest 不一致。")
            aggregate_parts.append(annotated)
            for pose_index in range(1, pose_count + 1):
                pose_rows.append(
                    {
                        "item_id": item_id,
                        "pose_index": pose_index,
                        "source_candidate_id": str(
                            item.get("source_candidate_id") or ""
                        ),
                        "source_record_id": str(
                            item.get("source_record_id") or ""
                        ),
                        "source_topology_sha256": str(
                            item.get("source_topology_sha256") or ""
                        ),
                        "result_pdbqt_sha256": str(
                            item.get("best_output_sha256") or ""
                        ),
                        "best_affinity_kcal_mol": item.get(
                            "best_affinity_kcal_mol"
                        ),
                        "validated_graph_sha256": expected_graph_sha256,
                    }
                )
            coverage["exported_items"] += 1
            coverage["pose_count"] += pose_count
        except Exception as exc:  # noqa: BLE001 - isolate one item export.
            code = (
                exc.code
                if isinstance(exc, ProtocolValidationError)
                else "RESULT_SDF_ITEM_EXPORT_FAILED"
            )
            message = (
                exc.message
                if isinstance(exc, ProtocolValidationError)
                else str(exc)
            )
            failures.append(
                {
                    "item_id": item_id,
                    "code": str(code),
                    "message": str(message),
                }
            )

    aggregate_relative = Path(
        "screening",
        "results",
        "screening_poses.sdf",
    )
    manifest_relative = Path(
        "screening",
        "results",
        "screening_pose_manifest.json",
    )
    map_relative = Path(
        "screening",
        "results",
        "screening_pose_map.csv",
    )
    aggregate_path = root / aggregate_relative
    manifest_path = root / manifest_relative
    map_path = root / map_relative
    generated = bool(aggregate_parts)
    if generated:
        aggregate_payload = b"".join(aggregate_parts)
        limits = _validated_resource_limits(state.get("resource_limits"))
        if len(aggregate_payload) > limits.max_total_input_bytes:
            raise ValueError("批量结果 SDF 超过任务总输入资源上限。")
        atomic_write_bytes(aggregate_path, aggregate_payload)
        buffer = io.StringIO(newline="")
        fields = (
            "item_id",
            "pose_index",
            "source_candidate_id",
            "source_record_id",
            "source_topology_sha256",
            "result_pdbqt_sha256",
            "best_affinity_kcal_mol",
            "validated_graph_sha256",
        )
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(pose_rows)
        atomic_write_text(map_path, buffer.getvalue())
        atomic_write_json(
            manifest_path,
            {
                "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
                "created_at": _now_iso(),
                "status": (
                    "complete"
                    if coverage["exported_items"] == len(succeeded)
                    else "partial"
                ),
                "aggregate_file": aggregate_relative.as_posix(),
                "aggregate_sha256": _sha256(aggregate_path),
                "aggregate_size_bytes": aggregate_path.stat().st_size,
                "pose_map_file": map_relative.as_posix(),
                "pose_map_sha256": _sha256(map_path),
                "pose_map_size_bytes": map_path.stat().st_size,
                "coverage": coverage,
                "poses": pose_rows,
                "failures": failures,
                "toolchain": toolchain_snapshot,
            },
        )
    artifacts = _screening_sdf_artifacts(root, result_root)
    if generated:
        artifacts.extend(
            [
                {
                    "file": aggregate_relative.as_posix(),
                    "sha256": _sha256(aggregate_path),
                    "size_bytes": aggregate_path.stat().st_size,
                },
                {
                    "file": manifest_relative.as_posix(),
                    "sha256": _sha256(manifest_path),
                    "size_bytes": manifest_path.stat().st_size,
                },
                {
                    "file": map_relative.as_posix(),
                    "sha256": _sha256(map_path),
                    "size_bytes": map_path.stat().st_size,
                },
            ]
        )
    result = {
        "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
        "generation_id": generation_id,
        "generated": generated,
        "status": (
            "complete"
            if generated and coverage["exported_items"] == len(succeeded)
            else "partial"
            if generated
            else "failed"
        ),
        "file": aggregate_relative.as_posix() if generated else "",
        "sha256": _sha256(aggregate_path) if generated else "",
        "size_bytes": aggregate_path.stat().st_size if generated else 0,
        "manifest_file": manifest_relative.as_posix() if generated else "",
        "manifest_sha256": _sha256(manifest_path) if generated else "",
        "manifest_size_bytes": manifest_path.stat().st_size if generated else 0,
        "pose_map_file": map_relative.as_posix() if generated else "",
        "pose_map_sha256": _sha256(map_path) if generated else "",
        "pose_map_size_bytes": map_path.stat().st_size if generated else 0,
        "reason": (
            "已从 Meeko 拓扑映射导出，并逐构象核对冻结原始重原子图。"
            if generated and not failures
            else "仅部分成功构象通过原始拓扑验证。"
            if generated
            else "没有成功构象通过受控拓扑导出与验证。"
        ),
        "topology_coverage": coverage,
        "toolchain": toolchain_snapshot,
        "failures": failures,
        "artifacts": artifacts,
    }
    outputs["sdf"] = result
    return result


def generate_screening_result_sdf(
    project_dir: str,
    *,
    mk_export_executor: Callable[..., dict[str, Any]] | None = None,
    topology_validator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state.get("status") not in TERMINAL_SCREENING_STATUSES:
            return _error(
                "SCREENING_RESULT_SDF_NOT_TERMINAL",
                "批量筛选尚未结束，不能生成最终结果 SDF。",
            )
        _verified_frozen_ligand_entries(root, state)
        result = _generate_screening_result_sdf(
            root,
            state,
            mk_export_executor=mk_export_executor,
            topology_validator=topology_validator,
        )
        _write_state(root, state)
        return {
            "ok": result.get("status") != "failed",
            "project_dir": str(root),
            "screening": state,
            "sdf": result,
            "message": str(result.get("reason") or ""),
            "error": (
                None
                if result.get("status") != "failed"
                else {
                    "code": "SCREENING_RESULT_SDF_FAILED",
                    "title": "生成批量结果 SDF 失败。",
                    "message": "生成批量结果 SDF 失败。",
                    "raw_error": json.dumps(
                        result.get("failures") or [],
                        ensure_ascii=False,
                    ),
                    "suggestion": "请检查 Meeko 导出记录与冻结原始拓扑。",
                }
            ),
        }
    except Exception as exc:  # noqa: BLE001 - workflow boundary.
        return _error(
            "SCREENING_RESULT_SDF_ERROR",
            "生成批量结果 SDF 时发生错误。",
            str(exc),
            "请检查成功构象、冻结拓扑和 Assisted 工具链。",
        )


def _csv_payload(rows: list[dict[str, Any]], *, ranked: bool = False) -> str:
    fields = (("rank",) + SUMMARY_FIELDS) if ranked else SUMMARY_FIELDS
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for rank, item in enumerate(rows, start=1):
        row = {
            "item_id": item["item_id"],
            "ligand_file": item.get("source_file") or item["ligand_file"],
            "status": item["status"],
            "attempts": item.get("attempt_count", 0),
            "best_affinity_kcal_mol": (
                "" if item.get("best_affinity_kcal_mol") is None else item["best_affinity_kcal_mol"]
            ),
            "best_output_file": item.get("best_output_file") or "",
            "best_output_sha256": item.get("best_output_sha256") or "",
            "best_output_size_bytes": item.get("best_output_size_bytes") or 0,
            "error": item.get("last_error") or "",
        }
        if ranked:
            row["rank"] = rank
        writer.writerow(row)
    return buffer.getvalue()


def _write_summaries(root: Path, state: dict[str, Any]) -> None:
    ordered = sorted(state["items"], key=lambda item: int(item["order"]))
    succeeded = sorted(
        (item for item in ordered if item["status"] == "succeeded"),
        key=lambda item: (float(item["best_affinity_kcal_mol"]), int(item["order"])),
    )
    results_dir = root / "screening" / "results"
    summary_relative = Path("screening", "results", "screening_summary.csv")
    top_relative = Path("screening", "results", "screening_top_n.csv")
    summary_path = results_dir / "screening_summary.csv"
    top_path = results_dir / "screening_top_n.csv"
    atomic_write_text(summary_path, _csv_payload(ordered))
    atomic_write_text(
        top_path,
        _csv_payload(succeeded[: int(state["top_n"])], ranked=True),
    )
    outputs = state.setdefault("outputs", {})
    outputs["summary_csv"] = summary_relative.as_posix()
    outputs["summary_sha256"] = _sha256(summary_path)
    outputs["summary_size_bytes"] = summary_path.stat().st_size
    outputs["top_n_csv"] = top_relative.as_posix()
    outputs["top_n_sha256"] = _sha256(top_path)
    outputs["top_n_size_bytes"] = top_path.stat().st_size
    outputs.setdefault("report_md", "")
    outputs.setdefault("report_sha256", "")
    outputs.setdefault("report_size_bytes", 0)
    outputs.setdefault("reported_at", "")
    outputs.setdefault(
        "sdf",
        {
            "generated": False,
            "status": "unavailable",
            "file": "",
            "reason": "未提供原始配体拓扑；PDBQT 不包含可靠键级，未生成 SDF。",
        },
    )


def _markdown_cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", r"\|").replace("\n", " ")


def _screening_status_label(status: Any) -> str:
    return {
        "ready": "已就绪",
        "running": "运行中",
        "cancel_requested": "等待安全取消",
        "canceled": "已取消",
        "interrupted": "已中断",
        "completed": "已完成",
        "completed_with_failures": "完成（含失败项）",
        "pending": "待处理",
        "succeeded": "成功",
        "failed": "失败",
    }.get(str(status or ""), str(status or "未知"))


def _screening_report_text(state: dict[str, Any]) -> str:
    ordered = sorted(
        (item for item in state.get("items", []) if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    )
    ranked = sorted(
        (
            item
            for item in ordered
            if item.get("status") == "succeeded"
            and isinstance(item.get("best_affinity_kcal_mol"), (int, float))
            and math.isfinite(float(item["best_affinity_kcal_mol"]))
        ),
        key=lambda item: (
            float(item["best_affinity_kcal_mol"]),
            int(item.get("order") or 0),
        ),
    )
    rank_by_id = {
        str(item.get("item_id") or ""): rank
        for rank, item in enumerate(ranked, start=1)
    }
    counts = {
        "total": len(ordered),
        "succeeded": sum(item.get("status") == "succeeded" for item in ordered),
        "failed": sum(item.get("status") == "failed" for item in ordered),
        "unfinished": sum(
            item.get("status") in {"pending", "running", "interrupted"}
            for item in ordered
        ),
    }
    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    receptor = inputs.get("receptor") if isinstance(inputs.get("receptor"), dict) else {}
    is_ad4 = str(state.get("scoring_protocol") or "vina") == "ad4_maps"
    ad4_maps = (
        inputs.get("ad4_maps")
        if isinstance(inputs.get("ad4_maps"), dict)
        else {}
    )
    ad4_manifest = (
        ad4_maps.get("manifest")
        if isinstance(ad4_maps.get("manifest"), dict)
        else {}
    )
    ad4_map_files = (
        ad4_maps.get("files")
        if isinstance(ad4_maps.get("files"), list)
        else []
    )
    topology_summary = (
        inputs.get("topology")
        if isinstance(inputs.get("topology"), dict)
        else {}
    )
    tools = state.get("tools") if isinstance(state.get("tools"), dict) else {}
    vina_tool = tools.get("vina") if isinstance(tools.get("vina"), dict) else {}
    box = state.get("box") if isinstance(state.get("box"), dict) else {}
    vina = _screening_vina_with_defaults(state.get("vina"))
    outputs = state.get("outputs") if isinstance(state.get("outputs"), dict) else {}
    sdf = outputs.get("sdf") if isinstance(outputs.get("sdf"), dict) else {}
    sdf_coverage = (
        sdf.get("topology_coverage")
        if isinstance(sdf.get("topology_coverage"), dict)
        else {}
    )
    sdf_toolchain = (
        sdf.get("toolchain")
        if isinstance(sdf.get("toolchain"), dict)
        else {}
    )
    sdf_python_tool = (
        sdf_toolchain.get("python")
        if isinstance(sdf_toolchain.get("python"), dict)
        else {}
    )
    sdf_rdkit_tool = (
        sdf_toolchain.get("rdkit")
        if isinstance(sdf_toolchain.get("rdkit"), dict)
        else {}
    )
    sdf_meeko_tool = (
        sdf_toolchain.get("meeko")
        if isinstance(sdf_toolchain.get("meeko"), dict)
        else {}
    )
    top_limit = max(1, int(state.get("top_n") or 20))
    max_evals_label = (
        "Vina 自动决定"
        if vina.get("max_evals") == 0
        else _markdown_cell(vina.get("max_evals"))
    )
    parameter_warnings = [
        str(value)
        for value in (
            state.get("parameter_warnings")
            if isinstance(state.get("parameter_warnings"), list)
            else []
        )
        if str(value).strip()
    ]
    legacy_zero_applied = _valid_legacy_energy_range_zero_marker(state)
    topology_rows: list[str] = []
    for item in ordered:
        facts = (
            item.get("chemical_facts")
            if isinstance(item.get("chemical_facts"), dict)
            else {}
        )
        topology_rows.append(
            "| {item_id} | {record} | {integrity} | {charge} | {heavy} | {rotatable} | {macrocycle} |".format(
                item_id=_markdown_cell(item.get("item_id")),
                record=_markdown_cell(
                    item.get("source_record_name")
                    or item.get("display_label")
                    or item.get("source_file")
                ),
                integrity=(
                    "已验证"
                    if item.get("topology_integrity") == "verified"
                    else "不可用"
                ),
                charge=_markdown_cell(facts.get("formal_charge")),
                heavy=_markdown_cell(facts.get("heavy_atom_count")),
                rotatable=_markdown_cell(
                    facts.get("rotatable_bond_count")
                ),
                macrocycle=(
                    "是"
                    if facts.get("has_macrocycle") is True
                    else "否"
                    if facts.get("has_macrocycle") is False
                    else "—"
                ),
            )
        )

    lines = [
        "# DockStart 批量筛选实验记录",
        "",
        "> Docking score 仅供结构结合趋势参考，不能替代实验验证。",
        "",
        "## 任务概览",
        "",
        f"- 筛选编号：`{_markdown_cell(state.get('screening_id'))}`",
        f"- 状态：{_screening_status_label(state.get('status'))}",
        f"- 创建时间：{_markdown_cell(state.get('created_at'))}",
        f"- 开始时间：{_markdown_cell(state.get('started_at'))}",
        f"- 结束时间：{_markdown_cell(state.get('finished_at'))}",
        f"- 配体总数：{counts['total']}",
        f"- 成功：{counts['succeeded']}",
        f"- 失败：{counts['failed']}",
        f"- 未完成：{counts['unfinished']}",
        "",
        "## 冻结输入与工具",
        "",
        f"- 受体快照：`{_markdown_cell(receptor.get('file'))}`",
        f"- 受体 SHA256：`{_markdown_cell(receptor.get('sha256'))}`",
        (
            "- 原始配体拓扑覆盖："
            f"{_markdown_cell(topology_summary.get('status'))}，"
            f"{_markdown_cell(topology_summary.get('available_count'))}/"
            f"{_markdown_cell(topology_summary.get('total_count'))}"
        ),
        f"- AutoDock Vina：{_markdown_cell(vina_tool.get('version'))}",
        f"- Vina 来源：{_markdown_cell(vina_tool.get('source'))}",
        f"- Vina SHA256：`{_markdown_cell(vina_tool.get('sha256'))}`",
        f"- 评分协议：{'标准 AutoDock4 maps' if is_ad4 else 'Vina / Vinardo'}",
        f"- 评分函数：{_markdown_cell(vina.get('scoring'))}",
        *(
            [
                f"- maps 集合：`{_markdown_cell(ad4_maps.get('map_set_id'))}`",
                f"- maps 前缀：`{_markdown_cell(ad4_maps.get('prefix'))}`",
                f"- maps 文件数：{len(ad4_map_files)}",
                f"- maps manifest SHA256：`{_markdown_cell(ad4_manifest.get('sha256'))}`",
            ]
            if is_ad4
            else []
        ),
        f"- 搜索彻底程度：{_markdown_cell(vina.get('exhaustiveness'))}",
        f"- 单条搜索链最大评估次数：{max_evals_label}",
        f"- 输出构象数：{_markdown_cell(vina.get('num_modes'))}",
        f"- 构象最小间距：{_markdown_cell(vina.get('min_rmsd'))} Å",
        f"- 能量范围：{_markdown_cell(vina.get('energy_range'))} kcal/mol",
        f"- 网格间距：{_markdown_cell(vina.get('spacing'))} Å",
        f"- 日志详细程度：{_markdown_cell(vina.get('verbosity'))}",
        f"- 关闭显式受体原子精修：{'是' if vina.get('no_refine') else '否'}",
        f"- 网格体素数取偶数：{'是' if vina.get('force_even_voxels') else '否'}",
        f"- 单任务 CPU：{_markdown_cell(vina.get('cpu'))}",
        f"- 随机种子：{_markdown_cell(vina.get('seed'))}",
        f"- 失败重试上限：{_markdown_cell(state.get('max_retries'))}",
        "",
        "### 对接箱体",
        "",
        "| 参数 | X | Y | Z |",
        "|---|---:|---:|---:|",
        (
            f"| 中心（Å） | {_markdown_cell(box.get('center_x'))} | "
            f"{_markdown_cell(box.get('center_y'))} | {_markdown_cell(box.get('center_z'))} |"
        ),
        (
            f"| 尺寸（Å） | {_markdown_cell(box.get('size_x'))} | "
            f"{_markdown_cell(box.get('size_y'))} | {_markdown_cell(box.get('size_z'))} |"
        ),
        "",
        "### 配体原始拓扑与化学事实",
        "",
        "| 队列项 | 原始记录 | 拓扑 | 形式电荷 | 重原子数 | 可旋转键数 | 大环 |",
        "|---|---|---|---:|---:|---:|---|",
        *(topology_rows or ["| — | — | — | — | — | — | — |"]),
        "",
        f"## Top {min(top_limit, len(ranked))}",
        "",
        "| 排名 | 配体 | 最佳评分（kcal/mol） | 尝试次数 | 输出 PDBQT |",
        "|---:|---|---:|---:|---|",
    ]
    if ranked:
        for item in ranked[:top_limit]:
            lines.append(
                "| {rank} | {ligand} | {score:.3f} | {attempts} | `{output}` |".format(
                    rank=rank_by_id.get(str(item.get("item_id") or ""), "—"),
                    ligand=_markdown_cell(item.get("source_file") or item.get("ligand_file")),
                    score=float(item["best_affinity_kcal_mol"]),
                    attempts=int(item.get("attempt_count") or 0),
                    output=_markdown_cell(item.get("best_output_file")),
                )
            )
    else:
        lines.append("| — | 尚无成功结果 | — | — | — |")

    lines.extend(
        [
            "",
            "## 完整结果",
            "",
            "| 原始顺序 | 排名 | 配体 | 状态 | 尝试次数 | 最佳评分（kcal/mol） | 输出 SHA256 | 错误 |",
            "|---:|---:|---|---|---:|---:|---|---|",
        ]
    )
    for item in ordered:
        score = item.get("best_affinity_kcal_mol")
        score_text = (
            f"{float(score):.3f}"
            if isinstance(score, (int, float)) and math.isfinite(float(score))
            else "—"
        )
        lines.append(
            "| {order} | {rank} | {ligand} | {status} | {attempts} | {score} | `{sha256}` | {error} |".format(
                order=int(item.get("order") or 0),
                rank=rank_by_id.get(str(item.get("item_id") or ""), "—"),
                ligand=_markdown_cell(item.get("source_file") or item.get("ligand_file")),
                status=_screening_status_label(item.get("status")),
                attempts=int(item.get("attempt_count") or 0),
                score=score_text,
                sha256=_markdown_cell(item.get("best_output_sha256")),
                error=_markdown_cell(item.get("last_error")),
            )
        )
    if not ordered:
        lines.append("| — | — | 尚无配体 | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            f"- 完整汇总：`{_markdown_cell(outputs.get('summary_csv'))}`",
            f"- Top N 汇总：`{_markdown_cell(outputs.get('top_n_csv'))}`",
            (
                (
                    f"- SDF：`{_markdown_cell(sdf.get('file'))}`"
                    f"（{_markdown_cell(sdf.get('status'))}；"
                    f"{_markdown_cell(sdf_coverage.get('exported_items'))}"
                    "/"
                    f"{_markdown_cell(sdf_coverage.get('succeeded_items'))}"
                    " 个成功配体）"
                )
                if sdf.get("generated")
                else f"- SDF：未生成。{_markdown_cell(sdf.get('reason'))}"
            ),
            (
                f"- SDF 拓扑验证清单：`{_markdown_cell(sdf.get('manifest_file'))}`"
                if sdf.get("manifest_file")
                else "- SDF 拓扑验证清单：—"
            ),
            (
                "- SDF 导出工具链："
                f"Python {_markdown_cell(sdf_python_tool.get('version'))}；"
                f"RDKit {_markdown_cell(sdf_rdkit_tool.get('version'))}；"
                f"Meeko {_markdown_cell(sdf_meeko_tool.get('version'))}"
                if sdf.get("generated")
                else "- SDF 导出工具链：—"
            ),
            (
                "- SDF 工具链快照 SHA256："
                f"`{_markdown_cell(sdf_toolchain.get('snapshot_sha256'))}`"
                if sdf.get("generated")
                else "- SDF 工具链快照 SHA256：—"
            ),
            "",
            "## 科学边界",
            "",
            (
                "- 本任务按固定受体与冻结 AutoDock4 maps 逐个运行配体，不是多个配体同时进入一个结合位点的联合对接。"
                if is_ad4
                else "- 本任务按固定受体、Box 与 Vina 参数逐个运行配体，不是多个配体同时进入一个结合位点的联合对接。"
            ),
            (
                "- 排名只在本批次相同 AD4 maps 和搜索参数下按 AD4 数值排序，不应与 Vina、Vinardo 或其他网格条件直接比较。"
                if is_ad4
                else "- 排名只在本批次相同评分协议和参数下按 Vina 数值排序，不应与其他评分函数或其他输入条件直接比较。"
            ),
            "- 单项失败不会自动说明该配体不能结合；应结合错误日志、输入质量和必要的进一步计算或实验判断。",
            "- PDBQT 不保存可靠完整的键级信息；没有受控原始拓扑时，DockStart 不会据此猜测并生成 SDF。",
            "- 批量结果 SDF 只收录经 Meeko 拓扑映射导出且与冻结原始重原子图一致的构象；失败或缺失覆盖会在清单中显式记录。",
            "",
        ]
    )
    if parameter_warnings or legacy_zero_applied:
        lines.extend(["## 参数兼容与排除说明", ""])
        if legacy_zero_applied:
            lines.append(
                "- 本记录沿用历史 schema v1 的 `energy_range = 0`；"
                "该兼容仅用于恢复既有批量任务，新任务必须使用大于 0 的值。"
            )
        lines.extend(f"- {_markdown_cell(warning)}" for warning in parameter_warnings)
        lines.append("")
    return "\n".join(lines)


def export_screening_markdown_report(project_dir: str) -> dict[str, Any]:
    """Write a terminal screening snapshot as one auditable Markdown record."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state.get("status") not in {
            "completed",
            "completed_with_failures",
            "canceled",
        }:
            return _error(
                "SCREENING_REPORT_NOT_TERMINAL",
                "批量筛选尚未结束，不能生成最终实验记录。",
                f"status={state.get('status')}",
                "请等待队列完成，或安全取消后再生成实验记录。",
            )
        outputs = state.setdefault("outputs", {})
        if any(
            not outputs.get(key)
            for key in (
                "summary_csv",
                "summary_sha256",
                "summary_size_bytes",
                "top_n_csv",
                "top_n_sha256",
                "top_n_size_bytes",
            )
        ):
            _write_summaries(root, state)
            outputs = state.setdefault("outputs", {})
        report_relative = Path("screening", "results", "screening_report.md")
        report_path = root / report_relative
        atomic_write_text(report_path, _screening_report_text(state))
        outputs["report_md"] = report_relative.as_posix()
        outputs["report_sha256"] = _sha256(report_path)
        outputs["report_size_bytes"] = report_path.stat().st_size
        outputs["reported_at"] = _now_iso()
        _write_state(root, state)
        return {
            "ok": True,
            "project_dir": str(root),
            "screening": state,
            "report_file": report_relative.as_posix(),
            "report_sha256": outputs["report_sha256"],
            "report_size_bytes": outputs["report_size_bytes"],
            "message": "批量筛选实验记录已生成。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - workflow boundary returns structured errors.
        return _error(
            "SCREENING_REPORT_ERROR",
            "生成批量筛选实验记录失败。",
            str(exc),
            "请检查 screening 状态、结果文件和项目目录写入权限。",
        )


def _persist_interrupted_run_failure(
    root: Path | None,
    *,
    error_key: str,
    code: str,
    message: str,
) -> None:
    if root is None:
        return
    try:
        persisted = _read_state(root)
    except Exception:
        return
    if persisted.get("status") not in {"running", "cancel_requested"}:
        return

    detected_at = _now_iso()
    persisted["status"] = "interrupted"
    persisted["finished_at"] = detected_at
    persisted[error_key] = {
        "code": code,
        "message": message,
        "detected_at": detected_at,
    }
    for item in persisted.get("items") or []:
        if not isinstance(item, dict) or item.get("status") != "running":
            continue
        item["status"] = "interrupted"
        item["last_error"] = message
        running_attempt = next(
            (
                attempt
                for attempt in reversed(item.get("attempts") or [])
                if isinstance(attempt, dict) and attempt.get("status") == "running"
            ),
            None,
        )
        if running_attempt is None:
            continue
        running_attempt.update(
            {
                "status": "interrupted",
                "finished_at": detected_at,
                "error": message,
                "integrity": {
                    "status": "not_checked",
                    "checked_at": detected_at,
                    "code": code,
                    "message": message,
                },
            }
        )
        directory_value = str(running_attempt.get("directory") or "")
        logical = PurePosixPath(directory_value)
        if (
            directory_value
            and "\\" not in directory_value
            and ":" not in directory_value
            and not logical.is_absolute()
            and ".." not in logical.parts
            and logical.parts[:2] == ("screening", "attempts")
        ):
            attempt_json = root.joinpath(*logical.parts) / "attempt.json"
            try:
                resolved_attempt_json = attempt_json.resolve(strict=True)
                resolved_attempt_json.relative_to(root)
                if resolved_attempt_json.is_file():
                    atomic_write_json(resolved_attempt_json, running_attempt)
            except (OSError, RuntimeError, ValueError):
                pass
    try:
        _write_state(root, persisted)
    except Exception:
        pass


def run_screening(
    project_dir: str,
    *,
    runner: Runner | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Run queued ligands serially, retrying without changing queue order."""

    root: Path | None = None
    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] not in {"ready"}:
            return _error(
                "SCREENING_NOT_READY",
                f"当前任务状态为 {state['status']}，不能直接运行。",
                suggestion="已取消或中断的任务请先执行 resume。",
            )
        if max_items is not None and max_items < 1:
            raise ValueError("max_items 必须大于 0。")
        execution_preflight = _validate_screening_execution_state(root, state)
        if not execution_preflight.get("ok"):
            return execution_preflight
        active_runner = runner or _default_runner
        state["status"] = "running"
        state["started_at"] = state.get("started_at") or _now_iso()
        _write_state(root, state)
        processed = 0
        max_attempts = state["max_retries"] + 1

        while state["queue"]:
            _refresh_cancel(root, state)
            if state.get("cancel_requested"):
                state["status"] = "canceled"
                state["finished_at"] = _now_iso()
                break
            item_id = state["queue"].pop(0)
            item = next((row for row in state["items"] if row["item_id"] == item_id), None)
            if item is None:
                raise ValueError(f"队列引用了不存在的项目：{item_id}")
            if item["status"] != "pending":
                continue
            succeeded = _attempt_item(root, state, item, active_runner)
            processed += 1
            if not succeeded and int(item["attempt_count"]) < max_attempts:
                item["status"] = "pending"
                state["queue"].append(item_id)
            _write_state(root, state)
            if max_items is not None and processed >= max_items and state["queue"]:
                state["status"] = "interrupted"
                state["finished_at"] = _now_iso()
                break

        if state.get("cancel_requested") and state["status"] in {"running", "cancel_requested"}:
            state["status"] = "canceled"
            state["finished_at"] = _now_iso()
        elif state["status"] == "running":
            failed = any(item["status"] == "failed" for item in state["items"])
            state["status"] = "completed_with_failures" if failed else "completed"
            state["finished_at"] = _now_iso()
        _write_summaries(root, state)
        try:
            _generate_screening_result_sdf(root, state)
        except Exception as exc:  # noqa: BLE001 - docking result remains valid.
            state.setdefault("outputs", {})["sdf"] = {
                "schema_version": SCREENING_RESULT_SDF_SCHEMA_VERSION,
                "generated": False,
                "status": "failed",
                "file": "",
                "reason": "批量对接已完成，但结果 SDF 后处理失败。",
                "topology_coverage": {
                    "total_items": len(state.get("items") or []),
                    "succeeded_items": sum(
                        isinstance(item, dict)
                        and item.get("status") == "succeeded"
                        for item in state.get("items") or []
                    ),
                    "succeeded_with_topology": sum(
                        isinstance(item, dict)
                        and item.get("status") == "succeeded"
                        and item.get("topology_integrity") == "verified"
                        for item in state.get("items") or []
                    ),
                    "exported_items": 0,
                    "pose_count": 0,
                },
                "failures": [
                    {
                        "item_id": "",
                        "code": "SCREENING_RESULT_SDF_ERROR",
                        "message": str(exc),
                    }
                ],
                "artifacts": [],
            }
        _write_state(root, state)
        return {
            "ok": True,
            "project_dir": str(root),
            "screening": state,
            "processed_attempts": processed,
            "message": f"批量筛选当前状态：{state['status']}。",
            "error": None,
        }
    except _ScreeningIntegrityError as exc:
        _persist_interrupted_run_failure(
            root,
            error_key="last_integrity_error",
            code=exc.code,
            message=str(exc),
        )
        return _error(
            exc.code,
            "批量筛选冻结输入或工具已发生变化。",
            str(exc),
            "请恢复任务创建时的冻结文件与 Vina；核对无误后再执行 resume。",
        )
    except Exception as exc:  # noqa: BLE001
        _persist_interrupted_run_failure(
            root,
            error_key="last_runtime_error",
            code="SCREENING_RUN_ERROR",
            message=str(exc),
        )
        return _error(
            "SCREENING_RUN_ERROR",
            "运行批量筛选失败。",
            str(exc),
            "请读取 screening.json，修复输入后使用 resume 恢复。",
        )


def _json_argument(value: str) -> dict[str, Any]:
    candidate = Path(value).expanduser()
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON 参数必须是对象。")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart 批量虚拟筛选")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--receptor", required=True)
    create.add_argument("--ligand", action="append", default=[])
    create.add_argument("--candidate-id", action="append", default=[])
    create.add_argument("--staging-revision", default="")
    create.add_argument("--vina-path")
    create.add_argument("--box-json", type=_json_argument, required=True)
    create.add_argument("--vina-json", type=_json_argument, required=True)
    create.add_argument("--limits-json", type=_json_argument)
    create.add_argument("--max-retries", type=int, default=1)
    create.add_argument("--top-n", type=int, default=20)
    stage = commands.add_parser("stage")
    stage.add_argument("--project", required=True)
    stage.add_argument("--file", action="append", required=True)
    stage.add_argument("--limits-json", type=_json_argument)
    retry_preparation = commands.add_parser("retry-preparation")
    retry_preparation.add_argument("--project", required=True)
    retry_preparation.add_argument(
        "--candidate-id",
        action="append",
        required=True,
    )
    retry_preparation.add_argument("--staging-revision", required=True)
    archives = commands.add_parser("archives")
    archives.add_argument("--project", required=True)
    archive_detail = commands.add_parser("archive-detail")
    archive_detail.add_argument("--project", required=True)
    archive_detail.add_argument("--archive-id", required=True)
    archive_export = commands.add_parser("archive-export")
    archive_export.add_argument("--project", required=True)
    archive_export.add_argument("--archive-id", required=True)
    archive_export.add_argument("--output", required=True)
    archive_export.add_argument("--overwrite", action="store_true")
    archive_compare = commands.add_parser("archive-compare")
    archive_compare.add_argument("--project", required=True)
    archive_compare.add_argument("--archive-id", action="append", required=True)
    for name in (
        "status",
        "run",
        "cancel",
        "resume",
        "archive",
        "report",
        "result-sdf",
    ):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name == "run":
            command.add_argument("--max-items", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "create":
        result = create_screening(
            args.project,
            args.receptor,
            args.ligand,
            ligand_candidate_ids=args.candidate_id,
            expected_staging_revision_sha256=args.staging_revision,
            vina_path=args.vina_path,
            box=args.box_json,
            vina=args.vina_json,
            max_retries=args.max_retries,
            top_n=args.top_n,
            resource_limits=args.limits_json,
        )
    elif args.command == "stage":
        result = stage_screening_inputs(
            args.project,
            args.file,
            resource_limits=args.limits_json,
        )
    elif args.command == "retry-preparation":
        result = retry_screening_preparation(
            args.project,
            args.candidate_id,
            expected_staging_revision_sha256=args.staging_revision,
        )
    elif args.command == "status":
        result = get_screening_status(args.project)
    elif args.command == "run":
        result = run_screening(args.project, max_items=args.max_items)
    elif args.command == "cancel":
        result = request_screening_cancel(args.project)
    elif args.command == "resume":
        result = resume_screening(args.project)
    elif args.command == "archive":
        result = archive_screening(args.project)
    elif args.command == "result-sdf":
        result = generate_screening_result_sdf(args.project)
    elif args.command == "archives":
        result = list_screening_archives(args.project)
    elif args.command == "archive-detail":
        result = get_screening_archive(args.project, args.archive_id)
    elif args.command == "archive-export":
        result = export_screening_archive_zip(
            args.project,
            args.archive_id,
            args.output,
            overwrite=args.overwrite,
        )
    elif args.command == "archive-compare":
        result = compare_screening_archives(args.project, args.archive_id)
    else:
        result = export_screening_markdown_report(args.project)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
