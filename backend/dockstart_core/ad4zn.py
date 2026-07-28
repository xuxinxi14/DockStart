"""AD4Zn beta receptor-preparation domain layer.

This module deliberately stops at auditable AD4Zn input preparation.  It does
not activate AutoGrid4, generate maps, or run docking.  Parameter data remains
an explicitly user-provided GPL asset and is never embedded in DockStart.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dockstart_core.persistence import atomic_write_bytes, atomic_write_text
from dockstart_core.project import _project_from_dict, load_project, save_project

PROTOCOL_ID = "ad4zn_beta"
STATE_SCHEMA_VERSION = 1
ALGORITHM_VERSION = "1.2"
ALGORITHM_NAME = "dockstart_vina_1_2_7_zinc_pseudo_compatible"
ALGORITHM_UPSTREAM_REFERENCE = (
    "https://github.com/ccsb-scripps/AutoDock-Vina/blob/"
    "v1.2.7/example/autodock_scripts/zinc_pseudo.py"
)
ALGORITHM_REFERENCE_SHA256 = (
    "ccfa97e10614b30d32839935d3fc72a3038d43206caf5c1e7f9e0da96693e88d"
)
MAX_RECEPTOR_BYTES = 100 * 1024 * 1024
MAX_PARAMETER_FILE_BYTES = 5 * 1024 * 1024
MAX_REVIEW_JSON_BYTES = 64 * 1024
NEIGHBOR_SEARCH_ANGSTROM = 4.5
COORDINATION_CUTOFF_ANGSTROM = 2.5
TZ_DISTANCE_ANGSTROM = 2.0
TZ_CHARGE = 0.0
CARBOXYLATE_AVERAGING_EXPONENT = 0.5
MIN_ZN_PLANE_SEPARATION_DEGREES = 1.0
COORDINATION_ATOM_TYPES = {"O", "OA", "NA", "N", "S", "SA"}
AUTODOCK_VDW_DIAMETERS = {
    "H": 2.0,
    "HD": 2.0,
    "HS": 2.0,
    "C": 4.0,
    "A": 4.0,
    "N": 3.5,
    "NA": 3.5,
    "NS": 3.5,
    "OA": 3.2,
    "OS": 3.2,
    "O": 3.2,
    "F": 3.1,
    "MG": 1.3,
    "P": 4.2,
    "S": 4.0,
    "SA": 4.0,
    "CL": 4.1,
    "CA": 2.0,
    "MN": 1.3,
    "FE": 1.3,
    "ZN": 1.5,
    "BR": 4.3,
    "I": 4.7,
    "Z": 4.0,
    "G": 4.0,
    "J": 4.0,
    "GA": 4.0,
    "Q": 4.0,
}
PREPARED_RECEPTOR_RELATIVE_PATH = "ad4zn/prepared/receptor_tz.pdbqt"
PARAMETER_RELATIVE_PATH = "ad4zn/parameters/AD4Zn.dat"
REQUIRED_CONFIRMATIONS = (
    "target",
    "coordination",
    "protonation",
    "water",
    "cofactor",
    "tz_pseudoatom",
    "zinc_only_scope",
)
CONFIRMATION_LABELS = {
    "target": "已确认目标锌位点",
    "coordination": "已核对配位原子与配位数",
    "protonation": "已核对受体质子化状态",
    "water": "已核对结晶水的保留或移除",
    "cofactor": "已核对辅因子及共结晶组分",
    "tz_pseudoatom": "已知晓将生成 TZ 伪原子",
    "zinc_only_scope": "已确认当前 beta 协议仅处理单核锌位点",
}
EXPECTED_FE_COEFFICIENTS = (
    "FE_coeff_vdW",
    "FE_coeff_hbond",
    "FE_coeff_estat",
    "FE_coeff_desolv",
    "FE_coeff_tors",
)
_EXPECTED_FE_CASEFOLD = {item.casefold(): item for item in EXPECTED_FE_COEFFICIENTS}
EXPECTED_FE_COEFFICIENT_VALUES = {
    "FE_coeff_vdW": 0.1662,
    "FE_coeff_hbond": 0.1209,
    "FE_coeff_estat": 0.1406,
    "FE_coeff_desolv": 0.1322,
    "FE_coeff_tors": 0.2983,
}
EXPECTED_AD4ZN_ATOM_PARAMETERS = {
    "ZN": (1.48, 0.550, 1.7000, -0.00110, 0.0, 0.0, 0.0, -1.0, -1.0, 4.0),
    "TZ": (1.00, 0.000, 0.0000, 0.00000, 0.0, 0.0, 0.0, -1.0, -1.0, 0.0),
}
SUPPORTED_PARAMETER_PROFILE_ID = "autodock_vina_1_2_7_ad4zn"
SUPPORTED_PARAMETER_UPSTREAM_REFERENCE = (
    "https://github.com/ccsb-scripps/AutoDock-Vina/blob/"
    "v1.2.7/data/AD4Zn.dat"
)
SUPPORTED_PARAMETER_REFERENCE_SHA256 = (
    "12b45d377f081c9f3dc25fba2d4585bb01b8367023f309769e441b8457fb1c00"
)
SUPPORTED_PARAMETER_LICENSE_ID = "GPL-2.0-or-later"
_METAL_TYPES = {
    "CA": "Ca",
    "CO": "Co",
    "CU": "Cu",
    "FE": "Fe",
    "MG": "Mg",
    "MN": "Mn",
    "NI": "Ni",
    "ZN": "Zn",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error_object(
    code: str,
    title: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, str]:
    return {
        "code": code,
        "title": title,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }


def _error(
    code: str,
    title: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "project": None,
        "error": _error_object(code, title, message, raw_error, suggestion),
    }


def _issue(
    code: str,
    title: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
    blocking: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = _error_object(code, title, message, raw_error, suggestion)
    payload["blocking"] = blocking
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _load_project_model(project_dir: str) -> tuple[Any | None, Path | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, None, loaded
    root = Path(project_dir).expanduser().resolve()
    try:
        return _project_from_dict(loaded["project"], root), root, None
    except Exception as exc:  # noqa: BLE001 - converted into a stable API error.
        return None, None, _error(
            "AD4ZN_PROJECT_INVALID",
            "项目数据无效",
            "project.json 无法用于 AD4Zn beta 准备。",
            str(exc),
            "请先修复或迁移项目文件。",
        )


def _state_from_project(project: Any) -> dict[str, Any]:
    raw = project.preserved_data.get("ad4zn")
    state = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    state["protocol_id"] = PROTOCOL_ID
    state["schema_version"] = STATE_SCHEMA_VERSION
    return state


def _store_state(project: Any, state: dict[str, Any]) -> None:
    state = copy.deepcopy(state)
    state["protocol_id"] = PROTOCOL_ID
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = _now_iso()
    project.preserved_data["ad4zn"] = state


def _contained_project_path(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool,
) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(relative_path or "").strip()
    if not text:
        return None, _error(
            "AD4ZN_RECEPTOR_NOT_SET",
            "尚未设置受体",
            "当前项目没有可用于 AD4Zn 分析的受体 PDBQT。",
            suggestion="请先导入或准备受体 PDBQT。",
        )
    relative = Path(text)
    if relative.is_absolute() or relative.drive:
        return None, _error(
            "AD4ZN_PROJECT_PATH_INVALID",
            "项目路径无效",
            "AD4Zn 项目资产必须使用项目内相对路径。",
            text,
        )
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        return None, _error(
            "AD4ZN_PROJECT_PATH_UNSAFE",
            "项目路径不安全",
            "AD4Zn 拒绝读取或写入项目目录外的文件。",
            str(exc),
        )
    if must_exist:
        try:
            if candidate.is_symlink() or resolved != candidate.absolute():
                return None, _error(
                    "AD4ZN_PROJECT_PATH_UNSAFE",
                    "项目路径不安全",
                    "AD4Zn 项目资产不能是符号链接或重解析路径。",
                    str(candidate),
                )
        except OSError as exc:
            return None, _error(
                "AD4ZN_PROJECT_PATH_UNSAFE",
                "项目路径不安全",
                "无法确认 AD4Zn 项目资产的真实路径。",
                str(exc),
            )
    return resolved, None


def _safe_output_path(root: Path, relative_path: str) -> tuple[Path | None, dict[str, Any] | None]:
    relative = Path(relative_path)
    if relative.is_absolute() or relative.drive:
        return None, _error(
            "AD4ZN_OUTPUT_PATH_INVALID",
            "输出路径无效",
            "AD4Zn 输出必须位于项目目录内。",
            relative_path,
        )
    target = root / relative
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        return None, _error(
            "AD4ZN_OUTPUT_PATH_UNSAFE",
            "输出路径不安全",
            "AD4Zn 拒绝写入项目目录外。",
            str(exc),
        )
    current = root
    try:
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists():
                if current.is_symlink() or current.resolve(strict=True) != current.absolute():
                    return None, _error(
                        "AD4ZN_OUTPUT_PATH_UNSAFE",
                        "输出路径不安全",
                        "AD4Zn 输出目录不能是符号链接或重解析路径。",
                        str(current),
                    )
            else:
                current.mkdir()
        if target.exists() and (
            target.is_symlink() or target.resolve(strict=True) != target.absolute()
        ):
            return None, _error(
                "AD4ZN_OUTPUT_PATH_UNSAFE",
                "输出路径不安全",
                "AD4Zn 输出文件不能是符号链接或重解析路径。",
                str(target),
            )
    except OSError as exc:
        return None, _error(
            "AD4ZN_OUTPUT_DIRECTORY_ERROR",
            "无法创建输出目录",
            "AD4Zn 无法创建安全的项目输出目录。",
            str(exc),
            "请确认项目目录可写且未被其他程序占用。",
        )
    return target, None


def _read_text_file(
    path: Path,
    *,
    maximum_bytes: int,
    empty_code: str,
    too_large_code: str,
    encoding_code: str,
) -> tuple[str | None, bytes | None, dict[str, Any] | None]:
    try:
        size = path.stat().st_size
        if size <= 0:
            return None, None, _error(
                empty_code,
                "文件为空",
                "文件为空，无法继续处理。",
                str(path),
            )
        if size > maximum_bytes:
            return None, None, _error(
                too_large_code,
                "文件过大",
                f"文件超过允许的 {maximum_bytes} 字节上限。",
                f"{size} bytes",
            )
        payload = path.read_bytes()
    except OSError as exc:
        return None, None, _error(
            "AD4ZN_FILE_READ_ERROR",
            "无法读取文件",
            "读取 AD4Zn 输入文件时发生错误。",
            str(exc),
        )
    if b"\x00" in payload:
        return None, None, _error(
            encoding_code,
            "文件不是文本",
            "文件包含 NUL 字节，不能作为文本输入。",
            str(path),
        )
    try:
        return payload.decode("utf-8-sig"), payload, None
    except UnicodeDecodeError as exc:
        return None, None, _error(
            encoding_code,
            "文本编码无效",
            "文件必须是 UTF-8/ASCII 文本。",
            str(exc),
        )


def _float_or_none(value: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int_or_default(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coordinate(x: float, y: float, z: float) -> dict[str, float]:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _parse_atom_line(line: str, line_number: int) -> dict[str, Any] | None:
    record_type = line[:6].strip().upper()
    if record_type not in {"ATOM", "HETATM"}:
        return None
    body = line.rstrip("\r\n")
    parts = body.split()

    serial = _int_or_default(body[6:11].strip() if len(body) >= 11 else "", line_number)
    name = body[12:16].strip() if len(body) >= 16 else ""
    altloc = body[16:17].strip() if len(body) >= 17 else ""
    residue_name = body[17:20].strip() if len(body) >= 20 else ""
    chain = body[21:22].strip() if len(body) >= 22 else ""
    residue_number = body[22:26].strip() if len(body) >= 26 else ""
    insertion_code = body[26:27].strip() if len(body) >= 27 else ""
    if not name and len(parts) >= 3:
        name = parts[2]
    if not residue_name and len(parts) >= 4:
        residue_name = parts[3]

    x = _float_or_none(body[30:38].strip() if len(body) >= 38 else "")
    y = _float_or_none(body[38:46].strip() if len(body) >= 46 else "")
    z = _float_or_none(body[46:54].strip() if len(body) >= 54 else "")
    if (x is None or y is None or z is None) and len(parts) >= 9:
        x = _float_or_none(parts[6])
        y = _float_or_none(parts[7])
        z = _float_or_none(parts[8])
    if x is None or y is None or z is None:
        return {
            "_parse_error": True,
            "record_type": record_type,
            "line_number": line_number,
            "raw": body,
        }

    atom_type = body[77:79].strip() if len(body) >= 79 else ""
    if not atom_type and parts:
        atom_type = parts[-1]
    charge = _float_or_none(body[68:76].strip() if len(body) >= 76 else "")
    if charge is None and len(parts) >= 2:
        charge = _float_or_none(parts[-2])

    atom_id = (
        f"{record_type}:{serial}:{chain or '-'}:{residue_name or '-'}:"
        f"{residue_number or '-'}{insertion_code}:{name or '-'}:L{line_number}"
    )
    return {
        "atom_id": atom_id,
        "record_type": record_type,
        "serial": serial,
        "name": name,
        "altloc": altloc,
        "residue_name": residue_name,
        "chain": chain,
        "residue_number": residue_number,
        "insertion_code": insertion_code,
        "atom_type": atom_type,
        "charge": charge,
        "coordinate": _coordinate(x, y, z),
        "line_number": line_number,
        "_raw_line": line,
    }


def _public_atom(atom: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in atom.items()
        if not key.startswith("_")
    }


def _type_key(atom: dict[str, Any]) -> str:
    value = str(atom.get("atom_type") or "").strip().upper()
    if value:
        return value
    for fallback in (atom.get("name"), atom.get("residue_name")):
        text = re.sub(r"[^A-Za-z]", "", str(fallback or "")).upper()
        if text in _METAL_TYPES or text == "TZ":
            return text
    return ""


def _metal_symbol(atom: dict[str, Any]) -> str:
    return _METAL_TYPES.get(_type_key(atom), "")


def _distance(left: dict[str, float], right: dict[str, float]) -> float:
    return math.sqrt(
        (left["x"] - right["x"]) ** 2
        + (left["y"] - right["y"]) ** 2
        + (left["z"] - right["z"]) ** 2
    )


def _site_id(atom: dict[str, Any]) -> str:
    return (
        f"ZN:{atom.get('serial')}:{atom.get('chain') or '-'}:"
        f"{atom.get('residue_name') or '-'}:{atom.get('residue_number') or '-'}"
        f"{atom.get('insertion_code') or ''}:L{atom.get('line_number')}"
    )


def _is_coordination_element(atom: dict[str, Any]) -> bool:
    return _type_key(atom) in COORDINATION_ATOM_TYPES


def _vector(
    start: dict[str, float],
    end: dict[str, float],
) -> tuple[float, float, float]:
    return (
        end["x"] - start["x"],
        end["y"] - start["y"],
        end["z"] - start["z"],
    )


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _norm(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _autodock_bonded(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    """Match the distance-based bond test used by Vina's zinc_pseudo.py."""

    left_diameter = AUTODOCK_VDW_DIAMETERS.get(_type_key(left))
    right_diameter = AUTODOCK_VDW_DIAMETERS.get(_type_key(right))
    if left_diameter is None or right_diameter is None:
        return False
    threshold = 0.25 * (left_diameter + right_diameter)
    return _distance(left["coordinate"], right["coordinate"]) < threshold


