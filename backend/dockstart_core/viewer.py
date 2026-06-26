"""Project-local structure file loading helpers for the DockStart viewer."""

from __future__ import annotations

import json
import re
import sys
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

    return ViewerStructureResult(
        ok=True,
        file_kind=file_kind,
        relative_path=relative_path,
        absolute_path=str(absolute_path),
        exists=True,
        format=validation["format"],
        content=content,
        size_bytes=validation["size_bytes"],
        message="结构文件已读取，仅用于 3D 几何查看，不做科学解释。",
        warnings=validation.get("warnings", []),
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
    summaries = [
        DockingPoseSummary(
            mode=int(pose["mode"]),
            relative_path=relative_path,
            size_bytes=len(pose["content"].encode("utf-8")),
            line_count=len(pose["content"].splitlines()),
            message="已识别 docking pose。",
        ).to_dict()
        for pose in poses
    ]
    return {
        "ok": True,
        "project_dir": str(_project_root(project_dir)),
        "run_id": run_id,
        "relative_path": relative_path,
        "format": validation["format"],
        "poses": summaries,
        "message": "Docking pose 列表已读取，仅用于几何查看。",
        "warnings": [] if poses else ["out.pdbqt 中没有识别到可显示的 pose。"],
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

    return ViewerStructureResult(
        ok=True,
        file_kind="docking_output",
        relative_path=relative_path,
        absolute_path=validation["absolute_path"],
        exists=True,
        format=validation["format"],
        content=str(selected["content"]),
        size_bytes=len(str(selected["content"]).encode("utf-8")),
        message=f"已读取 docking pose mode {selected_mode}，仅用于几何查看。",
        warnings=[],
        error=None,
    ).to_dict() | {"run_id": run_id, "mode": selected_mode}


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

    _print_json(_viewer_error("VIEWER_COMMAND_UNKNOWN", f"未知 Viewer 命令：{command}"))


if __name__ == "__main__":
    main()
