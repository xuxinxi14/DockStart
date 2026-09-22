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
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from adapters import vina_adapter
from dockstart_core.persistence import atomic_write_json, atomic_write_text
from dockstart_core.project import (
    _exclusive_file_lock,
    _project_from_dict,
    load_project,
    save_project,
)
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
MAPS_OPERATION_LOCK_NAME = ".vina-maps.lock"
MAPS_STAGING_PREFIX = ".vina-maps-staging-"


RunCallable = Callable[
    [list[str], str | Path, str | Path, str | Path, str | Path],
    Any,
]


class MapsManifestRebuildRequired(ValueError):
    """A legacy/incomplete manifest cannot satisfy the current scientific contract."""


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


def _maps_operation_lock_path(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    return root / MAPS_OPERATION_LOCK_NAME


def _run_with_maps_operation_lock(
    project_dir: str,
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Serialize every maps mutation/probe within one project.

    ``project.json`` already has revision-based optimistic locking, but map-set
    identifiers and compatibility-probe directories are filesystem resources.
    They need a protocol-local lock that spans allocation, staging, validation,
    publication, probing, and the final project revision update.
    """

    try:
        root = Path(project_dir).expanduser().resolve(strict=True)
    except OSError:
        # Preserve the established project-loading error contract. There is no
        # valid project directory in which a protocol lock could be created.
        return operation()
    project_json = root / "project.json"
    if (
        not root.is_dir()
        or project_json.is_symlink()
        or not project_json.is_file()
    ):
        return operation()
    manager = _exclusive_file_lock(_maps_operation_lock_path(root))
    try:
        manager.__enter__()
    except (OSError, RuntimeError) as exc:
        return _operation_error(
            "VINA_MAPS_OPERATION_LOCK_ERROR",
            "无法安全取得 Vina/Vinardo maps 项目操作锁。",
            str(exc),
            "请确认项目目录可写且未被文件同步或安全软件锁定后重试。",
        )
    try:
        return operation()
    finally:
        manager.__exit__(*sys.exc_info())


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


def _new_staging_map_set(
    root: Path,
    map_set_id: str,
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Create a private staging tree whose leaf has the final map-set name."""

    temporary = tempfile.TemporaryDirectory(
        prefix=MAPS_STAGING_PREFIX,
        dir=root / "maps",
    )
    staging_root = Path(temporary.name)
    staging_set_dir = staging_root / map_set_id
    return temporary, staging_set_dir


def _publish_staged_map_set(
    *,
    project: Any,
    root: Path,
    staging_set_dir: Path,
    map_set_id: str,
    tool_snapshot: dict[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Atomically publish one fully validated staged set, then revalidate it."""

    manifest_path = staging_set_dir / "manifest.json"
    staged_manifest, staged_inventory = _validate_local_manifest(
        project,
        root,
        manifest_path,
        tool_snapshot,
    )
    staged_manifest_hash = _sha256(manifest_path)
    staged_payload_hash = str(staged_inventory["payload_sha256"])
    final_set_dir = root / "maps" / map_set_id
    if final_set_dir.exists() or final_set_dir.is_symlink():
        raise FileExistsError(f"map set 已存在，拒绝覆盖：{map_set_id}")
    staging_set_dir.replace(final_set_dir)
    final_manifest_path = final_set_dir / "manifest.json"
    try:
        final_manifest, final_inventory = _validate_local_manifest(
            project,
            root,
            final_manifest_path,
            tool_snapshot,
        )
        if (
            _sha256(final_manifest_path) != staged_manifest_hash
            or final_inventory["payload_sha256"] != staged_payload_hash
            or final_manifest != staged_manifest
        ):
            raise ValueError("map set 原子发布前后的内容或 manifest 发生变化。")
    except Exception:
        # The directory was owned by this operation and has not been exposed
        # through project.json. Move it back under the private staging root so
        # TemporaryDirectory can remove it without deleting an unrelated set.
        discarded = staging_set_dir.parent / f"{map_set_id}.discarded"
        if (
            final_set_dir.is_dir()
            and not final_set_dir.is_symlink()
            and final_set_dir.resolve().parent == (root / "maps").resolve()
            and not discarded.exists()
        ):
            final_set_dir.replace(discarded)
        raise
    return final_manifest_path, final_manifest, final_inventory


def _rollback_uncommitted_published_set(
    *,
    root: Path,
    map_set_id: str,
    staging_parent: Path,
) -> bool:
    """Remove a newly published set only when project.json proves it is inactive."""

    loaded = load_project(str(root))
    if not loaded.get("ok"):
        return False
    payload = loaded.get("project")
    payload = payload if isinstance(payload, dict) else {}
    record = payload.get("vina_maps")
    record = record if isinstance(record, dict) else {}
    protocol = payload.get("docking_protocol")
    protocol = protocol if isinstance(protocol, dict) else {}
    committed = (
        str(record.get("map_set_id") or "") == map_set_id
        and str(record.get("active_manifest") or "")
        == Path("maps", map_set_id, "manifest.json").as_posix()
        and str(protocol.get("active_map_set_id") or "") == map_set_id
        and str(protocol.get("grid_source") or "") == "precomputed_maps"
    )
    if committed:
        return False
    final_set_dir = root / "maps" / map_set_id
    rollback_target = staging_parent / f"{map_set_id}.activation-rollback"
    if (
        not final_set_dir.is_dir()
        or final_set_dir.is_symlink()
        or final_set_dir.resolve().parent != (root / "maps").resolve()
        or rollback_target.exists()
    ):
        return False
    final_set_dir.replace(rollback_target)
    return True


def _activate_newly_published_set(
    *,
    project: Any,
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    tool_snapshot: dict[str, Any],
    staging_parent: Path,
    runner: RunCallable | None,
) -> dict[str, Any]:
    result = _activate_manifest(
        project,
        root,
        manifest_path,
        manifest,
        inventory,
        tool_snapshot,
        runner=runner,
    )
    if result.get("ok"):
        return result
    error = result.get("error")
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "")
    if not code.startswith("PROJECT_"):
        # A scientifically complete ready set remains reusable when only the
        # ligand probe fails. Existing behavior deliberately preserves it.
        return result
    rolled_back = _rollback_uncommitted_published_set(
        root=root,
        map_set_id=str(manifest["map_set_id"]),
        staging_parent=staging_parent,
    )
    next_result = copy.deepcopy(result)
    next_result["map_set_id"] = str(manifest["map_set_id"])
    next_result["publication_rollback"] = {
        "attempted": True,
        "removed_uncommitted_set": rolled_back,
    }
    if rolled_back:
        next_result["manifest_file"] = ""
    return next_result


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


def _grid_records_equivalent(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if (
        left.get("force_even_voxels") is not True
        or right.get("force_even_voxels") is not True
        or not _float_equal(left.get("spacing"), right.get("spacing"))
        or left.get("nelements") != right.get("nelements")
    ):
        return False
    for key in ("center", "actual_size"):
        left_values = left.get(key) if isinstance(left.get(key), dict) else {}
        right_values = (
            right.get(key) if isinstance(right.get(key), dict) else {}
        )
        if any(
            not _float_equal(left_values.get(axis), right_values.get(axis))
            for axis in ("x", "y", "z")
        ):
            return False
    left_box = (
        left.get("requested_box")
        if isinstance(left.get("requested_box"), dict)
        else {}
    )
    right_box = (
        right.get("requested_box")
        if isinstance(right.get("requested_box"), dict)
        else {}
    )
    for key in ("center", "size"):
        left_values = (
            left_box.get(key) if isinstance(left_box.get(key), dict) else {}
        )
        right_values = (
            right_box.get(key) if isinstance(right_box.get(key), dict) else {}
        )
        if any(
            not _float_equal(left_values.get(axis), right_values.get(axis))
            for axis in ("x", "y", "z")
        ):
            return False
    return True


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
    if result["command"] != command:
        result["ok"] = False
        result["error"] = (
            "Vina adapter 返回的实际命令与 DockStart 冻结命令不一致；"
            f"expected={command!r}; actual={result['command']!r}"
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
    required_top_level = {
        "schema_version",
        "map_set_id",
        "protocol_id",
        "source",
        "status",
        "created_at",
        "finished_at",
        "scoring_function",
        "receptor",
        "ligand_at_generation",
        "grid",
        "vina",
        "maps",
        "semantics",
        "provenance",
        "validation",
    }
    missing_top_level = sorted(required_top_level - set(manifest))
    if missing_top_level:
        raise MapsManifestRebuildRequired(
            "manifest 缺少当前科学合同要求的字段，不能安全补写；"
            f"请重新生成或重新导入 maps：{missing_top_level}"
        )
    if manifest.get("schema_version") != 1:
        raise MapsManifestRebuildRequired(
            "manifest schema_version 不是当前受支持的 1；"
            "该记录保持只读，请使用当前版本重新生成或重新导入 maps。"
        )
    if manifest.get("protocol_id") != "vina_maps":
        raise ValueError("manifest protocol_id 必须为 vina_maps。")
    if manifest.get("source") not in {
        "generated",
        "imported_manifest",
        "imported_raw_attested",
    }:
        raise ValueError("manifest source 不是受支持的 maps 来源。")
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
    missing_semantics = sorted(
        {
            "grid_only",
            "no_refine_equivalent",
            "rigid_receptor_only",
        }
        - set(semantics)
    )
    if missing_semantics:
        raise MapsManifestRebuildRequired(
            "manifest 缺少 grid-only 科学语义字段，不能安全推断；"
            f"请重建 maps：{missing_semantics}"
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
    required_receptor = {
        "relative_path",
        "size_bytes",
        "sha256",
        "source_relative_path",
        "source_sha256",
    }
    missing_receptor = sorted(required_receptor - set(receptor))
    if missing_receptor:
        raise MapsManifestRebuildRequired(
            "manifest receptor 快照字段不完整，不能安全推断；"
            f"请重建 maps：{missing_receptor}"
        )
    receptor_hash = str(receptor.get("source_sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(receptor_hash):
        raise ValueError("manifest receptor.source_sha256 无效。")
    vina = manifest.get("vina") if isinstance(manifest.get("vina"), dict) else {}
    required_vina = {
        "version",
        "path",
        "source",
        "sha256",
        "size_bytes",
        "command",
        "exit_code",
        "stdout_file",
        "stderr_file",
        "log_file",
    }
    missing_vina = sorted(required_vina - set(vina))
    if missing_vina:
        raise MapsManifestRebuildRequired(
            "manifest Vina 工具证据字段不完整，不能安全推断；"
            f"请重建 maps：{missing_vina}"
        )
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
    ligand = (
        manifest.get("ligand_at_generation")
        if isinstance(manifest.get("ligand_at_generation"), dict)
        else {}
    )
    missing_ligand = sorted(
        {
            "relative_path",
            "size_bytes",
            "sha256",
            "source_relative_path",
            "source_sha256",
        }
        - set(ligand)
    )
    if missing_ligand:
        raise MapsManifestRebuildRequired(
            "manifest 配体生成快照字段不完整，不能安全推断；"
            f"请重建 maps：{missing_ligand}"
        )
    grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
    missing_grid = sorted(
        {
            "requested_box",
            "center",
            "spacing",
            "nelements",
            "actual_size",
            "force_even_voxels",
        }
        - set(grid)
    )
    if missing_grid:
        raise MapsManifestRebuildRequired(
            "manifest 网格几何字段不完整，不能安全推断；"
            f"请重建 maps：{missing_grid}"
        )
    maps = manifest.get("maps") if isinstance(manifest.get("maps"), dict) else {}
    missing_maps = sorted(
        {
            "prefix",
            "atom_types",
            "files",
            "map_count",
            "total_size_bytes",
            "payload_sha256",
        }
        - set(maps)
    )
    if missing_maps:
        raise MapsManifestRebuildRequired(
            "manifest map 文件清单字段不完整，不能安全推断；"
            f"请重建 maps：{missing_maps}"
        )
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise MapsManifestRebuildRequired(
            "manifest provenance 必须是对象；请重建 maps。"
        )
    source = str(manifest.get("source") or "")
    if source == "generated" and provenance:
        raise ValueError("本地生成 maps 的 provenance 必须为空对象。")
    if source == "imported_raw_attested":
        attestation = (
            provenance.get("attestation")
            if isinstance(provenance.get("attestation"), dict)
            else {}
        )
        required_attestation = {
            "version",
            "confirmed",
            "scoring_function",
            "receptor_sha256",
            "vina_binary_sha256",
            "statement",
        }
        if (
            provenance.get("kind") != "raw_maps_attested"
            or set(attestation) != required_attestation
            or attestation.get("version") != 1
            or attestation.get("confirmed") is not True
            or attestation.get("statement") != RAW_ATTESTATION_STATEMENT
            or str(attestation.get("scoring_function") or "")
            != manifest["scoring_function"]
            or str(attestation.get("receptor_sha256") or "").lower()
            != receptor_hash
            or str(attestation.get("vina_binary_sha256") or "").lower()
            != vina_hash
        ):
            raise ValueError("原始 maps 的 provenance attestation 与 manifest 绑定不一致。")
    if source == "imported_manifest":
        source_manifest = provenance.get("source_manifest")
        if (
            provenance.get("kind") != "dockstart_manifest"
            or not SHA256_PATTERN.fullmatch(
                str(provenance.get("source_manifest_sha256") or "").lower()
            )
            or not MAP_SET_ID_PATTERN.fullmatch(
                str(provenance.get("source_map_set_id") or "")
            )
            or not isinstance(source_manifest, dict)
        ):
            raise ValueError("导入的 DockStart manifest provenance 不完整。")
        source_receptor = (
            source_manifest.get("receptor")
            if isinstance(source_manifest.get("receptor"), dict)
            else {}
        )
        source_vina = (
            source_manifest.get("vina")
            if isinstance(source_manifest.get("vina"), dict)
            else {}
        )
        source_maps = (
            source_manifest.get("maps")
            if isinstance(source_manifest.get("maps"), dict)
            else {}
        )
        if (
            str(source_manifest.get("map_set_id") or "")
            != str(provenance.get("source_map_set_id") or "")
            or str(source_manifest.get("scoring_function") or "") != scoring
            or str(source_receptor.get("source_sha256") or "").lower()
            != receptor_hash
            or str(source_vina.get("sha256") or "").lower() != vina_hash
            or int(source_vina.get("size_bytes") or 0) != vina_size
            or str(source_maps.get("payload_sha256") or "").lower()
            != str(maps.get("payload_sha256") or "").lower()
            or not _grid_records_equivalent(
                source_manifest.get("grid"),
                manifest.get("grid"),
            )
        ):
            raise ValueError("来源 manifest 的科学绑定与本地导入 manifest 不一致。")
    validation = (
        manifest.get("validation")
        if isinstance(manifest.get("validation"), dict)
        else {}
    )
    missing_validation = sorted(
        {"complete", "issues", "validated_at"} - set(validation)
    )
    if missing_validation:
        raise MapsManifestRebuildRequired(
            "manifest validation 证据字段不完整；"
            f"请重建 maps：{missing_validation}"
        )
    if (
        validation.get("complete") is not True
        or validation.get("issues") != []
        or not str(validation.get("validated_at") or "").strip()
    ):
        raise ValueError("manifest validation 未声明完整且无问题。")
    if (
        not str(manifest.get("created_at") or "").strip()
        or not str(manifest.get("finished_at") or "").strip()
    ):
        raise ValueError("manifest 缺少生成时间证据。")
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
    ligand_sha_before = _sha256(ligand_path)
    maps_payload_before = str(inventory["payload_sha256"])
    try:
        result = _run_vina_command(
            command,
            root,
            stdout_path,
            stderr_path,
            log_path,
            runner=runner,
        )
    except (KeyboardInterrupt, SystemExit):
        shutil.rmtree(probe_dir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - adapter failures are audit evidence.
        result = {
            "ok": False,
            "command": command,
            "pid": None,
            "exit_code": None,
            "error": f"兼容性探针调用异常：{exc}",
            "stdout": "",
            "stderr": str(exc),
        }
        if not stdout_path.exists():
            atomic_write_text(stdout_path, "")
        if not stderr_path.exists():
            atomic_write_text(stderr_path, str(exc))
        if not log_path.exists():
            atomic_write_text(log_path, "")
    integrity_issues: list[str] = []
    try:
        ligand_sha_after = _sha256(ligand_path)
    except OSError as exc:
        ligand_sha_after = ""
        integrity_issues.append(f"探针结束后无法读取配体：{exc}")
    if ligand_sha_after != ligand_sha_before:
        integrity_issues.append("探针期间当前配体 SHA256 发生变化。")
    try:
        inventory_after = _inventory_from_directory(
            set_dir,
            str(inventory["prefix"]),
            expected_names={str(item["name"]) for item in inventory["files"]},
        )
        if inventory_after["payload_sha256"] != maps_payload_before:
            integrity_issues.append("探针期间 maps payload SHA256 发生变化。")
    except (OSError, TypeError, ValueError) as exc:
        integrity_issues.append(f"探针结束后 maps 完整性校验失败：{exc}")
    binary_path = Path(tool_snapshot["path"])
    try:
        binary_after = _sha256(binary_path)
    except OSError as exc:
        binary_after = ""
        integrity_issues.append(f"探针结束后无法读取 Vina binary：{exc}")
    binary_stable = binary_after == tool_snapshot["sha256"]
    if not binary_stable:
        integrity_issues.append("探针期间 Vina binary SHA256 发生变化。")
    ok = bool(result["ok"]) and not integrity_issues
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
        "ligand_sha256": ligand_sha_before,
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
                "; ".join(integrity_issues)
                or str(result.get("error") or "")
                or f"Vina exit_code={result.get('exit_code')}",
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
    manifest_hash_before = _sha256(manifest_path)
    maps_payload_before = str(inventory["payload_sha256"])
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
    try:
        if _sha256(manifest_path) != manifest_hash_before:
            raise ValueError("兼容性探针期间 manifest SHA256 发生变化。")
        verified_manifest, verified_inventory = _validate_local_manifest(
            project,
            root,
            manifest_path,
            tool_snapshot,
        )
        if (
            str(verified_inventory["payload_sha256"]) != maps_payload_before
            or verified_manifest != manifest
        ):
            raise ValueError("兼容性探针期间 maps 或 manifest 内容发生变化。")
    except MapsManifestRebuildRequired as exc:
        return _operation_error(
            "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
            "所选 maps manifest 不满足当前科学合同，已保持只读且未激活。",
            str(exc),
            "请使用当前版本重新生成或重新导入 maps。",
            map_set_id=manifest["map_set_id"],
            manifest_file=manifest_path.relative_to(root).as_posix(),
            compatibility_probe=probe,
        )
    except (OSError, TypeError, ValueError) as exc:
        return _operation_error(
            "VINA_MAPS_ACTIVATION_INPUT_CHANGED",
            "兼容性探针期间 maps、manifest、受体、Box 或工具证据发生变化，未激活该 map set。",
            str(exc),
            "请确认没有其他程序修改项目文件后重新校验或重建 maps。",
            map_set_id=manifest["map_set_id"],
            manifest_file=manifest_path.relative_to(root).as_posix(),
            compatibility_probe=probe,
        )
    manifest = verified_manifest
    inventory = verified_inventory
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


def _validate_active_maps_locked(
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
        legacy_record = bool(record)
        missing_is_error = _grid_source(project) == "precomputed_maps"
        error = (
            _error_payload(
                (
                    "VINA_MAPS_RECORD_REBUILD_REQUIRED"
                    if legacy_record
                    else "VINA_MAPS_NOT_PREPARED"
                ),
                (
                    "项目中的旧 maps 记录缺少活动 manifest 及完整性证据，"
                    "不能自动推断或补写。"
                    if legacy_record
                    else "当前协议要求预计算 maps，但项目没有活动 manifest。"
                ),
                suggestion=(
                    "旧记录保持只读；请使用当前版本重新生成或重新导入 maps。"
                    if legacy_record
                    else "请先生成或导入 maps，或将网格来源切换回受体。"
                ),
            )
            if missing_is_error or legacy_record
            else None
        )
        return _status_payload(
            ok=not (missing_is_error or legacy_record),
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
    except MapsManifestRebuildRequired as exc:
        error = _error_payload(
            "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
            "活动 maps manifest 不满足当前科学合同，已保持只读。",
            str(exc),
            "请使用当前版本重新生成或重新导入 maps；DockStart 不会猜测缺失证据。",
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
            try:
                if _sha256(manifest_path) != expected_manifest_hash:
                    raise ValueError(
                        "兼容性探针期间活动 manifest SHA256 发生变化。"
                    )
                refreshed_manifest, refreshed_inventory = (
                    _validate_local_manifest(
                        project,
                        root,
                        manifest_path,
                        tool_snapshot,
                    )
                )
                if (
                    refreshed_inventory["payload_sha256"]
                    != inventory["payload_sha256"]
                    or refreshed_manifest != manifest
                ):
                    raise ValueError(
                        "兼容性探针期间活动 maps 或 manifest 发生变化。"
                    )
            except MapsManifestRebuildRequired as exc:
                error = _error_payload(
                    "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
                    "探针期间检测到 manifest 不再满足当前科学合同，未保存探针记录。",
                    str(exc),
                    "请使用当前版本重新生成或重新导入 maps。",
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
                    manifest=None,
                    maps_prefix="",
                    issues=[str(exc)],
                    compatibility_probe=probe,
                    tool=tool,
                    current_context=current_context,
                    error=error,
                )
            except (OSError, TypeError, ValueError) as exc:
                error = _error_payload(
                    "VINA_MAPS_PROBE_INPUT_CHANGED",
                    "兼容性探针期间 maps、manifest、受体、Box 或工具证据发生变化，未保存探针记录。",
                    str(exc),
                    "请确认没有其他程序修改项目文件后重新校验或重建 maps。",
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
                    manifest=None,
                    maps_prefix="",
                    issues=[str(exc)],
                    compatibility_probe=probe,
                    tool=tool,
                    current_context=current_context,
                    error=error,
                )
            manifest = refreshed_manifest
            inventory = refreshed_inventory
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


def validate_active_maps(
    project_dir: str,
    probe_ligand: bool = True,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return _run_with_maps_operation_lock(
        project_dir,
        lambda: _validate_active_maps_locked(
            project_dir,
            probe_ligand,
            runner=runner,
        ),
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


def _generate_maps_locked(
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
    manifest_relative = Path("maps", map_set_id, "manifest.json").as_posix()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    set_dir = root / "maps" / map_set_id
    try:
        temporary, set_dir = _new_staging_map_set(root, map_set_id)
        inputs_dir = set_dir / "inputs"
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
        manifest_path, manifest, inventory = _publish_staged_map_set(
            project=project,
            root=root,
            staging_set_dir=set_dir,
            map_set_id=map_set_id,
            tool_snapshot=tool_snapshot,
        )
        if activate:
            return _activate_newly_published_set(
                project=project,
                root=root,
                manifest_path=manifest_path,
                manifest=manifest,
                inventory=inventory,
                tool_snapshot=tool_snapshot,
                staging_parent=set_dir.parent,
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
    except Exception as exc:  # noqa: BLE001 - public boundary returns structured data.
        return _operation_error(
            "VINA_MAPS_GENERATION_FAILED",
            "Vina 未生成完整且可审计的预计算 maps；未发布半成品。",
            str(exc),
            "请检查 Vina 输出、输入快照、磁盘空间和目录权限后重试。",
            map_set_id=map_set_id,
            manifest_file="",
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


def generate_maps(
    project_dir: str,
    options: dict[str, Any] | None = None,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return _run_with_maps_operation_lock(
        project_dir,
        lambda: _generate_maps_locked(
            project_dir,
            options,
            runner=runner,
        ),
    )


def _activate_map_set_locked(
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
    except MapsManifestRebuildRequired as exc:
        return _operation_error(
            "VINA_MAPS_MANIFEST_REBUILD_REQUIRED",
            "所选 maps manifest 不满足当前科学合同，已保持只读且未激活。",
            str(exc),
            "请使用当前版本重新生成或重新导入 maps。",
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


def activate_map_set(
    project_dir: str,
    map_set_id: str,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return _run_with_maps_operation_lock(
        project_dir,
        lambda: _activate_map_set_locked(
            project_dir,
            map_set_id,
            runner=runner,
        ),
    )


def _set_grid_source_locked(
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
        return _activate_map_set_locked(project_dir, selected, runner=runner)
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


def set_grid_source(
    project_dir: str,
    mode: str,
    map_set_id: str = "",
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return _run_with_maps_operation_lock(
        project_dir,
        lambda: _set_grid_source_locked(
            project_dir,
            mode,
            map_set_id,
            runner=runner,
        ),
    )


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
    except MapsManifestRebuildRequired as exc:
        return _operation_error(
            "VINA_MAPS_IMPORT_MANIFEST_REBUILD_REQUIRED",
            "所选来源 manifest 不满足当前科学合同，已保持只读且未导入。",
            str(exc),
            "请在来源项目中用当前版本重新生成 maps，再重新导入。",
        )
    except (OSError, TypeError, ValueError) as exc:
        return _operation_error(
            "VINA_MAPS_IMPORT_MANIFEST_INVALID",
            "所选 DockStart maps manifest 未通过来源、完整性或项目绑定校验。",
            str(exc),
        )
    map_set_id = _next_map_set_id(root)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    set_dir = root / "maps" / map_set_id
    try:
        temporary, set_dir = _new_staging_map_set(root, map_set_id)
        inputs_dir = set_dir / "inputs"
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
        manifest_path, manifest, local_inventory = _publish_staged_map_set(
            project=project,
            root=root,
            staging_set_dir=set_dir,
            map_set_id=map_set_id,
            tool_snapshot=tool_snapshot,
        )
        if activate:
            return _activate_newly_published_set(
                project=project,
                root=root,
                manifest_path=manifest_path,
                manifest=manifest,
                inventory=local_inventory,
                tool_snapshot=tool_snapshot,
                staging_parent=set_dir.parent,
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
            "复制或登记 DockStart maps 时发生错误；未发布半成品。",
            str(exc),
            map_set_id=map_set_id,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


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
    temporary: tempfile.TemporaryDirectory[str] | None = None
    set_dir = root / "maps" / map_set_id
    try:
        temporary, set_dir = _new_staging_map_set(root, map_set_id)
        inputs_dir = set_dir / "inputs"
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
        manifest_path, manifest, local_inventory = _publish_staged_map_set(
            project=project,
            root=root,
            staging_set_dir=set_dir,
            map_set_id=map_set_id,
            tool_snapshot=tool_snapshot,
        )
        if activate:
            return _activate_newly_published_set(
                project=project,
                root=root,
                manifest_path=manifest_path,
                manifest=manifest,
                inventory=local_inventory,
                tool_snapshot=tool_snapshot,
                staging_parent=set_dir.parent,
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
            "复制或登记原始 maps 时发生错误；未发布半成品。",
            str(exc),
            map_set_id=map_set_id,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()


def _import_maps_locked(
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


def import_maps(
    project_dir: str,
    source_path: str,
    options: dict[str, Any] | None = None,
    *,
    runner: RunCallable | None = None,
) -> dict[str, Any]:
    return _run_with_maps_operation_lock(
        project_dir,
        lambda: _import_maps_locked(
            project_dir,
            source_path,
            options,
            runner=runner,
        ),
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
