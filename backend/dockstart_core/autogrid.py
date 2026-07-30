"""AutoGrid4 GPF, affinity-map, and AutoDock4 protocol infrastructure."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from adapters import autogrid_adapter
from dockstart_core.persistence import atomic_write_text
from dockstart_core.project import BoxSettings, _project_from_dict, load_project, save_project
from dockstart_core.settings import load_settings

MAP_SET_ID_PATTERN = re.compile(r"^ad4_(\d{3,})$")
AD4ZN_MAP_SET_ID_PATTERN = re.compile(r"^ad4zn_(\d{3,})$")
HYDRATED_MAP_SET_ID_PATTERN = re.compile(r"^hydrated_(\d{3,})$")
MAP_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_SPACING = 0.375
MAX_GRID_POINTS = 126
MAX_PARAMETER_FILE_BYTES = 5 * 1024 * 1024
BOX_COVERAGE_EPSILON_ANGSTROM = 0.0
GRID_GEOMETRY_TOLERANCE_ANGSTROM = 1e-6
REQUESTED_BOX_GRID_COVERAGE_METHOD = (
    "requested_box_and_autogrid_npts_spacing_v1"
)
AD4ZN_PROTOCOL_ID = "ad4zn_beta"
HYDRATED_PROTOCOL_ID = "hydrated_ad4_experimental"
AD4ZN_RECEPTOR_TYPES = {"Zn", "TZ"}
AD4ZN_BOX_COVERAGE_METHOD = (
    "ad4zn_zn_tz_requested_box_and_autogrid_npts_spacing_v1"
)
AD4_MIN_AUTOGRID_VERSION = (4, 2, 6)
AD4ZN_MIN_AUTOGRID_VERSION = (4, 2, 7)
AD4ZN_NBP_R_EPS = (
    "nbp_r_eps 0.25 23.2135 12 6 NA TZ",
    "nbp_r_eps 2.1 3.8453 12 6 OA Zn",
    "nbp_r_eps 2.25 7.5914 12 6 SA Zn",
    "nbp_r_eps 1.0 0.0 12 6 HD Zn",
    "nbp_r_eps 2.0 0.0060 12 6 NA Zn",
    "nbp_r_eps 2.0 0.2966 12 6 N Zn",
)
STANDARD_NON_METAL_TYPES = {
    "A",
    "B",
    "Br",
    "C",
    "Cl",
    "F",
    "H",
    "HD",
    "HS",
    "I",
    "N",
    "NA",
    "NS",
    "O",
    "OA",
    "OS",
    "P",
    "S",
    "SA",
    "Si",
}
METAL_TYPES = {"Ca", "Co", "Cu", "Fe", "Mg", "Mn", "Ni", "Zn"}
CANONICAL_TYPES = {
    value.lower(): value
    for value in sorted(
        STANDARD_NON_METAL_TYPES | METAL_TYPES | {"TZ", "W"}
    )
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error(code: str, message: str, raw_error: str = "", suggestion: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "project": None,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _issue_messages(issues: Any) -> str:
    if not isinstance(issues, list):
        return str(issues or "")
    messages: list[str] = []
    for issue in issues:
        if isinstance(issue, dict):
            message = str(
                issue.get("message")
                or issue.get("title")
                or issue.get("raw_error")
                or issue.get("code")
                or ""
            ).strip()
        else:
            message = str(issue or "").strip()
        if message:
            messages.append(message)
    return "；".join(messages)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(path: Path, relative_path: str) -> dict[str, Any]:
    return {
        "relative_path": Path(relative_path).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _load_project_model(project_dir: str) -> tuple[Any | None, Path | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, None, loaded
    root = Path(project_dir).expanduser().resolve()
    try:
        return _project_from_dict(loaded["project"], root), root, None
    except Exception as exc:  # noqa: BLE001
        return None, None, _error(
            "MAPS_PROJECT_INVALID",
            "project.json 无法用于 AutoDock4 maps 工作流。",
            str(exc),
            "请先修复项目文件后重试。",
        )


def _project_file(root: Path, relative_path: str, label: str) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"MAPS_{label.upper()}_NOT_SET",
            f"尚未设置{label} PDBQT。",
            suggestion=f"请先导入准备后的{label} PDBQT。",
        )
    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"MAPS_{label.upper()}_PATH_INVALID",
            f"{label} PDBQT 必须使用项目内相对路径。",
        )
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, _error(
            f"MAPS_{label.upper()}_OUTSIDE_PROJECT",
            f"{label} PDBQT 指向项目目录外。",
            str(candidate),
        )
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return None, _error(
            f"MAPS_{label.upper()}_MISSING",
            f"没有找到非空的{label} PDBQT。",
            str(candidate),
        )
    return candidate, None


def canonical_atom_type(value: str) -> str:
    """Return AutoDock's canonical spelling for a known atom type.

    PDBQT writers are not consistent about element-like case (for example
    ``CL`` versus ``Cl``).  AutoGrid map filenames and parameter files use the
    canonical spelling.  Unknown values are deliberately preserved so normal
    parameter/type validation can reject them instead of silently translating
    them into a supported type.
    """

    return CANONICAL_TYPES.get(str(value or "").strip().lower(), str(value or "").strip())


def canonical_atom_types(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return sorted(
        {
            normalized
            for value in values
            if (normalized := canonical_atom_type(str(value or "")))
        }
    )


# Internal compatibility alias for the existing call sites in this module.
_canonical_atom_type = canonical_atom_type


def read_pdbqt_atom_types(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    atom_types: set[str] = set()
    atom_count = 0
    try:
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
                    continue
                atom_count += 1
                parts = line.split()
                if parts:
                    atom_type = _canonical_atom_type(parts[-1])
                    if atom_type:
                        atom_types.add(atom_type)
    except OSError as exc:
        return _error("MAPS_PDBQT_READ_ERROR", "无法读取 PDBQT 原子类型。", str(exc))
    if atom_count <= 0 or not atom_types:
        return _error(
            "MAPS_ATOM_TYPES_MISSING",
            "PDBQT 中没有可用于 AutoGrid4 的 ATOM/HETATM 原子类型。",
            str(source),
            "请检查文件是否为准备后的 PDBQT。",
        )
    return {
        "ok": True,
        "path": str(source),
        "atom_count": atom_count,
        "atom_types": sorted(atom_types),
        "error": None,
    }


def _validated_type_list(
    requested: Any,
    detected: list[str],
    *,
    role: str,
    eligibility_from_detected_only: bool = False,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    if requested in (None, "", []):
        values = list(detected)
    elif isinstance(requested, str):
        values = [item for item in re.split(r"[\s,;]+", requested.strip()) if item]
    elif isinstance(requested, list):
        values = [str(item).strip() for item in requested if str(item).strip()]
    else:
        return None, _error("MAPS_ATOM_TYPES_INVALID", f"{role}原子类型必须是列表或逗号分隔文本。")
    normalized = sorted({_canonical_atom_type(item) for item in values})
    detected_normalized = sorted({_canonical_atom_type(item) for item in detected})
    unknown = sorted(set(normalized) - STANDARD_NON_METAL_TYPES - METAL_TYPES)
    actual_unknown = sorted(set(detected_normalized) - STANDARD_NON_METAL_TYPES - METAL_TYPES)
    actual_metals = sorted(set(detected_normalized) & METAL_TYPES)
    declared_metals = sorted(set(normalized) & METAL_TYPES)
    metals = actual_metals if eligibility_from_detected_only else declared_metals
    missing = sorted(set(detected_normalized) - set(normalized))
    if actual_unknown:
        return None, _error(
            "MAPS_ATOM_TYPE_UNSUPPORTED",
            f"{role} PDBQT 实际包含当前 AutoDock4 基础协议未支持的原子类型。",
            ", ".join(actual_unknown),
            "三级协议仅开放标准非金属体系；特殊类型应由后续专用协议管理。",
        )
    if unknown:
        return None, _error(
            "MAPS_ATOM_TYPE_UNSUPPORTED",
            f"{role}原子类型声明包含当前 AutoDock4 基础协议未支持的类型。",
            ", ".join(unknown),
            "三级协议仅开放标准非金属体系；特殊类型应由后续专用协议管理。",
        )
    if metals:
        return None, _error(
            "MAPS_METAL_PROTOCOL_REQUIRED",
            f"{role}包含金属原子类型，不能进入标准 AutoDock4 (maps) 协议。",
            ", ".join(metals),
            "含 Zn 的体系请等待 AD4Zn 专用协议；其他金属也需要单独参数与人工审查。",
        )
    if missing:
        return None, _error(
            "MAPS_ATOM_TYPES_INCOMPLETE",
            f"{role}原子类型列表遗漏了 PDBQT 中实际存在的类型。",
            ", ".join(missing),
            "请保留所有检测到的原子类型；可额外加入用于复用 maps 的标准类型。",
        )
    return normalized, None


def _validated_ad4zn_receptor_types(
    requested: Any,
    detected: list[str],
) -> tuple[list[str] | None, dict[str, Any] | None]:
    if requested in (None, "", []):
        values = list(detected)
    elif isinstance(requested, str):
        values = [item for item in re.split(r"[\s,;]+", requested.strip()) if item]
    elif isinstance(requested, list):
        values = [str(item).strip() for item in requested if str(item).strip()]
    else:
        return None, _error(
            "AD4ZN_RECEPTOR_TYPES_INVALID",
            "AD4Zn 受体原子类型必须是列表或分隔文本。",
        )
    normalized = sorted({_canonical_atom_type(item) for item in values})
    detected_normalized = sorted({_canonical_atom_type(item) for item in detected})
    allowed = STANDARD_NON_METAL_TYPES | AD4ZN_RECEPTOR_TYPES
    unknown = sorted(set(normalized) - allowed)
    actual_unknown = sorted(set(detected_normalized) - allowed)
    unsupported_metals = sorted(
        (set(normalized) | set(detected_normalized)) & (METAL_TYPES - {"Zn"})
    )
    missing = sorted(set(detected_normalized) - set(normalized))
    if actual_unknown or unknown:
        return None, _error(
            "AD4ZN_RECEPTOR_TYPE_UNSUPPORTED",
            "AD4Zn beta 受体包含未验证的原子类型。",
            ", ".join(sorted(set(actual_unknown + unknown))),
            "请只使用经准备的 Zn/TZ 刚性受体；其他特殊类型需要单独协议验证。",
        )
    if unsupported_metals:
        return None, _error(
            "AD4ZN_NON_ZN_METAL_UNSUPPORTED",
            "AD4Zn beta 只处理 Zn，当前受体仍包含其他金属类型。",
            ", ".join(unsupported_metals),
            "请人工检查受体，并为本次 beta 协议准备只包含目标 Zn 环境的受体。",
        )
    if {"Zn", "TZ"} - set(detected_normalized):
        return None, _error(
            "AD4ZN_TZ_RECEPTOR_REQUIRED",
            "AD4Zn maps 必须使用同时包含 Zn 与 TZ 的专用受体。",
            ", ".join(detected_normalized),
            "请先完成 Zn 位点复核并生成 TZ 受体。",
        )
    if missing:
        return None, _error(
            "AD4ZN_RECEPTOR_TYPES_INCOMPLETE",
            "AD4Zn 受体原子类型列表遗漏了实际存在的类型。",
            ", ".join(missing),
        )
    return sorted("ZN" if item == "Zn" else item for item in normalized), None


def _validate_ad4zn_parameter_snapshot(
    parameter_path: Path,
    *,
    recorded_parameter: dict[str, Any],
    receptor_snapshot: Path,
    ligand_snapshot: Path,
    receptor_types: list[str],
    ligand_types: list[str],
) -> dict[str, Any]:
    """Revalidate the exact AD4Zn.dat bytes AutoGrid is about to read."""

    from dockstart_core.ad4zn import validate_parameter_file

    validation = validate_parameter_file(parameter_path)
    try:
        observed_size = parameter_path.stat().st_size
        observed_sha256 = _sha256(parameter_path)
    except OSError as exc:
        return _error(
            "AD4ZN_PARAMETER_SNAPSHOT_INVALID",
            "无法复核 AutoGrid 即将读取的 AD4Zn.dat 快照。",
            str(exc),
            "请停止同时修改参数文件，并重新生成 AD4Zn maps。",
        )

    expected_sha256 = str(recorded_parameter.get("sha256") or "").lower()
    try:
        expected_size = int(recorded_parameter.get("size_bytes"))
    except (TypeError, ValueError):
        expected_size = -1
    if (
        expected_size <= 0
        or observed_size != expected_size
        or not SHA256_PATTERN.fullmatch(expected_sha256)
        or observed_sha256 != expected_sha256
    ):
        validation_error = (
            validation.get("error")
            if isinstance(validation.get("error"), dict)
            else {}
        )
        return _error(
            "AD4ZN_PARAMETER_CHANGED_DURING_MAPS_PREPARATION",
            "AD4Zn.dat 在 maps 准备过程中发生变化，已阻止启动 AutoGrid。",
            json.dumps(
                {
                    "expected_size_bytes": expected_size,
                    "observed_size_bytes": observed_size,
                    "expected_sha256": expected_sha256,
                    "observed_sha256": observed_sha256,
                    "strict_validation_code": str(
                        validation_error.get("code") or ""
                    ),
                    "strict_validation_message": str(
                        validation_error.get("message") or ""
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请停止同时修改参数文件，重新记录官方 AD4Zn.dat 后再生成 maps。",
        )
    if not validation.get("ok"):
        error = validation.get("error") or {}
        return _error(
            "AD4ZN_PARAMETER_SNAPSHOT_INVALID",
            "AutoGrid 即将读取的 AD4Zn.dat 未通过严格复核。",
            json.dumps(
                {
                    "validation_code": str(error.get("code") or ""),
                    "validation_message": str(error.get("message") or ""),
                    "validation_raw_error": str(
                        error.get("raw_error") or ""
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请重新记录固定上游的官方 AD4Zn.dat 后再生成 maps。",
        )
    if (
        validation.get("matches_reference_sha256") is not True
        or validation.get("canonical_sha256")
        != validation.get("reference_sha256")
    ):
        return _error(
            "AD4ZN_PARAMETER_SNAPSHOT_REFERENCE_MISMATCH",
            "AutoGrid 即将读取的 AD4Zn.dat 不是固定的受支持版本。",
            (
                f"actual={validation.get('canonical_sha256')}; "
                f"expected={validation.get('reference_sha256')}"
            ),
            "请从固定上游参考重新取得 AutoDock Vina v1.2.7 AD4Zn.dat。",
        )

    receptor_detected = read_pdbqt_atom_types(receptor_snapshot)
    if not receptor_detected.get("ok"):
        return receptor_detected
    ligand_detected = read_pdbqt_atom_types(ligand_snapshot)
    if not ligand_detected.get("ok"):
        return ligand_detected
    frozen_receptor_types = sorted(
        {
            "ZN" if str(item).strip().upper() == "ZN" else str(item).strip()
            for item in receptor_detected["atom_types"]
            if str(item).strip()
        }
    )
    frozen_ligand_types = sorted(
        {
            str(item).strip()
            for item in ligand_detected["atom_types"]
            if str(item).strip()
        }
    )
    declared_receptor_types = sorted(
        {
            "ZN" if str(item).strip().upper() == "ZN" else str(item).strip()
            for item in receptor_types
            if str(item).strip()
        }
    )
    declared_ligand_types = sorted(
        {
            str(item).strip()
            for item in ligand_types
            if str(item).strip()
        }
    )
    if (
        frozen_receptor_types != declared_receptor_types
        or frozen_ligand_types != declared_ligand_types
    ):
        return _error(
            "AD4ZN_INPUT_TYPES_CHANGED_DURING_MAPS_PREPARATION",
            "冻结输入的原子类型与生成 GPF 时的声明不一致，已阻止启动 AutoGrid。",
            json.dumps(
                {
                    "declared_receptor_atom_types": declared_receptor_types,
                    "frozen_receptor_atom_types": frozen_receptor_types,
                    "declared_ligand_atom_types": declared_ligand_types,
                    "frozen_ligand_atom_types": frozen_ligand_types,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请停止同时修改受体或配体，并重新生成 AD4Zn maps。",
        )

    parameter_atom_types = sorted(
        {
            str(item).strip().upper()
            for item in validation.get("atom_types", [])
            if str(item).strip()
        }
    )
    required_atom_types = sorted(
        {
            str(item).strip().upper()
            for item in frozen_receptor_types + frozen_ligand_types
            if str(item).strip()
        }
    )
    missing_atom_types = sorted(
        set(required_atom_types) - set(parameter_atom_types)
    )
    if missing_atom_types:
        return _error(
            "AD4ZN_PARAMETER_SNAPSHOT_ATOM_TYPES_UNCOVERED",
            "冻结的 AD4Zn.dat 未覆盖实际输入中的全部原子类型。",
            ", ".join(missing_atom_types),
            "请检查受体和配体 PDBQT 原子类型；不要用未参数化类型运行 AutoGrid。",
        )
    return {
        "ok": True,
        "parameter_validation": validation,
        "receptor_atom_types": frozen_receptor_types,
        "ligand_atom_types": frozen_ligand_types,
        "required_atom_types": required_atom_types,
        "error": None,
    }


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(part) for part in match.groups()) if match else ()


def minimum_autogrid_version(protocol_id: str) -> tuple[int, int, int]:
    """Return the minimum scientifically accepted AutoGrid version."""

    return (
        AD4ZN_MIN_AUTOGRID_VERSION
        if str(protocol_id or "").strip().lower() == AD4ZN_PROTOCOL_ID
        else AD4_MIN_AUTOGRID_VERSION
    )


def autogrid_version_supported(version: str, protocol_id: str) -> bool:
    """Check a recorded AutoGrid version against the protocol hard gate."""

    parsed = _version_tuple(version)
    return bool(parsed) and parsed >= minimum_autogrid_version(protocol_id)


def _autogrid_version_label(protocol_id: str) -> str:
    return ".".join(str(part) for part in minimum_autogrid_version(protocol_id))


def _autogrid_supports_ad4zn(version: str) -> bool:
    return autogrid_version_supported(version, AD4ZN_PROTOCOL_ID)


def _even_grid_points(size: float, spacing: float) -> int:
    points = max(2, math.ceil(float(size) / spacing))
    if points % 2:
        points += 1
    return points


def _parse_grid_points(options: dict[str, Any], box: Any, spacing: float) -> tuple[list[int] | None, dict[str, Any] | None]:
    raw = options.get("grid_points")
    explicit_axes = [
        options.get(f"grid_points_{axis}")
        for axis in ("x", "y", "z")
    ]
    derived_default = False
    if isinstance(raw, dict):
        values = [raw.get(axis) for axis in ("x", "y", "z")]
    elif isinstance(raw, list) and len(raw) == 3:
        values = raw
    elif raw in (None, "") and all(
        value in (None, "") for value in explicit_axes
    ):
        derived_default = True
        values = [
            _even_grid_points(box.size_x, spacing),
            _even_grid_points(box.size_y, spacing),
            _even_grid_points(box.size_z, spacing),
        ]
    elif raw in (None, ""):
        values = explicit_axes
    else:
        values = explicit_axes
    if derived_default and any(
        int(value) > MAX_GRID_POINTS for value in values
    ):
        return None, _error(
            "MAPS_GRID_TOO_LARGE",
            "当前 Box 与 spacing 需要超过 AutoGrid4 每轴 126 的网格点上限。",
            json.dumps(
                {
                    axis: int(value)
                    for axis, value in zip(("x", "y", "z"), values)
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请减小对应轴的 Box 尺寸或在科学上合理的前提下增大 spacing；DockStart 不会静默截断网格。",
        )
    parsed: list[int] = []
    for axis, value in zip(("X", "Y", "Z"), values):
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None, _error("MAPS_GRID_POINTS_INVALID", f"{axis} 轴 grid points 必须是偶数整数。")
        if number < 2 or number > MAX_GRID_POINTS or number % 2:
            return None, _error(
                "MAPS_GRID_POINTS_RANGE",
                f"{axis} 轴 grid points 必须是 2–{MAX_GRID_POINTS} 范围内的偶数。",
                str(value),
                "AutoGrid4 的 npts 使用每轴偶数点数；可先使用系统根据 Box 推导的默认值。",
            )
        parsed.append(number)
    return parsed, None


def _validated_spacing(value: Any) -> tuple[float | None, dict[str, Any] | None]:
    try:
        spacing = float(DEFAULT_SPACING if value in (None, "") else value)
    except (TypeError, ValueError):
        return None, _error("MAPS_SPACING_INVALID", "spacing 必须是有限数值。")
    if not math.isfinite(spacing) or spacing < 0.1 or spacing > 1.0:
        return None, _error(
            "MAPS_SPACING_RANGE",
            "spacing 必须在 0.1–1.0 Å 之间。",
            str(value),
            "标准 AutoDock4 网格建议使用 0.375 Å。",
        )
    return spacing, None


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_requested_box_grid_coverage(
    box: Any,
    points: Any,
    spacing: Any,
) -> dict[str, Any]:
    """Compute a canonical closed-interval requested-Box coverage contract.

    AutoGrid ``npts`` are axis intervals, so the effective span is exactly
    ``npts * spacing`` around the shared grid center.  A positive margin means
    the effective grid extends beyond the requested Box on that side.
    """

    axes = ("x", "y", "z")

    def box_value(kind: str, axis: str) -> Any:
        if isinstance(box, Mapping):
            nested = box.get(kind)
            if isinstance(nested, Mapping):
                return nested.get(axis)
            return box.get(f"{kind}_{axis}")
        return getattr(box, f"{kind}_{axis}")

    try:
        center = {
            axis: float(box_value("center", axis))
            for axis in axes
        }
        requested_size = {
            axis: float(box_value("size", axis))
            for axis in axes
        }
        if isinstance(points, Mapping):
            raw_points = [points.get(axis) for axis in axes]
        elif isinstance(points, (list, tuple)) and len(points) == 3:
            raw_points = list(points)
        else:
            raise ValueError("grid points 必须包含 X/Y/Z 三个轴。")
        axis_intervals: dict[str, int] = {}
        for axis, raw_value in zip(axes, raw_points):
            if isinstance(raw_value, bool):
                raise ValueError(f"{axis.upper()} 轴 grid points 不能是布尔值。")
            parsed = int(raw_value)
            if float(raw_value) != parsed:
                raise ValueError(f"{axis.upper()} 轴 grid points 必须是整数。")
            axis_intervals[axis] = parsed
        parsed_spacing = float(spacing)
    except (AttributeError, OverflowError, TypeError, ValueError) as exc:
        return _error(
            "MAPS_GRID_COVERAGE_GEOMETRY_INVALID",
            "无法读取请求 Box 或 AutoGrid 网格几何。",
            str(exc),
            "请使用有限的 Box 参数、正 spacing 和三个轴的偶数 grid points。",
        )

    numeric_values = [
        *center.values(),
        *requested_size.values(),
        parsed_spacing,
    ]
    if (
        not all(math.isfinite(value) for value in numeric_values)
        or any(value <= 0 for value in requested_size.values())
        or parsed_spacing <= 0
        or any(
            value < 2
            or value > MAX_GRID_POINTS
            or value % 2
            for value in axis_intervals.values()
        )
    ):
        return _error(
            "MAPS_GRID_COVERAGE_GEOMETRY_INVALID",
            "请求 Box 与 AutoGrid 网格几何无效。",
            json.dumps(
                {
                    "center": center,
                    "requested_size": requested_size,
                    "spacing": parsed_spacing,
                    "axis_intervals": axis_intervals,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "Box 中心必须是有限数，Box 尺寸和 spacing 必须为正数，grid points 必须是 2–126 的偶数。",
        )

    effective_size = {
        axis: axis_intervals[axis] * parsed_spacing
        for axis in axes
    }
    requested_bounds = {
        "min": {
            axis: center[axis] - requested_size[axis] / 2.0
            for axis in axes
        },
        "max": {
            axis: center[axis] + requested_size[axis] / 2.0
            for axis in axes
        },
    }
    effective_bounds = {
        "min": {
            axis: center[axis] - effective_size[axis] / 2.0
            for axis in axes
        },
        "max": {
            axis: center[axis] + effective_size[axis] / 2.0
            for axis in axes
        },
    }
    axis_coverage: dict[str, dict[str, Any]] = {}
    for axis in axes:
        lower_margin = (
            requested_bounds["min"][axis]
            - effective_bounds["min"][axis]
        )
        upper_margin = (
            effective_bounds["max"][axis]
            - requested_bounds["max"][axis]
        )
        minimum_margin = min(lower_margin, upper_margin)
        axis_coverage[axis] = {
            "lower_margin_angstrom": lower_margin,
            "upper_margin_angstrom": upper_margin,
            "minimum_margin_angstrom": minimum_margin,
            "covers_requested_interval": (
                lower_margin >= -GRID_GEOMETRY_TOLERANCE_ANGSTROM
                and upper_margin >= -GRID_GEOMETRY_TOLERANCE_ANGSTROM
            ),
        }

    coverage = {
        "method": REQUESTED_BOX_GRID_COVERAGE_METHOD,
        "interval_semantics": "closed",
        "tolerance_angstrom": GRID_GEOMETRY_TOLERANCE_ANGSTROM,
        "box_center_angstrom": center,
        "requested_box_size_angstrom": requested_size,
        "requested_box_bounds_angstrom": requested_bounds,
        "effective_grid_spacing_angstrom": parsed_spacing,
        "effective_grid_axis_intervals": axis_intervals,
        "effective_grid_size_angstrom": effective_size,
        "effective_grid_bounds_angstrom": effective_bounds,
        "axis_coverage": axis_coverage,
        "covers_requested_box": all(
            item["covers_requested_interval"]
            for item in axis_coverage.values()
        ),
    }
    coverage_sha256 = _canonical_json_sha256(coverage)
    return {
        "ok": True,
        "coverage": coverage,
        "coverage_sha256": coverage_sha256,
        "canonical_sha256": coverage_sha256,
        "error": None,
    }


def _read_ad4zn_marker_coordinates(path: Path) -> dict[str, Any]:
    """Read the exact ZN/TZ markers from the PDBQT bytes AutoGrid will use."""

    markers: dict[str, list[dict[str, Any]]] = {"ZN": [], "TZ": []}
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                digest.update(raw_line)
                size_bytes += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace")
                if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
                    continue
                parts = line.split()
                atom_type = (
                    _canonical_atom_type(parts[-1])
                    if parts
                    else ""
                )
                marker_type = (
                    "ZN"
                    if atom_type == "Zn"
                    else "TZ"
                    if atom_type == "TZ"
                    else ""
                )
                if not marker_type:
                    continue
                try:
                    coordinate = {
                        "x": float(line[30:38]),
                        "y": float(line[38:46]),
                        "z": float(line[46:54]),
                    }
                except (TypeError, ValueError) as exc:
                    return _error(
                        "AD4ZN_ZN_TZ_COORDINATE_INVALID",
                        "AD4Zn TZ 受体中的 ZN/TZ 坐标无法解析。",
                        (
                            f"path={path}; line={line_number}; "
                            f"atom_type={marker_type}; error={exc}"
                        ),
                        "请重新生成包含标准定宽三维坐标的 TZ 受体。",
                    )
                if not all(
                    math.isfinite(value)
                    for value in coordinate.values()
                ):
                    return _error(
                        "AD4ZN_ZN_TZ_COORDINATE_INVALID",
                        "AD4Zn TZ 受体中的 ZN/TZ 坐标必须是有限数。",
                        (
                            f"path={path}; line={line_number}; "
                            f"atom_type={marker_type}; "
                            f"coordinate={coordinate}"
                        ),
                        "请重新生成 TZ 受体。",
                    )
                markers[marker_type].append(
                    {
                        "atom_type": marker_type,
                        "line_number": line_number,
                        "serial": line[6:11].strip(),
                        "atom_name": line[12:16].strip(),
                        "coordinate_angstrom": coordinate,
                    }
                )
    except OSError as exc:
        return _error(
            "AD4ZN_ZN_TZ_COORDINATE_READ_ERROR",
            "无法读取 AD4Zn TZ 受体中的 ZN/TZ 坐标。",
            str(exc),
            "请确认 TZ 受体仍存在且可读。",
        )

    counts = {key: len(values) for key, values in markers.items()}
    if counts != {"ZN": 1, "TZ": 1}:
        return _error(
            "AD4ZN_ZN_TZ_COUNT_INVALID",
            "AD4Zn maps 输入必须恰好包含一个 ZN 和一个 TZ。",
            json.dumps(
                {
                    "path": str(path),
                    "counts": counts,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请重新生成单 Zn 位点的 TZ 受体后再生成 maps。",
        )
    return {
        "ok": True,
        "receptor": {
            "size_bytes": size_bytes,
            "sha256": digest.hexdigest(),
        },
        "markers": {
            key: values[0]
            for key, values in markers.items()
        },
        "error": None,
    }


def compute_ad4zn_box_coverage(
    receptor_path: Path,
    box: Any,
    points: list[int],
    spacing: float,
) -> dict[str, Any]:
    """Build a reproducible closed-interval ZN/TZ coverage audit."""

    marker_result = _read_ad4zn_marker_coordinates(receptor_path)
    if not marker_result.get("ok"):
        return marker_result
    axes = ("x", "y", "z")
    try:
        center = {
            axis: float(
                box.get(f"center_{axis}")
                if isinstance(box, dict)
                else getattr(box, f"center_{axis}")
            )
            for axis in axes
        }
        requested_size = {
            axis: float(
                box.get(f"size_{axis}")
                if isinstance(box, dict)
                else getattr(box, f"size_{axis}")
            )
            for axis in axes
        }
        raw_axis_intervals = {
            axis: value
            for axis, value in zip(axes, points)
        }
        axis_intervals = {
            axis: int(value)
            for axis, value in raw_axis_intervals.items()
        }
        parsed_spacing = float(spacing)
    except (AttributeError, TypeError, ValueError) as exc:
        return _error(
            "AD4ZN_BOX_COVERAGE_GEOMETRY_INVALID",
            "无法读取 AD4Zn 的 Box 或 AutoGrid 网格几何。",
            str(exc),
            "请重新设置 Box、spacing 和三个轴的 grid points。",
        )
    numeric_values = [
        *center.values(),
        *requested_size.values(),
        parsed_spacing,
    ]
    if (
        len(points) != 3
        or not all(math.isfinite(value) for value in numeric_values)
        or any(value <= 0 for value in requested_size.values())
        or parsed_spacing <= 0
        or any(
            value < 2
            or value > MAX_GRID_POINTS
            or value % 2
            for value in axis_intervals.values()
        )
        or any(
            isinstance(raw_axis_intervals[axis], bool)
            or float(raw_axis_intervals[axis])
            != axis_intervals[axis]
            for axis in axes
        )
    ):
        return _error(
            "AD4ZN_BOX_COVERAGE_GEOMETRY_INVALID",
            "AD4Zn 的 Box 与 AutoGrid 网格几何无效。",
            json.dumps(
                {
                    "center": center,
                    "requested_size": requested_size,
                    "spacing": parsed_spacing,
                    "axis_intervals": axis_intervals,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "请使用有限的 Box 参数、正 spacing 和合法的偶数 grid points。",
        )

    effective_size = {
        axis: axis_intervals[axis] * parsed_spacing
        for axis in axes
    }
    requested_bounds = {
        "min": {
            axis: center[axis] - requested_size[axis] / 2.0
            for axis in axes
        },
        "max": {
            axis: center[axis] + requested_size[axis] / 2.0
            for axis in axes
        },
    }
    effective_bounds = {
        "min": {
            axis: center[axis] - effective_size[axis] / 2.0
            for axis in axes
        },
        "max": {
            axis: center[axis] + effective_size[axis] / 2.0
            for axis in axes
        },
    }
    marker_audit: dict[str, dict[str, Any]] = {}
    for marker_type in ("ZN", "TZ"):
        marker = copy.deepcopy(marker_result["markers"][marker_type])
        coordinate = marker["coordinate_angstrom"]
        requested_margin = {
            axis: min(
                coordinate[axis] - requested_bounds["min"][axis],
                requested_bounds["max"][axis] - coordinate[axis],
            )
            for axis in axes
        }
        effective_margin = {
            axis: min(
                coordinate[axis] - effective_bounds["min"][axis],
                effective_bounds["max"][axis] - coordinate[axis],
            )
            for axis in axes
        }
        marker.update(
            {
                "requested_box_margin_angstrom": requested_margin,
                "effective_grid_margin_angstrom": effective_margin,
                "inside_requested_box": all(
                    value >= -BOX_COVERAGE_EPSILON_ANGSTROM
                    for value in requested_margin.values()
                ),
                "inside_effective_grid": all(
                    value >= -BOX_COVERAGE_EPSILON_ANGSTROM
                    for value in effective_margin.values()
                ),
            }
        )
        marker_audit[marker_type] = marker

    coverage = {
        "method": AD4ZN_BOX_COVERAGE_METHOD,
        "interval_semantics": "closed",
        "epsilon_angstrom": BOX_COVERAGE_EPSILON_ANGSTROM,
        "receptor": copy.deepcopy(marker_result["receptor"]),
        "box_center_angstrom": center,
        "requested_box_size_angstrom": requested_size,
        "requested_box_bounds_angstrom": requested_bounds,
        "effective_grid_spacing_angstrom": parsed_spacing,
        "effective_grid_axis_intervals": axis_intervals,
        "effective_grid_size_angstrom": effective_size,
        "effective_grid_bounds_angstrom": effective_bounds,
        "markers": marker_audit,
        "all_zn_tz_inside_requested_box": all(
            marker["inside_requested_box"]
            for marker in marker_audit.values()
        ),
        "all_zn_tz_inside_effective_grid": all(
            marker["inside_effective_grid"]
            for marker in marker_audit.values()
        ),
    }
    return {
        "ok": True,
        "box_coverage": coverage,
        "box_coverage_sha256": _canonical_json_sha256(coverage),
        "error": None,
    }


def _next_map_set_id(root: Path, *, protocol_id: str = "ad4_maps") -> str:
    maps_dir = root / "maps"
    pattern = (
        AD4ZN_MAP_SET_ID_PATTERN
        if protocol_id == AD4ZN_PROTOCOL_ID
        else HYDRATED_MAP_SET_ID_PATTERN
        if protocol_id == HYDRATED_PROTOCOL_ID
        else MAP_SET_ID_PATTERN
    )
    prefix = (
        "ad4zn"
        if protocol_id == AD4ZN_PROTOCOL_ID
        else "hydrated"
        if protocol_id == HYDRATED_PROTOCOL_ID
        else "ad4"
    )
    numbers = [
        int(match.group(1))
        for child in maps_dir.iterdir()
        if child.is_dir() and (match := pattern.match(child.name))
    ] if maps_dir.is_dir() else []
    return f"{prefix}_{max(numbers, default=0) + 1:03d}"


def _active_protocol(project: Any) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    if not isinstance(protocol, dict):
        return "vina"
    return "ad4_maps" if str(protocol.get("engine") or "").lower() == "ad4_maps" else "vina"


def _active_protocol_id(project: Any) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    if not isinstance(protocol, dict):
        return "rigid_single"
    protocol_id = str(protocol.get("protocol_id") or "").strip().lower()
    if (
        str(protocol.get("engine") or "").strip().lower() == "ad4_maps"
        and protocol_id == AD4ZN_PROTOCOL_ID
    ):
        return AD4ZN_PROTOCOL_ID
    if str(protocol.get("engine") or "").strip().lower() == "ad4_maps":
        return "ad4_maps"
    return protocol_id or "rigid_single"


def set_scoring_protocol(project_dir: str, protocol: str) -> dict[str, Any]:
    normalized = str(protocol or "").strip().lower()
    if normalized not in {"vina", "ad4_maps", AD4ZN_PROTOCOL_ID}:
        return _error(
            "PROTOCOL_SCORING_INVALID",
            "对接评分协议只支持 Vina/Vinardo、标准 AutoDock4 maps 或 AD4Zn beta。",
        )
    project, _, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None
    current = project.preserved_data.get("docking_protocol")
    docking_protocol = copy.deepcopy(current) if isinstance(current, dict) else {}
    docking_protocol["engine"] = (
        "ad4_maps" if normalized in {"ad4_maps", AD4ZN_PROTOCOL_ID} else "vina"
    )
    docking_protocol["protocol_id"] = (
        normalized if normalized in {"ad4_maps", AD4ZN_PROTOCOL_ID} else "rigid_single"
    )
    if normalized in {"ad4_maps", AD4ZN_PROTOCOL_ID}:
        docking_protocol["autobox"] = False
        docking_protocol["run_mode"] = "dock"
        docking_protocol["receptor_mode"] = "rigid"
    if normalized == AD4ZN_PROTOCOL_ID:
        docking_protocol["stability"] = "beta"
    else:
        docking_protocol.pop("stability", None)
    docking_protocol.setdefault("receptor_mode", str(docking_protocol.get("mode") or "rigid"))
    project.preserved_data["docking_protocol"] = docking_protocol
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "protocol": normalized,
        "message": (
            "已切换到 AD4Zn beta；仅支持 Zn、刚性受体、单配体全局对接。"
            if normalized == AD4ZN_PROTOCOL_ID
            else "已切换到 AutoDock4 (maps) 协议。"
            if normalized == "ad4_maps"
            else "已切换到标准 Vina/Vinardo 协议。"
        ),
        "error": None,
    }


def get_maps_defaults(project_dir: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    protocol_id = _active_protocol_id(project)
    ad4zn_status: dict[str, Any] | None = None
    if protocol_id == AD4ZN_PROTOCOL_ID:
        from dockstart_core.ad4zn import get_status as get_ad4zn_status

        ad4zn_status = get_ad4zn_status(project_dir)
        prepared = (
            ad4zn_status.get("prepared_receptor")
            if isinstance(ad4zn_status.get("prepared_receptor"), dict)
            else {}
        )
        prepared_relative = str(prepared.get("relative_path") or "")
        receptor_path = _contained_project_path(root, prepared_relative)
        if (
            receptor_path is None
            or not receptor_path.is_file()
            or receptor_path.stat().st_size <= 0
        ):
            receptor_path = None
    else:
        receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
        if receptor_error:
            return receptor_error
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error:
        return ligand_error
    assert ligand_path is not None
    receptor_types = (
        read_pdbqt_atom_types(receptor_path)
        if receptor_path is not None
        else {"ok": True, "atom_types": []}
    )
    if not receptor_types.get("ok"):
        return receptor_types
    ligand_types = read_pdbqt_atom_types(ligand_path)
    if not ligand_types.get("ok"):
        return ligand_types
    if protocol_id == AD4ZN_PROTOCOL_ID and receptor_path is not None:
        _, receptor_type_error = _validated_ad4zn_receptor_types(
            None,
            receptor_types["atom_types"],
        )
        if receptor_type_error:
            return receptor_type_error
    elif protocol_id != AD4ZN_PROTOCOL_ID:
        _, receptor_type_error = _validated_type_list(
            None,
            receptor_types["atom_types"],
            role="受体",
        )
        if receptor_type_error:
            return receptor_type_error
    _, ligand_type_error = _validated_type_list(None, ligand_types["atom_types"], role="配体")
    if ligand_type_error:
        return ligand_type_error
    spacing = DEFAULT_SPACING
    points, points_error = _parse_grid_points({}, project.box, spacing)
    if points_error:
        return points_error
    assert points is not None
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol": (
            AD4ZN_PROTOCOL_ID
            if protocol_id == AD4ZN_PROTOCOL_ID
            else _active_protocol(project)
        ),
        "protocol_id": protocol_id,
        "ad4zn": ad4zn_status,
        "defaults": {
            "spacing": spacing,
            "grid_points": {"x": points[0], "y": points[1], "z": points[2]},
            "center": {
                "x": project.box.center_x,
                "y": project.box.center_y,
                "z": project.box.center_z,
            },
            "actual_size": {
                "x": round(points[0] * spacing, 6),
                "y": round(points[1] * spacing, 6),
                "z": round(points[2] * spacing, 6),
            },
            "receptor_atom_types": receptor_types["atom_types"],
            "ligand_atom_types": ligand_types["atom_types"],
            "parameter_file": (
                str(
                    (
                        ad4zn_status.get("parameter_file")
                        if isinstance(ad4zn_status, dict)
                        and isinstance(ad4zn_status.get("parameter_file"), dict)
                        else {}
                    ).get("relative_path")
                    or ""
                )
                if protocol_id == AD4ZN_PROTOCOL_ID
                else ""
            ),
        },
        "message": "已根据当前 PDBQT 与 Box 生成 AutoGrid4 默认参数。",
        "error": None,
    }


def _gpf_text(
    *,
    center: list[float],
    points: list[int],
    spacing: float,
    receptor_types: list[str],
    ligand_types: list[str],
    parameter_file: str,
    receptor_file: str = "inputs/receptor.pdbqt",
    protocol_id: str = "ad4_maps",
) -> str:
    lines = []
    if parameter_file:
        lines.append(f"parameter_file {Path(parameter_file).as_posix()}")
    lines.extend(
        [
            f"npts {points[0]} {points[1]} {points[2]}",
            "gridfld receptor.maps.fld",
            f"spacing {spacing:g}",
            f"receptor_types {' '.join(receptor_types)}",
            f"ligand_types {' '.join(ligand_types)}",
            f"receptor {Path(receptor_file).as_posix()}",
            f"gridcenter {center[0]:g} {center[1]:g} {center[2]:g}",
            "smooth 0.500",
        ],
    )
    lines.extend(f"map receptor.{atom_type}.map" for atom_type in ligand_types)
    lines.extend(["elecmap receptor.e.map", "dsolvmap receptor.d.map"])
    if protocol_id == AD4ZN_PROTOCOL_ID:
        lines.append("dielectric -0.1465")
        lines.extend(AD4ZN_NBP_R_EPS)
    else:
        lines.append("dielectric -42.000")
    return "\n".join(lines) + "\n"


def _required_map_names(prefix: str, ligand_types: list[str]) -> list[str]:
    return [
        f"{prefix}.maps.fld",
        *(f"{prefix}.{atom_type}.map" for atom_type in ligand_types),
        f"{prefix}.e.map",
        f"{prefix}.d.map",
    ]


def _parse_autogrid_log(log_text: str) -> dict[str, Any]:
    error_lines = [
        line.strip()
        for line in log_text.splitlines()
        if re.search(r"\b(?:ERROR|FATAL)\b", line, re.IGNORECASE)
    ]
    return {
        "error_lines": error_lines[:40],
        "has_error": bool(error_lines),
        "successful_completion": "successful completion" in log_text.lower(),
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _activate_map_set(project: Any, manifest_relative: str, manifest: dict[str, Any]) -> dict[str, Any]:
    protocol_id = str(manifest.get("protocol_id") or "ad4_maps")
    state_key = "ad4zn" if protocol_id == AD4ZN_PROTOCOL_ID else "ad4_maps"
    current_state = project.preserved_data.get(state_key)
    state = copy.deepcopy(current_state) if isinstance(current_state, dict) else {}
    state.update(
        {
            "active_manifest": Path(manifest_relative).as_posix(),
            "map_set_id": manifest["map_set_id"],
            "status": manifest["status"],
            "updated_at": _now_iso(),
            **(
                {
                    "box_coverage_sha256": str(
                        (
                            manifest.get("ad4zn")
                            if isinstance(manifest.get("ad4zn"), dict)
                            else {}
                        ).get("box_coverage_sha256")
                        or ""
                    )
                }
                if protocol_id == AD4ZN_PROTOCOL_ID
                else {}
            ),
        }
    )
    project.preserved_data[state_key] = state
    docking_protocol = project.preserved_data.get("docking_protocol")
    protocol = copy.deepcopy(docking_protocol) if isinstance(docking_protocol, dict) else {}
    protocol.update(
        {
            "engine": "ad4_maps",
            "protocol_id": protocol_id,
            "autobox": False,
            "run_mode": "dock",
            "receptor_mode": "rigid",
        }
    )
    if protocol_id == AD4ZN_PROTOCOL_ID:
        protocol["stability"] = "beta"
    else:
        protocol.pop("stability", None)
    protocol.setdefault("receptor_mode", str(protocol.get("mode") or "rigid"))
    project.preserved_data["docking_protocol"] = protocol
    grid = manifest["grid"]
    project.box = BoxSettings(
        center_x=float(grid["center"]["x"]),
        center_y=float(grid["center"]["y"]),
        center_z=float(grid["center"]["z"]),
        size_x=float(grid["actual_size"]["x"]),
        size_y=float(grid["actual_size"]["y"]),
        size_z=float(grid["actual_size"]["z"]),
    )
    return save_project(project)


def generate_maps(
    project_dir: str,
    options: dict[str, Any] | None = None,
    *,
    runner: Callable[..., dict[str, Any]] | None = None,
    _profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options if isinstance(options, dict) else {}
    profile = _profile if isinstance(_profile, dict) else {}
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    profile_protocol_id = str(profile.get("protocol_id") or "").strip().lower()
    if profile_protocol_id and profile_protocol_id != HYDRATED_PROTOCOL_ID:
        return _error(
            "MAPS_INTERNAL_PROFILE_INVALID",
            "AutoGrid maps 内部协议配置无效。",
            profile_protocol_id,
        )
    protocol_id = profile_protocol_id or _active_protocol_id(project)
    if protocol_id not in {AD4ZN_PROTOCOL_ID, HYDRATED_PROTOCOL_ID}:
        protocol_id = "ad4_maps"
    is_ad4zn = protocol_id == AD4ZN_PROTOCOL_ID
    is_hydrated = protocol_id == HYDRATED_PROTOCOL_ID
    docking_protocol = project.preserved_data.get("docking_protocol")
    receptor_mode = (
        "rigid"
        if is_hydrated
        else str(docking_protocol.get("receptor_mode") or docking_protocol.get("mode") or "rigid")
        if isinstance(docking_protocol, dict)
        else "rigid"
    )
    if receptor_mode == "flexible":
        return _error(
            "MAPS_FLEXIBLE_RECEPTOR_UNSUPPORTED",
            "AutoDock4 (maps) 的 v0.12.0 基准仅开放刚性受体。",
            suggestion="请先切换到刚性受体；柔性 AD4 maps 需单独科学回归后再开放。",
        )
    ad4zn_status: dict[str, Any] | None = None
    original_receptor_path: Path | None = None
    if is_ad4zn:
        from dockstart_core.ad4zn import get_status as get_ad4zn_status

        ad4zn_status = get_ad4zn_status(project_dir)
        if not ad4zn_status.get("ok") or not ad4zn_status.get("preparation_ready"):
            error = ad4zn_status.get("error") or {}
            return _error(
                str(error.get("code") or "AD4ZN_PREPARATION_INCOMPLETE"),
                str(
                    error.get("message")
                    or "AD4Zn 的 Zn 复核、TZ 受体或参数文件尚未准备完成。"
                ),
                str(
                    error.get("raw_error")
                    or _issue_messages(ad4zn_status.get("issues"))
                ),
                str(
                    error.get("suggestion")
                    or "请依次完成 Zn 位点复核、TZ 受体生成和 AD4Zn.dat 校验。"
                ),
            )
        prepared = (
            ad4zn_status.get("prepared_receptor")
            if isinstance(ad4zn_status.get("prepared_receptor"), dict)
            else {}
        )
        receptor_relative = str(prepared.get("relative_path") or "")
        receptor_path = _contained_project_path(root, receptor_relative)
        if (
            receptor_path is None
            or not receptor_path.is_file()
            or receptor_path.stat().st_size <= 0
        ):
            return _error(
                "AD4ZN_TZ_RECEPTOR_MISSING",
                "AD4Zn TZ 受体不存在或路径不安全。",
                receptor_relative,
                "请重新生成 TZ 受体。",
            )
        original_receptor_path, original_receptor_error = _project_file(
            root,
            project.receptor.file,
            "受体",
        )
        if original_receptor_error:
            return original_receptor_error
    else:
        receptor_relative = Path(project.receptor.file).as_posix()
        receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
        if receptor_error:
            return receptor_error
        original_receptor_path = receptor_path
    ligand_relative = (
        str(profile.get("ligand_file") or "").strip()
        if is_hydrated
        else str(project.ligand.file or "")
    )
    ligand_path, ligand_error = _project_file(root, ligand_relative, "配体")
    if ligand_error:
        return ligand_error
    assert receptor_path is not None and ligand_path is not None

    receptor_detected = read_pdbqt_atom_types(receptor_path)
    ligand_detected = read_pdbqt_atom_types(ligand_path)
    if not receptor_detected.get("ok"):
        return receptor_detected
    if not ligand_detected.get("ok"):
        return ligand_detected
    receptor_types, type_error = (
        _validated_ad4zn_receptor_types(
            None,
            receptor_detected["atom_types"],
        )
        if is_ad4zn
        else _validated_type_list(
            options.get("receptor_atom_types"),
            receptor_detected["atom_types"],
            role="受体",
        )
    )
    if type_error:
        return type_error
    detected_ligand_types = list(ligand_detected["atom_types"])
    if is_hydrated:
        if "W" not in detected_ligand_types:
            return _error(
                "HYDRATED_LIGAND_W_TYPE_MISSING",
                "水合配体 PDBQT 中没有 W 类型伪水原子。",
                suggestion="请重新完成水合配体准备后再生成 maps。",
            )
        detected_ligand_types = [
            atom_type for atom_type in detected_ligand_types if atom_type != "W"
        ]
    ligand_types, type_error = _validated_type_list(
        options.get("ligand_atom_types"),
        detected_ligand_types,
        role="配体",
    )
    if type_error:
        return type_error
    assert receptor_types is not None and ligand_types is not None
    if is_hydrated:
        ligand_types = sorted(set(ligand_types) | {"HD", "OA"})
    spacing, spacing_error = _validated_spacing(options.get("spacing"))
    if spacing_error:
        return spacing_error
    assert spacing is not None
    points, points_error = _parse_grid_points(options, project.box, spacing)
    if points_error:
        return points_error
    assert points is not None
    requested_grid_coverage: dict[str, Any] | None = None
    requested_grid_coverage_sha256 = ""
    if is_hydrated:
        coverage_result = compute_requested_box_grid_coverage(
            project.box,
            points,
            spacing,
        )
        if not coverage_result.get("ok"):
            return coverage_result
        requested_grid_coverage = coverage_result["coverage"]
        requested_grid_coverage_sha256 = str(
            coverage_result["coverage_sha256"]
        )
        if not requested_grid_coverage["covers_requested_box"]:
            uncovered_axes = [
                axis.upper()
                for axis, record in requested_grid_coverage[
                    "axis_coverage"
                ].items()
                if not record["covers_requested_interval"]
            ]
            return _error(
                "HYDRATED_GRID_UNDER_COVERS_REQUESTED_BOX",
                "实际 AutoGrid 网格没有完整覆盖当前请求的对接 Box，已阻止生成水合 maps。",
                json.dumps(
                    {
                        "uncovered_axes": uncovered_axes,
                        "coverage": requested_grid_coverage,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "请增大对应轴的 grid points 或 spacing，直到 npts × spacing 覆盖整个请求 Box。",
            )
    box_coverage: dict[str, Any] | None = None
    box_coverage_sha256 = ""
    ad4zn_source_receptor_sha256 = ""
    if is_ad4zn:
        coverage_result = compute_ad4zn_box_coverage(
            receptor_path,
            project.box,
            points,
            spacing,
        )
        if not coverage_result.get("ok"):
            return coverage_result
        box_coverage = coverage_result["box_coverage"]
        box_coverage_sha256 = str(
            coverage_result["box_coverage_sha256"]
        )
        ad4zn_source_receptor_sha256 = str(
            box_coverage["receptor"]["sha256"]
        )
        if not box_coverage["all_zn_tz_inside_requested_box"]:
            outside = [
                marker_type
                for marker_type, marker in box_coverage["markers"].items()
                if not marker["inside_requested_box"]
            ]
            return _error(
                "AD4ZN_ZN_TZ_OUTSIDE_REQUESTED_BOX",
                "ZN/TZ 未同时位于当前请求的对接 Box 内，已阻止生成 maps。",
                json.dumps(
                    {
                        "outside_markers": outside,
                        "box_coverage": box_coverage,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "请调整 Box，使 ZN 与 TZ 均位于三个轴的闭区间内。",
            )
        if not box_coverage["all_zn_tz_inside_effective_grid"]:
            outside = [
                marker_type
                for marker_type, marker in box_coverage["markers"].items()
                if not marker["inside_effective_grid"]
            ]
            return _error(
                "AD4ZN_ZN_TZ_OUTSIDE_EFFECTIVE_GRID",
                "ZN/TZ 未同时位于 npts × spacing 形成的实际 AutoGrid 网格内，已阻止生成 maps。",
                json.dumps(
                    {
                        "outside_markers": outside,
                        "box_coverage": box_coverage,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "请增大对应轴的 grid points 或 spacing，并确认实际网格仍覆盖目标口袋。",
            )

    parameter_source: Path | None = None
    parameter_value = (
        str(
            (
                ad4zn_status.get("parameter_file")
                if isinstance(ad4zn_status, dict)
                and isinstance(ad4zn_status.get("parameter_file"), dict)
                else {}
            ).get("relative_path")
            or ""
        )
        if is_ad4zn
        else str(options.get("parameter_file") or "").strip()
    )
    if is_hydrated and parameter_value:
        return _error(
            "HYDRATED_MAPS_PARAMETER_FILE_UNSUPPORTED",
            "水合对接首版不接受自定义 AutoGrid 参数文件。",
            suggestion="请清除 parameter_file，并使用固定的标准 AD4 maps 参数。",
        )
    if parameter_value:
        parameter_source = (
            _contained_project_path(root, parameter_value)
            if is_ad4zn
            else Path(parameter_value).expanduser().resolve()
        )
        if parameter_source is None:
            return _error(
                "AD4ZN_PARAMETER_PATH_INVALID",
                "AD4Zn 参数文件路径不在当前项目内。",
                parameter_value,
            )
        if not parameter_source.is_file() or parameter_source.stat().st_size <= 0:
            return _error(
                "MAPS_PARAMETER_FILE_MISSING",
                "指定的 AutoGrid 参数文件不存在或为空。",
                str(parameter_source),
            )
        if parameter_source.stat().st_size > MAX_PARAMETER_FILE_BYTES:
            return _error("MAPS_PARAMETER_FILE_TOO_LARGE", "AutoGrid 参数文件超过 5 MB，已拒绝导入。")

    if is_ad4zn and parameter_source is None:
        return _error(
            "AD4ZN_PARAMETER_FILE_REQUIRED",
            "AD4Zn maps 必须使用已校验的 AD4Zn.dat。",
            suggestion="请选择官方 AD4Zn.dat，DockStart 会复制并冻结到项目中。",
        )

    settings = load_settings()
    detection = autogrid_adapter.detect(settings.tool_paths.autogrid4)
    if detection.status != "ok" or not detection.path:
        return _error(
            "AUTOGRID_NOT_AVAILABLE",
            detection.message or "未检测到 AutoGrid4。",
            detection.raw_error,
            "请在工具路径设置中配置外部 autogrid4.exe；DockStart 不会在安装包中内置 GPL 工具。",
        )
    if not autogrid_version_supported(detection.version, protocol_id):
        minimum_version = _autogrid_version_label(protocol_id)
        error_code = (
            "AD4ZN_AUTOGRID_VERSION_UNSUPPORTED"
            if is_ad4zn
            else "HYDRATED_AUTOGRID_VERSION_UNSUPPORTED"
            if is_hydrated
            else "AUTOGRID_VERSION_UNSUPPORTED"
        )
        protocol_label = (
            "AD4Zn beta"
            if is_ad4zn
            else "实验性水合 AD4"
            if is_hydrated
            else "标准 AutoDock4 maps"
        )
        return _error(
            error_code,
            f"{protocol_label}需要 AutoGrid {minimum_version} 或更高版本。",
            (
                f"detected={detection.version or 'unknown'}; "
                f"required>={minimum_version}"
            ),
            (
                "官方说明 4.2.6 与 4.2.7 的 nbp_r_eps 行为不同；"
                "请配置 ADFR Suite 提供的 AutoGrid 4.2.7.x。"
                if is_ad4zn
                else f"请配置可明确识别为 {minimum_version} 或更高版本的 AutoGrid4。"
            ),
        )

    map_set_id = _next_map_set_id(root, protocol_id=protocol_id)
    set_dir = root / "maps" / map_set_id
    inputs_dir = set_dir / "inputs"
    manifest_relative = Path("maps", map_set_id, "manifest.json").as_posix()
    created_at = _now_iso()
    try:
        inputs_dir.mkdir(parents=True, exist_ok=False)
        receptor_snapshot = inputs_dir / (
            "receptor_tz.pdbqt" if is_ad4zn else "receptor.pdbqt"
        )
        ligand_snapshot = inputs_dir / "ligand.pdbqt"
        shutil.copyfile(receptor_path, receptor_snapshot)
        shutil.copyfile(ligand_path, ligand_snapshot)
        if is_ad4zn:
            snapshot_coverage_result = compute_ad4zn_box_coverage(
                receptor_snapshot,
                project.box,
                points,
                spacing,
            )
            snapshot_coverage_sha256 = str(
                snapshot_coverage_result.get("box_coverage_sha256")
                or ""
            )
            if (
                not snapshot_coverage_result.get("ok")
                or snapshot_coverage_sha256 != box_coverage_sha256
            ):
                error = (
                    snapshot_coverage_result.get("error")
                    if isinstance(
                        snapshot_coverage_result.get("error"),
                        dict,
                    )
                    else {}
                )
                failure = _error(
                    "AD4ZN_TZ_RECEPTOR_CHANGED_DURING_MAPS_PREPARATION",
                    "复制 TZ 受体时 ZN/TZ 坐标或文件字节发生变化，已阻止启动 AutoGrid。",
                    (
                        str(error.get("raw_error") or "")
                        + (
                            (
                                f"; source_coverage={box_coverage_sha256}; "
                                "snapshot_coverage="
                                f"{snapshot_coverage_sha256 or 'invalid'}"
                            )
                            if box_coverage_sha256
                            != snapshot_coverage_sha256
                            else ""
                        )
                    ).strip("; "),
                    "请停止同时修改受体文件，重新生成 TZ 受体后再生成 maps。",
                )
                _write_manifest(
                    set_dir / "manifest.json",
                    {
                        "schema_version": 1,
                        "map_set_id": map_set_id,
                        "protocol_id": AD4ZN_PROTOCOL_ID,
                        "source": "generated",
                        "status": "failed",
                        "created_at": created_at,
                        "finished_at": _now_iso(),
                        "error": copy.deepcopy(failure["error"]),
                    },
                )
                return failure | {
                    "map_set_id": map_set_id,
                    "manifest_file": manifest_relative,
                }
            box_coverage = snapshot_coverage_result["box_coverage"]
            box_coverage_sha256 = snapshot_coverage_sha256
        parameter_relative = ""
        parameter_snapshot: dict[str, Any] | None = None
        parameter_target: Path | None = None
        if parameter_source is not None:
            parameter_target = inputs_dir / (
                "AD4Zn.dat" if is_ad4zn else "parameter_file.dat"
            )
            shutil.copyfile(parameter_source, parameter_target)
            parameter_relative = Path("inputs", parameter_target.name).as_posix()
            parameter_snapshot = {
                **_snapshot(parameter_target, Path("maps", map_set_id, parameter_relative).as_posix()),
                "source_path": str(parameter_source),
            }
        center = [project.box.center_x, project.box.center_y, project.box.center_z]
        actual_size = [round(value * spacing, 6) for value in points]
        gpf_path = set_dir / "receptor.gpf"
        atomic_write_text(
            gpf_path,
            _gpf_text(
                center=center,
                points=points,
                spacing=spacing,
                receptor_types=receptor_types,
                ligand_types=ligand_types,
                parameter_file=parameter_relative,
                receptor_file=Path("inputs", receptor_snapshot.name).as_posix(),
                protocol_id=protocol_id,
            ),
        )
        if is_ad4zn:
            assert parameter_target is not None
            recorded_parameter = (
                ad4zn_status.get("parameter_file")
                if isinstance(ad4zn_status, dict)
                and isinstance(ad4zn_status.get("parameter_file"), dict)
                else {}
            )
            parameter_revalidation = _validate_ad4zn_parameter_snapshot(
                parameter_target,
                recorded_parameter=recorded_parameter,
                receptor_snapshot=receptor_snapshot,
                ligand_snapshot=ligand_snapshot,
                receptor_types=receptor_types,
                ligand_types=ligand_types,
            )
            if not parameter_revalidation.get("ok"):
                failure = parameter_revalidation
                failure_error = (
                    failure.get("error")
                    if isinstance(failure.get("error"), dict)
                    else {}
                )
                failed_manifest = {
                    "schema_version": 1,
                    "map_set_id": map_set_id,
                    "protocol_id": AD4ZN_PROTOCOL_ID,
                    "stability": "beta",
                    "source": "generated",
                    "status": "failed",
                    "created_at": created_at,
                    "finished_at": _now_iso(),
                    "receptor": _snapshot(
                        receptor_snapshot,
                        Path(
                            "maps",
                            map_set_id,
                            "inputs",
                            receptor_snapshot.name,
                        ).as_posix(),
                    ),
                    "ligand": _snapshot(
                        ligand_snapshot,
                        Path(
                            "maps",
                            map_set_id,
                            "inputs",
                            ligand_snapshot.name,
                        ).as_posix(),
                    ),
                    "parameter_file": parameter_snapshot,
                    "gpf": _snapshot(
                        gpf_path,
                        Path(
                            "maps",
                            map_set_id,
                            "receptor.gpf",
                        ).as_posix(),
                    ),
                    "validation": {
                        "complete": False,
                        "missing_files": [],
                        "issues": [
                            str(
                                failure_error.get("message")
                                or failure_error.get("code")
                                or "AD4Zn.dat 快照复核失败。"
                            )
                        ],
                    },
                    "error": copy.deepcopy(failure_error),
                }
                _write_manifest(set_dir / "manifest.json", failed_manifest)
                return failure | {
                    "map_set_id": map_set_id,
                    "manifest_file": manifest_relative,
                    "manifest": failed_manifest,
                }
            strict_parameter = parameter_revalidation[
                "parameter_validation"
            ]
            assert parameter_snapshot is not None
            parameter_snapshot.update(
                {
                    "canonical_sha256": strict_parameter[
                        "canonical_sha256"
                    ],
                    "reference_sha256": strict_parameter[
                        "reference_sha256"
                    ],
                    "reference_hash_basis": strict_parameter[
                        "reference_hash_basis"
                    ],
                    "matches_reference_sha256": True,
                    "supported_profile_id": strict_parameter[
                        "supported_profile_id"
                    ],
                    "license_id": strict_parameter["license_id"],
                    "atom_types": copy.deepcopy(
                        strict_parameter["atom_types"]
                    ),
                    "atom_type_table_sha256": strict_parameter[
                        "atom_type_table_sha256"
                    ],
                    "required_atom_types": copy.deepcopy(
                        parameter_revalidation["required_atom_types"]
                    ),
                }
            )
        run_impl = runner or autogrid_adapter.run
        try:
            result = run_impl(
                detection.path,
                gpf_path.name,
                "autogrid.glg",
                set_dir,
                timeout_seconds=1800,
            )
        except KeyboardInterrupt as exc:
            interrupted_manifest = {
                "schema_version": 1,
                "map_set_id": map_set_id,
                "protocol_id": protocol_id,
                "stability": (
                    "beta"
                    if is_ad4zn
                    else "experimental"
                    if is_hydrated
                    else "stable"
                ),
                "source": "generated",
                "status": "interrupted",
                "created_at": created_at,
                "finished_at": _now_iso(),
                "receptor": {
                    **_snapshot(
                        receptor_snapshot,
                        Path(
                            "maps",
                            map_set_id,
                            "inputs",
                            receptor_snapshot.name,
                        ).as_posix(),
                    ),
                    "source_relative_path": Path(
                        receptor_relative
                    ).as_posix(),
                },
                "ligand": {
                    **_snapshot(
                        ligand_snapshot,
                        Path(
                            "maps",
                            map_set_id,
                            "inputs",
                            "ligand.pdbqt",
                        ).as_posix(),
                    ),
                    "source_relative_path": Path(
                        ligand_relative
                    ).as_posix(),
                },
                "parameter_file": parameter_snapshot,
                "gpf": _snapshot(
                    gpf_path,
                    Path(
                        "maps",
                        map_set_id,
                        "receptor.gpf",
                    ).as_posix(),
                ),
                "autogrid": {
                    "path": detection.path,
                    "version": detection.version,
                    "source": detection.source,
                    "sha256": (
                        _sha256(Path(detection.path))
                        if Path(detection.path).is_file()
                        else ""
                    ),
                    "command": [
                        detection.path,
                        "-p",
                        gpf_path.name,
                        "-l",
                        "autogrid.glg",
                    ],
                    "exit_code": None,
                },
                "validation": {
                    "complete": False,
                    "missing_files": [],
                    "issues": [
                        (
                            "AutoGrid4 runner 在返回结果前被中断；"
                            "目录内文件仅作为未发布审计证据保留。"
                        )
                    ],
                },
                "error": {
                    "code": "AUTOGRID_RUN_INTERRUPTED",
                    "message": "AutoGrid4 maps 生成被中断。",
                    "raw_error": str(exc),
                    "suggestion": (
                        "请保留该非 active 记录，并重新生成新的 maps。"
                    ),
                },
            }
            _write_manifest(
                set_dir / "manifest.json",
                interrupted_manifest,
            )
            raise
        atomic_write_text(set_dir / "stdout.txt", str(result.get("stdout") or ""))
        atomic_write_text(set_dir / "stderr.txt", str(result.get("stderr") or result.get("error") or ""))
        log_path = set_dir / "autogrid.glg"
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        log_summary = _parse_autogrid_log(log_text)
        required_names = _required_map_names("receptor", ligand_types)
        missing = [name for name in required_names if not (set_dir / name).is_file() or (set_dir / name).stat().st_size <= 0]
        map_files = [
            {
                "name": name,
                **_snapshot(set_dir / name, Path("maps", map_set_id, name).as_posix()),
            }
            for name in required_names
            if (set_dir / name).is_file() and (set_dir / name).stat().st_size > 0
        ]
        xyz_path = set_dir / "receptor.maps.xyz"
        if xyz_path.is_file() and xyz_path.stat().st_size > 0:
            map_files.append(
                {"name": xyz_path.name, **_snapshot(xyz_path, Path("maps", map_set_id, xyz_path.name).as_posix())}
            )
        ready = (
            bool(result.get("ok"))
            and not missing
            and not log_summary["has_error"]
            and (
                log_summary["successful_completion"]
                if is_ad4zn or is_hydrated
                else True
            )
        )
        executable_path = Path(detection.path)
        manifest = {
            "schema_version": 1,
            "map_set_id": map_set_id,
            "protocol_id": protocol_id,
            "stability": (
                "beta"
                if is_ad4zn
                else "experimental"
                if is_hydrated
                else "stable"
            ),
            "source": "generated",
            "status": "ready" if ready else "failed",
            "created_at": created_at,
            "finished_at": _now_iso(),
            **(
                {
                    "box": {
                        "center": {
                            "x": center[0],
                            "y": center[1],
                            "z": center[2],
                        },
                        "size": {
                            "x": project.box.size_x,
                            "y": project.box.size_y,
                            "z": project.box.size_z,
                        },
                    },
                    "grid_coverage": copy.deepcopy(
                        requested_grid_coverage
                    ),
                    "grid_coverage_sha256": (
                        requested_grid_coverage_sha256
                    ),
                }
                if is_hydrated
                else {}
            ),
            "receptor": {
                **_snapshot(
                    receptor_snapshot,
                    Path(
                        "maps",
                        map_set_id,
                        "inputs",
                        receptor_snapshot.name,
                    ).as_posix(),
                ),
                "source_relative_path": Path(receptor_relative).as_posix(),
                "source_sha256": (
                    ad4zn_source_receptor_sha256
                    if is_ad4zn
                    else _sha256(receptor_path)
                ),
                **(
                    {
                        "original_source_relative_path": Path(
                            project.receptor.file
                        ).as_posix(),
                        "original_source_sha256": (
                            _sha256(original_receptor_path)
                            if original_receptor_path is not None
                            else ""
                        ),
                    }
                    if is_ad4zn
                    else {}
                ),
                "atom_types": receptor_types,
            },
            "ligand": {
                **_snapshot(ligand_snapshot, Path("maps", map_set_id, "inputs", "ligand.pdbqt").as_posix()),
                "source_relative_path": Path(ligand_relative).as_posix(),
                "source_sha256": _sha256(ligand_path),
                "atom_types": ligand_detected["atom_types"],
                "autogrid_atom_types": ligand_types,
            },
            "grid": {
                "center": {"x": center[0], "y": center[1], "z": center[2]},
                "requested_box": {
                    "center": {
                        "x": center[0],
                        "y": center[1],
                        "z": center[2],
                    },
                    "size": {
                        "x": project.box.size_x,
                        "y": project.box.size_y,
                        "z": project.box.size_z,
                    },
                },
                "grid_points": {"x": points[0], "y": points[1], "z": points[2]},
                "spacing": spacing,
                "actual_size": {"x": actual_size[0], "y": actual_size[1], "z": actual_size[2]},
            },
            "parameter_file": parameter_snapshot,
            "gpf": _snapshot(gpf_path, Path("maps", map_set_id, "receptor.gpf").as_posix()),
            "autogrid": {
                "path": detection.path,
                "version": detection.version,
                "source": detection.source,
                "sha256": _sha256(executable_path) if executable_path.is_file() else "",
                "command": result.get("command") or [],
                "exit_code": result.get("exit_code"),
                "stdout_file": Path("maps", map_set_id, "stdout.txt").as_posix(),
                "stderr_file": Path("maps", map_set_id, "stderr.txt").as_posix(),
                "log_file": Path("maps", map_set_id, "autogrid.glg").as_posix(),
                "log_summary": log_summary,
            },
            "maps": {
                "prefix": Path("maps", map_set_id, "receptor").as_posix(),
                "ligand_atom_types": ligand_types,
                "required_files": required_names,
                "files": map_files,
            },
            "ad4zn": (
                {
                    "protocol_id": AD4ZN_PROTOCOL_ID,
                    "algorithm": {
                        key: copy.deepcopy(value)
                        for key, value in (
                            ad4zn_status.get("prepared_receptor")
                            if isinstance(ad4zn_status, dict)
                            and isinstance(
                                ad4zn_status.get("prepared_receptor"), dict
                            )
                            else {}
                        ).items()
                        if key
                        in {
                            "algorithm_name",
                            "algorithm_version",
                            "algorithm_reference",
                            "algorithm_reference_sha256",
                            "limits",
                        }
                    },
                    "selected_site": copy.deepcopy(
                        ad4zn_status.get("selected_site")
                        if isinstance(ad4zn_status, dict)
                        else {}
                    ),
                    "review": copy.deepcopy(
                        ad4zn_status.get("review")
                        if isinstance(ad4zn_status, dict)
                        else {}
                    ),
                    "prepared_receptor": copy.deepcopy(
                        ad4zn_status.get("prepared_receptor")
                        if isinstance(ad4zn_status, dict)
                        else {}
                    ),
                    "parameter_file": copy.deepcopy(
                        ad4zn_status.get("parameter_file")
                        if isinstance(ad4zn_status, dict)
                        else {}
                    ),
                    "box_coverage": copy.deepcopy(box_coverage),
                    "box_coverage_sha256": box_coverage_sha256,
                    "scientific_scope": {
                        "zinc_only": True,
                        "multinuclear_metal_supported": False,
                        "near_coplanar_tz_blocked": True,
                        "rigid_receptor": True,
                        "single_ligand": True,
                        "global_docking_only": True,
                    },
                }
                if is_ad4zn
                else None
            ),
            "validation": {
                "complete": ready,
                "missing_files": missing,
                "issues": (
                    log_summary["error_lines"]
                    + (
                        []
                        if (
                            not is_ad4zn
                            and not is_hydrated
                        )
                        or log_summary["successful_completion"]
                        else ["AutoGrid GLG 未包含 Successful Completion。"]
                    )
                ),
            },
        }
        _write_manifest(set_dir / "manifest.json", manifest)
        if not ready:
            return _error(
                "AUTOGRID_RUN_FAILED",
                "AutoGrid4 未生成完整、可用的 affinity maps。",
                "\n".join(
                    [
                        str(result.get("error") or ""),
                        *(f"缺失：{name}" for name in missing),
                        *log_summary["error_lines"],
                    ],
                ).strip(),
                f"请查看 {Path('maps', map_set_id, 'autogrid.glg').as_posix()} 与 stderr.txt。",
            ) | {"map_set_id": map_set_id, "manifest_file": manifest_relative}
        if not is_hydrated:
            saved = _activate_map_set(project, manifest_relative, manifest)
            if not saved.get("ok"):
                return saved
        return {
            "ok": True,
            "project_dir": str(root),
            "project": project.to_dict(),
            "map_set_id": map_set_id,
            "manifest_file": manifest_relative,
            "manifest": manifest,
            "message": (
                "水合对接基础 affinity maps 已生成并完成完整性校验。"
                if is_hydrated
                else "AutoGrid4 affinity maps 已生成、校验并绑定到当前受体。"
            ),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        if set_dir.is_dir() and not (set_dir / "manifest.json").exists():
            _write_manifest(
                set_dir / "manifest.json",
                {
                    "schema_version": 1,
                    "map_set_id": map_set_id,
                    "protocol_id": protocol_id,
                    "source": "generated",
                    "status": "failed",
                    "created_at": created_at,
                    "finished_at": _now_iso(),
                    "error": str(exc),
                },
            )
        return _error(
            "AUTOGRID_WORKFLOW_ERROR",
            "生成 AutoDock4 affinity maps 时发生错误。",
            str(exc),
            "请保留失败的 maps 记录并查看 AutoGrid 日志。",
        )


def generate_hydrated_base_maps(
    project_dir: str,
    hydrated_ligand_file: str,
    options: dict[str, Any] | None = None,
    *,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate an isolated AD4 map set for a hydrated ligand.

    The returned set deliberately omits activation in ``project.json``.  A
    hydrated workflow must derive and validate the W map before publishing its
    own active manifest.
    """

    relative = Path(str(hydrated_ligand_file or "").strip())
    if not str(relative) or relative.is_absolute():
        return _error(
            "HYDRATED_LIGAND_PATH_INVALID",
            "水合配体必须使用当前项目内的相对路径。",
            str(hydrated_ligand_file or ""),
        )
    normalized_options = options if isinstance(options, dict) else {}
    allowed = {
        "spacing",
        "grid_points",
        "grid_points_x",
        "grid_points_y",
        "grid_points_z",
    }
    unknown = sorted(set(normalized_options) - allowed)
    if unknown:
        return _error(
            "HYDRATED_MAPS_OPTIONS_UNSUPPORTED",
            "水合 maps 选项包含首版未开放的字段。",
            ", ".join(unknown),
            "当前仅允许设置 spacing 与各轴 grid points。",
        )
    return generate_maps(
        project_dir,
        normalized_options,
        runner=runner,
        _profile={
            "protocol_id": HYDRATED_PROTOCOL_ID,
            "ligand_file": relative.as_posix(),
        },
    )


