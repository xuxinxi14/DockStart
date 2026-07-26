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
from typing import Any, Callable

from adapters import autogrid_adapter
from dockstart_core.persistence import atomic_write_text
from dockstart_core.project import BoxSettings, _project_from_dict, load_project, save_project
from dockstart_core.settings import load_settings

MAP_SET_ID_PATTERN = re.compile(r"^ad4_(\d{3,})$")
MAP_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_SPACING = 0.375
MAX_GRID_POINTS = 126
MAX_PARAMETER_FILE_BYTES = 5 * 1024 * 1024
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
    value.lower(): value for value in sorted(STANDARD_NON_METAL_TYPES | METAL_TYPES)
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


def _canonical_atom_type(value: str) -> str:
    return CANONICAL_TYPES.get(str(value or "").strip().lower(), str(value or "").strip())


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
    unknown = sorted(set(normalized) - STANDARD_NON_METAL_TYPES - METAL_TYPES)
    metals = sorted(set(normalized) & METAL_TYPES)
    missing = sorted(set(detected) - set(normalized))
    if unknown:
        return None, _error(
            "MAPS_ATOM_TYPE_UNSUPPORTED",
            f"{role}包含当前 AutoDock4 基础协议未支持的原子类型。",
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


def _even_grid_points(size: float, spacing: float) -> int:
    points = max(2, math.ceil(float(size) / spacing))
    if points % 2:
        points += 1
    return min(points, MAX_GRID_POINTS)


def _parse_grid_points(options: dict[str, Any], box: Any, spacing: float) -> tuple[list[int] | None, dict[str, Any] | None]:
    raw = options.get("grid_points")
    if isinstance(raw, dict):
        values = [raw.get(axis) for axis in ("x", "y", "z")]
    elif isinstance(raw, list) and len(raw) == 3:
        values = raw
    elif raw in (None, ""):
        values = [
            _even_grid_points(box.size_x, spacing),
            _even_grid_points(box.size_y, spacing),
            _even_grid_points(box.size_z, spacing),
        ]
    else:
        values = [options.get(f"grid_points_{axis}") for axis in ("x", "y", "z")]
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


def _next_map_set_id(root: Path) -> str:
    maps_dir = root / "maps"
    numbers = [
        int(match.group(1))
        for child in maps_dir.iterdir()
        if child.is_dir() and (match := MAP_SET_ID_PATTERN.match(child.name))
    ] if maps_dir.is_dir() else []
    return f"ad4_{max(numbers, default=0) + 1:03d}"


def _active_protocol(project: Any) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    if not isinstance(protocol, dict):
        return "vina"
    return "ad4_maps" if str(protocol.get("engine") or "").lower() == "ad4_maps" else "vina"


def set_scoring_protocol(project_dir: str, protocol: str) -> dict[str, Any]:
    normalized = str(protocol or "").strip().lower()
    if normalized not in {"vina", "ad4_maps"}:
        return _error(
            "PROTOCOL_SCORING_INVALID",
            "对接评分协议只支持标准 Vina/Vinardo 或 AutoDock4 (maps)。",
        )
    project, _, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None
    current = project.preserved_data.get("docking_protocol")
    docking_protocol = copy.deepcopy(current) if isinstance(current, dict) else {}
    docking_protocol["engine"] = normalized
    docking_protocol["protocol_id"] = "ad4_maps" if normalized == "ad4_maps" else "rigid_single"
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
        "message": "已切换到 AutoDock4 (maps) 协议。" if normalized == "ad4_maps" else "已切换到标准 Vina/Vinardo 协议。",
        "error": None,
    }


