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

from dockstart_core import __version__
from dockstart_core.persistence import atomic_write_bytes
from dockstart_core.project import _error, _now_iso, _project_from_dict, _success, load_project, save_project

PDB_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{4}$")
SUPPORTED_PDB_FORMATS = {"pdb", "cif"}
SUPPORTED_PUBCHEM_FORMATS = {"sdf"}
SUPPORTED_LOCAL_RECEPTOR_FORMATS = {"pdb", "cif"}
SUPPORTED_LOCAL_LIGAND_FORMATS = {"sdf", "mol"}
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20

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


def validate_search_limit(limit: int | str) -> dict[str, Any]:
    """Validate the user-controlled candidate count for remote searches."""

    try:
        value = int(limit)
    except (TypeError, ValueError):
        return _error(
            "STRUCTURE_SEARCH_LIMIT_INVALID",
            "候选结果数量必须是整数。",
            raw_error=str(limit),
            suggestion=f"请输入 1 到 {MAX_SEARCH_LIMIT} 之间的整数。",
        )
    if value < 1 or value > MAX_SEARCH_LIMIT:
        return _error(
            "STRUCTURE_SEARCH_LIMIT_OUT_OF_RANGE",
            f"候选结果数量必须在 1 到 {MAX_SEARCH_LIMIT} 之间。",
            raw_error=str(limit),
            suggestion=f"建议先查看 {DEFAULT_SEARCH_LIMIT} 个候选；如有需要再调整。",
        )
    return {"ok": True, "limit": value, "error": None}


def _validate_search_query(query: str, provider_name: str) -> dict[str, Any]:
    value = str(query or "").strip()
    if not value:
        return _error(
            "STRUCTURE_SEARCH_QUERY_REQUIRED",
            f"{provider_name} 搜索内容不能为空。",
            suggestion="请输入结构 ID、化合物名称或关键词。",
        )
    if len(value) > 200:
        return _error(
            "STRUCTURE_SEARCH_QUERY_TOO_LONG",
            f"{provider_name} 搜索内容过长。",
            raw_error=value,
            suggestion="请缩短为 200 个字符以内的 ID、名称或关键词。",
        )
    return {"ok": True, "query": value, "error": None}


def _fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"DockStart/{__version__}"})
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


