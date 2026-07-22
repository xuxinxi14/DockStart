"""Deterministic single-receptor/multi-ligand virtual screening.

This module is deliberately self-contained and does not mutate ``project.json``.
Every job is stored below ``screening/`` and can therefore be removed without
changing the existing single-ligand DockStart workflow.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from adapters import vina_adapter
from dockstart_core.persistence import atomic_write_bytes, atomic_write_json, atomic_write_text
from dockstart_core.preparation import _ligand_preparation_script_text, get_preparation_tool_status
from dockstart_core.screening_models import (
    SCREENING_SCHEMA_VERSION,
    ScreeningItem,
    ScreeningResourceLimits,
    ScreeningStagedInput,
    ScreeningToolSnapshot,
)
from dockstart_core.settings import load_settings


STATE_RELATIVE_PATH = Path("screening", "screening.json")
SCREENING_ROOT = Path("screening")
STAGING_RELATIVE_PATH = Path("screening", "staging")
STAGING_INDEX_RELATIVE_PATH = STAGING_RELATIVE_PATH / "index.json"
ARCHIVE_RELATIVE_PATH = Path("screening", "archive")
ACTIVE_JOB_NAMES = ("inputs", "attempts", "results")
SUMMARY_FIELDS = (
    "item_id",
    "ligand_file",
    "status",
    "attempts",
    "best_affinity_kcal_mol",
    "best_output_file",
    "error",
)
SCORE_ROW = re.compile(
    r"^\s*(\d+)\s+(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s+(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$",
)

Runner = Callable[..., Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(code: str, message: str, raw_error: str = "", suggestion: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "title": message,
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


def _project_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("项目路径不是目录。")
    return root


def _project_file(root: Path, value: str | Path, *, label: str) -> tuple[Path, str]:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于项目目录内。") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} 不是文件。")
    if resolved.suffix.lower() != ".pdbqt":
        raise ValueError(f"{label} 必须是 PDBQT 文件。")
    if resolved.stat().st_size <= 0:
        raise ValueError(f"{label} 为空文件。")
    return resolved, relative.as_posix()


def _pdbqt_file(value: str | Path, *, label: str) -> Path:
    resolved = Path(value).expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} 不是文件。")
    if resolved.suffix.lower() != ".pdbqt":
        raise ValueError(f"{label} 必须是 PDBQT 文件。")
    if resolved.stat().st_size <= 0:
        raise ValueError(f"{label} 为空文件。")
    return resolved


def _vina_executable(value: str) -> str:
    supplied = value.strip()
    if not supplied:
        raise ValueError("未提供 AutoDock Vina 可执行文件。")
    discovered = shutil.which(supplied) if not Path(supplied).is_absolute() else None
    candidate = Path(discovered or supplied).expanduser().resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("AutoDock Vina 路径不是文件。")
    return str(candidate)


def _unused_detection_path(parent: Path) -> str:
    """Return a definitely absent bundled-path override for explicit detection."""

    for index in range(1000):
        candidate = parent / f".dockstart-explicit-vina-{index}.disabled"
        if not candidate.exists():
            return str(candidate)
    raise RuntimeError("无法为显式 Vina 路径建立隔离检测。")


def _resolve_vina_tool(vina_path: str | None) -> ScreeningToolSnapshot:
    requested = str(vina_path or "").strip()
    if requested:
        executable = _vina_executable(requested)
        detection = vina_adapter.detect(
            executable,
            bundled_path=_unused_detection_path(Path(executable).parent),
        )
        source = "explicit"
    else:
        settings = load_settings()
        detection = vina_adapter.detect(settings.tool_paths.vina)
        if detection.status != "ok" or not detection.path:
            raise ValueError(detection.message or "未检测到可用的 AutoDock Vina。")
        executable = _vina_executable(detection.path)
        source = str(detection.source or "auto")

    if detection.status != "ok" or not detection.path:
        detail = detection.raw_error or detection.message or "AutoDock Vina 检测失败。"
        raise ValueError(detail)
    return ScreeningToolSnapshot(
        path=executable,
        version=str(detection.version or ""),
        source=source,
        sha256=_sha256(Path(executable)),
        detection_status=str(detection.status or "unknown"),
    )


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字。")
    return number


def _validate_settings(
    box: dict[str, Any],
    vina: dict[str, Any],
    limits: ScreeningResourceLimits,
) -> tuple[dict[str, float], dict[str, Any]]:
    normalized_box: dict[str, float] = {}
    for axis in "xyz":
        normalized_box[f"center_{axis}"] = _finite_number(box.get(f"center_{axis}"), f"center_{axis}")
        edge = _finite_number(box.get(f"size_{axis}"), f"size_{axis}")
        if edge <= 0 or edge > limits.max_box_edge_angstrom:
            raise ValueError(
                f"size_{axis} 必须大于 0 且不超过 {limits.max_box_edge_angstrom:g} Å。",
            )
        normalized_box[f"size_{axis}"] = edge

    def integer(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = vina.get(name, default)
        if isinstance(raw, bool):
            raise ValueError(f"{name} 必须是整数。")
        try:
            parsed = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是整数。") from exc
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间。")
        return parsed

    scoring = str(vina.get("scoring") or "vina").strip().lower()
    if scoring not in {"vina", "vinardo"}:
        raise ValueError("批量筛选当前仅支持 vina 或 vinardo 评分函数。")
    energy_range = _finite_number(vina.get("energy_range", 3), "energy_range")
    if energy_range < 0 or energy_range > 20:
        raise ValueError("energy_range 必须在 0 到 20 之间。")
    seed_value = vina.get("seed")
    seed = None if seed_value in (None, "") else integer("seed", 0, -2_147_483_648, 2_147_483_647)
    normalized_vina = {
        "scoring": scoring,
        "exhaustiveness": integer("exhaustiveness", 8, 1, limits.max_exhaustiveness),
        "num_modes": integer("num_modes", 9, 1, limits.max_num_modes),
        "energy_range": energy_range,
        "cpu": integer("cpu", 1, 1, limits.max_cpu),
        "seed": seed,
    }
    return normalized_box, normalized_vina


def _state_path(root: Path) -> Path:
    return root / STATE_RELATIVE_PATH


def _write_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    atomic_write_json(_state_path(root), state)


def _read_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.is_file():
        raise FileNotFoundError("没有找到 screening/screening.json。")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != SCREENING_SCHEMA_VERSION:
        raise ValueError("screening.json 的 schema 版本不受支持。")
    if not isinstance(state.get("items"), list) or not isinstance(state.get("queue"), list):
        raise ValueError("screening.json 缺少有效的 items 或 queue。")
    return state


def _copy_snapshot(source: Path, destination: Path) -> None:
    atomic_write_bytes(destination, source.read_bytes())


def _looks_like_pdbqt(path: Path) -> bool:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return any(line.startswith(("ATOM  ", "HETATM")) for line in handle)


def _prepare_raw_screening_ligand(root: Path, source: Path, python_path: str) -> Path:
    """Prepare one SDF/MOL into an isolated staging candidate."""

    staging_root = root / STAGING_RELATIVE_PATH
    staging_root.mkdir(parents=True, exist_ok=True)
    script = staging_root / "prepare_ligand_rdkit_meeko.py"
    if not script.is_file():
        atomic_write_text(script, _ligand_preparation_script_text())
    source_digest = _sha256(source)
    candidate = staging_root / f".{source_digest}.candidate.pdbqt"
    completed = subprocess.run(
        [python_path, "-I", "-B", str(script), str(source), str(candidate)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not candidate.is_file() or not _looks_like_pdbqt(candidate):
        candidate.unlink(missing_ok=True)
        detail = "\n".join(value for value in (completed.stderr.strip(), completed.stdout.strip()) if value)
        raise ValueError(f"{source.name} 自动准备 PDBQT 失败：{detail or '未生成有效 PDBQT'}")
    return candidate


def stage_screening_inputs(
    project_dir: str,
    files: list[str],
    *,
    resource_limits: dict[str, Any] | ScreeningResourceLimits | None = None,
) -> dict[str, Any]:
    """Import PDBQT or prepare SDF/MOL into content-addressed staging."""

    try:
        root = _project_root(project_dir)
        limits = (
            resource_limits
            if isinstance(resource_limits, ScreeningResourceLimits)
            else ScreeningResourceLimits.from_dict(resource_limits)
        )
        if not files:
            raise ValueError("至少需要一个待导入的配体文件。")

        raw_values = [value for value in files if Path(value).suffix.lower() in {".sdf", ".mol"}]
        python_path = ""
        if raw_values:
            tool_status = get_preparation_tool_status(str(root))
            tools = tool_status.get("tools") if isinstance(tool_status.get("tools"), dict) else {}
            python_tool = tools.get("python") if isinstance(tools.get("python"), dict) else {}
            rdkit_tool = tools.get("rdkit") if isinstance(tools.get("rdkit"), dict) else {}
            meeko_tool = tools.get("meeko") if isinstance(tools.get("meeko"), dict) else {}
            if not tool_status.get("ok") or any(tool.get("status") != "ok" for tool in (python_tool, rdkit_tool, meeko_tool)):
                raise ValueError("SDF/MOL 自动准备需要可用的内置 Python、RDKit 与 Meeko。")
            python_path = str(python_tool.get("path") or "")

        sources: list[tuple[Path, Path, str, int]] = []
        unique_bytes: dict[str, int] = {}
        for value in files:
            original = Path(value).expanduser().resolve(strict=True)
            if not original.is_file() or original.stat().st_size <= 0:
                raise ValueError(f"配体文件不可用：{original}")
            suffix = original.suffix.lower()
            if suffix == ".pdbqt":
                source = _pdbqt_file(original, label="筛选输入")
            elif suffix in {".sdf", ".mol"}:
                source = _prepare_raw_screening_ligand(root, original, python_path)
            else:
                raise ValueError(f"暂不支持的批量配体格式：{suffix or '无扩展名'}")
            size_bytes = source.stat().st_size
            if size_bytes > limits.max_staged_file_bytes:
                raise ValueError(
                    f"文件 {source.name} 超过 staging 单文件上限 "
                    f"{limits.max_staged_file_bytes} B。",
                )
            if not _looks_like_pdbqt(source):
                raise ValueError(f"文件 {source.name} 未包含 PDBQT 原子记录。")
            digest = _sha256(source)
            unique_bytes.setdefault(digest, size_bytes)
            sources.append((original, source, digest, size_bytes))
        if sum(unique_bytes.values()) > limits.max_total_input_bytes:
            raise ValueError("待导入文件总大小超过批量筛选资源上限。")

        index_path = root / STAGING_INDEX_RELATIVE_PATH
        index: dict[str, Any] = {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "files": {},
        }
        if index_path.is_file():
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not isinstance(loaded.get("files"), dict):
                raise ValueError("screening/staging/index.json 无效，已拒绝覆盖。")
            index = loaded

        staged: list[dict[str, Any]] = []
        for original, source, digest, size_bytes in sources:
            relative = STAGING_RELATIVE_PATH / f"{digest}.pdbqt"
            destination = root / relative
            if destination.exists() and (
                not destination.is_file()
                or destination.stat().st_size != size_bytes
                or _sha256(destination) != digest
            ):
                raise ValueError(f"staging 目标已存在但内容校验失败：{relative.as_posix()}")
            if not destination.exists():
                _copy_snapshot(source, destination)
                if _sha256(destination) != digest:
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"导入后 SHA256 校验失败：{source.name}")

            record = ScreeningStagedInput(
                file=relative.as_posix(),
                original_name=original.name,
                sha256=digest,
                size_bytes=size_bytes,
            ).to_dict()
            record["source_file"] = str(original)
            record["source_format"] = original.suffix.lower().lstrip(".")
            record["prepared_during_import"] = original.suffix.lower() != ".pdbqt"
            record["staged_at"] = _now_iso()
            index["files"][digest] = record
            staged.append(record)
            if source.name.startswith(".") and source.name.endswith(".candidate.pdbqt"):
                source.unlink(missing_ok=True)

        index["updated_at"] = _now_iso()
        atomic_write_json(index_path, index)
        return {
            "ok": True,
            "project_dir": str(root),
            "staged": staged,
            "message": f"已准备并安全导入 {len(staged)} 个配体 PDBQT 快照。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - workflow boundary returns structured errors.
        return _error(
            "SCREENING_STAGE_ERROR",
            "导入批量筛选配体失败。",
            str(exc),
            "请检查文件格式、大小和项目目录写入权限。",
        )


def _active_job_artifacts(root: Path) -> list[Path]:
    screening_root = root / SCREENING_ROOT
    return [screening_root / name for name in ACTIVE_JOB_NAMES if (screening_root / name).exists()]


def _next_screening_id(root: Path) -> str:
    highest = 0
    archive_root = root / ARCHIVE_RELATIVE_PATH
    if archive_root.is_dir():
        for path in archive_root.iterdir():
            match = re.match(r"^screening_(\d+)(?:_|$)", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"screening_{highest + 1:03d}"


def create_screening(
    project_dir: str,
    receptor_file: str,
    ligand_files: list[str],
    *,
    vina_path: str | None = None,
    box: dict[str, Any],
    vina: dict[str, Any],
    max_retries: int = 1,
    top_n: int = 20,
    resource_limits: dict[str, Any] | ScreeningResourceLimits | None = None,
) -> dict[str, Any]:
    """Create a screening job without changing the legacy project model."""

    try:
        root = _project_root(project_dir)
        if _state_path(root).exists():
            return _error(
                "SCREENING_ALREADY_EXISTS",
                "当前项目已经存在批量筛选任务。",
                str(_state_path(root)),
                "请继续、恢复或归档现有任务后再创建新任务。",
            )
        orphaned = _active_job_artifacts(root)
        if orphaned:
            return _error(
                "SCREENING_ORPHANED_DATA",
                "检测到未归档的批量筛选文件，已拒绝覆盖。",
                ", ".join(str(path) for path in orphaned),
                "请先恢复原任务或人工核对并归档这些文件。",
            )
        limits = (
            resource_limits
            if isinstance(resource_limits, ScreeningResourceLimits)
            else ScreeningResourceLimits.from_dict(resource_limits)
        )
        if max_retries < 0 or max_retries > limits.max_retries:
            raise ValueError(f"max_retries 必须在 0 到 {limits.max_retries} 之间。")
        if not ligand_files:
            raise ValueError("至少需要一个配体 PDBQT。")
        if len(ligand_files) > limits.max_ligands:
            raise ValueError(f"配体数量超过资源上限 {limits.max_ligands}。")
        if top_n < 1 or top_n > limits.max_ligands:
            raise ValueError(f"top_n 必须在 1 到 {limits.max_ligands} 之间。")

        receptor_path, receptor_relative = _project_file(root, receptor_file, label="受体")
        ligand_entries: list[tuple[Path, str]] = []
        seen: set[str] = set()
        for ligand in ligand_files:
            path, relative = _project_file(root, ligand, label="配体")
            key = relative.casefold()
            if key in seen:
                raise ValueError(f"配体列表包含重复文件：{relative}")
            seen.add(key)
            if path.stat().st_size > limits.max_ligand_bytes:
                raise ValueError(f"配体文件超过单文件资源上限：{relative}")
            ligand_entries.append((path, relative))
        ligand_entries.sort(key=lambda entry: (entry[1].casefold(), entry[1]))
        total_bytes = receptor_path.stat().st_size + sum(path.stat().st_size for path, _ in ligand_entries)
        if total_bytes > limits.max_total_input_bytes:
            raise ValueError("受体和配体输入总大小超过批量筛选资源上限。")

        normalized_box, normalized_vina = _validate_settings(box, vina, limits)
        vina_tool = _resolve_vina_tool(vina_path)

        screening_root = root / SCREENING_ROOT
        inputs_root = screening_root / "inputs"
        receptor_snapshot = inputs_root / "receptor.pdbqt"
        _copy_snapshot(receptor_path, receptor_snapshot)
        items: list[ScreeningItem] = []
        for index, (ligand_path, source_relative) in enumerate(ligand_entries, start=1):
            item_id = f"ligand_{index:04d}"
            relative_snapshot = Path("screening", "inputs", "ligands", f"{item_id}.pdbqt")
            snapshot_path = root / relative_snapshot
            _copy_snapshot(ligand_path, snapshot_path)
            items.append(
                ScreeningItem(
                    item_id=item_id,
                    order=index,
                    ligand_file=relative_snapshot.as_posix(),
                    source_file=source_relative,
                    sha256=_sha256(snapshot_path),
                    size_bytes=snapshot_path.stat().st_size,
                ),
            )

        created_at = _now_iso()
        state: dict[str, Any] = {
            "schema_version": SCREENING_SCHEMA_VERSION,
            "screening_id": _next_screening_id(root),
            "status": "ready",
            "created_at": created_at,
            "updated_at": created_at,
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "vina_path": vina_tool.path,
            "tools": {"vina": vina_tool.to_dict()},
            "inputs": {
                "receptor": {
                    "source_file": receptor_relative,
                    "file": Path("screening", "inputs", "receptor.pdbqt").as_posix(),
                    "sha256": _sha256(receptor_snapshot),
                    "size_bytes": receptor_snapshot.stat().st_size,
                },
                "raw_ligand_topology_available": False,
            },
            "box": normalized_box,
            "vina": normalized_vina,
            "max_retries": int(max_retries),
            "top_n": int(top_n),
            "resource_limits": limits.to_dict(),
            "queue": [item.item_id for item in items],
            "items": [item.to_dict() for item in items],
            "outputs": {
                "summary_csv": "",
                "top_n_csv": "",
                "sdf": {
                    "generated": False,
                    "file": "",
                    "reason": "未提供原始配体拓扑；PDBQT 不包含可靠键级，未生成 SDF。",
                },
            },
        }
        _write_state(root, state)
        return {
            "ok": True,
            "project_dir": str(root),
            "state_file": STATE_RELATIVE_PATH.as_posix(),
            "screening": state,
            "message": f"批量筛选任务已创建，共 {len(items)} 个配体。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - CLI/workflow boundary returns structured errors.
        return _error(
            "SCREENING_CREATE_ERROR",
            "创建批量筛选任务失败。",
            str(exc),
            "请检查输入文件、Vina 路径和资源上限。",
        )


def get_screening_status(project_dir: str) -> dict[str, Any]:
    try:
        root = _project_root(project_dir)
        index_path = root / STAGING_INDEX_RELATIVE_PATH
        staged: list[dict[str, Any]] = []
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            files = index.get("files") if isinstance(index, dict) else None
            if isinstance(files, dict):
                staged = [item for item in files.values() if isinstance(item, dict)]
        state = _read_state(root) if _state_path(root).is_file() else None
        return {
            "ok": True,
            "project_dir": str(root),
            "state_file": STATE_RELATIVE_PATH.as_posix() if state else "",
            "screening": state,
            "staged": staged,
            "mode": "batch" if len(staged) > 1 or state else "single",
            "message": "批量筛选状态已读取。" if state else f"已读取 {len(staged)} 个待筛选配体快照。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_READ_ERROR", "读取批量筛选状态失败。", str(exc))


def _safe_rmtree(path: Path, *, expected_parent: Path) -> None:
    resolved_parent = path.parent.resolve(strict=True)
    if resolved_parent != expected_parent.resolve(strict=True):
        raise ValueError(f"拒绝删除 screening 目录之外的路径：{path}")
    if path.is_dir():
        shutil.rmtree(path)


def archive_screening(project_dir: str) -> dict[str, Any]:
    """Archive a terminal job and clear only its active workspace."""

    temporary_archive: Path | None = None
    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state.get("status") not in {"completed", "completed_with_failures", "canceled"}:
            return _error(
                "SCREENING_NOT_TERMINAL",
                "只有已完成或已取消的批量筛选任务可以归档。",
                f"status={state.get('status')}",
                "请先完成任务，或请求取消并等待当前配体结束。",
            )

        archive_root = root / ARCHIVE_RELATIVE_PATH
        archive_root.mkdir(parents=True, exist_ok=True)
        compact_time = re.sub(r"[^0-9]", "", _now_iso())[:14]
        base_name = f"{state['screening_id']}_{compact_time}"
        final_archive = archive_root / base_name
        suffix = 1
        while final_archive.exists():
            final_archive = archive_root / f"{base_name}_{suffix:02d}"
            suffix += 1
        temporary_archive = archive_root / f".{final_archive.name}.tmp"
        if temporary_archive.exists():
            raise ValueError(f"临时归档目录已存在，已拒绝覆盖：{temporary_archive}")
        temporary_archive.mkdir()

        shutil.copy2(_state_path(root), temporary_archive / "screening.json")
        screening_root = root / SCREENING_ROOT
        for name in ACTIVE_JOB_NAMES:
            source = screening_root / name
            if source.is_dir():
                shutil.copytree(source, temporary_archive / name)
        atomic_write_json(
            temporary_archive / "archive_manifest.json",
            {
                "schema_version": 1,
                "screening_id": state["screening_id"],
                "status": state["status"],
                "archived_at": _now_iso(),
                "state_sha256": _sha256(temporary_archive / "screening.json"),
            },
        )
        os.replace(temporary_archive, final_archive)
        temporary_archive = None

        for name in ACTIVE_JOB_NAMES:
            _safe_rmtree(screening_root / name, expected_parent=screening_root)
        _state_path(root).unlink()
        relative_archive = final_archive.relative_to(root).as_posix()
        return {
            "ok": True,
            "project_dir": str(root),
            "archive": relative_archive,
            "screening": state,
            "message": "批量筛选任务已归档，可以创建下一任务。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        if temporary_archive is not None and temporary_archive.is_dir():
            try:
                _safe_rmtree(temporary_archive, expected_parent=temporary_archive.parent)
            except Exception:
                pass
        return _error(
            "SCREENING_ARCHIVE_ERROR",
            "归档批量筛选任务失败。",
            str(exc),
            "原任务不会被新任务覆盖；请检查目录权限后重试。",
        )


def request_screening_cancel(project_dir: str) -> dict[str, Any]:
    """Request cancellation; the active ligand is allowed to finish safely."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] in {"completed", "completed_with_failures", "canceled"}:
            return {
                "ok": True,
                "screening": state,
                "message": "任务已经处于终止状态，无需取消。",
                "error": None,
            }
        state["cancel_requested"] = True
        if state["status"] in {"ready", "interrupted"}:
            state["status"] = "canceled"
            state["finished_at"] = _now_iso()
            message = "批量筛选尚未运行，已立即取消。"
        else:
            state["status"] = "cancel_requested"
            message = "已请求取消；当前配体完成后停止队列。"
        _write_state(root, state)
        return {
            "ok": True,
            "screening": state,
            "message": message,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_CANCEL_ERROR", "请求取消批量筛选失败。", str(exc))


def resume_screening(project_dir: str) -> dict[str, Any]:
    """Recover canceled or interrupted items into their original stable order."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] in {"completed", "completed_with_failures"}:
            return _error("SCREENING_ALREADY_FINISHED", "批量筛选已经完成，不能恢复。")
        for item in state["items"]:
            if item.get("status") != "running":
                continue
            running_attempt = next(
                (
                    attempt
                    for attempt in reversed(item.get("attempts") or [])
                    if attempt.get("status") == "running"
                ),
                None,
            )
            try:
                pid = int((running_attempt or {}).get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0 and vina_adapter.is_process_running(pid):
                return _error(
                    "SCREENING_PROCESS_ACTIVE",
                    "检测到原批量筛选 Vina 进程仍在运行，已拒绝重复启动。",
                    f"item={item.get('item_id')}, pid={pid}",
                    "请等待当前配体结束后再恢复；不要同时启动第二个筛选进程。",
                )
        max_attempts = int(state.get("max_retries", 0)) + 1
        recoverable: list[dict[str, Any]] = []
        for item in state["items"]:
            if item["status"] == "running":
                item["status"] = "interrupted"
                item["last_error"] = "上次运行在状态持久化前中断。"
            if item["status"] in {"pending", "interrupted"}:
                if int(item.get("attempt_count") or 0) < max_attempts:
                    item["status"] = "pending"
                    recoverable.append(item)
                else:
                    item["status"] = "failed"
        state["queue"] = [item["item_id"] for item in sorted(recoverable, key=lambda row: int(row["order"]))]
        state["cancel_requested"] = False
        state["status"] = "ready"
        state["finished_at"] = None
        _write_state(root, state)
        return {
            "ok": True,
            "screening": state,
            "message": f"批量筛选已恢复，队列中有 {len(state['queue'])} 个配体。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error("SCREENING_RESUME_ERROR", "恢复批量筛选失败。", str(exc))


def _config_text(state: dict[str, Any]) -> str:
    box = state["box"]
    vina = state["vina"]
    lines = [
        "receptor = receptor.pdbqt",
        "ligand = ligand.pdbqt",
        f"scoring = {vina['scoring']}",
        "",
        f"center_x = {box['center_x']:g}",
        f"center_y = {box['center_y']:g}",
        f"center_z = {box['center_z']:g}",
        f"size_x = {box['size_x']:g}",
        f"size_y = {box['size_y']:g}",
        f"size_z = {box['size_z']:g}",
        "",
        f"exhaustiveness = {vina['exhaustiveness']}",
        f"num_modes = {vina['num_modes']}",
        f"energy_range = {vina['energy_range']:g}",
        f"cpu = {vina['cpu']}",
    ]
    if vina.get("seed") is not None:
        lines.append(f"seed = {vina['seed']}")
    return "\n".join(lines) + "\n"


def _default_runner(**kwargs: Any) -> dict[str, Any]:
    result = vina_adapter.run_managed(
        kwargs["command"],
        kwargs["cwd"],
        kwargs["stdout_path"],
        kwargs["stderr_path"],
        kwargs["log_path"],
        on_started=kwargs.get("on_started"),
    )
    return {"pid": result.pid, "exit_code": result.exit_code, "error": result.error}


def _runner_result(value: Any) -> tuple[int | None, str, int | None]:
    if isinstance(value, int):
        return value, "", None
    if isinstance(value, dict):
        exit_code = value.get("exit_code")
        return (
            int(exit_code) if exit_code is not None else None,
            str(value.get("error") or ""),
            int(value["pid"]) if value.get("pid") is not None else None,
        )
    exit_code = getattr(value, "exit_code", None)
    return (
        int(exit_code) if exit_code is not None else None,
        str(getattr(value, "error", "") or ""),
        int(getattr(value, "pid")) if getattr(value, "pid", None) is not None else None,
    )


def _best_affinity(log_path: Path) -> float | None:
    if not log_path.is_file():
        return None
    scores: list[float] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SCORE_ROW.match(line)
        if match:
            scores.append(float(match.group(2)))
    return min(scores) if scores else None


def _refresh_cancel(root: Path, state: dict[str, Any]) -> None:
    try:
        current = _read_state(root)
    except Exception:
        return
    if current.get("cancel_requested"):
        state["cancel_requested"] = True


def _attempt_item(root: Path, state: dict[str, Any], item: dict[str, Any], runner: Runner) -> bool:
    item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
    attempt_number = item["attempt_count"]
    attempt_relative = Path("screening", "attempts", item["item_id"], f"attempt_{attempt_number:03d}")
    attempt_dir = root / attempt_relative
    attempt_dir.mkdir(parents=True, exist_ok=False)
    receptor_path = root / state["inputs"]["receptor"]["file"]
    ligand_path = root / item["ligand_file"]
    _copy_snapshot(receptor_path, attempt_dir / "receptor.pdbqt")
    _copy_snapshot(ligand_path, attempt_dir / "ligand.pdbqt")
    atomic_write_text(attempt_dir / "config.txt", _config_text(state))

    output_path = attempt_dir / "out.pdbqt"
    stdout_path = attempt_dir / "stdout.txt"
    stderr_path = attempt_dir / "stderr.txt"
    log_path = attempt_dir / "log.txt"
    command = [state["vina_path"], "--config", "config.txt", "--out", "out.pdbqt"]
    started_at = _now_iso()
    attempt_record: dict[str, Any] = {
        "attempt": attempt_number,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "directory": attempt_relative.as_posix(),
        "command": command,
        "pid": None,
        "exit_code": None,
        "best_affinity_kcal_mol": None,
        "output_file": "",
        "error": "",
    }
    item["status"] = "running"
    item.setdefault("attempts", []).append(attempt_record)
    atomic_write_json(attempt_dir / "attempt.json", attempt_record)
    _write_state(root, state)

    def on_started(pid: int) -> None:
        attempt_record["pid"] = int(pid)
        atomic_write_json(attempt_dir / "attempt.json", attempt_record)
        _refresh_cancel(root, state)
        if state.get("cancel_requested"):
            state["status"] = "cancel_requested"
        _write_state(root, state)

    try:
        returned = runner(
            command=command,
            cwd=attempt_dir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            log_path=log_path,
            output_path=output_path,
            item=dict(item),
            attempt=attempt_number,
            on_started=on_started,
        )
        exit_code, run_error, pid = _runner_result(returned)
    except Exception as exc:  # noqa: BLE001 - external runner failures become item failures.
        exit_code, run_error, pid = None, str(exc), None

    for path in (stdout_path, stderr_path, log_path):
        if not path.exists():
            atomic_write_text(path, "")
    affinity = _best_affinity(log_path)
    success = exit_code == 0 and output_path.is_file() and output_path.stat().st_size > 0 and affinity is not None
    if not success and not run_error:
        if exit_code not in (0, None):
            run_error = f"AutoDock Vina 退出码为 {exit_code}。"
        elif not output_path.is_file() or output_path.stat().st_size == 0:
            run_error = "Vina 未生成非空 out.pdbqt。"
        elif affinity is None:
            run_error = "Vina 日志中没有可解析的 score。"
        else:
            run_error = "Vina 运行失败。"

    finished_at = _now_iso()
    attempt_record.update(
        {
            "status": "succeeded" if success else "failed",
            "finished_at": finished_at,
            "pid": pid if pid is not None else attempt_record.get("pid"),
            "exit_code": exit_code,
            "best_affinity_kcal_mol": affinity,
            "output_file": (
                (attempt_relative / "out.pdbqt").as_posix() if output_path.is_file() else ""
            ),
            "error": run_error,
        },
    )
    if success:
        item["status"] = "succeeded"
        item["best_affinity_kcal_mol"] = affinity
        item["best_output_file"] = attempt_record["output_file"]
        item["last_error"] = ""
    else:
        item["status"] = "failed"
        item["last_error"] = run_error
    atomic_write_json(attempt_dir / "attempt.json", attempt_record)
    _refresh_cancel(root, state)
    return success


def _csv_payload(rows: list[dict[str, Any]], *, ranked: bool = False) -> str:
    fields = (("rank",) + SUMMARY_FIELDS) if ranked else SUMMARY_FIELDS
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for rank, item in enumerate(rows, start=1):
        row = {
            "item_id": item["item_id"],
            "ligand_file": item.get("source_file") or item["ligand_file"],
            "status": item["status"],
            "attempts": item.get("attempt_count", 0),
            "best_affinity_kcal_mol": (
                "" if item.get("best_affinity_kcal_mol") is None else item["best_affinity_kcal_mol"]
            ),
            "best_output_file": item.get("best_output_file") or "",
            "error": item.get("last_error") or "",
        }
        if ranked:
            row["rank"] = rank
        writer.writerow(row)
    return buffer.getvalue()


def _write_summaries(root: Path, state: dict[str, Any]) -> None:
    ordered = sorted(state["items"], key=lambda item: int(item["order"]))
    succeeded = sorted(
        (item for item in ordered if item["status"] == "succeeded"),
        key=lambda item: (float(item["best_affinity_kcal_mol"]), int(item["order"])),
    )
    results_dir = root / "screening" / "results"
    summary_relative = Path("screening", "results", "screening_summary.csv")
    top_relative = Path("screening", "results", "screening_top_n.csv")
    atomic_write_text(results_dir / "screening_summary.csv", _csv_payload(ordered))
    atomic_write_text(
        results_dir / "screening_top_n.csv",
        _csv_payload(succeeded[: int(state["top_n"])], ranked=True),
    )
    state["outputs"]["summary_csv"] = summary_relative.as_posix()
    state["outputs"]["top_n_csv"] = top_relative.as_posix()
    state["outputs"]["sdf"] = {
        "generated": False,
        "file": "",
        "reason": "未提供原始配体拓扑；PDBQT 不包含可靠键级，未生成 SDF。",
    }


def run_screening(
    project_dir: str,
    *,
    runner: Runner | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Run queued ligands serially, retrying without changing queue order."""

    try:
        root = _project_root(project_dir)
        state = _read_state(root)
        if state["status"] not in {"ready"}:
            return _error(
                "SCREENING_NOT_READY",
                f"当前任务状态为 {state['status']}，不能直接运行。",
                suggestion="已取消或中断的任务请先执行 resume。",
            )
        if max_items is not None and max_items < 1:
            raise ValueError("max_items 必须大于 0。")
        active_runner = runner or _default_runner
        state["status"] = "running"
        state["started_at"] = state.get("started_at") or _now_iso()
        _write_state(root, state)
        processed = 0
        max_attempts = int(state.get("max_retries", 0)) + 1

        while state["queue"]:
            _refresh_cancel(root, state)
            if state.get("cancel_requested"):
                state["status"] = "canceled"
                state["finished_at"] = _now_iso()
                break
            item_id = state["queue"].pop(0)
            item = next((row for row in state["items"] if row["item_id"] == item_id), None)
            if item is None:
                raise ValueError(f"队列引用了不存在的项目：{item_id}")
            if item["status"] != "pending":
                continue
            succeeded = _attempt_item(root, state, item, active_runner)
            processed += 1
            if not succeeded and int(item["attempt_count"]) < max_attempts:
                item["status"] = "pending"
                state["queue"].append(item_id)
            _write_state(root, state)
            if max_items is not None and processed >= max_items and state["queue"]:
                state["status"] = "interrupted"
                state["finished_at"] = _now_iso()
                break

        if state.get("cancel_requested") and state["status"] in {"running", "cancel_requested"}:
            state["status"] = "canceled"
            state["finished_at"] = _now_iso()
        elif state["status"] == "running":
            failed = any(item["status"] == "failed" for item in state["items"])
            state["status"] = "completed_with_failures" if failed else "completed"
            state["finished_at"] = _now_iso()
        _write_summaries(root, state)
        _write_state(root, state)
        return {
            "ok": True,
            "project_dir": str(root),
            "screening": state,
            "processed_attempts": processed,
            "message": f"批量筛选当前状态：{state['status']}。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return _error(
            "SCREENING_RUN_ERROR",
            "运行批量筛选失败。",
            str(exc),
            "请读取 screening.json，修复输入后使用 resume 恢复。",
        )


def _json_argument(value: str) -> dict[str, Any]:
    candidate = Path(value).expanduser()
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("JSON 参数必须是对象。")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart 批量虚拟筛选")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--receptor", required=True)
    create.add_argument("--ligand", action="append", required=True)
    create.add_argument("--vina-path")
    create.add_argument("--box-json", type=_json_argument, required=True)
    create.add_argument("--vina-json", type=_json_argument, required=True)
    create.add_argument("--limits-json", type=_json_argument)
    create.add_argument("--max-retries", type=int, default=1)
    create.add_argument("--top-n", type=int, default=20)
    stage = commands.add_parser("stage")
    stage.add_argument("--project", required=True)
    stage.add_argument("--file", action="append", required=True)
    stage.add_argument("--limits-json", type=_json_argument)
    for name in ("status", "run", "cancel", "resume", "archive"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name == "run":
            command.add_argument("--max-items", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "create":
        result = create_screening(
            args.project,
            args.receptor,
            args.ligand,
            vina_path=args.vina_path,
            box=args.box_json,
            vina=args.vina_json,
            max_retries=args.max_retries,
            top_n=args.top_n,
            resource_limits=args.limits_json,
        )
    elif args.command == "stage":
        result = stage_screening_inputs(
            args.project,
            args.file,
            resource_limits=args.limits_json,
        )
    elif args.command == "status":
        result = get_screening_status(args.project)
    elif args.command == "run":
        result = run_screening(args.project, max_items=args.max_items)
    elif args.command == "cancel":
        result = request_screening_cancel(args.project)
    elif args.command == "resume":
        result = resume_screening(args.project)
    else:
        result = archive_screening(args.project)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
