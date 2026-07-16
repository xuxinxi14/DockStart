"""Project-local structure file loading helpers for the DockStart viewer."""

from __future__ import annotations

import json
import re
import sys
import csv
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dockstart_core.project import (
    RUN_ID_PATTERN,
    _error,
    _project_from_dict,
    load_project,
    update_box_params,
    validate_box_params,
)
from dockstart_core.viewer_models import DockingPoseSummary, ViewerStructureResult

MAX_VIEWER_FILE_BYTES = 20 * 1024 * 1024

VIEWER_FILE_KINDS = {
    "receptor_raw": "受体原始文件",
    "ligand_raw": "配体原始文件",
    "receptor_prepared": "准备后的受体 PDBQT",
    "ligand_prepared": "准备后的配体 PDBQT",
    "docking_output": "Vina 输出 PDBQT",
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
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


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
            "结构文件超过 20 MB，为避免前端卡顿，本次没有读取内容。",
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
    if file_kind not in VIEWER_FILE_KINDS:
        return _viewer_error(
            "VIEWER_FILE_KIND_INVALID",
            "结构文件类型无效。",
            file_kind=file_kind,
            raw_error=str(file_kind),
            suggestion="请使用 receptor_raw、ligand_raw、receptor_prepared、ligand_prepared 或 docking_output。",
        )

    project, error = _load_project_model(project_dir)
    if error:
        return error

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
            if isinstance(metadata, dict) and metadata.get("output_file"):
                return str(metadata["output_file"]), None
        except Exception:
            # Metadata is helpful but not required for viewer fallback.
            pass

    return Path("runs", run_id, "out.pdbqt").as_posix(), None


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


def load_docking_pose_for_viewer(project_dir: str, run_id: str, mode: int | None = None) -> dict[str, Any]:
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
    if not poses:
        return _viewer_error(
            "VIEWER_POSE_NOT_FOUND",
            "out.pdbqt 中没有可显示的 docking pose。",
            file_kind="docking_output",
            relative_path=relative_path,
            suggestion="请确认 Vina 已成功生成非空 out.pdbqt。",
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
    project, project_error = _load_project_model(project_dir)
    display_pose = (
        _docking_pose_display_content(
            project_dir,
            project,
            run_id,
            str(selected["content"]),
            selected_mode,
        )
        if project_error is None and project is not None
        else None
    )
    if display_pose is not None:
        viewer_content, viewer_format, viewer_warnings = display_pose
    else:
        viewer_content, viewer_format, viewer_warnings = _viewer_content(
            str(selected["content"]),
            validation["format"],
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
        message=f"已读取 docking pose mode {selected_mode}，仅用于几何查看。",
        warnings=[*score_summary.get("warnings", []), *viewer_warnings],
        error=None,
    ).to_dict() | {"run_id": run_id, "mode": selected_mode, "score": selected_score}


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

    if command == "load-pose":
        if len(sys.argv) < 4:
            _print_json(_viewer_error("VIEWER_POSE_LOAD_ARGS", "读取 docking pose 需要 project_dir 和 run_id 参数。"))
            return
        mode = int(sys.argv[4]) if len(sys.argv) >= 5 and sys.argv[4] else None
        _print_json(load_docking_pose_for_viewer(sys.argv[2], sys.argv[3], mode))
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
