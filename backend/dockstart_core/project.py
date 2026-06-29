"""Project creation and PDBQT import helpers for DockStart."""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adapters import vina_adapter
from dockstart_core.preparation_models import PreparationState, preparation_state_from_dict
from dockstart_core.process_utils import hidden_subprocess_kwargs
from dockstart_core.settings import load_settings

PROJECT_DIRS = ("raw", "prepared", "configs", "runs", "results", "reports", "preparation")
PROJECT_NAME_PATTERN = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]+$")
RUN_ID_PATTERN = re.compile(r"^run_(\d{3,})$")
VINA_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
VINA_SCORE_ROW_PATTERN = re.compile(
    rf"^\s*(\d+)\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})\s+({VINA_NUMBER_PATTERN})(?:\s|$)",
)
SCORES_CSV_FIELDS = ("mode", "affinity_kcal_mol", "rmsd_lb", "rmsd_ub")
DOCKING_SCORE_DISCLAIMER = "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
RUN_REPORT_FILE = "docking_report.md"
PROJECT_REPORT_FILE = Path("reports", "docking_report.md").as_posix()


@dataclass
class ProjectFileRef:
    source: str = ""
    source_id: str = ""
    query_type: str = ""
    downloaded_at: str = ""
    raw_file: str = ""
    file: str = ""


@dataclass
class BoxSettings:
    center_x: float = 0
    center_y: float = 0
    center_z: float = 0
    size_x: float = 20
    size_y: float = 20
    size_z: float = 20


@dataclass
class VinaSettings:
    exhaustiveness: int = 8
    num_modes: int = 9
    energy_range: float = 4
    cpu: int = 0
    seed: int | None = None


@dataclass
class ConfigSettings:
    vina_config_file: str = ""
    generated_at: str = ""


@dataclass
class DockStartProject:
    project_name: str
    created_at: str
    updated_at: str
    project_dir: str
    receptor: ProjectFileRef = field(default_factory=ProjectFileRef)
    ligand: ProjectFileRef = field(default_factory=ProjectFileRef)
    box: BoxSettings = field(default_factory=BoxSettings)
    vina: VinaSettings = field(default_factory=VinaSettings)
    config: ConfigSettings = field(default_factory=ConfigSettings)
    preparation: PreparationState = field(default_factory=PreparationState)
    latest_preparation: dict[str, str] = field(default_factory=lambda: {"receptor": "", "ligand": ""})
    runs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def _success(project: DockStartProject, message: str = "", warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "message": message,
        "warnings": warnings or [],
    }


def _project_json_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def _sanitize_project_name(project_name: str) -> str:
    return project_name.strip()


def _validate_project_name(project_name: str) -> str | None:
    if not project_name:
        return "项目名称不能为空。"
    if project_name in {".", ".."}:
        return "项目名称不能是 . 或 ..。"
    if not PROJECT_NAME_PATTERN.match(project_name):
        return "项目名称不能包含路径分隔符或 Windows 文件名保留字符。"
    return None


def _value_or_default(data: dict[str, Any], key: str, default: Any) -> Any:
    value = data.get(key, default)
    return default if value is None or value == "" else value


def _project_from_dict(data: dict[str, Any], fallback_dir: Path) -> DockStartProject:
    receptor = data.get("receptor") if isinstance(data.get("receptor"), dict) else {}
    ligand = data.get("ligand") if isinstance(data.get("ligand"), dict) else {}
    box = data.get("box") if isinstance(data.get("box"), dict) else {}
    vina = data.get("vina") if isinstance(data.get("vina"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    preparation = data.get("preparation") if isinstance(data.get("preparation"), dict) else {}
    latest_preparation = data.get("latest_preparation") if isinstance(data.get("latest_preparation"), dict) else {}
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    stored_project_dir = str(data.get("project_dir", "") or "").strip()
    if stored_project_dir and Path(stored_project_dir).expanduser().is_absolute():
        project_dir = stored_project_dir
    else:
        project_dir = str(fallback_dir)

    return DockStartProject(
        project_name=str(data.get("project_name", fallback_dir.name) or fallback_dir.name),
        created_at=str(data.get("created_at", "") or _now_iso()),
        updated_at=str(data.get("updated_at", "") or _now_iso()),
        project_dir=project_dir,
        receptor=ProjectFileRef(
            source=str(receptor.get("source", "") or ""),
            source_id=str(receptor.get("source_id", "") or ""),
            query_type=str(receptor.get("query_type", "") or ""),
            downloaded_at=str(receptor.get("downloaded_at", "") or ""),
            raw_file=str(receptor.get("raw_file", "") or ""),
            file=str(receptor.get("file", "") or ""),
        ),
        ligand=ProjectFileRef(
            source=str(ligand.get("source", "") or ""),
            source_id=str(ligand.get("source_id", "") or ""),
            query_type=str(ligand.get("query_type", "") or ""),
            downloaded_at=str(ligand.get("downloaded_at", "") or ""),
            raw_file=str(ligand.get("raw_file", "") or ""),
            file=str(ligand.get("file", "") or ""),
        ),
        box=BoxSettings(
            center_x=float(_value_or_default(box, "center_x", 0)),
            center_y=float(_value_or_default(box, "center_y", 0)),
            center_z=float(_value_or_default(box, "center_z", 0)),
            size_x=float(_value_or_default(box, "size_x", 20)),
            size_y=float(_value_or_default(box, "size_y", 20)),
            size_z=float(_value_or_default(box, "size_z", 20)),
        ),
        vina=VinaSettings(
            exhaustiveness=int(_value_or_default(vina, "exhaustiveness", 8)),
            num_modes=int(_value_or_default(vina, "num_modes", 9)),
            energy_range=float(_value_or_default(vina, "energy_range", 4)),
            cpu=int(_value_or_default(vina, "cpu", 0)),
            seed=None if vina.get("seed", None) in ("", None) else int(vina.get("seed")),
        ),
        config=ConfigSettings(
            vina_config_file=str(config.get("vina_config_file", "") or ""),
            generated_at=str(config.get("generated_at", "") or ""),
        ),
        preparation=preparation_state_from_dict(preparation),
        latest_preparation={
            "receptor": str(latest_preparation.get("receptor", "") or ""),
            "ligand": str(latest_preparation.get("ligand", "") or ""),
        },
        runs=runs,
    )


def ensure_project_structure(project_dir: str | Path) -> dict[str, Any]:
    try:
        path = Path(project_dir).expanduser()
        for directory in PROJECT_DIRS:
            (path / directory).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "project_dir": str(path), "error": None}
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_STRUCTURE_ERROR",
            "创建项目目录结构时发生错误。",
            str(exc),
            "请确认项目保存目录存在且有写入权限。",
        )


def save_project(project: DockStartProject) -> dict[str, Any]:
    try:
        project_dir = Path(project.project_dir).expanduser()
        structure = ensure_project_structure(project_dir)
        if not structure.get("ok"):
            return structure
        project.updated_at = _now_iso()
        _project_json_path(project_dir).write_text(
            json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return _success(project, "项目已保存。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_SAVE_ERROR",
            "保存 project.json 时发生错误。",
            str(exc),
            "请确认项目目录可写。",
        )


def create_project(project_name: str, base_dir: str) -> dict[str, Any]:
    safe_name = _sanitize_project_name(project_name)
    name_error = _validate_project_name(safe_name)
    if name_error:
        return _error("INVALID_PROJECT_NAME", name_error, suggestion="请使用普通文件夹名称作为项目名。")

    if not base_dir.strip():
        return _error("BASE_DIR_REQUIRED", "项目保存目录不能为空。", suggestion="请输入一个可写入的父目录。")

    base_path = Path(base_dir).expanduser()
    project_dir = base_path / safe_name

    if project_dir.exists():
        return _error(
            "PROJECT_DIR_EXISTS",
            "项目目录已存在，DockStart 不会覆盖已有项目。",
            suggestion="请更换项目名称，或选择另一个保存目录。",
        )

    try:
        project_dir.mkdir(parents=True, exist_ok=False)
        structure = ensure_project_structure(project_dir)
        if not structure.get("ok"):
            return structure

        created_at = _now_iso()
        project = DockStartProject(
            project_name=safe_name,
            created_at=created_at,
            updated_at=created_at,
            project_dir=str(project_dir),
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "项目创建成功。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CREATE_ERROR",
            "创建项目时发生错误。",
            str(exc),
            "请确认保存目录存在且有写入权限。",
        )


def load_project(project_dir: str) -> dict[str, Any]:
    try:
        path = Path(project_dir).expanduser()
        project_json = _project_json_path(path)
        if not project_json.exists():
            return _error(
                "PROJECT_JSON_NOT_FOUND",
                "没有找到 project.json，无法读取 DockStart 项目。",
                suggestion="请确认选择的是 DockStart 项目目录。",
            )

        data = json.loads(project_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _error("PROJECT_JSON_INVALID", "project.json 格式不是 JSON 对象。")

        project = _project_from_dict(data, path)
        return _success(project, "项目读取成功。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_LOAD_ERROR",
            "读取项目时发生错误。",
            str(exc),
            "请检查 project.json 是否完整。",
        )


def validate_pdbqt_file(path: str) -> dict[str, Any]:
    file_path = Path(path).expanduser()

    if not file_path.exists():
        return _error(
            "PDBQT_FILE_NOT_FOUND",
            "没有找到 PDBQT 文件，请检查输入路径。",
            suggestion="请确认文件路径正确，或重新选择 .pdbqt 文件。",
        )
    if not file_path.is_file():
        return _error("PDBQT_PATH_NOT_FILE", "PDBQT 路径不是一个文件。")
    if file_path.suffix.lower() != ".pdbqt":
        return _error(
            "PDBQT_EXTENSION_INVALID",
            "文件扩展名不是 .pdbqt。",
            suggestion="第一版只支持已经准备好的 receptor.pdbqt 和 ligand.pdbqt。",
        )
    if file_path.stat().st_size == 0:
        return _error(
            "PDBQT_FILE_EMPTY",
            "PDBQT 文件为空，无法导入。",
            suggestion="请确认该文件是 AutoDock Vina 可用的 PDBQT 文件。",
        )

    return {"ok": True, "path": str(file_path), "error": None}


def _import_pdbqt(project_dir: str, source_path: str, role: str) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("PDBQT_ROLE_INVALID", "PDBQT 导入类型无效。")

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    validation = validate_pdbqt_file(source_path)
    if not validation.get("ok"):
        return validation

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        target_relative = f"prepared/{role}.pdbqt"
        target_path = Path(project.project_dir).expanduser() / "prepared" / f"{role}.pdbqt"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(source_path).expanduser(), target_path)

        file_ref = getattr(project, role)
        file_ref.source = "local"
        file_ref.file = target_relative

        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        label = "受体" if role == "receptor" else "配体"
        return _success(project, f"{label} PDBQT 已导入。")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PDBQT_IMPORT_ERROR",
            "导入 PDBQT 文件时发生错误。",
            str(exc),
            "请确认项目目录可写，源文件未被其他程序锁定。",
        )