def _bond_graph(atoms: list[dict[str, Any]]) -> dict[int, set[int]]:
    graph = {index: set() for index in range(len(atoms))}
    for left_index, left in enumerate(atoms):
        for right_index in range(left_index + 1, len(atoms)):
            if not _autodock_bonded(left, atoms[right_index]):
                continue
            graph[left_index].add(right_index)
            graph[right_index].add(left_index)
    return graph


def _carboxylate_triplets(
    atoms: list[dict[str, Any]],
    graph: dict[int, set[int]],
) -> list[tuple[int, int, int]]:
    """Find C(O)O groups using the official connectivity criteria."""

    triplets: list[tuple[int, int, int]] = []
    used_oxygen: set[int] = set()
    for carbon_index, carbon in enumerate(atoms):
        if _type_key(carbon) not in {"C", "A"}:
            continue
        oxygen_indices = sorted(
            (
                neighbor
                for neighbor in graph.get(carbon_index, set())
                if _type_key(atoms[neighbor]) in {"O", "OA"}
                and len(graph.get(neighbor, set())) == 1
                and neighbor not in used_oxygen
            ),
            key=lambda index: int(atoms[index]["line_number"]),
        )
        if len(oxygen_indices) < 2:
            continue
        best_pair = min(
            (
                (left, right)
                for offset, left in enumerate(oxygen_indices[:-1])
                for right in oxygen_indices[offset + 1 :]
            ),
            key=lambda pair: (
                _distance(
                    atoms[carbon_index]["coordinate"],
                    atoms[pair[0]]["coordinate"],
                )
                + _distance(
                    atoms[carbon_index]["coordinate"],
                    atoms[pair[1]]["coordinate"],
                ),
                int(atoms[pair[0]]["line_number"]),
                int(atoms[pair[1]]["line_number"]),
            ),
        )
        used_oxygen.update(best_pair)
        triplets.append((carbon_index, best_pair[0], best_pair[1]))
    return triplets


def _weighted_carboxylate_coordinate(
    first: dict[str, Any],
    zinc: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, float] | None:
    oxygen_distance = _distance(
        first["coordinate"],
        second["coordinate"],
    )
    if oxygen_distance <= 1e-9:
        return None
    first_distance = _distance(first["coordinate"], zinc["coordinate"])
    second_distance = _distance(second["coordinate"], zinc["coordinate"])
    normalized_delta = (
        second_distance - first_distance
    ) / oxygen_distance
    signed_ratio = math.copysign(
        abs(normalized_delta) ** CARBOXYLATE_AVERAGING_EXPONENT,
        normalized_delta,
    )
    weight = (1.0 - signed_ratio) / 2.0
    first_to_second = _vector(first["coordinate"], second["coordinate"])
    return _coordinate(
        first["coordinate"]["x"] + first_to_second[0] * weight,
        first["coordinate"]["y"] + first_to_second[1] * weight,
        first["coordinate"]["z"] + first_to_second[2] * weight,
    )


