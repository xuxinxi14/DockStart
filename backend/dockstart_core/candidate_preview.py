"""Read a remote structure candidate into memory for a temporary 3D preview.

This module deliberately has no project directory argument and performs no
filesystem writes.  A candidate must be selected explicitly by the caller;
the accepted payload is the ``selection`` object returned by structure search.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from dockstart_core import __version__
from dockstart_core.structure_fetch import validate_pdb_id, validate_pubchem_cid, validate_pubchem_name

DEFAULT_TIMEOUT_SECONDS = 30
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
SUPPORTED_RCSB_FORMATS = {"pdb", "cif"}

PreviewFetcher = Callable[[str, int], bytes]


class _PreviewTooLargeError(RuntimeError):
    """Signal that a remote response exceeds the hard preview payload limit."""


def _empty_response(
    *,
    provider: str = "",
    source_id: str = "",
    file_format: str = "",
    message: str = "",
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "provider": provider,
        "source_id": source_id,
        "format": file_format,
        "content": "",
        "size_bytes": 0,
        "message": message,
        "warnings": [],
        "error": error,
    }


def _preview_error(
    code: str,
    message: str,
    *,
    provider: str = "",
    source_id: str = "",
    file_format: str = "",
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return _empty_response(
        provider=provider,
        source_id=source_id,
        file_format=file_format,
        message=message,
        error={
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    )


def _fetch_bytes_limited(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"DockStart/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - allowlisted HTTPS endpoints.
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_PREVIEW_BYTES:
                    raise _PreviewTooLargeError(f"Content-Length={content_length}")
            except ValueError:
                pass
        data = response.read(MAX_PREVIEW_BYTES + 1)
    if len(data) > MAX_PREVIEW_BYTES:
        raise _PreviewTooLargeError(f"downloaded={len(data)}")
    return data


def _download_preview(
    url: str,
    *,
    fetcher: PreviewFetcher | None,
    timeout: int,
    byte_limit: int,
    provider: str,
    source_id: str,
    file_format: str,
) -> tuple[bytes | None, dict[str, Any] | None]:
    try:
        data = (fetcher or _fetch_bytes_limited)(url, timeout)
    except _PreviewTooLargeError as exc:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_TOO_LARGE",
            "候选结构过大，未加载 3D 预览。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=str(exc),
            suggestion=f"临时预览最多读取 {MAX_PREVIEW_BYTES // (1024 * 1024)} MB；可先下载目标结构，再在项目工作台中查看。",
        )
    except urllib.error.HTTPError as exc:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_HTTP_ERROR",
            "候选结构预览失败，远端服务返回错误。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=f"HTTP {exc.code}: {exc.reason}",
            suggestion="请确认候选仍然有效，稍后重试，或先下载目标结构。",
        )
    except urllib.error.URLError as exc:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_NETWORK_ERROR",
            "候选结构预览失败，可能是网络不可用或请求超时。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=str(exc.reason),
            suggestion="请检查网络连接后重试；预览失败不会改变项目文件。",
        )
    except TimeoutError as exc:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_TIMEOUT",
            "候选结构预览请求超时。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=str(exc),
            suggestion="请稍后重试，或先下载目标结构。",
        )
    except Exception as exc:  # noqa: BLE001 - the UI always receives a structured error.
        return None, _preview_error(
            "STRUCTURE_PREVIEW_DOWNLOAD_ERROR",
            "读取候选结构预览时发生错误。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=str(exc),
            suggestion="请重新选择候选并重试；预览失败不会改变项目文件。",
        )

    if not data:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_EMPTY",
            "候选结构返回了空内容，无法预览。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=url,
            suggestion="请重新搜索或选择其他候选。",
        )
    if len(data) > byte_limit:
        return None, _preview_error(
            "STRUCTURE_PREVIEW_TOO_LARGE",
            "候选结构过大，未加载 3D 预览。",
            provider=provider,
            source_id=source_id,
            file_format=file_format,
            raw_error=f"size_bytes={len(data)}; limit_bytes={byte_limit}",
            suggestion=f"临时预览最多读取 {MAX_PREVIEW_BYTES // (1024 * 1024)} MB；可先下载目标结构，再在项目工作台中查看。",
        )
    return data, None


def _selection_target(selection: dict[str, Any]) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    command = str(selection.get("download_command") or "").strip().lower()
    if command == "fetch-pdb":
        validated = validate_pdb_id(str(selection.get("pdb_id") or ""))
        if not validated.get("ok"):
            error = validated.get("error") or {}
            return None, _preview_error(
                str(error.get("code") or "PDB_ID_INVALID"),
                str(error.get("message") or "PDB ID 无效。"),
                provider="rcsb",
                raw_error=str(error.get("raw_error") or ""),
                suggestion=str(error.get("suggestion") or "请重新选择 RCSB 候选。"),
            )
        source_id = str(validated["pdb_id"])
        file_format = str(selection.get("format") or "pdb").strip().lower()
        if file_format not in SUPPORTED_RCSB_FORMATS:
            return None, _preview_error(
                "STRUCTURE_PREVIEW_FORMAT_UNSUPPORTED",
                "RCSB 候选预览只支持 PDB 或 mmCIF 格式。",
                provider="rcsb",
                source_id=source_id,
                file_format=file_format,
                raw_error=file_format,
                suggestion="请使用候选 selection 中的 pdb 或 cif 格式。",
            )
        return {
            "provider": "rcsb",
            "source_id": source_id,
            "format": file_format,
            "url": f"https://files.rcsb.org/download/{source_id}.{file_format}",
        }, None

    if command == "fetch-pubchem":
        query_type = str(selection.get("query_type") or "").strip().lower()
        query = str(selection.get("query") or "").strip()
        file_format = str(selection.get("format") or "sdf").strip().lower()
        if file_format != "sdf":
            return None, _preview_error(
                "STRUCTURE_PREVIEW_FORMAT_UNSUPPORTED",
                "PubChem 候选预览只支持 SDF 格式。",
                provider="pubchem",
                source_id=query,
                file_format=file_format,
                raw_error=file_format,
                suggestion="请使用候选 selection 中的 sdf 格式。",
            )
        if query_type == "cid":
            validated = validate_pubchem_cid(query)
            if validated.get("ok"):
                source_id = str(validated["cid"])
                url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{source_id}/SDF"
            else:
                error = validated.get("error") or {}
                return None, _preview_error(
                    str(error.get("code") or "PUBCHEM_CID_INVALID"),
                    str(error.get("message") or "PubChem CID 无效。"),
                    provider="pubchem",
                    source_id=query,
                    file_format=file_format,
                    raw_error=str(error.get("raw_error") or ""),
                    suggestion=str(error.get("suggestion") or "请重新选择 PubChem 候选。"),
                )
        elif query_type == "name":
            validated = validate_pubchem_name(query)
            if validated.get("ok"):
                source_id = str(validated["name"])
                url = (
                    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
                    f"{quote(source_id, safe='')}/SDF"
                )
            else:
                error = validated.get("error") or {}
                return None, _preview_error(
                    str(error.get("code") or "PUBCHEM_NAME_REQUIRED"),
                    str(error.get("message") or "PubChem 名称无效。"),
                    provider="pubchem",
                    source_id=query,
                    file_format=file_format,
                    raw_error=str(error.get("raw_error") or ""),
                    suggestion=str(error.get("suggestion") or "请重新选择 PubChem 候选。"),
                )
        else:
            return None, _preview_error(
                "STRUCTURE_PREVIEW_QUERY_TYPE_UNSUPPORTED",
                "PubChem 候选预览需要明确的 CID 或名称查询类型。",
                provider="pubchem",
                source_id=query,
                file_format=file_format,
                raw_error=query_type,
                suggestion="请直接使用搜索结果返回的 selection，不要手动省略 query_type。",
            )
        return {
            "provider": "pubchem",
            "source_id": source_id,
            "format": file_format,
            "url": url,
        }, None

    return None, _preview_error(
        "STRUCTURE_PREVIEW_SELECTION_REQUIRED" if not command else "STRUCTURE_PREVIEW_SELECTION_UNSUPPORTED",
        "请先明确选择一个候选结构，再加载 3D 预览。" if not command else "该候选类型暂不支持 3D 预览。",
        raw_error=command,
        suggestion="请从 RCSB 或 PubChem 候选列表中选择一项；DockStart 不会默认选择第一个结果。",
    )


def preview_candidate_structure(
    selection: dict[str, Any] | None,
    *,
    fetcher: PreviewFetcher | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    byte_limit: int = MAX_PREVIEW_BYTES,
) -> dict[str, Any]:
    """Return one explicitly selected remote structure without writing files."""

    if not isinstance(selection, dict) or not selection:
        return _preview_error(
            "STRUCTURE_PREVIEW_SELECTION_REQUIRED",
            "请先明确选择一个候选结构，再加载 3D 预览。",
            suggestion="请从候选列表中点击“预览”；DockStart 不会默认选择第一个结果。",
        )
    if not isinstance(byte_limit, int) or byte_limit < 1 or byte_limit > MAX_PREVIEW_BYTES:
        return _preview_error(
            "STRUCTURE_PREVIEW_LIMIT_INVALID",
            "候选结构预览大小上限无效。",
            raw_error=str(byte_limit),
            suggestion=f"预览上限必须在 1 字节到 {MAX_PREVIEW_BYTES} 字节之间。",
        )

    target, target_error = _selection_target(selection)
    if target_error:
        return target_error
    assert target is not None

    data, download_error = _download_preview(
        target["url"],
        fetcher=fetcher,
        timeout=timeout,
        byte_limit=byte_limit,
        provider=target["provider"],
        source_id=target["source_id"],
        file_format=target["format"],
    )
    if download_error:
        return download_error
    assert data is not None

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _preview_error(
            "STRUCTURE_PREVIEW_ENCODING_INVALID",
            "候选结构不是有效的 UTF-8 文本，无法安全预览。",
            provider=target["provider"],
            source_id=target["source_id"],
            file_format=target["format"],
            raw_error=str(exc),
            suggestion="请先下载该结构并检查文件编码，或选择其他候选。",
        )

    response = {
        "ok": True,
        "provider": target["provider"],
        "source_id": target["source_id"],
        "format": target["format"],
        "content": content,
        "size_bytes": len(data),
        "message": "候选结构已加载到临时 3D 预览；尚未写入当前项目。",
        "warnings": ["该预览只用于选择结构，不代表结构准备或科学验证已经完成。"],
        "error": None,
    }
    # The JSON transport adds escaping and metadata around ``content``. Enforce
    # the same hard ceiling on the complete response, not only the download.
    response_size = len(json.dumps(response, ensure_ascii=False).encode("utf-8")) + 1
    if response_size > MAX_PREVIEW_BYTES:
        return _preview_error(
            "STRUCTURE_PREVIEW_TOO_LARGE",
            "候选结构过大，未加载 3D 预览。",
            provider=target["provider"],
            source_id=target["source_id"],
            file_format=target["format"],
            raw_error=f"response_bytes={response_size}; limit_bytes={MAX_PREVIEW_BYTES}",
            suggestion=f"临时预览响应最多 {MAX_PREVIEW_BYTES // (1024 * 1024)} MB；可先下载目标结构，再在项目工作台中查看。",
        )
    return response


def _print_json(payload: dict[str, Any]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command == "preview-candidate":
        if len(sys.argv) < 3:
            _print_json(
                _preview_error(
                    "STRUCTURE_PREVIEW_SELECTION_REQUIRED",
                    "预览候选结构需要 selection JSON 参数。",
                    suggestion="请先从候选列表选择一项，再传入搜索结果中的 selection。",
                ),
            )
            return
        try:
            selection = json.loads(sys.argv[2])
        except json.JSONDecodeError as exc:
            _print_json(
                _preview_error(
                    "STRUCTURE_PREVIEW_SELECTION_JSON_INVALID",
                    "候选 selection JSON 无法解析。",
                    raw_error=str(exc),
                    suggestion="请直接传入搜索接口返回的 selection 对象。",
                ),
            )
            return
        _print_json(preview_candidate_structure(selection))
        return

    _print_json(_preview_error("STRUCTURE_PREVIEW_COMMAND_UNKNOWN", f"未知候选预览命令：{command}"))


if __name__ == "__main__":
    main()
