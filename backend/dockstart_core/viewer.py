"""Project-local structure file loading helpers for the DockStart viewer."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dockstart_core.project import (
    HYDRATED_PROTOCOL_ID,
    RUN_ID_PATTERN,
    _active_receptor_inputs,
    _error,
    _local_only_execution_plan_enabled,
    _project_from_dict,
    _read_run_metadata,
    load_project,
    update_box_params,
    validate_box_params,
)
from dockstart_core.pose_comparison import compare_local_only_pose_texts
from dockstart_core.screening import _screening_import_revision, get_screening_archive
from dockstart_core.viewer_models import DockingPoseSummary, ViewerStructureResult

MAX_VIEWER_FILE_BYTES = 20 * 1024 * 1024

VIEWER_FILE_KINDS = {
    "receptor_raw": "受体原始文件",
    "ligand_raw": "配体原始文件",
    "receptor_prepared": "准备后的受体 PDBQT",
    "ligand_prepared": "准备后的配体 PDBQT",
    "docking_output": "Vina 输出 PDBQT",
}

INTERNAL_VIEWER_FILE_KINDS = {
    "receptor_run": "实际运行用刚性受体 PDBQT",
    "receptor_flex": "实际运行用柔性侧链 PDBQT",
}

TEXT_STRUCTURE_EXTENSIONS = {
    ".pdb": "pdb",
    ".pdbqt": "pdbqt",
    ".cif": "cif",
    ".sdf": "sdf",
    ".mol": "mol",
    ".mol2": "mol2",
}

MODEL_PATTERN = re.compile(r"^\s*MODEL\s+(\d+)?\s*$", re.IGNORECASE)
ENDMDL_PATTERN = re.compile(r"^\s*ENDMDL\s*$", re.IGNORECASE)
SCREENING_ITEM_ID_PATTERN = re.compile(r"^ligand_\d{4,}$")
SCREENING_CANDIDATE_ID_PATTERN = re.compile(
    r"^ligand_[0-9a-f]{12}_\d{4}(?:_\d{2})?$"
)
PDBQT_VIEWER_RECORDS = {
    "ATOM",
    "HETATM",
    "MODEL",
    "ENDMDL",
    "TER",
    "CONECT",
    "REMARK",
}

PDBQT_ELEMENT_BY_TYPE = {
    "A": "C",
    "C": "C",
    "N": "N",
    "NA": "N",
    "O": "O",
    "OA": "O",
    "S": "S",
    "SA": "S",
    "P": "P",
    "H": "H",
    "HD": "H",
    "F": "F",
    "CL": "Cl",
    "BR": "Br",
    "I": "I",
    "W": "O",
}


def _viewer_error(
    code: str,
    message: str,
    file_kind: str = "",
    relative_path: str = "",
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    payload = _error(code, message, raw_error=raw_error, suggestion=suggestion)
    payload.update(
        {
            "file_kind": file_kind,
            "relative_path": relative_path,
            "message": message,
            "warnings": [],
        }
    )
    return payload


def _load_project_model(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    return _project_from_dict(loaded["project"], Path(project_dir).expanduser()), None


def _project_root(project_dir: str | Path) -> Path:
    return Path(project_dir).expanduser().resolve()


def _detect_format(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    return TEXT_STRUCTURE_EXTENSIONS.get(suffix, "unknown")


def _normalize_pdbqt_for_viewer(content: str) -> str:
    """Build a PDB-compatible display copy without changing stored PDBQT.

    3Dmol routes PDBQT through its PDB parser. That parser treats every record
    beginning with ``END`` as the end of the model, so Meeko's ``ENDROOT`` and
    ``ENDBRANCH`` records truncate flexible ligands. Keep only records the PDB
    parser understands; all atom coordinates and identities remain unchanged.
    """

    lines: list[str] = []
    for line in content.splitlines():
        record = line[:6].strip().upper()
        if record in PDBQT_VIEWER_RECORDS:
            if (
                record in {"ATOM", "HETATM"}
                and line.split()
                and line.split()[-1].upper() == "W"
            ):
                display_line = line.ljust(80)
                display_line = (
                    display_line[:12]
                    + " O  "
                    + display_line[16:76]
                    + " O"
                    + display_line[78:]
                )
                line = display_line
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _is_hydrated_run_metadata(metadata: dict[str, Any]) -> bool:
    protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    return (
        str(metadata.get("protocol_id") or "") == HYDRATED_PROTOCOL_ID
        or str(protocol.get("protocol_id") or "") == HYDRATED_PROTOCOL_ID
    )


def _viewer_content(content: str, structure_format: str) -> tuple[str, str, list[str]]:
    if structure_format != "pdbqt":
        return content, structure_format, []
    return (
        _normalize_pdbqt_for_viewer(content),
        "pdb",
        [
            "PDBQT 拓扑记录已从本次 3D 显示副本中移除，以避免查看器截断柔性配体；项目中的原始 PDBQT 文件未被修改。"
        ],
    )


def _latest_docking_output(project: Any) -> str:
    for run in reversed(project.runs or []):
        if not isinstance(run, dict):
            continue
        output_file = str(run.get("output_file") or "")
        if output_file:
            return output_file
        run_id = str(run.get("run_id") or "")
        if RUN_ID_PATTERN.match(run_id):
            return Path("runs", run_id, "out.pdbqt").as_posix()
    return ""


def _relative_path_for_kind(project: Any, file_kind: str) -> str | None:
    if file_kind == "receptor_raw":
        return project.receptor.raw_file
    if file_kind == "ligand_raw":
        return project.ligand.raw_file
    if file_kind == "receptor_prepared":
        return project.receptor.file
    if file_kind == "ligand_prepared":
        return project.ligand.file
    if file_kind == "docking_output":
        return _latest_docking_output(project)
    return None


def _prepared_ligand_display_source(
    project_dir: str,
    project: Any,
) -> tuple[str, dict[str, Any], str] | None:
    """Reuse the preparation input for display when it is the matching SDF/MOL.

    PDBQT does not retain complete bond-order information. Rendering its atom
    records through a PDB parser can therefore look different from the exact
    SDF/MOL candidate the user selected. The original file is used only as a
    read-only display source when the finished preparation record proves that
    it produced the current PDBQT.
    """

    raw_relative = str(project.ligand.raw_file or "").strip()
    prepared_relative = str(project.ligand.file or "").strip()
    preparation = project.preparation.ligand
    if (
        preparation.status != "finished"
        or str(preparation.input_file or "").strip() != raw_relative
        or str(preparation.output_file or "").strip() != prepared_relative
        or _detect_format(raw_relative) not in {"sdf", "mol", "mol2"}
    ):
        return None

    validation = validate_viewer_file(project_dir, raw_relative)
    if not validation.get("ok"):
        return None
    try:
        content = Path(validation["absolute_path"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    return raw_relative, validation, content


def _pdbqt_atoms(content: str) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    for line in content.splitlines():
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        try:
            serial = int(line[6:11].strip())
            x = float(line[30:38].strip())
            y = float(line[38:46].strip())
            z = float(line[46:54].strip())
        except (TypeError, ValueError):
            continue
        atom_type = line.split()[-1].upper() if line.split() else ""
        element = PDBQT_ELEMENT_BY_TYPE.get(atom_type)
        if not element:
            atom_name = re.sub(r"[^A-Za-z]", "", line[12:16]).strip()
            element = atom_name[:1].upper() if atom_name else ""
        atoms.append(
            {
                "serial": serial,
                "element": element,
                "x": x,
                "y": y,
                "z": z,
            }
        )
    return atoms


def _parse_v2000_display_source(content: str) -> dict[str, Any] | None:
    first_record = content.split("$$$$", 1)[0]
    lines = first_record.splitlines()
    if len(lines) < 4 or "V2000" not in lines[3].upper():
        return None
    try:
        atom_count = int(lines[3][0:3].strip())
        bond_count = int(lines[3][3:6].strip())
    except (TypeError, ValueError):
        return None
    if atom_count <= 0 or len(lines) < 4 + atom_count + bond_count:
        return None

    atoms: list[dict[str, Any]] = []
    for index, line in enumerate(lines[4 : 4 + atom_count], start=1):
        try:
            atoms.append(
                {
                    "index": index,
                    "x": float(line[0:10].strip()),
                    "y": float(line[10:20].strip()),
                    "z": float(line[20:30].strip()),
                    "element": line[31:34].strip(),
                    "suffix": line[30:],
                }
            )
        except (TypeError, ValueError):
            return None

    bonds: list[dict[str, Any]] = []
    for line in lines[4 + atom_count : 4 + atom_count + bond_count]:
        try:
            bonds.append(
                {
                    "start": int(line[0:3].strip()),
                    "end": int(line[3:6].strip()),
                    "suffix": line[6:],
                }
            )
        except (TypeError, ValueError):
            return None
    return {"atoms": atoms, "bonds": bonds}


def _match_v2000_atoms_to_pdbqt(
    source_atoms: list[dict[str, Any]],
    prepared_atoms: list[dict[str, Any]],
    tolerance: float = 0.03,
) -> dict[int, int] | None:
    heavy_source = [atom for atom in source_atoms if str(atom["element"]).upper() != "H"]
    heavy_prepared = [atom for atom in prepared_atoms if str(atom["element"]).upper() != "H"]
    if not heavy_source or len(heavy_source) != len(heavy_prepared):
        return None

    available = set(range(len(heavy_prepared)))
    mapping: dict[int, int] = {}
    for source_atom in heavy_source:
        candidates: list[tuple[float, int]] = []
        source_element = str(source_atom["element"]).upper()
        for prepared_index in available:
            prepared_atom = heavy_prepared[prepared_index]
            if str(prepared_atom["element"]).upper() != source_element:
                continue
            distance_squared = sum(
                (float(source_atom[axis]) - float(prepared_atom[axis])) ** 2
                for axis in ("x", "y", "z")
            )
            candidates.append((distance_squared, prepared_index))
        if not candidates:
            return None
        distance_squared, prepared_index = min(candidates)
        if distance_squared > tolerance**2:
            return None
        available.remove(prepared_index)
        mapping[int(source_atom["index"])] = int(heavy_prepared[prepared_index]["serial"])
    return mapping


def _v2000_with_pose_coordinates(
    source: dict[str, Any],
    source_to_pdbqt: dict[int, int],
    pose_atoms: list[dict[str, Any]],
    mode: int,
) -> str | None:
    pose_by_serial = {int(atom["serial"]): atom for atom in pose_atoms}
    heavy_atoms = [
        atom
        for atom in source["atoms"]
        if str(atom["element"]).upper() != "H" and int(atom["index"]) in source_to_pdbqt
    ]
    if not heavy_atoms or len(heavy_atoms) != len(source_to_pdbqt):
        return None

    source_to_display = {
        int(atom["index"]): display_index
        for display_index, atom in enumerate(heavy_atoms, start=1)
    }
    bonds = [
        bond
        for bond in source["bonds"]
        if int(bond["start"]) in source_to_display and int(bond["end"]) in source_to_display
    ]
    output = [
        f"DockStart docking pose Mode {mode}",
        "  DockStart          3D",
        "",
        f"{len(heavy_atoms):>3}{len(bonds):>3}  0  0  0  0            999 V2000",
    ]
    for source_atom in heavy_atoms:
        serial = source_to_pdbqt[int(source_atom["index"])]
        pose_atom = pose_by_serial.get(serial)
        if pose_atom is None or str(pose_atom["element"]).upper() != str(source_atom["element"]).upper():
            return None
        output.append(
            f"{float(pose_atom['x']):10.4f}"
            f"{float(pose_atom['y']):10.4f}"
            f"{float(pose_atom['z']):10.4f}"
            f"{source_atom['suffix']}"
        )
    for bond in bonds:
        output.append(
            f"{source_to_display[int(bond['start'])]:>3}"
            f"{source_to_display[int(bond['end'])]:>3}"
            f"{bond['suffix']}"
        )
    output.extend(["M  END", "$$$$"])
    return "\n".join(output) + "\n"


def _docking_pose_display_content(
    project_dir: str,
    project: Any,
    run_id: str,
    pose_content: str,
    mode: int,
) -> tuple[str, str, list[str]] | None:
    display_source = _prepared_ligand_display_source(project_dir, project)
    if display_source is None:
        return None
    _, source_validation, source_content = display_source
    if source_validation["format"] not in {"sdf", "mol"}:
        return None
    source = _parse_v2000_display_source(source_content)
    if source is None:
        return None

    project_root = _project_root(project_dir)
    run_input = project_root / "runs" / run_id / "inputs" / "ligand.pdbqt"
    prepared_path = project_root / str(project.ligand.file or "")
    mapping_source_path = run_input if run_input.is_file() else prepared_path
    try:
        prepared_content = mapping_source_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    source_to_pdbqt = _match_v2000_atoms_to_pdbqt(source["atoms"], _pdbqt_atoms(prepared_content))
    if source_to_pdbqt is None:
        return None
    reconstructed = _v2000_with_pose_coordinates(
        source,
        source_to_pdbqt,
        _pdbqt_atoms(pose_content),
        mode,
    )
    if reconstructed is None:
        return None
    return (
        reconstructed,
        "sdf",
        [
            "结果构象沿用生成该配体 PDBQT 的原始 SDF/MOL 键拓扑，并使用当前 Mode 的 Vina 坐标；"
            "显示副本不修改 out.pdbqt。"
        ],
    )


def _resolve_project_file(
    project_root: Path,
    relative_path: str,
    file_kind: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    value = str(relative_path or "").strip()
    if not value:
        return None, _viewer_error(
            "VIEWER_FILE_NOT_SET",
            f"{VIEWER_FILE_KINDS.get(file_kind, '结构文件')} 尚未记录在 project.json 中。",
            file_kind=file_kind,
            suggestion="请先完成对应的下载、准备或运行步骤，再打开 3D 查看。",
        )

    path = Path(value)
    if path.is_absolute():
        return None, _viewer_error(
            "VIEWER_FILE_PATH_ABSOLUTE",
            "Viewer 只允许读取项目目录内的相对路径文件。",
            file_kind=file_kind,
            relative_path=value,
            suggestion="请使用 DockStart 项目内的 raw、prepared 或 runs 文件。",
        )

    resolved = (project_root / path).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return None, _viewer_error(
            "VIEWER_FILE_OUTSIDE_PROJECT",
            "结构文件路径指向项目目录外，已拒绝读取。",
            file_kind=file_kind,
            relative_path=value,
            raw_error=str(resolved),
            suggestion="请确认 project.json 中记录的是项目目录内的相对路径。",
        )

    return resolved, None


def _status_for_relative_path(project_root: Path, file_kind: str, relative_path: str) -> dict[str, Any]:
    file_path, error = _resolve_project_file(project_root, relative_path, file_kind)
    structure_format = _detect_format(relative_path)
    if error:
        return ViewerStructureResult(
            ok=False,
            file_kind=file_kind,
            relative_path=relative_path,
            format=structure_format,
            message=error["message"],
            error=error.get("error"),
        ).to_dict()

    assert file_path is not None
    exists = file_path.exists()
    is_file = file_path.is_file()
    size = file_path.stat().st_size if exists and is_file else 0
    if exists and is_file and size > 0:
        ok = True
        message = "结构文件可读取。"
        warnings: list[str] = []
    elif exists and is_file:
        ok = False
        message = "结构文件为空，无法用于 3D 查看。"
        warnings = []
    elif exists:
        ok = False
        message = "结构路径不是文件，无法用于 3D 查看。"
        warnings = []
    else:
        ok = False
        message = "结构文件不存在。"
        warnings = []

    if structure_format == "unknown" and relative_path:
        warnings.append("当前文件扩展名不是常见结构格式，viewer 可能无法显示。")

    return ViewerStructureResult(
        ok=ok,
        file_kind=file_kind,
        relative_path=relative_path,
        absolute_path=str(file_path),
        exists=exists,
        format=structure_format,
        size_bytes=size,
        message=message,
        warnings=warnings,
        error=None if ok else {"code": "VIEWER_FILE_NOT_READY", "message": message},
    ).to_dict()


def validate_viewer_file(project_dir: str, relative_path: str) -> dict[str, Any]:
    project_root = _project_root(project_dir)
    file_path, error = _resolve_project_file(project_root, relative_path, "custom")
    if error:
        return error

    assert file_path is not None
    if not file_path.exists():
        return _viewer_error(
            "VIEWER_FILE_NOT_FOUND",
            "没有找到结构文件，无法加载到 3D viewer。",
            relative_path=relative_path,
            raw_error=str(file_path),
            suggestion="请确认对应步骤已经生成或下载该文件。",
        )
    if not file_path.is_file():
        return _viewer_error(
            "VIEWER_PATH_NOT_FILE",
            "结构路径不是文件，无法加载到 3D viewer。",
            relative_path=relative_path,
            raw_error=str(file_path),
        )

    size = file_path.stat().st_size
    if size <= 0:
        return _viewer_error(
            "VIEWER_FILE_EMPTY",
            "结构文件为空，无法加载到 3D viewer。",
            relative_path=relative_path,
            raw_error=str(file_path),
        )
    if size > MAX_VIEWER_FILE_BYTES:
        return _viewer_error(
            "VIEWER_FILE_TOO_LARGE",
            "结构文件超过 20 MB 的预览上限，本次没有读取内容。",
            relative_path=relative_path,
            raw_error=f"{size} bytes",
            suggestion="请使用更小的结构文件，或后续版本的分块/压缩查看能力。",
        )

    return {
        "ok": True,
        "relative_path": relative_path,
        "absolute_path": str(file_path),
        "format": _detect_format(relative_path),
        "size_bytes": size,
        "message": "结构文件通过 viewer 读取前检查。",
        "warnings": [],
        "error": None,
    }


def get_viewer_file_status(project_dir: str) -> dict[str, Any]:
    project, error = _load_project_model(project_dir)
    if error:
        return error

    project_root = _project_root(project_dir)
    files = {}
    for file_kind in VIEWER_FILE_KINDS:
        relative_path = _relative_path_for_kind(project, file_kind) or ""
        files[file_kind] = _status_for_relative_path(project_root, file_kind, relative_path)

    docking_outputs = []
    for run in project.runs or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "")
        if not RUN_ID_PATTERN.match(run_id):
            continue
        output_file = str(run.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())
        status = _status_for_relative_path(project_root, "docking_output", output_file)
        status["run_id"] = run_id
        status["run_status"] = run.get("status", "")
        docking_outputs.append(status)

    return {
        "ok": True,
        "project_dir": str(project_root),
        "files": files,
        "docking_outputs": docking_outputs,
        "message": "Viewer 文件状态已读取。",
        "warnings": [],
        "error": None,
    }


def _build_box_payload(box: dict[str, Any]) -> dict[str, Any]:
    center_x = float(box["center_x"])
    center_y = float(box["center_y"])
    center_z = float(box["center_z"])
    size_x = float(box["size_x"])
    size_y = float(box["size_y"])
    size_z = float(box["size_z"])
    min_x = center_x - size_x / 2
    max_x = center_x + size_x / 2
    min_y = center_y - size_y / 2
    max_y = center_y + size_y / 2
    min_z = center_z - size_z / 2
    max_z = center_z + size_z / 2
    corners = [
        {"x": x, "y": y, "z": z}
        for x in (min_x, max_x)
        for y in (min_y, max_y)
        for z in (min_z, max_z)
    ]
    return {
        "center_x": center_x,
        "center_y": center_y,
        "center_z": center_z,
        "size_x": size_x,
        "size_y": size_y,
        "size_z": size_z,
        "unit": "angstrom",
        "min": {"x": min_x, "y": min_y, "z": min_z},
        "max": {"x": max_x, "y": max_y, "z": max_z},
        "corners": corners,
        "viewer_box_payload": {
            "center": {"x": center_x, "y": center_y, "z": center_z},
            "dimensions": {"w": size_x, "h": size_y, "d": size_z},
            "color": "orange",
            "alpha": 0.16,
            "wireframe": True,
        },
    }


def get_box_visualization(project_dir: str) -> dict[str, Any]:
    project, error = _load_project_model(project_dir)
    if error:
        return error

    box = asdict(project.box)
    validation = validate_box_params(box)
    if not validation.get("ok"):
        validation["project_dir"] = str(_project_root(project_dir))
        return validation

    payload = _build_box_payload(validation.get("box", box))
    return {
        "ok": True,
        "project_dir": str(_project_root(project_dir)),
        "box": validation.get("box", box),
        "visualization": payload,
        "warnings": validation.get("warnings", []),
        "message": "Box 可视化数据已生成。Box 只表示 Vina 搜索空间，不代表自动识别结合口袋。",
        "error": None,
    }


def update_box_from_visualization(project_dir: str, box_params: dict[str, Any]) -> dict[str, Any]:
    updated = update_box_params(project_dir, box_params)
    if not updated.get("ok"):
        return updated

    visualization = get_box_visualization(project_dir)
    if visualization.get("ok"):
        visualization["project"] = updated.get("project")
        visualization["message"] = "Box 参数已保存，并已更新 viewer 可视化数据。"
    return visualization


def load_structure_for_viewer(project_dir: str, file_kind: str) -> dict[str, Any]:
    if file_kind not in VIEWER_FILE_KINDS and file_kind not in INTERNAL_VIEWER_FILE_KINDS:
        return _viewer_error(
            "VIEWER_FILE_KIND_INVALID",
            "结构文件类型无效。",
            file_kind=file_kind,
            raw_error=str(file_kind),
            suggestion="请使用 receptor_raw、ligand_raw、receptor_prepared、receptor_run、receptor_flex、ligand_prepared 或 docking_output。",
        )

    project, error = _load_project_model(project_dir)
    if error:
        return error

    if file_kind in {"receptor_run", "receptor_flex"}:
        receptor_inputs = _active_receptor_inputs(
            _project_root(project_dir),
            project,
        )
        if not receptor_inputs.get("ok"):
            return receptor_inputs
        relative_path = str(
            receptor_inputs.get(
                "receptor_file" if file_kind == "receptor_run" else "flex_file"
            )
            or ""
        )
    else:
        relative_path = _relative_path_for_kind(project, file_kind) or ""
    validation = validate_viewer_file(project_dir, relative_path)
    if not validation.get("ok"):
        validation["file_kind"] = file_kind
        return validation

    absolute_path = Path(validation["absolute_path"])
    try:
        content = absolute_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _viewer_error(
            "VIEWER_FILE_READ_ERROR",
            "读取结构文件时发生错误。",
            file_kind=file_kind,
            relative_path=relative_path,
            raw_error=str(exc),
            suggestion="请确认该文件是可读取的文本结构文件。",
        )

    display_warning: list[str] = []
    if file_kind == "ligand_prepared":
        display_source = _prepared_ligand_display_source(project_dir, project)
        if display_source is not None:
            relative_path, validation, content = display_source
            absolute_path = Path(validation["absolute_path"])
            display_warning.append(
                "3D 显示使用生成当前 PDBQT 的原始 SDF/MOL，以保留一致的键级和视觉拓扑；对接计算仍使用准备后的 PDBQT。"
            )

    viewer_content, viewer_format, viewer_warnings = _viewer_content(content, validation["format"])
    return ViewerStructureResult(
        ok=True,
        file_kind=file_kind,
        relative_path=relative_path,
        absolute_path=str(absolute_path),
        exists=True,
        format=viewer_format,
        content=viewer_content,
        size_bytes=validation["size_bytes"],
        message="结构文件已读取，仅用于 3D 几何查看，不做科学解释。",
        warnings=[*validation.get("warnings", []), *display_warning, *viewer_warnings],
        error=None,
    ).to_dict()


def _run_output_relative_path(project_dir: str, run_id: str) -> tuple[str | None, dict[str, Any] | None]:
    if not RUN_ID_PATTERN.match(run_id):
        return None, _viewer_error(
            "VIEWER_RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            raw_error=str(run_id),
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    metadata_path = _project_root(project_dir) / "runs" / run_id / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict):
                pose_file = str(metadata.get("pose_file") or metadata.get("output_file") or "")
                if pose_file:
                    return pose_file, None
        except Exception:
            # Metadata is helpful but not required for viewer fallback.
            pass

    return Path("runs", run_id, "out.pdbqt").as_posix(), None


def _run_evaluation_pose_relative_path(
    project_dir: str,
    run_id: str,
    pose_kind: str | None,
) -> tuple[str | None, str, dict[str, Any] | None]:
    normalized_kind = str(pose_kind or "").strip().lower()
    if not normalized_kind:
        relative_path, error = _run_output_relative_path(project_dir, run_id)
        return relative_path, "", error
    if normalized_kind not in {"input", "optimized"}:
        return None, "", _viewer_error(
            "VIEWER_POSE_KIND_INVALID",
            "评价姿势类型只能是 input 或 optimized。",
            raw_error=normalized_kind,
        )
    if not RUN_ID_PATTERN.match(run_id):
        return None, "", _viewer_error(
            "VIEWER_RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            raw_error=str(run_id),
        )

    metadata_path = _project_root(project_dir) / "runs" / run_id / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - return structured viewer errors.
        return None, "", _viewer_error(
            "VIEWER_RUN_METADATA_INVALID",
            "无法读取本次 run 的 metadata.json，不能选择评价姿势。",
            raw_error=str(exc),
        )
    if not isinstance(metadata, dict):
        return None, "", _viewer_error(
            "VIEWER_RUN_METADATA_INVALID",
            "本次 run 的 metadata.json 结构无效。",
        )
    run_mode = str(metadata.get("run_mode") or "dock").strip().lower()
    if normalized_kind == "input":
        if run_mode not in {"score_only", "local_only"}:
            return None, "", _viewer_error(
                "VIEWER_INPUT_POSE_NOT_APPLICABLE",
                "全局对接结果不使用评价模式的输入姿势视图。",
            )
        return (
            Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix(),
            "输入姿势",
            None,
        )
    if run_mode != "local_only":
        return None, "", _viewer_error(
            "VIEWER_OPTIMIZED_POSE_NOT_APPLICABLE",
            "只有 local_only run 才有局部优化后姿势。",
        )
    expected = Path("runs", run_id, "optimized.pdbqt").as_posix()
    recorded = str(metadata.get("output_file") or metadata.get("pose_file") or "")
    if recorded != expected:
        return None, "", _viewer_error(
            "VIEWER_OPTIMIZED_POSE_PATH_INVALID",
            "metadata 中的局部优化姿势路径与固定 run 路径不一致。",
            raw_error=f"recorded={recorded!r}; expected={expected!r}",
        )
    return expected, "优化后姿势", None


def _read_fixed_local_only_viewer_file(
    project_root: Path,
    relative_path: str,
    *,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read one fixed run PDBQT once and bind validation to those exact bytes."""

    lexical_path = project_root / relative_path
    if lexical_path.is_symlink():
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_PATH_UNSAFE",
            f"{label}不能是符号链接。",
            relative_path=relative_path,
            raw_error=str(lexical_path),
            suggestion="请保留 run 目录中的原始快照文件，不要用链接替换。",
        )
    try:
        resolved_path = lexical_path.resolve(strict=True)
        resolved_path.relative_to(project_root)
    except FileNotFoundError:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_FILE_NOT_FOUND",
            f"没有找到{label}。",
            relative_path=relative_path,
            raw_error=str(lexical_path),
            suggestion="请保留该 local_only run 的完整 inputs 与 optimized.pdbqt。",
        )
    except (OSError, ValueError) as exc:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_PATH_UNSAFE",
            f"{label}路径不安全，已拒绝读取。",
            relative_path=relative_path,
            raw_error=str(exc),
        )
    if resolved_path != lexical_path.absolute() or not resolved_path.is_file():
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_PATH_UNSAFE",
            f"{label}不是 run 目录中的普通固定文件。",
            relative_path=relative_path,
            raw_error=f"lexical={lexical_path}; resolved={resolved_path}",
        )
    try:
        content_bytes = resolved_path.read_bytes()
    except OSError as exc:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_READ_ERROR",
            f"无法读取{label}。",
            relative_path=relative_path,
            raw_error=str(exc),
        )
    size_bytes = len(content_bytes)
    if size_bytes <= 0:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_FILE_EMPTY",
            f"{label}为空，无法叠合显示。",
            relative_path=relative_path,
            raw_error=str(resolved_path),
        )
    if size_bytes > MAX_VIEWER_FILE_BYTES:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_FILE_TOO_LARGE",
            f"{label}超过 20 MB 的 3D 预览上限。",
            relative_path=relative_path,
            raw_error=f"{size_bytes} bytes",
        )
    try:
        content = content_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return None, _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_ENCODING_INVALID",
            f"{label}不是有效的 UTF-8 PDBQT 文件。",
            relative_path=relative_path,
            raw_error=str(exc),
        )
    return (
        {
            "relative_path": relative_path,
            "absolute_path": str(resolved_path),
            "content": content,
            "size_bytes": size_bytes,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
        },
        None,
    )