def _reach_through_three_bonds(
    graph: dict[int, set[int]],
) -> dict[int, set[int]]:
    reach: dict[int, set[int]] = {}
    for index, direct in graph.items():
        connected = set(direct)
        for neighbor in direct:
            connected.update(graph.get(neighbor, set()))
        connected.discard(index)
        reach[index] = connected
    return reach


def _tz_direction_from_coordination_plane(
    zinc: dict[str, Any],
    coordinates: list[dict[str, float]],
) -> tuple[
    tuple[float, float, float] | None,
    str,
    float | None,
    float | None,
]:
    """Return the official plane-normal TZ direction, on Zn's open side."""

    first, second, third = coordinates
    normal = _cross(
        _vector(first, second),
        _vector(second, third),
    )
    normal_length = _norm(normal)
    if normal_length <= 1e-9:
        return (
            None,
            "三个配位组的代表坐标共线，无法确定 TZ 平面法向。",
            None,
            None,
        )
    first_to_zinc = _vector(first, zinc["coordinate"])
    zinc_side = _dot(first_to_zinc, normal)
    plane_separation_angstrom = abs(zinc_side) / normal_length
    mean_coordination_distance = sum(
        _distance(zinc["coordinate"], coordinate)
        for coordinate in coordinates
    ) / len(coordinates)
    if mean_coordination_distance <= 1e-9:
        return (
            None,
            "Zn 与配位代表点重合，无法确定 TZ 平面法向。",
            None,
            plane_separation_angstrom,
        )
    plane_separation_ratio = min(
        1.0,
        plane_separation_angstrom / mean_coordination_distance,
    )
    plane_separation_degrees = math.degrees(math.asin(plane_separation_ratio))
    if plane_separation_degrees < MIN_ZN_PLANE_SEPARATION_DEGREES:
        return (
            None,
            (
                "Zn 与三个配位代表点近乎共面，TZ 法向侧别不稳定；"
                "当前 beta 不自动生成。"
            ),
            plane_separation_degrees,
            plane_separation_angstrom,
        )
    orientation = 1.0 if zinc_side > 0 else -1.0
    return (
        (
            orientation * normal[0] / normal_length,
            orientation * normal[1] / normal_length,
            orientation * normal[2] / normal_length,
        ),
        "",
        plane_separation_degrees,
        plane_separation_angstrom,
    )


def _analyse_site(zn: dict[str, Any], ordinary_atoms: list[dict[str, Any]]) -> dict[str, Any]:
    nearby_metals = sorted(
        (
            atom
            for atom in ordinary_atoms
            if atom["line_number"] != zn["line_number"]
            and _metal_symbol(atom)
            and _distance(zn["coordinate"], atom["coordinate"])
            < NEIGHBOR_SEARCH_ANGSTROM
        ),
        key=lambda atom: (
            _distance(zn["coordinate"], atom["coordinate"]),
            int(atom["line_number"]),
        ),
    )
    nearby = sorted(
        (
            atom
            for atom in ordinary_atoms
            if atom["line_number"] != zn["line_number"]
            and not _metal_symbol(atom)
            and _distance(zn["coordinate"], atom["coordinate"])
            < NEIGHBOR_SEARCH_ANGSTROM
        ),
        key=lambda atom: (
            _distance(zn["coordinate"], atom["coordinate"]),
            int(atom["line_number"]),
        ),
    )
    initial_graph = _bond_graph(nearby)
    carboxylates = _carboxylate_triplets(nearby, initial_graph)
    carboxylate_indices = {
        index for triplet in carboxylates for index in triplet
    }
    remaining = [
        atom
        for index, atom in enumerate(nearby)
        if index not in carboxylate_indices
    ]
    remaining_graph = _bond_graph(remaining)
    reach = _reach_through_three_bonds(remaining_graph)
    removed: set[int] = set()
    selected_atoms: list[dict[str, Any]] = []
    for index, atom in enumerate(remaining):
        if (
            index in removed
            or not _is_coordination_element(atom)
            or _distance(zn["coordinate"], atom["coordinate"])
            > COORDINATION_CUTOFF_ANGSTROM
        ):
            continue
        selected_atoms.append(atom)
        removed.update(reach.get(index, set()))

    coordination_groups: list[dict[str, Any]] = [
        {
            "group_id": f"atom:{atom['atom_id']}",
            "kind": "atom",
            "representative_coordinate": copy.deepcopy(atom["coordinate"]),
            "representative_distance": round(
                _distance(zn["coordinate"], atom["coordinate"]),
                6,
            ),
            "members": [_public_atom(atom)],
        }
        for atom in selected_atoms
    ]
    for carbon_index, first_index, second_index in carboxylates:
        first = nearby[first_index]
        second = nearby[second_index]
        representative = _weighted_carboxylate_coordinate(
            first,
            zn,
            second,
        )
        if representative is None:
            continue
        representative_distance = _distance(
            zn["coordinate"],
            representative,
        )
        if representative_distance > COORDINATION_CUTOFF_ANGSTROM:
            continue
        carbon = nearby[carbon_index]
        coordination_groups.append(
            {
                "group_id": f"carboxylate-carbon:{carbon['atom_id']}",
                "kind": "carboxylate",
                "representative_coordinate": representative,
                "representative_distance": round(
                    representative_distance,
                    6,
                ),
                "members": [
                    _public_atom(first),
                    _public_atom(second),
                ],
                "averaging_exponent": CARBOXYLATE_AVERAGING_EXPONENT,
            }
        )

    coordinating_atom_ids = {
        str(member.get("atom_id") or "")
        for group in coordination_groups
        for member in group["members"]
        if isinstance(member, dict)
    }
    neighbor_rows = [
        {
            "atom": _public_atom(atom),
            "distance": round(
                _distance(zn["coordinate"], atom["coordinate"]),
                6,
            ),
            "eligible": _is_coordination_element(atom),
            "coordinating": atom["atom_id"] in coordinating_atom_ids,
            "excluded_by_connectivity": (
                _is_coordination_element(atom)
                and _distance(zn["coordinate"], atom["coordinate"])
                <= COORDINATION_CUTOFF_ANGSTROM
                and atom["atom_id"] not in coordinating_atom_ids
            ),
        }
        for atom in nearby
    ]

    geometry_error = ""
    plane_separation_degrees: float | None = None
    plane_separation_angstrom: float | None = None
    can_generate = len(coordination_groups) == 3 and not nearby_metals
    tz_candidate: dict[str, Any] | None = None
    if can_generate:
        (
            direction,
            geometry_error,
            plane_separation_degrees,
            plane_separation_angstrom,
        ) = _tz_direction_from_coordination_plane(
            zn,
            [
                group["representative_coordinate"]
                for group in coordination_groups
            ],
        )
        if direction is None:
            can_generate = False
        else:
            tz_coordinate = _coordinate(
                zn["coordinate"]["x"] + TZ_DISTANCE_ANGSTROM * direction[0],
                zn["coordinate"]["y"] + TZ_DISTANCE_ANGSTROM * direction[1],
                zn["coordinate"]["z"] + TZ_DISTANCE_ANGSTROM * direction[2],
            )
            tz_candidate = {
                "coordinate": tz_coordinate,
                "distance": TZ_DISTANCE_ANGSTROM,
                "charge": TZ_CHARGE,
                "direction": {
                    "x": direction[0],
                    "y": direction[1],
                    "z": direction[2],
                },
                "method": "coordination_plane_normal",
            }

    if nearby_metals:
        geometry_error = (
            "目标 Zn 的 4.5 Å 邻域内检测到其他金属；"
            "当前 beta 不自动处理多核金属位点。"
        )
    elif len(coordination_groups) != 3:
        geometry_error = (
            f"检测到 {len(coordination_groups)} 个配位组；"
            "当前 beta 算法只为三配位 Zn 生成 TZ。"
        )
    return {
        "site_id": _site_id(zn),
        "zn": _public_atom(zn),
        "neighbors": neighbor_rows,
        "nearby_metals": [
            {
                "atom": _public_atom(atom),
                "distance": round(
                    _distance(zn["coordinate"], atom["coordinate"]),
                    6,
                ),
            }
            for atom in nearby_metals
        ],
        "coordination_groups": coordination_groups,
        "coordination_number": len(coordination_groups),
        "can_generate": can_generate,
        "tz_candidate": tz_candidate,
        "geometry_message": "" if can_generate else geometry_error,
        "plane_separation_degrees": (
            round(plane_separation_degrees, 6)
            if plane_separation_degrees is not None
            else None
        ),
        "plane_separation_angstrom": (
            round(plane_separation_angstrom, 6)
            if plane_separation_angstrom is not None
            else None
        ),
        "limits": {
            "neighbor_search_angstrom": NEIGHBOR_SEARCH_ANGSTROM,
            "coordination_cutoff_angstrom": COORDINATION_CUTOFF_ANGSTROM,
            "tz_distance_angstrom": TZ_DISTANCE_ANGSTROM,
            "minimum_plane_separation_degrees": (
                MIN_ZN_PLANE_SEPARATION_DEGREES
            ),
        },
    }