def _parse_gpf(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = {"maps": []}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0].lower()
        values = parts[1:]
        if key == "map" and values:
            parsed["maps"].append(values[0])
        elif key in {"npts", "gridcenter"} and len(values) >= 3:
            parsed[key] = values[:3]
        elif key in {"spacing", "receptor", "gridfld", "parameter_file"} and values:
            parsed[key] = values[0]
        elif key in {"receptor_types", "ligand_types"}:
            parsed[key] = values
    return parsed


def import_maps(project_dir: str, fld_file: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    if _active_protocol_id(project) == AD4ZN_PROTOCOL_ID:
        return _error(
            "AD4ZN_GENERIC_MAP_IMPORT_UNSUPPORTED",
            "AD4Zn beta 不接受缺少完整协议记录的通用 `.maps.fld` 导入。",
            suggestion="请在 DockStart 中完成 Zn 复核、TZ 受体和参数校验后生成专用 maps。",
        )
    source_fld = Path(fld_file).expanduser().resolve()
    if not source_fld.is_file() or source_fld.stat().st_size <= 0 or not source_fld.name.endswith(".maps.fld"):
        return _error(
            "MAPS_IMPORT_FLD_INVALID",
            "请选择非空的 `.maps.fld` 文件。",
            str(source_fld),
        )
    prefix = source_fld.name[: -len(".maps.fld")]
    if not MAP_PREFIX_PATTERN.fullmatch(prefix):
        return _error("MAPS_IMPORT_PREFIX_INVALID", "maps 前缀包含不安全字符。", prefix)
    source_dir = source_fld.parent
    source_gpf = source_dir / f"{prefix}.gpf"
    if not source_gpf.is_file():
        gpf_candidates = sorted(source_dir.glob("*.gpf"))
        source_gpf = gpf_candidates[0] if len(gpf_candidates) == 1 else source_gpf
    if not source_gpf.is_file():
        return _error(
            "MAPS_IMPORT_GPF_REQUIRED",
            "导入外部 maps 时必须同时提供对应 GPF，才能核对受体来源、网格和原子类型。",
            str(source_dir),
        )
    try:
        gpf = _parse_gpf(source_gpf)
        points = [int(value) for value in gpf.get("npts", [])]
        center = [float(value) for value in gpf.get("gridcenter", [])]
        spacing = float(gpf.get("spacing"))
    except (OSError, TypeError, ValueError) as exc:
        return _error("MAPS_IMPORT_GPF_INVALID", "GPF 中的 npts、gridcenter 或 spacing 无法解析。", str(exc))
    if len(points) != 3 or len(center) != 3:
        return _error("MAPS_IMPORT_GPF_INCOMPLETE", "GPF 缺少完整的 npts 或 gridcenter。")
    validated_points, points_error = _parse_grid_points({"grid_points": points}, project.box, spacing)
    if points_error:
        return points_error
    validated_spacing, spacing_error = _validated_spacing(spacing)
    if spacing_error:
        return spacing_error
    assert validated_points is not None and validated_spacing is not None

    ad4zn_prepared_path: Path | None = None
    ad4zn_frozen_receptor_path: Path | None = None
    receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error:
        return ligand_error
    assert receptor_path is not None and ligand_path is not None
    gpf_receptor = str(gpf.get("receptor") or "")
    source_receptor = (source_gpf.parent / gpf_receptor).resolve() if gpf_receptor else None
    if source_receptor is None or not source_receptor.is_file():
        return _error(
            "MAPS_IMPORT_RECEPTOR_PROVENANCE_MISSING",
            "GPF 引用的受体 PDBQT 不可读取，无法把 maps 绑定到当前受体。",
            gpf_receptor,
        )
    if _sha256(source_receptor) != _sha256(receptor_path):
        return _error(
            "MAPS_RECEPTOR_HASH_MISMATCH",
            "外部 maps 的 GPF 受体与当前项目受体 SHA256 不一致。",
            f"gpf={_sha256(source_receptor)}; project={_sha256(receptor_path)}",
            "请打开匹配的项目受体，或重新生成 maps；不要仅凭文件名判断。",
        )
    receptor_detected = read_pdbqt_atom_types(receptor_path)
    ligand_detected = read_pdbqt_atom_types(ligand_path)
    if not receptor_detected.get("ok"):
        return receptor_detected
    if not ligand_detected.get("ok"):
        return ligand_detected
    receptor_types, type_error = _validated_type_list(
        gpf.get("receptor_types"),
        receptor_detected["atom_types"],
        role="受体",
        eligibility_from_detected_only=True,
    )
    if type_error:
        return type_error
    ligand_types, type_error = _validated_type_list(
        gpf.get("ligand_types"),
        ligand_detected["atom_types"],
        role="配体",
        eligibility_from_detected_only=True,
    )
    if type_error:
        return type_error
    assert receptor_types is not None and ligand_types is not None
    required_names = _required_map_names(prefix, ligand_types)
    missing = [name for name in required_names if not (source_dir / name).is_file() or (source_dir / name).stat().st_size <= 0]
    if missing:
        return _error(
            "MAPS_IMPORT_INCOMPLETE",
            "外部 maps 文件集合不完整。",
            ", ".join(missing),
            "请补齐 .maps.fld、每种配体原子类型的 .map、e.map 和 d.map。",
        )

    map_set_id = _next_map_set_id(root)
    set_dir = root / "maps" / map_set_id
    inputs_dir = set_dir / "inputs"
    manifest_relative = Path("maps", map_set_id, "manifest.json").as_posix()
    try:
        inputs_dir.mkdir(parents=True, exist_ok=False)
        receptor_snapshot = inputs_dir / "receptor.pdbqt"
        ligand_snapshot = inputs_dir / "ligand.pdbqt"
        shutil.copyfile(receptor_path, receptor_snapshot)
        shutil.copyfile(ligand_path, ligand_snapshot)
        target_gpf = set_dir / source_gpf.name
        shutil.copyfile(source_gpf, target_gpf)
        copied_files = []
        for name in required_names:
            source = source_dir / name
            target = set_dir / name
            shutil.copyfile(source, target)
            copied_files.append(
                {"name": name, **_snapshot(target, Path("maps", map_set_id, name).as_posix())}
            )
        xyz_source = source_dir / f"{prefix}.maps.xyz"
        if xyz_source.is_file() and xyz_source.stat().st_size > 0:
            xyz_target = set_dir / xyz_source.name
            shutil.copyfile(xyz_source, xyz_target)
            copied_files.append(
                {"name": xyz_target.name, **_snapshot(xyz_target, Path("maps", map_set_id, xyz_target.name).as_posix())}
            )
        actual_size = [round(value * validated_spacing, 6) for value in validated_points]
        manifest = {
            "schema_version": 1,
            "map_set_id": map_set_id,
            "protocol_id": "ad4_maps",
            "source": "imported",
            "source_fld": str(source_fld),
            "status": "ready",
            "created_at": _now_iso(),
            "finished_at": _now_iso(),
            "receptor": {
                **_snapshot(receptor_snapshot, Path("maps", map_set_id, "inputs", "receptor.pdbqt").as_posix()),
                "source_relative_path": Path(project.receptor.file).as_posix(),
                "source_sha256": _sha256(receptor_path),
                "atom_types": receptor_detected["atom_types"],
                "gpf_declared_atom_types": receptor_types,
                "provenance_file": str(source_receptor),
            },
            "ligand": {
                **_snapshot(ligand_snapshot, Path("maps", map_set_id, "inputs", "ligand.pdbqt").as_posix()),
                "source_relative_path": Path(project.ligand.file).as_posix(),
                "source_sha256": _sha256(ligand_path),
                "atom_types": ligand_detected["atom_types"],
                "gpf_declared_atom_types": ligand_types,
            },
            "grid": {
                "center": {"x": center[0], "y": center[1], "z": center[2]},
                "grid_points": {
                    "x": validated_points[0],
                    "y": validated_points[1],
                    "z": validated_points[2],
                },
                "spacing": validated_spacing,
                "actual_size": {"x": actual_size[0], "y": actual_size[1], "z": actual_size[2]},
            },
            "parameter_file": None,
            "gpf": _snapshot(target_gpf, Path("maps", map_set_id, target_gpf.name).as_posix()),
            "autogrid": {
                "path": "",
                "version": "",
                "source": "external_import",
                "sha256": "",
                "command": [],
                "exit_code": None,
                "stdout_file": "",
                "stderr_file": "",
                "log_file": "",
                "log_summary": {},
            },
            "maps": {
                "prefix": Path("maps", map_set_id, prefix).as_posix(),
                "ligand_atom_types": ligand_types,
                "required_files": required_names,
                "files": copied_files,
            },
            "validation": {"complete": True, "missing_files": [], "issues": []},
        }
        _write_manifest(set_dir / "manifest.json", manifest)
        saved = _activate_map_set(project, manifest_relative, manifest)
        if not saved.get("ok"):
            return saved
        return {
            "ok": True,
            "project_dir": str(root),
            "project": project.to_dict(),
            "map_set_id": map_set_id,
            "manifest_file": manifest_relative,
            "manifest": manifest,
            "message": "外部 affinity maps 已导入、完整性校验并通过受体 SHA256 绑定。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("MAPS_IMPORT_ERROR", "导入外部 affinity maps 时发生错误。", str(exc))


def _contained_project_path(root: Path, relative_path: str) -> Path | None:
    relative = Path(relative_path)
    if relative.is_absolute():
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def validate_active_maps(project_dir: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    protocol_id = _active_protocol_id(project)
    is_ad4zn = protocol_id == AD4ZN_PROTOCOL_ID
    state_key = "ad4zn" if is_ad4zn else "ad4_maps"
    active_record = project.preserved_data.get(state_key)
    manifest_relative = (
        str(active_record.get("active_manifest") or "")
        if isinstance(active_record, dict)
        else ""
    )
    if not manifest_relative:
        return _error(
            "MAPS_NOT_PREPARED",
            "当前项目还没有活动的 AutoDock4 affinity maps。",
            suggestion="请先生成 maps，或导入带 GPF 和受体来源证据的已有 maps。",
        )
    manifest_path = _contained_project_path(root, manifest_relative)
    if manifest_path is None or not manifest_path.is_file():
        return _error("MAPS_MANIFEST_MISSING", "活动 maps manifest 不存在或路径不安全。", manifest_relative)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _error("MAPS_MANIFEST_INVALID", "maps manifest 无法解析。", str(exc))
    if not isinstance(manifest, dict) or manifest.get("status") != "ready":
        return _error("MAPS_MANIFEST_NOT_READY", "maps manifest 未处于 ready 状态。")
    issues: list[str] = []
    expected_protocol_id = AD4ZN_PROTOCOL_ID if is_ad4zn else "ad4_maps"
    if str(manifest.get("protocol_id") or "") != expected_protocol_id:
        issues.append(
            f"manifest 协议为 {manifest.get('protocol_id') or '未记录'}，"
            f"与当前 {expected_protocol_id} 不一致。"
        )
    autogrid_record = (
        manifest.get("autogrid")
        if isinstance(manifest.get("autogrid"), dict)
        else {}
    )
    if (
        (
            is_ad4zn
            or str(manifest.get("source") or "") == "generated"
        )
        and not autogrid_version_supported(
            str(autogrid_record.get("version") or ""),
            expected_protocol_id,
        )
    ):
        minimum_version = _autogrid_version_label(expected_protocol_id)
        issues.append(
            (
                "AD4Zn maps"
                if is_ad4zn
                else "标准 AD4 maps"
            )
            + f" 未绑定可解析的 AutoGrid {minimum_version}+ 生成版本。"
        )
    files = (manifest.get("maps") or {}).get("files") if isinstance(manifest.get("maps"), dict) else []
    if not isinstance(files, list) or not files:
        issues.append("manifest 没有记录 maps 文件。")
    else:
        for item in files:
            if not isinstance(item, dict):
                issues.append("manifest 包含无效 maps 文件记录。")
                continue
            relative_path = str(item.get("relative_path") or "")
            path = _contained_project_path(root, relative_path)
            expected = str(item.get("sha256") or "").lower()
            if path is None or not path.is_file() or path.stat().st_size <= 0:
                issues.append(f"缺失或为空：{relative_path}")
            elif not SHA256_PATTERN.fullmatch(expected) or _sha256(path) != expected:
                issues.append(f"SHA256 不匹配：{relative_path}")
    for label, record in (
        (
            "GPF",
            manifest.get("gpf")
            if isinstance(manifest.get("gpf"), dict)
            else {},
        ),
        (
            "参数文件",
            manifest.get("parameter_file")
            if isinstance(manifest.get("parameter_file"), dict)
            else {},
        ),
    ):
        if not record:
            if is_ad4zn:
                issues.append(f"AD4Zn manifest 缺少{label}快照。")
            continue
        relative_path = str(record.get("relative_path") or "")
        path = _contained_project_path(root, relative_path)
        expected = str(record.get("sha256") or "").lower()
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            issues.append(f"{label}缺失或为空：{relative_path}")
        elif not SHA256_PATTERN.fullmatch(expected) or _sha256(path) != expected:
            issues.append(f"{label} SHA256 不匹配：{relative_path}")
    if is_ad4zn:
        gpf_record = (
            manifest.get("gpf")
            if isinstance(manifest.get("gpf"), dict)
            else {}
        )
        gpf_path = _contained_project_path(
            root,
            str(gpf_record.get("relative_path") or ""),
        )
        if gpf_path is not None and gpf_path.is_file():
            gpf_text = gpf_path.read_text(encoding="utf-8", errors="replace")
            required_gpf_lines = {
                "dielectric -0.1465",
                *AD4ZN_NBP_R_EPS,
            }
            missing_gpf_lines = sorted(
                line for line in required_gpf_lines if line not in gpf_text
            )
            if "parameter_file inputs/AD4Zn.dat" not in gpf_text:
                missing_gpf_lines.append(
                    "parameter_file inputs/AD4Zn.dat"
                )
            if "receptor inputs/receptor_tz.pdbqt" not in gpf_text:
                missing_gpf_lines.append(
                    "receptor inputs/receptor_tz.pdbqt"
                )
            if missing_gpf_lines:
                issues.append(
                    "AD4Zn GPF 缺少专用参数："
                    + ", ".join(missing_gpf_lines)
                )
        log_summary = (
            autogrid_record.get("log_summary")
            if isinstance(autogrid_record.get("log_summary"), dict)
            else {}
        )
        if log_summary.get("successful_completion") is not True:
            issues.append("AD4Zn AutoGrid 日志未记录 Successful Completion。")
    receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
    if receptor_error or receptor_path is None:
        issues.append(str((receptor_error or {}).get("error", {}).get("message") or "当前受体不可读取。"))
    elif is_ad4zn:
        receptor_manifest = (
            manifest.get("receptor")
            if isinstance(manifest.get("receptor"), dict)
            else {}
        )
        frozen_relative = str(
            receptor_manifest.get("relative_path") or ""
        )
        frozen_path = _contained_project_path(root, frozen_relative)
        expected_frozen_sha256 = str(
            receptor_manifest.get("sha256") or ""
        ).lower()
        try:
            expected_frozen_size = int(
                receptor_manifest.get("size_bytes")
            )
        except (TypeError, ValueError):
            expected_frozen_size = -1
        if (
            frozen_path is None
            or not frozen_path.is_file()
            or frozen_path.stat().st_size <= 0
        ):
            issues.append(
                "AD4Zn maps 冻结的 TZ 受体快照缺失或路径不安全。"
            )
        elif (
            expected_frozen_size <= 0
            or frozen_path.stat().st_size != expected_frozen_size
            or not SHA256_PATTERN.fullmatch(expected_frozen_sha256)
            or _sha256(frozen_path) != expected_frozen_sha256
        ):
            issues.append(
                "AD4Zn maps 冻结的 TZ 受体快照大小或 SHA256 不匹配。"
            )
        else:
            ad4zn_frozen_receptor_path = frozen_path
        expected_original = str(
            receptor_manifest.get("original_source_sha256") or ""
        ).lower()
        if (
            not SHA256_PATTERN.fullmatch(expected_original)
            or _sha256(receptor_path) != expected_original
        ):
            issues.append("当前原始受体 SHA256 与 AD4Zn maps 绑定记录不一致。")
        try:
            from dockstart_core.ad4zn import get_status as get_ad4zn_status

            ad4zn_status = get_ad4zn_status(project_dir)
        except Exception as exc:  # noqa: BLE001
            ad4zn_status = {"ok": False, "issues": [str(exc)]}
        if not ad4zn_status.get("ok") or not ad4zn_status.get("preparation_ready"):
            issues.append(
                "AD4Zn Zn 复核、TZ 受体或参数记录已失效："
                + _issue_messages(ad4zn_status.get("issues"))
            )
        prepared = (
            ad4zn_status.get("prepared_receptor")
            if isinstance(ad4zn_status.get("prepared_receptor"), dict)
            else {}
        )
        prepared_path = _contained_project_path(
            root,
            str(prepared.get("relative_path") or ""),
        )
        if prepared_path is None or not prepared_path.is_file():
            prepared_path = _contained_project_path(
                root,
                str(
                    receptor_manifest.get("source_relative_path") or ""
                ),
            )
        if prepared_path is not None and prepared_path.is_file():
            ad4zn_prepared_path = prepared_path
        expected_prepared = str(receptor_manifest.get("source_sha256") or "").lower()
        if (
            prepared_path is None
            or not prepared_path.is_file()
            or not SHA256_PATTERN.fullmatch(expected_prepared)
            or _sha256(prepared_path) != expected_prepared
        ):
            issues.append("当前 TZ 受体与 AD4Zn maps 绑定记录不一致。")
        manifest_parameter = (
            manifest.get("parameter_file")
            if isinstance(manifest.get("parameter_file"), dict)
            else {}
        )
        current_parameter = (
            ad4zn_status.get("parameter_file")
            if isinstance(ad4zn_status.get("parameter_file"), dict)
            else {}
        )
        if (
            str(manifest_parameter.get("sha256") or "").lower()
            != str(current_parameter.get("sha256") or "").lower()
            or not SHA256_PATTERN.fullmatch(
                str(manifest_parameter.get("sha256") or "").lower()
            )
        ):
            issues.append("当前 AD4Zn.dat 与 maps 绑定参数文件不一致。")
        manifest_ad4zn = (
            manifest.get("ad4zn")
            if isinstance(manifest.get("ad4zn"), dict)
            else {}
        )
        manifest_review = (
            manifest_ad4zn.get("review")
            if isinstance(manifest_ad4zn.get("review"), dict)
            else {}
        )
        current_review = (
            ad4zn_status.get("review")
            if isinstance(ad4zn_status.get("review"), dict)
            else {}
        )
        if (
            str(manifest_review.get("receptor_sha256") or "").lower()
            != str(current_review.get("receptor_sha256") or "").lower()
            or str(manifest_review.get("selected_site_id") or "")
            != str(current_review.get("selected_site_id") or "")
        ):
            issues.append("Zn 位点人工复核记录与 AD4Zn maps manifest 不一致。")
    else:
        expected_receptor = str((manifest.get("receptor") or {}).get("source_sha256") or "").lower()
        if not SHA256_PATTERN.fullmatch(expected_receptor) or _sha256(receptor_path) != expected_receptor:
            issues.append("当前受体 SHA256 与 maps 绑定受体不一致。")
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    current_ligand_types: list[str] = []
    if ligand_error or ligand_path is None:
        issues.append(str((ligand_error or {}).get("error", {}).get("message") or "当前配体不可读取。"))
    else:
        ligand_types = read_pdbqt_atom_types(ligand_path)
        if not ligand_types.get("ok"):
            issues.append(str((ligand_types.get("error") or {}).get("message") or "当前配体原子类型不可读取。"))
        else:
            current_ligand_types = ligand_types["atom_types"]
            available = set((manifest.get("maps") or {}).get("ligand_atom_types") or [])
            missing_types = sorted(set(current_ligand_types) - available)
            if missing_types:
                issues.append(f"maps 缺少当前配体原子类型：{', '.join(missing_types)}")
    grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
    center = grid.get("center") if isinstance(grid.get("center"), dict) else {}
    actual_size = grid.get("actual_size") if isinstance(grid.get("actual_size"), dict) else {}
    for project_value, manifest_value, label in (
        (project.box.center_x, center.get("x"), "center_x"),
        (project.box.center_y, center.get("y"), "center_y"),
        (project.box.center_z, center.get("z"), "center_z"),
        (project.box.size_x, actual_size.get("x"), "size_x"),
        (project.box.size_y, actual_size.get("y"), "size_y"),
        (project.box.size_z, actual_size.get("z"), "size_z"),
    ):
        try:
            if not math.isclose(float(project_value), float(manifest_value), abs_tol=1e-6):
                issues.append(f"当前 Box 的 {label} 与 maps 网格不一致。")
        except (TypeError, ValueError):
            issues.append(f"maps manifest 缺少有效的 {label}。")
    if is_ad4zn:
        manifest_ad4zn = (
            manifest.get("ad4zn")
            if isinstance(manifest.get("ad4zn"), dict)
            else {}
        )
        frozen_coverage = (
            manifest_ad4zn.get("box_coverage")
            if isinstance(manifest_ad4zn.get("box_coverage"), dict)
            else {}
        )
        frozen_coverage_sha256 = str(
            manifest_ad4zn.get("box_coverage_sha256") or ""
        ).lower()
        active_coverage_sha256 = (
            str(active_record.get("box_coverage_sha256") or "").lower()
            if isinstance(active_record, dict)
            else ""
        )
        if not frozen_coverage:
            issues.append("AD4Zn manifest 缺少 ZN/TZ Box 覆盖记录。")
        else:
            try:
                observed_frozen_sha256 = _canonical_json_sha256(
                    frozen_coverage
                )
            except (TypeError, ValueError) as exc:
                observed_frozen_sha256 = ""
                issues.append(
                    "AD4Zn manifest 的 ZN/TZ Box 覆盖记录无法规范化："
                    + str(exc)
                )
            if (
                not SHA256_PATTERN.fullmatch(frozen_coverage_sha256)
                or observed_frozen_sha256 != frozen_coverage_sha256
            ):
                issues.append(
                    "AD4Zn manifest 的 ZN/TZ Box 覆盖记录摘要不匹配。"
                )
            if (
                not SHA256_PATTERN.fullmatch(active_coverage_sha256)
                or active_coverage_sha256 != frozen_coverage_sha256
            ):
                issues.append(
                    "项目活动 AD4Zn 记录与 maps 的 Box 覆盖摘要不一致。"
                )

        requested_box = (
            grid.get("requested_box")
            if isinstance(grid.get("requested_box"), dict)
            else {}
        )
        requested_center = (
            requested_box.get("center")
            if isinstance(requested_box.get("center"), dict)
            else {}
        )
        requested_size = (
            requested_box.get("size")
            if isinstance(requested_box.get("size"), dict)
            else {}
        )
        grid_points = (
            grid.get("grid_points")
            if isinstance(grid.get("grid_points"), dict)
            else {}
        )
        coverage_box = {
            **{
                f"center_{axis}": requested_center.get(axis)
                for axis in ("x", "y", "z")
            },
            **{
                f"size_{axis}": requested_size.get(axis)
                for axis in ("x", "y", "z")
            },
        }
        for axis in ("x", "y", "z"):
            try:
                if not math.isclose(
                    float(requested_center.get(axis)),
                    float(center.get(axis)),
                    rel_tol=0.0,
                    abs_tol=GRID_GEOMETRY_TOLERANCE_ANGSTROM,
                ):
                    issues.append(
                        "AD4Zn 请求 Box 与实际 AutoGrid 网格的"
                        f" {axis.upper()} 轴中心不一致。"
                    )
            except (TypeError, ValueError):
                issues.append(
                    "AD4Zn manifest 缺少请求 Box 的"
                    f" {axis.upper()} 轴中心。"
                )
        coverage_sources = (
            ("当前 TZ 受体", ad4zn_prepared_path),
            ("冻结 TZ 受体", ad4zn_frozen_receptor_path),
        )
        for source_label, source_path in coverage_sources:
            if source_path is None:
                issues.append(
                    f"无法从{source_label}重算 ZN/TZ Box 覆盖。"
                )
                continue
            observed_coverage = compute_ad4zn_box_coverage(
                source_path,
                coverage_box,
                [
                    grid_points.get("x"),
                    grid_points.get("y"),
                    grid_points.get("z"),
                ],
                grid.get("spacing"),
            )
            if not observed_coverage.get("ok"):
                error = observed_coverage.get("error") or {}
                issues.append(
                    f"{source_label}的 ZN/TZ Box 覆盖重算失败："
                    + str(
                        error.get("message")
                        or error.get("code")
                        or "未知错误"
                    )
                )
                continue
            current_coverage = observed_coverage["box_coverage"]
            if not current_coverage[
                "all_zn_tz_inside_requested_box"
            ]:
                issues.append(
                    f"{source_label}中的 ZN/TZ 不完全位于请求 Box 内。"
                )
            if not current_coverage[
                "all_zn_tz_inside_effective_grid"
            ]:
                issues.append(
                    f"{source_label}中的 ZN/TZ 不完全位于实际 AutoGrid 网格内。"
                )
            if frozen_coverage and current_coverage != frozen_coverage:
                issues.append(
                    f"{source_label}重算的 ZN/TZ Box 覆盖与 manifest 不一致。"
                )
            observed_size = current_coverage[
                "effective_grid_size_angstrom"
            ]
            for axis in ("x", "y", "z"):
                try:
                    if not math.isclose(
                        float(observed_size[axis]),
                        float(actual_size.get(axis)),
                        rel_tol=0.0,
                        abs_tol=GRID_GEOMETRY_TOLERANCE_ANGSTROM,
                    ):
                        issues.append(
                            "AD4Zn manifest 的 actual_size 不等于"
                            f" {axis.upper()} 轴 npts × spacing。"
                        )
                except (KeyError, TypeError, ValueError):
                    issues.append(
                        "AD4Zn manifest 缺少可验证的实际网格"
                        f" {axis.upper()} 轴尺寸。"
                    )
    maps_prefix = str((manifest.get("maps") or {}).get("prefix") or "")
    prefix_path = _contained_project_path(root, maps_prefix)
    if prefix_path is None:
        issues.append("maps prefix 路径不安全。")
    return {
        "ok": not issues,
        "ready": not issues,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol": protocol_id if is_ad4zn else _active_protocol(project),
        "protocol_id": protocol_id,
        "protocol_active": (
            _active_protocol(project) == "ad4_maps"
            and str(manifest.get("protocol_id") or "") == expected_protocol_id
        ),
        "map_set_id": str(manifest.get("map_set_id") or ""),
        "manifest_file": Path(manifest_relative).as_posix(),
        "manifest": manifest,
        "maps_prefix": Path(maps_prefix).as_posix() if maps_prefix else "",
        "ligand_atom_types": current_ligand_types,
        "issues": issues,
        "message": "活动 affinity maps 完整且与当前受体、配体类型和 Box 一致。" if not issues else "活动 affinity maps 已失效或不完整。",
        "error": None if not issues else {
            "code": "MAPS_VALIDATION_FAILED",
            "message": "活动 affinity maps 已失效或不完整。",
            "raw_error": "；".join(issues),
            "suggestion": "请恢复原受体/Box，或重新生成/导入 maps。",
        },
    }


def get_maps_status(project_dir: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    protocol_id = _active_protocol_id(project)
    settings = load_settings()
    tool = autogrid_adapter.detect(settings.tool_paths.autogrid4).to_dict()
    tool["ad4zn_compatible"] = (
        _autogrid_supports_ad4zn(str(tool.get("version") or ""))
        if protocol_id == AD4ZN_PROTOCOL_ID
        else None
    )
    validated = validate_active_maps(project_dir)
    if (validated.get("error") or {}).get("code") == "MAPS_NOT_PREPARED":
        return {
            "ok": True,
            "ready": False,
            "project_dir": str(root),
            "project": project.to_dict(),
            "protocol": (
                protocol_id
                if protocol_id == AD4ZN_PROTOCOL_ID
                else _active_protocol(project)
            ),
            "protocol_id": protocol_id,
            "protocol_active": _active_protocol(project) == "ad4_maps",
            "map_set_id": "",
            "manifest_file": "",
            "manifest": None,
            "maps_prefix": "",
            "ligand_atom_types": [],
            "issues": ["尚未生成或导入 maps。"],
            "tool": tool,
            "message": "尚未生成或导入 AutoDock4 affinity maps。",
            "error": None,
        }
    validated["tool"] = tool
    return validated


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command in {"status", "defaults"} and len(sys.argv) >= 3:
        _print_json(get_maps_status(sys.argv[2]) if command == "status" else get_maps_defaults(sys.argv[2]))
        return
    if command == "set-protocol" and len(sys.argv) >= 4:
        _print_json(set_scoring_protocol(sys.argv[2], sys.argv[3]))
        return
    if command == "generate" and len(sys.argv) >= 3:
        try:
            options = json.loads(sys.argv[3]) if len(sys.argv) >= 4 else {}
        except json.JSONDecodeError as exc:
            _print_json(_error("MAPS_OPTIONS_JSON_INVALID", "AutoGrid 参数不是有效 JSON。", str(exc)))
            return
        _print_json(generate_maps(sys.argv[2], options))
        return
    if command == "import" and len(sys.argv) >= 4:
        _print_json(import_maps(sys.argv[2], sys.argv[3]))
        return
    _print_json(_error("MAPS_COMMAND_UNKNOWN", f"未知 AutoGrid/maps 命令：{command}"))


if __name__ == "__main__":
    main()