def _local_only_ligand_display_text(content: str) -> str:
    """Remove flexible-receptor blocks from the ligand display copy."""

    lines: list[str] = []
    inside_flexible_receptor = False
    for line in content.splitlines():
        upper = line.strip().upper()
        if upper.startswith("BEGIN_RES"):
            inside_flexible_receptor = True
            continue
        if upper.startswith("END_RES"):
            inside_flexible_receptor = False
            continue
        if not inside_flexible_receptor:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _local_only_flexible_receptor_display_text(content: str) -> str:
    """Extract flexible-receptor blocks from a local-only PDBQT display copy."""

    lines: list[str] = []
    inside_flexible_receptor = False
    for line in content.splitlines():
        upper = line.strip().upper()
        if upper.startswith("BEGIN_RES"):
            inside_flexible_receptor = True
            continue
        if upper.startswith("END_RES"):
            inside_flexible_receptor = False
            continue
        if inside_flexible_receptor:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _local_only_bundle_structure(
    file_record: dict[str, Any],
    *,
    file_kind: str,
    pose_kind: str = "",
    pose_label: str,
    ligand_only: bool = False,
    flexible_receptor_only: bool = False,
) -> dict[str, Any]:
    if flexible_receptor_only:
        display_source = _local_only_flexible_receptor_display_text(
            str(file_record["content"])
        )
    elif ligand_only:
        display_source = _local_only_ligand_display_text(str(file_record["content"]))
    else:
        display_source = str(file_record["content"])
    viewer_content, viewer_format, viewer_warnings = _viewer_content(
        display_source,
        "pdbqt",
    )
    payload = ViewerStructureResult(
        ok=True,
        file_kind=file_kind,
        relative_path=str(file_record["relative_path"]),
        absolute_path=str(file_record["absolute_path"]),
        exists=True,
        format=viewer_format,
        content=viewer_content,
        size_bytes=len(viewer_content.encode("utf-8")),
        message=f"{pose_label}已从本次 run 的固定快照读取。",
        warnings=viewer_warnings,
        error=None,
    ).to_dict()
    if pose_kind:
        payload.update(
            {
                "mode": 1,
                "pose_kind": pose_kind,
                "pose_label": pose_label,
            }
        )
    return payload