def _parse_receptor_text(text: str) -> dict[str, Any]:
    lines = text.splitlines(keepends=True)
    atoms: list[dict[str, Any]] = []
    malformed_atom_lines: list[int] = []
    for line_number, line in enumerate(lines, start=1):
        atom = _parse_atom_line(line, line_number)
        if atom is None:
            continue
        if atom.get("_parse_error"):
            malformed_atom_lines.append(line_number)
            continue
        atoms.append(atom)
    ordinary_atoms = [item for item in atoms if _type_key(item) != "TZ"]
    existing_tz = [item for item in atoms if _type_key(item) == "TZ"]
    zinc_atoms = [item for item in ordinary_atoms if _metal_symbol(item) == "Zn"]
    other_metals = [
        item
        for item in ordinary_atoms
        if _metal_symbol(item) and _metal_symbol(item) != "Zn"
    ]
    sites = [_analyse_site(item, ordinary_atoms) for item in zinc_atoms]
    return {
        "lines": lines,
        "atoms": atoms,
        "ordinary_atoms": ordinary_atoms,
        "sites": sites,
        "other_metals": [_public_atom(item) for item in other_metals],
        "existing_tz": [_public_atom(item) for item in existing_tz],
        "malformed_atom_lines": malformed_atom_lines,
    }


def _load_receptor(
    project: Any,
    root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path, path_error = _contained_project_path(
        root,
        str(project.receptor.file or ""),
        must_exist=True,
    )
    if path_error or path is None:
        return None, path_error
    if not path.is_file():
        return None, _error(
            "AD4ZN_RECEPTOR_MISSING",
            "受体文件不存在",
            "没有找到当前项目的受体 PDBQT。",
            str(path),
            "请重新导入或准备受体。",
        )
    text, payload, read_error = _read_text_file(
        path,
        maximum_bytes=MAX_RECEPTOR_BYTES,
        empty_code="AD4ZN_RECEPTOR_EMPTY",
        too_large_code="AD4ZN_RECEPTOR_TOO_LARGE",
        encoding_code="AD4ZN_RECEPTOR_TEXT_INVALID",
    )
    if read_error or text is None or payload is None:
        return None, read_error
    parsed = _parse_receptor_text(text)
    if parsed["malformed_atom_lines"]:
        return None, _error(
            "AD4ZN_RECEPTOR_COORDINATE_INVALID",
            "受体坐标无效",
            "受体 PDBQT 中存在无法解析坐标的 ATOM/HETATM 记录。",
            ", ".join(str(item) for item in parsed["malformed_atom_lines"]),
            "请检查受体 PDBQT 的坐标列。",
        )
    if not parsed["atoms"]:
        return None, _error(
            "AD4ZN_RECEPTOR_ATOMS_MISSING",
            "受体没有原子记录",
            "受体 PDBQT 中没有可读取的 ATOM/HETATM 记录。",
            str(path),
        )
    return {
        "path": path,
        "relative_path": Path(str(project.receptor.file)).as_posix(),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "text": text,
        "bytes": payload,
        "parsed": parsed,
    }, None


def _confirmations_from_payload(payload: dict[str, Any]) -> tuple[dict[str, bool] | None, dict[str, Any] | None]:
    raw = payload.get("confirmations")
    if raw is None:
        raw = payload
    if not isinstance(raw, dict):
        return None, _error(
            "AD4ZN_CONFIRMATIONS_INVALID",
            "确认项格式无效",
            "AD4Zn 审查确认项必须是 JSON 对象。",
        )
    missing = [key for key in REQUIRED_CONFIRMATIONS if raw.get(key) is not True]
    if missing:
        labels = "；".join(CONFIRMATION_LABELS[key] for key in missing)
        return None, _error(
            "AD4ZN_CONFIRMATIONS_REQUIRED",
            "尚未完成必要确认",
            "保存 AD4Zn 审查前，七项确认都必须由用户明确勾选。",
            labels,
            "请逐项核对目标、配位、质子化、水、辅因子、TZ 和仅锌范围。",
        )
    return {key: True for key in REQUIRED_CONFIRMATIONS}, None


def _review_binding_payload(
    receptor_sha256: str,
    selected_site_id: str,
    confirmations: dict[str, bool],
) -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "receptor_sha256": receptor_sha256,
        "selected_site_id": selected_site_id,
        "confirmations": {key: confirmations.get(key) is True for key in REQUIRED_CONFIRMATIONS},
    }


def _review_validity(
    review: Any,
    receptor_sha256: str,
    site_ids: set[str],
) -> tuple[bool, str]:
    if not isinstance(review, dict):
        return False, "尚未保存 AD4Zn 审查。"
    if review.get("protocol_id") != PROTOCOL_ID:
        return False, "审查记录不属于当前 AD4Zn beta 协议。"
    if str(review.get("receptor_sha256") or "") != receptor_sha256:
        return False, "当前受体 SHA256 已变化。"
    selected = str(review.get("selected_site_id") or "")
    if not selected or selected not in site_ids:
        return False, "审查选择的 Zn 位点在当前受体中不存在。"
    confirmations = review.get("confirmations")
    if not isinstance(confirmations, dict) or any(
        confirmations.get(key) is not True for key in REQUIRED_CONFIRMATIONS
    ):
        return False, "审查记录缺少七项明确确认。"
    expected = _canonical_json_sha256(
        _review_binding_payload(receptor_sha256, selected, confirmations)
    )
    if str(review.get("review_binding_sha256") or "") != expected:
        return False, "审查绑定摘要无效。"
    return True, ""


def _valid_recorded_file(
    root: Path,
    record: Any,
    *,
    expected_suffix: str = "",
) -> tuple[bool, str]:
    if not isinstance(record, dict):
        return False, "尚未记录文件。"
    relative_path = str(record.get("relative_path") or "")
    expected_hash = str(record.get("sha256") or "").lower()
    if len(expected_hash) != 64 or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return False, "记录的 SHA256 无效。"
    if expected_suffix and not relative_path.lower().endswith(expected_suffix.lower()):
        return False, "记录的文件类型无效。"
    path, path_error = _contained_project_path(root, relative_path, must_exist=True)
    if path_error or path is None or not path.is_file():
        return False, "记录的文件不存在或路径不安全。"
    try:
        size = path.stat().st_size
        if size <= 0 or size != int(record.get("size_bytes") or 0):
            return False, "记录的文件大小已变化。"
        if _sha256(path) != expected_hash:
            return False, "记录的文件 SHA256 已变化。"
    except (OSError, TypeError, ValueError):
        return False, "无法校验记录的文件。"
    return True, ""


def _prepared_validity(
    root: Path,
    record: Any,
    *,
    receptor_sha256: str,
    review: dict[str, Any] | None,
    review_valid: bool,
) -> tuple[bool, str]:
    if not isinstance(record, dict) or Path(
        str(record.get("relative_path") or "")
    ).as_posix() != PREPARED_RECEPTOR_RELATIVE_PATH:
        return False, "派生受体路径不是 AD4Zn 固定项目路径。"
    valid_file, reason = _valid_recorded_file(root, record, expected_suffix=".pdbqt")
    if not valid_file:
        return False, reason
    if not isinstance(record, dict):
        return False, "派生受体记录无效。"
    if record.get("protocol_id") != PROTOCOL_ID:
        return False, "派生受体不属于当前 AD4Zn beta 协议。"
    if str(record.get("input_sha256") or "") != receptor_sha256:
        return False, "派生受体绑定的输入受体已变化。"
    if str(record.get("algorithm_version") or "") != ALGORITHM_VERSION:
        return False, "派生受体使用了不同的算法版本。"
    if (
        record.get("algorithm_reference") != ALGORITHM_UPSTREAM_REFERENCE
        or record.get("algorithm_reference_sha256")
        != ALGORITHM_REFERENCE_SHA256
    ):
        return False, "派生受体缺少受支持算法的固定上游参考。"
    if not review_valid or not isinstance(review, dict):
        return False, "派生受体对应的审查已失效。"
    if str(record.get("review_binding_sha256") or "") != str(
        review.get("review_binding_sha256") or ""
    ):
        return False, "派生受体与当前审查记录不一致。"
    if str(record.get("selected_site_id") or "") != str(
        review.get("selected_site_id") or ""
    ):
        return False, "派生受体与当前选择的 Zn 位点不一致。"
    path, path_error = _contained_project_path(
        root,
        PREPARED_RECEPTOR_RELATIVE_PATH,
        must_exist=True,
    )
    if path_error or path is None:
        return False, "派生受体路径不可读取。"
    text, _, read_error = _read_text_file(
        path,
        maximum_bytes=MAX_RECEPTOR_BYTES,
        empty_code="AD4ZN_PREPARED_RECEPTOR_EMPTY",
        too_large_code="AD4ZN_PREPARED_RECEPTOR_TOO_LARGE",
        encoding_code="AD4ZN_PREPARED_RECEPTOR_TEXT_INVALID",
    )
    if read_error or text is None:
        return False, "派生受体不是可读取的 PDBQT 文本。"
    parsed = _parse_receptor_text(text)
    zinc_atoms = [
        item
        for item in parsed["ordinary_atoms"]
        if _metal_symbol(item) == "Zn"
    ]
    tz_atoms = [item for item in parsed["atoms"] if _type_key(item) == "TZ"]
    if parsed["malformed_atom_lines"]:
        return False, "派生受体含无效的 ATOM/HETATM 坐标。"
    if len(tz_atoms) != 1:
        return False, "派生受体必须恰好包含一个 TZ。"
    if not zinc_atoms:
        return False, "派生受体中没有 Zn/ZN 原子。"
    changes = record.get("all_zn_charge_changes")
    if not isinstance(changes, list) or len(changes) != len(zinc_atoms):
        return False, "派生受体中的 Zn 数量与电荷变更记录不一致。"
    if any(
        _type_key(item) != "ZN"
        or item.get("charge") is None
        or not math.isclose(float(item["charge"]), 0.0, abs_tol=1e-9)
        for item in zinc_atoms
    ):
        return False, "派生受体中的 Zn 未全部规范为 ZN/0.000。"
    tz = tz_atoms[0]
    if (
        tz.get("charge") is None
        or not math.isclose(float(tz["charge"]), TZ_CHARGE, abs_tol=1e-9)
    ):
        return False, "派生受体中的 TZ 电荷无效。"
    recorded_tz = record.get("tz")
    if not isinstance(recorded_tz, dict) or not isinstance(
        recorded_tz.get("coordinate"), dict
    ):
        return False, "派生受体记录缺少 TZ 坐标。"
    for axis in ("x", "y", "z"):
        try:
            if not math.isclose(
                float(recorded_tz["coordinate"][axis]),
                float(tz["coordinate"][axis]),
                abs_tol=1e-6,
            ):
                return False, "派生受体中的 TZ 坐标与记录不一致。"
        except (KeyError, TypeError, ValueError):
            return False, "派生受体记录的 TZ 坐标无效。"
    return True, ""


