"""Fetch raw receptor and ligand structure files for DockStart projects."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dockstart_core.project import _error, _project_from_dict, _success, load_project, save_project

PDB_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{4}$")
SUPPORTED_PDB_FORMATS = {"pdb", "cif"}
SUPPORTED_PUBCHEM_FORMATS = {"sdf"}
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
            suggestion="本轮只支持 PubChem CID，不支持名称或 SMILES。",
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


def _fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "DockStart/0.2.5"})
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
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)
    return {"ok": True, "path": str(target_path), "error": None}


def _rcsb_url(pdb_id: str, file_format: str) -> str:
    return f"https://files.rcsb.org/download/{pdb_id}.{file_format}"


def _pubchem_url(cid: str) -> str:
    return f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF"


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
    project.receptor.raw_file = relative_file
    if not project.receptor.file:
        project.receptor.file = "prepared/receptor.pdbqt"

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
    cid: str | int,
    format: str = "sdf",  # noqa: A002 - public API follows the task wording.
    overwrite: bool = False,
    fetcher: Fetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    validation = validate_pubchem_cid(cid)
    if not validation.get("ok"):
        return validation

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

    normalized_cid = validation["cid"]
    relative_file = Path("raw", f"ligand_{normalized_cid}.sdf").as_posix()
    project_path = Path(project.project_dir).expanduser()
    target_path = project_path / relative_file
    url = _pubchem_url(normalized_cid)

    data, download_error = _download(url, fetcher, timeout)
    if download_error:
        return download_error
    assert data is not None

    write_result = _write_raw_file(target_path, data, overwrite)
    if not write_result.get("ok"):
        return write_result

    project.ligand.source = "pubchem"
    project.ligand.source_id = normalized_cid
    project.ligand.raw_file = relative_file
    if not project.ligand.file:
        project.ligand.file = "prepared/ligand.pdbqt"

    saved = save_project(project)
    if not saved.get("ok"):
        return saved

    return {
        **_success(project, "PubChem 原始配体 SDF 已下载到 raw/ 目录。"),
        "source": "pubchem",
        "source_id": normalized_cid,
        "format": file_format,
        "raw_file": relative_file,
        "url": url,
    }


def _raw_file_status(project_path: Path, relative_file: str, key: str, name: str) -> dict[str, Any]:
    if not relative_file:
        return {
            "key": key,
            "name": name,
            "path": "",
            "exists": False,
            "is_file": False,
            "size": 0,
            "non_empty": False,
            "status": "missing",
            "message": f"{name} 尚未记录 raw 文件。",
            "raw_error": "",
        }

    path = project_path / relative_file
    exists = path.exists()
    is_file = path.is_file()
    size = path.stat().st_size if exists and is_file else 0
    non_empty = size > 0
    if not exists:
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
        "path": relative_file,
        "exists": exists,
        "is_file": is_file,
        "size": size,
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
    files = [
        _raw_file_status(project_path, project.receptor.raw_file, "receptor_raw", "受体原始结构"),
        _raw_file_status(project_path, project.ligand.raw_file, "ligand_raw", "配体原始结构"),
    ]
    return {
        "ok": True,
        "project_dir": project.project_dir,
        "project": project.to_dict(),
        "files": files,
        "message": "raw 文件状态已读取。",
        "error": None,
    }


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
            _print_json(_error("FETCH_PUBCHEM_ARGS", "下载 PubChem 配体需要 project_dir 和 cid 参数。"))
            return
        file_format = sys.argv[4] if len(sys.argv) > 4 else "sdf"
        overwrite = _parse_bool(sys.argv[5]) if len(sys.argv) > 5 else False
        _print_json(fetch_pubchem_ligand(sys.argv[2], sys.argv[3], file_format, overwrite))
        return

    if command == "raw-files-status":
        if len(sys.argv) < 3:
            _print_json(_error("RAW_FILES_STATUS_ARGS", "读取 raw 文件状态需要 project_dir 参数。"))
            return
        _print_json(get_raw_files_status(sys.argv[2]))
        return

    _print_json(_error("STRUCTURE_FETCH_COMMAND_UNKNOWN", f"未知结构下载命令：{command}"))


if __name__ == "__main__":
    main()