def import_receptor_pdbqt(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "receptor")


def import_ligand_pdbqt(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_pdbqt(project_dir, source_path, "ligand")


def _parse_box_number(box: dict[str, Any], key: str) -> tuple[float | None, dict[str, Any] | None]:
    value = box.get(key)
    if value is None:
        return None, _error(
            "BOX_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的对接箱体参数。",
        )
    if isinstance(value, str) and not value.strip():
        return None, _error(
            "BOX_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的对接箱体参数。",
        )
    if isinstance(value, bool):
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 必须是数字。",
            suggestion="请输入普通数字，例如 0、-12.5 或 20。",
        )

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 必须是数字。",
            str(exc),
            "请输入普通数字，例如 0、-12.5 或 20。",
        )

    if not math.isfinite(number):
        return None, _error(
            "BOX_PARAM_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            suggestion="请输入有限数字。",
        )
    return number, None


def validate_box_params(box: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(box, dict):
        return _error(
            "BOX_PARAMS_INVALID",
            "Box 参数格式无效。",
            suggestion="请提交包含 center_x/y/z 和 size_x/y/z 的对象。",
        )

    parsed: dict[str, float] = {}
    for key in ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z"):
        value, error = _parse_box_number(box, key)
        if error:
            return error
        parsed[key] = value if value is not None else 0

    for key in ("size_x", "size_y", "size_z"):
        if parsed[key] <= 0:
            return _error(
                "BOX_SIZE_NOT_POSITIVE",
                f"{key} 必须大于 0。",
                suggestion="对接箱体尺寸必须是正数，单位为 Å。",
            )

    warnings: list[str] = []
    if any(parsed[key] > 60 for key in ("size_x", "size_y", "size_z")):
        warnings.append("对接箱体尺寸较大，可能导致搜索变慢或结果不稳定，请确认是否覆盖了合理结合区域。")

    return {
        "ok": True,
        "box": parsed,
        "warnings": warnings,
        "error": None,
    }


def get_box_params(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "box": project.box.to_dict() if hasattr(project.box, "to_dict") else asdict(project.box),
        "warnings": [],
        "message": "Box 参数读取成功。",
    }


def update_box_params(project_dir: str, box: dict[str, Any]) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    validation = validate_box_params(box)
    if not validation.get("ok"):
        return validation

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        parsed_box = validation["box"]
        project.box = BoxSettings(
            center_x=parsed_box["center_x"],
            center_y=parsed_box["center_y"],
            center_z=parsed_box["center_z"],
            size_x=parsed_box["size_x"],
            size_y=parsed_box["size_y"],
            size_z=parsed_box["size_z"],
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "Box 参数已保存。", validation.get("warnings", []))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "BOX_UPDATE_ERROR",
            "保存 Box 参数时发生错误。",
            str(exc),
            "请确认 project.json 可写。",
        )


def _parse_vina_int(
    vina: dict[str, Any],
    key: str,
    *,
    allow_zero: bool = False,
) -> tuple[int | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, _error(
            "VINA_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的 Vina 参数；seed 可以留空。",
        )
    if isinstance(value, bool):
        return None, _error(
            "VINA_PARAM_INTEGER_REQUIRED",
            f"{key} 必须是整数。",
            suggestion="请输入整数，例如 8、9 或 0。",
        )

    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None, _error(
                "VINA_PARAM_INVALID",
                f"{key} 不能是 NaN 或 Infinity。",
                suggestion="请输入有限数字。",
            )
        if not value.is_integer():
            return None, _error(
                "VINA_PARAM_INTEGER_REQUIRED",
                f"{key} 必须是整数。",
                suggestion="请输入整数，不要输入小数。",
            )
        number = int(value)
    else:
        try:
            number = int(str(value).strip(), 10)
        except (TypeError, ValueError) as exc:
            return None, _error(
                "VINA_PARAM_INTEGER_REQUIRED",
                f"{key} 必须是整数。",
                str(exc),
                "请输入整数，例如 8、9 或 0。",
            )

    if allow_zero:
        if number < 0:
            return None, _error(
                "VINA_PARAM_NON_NEGATIVE_REQUIRED",
                f"{key} 必须是非负整数。",
                suggestion="cpu 可以填写 0 表示自动，或填写一个正整数。",
            )
    elif number <= 0:
        return None, _error(
            "VINA_PARAM_POSITIVE_REQUIRED",
            f"{key} 必须是正整数。",
            suggestion="请输入大于 0 的整数。",
        )

    return number, None


def _parse_vina_positive_float(
    vina: dict[str, Any],
    key: str,
) -> tuple[float | None, dict[str, Any] | None]:
    value = vina.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, _error(
            "VINA_PARAM_REQUIRED",
            f"{key} 不能为空。",
            suggestion="请填写完整的 Vina 参数；seed 可以留空。",
        )
    if isinstance(value, bool):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            suggestion="请输入正数，例如 3、4 或 7.5。",
        )

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 必须是数字。",
            str(exc),
            "请输入正数，例如 3、4 或 7.5。",
        )

    if not math.isfinite(number):
        return None, _error(
            "VINA_PARAM_INVALID",
            f"{key} 不能是 NaN 或 Infinity。",
            suggestion="请输入有限数字。",
        )
    if number <= 0:
        return None, _error(
            "VINA_PARAM_POSITIVE_REQUIRED",
            f"{key} 必须是正数。",
            suggestion="energy_range 需要大于 0，单位为 kcal/mol。",
        )

    return number, None


def _parse_vina_seed(vina: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    value = vina.get("seed")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, _error(
            "VINA_SEED_INTEGER_REQUIRED",
            "seed 必须是整数，或留空。",
            suggestion="留空表示随机；填写整数可提高复现性。",
        )

    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None, _error(
                "VINA_PARAM_INVALID",
                "seed 不能是 NaN 或 Infinity。",
                suggestion="seed 可以留空，或填写一个整数。",
            )
        if not value.is_integer():
            return None, _error(
                "VINA_SEED_INTEGER_REQUIRED",
                "seed 必须是整数，或留空。",
                suggestion="留空表示随机；填写整数可提高复现性。",
            )
        return int(value), None

    try:
        return int(str(value).strip(), 10), None
    except (TypeError, ValueError) as exc:
        return None, _error(
            "VINA_SEED_INTEGER_REQUIRED",
            "seed 必须是整数，或留空。",
            str(exc),
            "留空表示随机；填写整数可提高复现性。",
        )


def validate_vina_params(vina: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(vina, dict):
        return _error(
            "VINA_PARAMS_INVALID",
            "Vina 参数格式无效。",
            suggestion="请提交包含 exhaustiveness、num_modes、energy_range、cpu 和 seed 的对象。",
        )

    exhaustiveness, error = _parse_vina_int(vina, "exhaustiveness")
    if error:
        return error
    num_modes, error = _parse_vina_int(vina, "num_modes")
    if error:
        return error
    energy_range, error = _parse_vina_positive_float(vina, "energy_range")
    if error:
        return error
    cpu, error = _parse_vina_int(vina, "cpu", allow_zero=True)
    if error:
        return error
    seed, error = _parse_vina_seed(vina)
    if error:
        return error

    parsed = {
        "exhaustiveness": exhaustiveness,
        "num_modes": num_modes,
        "energy_range": energy_range,
        "cpu": cpu,
        "seed": seed,
    }

    warnings: list[str] = []
    if exhaustiveness is not None and exhaustiveness > 64:
        warnings.append("exhaustiveness 较高，可能显著增加运行时间；新手建议从 8 开始。")
    if num_modes is not None and num_modes > 50:
        warnings.append("num_modes 较多，结果文件和解析成本可能增加；请确认确实需要这么多构象。")
    if energy_range is not None and energy_range > 10:
        warnings.append("energy_range 较大，可能保留较多高能构象；新手建议 3 或 4。")
    if cpu == 0:
        warnings.append("cpu 设置为 0，将由 Vina 自动决定或使用默认 CPU 设置。")

    return {
        "ok": True,
        "vina": parsed,
        "warnings": warnings,
        "error": None,
    }


def get_vina_params(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "vina": asdict(project.vina),
        "warnings": [],
        "message": "Vina 参数读取成功。",
    }


def update_vina_params(project_dir: str, vina: dict[str, Any]) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    validation = validate_vina_params(vina)
    if not validation.get("ok"):
        return validation

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        parsed_vina = validation["vina"]
        project.vina = VinaSettings(
            exhaustiveness=parsed_vina["exhaustiveness"],
            num_modes=parsed_vina["num_modes"],
            energy_range=parsed_vina["energy_range"],
            cpu=parsed_vina["cpu"],
            seed=parsed_vina["seed"],
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return _success(project, "Vina 参数已保存。", validation.get("warnings", []))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_UPDATE_ERROR",
            "保存 Vina 参数时发生错误。",
            str(exc),
            "请确认 project.json 可写。",
        )


def _format_config_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)


def _project_relative_file(project_dir: Path, relative_path: str, role_label: str) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{role_label.upper()}_FILE_NOT_SET",
            f"{role_label} PDBQT 文件尚未导入，无法生成 Vina 配置文件。",
            suggestion=f"请先回到 PDBQT 导入页，导入 {role_label}.pdbqt。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{role_label.upper()}_FILE_PATH_NOT_RELATIVE",
            f"{role_label} 文件路径必须是项目内相对路径。",
            suggestion="请重新导入 PDBQT 文件，让 DockStart 使用 prepared 目录中的副本。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{role_label.upper()}_FILE_OUTSIDE_PROJECT",
            f"{role_label} 文件路径指向项目目录外，无法写入配置文件。",
            suggestion="请重新导入 PDBQT 文件，让 DockStart 使用项目目录内的 prepared 文件。",
        )
    if not file_path.exists():
        return None, _error(
            f"{role_label.upper()}_FILE_NOT_FOUND",
            f"没有找到准备后的 {role_label}.pdbqt 文件。",
            raw_error=str(file_path),
            suggestion=f"请确认 {relative_path} 是否存在，或重新导入 {role_label}.pdbqt。",
        )
    if not file_path.is_file():
        return None, _error(
            f"{role_label.upper()}_PATH_NOT_FILE",
            f"{role_label} PDBQT 路径不是一个文件。",
            raw_error=str(file_path),
            suggestion="请重新导入 PDBQT 文件。",
        )

    return file_path, None


def _project_file_exists_non_empty(project_dir: Path, relative_path: str) -> bool:
    if not relative_path:
        return False
    path = Path(relative_path)
    if path.is_absolute():
        return path.is_file() and path.stat().st_size > 0
    resolved = (project_dir.resolve() / path).resolve()
    try:
        resolved.relative_to(project_dir.resolve())
    except ValueError:
        return False
    return resolved.is_file() and resolved.stat().st_size > 0