def _parameter_validity(root: Path, record: Any) -> tuple[bool, str]:
    if not isinstance(record, dict) or Path(
        str(record.get("relative_path") or "")
    ).as_posix() != PARAMETER_RELATIVE_PATH:
        return False, "参数文件路径不是 AD4Zn 固定项目路径。"
    valid_file, reason = _valid_recorded_file(root, record, expected_suffix=".dat")
    if not valid_file:
        return False, reason
    if not isinstance(record, dict):
        return False, "参数文件记录无效。"
    if record.get("protocol_id") != PROTOCOL_ID:
        return False, "参数文件不属于当前 AD4Zn beta 协议。"
    if record.get("license_source") != "user_provided_gpl_asset":
        return False, "参数文件缺少用户提供 GPL 资产标记。"
    if record.get("license_id") != SUPPORTED_PARAMETER_LICENSE_ID:
        return False, "参数文件缺少明确的 GPL-2.0-or-later 许可证记录。"
    if record.get("supported_profile_id") != SUPPORTED_PARAMETER_PROFILE_ID:
        return False, "参数文件不属于当前支持的 AD4Zn 参数配置。"
    path, path_error = _contained_project_path(root, PARAMETER_RELATIVE_PATH, must_exist=True)
    if path_error or path is None:
        return False, "参数文件路径不可读取。"
    semantic = validate_parameter_file(path)
    if not semantic.get("ok"):
        return False, str(
            (semantic.get("error") or {}).get("message")
            or "参数文件内容校验失败。"
        )
    if (
        semantic.get("sha256") != record.get("sha256")
        or int(semantic.get("size_bytes") or 0) != int(record.get("size_bytes") or 0)
        or semantic.get("license_id") != record.get("license_id")
        or semantic.get("supported_profile_id")
        != record.get("supported_profile_id")
        or semantic.get("upstream_reference")
        != record.get("upstream_reference")
        or semantic.get("reference_sha256")
        != record.get("reference_sha256")
        or semantic.get("matches_reference_sha256")
        is not record.get("matches_reference_sha256")
        or semantic.get("license_notice_detected")
        is not record.get("license_notice_detected")
    ):
        return False, "参数文件的校验结果与项目记录不一致。"
    return True, ""


def _empty_status(project: Any, root: Path, issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol_id": PROTOCOL_ID,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_reference": ALGORITHM_UPSTREAM_REFERENCE,
        "algorithm_reference_sha256": ALGORITHM_REFERENCE_SHA256,
        "ready": False,
        "preparation_ready": False,
        "receptor": None,
        "sites": [],
        "selected_site_id": "",
        "selected_site": None,
        "other_metals": [],
        "existing_tz": [],
        "review": {
            "recorded": False,
            "valid": False,
            "invalid_reason": "尚未保存 AD4Zn 审查。",
        },
        "review_valid": False,
        "prepared_receptor": {
            "recorded": False,
            "valid": False,
            "invalid_reason": "尚未生成 AD4Zn 派生受体。",
        },
        "prepared_receptor_valid": False,
        "parameter_file": {
            "recorded": False,
            "valid": False,
            "invalid_reason": "尚未记录 AD4Zn 参数文件。",
        },
        "parameter_file_valid": False,
        "required_confirmations": list(REQUIRED_CONFIRMATIONS),
        "step_readiness": {
            "review": False,
            "prepare": False,
            "parameter": False,
            "run": False,
        },
        "compatibility": {
            "autogrid_min_version": "4.2.7",
            "tool_checked": False,
            "maps_pipeline_connected": True,
        },
        "issues": [issue],
        "message": "AD4Zn beta 准备尚不可用。",
        "error": None,
    }


def get_status(project_dir: str) -> dict[str, Any]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    receptor, receptor_error = _load_receptor(project, root)
    if receptor_error or receptor is None:
        source_error = (receptor_error or {}).get("error") or {}
        issue = _issue(
            str(source_error.get("code") or "AD4ZN_RECEPTOR_UNAVAILABLE"),
            str(source_error.get("title") or "受体不可用"),
            str(source_error.get("message") or "当前受体无法用于 AD4Zn 分析。"),
            raw_error=str(source_error.get("raw_error") or ""),
            suggestion=str(source_error.get("suggestion") or ""),
        )
        return _empty_status(project, root, issue)

    parsed = receptor["parsed"]
    sites = parsed["sites"]
    site_by_id = {item["site_id"]: item for item in sites}
    other_metals = parsed["other_metals"]
    existing_tz = parsed["existing_tz"]
    state = _state_from_project(project)

    review_record = state.get("review") if isinstance(state.get("review"), dict) else None
    review_valid, review_reason = _review_validity(
        review_record,
        receptor["sha256"],
        set(site_by_id),
    )
    selected_site_id = (
        str(review_record.get("selected_site_id") or "")
        if isinstance(review_record, dict)
        else ""
    )
    selected_site = site_by_id.get(selected_site_id)

    prepared_record = (
        state.get("prepared_receptor")
        if isinstance(state.get("prepared_receptor"), dict)
        else None
    )
    prepared_valid, prepared_reason = _prepared_validity(
        root,
        prepared_record,
        receptor_sha256=receptor["sha256"],
        review=review_record,
        review_valid=review_valid,
    )
    parameter_record = (
        state.get("parameter_file")
        if isinstance(state.get("parameter_file"), dict)
        else None
    )
    parameter_valid, parameter_reason = _parameter_validity(root, parameter_record)

    issues: list[dict[str, Any]] = []
    if not sites:
        issues.append(
            _issue(
                "AD4ZN_ZN_NOT_FOUND",
                "未检测到锌",
                "当前受体 PDBQT 中没有 Zn/ZN 原子类型。",
                suggestion="AD4Zn beta 仅适用于含锌受体。",
            )
        )
    if other_metals:
        labels = ", ".join(
            f"{item.get('atom_type') or item.get('name')}@L{item.get('line_number')}"
            for item in other_metals
        )
        issues.append(
            _issue(
                "AD4ZN_NON_ZN_METAL_UNSUPPORTED",
                "检测到非锌金属",
                "当前 AD4Zn beta 不处理非 Zn 金属。",
                raw_error=labels,
                suggestion="请使用适合该金属的独立参数协议，或更换受体。",
            )
        )
    if len(sites) > 1 and selected_site is None:
        issues.append(
            _issue(
                "AD4ZN_MULTIPLE_ZN_UNSELECTED",
                "尚未选择目标锌位点",
                "受体包含多个 Zn，必须明确选择其中一个位点生成 TZ。",
                suggestion="请查看各位点配位信息后保存审查。",
            )
        )
    if existing_tz:
        issues.append(
            _issue(
                "AD4ZN_EXISTING_TZ_WILL_BE_REPLACED",
                "检测到已有 TZ",
                "准备派生受体时会移除全部旧 TZ，并只为所选 Zn 添加一个新 TZ。",
                raw_error=f"{len(existing_tz)} 个 TZ",
                blocking=False,
            )
        )
    if sites and not other_metals and not review_valid:
        code = (
            "AD4ZN_REVIEW_RECEPTOR_CHANGED"
            if isinstance(review_record, dict)
            and str(review_record.get("receptor_sha256") or "") != receptor["sha256"]
            else "AD4ZN_REVIEW_REQUIRED"
        )
        issues.append(
            _issue(
                code,
                "AD4Zn 审查未生效",
                review_reason,
                suggestion="请选择 Zn 位点并完成七项明确确认。",
            )
        )
    if review_valid and selected_site is not None and not selected_site["can_generate"]:
        issues.append(
            _issue(
                "AD4ZN_TZ_GEOMETRY_UNSUPPORTED",
                "当前位点不能自动生成 TZ",
                selected_site["geometry_message"],
                suggestion="请人工核对配位环境；当前 beta 仅支持三配位 Zn。",
            )
        )
    if review_valid and selected_site is not None and selected_site["can_generate"] and not prepared_valid:
        issues.append(
            _issue(
                "AD4ZN_PREPARED_RECEPTOR_REQUIRED",
                "派生受体未就绪",
                prepared_reason,
                suggestion="请重新生成与当前审查绑定的 AD4Zn 派生受体。",
            )
        )
    if not parameter_valid:
        issues.append(
            _issue(
                "AD4ZN_PARAMETER_REQUIRED",
                "AD4Zn 参数文件未就绪",
                parameter_reason,
                suggestion="请由用户自行提供并记录合法的 AD4Zn 参数文件。",
            )
        )

    review_view = (
        copy.deepcopy(review_record)
        if review_record
        else {
            "recorded": False,
            "valid": False,
            "invalid_reason": review_reason,
        }
    )
    if review_record:
        review_view["recorded"] = True
        review_view["valid"] = review_valid
        review_view["invalid_reason"] = "" if review_valid else review_reason
    prepared_view = (
        copy.deepcopy(prepared_record)
        if prepared_record
        else {
            "recorded": False,
            "valid": False,
            "invalid_reason": prepared_reason,
        }
    )
    if prepared_record:
        prepared_view["recorded"] = True
        prepared_view["valid"] = prepared_valid
        prepared_view["invalid_reason"] = "" if prepared_valid else prepared_reason
    parameter_view = (
        copy.deepcopy(parameter_record)
        if parameter_record
        else {
            "recorded": False,
            "valid": False,
            "invalid_reason": parameter_reason,
        }
    )
    if parameter_record:
        parameter_view["recorded"] = True
        parameter_view["valid"] = parameter_valid
        parameter_view["invalid_reason"] = "" if parameter_valid else parameter_reason

    review_step_ready = any(
        bool(site.get("can_generate")) for site in sites
    ) and not other_metals
    prepare_step_ready = (
        review_valid
        and selected_site is not None
        and bool(selected_site["can_generate"])
        and not other_metals
    )
    preparation_ready = prepare_step_ready and prepared_valid and parameter_valid
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol_id": PROTOCOL_ID,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_reference": ALGORITHM_UPSTREAM_REFERENCE,
        "algorithm_reference_sha256": ALGORITHM_REFERENCE_SHA256,
        "ready": preparation_ready,
        "preparation_ready": preparation_ready,
        "receptor": {
            "relative_path": receptor["relative_path"],
            "size_bytes": receptor["size_bytes"],
            "sha256": receptor["sha256"],
        },
        "sites": sites,
        "selected_site_id": selected_site_id,
        "selected_site": copy.deepcopy(selected_site),
        "other_metals": other_metals,
        "existing_tz": existing_tz,
        "review": review_view,
        "review_valid": review_valid,
        "prepared_receptor": prepared_view,
        "prepared_receptor_valid": prepared_valid,
        "parameter_file": parameter_view,
        "parameter_file_valid": parameter_valid,
        "required_confirmations": list(REQUIRED_CONFIRMATIONS),
        "step_readiness": {
            "review": review_step_ready,
            "prepare": prepare_step_ready,
            "parameter": parameter_valid,
            "run": preparation_ready,
        },
        "compatibility": {
            "autogrid_min_version": "4.2.7",
            "tool_checked": False,
            "maps_pipeline_connected": True,
        },
        "issues": issues,
        "message": (
            "AD4Zn beta 准备资产已就绪，可以继续生成专用 maps。"
            if preparation_ready
            else "AD4Zn beta 准备尚未完成。"
        ),
        "error": None,
    }


