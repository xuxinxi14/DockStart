"""Fetch raw receptor and ligand structure files for DockStart projects."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dockstart_core.persistence import atomic_write_bytes
from dockstart_core.project import _error, _now_iso, _project_from_dict, _success, load_project, save_project

PDB_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{4}$")
SUPPORTED_PDB_FORMATS = {"pdb", "cif"}
SUPPORTED_PUBCHEM_FORMATS = {"sdf"}
SUPPORTED_LOCAL_RECEPTOR_FORMATS = {"pdb", "cif"}
SUPPORTED_LOCAL_LIGAND_FORMATS = {"sdf", "mol"}
DEFAULT_TIMEOUT_SECONDS = 30

Fetcher = Callable[[str, int], bytes]


def validate_pdb_id(pdb_id: str) -> dict[str, Any]:
    value = str(pdb_id or "").strip()
    if not value:
        return _error(
            "PDB_ID_REQUIRED",
            "PDB ID 不能为空。",
            suggestion="请输入 4 位 PDB ID，例如 1HSG。",
        )
    if not PDB_ID_PATTERN.match(value):
        return _error(
            "PDB_ID_INVALID",
            "PDB ID 必须是 4 位英文字母或数字。",
            raw_error=value,
            suggestion="请检查 PDB ID，例如 1HSG。",
        )
    return {"ok": True, "pdb_id": value.upper(), "error": None}


def validate_pubchem_cid(cid: str | int) -> dict[str, Any]:
    value = str(cid if cid is not None else "").strip()
    if not value:
        return _error(
            "PUBCHEM_CID_REQUIRED",
            "PubChem CID 不能为空。",
            suggestion="请输入正整数 CID，例如 2244。",
        )
    if not value.isdecimal():
        return _error(
            "PUBCHEM_CID_INVALID",
            "PubChem CID 必须是正整数。",
            raw_error=value,
            suggestion="如需按名称查询，请在前端选择“名称”；SMILES 查询当前暂未支持。",
        )
    number = int(value)
    if number <= 0:
        return _error(
            "PUBCHEM_CID_INVALID",
            "PubChem CID 必须大于 0。",
            raw_error=value,
            suggestion="请输入正整数 CID，例如 2244。",
        )
    return {"ok": True, "cid": str(number), "error": None}


def validate_pubchem_name(name: str) -> dict[str, Any]:
    value = str(name or "").strip()
    if not value:
        return _error(
            "PUBCHEM_NAME_REQUIRED",
            "PubChem 名称不能为空。",
            suggestion="请输入化合物英文名，例如 aspirin。",
        )
    if len(value) > 120:
        return _error(
            "PUBCHEM_NAME_TOO_LONG",
            "PubChem 名称过长。",
            raw_error=value,
            suggestion="请使用更短的常用英文名，或改用 PubChem CID。",
        )
    return {"ok": True, "name": value, "error": None}


def _fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DockStart/0.2.7"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoints only.
        return response.read()


def _download(url: str, fetcher: Fetcher | None, timeout: int) -> tuple[bytes | None, dict[str, Any] | None]:
    try:
        data = (fetcher or _fetch_bytes)(url, timeout)
    except urllib.error.HTTPError as exc:
        return None, _error(
            "STRUCTURE_DOWNLOAD_HTTP_ERROR",
            "下载原始结构文件失败，远端服务返回错误。",
            raw_error=f"HTTP {exc.code}: {exc.reason}",
            suggestion="请确认 ID 是否正确，稍后重试，或手动下载后导入。",
        )
    except urllib.error.URLError as exc:
        return None, _error(
            "STRUCTURE_DOWNLOAD_NETWORK_ERROR",
            "下载原始结构文件失败，可能是网络不可用或请求超时。",
            raw_error=str(exc.reason),
            suggestion="请检查网络连接，或稍后重试。",
        )
    except TimeoutError as exc:
        return None, _error(
            "STRUCTURE_DOWNLOAD_TIMEOUT",
            "下载原始结构文件超时。",
            raw_error=str(exc),
            suggestion="请检查网络连接，或稍后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - always return structured errors for UI.
        return None, _error(
            "STRUCTURE_DOWNLOAD_ERROR",
            "下载原始结构文件时发生错误。",
            raw_error=str(exc),
            suggestion="请确认输入 ID 正确，或手动下载后导入。",
        )

    if not data:
        return None, _error(
            "STRUCTURE_DOWNLOAD_EMPTY",
            "下载结果为空，未写入 raw 文件。",
            raw_error=url,
            suggestion="请确认 ID 和格式是否正确。",
        )
    return data, None


def _load_project_for_raw(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    return _project_from_dict(loaded["project"], Path(project_dir).expanduser()), None


def _write_raw_file(target_path: Path, data: bytes, overwrite: bool) -> dict[str, Any]:
    if target_path.exists() and not overwrite:
        return _error(
            "RAW_FILE_EXISTS",
            "raw 文件已存在，当前设置不会覆盖。",
            raw_error=str(target_path),
            suggestion="如需重新下载，请开启 overwrite。",
        )
    atomic_write_bytes(target_path, data)
    return {"ok": True, "path": str(target_path), "error": None}


def _validate_local_raw_file(
    source_path: str,
    supported_formats: set[str],
    label: str,
    format_suggestion: str,
) -> dict[str, Any]:
    file_path = Path(source_path).expanduser()
    if not source_path.strip():
        return _error(
            "LOCAL_RAW_PATH_REQUIRED",
            f"{label}文件不能为空。",
            suggestion="请选择一个本地结构文件。",
        )
    if not file_path.exists():
        return _error(
            "LOCAL_RAW_FILE_NOT_FOUND",
            f"没有找到{label}文件。",
            raw_error=str(file_path),
            suggestion="请确认文件路径正确，或重新选择文件。",
        )
    if not file_path.is_file():
        return _error(
            "LOCAL_RAW_PATH_NOT_FILE",
            f"{label}路径不是一个文件。",
            raw_error=str(file_path),
            suggestion="请选择具体的结构文件，而不是文件夹。",
        )
    file_format = file_path.suffix.lower().lstrip(".")
    if file_format not in supported_formats:
        return _error(
            "LOCAL_RAW_FORMAT_UNSUPPORTED",
            f"{label}格式暂不支持内置 PDBQT 转换。",
            raw_error=file_path.suffix,
            suggestion=format_suggestion,
        )
    if file_path.stat().st_size == 0:
        return _error(
            "LOCAL_RAW_FILE_EMPTY",
            f"{label}文件为空。",
            raw_error=str(file_path),
            suggestion="请确认该文件是有效的结构文件。",
        )
    return {"ok": True, "path": str(file_path), "format": file_format, "error": None}


def _import_local_raw_file(project_dir: str, source_path: str, role: str) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("LOCAL_RAW_ROLE_INVALID", "原始结构导入类型无效。")

    is_receptor = role == "receptor"
    label = "受体结构" if is_receptor else "配体结构"
    supported_formats = SUPPORTED_LOCAL_RECEPTOR_FORMATS if is_receptor else SUPPORTED_LOCAL_LIGAND_FORMATS
    format_suggestion = (
        "受体原始结构当前支持 PDB（.pdb）和 mmCIF（.cif）；PDBQT 请使用“已有 PDBQT”导入入口。"
        if is_receptor
        else "配体原始结构当前支持 SDF（.sdf）和 MOL（.mol）；MOL2、PDB 与 SMILES 暂不支持内置转换。"
    )
    validation = _validate_local_raw_file(source_path, supported_formats, label, format_suggestion)
    if not validation.get("ok"):
        return validation

    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None

    source = Path(str(validation["path"]))
    file_format = str(validation["format"])
    project_path = Path(project.project_dir).expanduser()
    relative_file = Path("raw", f"{role}_{_safe_file_slug(source.stem)}.{file_format}").as_posix()
    target_path = project_path / relative_file

    if target_path.exists():
        return _error(
            "LOCAL_RAW_FILE_EXISTS",
            "项目 raw/ 目录中已经存在同名文件，DockStart 不会覆盖。",
            raw_error=str(target_path),
            suggestion="请更换项目名称、保存目录，或先清理已有 raw 文件。",
        )

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target_path, source.read_bytes())
        file_ref = project.receptor if is_receptor else project.ligand
        file_ref.source = "local_file"
        file_ref.source_id = source.name
        file_ref.query_type = "local_file"
        file_ref.downloaded_at = _now_iso()
        file_ref.raw_file = relative_file
        _invalidate_prepared_reference(file_ref)

        saved = save_project(project)
        if not saved.get("ok"):
            return saved

        return {
            **_success(project, f"{label}已复制到 raw/ 目录。"),
            "source": "local_file",
            "source_id": source.name,
            "query_type": "local_file",
            "format": file_format,
            "raw_file": relative_file,
        }
    except Exception as exc:  # noqa: BLE001 - UI needs structured errors.
        return _error(
            "LOCAL_RAW_IMPORT_ERROR",
            "导入本地原始结构文件时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目目录可写，源文件未被其他程序锁定。",
        )


def import_receptor_raw_file(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_local_raw_file(project_dir, source_path, "receptor")


def import_ligand_raw_file(project_dir: str, source_path: str) -> dict[str, Any]:
    return _import_local_raw_file(project_dir, source_path, "ligand")


def _invalidate_prepared_reference(file_ref: Any) -> None:
    """Mark the prepared input stale after acquiring a new raw structure.

    The current project schema does not record a trustworthy digest link from a
    raw structure to the PDBQT generated from it.  Therefore an existing PDBQT
    cannot be assumed to represent newly imported or downloaded raw input.  The
    file is intentionally left on disk for audit/recovery, while its active
    project reference is cleared so downstream workflow checks cannot treat it
    as ready.
    """

    file_ref.file = ""


def _rcsb_url(pdb_id: str, file_format: str) -> str:
    return f"https://files.rcsb.org/download/{pdb_id}.{file_format}"


def _pubchem_url(cid: str) -> str:
    return f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF"


def _pubchem_name_url(name: str) -> str:
    return f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(name, safe='')}/SDF"


def _safe_file_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return (slug or "query")[:80]


def fetch_pdb_structure(
    project_dir: str,
    pdb_id: str,
    format: str = "pdb",  # noqa: A002 - public API follows the task wording.
    overwrite: bool = False,
    fetcher: Fetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    validation = validate_pdb_id(pdb_id)
    if not validation.get("ok"):
        return validation

    file_format = str(format or "pdb").strip().lower()
    if file_format not in SUPPORTED_PDB_FORMATS:
        return _error(
            "PDB_FORMAT_UNSUPPORTED",
            "当前只支持下载 pdb 或 cif 格式的 RCSB 结构文件。",
            raw_error=file_format,
            suggestion="请选择 pdb；如需 cif，请确认当前版本前端已开放该选项。",
        )

    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None

    normalized_pdb_id = validation["pdb_id"]
    relative_file = Path("raw", f"receptor_{normalized_pdb_id}.{file_format}").as_posix()
    project_path = Path(project.project_dir).expanduser()
    target_path = project_path / relative_file
    url = _rcsb_url(normalized_pdb_id, file_format)

    data, download_error = _download(url, fetcher, timeout)
    if download_error:
        return download_error
    assert data is not None

    write_result = _write_raw_file(target_path, data, overwrite)
    if not write_result.get("ok"):
        return write_result

    project.receptor.source = "rcsb_pdb"
    project.receptor.source_id = normalized_pdb_id
    project.receptor.query_type = "pdb_id"
    project.receptor.downloaded_at = _now_iso()
    project.receptor.raw_file = relative_file
    _invalidate_prepared_reference(project.receptor)

    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    return {
        **_success(project, "RCSB PDB 原始受体结构已下载到 raw/ 目录。"),
        "source": "rcsb_pdb",
        "source_id": normalized_pdb_id,
        "format": file_format,
        "raw_file": relative_file,
        "url": url,
    }


def fetch_pubchem_ligand(
    project_dir: str,
    query: str | int,
    format: str = "sdf",  # noqa: A002 - public API follows the task wording.
    overwrite: bool = False,
    query_type: str = "cid",
    fetcher: Fetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    normalized_query_type = str(query_type or "cid").strip().lower()
    if normalized_query_type == "smiles":
        return _error(
            "PUBCHEM_SMILES_UNSUPPORTED",
            "SMILES 查询暂未支持。",
            raw_error=str(query or ""),
            suggestion="请先使用 PubChem CID 或名称查询。本阶段不会用 RDKit 解析 SMILES，也不会生成 3D 或 PDBQT。",
        )
    if normalized_query_type not in {"cid", "name"}:
        return _error(
            "PUBCHEM_QUERY_TYPE_UNSUPPORTED",
            "PubChem 查询类型暂不支持。",
            raw_error=normalized_query_type,
            suggestion="请选择 CID 或名称查询；SMILES 查询当前只提供暂未支持提示。",
        )

    if normalized_query_type == "cid":
        validation = validate_pubchem_cid(query)
        if not validation.get("ok"):
            return validation
        source_id = validation["cid"]
        relative_file = Path("raw", f"ligand_{source_id}.sdf").as_posix()
        url = _pubchem_url(source_id)
    else:
        validation = validate_pubchem_name(str(query))
        if not validation.get("ok"):
            return validation
        source_id = validation["name"]
        relative_file = Path("raw", f"ligand_name_{_safe_file_slug(source_id)}.sdf").as_posix()
        url = _pubchem_name_url(source_id)

    file_format = str(format or "sdf").strip().lower()
    if file_format not in SUPPORTED_PUBCHEM_FORMATS:
        return _error(
            "PUBCHEM_FORMAT_UNSUPPORTED",
            "当前 PubChem 下载只支持 sdf 格式。",
            raw_error=file_format,
            suggestion="请选择 sdf。后续格式转换会在准备流程中单独实现。",
        )

    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    target_path = project_path / relative_file

    data, download_error = _download(url, fetcher, timeout)
    if download_error:
        return download_error
    assert data is not None

    write_result = _write_raw_file(target_path, data, overwrite)
    if not write_result.get("ok"):
        return write_result

    project.ligand.source = "pubchem"
    project.ligand.source_id = source_id
    project.ligand.query_type = normalized_query_type
    project.ligand.downloaded_at = _now_iso()
    project.ligand.raw_file = relative_file
    _invalidate_prepared_reference(project.ligand)

    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    return {
        **_success(project, "PubChem 原始配体 SDF 已下载到 raw/ 目录。"),
        "source": "pubchem",
        "source_id": source_id,
        "query_type": normalized_query_type,
        "format": file_format,
        "raw_file": relative_file,
        "url": url,
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()


def _raw_file_status(project_path: Path, file_ref: Any, key: str, name: str) -> dict[str, Any]:
    relative_file = str(getattr(file_ref, "raw_file", "") or "")
    source = str(getattr(file_ref, "source", "") or "")
    source_id = str(getattr(file_ref, "source_id", "") or "")
    query_type = str(getattr(file_ref, "query_type", "") or "")
    downloaded_at = str(getattr(file_ref, "downloaded_at", "") or "")

    if not relative_file:
        return {
            "key": key,
            "name": name,
            "source": source,
            "source_id": source_id,
            "query_type": query_type,
            "downloaded_at": downloaded_at,
            "raw_file": "",
            "path": "",
            "exists": False,
            "is_file": False,
            "size": 0,
            "size_bytes": 0,
            "modified_at": "",
            "absolute_path": "",
            "record_consistent": False,
            "non_empty": False,
            "status": "missing",
            "message": f"{name} 尚未记录 raw 文件。",
            "raw_error": "",
        }

    path = project_path / relative_file
    raw_dir = project_path / "raw"
    resolved_path = path.resolve()
    resolved_raw_dir = raw_dir.resolve()
    is_inside_raw = _is_relative_to(resolved_path, resolved_raw_dir)
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    non_empty = size > 0
    modified_at = _modified_at(path) if exists and is_file else ""
    if not is_inside_raw:
        status = "error"
        message = f"{name} raw 记录不在项目 raw/ 目录内。"
    elif not exists:
        status = "missing"
        message = f"{name} raw 文件不存在。"
    elif not is_file:
        status = "error"
        message = f"{name} raw 路径不是文件。"
    elif not non_empty:
        status = "empty"
        message = f"{name} raw 文件为空。"
    else:
        status = "ok"
        message = f"{name} raw 文件存在。"

    return {
        "key": key,
        "name": name,
        "source": source,
        "source_id": source_id,
        "query_type": query_type,
        "downloaded_at": downloaded_at,
        "raw_file": relative_file,
        "path": relative_file,
        "exists": exists,
        "is_file": is_file,
        "size": size,
        "size_bytes": size,
        "modified_at": modified_at,
        "absolute_path": str(resolved_path),
        "record_consistent": is_inside_raw and exists and is_file and non_empty,
        "non_empty": non_empty,
        "status": status,
        "message": message,
        "raw_error": "" if status == "ok" else str(path),
    }


def get_raw_files_status(project_dir: str) -> dict[str, Any]:
    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    receptor = _raw_file_status(project_path, project.receptor, "receptor_raw", "受体原始结构")
    ligand = _raw_file_status(project_path, project.ligand, "ligand_raw", "配体原始结构")
    files = [receptor, ligand]
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "receptor": receptor,
        "ligand": ligand,
        "files": files,
        "message": "raw 文件状态已读取。",
        "error": None,
    }


def _clear_raw_record(project_dir: str, role: str, delete_file: bool = False) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("RAW_ROLE_INVALID", "raw 记录类型无效。")

    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None

    project_path = Path(project.project_dir).expanduser()
    raw_dir = (project_path / "raw").resolve()
    file_ref = getattr(project, role)
    raw_file = str(file_ref.raw_file or "")
    deleted_file = ""

    if delete_file and raw_file:
        target_path = (project_path / raw_file).resolve()
        if not _is_relative_to(target_path, raw_dir):
            return _error(
                "RAW_DELETE_OUTSIDE_RAW_DIR",
                "为了保护项目文件，只允许删除项目 raw/ 目录下的 raw 文件。",
                raw_error=str(target_path),
                suggestion="请先检查 project.json 中的 raw_file 记录是否正确。",
            )
        if target_path.exists():
            if not target_path.is_file():
                return _error(
                    "RAW_DELETE_TARGET_NOT_FILE",
                    "raw_file 指向的路径不是文件，DockStart 不会删除它。",
                    raw_error=str(target_path),
                    suggestion="请手动检查 raw/ 目录内容。",
                )
            target_path.unlink()
            deleted_file = str(target_path)

    file_ref.source = ""
    file_ref.source_id = ""
    file_ref.query_type = ""
    file_ref.downloaded_at = ""
    file_ref.raw_file = ""

    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    label = "受体" if role == "receptor" else "配体"
    response = get_raw_files_status(project.project_dir)
    if not response.get("ok"):
        return response
    response.update(
        {
            "message": f"{label} raw 记录已清除。" + (" raw 文件也已删除。" if deleted_file else ""),
            "deleted_file": deleted_file,
        },
    )
    return response


def clear_receptor_raw_record(project_dir: str, delete_file: bool = False) -> dict[str, Any]:
    return _clear_raw_record(project_dir, "receptor", delete_file)


def clear_ligand_raw_record(project_dir: str, delete_file: bool = False) -> dict[str, Any]:
    return _clear_raw_record(project_dir, "ligand", delete_file)


def _parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    if command == "fetch-pdb":
        if len(sys.argv) < 4:
            _print_json(_error("FETCH_PDB_ARGS", "下载 PDB 结构需要 project_dir 和 pdb_id 参数。"))
            return
        file_format = sys.argv[4] if len(sys.argv) > 4 else "pdb"
        overwrite = _parse_bool(sys.argv[5]) if len(sys.argv) > 5 else False
        _print_json(fetch_pdb_structure(sys.argv[2], sys.argv[3], file_format, overwrite))
        return

    if command == "fetch-pubchem":
        if len(sys.argv) < 4:
            _print_json(_error("FETCH_PUBCHEM_ARGS", "下载 PubChem 配体需要 project_dir 和查询值参数。"))
            return
        file_format = sys.argv[4] if len(sys.argv) > 4 else "sdf"
        overwrite = _parse_bool(sys.argv[5]) if len(sys.argv) > 5 else False
        query_type = sys.argv[6] if len(sys.argv) > 6 else "cid"
        _print_json(fetch_pubchem_ligand(sys.argv[2], sys.argv[3], file_format, overwrite, query_type))
        return

    if command == "raw-files-status":
        if len(sys.argv) < 3:
            _print_json(_error("RAW_FILES_STATUS_ARGS", "读取 raw 文件状态需要 project_dir 参数。"))
            return
        _print_json(get_raw_files_status(sys.argv[2]))
        return

    if command == "import-receptor-raw":
        if len(sys.argv) < 4:
            _print_json(_error("IMPORT_RECEPTOR_RAW_ARGS", "导入受体原始结构需要 project_dir 和 source_path 参数。"))
            return
        _print_json(import_receptor_raw_file(sys.argv[2], sys.argv[3]))
        return

    if command == "import-ligand-raw":
        if len(sys.argv) < 4:
            _print_json(_error("IMPORT_LIGAND_RAW_ARGS", "导入配体原始结构需要 project_dir 和 source_path 参数。"))
            return
        _print_json(import_ligand_raw_file(sys.argv[2], sys.argv[3]))
        return

    if command == "clear-receptor-raw":
        if len(sys.argv) < 3:
            _print_json(_error("CLEAR_RECEPTOR_RAW_ARGS", "清除受体 raw 记录需要 project_dir 参数。"))
            return
        delete_file = _parse_bool(sys.argv[3]) if len(sys.argv) > 3 else False
        _print_json(clear_receptor_raw_record(sys.argv[2], delete_file))
        return

    if command == "clear-ligand-raw":
        if len(sys.argv) < 3:
            _print_json(_error("CLEAR_LIGAND_RAW_ARGS", "清除配体 raw 记录需要 project_dir 参数。"))
            return
        delete_file = _parse_bool(sys.argv[3]) if len(sys.argv) > 3 else False
        _print_json(clear_ligand_raw_record(sys.argv[2], delete_file))
        return

    _print_json(_error("STRUCTURE_FETCH_COMMAND_UNKNOWN", f"未知结构下载命令：{command}"))


if __name__ == "__main__":
    main()
