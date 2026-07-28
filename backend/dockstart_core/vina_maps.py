"""Audited Vina/Vinardo precomputed-map workflow.

AutoDock Vina map files contain only grid geometry and numeric values.  They
do not identify the scoring function, receptor bytes, or Vina executable that
created them.  DockStart therefore treats the companion manifest as part of
the scientific input and never activates an unbound collection of ``.map``
files without an explicit provenance attestation.
"""

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

from adapters import vina_adapter
from dockstart_core.persistence import atomic_write_json, atomic_write_text
from dockstart_core.project import _project_from_dict, load_project, save_project
from dockstart_core.settings import load_settings

MAP_SET_ID_PATTERN = re.compile(r"^vina_(\d{3,})$")
MAP_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAP_HEADER_KEYS = (
    "GRID_PARAMETER_FILE",
    "GRID_DATA_FILE",
    "MACROMOLECULE",
    "SPACING",
    "NELEMENTS",
    "CENTER",
)
VINA_XS_TYPES = {
    "C_H",
    "C_P",
    "N_P",
    "N_D",
    "N_A",
    "N_DA",
    "O_P",
    "O_D",
    "O_A",
    "O_DA",
    "S_P",
    "P_P",
    "F_H",
    "Cl_H",
    "Br_H",
    "I_H",
    "Si",
    "At",
    "Met_D",
    "W",
}
RAW_ATTESTATION_STATEMENT = (
    "source maps belong to the stated receptor and scoring function"
)
MAX_MAP_FILES = 32
MAX_MAP_FILE_BYTES = 512 * 1024 * 1024
MAX_MAP_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
MAX_POINTS_PER_MAP = 20_000_000
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MIN_SPACING = 0.1
MAX_SPACING = 2.0
FLOAT_TOLERANCE = 1e-6


RunCallable = Callable[
    [list[str], str | Path, str | Path, str | Path, str | Path],
    Any,
]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error_payload(
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }


def _operation_error(
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "ok": False,
        "project": None,
        "error": _error_payload(code, message, raw_error, suggestion),
        **extra,
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


def _payload_sha256(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: str(value.get("name") or "")):
        record = (
            f"{item.get('name', '')}\0{item.get('size_bytes', '')}\0"
            f"{item.get('sha256', '')}\n"
        )
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def _contained_path(root: Path, relative_path: str) -> Path | None:
    relative = Path(str(relative_path or ""))
    if not relative_path or relative.is_absolute():
        return None
    unresolved = root / relative
    if unresolved.is_symlink():
        return None
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _project_file(
    root: Path,
    relative_path: str,
    role: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    candidate = _contained_path(root, relative_path)
    if candidate is None:
        return None, _operation_error(
            f"VINA_MAPS_{role.upper()}_PATH_INVALID",
            f"{role} PDBQT 必须是项目目录内的相对路径。",
            str(relative_path or ""),
        )
    try:
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size <= 0
        ):
            return None, _operation_error(
                f"VINA_MAPS_{role.upper()}_MISSING",
                f"没有找到非空的{role} PDBQT。",
                str(candidate),
            )
    except OSError as exc:
        return None, _operation_error(
            f"VINA_MAPS_{role.upper()}_READ_ERROR",
            f"无法读取{role} PDBQT。",
            str(exc),
        )
    return candidate, None


def _load_project_model(
    project_dir: str,
) -> tuple[Any | None, Path | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, None, loaded
    root = Path(project_dir).expanduser().resolve()
    try:
        return _project_from_dict(loaded["project"], root), root, None
    except Exception as exc:  # noqa: BLE001 - public boundary returns structured data.
        return None, None, _operation_error(
            "VINA_MAPS_PROJECT_INVALID",
            "project.json 无法用于 Vina 预计算 maps 工作流。",
            str(exc),
            "请先修复项目文件后重试。",
        )


def _next_map_set_id(root: Path) -> str:
    maps_dir = root / "maps"
    numbers: list[int] = []
    if maps_dir.is_dir():
        for child in maps_dir.iterdir():
            match = MAP_SET_ID_PATTERN.fullmatch(child.name) if child.is_dir() else None
            if match:
                numbers.append(int(match.group(1)))
    return f"vina_{max(numbers, default=0) + 1:03d}"


def _validate_maps_directory(root: Path) -> dict[str, Any] | None:
    maps_dir = root / "maps"
    try:
        maps_dir.mkdir(parents=True, exist_ok=True)
        if (
            maps_dir.is_symlink()
            or not maps_dir.is_dir()
            or maps_dir.resolve() != maps_dir.absolute()
        ):
            raise OSError("项目 maps 目录不能是符号链接或重解析目录。")
    except OSError as exc:
        return _operation_error(
            "VINA_MAPS_DIRECTORY_UNSAFE",
            "项目 maps 目录不可安全写入。",
            str(exc),
        )
    return None


def _next_probe_id(set_dir: Path) -> str:
    probes_dir = set_dir / "probes"
    numbers: list[int] = []
    if probes_dir.is_dir():
        for child in probes_dir.iterdir():
            match = re.fullmatch(r"probe_(\d{3,})", child.name) if child.is_dir() else None
            if match:
                numbers.append(int(match.group(1)))
    return f"probe_{max(numbers, default=0) + 1:03d}"


def _protocol(project: Any) -> dict[str, Any]:
    value = project.preserved_data.get("docking_protocol")
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _grid_source(project: Any) -> str:
    return (
        "precomputed_maps"
        if str(_protocol(project).get("grid_source") or "").strip().lower()
        == "precomputed_maps"
        else "receptor"
    )


def _receptor_mode(project: Any) -> str:
    protocol = _protocol(project)
    return str(
        protocol.get("receptor_mode") or protocol.get("mode") or "rigid"
    ).strip().lower()


def _scoring_function(project: Any) -> str:
    return str(project.vina.scoring or "vina").strip().lower()


def _box_payload(box: Any) -> dict[str, Any]:
    return {
        "center": {
            "x": float(box.center_x),
            "y": float(box.center_y),
            "z": float(box.center_z),
        },
        "size": {
            "x": float(box.size_x),
            "y": float(box.size_y),
            "z": float(box.size_z),
        },
    }


def _tool_dict(detection: Any) -> dict[str, Any]:
    if hasattr(detection, "to_dict"):
        return detection.to_dict()
    return {
        "status": str(getattr(detection, "status", "") or ""),
        "version": str(getattr(detection, "version", "") or ""),
        "path": str(getattr(detection, "path", "") or ""),
        "source": str(getattr(detection, "source", "") or ""),
        "message": str(getattr(detection, "message", "") or ""),
        "raw_error": str(getattr(detection, "raw_error", "") or ""),
        "capabilities": copy.deepcopy(getattr(detection, "capabilities", {}) or {}),
    }


def _detect_vina() -> Any:
    return vina_adapter.detect(load_settings().tool_paths.vina)


def _tool_snapshot(
    detection: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if str(getattr(detection, "status", "") or "") != "ok":
        return None, _operation_error(
            "VINA_MAPS_VINA_UNAVAILABLE",
            str(getattr(detection, "message", "") or "AutoDock Vina 不可用。"),
            str(getattr(detection, "raw_error", "") or ""),
            "请先在工具链设置中修复 AutoDock Vina。",
        )
    executable = Path(str(getattr(detection, "path", "") or "")).expanduser()
    try:
        executable = executable.resolve(strict=True)
        if executable.is_symlink() or not executable.is_file():
            raise OSError("Vina 路径不是普通文件。")
        size_bytes = executable.stat().st_size
        if size_bytes <= 0:
            raise OSError("Vina 可执行文件为空。")
        return {
            "version": str(getattr(detection, "version", "") or ""),
            "path": str(executable),
            "source": str(getattr(detection, "source", "") or "unknown"),
            "sha256": _sha256(executable),
            "size_bytes": size_bytes,
        }, None
    except OSError as exc:
        return None, _operation_error(
            "VINA_MAPS_VINA_BINARY_INVALID",
            "检测到的 Vina 可执行文件不可安全读取。",
            str(exc),
            "请重新配置 Vina 路径。",
        )


def _feature_supported(detection: Any, key: str) -> bool:
    capabilities = getattr(detection, "capabilities", {})
    features = (
        capabilities.get("features")
        if isinstance(capabilities, dict)
        and isinstance(capabilities.get("features"), dict)
        else {}
    )
    feature = features.get(key) if isinstance(features, dict) else None
    return isinstance(feature, dict) and feature.get("supported") is True


def _require_features(
    detection: Any,
    keys: tuple[str, ...],
) -> dict[str, Any] | None:
    missing = [key for key in keys if not _feature_supported(detection, key)]
    if not missing:
        return None
    return _operation_error(
        "VINA_MAPS_CAPABILITY_UNAVAILABLE",
        "当前 Vina 未确认支持预计算 maps 所需的命令行能力。",
        ", ".join(missing),
        "请使用能够在 --help_advanced 中声明相应选项的 Vina 1.2.x。",
    )


def _current_context(
    project: Any,
    root: Path,
    detection: Any,
    tool_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    receptor_path, _ = _project_file(root, project.receptor.file, "受体")
    receptor_sha = _sha256(receptor_path) if receptor_path is not None else ""
    scoring = _scoring_function(project)
    box = _box_payload(project.box)
    spacing = float(project.vina.spacing)
    template = {
        "version": 1,
        "confirmed": False,
        "scoring_function": scoring,
        "receptor_sha256": receptor_sha,
        "vina_binary_sha256": str((tool_snapshot or {}).get("sha256") or ""),
        "statement": RAW_ATTESTATION_STATEMENT,
    }
    return {
        "scoring_function": scoring,
        "receptor": {
            "file": Path(project.receptor.file).as_posix()
            if project.receptor.file
            else "",
            "source_sha256": receptor_sha,
        },
        "grid": {
            "requested_box": box,
            "spacing": spacing,
            "force_even_voxels": True,
        },
        "vina": copy.deepcopy(tool_snapshot)
        if tool_snapshot is not None
        else {
            "version": str(getattr(detection, "version", "") or ""),
            "path": str(getattr(detection, "path", "") or ""),
            "source": str(getattr(detection, "source", "") or ""),
            "sha256": "",
            "size_bytes": 0,
        },
        "raw_import_attestation_template": template,
    }


def _parse_finite_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} 不是数字。") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} 不是有限数字。")
    return parsed


def _parse_map_file(
    path: Path,
    *,
    expected_prefix: str,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"map 不是普通文件：{path.name}")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise ValueError(f"map 文件为空：{path.name}")
    if size_bytes > MAX_MAP_FILE_BYTES:
        raise ValueError(f"map 文件超过 512 MiB：{path.name}")
    expected_lead = f"{expected_prefix}."
    if not path.name.startswith(expected_lead) or not path.name.endswith(".map"):
        raise ValueError(f"map 文件名与 prefix 不匹配：{path.name}")
    atom_type = path.name[len(expected_lead) : -len(".map")]
    if atom_type not in VINA_XS_TYPES:
        raise ValueError(f"未知或不支持的 Vina XS 原子类型：{atom_type}")

    digest = hashlib.sha256()
    headers: list[str] = []
    value_count = 0
    spacing = 0.0
    nelements: tuple[int, int, int] = (0, 0, 0)
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            digest.update(raw_line)
            try:
                line = raw_line.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"{path.name} 第 {line_number} 行不是 ASCII 文本。"
                ) from exc
            if line_number <= 6:
                if not line:
                    raise ValueError(f"{path.name} 的 header 第 {line_number} 行为空。")
                headers.append(line)
                continue
            if not line:
                raise ValueError(f"{path.name} 第 {line_number} 行为空。")
            for token in line.split():
                _parse_finite_float(
                    token,
                    f"{path.name} 第 {line_number} 行网格值",
                )
                value_count += 1

    if len(headers) != 6:
        raise ValueError(f"{path.name} 缺少完整的六行 Vina map header。")
    parsed_headers: list[list[str]] = []
    for index, (line, expected_key) in enumerate(
        zip(headers, MAP_HEADER_KEYS, strict=True),
        start=1,
    ):
        parts = line.split()
        if not parts or parts[0] != expected_key:
            raise ValueError(
                f"{path.name} header 第 {index} 行必须以 {expected_key} 开头。"
            )
        parsed_headers.append(parts)
    for index in range(3):
        if len(parsed_headers[index]) < 2:
            raise ValueError(f"{path.name} header 第 {index + 1} 行缺少值。")
    if len(parsed_headers[3]) != 2:
        raise ValueError(f"{path.name} 的 SPACING header 格式无效。")
    spacing = _parse_finite_float(parsed_headers[3][1], "SPACING")
    if spacing < MIN_SPACING or spacing > MAX_SPACING:
        raise ValueError(
            f"{path.name} 的 SPACING 必须在 {MIN_SPACING}–{MAX_SPACING} Å。"
        )
    if len(parsed_headers[4]) != 4:
        raise ValueError(f"{path.name} 的 NELEMENTS header 格式无效。")
    try:
        nelements = tuple(int(value) for value in parsed_headers[4][1:4])  # type: ignore[assignment]
    except ValueError as exc:
        raise ValueError(f"{path.name} 的 NELEMENTS 必须为整数。") from exc
    if any(value <= 0 or value % 2 for value in nelements):
        raise ValueError(
            f"{path.name} 的 NELEMENTS 必须是三个正偶数。"
        )
    if len(parsed_headers[5]) != 4:
        raise ValueError(f"{path.name} 的 CENTER header 格式无效。")
    center = tuple(  # type: ignore[assignment]
        _parse_finite_float(value, "CENTER")
        for value in parsed_headers[5][1:4]
    )
    expected_count = math.prod(value + 1 for value in nelements)
    if expected_count > MAX_POINTS_PER_MAP:
        raise ValueError(
            f"{path.name} 声明的网格点数超过安全上限 {MAX_POINTS_PER_MAP}。"
        )
    if value_count != expected_count:
        raise ValueError(
            f"{path.name} 网格值数量不正确：应为 {expected_count}，实际为 {value_count}。"
        )
    return {
        "name": path.name,
        "atom_type": atom_type,
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
        "header": {
            "grid_parameter_file": " ".join(parsed_headers[0][1:]),
            "grid_data_file": " ".join(parsed_headers[1][1:]),
            "macromolecule": " ".join(parsed_headers[2][1:]),
            "spacing": spacing,
            "nelements": {
                "x": nelements[0],
                "y": nelements[1],
                "z": nelements[2],
            },
            "center": {
                "x": center[0],
                "y": center[1],
                "z": center[2],
            },
            "value_count": value_count,
        },
    }


def _float_equal(left: Any, right: Any) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=FLOAT_TOLERANCE,
        )
    except (TypeError, ValueError):
        return False