def _parse_json_object(value: Any, *, code: str, label: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if isinstance(value, dict):
        return copy.deepcopy(value), None
    if not isinstance(value, str):
        return None, _error(code, f"{label}格式无效", f"{label}必须是 JSON 对象。")
    if len(value.encode("utf-8")) > MAX_REVIEW_JSON_BYTES:
        return None, _error(code, f"{label}过大", f"{label}超过允许的大小。")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, _error(code, f"{label}不是有效 JSON", f"无法解析{label}。", str(exc))
    if not isinstance(payload, dict):
        return None, _error(code, f"{label}格式无效", f"{label}必须是 JSON 对象。")
    return payload, None


def save_review(project_dir: str, review_json: str | dict[str, Any]) -> dict[str, Any]:
    payload, json_error = _parse_json_object(
        review_json,
        code="AD4ZN_REVIEW_JSON_INVALID",
        label="审查数据",
    )
    if json_error or payload is None:
        return json_error
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    receptor, receptor_error = _load_receptor(project, root)
    if receptor_error or receptor is None:
        return receptor_error
    sites = receptor["parsed"]["sites"]
    other_metals = receptor["parsed"]["other_metals"]
    if not sites:
        return _error(
            "AD4ZN_ZN_NOT_FOUND",
            "未检测到锌",
            "当前受体 PDBQT 中没有可选择的 Zn/ZN 位点。",
        )
    if other_metals:
        return _error(
            "AD4ZN_NON_ZN_METAL_UNSUPPORTED",
            "检测到非锌金属",
            "当前 AD4Zn beta 不允许含非 Zn 金属的受体进入准备流程。",
            ", ".join(str(item.get("atom_type") or item.get("name")) for item in other_metals),
        )
    selected_site_id = str(payload.get("selected_site_id") or "").strip()
    if not selected_site_id:
        return _error(
            "AD4ZN_SITE_SELECTION_REQUIRED",
            "尚未选择锌位点",
            "必须明确选择一个 Zn 位点，单 Zn 受体也不能省略该确认。",
        )
    site_ids = {item["site_id"] for item in sites}
    if selected_site_id not in site_ids:
        return _error(
            "AD4ZN_SITE_SELECTION_INVALID",
            "锌位点选择无效",
            "所选 Zn 位点不属于当前受体。",
            selected_site_id,
            "请刷新位点列表后重新选择。",
        )
    selected_site = next(
        item for item in sites if item["site_id"] == selected_site_id
    )
    if not selected_site.get("can_generate"):
        return _error(
            "AD4ZN_TZ_GEOMETRY_UNSUPPORTED",
            "当前位点不能自动生成 TZ",
            str(
                selected_site.get("geometry_message")
                or "所选 Zn 位点不满足当前 beta 的自动准备边界。"
            ),
            suggestion="请更换目标位点，或使用经过独立验证的人工准备流程。",
        )
    confirmations, confirmation_error = _confirmations_from_payload(payload)
    if confirmation_error or confirmations is None:
        return confirmation_error
    binding_payload = _review_binding_payload(
        receptor["sha256"],
        selected_site_id,
        confirmations,
    )
    review = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": STATE_SCHEMA_VERSION,
        "selected_site_id": selected_site_id,
        "confirmations": confirmations,
        "receptor_relative_path": receptor["relative_path"],
        "receptor_sha256": receptor["sha256"],
        "review_binding_sha256": _canonical_json_sha256(binding_payload),
        "saved_at": _now_iso(),
    }
    state = _state_from_project(project)
    state["review"] = review
    _store_state(project, state)
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    status = get_status(str(root))
    if status.get("ok"):
        status["message"] = "AD4Zn 位点审查已保存并绑定当前受体 SHA256。"
    return status


def _replace_fixed_field(body: str, start: int, end: int, value: str) -> str:
    width = end - start
    if len(value) != width:
        raise ValueError(f"固定列宽错误：{start}:{end}")
    body = body.ljust(max(len(body), end))
    return body[:start] + value + body[end:]


def _normalized_zn_line(line: str) -> str:
    body = line.rstrip("\r\n")
    newline = line[len(body) :]
    body = _replace_fixed_field(body, 68, 76, f"{0.0:8.3f}")
    body = _replace_fixed_field(body, 77, 79, "ZN")
    return body + newline


def _tz_line_from_zn(
    zn_line: str,
    *,
    serial: int,
    coordinate: dict[str, float],
    newline_fallback: str,
) -> str:
    body = zn_line.rstrip("\r\n")
    newline = zn_line[len(body) :] or newline_fallback
    body = _replace_fixed_field(body, 0, 6, "HETATM")
    body = _replace_fixed_field(body, 6, 11, f"{serial:5d}")
    body = _replace_fixed_field(body, 12, 16, "  TZ")
    body = _replace_fixed_field(body, 30, 38, f"{coordinate['x']:8.3f}")
    body = _replace_fixed_field(body, 38, 46, f"{coordinate['y']:8.3f}")
    body = _replace_fixed_field(body, 46, 54, f"{coordinate['z']:8.3f}")
    body = _replace_fixed_field(body, 68, 76, f"{TZ_CHARGE:8.3f}")
    body = _replace_fixed_field(body, 77, 79, "TZ")
    return body + newline