def _prepared_input_hint(project: DockStartProject, project_dir: Path, target: str, fallback_error: dict[str, Any]) -> dict[str, Any]:
    target_label = "受体" if target == "receptor" else "配体"
    file_ref = getattr(project, target)
    preparation_result = getattr(project.preparation, target)
    raw_file = str(file_ref.raw_file or "")
    fallback_code = fallback_error.get("error", {}).get("code", f"{target.upper()}_FILE_NOT_READY")

    if preparation_result.status == "failed":
        prep_error = preparation_result.error if isinstance(preparation_result.error, dict) else {}
        return _error(
            f"{target.upper()}_PREPARATION_FAILED",
            f"{target_label} PDBQT 自动准备上次失败，请先查看 preparation 日志。",
            raw_error=str(prep_error.get("raw_error") or preparation_result.log_file or raw_file),
            suggestion=f"请回到自动准备页面查看 {target} preparation 日志，修复后重新准备或手动导入 prepared/{target}.pdbqt。",
        )

    if raw_file and fallback_code in {f"{target.upper()}_FILE_NOT_SET", f"{target.upper()}_FILE_NOT_FOUND"}:
        if _project_file_exists_non_empty(project_dir, raw_file):
            return _error(
                f"{target.upper()}_PDBQT_NOT_PREPARED",
                f"已下载 raw {target}，但尚未准备 prepared/{target}.pdbqt。",
                raw_error=raw_file,
                suggestion=f"请前往自动准备页面生成 prepared/{target}.pdbqt，或手动导入已经准备好的 {target}.pdbqt。",
            )

    return fallback_error


def validate_config_prerequisites(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    try:
        project = _project_from_dict(loaded["project"], project_path)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_CONFIG_INVALID",
            "项目配置格式不完整，无法生成 Vina 配置文件。",
            str(exc),
            "请检查 project.json 中 receptor、ligand、box 和 vina 字段是否完整。",
        )

    _, receptor_error = _project_relative_file(project_path, project.receptor.file, "receptor")
    if receptor_error:
        return _prepared_input_hint(project, project_path, "receptor", receptor_error)
    _, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        return _prepared_input_hint(project, project_path, "ligand", ligand_error)

    box_validation = validate_box_params(asdict(project.box))
    if not box_validation.get("ok"):
        return box_validation

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        return vina_validation

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "box": box_validation["box"],
        "vina": vina_validation["vina"],
        "warnings": vina_validation.get("warnings", []) + box_validation.get("warnings", []),
        "error": None,
    }


def build_vina_config_text(project_dir: str) -> dict[str, Any]:
    prerequisites = validate_config_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    project = _project_from_dict(prerequisites["project"], Path(project_dir).expanduser())
    box = prerequisites["box"]
    vina = prerequisites["vina"]
    lines = [
        f"receptor = {Path(project.receptor.file).as_posix()}",
        f"ligand = {Path(project.ligand.file).as_posix()}",
        "",
        f"center_x = {_format_config_number(box['center_x'])}",
        f"center_y = {_format_config_number(box['center_y'])}",
        f"center_z = {_format_config_number(box['center_z'])}",
        "",
        f"size_x = {_format_config_number(box['size_x'])}",
        f"size_y = {_format_config_number(box['size_y'])}",
        f"size_z = {_format_config_number(box['size_z'])}",
        "",
        f"exhaustiveness = {vina['exhaustiveness']}",
        f"num_modes = {vina['num_modes']}",
        f"energy_range = {_format_config_number(vina['energy_range'])}",
        f"cpu = {vina['cpu']}",
    ]
    if vina["seed"] is not None:
        lines.append(f"seed = {vina['seed']}")

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "config_file": "configs/vina_config.txt",
        "config_text": "\n".join(lines) + "\n",
        "warnings": prerequisites.get("warnings", []),
        "message": "Vina 配置预览已生成。",
        "error": None,
    }


def get_vina_config_preview(project_dir: str) -> dict[str, Any]:
    return build_vina_config_text(project_dir)


def generate_vina_config(project_dir: str) -> dict[str, Any]:
    preview = build_vina_config_text(project_dir)
    if not preview.get("ok"):
        return preview

    try:
        project = _project_from_dict(preview["project"], Path(project_dir).expanduser())
        config_relative = "configs/vina_config.txt"
        config_path = Path(project.project_dir).expanduser() / "configs" / "vina_config.txt"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(preview["config_text"], encoding="utf-8")

        generated_at = _now_iso()
        project.config.vina_config_file = config_relative
        project.config.generated_at = generated_at
        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "config_file": config_relative,
            "config_text": preview["config_text"],
            "warnings": preview.get("warnings", []),
            "message": "vina_config.txt 已生成。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "VINA_CONFIG_WRITE_ERROR",
            "写入 vina_config.txt 时发生错误。",
            str(exc),
            "请确认项目 configs 目录可写。",
        )


def _run_check(
    key: str,
    name: str,
    status: str,
    message: str,
    path: str = "",
    version: str = "",
    raw_error: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "status": status,
        "message": message,
        "path": path,
        "version": version,
        "raw_error": raw_error,
    }


