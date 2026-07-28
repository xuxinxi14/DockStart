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
from typing import Any

from adapters import meeko_adapter
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
)

PROTOCOL_ID = "hydrated_ad4_experimental"
PROJECT_STATE_KEY = "hydrated_docking"
PREPARATION_ROOT = Path("protocols", "hydrated", "ligand_preparations")
PREPARATION_ID_PATTERN = re.compile(r"^hydrated_ligand_(\d{3,})$")
MAX_RAW_LIGAND_BYTES = 32 * 1024 * 1024
MAX_GENERATED_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TOOL_FILE_BYTES = 1024 * 1024 * 1024


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
    for line in text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        fields = line.split()
        if not fields:
            continue
        atom_count += 1
        atom_type = fields[-1]
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


def _pointer_from_project(project: Any) -> tuple[str, str]:
    state = project.preserved_data.get(PROJECT_STATE_KEY)
    if not isinstance(state, Mapping):
        return "", ""
    pointer = state.get("active_ligand_manifest")
    if isinstance(pointer, Mapping):
        return str(pointer.get("path") or ""), str(pointer.get("sha256") or "")
    return str(pointer or ""), ""


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
        return base

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
        return base
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, PreparationPathError) as exc:
        base.update(
            {
                "status": "invalid",
                "issues": [str(exc)],
                "message": "水合配体准备记录已失效。",
            }
        )
        return base


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
    _print_json(
        _structured_error(
            "HYDRATED_COMMAND_INVALID",
            "水合配体命令无效。",
            suggestion="使用 status PROJECT 或 prepare-ligand PROJECT。",
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
