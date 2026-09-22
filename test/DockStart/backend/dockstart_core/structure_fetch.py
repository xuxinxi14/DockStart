"""Fetch raw receptor and ligand structure files for DockStart projects."""

from __future__ import annotations

import copy
import json
import math
import os
import queue
import re
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dockstart_core import __version__
from dockstart_core.project import (
    ProjectFileSafetyError,
    _assert_directory_identity,
    _error,
    _existing_file_snapshot_for_publication,
    _now_iso,
    _preparation_target_lock,
    _project_from_dict,
    _publish_project_file_bytes,
    _read_external_file_snapshot_no_follow,
    _read_file_snapshot_no_follow,
    _rollback_file_publication,
    _safe_project_storage_file,
    _success,
    load_project,
    save_project,
)

PDB_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{4}$")
SUPPORTED_PDB_FORMATS = {"pdb", "cif"}
SUPPORTED_PUBCHEM_FORMATS = {"sdf"}
SUPPORTED_LOCAL_RECEPTOR_FORMATS = {"pdb", "cif"}
SUPPORTED_LOCAL_LIGAND_FORMATS = {"sdf", "mol", "mol2"}
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 20
MAX_SEARCH_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_STRUCTURE_DOWNLOAD_BYTES = 256 * 1024 * 1024
STRUCTURE_DOWNLOAD_CHUNK_BYTES = 1024 * 1024

Fetcher = Callable[[str, int | float], bytes]


class _SearchResponseTooLarge(RuntimeError):
    def __init__(self, observed_size: int, source: str) -> None:
        super().__init__(f"{source}={observed_size}")
        self.observed_size = observed_size
        self.source = source


class _StructureDownloadTooLarge(RuntimeError):
    def __init__(self, observed_size: int, source: str) -> None:
        super().__init__(f"{source}={observed_size}")
        self.observed_size = observed_size
        self.source = source


class _SearchDeadline:
    """Share one hard wall-clock budget across a complete remote search."""

    def __init__(self, timeout: int | float) -> None:
        try:
            seconds = float(timeout)
        except (TypeError, ValueError):
            seconds = 0.001
        if not math.isfinite(seconds) or seconds <= 0:
            seconds = 0.001
        self.timeout_seconds = seconds
        self.expires_at = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def timeout_error(self) -> TimeoutError:
        return TimeoutError(f"结构搜索超过总时限：{self.timeout_seconds:g} 秒")


class _DownloadDeadline(_SearchDeadline):
    """Hard wall-clock budget for one structure-file download."""

    def timeout_error(self) -> TimeoutError:
        return TimeoutError(f"结构下载超过总时限：{self.timeout_seconds:g} 秒")


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
            suggestion="如需按名称查询，请选择“名称”搜索方式；SMILES 查询当前暂未支持。",
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


def _fetch_bytes(url: str, timeout: int | float = DEFAULT_TIMEOUT_SECONDS) -> bytes:
    """Read one fixed-endpoint structure response with hard byte/time limits."""

    deadline = _DownloadDeadline(timeout)
    request = urllib.request.Request(url, headers={"User-Agent": f"DockStart/{__version__}"})
    with urllib.request.urlopen(request, timeout=deadline.remaining()) as response:  # noqa: S310 - fixed HTTPS endpoints only.
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = -1
            if declared_size > MAX_STRUCTURE_DOWNLOAD_BYTES:
                raise _StructureDownloadTooLarge(declared_size, "content_length")

        chunks: list[bytes] = []
        observed_size = 0
        while True:
            if deadline.remaining() <= 0:
                raise deadline.timeout_error()
            chunk = response.read(
                min(
                    STRUCTURE_DOWNLOAD_CHUNK_BYTES,
                    MAX_STRUCTURE_DOWNLOAD_BYTES - observed_size + 1,
                )
            )
            if not chunk:
                break
            observed_size += len(chunk)
            if observed_size > MAX_STRUCTURE_DOWNLOAD_BYTES:
                raise _StructureDownloadTooLarge(observed_size, "actual_bytes")
            chunks.append(chunk)
        if deadline.remaining() <= 0:
            raise deadline.timeout_error()
        return b"".join(chunks)