def _fetch_json(
    url: str,
    fetcher: Fetcher | None,
    timeout: int,
    provider_name: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read JSON from a fixed official endpoint with UI-safe errors."""

    try:
        data = (fetcher or _fetch_bytes)(url, timeout)
    except urllib.error.HTTPError as exc:
        return None, _error(
            "STRUCTURE_SEARCH_HTTP_ERROR",
            f"{provider_name} 搜索失败，远端服务返回错误。",
            raw_error=f"HTTP {exc.code}: {exc.reason}",
            suggestion="请检查 ID 或关键词，稍后重试；也可以手动下载后导入。",
        )
    except urllib.error.URLError as exc:
        return None, _error(
            "STRUCTURE_SEARCH_NETWORK_ERROR",
            f"{provider_name} 搜索失败，可能是网络不可用或请求超时。",
            raw_error=str(exc.reason),
            suggestion="请检查网络连接，或稍后重试。",
        )
    except TimeoutError as exc:
        return None, _error(
            "STRUCTURE_SEARCH_TIMEOUT",
            f"{provider_name} 搜索超时。",
            raw_error=str(exc),
            suggestion="请缩短关键词、减少候选数量，或稍后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - UI needs structured errors.
        return None, _error(
            "STRUCTURE_SEARCH_ERROR",
            f"{provider_name} 搜索时发生错误。",
            raw_error=str(exc),
            suggestion="请检查输入内容，或稍后重试。",
        )

    if not data:
        return None, _error(
            "STRUCTURE_SEARCH_EMPTY_RESPONSE",
            f"{provider_name} 搜索返回了空响应。",
            raw_error=url,
            suggestion="请稍后重试，或手动下载后导入。",
        )
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, _error(
            "STRUCTURE_SEARCH_RESPONSE_INVALID",
            f"{provider_name} 搜索返回了无法识别的数据。",
            raw_error=str(exc),
            suggestion="远端服务可能暂时异常，请稍后重试。",
        )
    if not isinstance(payload, dict):
        return None, _error(
            "STRUCTURE_SEARCH_RESPONSE_INVALID",
            f"{provider_name} 搜索返回的数据结构不符合预期。",
            raw_error=type(payload).__name__,
            suggestion="远端服务可能更新了接口，请稍后重试或报告此问题。",
        )
    return payload, None


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


def _rcsb_entry_metadata_url(pdb_id: str) -> str:
    return f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"


def _rcsb_entries_metadata_url(pdb_ids: list[str]) -> str:
    ids = ",".join(json.dumps(pdb_id) for pdb_id in pdb_ids)
    query = (
        "{entries(entry_ids:["
        f"{ids}"
        "]){rcsb_id struct{title} exptl{method} "
        "rcsb_entry_info{experimental_method resolution_combined polymer_entity_count "
        "nonpolymer_entity_count deposited_atom_count} "
        "rcsb_accession_info{initial_release_date} struct_keywords{text}}}"
    )
    return f"https://data.rcsb.org/graphql?query={quote(query, safe='')}"


def _rcsb_keyword_search_url(query: str, limit: int) -> str:
    payload = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": query},
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": limit},
            "scoring_strategy": "combined",
        },
    }
    encoded = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    return f"https://search.rcsb.org/rcsbsearch/v2/query?json={encoded}"


def _pubchem_autocomplete_url(query: str, limit: int) -> str:
    return (
        "https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete/compound/"
        f"{quote(query, safe='')}/json?limit={limit}"
    )


def _pubchem_property_url(cid: str) -> str:
    properties = "Title,MolecularFormula,MolecularWeight,IsomericSMILES,InChIKey"
    return f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/{properties}/JSON"


def _rcsb_candidate(metadata: dict[str, Any], pdb_id: str, score: Any = None) -> dict[str, Any]:
    entry_info = metadata.get("rcsb_entry_info") if isinstance(metadata.get("rcsb_entry_info"), dict) else {}
    accession = (
        metadata.get("rcsb_accession_info") if isinstance(metadata.get("rcsb_accession_info"), dict) else {}
    )
    structure = metadata.get("struct") if isinstance(metadata.get("struct"), dict) else {}
    keywords = metadata.get("struct_keywords") if isinstance(metadata.get("struct_keywords"), dict) else {}
    experiments = metadata.get("exptl") if isinstance(metadata.get("exptl"), list) else []

    title = str(structure.get("title") or pdb_id).strip()
    method = str(entry_info.get("experimental_method") or "").strip()
    if not method and experiments and isinstance(experiments[0], dict):
        method = str(experiments[0].get("method") or "").strip()

    resolution: float | None = None
    resolution_values = entry_info.get("resolution_combined")
    if isinstance(resolution_values, list) and resolution_values:
        try:
            resolution = float(resolution_values[0])
        except (TypeError, ValueError):
            resolution = None

    subtitle_parts = [part for part in (method, f"{resolution:g} Å" if resolution is not None else "") if part]
    return {
        "candidate_id": f"rcsb:{pdb_id}",
        "provider": "rcsb",
        "source_id": pdb_id,
        "title": title,
        "subtitle": " · ".join(subtitle_parts),
        "metadata": {
            "metadata_status": "ready",
            "experimental_method": method,
            "resolution_angstrom": resolution,
            "initial_release_date": str(accession.get("initial_release_date") or ""),
            "polymer_entity_count": entry_info.get("polymer_entity_count"),
            "nonpolymer_entity_count": entry_info.get("nonpolymer_entity_count"),
            "deposited_atom_count": entry_info.get("deposited_atom_count"),
            "keywords": str(keywords.get("text") or ""),
            "search_score": score,
        },
        "selection": {
            "download_command": "fetch-pdb",
            "pdb_id": pdb_id,
            "query_type": "pdb_id",
            "format": "pdb",
        },
    }


def _rcsb_candidate_without_metadata(pdb_id: str, score: Any, error: dict[str, Any]) -> dict[str, Any]:
    error_payload = error.get("error") if isinstance(error.get("error"), dict) else {}
    candidate = _rcsb_candidate({}, pdb_id, score)
    candidate["metadata"] = {
        "metadata_status": "unavailable",
        "search_score": score,
        "error_code": str(error_payload.get("code") or "STRUCTURE_SEARCH_ERROR"),
    }
    return candidate


def _search_response(
    provider: str,
    query: str,
    query_type: str,
    requested_limit: int,
    total_count: int,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    returned_count = len(candidates)
    return {
        "ok": True,
        "provider": provider,
        "query": query,
        "query_type": query_type,
        "requested_limit": requested_limit,
        "total_count": total_count,
        "returned_count": returned_count,
        "truncated": total_count > returned_count,
        "selection_required": True,
        "candidates": candidates,
        "message": (
            f"找到 {returned_count} 个候选，请明确选择后再下载。"
            if candidates
            else "没有找到可下载的候选，请调整 ID、名称或关键词。"
        ),
        "error": None,
    }


def search_rcsb_candidates(
    query: str,
    limit: int | str = DEFAULT_SEARCH_LIMIT,
    query_type: str = "auto",
    fetcher: Fetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return RCSB candidates without downloading or mutating a project."""

    query_validation = _validate_search_query(query, "RCSB PDB")
    if not query_validation.get("ok"):
        return query_validation
    limit_validation = validate_search_limit(limit)
    if not limit_validation.get("ok"):
        return limit_validation

    normalized_query = str(query_validation["query"])
    result_limit = int(limit_validation["limit"])
    normalized_type = str(query_type or "auto").strip().lower()
    if normalized_type not in {"auto", "pdb_id", "keyword"}:
        return _error(
            "RCSB_SEARCH_QUERY_TYPE_UNSUPPORTED",
            "RCSB 搜索类型暂不支持。",
            raw_error=normalized_type,
            suggestion="请选择自动识别、PDB ID 或关键词搜索。",
        )
    if normalized_type == "auto":
        normalized_type = "pdb_id" if PDB_ID_PATTERN.fullmatch(normalized_query) else "keyword"

    if normalized_type == "pdb_id":
        validation = validate_pdb_id(normalized_query)
        if not validation.get("ok"):
            return validation
        pdb_id = str(validation["pdb_id"])
        metadata, metadata_error = _fetch_json(
            _rcsb_entry_metadata_url(pdb_id), fetcher, timeout, "RCSB PDB"
        )
        if metadata_error:
            return metadata_error
        assert metadata is not None
        return _search_response("rcsb", normalized_query, normalized_type, result_limit, 1, [_rcsb_candidate(metadata, pdb_id)])

    search_payload, search_error = _fetch_json(
        _rcsb_keyword_search_url(normalized_query, result_limit), fetcher, timeout, "RCSB PDB"
    )
    if search_error:
        return search_error
    assert search_payload is not None

    raw_results = search_payload.get("result_set")
    if not isinstance(raw_results, list):
        raw_results = []
    candidate_rows: list[tuple[str, Any]] = []
    for raw_result in raw_results[:result_limit]:
        if not isinstance(raw_result, dict):
            continue
        pdb_id = str(raw_result.get("identifier") or "").strip().upper()
        if not PDB_ID_PATTERN.fullmatch(pdb_id):
            continue
        candidate_rows.append((pdb_id, raw_result.get("score")))

    metadata_by_id: dict[str, dict[str, Any]] = {}
    metadata_error: dict[str, Any] | None = None
    if candidate_rows:
        metadata_payload, metadata_error = _fetch_json(
            _rcsb_entries_metadata_url([pdb_id for pdb_id, _score in candidate_rows]),
            fetcher,
            timeout,
            "RCSB PDB",
        )
        if metadata_payload is not None:
            data = metadata_payload.get("data") if isinstance(metadata_payload.get("data"), dict) else {}
            entries = data.get("entries")
            if isinstance(entries, list):
                metadata_by_id = {
                    str(entry.get("rcsb_id") or "").upper(): entry
                    for entry in entries
                    if isinstance(entry, dict) and entry.get("rcsb_id")
                }

    candidates: list[dict[str, Any]] = []
    for pdb_id, score in candidate_rows:
        metadata = metadata_by_id.get(pdb_id)
        if metadata is not None:
            candidates.append(_rcsb_candidate(metadata, pdb_id, score))
            continue
        fallback_error = metadata_error or _error(
            "RCSB_METADATA_NOT_RETURNED",
            "RCSB PDB 未返回该候选的结构元数据。",
            raw_error=pdb_id,
            suggestion="仍可选择并下载该 PDB ID，下载后请人工核对结构。",
        )
        candidates.append(_rcsb_candidate_without_metadata(pdb_id, score, fallback_error))

    try:
        total_count = max(0, int(search_payload.get("total_count") or len(raw_results)))
    except (TypeError, ValueError):
        total_count = len(raw_results)
    return _search_response("rcsb", normalized_query, normalized_type, result_limit, total_count, candidates)


def _pubchem_candidate_from_property(property_record: dict[str, Any]) -> dict[str, Any] | None:
    cid_value = property_record.get("CID")
    validation = validate_pubchem_cid(cid_value)
    if not validation.get("ok"):
        return None
    cid = str(validation["cid"])
    title = str(property_record.get("Title") or f"PubChem CID {cid}").strip()
    return {
        "candidate_id": f"pubchem:{cid}",
        "provider": "pubchem",
        "source_id": cid,
        "title": title,
        "subtitle": " · ".join(
            str(value)
            for value in (property_record.get("MolecularFormula"), property_record.get("MolecularWeight"))
            if value not in (None, "")
        ),
        "metadata": {
            "metadata_status": "ready",
            "record_type": "compound",
            "molecular_formula": property_record.get("MolecularFormula"),
            "molecular_weight": property_record.get("MolecularWeight"),
            "isomeric_smiles": property_record.get("IsomericSMILES") or property_record.get("SMILES"),
            "inchi_key": property_record.get("InChIKey"),
        },
        "selection": {
            "download_command": "fetch-pubchem",
            "query": cid,
            "query_type": "cid",
            "format": "sdf",
        },
    }


def _pubchem_name_candidate(name: str) -> dict[str, Any]:
    return {
        "candidate_id": f"pubchem-name:{name}",
        "provider": "pubchem",
        "source_id": name,
        "title": name,
        "subtitle": "PubChem 名称候选",
        "metadata": {
            "metadata_status": "resolves_on_selection",
            "record_type": "compound_name_suggestion",
            "notice": "名称候选将在用户选择并下载时由 PubChem 解析为标准化合物记录。",
        },
        "selection": {
            "download_command": "fetch-pubchem",
            "query": name,
            "query_type": "name",
            "format": "sdf",
        },
    }


def search_pubchem_candidates(
    query: str,
    limit: int | str = DEFAULT_SEARCH_LIMIT,
    query_type: str = "auto",
    fetcher: Fetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return PubChem compound candidates without writing a project file."""

    query_validation = _validate_search_query(query, "PubChem")
    if not query_validation.get("ok"):
        return query_validation
    limit_validation = validate_search_limit(limit)
    if not limit_validation.get("ok"):
        return limit_validation

    normalized_query = str(query_validation["query"])
    result_limit = int(limit_validation["limit"])
    normalized_type = str(query_type or "auto").strip().lower()
    if normalized_type not in {"auto", "cid", "name", "keyword"}:
        return _error(
            "PUBCHEM_SEARCH_QUERY_TYPE_UNSUPPORTED",
            "PubChem 搜索类型暂不支持。",
            raw_error=normalized_type,
            suggestion="请选择自动识别、CID 或名称搜索。",
        )
    if normalized_type == "auto":
        normalized_type = "cid" if normalized_query.isdecimal() else "name"

    if normalized_type == "cid":
        validation = validate_pubchem_cid(normalized_query)
        if not validation.get("ok"):
            return validation
        cid = str(validation["cid"])
        property_payload, property_error = _fetch_json(
            _pubchem_property_url(cid), fetcher, timeout, "PubChem"
        )
        if property_error:
            return property_error
        assert property_payload is not None
        property_table = (
            property_payload.get("PropertyTable")
            if isinstance(property_payload.get("PropertyTable"), dict)
            else {}
        )
        raw_properties = property_table.get("Properties")
        if not isinstance(raw_properties, list):
            raw_properties = []
        candidates = [
            candidate
            for item in raw_properties[:1]
            if isinstance(item, dict)
            for candidate in [_pubchem_candidate_from_property(item)]
            if candidate is not None
        ]
        return _search_response("pubchem", normalized_query, normalized_type, result_limit, len(candidates), candidates)

    autocomplete_payload, autocomplete_error = _fetch_json(
        _pubchem_autocomplete_url(normalized_query, result_limit), fetcher, timeout, "PubChem"
    )
    if autocomplete_error:
        return autocomplete_error
    assert autocomplete_payload is not None
    dictionary_terms = (
        autocomplete_payload.get("dictionary_terms")
        if isinstance(autocomplete_payload.get("dictionary_terms"), dict)
        else {}
    )
    raw_names = dictionary_terms.get("compound")
    if not isinstance(raw_names, list):
        raw_names = []
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        name = str(raw_name or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= result_limit:
            break
    candidates = [_pubchem_name_candidate(name) for name in names]
    try:
        total_count = max(0, int(autocomplete_payload.get("total") or len(candidates)))
    except (TypeError, ValueError):
        total_count = len(candidates)
    return _search_response("pubchem", normalized_query, normalized_type, result_limit, total_count, candidates)


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

    if command == "search-rcsb":
        if len(sys.argv) < 3:
            _print_json(_error("SEARCH_RCSB_ARGS", "搜索 RCSB PDB 需要 query 参数。"))
            return
        limit = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_SEARCH_LIMIT
        query_type = sys.argv[4] if len(sys.argv) > 4 else "auto"
        _print_json(search_rcsb_candidates(sys.argv[2], limit, query_type))
        return

    if command == "search-pubchem":
        if len(sys.argv) < 3:
            _print_json(_error("SEARCH_PUBCHEM_ARGS", "搜索 PubChem 需要 query 参数。"))
            return
        limit = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_SEARCH_LIMIT
        query_type = sys.argv[4] if len(sys.argv) > 4 else "auto"
        _print_json(search_pubchem_candidates(sys.argv[2], limit, query_type))
        return

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