def _render_prepared_receptor(
    receptor: dict[str, Any],
    selected_site: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    parsed = receptor["parsed"]
    selected_line_number = int(selected_site["zn"]["line_number"])
    serials = [
        int(item.get("serial") or 0)
        for item in parsed["atoms"]
        if int(item.get("serial") or 0) > 0
    ]
    tz_serial = max(serials, default=0) + 1
    if tz_serial > 99999:
        return None, None, _error(
            "AD4ZN_TZ_SERIAL_EXHAUSTED",
            "无法分配 TZ 原子序号",
            "受体原子序号已达到 PDB/PDBQT 五位字段上限。",
        )
    tz_candidate = selected_site.get("tz_candidate")
    if not isinstance(tz_candidate, dict) or not isinstance(
        tz_candidate.get("coordinate"), dict
    ):
        return None, None, _error(
            "AD4ZN_TZ_CANDIDATE_MISSING",
            "TZ 候选坐标缺失",
            "所选 Zn 位点没有可写入的 TZ 坐标。",
        )

    line_atoms = {
        int(item["line_number"]): item
        for item in parsed["atoms"]
    }
    newline_fallback = "\r\n" if "\r\n" in receptor["text"] else "\n"
    rendered: list[str] = []
    inserted = False
    removed_tz = 0
    charge_changes: list[dict[str, Any]] = []
    for line_number, line in enumerate(parsed["lines"], start=1):
        atom = line_atoms.get(line_number)
        if atom is not None and _type_key(atom) == "TZ":
            removed_tz += 1
            continue
        if atom is not None and _metal_symbol(atom) == "Zn":
            normalized_zn = _normalized_zn_line(line)
            if (
                line_number == selected_line_number
                and not normalized_zn.endswith(("\n", "\r"))
            ):
                normalized_zn += newline_fallback
            rendered.append(normalized_zn)
            charge_changes.append(
                {
                    "site_id": _site_id(atom),
                    "atom_id": atom["atom_id"],
                    "line_number": line_number,
                    "old_atom_type": atom.get("atom_type"),
                    "new_atom_type": "ZN",
                    "old_charge": atom.get("charge"),
                    "new_charge": 0.0,
                }
            )
            if line_number == selected_line_number:
                rendered.append(
                    _tz_line_from_zn(
                        line,
                        serial=tz_serial,
                        coordinate=tz_candidate["coordinate"],
                        newline_fallback=newline_fallback,
                    )
                )
                inserted = True
            continue
        rendered.append(line)
    if not inserted:
        return None, None, _error(
            "AD4ZN_SELECTED_SITE_DISAPPEARED",
            "所选锌位点已变化",
            "生成过程中未找到审查选择的 Zn 原子。",
            suggestion="请刷新状态并重新审查。",
        )
    output_text = "".join(rendered)
    verification = _parse_receptor_text(output_text)
    output_zinc = [
        item
        for item in verification["ordinary_atoms"]
        if _metal_symbol(item) == "Zn"
    ]
    output_tz = [
        item for item in verification["atoms"] if _type_key(item) == "TZ"
    ]
    if len(output_tz) != 1 or any(
        item.get("charge") is None
        or not math.isclose(float(item["charge"]), 0.0, abs_tol=1e-9)
        or _type_key(item) != "ZN"
        for item in output_zinc
    ):
        return None, None, _error(
            "AD4ZN_OUTPUT_VERIFICATION_FAILED",
            "派生受体校验失败",
            "派生受体未满足“全部 Zn 为 ZN/0.000 且恰好一个 TZ”的约束。",
        )
    written_tz = output_tz[0]
    selected_identity = selected_site["zn"]
    selected_zn_output = next(
        (
            item
            for item in output_zinc
            if int(item.get("serial") or 0) == int(selected_identity.get("serial") or 0)
            and str(item.get("chain") or "") == str(selected_identity.get("chain") or "")
            and str(item.get("residue_name") or "")
            == str(selected_identity.get("residue_name") or "")
            and str(item.get("residue_number") or "")
            == str(selected_identity.get("residue_number") or "")
            and str(item.get("insertion_code") or "")
            == str(selected_identity.get("insertion_code") or "")
        ),
        None,
    )
    if selected_zn_output is None:
        return None, None, _error(
            "AD4ZN_OUTPUT_VERIFICATION_FAILED",
            "派生受体校验失败",
            "无法在派生受体中重新定位所选 Zn。",
        )
    serialized_distance = _distance(
        selected_zn_output["coordinate"],
        written_tz["coordinate"],
    )
    metadata = {
        "removed_existing_tz_count": removed_tz,
        "all_zn_charge_changes": charge_changes,
        "selected_zn": _public_atom(selected_zn_output),
        "tz": {
            **_public_atom(written_tz),
            "target_distance_angstrom": TZ_DISTANCE_ANGSTROM,
            "serialized_distance_angstrom": round(serialized_distance, 6),
            "charge": TZ_CHARGE,
        },
    }
    return output_text, metadata, None


def prepare_receptor(
    project_dir: str,
    options_json: str | dict[str, Any] = "{}",
) -> dict[str, Any]:
    options, options_error = _parse_json_object(
        options_json,
        code="AD4ZN_OPTIONS_JSON_INVALID",
        label="准备选项",
    )
    if options_error or options is None:
        return options_error
    if options:
        return _error(
            "AD4ZN_OPTIONS_UNSUPPORTED",
            "准备选项不受支持",
            "AD4Zn beta 当前使用固定的 4.5/2.5/2.0 Å 科学参数，不接受覆盖。",
            ", ".join(sorted(str(key) for key in options)),
        )
    status = get_status(project_dir)
    if not status.get("ok"):
        return status
    if status.get("other_metals"):
        return _error(
            "AD4ZN_NON_ZN_METAL_UNSUPPORTED",
            "检测到非锌金属",
            "当前 AD4Zn beta 不允许含非 Zn 金属的受体进入准备流程。",
        )
    if not status.get("review_valid"):
        return _error(
            "AD4ZN_REVIEW_REQUIRED",
            "AD4Zn 审查未生效",
            "必须先选择 Zn 位点、完成七项确认，并绑定当前受体 SHA256。",
        )
    selected_site = status.get("selected_site")
    if not isinstance(selected_site, dict) or not selected_site.get("can_generate"):
        return _error(
            "AD4ZN_TZ_GEOMETRY_UNSUPPORTED",
            "当前位点不能自动生成 TZ",
            str((selected_site or {}).get("geometry_message") or "所选位点不可生成 TZ。"),
            suggestion="当前 beta 仅支持三配位 Zn。",
        )

    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    receptor, receptor_error = _load_receptor(project, root)
    if receptor_error or receptor is None:
        return receptor_error
    if receptor["sha256"] != str((status.get("receptor") or {}).get("sha256") or ""):
        return _error(
            "AD4ZN_RECEPTOR_CHANGED_DURING_PREPARATION",
            "受体在准备过程中发生变化",
            "AD4Zn 已停止写入，避免使用过期审查生成派生受体。",
            suggestion="请刷新状态并重新审查。",
        )
    output_text, generation_metadata, generation_error = _render_prepared_receptor(
        receptor,
        selected_site,
    )
    if generation_error or output_text is None or generation_metadata is None:
        return generation_error
    relative_path = PREPARED_RECEPTOR_RELATIVE_PATH
    output_path, output_error = _safe_output_path(root, relative_path)
    if output_error or output_path is None:
        return output_error
    try:
        atomic_write_text(output_path, output_text, encoding="utf-8")
        output_size = output_path.stat().st_size
        output_hash = _sha256(output_path)
    except OSError as exc:
        return _error(
            "AD4ZN_PREPARED_RECEPTOR_WRITE_ERROR",
            "无法保存派生受体",
            "写入 AD4Zn 派生受体时发生错误。",
            str(exc),
            "请确认项目目录可写。",
        )

    state = _state_from_project(project)
    review = state.get("review")
    if not isinstance(review, dict):
        return _error(
            "AD4ZN_REVIEW_REQUIRED",
            "AD4Zn 审查未生效",
            "项目中的审查记录已变化，请重新审查。",
        )
    record = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": STATE_SCHEMA_VERSION,
        "algorithm_name": ALGORITHM_NAME,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_reference": ALGORITHM_UPSTREAM_REFERENCE,
        "algorithm_reference_sha256": ALGORITHM_REFERENCE_SHA256,
        "limits": {
            "neighbor_search_angstrom": NEIGHBOR_SEARCH_ANGSTROM,
            "coordination_cutoff_angstrom": COORDINATION_CUTOFF_ANGSTROM,
            "tz_distance_angstrom": TZ_DISTANCE_ANGSTROM,
            "minimum_plane_separation_degrees": (
                MIN_ZN_PLANE_SEPARATION_DEGREES
            ),
        },
        "selected_site_id": selected_site["site_id"],
        "review_binding_sha256": review["review_binding_sha256"],
        "input_relative_path": receptor["relative_path"],
        "input_sha256": receptor["sha256"],
        "input_size_bytes": receptor["size_bytes"],
        "relative_path": relative_path,
        "sha256": output_hash,
        "size_bytes": output_size,
        **generation_metadata,
        "prepared_at": _now_iso(),
    }
    state["prepared_receptor"] = record
    _store_state(project, state)
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    refreshed = get_status(str(root))
    if refreshed.get("ok"):
        refreshed["message"] = (
            "AD4Zn 派生受体已独立保存；项目当前受体未被覆盖。"
        )
    return refreshed