def _vina_header_float_equal(requested: Any, header_value: Any) -> bool:
    """Compare a CLI value with Vina's six-significant-digit map header."""

    try:
        serialized = float(f"{float(requested):.6g}")
    except (TypeError, ValueError):
        return False
    return _float_equal(serialized, header_value)


def _inventory_from_directory(
    directory: Path,
    prefix: str,
    *,
    expected_names: set[str] | None = None,
) -> dict[str, Any]:
    if not MAP_PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError("maps prefix 包含不安全字符。")
    candidates = sorted(directory.glob(f"{prefix}.*.map"), key=lambda item: item.name)
    if not candidates:
        raise ValueError("没有找到与 prefix 匹配的 Vina map 文件。")
    if len(candidates) > MAX_MAP_FILES:
        raise ValueError(f"map 文件数量超过安全上限 {MAX_MAP_FILES}。")
    actual_names = {path.name for path in candidates}
    if expected_names is not None and actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise ValueError(
            "maps 文件集合与 manifest 不一致；"
            f"缺少={missing or '[]'}；额外={unexpected or '[]'}"
        )
    files: list[dict[str, Any]] = []
    common_spacing: float | None = None
    common_nelements: dict[str, int] | None = None
    common_center: dict[str, float] | None = None
    atom_types: set[str] = set()
    total_size = 0
    for candidate in candidates:
        parsed = _parse_map_file(candidate, expected_prefix=prefix)
        atom_type = str(parsed["atom_type"])
        if atom_type in atom_types:
            raise ValueError(f"重复的 Vina XS 原子类型 map：{atom_type}")
        atom_types.add(atom_type)
        header = parsed["header"]
        if common_spacing is None:
            common_spacing = float(header["spacing"])
            common_nelements = dict(header["nelements"])
            common_center = dict(header["center"])
        elif (
            not _float_equal(common_spacing, header["spacing"])
            or common_nelements != header["nelements"]
            or any(
                not _float_equal(common_center[axis], header["center"][axis])
                for axis in ("x", "y", "z")
            )
        ):
            raise ValueError(
                f"{candidate.name} 的 spacing、NELEMENTS 或 CENTER 与其他 maps 不一致。"
            )
        total_size += int(parsed["size_bytes"])
        if total_size > MAX_MAP_TOTAL_BYTES:
            raise ValueError("maps 总大小超过 4 GiB 安全上限。")
        files.append(parsed)
    assert common_spacing is not None
    assert common_nelements is not None
    assert common_center is not None
    public_files = [
        {
            "name": item["name"],
            "atom_type": item["atom_type"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
        }
        for item in files
    ]
    actual_size = {
        axis: round(int(common_nelements[axis]) * common_spacing, 9)
        for axis in ("x", "y", "z")
    }
    return {
        "prefix": prefix,
        "atom_types": sorted(atom_types),
        "files": public_files,
        "map_count": len(public_files),
        "total_size_bytes": total_size,
        "payload_sha256": _payload_sha256(public_files),
        "spacing": common_spacing,
        "nelements": common_nelements,
        "center": common_center,
        "actual_size": actual_size,
    }


def _manifest_map_names(manifest: dict[str, Any]) -> set[str]:
    maps = manifest.get("maps") if isinstance(manifest.get("maps"), dict) else {}
    files = maps.get("files") if isinstance(maps.get("files"), list) else []
    names: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("manifest 包含无效的 map 文件记录。")
        name = str(item.get("name") or "")
        if not name or Path(name).name != name:
            raise ValueError("manifest 中的 map 文件名无效。")
        if name in names:
            raise ValueError(f"manifest 重复记录 map 文件：{name}")
        names.add(name)
    if not names:
        raise ValueError("manifest 没有记录 map 文件。")
    return names


def _validate_inventory_against_manifest(
    set_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    maps = manifest.get("maps") if isinstance(manifest.get("maps"), dict) else {}
    prefix_value = str(maps.get("prefix") or "")
    prefix = Path(prefix_value).name
    if (
        not prefix
        or not MAP_PREFIX_PATTERN.fullmatch(prefix)
        or "/" in prefix_value
        or "\\" in prefix_value
        or Path(prefix_value).is_absolute()
        or prefix_value != prefix
    ):
        raise ValueError("manifest 中的 maps prefix 不是安全 basename。")
    expected_names = _manifest_map_names(manifest)
    inventory = _inventory_from_directory(
        set_dir,
        prefix,
        expected_names=expected_names,
    )
    files = maps.get("files")
    assert isinstance(files, list)
    expected_by_name = {
        str(item["name"]): item for item in files if isinstance(item, dict)
    }
    for actual in inventory["files"]:
        expected = expected_by_name[actual["name"]]
        expected_hash = str(expected.get("sha256") or "").lower()
        if not SHA256_PATTERN.fullmatch(expected_hash):
            raise ValueError(f"{actual['name']} 缺少有效的 manifest SHA256。")
        if actual["sha256"] != expected_hash:
            raise ValueError(f"{actual['name']} 的 SHA256 与 manifest 不一致。")
        try:
            expected_size = int(expected.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{actual['name']} 缺少有效的 manifest 文件大小。"
            ) from exc
        if expected_size != actual["size_bytes"]:
            raise ValueError(f"{actual['name']} 的文件大小与 manifest 不一致。")
        if str(expected.get("atom_type") or "") != actual["atom_type"]:
            raise ValueError(f"{actual['name']} 的原子类型与 manifest 不一致。")
    if int(maps.get("map_count") or -1) != inventory["map_count"]:
        raise ValueError("manifest 的 map_count 与实际文件数量不一致。")
    if int(maps.get("total_size_bytes") or -1) != inventory["total_size_bytes"]:
        raise ValueError("manifest 的 total_size_bytes 与实际文件不一致。")
    if str(maps.get("payload_sha256") or "").lower() != inventory["payload_sha256"]:
        raise ValueError("manifest 的 maps payload_sha256 与实际文件不一致。")
    atom_types = maps.get("atom_types")
    if not isinstance(atom_types, list) or sorted(map(str, atom_types)) != inventory["atom_types"]:
        raise ValueError("manifest 的 maps atom_types 与实际文件不一致。")
    return inventory


def _copy_inventory_files(
    source_dir: Path,
    target_dir: Path,
    inventory: dict[str, Any],
    map_set_id: str,
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for item in inventory["files"]:
        source = source_dir / str(item["name"])
        target = target_dir / source.name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"拒绝复制非普通 map 文件：{source.name}")
        shutil.copyfile(source, target)
        copied.append(
            {
                "name": target.name,
                "atom_type": item["atom_type"],
                **_snapshot(
                    target,
                    Path("maps", map_set_id, target.name).as_posix(),
                ),
            }
        )
    return copied


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    atomic_write_json(path, manifest)


def _normalized_run_result(result: Any, command: list[str]) -> dict[str, Any]:
    if isinstance(result, dict):
        exit_code = result.get("exit_code")
        error = str(result.get("error") or "")
        ok = bool(result.get("ok")) if "ok" in result else exit_code == 0 and not error
        return {
            "ok": ok and exit_code == 0 and not error,
            "command": list(result.get("command") or command),
            "pid": result.get("pid"),
            "exit_code": exit_code,
            "error": error,
            "stdout": str(result.get("stdout") or ""),
            "stderr": str(result.get("stderr") or ""),
        }
    exit_code = getattr(result, "exit_code", None)
    error = str(getattr(result, "error", "") or "")
    return {
        "ok": exit_code == 0 and not error,
        "command": list(command),
        "pid": getattr(result, "pid", None),
        "exit_code": exit_code,
        "error": error,
        "stdout": "",
        "stderr": "",
    }


def _run_vina_command(
    command: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    log_path: Path,
    *,
    runner: RunCallable | None,
) -> dict[str, Any]:
    run_impl: RunCallable = runner or vina_adapter.run_command
    result = _normalized_run_result(
        run_impl(command, cwd, stdout_path, stderr_path, log_path),
        command,
    )
    # Test adapters and future embedded adapters may return output rather than
    # writing it.  Persist it here without overwriting files already produced
    # by the managed adapter.
    if not stdout_path.exists():
        atomic_write_text(stdout_path, result["stdout"])
    if not stderr_path.exists():
        atomic_write_text(stderr_path, result["stderr"] or result["error"])
    if not log_path.exists():
        atomic_write_text(log_path, result["stdout"])
    return result


def _grid_payload(
    requested_box: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "requested_box": copy.deepcopy(requested_box),
        "center": copy.deepcopy(inventory["center"]),
        "spacing": inventory["spacing"],
        "nelements": copy.deepcopy(inventory["nelements"]),
        "actual_size": copy.deepcopy(inventory["actual_size"]),
        "force_even_voxels": True,
    }


def _build_manifest(
    *,
    root: Path,
    set_dir: Path,
    map_set_id: str,
    source: str,
    project: Any,
    receptor_path: Path,
    ligand_path: Path,
    detection: Any,
    tool_snapshot: dict[str, Any],
    inventory: dict[str, Any],
    map_files: list[dict[str, Any]],
    command: list[str],
    exit_code: int | None,
    stdout_file: str,
    stderr_file: str,
    log_file: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receptor_snapshot = set_dir / "inputs" / "receptor.pdbqt"
    ligand_snapshot = set_dir / "inputs" / "ligand.pdbqt"
    requested_box = _box_payload(project.box)
    return {
        "schema_version": 1,
        "map_set_id": map_set_id,
        "protocol_id": "vina_maps",
        "source": source,
        "status": "ready",
        "created_at": _now_iso(),
        "finished_at": _now_iso(),
        "scoring_function": _scoring_function(project),
        "receptor": {
            **_snapshot(
                receptor_snapshot,
                Path("maps", map_set_id, "inputs", "receptor.pdbqt").as_posix(),
            ),
            "source_relative_path": Path(project.receptor.file).as_posix(),
            "source_sha256": _sha256(receptor_snapshot),
        },
        "ligand_at_generation": {
            **_snapshot(
                ligand_snapshot,
                Path("maps", map_set_id, "inputs", "ligand.pdbqt").as_posix(),
            ),
            "source_relative_path": Path(project.ligand.file).as_posix(),
            "source_sha256": _sha256(ligand_snapshot),
        },
        "grid": _grid_payload(requested_box, inventory),
        "vina": {
            **copy.deepcopy(tool_snapshot),
            "command": list(command),
            "exit_code": exit_code,
            "stdout_file": Path(stdout_file).as_posix(),
            "stderr_file": Path(stderr_file).as_posix(),
            "log_file": Path(log_file).as_posix(),
        },
        "maps": {
            "prefix": inventory["prefix"],
            "atom_types": list(inventory["atom_types"]),
            "files": map_files,
            "map_count": inventory["map_count"],
            "total_size_bytes": inventory["total_size_bytes"],
            "payload_sha256": inventory["payload_sha256"],
        },
        "semantics": {
            "grid_only": True,
            "no_refine_equivalent": True,
            "rigid_receptor_only": True,
        },
        "provenance": copy.deepcopy(provenance or {}),
        "validation": {
            "complete": True,
            "issues": [],
            "validated_at": _now_iso(),
        },
    }


def _validate_manifest_structure(manifest: Any, manifest_path: Path) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("manifest 顶层必须是 JSON 对象。")
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version 必须为 1。")
    if manifest.get("protocol_id") != "vina_maps":
        raise ValueError("manifest protocol_id 必须为 vina_maps。")
    if manifest.get("status") != "ready":
        raise ValueError("manifest 尚未处于 ready 状态。")
    map_set_id = str(manifest.get("map_set_id") or "")
    if not MAP_SET_ID_PATTERN.fullmatch(map_set_id):
        raise ValueError("manifest map_set_id 格式无效。")
    if manifest_path.parent.name != map_set_id:
        raise ValueError("manifest map_set_id 与所在目录不一致。")
    scoring = str(manifest.get("scoring_function") or "")
    if scoring not in {"vina", "vinardo"}:
        raise ValueError("manifest scoring_function 必须为 vina 或 vinardo。")
    semantics = (
        manifest.get("semantics")
        if isinstance(manifest.get("semantics"), dict)
        else {}
    )
    if (
        semantics.get("grid_only") is not True
        or semantics.get("no_refine_equivalent") is not True
        or semantics.get("rigid_receptor_only") is not True
    ):
        raise ValueError("manifest 缺少刚性受体 grid-only 语义声明。")
    receptor = (
        manifest.get("receptor")
        if isinstance(manifest.get("receptor"), dict)
        else {}
    )
    receptor_hash = str(receptor.get("source_sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(receptor_hash):
        raise ValueError("manifest receptor.source_sha256 无效。")
    vina = manifest.get("vina") if isinstance(manifest.get("vina"), dict) else {}
    vina_hash = str(vina.get("sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(vina_hash):
        raise ValueError("manifest vina.sha256 无效。")
    try:
        vina_size = int(vina.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest vina.size_bytes 无效。") from exc
    if vina_size <= 0:
        raise ValueError("manifest vina.size_bytes 必须为正数。")
    if not isinstance(vina.get("command"), list):
        raise ValueError("manifest vina.command 必须是参数数组。")
    return manifest


def _validate_grid_manifest(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
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
    if any(
        not math.isfinite(float(values.get(axis)))
        for values in (requested_center, requested_size)
        for axis in ("x", "y", "z")
    ):
        raise ValueError("manifest grid.requested_box 不完整或包含非有限数字。")
    if any(float(requested_size[axis]) <= 0 for axis in ("x", "y", "z")):
        raise ValueError("manifest grid.requested_box.size 必须为正数。")
    if grid.get("force_even_voxels") is not True:
        raise ValueError("manifest 必须声明 force_even_voxels=true。")
    for key in ("spacing",):
        if not _float_equal(grid.get(key), inventory[key]):
            raise ValueError(f"manifest grid.{key} 与 map header 不一致。")
    for key in ("center", "actual_size"):
        values = grid.get(key) if isinstance(grid.get(key), dict) else {}
        if any(
            not _float_equal(values.get(axis), inventory[key][axis])
            for axis in ("x", "y", "z")
        ):
            raise ValueError(f"manifest grid.{key} 与 map header 不一致。")
    nelements = (
        grid.get("nelements") if isinstance(grid.get("nelements"), dict) else {}
    )
    if any(
        nelements.get(axis) != inventory["nelements"][axis]
        for axis in ("x", "y", "z")
    ):
        raise ValueError("manifest grid.nelements 与 map header 不一致。")
    if any(
        not _vina_header_float_equal(
            requested_center[axis],
            inventory["center"][axis],
        )
        for axis in ("x", "y", "z")
    ):
        raise ValueError("manifest requested_box.center 与 map header CENTER 不一致。")
    for axis in ("x", "y", "z"):
        requested_elements = math.ceil(
            float(requested_size[axis]) / float(inventory["spacing"])
        )
        if requested_elements % 2:
            requested_elements += 1
        if requested_elements != int(inventory["nelements"][axis]):
            raise ValueError(
                "manifest requested_box.size、spacing 与 force-even "
                f"NELEMENTS 在 {axis} 轴不一致。"
            )


def _validate_project_binding(
    project: Any,
    root: Path,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    tool_snapshot: dict[str, Any] | None,
    *,
    require_project_box: bool = True,
) -> None:
    receptor_path, receptor_error = _project_file(
        root,
        project.receptor.file,
        "受体",
    )
    if receptor_error or receptor_path is None:
        raise ValueError(
            str((receptor_error or {}).get("error", {}).get("message") or "受体不可用。")
        )
    receptor = manifest["receptor"]
    if _sha256(receptor_path) != str(receptor.get("source_sha256") or ""):
        raise ValueError("当前受体 SHA256 与 maps manifest 绑定值不一致。")
    scoring = _scoring_function(project)
    if scoring != str(manifest.get("scoring_function") or ""):
        raise ValueError("当前评分函数与 maps manifest 不一致。")
    if tool_snapshot is None:
        raise ValueError("当前 Vina 可执行文件不可安全读取。")
    manifest_tool = manifest["vina"]
    if (
        tool_snapshot["sha256"] != str(manifest_tool.get("sha256") or "")
        or tool_snapshot["size_bytes"] != int(manifest_tool.get("size_bytes") or 0)
    ):
        raise ValueError("当前 Vina binary 与 maps manifest 绑定值不一致。")
    if require_project_box:
        box = _box_payload(project.box)
        grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
        requested = (
            grid.get("requested_box")
            if isinstance(grid.get("requested_box"), dict)
            else {}
        )
        requested_center = (
            requested.get("center")
            if isinstance(requested.get("center"), dict)
            else {}
        )
        requested_size = (
            requested.get("size")
            if isinstance(requested.get("size"), dict)
            else {}
        )
        if any(
            not _float_equal(box["center"][axis], requested_center.get(axis))
            or not _float_equal(box["size"][axis], requested_size.get(axis))
            for axis in ("x", "y", "z")
        ):
            raise ValueError("当前项目 Box 与 maps manifest 的请求 Box 不一致。")


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("maps manifest 不存在或不是普通文件。")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("maps manifest 为空或超过 5 MiB 安全上限。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"maps manifest 无法解析：{exc}") from exc
    return _validate_manifest_structure(payload, path)


def _validate_local_input_snapshots(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> None:
    set_dir = manifest_path.parent
    checks = (
        ("receptor", set_dir / "inputs" / "receptor.pdbqt", True),
        (
            "ligand_at_generation",
            set_dir / "inputs" / "ligand.pdbqt",
            True,
        ),
    )
    for key, path, require_source_match in checks:
        record = manifest.get(key) if isinstance(manifest.get(key), dict) else {}
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"manifest 缺少 {path.relative_to(set_dir).as_posix()} 快照。")
        actual_hash = _sha256(path)
        expected_hash = str(record.get("sha256") or "").lower()
        source_hash = str(record.get("source_sha256") or "").lower()
        if (
            not SHA256_PATTERN.fullmatch(expected_hash)
            or actual_hash != expected_hash
        ):
            raise ValueError(f"{key} 快照 SHA256 与 manifest 不一致。")
        if require_source_match and source_hash != actual_hash:
            raise ValueError(f"{key} 快照与 manifest 的 source_sha256 不一致。")
        if not SHA256_PATTERN.fullmatch(source_hash):
            raise ValueError(f"{key}.source_sha256 无效。")
        if int(record.get("size_bytes") or -1) != path.stat().st_size:
            raise ValueError(f"{key} 快照大小与 manifest 不一致。")


def _validate_local_manifest(
    project: Any,
    root: Path,
    manifest_path: Path,
    tool_snapshot: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_manifest(manifest_path)
    _validate_local_input_snapshots(manifest_path, manifest)
    inventory = _validate_inventory_against_manifest(
        manifest_path.parent,
        manifest,
    )
    _validate_grid_manifest(manifest, inventory)
    _validate_project_binding(
        project,
        root,
        manifest,
        inventory,
        tool_snapshot,
        require_project_box=True,
    )
    return manifest, inventory


def _probe_matches(
    probe: Any,
    *,
    ligand_sha256: str,
    vina_sha256: str,
    maps_payload_sha256: str,
    scoring_function: str,
) -> bool:
    return (
        isinstance(probe, dict)
        and probe.get("ok") is True
        and probe.get("status") == "compatible"
        and str(probe.get("ligand_sha256") or "") == ligand_sha256
        and str(probe.get("vina_binary_sha256") or "") == vina_sha256
        and str(probe.get("maps_payload_sha256") or "") == maps_payload_sha256
        and str(probe.get("scoring_function") or "") == scoring_function
    )


def _run_compatibility_probe(
    *,
    project: Any,
    root: Path,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    tool_snapshot: dict[str, Any],
    runner: RunCallable | None,
) -> dict[str, Any]:
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error or ligand_path is None:
        error = (ligand_error or {}).get("error") or {}
        return {
            "ok": False,
            "status": "failed",
            "message": str(error.get("message") or "当前配体不可读。"),
            "error": copy.deepcopy(error),
        }
    map_set_id = str(manifest["map_set_id"])
    set_dir = root / "maps" / map_set_id
    probe_id = _next_probe_id(set_dir)
    probe_dir = set_dir / "probes" / probe_id
    try:
        probe_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return {
            "ok": False,
            "status": "failed",
            "message": "无法创建 maps 兼容性探测记录。",
            "error": _error_payload(
                "VINA_MAPS_PROBE_DIRECTORY_ERROR",
                "无法创建 maps 兼容性探测记录。",
                str(exc),
            ),
        }
    prefix_relative = Path("maps", map_set_id, inventory["prefix"]).as_posix()
    ligand_relative = Path(project.ligand.file).as_posix()
    command = [
        tool_snapshot["path"],
        "--maps",
        prefix_relative,
        "--ligand",
        ligand_relative,
        "--scoring",
        str(manifest["scoring_function"]),
        "--score_only",
        "--cpu",
        "1",
        "--verbosity",
        "1",
    ]
    stdout_path = probe_dir / "stdout.txt"
    stderr_path = probe_dir / "stderr.txt"
    log_path = probe_dir / "log.txt"
    started_at = _now_iso()
    result = _run_vina_command(
        command,
        root,
        stdout_path,
        stderr_path,
        log_path,
        runner=runner,
    )
    ligand_sha = _sha256(ligand_path)
    binary_path = Path(tool_snapshot["path"])
    binary_after = _sha256(binary_path)
    binary_stable = binary_after == tool_snapshot["sha256"]
    ok = bool(result["ok"]) and binary_stable
    probe_relative = Path(
        "maps",
        map_set_id,
        "probes",
        probe_id,
        "probe.json",
    ).as_posix()
    probe = {
        "schema_version": 1,
        "probe_id": probe_id,
        "ok": ok,
        "status": "compatible" if ok else "failed",
        "message": (
            "当前配体已通过 Vina score_only maps 兼容性探测。"
            if ok
            else "当前配体未通过 Vina score_only maps 兼容性探测。"
        ),
        "started_at": started_at,
        "finished_at": _now_iso(),
        "ligand_file": ligand_relative,
        "ligand_sha256": ligand_sha,
        "vina_binary_sha256": tool_snapshot["sha256"],
        "maps_payload_sha256": inventory["payload_sha256"],
        "scoring_function": manifest["scoring_function"],
        "command": command,
        "exit_code": result["exit_code"],
        "stdout_file": Path(
            "maps", map_set_id, "probes", probe_id, "stdout.txt"
        ).as_posix(),
        "stderr_file": Path(
            "maps", map_set_id, "probes", probe_id, "stderr.txt"
        ).as_posix(),
        "log_file": Path(
            "maps", map_set_id, "probes", probe_id, "log.txt"
        ).as_posix(),
        "probe_file": probe_relative,
        "error": (
            None
            if ok
            else _error_payload(
                "VINA_MAPS_LIGAND_PROBE_FAILED",
                "当前配体未通过预计算 maps 兼容性探测。",
                (
                    str(result.get("error") or "")
                    or f"Vina exit_code={result.get('exit_code')}"
                    if binary_stable
                    else "探测期间 Vina binary SHA256 发生变化。"
                ),
                "请使用生成这些 maps 的受体、评分函数和 Vina binary，或重新生成 maps。",
            )
        ),
    }
    atomic_write_json(probe_dir / "probe.json", probe)
    return probe


def _activate_manifest(
    project: Any,
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    tool_snapshot: dict[str, Any],
    *,
    runner: RunCallable | None,
) -> dict[str, Any]:
    probe = _run_compatibility_probe(
        project=project,
        root=root,
        manifest=manifest,
        inventory=inventory,
        tool_snapshot=tool_snapshot,
        runner=runner,
    )
    if not probe.get("ok"):
        return _operation_error(
            "VINA_MAPS_LIGAND_PROBE_FAILED",
            str(probe.get("message") or "当前配体未通过 maps 兼容性探测。"),
            str((probe.get("error") or {}).get("raw_error") or ""),
            str((probe.get("error") or {}).get("suggestion") or ""),
            map_set_id=manifest["map_set_id"],
            manifest_file=manifest_path.relative_to(root).as_posix(),
            compatibility_probe=probe,
        )
    manifest_relative = manifest_path.relative_to(root).as_posix()
    manifest_hash = _sha256(manifest_path)
    project.preserved_data["vina_maps"] = {
        "active_manifest": manifest_relative,
        "manifest_sha256": manifest_hash,
        "map_set_id": manifest["map_set_id"],
        "status": "ready",
        "updated_at": _now_iso(),
        "compatibility_probe": copy.deepcopy(probe),
    }
    protocol = _protocol(project)
    protocol.update(
        {
            "engine": "vina",
            "protocol_id": "vina_maps",
            "grid_source": "precomputed_maps",
            "active_map_set_id": manifest["map_set_id"],
            "autobox": False,
        }
    )
    protocol.setdefault("receptor_mode", _receptor_mode(project))
    project.preserved_data["docking_protocol"] = protocol
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "map_set_id": manifest["map_set_id"],
        "manifest_file": manifest_relative,
        "manifest": manifest,
        "maps_prefix": Path(
            "maps",
            manifest["map_set_id"],
            inventory["prefix"],
        ).as_posix(),
        "compatibility_probe": probe,
        "message": "预计算 maps 已通过校验和当前配体探测，并已设为活动网格来源。",
        "error": None,
    }


def _status_payload(
    *,
    ok: bool,
    ready: bool,
    project: Any | None,
    root: Path | None,
    protocol_active: bool,
    grid_source: str,
    map_set_id: str = "",
    manifest_file: str = "",
    manifest: dict[str, Any] | None = None,
    maps_prefix: str = "",
    issues: list[str] | None = None,
    compatibility_probe: dict[str, Any] | None = None,
    tool: dict[str, Any] | None = None,
    current_context: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "ready": ready,
        "project": project.to_dict() if project is not None else None,
        "project_dir": str(root) if root is not None else "",
        "protocol_active": protocol_active,
        "grid_source": grid_source,
        "map_set_id": map_set_id,
        "manifest_file": manifest_file,
        "manifest": manifest,
        "maps_prefix": maps_prefix,
        "issues": list(issues or []),
        "compatibility_probe": compatibility_probe,
        "tool": tool or {},
        "current_context": current_context or {},
        "error": error,
    }


def validate_active_maps(
    project_dir: str,
    probe_ligand: bool = True,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    """Validate the active map set against every bound scientific input.

    ``probe_ligand=True`` means "ensure a current probe exists".  A successful
    probe is reused only while the ligand, map payload, scoring function, and
    Vina binary hashes are all unchanged.
    """

    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        error = load_error.get("error") if isinstance(load_error, dict) else None
        return _status_payload(
            ok=False,
            ready=False,
            project=None,
            root=Path(project_dir).expanduser().resolve(),
            protocol_active=False,
            grid_source="receptor",
            issues=[str((error or {}).get("message") or "项目无法读取。")],
            error=error,
        )
    assert project is not None and root is not None
    detection = _detect_vina()
    tool = _tool_dict(detection)
    tool_snapshot, tool_error_result = _tool_snapshot(detection)
    current_context = _current_context(
        project,
        root,
        detection,
        tool_snapshot,
    )
    record = project.preserved_data.get("vina_maps")
    record = record if isinstance(record, dict) else {}
    manifest_relative = str(record.get("active_manifest") or "")
    map_set_id = str(record.get("map_set_id") or "")
    protocol = _protocol(project)
    protocol_active = (
        _grid_source(project) == "precomputed_maps"
        and str(protocol.get("protocol_id") or "") == "vina_maps"
        and str(protocol.get("active_map_set_id") or "") == map_set_id
        and bool(map_set_id)
    )
    if not manifest_relative:
        missing_is_error = _grid_source(project) == "precomputed_maps"
        error = (
            _error_payload(
                "VINA_MAPS_NOT_PREPARED",
                "当前协议要求预计算 maps，但项目没有活动 manifest。",
                suggestion="请先生成或导入 maps，或将网格来源切换回受体。",
            )
            if missing_is_error
            else None
        )
        return _status_payload(
            ok=not missing_is_error,
            ready=False,
            project=project,
            root=root,
            protocol_active=False,
            grid_source=_grid_source(project),
            issues=[error["message"]] if error else [],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    manifest_path = _contained_path(root, manifest_relative)
    if manifest_path is None:
        error = _error_payload(
            "VINA_MAPS_MANIFEST_PATH_INVALID",
            "活动 maps manifest 路径不安全。",
            manifest_relative,
        )
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            issues=[error["message"]],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    if tool_error_result is not None:
        error = tool_error_result["error"]
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            issues=[str(error["message"])],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    assert tool_snapshot is not None
    capability_error = _require_features(detection, ("maps",))
    if capability_error is not None:
        error = capability_error["error"]
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            issues=[str(error["message"])],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    try:
        expected_manifest_hash = str(record.get("manifest_sha256") or "").lower()
        if (
            not SHA256_PATTERN.fullmatch(expected_manifest_hash)
            or _sha256(manifest_path) != expected_manifest_hash
        ):
            raise ValueError("活动 manifest SHA256 与 project.json 记录不一致。")
        manifest, inventory = _validate_local_manifest(
            project,
            root,
            manifest_path,
            tool_snapshot,
        )
        if map_set_id != str(manifest["map_set_id"]):
            raise ValueError("project.json 的 map_set_id 与 manifest 不一致。")
    except (OSError, TypeError, ValueError) as exc:
        error = _error_payload(
            "VINA_MAPS_VALIDATION_FAILED",
            "活动预计算 maps 未通过完整性或绑定校验。",
            str(exc),
            "请恢复原始文件，或重新生成/导入与当前项目匹配的 maps。",
        )
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            issues=[str(exc)],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    ligand_path, ligand_error = _project_file(root, project.ligand.file, "配体")
    if ligand_error or ligand_path is None:
        error = (ligand_error or {}).get("error") or _error_payload(
            "VINA_MAPS_LIGAND_MISSING",
            "当前配体不可读取。",
        )
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            manifest=manifest,
            maps_prefix=Path("maps", map_set_id, inventory["prefix"]).as_posix(),
            issues=[str(error.get("message") or "")],
            tool=tool,
            current_context=current_context,
            error=error,
        )
    stored_probe = (
        record.get("compatibility_probe")
        if isinstance(record.get("compatibility_probe"), dict)
        else {}
    )
    probe_current = _probe_matches(
        stored_probe,
        ligand_sha256=_sha256(ligand_path),
        vina_sha256=tool_snapshot["sha256"],
        maps_payload_sha256=inventory["payload_sha256"],
        scoring_function=str(manifest["scoring_function"]),
    )
    probe = copy.deepcopy(stored_probe)
    if probe_ligand and not probe_current:
        probe = _run_compatibility_probe(
            project=project,
            root=root,
            manifest=manifest,
            inventory=inventory,
            tool_snapshot=tool_snapshot,
            runner=runner,
        )
        if probe.get("ok"):
            record["compatibility_probe"] = copy.deepcopy(probe)
            record["updated_at"] = _now_iso()
            project.preserved_data["vina_maps"] = record
            saved = save_project(project)
            if not saved.get("ok"):
                error = saved.get("error") or _error_payload(
                    "VINA_MAPS_PROBE_SAVE_FAILED",
                    "兼容性探测成功，但无法保存探测记录。",
                )
                return _status_payload(
                    ok=False,
                    ready=False,
                    project=project,
                    root=root,
                    protocol_active=protocol_active,
                    grid_source=_grid_source(project),
                    map_set_id=map_set_id,
                    manifest_file=manifest_relative,
                    manifest=manifest,
                    maps_prefix=Path(
                        "maps", map_set_id, inventory["prefix"]
                    ).as_posix(),
                    issues=[str(error.get("message") or "")],
                    compatibility_probe=probe,
                    tool=tool,
                    current_context=current_context,
                    error=error,
                )
            probe_current = True
    if not probe_current:
        probe_error = (
            probe.get("error") if isinstance(probe.get("error"), dict) else {}
        )
        error = _error_payload(
            "VINA_MAPS_LIGAND_PROBE_REQUIRED",
            str(
                probe_error.get("message")
                or "缺少与当前配体、maps 和 Vina binary 匹配的 score_only 探测。"
            ),
            str(probe_error.get("raw_error") or ""),
            str(
                probe_error.get("suggestion")
                or "请重新执行兼容性探测，或重新生成 maps。"
            ),
        )
        return _status_payload(
            ok=False,
            ready=False,
            project=project,
            root=root,
            protocol_active=protocol_active,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            manifest=manifest,
            maps_prefix=Path("maps", map_set_id, inventory["prefix"]).as_posix(),
            issues=[error["message"]],
            compatibility_probe=probe if isinstance(probe, dict) else None,
            tool=tool,
            current_context=current_context,
            error=error,
        )
    if not protocol_active:
        return _status_payload(
            ok=True,
            ready=True,
            project=project,
            root=root,
            protocol_active=False,
            grid_source=_grid_source(project),
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
            manifest=manifest,
            maps_prefix=Path("maps", map_set_id, inventory["prefix"]).as_posix(),
            issues=[],
            compatibility_probe=probe,
            tool=tool,
            current_context=current_context,
            error=None,
        )
    return _status_payload(
        ok=True,
        ready=True,
        project=project,
        root=root,
        protocol_active=True,
        grid_source="precomputed_maps",
        map_set_id=map_set_id,
        manifest_file=manifest_relative,
        manifest=manifest,
        maps_prefix=Path("maps", map_set_id, inventory["prefix"]).as_posix(),
        issues=[],
        compatibility_probe=probe,
        tool=tool,
        current_context=current_context,
        error=None,
    )


def get_maps_status(
    project_dir: str,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return validate_active_maps(
        project_dir,
        probe_ligand=True,
        runner=runner,
    )


def _validated_generation_context(
    project_dir: str,
    *,
    needs_write_maps: bool,
) -> tuple[
    Any | None,
    Path | None,
    Path | None,
    Path | None,
    Any | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return None, None, None, None, None, None, load_error
    assert project is not None and root is not None
    maps_directory_error = _validate_maps_directory(root)
    if maps_directory_error:
        return None, None, None, None, None, None, maps_directory_error
    if _receptor_mode(project) == "flexible":
        return None, None, None, None, None, None, _operation_error(
            "VINA_MAPS_FLEXIBLE_RECEPTOR_UNSUPPORTED",
            "Vina/Vinardo 预计算 maps 当前只支持刚性受体。",
            suggestion="请先切换到刚性受体模式。",
        )
    scoring = _scoring_function(project)
    if scoring not in {"vina", "vinardo"}:
        return None, None, None, None, None, None, _operation_error(
            "VINA_MAPS_SCORING_INVALID",
            "预计算 maps 的评分函数只能是 vina 或 vinardo。",
            scoring,
        )
    receptor_path, receptor_error = _project_file(
        root,
        project.receptor.file,
        "受体",
    )
    if receptor_error:
        return None, None, None, None, None, None, receptor_error
    ligand_path, ligand_error = _project_file(
        root,
        project.ligand.file,
        "配体",
    )
    if ligand_error:
        return None, None, None, None, None, None, ligand_error
    try:
        spacing = float(project.vina.spacing)
        box = _box_payload(project.box)
        numeric_values = [
            spacing,
            *(box["center"][axis] for axis in ("x", "y", "z")),
            *(box["size"][axis] for axis in ("x", "y", "z")),
        ]
        if any(not math.isfinite(float(value)) for value in numeric_values):
            raise ValueError("Box 或 spacing 含有非有限数字。")
        if spacing < MIN_SPACING or spacing > MAX_SPACING:
            raise ValueError(
                f"spacing 必须在 {MIN_SPACING}–{MAX_SPACING} Å。"
            )
        if any(float(box["size"][axis]) <= 0 for axis in ("x", "y", "z")):
            raise ValueError("Box size 必须为正数。")
    except (TypeError, ValueError) as exc:
        return None, None, None, None, None, None, _operation_error(
            "VINA_MAPS_GRID_INVALID",
            "当前 Box 或 spacing 不能用于生成预计算 maps。",
            str(exc),
        )
    detection = _detect_vina()
    tool_snapshot, tool_error = _tool_snapshot(detection)
    if tool_error:
        return None, None, None, None, None, None, tool_error
    features = ("maps",)
    if needs_write_maps:
        features = (
            *features,
            "write_maps",
            "force_even_voxels",
            "no_refine",
        )
    capability_error = _require_features(detection, features)
    if capability_error:
        return None, None, None, None, None, None, capability_error
    assert receptor_path is not None and ligand_path is not None
    assert tool_snapshot is not None
    return (
        project,
        root,
        receptor_path,
        ligand_path,
        detection,
        tool_snapshot,
        None,
    )


def generate_maps(
    project_dir: str,
    options: dict[str, Any] | None = None,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    options = options if isinstance(options, dict) else {}
    (
        project,
        root,
        receptor_path,
        ligand_path,
        detection,
        tool_snapshot,
        context_error,
    ) = _validated_generation_context(project_dir, needs_write_maps=True)
    if context_error:
        return context_error
    assert project is not None and root is not None
    assert receptor_path is not None and ligand_path is not None
    assert detection is not None and tool_snapshot is not None
    requested_scoring = str(
        options.get("scoring_function") or _scoring_function(project)
    ).strip().lower()
    if requested_scoring not in {"vina", "vinardo"}:
        return _operation_error(
            "VINA_MAPS_SCORING_INVALID",
            "scoring_function 只能是 vina 或 vinardo。",
            requested_scoring,
        )
    if requested_scoring != _scoring_function(project):
        return _operation_error(
            "VINA_MAPS_SCORING_MISMATCH",
            "生成 maps 的评分函数必须与当前项目评分函数一致。",
            f"requested={requested_scoring}; project={_scoring_function(project)}",
        )
    activate = options.get("activate", True)
    if not isinstance(activate, bool):
        return _operation_error(
            "VINA_MAPS_ACTIVATE_INVALID",
            "activate 必须是 JSON 布尔值。",
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
        box = _box_payload(project.box)
        spacing = float(project.vina.spacing)
        prefix = "receptor"
        command = [
            tool_snapshot["path"],
            "--receptor",
            Path("inputs", "receptor.pdbqt").as_posix(),
            "--ligand",
            Path("inputs", "ligand.pdbqt").as_posix(),
            "--scoring",
            requested_scoring,
            "--center_x",
            f"{box['center']['x']:.15g}",
            "--center_y",
            f"{box['center']['y']:.15g}",
            "--center_z",
            f"{box['center']['z']:.15g}",
            "--size_x",
            f"{box['size']['x']:.15g}",
            "--size_y",
            f"{box['size']['y']:.15g}",
            "--size_z",
            f"{box['size']['z']:.15g}",
            "--spacing",
            f"{spacing:.15g}",
            "--force_even_voxels",
            "--no_refine",
            "--write_maps",
            prefix,
            "--score_only",
            "--cpu",
            "1",
            "--verbosity",
            "1",
        ]
        stdout_path = set_dir / "stdout.txt"
        stderr_path = set_dir / "stderr.txt"
        log_path = set_dir / "log.txt"
        result = _run_vina_command(
            command,
            set_dir,
            stdout_path,
            stderr_path,
            log_path,
            runner=runner,
        )
        binary_after = _sha256(Path(tool_snapshot["path"]))
        if binary_after != tool_snapshot["sha256"]:
            raise RuntimeError("生成期间 Vina binary SHA256 发生变化。")
        if not result["ok"]:
            raise RuntimeError(
                str(result.get("error") or "")
                or f"Vina exit_code={result.get('exit_code')}"
            )
        inventory = _inventory_from_directory(set_dir, prefix)
        map_files = [
            {
                "name": item["name"],
                "atom_type": item["atom_type"],
                **_snapshot(
                    set_dir / item["name"],
                    Path("maps", map_set_id, item["name"]).as_posix(),
                ),
            }
            for item in inventory["files"]
        ]
        manifest = _build_manifest(
            root=root,
            set_dir=set_dir,
            map_set_id=map_set_id,
            source="generated",
            project=project,
            receptor_path=receptor_path,
            ligand_path=ligand_path,
            detection=detection,
            tool_snapshot=tool_snapshot,
            inventory=inventory,
            map_files=map_files,
            command=result["command"],
            exit_code=result["exit_code"],
            stdout_file=Path("maps", map_set_id, "stdout.txt").as_posix(),
            stderr_file=Path("maps", map_set_id, "stderr.txt").as_posix(),
            log_file=Path("maps", map_set_id, "log.txt").as_posix(),
        )
        _write_manifest(set_dir / "manifest.json", manifest)
        manifest, inventory = _validate_local_manifest(
            project,
            root,
            set_dir / "manifest.json",
            tool_snapshot,
        )
        if activate:
            return _activate_manifest(
                project,
                root,
                set_dir / "manifest.json",
                manifest,
                inventory,
                tool_snapshot,
                runner=runner,
            )
        return {
            "ok": True,
            "ready": True,
            "project_dir": str(root),
            "project": project.to_dict(),
            "map_set_id": map_set_id,
            "manifest_file": manifest_relative,
            "manifest": manifest,
            "maps_prefix": Path("maps", map_set_id, prefix).as_posix(),
            "protocol_active": False,
            "compatibility_probe": None,
            "message": "预计算 maps 已生成并校验，尚未激活。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - preserve failed audit record.
        try:
            if set_dir.is_dir():
                failed_manifest = {
                    "schema_version": 1,
                    "map_set_id": map_set_id,
                    "protocol_id": "vina_maps",
                    "source": "generated",
                    "status": "failed",
                    "created_at": created_at,
                    "finished_at": _now_iso(),
                    "scoring_function": requested_scoring,
                    "error": str(exc),
                }
                _write_manifest(set_dir / "manifest.json", failed_manifest)
        except OSError:
            pass
        return _operation_error(
            "VINA_MAPS_GENERATION_FAILED",
            "Vina 未生成完整且可审计的预计算 maps。",
            str(exc),
            f"请查看 {Path('maps', map_set_id, 'stderr.txt').as_posix()} 和 log.txt。",
            map_set_id=map_set_id,
            manifest_file=manifest_relative,
        )


def activate_map_set(
    project_dir: str,
    map_set_id: str,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    (
        project,
        root,
        _receptor_path,
        _ligand_path,
        _detection,
        tool_snapshot,
        context_error,
    ) = _validated_generation_context(project_dir, needs_write_maps=False)
    if context_error:
        return context_error
    assert project is not None and root is not None and tool_snapshot is not None
    normalized = str(map_set_id or "").strip()
    if not MAP_SET_ID_PATTERN.fullmatch(normalized):
        return _operation_error(
            "VINA_MAPS_SET_ID_INVALID",
            "map_set_id 格式无效。",
            normalized,
        )
    manifest_path = root / "maps" / normalized / "manifest.json"
    try:
        manifest, inventory = _validate_local_manifest(
            project,
            root,
            manifest_path,
            tool_snapshot,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _operation_error(
            "VINA_MAPS_ACTIVATION_VALIDATION_FAILED",
            "所选 map set 未通过完整性和项目绑定校验。",
            str(exc),
        )
    return _activate_manifest(
        project,
        root,
        manifest_path,
        manifest,
        inventory,
        tool_snapshot,
        runner=runner,
    )


def set_grid_source(
    project_dir: str,
    mode: str,
    map_set_id: str = "",
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    normalized = str(mode or "").strip().lower()
    if normalized not in {"receptor", "precomputed_maps"}:
        return _operation_error(
            "VINA_MAPS_GRID_SOURCE_INVALID",
            "网格来源只能是 receptor 或 precomputed_maps。",
            normalized,
        )
    if normalized == "precomputed_maps":
        project, _, load_error = _load_project_model(project_dir)
        if load_error:
            return load_error
        assert project is not None
        record = project.preserved_data.get("vina_maps")
        record = record if isinstance(record, dict) else {}
        selected = str(map_set_id or record.get("map_set_id") or record.get("last_map_set_id") or "")
        if not selected:
            return _operation_error(
                "VINA_MAPS_SET_ID_REQUIRED",
                "切换到预计算 maps 时必须指定 map_set_id。",
            )
        return activate_map_set(project_dir, selected, runner=runner)
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    protocol = _protocol(project)
    receptor_mode = _receptor_mode(project)
    protocol.update(
        {
            "engine": "vina",
            "protocol_id": (
                "flexible_single" if receptor_mode == "flexible" else "rigid_single"
            ),
            "grid_source": "receptor",
        }
    )
    protocol.pop("active_map_set_id", None)
    project.preserved_data["docking_protocol"] = protocol
    record = project.preserved_data.get("vina_maps")
    if isinstance(record, dict):
        next_record = copy.deepcopy(record)
        next_record["status"] = "inactive"
        next_record["updated_at"] = _now_iso()
        project.preserved_data["vina_maps"] = next_record
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    return {
        "ok": True,
        "ready": False,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol_active": False,
        "grid_source": "receptor",
        "map_set_id": "",
        "manifest_file": "",
        "manifest": None,
        "maps_prefix": "",
        "compatibility_probe": None,
        "message": "网格来源已切换回受体实时计算；已有 map set 未被删除。",
        "error": None,
    }


def _validated_attestation(
    attestation: Any,
    *,
    project: Any,
    receptor_path: Path,
    tool_snapshot: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(attestation, dict):
        return None, _operation_error(
            "VINA_MAPS_IMPORT_ATTESTATION_REQUIRED",
            "导入无 DockStart manifest 的原始 maps 必须提供显式来源确认。",
            suggestion="请核对受体、评分函数和 Vina binary 后再确认导入。",
        )
    required_keys = {
        "version",
        "confirmed",
        "scoring_function",
        "receptor_sha256",
        "vina_binary_sha256",
        "statement",
    }
    if set(attestation) != required_keys:
        return None, _operation_error(
            "VINA_MAPS_IMPORT_ATTESTATION_INVALID",
            "原始 maps 来源确认字段不完整或包含未知字段。",
            f"expected={sorted(required_keys)}; actual={sorted(attestation)}",
        )
    if attestation.get("version") != 1 or attestation.get("confirmed") is not True:
        return None, _operation_error(
            "VINA_MAPS_IMPORT_ATTESTATION_INVALID",
            "原始 maps 来源确认必须使用 version=1 且 confirmed=true。",
        )
    if attestation.get("statement") != RAW_ATTESTATION_STATEMENT:
        return None, _operation_error(
            "VINA_MAPS_IMPORT_ATTESTATION_INVALID",
            "原始 maps 来源确认声明文本不匹配。",
        )
    scoring = str(attestation.get("scoring_function") or "").strip().lower()
    receptor_sha = str(attestation.get("receptor_sha256") or "").lower()
    binary_sha = str(attestation.get("vina_binary_sha256") or "").lower()
    if scoring != _scoring_function(project):
        return None, _operation_error(
            "VINA_MAPS_IMPORT_SCORING_MISMATCH",
            "来源确认中的评分函数与当前项目不一致。",
            f"attested={scoring}; project={_scoring_function(project)}",
        )
    if (
        not SHA256_PATTERN.fullmatch(receptor_sha)
        or receptor_sha != _sha256(receptor_path)
    ):
        return None, _operation_error(
            "VINA_MAPS_IMPORT_RECEPTOR_MISMATCH",
            "来源确认中的受体 SHA256 与当前项目不一致。",
        )
    if (
        not SHA256_PATTERN.fullmatch(binary_sha)
        or binary_sha != tool_snapshot["sha256"]
    ):
        return None, _operation_error(
            "VINA_MAPS_IMPORT_VINA_BINARY_MISMATCH",
            "来源确认中的 Vina binary SHA256 与当前工具链不一致。",
        )
    return copy.deepcopy(attestation), None


def _infer_prefix_from_map_name(name: str) -> str | None:
    if not name.endswith(".map"):
        return None
    stem = name[: -len(".map")]
    for atom_type in sorted(VINA_XS_TYPES, key=len, reverse=True):
        suffix = f".{atom_type}"
        if stem.endswith(suffix):
            prefix = stem[: -len(suffix)]
            return prefix if MAP_PREFIX_PATTERN.fullmatch(prefix) else None
    return None


def _select_raw_prefix(
    source_path: Path,
    options: dict[str, Any],
) -> tuple[Path | None, str | None, dict[str, Any] | None]:
    if source_path.is_file():
        if source_path.is_symlink() or source_path.suffix.lower() != ".map":
            return None, None, _operation_error(
                "VINA_MAPS_IMPORT_SOURCE_INVALID",
                "原始 maps 导入入口必须是普通 .map 文件或目录。",
                str(source_path),
            )
        source_dir = source_path.parent
        inferred = _infer_prefix_from_map_name(source_path.name)
        if inferred is None:
            return None, None, _operation_error(
                "VINA_MAPS_IMPORT_PREFIX_INVALID",
                "无法从所选 map 文件名识别安全的 Vina map prefix。",
                source_path.name,
            )
    elif source_path.is_dir() and not source_path.is_symlink():
        source_dir = source_path
        inferred_prefixes = {
            prefix
            for candidate in source_dir.glob("*.map")
            if not candidate.is_symlink()
            for prefix in [_infer_prefix_from_map_name(candidate.name)]
            if prefix is not None
        }
        inferred = (
            next(iter(inferred_prefixes))
            if len(inferred_prefixes) == 1
            else None
        )
    else:
        return None, None, _operation_error(
            "VINA_MAPS_IMPORT_SOURCE_INVALID",
            "原始 maps 导入入口不存在或不是普通文件/目录。",
            str(source_path),
        )
    requested = str(options.get("prefix") or "").strip()
    prefix = requested or inferred or ""
    if not MAP_PREFIX_PATTERN.fullmatch(prefix):
        return None, None, _operation_error(
            "VINA_MAPS_IMPORT_PREFIX_REQUIRED",
            "目录中存在多个或无法识别的 map prefix，请显式指定 prefix。",
            str(source_dir),
        )
    if source_path.is_file() and inferred != prefix:
        return None, None, _operation_error(
            "VINA_MAPS_IMPORT_PREFIX_MISMATCH",
            "指定 prefix 与所选 map 文件名不一致。",
            f"selected={inferred}; requested={prefix}",
        )
    return source_dir, prefix, None


def _import_manifest_source(
    *,
    project: Any,
    root: Path,
    receptor_path: Path,
    ligand_path: Path,
    detection: Any,
    tool_snapshot: dict[str, Any],
    source_manifest_path: Path,
    activate: bool,
    runner: RunCallable | None,
) -> dict[str, Any]:
    try:
        source_manifest_sha256 = _sha256(source_manifest_path)
        source_manifest = _read_manifest(source_manifest_path)
        source_inventory = _validate_inventory_against_manifest(
            source_manifest_path.parent,
            source_manifest,
        )
        _validate_grid_manifest(source_manifest, source_inventory)
        _validate_local_input_snapshots(
            source_manifest_path,
            source_manifest,
        )
        _validate_project_binding(
            project,
            root,
            source_manifest,
            source_inventory,
            tool_snapshot,
            require_project_box=True,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _operation_error(
            "VINA_MAPS_IMPORT_MANIFEST_INVALID",
            "所选 DockStart maps manifest 未通过来源、完整性或项目绑定校验。",
            str(exc),
        )
    map_set_id = _next_map_set_id(root)
    set_dir = root / "maps" / map_set_id
    inputs_dir = set_dir / "inputs"
    try:
        inputs_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(receptor_path, inputs_dir / "receptor.pdbqt")
        shutil.copyfile(ligand_path, inputs_dir / "ligand.pdbqt")
        copied_files = _copy_inventory_files(
            source_manifest_path.parent,
            set_dir,
            source_inventory,
            map_set_id,
        )
        local_inventory = _inventory_from_directory(
            set_dir,
            source_inventory["prefix"],
        )
        if (
            local_inventory["payload_sha256"]
            != source_inventory["payload_sha256"]
            or _sha256(source_manifest_path) != source_manifest_sha256
        ):
            raise ValueError("导入期间源 manifest 或 map 文件发生变化。")
        source_vina = (
            source_manifest.get("vina")
            if isinstance(source_manifest.get("vina"), dict)
            else {}
        )
        manifest = _build_manifest(
            root=root,
            set_dir=set_dir,
            map_set_id=map_set_id,
            source="imported_manifest",
            project=project,
            receptor_path=receptor_path,
            ligand_path=ligand_path,
            detection=detection,
            tool_snapshot=tool_snapshot,
            inventory=local_inventory,
            map_files=copied_files,
            command=list(source_vina.get("command") or []),
            exit_code=source_vina.get("exit_code"),
            stdout_file="",
            stderr_file="",
            log_file="",
            provenance={
                "kind": "dockstart_manifest",
                "source_manifest_path": str(source_manifest_path),
                "source_manifest_sha256": source_manifest_sha256,
                "source_map_set_id": source_manifest["map_set_id"],
                "source_manifest": copy.deepcopy(source_manifest),
            },
        )
        manifest_path = set_dir / "manifest.json"
        _write_manifest(manifest_path, manifest)
        manifest, local_inventory = _validate_local_manifest(
            project,
            root,
            manifest_path,
            tool_snapshot,
        )
        if activate:
            return _activate_manifest(
                project,
                root,
                manifest_path,
                manifest,
                local_inventory,
                tool_snapshot,
                runner=runner,
            )
        return {
            "ok": True,
            "ready": True,
            "project_dir": str(root),
            "project": project.to_dict(),
            "map_set_id": map_set_id,
            "manifest_file": manifest_path.relative_to(root).as_posix(),
            "manifest": manifest,
            "maps_prefix": Path(
                "maps",
                map_set_id,
                local_inventory["prefix"],
            ).as_posix(),
            "protocol_active": False,
            "compatibility_probe": None,
            "message": "DockStart maps manifest 已导入并校验，尚未激活。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured workflow error.
        return _operation_error(
            "VINA_MAPS_IMPORT_FAILED",
            "复制或登记 DockStart maps 时发生错误。",
            str(exc),
            map_set_id=map_set_id,
        )


def _import_raw_source(
    *,
    project: Any,
    root: Path,
    receptor_path: Path,
    ligand_path: Path,
    detection: Any,
    tool_snapshot: dict[str, Any],
    source_path: Path,
    options: dict[str, Any],
    activate: bool,
    runner: RunCallable | None,
) -> dict[str, Any]:
    attestation, attestation_error = _validated_attestation(
        options.get("attestation"),
        project=project,
        receptor_path=receptor_path,
        tool_snapshot=tool_snapshot,
    )
    if attestation_error:
        return attestation_error
    source_dir, prefix, prefix_error = _select_raw_prefix(source_path, options)
    if prefix_error:
        return prefix_error
    assert attestation is not None
    assert source_dir is not None and prefix is not None
    try:
        source_inventory = _inventory_from_directory(source_dir, prefix)
    except (OSError, ValueError) as exc:
        return _operation_error(
            "VINA_MAPS_IMPORT_RAW_INVALID",
            "原始 maps 未通过文件名、header、数值数量或一致性校验。",
            str(exc),
        )
    map_set_id = _next_map_set_id(root)
    set_dir = root / "maps" / map_set_id
    inputs_dir = set_dir / "inputs"
    try:
        inputs_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(receptor_path, inputs_dir / "receptor.pdbqt")
        shutil.copyfile(ligand_path, inputs_dir / "ligand.pdbqt")
        copied_files = _copy_inventory_files(
            source_dir,
            set_dir,
            source_inventory,
            map_set_id,
        )
        local_inventory = _inventory_from_directory(set_dir, prefix)
        source_after = _inventory_from_directory(source_dir, prefix)
        if (
            local_inventory["payload_sha256"]
            != source_inventory["payload_sha256"]
            or source_after["payload_sha256"]
            != source_inventory["payload_sha256"]
        ):
            raise ValueError("导入期间原始 map 文件发生变化。")
        manifest = _build_manifest(
            root=root,
            set_dir=set_dir,
            map_set_id=map_set_id,
            source="imported_raw_attested",
            project=project,
            receptor_path=receptor_path,
            ligand_path=ligand_path,
            detection=detection,
            tool_snapshot=tool_snapshot,
            inventory=local_inventory,
            map_files=copied_files,
            command=[],
            exit_code=None,
            stdout_file="",
            stderr_file="",
            log_file="",
            provenance={
                "kind": "raw_maps_attested",
                "source_path": str(source_path),
                "attestation": attestation,
                "note": (
                    "DockStart 已验证文件结构和当前哈希绑定；原始 maps 的历史"
                    "来源由用户 attestation 声明，不等同于可独立验证的生成记录。"
                ),
            },
        )
        manifest_path = set_dir / "manifest.json"
        _write_manifest(manifest_path, manifest)
        manifest, local_inventory = _validate_local_manifest(
            project,
            root,
            manifest_path,
            tool_snapshot,
        )
        if activate:
            return _activate_manifest(
                project,
                root,
                manifest_path,
                manifest,
                local_inventory,
                tool_snapshot,
                runner=runner,
            )
        return {
            "ok": True,
            "ready": True,
            "project_dir": str(root),
            "project": project.to_dict(),
            "map_set_id": map_set_id,
            "manifest_file": manifest_path.relative_to(root).as_posix(),
            "manifest": manifest,
            "maps_prefix": Path("maps", map_set_id, prefix).as_posix(),
            "protocol_active": False,
            "compatibility_probe": None,
            "message": "原始 maps 已按显式 attestation 导入并校验，尚未激活。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - public boundary returns structured data.
        return _operation_error(
            "VINA_MAPS_IMPORT_FAILED",
            "复制或登记原始 maps 时发生错误。",
            str(exc),
            map_set_id=map_set_id,
        )


def import_maps(
    project_dir: str,
    source_path: str,
    options: dict[str, Any] | None = None,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    options = options if isinstance(options, dict) else {}
    (
        project,
        root,
        receptor_path,
        ligand_path,
        detection,
        tool_snapshot,
        context_error,
    ) = _validated_generation_context(project_dir, needs_write_maps=False)
    if context_error:
        return context_error
    assert project is not None and root is not None
    assert receptor_path is not None and ligand_path is not None
    assert detection is not None and tool_snapshot is not None
    activate = options.get("activate", True)
    if not isinstance(activate, bool):
        return _operation_error(
            "VINA_MAPS_ACTIVATE_INVALID",
            "activate 必须是 JSON 布尔值。",
        )
    source = Path(str(source_path or "")).expanduser()
    try:
        source = source.resolve(strict=True)
    except OSError as exc:
        return _operation_error(
            "VINA_MAPS_IMPORT_SOURCE_INVALID",
            "所选 maps 来源不存在。",
            str(exc),
        )
    if source.is_file() and source.suffix.lower() == ".json":
        return _import_manifest_source(
            project=project,
            root=root,
            receptor_path=receptor_path,
            ligand_path=ligand_path,
            detection=detection,
            tool_snapshot=tool_snapshot,
            source_manifest_path=source,
            activate=activate,
            runner=runner,
        )
    return _import_raw_source(
        project=project,
        root=root,
        receptor_path=receptor_path,
        ligand_path=ligand_path,
        detection=detection,
        tool_snapshot=tool_snapshot,
        source_path=source,
        options=options,
        activate=activate,
        runner=runner,
    )


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def _json_options(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("options_json 必须是 JSON 对象。")
    return parsed


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if command == "status" and len(sys.argv) >= 3:
            _print_json(get_maps_status(sys.argv[2]))
            return
        if command == "set-mode" and len(sys.argv) >= 4:
            _print_json(
                set_grid_source(
                    sys.argv[2],
                    sys.argv[3],
                    sys.argv[4] if len(sys.argv) >= 5 else "",
                )
            )
            return
        if command == "generate" and len(sys.argv) >= 3:
            _print_json(
                generate_maps(
                    sys.argv[2],
                    _json_options(sys.argv[3] if len(sys.argv) >= 4 else None),
                )
            )
            return
        if command == "import" and len(sys.argv) >= 4:
            _print_json(
                import_maps(
                    sys.argv[2],
                    sys.argv[3],
                    _json_options(sys.argv[4] if len(sys.argv) >= 5 else None),
                )
            )
            return
        raise ValueError(
            "用法：vina_maps.py status <project_dir> | "
            "set-mode <project_dir> <receptor|precomputed_maps> [map_set_id] | "
            "generate <project_dir> [options_json] | "
            "import <project_dir> <source_path> [options_json]"
        )
    except Exception as exc:  # noqa: BLE001 - CLI always emits structured JSON.
        _print_json(
            _operation_error(
                "VINA_MAPS_CLI_ERROR",
                "Vina maps 命令执行失败。",
                str(exc),
            )
        )


if __name__ == "__main__":
    main()