def get_maps_defaults(project_dir: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error:
        return ligand_error
    assert receptor_path is not None and ligand_path is not None
    receptor_types = read_pdbqt_atom_types(receptor_path)
    if not receptor_types.get("ok"):
        return receptor_types
    ligand_types = read_pdbqt_atom_types(ligand_path)
    if not ligand_types.get("ok"):
        return ligand_types
    _, receptor_type_error = _validated_type_list(None, receptor_types["atom_types"], role="受体")
    if receptor_type_error:
        return receptor_type_error
    _, ligand_type_error = _validated_type_list(None, ligand_types["atom_types"], role="配体")
    if ligand_type_error:
        return ligand_type_error
    spacing = DEFAULT_SPACING
    points = [
        _even_grid_points(project.box.size_x, spacing),
        _even_grid_points(project.box.size_y, spacing),
        _even_grid_points(project.box.size_z, spacing),
    ]
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol": _active_protocol(project),
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
            "parameter_file": "",
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
            "receptor inputs/receptor.pdbqt",
            f"gridcenter {center[0]:g} {center[1]:g} {center[2]:g}",
            "smooth 0.500",
        ],
    )
    lines.extend(f"map receptor.{atom_type}.map" for atom_type in ligand_types)
    lines.extend(["elecmap receptor.e.map", "dsolvmap receptor.d.map", "dielectric -42.000"])
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
    project.preserved_data["ad4_maps"] = {
        "active_manifest": Path(manifest_relative).as_posix(),
        "map_set_id": manifest["map_set_id"],
        "status": manifest["status"],
        "updated_at": _now_iso(),
    }
    docking_protocol = project.preserved_data.get("docking_protocol")
    protocol = copy.deepcopy(docking_protocol) if isinstance(docking_protocol, dict) else {}
    protocol.update({"engine": "ad4_maps", "protocol_id": "ad4_maps"})
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
) -> dict[str, Any]:
    options = options if isinstance(options, dict) else {}
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    docking_protocol = project.preserved_data.get("docking_protocol")
    receptor_mode = (
        str(docking_protocol.get("receptor_mode") or docking_protocol.get("mode") or "rigid")
        if isinstance(docking_protocol, dict)
        else "rigid"
    )
    if receptor_mode == "flexible":
        return _error(
            "MAPS_FLEXIBLE_RECEPTOR_UNSUPPORTED",
            "AutoDock4 (maps) 的 v0.12.0 基准仅开放刚性受体。",
            suggestion="请先切换到刚性受体；柔性 AD4 maps 需单独科学回归后再开放。",
        )
    receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error:
        return ligand_error
    assert receptor_path is not None and ligand_path is not None

    receptor_detected = read_pdbqt_atom_types(receptor_path)
    ligand_detected = read_pdbqt_atom_types(ligand_path)
    if not receptor_detected.get("ok"):
        return receptor_detected
    if not ligand_detected.get("ok"):
        return ligand_detected
    receptor_types, type_error = _validated_type_list(
        options.get("receptor_atom_types"),
        receptor_detected["atom_types"],
        role="受体",
    )
    if type_error:
        return type_error
    ligand_types, type_error = _validated_type_list(
        options.get("ligand_atom_types"),
        ligand_detected["atom_types"],
        role="配体",
    )
    if type_error:
        return type_error
    assert receptor_types is not None and ligand_types is not None
    spacing, spacing_error = _validated_spacing(options.get("spacing"))
    if spacing_error:
        return spacing_error
    assert spacing is not None
    points, points_error = _parse_grid_points(options, project.box, spacing)
    if points_error:
        return points_error
    assert points is not None

    parameter_source: Path | None = None
    parameter_value = str(options.get("parameter_file") or "").strip()
    if parameter_value:
        parameter_source = Path(parameter_value).expanduser().resolve()
        if not parameter_source.is_file() or parameter_source.stat().st_size <= 0:
            return _error(
                "MAPS_PARAMETER_FILE_MISSING",
                "指定的 AutoGrid 参数文件不存在或为空。",
                str(parameter_source),
            )
        if parameter_source.stat().st_size > MAX_PARAMETER_FILE_BYTES:
            return _error("MAPS_PARAMETER_FILE_TOO_LARGE", "AutoGrid 参数文件超过 5 MB，已拒绝导入。")

    settings = load_settings()
    detection = autogrid_adapter.detect(settings.tool_paths.autogrid4)
    if detection.status != "ok" or not detection.path:
        return _error(
            "AUTOGRID_NOT_AVAILABLE",
            detection.message or "未检测到 AutoGrid4。",
            detection.raw_error,
            "请在工具路径设置中配置外部 autogrid4.exe；DockStart 不会在安装包中内置 GPL 工具。",
        )

    map_set_id = _next_map_set_id(root)
    set_dir = root / "maps" / map_set_id
    inputs_dir = set_dir / "inputs"
    manifest_relative = Path("maps", map_set_id, "manifest.json").as_posix()
    created_at = _now_iso()
    try:
        inputs_dir.mkdir(parents=True, exist_ok=False)
        receptor_snapshot = inputs_dir / "receptor.pdbqt"
        ligand_snapshot = inputs_dir / "ligand.pdbqt"
        shutil.copyfile(receptor_path, receptor_snapshot)
        shutil.copyfile(ligand_path, ligand_snapshot)
        parameter_relative = ""
        parameter_snapshot: dict[str, Any] | None = None
        if parameter_source is not None:
            parameter_target = inputs_dir / "parameter_file.dat"
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
            ),
        )
        run_impl = runner or autogrid_adapter.run
        result = run_impl(
            detection.path,
            gpf_path.name,
            "autogrid.glg",
            set_dir,
            timeout_seconds=1800,
        )
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
        ready = bool(result.get("ok")) and not missing and not log_summary["has_error"]
        executable_path = Path(detection.path)
        manifest = {
            "schema_version": 1,
            "map_set_id": map_set_id,
            "protocol_id": "ad4_maps",
            "source": "generated",
            "status": "ready" if ready else "failed",
            "created_at": created_at,
            "finished_at": _now_iso(),
            "receptor": {
                **_snapshot(receptor_snapshot, Path("maps", map_set_id, "inputs", "receptor.pdbqt").as_posix()),
                "source_relative_path": Path(project.receptor.file).as_posix(),
                "source_sha256": _sha256(receptor_path),
                "atom_types": receptor_types,
            },
            "ligand": {
                **_snapshot(ligand_snapshot, Path("maps", map_set_id, "inputs", "ligand.pdbqt").as_posix()),
                "source_relative_path": Path(project.ligand.file).as_posix(),
                "source_sha256": _sha256(ligand_path),
                "atom_types": ligand_types,
            },
            "grid": {
                "center": {"x": center[0], "y": center[1], "z": center[2]},
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
            "validation": {"complete": ready, "missing_files": missing, "issues": log_summary["error_lines"]},
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
            "message": "AutoGrid4 affinity maps 已生成、校验并绑定到当前受体。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        if set_dir.is_dir() and not (set_dir / "manifest.json").exists():
            _write_manifest(
                set_dir / "manifest.json",
                {
                    "schema_version": 1,
                    "map_set_id": map_set_id,
                    "protocol_id": "ad4_maps",
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
    )
    if type_error:
        return type_error
    ligand_types, type_error = _validated_type_list(
        gpf.get("ligand_types"),
        ligand_detected["atom_types"],
        role="配体",
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
                "atom_types": receptor_types,
                "provenance_file": str(source_receptor),
            },
            "ligand": {
                **_snapshot(ligand_snapshot, Path("maps", map_set_id, "inputs", "ligand.pdbqt").as_posix()),
                "source_relative_path": Path(project.ligand.file).as_posix(),
                "source_sha256": _sha256(ligand_path),
                "atom_types": ligand_types,
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
    record = project.preserved_data.get("ad4_maps")
    manifest_relative = str(record.get("active_manifest") or "") if isinstance(record, dict) else ""
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
    receptor_path, receptor_error = _project_file(root, project.receptor.file, "受体")
    if receptor_error or receptor_path is None:
        issues.append(str((receptor_error or {}).get("error", {}).get("message") or "当前受体不可读取。"))
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
    maps_prefix = str((manifest.get("maps") or {}).get("prefix") or "")
    prefix_path = _contained_project_path(root, maps_prefix)
    if prefix_path is None:
        issues.append("maps prefix 路径不安全。")
    return {
        "ok": not issues,
        "ready": not issues,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol": _active_protocol(project),
        "protocol_active": _active_protocol(project) == "ad4_maps",
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
    settings = load_settings()
    tool = autogrid_adapter.detect(settings.tool_paths.autogrid4).to_dict()
    validated = validate_active_maps(project_dir)
    if (validated.get("error") or {}).get("code") == "MAPS_NOT_PREPARED":
        return {
            "ok": True,
            "ready": False,
            "project_dir": str(root),
            "project": project.to_dict(),
            "protocol": _active_protocol(project),
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