def validate_parameter_file(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    try:
        if not source.exists():
            return _error(
                "AD4ZN_PARAMETER_NOT_FOUND",
                "没有找到参数文件",
                "指定的 AD4Zn 参数文件不存在。",
                str(source),
            )
        if not source.is_file():
            return _error(
                "AD4ZN_PARAMETER_NOT_FILE",
                "参数路径不是文件",
                "AD4Zn 参数路径必须指向普通文件。",
                str(source),
            )
        resolved = source.resolve(strict=True)
        if source.is_symlink() or resolved != source.absolute():
            return _error(
                "AD4ZN_PARAMETER_PATH_UNSAFE",
                "参数文件路径不安全",
                "AD4Zn 参数文件不能是符号链接或重解析路径。",
                str(source),
            )
    except OSError as exc:
        return _error(
            "AD4ZN_PARAMETER_PATH_ERROR",
            "无法检查参数文件",
            "检查 AD4Zn 参数文件路径时发生错误。",
            str(exc),
        )
    text, payload, read_error = _read_text_file(
        resolved,
        maximum_bytes=MAX_PARAMETER_FILE_BYTES,
        empty_code="AD4ZN_PARAMETER_EMPTY",
        too_large_code="AD4ZN_PARAMETER_TOO_LARGE",
        encoding_code="AD4ZN_PARAMETER_TEXT_INVALID",
    )
    if read_error or text is None or payload is None:
        return read_error

    coefficient_values: dict[str, float] = {}
    atom_types: set[str] = set()
    atom_parameter_values: dict[str, list[tuple[float, ...]]] = {}
    malformed_coefficients: list[str] = []
    malformed_atom_parameters: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        key = parts[0].casefold()
        if key in _EXPECTED_FE_CASEFOLD:
            canonical = _EXPECTED_FE_CASEFOLD[key]
            if len(parts) < 2:
                malformed_coefficients.append(f"{canonical}@L{line_number}")
                continue
            value = _float_or_none(parts[1])
            if value is None:
                malformed_coefficients.append(f"{canonical}@L{line_number}")
                continue
            if canonical in coefficient_values:
                malformed_coefficients.append(f"{canonical}@L{line_number}(重复)")
                continue
            coefficient_values[canonical] = value
        if key == "atom_par" and len(parts) >= 2:
            atom_type = parts[1].upper()
            atom_types.add(atom_type)
            if atom_type in EXPECTED_AD4ZN_ATOM_PARAMETERS:
                if len(parts) < 12:
                    malformed_atom_parameters.append(
                        f"{parts[1]}@L{line_number}"
                    )
                    continue
                values = tuple(
                    _float_or_none(value)
                    for value in parts[2:12]
                )
                if any(value is None for value in values):
                    malformed_atom_parameters.append(
                        f"{parts[1]}@L{line_number}"
                    )
                    continue
                atom_parameter_values.setdefault(atom_type, []).append(
                    tuple(float(value) for value in values if value is not None)
                )
    missing_coefficients = [
        item for item in EXPECTED_FE_COEFFICIENTS if item not in coefficient_values
    ]
    if malformed_coefficients or missing_coefficients:
        details = []
        if missing_coefficients:
            details.append("缺少：" + ", ".join(missing_coefficients))
        if malformed_coefficients:
            details.append("无效：" + ", ".join(malformed_coefficients))
        return _error(
            "AD4ZN_PARAMETER_FE_COEFFICIENTS_INVALID",
            "自由能系数不完整",
            "参数文件必须包含且仅解析出五项有效 FE_coeff 数值。",
            "；".join(details),
        )
    missing_atom_types = sorted({"ZN", "TZ"} - atom_types)
    if missing_atom_types:
        return _error(
            "AD4ZN_PARAMETER_ATOM_TYPES_MISSING",
            "AD4Zn 原子参数不完整",
            "参数文件必须同时包含 atom_par Zn/ZN 和 atom_par TZ。",
            ", ".join(missing_atom_types),
        )
    profile_mismatches = [
        atom_type
        for atom_type, expected in EXPECTED_AD4ZN_ATOM_PARAMETERS.items()
        if not atom_parameter_values.get(atom_type)
        or any(
            len(values) != len(expected)
            or any(
                not math.isclose(actual, target, rel_tol=0.0, abs_tol=1e-9)
                for actual, target in zip(values, expected, strict=True)
            )
            for values in atom_parameter_values.get(atom_type, [])
        )
    ]
    coefficient_mismatches = [
        key
        for key, expected in EXPECTED_FE_COEFFICIENT_VALUES.items()
        if not math.isclose(
            coefficient_values.get(key, math.nan),
            expected,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ]
    license_notice_detected = (
        "gnu general public license" in text.casefold()
        and (
            "version 2" in text.casefold()
            or "either version 2" in text.casefold()
        )
    )
    if malformed_atom_parameters or profile_mismatches or coefficient_mismatches:
        details = []
        if malformed_atom_parameters:
            details.append("格式无效：" + ", ".join(malformed_atom_parameters))
        if profile_mismatches:
            details.append("原子参数不匹配：" + ", ".join(profile_mismatches))
        if coefficient_mismatches:
            details.append("自由能系数不匹配：" + ", ".join(coefficient_mismatches))
        return _error(
            "AD4ZN_PARAMETER_PROFILE_MISMATCH",
            "参数文件与受支持的 AD4Zn 配置不匹配",
            "当前 beta 只接受与 AutoDock Vina v1.2.7 AD4Zn 关键参数一致的文件。",
            "；".join(details),
            "请从文档列出的固定上游参考地址重新取得 AD4Zn.dat。",
        )
    if not license_notice_detected:
        return _error(
            "AD4ZN_PARAMETER_LICENSE_NOTICE_MISSING",
            "参数文件缺少许可证声明",
            "未检测到 AD4Zn.dat 应有的 GNU GPL version 2 or later 声明。",
            suggestion="请从文档列出的固定上游参考地址重新取得完整文件。",
        )
    payload_sha256 = _sha256_bytes(payload)
    return {
        "ok": True,
        "path": str(resolved),
        "size_bytes": len(payload),
        "sha256": payload_sha256,
        "license_source": "user_provided_gpl_asset",
        "license_id": SUPPORTED_PARAMETER_LICENSE_ID,
        "license_notice_detected": license_notice_detected,
        "supported_profile_id": SUPPORTED_PARAMETER_PROFILE_ID,
        "upstream_reference": SUPPORTED_PARAMETER_UPSTREAM_REFERENCE,
        "reference_sha256": SUPPORTED_PARAMETER_REFERENCE_SHA256,
        "matches_reference_sha256": (
            payload_sha256 == SUPPORTED_PARAMETER_REFERENCE_SHA256
        ),
        "coefficients": coefficient_values,
        "atom_types": sorted(atom_types),
        "error": None,
    }


def record_parameter_file(project_dir: str, path: str | Path) -> dict[str, Any]:
    validation = validate_parameter_file(path)
    if not validation.get("ok"):
        return validation
    source = Path(str(validation["path"]))
    try:
        payload = source.read_bytes()
    except OSError as exc:
        return _error(
            "AD4ZN_PARAMETER_READ_ERROR",
            "无法读取参数文件",
            "记录 AD4Zn 参数文件前再次读取失败。",
            str(exc),
        )
    if len(payload) != int(validation["size_bytes"]) or _sha256_bytes(payload) != validation["sha256"]:
        return _error(
            "AD4ZN_PARAMETER_CHANGED_DURING_COPY",
            "参数文件在复制过程中发生变化",
            "AD4Zn 已停止复制，避免记录不一致的参数资产。",
            suggestion="请等待文件写入完成后重试。",
        )
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    relative_path = PARAMETER_RELATIVE_PATH
    target, target_error = _safe_output_path(root, relative_path)
    if target_error or target is None:
        return target_error
    try:
        atomic_write_bytes(target, payload)
    except OSError as exc:
        return _error(
            "AD4ZN_PARAMETER_COPY_ERROR",
            "无法记录参数文件",
            "复制 AD4Zn 参数文件到项目时发生错误。",
            str(exc),
            "请确认项目目录可写。",
        )
    state = _state_from_project(project)
    state["parameter_file"] = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": STATE_SCHEMA_VERSION,
        "relative_path": relative_path,
        "source_path": str(source),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "license_source": "user_provided_gpl_asset",
        "license_id": validation["license_id"],
        "license_notice_detected": validation["license_notice_detected"],
        "supported_profile_id": validation["supported_profile_id"],
        "upstream_reference": validation["upstream_reference"],
        "reference_sha256": validation["reference_sha256"],
        "matches_reference_sha256": validation["matches_reference_sha256"],
        "coefficients": copy.deepcopy(validation["coefficients"]),
        "atom_types": copy.deepcopy(validation["atom_types"]),
        "recorded_at": _now_iso(),
    }
    _store_state(project, state)
    saved = save_project(project)
    if not saved.get("ok"):
        return saved
    status = get_status(str(root))
    if status.get("ok"):
        status["message"] = "用户提供的 AD4Zn 参数文件已校验、复制并记录 SHA256。"
    return status


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command == "status" and len(sys.argv) >= 3:
        _print_json(get_status(sys.argv[2]))
        return
    if command == "review" and len(sys.argv) >= 4:
        _print_json(save_review(sys.argv[2], sys.argv[3]))
        return
    if command == "prepare" and len(sys.argv) >= 3:
        options = sys.argv[3] if len(sys.argv) >= 4 else "{}"
        _print_json(prepare_receptor(sys.argv[2], options))
        return
    if command == "parameter" and len(sys.argv) >= 4:
        _print_json(record_parameter_file(sys.argv[2], sys.argv[3]))
        return
    _print_json(
        _error(
            "AD4ZN_COMMAND_UNKNOWN",
            "未知 AD4Zn 命令",
            f"未知命令：{command}",
            suggestion="可用命令：status、review、prepare、parameter。",
        )
    )


if __name__ == "__main__":
    main()