def _run_error(
    code: str,
    message: str,
    checks: list[dict[str, Any]],
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    payload = _error(code, message, raw_error, suggestion)
    payload["checks"] = checks
    payload["warnings"] = []
    return payload


def _config_relative_path(project: DockStartProject) -> str:
    return project.config.vina_config_file or "configs/vina_config.txt"


def _project_relative_existing_file(
    project_dir: Path,
    relative_path: str,
    error_prefix: str,
    display_name: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{error_prefix}_NOT_SET",
            f"{display_name} 尚未生成或记录在 project.json 中。",
            suggestion=f"请先完成对应步骤，确认项目中存在 {display_name}。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{error_prefix}_PATH_NOT_RELATIVE",
            f"{display_name} 路径必须是项目内相对路径。",
            suggestion="请重新生成配置文件，避免使用用户机器上的绝对路径。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{error_prefix}_OUTSIDE_PROJECT",
            f"{display_name} 路径指向项目目录外，无法用于可复现运行记录。",
            raw_error=str(file_path),
            suggestion="请重新生成配置文件，让路径保留在项目目录内。",
        )

    if not file_path.exists():
        return None, _error(
            f"{error_prefix}_NOT_FOUND",
            f"没有找到 {display_name}。",
            raw_error=str(file_path),
            suggestion=f"请先生成或恢复 {relative_path}。",
        )
    if not file_path.is_file():
        return None, _error(
            f"{error_prefix}_PATH_NOT_FILE",
            f"{display_name} 路径不是一个文件。",
            raw_error=str(file_path),
            suggestion="请检查项目文件结构。",
        )

    return file_path, None


def _status_from_error_code(code: str) -> str:
    return "missing" if code.endswith("_NOT_SET") or code.endswith("_NOT_FOUND") or code.endswith("_NOT_PREPARED") else "error"


def _build_vina_command(
    vina_path: str,
    config_file: str,
    run_id: str,
) -> list[str]:
    return [
        vina_path or "vina",
        "--config",
        Path(config_file).as_posix(),
        "--out",
        Path("runs", run_id, "out.pdbqt").as_posix(),
    ]


def _format_command_preview(command: list[str]) -> str:
    return " ".join(f'"{part}"' if any(char.isspace() for char in part) else part for part in command)


def validate_run_prerequisites(project_dir: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        error = loaded.get("error", {})
        checks.append(
            _run_check(
                "project_json",
                "project.json",
                "missing",
                error.get("message", "没有找到 project.json。"),
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(
            error.get("code", "PROJECT_JSON_NOT_FOUND"),
            error.get("message", "没有找到 project.json，无法准备运行记录。"),
            checks,
            error.get("raw_error", ""),
            error.get("suggestion", "请先创建或选择 DockStart 项目。"),
        )

    project_path = Path(project_dir).expanduser()
    project = _project_from_dict(loaded["project"], project_path)
    checks.append(_run_check("project_json", "project.json", "ok", "已读取项目配置。", "project.json"))

    receptor_path, receptor_error = _project_relative_file(project_path, project.receptor.file, "receptor")
    if receptor_error:
        receptor_error = _prepared_input_hint(project, project_path, "receptor", receptor_error)
        error = receptor_error["error"]
        checks.append(
            _run_check(
                "receptor",
                "receptor.pdbqt",
                _status_from_error_code(error["code"]),
                error["message"],
                project.receptor.file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    if receptor_path and receptor_path.stat().st_size == 0:
        checks.append(
            _run_check(
                "receptor",
                "receptor.pdbqt",
                "error",
                "受体 PDBQT 文件为空，无法准备运行记录。",
                project.receptor.file,
                raw_error=str(receptor_path),
            ),
        )
        return _run_error(
            "RECEPTOR_FILE_EMPTY",
            "受体 PDBQT 文件为空，无法准备运行记录。",
            checks,
            str(receptor_path),
            "请重新导入非空的 receptor.pdbqt。",
        )
    checks.append(_run_check("receptor", "receptor.pdbqt", "ok", "已找到受体 PDBQT 文件。", project.receptor.file))

    ligand_path, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        ligand_error = _prepared_input_hint(project, project_path, "ligand", ligand_error)
        error = ligand_error["error"]
        checks.append(
            _run_check(
                "ligand",
                "ligand.pdbqt",
                _status_from_error_code(error["code"]),
                error["message"],
                project.ligand.file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    if ligand_path and ligand_path.stat().st_size == 0:
        checks.append(
            _run_check(
                "ligand",
                "ligand.pdbqt",
                "error",
                "配体 PDBQT 文件为空，无法准备运行记录。",
                project.ligand.file,
                raw_error=str(ligand_path),
            ),
        )
        return _run_error(
            "LIGAND_FILE_EMPTY",
            "配体 PDBQT 文件为空，无法准备运行记录。",
            checks,
            str(ligand_path),
            "请重新导入非空的 ligand.pdbqt。",
        )
    checks.append(_run_check("ligand", "ligand.pdbqt", "ok", "已找到配体 PDBQT 文件。", project.ligand.file))

    config_file = _config_relative_path(project)
    config_path, config_error = _project_relative_existing_file(project_path, config_file, "VINA_CONFIG", "configs/vina_config.txt")
    if config_error:
        error = config_error["error"]
        checks.append(
            _run_check(
                "vina_config",
                "vina_config.txt",
                _status_from_error_code(error["code"]),
                error["message"],
                config_file,
                raw_error=error.get("raw_error", ""),
            ),
        )
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("vina_config", "vina_config.txt", "ok", "已找到 Vina 配置文件。", config_file))

    box_validation = validate_box_params(asdict(project.box))
    if not box_validation.get("ok"):
        error = box_validation["error"]
        checks.append(_run_check("box", "Box 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("box", "Box 参数", "ok", "Box 参数格式有效。"))

    vina_validation = validate_vina_params(asdict(project.vina))
    if not vina_validation.get("ok"):
        error = vina_validation["error"]
        checks.append(_run_check("vina_params", "Vina 参数", "error", error["message"], raw_error=error.get("raw_error", "")))
        return _run_error(error["code"], error["message"], checks, error.get("raw_error", ""), error.get("suggestion", ""))
    checks.append(_run_check("vina_params", "Vina 参数", "ok", "Vina 参数格式有效。"))

    settings = load_settings()
    vina_detection = vina_adapter.detect(settings.tool_paths.vina)
    vina_dict = vina_detection.to_dict()
    if vina_detection.status != "ok":
        checks.append(
            _run_check(
                "vina",
                "AutoDock Vina",
                vina_detection.status,
                vina_detection.message or "未检测到 AutoDock Vina，无法准备运行命令。",
                vina_detection.path,
                vina_detection.version,
                vina_detection.raw_error,
            ),
        )
        return _run_error(
            "VINA_NOT_AVAILABLE",
            vina_detection.message or "未检测到 AutoDock Vina，无法准备运行命令。",
            checks,
            vina_detection.raw_error,
            "请先在工具路径设置中配置 vina.exe，或确认 vina/vina.exe 已加入 PATH。",
        )
    checks.append(
        _run_check(
            "vina",
            "AutoDock Vina",
            "ok",
            vina_detection.message or "已检测到 AutoDock Vina。",
            vina_detection.path,
            vina_detection.version,
            vina_detection.raw_error,
        ),
    )

    next_run_id = get_next_run_id(project.project_dir)
    command = _build_vina_command(vina_detection.path, config_file, next_run_id)
    warnings = box_validation.get("warnings", []) + vina_validation.get("warnings", [])
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "checks": checks,
        "warnings": warnings,
        "config_file": Path(config_file).as_posix(),
        "config_path": str(config_path) if config_path else "",
        "next_run_id": next_run_id,
        "vina": vina_dict,
        "vina_path": vina_detection.path,
        "vina_version": vina_detection.version,
        "command": command,
        "command_preview": _format_command_preview(command),
        "message": "运行前检查通过，可以准备运行记录。",
        "error": None,
    }


def get_next_run_id(project_dir: str) -> str:
    project_path = Path(project_dir).expanduser()
    numbers: set[int] = set()
    runs_dir = project_path / "runs"
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            match = RUN_ID_PATTERN.match(child.name)
            if child.is_dir() and match:
                numbers.add(int(match.group(1)))

    project_json = _project_json_path(project_path)
    if project_json.exists():
        try:
            data = json.loads(project_json.read_text(encoding="utf-8"))
            for item in data.get("runs", []):
                if isinstance(item, dict):
                    match = RUN_ID_PATTERN.match(str(item.get("run_id", "")))
                    if match:
                        numbers.add(int(match.group(1)))
        except Exception:
            pass

    next_number = max(numbers, default=0) + 1
    return f"run_{next_number:03d}"


def build_vina_command_preview(project_dir: str, run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用 DockStart 自动生成的 run_id。",
        )

    prerequisites = validate_run_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    command = _build_vina_command(prerequisites["vina_path"], prerequisites["config_file"], run_id)
    return {
        "ok": True,
        "project_dir": prerequisites["project_dir"],
        "project": prerequisites["project"],
        "run_id": run_id,
        "command": command,
        "command_preview": _format_command_preview(command),
        "checks": prerequisites.get("checks", []),
        "warnings": prerequisites.get("warnings", []),
        "message": "Vina 命令预览已生成；当前版本不会执行该命令。",
        "error": None,
    }


def prepare_vina_run(project_dir: str) -> dict[str, Any]:
    prerequisites = validate_run_prerequisites(project_dir)
    if not prerequisites.get("ok"):
        return prerequisites

    project = _project_from_dict(prerequisites["project"], Path(project_dir).expanduser())
    run_id = prerequisites["next_run_id"]
    run_dir = Path(project.project_dir).expanduser() / "runs" / run_id
    if run_dir.exists():
        return _run_error(
            "RUN_DIR_EXISTS",
            f"{run_id} 已存在，DockStart 不会覆盖已有运行目录。",
            prerequisites.get("checks", []),
            str(run_dir),
            "请重新检查 runs 目录，或保留已有运行记录后再次准备。",
        )

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        created_at = _now_iso()
        config_file = prerequisites["config_file"]
        config_path = Path(project.project_dir).expanduser() / config_file
        config_text = config_path.read_text(encoding="utf-8")

        metadata_file = Path("runs", run_id, "metadata.json").as_posix()
        command_preview_file = Path("runs", run_id, "command_preview.txt").as_posix()
        config_snapshot_file = Path("runs", run_id, "config_snapshot.txt").as_posix()
        output_file = Path("runs", run_id, "out.pdbqt").as_posix()
        log_file = Path("runs", run_id, "log.txt").as_posix()
        command = _build_vina_command(prerequisites["vina_path"], config_file, run_id)

        metadata = {
            "run_id": run_id,
            "status": "prepared",
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "vina_version": prerequisites.get("vina_version", ""),
            "vina_path": prerequisites.get("vina_path", ""),
            "command": command,
            "config_file": Path(config_file).as_posix(),
            "config_snapshot": config_snapshot_file,
            "output_file": output_file,
            "log_file": log_file,
            "exit_code": None,
            "best_affinity": None,
            "note": "当前版本只准备运行记录，尚未真正调用 AutoDock Vina。",
        }

        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (run_dir / "command_preview.txt").write_text(_format_command_preview(command) + "\n", encoding="utf-8")
        (run_dir / "config_snapshot.txt").write_text(config_text, encoding="utf-8")

        project.runs.append(
            {
                "run_id": run_id,
                "status": "prepared",
                "metadata_file": metadata_file,
                "created_at": created_at,
            },
        )
        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": metadata_file,
            "command_preview_file": command_preview_file,
            "config_snapshot_file": config_snapshot_file,
            "command": command,
            "command_preview": _format_command_preview(command),
            "checks": prerequisites.get("checks", []),
            "warnings": prerequisites.get("warnings", []),
            "message": "运行记录已准备完成；当前版本尚未真正调用 AutoDock Vina。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _run_error(
            "RUN_PREPARE_ERROR",
            "准备运行记录时发生错误。",
            prerequisites.get("checks", []),
            str(exc),
            "请确认项目 runs 目录可写，并重新准备运行记录。",
        )


def _workflow_file_status(project_dir: Path, relative_path: str) -> dict[str, Any]:
    if not relative_path:
        return {"path": "", "exists": False, "non_empty": False, "status": "missing"}
    path = Path(relative_path)
    if not path.is_absolute():
        path = project_dir / path
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    if exists and is_file and size > 0:
        status = "ok"
    elif exists and is_file:
        status = "empty"
    else:
        status = "missing"
    return {
        "path": relative_path,
        "absolute_path": str(path),
        "exists": exists,
        "non_empty": size > 0,
        "size": size,
        "status": status,
    }


def _workflow_preparation_summary(project: DockStartProject, target: str) -> dict[str, Any]:
    prep = getattr(project.preparation, target)
    return {
        "status": prep.status,
        "method": prep.method,
        "input_file": prep.input_file,
        "output_file": prep.output_file,
        "log_file": prep.log_file,
        "error": prep.error,
    }


def _workflow_viewer_status(
    project_path: Path,
    project: DockStartProject,
    receptor_raw: dict[str, Any],
    ligand_raw: dict[str, Any],
    receptor_prepared: dict[str, Any],
    ligand_prepared: dict[str, Any],
) -> dict[str, Any]:
    available_runs: list[dict[str, Any]] = []
    for run in project.runs or []:
        if not isinstance(run, dict):
            continue
        run_id = str(run.get("run_id") or "")
        if not RUN_ID_PATTERN.match(run_id):
            continue
        output_file = str(run.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())
        output_status = _workflow_file_status(project_path, output_file)
        if output_status["status"] == "ok":
            available_runs.append(
                {
                    "run_id": run_id,
                    "status": run.get("status", ""),
                    "output_file": output_file,
                    "size": output_status.get("size", 0),
                }
            )

    can_view_raw_receptor = receptor_raw["status"] == "ok"
    can_view_raw_ligand = ligand_raw["status"] == "ok"
    can_view_prepared_receptor = receptor_prepared["status"] == "ok"
    can_view_prepared_ligand = ligand_prepared["status"] == "ok"
    can_view_docking_output = bool(available_runs)

    if can_view_docking_output:
        recommended = "已有 Vina out.pdbqt，可以打开 3D Viewer 查看 docking pose。"
    elif can_view_prepared_receptor or can_view_prepared_ligand:
        recommended = "已有 prepared PDBQT，可以打开 3D Viewer 查看结构和 Box。"
    elif can_view_raw_receptor or can_view_raw_ligand:
        recommended = "已有 raw 结构，可以打开 3D Viewer 预览；运行 Vina 前仍需准备 PDBQT。"
    else:
        recommended = "请先下载 raw 结构或准备 PDBQT 文件，再打开 3D Viewer。"

    return {
        "can_view_raw_receptor": can_view_raw_receptor,
        "can_view_raw_ligand": can_view_raw_ligand,
        "can_view_prepared_receptor": can_view_prepared_receptor,
        "can_view_prepared_ligand": can_view_prepared_ligand,
        "can_view_docking_output": can_view_docking_output,
        "available_runs": available_runs,
        "recommended_viewer_action": recommended,
    }


def get_project_workflow_status(project_dir: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    project = _project_from_dict(loaded["project"], project_path)
    receptor_raw = _workflow_file_status(project_path, project.receptor.raw_file)
    ligand_raw = _workflow_file_status(project_path, project.ligand.raw_file)
    receptor_prepared = _workflow_file_status(project_path, project.receptor.file)
    ligand_prepared = _workflow_file_status(project_path, project.ligand.file)
    config_file = _config_relative_path(project)
    config_status = _workflow_file_status(project_path, config_file)
    box_validation = validate_box_params(asdict(project.box))
    vina_validation = validate_vina_params(asdict(project.vina))
    latest_run = project.runs[-1] if project.runs else None

    if receptor_prepared["status"] != "ok":
        if project.preparation.receptor.status == "failed":
            next_action = "receptor PDBQT 自动准备失败，请查看 preparation 日志并重新准备或手动导入。"
        elif receptor_raw["status"] == "ok":
            next_action = "已下载 raw receptor，请准备 receptor PDBQT。"
        else:
            next_action = "请先下载 receptor raw 文件，或直接导入 prepared/receptor.pdbqt。"
    elif ligand_prepared["status"] != "ok":
        if project.preparation.ligand.status == "failed":
            next_action = "ligand PDBQT 自动准备失败，请查看 preparation 日志并重新准备或手动导入。"
        elif ligand_raw["status"] == "ok":
            next_action = "已下载 raw ligand，请准备 ligand PDBQT。"
        else:
            next_action = "请先下载 ligand raw 文件，或直接导入 prepared/ligand.pdbqt。"
    elif not box_validation.get("ok"):
        next_action = "请设置合法的 docking box 参数。"
    elif not vina_validation.get("ok"):
        next_action = "请设置合法的 Vina 参数。"
    elif config_status["status"] != "ok":
        next_action = "请生成 configs/vina_config.txt。"
    elif not latest_run:
        next_action = "输入文件和参数已就绪，可以准备并运行 Vina。"
    elif latest_run.get("status") == "prepared":
        next_action = f"{latest_run.get('run_id')} 已准备，可以执行 Vina。"
    elif latest_run.get("status") == "finished":
        next_action = f"{latest_run.get('run_id')} 已完成，可以查看结果或导出报告。"
    elif latest_run.get("status") == "failed":
        next_action = f"{latest_run.get('run_id')} 运行失败，请查看 stdout/stderr/log。"
    else:
        next_action = "请查看最新 run 状态，并按流程继续。"

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "raw": {
            "receptor": receptor_raw,
            "ligand": ligand_raw,
        },
        "prepared": {
            "receptor": receptor_prepared,
            "ligand": ligand_prepared,
        },
        "preparation": {
            "receptor": _workflow_preparation_summary(project, "receptor"),
            "ligand": _workflow_preparation_summary(project, "ligand"),
        },
        "box": {
            "status": "ok" if box_validation.get("ok") else "error",
            "warnings": box_validation.get("warnings", []),
            "error": box_validation.get("error"),
        },
        "vina": {
            "status": "ok" if vina_validation.get("ok") else "error",
            "warnings": vina_validation.get("warnings", []),
            "error": vina_validation.get("error"),
        },
        "config": config_status,
        "latest_run": latest_run,
        "viewer": _workflow_viewer_status(
            project_path,
            project,
            receptor_raw,
            ligand_raw,
            receptor_prepared,
            ligand_prepared,
        ),
        "next_recommended_action": next_action,
        "message": "项目工作流状态已读取。",
        "error": None,
    }


def load_run_metadata(project_dir: str, run_id: str) -> dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    metadata_path = Path(project_dir).expanduser() / "runs" / run_id / "metadata.json"
    try:
        if not metadata_path.exists():
            return _error(
                "RUN_METADATA_NOT_FOUND",
                "没有找到该 run 的 metadata.json。",
                raw_error=str(metadata_path),
                suggestion="请先准备运行记录，或检查 run_id 是否正确。",
            )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return _error("RUN_METADATA_INVALID", "metadata.json 格式不是 JSON 对象。")
        return {
            "ok": True,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": Path("runs", run_id, "metadata.json").as_posix(),
            "message": "运行元数据读取成功。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_READ_ERROR",
            "读取运行元数据时发生错误。",
            str(exc),
            "请确认 metadata.json 可以读取。",
        )


def _metadata_relative_path(run_id: str) -> str:
    return Path("runs", run_id, "metadata.json").as_posix()


def _run_metadata_path(project_dir: str | Path, run_id: str) -> Path:
    return Path(project_dir).expanduser() / "runs" / run_id / "metadata.json"


def _write_run_metadata(project_dir: str | Path, run_id: str, metadata: dict[str, Any]) -> None:
    metadata_path = _run_metadata_path(project_dir, run_id)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_run_metadata(project_dir: str, run_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not RUN_ID_PATTERN.match(run_id):
        return None, _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    metadata_path = _run_metadata_path(project_dir, run_id)
    if not metadata_path.exists():
        return None, _error(
            "RUN_METADATA_NOT_FOUND",
            "没有找到该 run 的 metadata.json，无法执行 Vina。",
            raw_error=str(metadata_path),
            suggestion="请先在运行准备页创建 run 记录。",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return None, _error(
            "RUN_METADATA_READ_ERROR",
            "读取 metadata.json 时发生错误。",
            str(exc),
            "请检查 metadata.json 是否为 UTF-8 JSON 文件。",
        )

    if not isinstance(metadata, dict):
        return None, _error("RUN_METADATA_INVALID", "metadata.json 格式不是 JSON 对象。")

    return metadata, None


def _project_relative_path_for_run(
    project_dir: Path,
    relative_path: str,
    error_prefix: str,
    display_name: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    if not relative_path:
        return None, _error(
            f"{error_prefix}_NOT_SET",
            f"{display_name} 路径尚未设置。",
            suggestion=f"请回到运行准备页，重新生成包含 {display_name} 的 run metadata。",
        )

    relative = Path(relative_path)
    if relative.is_absolute():
        return None, _error(
            f"{error_prefix}_PATH_NOT_RELATIVE",
            f"{display_name} 路径必须是项目内相对路径。",
            suggestion="请重新准备运行记录，避免使用用户机器绝对路径。",
        )

    project_root = project_dir.resolve()
    file_path = (project_root / relative).resolve()
    try:
        file_path.relative_to(project_root)
    except ValueError:
        return None, _error(
            f"{error_prefix}_OUTSIDE_PROJECT",
            f"{display_name} 路径指向项目目录外，DockStart 不会执行该 run。",
            raw_error=str(file_path),
            suggestion="请重新准备运行记录，让输出路径保留在项目目录内。",
        )

    return file_path, None


def _file_status(project_dir: Path, relative_path: str, key: str, name: str) -> dict[str, Any]:
    path, error = _project_relative_path_for_run(project_dir, relative_path, key.upper(), name)
    if error:
        return {
            "key": key,
            "name": name,
            "path": relative_path,
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "error",
            "message": error["error"]["message"],
            "raw_error": error["error"].get("raw_error", ""),
        }

    exists = bool(path and path.exists())
    is_file = bool(path and path.is_file())
    size = path.stat().st_size if path and is_file else 0
    if exists and is_file and size > 0:
        status = "ok"
        message = "文件存在且非空。"
    elif exists and is_file:
        status = "empty"
        message = "文件存在，但当前为空。"
    else:
        status = "missing"
        message = "文件尚未生成。"

    return {
        "key": key,
        "name": name,
        "path": relative_path,
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "non_empty": size > 0,
        "status": status,
        "message": message,
        "raw_error": "",
    }


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is False and isinstance(payload.get("error"), dict)


def _looks_like_score_row_candidate(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\b", line))


def parse_vina_log_text(log_text: str) -> list[dict[str, Any]] | dict[str, Any]:
    """Parse the score table from AutoDock Vina log text.

    This only extracts mode, affinity, and RMSD bounds. It does not interpret
    docking quality or pose geometry.
    """

    if not isinstance(log_text, str) or not log_text.strip():
        return _error(
            "VINA_LOG_EMPTY",
            "log.txt 为空，无法解析 Vina 结果表格。",
            suggestion="请确认该 run 已成功生成非空 log.txt，然后再解析结果。",
        )

    saw_header = False
    scores: list[dict[str, Any]] = []
    for line_number, line in enumerate(log_text.splitlines(), start=1):
        lower = line.lower()
        if not saw_header:
            if "mode" in lower and "affinity" in lower:
                saw_header = True
            continue

        stripped = line.strip()
        if not stripped:
            if scores:
                break
            continue
        if set(stripped) <= {"-", "+", "|", " "}:
            continue
        if "kcal" in lower or "rmsd" in lower or "mode" in lower or "affinity" in lower:
            continue

        match = VINA_SCORE_ROW_PATTERN.match(line)
        if match:
            mode_text, affinity_text, rmsd_lb_text, rmsd_ub_text = match.groups()
            scores.append(
                {
                    "mode": int(mode_text),
                    "affinity_kcal_mol": float(affinity_text),
                    "rmsd_lb": float(rmsd_lb_text),
                    "rmsd_ub": float(rmsd_ub_text),
                },
            )
            continue

        if _looks_like_score_row_candidate(line):
            return _error(
                "VINA_RESULT_ROW_PARSE_ERROR",
                f"Vina 结果表格第 {line_number} 行无法解析。",
                raw_error=line,
                suggestion="请确认 log.txt 中的结果行包含 mode、affinity、RMSD lower bound 和 RMSD upper bound 四列。",
            )
        if scores:
            break

    if not saw_header:
        return _error(
            "VINA_RESULT_TABLE_NOT_FOUND",
            "没有在 log.txt 中找到 Vina 结果表格。",
            suggestion="请确认 log.txt 来自 AutoDock Vina，并包含 mode / affinity / RMSD 表格。",
        )
    if not scores:
        return _error(
            "VINA_RESULT_ROWS_NOT_FOUND",
            "找到了 Vina 结果表头，但没有解析到任何结果行。",
            suggestion="请检查 log.txt 是否包含完整的 docking score 表格。",
        )
    return scores


def parse_vina_log_file(project_dir: str, run_id: str) -> list[dict[str, Any]] | dict[str, Any]:
    if not RUN_ID_PATTERN.match(run_id):
        return _error(
            "RUN_ID_INVALID",
            "run_id 格式无效，应类似 run_001。",
            suggestion="请使用项目 runs 列表中的 run_id。",
        )

    project_path = Path(project_dir).expanduser()
    log_file = Path("runs", run_id, "log.txt").as_posix()
    log_path, log_error = _project_relative_path_for_run(project_path, log_file, "VINA_LOG", "log.txt")
    if log_error:
        return log_error
    assert log_path is not None

    if not log_path.exists():
        return _error(
            "VINA_LOG_NOT_FOUND",
            "没有找到该 run 的 log.txt，无法解析 Vina 结果。",
            raw_error=str(log_path),
            suggestion="请先成功运行 Vina，或确认 runs/{run_id}/log.txt 是否存在。",
        )
    if not log_path.is_file():
        return _error(
            "VINA_LOG_NOT_FILE",
            "该 run 的 log.txt 路径不是文件。",
            raw_error=str(log_path),
            suggestion="请检查 run 目录结构是否完整。",
        )
    if log_path.stat().st_size == 0:
        return _error(
            "VINA_LOG_EMPTY",
            "该 run 的 log.txt 为空，无法解析 Vina 结果。",
            raw_error=str(log_path),
            suggestion="请先成功运行 Vina，生成非空 log.txt。",
        )

    try:
        return parse_vina_log_text(log_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return _error(
            "VINA_LOG_READ_ERROR",
            "读取 log.txt 时发生编码错误。",
            raw_error=str(exc),
            suggestion="请确认 log.txt 是可读取的文本文件。",
        )


def export_scores_csv(project_dir: str, run_id: str, scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(scores, list) or not scores:
        return _error(
            "SCORES_EMPTY",
            "没有可导出的 Vina score 记录。",
            suggestion="请先解析包含结果表格的 log.txt。",
        )

    project_path = Path(project_dir).expanduser()
    run_scores_file = Path("runs", run_id, "scores.csv").as_posix()
    project_scores_file = Path("results", "scores.csv").as_posix()

    run_scores_path, run_path_error = _project_relative_path_for_run(project_path, run_scores_file, "RUN_SCORES_CSV", "scores.csv")
    if run_path_error:
        return run_path_error
    project_scores_path, project_path_error = _project_relative_path_for_run(
        project_path,
        project_scores_file,
        "PROJECT_SCORES_CSV",
        "results/scores.csv",
    )
    if project_path_error:
        return project_path_error
    assert run_scores_path is not None
    assert project_scores_path is not None

    try:
        for target_path in (run_scores_path, project_scores_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with target_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=SCORES_CSV_FIELDS)
                writer.writeheader()
                for score in scores:
                    writer.writerow({field: score[field] for field in SCORES_CSV_FIELDS})
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "SCORES_CSV_WRITE_ERROR",
            "写入 scores.csv 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目 results 目录和 run 目录可以写入。",
        )

    return {
        "ok": True,
        "project_dir": str(project_path),
        "run_id": run_id,
        "scores": scores,
        "scores_file": run_scores_file,
        "project_scores_file": project_scores_file,
        "message": "scores.csv 已导出。",
        "error": None,
    }


def _parse_scores_csv_row(row: dict[str, str], line_number: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        return (
            {
                "mode": int(row["mode"]),
                "affinity_kcal_mol": float(row["affinity_kcal_mol"]),
                "rmsd_lb": float(row["rmsd_lb"]),
                "rmsd_ub": float(row["rmsd_ub"]),
            },
            None,
        )
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return None, _error(
            "SCORES_CSV_ROW_INVALID",
            f"scores.csv 第 {line_number} 行无法解析。",
            raw_error=str(exc),
            suggestion="请重新从 Vina log 解析并导出 scores.csv。",
        )


def load_scores_csv(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
    scores_file = str(metadata.get("scores_file") or Path("runs", run_id, "scores.csv").as_posix())
    scores_path, path_error = _project_relative_path_for_run(project_path, scores_file, "SCORES_CSV", "scores.csv")
    if path_error:
        return path_error
    assert scores_path is not None

    if not scores_path.exists():
        return _error(
            "SCORES_CSV_NOT_FOUND",
            "没有找到该 run 的 scores.csv。",
            raw_error=str(scores_path),
            suggestion="请先点击“解析结果”，从 log.txt 导出 scores.csv。",
        )
    if not scores_path.is_file():
        return _error(
            "SCORES_CSV_NOT_FILE",
            "scores.csv 路径不是文件。",
            raw_error=str(scores_path),
            suggestion="请检查 run 目录结构是否完整。",
        )
    if scores_path.stat().st_size == 0:
        return _error(
            "SCORES_CSV_EMPTY",
            "scores.csv 为空，无法读取结果表格。",
            raw_error=str(scores_path),
            suggestion="请重新解析 Vina log 并导出 scores.csv。",
        )

    try:
        with scores_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != SCORES_CSV_FIELDS:
                return _error(
                    "SCORES_CSV_HEADER_INVALID",
                    "scores.csv 表头不符合 DockStart 当前版本要求。",
                    raw_error=",".join(reader.fieldnames or []),
                    suggestion="请重新从 Vina log 解析并导出 scores.csv。",
                )
            scores: list[dict[str, Any]] = []
            for line_number, row in enumerate(reader, start=2):
                parsed, row_error = _parse_scores_csv_row(row, line_number)
                if row_error:
                    return row_error
                assert parsed is not None
                scores.append(parsed)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "SCORES_CSV_READ_ERROR",
            "读取 scores.csv 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 scores.csv 是 UTF-8 文本文件。",
        )

    if not scores:
        return _error(
            "SCORES_CSV_NO_ROWS",
            "scores.csv 中没有结果记录。",
            suggestion="请重新解析包含 Vina 结果表格的 log.txt。",
        )

    loaded = load_project(project_dir)
    project = loaded.get("project") if loaded.get("ok") else None
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project,
        "run_id": run_id,
        "metadata": metadata,
        "scores": scores,
        "scores_file": scores_file,
        "project_scores_file": str(metadata.get("project_scores_file") or Path("results", "scores.csv").as_posix()),
        "best_affinity": metadata.get("best_affinity", scores[0]["affinity_kcal_mol"]),
        "analyzed_at": metadata.get("analyzed_at", ""),
        "message": "scores.csv 已读取。",
        "error": None,
    }


def analyze_vina_run_results(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    status = str(metadata.get("status") or "")
    if status != "finished":
        return _error(
            "RUN_STATUS_NOT_FINISHED",
            f"当前 run 状态为 {status or 'unknown'}，只有 finished 状态的 run 可以解析结果。",
            suggestion="请先成功运行 Vina，确认 run.status 为 finished 后再解析结果。",
        )

    parsed_scores = parse_vina_log_file(project_dir, run_id)
    if _is_error_payload(parsed_scores):
        return parsed_scores
    assert isinstance(parsed_scores, list)

    exported = export_scores_csv(project_dir, run_id, parsed_scores)
    if not exported.get("ok"):
        return exported

    analyzed_at = _now_iso()
    best_affinity = parsed_scores[0]["affinity_kcal_mol"]
    metadata.update(
        {
            "best_affinity": best_affinity,
            "scores_file": exported["scores_file"],
            "project_scores_file": exported["project_scores_file"],
            "analyzed_at": analyzed_at,
        },
    )

    try:
        _write_run_metadata(project_dir, run_id, metadata)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_WRITE_ERROR",
            "写入 metadata.json 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 run 目录可以写入。",
        )

    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "best_affinity": best_affinity,
            "scores_file": exported["scores_file"],
            "analyzed_at": analyzed_at,
        },
    )
    if not project_update.get("ok"):
        return project_update

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": project_update.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "scores": parsed_scores,
        "scores_file": exported["scores_file"],
        "project_scores_file": exported["project_scores_file"],
        "best_affinity": best_affinity,
        "analyzed_at": analyzed_at,
        "message": "Vina 结果已解析，scores.csv 已导出。",
        "error": None,
    }


def _markdown_cell(value: Any) -> str:
    if value is None or value == "":
        text = "未记录"
    elif isinstance(value, float):
        text = _format_config_number(value)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(_markdown_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _command_for_report(metadata: dict[str, Any]) -> list[str]:
    command = metadata.get("command")
    if isinstance(command, list):
        return [str(item) for item in command]
    return []


def _load_report_context(project_dir: str, run_id: str) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    try:
        project = _project_from_dict(loaded["project"], project_path)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "PROJECT_REPORT_CONFIG_INVALID",
            "project.json 格式不完整，无法导出 Markdown 报告。",
            raw_error=str(exc),
            suggestion="请检查 project.json 中 receptor、ligand、box、vina 和 runs 字段是否完整。",
        )

    metadata, metadata_error = _read_run_metadata(project_dir, run_id)
    if metadata_error:
        return metadata_error
    assert metadata is not None

    status = str(metadata.get("status") or "")
    if status != "finished":
        return _error(
            "RUN_STATUS_NOT_FINISHED",
            f"当前 run 状态为 {status or 'unknown'}，只有 finished 状态的 run 可以导出 Markdown 报告。",
            suggestion="请先成功运行 Vina，并解析 scores.csv 后再导出报告。",
        )

    if not any(isinstance(item, dict) and item.get("run_id") == run_id for item in project.runs):
        return _error(
            "RUN_SUMMARY_NOT_FOUND",
            "project.json 的 runs 数组中没有找到对应 run，无法导出 Markdown 报告。",
            suggestion="请确认该 run 来自当前 DockStart 项目，或重新准备运行记录。",
        )

    receptor_path, receptor_error = _project_relative_existing_file(
        project_path,
        project.receptor.file,
        "RECEPTOR_FILE",
        "receptor.pdbqt",
    )
    if receptor_error:
        return receptor_error
    ligand_path, ligand_error = _project_relative_existing_file(
        project_path,
        project.ligand.file,
        "LIGAND_FILE",
        "ligand.pdbqt",
    )
    if ligand_error:
        return ligand_error

    config_file = str(metadata.get("config_file") or _config_relative_path(project))
    config_path, config_error = _project_relative_existing_file(project_path, config_file, "VINA_CONFIG", "vina_config.txt")
    if config_error:
        return config_error

    scores_payload = load_scores_csv(project_dir, run_id)
    if not scores_payload.get("ok"):
        return scores_payload

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project.to_dict(),
        "metadata": metadata,
        "run_id": run_id,
        "scores": scores_payload["scores"],
        "scores_file": scores_payload["scores_file"],
        "project_scores_file": scores_payload.get("project_scores_file", Path("results", "scores.csv").as_posix()),
        "receptor_file": project.receptor.file,
        "receptor_path": str(receptor_path) if receptor_path else "",
        "ligand_file": project.ligand.file,
        "ligand_path": str(ligand_path) if ligand_path else "",
        "config_file": config_file,
        "config_path": str(config_path) if config_path else "",
        "error": None,
    }


def build_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    context = _load_report_context(project_dir, run_id)
    if not context.get("ok"):
        return context

    project = _project_from_dict(context["project"], Path(project_dir).expanduser())
    metadata = context["metadata"]
    scores = context["scores"]
    command = _command_for_report(metadata)
    command_text = json.dumps(command, ensure_ascii=False, indent=2)
    vina_path = command[0] if command else str(metadata.get("vina_path") or "")

    input_rows = [
        ["receptor 文件", context["receptor_file"]],
        ["ligand 文件", context["ligand_file"]],
        ["vina_config.txt", context["config_file"]],
        ["log.txt", str(metadata.get("log_file") or Path("runs", run_id, "log.txt").as_posix())],
        ["out.pdbqt", str(metadata.get("output_file") or Path("runs", run_id, "out.pdbqt").as_posix())],
        ["stdout.txt", str(metadata.get("stdout_file") or Path("runs", run_id, "stdout.txt").as_posix())],
        ["stderr.txt", str(metadata.get("stderr_file") or Path("runs", run_id, "stderr.txt").as_posix())],
    ]
    box_rows = [
        ["center_x", project.box.center_x, "Å"],
        ["center_y", project.box.center_y, "Å"],
        ["center_z", project.box.center_z, "Å"],
        ["size_x", project.box.size_x, "Å"],
        ["size_y", project.box.size_y, "Å"],
        ["size_z", project.box.size_z, "Å"],
    ]
    vina_rows = [
        ["exhaustiveness", project.vina.exhaustiveness],
        ["num_modes", project.vina.num_modes],
        ["energy_range", project.vina.energy_range],
        ["cpu", project.vina.cpu],
        ["seed", project.vina.seed if project.vina.seed is not None else "未设置"],
    ]
    score_rows = [
        [score["mode"], score["affinity_kcal_mol"], score["rmsd_lb"], score["rmsd_ub"]]
        for score in scores
    ]

    report_text = "\n".join(
        [
            "# DockStart Docking Report",
            "",
            "## 1. 项目信息",
            "",
            f"- 项目名称: {_markdown_cell(project.project_name)}",
            f"- 项目路径: {_markdown_cell(project.project_dir)}",
            f"- 创建时间: {_markdown_cell(project.created_at)}",
            f"- 更新时间: {_markdown_cell(project.updated_at)}",
            f"- run_id: {_markdown_cell(run_id)}",
            "",
            "## 2. 输入文件",
            "",
            _markdown_table(["项目", "路径"], input_rows),
            "",
            "## 3. Box 参数",
            "",
            _markdown_table(["参数", "值", "单位"], box_rows),
            "",
            "## 4. Vina 参数",
            "",
            _markdown_table(["参数", "值"], vina_rows),
            "",
            "## 5. 运行信息",
            "",
            f"- Vina 路径: {_markdown_cell(vina_path)}",
            f"- Vina 版本: {_markdown_cell(metadata.get('vina_version'))}",
            f"- started_at: {_markdown_cell(metadata.get('started_at'))}",
            f"- finished_at: {_markdown_cell(metadata.get('finished_at'))}",
            f"- exit_code: {_markdown_cell(metadata.get('exit_code'))}",
            "",
            "命令数组:",
            "",
            "```json",
            command_text,
            "```",
            "",
            "## 6. Docking Score 结果",
            "",
            _markdown_table(["Mode", "Affinity kcal/mol", "RMSD l.b.", "RMSD u.b."], score_rows),
            "",
            "## 7. 重要说明",
            "",
            DOCKING_SCORE_DISCLAIMER,
            "",
            "- 本报告不证明真实药效；",
            "- 本报告不包含相互作用分析；",
            "- 本报告不包含分子动力学验证；",
            "- 结果依赖输入结构、box、参数和 Vina 版本。",
            "",
        ],
    )

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": project.to_dict(),
        "run_id": run_id,
        "metadata": metadata,
        "scores": scores,
        "scores_file": context["scores_file"],
        "project_scores_file": context["project_scores_file"],
        "report_text": report_text,
        "message": "Markdown 报告内容已生成。",
        "error": None,
    }


def _report_file_statuses(project_dir: str, run_id: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    project_path = Path(project_dir).expanduser()
    scores_file = str(metadata.get("scores_file") or Path("runs", run_id, "scores.csv").as_posix())
    report_file = str(metadata.get("report_file") or Path("runs", run_id, RUN_REPORT_FILE).as_posix())
    project_report_file = str(metadata.get("project_report_file") or PROJECT_REPORT_FILE)
    return [
        _file_status(project_path, scores_file, "scores", "scores.csv"),
        _file_status(project_path, report_file, "run_report", f"runs/{run_id}/docking_report.md"),
        _file_status(project_path, project_report_file, "project_report", "reports/docking_report.md"),
    ]


def get_report_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    files = _report_file_statuses(project_dir, run_id, metadata)
    scores_status = next((item for item in files if item["key"] == "scores"), None)
    report_files = [item for item in files if item["key"] in {"run_report", "project_report"}]
    reports_ready = all(item["status"] == "ok" for item in report_files)
    can_export = str(metadata.get("status") or "") == "finished" and bool(scores_status and scores_status["status"] == "ok")

    return {
        "ok": True,
        "project_dir": str(Path(project_dir).expanduser()),
        "project": loaded.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "files": files,
        "scores_status": scores_status,
        "report_status": "exported" if reports_ready else "missing",
        "can_export": can_export,
        "report_file": str(metadata.get("report_file") or Path("runs", run_id, RUN_REPORT_FILE).as_posix()),
        "project_report_file": str(metadata.get("project_report_file") or PROJECT_REPORT_FILE),
        "reported_at": str(metadata.get("reported_at") or ""),
        "message": "报告状态已读取。",
        "error": None,
    }


def export_markdown_report(project_dir: str, run_id: str) -> dict[str, Any]:
    built = build_markdown_report(project_dir, run_id)
    if not built.get("ok"):
        return built

    project_path = Path(project_dir).expanduser()
    run_report_file = Path("runs", run_id, RUN_REPORT_FILE).as_posix()
    project_report_file = PROJECT_REPORT_FILE
    run_report_path, run_report_error = _project_relative_path_for_run(
        project_path,
        run_report_file,
        "RUN_REPORT",
        f"runs/{run_id}/docking_report.md",
    )
    if run_report_error:
        return run_report_error
    project_report_path, project_report_error = _project_relative_path_for_run(
        project_path,
        project_report_file,
        "PROJECT_REPORT",
        "reports/docking_report.md",
    )
    if project_report_error:
        return project_report_error
    assert run_report_path is not None
    assert project_report_path is not None

    try:
        for target_path in (project_report_path, run_report_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(built["report_text"], encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "MARKDOWN_REPORT_WRITE_ERROR",
            "写入 Markdown 报告时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目 reports 目录和 run 目录可以写入。",
        )

    reported_at = _now_iso()
    metadata = dict(built["metadata"])
    metadata.update(
        {
            "report_file": run_report_file,
            "project_report_file": project_report_file,
            "reported_at": reported_at,
        },
    )
    try:
        _write_run_metadata(project_dir, run_id, metadata)
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_WRITE_ERROR",
            "写入 metadata.json 时发生错误。",
            raw_error=str(exc),
            suggestion="请确认 run 目录可以写入。",
        )

    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "report_file": run_report_file,
            "project_report_file": project_report_file,
            "reported_at": reported_at,
        },
    )
    if not project_update.get("ok"):
        return project_update

    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project_update.get("project"),
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "report_file": run_report_file,
        "project_report_file": project_report_file,
        "reported_at": reported_at,
        "files": _report_file_statuses(project_dir, run_id, metadata),
        "message": "Markdown 报告已导出。",
        "error": None,
    }


def update_run_metadata(project_dir: str, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return _error("RUN_METADATA_PATCH_INVALID", "metadata patch 必须是 JSON 对象。")

    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    try:
        metadata.update(patch)
        _write_run_metadata(project_dir, run_id, metadata)
        return {
            "ok": True,
            "project_dir": str(Path(project_dir).expanduser()),
            "run_id": run_id,
            "metadata": metadata,
            "metadata_file": _metadata_relative_path(run_id),
            "message": "运行元数据已更新。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_METADATA_WRITE_ERROR",
            "写入 metadata.json 时发生错误。",
            str(exc),
            "请确认 run 目录可写。",
        )


def update_project_run_summary(project_dir: str, run_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return _error("RUN_SUMMARY_PATCH_INVALID", "run summary patch 必须是 JSON 对象。")

    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    try:
        project = _project_from_dict(loaded["project"], Path(project_dir).expanduser())
        matched = False
        for run_summary in project.runs:
            if isinstance(run_summary, dict) and run_summary.get("run_id") == run_id:
                run_summary.update(patch)
                matched = True
                break

        if not matched:
            return _error(
                "RUN_SUMMARY_NOT_FOUND",
                "project.json 的 runs 数组中没有找到对应 run。",
                suggestion="请回到运行准备页重新创建运行记录。",
            )

        saved = save_project(project)
        if not saved.get("ok"):
            return saved
        return {
            "ok": True,
            "project_dir": project.project_dir,
            "project": project.to_dict(),
            "run_id": run_id,
            "message": "project.json 中的 run 摘要已更新。",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_SUMMARY_UPDATE_ERROR",
            "更新 project.json 中的 run 摘要时发生错误。",
            str(exc),
            "请确认 project.json 可以写入。",
        )


def _validate_execute_prerequisites(
    project_dir: str,
    run_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return loaded

    project_path = Path(project_dir).expanduser()
    project = _project_from_dict(loaded["project"], project_path)
    if not any(isinstance(item, dict) and item.get("run_id") == run_id for item in project.runs):
        return _error(
            "RUN_SUMMARY_NOT_FOUND",
            "project.json 的 runs 数组中没有找到对应 run。",
            suggestion="请回到运行准备页重新创建运行记录。",
        )

    receptor_path, receptor_error = _project_relative_file(project_path, project.receptor.file, "receptor")
    if receptor_error:
        return receptor_error
    if receptor_path and receptor_path.stat().st_size == 0:
        return _error(
            "RECEPTOR_FILE_EMPTY",
            "受体 PDBQT 文件为空，无法执行 Vina。",
            raw_error=str(receptor_path),
            suggestion="请重新导入非空的 receptor.pdbqt。",
        )

    ligand_path, ligand_error = _project_relative_file(project_path, project.ligand.file, "ligand")
    if ligand_error:
        return ligand_error
    if ligand_path and ligand_path.stat().st_size == 0:
        return _error(
            "LIGAND_FILE_EMPTY",
            "配体 PDBQT 文件为空，无法执行 Vina。",
            raw_error=str(ligand_path),
            suggestion="请重新导入非空的 ligand.pdbqt。",
        )

    config_file = str(metadata.get("config_file") or _config_relative_path(project))
    config_path, config_error = _project_relative_existing_file(project_path, config_file, "VINA_CONFIG", "configs/vina_config.txt")
    if config_error:
        return config_error
    if config_path and config_path.stat().st_size == 0:
        return _error(
            "VINA_CONFIG_FILE_EMPTY",
            "vina_config.txt 文件为空，无法执行 Vina。",
            raw_error=str(config_path),
            suggestion="请重新生成 vina_config.txt。",
        )

    command = metadata.get("command")
    if not isinstance(command, list) or not command:
        return _error(
            "RUN_COMMAND_INVALID",
            "metadata.command 必须是非空数组，不能执行字符串命令。",
            suggestion="请回到运行准备页重新生成 run 记录。",
        )
    if not all(isinstance(item, str) and item.strip() for item in command):
        return _error(
            "RUN_COMMAND_INVALID",
            "metadata.command 数组中的每一项都必须是非空字符串。",
            suggestion="请回到运行准备页重新生成 run 记录。",
        )

    output_file = str(metadata.get("output_file") or "")
    log_file = str(metadata.get("log_file") or "")
    output_path, output_error = _project_relative_path_for_run(project_path, output_file, "RUN_OUTPUT_FILE", "out.pdbqt")
    if output_error:
        return output_error
    log_path, log_error = _project_relative_path_for_run(project_path, log_file, "RUN_LOG_FILE", "log.txt")
    if log_error:
        return log_error

    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "command": command,
        "config_file": config_file,
        "config_path": str(config_path) if config_path else "",
        "output_file": output_file,
        "output_path": str(output_path) if output_path else "",
        "log_file": log_file,
        "log_path": str(log_path) if log_path else "",
        "error": None,
    }


def get_run_files_status(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    project_path = Path(project_dir).expanduser()
    files = [
        _file_status(project_path, _metadata_relative_path(run_id), "metadata", "metadata.json"),
        _file_status(
            project_path,
            str(metadata.get("config_snapshot") or Path("runs", run_id, "config_snapshot.txt").as_posix()),
            "config_snapshot",
            "config_snapshot.txt",
        ),
        _file_status(
            project_path,
            str(metadata.get("stdout_file") or Path("runs", run_id, "stdout.txt").as_posix()),
            "stdout",
            "stdout.txt",
        ),
        _file_status(
            project_path,
            str(metadata.get("stderr_file") or Path("runs", run_id, "stderr.txt").as_posix()),
            "stderr",
            "stderr.txt",
        ),
        _file_status(project_path, str(metadata.get("log_file") or ""), "log", "log.txt"),
        _file_status(project_path, str(metadata.get("output_file") or ""), "out", "out.pdbqt"),
    ]

    loaded = load_project(project_dir)
    project = loaded.get("project") if loaded.get("ok") else None
    return {
        "ok": True,
        "project_dir": str(project_path),
        "project": project,
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "files": files,
        "message": "运行文件状态已读取。",
        "error": None,
    }


def execute_prepared_vina_run(project_dir: str, run_id: str) -> dict[str, Any]:
    metadata, error = _read_run_metadata(project_dir, run_id)
    if error:
        return error
    assert metadata is not None

    status = str(metadata.get("status") or "")
    if status != "prepared":
        return _error(
            "RUN_STATUS_NOT_EXECUTABLE",
            f"当前 run 状态为 {status or 'unknown'}，只能执行 prepared 状态的 run。",
            suggestion="请不要重复执行 running、finished、failed 或 cancelled 的 run。",
        )

    prerequisites = _validate_execute_prerequisites(project_dir, run_id, metadata)
    if not prerequisites.get("ok"):
        return prerequisites

    project_path = Path(project_dir).expanduser()
    stdout_file = Path("runs", run_id, "stdout.txt").as_posix()
    stderr_file = Path("runs", run_id, "stderr.txt").as_posix()
    stdout_path = project_path / stdout_file
    stderr_path = project_path / stderr_file
    log_file = str(metadata.get("log_file") or Path("runs", run_id, "log.txt").as_posix())
    log_path = project_path / log_file
    output_path = Path(prerequisites["output_path"])
    command = [str(item) for item in prerequisites["command"]]

    started_at = _now_iso()
    metadata.update(
        {
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
            "exit_code": None,
            "best_affinity": None,
        },
    )
    try:
        _write_run_metadata(project_dir, run_id, metadata)
        running_project_update = update_project_run_summary(project_dir, run_id, {"status": "running", "started_at": started_at})
        if not running_project_update.get("ok"):
            metadata["status"] = "prepared"
            metadata["started_at"] = None
            _write_run_metadata(project_dir, run_id, metadata)
            return running_project_update
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        return _error(
            "RUN_START_UPDATE_ERROR",
            "更新运行中状态时发生错误，未执行 Vina。",
            str(exc),
            "请确认 run 目录和 project.json 可写。",
        )

    exit_code: int | None
    stdout_text = ""
    stderr_text = ""
    run_exception = ""
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            **hidden_subprocess_kwargs(),
        )
        exit_code = completed.returncode
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
    except Exception as exc:  # noqa: BLE001 - return structured errors.
        exit_code = None
        run_exception = str(exc)
        stderr_text = run_exception

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(stdout_text, encoding="utf-8")

    finished_at = _now_iso()
    output_ok = output_path.exists() and output_path.is_file() and output_path.stat().st_size > 0
    if exit_code == 0 and output_ok:
        final_status = "finished"
        error_message = ""
    elif exit_code == 0:
        final_status = "failed"
        error_message = "AutoDock Vina 结束码为 0，但没有生成非空 out.pdbqt。请查看 stdout.txt、stderr.txt 和 log.txt。"
    else:
        final_status = "failed"
        error_message = "AutoDock Vina 执行失败，请查看 stderr.txt 和 log.txt。"

    metadata.update(
        {
            "status": final_status,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
            "output_file": prerequisites["output_file"],
            "log_file": prerequisites["log_file"],
            "best_affinity": None,
        },
    )
    if error_message:
        metadata["error_message"] = error_message
    else:
        metadata.pop("error_message", None)

    _write_run_metadata(project_dir, run_id, metadata)
    project_update = update_project_run_summary(
        project_dir,
        run_id,
        {
            "status": final_status,
            "finished_at": finished_at,
            "exit_code": exit_code,
        },
    )

    files_status = get_run_files_status(project_dir, run_id)
    if not project_update.get("ok"):
        project_update["metadata"] = metadata
        project_update["files"] = files_status.get("files", [])
        return project_update

    payload = {
        "ok": final_status == "finished",
        "project_dir": str(project_path),
        "project": project_update.get("project") if project_update.get("ok") else None,
        "run_id": run_id,
        "metadata": metadata,
        "metadata_file": _metadata_relative_path(run_id),
        "stdout_file": stdout_file,
        "stderr_file": stderr_file,
        "output_file": prerequisites["output_file"],
        "log_file": prerequisites["log_file"],
        "files": files_status.get("files", []),
        "message": "Vina 运行完成。" if final_status == "finished" else error_message,
        "error": None,
    }

    if final_status != "finished":
        payload["error"] = {
            "code": "VINA_RUN_FAILED",
            "message": error_message,
            "raw_error": run_exception or stderr_text,
            "suggestion": "请查看 stderr.txt、stdout.txt 和 log.txt，确认 Vina 路径、输入文件和参数是否正确。",
        }
    return payload


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    if command == "create":
        if len(sys.argv) < 4:
            _print_json(_error("PROJECT_CREATE_ARGS", "创建项目需要 project_name 和 base_dir 参数。"))
            return
        _print_json(create_project(sys.argv[2], sys.argv[3]))
        return

    if command == "load":
        if len(sys.argv) < 3:
            _print_json(_error("PROJECT_LOAD_ARGS", "读取项目需要 project_dir 参数。"))
            return
        _print_json(load_project(sys.argv[2]))
        return

    if command == "import-receptor":
        if len(sys.argv) < 4:
            _print_json(_error("PDBQT_IMPORT_ARGS", "导入受体需要 project_dir 和 source_path 参数。"))
            return
        _print_json(import_receptor_pdbqt(sys.argv[2], sys.argv[3]))
        return

    if command == "import-ligand":
        if len(sys.argv) < 4:
            _print_json(_error("PDBQT_IMPORT_ARGS", "导入配体需要 project_dir 和 source_path 参数。"))
            return
        _print_json(import_ligand_pdbqt(sys.argv[2], sys.argv[3]))
        return

    if command == "get-box":
        if len(sys.argv) < 3:
            _print_json(_error("BOX_GET_ARGS", "读取 Box 参数需要 project_dir 参数。"))
            return
        _print_json(get_box_params(sys.argv[2]))
        return

    if command == "update-box":
        if len(sys.argv) < 4:
            _print_json(_error("BOX_UPDATE_ARGS", "保存 Box 参数需要 project_dir 和 box JSON 参数。"))
            return
        try:
            box = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(_error("BOX_JSON_INVALID", "Box 参数不是有效 JSON。", str(exc)))
            return
        _print_json(update_box_params(sys.argv[2], box))
        return

    if command == "get-vina":
        if len(sys.argv) < 3:
            _print_json(_error("VINA_GET_ARGS", "读取 Vina 参数需要 project_dir 参数。"))
            return
        _print_json(get_vina_params(sys.argv[2]))
        return

    if command == "update-vina":
        if len(sys.argv) < 4:
            _print_json(_error("VINA_UPDATE_ARGS", "保存 Vina 参数需要 project_dir 和 vina JSON 参数。"))
            return
        try:
            vina = json.loads(sys.argv[3])
        except json.JSONDecodeError as exc:
            _print_json(_error("VINA_JSON_INVALID", "Vina 参数不是有效 JSON。", str(exc)))
            return
        _print_json(update_vina_params(sys.argv[2], vina))
        return

    if command == "preview-config":
        if len(sys.argv) < 3:
            _print_json(_error("CONFIG_PREVIEW_ARGS", "预览 Vina 配置需要 project_dir 参数。"))
            return
        _print_json(get_vina_config_preview(sys.argv[2]))
        return

    if command == "generate-config":
        if len(sys.argv) < 3:
            _print_json(_error("CONFIG_GENERATE_ARGS", "生成 Vina 配置需要 project_dir 参数。"))
            return
        _print_json(generate_vina_config(sys.argv[2]))
        return

    if command == "validate-run":
        if len(sys.argv) < 3:
            _print_json(_error("RUN_VALIDATE_ARGS", "运行前检查需要 project_dir 参数。"))
            return
        _print_json(validate_run_prerequisites(sys.argv[2]))
        return

    if command == "prepare-run":
        if len(sys.argv) < 3:
            _print_json(_error("RUN_PREPARE_ARGS", "准备运行记录需要 project_dir 参数。"))
            return
        _print_json(prepare_vina_run(sys.argv[2]))
        return

    if command == "workflow-status":
        if len(sys.argv) < 3:
            _print_json(_error("WORKFLOW_STATUS_ARGS", "读取项目工作流状态需要 project_dir 参数。"))
            return
        _print_json(get_project_workflow_status(sys.argv[2]))
        return

    if command == "load-run-metadata":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_METADATA_ARGS", "读取运行元数据需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_run_metadata(sys.argv[2], sys.argv[3]))
        return

    if command == "execute-run":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_EXECUTE_ARGS", "执行 prepared run 需要 project_dir 和 run_id 参数。"))
            return
        _print_json(execute_prepared_vina_run(sys.argv[2], sys.argv[3]))
        return

    if command == "run-files-status":
        if len(sys.argv) < 4:
            _print_json(_error("RUN_FILES_STATUS_ARGS", "读取运行文件状态需要 project_dir 和 run_id 参数。"))
            return
        _print_json(get_run_files_status(sys.argv[2], sys.argv[3]))
        return

    if command == "analyze-results":
        if len(sys.argv) < 4:
            _print_json(_error("RESULT_ANALYZE_ARGS", "解析 Vina 结果需要 project_dir 和 run_id 参数。"))
            return
        _print_json(analyze_vina_run_results(sys.argv[2], sys.argv[3]))
        return

    if command == "load-scores":
        if len(sys.argv) < 4:
            _print_json(_error("SCORES_LOAD_ARGS", "读取 scores.csv 需要 project_dir 和 run_id 参数。"))
            return
        _print_json(load_scores_csv(sys.argv[2], sys.argv[3]))
        return

    if command == "export-report":
        if len(sys.argv) < 4:
            _print_json(_error("REPORT_EXPORT_ARGS", "导出 Markdown 报告需要 project_dir 和 run_id 参数。"))
            return
        _print_json(export_markdown_report(sys.argv[2], sys.argv[3]))
        return

    if command == "report-status":
        if len(sys.argv) < 4:
            _print_json(_error("REPORT_STATUS_ARGS", "读取报告状态需要 project_dir 和 run_id 参数。"))
            return
        _print_json(get_report_status(sys.argv[2], sys.argv[3]))
        return

    _print_json(_error("PROJECT_COMMAND_UNKNOWN", f"未知项目命令：{command}"))


if __name__ == "__main__":
    main()
