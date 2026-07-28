"""Independent hydrated-ligand preparation for the experimental AD4 protocol.

This module deliberately does not alter the standard prepared ligand or the
active docking protocol.  A successful preparation publishes an immutable
manifest pointer under ``project.preserved_data["hydrated_docking"]``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from adapters import autogrid_adapter, meeko_adapter
from dockstart_core import autogrid, hydrated_maps
from dockstart_core.persistence import atomic_write_bytes, atomic_write_json, atomic_write_text
from dockstart_core.preparation import (
    PreparationPathError,
    _is_reparse_or_symlink,
    _safe_project_path,
    _tool_status,
)
from dockstart_core.project import (
    _error,
    _exclusive_file_lock,
    _project_from_dict,
    _project_lock,
    _read_and_migrate_project_unlocked,
    _write_project_json_unlocked,
    load_project,
    validate_box_params,
)
from dockstart_core.settings import load_settings

PROTOCOL_ID = "hydrated_ad4_experimental"
PROJECT_STATE_KEY = "hydrated_docking"
PREPARATION_ROOT = Path("protocols", "hydrated", "ligand_preparations")
PREPARATION_ID_PATTERN = re.compile(r"^hydrated_ligand_(\d{3,})$")
MAX_RAW_LIGAND_BYTES = 32 * 1024 * 1024
MAX_GENERATED_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TOOL_FILE_BYTES = 1024 * 1024 * 1024
MAX_RECEPTOR_BYTES = 256 * 1024 * 1024
HYDRATED_MAPS_MANIFEST_NAME = "hydrated_manifest.json"
HYDRATED_MAPS_ALLOWED_OPTIONS = frozenset(
    {
        "spacing",
        "grid_points",
        "grid_points_x",
        "grid_points_y",
        "grid_points_z",
    },
)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative_path(path: Path, project_root: Path) -> str:
    return path.absolute().relative_to(project_root.absolute()).as_posix()


def _structured_error(
    code: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
    project_dir: str = "",
    preparation_id: str = "",
    manifest_file: str = "",
) -> dict[str, Any]:
    payload = _error(code, message, raw_error=raw_error, suggestion=suggestion)
    payload.update(
        {
            "protocol_id": PROTOCOL_ID,
            "project_dir": project_dir,
            "preparation_id": preparation_id,
            "manifest_file": manifest_file,
            "message": message,
        }
    )
    return payload


def _load_project_model(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    project_root = Path(project_dir).expanduser().resolve()
    return _project_from_dict(loaded["project"], project_root), None


def _stable_regular_project_file(
    project_root: Path,
    relative_file: str,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[bytes, dict[str, Any]]:
    supplied = Path(str(relative_file or ""))
    if not str(relative_file or "").strip():
        raise ValueError(f"{label}尚未记录。")
    if supplied.is_absolute():
        raise PreparationPathError(f"{label}必须使用项目内相对路径。")
    path = _safe_project_path(project_root, supplied, allow_missing=False)
    if _is_reparse_or_symlink(path):
        raise PreparationPathError(f"{label}不能是符号链接、junction 或 reparse point。")

    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label}不是普通文件。")
    if before.st_size <= 0:
        raise ValueError(f"{label}为空。")
    if before.st_size > maximum_bytes:
        raise ValueError(f"{label}超过 {maximum_bytes} 字节上限。")

    with path.open("rb") as handle:
        payload = handle.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ValueError(f"{label}超过 {maximum_bytes} 字节上限。")

    after = os.lstat(path)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise RuntimeError(f"读取{label}时文件发生变化。")
    return payload, {
        "path": _relative_path(path, project_root),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "modified_at_ns": int(after.st_mtime_ns),
        "captured_at": _now_iso(),
    }


def _stable_external_file_snapshot(path_value: str, *, label: str) -> dict[str, Any]:
    path = Path(str(path_value or "")).expanduser()
    if not str(path_value or "").strip() or not path.is_file():
        raise ValueError(f"{label}文件不存在。")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label}不是普通文件。")
    if before.st_size <= 0 or before.st_size > MAX_TOOL_FILE_BYTES:
        raise ValueError(f"{label}大小不在允许范围内。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"读取{label}时文件发生变化。")
    return {
        "path": str(path.resolve()),
        "size_bytes": int(after.st_size),
        "sha256": digest.hexdigest(),
        "modified_at_ns": int(after.st_mtime_ns),
        "captured_at": _now_iso(),
    }


def _snapshots_match(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return (
        str(before.get("path") or "") == str(after.get("path") or "")
        and int(before.get("size_bytes") or 0) == int(after.get("size_bytes") or 0)
        and str(before.get("sha256") or "") == str(after.get("sha256") or "")
    )


def _ensure_plain_directory(project_root: Path, relative: Path) -> Path:
    directory = _safe_project_path(project_root, relative)
    directory.mkdir(parents=True, exist_ok=True)
    checked = _safe_project_path(project_root, relative, allow_missing=False)
    if _is_reparse_or_symlink(checked) or not checked.is_dir():
        raise PreparationPathError(f"目录不安全：{relative.as_posix()}")
    return checked


def _next_preparation_id(project_root: Path) -> str:
    root = _ensure_plain_directory(project_root, PREPARATION_ROOT)
    maximum = 0
    for child in root.iterdir():
        checked = _safe_project_path(
            project_root,
            PREPARATION_ROOT / child.name,
            allow_missing=False,
        )
        if _is_reparse_or_symlink(checked):
            raise PreparationPathError(f"准备记录不能是链接：{checked}")
        match = PREPARATION_ID_PATTERN.match(child.name)
        if match and checked.is_dir():
            maximum = max(maximum, int(match.group(1)))
    return f"hydrated_ligand_{maximum + 1:03d}"


def _generated_script_text() -> str:
    return r'''
from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path


def emit(payload, *, stderr=False):
    stream = sys.stderr if stderr else sys.stdout
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True), file=stream)


def normalize_writer_result(result):
    if isinstance(result, tuple):
        if len(result) >= 2 and result[1] is False:
            detail = str(result[2]) if len(result) >= 3 else "Meeko PDBQT writer failed."
            raise RuntimeError(detail)
        return str(result[0])
    return str(result)


def writer_class(meeko):
    for name in ("PDBQTWriterLegacy", "PDBQTWriter"):
        candidate = getattr(meeko, name, None)
        if candidate is not None and callable(getattr(candidate, "write_string", None)):
            return candidate, name
    raise RuntimeError("Meeko PDBQT writer.write_string is unavailable.")


def count_waters(pdbqt_text):
    return sum(
        1
        for line in pdbqt_text.splitlines()
        if (line.startswith("ATOM") or line.startswith("HETATM"))
        and line.split()
        and line.split()[-1] == "W"
    )


def probe():
    import rdkit
    import meeko
    from rdkit import Chem
    from rdkit.Chem import AllChem

    molecule = Chem.AddHs(Chem.MolFromSmiles("CO"))
    if AllChem.EmbedMolecule(molecule, randomSeed=1729) != 0:
        raise RuntimeError("RDKit hydrate capability probe could not build a 3D molecule.")
    preparator = meeko.MoleculePreparation(hydrate=True)
    setups = preparator.prepare(molecule)
    if len(setups) != 1:
        raise RuntimeError("Meeko hydrate capability probe returned an unexpected setup count.")
    writer, writer_name = writer_class(meeko)
    pdbqt_text = normalize_writer_result(writer.write_string(setups[0]))
    water_count = count_waters(pdbqt_text)
    if water_count <= 0:
        raise RuntimeError("Meeko hydrate capability probe generated no W atoms.")
    emit(
        {
            "ok": True,
            "rdkit_version": str(getattr(rdkit, "__version__", "")),
            "rdkit_module_file": str(Path(rdkit.__file__).resolve()),
            "meeko_version": str(getattr(meeko, "__version__", "")),
            "meeko_module_file": str(Path(meeko.__file__).resolve()),
            "writer_interface": writer_name,
            "hydrate_supported": True,
            "probe_water_count": water_count,
        }
    )
    return 0


def read_single_molecule(path, Chem):
    suffix = path.suffix.lower()
    if suffix == ".sdf":
        supplier = Chem.ForwardSDMolSupplier(
            io.BytesIO(path.read_bytes()),
            sanitize=True,
            removeHs=False,
        )
        entries = list(supplier)
        if len(entries) != 1 or entries[0] is None:
            raise RuntimeError("SDF must contain exactly one valid molecule.")
        return entries[0]
    if suffix == ".mol":
        molecule = Chem.MolFromMolBlock(
            path.read_text(encoding="utf-8", errors="strict"),
            sanitize=True,
            removeHs=False,
        )
        if molecule is None:
            raise RuntimeError("RDKit could not read the MOL molecule.")
        return molecule
    raise RuntimeError("Only SDF and MOL ligand inputs are supported.")


def validate_chemistry(molecule, Chem):
    if molecule.GetNumAtoms() <= 0:
        raise RuntimeError("The ligand contains no atoms.")
    if molecule.GetNumConformers() != 1:
        raise RuntimeError("The ligand must contain exactly one conformer.")
    conformer = molecule.GetConformer()
    if not conformer.Is3D():
        raise RuntimeError("The ligand does not provide a 3D conformer.")
    for atom_index in range(molecule.GetNumAtoms()):
        point = conformer.GetAtomPosition(atom_index)
        if not all(math.isfinite(value) for value in (point.x, point.y, point.z)):
            raise RuntimeError("The ligand contains non-finite coordinates.")
    for bond in molecule.GetBonds():
        order = float(bond.GetBondTypeAsDouble())
        if bond.GetBondType() == Chem.BondType.UNSPECIFIED or not math.isfinite(order) or order <= 0:
            raise RuntimeError("The ligand contains an unspecified or invalid bond order.")


def prepare(source, added_h_output, pdbqt_output):
    import rdkit
    import meeko
    from rdkit import Chem

    molecule = read_single_molecule(source, Chem)
    validate_chemistry(molecule, Chem)
    molecule = Chem.AddHs(molecule, addCoords=True)
    Chem.SanitizeMol(molecule)
    validate_chemistry(molecule, Chem)

    mol_block = Chem.MolToMolBlock(molecule, confId=molecule.GetConformer().GetId())
    added_h_output.write_text(mol_block.rstrip() + "\n$$$$\n", encoding="utf-8")

    preparator = meeko.MoleculePreparation(hydrate=True)
    setups = preparator.prepare(molecule)
    if len(setups) != 1:
        raise RuntimeError("Meeko returned an unexpected ligand setup count.")
    writer, writer_name = writer_class(meeko)
    pdbqt_text = normalize_writer_result(writer.write_string(setups[0]))
    if not pdbqt_text.strip():
        raise RuntimeError("Meeko generated an empty hydrated PDBQT.")
    water_count = count_waters(pdbqt_text)
    if water_count <= 0:
        raise RuntimeError("Meeko generated no W atoms.")
    pdbqt_output.write_text(pdbqt_text, encoding="utf-8")
    emit(
        {
            "ok": True,
            "rdkit_version": str(getattr(rdkit, "__version__", "")),
            "meeko_version": str(getattr(meeko, "__version__", "")),
            "writer_interface": writer_name,
            "atom_count": molecule.GetNumAtoms(),
            "bond_count": molecule.GetNumBonds(),
            "water_count": water_count,
        }
    )
    return 0


def main():
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "probe":
            return probe()
        if len(sys.argv) == 5 and sys.argv[1] == "prepare":
            return prepare(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        emit({"ok": False, "message": "Invalid hydrated preparation arguments."}, stderr=True)
        return 2
    except Exception as exc:
        emit(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            stderr=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _parse_last_json(text: str) -> dict[str, Any]:
    for line in reversed(str(text or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _validate_detected_tools(tools: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None]:
    python_tool = tools.get("python") if isinstance(tools.get("python"), Mapping) else {}
    rdkit_tool = tools.get("rdkit") if isinstance(tools.get("rdkit"), Mapping) else {}
    meeko_tool = tools.get("meeko") if isinstance(tools.get("meeko"), Mapping) else {}
    missing: list[str] = []
    if python_tool.get("status") != "ok" or not str(python_tool.get("path") or ""):
        missing.append("Python")
    if rdkit_tool.get("status") != "ok":
        missing.append("RDKit")
    rdkit_capabilities = (
        rdkit_tool.get("capabilities")
        if isinstance(rdkit_tool.get("capabilities"), Mapping)
        else {}
    )
    if (
        not isinstance(rdkit_capabilities.get("import"), Mapping)
        or rdkit_capabilities["import"].get("status") != "ok"
    ):
        missing.append("RDKit import")
    if meeko_tool.get("status") != "ok":
        missing.append("Meeko")
    ligand_capability = (
        meeko_tool.get("capabilities", {}).get("ligand_preparation", {})
        if isinstance(meeko_tool.get("capabilities"), Mapping)
        else {}
    )
    if not isinstance(ligand_capability, Mapping) or ligand_capability.get("status") != "ok":
        missing.append("Meeko ligand preparation")
    if missing:
        return "", _structured_error(
            "HYDRATED_TOOLS_NOT_READY",
            "水合配体准备所需工具不可用。",
            raw_error=", ".join(missing),
            suggestion="请确认 Assisted Python、RDKit 与 Meeko 可用后重试。",
        )
    return str(python_tool.get("path") or ""), None


def _validate_hydrated_pdbqt(path: Path, project_root: Path) -> dict[str, Any]:
    safe = _safe_project_path(project_root, path, allow_missing=False)
    if _is_reparse_or_symlink(safe):
        raise PreparationPathError("水合 PDBQT 候选不能是链接。")
    details = os.lstat(safe)
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
        raise ValueError("Meeko 未生成非空水合 PDBQT。")
    if details.st_size > MAX_GENERATED_ARTIFACT_BYTES:
        raise ValueError("水合 PDBQT 超过大小上限。")
    text = safe.read_text(encoding="utf-8", errors="strict")
    atom_count = 0
    water_count = 0
    non_water_count = 0
    atom_types: set[str] = set()
    for line in text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        fields = line.split()
        if not fields:
            continue
        atom_count += 1
        atom_type = fields[-1].upper()
        atom_types.add(atom_type)
        if atom_type == "W":
            water_count += 1
        else:
            non_water_count += 1
        try:
            coordinates = (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("水合 PDBQT 含有无法读取的原子坐标。") from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("水合 PDBQT 含有非有限坐标。")
    if atom_count <= 0 or non_water_count <= 0:
        raise ValueError("水合 PDBQT 缺少配体原子。")
    if water_count <= 0:
        raise ValueError("水合 PDBQT 未包含 W 原子。")
    payload = safe.read_bytes()
    return {
        "path": _relative_path(safe, project_root),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "atom_count": atom_count,
        "non_water_atom_count": non_water_count,
        "water_count": water_count,
        "atom_types": sorted(atom_types),
    }


def _validate_added_h_sdf(path: Path, project_root: Path) -> dict[str, Any]:
    safe = _safe_project_path(project_root, path, allow_missing=False)
    if _is_reparse_or_symlink(safe):
        raise PreparationPathError("加氢 SDF 候选不能是链接。")
    details = os.lstat(safe)
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
        raise ValueError("RDKit 未生成非空加氢 SDF。")
    if details.st_size > MAX_GENERATED_ARTIFACT_BYTES:
        raise ValueError("加氢 SDF 超过大小上限。")
    payload = safe.read_bytes()
    text = payload.decode("utf-8", errors="strict")
    if "M  END" not in text or "$$$$" not in text:
        raise ValueError("加氢 SDF 结构不完整。")
    return {
        "path": _relative_path(safe, project_root),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }


def _artifact_snapshot(path: Path, project_root: Path) -> dict[str, Any]:
    try:
        safe = _safe_project_path(project_root, path, allow_missing=False)
        if _is_reparse_or_symlink(safe) or not safe.is_file():
            return {"path": _relative_path(path, project_root), "exists": False}
        payload = safe.read_bytes()
        return {
            "path": _relative_path(safe, project_root),
            "exists": True,
            "size_bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        }
    except (OSError, ValueError, PreparationPathError):
        return {"path": str(path), "exists": False}


def _write_failure_manifest(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    code: str,
    message: str,
    raw_error: str,
    suggestion: str,
) -> dict[str, Any]:
    manifest["status"] = "failed"
    manifest["finished_at"] = _now_iso()
    manifest["error"] = {
        "code": code,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }
    atomic_write_json(manifest_path, manifest)
    return _structured_error(
        code,
        message,
        raw_error=raw_error,
        suggestion=suggestion,
        project_dir=str(manifest.get("project_dir") or ""),
        preparation_id=str(manifest.get("preparation_id") or ""),
        manifest_file=str(manifest.get("manifest_file") or ""),
    )


def _stable_project_artifact_snapshot(
    project_root: Path,
    value: str | Path,
    *,
    label: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    supplied = Path(value)
    path = _safe_project_path(project_root, supplied, allow_missing=False)
    if _is_reparse_or_symlink(path):
        raise PreparationPathError(f"{label}不能是符号链接、junction 或 reparse point。")
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label}不是普通文件。")
    if before.st_size <= 0:
        raise ValueError(f"{label}为空。")
    if before.st_size > maximum_bytes:
        raise ValueError(f"{label}超过 {maximum_bytes} 字节上限。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = os.lstat(path)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"读取{label}时文件发生变化。")
    return {
        "relative_path": _relative_path(path, project_root),
        "size_bytes": int(after.st_size),
        "sha256": digest.hexdigest(),
        "modified_at_ns": int(after.st_mtime_ns),
        "captured_at": _now_iso(),
    }


def _box_snapshot(project: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    submitted = {
        "center_x": project.box.center_x,
        "center_y": project.box.center_y,
        "center_z": project.box.center_z,
        "size_x": project.box.size_x,
        "size_y": project.box.size_y,
        "size_z": project.box.size_z,
    }
    validated = validate_box_params(submitted)
    if not validated.get("ok"):
        error = validated.get("error") if isinstance(validated.get("error"), Mapping) else {}
        return None, _structured_error(
            str(error.get("code") or "HYDRATED_MAPS_BOX_INVALID"),
            str(error.get("message") or "当前 Box 参数无效。"),
            raw_error=str(error.get("raw_error") or ""),
            suggestion=str(error.get("suggestion") or "请先设置有效的对接箱体。"),
            project_dir=str(project.project_dir or ""),
        )
    box = validated["box"]
    return {
        "center": {
            "x": float(box["center_x"]),
            "y": float(box["center_y"]),
            "z": float(box["center_z"]),
        },
        "size": {
            "x": float(box["size_x"]),
            "y": float(box["size_y"]),
            "z": float(box["size_z"]),
        },
    }, None


def _project_receptor_mode(project: Any) -> str:
    protocol = project.preserved_data.get("docking_protocol")
    if not isinstance(protocol, Mapping):
        return "rigid"
    return str(
        protocol.get("receptor_mode")
        or protocol.get("mode")
        or "rigid"
    ).strip().lower()


def _maps_pointer_from_project(project: Any) -> tuple[str, str]:
    state = project.preserved_data.get(PROJECT_STATE_KEY)
    if not isinstance(state, Mapping):
        return "", ""
    pointer = state.get("active_maps_manifest")
    if isinstance(pointer, Mapping):
        return str(pointer.get("path") or ""), str(pointer.get("sha256") or "")
    return str(pointer or ""), ""


def _same_geometry(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        return (
            math.isclose(
                float(left.get("spacing")),
                float(right.get("spacing")),
                rel_tol=0.0,
                abs_tol=hydrated_maps.GEOMETRY_ABS_TOLERANCE,
            )
            and all(
                int((left.get("nelements") or {}).get(axis))
                == int((right.get("nelements") or {}).get(axis))
                for axis in ("x", "y", "z")
            )
            and all(
                math.isclose(
                    float((left.get("center") or {}).get(axis)),
                    float((right.get("center") or {}).get(axis)),
                    rel_tol=0.0,
                    abs_tol=hydrated_maps.GEOMETRY_ABS_TOLERANCE,
                )
                for axis in ("x", "y", "z")
            )
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _grid_geometry(grid: Mapping[str, Any]) -> dict[str, Any]:
    points = grid.get("grid_points") if isinstance(grid.get("grid_points"), Mapping) else {}
    center = grid.get("center") if isinstance(grid.get("center"), Mapping) else {}
    return {
        "spacing": grid.get("spacing"),
        "nelements": {
            axis: points.get(axis)
            for axis in ("x", "y", "z")
        },
        "center": {
            axis: center.get(axis)
            for axis in ("x", "y", "z")
        },
    }


def _detect_autogrid_snapshot() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    settings = load_settings()
    detection = autogrid_adapter.detect(settings.tool_paths.autogrid4)
    if detection.status != "ok" or not detection.path:
        return None, _structured_error(
            "HYDRATED_AUTOGRID_NOT_AVAILABLE",
            detection.message or "未检测到 AutoGrid4。",
            raw_error=str(detection.raw_error or ""),
            suggestion="请配置外部 AutoGrid4；DockStart 不会在安装包中内置该工具。",
        )
    try:
        executable = _stable_external_file_snapshot(
            str(detection.path),
            label="AutoGrid4",
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return None, _structured_error(
            "HYDRATED_AUTOGRID_INVALID",
            "AutoGrid4 可执行文件无法完成完整性校验。",
            raw_error=str(exc),
            suggestion="请重新配置可读取的 AutoGrid4 可执行文件。",
        )
    return {
        **executable,
        "version": str(detection.version or ""),
        "source": str(detection.source or ""),
    }, None


def _pointer_from_project(project: Any) -> tuple[str, str]:
    state = project.preserved_data.get(PROJECT_STATE_KEY)
    if not isinstance(state, Mapping):
        return "", ""
    pointer = state.get("active_ligand_manifest")
    if isinstance(pointer, Mapping):
        return str(pointer.get("path") or ""), str(pointer.get("sha256") or "")
    return str(pointer or ""), ""


def _maps_status_defaults(project: Any) -> dict[str, Any]:
    manifest_relative, expected_sha256 = _maps_pointer_from_project(project)
    return {
        "maps_status": "not_prepared",
        "maps_ready": False,
        "maps_valid": False,
        "active_maps_manifest": manifest_relative,
        "maps_manifest_sha256": expected_sha256,
        "maps_manifest": None,
        "maps_issues": [],
        "maps": None,
    }


def _record_integrity_issue(
    project_root: Path,
    record: Mapping[str, Any],
    *,
    label: str,
    expected_parent: Path | None = None,
    maximum_bytes: int = hydrated_maps.MAX_MAP_FILE_BYTES,
    allow_empty: bool = False,
) -> tuple[Path | None, dict[str, Any] | None, str | None]:
    relative = str(record.get("relative_path") or "")
    if not relative:
        return None, None, f"{label}未记录路径。"
    try:
        path = _safe_project_path(project_root, Path(relative), allow_missing=False)
        if expected_parent is not None and path.parent != expected_parent:
            raise PreparationPathError(f"{label}不在活动 maps 记录目录。")
        if _is_reparse_or_symlink(path):
            raise PreparationPathError(f"{label}不能是链接。")
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label}不是普通文件。")
        if before.st_size < 0 or (before.st_size == 0 and not allow_empty):
            raise ValueError(f"{label}为空。")
        if before.st_size > maximum_bytes:
            raise ValueError(f"{label}超过大小上限。")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.lstat(path)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError(f"读取{label}时文件发生变化。")
        actual = {
            "relative_path": _relative_path(path, project_root),
            "size_bytes": int(after.st_size),
            "sha256": digest.hexdigest(),
        }
        recorded_size = record.get("size_bytes")
        if (
            actual["relative_path"] != Path(relative).as_posix()
            or recorded_size is None
            or actual["size_bytes"] != int(recorded_size)
            or actual["sha256"] != str(record.get("sha256") or "").lower()
        ):
            return path, actual, f"{label}完整性校验失败。"
        return path, actual, None
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        PreparationPathError,
    ) as exc:
        return None, None, f"{label}不可用：{exc}"


def _inspect_active_maps(
    project: Any,
    project_root: Path,
    ligand_status: Mapping[str, Any],
) -> dict[str, Any]:
    result = _maps_status_defaults(project)
    manifest_relative, expected_manifest_sha256 = _maps_pointer_from_project(project)
    if not manifest_relative:
        return result

    issues: list[str] = []
    manifest: dict[str, Any] | None = None
    actual_manifest_sha256 = ""
    try:
        manifest_path = _safe_project_path(
            project_root,
            Path(manifest_relative),
            allow_missing=False,
        )
        map_set_id = manifest_path.parent.name
        expected = (
            Path("maps")
            / map_set_id
            / HYDRATED_MAPS_MANIFEST_NAME
        ).as_posix()
        if (
            Path(manifest_relative).as_posix() != expected
            or not autogrid.HYDRATED_MAP_SET_ID_PATTERN.fullmatch(map_set_id)
            or _is_reparse_or_symlink(manifest_path)
        ):
            raise PreparationPathError("active maps manifest 路径不安全。")
        manifest_bytes, manifest_snapshot = _stable_regular_project_file(
            project_root,
            manifest_relative,
            label="active maps manifest",
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        actual_manifest_sha256 = manifest_snapshot["sha256"]
        if (
            not expected_manifest_sha256
            or actual_manifest_sha256 != expected_manifest_sha256
        ):
            issues.append("maps manifest SHA256 与项目指针不一致。")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("active maps manifest 不是 JSON 对象。")
        if manifest.get("protocol_id") != PROTOCOL_ID:
            issues.append("maps manifest 协议标识不匹配。")
        if manifest.get("map_set_id") != map_set_id:
            issues.append("maps manifest 的记录编号与目录不一致。")
        if manifest.get("status") != "ready":
            issues.append("maps manifest 尚未完成。")

        ligand_pointer_path, ligand_pointer_sha256 = _pointer_from_project(project)
        recorded_ligand_manifest = (
            manifest.get("hydrated_ligand_manifest")
            if isinstance(manifest.get("hydrated_ligand_manifest"), Mapping)
            else {}
        )
        if (
            ligand_status.get("preparation_ready") is not True
            or ligand_status.get("valid") is not True
        ):
            issues.append("活动水合配体准备记录已失效。")
        if (
            str(recorded_ligand_manifest.get("path") or "")
            != ligand_pointer_path
            or str(recorded_ligand_manifest.get("sha256") or "")
            != ligand_pointer_sha256
        ):
            issues.append("maps 绑定的水合配体 manifest 已变化。")

        if _project_receptor_mode(project) != "rigid":
            issues.append("水合 maps 只适用于刚性受体。")
        receptor_record = (
            manifest.get("receptor")
            if isinstance(manifest.get("receptor"), Mapping)
            else {}
        )
        if str(receptor_record.get("source_relative_path") or "") != str(
            project.receptor.file or ""
        ):
            issues.append("当前刚性受体路径与 maps 绑定记录不一致。")
        elif str(receptor_record.get("source_sha256") or "") != str(
            receptor_record.get("sha256") or ""
        ):
            issues.append("maps 的刚性受体来源哈希记录不一致。")
        else:
            try:
                _, current_receptor = _stable_regular_project_file(
                    project_root,
                    project.receptor.file,
                    label="刚性受体",
                    maximum_bytes=MAX_RECEPTOR_BYTES,
                )
                if (
                    current_receptor.get("sha256")
                    != receptor_record.get("sha256")
                    or current_receptor.get("size_bytes")
                    != receptor_record.get("size_bytes")
                ):
                    issues.append("当前刚性受体内容与 maps 绑定记录不一致。")
            except (OSError, ValueError, RuntimeError, PreparationPathError) as exc:
                issues.append(f"当前刚性受体不可用：{exc}")

        current_box, box_error = _box_snapshot(project)
        if box_error or current_box != manifest.get("box"):
            issues.append("当前 Box 与 maps 绑定记录不一致。")

        recorded_ligand = (
            manifest.get("hydrated_ligand")
            if isinstance(manifest.get("hydrated_ligand"), Mapping)
            else {}
        )
        ligand_manifest = (
            ligand_status.get("manifest")
            if isinstance(ligand_status.get("manifest"), Mapping)
            else {}
        )
        ligand_outputs = (
            ligand_manifest.get("outputs")
            if isinstance(ligand_manifest.get("outputs"), Mapping)
            else {}
        )
        current_hydrated = (
            ligand_outputs.get("hydrated_pdbqt")
            if isinstance(ligand_outputs.get("hydrated_pdbqt"), Mapping)
            else {}
        )
        if (
            str(recorded_ligand.get("relative_path") or "")
            != str(current_hydrated.get("path") or "")
            or str(recorded_ligand.get("sha256") or "")
            != str(current_hydrated.get("sha256") or "")
            or int(recorded_ligand.get("water_count") or 0)
            != int(current_hydrated.get("water_count") or 0)
            or list(recorded_ligand.get("atom_types") or [])
            != list(current_hydrated.get("atom_types") or [])
        ):
            issues.append("当前水合配体输出与 maps 绑定记录不一致。")
        maps_ligand = (
            manifest.get("ligand")
            if isinstance(manifest.get("ligand"), Mapping)
            else {}
        )
        if (
            str(maps_ligand.get("source_relative_path") or "")
            != str(recorded_ligand.get("relative_path") or "")
            or str(maps_ligand.get("source_sha256") or "")
            != str(recorded_ligand.get("sha256") or "")
        ):
            issues.append("maps 的水合配体来源绑定记录不一致。")

        base_record = (
            manifest.get("base_maps_manifest")
            if isinstance(manifest.get("base_maps_manifest"), Mapping)
            else {}
        )
        base_path, _, base_issue = _record_integrity_issue(
            project_root,
            base_record,
            label="基础 maps manifest",
            expected_parent=manifest_path.parent,
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        if base_issue:
            issues.append(base_issue)
        elif base_path is not None:
            try:
                base_manifest = json.loads(base_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(base_manifest, dict)
                    or base_manifest.get("protocol_id") != PROTOCOL_ID
                    or base_manifest.get("map_set_id") != map_set_id
                    or base_manifest.get("status") != "ready"
                ):
                    issues.append("基础 maps manifest 的协议、编号或状态无效。")
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                issues.append(f"基础 maps manifest 无法解析：{exc}")

        recorded_tool = (
            manifest.get("autogrid")
            if isinstance(manifest.get("autogrid"), Mapping)
            else {}
        )
        if (
            not str(recorded_tool.get("path") or "")
            or not re.fullmatch(
                r"[0-9a-fA-F]{64}",
                str(recorded_tool.get("sha256") or ""),
            )
            or not str(recorded_tool.get("version") or "")
            or not str(recorded_tool.get("source") or "")
        ):
            issues.append("maps 缺少完整的 AutoGrid4 生成工具记录。")

        maps_record = (
            manifest.get("maps")
            if isinstance(manifest.get("maps"), Mapping)
            else {}
        )
        files = maps_record.get("files")
        required_names = maps_record.get("required_files")
        hydrated_ligand_types = maps_record.get("ligand_atom_types")
        autogrid_ligand_types = maps_record.get(
            "autogrid_ligand_atom_types"
        )
        if (
            not isinstance(hydrated_ligand_types, list)
            or "W" not in hydrated_ligand_types
            or len(set(str(item) for item in hydrated_ligand_types))
            != len(hydrated_ligand_types)
            or not isinstance(autogrid_ligand_types, list)
            or "W" in autogrid_ligand_types
            or not {"OA", "HD"}.issubset(
                {str(item) for item in autogrid_ligand_types}
            )
            or not (
                {str(item) for item in hydrated_ligand_types} - {"W"}
            ).issubset({str(item) for item in autogrid_ligand_types})
        ):
            issues.append("水合配体原子类型与 AutoGrid 基础类型记录无效。")
        if (
            not isinstance(files, list)
            or not files
            or not isinstance(required_names, list)
        ):
            issues.append("maps 文件清单不完整。")
        else:
            required_set = {str(name) for name in required_names}
            mandatory = {
                "receptor.maps.fld",
                "receptor.e.map",
                "receptor.d.map",
                "receptor.OA.map",
                "receptor.HD.map",
                "receptor.W.map",
            }
            if not mandatory.issubset(required_set):
                issues.append("maps 文件清单缺少 fld、OA、HD、W、e 或 d map。")
            if str(maps_record.get("prefix") or "") != Path(
                "maps",
                map_set_id,
                "receptor",
            ).as_posix():
                issues.append("maps prefix 与活动记录目录不一致。")
            names: set[str] = set()
            reference_geometry = (
                maps_record.get("geometry")
                if isinstance(maps_record.get("geometry"), Mapping)
                else {}
            )
            manifest_grid = (
                manifest.get("grid")
                if isinstance(manifest.get("grid"), Mapping)
                else {}
            )
            if not _same_geometry(reference_geometry, _grid_geometry(manifest_grid)):
                issues.append("maps 几何与 grid 记录不一致。")
            for item in files:
                if not isinstance(item, Mapping):
                    issues.append("maps 文件清单包含无效记录。")
                    continue
                name = str(item.get("name") or "")
                if not name or name in names:
                    issues.append("maps 文件清单包含空名称或重复名称。")
                    continue
                names.add(name)
                file_path, actual, file_issue = _record_integrity_issue(
                    project_root,
                    item,
                    label=f"map 文件 {name}",
                    expected_parent=manifest_path.parent,
                )
                if file_issue:
                    issues.append(file_issue)
                    continue
                if name.endswith(".map") and file_path is not None and actual is not None:
                    try:
                        parsed = hydrated_maps.parse_autogrid_map(file_path)
                        if (
                            parsed.sha256 != actual["sha256"]
                            or not _same_geometry(
                                parsed.geometry.to_dict(),
                                reference_geometry,
                            )
                        ):
                            issues.append(f"map 文件 {name} 的几何或哈希不匹配。")
                    except hydrated_maps.HydratedMapError as exc:
                        issues.append(f"map 文件 {name} 无效：{exc.message}")
            if required_set != names:
                issues.append("maps 文件清单与 required_files 不一致。")

        water_record = (
            maps_record.get("water_map")
            if isinstance(maps_record.get("water_map"), Mapping)
            else {}
        )
        water_parameters = (
            water_record.get("parameters")
            if isinstance(water_record.get("parameters"), Mapping)
            else {}
        )
        file_items = files if isinstance(files, list) else []
        if (
            str(water_record.get("name") or "") != "receptor.W.map"
            or str(water_record.get("sha256") or "")
            != next(
                (
                    str(item.get("sha256") or "")
                    for item in file_items
                    if isinstance(item, Mapping)
                    and item.get("name") == "receptor.W.map"
                ),
                "",
            )
        ):
            issues.append("W.map 记录与 maps 文件清单不一致。")
        if (
            str(water_record.get("method") or "") != "hydrated_ad4_best_v1"
            or water_parameters
            != {
                "mode": "BEST",
                "weight": hydrated_maps.BEST_WEIGHT,
                "entropy": hydrated_maps.DISPLACEMENT_ENTROPY,
                "oa_weight": hydrated_maps.OA_WEIGHT,
                "hd_weight": hydrated_maps.HD_WEIGHT,
                "output_decimals": hydrated_maps.OUTPUT_DECIMALS,
                "positive_value_rule": (
                    "entropy_if_oa_gt_0_or_hd_gt_0"
                ),
            }
        ):
            issues.append("W.map 生成方法记录无效。")
        water_sources = (
            water_record.get("sources")
            if isinstance(water_record.get("sources"), Mapping)
            else {}
        )
        for source_key, source_name in (
            ("oa", "receptor.OA.map"),
            ("hd", "receptor.HD.map"),
        ):
            source_record = (
                water_sources.get(source_key)
                if isinstance(water_sources.get(source_key), Mapping)
                else {}
            )
            map_record = next(
                (
                    item
                    for item in file_items
                    if isinstance(item, Mapping)
                    and item.get("name") == source_name
                ),
                {},
            )
            if (
                str(source_record.get("relative_path") or "")
                != str(map_record.get("relative_path") or "")
                or str(source_record.get("sha256") or "")
                != str(map_record.get("sha256") or "")
            ):
                issues.append(f"W.map 的 {source_key.upper()} 来源绑定无效。")

        audit_files = manifest.get("audit_files")
        if not isinstance(audit_files, list) or not audit_files:
            issues.append("maps manifest 缺少审计文件。")
        else:
            for item in audit_files:
                if not isinstance(item, Mapping):
                    issues.append("审计文件清单包含无效记录。")
                    continue
                _, _, audit_issue = _record_integrity_issue(
                    project_root,
                    item,
                    label=f"审计文件 {item.get('name') or ''}",
                    maximum_bytes=MAX_RECEPTOR_BYTES,
                    allow_empty=True,
                )
                if audit_issue:
                    issues.append(audit_issue)
    except (
        OSError,
        AttributeError,
        TypeError,
        UnicodeError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        PreparationPathError,
    ) as exc:
        issues.append(str(exc))

    result.update(
        {
            "maps_status": "ready" if manifest is not None and not issues else "invalid",
            "maps_ready": manifest is not None and not issues,
            "maps_valid": manifest is not None and not issues,
            "maps_manifest_sha256": actual_manifest_sha256,
            "maps_manifest": manifest,
            "maps_issues": issues,
            "maps": (
                manifest.get("maps")
                if manifest is not None and not issues
                and isinstance(manifest.get("maps"), Mapping)
                else None
            ),
        }
    )
    return result


def _attach_maps_status(
    project: Any,
    project_root: Path,
    ligand_status: dict[str, Any],
) -> dict[str, Any]:
    ligand_status.update(_inspect_active_maps(project, project_root, ligand_status))
    return ligand_status


def get_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None
    project_root = Path(project.project_dir).expanduser().resolve()
    manifest_relative, expected_manifest_sha256 = _pointer_from_project(project)
    base = {
        "ok": True,
        "protocol_id": PROTOCOL_ID,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "status": "not_prepared",
        "preparation_ready": False,
        "valid": False,
        "active_ligand_manifest": manifest_relative,
        "manifest_sha256": expected_manifest_sha256,
        "manifest": None,
        "issues": [],
        "message": "尚未准备水合配体。",
        "error": None,
    }
    if not manifest_relative:
        return _attach_maps_status(project, project_root, base)

    issues: list[str] = []
    try:
        manifest_path = _safe_project_path(
            project_root,
            Path(manifest_relative),
            allow_missing=False,
        )
        expected_parent = PREPARATION_ROOT / manifest_path.parent.name
        expected_manifest = expected_parent / "manifest.json"
        if Path(manifest_relative).as_posix() != expected_manifest.as_posix():
            raise PreparationPathError("active manifest 不在标准水合配体记录目录。")
        if (
            not PREPARATION_ID_PATTERN.match(manifest_path.parent.name)
            or _is_reparse_or_symlink(manifest_path)
            or not manifest_path.is_file()
        ):
            raise PreparationPathError("active manifest 路径不安全。")
        manifest_bytes = manifest_path.read_bytes()
        if not manifest_bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError("active manifest 大小无效。")
        actual_manifest_sha256 = _sha256_bytes(manifest_bytes)
        if not expected_manifest_sha256 or actual_manifest_sha256 != expected_manifest_sha256:
            issues.append("manifest SHA256 与项目指针不一致。")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("active manifest 不是 JSON 对象。")
        if manifest.get("protocol_id") != PROTOCOL_ID:
            issues.append("manifest 协议标识不匹配。")
        if manifest.get("status") != "finished":
            issues.append("manifest 尚未完成。")

        source = manifest.get("source") if isinstance(manifest.get("source"), Mapping) else {}
        if str(source.get("path") or "") != str(project.ligand.raw_file or ""):
            issues.append("项目当前 raw 配体与准备记录不一致。")
        else:
            try:
                _, current_source = _stable_regular_project_file(
                    project_root,
                    project.ligand.raw_file,
                    label="raw 配体",
                    maximum_bytes=MAX_RAW_LIGAND_BYTES,
                )
                if (
                    current_source.get("sha256") != source.get("sha256")
                    or current_source.get("size_bytes") != source.get("size_bytes")
                ):
                    issues.append("raw 配体内容已变化。")
            except (OSError, ValueError, RuntimeError, PreparationPathError) as exc:
                issues.append(f"raw 配体不可用：{exc}")

        outputs = (
            manifest.get("outputs")
            if isinstance(manifest.get("outputs"), Mapping)
            else {}
        )
        for key, label in (
            ("added_h_sdf", "加氢 SDF"),
            ("hydrated_pdbqt", "水合 PDBQT"),
        ):
            recorded = outputs.get(key) if isinstance(outputs.get(key), Mapping) else {}
            relative = str(recorded.get("path") or "")
            try:
                artifact = _safe_project_path(
                    project_root,
                    Path(relative),
                    allow_missing=False,
                )
                if artifact.parent != manifest_path.parent:
                    raise PreparationPathError(f"{label}不在 active 记录目录。")
                snapshot = _artifact_snapshot(artifact, project_root)
                if (
                    not snapshot.get("exists")
                    or snapshot.get("sha256") != recorded.get("sha256")
                    or snapshot.get("size_bytes") != recorded.get("size_bytes")
                ):
                    issues.append(f"{label}完整性校验失败。")
                if key == "hydrated_pdbqt":
                    inspected = _validate_hydrated_pdbqt(artifact, project_root)
                    if int(inspected.get("water_count") or 0) != int(
                        recorded.get("water_count") or 0
                    ):
                        issues.append("水合 PDBQT 的 W 数量已变化。")
            except (OSError, UnicodeError, ValueError, PreparationPathError) as exc:
                issues.append(f"{label}不可用：{exc}")

        script = manifest.get("script") if isinstance(manifest.get("script"), Mapping) else {}
        script_relative = str(script.get("path") or "")
        try:
            script_path = _safe_project_path(
                project_root,
                Path(script_relative),
                allow_missing=False,
            )
            if script_path.parent != manifest_path.parent:
                raise PreparationPathError("准备脚本不在 active 记录目录。")
            script_snapshot = _artifact_snapshot(script_path, project_root)
            if script_snapshot.get("sha256") != script.get("sha256"):
                issues.append("准备脚本完整性校验失败。")
        except (OSError, ValueError, PreparationPathError) as exc:
            issues.append(f"准备脚本不可用：{exc}")

        base.update(
            {
                "status": "ready" if not issues else "invalid",
                "preparation_ready": not issues,
                "valid": not issues,
                "manifest_sha256": actual_manifest_sha256,
                "manifest": manifest,
                "issues": issues,
                "message": (
                    "水合配体准备记录有效。"
                    if not issues
                    else "水合配体准备记录已失效。"
                ),
            }
        )
        return _attach_maps_status(project, project_root, base)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, PreparationPathError) as exc:
        base.update(
            {
                "status": "invalid",
                "issues": [str(exc)],
                "message": "水合配体准备记录已失效。",
            }
        )
        return _attach_maps_status(project, project_root, base)


def _normalize_hydrated_maps_options(
    options: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if options is None:
        return {}, None
    if not isinstance(options, dict):
        return None, _structured_error(
            "HYDRATED_MAPS_OPTIONS_INVALID",
            "水合 maps 选项必须是对象。",
            suggestion="当前仅允许设置 spacing 与各轴 grid points。",
        )
    unknown = sorted(set(options) - HYDRATED_MAPS_ALLOWED_OPTIONS)
    if unknown:
        return None, _structured_error(
            "HYDRATED_MAPS_OPTIONS_UNSUPPORTED",
            "水合 maps 选项包含未开放字段。",
            raw_error=", ".join(unknown),
            suggestion="当前仅允许设置 spacing 与各轴 grid points。",
        )
    return copy.deepcopy(options), None


def _audit_file_snapshot(
    project_root: Path,
    path: Path,
    *,
    name: str,
    maximum_bytes: int = MAX_RECEPTOR_BYTES,
) -> dict[str, Any]:
    safe = _safe_project_path(project_root, path, allow_missing=False)
    if _is_reparse_or_symlink(safe):
        raise PreparationPathError(f"审计文件 {name} 不能是链接。")
    before = os.lstat(safe)
    if not stat.S_ISREG(before.st_mode) or before.st_size < 0:
        raise ValueError(f"审计文件 {name} 不是普通文件。")
    if before.st_size > maximum_bytes:
        raise ValueError(f"审计文件 {name} 超过大小上限。")
    digest = hashlib.sha256()
    with safe.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = os.lstat(safe)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"读取审计文件 {name} 时内容发生变化。")
    return {
        "name": name,
        "relative_path": _relative_path(safe, project_root),
        "size_bytes": int(after.st_size),
        "sha256": digest.hexdigest(),
    }


def _existing_hydrated_map_sets(project_root: Path) -> set[str]:
    maps_root = _safe_project_path(project_root, Path("maps"))
    if not maps_root.exists():
        return set()
    checked = _safe_project_path(project_root, Path("maps"), allow_missing=False)
    if _is_reparse_or_symlink(checked) or not checked.is_dir():
        raise PreparationPathError("maps 目录不安全。")
    names: set[str] = set()
    for child in checked.iterdir():
        candidate = _safe_project_path(
            project_root,
            Path("maps", child.name),
            allow_missing=False,
        )
        if _is_reparse_or_symlink(candidate):
            raise PreparationPathError(f"maps 记录不能是链接：{child.name}")
        if (
            candidate.is_dir()
            and autogrid.HYDRATED_MAP_SET_ID_PATTERN.fullmatch(child.name)
        ):
            names.add(child.name)
    return names


def _new_hydrated_map_set(
    project_root: Path,
    before: set[str],
    base_result: Mapping[str, Any],
) -> tuple[str, Path] | tuple[None, None]:
    candidate_id = str(base_result.get("map_set_id") or "")
    if candidate_id and autogrid.HYDRATED_MAP_SET_ID_PATTERN.fullmatch(candidate_id):
        relative = Path("maps", candidate_id)
        try:
            candidate = _safe_project_path(
                project_root,
                relative,
                allow_missing=False,
            )
            if not _is_reparse_or_symlink(candidate) and candidate.is_dir():
                return candidate_id, candidate
        except (OSError, PreparationPathError):
            pass
    after = _existing_hydrated_map_sets(project_root)
    created = sorted(after - before)
    if len(created) != 1:
        return None, None
    candidate_id = created[0]
    candidate = _safe_project_path(
        project_root,
        Path("maps", candidate_id),
        allow_missing=False,
    )
    return candidate_id, candidate


def _write_maps_failure_manifest(
    project_root: Path,
    set_dir: Path,
    manifest: dict[str, Any],
    *,
    code: str,
    message: str,
    raw_error: str,
    suggestion: str,
) -> dict[str, Any]:
    manifest["status"] = "failed"
    manifest["finished_at"] = _now_iso()
    manifest["error"] = {
        "code": code,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }
    manifest_path = set_dir / HYDRATED_MAPS_MANIFEST_NAME
    atomic_write_json(manifest_path, manifest)
    manifest_sha256 = _sha256_bytes(manifest_path.read_bytes())
    response = _structured_error(
        code,
        message,
        raw_error=raw_error,
        suggestion=suggestion,
        project_dir=str(manifest.get("project_dir") or ""),
        manifest_file=_relative_path(manifest_path, project_root),
    )
    response.update(
        {
            "map_set_id": str(manifest.get("map_set_id") or ""),
            "manifest_sha256": manifest_sha256,
            "manifest": manifest,
        }
    )
    return response


def _validate_generated_base_maps(
    project_root: Path,
    base_result: Mapping[str, Any],
    *,
    map_set_id: str,
    set_dir: Path,
    receptor_before: Mapping[str, Any],
    receptor_relative: str,
    hydrated_before: Mapping[str, Any],
    autogrid_before: Mapping[str, Any],
    box_before: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_relative = str(base_result.get("manifest_file") or "")
    expected_manifest = Path("maps", map_set_id, "manifest.json").as_posix()
    if manifest_relative != expected_manifest:
        raise ValueError("基础 maps manifest 路径与记录编号不一致。")
    base_bytes, base_snapshot_raw = _stable_regular_project_file(
        project_root,
        manifest_relative,
        label="基础 maps manifest",
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    base_manifest = json.loads(base_bytes.decode("utf-8"))
    if (
        not isinstance(base_manifest, dict)
        or base_manifest.get("protocol_id") != PROTOCOL_ID
        or base_manifest.get("map_set_id") != map_set_id
        or base_manifest.get("status") != "ready"
    ):
        raise ValueError("基础 maps manifest 的协议、编号或状态无效。")
    base_snapshot = {
        "relative_path": base_snapshot_raw["path"],
        "size_bytes": base_snapshot_raw["size_bytes"],
        "sha256": base_snapshot_raw["sha256"],
    }

    receptor_record = (
        base_manifest.get("receptor")
        if isinstance(base_manifest.get("receptor"), Mapping)
        else {}
    )
    ligand_record = (
        base_manifest.get("ligand")
        if isinstance(base_manifest.get("ligand"), Mapping)
        else {}
    )
    if (
        str(receptor_record.get("source_relative_path") or "")
        != Path(receptor_relative).as_posix()
        or str(receptor_record.get("source_sha256") or "")
        != str(receptor_before.get("sha256") or "")
    ):
        raise ValueError("基础 maps 未绑定到当前刚性受体。")
    if (
        str(ligand_record.get("source_relative_path") or "")
        != str(hydrated_before.get("relative_path") or "")
        or str(ligand_record.get("source_sha256") or "")
        != str(hydrated_before.get("sha256") or "")
    ):
        raise ValueError("基础 maps 未绑定到活动水合配体。")

    inputs_dir = set_dir / "inputs"
    for record, label in (
        (receptor_record, "基础 maps 受体快照"),
        (ligand_record, "基础 maps 水合配体快照"),
    ):
        _, _, issue = _record_integrity_issue(
            project_root,
            record,
            label=label,
            expected_parent=inputs_dir,
            maximum_bytes=MAX_RECEPTOR_BYTES,
        )
        if issue:
            raise ValueError(issue)

    grid = (
        base_manifest.get("grid")
        if isinstance(base_manifest.get("grid"), Mapping)
        else {}
    )
    grid_center = (
        grid.get("center")
        if isinstance(grid.get("center"), Mapping)
        else {}
    )
    if any(
        not math.isclose(
            float(grid_center.get(axis)),
            float((box_before.get("center") or {}).get(axis)),
            rel_tol=0.0,
            abs_tol=hydrated_maps.GEOMETRY_ABS_TOLERANCE,
        )
        for axis in ("x", "y", "z")
    ):
        raise ValueError("基础 maps 网格中心与当前 Box 不一致。")

    base_tool = (
        base_manifest.get("autogrid")
        if isinstance(base_manifest.get("autogrid"), Mapping)
        else {}
    )
    if any(
        str(base_tool.get(key) or "") != str(autogrid_before.get(key) or "")
        for key in ("path", "sha256", "version")
    ):
        raise ValueError("基础 maps 记录的 AutoGrid4 与生成前快照不一致。")
    if (
        not isinstance(base_tool.get("command"), list)
        or not base_tool.get("command")
        or base_tool.get("exit_code") is None
        or int(base_tool.get("exit_code")) != 0
    ):
        raise ValueError("基础 maps 未记录成功的 AutoGrid4 参数数组。")
    log_summary = (
        base_tool.get("log_summary")
        if isinstance(base_tool.get("log_summary"), Mapping)
        else {}
    )
    if log_summary.get("successful_completion") is not True:
        raise ValueError("基础 maps 的 AutoGrid 日志未确认 Successful Completion。")

    gpf_record = (
        base_manifest.get("gpf")
        if isinstance(base_manifest.get("gpf"), Mapping)
        else {}
    )
    gpf_path, _, gpf_issue = _record_integrity_issue(
        project_root,
        gpf_record,
        label="水合 maps GPF",
        expected_parent=set_dir,
        maximum_bytes=autogrid.MAX_PARAMETER_FILE_BYTES,
    )
    if gpf_issue or gpf_path is None:
        raise ValueError(gpf_issue or "水合 maps GPF 不可用。")

    maps_record = (
        base_manifest.get("maps")
        if isinstance(base_manifest.get("maps"), Mapping)
        else {}
    )
    required_names = maps_record.get("required_files")
    file_records = maps_record.get("files")
    if not isinstance(required_names, list) or not isinstance(file_records, list):
        raise ValueError("基础 maps 文件清单无效。")
    if "receptor.OA.map" not in required_names or "receptor.HD.map" not in required_names:
        raise ValueError("基础 maps 缺少生成 W.map 所需的 OA 或 HD map。")
    records_by_name = {
        str(item.get("name") or ""): item
        for item in file_records
        if isinstance(item, Mapping) and item.get("name")
    }
    if set(str(name) for name in required_names) - set(records_by_name):
        raise ValueError("基础 maps 文件清单缺少必需文件记录。")

    validated_files: list[dict[str, Any]] = []
    reference_geometry: dict[str, Any] | None = None
    paths: dict[str, Path] = {}
    for name in [str(value) for value in required_names]:
        record = records_by_name[name]
        path, actual, issue = _record_integrity_issue(
            project_root,
            record,
            label=f"基础 map {name}",
            expected_parent=set_dir,
        )
        if issue or path is None or actual is None:
            raise ValueError(issue or f"基础 map {name} 不可用。")
        paths[name] = path
        validated = {"name": name, **actual}
        if name.endswith(".map"):
            parsed = hydrated_maps.parse_autogrid_map(path)
            geometry = parsed.geometry.to_dict()
            if parsed.sha256 != actual["sha256"]:
                raise ValueError(f"基础 map {name} 在解析期间发生变化。")
            if reference_geometry is None:
                reference_geometry = geometry
            elif not _same_geometry(reference_geometry, geometry):
                raise ValueError(f"基础 map {name} 的几何与其他 map 不一致。")
            validated["geometry"] = geometry
        validated_files.append(validated)
    if reference_geometry is None or not _same_geometry(
        reference_geometry,
        _grid_geometry(grid),
    ):
        raise ValueError("基础 maps 的文件几何与 grid 记录不一致。")

    audit_files = [
        _audit_file_snapshot(project_root, gpf_path, name="receptor.gpf"),
        _audit_file_snapshot(
            project_root,
            _safe_project_path(
                project_root,
                Path(str(receptor_record.get("relative_path") or "")),
                allow_missing=False,
            ),
            name="inputs/receptor.pdbqt",
        ),
        _audit_file_snapshot(
            project_root,
            _safe_project_path(
                project_root,
                Path(str(ligand_record.get("relative_path") or "")),
                allow_missing=False,
            ),
            name="inputs/ligand.pdbqt",
        ),
    ]
    for name in ("stdout.txt", "stderr.txt", "autogrid.glg"):
        audit_files.append(
            _audit_file_snapshot(
                project_root,
                set_dir / name,
                name=name,
            )
        )
    log_path = set_dir / "autogrid.glg"
    if "successful completion" not in log_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).lower():
        raise ValueError("AutoGrid 审计日志未包含 Successful Completion。")
    return {
        "base_manifest": base_manifest,
        "base_manifest_snapshot": base_snapshot,
        "grid": copy.deepcopy(grid),
        "geometry": reference_geometry,
        "files": validated_files,
        "ligand_atom_types": copy.deepcopy(
            maps_record.get("ligand_atom_types") or []
        ),
        "paths": paths,
        "gpf": {
            "relative_path": gpf_record.get("relative_path"),
            "size_bytes": gpf_record.get("size_bytes"),
            "sha256": gpf_record.get("sha256"),
        },
        "audit_files": audit_files,
        "autogrid_command": copy.deepcopy(base_tool.get("command")),
    }


def prepare_hydrated_ligand(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None
    project_root = Path(project.project_dir).expanduser().resolve()

    raw_file = str(project.ligand.raw_file or "")
    suffix = Path(raw_file).suffix.lower()
    if suffix not in {".sdf", ".mol"}:
        return _structured_error(
            "HYDRATED_LIGAND_FORMAT_UNSUPPORTED",
            "水合配体准备仅支持项目内 SDF 或 MOL。",
            raw_error=suffix or "未记录 raw 配体",
            suggestion="请先导入含键级和三维坐标的 SDF 或 MOL。",
            project_dir=project.project_dir,
        )
    try:
        source_bytes, source_before = _stable_regular_project_file(
            project_root,
            raw_file,
            label="raw 配体",
            maximum_bytes=MAX_RAW_LIGAND_BYTES,
        )
    except (OSError, ValueError, RuntimeError, PreparationPathError) as exc:
        return _structured_error(
            "HYDRATED_LIGAND_SOURCE_INVALID",
            "raw 配体不可用于水合准备。",
            raw_error=str(exc),
            suggestion="请确认文件位于项目内、不是链接、非空且格式为 SDF 或 MOL。",
            project_dir=project.project_dir,
        )

    tools = _tool_status()
    python_path, tool_error = _validate_detected_tools(tools)
    if tool_error:
        tool_error["project_dir"] = project.project_dir
        return tool_error

    lock_path = project_root / ".hydrated-ligand.lock"
    with _exclusive_file_lock(lock_path):
        try:
            preparation_id = _next_preparation_id(project_root)
            record_relative = PREPARATION_ROOT / preparation_id
            record_dir = _safe_project_path(project_root, record_relative)
            record_dir.mkdir(exist_ok=False)
            record_dir = _safe_project_path(
                project_root,
                record_relative,
                allow_missing=False,
            )
            if _is_reparse_or_symlink(record_dir) or not record_dir.is_dir():
                raise PreparationPathError("水合配体准备记录目录不安全。")
        except (OSError, ValueError, PreparationPathError) as exc:
            return _structured_error(
                "HYDRATED_PREPARATION_RECORD_ERROR",
                "无法创建水合配体准备记录。",
                raw_error=str(exc),
                suggestion="请检查项目 protocols 目录的写入权限和路径完整性。",
                project_dir=project.project_dir,
            )

        manifest_path = record_dir / "manifest.json"
        manifest_relative = _relative_path(manifest_path, project_root)
        source_snapshot_path = record_dir / f"source_input{suffix}"
        script_path = record_dir / "prepare_hydrated_ligand.py"
        candidate_added_h = record_dir / "candidate_ligand_added_h.sdf"
        candidate_pdbqt = record_dir / "candidate_ligand_hydrated.pdbqt"
        final_added_h = record_dir / "ligand_added_h.sdf"
        final_pdbqt = record_dir / "ligand_hydrated.pdbqt"
        stdout_path = record_dir / "stdout.txt"
        stderr_path = record_dir / "stderr.txt"
        created_at = _now_iso()
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "protocol_id": PROTOCOL_ID,
            "preparation_id": preparation_id,
            "project_dir": project.project_dir,
            "record_dir": _relative_path(record_dir, project_root),
            "manifest_file": manifest_relative,
            "status": "starting",
            "created_at": created_at,
            "started_at": created_at,
            "finished_at": None,
            "source": {
                **source_before,
                "snapshot_file": _relative_path(source_snapshot_path, project_root),
            },
            "script": {},
            "tools": {"detected": copy.deepcopy(tools)},
            "command": [],
            "outputs": {},
            "integrity": {},
            "error": None,
        }

        try:
            atomic_write_bytes(source_snapshot_path, source_bytes)
            script_text = _generated_script_text()
            atomic_write_text(script_path, script_text)
            source_snapshot = _artifact_snapshot(source_snapshot_path, project_root)
            if source_snapshot.get("sha256") != source_before.get("sha256"):
                raise RuntimeError("raw 配体快照与源文件 SHA256 不一致。")
            script_before = _artifact_snapshot(script_path, project_root)
            if not script_before.get("exists"):
                raise RuntimeError("未能写入项目内准备脚本。")
            manifest["script"] = script_before

            python_before = _stable_external_file_snapshot(
                python_path,
                label="Python",
            )
            probe_command = [
                python_path,
                "-I",
                "-B",
                str(script_path),
                "probe",
            ]
            probe_completed = meeko_adapter.run_preparation_command(
                probe_command,
                cwd=record_dir,
            )
            probe_stdout = str(probe_completed.stdout or "")
            probe_stderr = str(probe_completed.stderr or "")
            probe_payload = _parse_last_json(probe_stdout)
            if (
                int(probe_completed.returncode) != 0
                or probe_payload.get("ok") is not True
                or probe_payload.get("hydrate_supported") is not True
                or int(probe_payload.get("probe_water_count") or 0) <= 0
            ):
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_MEEKO_CAPABILITY_MISSING",
                    message="Meeko 水合配体能力不可用。",
                    raw_error=probe_stderr or probe_stdout,
                    suggestion="请使用支持 MoleculePreparation(hydrate=True) 的 Meeko 工具链。",
                )

            rdkit_before = _stable_external_file_snapshot(
                str(probe_payload.get("rdkit_module_file") or ""),
                label="RDKit",
            )
            meeko_before = _stable_external_file_snapshot(
                str(probe_payload.get("meeko_module_file") or ""),
                label="Meeko",
            )
            manifest["tools"].update(
                {
                    "probe_command": probe_command,
                    "probe": probe_payload,
                    "probe_stdout": probe_stdout,
                    "probe_stderr": probe_stderr,
                    "before": {
                        "python": python_before,
                        "rdkit": rdkit_before,
                        "meeko": meeko_before,
                    },
                    "rdkit_version": str(probe_payload.get("rdkit_version") or ""),
                    "meeko_version": str(probe_payload.get("meeko_version") or ""),
                }
            )

            prepare_command = [
                python_path,
                "-I",
                "-B",
                str(script_path),
                "prepare",
                str(source_snapshot_path),
                str(candidate_added_h),
                str(candidate_pdbqt),
            ]
            manifest["command"] = prepare_command
            manifest["status"] = "running"
            atomic_write_json(manifest_path, manifest)
            completed = meeko_adapter.run_preparation_command(
                prepare_command,
                cwd=record_dir,
            )
            stdout = str(completed.stdout or "")
            stderr = str(completed.stderr or "")
            atomic_write_text(stdout_path, stdout)
            atomic_write_text(stderr_path, stderr)
            execution_payload = _parse_last_json(stdout)
            manifest["execution"] = {
                "exit_code": int(completed.returncode),
                "result": execution_payload,
                "stdout": _artifact_snapshot(stdout_path, project_root),
                "stderr": _artifact_snapshot(stderr_path, project_root),
            }
            if int(completed.returncode) != 0 or execution_payload.get("ok") is not True:
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_LIGAND_PREPARATION_FAILED",
                    message="水合配体准备失败。",
                    raw_error=stderr or stdout,
                    suggestion="请检查配体三维坐标、键级和工具链版本。",
                )

            added_candidate = _validate_added_h_sdf(candidate_added_h, project_root)
            pdbqt_candidate = _validate_hydrated_pdbqt(candidate_pdbqt, project_root)
            if int(pdbqt_candidate.get("water_count") or 0) <= 0:
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_LIGAND_WATER_MISSING",
                    message="水合配体未生成 W 原子。",
                    raw_error=json.dumps(pdbqt_candidate, ensure_ascii=False),
                    suggestion="请检查 Meeko hydrate 能力和配体供体/受体结构。",
                )

            _, source_after = _stable_regular_project_file(
                project_root,
                raw_file,
                label="raw 配体",
                maximum_bytes=MAX_RAW_LIGAND_BYTES,
            )
            python_after = _stable_external_file_snapshot(python_path, label="Python")
            rdkit_after = _stable_external_file_snapshot(
                str(probe_payload.get("rdkit_module_file") or ""),
                label="RDKit",
            )
            meeko_after = _stable_external_file_snapshot(
                str(probe_payload.get("meeko_module_file") or ""),
                label="Meeko",
            )
            script_after = _artifact_snapshot(script_path, project_root)
            integrity = {
                "source_before": source_before,
                "source_after": source_after,
                "source_unchanged": _snapshots_match(source_before, source_after),
                "tools_after": {
                    "python": python_after,
                    "rdkit": rdkit_after,
                    "meeko": meeko_after,
                },
                "tools_unchanged": (
                    _snapshots_match(python_before, python_after)
                    and _snapshots_match(rdkit_before, rdkit_after)
                    and _snapshots_match(meeko_before, meeko_after)
                ),
                "script_unchanged": (
                    script_after.get("sha256") == script_before.get("sha256")
                ),
            }
            manifest["integrity"] = integrity
            if not integrity["source_unchanged"]:
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_LIGAND_SOURCE_CHANGED",
                    message="准备期间 raw 配体发生变化，结果未发布。",
                    raw_error=json.dumps(integrity, ensure_ascii=False, sort_keys=True),
                    suggestion="请确认当前 raw 配体后重新准备。",
                )
            if not integrity["tools_unchanged"] or not integrity["script_unchanged"]:
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_PREPARATION_TOOL_CHANGED",
                    message="准备期间工具或脚本发生变化，结果未发布。",
                    raw_error=json.dumps(integrity, ensure_ascii=False, sort_keys=True),
                    suggestion="请重新检测工具链后再次准备。",
                )

            atomic_write_bytes(final_added_h, candidate_added_h.read_bytes())
            atomic_write_bytes(final_pdbqt, candidate_pdbqt.read_bytes())
            final_added = _validate_added_h_sdf(final_added_h, project_root)
            final_hydrated = _validate_hydrated_pdbqt(final_pdbqt, project_root)
            if (
                final_added.get("sha256") != added_candidate.get("sha256")
                or final_hydrated.get("sha256") != pdbqt_candidate.get("sha256")
            ):
                raise RuntimeError("原子发布后的输出与候选文件不一致。")

            manifest["outputs"] = {
                "added_h_sdf": final_added,
                "hydrated_pdbqt": final_hydrated,
            }
            manifest["water_count"] = int(final_hydrated["water_count"])
            manifest["status"] = "finished"
            manifest["finished_at"] = _now_iso()
            manifest["error"] = None
            atomic_write_json(manifest_path, manifest)
            manifest_sha256 = _sha256_bytes(manifest_path.read_bytes())

            with _project_lock(project_root):
                manifest_check = _artifact_snapshot(manifest_path, project_root)
                if (
                    not manifest_check.get("exists")
                    or manifest_check.get("sha256") != manifest_sha256
                ):
                    raise RuntimeError("激活前 manifest 完整性校验失败。")
                added_check = _validate_added_h_sdf(final_added_h, project_root)
                hydrated_check = _validate_hydrated_pdbqt(final_pdbqt, project_root)
                if (
                    added_check.get("sha256") != final_added.get("sha256")
                    or hydrated_check.get("sha256") != final_hydrated.get("sha256")
                    or hydrated_check.get("water_count") != final_hydrated.get("water_count")
                ):
                    raise RuntimeError("激活前水合配体输出完整性校验失败。")
                activation_script = _artifact_snapshot(script_path, project_root)
                if activation_script.get("sha256") != script_before.get("sha256"):
                    raise RuntimeError("激活前准备脚本完整性校验失败。")
                activation_python = _stable_external_file_snapshot(
                    python_path,
                    label="Python",
                )
                activation_rdkit = _stable_external_file_snapshot(
                    str(probe_payload.get("rdkit_module_file") or ""),
                    label="RDKit",
                )
                activation_meeko = _stable_external_file_snapshot(
                    str(probe_payload.get("meeko_module_file") or ""),
                    label="Meeko",
                )
                if not (
                    _snapshots_match(python_before, activation_python)
                    and _snapshots_match(rdkit_before, activation_rdkit)
                    and _snapshots_match(meeko_before, activation_meeko)
                ):
                    raise RuntimeError("激活前工具完整性校验失败。")
                current_data, _, _ = _read_and_migrate_project_unlocked(
                    project_root,
                    persist_migration=True,
                )
                current_project = _project_from_dict(current_data, project_root)
                if str(current_project.ligand.raw_file or "") != raw_file:
                    raise RuntimeError("project.json 的 raw 配体引用已变化。")
                _, source_at_activation = _stable_regular_project_file(
                    project_root,
                    raw_file,
                    label="raw 配体",
                    maximum_bytes=MAX_RAW_LIGAND_BYTES,
                )
                if not _snapshots_match(source_before, source_at_activation):
                    raise RuntimeError("激活前 raw 配体内容已变化。")
                current_project.preserved_data[PROJECT_STATE_KEY] = {
                    "active_ligand_manifest": {
                        "path": manifest_relative,
                        "sha256": manifest_sha256,
                    },
                    "updated_at": _now_iso(),
                }
                current_project.updated_at = _now_iso()
                current_project.revision += 1
                _write_project_json_unlocked(project_root, current_project)

            status = get_status(str(project_root))
            status.update(
                {
                    "preparation_id": preparation_id,
                    "manifest_file": manifest_relative,
                    "water_count": int(final_hydrated["water_count"]),
                    "message": "水合配体已准备并保存为独立记录。",
                }
            )
            return status
        except (OSError, UnicodeError, ValueError, RuntimeError, PreparationPathError) as exc:
            try:
                manifest.setdefault("outputs", {})
                manifest["candidate_outputs"] = {
                    "added_h_sdf": _artifact_snapshot(candidate_added_h, project_root),
                    "hydrated_pdbqt": _artifact_snapshot(candidate_pdbqt, project_root),
                }
                return _write_failure_manifest(
                    manifest_path,
                    manifest,
                    code="HYDRATED_LIGAND_PREPARATION_ERROR",
                    message="水合配体准备未完成。",
                    raw_error=str(exc),
                    suggestion="请检查输入结构、项目路径和工具链后重试。",
                )
            except Exception as manifest_exc:  # noqa: BLE001 - preserve structured boundary.
                return _structured_error(
                    "HYDRATED_LIGAND_AUDIT_WRITE_FAILED",
                    "水合配体准备失败，且审计记录未能完整写入。",
                    raw_error=f"{exc}; audit: {manifest_exc}",
                    suggestion="请检查项目 protocols 目录的写入权限。",
                    project_dir=project.project_dir,
                    preparation_id=preparation_id,
                    manifest_file=manifest_relative,
                )


def generate_hydrated_maps(
    project_dir: str,
    options: dict[str, Any] | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_options, options_error = _normalize_hydrated_maps_options(options)
    if options_error:
        return options_error
    assert normalized_options is not None

    project, project_error = _load_project_model(project_dir)
    if project_error:
        return project_error
    assert project is not None
    project_root = Path(project.project_dir).expanduser().resolve()
    if _project_receptor_mode(project) != "rigid":
        return _structured_error(
            "HYDRATED_MAPS_RIGID_RECEPTOR_REQUIRED",
            "水合 maps 仅支持刚性受体。",
            suggestion="请切换为刚性受体后重新生成 maps。",
            project_dir=project.project_dir,
        )
    box_before, box_error = _box_snapshot(project)
    if box_error:
        return box_error
    assert box_before is not None

    ligand_status = get_status(str(project_root))
    if (
        not ligand_status.get("ok")
        or ligand_status.get("preparation_ready") is not True
        or ligand_status.get("valid") is not True
    ):
        return _structured_error(
            "HYDRATED_LIGAND_NOT_READY",
            "活动水合配体准备记录不可用。",
            raw_error="；".join(
                str(issue) for issue in ligand_status.get("issues", [])
            ),
            suggestion="请重新准备并校验水合配体。",
            project_dir=project.project_dir,
        )
    ligand_manifest = (
        ligand_status.get("manifest")
        if isinstance(ligand_status.get("manifest"), Mapping)
        else {}
    )
    ligand_outputs = (
        ligand_manifest.get("outputs")
        if isinstance(ligand_manifest.get("outputs"), Mapping)
        else {}
    )
    hydrated_record = (
        ligand_outputs.get("hydrated_pdbqt")
        if isinstance(ligand_outputs.get("hydrated_pdbqt"), Mapping)
        else {}
    )
    hydrated_relative = str(hydrated_record.get("path") or "")
    try:
        hydrated_validated = _validate_hydrated_pdbqt(
            _safe_project_path(
                project_root,
                Path(hydrated_relative),
                allow_missing=False,
            ),
            project_root,
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
        PreparationPathError,
    ) as exc:
        return _structured_error(
            "HYDRATED_LIGAND_OUTPUT_INVALID",
            "活动水合配体输出不可用。",
            raw_error=str(exc),
            suggestion="请重新准备水合配体。",
            project_dir=project.project_dir,
        )
    hydrated_before = {
        "relative_path": hydrated_validated["path"],
        "size_bytes": hydrated_validated["size_bytes"],
        "sha256": hydrated_validated["sha256"],
        "water_count": hydrated_validated["water_count"],
        "atom_count": hydrated_validated["atom_count"],
        "atom_types": copy.deepcopy(hydrated_validated["atom_types"]),
    }
    if (
        hydrated_before["sha256"] != hydrated_record.get("sha256")
        or hydrated_before["size_bytes"] != hydrated_record.get("size_bytes")
    ):
        return _structured_error(
            "HYDRATED_LIGAND_OUTPUT_CHANGED",
            "活动水合配体输出已变化。",
            suggestion="请重新准备并校验水合配体。",
            project_dir=project.project_dir,
        )
    ligand_manifest_path, ligand_manifest_sha256 = _pointer_from_project(project)
    if (
        not ligand_manifest_path
        or ligand_manifest_sha256 != ligand_status.get("manifest_sha256")
    ):
        return _structured_error(
            "HYDRATED_LIGAND_MANIFEST_CHANGED",
            "水合配体 manifest 指针已变化。",
            suggestion="请重新读取项目状态后再生成 maps。",
            project_dir=project.project_dir,
        )

    receptor_relative = str(project.receptor.file or "")
    if Path(receptor_relative).suffix.lower() != ".pdbqt":
        return _structured_error(
            "HYDRATED_MAPS_RECEPTOR_INVALID",
            "水合 maps 需要项目内刚性受体 PDBQT。",
            suggestion="请先导入准备后的刚性受体 PDBQT。",
            project_dir=project.project_dir,
        )
    try:
        _, receptor_raw = _stable_regular_project_file(
            project_root,
            receptor_relative,
            label="刚性受体",
            maximum_bytes=MAX_RECEPTOR_BYTES,
        )
    except (OSError, ValueError, RuntimeError, PreparationPathError) as exc:
        return _structured_error(
            "HYDRATED_MAPS_RECEPTOR_INVALID",
            "当前刚性受体不可用于生成 maps。",
            raw_error=str(exc),
            suggestion="请重新导入项目内的刚性受体 PDBQT。",
            project_dir=project.project_dir,
        )
    receptor_before = {
        "source_relative_path": receptor_raw["path"],
        "source_sha256": receptor_raw["sha256"],
        "path": receptor_raw["path"],
        "relative_path": receptor_raw["path"],
        "size_bytes": receptor_raw["size_bytes"],
        "sha256": receptor_raw["sha256"],
    }

    autogrid_before, autogrid_error = _detect_autogrid_snapshot()
    if autogrid_error:
        autogrid_error["project_dir"] = project.project_dir
        return autogrid_error
    assert autogrid_before is not None

    maps_lock = project_root / ".hydrated-maps.lock"
    with _exclusive_file_lock(maps_lock):
        try:
            existing_sets = _existing_hydrated_map_sets(project_root)
        except (OSError, ValueError, PreparationPathError) as exc:
            return _structured_error(
                "HYDRATED_MAPS_DIRECTORY_INVALID",
                "水合 maps 记录目录不可用。",
                raw_error=str(exc),
                suggestion="请检查项目 maps 目录。",
                project_dir=project.project_dir,
            )

        created_at = _now_iso()
        base_result = autogrid.generate_hydrated_base_maps(
            str(project_root),
            hydrated_relative,
            normalized_options,
            runner=runner,
        )
        map_set_id, set_dir = _new_hydrated_map_set(
            project_root,
            existing_sets,
            base_result,
        )
        if not map_set_id or set_dir is None:
            error = (
                base_result.get("error")
                if isinstance(base_result.get("error"), Mapping)
                else {}
            )
            return _structured_error(
                str(error.get("code") or "HYDRATED_BASE_MAPS_RECORD_MISSING"),
                str(
                    error.get("message")
                    or "AutoGrid4 未创建可审计的水合基础 maps 记录。"
                ),
                raw_error=str(error.get("raw_error") or ""),
                suggestion=str(
                    error.get("suggestion")
                    or "请检查 maps 目录和 AutoGrid 日志。"
                ),
                project_dir=project.project_dir,
            )

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "protocol_id": PROTOCOL_ID,
            "stability": "experimental",
            "map_set_id": map_set_id,
            "project_dir": project.project_dir,
            "status": "starting",
            "created_at": created_at,
            "started_at": created_at,
            "finished_at": None,
            "hydrated_ligand_manifest": {
                "path": ligand_manifest_path,
                "sha256": ligand_manifest_sha256,
            },
            "hydrated_ligand": copy.deepcopy(hydrated_before),
            "ligand": {
                **copy.deepcopy(hydrated_before),
                "source_relative_path": hydrated_before["relative_path"],
                "source_sha256": hydrated_before["sha256"],
            },
            "receptor": copy.deepcopy(receptor_before),
            "box": copy.deepcopy(box_before),
            "requested_options": copy.deepcopy(normalized_options),
            "base_maps_manifest": {},
            "autogrid": {
                **copy.deepcopy(autogrid_before),
                "command": [],
            },
            "maps": {},
            "audit_files": [],
            "integrity": {},
            "error": None,
        }
        if not base_result.get("ok"):
            base_error = (
                base_result.get("error")
                if isinstance(base_result.get("error"), Mapping)
                else {}
            )
            base_manifest_path = set_dir / "manifest.json"
            if base_manifest_path.is_file():
                try:
                    manifest["base_maps_manifest"] = (
                        _stable_project_artifact_snapshot(
                            project_root,
                            base_manifest_path,
                            label="失败的基础 maps manifest",
                            maximum_bytes=MAX_MANIFEST_BYTES,
                        )
                    )
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    PreparationPathError,
                ):
                    pass
            return _write_maps_failure_manifest(
                project_root,
                set_dir,
                manifest,
                code=str(base_error.get("code") or "HYDRATED_BASE_MAPS_FAILED"),
                message=str(
                    base_error.get("message")
                    or "AutoGrid4 未生成完整的水合基础 maps。"
                ),
                raw_error=str(base_error.get("raw_error") or ""),
                suggestion=str(
                    base_error.get("suggestion")
                    or f"请查看 maps/{map_set_id}/autogrid.glg。"
                ),
            )

        try:
            bundle = _validate_generated_base_maps(
                project_root,
                base_result,
                map_set_id=map_set_id,
                set_dir=set_dir,
                receptor_before=receptor_before,
                receptor_relative=receptor_relative,
                hydrated_before=hydrated_before,
                autogrid_before=autogrid_before,
                box_before=box_before,
            )
            manifest["base_maps_manifest"] = bundle["base_manifest_snapshot"]
            manifest["audit_files"] = bundle["audit_files"]
            manifest["autogrid"]["command"] = bundle["autogrid_command"]
            manifest["autogrid"]["exit_code"] = 0

            oa_path = bundle["paths"]["receptor.OA.map"]
            hd_path = bundle["paths"]["receptor.HD.map"]
            water_path = set_dir / "receptor.W.map"
            water_result = hydrated_maps.generate_best_water_map(
                oa_path,
                hd_path,
                water_path,
            )
            water_parsed = hydrated_maps.parse_autogrid_map(water_path)
            water_snapshot = _stable_project_artifact_snapshot(
                project_root,
                water_path,
                label="W.map",
                maximum_bytes=hydrated_maps.MAX_MAP_FILE_BYTES,
            )
            if (
                water_snapshot["sha256"] != water_parsed.sha256
                or water_snapshot["sha256"]
                != str((water_result.get("output") or {}).get("sha256") or "")
                or not _same_geometry(
                    water_parsed.geometry.to_dict(),
                    bundle["geometry"],
                )
            ):
                raise RuntimeError("W.map 的哈希或几何与基础 maps 不一致。")

            water_file_record = {
                "name": "receptor.W.map",
                **water_snapshot,
                "geometry": water_parsed.geometry.to_dict(),
            }
            map_files = [copy.deepcopy(item) for item in bundle["files"]]
            map_files.append(water_file_record)
            required_files = [str(item["name"]) for item in map_files]
            oa_record = next(
                item for item in map_files if item["name"] == "receptor.OA.map"
            )
            hd_record = next(
                item for item in map_files if item["name"] == "receptor.HD.map"
            )
            water_record = {
                **water_file_record,
                "method": str(water_result.get("method") or ""),
                "parameters": copy.deepcopy(water_result.get("parameters") or {}),
                "statistics": copy.deepcopy(water_result.get("statistics") or {}),
                "sources": {
                    "oa": {
                        "relative_path": oa_record["relative_path"],
                        "size_bytes": oa_record["size_bytes"],
                        "sha256": oa_record["sha256"],
                    },
                    "hd": {
                        "relative_path": hd_record["relative_path"],
                        "size_bytes": hd_record["size_bytes"],
                        "sha256": hd_record["sha256"],
                    },
                },
            }
            manifest["maps"] = {
                "prefix": Path("maps", map_set_id, "receptor").as_posix(),
                "ligand_atom_types": copy.deepcopy(
                    hydrated_before["atom_types"]
                ),
                "autogrid_ligand_atom_types": copy.deepcopy(
                    bundle["ligand_atom_types"]
                ),
                "required_files": required_files,
                "files": map_files,
                "geometry": copy.deepcopy(bundle["geometry"]),
                "water_map": water_record,
            }
            base_grid = bundle["grid"]
            manifest["grid"] = copy.deepcopy(base_grid)
            manifest["resolved_options"] = {
                "spacing": base_grid.get("spacing"),
                "grid_points": copy.deepcopy(base_grid.get("grid_points") or {}),
            }

            _, receptor_after_raw = _stable_regular_project_file(
                project_root,
                receptor_relative,
                label="刚性受体",
                maximum_bytes=MAX_RECEPTOR_BYTES,
            )
            hydrated_after_validated = _validate_hydrated_pdbqt(
                _safe_project_path(
                    project_root,
                    Path(hydrated_relative),
                    allow_missing=False,
                ),
                project_root,
            )
            hydrated_after = {
                "relative_path": hydrated_after_validated["path"],
                "size_bytes": hydrated_after_validated["size_bytes"],
                "sha256": hydrated_after_validated["sha256"],
                "water_count": hydrated_after_validated["water_count"],
                "atom_count": hydrated_after_validated["atom_count"],
                "atom_types": copy.deepcopy(
                    hydrated_after_validated["atom_types"]
                ),
            }
            autogrid_after, autogrid_after_error = _detect_autogrid_snapshot()
            if autogrid_after_error or autogrid_after is None:
                raise RuntimeError("生成后 AutoGrid4 无法完成完整性复核。")
            project_after, project_after_error = _load_project_model(
                str(project_root)
            )
            if project_after_error or project_after is None:
                raise RuntimeError("生成后无法重新读取项目。")
            box_after, box_after_error = _box_snapshot(project_after)
            if box_after_error:
                raise RuntimeError("生成后 Box 无法重新校验。")
            ligand_after = get_status(str(project_root))
            source_unchanged = (
                _snapshots_match(receptor_before, receptor_after_raw)
                and hydrated_before == hydrated_after
                and _pointer_from_project(project_after)
                == (ligand_manifest_path, ligand_manifest_sha256)
                and ligand_after.get("preparation_ready") is True
                and ligand_after.get("valid") is True
                and _project_receptor_mode(project_after) == "rigid"
                and box_after == box_before
            )
            tool_unchanged = _snapshots_match(
                autogrid_before,
                autogrid_after,
            ) and all(
                str(autogrid_before.get(key) or "")
                == str(autogrid_after.get(key) or "")
                for key in ("version", "source")
            )
            manifest["integrity"] = {
                "sources_unchanged": source_unchanged,
                "autogrid_unchanged": tool_unchanged,
                "receptor_after": receptor_after_raw,
                "hydrated_ligand_after": hydrated_after,
                "autogrid_after": autogrid_after,
            }
            if not source_unchanged:
                return _write_maps_failure_manifest(
                    project_root,
                    set_dir,
                    manifest,
                    code="HYDRATED_MAPS_SOURCE_CHANGED",
                    message="生成期间受体、Box 或水合配体发生变化，maps 未激活。",
                    raw_error=json.dumps(
                        manifest["integrity"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    suggestion="请确认当前输入后重新生成 maps。",
                )
            if not tool_unchanged:
                return _write_maps_failure_manifest(
                    project_root,
                    set_dir,
                    manifest,
                    code="HYDRATED_AUTOGRID_CHANGED",
                    message="生成期间 AutoGrid4 发生变化，maps 未激活。",
                    raw_error=json.dumps(
                        manifest["integrity"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    suggestion="请重新检测 AutoGrid4 后生成 maps。",
                )

            manifest["status"] = "ready"
            manifest["finished_at"] = _now_iso()
            manifest["error"] = None
            manifest_path = set_dir / HYDRATED_MAPS_MANIFEST_NAME
            atomic_write_json(manifest_path, manifest)
            manifest_sha256 = _sha256_bytes(manifest_path.read_bytes())
            manifest_relative = _relative_path(manifest_path, project_root)

            with _project_lock(project_root):
                manifest_check = _stable_project_artifact_snapshot(
                    project_root,
                    manifest_path,
                    label="待激活水合 maps manifest",
                    maximum_bytes=MAX_MANIFEST_BYTES,
                )
                if manifest_check["sha256"] != manifest_sha256:
                    raise RuntimeError("激活前 maps manifest 完整性校验失败。")
                current_data, _, _ = _read_and_migrate_project_unlocked(
                    project_root,
                    persist_migration=True,
                )
                current_project = _project_from_dict(current_data, project_root)
                if _project_receptor_mode(current_project) != "rigid":
                    raise RuntimeError("激活前受体模式已变化。")
                current_box, current_box_error = _box_snapshot(current_project)
                if current_box_error or current_box != box_before:
                    raise RuntimeError("激活前 Box 已变化。")
                if str(current_project.receptor.file or "") != receptor_relative:
                    raise RuntimeError("激活前刚性受体路径已变化。")
                _, current_receptor = _stable_regular_project_file(
                    project_root,
                    receptor_relative,
                    label="刚性受体",
                    maximum_bytes=MAX_RECEPTOR_BYTES,
                )
                if not _snapshots_match(receptor_before, current_receptor):
                    raise RuntimeError("激活前刚性受体内容已变化。")
                if _pointer_from_project(current_project) != (
                    ligand_manifest_path,
                    ligand_manifest_sha256,
                ):
                    raise RuntimeError("激活前水合配体 manifest 指针已变化。")
                ligand_manifest_bytes, ligand_manifest_check = (
                    _stable_regular_project_file(
                        project_root,
                        ligand_manifest_path,
                        label="水合配体 manifest",
                        maximum_bytes=MAX_MANIFEST_BYTES,
                    )
                )
                if ligand_manifest_check["sha256"] != ligand_manifest_sha256:
                    raise RuntimeError("激活前水合配体 manifest 已变化。")
                current_ligand_manifest = json.loads(
                    ligand_manifest_bytes.decode("utf-8")
                )
                current_source = (
                    current_ligand_manifest.get("source")
                    if isinstance(current_ligand_manifest.get("source"), Mapping)
                    else {}
                )
                if str(current_source.get("path") or "") != str(
                    current_project.ligand.raw_file or ""
                ):
                    raise RuntimeError("激活前 raw 配体路径已变化。")
                _, current_raw = _stable_regular_project_file(
                    project_root,
                    current_project.ligand.raw_file,
                    label="raw 配体",
                    maximum_bytes=MAX_RAW_LIGAND_BYTES,
                )
                if (
                    current_raw.get("sha256") != current_source.get("sha256")
                    or current_raw.get("size_bytes")
                    != current_source.get("size_bytes")
                ):
                    raise RuntimeError("激活前 raw 配体内容已变化。")
                current_outputs = (
                    current_ligand_manifest.get("outputs")
                    if isinstance(current_ligand_manifest.get("outputs"), Mapping)
                    else {}
                )
                current_hydrated_record = (
                    current_outputs.get("hydrated_pdbqt")
                    if isinstance(current_outputs.get("hydrated_pdbqt"), Mapping)
                    else {}
                )
                current_hydrated = _validate_hydrated_pdbqt(
                    _safe_project_path(
                        project_root,
                        Path(str(current_hydrated_record.get("path") or "")),
                        allow_missing=False,
                    ),
                    project_root,
                )
                if (
                    current_hydrated.get("sha256")
                    != hydrated_before.get("sha256")
                    or current_hydrated.get("water_count")
                    != hydrated_before.get("water_count")
                ):
                    raise RuntimeError("激活前水合配体输出已变化。")
                current_tool, current_tool_error = _detect_autogrid_snapshot()
                if (
                    current_tool_error
                    or current_tool is None
                    or not _snapshots_match(autogrid_before, current_tool)
                    or any(
                        str(autogrid_before.get(key) or "")
                        != str(current_tool.get(key) or "")
                        for key in ("version", "source")
                    )
                ):
                    raise RuntimeError("激活前 AutoGrid4 已变化。")

                base_path, _, base_issue = _record_integrity_issue(
                    project_root,
                    manifest["base_maps_manifest"],
                    label="基础 maps manifest",
                    expected_parent=set_dir,
                    maximum_bytes=MAX_MANIFEST_BYTES,
                )
                if base_issue or base_path is None:
                    raise RuntimeError(
                        base_issue or "激活前基础 maps manifest 不可用。"
                    )
                for item in manifest["maps"]["files"]:
                    path, actual, issue = _record_integrity_issue(
                        project_root,
                        item,
                        label=f"map 文件 {item['name']}",
                        expected_parent=set_dir,
                    )
                    if issue or path is None or actual is None:
                        raise RuntimeError(
                            issue or f"激活前 map 文件 {item['name']} 不可用。"
                        )
                    if str(item["name"]).endswith(".map"):
                        parsed = hydrated_maps.parse_autogrid_map(path)
                        if (
                            parsed.sha256 != actual["sha256"]
                            or not _same_geometry(
                                parsed.geometry.to_dict(),
                                manifest["maps"]["geometry"],
                            )
                        ):
                            raise RuntimeError(
                                f"激活前 map 文件 {item['name']} 的几何无效。"
                            )
                for item in manifest["audit_files"]:
                    _, _, issue = _record_integrity_issue(
                        project_root,
                        item,
                        label=f"审计文件 {item['name']}",
                        maximum_bytes=MAX_RECEPTOR_BYTES,
                        allow_empty=True,
                    )
                    if issue:
                        raise RuntimeError(issue)

                state = current_project.preserved_data.get(PROJECT_STATE_KEY)
                hydrated_state = (
                    copy.deepcopy(state) if isinstance(state, Mapping) else {}
                )
                hydrated_state["active_maps_manifest"] = {
                    "path": manifest_relative,
                    "sha256": manifest_sha256,
                }
                hydrated_state["updated_at"] = _now_iso()
                current_project.preserved_data[PROJECT_STATE_KEY] = hydrated_state
                current_project.updated_at = _now_iso()
                current_project.revision += 1
                project_payload = current_project.to_dict()
                _write_project_json_unlocked(project_root, current_project)

            return {
                "ok": True,
                "protocol_id": PROTOCOL_ID,
                "project_dir": str(project_root),
                "project": project_payload,
                "map_set_id": map_set_id,
                "manifest_file": manifest_relative,
                "manifest_sha256": manifest_sha256,
                "manifest": manifest,
                "active_maps_manifest": {
                    "path": manifest_relative,
                    "sha256": manifest_sha256,
                },
                "maps_prefix": manifest["maps"]["prefix"],
                "map_files": manifest["maps"]["files"],
                "water_map": manifest["maps"]["water_map"],
                "maps_ready": True,
                "message": "水合 AD4 maps 已生成、校验并保存为独立记录。",
                "error": None,
            }
        except (
            OSError,
            UnicodeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
            PreparationPathError,
            hydrated_maps.HydratedMapError,
        ) as exc:
            return _write_maps_failure_manifest(
                project_root,
                set_dir,
                manifest,
                code=(
                    exc.code
                    if isinstance(exc, hydrated_maps.HydratedMapError)
                    else "HYDRATED_MAPS_INTEGRITY_FAILED"
                ),
                message="水合 maps 生成或完整性校验未完成。",
                raw_error=str(exc),
                suggestion=f"请查看 maps/{map_set_id} 中的审计记录后重试。",
            )


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "status":
        _print_json(get_status(sys.argv[2]))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "prepare-ligand":
        result = prepare_hydrated_ligand(sys.argv[2])
        _print_json(result)
        return 0 if result.get("ok") else 1
    if sys.argv[1:2] == ["generate-maps"] and len(sys.argv) in {3, 5}:
        if len(sys.argv) == 5 and sys.argv[3] != "--options-json":
            _print_json(
                _structured_error(
                    "HYDRATED_MAPS_OPTIONS_INVALID",
                    "水合 maps 选项参数无效。",
                    suggestion="使用 --options-json 后跟 JSON 对象。",
                )
            )
            return 2
        try:
            options = json.loads(sys.argv[4]) if len(sys.argv) == 5 else None
        except json.JSONDecodeError as exc:
            _print_json(
                _structured_error(
                    "HYDRATED_MAPS_OPTIONS_JSON_INVALID",
                    "水合 maps 选项不是有效 JSON。",
                    raw_error=str(exc),
                    suggestion="请提交包含 spacing 或 grid_points 的 JSON 对象。",
                )
            )
            return 2
        result = generate_hydrated_maps(sys.argv[2], options)
        _print_json(result)
        return 0 if result.get("ok") else 1
    _print_json(
        _structured_error(
            "HYDRATED_COMMAND_INVALID",
            "水合对接命令无效。",
            suggestion=(
                "使用 status PROJECT、prepare-ligand PROJECT，"
                "或 generate-maps PROJECT [--options-json JSON]。"
            ),
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