def _fetch_search_bytes(
    url: str,
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Read a bounded JSON response from DockStart's fixed official APIs."""

    request = urllib.request.Request(url, headers={"User-Agent": f"DockStart/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoints only.
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = -1
            if declared_size > MAX_SEARCH_RESPONSE_BYTES:
                raise _SearchResponseTooLarge(declared_size, "content_length")

        data = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
        if len(data) > MAX_SEARCH_RESPONSE_BYTES:
            raise _SearchResponseTooLarge(len(data), "actual_bytes")
        return data


def _fetch_search_bytes_with_deadline(
    url: str,
    fetcher: Fetcher | None,
    deadline: _SearchDeadline,
) -> bytes:
    """Read one search response without exceeding the search-wide deadline.

    ``urllib`` treats its timeout as a limit for individual blocking socket
    operations. A remote service that continuously drips bytes can therefore
    exceed the intended total request duration. The network read runs in a
    daemon worker so the short-lived structure-search Python process can exit
    immediately after returning a timeout payload instead of waiting for an
    abandoned request.
    """

    remaining = deadline.remaining()
    if remaining <= 0:
        raise deadline.timeout_error()

    result_queue: queue.Queue[tuple[bool, bytes | Exception]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            data = (
                fetcher(url, remaining)
                if fetcher
                else _fetch_search_bytes(url, remaining)
            )
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller thread.
            result_queue.put((False, exc))
        else:
            result_queue.put((True, data))

    thread = threading.Thread(
        target=worker,
        name="dockstart-structure-search",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=deadline.remaining())
    if thread.is_alive():
        raise deadline.timeout_error()

    try:
        succeeded, payload = result_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError("结构搜索线程没有返回结果。") from exc
    if succeeded:
        assert isinstance(payload, bytes)
        return payload
    assert isinstance(payload, Exception)
    raise payload


def _download(url: str, fetcher: Fetcher | None, timeout: int) -> tuple[bytes | None, dict[str, Any] | None]:
    deadline = _DownloadDeadline(timeout)
    result_queue: queue.Queue[tuple[bool, bytes | Exception]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            data = (fetcher or _fetch_bytes)(url, deadline.remaining())
            if not isinstance(data, bytes):
                raise TypeError(f"下载器必须返回 bytes，实际为 {type(data).__name__}")
            if len(data) > MAX_STRUCTURE_DOWNLOAD_BYTES:
                raise _StructureDownloadTooLarge(len(data), "actual_bytes")
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller thread.
            result_queue.put((False, exc))
        else:
            result_queue.put((True, data))

    try:
        thread = threading.Thread(
            target=worker,
            name="dockstart-structure-download",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=deadline.remaining())
        if thread.is_alive():
            raise deadline.timeout_error()
        try:
            succeeded, payload = result_queue.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError("结构下载线程没有返回结果。") from exc
        if not succeeded:
            assert isinstance(payload, Exception)
            raise payload
        assert isinstance(payload, bytes)
        data = payload
        if deadline.remaining() <= 0:
            raise deadline.timeout_error()
    except _StructureDownloadTooLarge as exc:
        return None, _error(
            "STRUCTURE_DOWNLOAD_TOO_LARGE",
            "下载的原始结构文件超过 256 MiB 安全上限，已停止读取。",
            raw_error=(
                f"{exc.source}={exc.observed_size} bytes; "
                f"limit={MAX_STRUCTURE_DOWNLOAD_BYTES} bytes"
            ),
            suggestion="请从官方来源手动下载并核对文件；超大结构不应直接通过当前下载入口导入。",
        )
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


def _validate_downloaded_structure(data: bytes, file_format: str) -> dict[str, Any]:
    """Reject obvious error pages or payloads that are not the requested format."""

    if not data or b"\x00" in data[: 1024 * 1024]:
        return _error(
            "STRUCTURE_DOWNLOAD_FORMAT_INVALID",
            "下载结果不是可识别的文本结构文件。",
            raw_error=f"format={file_format}; size={len(data)}",
            suggestion="远端服务可能返回了错误页面，请稍后重试或手动下载并导入。",
        )
    try:
        sample = data[: 4 * 1024 * 1024].decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return _error(
            "STRUCTURE_DOWNLOAD_FORMAT_INVALID",
            "下载结果不是有效的 UTF-8 文本结构文件。",
            raw_error=str(exc),
            suggestion="请从官方结构页面手动下载对应格式并核对文件。",
        )

    normalized = sample.lstrip()
    lowered = normalized[:4096].casefold()
    if (
        lowered.startswith("<!doctype html")
        or lowered.startswith("<html")
        or lowered.startswith("<?xml")
        or lowered.startswith("{")
        or lowered.startswith("[")
    ):
        return _error(
            "STRUCTURE_DOWNLOAD_FORMAT_INVALID",
            "远端服务返回的内容不是请求的结构文件。",
            raw_error=f"format={file_format}; prefix={normalized[:80]!r}",
            suggestion="请确认结构 ID 或名称有效，稍后重试，或手动下载后导入。",
        )

    valid = False
    if file_format == "pdb":
        valid = re.search(
            r"(?m)^(?:HEADER|TITLE |COMPND|SOURCE|KEYWDS|EXPDTA|AUTHOR|REMARK|DBREF |SEQRES|CRYST1|MODEL |ATOM  |HETATM)",
            sample,
        ) is not None
    elif file_format == "cif":
        valid = re.search(r"(?m)^data_[^\s]+", sample) is not None
    elif file_format == "sdf":
        valid = re.search(r"(?m)^M  END\s*$", sample) is not None

    if not valid:
        return _error(
            "STRUCTURE_DOWNLOAD_FORMAT_INVALID",
            f"下载结果不符合 {file_format.upper()} 文件的最小结构特征。",
            raw_error=f"format={file_format}; size={len(data)}",
            suggestion="请确认远端条目存在且格式正确，或手动下载后导入。",
        )
    return {"ok": True, "format": file_format, "size_bytes": len(data), "error": None}


def _fetch_json(
    url: str,
    fetcher: Fetcher | None,
    timeout: int | float,
    provider_name: str,
    deadline: _SearchDeadline | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read JSON from a fixed official endpoint with UI-safe errors."""

    active_deadline = deadline or _SearchDeadline(timeout)
    try:
        data = _fetch_search_bytes_with_deadline(url, fetcher, active_deadline)
        if len(data) > MAX_SEARCH_RESPONSE_BYTES:
            raise _SearchResponseTooLarge(len(data), "actual_bytes")
    except _SearchResponseTooLarge as exc:
        return None, _error(
            "STRUCTURE_SEARCH_RESPONSE_TOO_LARGE",
            f"{provider_name} 搜索响应超过 4 MiB 安全上限，已停止读取。",
            raw_error=(
                f"{exc.source}={exc.observed_size} bytes; "
                f"limit={MAX_SEARCH_RESPONSE_BYTES} bytes"
            ),
            suggestion="请减少候选数量、缩短关键词后重试，或手动下载并导入目标结构。",
        )
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
    if active_deadline.remaining() <= 0:
        timeout_error = active_deadline.timeout_error()
        return None, _error(
            "STRUCTURE_SEARCH_TIMEOUT",
            f"{provider_name} 搜索超时。",
            raw_error=str(timeout_error),
            suggestion="请缩短关键词、减少候选数量，或稍后重试。",
        )
    return payload, None


def _load_project_for_raw(project_dir: str) -> tuple[Any | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, loaded
    return _project_from_dict(loaded["project"], Path(project_dir).expanduser()), None


def _preparation_running_error(project: Any, target: str) -> dict[str, Any] | None:
    preparation = getattr(project, "preparation", None)
    target_state = getattr(preparation, target, None)
    if str(getattr(target_state, "status", "") or "") != "running":
        return None

    label = "受体" if target == "receptor" else "配体"
    prep_id = str(getattr(target_state, "prep_id", "") or "")
    return _error(
        "PREPARATION_IN_PROGRESS",
        f"{label}格式转换仍在运行，当前不能替换对应的原始结构。",
        raw_error=f"target={target}; prep_id={prep_id or 'unknown'}",
        suggestion="请等待转换结束；如任务已异常中断，请先在“格式转换与 PDBQT 准备”页面恢复或重置状态。",
    )


def _append_rollback_failure(
    result: dict[str, Any],
    rollback_error: str,
    operation: str,
) -> dict[str, Any]:
    failed = copy.deepcopy(result)
    error = failed.get("error") if isinstance(failed.get("error"), dict) else {}
    raw_error = str(error.get("raw_error") or "")
    detail = f"{operation} rollback failed: {rollback_error}"
    error["raw_error"] = f"{raw_error}; {detail}" if raw_error else detail
    error["suggestion"] = (
        "project.json 提交失败且 raw 文件无法自动恢复；"
        "请停止格式转换并人工核对 project.json 与 raw/ 目录。"
    )
    failed["error"] = error
    return failed


def _commit_raw_file(
    project_dir: str,
    *,
    role: str,
    relative_file: str,
    payload: bytes,
    overwrite: bool,
    source: str,
    source_id: str,
    query_type: str,
    message: str,
    exists_code: str = "RAW_FILE_EXISTS",
    exists_message: str = "raw 文件已存在，当前设置不会覆盖。",
    exists_suggestion: str = "如需重新下载，请开启 overwrite。",
) -> dict[str, Any]:
    """Publish one raw file and its project reference as a rollback transaction."""

    if role not in {"receptor", "ligand"}:
        return _error("RAW_ROLE_INVALID", "raw 记录类型无效。")
    try:
        with _preparation_target_lock(project_dir, role):
            project, project_error = _load_project_for_raw(project_dir)
            if project_error:
                return project_error
            assert project is not None
            running_error = _preparation_running_error(project, role)
            if running_error:
                return running_error

            project_path = Path(project.project_dir).expanduser().resolve(strict=True)
            target_path, parent_identity = _safe_project_storage_file(
                project_path,
                relative_file,
                "raw",
            )
            try:
                publication = _publish_project_file_bytes(
                    target_path,
                    payload,
                    overwrite=overwrite,
                    parent_identity=parent_identity,
                )
            except FileExistsError:
                return _error(
                    exists_code,
                    exists_message,
                    raw_error=str(target_path),
                    suggestion=exists_suggestion,
                )
            rollback_required = True
            try:
                file_ref = getattr(project, role)
                file_ref.source = source
                file_ref.source_id = source_id
                file_ref.query_type = query_type
                file_ref.downloaded_at = _now_iso()
                file_ref.raw_file = relative_file
                _invalidate_prepared_reference(file_ref)

                saved = save_project(project)
                if not saved.get("ok"):
                    rollback_error = _rollback_file_publication(publication)
                    rollback_required = False
                    return (
                        _append_rollback_failure(saved, rollback_error, "raw publication")
                        if rollback_error
                        else saved
                    )
                rollback_required = False
                return {
                    **_success(project, message),
                    "source": source,
                    "source_id": source_id,
                    "query_type": query_type,
                    "raw_file": relative_file,
                }
            finally:
                if rollback_required:
                    rollback_error = _rollback_file_publication(publication)
                    if rollback_error:
                        raise RuntimeError(
                            f"raw 文件事务异常且回滚失败：{rollback_error}"
                        )
    except ProjectFileSafetyError as exc:
        return _error(
            exc.code,
            "项目 raw 路径不安全，已拒绝写入。",
            raw_error=str(exc),
            suggestion="请恢复项目内普通的 raw/ 目录和文件，移除符号链接、junction 或硬链接后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - return UI-safe errors.
        return _error(
            "RAW_FILE_WRITE_ERROR",
            "写入项目 raw 文件时发生错误。",
            raw_error=str(exc),
            suggestion="请确认项目 raw/ 目录可写且没有被其他程序替换。",
        )


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
        else "配体原始结构当前支持 SDF（.sdf）、MOL（.mol）和 MOL2（.mol2）；PDB 与 SMILES 暂不支持内置转换。"
    )
    validation = _validate_local_raw_file(source_path, supported_formats, label, format_suggestion)
    if not validation.get("ok"):
        return validation

    source = Path(str(validation["path"]))
    file_format = str(validation["format"])
    relative_file = Path("raw", f"{role}_{_safe_file_slug(source.stem)}.{file_format}").as_posix()
    try:
        payload = _read_external_file_snapshot_no_follow(source)
    except OSError as exc:
        return _error(
            "LOCAL_RAW_SOURCE_UNSAFE",
            f"{label}源文件不是稳定的普通文件，已拒绝导入。",
            raw_error=str(exc),
            suggestion="请选择不经过符号链接、junction 或 reparse point 的普通结构文件。",
        )
    if not payload:
        return _error(
            "LOCAL_RAW_FILE_EMPTY",
            f"{label}文件为空。",
            raw_error=str(source),
            suggestion="请确认该文件是有效的结构文件。",
        )

    result = _commit_raw_file(
        project_dir,
        role=role,
        relative_file=relative_file,
        payload=payload,
        overwrite=False,
        source="local_file",
        source_id=source.name,
        query_type="local_file",
        message=f"{label}已复制到 raw/ 目录。",
        exists_code="LOCAL_RAW_FILE_EXISTS",
        exists_message="项目 raw/ 目录中已经存在同名文件，DockStart 不会覆盖。",
        exists_suggestion="请更换文件名，或先清理已有 raw 文件。",
    )
    if result.get("ok"):
        result["format"] = file_format
    return result


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
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
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

    deadline = _SearchDeadline(timeout)
    if normalized_type == "pdb_id":
        validation = validate_pdb_id(normalized_query)
        if not validation.get("ok"):
            return validation
        pdb_id = str(validation["pdb_id"])
        metadata, metadata_error = _fetch_json(
            _rcsb_entry_metadata_url(pdb_id),
            fetcher,
            timeout,
            "RCSB PDB",
            deadline,
        )
        if metadata_error:
            return metadata_error
        assert metadata is not None
        return _search_response("rcsb", normalized_query, normalized_type, result_limit, 1, [_rcsb_candidate(metadata, pdb_id)])

    search_payload, search_error = _fetch_json(
        _rcsb_keyword_search_url(normalized_query, result_limit),
        fetcher,
        timeout,
        "RCSB PDB",
        deadline,
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
            deadline,
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
    timeout: int | float = DEFAULT_TIMEOUT_SECONDS,
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

    deadline = _SearchDeadline(timeout)
    if normalized_type == "cid":
        validation = validate_pubchem_cid(normalized_query)
        if not validation.get("ok"):
            return validation
        cid = str(validation["cid"])
        property_payload, property_error = _fetch_json(
            _pubchem_property_url(cid),
            fetcher,
            timeout,
            "PubChem",
            deadline,
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
        _pubchem_autocomplete_url(normalized_query, result_limit),
        fetcher,
        timeout,
        "PubChem",
        deadline,
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
            suggestion="请选择 PDB；如需 mmCIF，请在搜索表单中将下载格式改为 mmCIF。",
        )

    project, project_error = _load_project_for_raw(project_dir)
    if project_error:
        return project_error
    assert project is not None
    running_error = _preparation_running_error(project, "receptor")
    if running_error:
        return running_error

    normalized_pdb_id = validation["pdb_id"]
    relative_file = Path("raw", f"receptor_{normalized_pdb_id}.{file_format}").as_posix()
    url = _rcsb_url(normalized_pdb_id, file_format)

    data, download_error = _download(url, fetcher, timeout)
    if download_error:
        return download_error
    assert data is not None
    format_validation = _validate_downloaded_structure(data, file_format)
    if not format_validation.get("ok"):
        return format_validation

    result = _commit_raw_file(
        project_dir,
        role="receptor",
        relative_file=relative_file,
        payload=data,
        overwrite=overwrite,
        source="rcsb_pdb",
        source_id=normalized_pdb_id,
        query_type="pdb_id",
        message="RCSB PDB 原始受体结构已下载到 raw/ 目录。",
    )
    if result.get("ok"):
        result.update({"format": file_format, "url": url})
    return result


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
    running_error = _preparation_running_error(project, "ligand")
    if running_error:
        return running_error

    data, download_error = _download(url, fetcher, timeout)
    if download_error:
        return download_error
    assert data is not None
    format_validation = _validate_downloaded_structure(data, file_format)
    if not format_validation.get("ok"):
        return format_validation

    result = _commit_raw_file(
        project_dir,
        role="ligand",
        relative_file=relative_file,
        payload=data,
        overwrite=overwrite,
        source="pubchem",
        source_id=source_id,
        query_type=normalized_query_type,
        message="PubChem 原始配体 SDF 已下载到 raw/ 目录。",
    )
    if result.get("ok"):
        result.update({"format": file_format, "url": url})
    return result


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
    exists = False
    is_file = False
    size = 0
    non_empty = False
    modified_at = ""
    absolute_path = str(path.absolute())
    path_safe = False
    hardlinked = False
    safety_error = ""
    try:
        path, parent_identity = _safe_project_storage_file(
            project_path,
            relative_file,
            "raw",
        )
        absolute_path = str(path.absolute())
        path_safe = True
        exists = os.path.lexists(path)
        if exists:
            details = os.lstat(path)
            is_file = stat.S_ISREG(details.st_mode)
            hardlinked = is_file and int(getattr(details, "st_nlink", 1) or 1) > 1
            size = int(details.st_size) if is_file else 0
            non_empty = size > 0
            modified_at = (
                datetime.fromtimestamp(details.st_mtime, UTC)
                .replace(microsecond=0)
                .isoformat()
                if is_file
                else ""
            )
        _assert_directory_identity(path.parent, parent_identity)
    except (ProjectFileSafetyError, OSError) as exc:
        path_safe = False
        safety_error = str(exc)

    if not path_safe:
        status = "error"
        message = f"{name} raw 路径不安全或越出项目 raw/ 目录。"
    elif not exists:
        status = "missing"
        message = f"{name} raw 文件不存在。"
    elif not is_file:
        status = "error"
        message = f"{name} raw 路径不是普通文件。"
    elif hardlinked:
        status = "error"
        message = f"{name} raw 文件存在多个硬链接，已标记为不安全。"
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
        "absolute_path": absolute_path,
        "record_consistent": path_safe and exists and is_file and not hardlinked and non_empty,
        "non_empty": non_empty,
        "status": status,
        "message": message,
        "raw_error": "" if status == "ok" else (safety_error or str(path)),
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


def _stage_raw_file_deletion(
    target_path: Path,
    parent_identity: tuple[int, int],
) -> dict[str, Any] | None:
    """Move a raw file to a sibling tombstone before committing project.json."""

    existed, snapshot = _existing_file_snapshot_for_publication(target_path)
    if not existed:
        return None
    _assert_directory_identity(target_path.parent, parent_identity)
    tombstone: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target_path.name}.delete-",
            suffix=".tmp",
            dir=target_path.parent,
            delete=False,
        ) as handle:
            handle.flush()
            os.fsync(handle.fileno())
            tombstone = Path(handle.name)
        os.replace(target_path, tombstone)
        _assert_directory_identity(target_path.parent, parent_identity)
        if _read_file_snapshot_no_follow(tombstone) != snapshot:
            raise OSError("raw 删除暂存文件与原文件快照不一致")
        return {
            "target": target_path,
            "tombstone": tombstone,
            "snapshot": snapshot,
            "parent_identity": parent_identity,
        }
    except Exception:
        if tombstone is not None and os.path.lexists(tombstone) and not os.path.lexists(target_path):
            try:
                os.replace(tombstone, target_path)
            except OSError:
                pass
        elif tombstone is not None and os.path.lexists(tombstone):
            try:
                tombstone.unlink()
            except OSError:
                pass
        raise


def _rollback_staged_raw_deletion(staged: dict[str, Any]) -> str:
    try:
        target = Path(staged["target"])
        tombstone = Path(staged["tombstone"])
        snapshot = bytes(staged["snapshot"])
        parent_identity = staged["parent_identity"]
        _assert_directory_identity(target.parent, parent_identity)
        if os.path.lexists(target):
            raise OSError("raw 原路径已被其他操作占用，拒绝覆盖")
        existed, current = _existing_file_snapshot_for_publication(tombstone)
        if not existed or current != snapshot:
            raise OSError("raw 删除暂存文件已发生变化")
        os.replace(tombstone, target)
        _assert_directory_identity(target.parent, parent_identity)
        if _read_file_snapshot_no_follow(target) != snapshot:
            raise OSError("raw 文件回滚后的字节不一致")
        return ""
    except Exception as exc:  # noqa: BLE001 - caller records rollback diagnostics.
        return str(exc)


def _finalize_staged_raw_deletion(staged: dict[str, Any]) -> str:
    try:
        tombstone = Path(staged["tombstone"])
        snapshot = bytes(staged["snapshot"])
        parent_identity = staged["parent_identity"]
        _assert_directory_identity(tombstone.parent, parent_identity)
        existed, current = _existing_file_snapshot_for_publication(tombstone)
        if not existed or current != snapshot:
            raise OSError("raw 删除暂存文件已发生变化，未自动删除")
        tombstone.unlink()
        _assert_directory_identity(tombstone.parent, parent_identity)
        return ""
    except Exception as exc:  # noqa: BLE001 - committed project remains authoritative.
        return str(exc)


def _clear_raw_record(project_dir: str, role: str, delete_file: bool = False) -> dict[str, Any]:
    if role not in {"receptor", "ligand"}:
        return _error("RAW_ROLE_INVALID", "raw 记录类型无效。")

    try:
        with _preparation_target_lock(project_dir, role):
            project, project_error = _load_project_for_raw(project_dir)
            if project_error:
                return project_error
            assert project is not None
            running_error = _preparation_running_error(project, role)
            if running_error:
                return running_error

            project_path = Path(project.project_dir).expanduser().resolve(strict=True)
            file_ref = getattr(project, role)
            raw_file = str(file_ref.raw_file or "")
            deleted_file = ""
            staged: dict[str, Any] | None = None

            if delete_file and raw_file:
                try:
                    target_path, parent_identity = _safe_project_storage_file(
                        project_path,
                        raw_file,
                        "raw",
                    )
                except ProjectFileSafetyError as exc:
                    code = (
                        "RAW_DELETE_OUTSIDE_RAW_DIR"
                        if exc.code == "PROJECT_STORAGE_PATH_OUTSIDE_ROOT"
                        else "RAW_DELETE_PATH_UNSAFE"
                    )
                    return _error(
                        code,
                        "为了保护项目文件，只允许删除项目 raw/ 目录中的普通文件。",
                        raw_error=str(exc),
                        suggestion="请先检查 project.json 中的 raw_file 记录以及 raw/ 目录是否含链接。",
                    )
                staged = _stage_raw_file_deletion(target_path, parent_identity)
                if staged is not None:
                    deleted_file = str(target_path)
            rollback_required = staged is not None
            try:
                file_ref.source = ""
                file_ref.source_id = ""
                file_ref.query_type = ""
                file_ref.downloaded_at = ""
                file_ref.raw_file = ""

                saved = save_project(project)
                if not saved.get("ok"):
                    if staged is not None:
                        rollback_error = _rollback_staged_raw_deletion(staged)
                        rollback_required = False
                        if rollback_error:
                            return _append_rollback_failure(saved, rollback_error, "raw deletion")
                    return saved

                rollback_required = False
                cleanup_warning = _finalize_staged_raw_deletion(staged) if staged is not None else ""
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
                if cleanup_warning:
                    response.setdefault("warnings", []).append(
                        "raw 记录已提交，但删除暂存文件清理失败；请关闭 DockStart 后检查 raw/ 中的隐藏 .delete-*.tmp 文件。"
                    )
                    response["cleanup_error"] = cleanup_warning
                return response
            finally:
                if rollback_required and staged is not None:
                    rollback_error = _rollback_staged_raw_deletion(staged)
                    if rollback_error:
                        raise RuntimeError(
                            f"raw 清除事务异常且文件回滚失败：{rollback_error}"
                        )
    except ProjectFileSafetyError as exc:
        return _error(
            exc.code,
            "项目 raw 路径不安全，已拒绝清除。",
            raw_error=str(exc),
            suggestion="请恢复普通的 raw/ 目录和文件，移除符号链接、junction 或硬链接后重试。",
        )
    except Exception as exc:  # noqa: BLE001 - UI needs structured errors.
        return _error(
            "RAW_CLEAR_ERROR",
            "清除 raw 记录时发生错误，project.json 未提交不完整状态。",
            raw_error=str(exc),
            suggestion="请确认项目目录可写，并检查 raw/ 目录是否被其他程序占用。",
        )


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