def _local_only_pair_hash_error(
    *,
    label: str,
    expected: str,
    actual: str,
    relative_path: str,
) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_HASH_MISSING",
            f"metadata 中缺少可验证的{label} SHA256。",
            relative_path=relative_path,
            raw_error=f"recorded={expected!r}",
            suggestion="请重新准备并执行新的 local_only run。",
        )
    if expected.lower() != actual.lower():
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_HASH_MISMATCH",
            f"{label}在 run 完成后发生变化，已拒绝叠合显示。",
            relative_path=relative_path,
            raw_error=f"expected={expected}; actual={actual}",
            suggestion="请保留该 run 作为审计记录，并重新执行新的 local_only run。",
        )
    return None


def load_local_only_pose_pair_for_viewer(
    project_dir: str,
    run_id: str,
) -> dict[str, Any]:
    """Load one immutable local-only receptor/input/optimized display bundle."""

    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None
    run_mode = str(metadata.get("run_mode") or "dock").strip().lower()
    if run_mode != "local_only":
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_NOT_APPLICABLE",
            "只有 local_only run 才能显示优化前后姿势叠合。",
            raw_error=f"run_mode={run_mode}",
        )
    if str(metadata.get("status") or "") != "finished":
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_NOT_FINISHED",
            "本次 local_only run 尚未成功完成，不能显示优化前后叠合。",
            raw_error=f"status={metadata.get('status')!r}",
        )
    recorded_run_id = str(metadata.get("run_id") or run_id)
    if recorded_run_id != run_id:
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_RUN_ID_MISMATCH",
            "metadata 中的 run_id 与请求不一致。",
            raw_error=f"recorded={recorded_run_id!r}; requested={run_id!r}",
        )

    receptor_relative = Path("runs", run_id, "inputs", "receptor.pdbqt").as_posix()
    input_relative = Path("runs", run_id, "inputs", "ligand.pdbqt").as_posix()
    flex_relative = Path("runs", run_id, "inputs", "flex.pdbqt").as_posix()
    optimized_relative = Path("runs", run_id, "optimized.pdbqt").as_posix()
    output_file = str(metadata.get("output_file") or "")
    pose_file = str(metadata.get("pose_file") or "")
    recorded_pose_paths = [value for value in (output_file, pose_file) if value]
    if (
        not recorded_pose_paths
        or any(value != optimized_relative for value in recorded_pose_paths)
    ):
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_PATH_INVALID",
            "metadata 中的优化后姿势路径与固定 run 路径不一致。",
            relative_path=optimized_relative,
            raw_error=(
                f"output_file={output_file!r}; pose_file={pose_file!r}; "
                f"expected={optimized_relative!r}"
            ),
        )

    modern_plan = _local_only_execution_plan_enabled(metadata)
    plan_or_phases_present = (
        metadata.get("execution_plan") is not None
        or metadata.get("execution_phases") is not None
    )
    if plan_or_phases_present and not modern_plan:
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_EXECUTION_PLAN_INVALID",
            "local_only 的执行计划记录不完整或版本不受支持，不能降级为历史 run 读取。",
            suggestion="请保留该 run，并重新准备新的 local_only run。",
        )
    if modern_plan:
        phases = (
            metadata.get("execution_phases")
            if isinstance(metadata.get("execution_phases"), dict)
            else {}
        )
        if any(
            str((phases.get(phase_id) or {}).get("status") or "") != "finished"
            for phase_id in ("input_score", "local_optimization")
            if isinstance(phases.get(phase_id), dict)
        ) or any(
            not isinstance(phases.get(phase_id), dict)
            for phase_id in ("input_score", "local_optimization")
        ):
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_PHASES_INVALID",
                "local_only 的输入评分或局部优化阶段没有完整完成记录。",
                suggestion="请重新准备并执行新的 local_only run。",
            )
        if output_file != optimized_relative or pose_file != optimized_relative:
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_PATH_INVALID",
                "现代 local_only run 必须同时记录固定的 output_file 与 pose_file。",
                relative_path=optimized_relative,
            )

    project_root = _project_root(project_dir)
    file_records: dict[str, dict[str, Any]] = {}
    for key, relative_path, label in (
        ("receptor", receptor_relative, "受体快照"),
        ("input", input_relative, "输入配体姿势"),
        ("optimized", optimized_relative, "优化后配体姿势"),
    ):
        record, read_error = _read_fixed_local_only_viewer_file(
            project_root,
            relative_path,
            label=label,
        )
        if read_error:
            return read_error
        assert record is not None
        file_records[key] = record

    input_hashes = (
        metadata.get("input_sha256")
        if isinstance(metadata.get("input_sha256"), dict)
        else {}
    )
    snapshots = (
        metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), dict)
        else {}
    )
    snapshot_inputs = (
        snapshots.get("inputs")
        if isinstance(snapshots.get("inputs"), dict)
        else {}
    )
    artifacts = (
        metadata.get("artifacts")
        if isinstance(metadata.get("artifacts"), dict)
        else {}
    )
    out_artifact = (
        artifacts.get("out")
        if isinstance(artifacts.get("out"), dict)
        else {}
    )
    docking_protocol = (
        metadata.get("docking_protocol")
        if isinstance(metadata.get("docking_protocol"), dict)
        else {}
    )
    recorded_flexible_receptor = bool(
        input_hashes.get("flex")
        or snapshot_inputs.get("flex")
        or str(
            docking_protocol.get("receptor_mode")
            or docking_protocol.get("mode")
            or ""
        ).strip().lower()
        == "flexible"
    )
    flex_snapshot_path = project_root / flex_relative
    optimized_flex_display = _local_only_flexible_receptor_display_text(
        str(file_records["optimized"]["content"])
    )
    flexible_receptor = bool(
        recorded_flexible_receptor
        or flex_snapshot_path.exists()
        or optimized_flex_display.strip()
    )
    if flexible_receptor:
        flex_record, flex_read_error = _read_fixed_local_only_viewer_file(
            project_root,
            flex_relative,
            label="柔性受体输入快照",
        )
        if flex_read_error:
            return flex_read_error
        assert flex_record is not None
        file_records["flex"] = flex_record
        input_flex_display = _local_only_flexible_receptor_display_text(
            str(flex_record["content"])
        )
        if not any(
            line[:6].strip().upper() in {"ATOM", "HETATM"}
            for line in input_flex_display.splitlines()
        ):
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_FLEX_INPUT_INVALID",
                "柔性受体输入快照中没有可显示的 BEGIN_RES/END_RES 原子记录。",
                relative_path=flex_relative,
            )
        if not any(
            line[:6].strip().upper() in {"ATOM", "HETATM"}
            for line in optimized_flex_display.splitlines()
        ):
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_FLEX_OUTPUT_MISSING",
                "优化后姿势没有记录柔性受体残基，不能完整显示该 flexible local_only run。",
                relative_path=optimized_relative,
            )

    if modern_plan:
        snapshot_roles = [
            ("receptor", "receptor", "受体快照", receptor_relative),
            ("ligand", "input", "输入配体姿势", input_relative),
        ]
        if flexible_receptor:
            snapshot_roles.append(
                ("flex", "flex", "柔性受体输入快照", flex_relative)
            )
        for key, record_key, label, relative_path in snapshot_roles:
            input_expected = str(input_hashes.get(key) or "")
            snapshot_record = (
                snapshot_inputs.get(key)
                if isinstance(snapshot_inputs.get(key), dict)
                else {}
            )
            snapshot_expected = str(snapshot_record.get("sha256") or "")
            snapshot_relative = str(snapshot_record.get("relative_path") or "")
            if (
                input_expected.lower() != snapshot_expected.lower()
                or snapshot_relative != relative_path
            ):
                return _viewer_error(
                    "VIEWER_LOCAL_POSE_PAIR_SNAPSHOT_INVALID",
                    f"{label}的两份 metadata 快照记录不一致。",
                    relative_path=relative_path,
                    raw_error=(
                        f"input_sha256={input_expected!r}; "
                        f"snapshot_sha256={snapshot_expected!r}; "
                        f"snapshot_path={snapshot_relative!r}"
                    ),
                )
            hash_error = _local_only_pair_hash_error(
                label=label,
                expected=input_expected,
                actual=str(file_records[record_key]["sha256"]),
                relative_path=relative_path,
            )
            if hash_error:
                return hash_error

        out_expected = str(out_artifact.get("sha256") or "")
        out_relative = str(out_artifact.get("relative_path") or "")
        if (
            bool(out_artifact.get("backfilled_unverified"))
            or str(out_artifact.get("verification_status") or "").lower()
            == "unknown"
        ):
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_HASH_UNVERIFIED",
                "优化后姿势的哈希仅在恢复时观察到，不能作为执行时完整性证据。",
                relative_path=optimized_relative,
            )
        if out_relative != optimized_relative:
            return _viewer_error(
                "VIEWER_LOCAL_POSE_PAIR_SNAPSHOT_INVALID",
                "优化后姿势的 artifact 路径与固定 run 路径不一致。",
                relative_path=optimized_relative,
                raw_error=f"artifact_path={out_relative!r}",
            )
        hash_error = _local_only_pair_hash_error(
            label="优化后配体姿势",
            expected=out_expected,
            actual=str(file_records["optimized"]["sha256"]),
            relative_path=optimized_relative,
        )
        if hash_error:
            return hash_error
        integrity_status = "verified"
        integrity_source = "execution_metadata"
        pair_warnings: list[str] = []
    else:
        # Historical single-stage local_only runs did not always record hashes.
        # Enforce any valid hashes that do exist, but never promote current-file
        # observations to execution-time provenance.
        legacy_hash_candidates = {
            "receptor": [
                str(input_hashes.get("receptor") or ""),
                str(
                    (snapshot_inputs.get("receptor") or {}).get("sha256") or ""
                )
                if isinstance(snapshot_inputs.get("receptor"), dict)
                else "",
            ],
            "input": [
                str(input_hashes.get("ligand") or ""),
                str((snapshot_inputs.get("ligand") or {}).get("sha256") or "")
                if isinstance(snapshot_inputs.get("ligand"), dict)
                else "",
            ],
            "optimized": [str(out_artifact.get("sha256") or "")],
        }
        if flexible_receptor:
            legacy_hash_candidates["flex"] = [
                str(input_hashes.get("flex") or ""),
                str((snapshot_inputs.get("flex") or {}).get("sha256") or "")
                if isinstance(snapshot_inputs.get("flex"), dict)
                else "",
            ]
        for key, candidates in legacy_hash_candidates.items():
            actual = str(file_records[key]["sha256"])
            for expected in candidates:
                if re.fullmatch(r"[0-9a-fA-F]{64}", expected) and (
                    expected.lower() != actual.lower()
                ):
                    return _viewer_error(
                        "VIEWER_LOCAL_POSE_PAIR_HASH_MISMATCH",
                        "历史 local_only run 中已记录的姿势哈希与当前文件不一致。",
                        relative_path=str(file_records[key]["relative_path"]),
                        raw_error=f"expected={expected}; actual={actual}",
                    )
        integrity_status = "legacy_unverified"
        integrity_source = "current_file_observation"
        pair_warnings = [
            "该历史 local_only run 未记录完整的执行时哈希；本次结果仅供几何叠合，不代表审计完整。"
        ]

    comparison = compare_local_only_pose_texts(
        str(file_records["input"]["content"]),
        str(file_records["optimized"]["content"]),
    )
    if not comparison.get("ok"):
        comparison_error = (
            comparison.get("error")
            if isinstance(comparison.get("error"), dict)
            else {}
        )
        return _viewer_error(
            "VIEWER_LOCAL_POSE_PAIR_IDENTITY_MISMATCH",
            "输入姿势与优化后姿势的原子身份不能严格对应，已拒绝叠合显示。",
            raw_error=(
                f"{comparison_error.get('code', '')}: "
                f"{comparison_error.get('raw_error', '')}"
            ).strip(": "),
            suggestion=str(comparison_error.get("suggestion") or ""),
        )

    comparison_warnings = (
        comparison.get("warnings")
        if isinstance(comparison.get("warnings"), list)
        else []
    )
    pair_warnings.extend(str(item) for item in comparison_warnings)
    if flexible_receptor:
        pair_warnings.append(
            "该 run 使用柔性受体；完整受体视图由刚性受体与对应的柔性受体层共同组成。"
        )
    payload = {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "run_mode": "local_only",
        "integrity": {
            "status": integrity_status,
            "source": integrity_source,
            "receptor_sha256": file_records["receptor"]["sha256"],
            "input_sha256": file_records["input"]["sha256"],
            "optimized_sha256": file_records["optimized"]["sha256"],
            **(
                {"flex_input_sha256": file_records["flex"]["sha256"]}
                if flexible_receptor
                else {}
            ),
        },
        "coordinate_frame": {
            "source": "run_snapshot",
            "same_receptor_frame": True,
            "alignment_applied": False,
            "flexible_receptor": flexible_receptor,
        },
        "receptor": _local_only_bundle_structure(
            file_records["receptor"],
            file_kind="run_receptor",
            pose_label="刚性受体快照" if flexible_receptor else "受体快照",
        ),
        "input": _local_only_bundle_structure(
            file_records["input"],
            file_kind="docking_output",
            pose_kind="input",
            pose_label="输入姿势",
            ligand_only=True,
        ),
        "optimized": _local_only_bundle_structure(
            file_records["optimized"],
            file_kind="docking_output",
            pose_kind="optimized",
            pose_label="优化后姿势",
            ligand_only=True,
        ),
        "comparison": comparison,
        "warnings": pair_warnings,
        "message": (
            "已读取本次 flexible local_only run 的刚性受体、柔性受体与优化前后姿势。"
            if flexible_receptor
            else "已读取本次 local_only run 的受体、输入姿势与优化后姿势。"
        ),
        "error": None,
    }
    if flexible_receptor:
        payload.update(
            {
                "flex_receptor_input": _local_only_bundle_structure(
                    file_records["flex"],
                    file_kind="run_flexible_receptor",
                    pose_label="柔性受体输入快照",
                    flexible_receptor_only=True,
                ),
                "flex_receptor_optimized": _local_only_bundle_structure(
                    file_records["optimized"],
                    file_kind="run_flexible_receptor",
                    pose_label="优化后柔性受体",
                    flexible_receptor_only=True,
                ),
            }
        )
    return payload


def _parse_pdbqt_poses(content: str) -> list[dict[str, Any]]:
    lines = content.splitlines()
    poses: list[dict[str, Any]] = []
    current: list[str] | None = None
    current_mode = 1
    implicit_mode = 1

    for line in lines:
        model_match = MODEL_PATTERN.match(line)
        if model_match:
            if current is not None:
                poses.append({"mode": current_mode, "content": "\n".join(current) + "\n"})
            current = [line]
            mode_text = model_match.group(1)
            current_mode = int(mode_text) if mode_text else implicit_mode
            implicit_mode = max(implicit_mode, current_mode + 1)
            continue

        if current is not None:
            current.append(line)
            if ENDMDL_PATTERN.match(line):
                poses.append({"mode": current_mode, "content": "\n".join(current) + "\n"})
                current = None
            continue

    if current is not None:
        poses.append({"mode": current_mode, "content": "\n".join(current) + "\n"})

    if not poses and content.strip():
        poses.append({"mode": 1, "content": content})

    return poses


def _viewer_sha256(path: Path) -> str:
    """Return a streaming SHA256 for viewer integrity checks."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _screening_staging_root(
    project_root: Path,
) -> tuple[Path | None, dict[str, Any] | None]:
    screening_dir = project_root / "screening"
    staging_dir = screening_dir / "staging"
    if screening_dir.is_symlink() or staging_dir.is_symlink():
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
            "批量配体 staging 路径不能是符号链接。",
            file_kind="screening_candidate",
            relative_path=Path("screening", "staging").as_posix(),
            suggestion="请重新导入批量配体以重建项目内的普通 staging 目录。",
        )
    if not staging_dir.is_dir():
        return None, _viewer_error(
            "VIEWER_SCREENING_STAGING_NOT_FOUND",
            "没有找到批量配体 staging 目录。",
            file_kind="screening_candidate",
            relative_path=Path("screening", "staging").as_posix(),
            suggestion="请重新导入批量配体后再打开 3D 预览。",
        )
    try:
        resolved_staging = staging_dir.resolve(strict=True)
        resolved_staging.relative_to(project_root)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
            "批量配体 staging 路径不属于当前项目。",
            file_kind="screening_candidate",
            relative_path=Path("screening", "staging").as_posix(),
            raw_error=str(exc),
            suggestion="请重新导入批量配体以重建项目内的 staging 快照。",
        )
    return resolved_staging, None


def _read_verified_screening_snapshot(
    project_root: Path,
    staging_root: Path,
    *,
    relative_path: Any,
    expected_sha256: Any,
    expected_size: Any,
    allowed_extensions: set[str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    value = str(relative_path or "").strip()
    raw_parts = value.split("/") if value and "\\" not in value else []
    path = Path(value)
    if (
        not raw_parts
        or "\x00" in value
        or path.is_absolute()
        or bool(path.drive)
        or any(part in {"", ".", ".."} for part in raw_parts)
        or tuple(part.lower() for part in raw_parts[:2])
        != ("screening", "staging")
        or len(raw_parts) < 3
    ):
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
            "批量配体候选路径不属于当前 staging 快照。",
            file_kind="screening_candidate",
            relative_path=value,
            suggestion="请重新导入该配体以生成新的可复现快照。",
        )
    if path.suffix.lower() not in allowed_extensions:
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_FORMAT_INVALID",
            "批量配体候选文件格式与记录类型不一致。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=f"allowed={','.join(sorted(allowed_extensions))}",
            suggestion="请重新导入该配体以重建正确格式的 staging 快照。",
        )

    normalized_sha256 = str(expected_sha256 or "").strip().lower()
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", normalized_sha256)
    ):
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_IDENTITY_INVALID",
            "批量配体候选缺少可信的文件身份记录。",
            file_kind="screening_candidate",
            relative_path=value,
            suggestion="请重新导入该配体以生成新的可复现快照。",
        )

    candidate_path = project_root.joinpath(*raw_parts)
    current = project_root
    for part in raw_parts:
        current /= part
        if current.is_symlink():
            return None, _viewer_error(
                "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
                "批量配体候选路径不能包含符号链接。",
                file_kind="screening_candidate",
                relative_path=value,
                suggestion="请重新导入该配体以生成普通文件快照。",
            )
    if not candidate_path.exists():
        return None, _viewer_error(
            "VIEWER_FILE_NOT_FOUND",
            "没有找到批量配体候选文件。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=str(candidate_path),
            suggestion="请重新导入该配体以生成新的 staging 快照。",
        )
    if not candidate_path.is_file():
        return None, _viewer_error(
            "VIEWER_PATH_NOT_FILE",
            "批量配体候选路径不是普通文件。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=str(candidate_path),
        )
    try:
        resolved_path = candidate_path.resolve(strict=True)
        resolved_path.relative_to(staging_root)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
            "批量配体候选解析后不属于当前 staging 快照。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=str(exc),
            suggestion="请重新导入该配体以生成新的可复现快照。",
        )

    try:
        with resolved_path.open("rb") as handle:
            content_bytes = handle.read(MAX_VIEWER_FILE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - return a structured viewer error.
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_READ_ERROR",
            "读取批量配体快照时发生错误。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=str(exc),
        )
    if not content_bytes:
        return None, _viewer_error(
            "VIEWER_FILE_EMPTY",
            "批量配体候选文件为空，无法用于 3D 预览。",
            file_kind="screening_candidate",
            relative_path=value,
        )
    if len(content_bytes) > MAX_VIEWER_FILE_BYTES:
        return None, _viewer_error(
            "VIEWER_FILE_TOO_LARGE",
            "批量配体候选文件超过 20 MB 的预览上限。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=f"> {MAX_VIEWER_FILE_BYTES} bytes",
        )
    actual_sha256 = hashlib.sha256(content_bytes).hexdigest()
    if len(content_bytes) != expected_size or actual_sha256 != normalized_sha256:
        return None, _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_HASH_MISMATCH",
            "批量配体快照已发生变化，已拒绝加载。",
            file_kind="screening_candidate",
            relative_path=value,
            raw_error=(
                f"expected_size={expected_size}; actual_size={len(content_bytes)}; "
                f"expected_sha256={normalized_sha256}; actual_sha256={actual_sha256}"
            ),
            suggestion="请重新导入配体并创建新的批量任务。",
        )
    return {
        "relative_path": value,
        "absolute_path": str(resolved_path),
        "format": _detect_format(value),
        "content": content_bytes.decode("utf-8", errors="replace"),
        "size_bytes": len(content_bytes),
        "sha256": actual_sha256,
    }, None


def load_screening_candidate_for_viewer(
    project_dir: str,
    candidate_id: str,
    expected_revision_sha256: str,
) -> dict[str, Any]:
    """Load one frozen batch-import candidate before the queue is created.

    The staging index is the authority for both the user-facing identity and
    the immutable file hash.  Prefer the frozen source topology when it is
    available so the preview keeps the original bond representation; otherwise
    fall back to the prepared PDBQT snapshot used by the screening queue.
    """

    normalized_candidate_id = str(candidate_id or "").strip().lower()
    if not SCREENING_CANDIDATE_ID_PATTERN.fullmatch(normalized_candidate_id):
        return _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_INVALID",
            "批量配体候选编号无效。",
            file_kind="screening_candidate",
            raw_error=str(candidate_id),
            suggestion="请从当前批量配体列表中重新选择一个候选。",
        )

    project_root = _project_root(project_dir)
    staging_root, staging_error = _screening_staging_root(project_root)
    if staging_error:
        return staging_error
    assert staging_root is not None
    index_path = staging_root / "index.json"
    if index_path.is_symlink():
        return _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_PATH_INVALID",
            "批量配体导入记录不能是符号链接。",
            file_kind="screening_candidate",
            relative_path=Path("screening", "staging", "index.json").as_posix(),
            suggestion="请重新导入批量配体以重建 staging 记录。",
        )
    if not index_path.is_file():
        return _viewer_error(
            "VIEWER_SCREENING_STAGING_NOT_FOUND",
            "没有找到批量配体导入记录。",
            file_kind="screening_candidate",
            relative_path=Path("screening", "staging", "index.json").as_posix(),
            suggestion="请重新导入批量配体后再打开 3D 预览。",
        )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - return a structured viewer error.
        return _viewer_error(
            "VIEWER_SCREENING_STAGING_INVALID",
            "批量配体导入记录无法解析。",
            file_kind="screening_candidate",
            raw_error=str(exc),
            suggestion="请重新导入批量配体以重建 staging 记录。",
        )

    preview = index.get("last_import") if isinstance(index, dict) else None
    candidates = preview.get("candidates") if isinstance(preview, dict) else None
    if not isinstance(candidates, list):
        return _viewer_error(
            "VIEWER_SCREENING_PREVIEW_NOT_FOUND",
            "当前项目没有可供预览的批量配体候选。",
            file_kind="screening_candidate",
            suggestion="请返回结构导入步骤并重新选择配体文件。",
        )
    recorded_revision = str(preview.get("revision_sha256") or "").lower()
    requested_revision = str(expected_revision_sha256 or "").strip().lower()
    computed_revision = _screening_import_revision(preview)
    if (
        not re.fullmatch(r"[0-9a-f]{64}", recorded_revision)
        or not re.fullmatch(r"[0-9a-f]{64}", requested_revision)
        or requested_revision != recorded_revision
        or recorded_revision != computed_revision
    ):
        return _viewer_error(
            "VIEWER_SCREENING_REVISION_MISMATCH",
            "批量配体列表已经变化，已停止加载旧候选。",
            file_kind="screening_candidate",
            raw_error=(
                f"expected={requested_revision or 'missing'}; "
                f"current={recorded_revision or 'missing'}; "
                f"computed={computed_revision or 'missing'}"
            ),
            suggestion="请刷新页面并从当前批量配体列表重新选择。",
        )
    candidate = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("candidate_id") or "").lower()
            == normalized_candidate_id
        ),
        None,
    )
    if candidate is None:
        return _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_NOT_FOUND",
            "没有找到所选批量配体候选。",
            file_kind="screening_candidate",
            raw_error=normalized_candidate_id,
            suggestion="请刷新批量配体列表后重新选择。",
        )
    if str(candidate.get("status") or "") not in {"ready", "duplicate"}:
        return _viewer_error(
            "VIEWER_SCREENING_CANDIDATE_NOT_READY",
            "所选批量配体尚未准备为可预览结构。",
            file_kind="screening_candidate",
            raw_error=str(candidate.get("status") or "unknown"),
            suggestion="请先处理该配体的导入或结构审查问题。",
        )

    prepared_snapshot, prepared_error = _read_verified_screening_snapshot(
        project_root,
        staging_root,
        relative_path=candidate.get("file"),
        expected_sha256=candidate.get("sha256"),
        expected_size=candidate.get("size_bytes"),
        allowed_extensions={".pdbqt"},
    )
    if prepared_error:
        return prepared_error
    assert prepared_snapshot is not None

    display_snapshot = prepared_snapshot
    if candidate.get("topology_integrity") == "verified":
        topology_snapshot, topology_error = _read_verified_screening_snapshot(
            project_root,
            staging_root,
            relative_path=candidate.get("source_topology_file"),
            expected_sha256=candidate.get("source_topology_sha256"),
            expected_size=candidate.get("source_topology_size_bytes"),
            allowed_extensions={".sdf", ".mol", ".mol2"},
        )
        if topology_error:
            return topology_error
        assert topology_snapshot is not None
        display_snapshot = topology_snapshot
        source_kind = "frozen_source_topology"
    else:
        source_kind = "prepared_pdbqt"

    viewer_content, viewer_format, viewer_warnings = _viewer_content(
        str(display_snapshot["content"]),
        str(display_snapshot["format"]),
    )
    display_name = str(
        candidate.get("source_record_name")
        or candidate.get("original_name")
        or normalized_candidate_id
    )
    return ViewerStructureResult(
        ok=True,
        file_kind="screening_candidate",
        relative_path=str(display_snapshot["relative_path"]),
        absolute_path=str(display_snapshot["absolute_path"]),
        exists=True,
        format=viewer_format,
        content=viewer_content,
        size_bytes=int(display_snapshot["size_bytes"]),
        message="批量配体候选已加载。",
        warnings=viewer_warnings,
        error=None,
    ).to_dict() | {
        "candidate_id": normalized_candidate_id,
        "display_name": display_name,
        "original_name": str(candidate.get("original_name") or ""),
        "source_kind": source_kind,
        "sha256": str(display_snapshot["sha256"]),
        "prepared_sha256": str(prepared_snapshot["sha256"]),
    }


def load_screening_pose_for_viewer(
    project_dir: str,
    item_id: str,
    mode: int | None = None,
) -> dict[str, Any]:
    """Load one successful screening pose with its frozen receptor.

    New screening records bind both structures to SHA256 values.  Historical
    records without an output hash remain viewable, but the response labels
    their output integrity as ``legacy_unverified`` instead of implying that
    the file is unchanged.
    """

    if not SCREENING_ITEM_ID_PATTERN.fullmatch(str(item_id or "")):
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_INVALID",
            "批量筛选配体编号无效。",
            file_kind="screening_output",
            raw_error=str(item_id),
            suggestion="请从当前批量筛选完整结果表中选择配体。",
        )
    try:
        selected_mode = int(mode) if mode is not None else 1
    except (TypeError, ValueError):
        selected_mode = 0
    if selected_mode < 1:
        return _viewer_error(
            "VIEWER_SCREENING_MODE_INVALID",
            "批量筛选构象编号必须是大于 0 的整数。",
            file_kind="screening_output",
            raw_error=repr(mode),
        )

    project_root = _project_root(project_dir)
    state_path = project_root / "screening" / "screening.json"
    if not state_path.is_file():
        return _viewer_error(
            "VIEWER_SCREENING_STATE_NOT_FOUND",
            "没有找到当前批量筛选记录。",
            file_kind="screening_output",
            relative_path=Path("screening", "screening.json").as_posix(),
            suggestion="请先完成或恢复当前批量筛选任务。",
        )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - return a structured viewer error.
        return _viewer_error(
            "VIEWER_SCREENING_STATE_INVALID",
            "批量筛选状态文件无法解析。",
            file_kind="screening_output",
            raw_error=str(exc),
            suggestion="请检查 screening/screening.json，或恢复对应筛选记录。",
        )
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or not isinstance(state.get("items"), list)
    ):
        return _viewer_error(
            "VIEWER_SCREENING_STATE_UNSUPPORTED",
            "批量筛选状态格式不受支持。",
            file_kind="screening_output",
            raw_error=f"schema_version={state.get('schema_version') if isinstance(state, dict) else 'invalid'}",
        )

    item = next(
        (
            candidate
            for candidate in state["items"]
            if isinstance(candidate, dict)
            and str(candidate.get("item_id") or "") == item_id
        ),
        None,
    )
    if item is None:
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_NOT_FOUND",
            "当前批量筛选中没有找到所选配体。",
            file_kind="screening_output",
            raw_error=item_id,
        )
    if item.get("status") != "succeeded":
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_NOT_SUCCEEDED",
            "只有成功完成的批量筛选配体可以查看构象。",
            file_kind="screening_output",
            raw_error=f"{item_id}: status={item.get('status')}",
            suggestion="请等待该配体成功完成，或查看失败诊断。",
        )

    output_relative = str(item.get("best_output_file") or "")
    succeeded_attempt = next(
        (
            attempt
            for attempt in reversed(item.get("attempts") or [])
            if isinstance(attempt, dict)
            and attempt.get("status") == "succeeded"
            and str(attempt.get("output_file") or "") == output_relative
        ),
        None,
    )
    if not output_relative or succeeded_attempt is None:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_RECORD_INVALID",
            "成功配体缺少与尝试记录一致的输出文件，已拒绝读取。",
            file_kind="screening_output",
            relative_path=output_relative,
            suggestion="请保留筛选记录并检查 attempt.json；不要手工重写 best_output_file。",
        )

    inputs = state.get("inputs") if isinstance(state.get("inputs"), dict) else {}
    receptor_record = (
        inputs.get("receptor")
        if isinstance(inputs.get("receptor"), dict)
        else {}
    )
    receptor_relative = str(receptor_record.get("file") or "")
    attempt_directory = str(succeeded_attempt.get("directory") or "")
    attempt_parts = Path(attempt_directory).parts
    expected_attempt_prefix = ("screening", "attempts", item_id)
    expected_output_relative = (
        (Path(attempt_directory) / "out.pdbqt").as_posix()
        if attempt_directory
        else ""
    )
    if (
        Path(receptor_relative).parts
        != ("screening", "inputs", "receptor.pdbqt")
        or len(attempt_parts) != 4
        or attempt_parts[:3] != expected_attempt_prefix
        or not re.fullmatch(r"attempt_\d{3,}", attempt_parts[3])
        or Path(output_relative).as_posix() != expected_output_relative
    ):
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_SCOPE_INVALID",
            "筛选构象记录没有指向本次任务的冻结受体和配体 attempt，已拒绝读取。",
            file_kind="screening_output",
            relative_path=output_relative,
            raw_error=(
                f"receptor={receptor_relative}; "
                f"attempt_directory={attempt_directory}; output={output_relative}"
            ),
            suggestion="请从当前筛选结果表重新选择配体，或恢复未修改的筛选记录。",
        )
    receptor_validation = validate_viewer_file(project_dir, receptor_relative)
    if not receptor_validation.get("ok"):
        receptor_validation["file_kind"] = "screening_receptor"
        return receptor_validation
    output_validation = validate_viewer_file(project_dir, output_relative)
    if not output_validation.get("ok"):
        output_validation["file_kind"] = "screening_output"
        return output_validation

    receptor_path = Path(receptor_validation["absolute_path"])
    output_path = Path(output_validation["absolute_path"])
    receptor_actual_sha256 = _viewer_sha256(receptor_path)
    output_actual_sha256 = _viewer_sha256(output_path)
    receptor_expected_sha256 = str(receptor_record.get("sha256") or "")
    item_output_sha256 = str(item.get("best_output_sha256") or "")
    attempt_output_sha256 = str(succeeded_attempt.get("output_sha256") or "")
    if (
        item_output_sha256
        and attempt_output_sha256
        and item_output_sha256.lower() != attempt_output_sha256.lower()
    ):
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_HASH_CONFLICT",
            "批量筛选的配体与尝试记录保存了不同的输出 SHA256，已拒绝显示。",
            file_kind="screening_output",
            relative_path=output_relative,
            suggestion="请保留筛选记录并检查 attempt.json；不要手工修改输出哈希。",
        )
    output_expected_sha256 = item_output_sha256 or attempt_output_sha256
    mismatches = [
        label
        for label, expected, actual in (
            ("受体", receptor_expected_sha256, receptor_actual_sha256),
            ("配体输出", output_expected_sha256, output_actual_sha256),
        )
        if expected and expected.lower() != actual.lower()
    ]
    if mismatches:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_HASH_MISMATCH",
            f"{'、'.join(mismatches)} SHA256 与筛选记录不一致，已拒绝显示。",
            file_kind="screening_output",
            relative_path=output_relative,
            raw_error="筛选输出可能在任务完成后被替换或修改。",
            suggestion="请从原始筛选归档恢复文件，或重新运行该配体。",
        )

    try:
        receptor_content = receptor_path.read_text(encoding="utf-8", errors="replace")
        output_content = output_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - return a structured viewer error.
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_READ_ERROR",
            "读取批量筛选结构文件时发生错误。",
            file_kind="screening_output",
            relative_path=output_relative,
            raw_error=str(exc),
        )
    poses = _parse_pdbqt_poses(output_content)
    selected = next(
        (pose for pose in poses if int(pose["mode"]) == selected_mode),
        None,
    )
    if selected is None:
        return _viewer_error(
            "VIEWER_SCREENING_POSE_NOT_FOUND",
            f"批量筛选输出中没有 Mode {selected_mode}。",
            file_kind="screening_output",
            relative_path=output_relative,
            suggestion="请查看该输出实际包含的构象编号。",
        )

    receptor_viewer_content, receptor_format, receptor_warnings = _viewer_content(
        receptor_content,
        str(receptor_validation["format"]),
    )
    pose_viewer_content, pose_format, pose_warnings = _viewer_content(
        str(selected["content"]),
        str(output_validation["format"]),
    )
    integrity_status = (
        "verified"
        if receptor_expected_sha256 and output_expected_sha256
        else "legacy_unverified"
    )
    integrity_warning = (
        []
        if integrity_status == "verified"
        else ["历史筛选记录没有保存完整输出 SHA256；本次只能确认文件当前可读，不能证明完成后未被替换。"]
    )
    receptor_result = ViewerStructureResult(
        ok=True,
        file_kind="screening_receptor",
        relative_path=receptor_relative,
        absolute_path=str(receptor_path),
        exists=True,
        format=receptor_format,
        content=receptor_viewer_content,
        size_bytes=len(receptor_viewer_content.encode("utf-8")),
        message="已读取本次筛选冻结的受体快照。",
        warnings=receptor_warnings,
        error=None,
    ).to_dict()
    pose_result = ViewerStructureResult(
        ok=True,
        file_kind="screening_output",
        relative_path=output_relative,
        absolute_path=str(output_path),
        exists=True,
        format=pose_format,
        content=pose_viewer_content,
        size_bytes=len(pose_viewer_content.encode("utf-8")),
        message=f"已读取 {item_id} 的 Mode {selected_mode}。",
        warnings=[*pose_warnings, *integrity_warning],
        error=None,
    ).to_dict() | {
        "mode": selected_mode,
        "pose_label": f"{item_id} Mode {selected_mode}",
    }
    return {
        "ok": True,
        "project_dir": str(project_root),
        "screening_id": str(state.get("screening_id") or ""),
        "item_id": item_id,
        "source_file": str(item.get("source_file") or item.get("ligand_file") or ""),
        "best_affinity_kcal_mol": item.get("best_affinity_kcal_mol"),
        "available_modes": [int(pose["mode"]) for pose in poses],
        "mode": selected_mode,
        "receptor": receptor_result,
        "pose": pose_result,
        "integrity": {
            "status": integrity_status,
            "receptor_sha256": receptor_actual_sha256,
            "output_sha256": output_actual_sha256,
            "expected_receptor_sha256": receptor_expected_sha256,
            "expected_output_sha256": output_expected_sha256,
        },
        "message": (
            "批量筛选受体与最佳构象已通过 SHA256 核对并加载。"
            if integrity_status == "verified"
            else "历史批量筛选构象已加载，但缺少完成时输出 SHA256。"
        ),
        "warnings": integrity_warning,
        "error": None,
    }


def load_archived_screening_pose_for_viewer(
    project_dir: str,
    archive_id: str,
    item_id: str,
    mode: int | None = None,
) -> dict[str, Any]:
    """Load one pose from a verified immutable screening archive."""

    if not SCREENING_ITEM_ID_PATTERN.fullmatch(str(item_id or "")):
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_INVALID",
            "批量筛选配体编号无效。",
            file_kind="screening_archive_output",
            raw_error=str(item_id),
            suggestion="请从历史筛选完整结果表中选择配体。",
        )
    try:
        selected_mode = int(mode) if mode is not None else 1
    except (TypeError, ValueError):
        selected_mode = 0
    if selected_mode < 1 or selected_mode > 50:
        return _viewer_error(
            "VIEWER_SCREENING_MODE_INVALID",
            "批量筛选构象编号必须在 1 到 50 之间。",
            file_kind="screening_archive_output",
            raw_error=repr(mode),
        )

    archived = get_screening_archive(project_dir, archive_id)
    if not archived.get("ok"):
        error = archived.get("error") if isinstance(archived.get("error"), dict) else {}
        return _viewer_error(
            "VIEWER_SCREENING_ARCHIVE_INVALID",
            "批量筛选历史归档未通过读取校验。",
            file_kind="screening_archive_output",
            raw_error=(
                f"{error.get('code') or 'SCREENING_ARCHIVE_READ_ERROR'}: "
                f"{error.get('raw_error') or error.get('message') or 'unknown'}"
            ),
            suggestion="请从历史归档列表选择 valid=true 的记录。",
        )
    state = archived.get("screening")
    files = archived.get("files")
    if not isinstance(state, dict) or not isinstance(files, dict):
        return _viewer_error(
            "VIEWER_SCREENING_ARCHIVE_INVALID",
            "批量筛选历史归档详情缺少状态或文件索引。",
            file_kind="screening_archive_output",
        )
    item = next(
        (
            candidate
            for candidate in state.get("items") or []
            if isinstance(candidate, dict)
            and str(candidate.get("item_id") or "") == item_id
        ),
        None,
    )
    if item is None:
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_NOT_FOUND",
            "所选历史筛选中没有找到该配体。",
            file_kind="screening_archive_output",
            raw_error=item_id,
        )
    if item.get("status") != "succeeded":
        return _viewer_error(
            "VIEWER_SCREENING_ITEM_NOT_SUCCEEDED",
            "只有成功完成的历史筛选配体可以查看构象。",
            file_kind="screening_archive_output",
            raw_error=f"{item_id}: status={item.get('status')}",
        )
    output_relative = str(item.get("best_output_file") or "")
    succeeded_attempt = next(
        (
            attempt
            for attempt in reversed(item.get("attempts") or [])
            if isinstance(attempt, dict)
            and attempt.get("status") == "succeeded"
            and str(attempt.get("output_file") or "") == output_relative
        ),
        None,
    )
    if not output_relative or succeeded_attempt is None:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_RECORD_INVALID",
            "历史筛选配体缺少一致的成功输出记录。",
            file_kind="screening_archive_output",
            raw_error=item_id,
        )

    item_files = files.get("items") if isinstance(files.get("items"), dict) else {}
    selected_files = (
        item_files.get(item_id)
        if isinstance(item_files.get(item_id), dict)
        else {}
    )
    receptor_relative = str(files.get("receptor") or "")
    relocated_output = str(selected_files.get("best_output") or "")
    receptor_validation = validate_viewer_file(project_dir, receptor_relative)
    if not receptor_validation.get("ok"):
        receptor_validation["file_kind"] = "screening_archive_receptor"
        return receptor_validation
    output_validation = validate_viewer_file(project_dir, relocated_output)
    if not output_validation.get("ok"):
        output_validation["file_kind"] = "screening_archive_output"
        return output_validation

    receptor_path = Path(receptor_validation["absolute_path"])
    output_path = Path(output_validation["absolute_path"])
    receptor_record = (
        state.get("inputs", {}).get("receptor", {})
        if isinstance(state.get("inputs"), dict)
        and isinstance(state.get("inputs", {}).get("receptor"), dict)
        else {}
    )
    receptor_expected_hash = str(receptor_record.get("sha256") or "")
    item_output_hash = str(item.get("best_output_sha256") or "")
    attempt_output_hash = str(succeeded_attempt.get("output_sha256") or "")
    if (
        item_output_hash
        and attempt_output_hash
        and item_output_hash.lower() != attempt_output_hash.lower()
    ):
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_HASH_CONFLICT",
            "历史筛选的配体与尝试记录保存了不同的输出 SHA256，已拒绝显示。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
        )
    output_expected_hash = item_output_hash or attempt_output_hash

    def recorded_size(value: Any) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            return -1
        return parsed

    receptor_expected_size = recorded_size(receptor_record.get("size_bytes"))
    item_output_size = recorded_size(item.get("best_output_size_bytes"))
    attempt_output_size = recorded_size(succeeded_attempt.get("output_size_bytes"))
    if min(receptor_expected_size, item_output_size, attempt_output_size) < 0:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_SIZE_INVALID",
            "历史筛选记录了无效的文件大小。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
        )
    if (
        item_output_size
        and attempt_output_size
        and item_output_size != attempt_output_size
    ):
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_SIZE_CONFLICT",
            "历史筛选的配体与尝试记录保存了不同的输出文件大小，已拒绝显示。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
        )
    output_expected_size = item_output_size or attempt_output_size
    actual_receptor_size = receptor_path.stat().st_size
    actual_output_size = output_path.stat().st_size
    size_mismatches = [
        label
        for label, expected, actual in (
            ("受体", receptor_expected_size, actual_receptor_size),
            ("配体输出", output_expected_size, actual_output_size),
        )
        if expected and expected != actual
    ]
    if size_mismatches:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_SIZE_MISMATCH",
            f"{'、'.join(size_mismatches)}大小与历史筛选记录不一致，已拒绝显示。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
        )

    receptor_actual_hash = _viewer_sha256(receptor_path)
    output_actual_hash = _viewer_sha256(output_path)
    hash_mismatches = [
        label
        for label, expected, actual in (
            ("受体", receptor_expected_hash, receptor_actual_hash),
            ("配体输出", output_expected_hash, output_actual_hash),
        )
        if expected and expected.lower() != actual.lower()
    ]
    if hash_mismatches:
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_HASH_MISMATCH",
            f"{'、'.join(hash_mismatches)} SHA256 与历史筛选记录不一致，已拒绝显示。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
            suggestion="请恢复原归档文件，或重新运行该批量筛选。",
        )

    try:
        receptor_content = receptor_path.read_text(encoding="utf-8", errors="replace")
        output_content = output_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - return a structured viewer error.
        return _viewer_error(
            "VIEWER_SCREENING_OUTPUT_READ_ERROR",
            "读取历史筛选结构文件时发生错误。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
            raw_error=str(exc),
        )
    poses = _parse_pdbqt_poses(output_content)
    selected = next(
        (pose for pose in poses if int(pose["mode"]) == selected_mode),
        None,
    )
    if selected is None:
        return _viewer_error(
            "VIEWER_SCREENING_POSE_NOT_FOUND",
            f"历史筛选输出中没有 Mode {selected_mode}。",
            file_kind="screening_archive_output",
            relative_path=relocated_output,
        )
    receptor_viewer_content, receptor_format, receptor_warnings = _viewer_content(
        receptor_content,
        str(receptor_validation["format"]),
    )
    pose_viewer_content, pose_format, pose_warnings = _viewer_content(
        str(selected["content"]),
        str(output_validation["format"]),
    )
    integrity_status = (
        "verified"
        if receptor_expected_hash and output_expected_hash
        else "legacy_unverified"
    )
    integrity_warning = (
        []
        if integrity_status == "verified"
        else ["历史归档没有保存完整输出 SHA256；文件可读但无法证明归档后未被替换。"]
    )
    label = str(item.get("display_label") or item_id)
    receptor_result = ViewerStructureResult(
        ok=True,
        file_kind="screening_archive_receptor",
        relative_path=receptor_relative,
        absolute_path=str(receptor_path),
        exists=True,
        format=receptor_format,
        content=receptor_viewer_content,
        size_bytes=len(receptor_viewer_content.encode("utf-8")),
        message="已读取历史筛选冻结的受体快照。",
        warnings=receptor_warnings,
        error=None,
    ).to_dict()
    pose_result = ViewerStructureResult(
        ok=True,
        file_kind="screening_archive_output",
        relative_path=relocated_output,
        absolute_path=str(output_path),
        exists=True,
        format=pose_format,
        content=pose_viewer_content,
        size_bytes=len(pose_viewer_content.encode("utf-8")),
        message=f"已读取 {label} 的 Mode {selected_mode}。",
        warnings=[*pose_warnings, *integrity_warning],
        error=None,
    ).to_dict() | {
        "mode": selected_mode,
        "pose_label": f"{label} Mode {selected_mode}",
    }
    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser().resolve()),
        "archive_id": str(archive_id),
        "screening_id": str(state.get("screening_id") or ""),
        "item_id": item_id,
        "display_label": label,
        "source_file": str(item.get("source_file") or item.get("ligand_file") or ""),
        "best_affinity_kcal_mol": item.get("best_affinity_kcal_mol"),
        "available_modes": [int(pose["mode"]) for pose in poses],
        "mode": selected_mode,
        "receptor": receptor_result,
        "pose": pose_result,
        "integrity": {
            "status": integrity_status,
            "state": "verified",
            "receptor_sha256": receptor_actual_hash,
            "output_sha256": output_actual_hash,
            "expected_receptor_sha256": receptor_expected_hash,
            "expected_output_sha256": output_expected_hash,
        },
        "message": (
            "历史筛选受体与最佳构象已通过状态和文件 SHA256 核对并加载。"
            if integrity_status == "verified"
            else "历史筛选构象已加载，但旧归档缺少完成时输出 SHA256。"
        ),
        "warnings": integrity_warning,
        "error": None,
    }


def _parse_float_cell(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _default_scores_relative_path(project_dir: str, run_id: str) -> str:
    metadata_path = _project_root(project_dir) / "runs" / run_id / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and metadata.get("scores_file"):
                return str(metadata["scores_file"])
        except Exception:
            pass
    return Path("runs", run_id, "scores.csv").as_posix()


def load_pose_score_summary(project_dir: str, run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _viewer_error(
            "VIEWER_RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            raw_error=str(run_id),
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    relative_path = _default_scores_relative_path(project_dir, run_id)
    project_root = _project_root(project_dir)
    scores_path, path_error = _resolve_project_file(project_root, relative_path, "scores_csv")
    if path_error:
        return path_error
    assert scores_path is not None

    if not scores_path.exists():
        return {
            "ok": True,
            "project_dir": str(project_root),
            "run_id": run_id,
            "scores_file": relative_path,
            "scores": [],
            "warnings": ["没有找到 scores.csv，仍可查看 docking pose，但不会显示 affinity/rmsd 摘要。"],
            "message": "scores.csv 不存在，pose 查看将只显示几何结构。",
            "error": None,
        }
    if not scores_path.is_file() or scores_path.stat().st_size <= 0:
        return {
            "ok": True,
            "project_dir": str(project_root),
            "run_id": run_id,
            "scores_file": relative_path,
            "scores": [],
            "warnings": ["scores.csv 不可读取或为空，仍可查看 docking pose。"],
            "message": "scores.csv 不可读取或为空，pose 查看将只显示几何结构。",
            "error": None,
        }

    scores: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with scores_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader, start=2):
                try:
                    mode = int(str(row.get("mode", "")).strip())
                except Exception:
                    warnings.append(f"scores.csv 第 {index} 行 mode 无法解析，已跳过。")
                    continue
                scores.append(
                    {
                        "mode": mode,
                        "affinity_kcal_mol": _parse_float_cell(row.get("affinity_kcal_mol")),
                        "rmsd_lb": _parse_float_cell(row.get("rmsd_lb")),
                        "rmsd_ub": _parse_float_cell(row.get("rmsd_ub")),
                    }
                )
    except Exception as exc:  # noqa: BLE001 - return structured warnings.
        return {
            "ok": True,
            "project_dir": str(project_root),
            "run_id": run_id,
            "scores_file": relative_path,
            "scores": [],
            "warnings": [f"读取 scores.csv 时发生错误，仍可查看 docking pose：{exc}"],
            "message": "scores.csv 读取失败，pose 查看将只显示几何结构。",
            "error": None,
        }

    return {
        "ok": True,
        "project_dir": str(project_root),
        "run_id": run_id,
        "scores_file": relative_path,
        "scores": scores,
        "warnings": warnings,
        "message": "pose score 摘要已读取。",
        "error": None,
    }


def list_docking_poses(project_dir: str, run_id: str) -> dict[str, Any]:
    relative_path, path_error = _run_output_relative_path(project_dir, run_id)
    if path_error:
        return path_error

    assert relative_path is not None
    validation = validate_viewer_file(project_dir, relative_path)
    if not validation.get("ok"):
        validation["run_id"] = run_id
        validation["file_kind"] = "docking_output"
        return validation

    content = Path(validation["absolute_path"]).read_text(encoding="utf-8", errors="replace")
    poses = _parse_pdbqt_poses(content)
    score_summary = load_pose_score_summary(project_dir, run_id)
    score_by_mode = {
        int(score["mode"]): score
        for score in score_summary.get("scores", [])
        if isinstance(score, dict) and str(score.get("mode", "")).isdigit()
    }
    summaries = [
        (
            lambda pose_mode, score: DockingPoseSummary(
                mode=pose_mode,
                relative_path=relative_path,
                size_bytes=len(pose["content"].encode("utf-8")),
                line_count=len(pose["content"].splitlines()),
                affinity_kcal_mol=score.get("affinity_kcal_mol") if score else None,
                rmsd_lb=score.get("rmsd_lb") if score else None,
                rmsd_ub=score.get("rmsd_ub") if score else None,
                message="已识别 docking pose。",
            ).to_dict()
        )(int(pose["mode"]), score_by_mode.get(int(pose["mode"])))
        for pose in poses
    ]
    warnings = [] if poses else ["out.pdbqt 中没有识别到可显示的 pose。"]
    warnings.extend(score_summary.get("warnings", []))
    return {
        "ok": True,
        "project_dir": str(_project_root(project_dir)),
        "run_id": run_id,
        "relative_path": relative_path,
        "format": validation["format"],
        "poses": summaries,
        "scores_file": score_summary.get("scores_file", ""),
        "message": "Docking pose 列表已读取，仅用于几何查看。",
        "warnings": warnings,
        "error": None,
    }


def load_docking_pose_for_viewer(
    project_dir: str,
    run_id: str,
    mode: int | None = None,
    pose_kind: str | None = None,
) -> dict[str, Any]:
    relative_path, pose_label, path_error = _run_evaluation_pose_relative_path(
        project_dir,
        run_id,
        pose_kind,
    )
    if path_error:
        return path_error

    assert relative_path is not None
    validation = validate_viewer_file(project_dir, relative_path)
    if not validation.get("ok"):
        validation["run_id"] = run_id
        validation["file_kind"] = "docking_output"
        return validation

    content = Path(validation["absolute_path"]).read_text(encoding="utf-8", errors="replace")
    poses = _parse_pdbqt_poses(content)
    if not poses:
        return _viewer_error(
            "VIEWER_POSE_NOT_FOUND",
            f"{pose_label or '输出文件'}中没有可显示的姿势。",
            file_kind="docking_output",
            relative_path=relative_path,
            suggestion="请确认本次 run 的姿势文件存在且非空。",
        )

    selected_mode = int(mode) if mode is not None else int(poses[0]["mode"])
    selected = next((pose for pose in poses if int(pose["mode"]) == selected_mode), None)
    if selected is None:
        return _viewer_error(
            "VIEWER_POSE_MODE_NOT_FOUND",
            f"没有找到 mode {selected_mode} 对应的 docking pose。",
            file_kind="docking_output",
            relative_path=relative_path,
            suggestion="请从 pose 列表中选择已有 mode。",
        )

    score_summary = load_pose_score_summary(project_dir, run_id)
    selected_score = next(
        (score for score in score_summary.get("scores", []) if isinstance(score, dict) and score.get("mode") == selected_mode),
        None,
    )
    run_metadata, run_metadata_error = _read_run_metadata(
        project_dir,
        run_id,
    )
    hydrated_pose = bool(
        run_metadata_error is None
        and isinstance(run_metadata, dict)
        and _is_hydrated_run_metadata(run_metadata)
    )
    project, project_error = _load_project_model(project_dir)
    display_pose = (
        _docking_pose_display_content(
            project_dir,
            project,
            run_id,
            str(selected["content"]),
            selected_mode,
        )
        if (
            not hydrated_pose
            and project_error is None
            and project is not None
        )
        else None
    )
    if display_pose is not None:
        viewer_content, viewer_format, viewer_warnings = display_pose
    else:
        viewer_content, viewer_format, viewer_warnings = _viewer_content(
            str(selected["content"]),
            validation["format"],
        )
        if hydrated_pose:
            viewer_warnings.append(
                "水合构象按保留水 PDBQT 显示；W 原子仅在 3D 显示副本中按水氧处理，原始文件未修改。"
            )
    return ViewerStructureResult(
        ok=True,
        file_kind="docking_output",
        relative_path=relative_path,
        absolute_path=validation["absolute_path"],
        exists=True,
        format=viewer_format,
        content=viewer_content,
        size_bytes=len(viewer_content.encode("utf-8")),
        message=(
            f"已读取{pose_label}，仅用于几何查看。"
            if pose_label
            else f"已读取 docking pose mode {selected_mode}，仅用于几何查看。"
        ),
        warnings=[*score_summary.get("warnings", []), *viewer_warnings],
        error=None,
    ).to_dict() | {
        "run_id": run_id,
        "mode": selected_mode,
        "pose_kind": str(pose_kind or ""),
        "pose_label": pose_label,
        "score": selected_score,
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) < 2:
        _print_json(get_viewer_file_status("."))
        return

    command = sys.argv[1]
    if command == "file-status":
        if len(sys.argv) < 3:
            _print_json(_viewer_error("VIEWER_STATUS_ARGS", "读取 Viewer 文件状态需要 project_dir 参数。"))
            return
        _print_json(get_viewer_file_status(sys.argv[2]))
        return

    if command == "load-structure":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_LOAD_ARGS", "读取结构文件需要 project_dir 和 file_kind 参数。"))
            return
        _print_json(load_structure_for_viewer(sys.argv[2], sys.argv[3]))
        return

    if command == "load-screening-candidate":
        if len(sys.argv) < 5:
            _print_json(
                _viewer_error(
                    "VIEWER_SCREENING_CANDIDATE_ARGS",
                    "读取批量配体候选需要 project_dir、candidate_id 和导入 revision。",
                )
            )
            return
        _print_json(
            load_screening_candidate_for_viewer(
                sys.argv[2],
                sys.argv[3],
                sys.argv[4],
            )
        )
        return

    if command == "load-screening-pose":
        if len(sys.argv) < 4:
            _print_json(
                _viewer_error(
                    "VIEWER_SCREENING_POSE_ARGS",
                    "读取批量筛选构象需要 project_dir 和 item_id 参数。",
                )
            )
            return
        requested_mode: int | None = None
        if len(sys.argv) >= 5:
            try:
                requested_mode = int(sys.argv[4])
            except ValueError:
                _print_json(
                    _viewer_error(
                        "VIEWER_SCREENING_MODE_INVALID",
                        "批量筛选构象编号必须是整数。",
                        raw_error=sys.argv[4],
                    )
                )
                return
        _print_json(
            load_screening_pose_for_viewer(
                sys.argv[2],
                sys.argv[3],
                requested_mode,
            )
        )
        return

    if command == "load-screening-archive-pose":
        if len(sys.argv) < 5:
            _print_json(
                _viewer_error(
                    "VIEWER_SCREENING_ARCHIVE_POSE_ARGS",
                    "读取历史筛选构象需要 project_dir、archive_id 和 item_id 参数。",
                )
            )
            return
        requested_mode: int | None = None
        if len(sys.argv) >= 6:
            try:
                requested_mode = int(sys.argv[5])
            except ValueError:
                _print_json(
                    _viewer_error(
                        "VIEWER_SCREENING_MODE_INVALID",
                        "批量筛选构象编号必须是整数。",
                        raw_error=sys.argv[5],
                    )
                )
                return
        _print_json(
            load_archived_screening_pose_for_viewer(
                sys.argv[2],
                sys.argv[3],
                sys.argv[4],
                requested_mode,
            )
        )
        return

    if command == "box-visualization":
        if len(sys.argv) < 3:
            _print_json(_viewer_error("VIEWER_BOX_ARGS", "读取 Box 可视化数据需要 project_dir 参数。"))
            return
        _print_json(get_box_visualization(sys.argv[2]))
        return

    if command == "update-box-visualization":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_BOX_UPDATE_ARGS", "保存 Box 可视化参数需要 project_dir 和 box JSON 参数。"))
            return
        try:
            box_params = json.loads(sys.argv[3])
        except Exception as exc:  # noqa: BLE001 - return structured errors.
            _print_json(_viewer_error("VIEWER_BOX_JSON_INVALID", "Box JSON 格式无效。", raw_error=str(exc)))
            return
        _print_json(update_box_from_visualization(sys.argv[2], box_params))
        return

    if command == "list-poses":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_POSE_LIST_ARGS", "读取 pose 列表需要 project_dir 和 run_id 参数。"))
            return
        _print_json(list_docking_poses(sys.argv[2], sys.argv[3]))
        return

    if command == "load-local-pose-pair":
        if len(sys.argv) < 4:
            _print_json(
                _viewer_error(
                    "VIEWER_LOCAL_POSE_PAIR_ARGS",
                    "读取 local_only 姿势叠合需要 project_dir 和 run_id 参数。",
                )
            )
            return
        _print_json(load_local_only_pose_pair_for_viewer(sys.argv[2], sys.argv[3]))
        return

    if command == "load-pose":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_POSE_LOAD_ARGS", "读取 docking pose 需要 project_dir 和 run_id 参数。"))
            return
        mode = int(sys.argv[4]) if len(sys.argv) >= 5 and sys.argv[4] else None
        pose_kind = sys.argv[5] if len(sys.argv) >= 6 and sys.argv[5] else None
        _print_json(
            load_docking_pose_for_viewer(
                sys.argv[2],
                sys.argv[3],
                mode,
                pose_kind,
            )
        )
        return

    if command == "score-summary":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_SCORE_SUMMARY_ARGS", "读取 pose score 摘要需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_pose_score_summary(sys.argv[2], sys.argv[3]))
        return

    _print_json(_viewer_error("VIEWER_COMMAND_UNKNOWN", f"未知 Viewer 命令：{command}"))


if __name__ == "__main__":
    main()
