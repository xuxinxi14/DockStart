"""Verify the metadata-only 2BYS/2ZJU hydrated AD4 acceptance contract.

The verifier deliberately commits no upstream structure or generated result.
It downloads RCSB sources into an isolated temporary directory, verifies exact
wire bytes for receptor PDB files and canonical scientific mol-block identity
for ModelServer ligand SDF files, exercises the current DockStart public
project API, records provenance, and removes the temporary directory on both
success and failure.

This is an engineering-chain gate.  Historical affinities and pose RMSDs are
diagnostics only; neither is a scientific redocking success oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import (  # noqa: E402
    autogrid_adapter,
    meeko_adapter,
    python_adapter,
    rdkit_adapter,
    vina_adapter,
)
from dockstart_core.advanced_protocols import (  # noqa: E402
    MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION,
    meeko_receptor_control_arguments,
    normalize_meeko_receptor_controls,
)
from dockstart_core.hydrated import (  # noqa: E402
    generate_hydrated_maps,
    get_status as get_hydrated_status,
    prepare_hydrated_ligand,
)
from dockstart_core.hydrated_run import (  # noqa: E402
    get_hydrated_run_preflight,
    load_hydrated_results,
    prepare_hydrated_run,
    vina_affinity_serialization_matches,
)
from dockstart_core.preparation import (  # noqa: E402
    prepare_ligand_pdbqt,
    prepare_receptor_pdbqt,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    update_box_params,
    update_vina_params,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    save_settings,
)
from dockstart_core.structure_fetch import (  # noqa: E402
    import_ligand_raw_file,
    import_receptor_raw_file,
)
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR  # noqa: E402

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "hydrated_multisystem"
    / "source_manifest.json"
)
FIXTURE_FILES = frozenset({"README.md", "source_manifest.json"})
SYSTEM_IDS = ("2BYS", "2ZJU")
MANIFEST_CONTAINS_FLAGS = (
    "contains_upstream_structure_files",
    "contains_parameter_files",
    "contains_maps_or_outputs",
    "contains_executables",
)
PREPARATION_TOOLS_SNAPSHOT_ENV_VAR = "DOCKSTART_PREPARATION_TOOLS_JSON"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MODEL_SERVER_SDF_CANONICALIZATION = "model_server_sdf_mol_block_lf_v1"
MODEL_PATTERN = re.compile(r"^\s*MODEL\s+(\d+)\s*$")
AFFINITY_PATTERN = re.compile(
    r"^\s*REMARK\s+VINA\s+RESULT:\s+"
    r"(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)\s+"
    r"(-?\d+(?:\.\d+)?)"
)
Fetcher = Callable[..., Any]


class HydratedMultisystemError(RuntimeError):
    """Stable JSON-serializable verifier failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.steps: dict[str, Any] = {}


def _fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise HydratedMultisystemError(code, message, details=details)


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if urllib.parse.urlsplit(resolved).scheme.lower() != "https":
            _fail(
                "HYDRATED_MULTISYSTEM_DOWNLOAD_REDIRECT_INVALID",
                "RCSB 下载重定向链包含非 HTTPS 跳转。",
                details={
                    "from_url": req.full_url,
                    "target_url": resolved,
                    "status_code": code,
                },
            )
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            resolved,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _canonicalize_model_server_sdf(payload: bytes) -> bytes:
    """Return the single-record SDF mol block with stable LF framing.

    RCSB ModelServer appends request/job/statistics properties whose values and
    even capitalization can change between equivalent downloads.  They are
    retained in the downloaded wire file for audit, but are deliberately
    excluded from the cross-run identity oracle.  Atom, bond, coordinate,
    charge, and every other mol-block line remain byte-significant after
    newline normalization.
    """

    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "HYDRATED_MULTISYSTEM_SDF_CANONICALIZATION_INVALID",
            "ModelServer SDF 不是有效 UTF-8，无法建立科学内容身份。",
            details={"error": str(exc)},
        )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n"
    lines = normalized.splitlines()
    m_end_indexes = [
        index for index, line in enumerate(lines) if line == "M  END"
    ]
    record_end_indexes = [
        index for index, line in enumerate(lines) if line == "$$$$"
    ]
    if (
        len(m_end_indexes) != 1
        or len(record_end_indexes) != 1
        or record_end_indexes[0] != len(lines) - 1
        or m_end_indexes[0] >= record_end_indexes[0]
        or m_end_indexes[0] < 3
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_SDF_CANONICALIZATION_INVALID",
            "ModelServer SDF 必须是含唯一 M  END、并以 $$$$ 结束的单一记录。",
            details={
                "m_end_count": len(m_end_indexes),
                "record_end_count": len(record_end_indexes),
            },
        )
    canonical_lines = lines[: m_end_indexes[0] + 1]
    return ("\n".join(canonical_lines) + "\n\n").encode("utf-8")


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(value)))


def _tool_payload(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        payload = result.to_dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return dict(vars(result)) if hasattr(result, "__dict__") else {}


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        _fail(
            "HYDRATED_MULTISYSTEM_FILE_MISSING",
            f"{label} 不存在或为空。",
            details={"path": str(resolved)},
        )
    return resolved


def _project_path(
    project_root: Path,
    value: str | Path,
    label: str,
    *,
    allow_empty: bool = False,
) -> Path:
    text = os.fspath(value).strip()
    if not text:
        if allow_empty:
            return project_root
        _fail(
            "HYDRATED_MULTISYSTEM_ARTIFACT_PATH_MISSING",
            f"{label} 未记录项目内路径。",
        )
    candidate = Path(text)
    resolved = (
        candidate.expanduser().resolve(strict=False)
        if candidate.is_absolute()
        else (project_root / candidate).resolve(strict=False)
    )
    try:
        resolved.relative_to(project_root.resolve(strict=False))
    except ValueError:
        _fail(
            "HYDRATED_MULTISYSTEM_ARTIFACT_OUTSIDE_PROJECT",
            f"{label} 越出临时项目目录。",
            details={"path": str(resolved), "project": str(project_root)},
        )
    return resolved


def _project_artifact(
    project_root: Path,
    value: str | Path,
    label: str,
    *,
    non_empty: bool = True,
) -> Path:
    path = _project_path(project_root, value, label)
    if not path.is_file() or (non_empty and path.stat().st_size <= 0):
        _fail(
            "HYDRATED_MULTISYSTEM_ARTIFACT_INVALID",
            f"{label} 不存在或不满足非空要求。",
            details={"path": str(path)},
        )
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "HYDRATED_MULTISYSTEM_JSON_INVALID",
            f"{label} 不是有效 UTF-8 JSON。",
            details={"path": str(path), "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "HYDRATED_MULTISYSTEM_JSON_INVALID",
            f"{label} 顶层必须是对象。",
            details={"path": str(path)},
        )
    return payload


def _require_ok(label: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        details = dict(result) if isinstance(result, Mapping) else {"result": repr(result)}
        _fail(
            "HYDRATED_MULTISYSTEM_PROJECT_API_FAILED",
            f"DockStart 公开 API 步骤 {label} 失败。",
            details={"api": label, "result": details},
        )
    return dict(result)


def _require_preparation_ok(
    label: str,
    result: Any,
    project_root: Path,
) -> dict[str, Any]:
    if isinstance(result, Mapping) and result.get("ok") is True:
        return dict(result)
    payload = dict(result) if isinstance(result, Mapping) else {"result": repr(result)}
    streams: dict[str, Any] = {}
    for stream in ("stdout", "stderr"):
        relative_path = str(payload.get(f"{stream}_file") or "")
        text = ""
        read_error = ""
        if relative_path:
            try:
                path = _project_path(
                    project_root,
                    relative_path,
                    f"{label} {stream}",
                )
                if path.is_file():
                    text = path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
            except Exception as exc:  # noqa: BLE001 - preserve primary failure.
                read_error = str(exc)
        streams[stream] = {
            "relative_path": relative_path,
            "text": text[-100_000:],
            "truncated_to_last_characters": (
                max(0, len(text) - 100_000)
            ),
            "read_error": read_error,
        }
    _fail(
        "HYDRATED_MULTISYSTEM_PROJECT_API_FAILED",
        f"DockStart 公开 API 步骤 {label} 失败。",
        details={
            "api": label,
            "result": payload,
            "stdout": streams["stdout"],
            "stderr": streams["stderr"],
        },
    )


def _validated_result_manifest(
    project_root: Path,
    result: Mapping[str, Any],
    *,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    relative_path = str(result.get("manifest_file") or "")
    manifest_path = _project_artifact(
        project_root,
        relative_path,
        f"{label} manifest",
    )
    disk_manifest = _read_json(manifest_path, f"{label} manifest")
    supplied = result.get("manifest")
    if isinstance(supplied, Mapping) and dict(supplied) != disk_manifest:
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_RESPONSE_MISMATCH",
            f"{label} API 响应中的 manifest 与已发布文件不一致。",
        )
    actual_sha = _sha256(manifest_path)
    recorded_sha = str(result.get("manifest_sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(recorded_sha) or recorded_sha != actual_sha:
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_IDENTITY_MISMATCH",
            f"{label} manifest SHA256 与已发布文件不一致。",
            details={"recorded": recorded_sha, "actual": actual_sha},
        )
    return disk_manifest, {
        "relative_path": relative_path,
        "size_bytes": manifest_path.stat().st_size,
        "sha256": actual_sha,
    }


def _validate_manifest_contract(
    payload: Mapping[str, Any],
    *,
    manifest_path: Path,
) -> None:
    if (
        payload.get("schema_version") != 1
        or payload.get("fixture_id") != "hydrated_multisystem_rcsb_external"
        or payload.get("distribution") != "metadata_only"
        or any(payload.get(flag) is not False for flag in MANIFEST_CONTAINS_FLAGS)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "多体系 fixture 必须是 metadata-only schema v1。",
        )
    systems = payload.get("systems")
    protocol = payload.get("protocol")
    toolchain = payload.get("toolchain")
    source_policy = payload.get("source_policy")
    if (
        not isinstance(systems, Mapping)
        or tuple(systems) != SYSTEM_IDS
        or not isinstance(protocol, Mapping)
        or not isinstance(toolchain, Mapping)
        or not isinstance(source_policy, Mapping)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "多体系清单必须且只能包含有序的 2BYS、2ZJU 及完整协议。",
        )

    fixture_names = {item.name for item in manifest_path.parent.iterdir()}
    if fixture_names != FIXTURE_FILES:
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "metadata-only fixture 目录包含未允许的文件。",
            details={"files": sorted(fixture_names)},
        )

    maximum = int(source_policy.get("maximum_source_size_bytes") or 0)
    if (
        maximum <= 0
        or source_policy.get("receptor_pdb_identity")
        != "exact_wire_size_sha256_v1"
        or source_policy.get("model_server_ligand_sdf_identity")
        != "canonical_scientific_content_v1"
        or source_policy.get("model_server_ligand_sdf_canonicalization")
        != MODEL_SERVER_SDF_CANONICALIZATION
        or source_policy.get(
            "wire_size_sha256_and_final_url_recorded_for_audit"
        )
        is not True
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "source_policy 缺少有效大小上限或来源身份策略。",
        )
    for system_id in SYSTEM_IDS:
        system = systems[system_id]
        if not isinstance(system, Mapping):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id} 记录无效。",
            )
        receptor_source = system.get("receptor_source")
        ligand_source = system.get("ligand_source")
        if not isinstance(receptor_source, Mapping) or not isinstance(
            ligand_source, Mapping
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id} 来源记录无效。",
            )
        for source_key, source in (
            ("receptor_source", receptor_source),
            ("ligand_source", ligand_source),
        ):
            url = str(source.get("url") or "")
            file_name = str(source.get("file_name") or "")
            if (
                not url.startswith("https://")
                or Path(file_name).name != file_name
                or not file_name
            ):
                _fail(
                    "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                    f"{system_id}.{source_key} 的 URL 或文件名无效。",
                )
        receptor_sha = str(receptor_source.get("sha256") or "")
        receptor_size = int(receptor_source.get("size_bytes") or 0)
        if (
            not SHA256_PATTERN.fullmatch(receptor_sha)
            or receptor_size <= 0
            or receptor_size > maximum
            or any(
                key in receptor_source
                for key in (
                    "canonicalization",
                    "canonical_size_bytes",
                    "canonical_sha256",
                )
            )
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id}.receptor_source 缺少固定 wire 大小或 SHA256。",
            )
        ligand_canonical_sha = str(
            ligand_source.get("canonical_sha256") or ""
        )
        ligand_canonical_size = int(
            ligand_source.get("canonical_size_bytes") or 0
        )
        if (
            ligand_source.get("canonicalization")
            != MODEL_SERVER_SDF_CANONICALIZATION
            or not SHA256_PATTERN.fullmatch(ligand_canonical_sha)
            or ligand_canonical_size <= 0
            or ligand_canonical_size > maximum
            or "size_bytes" in ligand_source
            or "sha256" in ligand_source
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id}.ligand_source 必须只固定 canonical mol-block 身份。",
            )
        expected_component = "LOB" if system_id == "2BYS" else "IM4"
        if (
            ligand_source.get("component_id") != expected_component
            or ligand_source.get("auth_asym_id") != "A"
            or ligand_source.get("auth_seq_id") != 301
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id}.ligand_source 的组分、链或残基编号无效。",
            )

        hydrated = system.get("hydrated_ligand")
        diagnostics = system.get("diagnostics")
        receptor_preparation = system.get("receptor_preparation")
        receptor_controls = (
            receptor_preparation.get("receptor_controls")
            if isinstance(receptor_preparation, Mapping)
            else None
        )
        alternate_locations = (
            receptor_controls.get("alternate_locations")
            if isinstance(receptor_controls, Mapping)
            else None
        )
        if (
            not isinstance(hydrated, Mapping)
            or int(hydrated.get("water_count") or 0) not in {6, 7}
            or not isinstance(hydrated.get("atom_types"), list)
            or not isinstance(diagnostics, Mapping)
            or diagnostics.get("best_affinity_gate") is not False
            or diagnostics.get("pose_rmsd_gate") is not False
            or diagnostics.get("cross_platform_gate") is not False
            or diagnostics.get("pose_rmsd_classification")
            != "known_pose_quality_non_positive"
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id} 水或非门禁诊断契约无效。",
        )
        expected_altloc_count = 69 if system_id == "2BYS" else 4
        expected_deleted = (
            [
                {
                    "selector": f"{chain}:301",
                    "expected_component_id": "LOB",
                    "reason": "co_crystal_ligand",
                }
                for chain in "ABCDEFGHIJ"
            ]
            if system_id == "2BYS"
            else [
                {
                    "selector": selector,
                    "expected_component_id": "IM4",
                    "reason": "co_crystal_ligand",
                }
                for selector in ("A:301", "C:301", "D:301", "D:302", "E:301")
            ]
        )
        if (
            not isinstance(receptor_preparation, Mapping)
            or receptor_preparation.get("protocol")
            != "meeko_receptor_controls"
            or receptor_preparation.get("decision_scope")
            != "reviewed_engineering_chain_choice_not_scientific_optimum"
            or receptor_preparation.get("receptor_controls_canonicalization")
            != MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION
            or not isinstance(receptor_controls, Mapping)
            or set(receptor_controls)
            != {
                "schema_version",
                "allow_bad_res",
                "alternate_locations",
                "template_assignments",
                "deleted_residues",
            }
            or receptor_controls.get("schema_version") != 1
            or receptor_controls.get("allow_bad_res") is not False
            or not isinstance(alternate_locations, Mapping)
            or len(alternate_locations) != expected_altloc_count
            or set(alternate_locations.values()) != {"A"}
            or dict(receptor_controls.get("template_assignments") or {})
            or list(receptor_controls.get("deleted_residues") or [])
            != expected_deleted
            or receptor_preparation.get("receptor_controls_sha256")
            != _canonical_json_sha256(receptor_controls)
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"{system_id} 的显式受体 altloc 控制合同无效。",
            )

    grid = protocol.get("grid")
    vina = protocol.get("vina")
    if (
        not isinstance(grid, Mapping)
        or float(grid.get("spacing") or 0.0) != 0.375
        or dict(grid.get("grid_points") or {}) != {"x": 54, "y": 54, "z": 54}
        or dict(grid.get("actual_size_angstrom") or {})
        != {"x": 20.25, "y": 20.25, "z": 20.25}
        or not isinstance(vina, Mapping)
        or int(vina.get("num_modes") or 0) != 9
        or int(vina.get("verbosity") or 0) != 1
        or list(vina.get("allowed_continuous_mode_ids") or [])
        != list(range(1, 10))
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "协议必须固定为 54³、0.375 Å，并请求最多 9 个连续构象。",
        )
    required_versions = {
        "python": "3.11.15",
        "rdkit": "2026.03.3",
        "meeko": "0.7.1",
        "autogrid": "4.2.7",
        "vina": "1.2.7",
    }
    for key, version in required_versions.items():
        record = toolchain.get(key)
        if not isinstance(record, Mapping) or str(record.get("version") or "") != version:
            _fail(
                "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
                f"工具 {key} 必须固定为 {version}。",
            )


def _load_manifest() -> dict[str, Any]:
    manifest = _read_json(MANIFEST_PATH, "水合多体系来源清单")
    _validate_manifest_contract(manifest, manifest_path=MANIFEST_PATH)
    return manifest


def _default_fetcher(url: str, maximum_bytes: int, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DockStart-hydrated-multisystem-verifier/1"},
    )
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    with opener.open(request, timeout=timeout) as response:  # noqa: S310
        final_url = str(response.geturl())
        if not final_url.startswith("https://"):
            _fail(
                "HYDRATED_MULTISYSTEM_DOWNLOAD_REDIRECT_INVALID",
                "RCSB 下载重定向离开 HTTPS。",
                details={"requested_url": url, "final_url": final_url},
            )
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > maximum_bytes:
            _fail(
                "HYDRATED_MULTISYSTEM_DOWNLOAD_TOO_LARGE",
                "RCSB 响应超过清单大小上限。",
                details={"declared_size": int(declared), "maximum": maximum_bytes},
            )
        payload = response.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        _fail(
            "HYDRATED_MULTISYSTEM_DOWNLOAD_TOO_LARGE",
            "RCSB 响应超过清单大小上限。",
            details={"actual_size": len(payload), "maximum": maximum_bytes},
        )
    return {"payload": payload, "final_url": final_url}


def _coerce_fetch_result(result: Any, requested_url: str) -> tuple[bytes, str]:
    if isinstance(result, bytes):
        return result, requested_url
    if isinstance(result, tuple) and len(result) == 2:
        payload, final_url = result
        if isinstance(payload, bytes):
            return payload, str(final_url)
    if isinstance(result, Mapping) and isinstance(result.get("payload"), bytes):
        return bytes(result["payload"]), str(result.get("final_url") or requested_url)
    _fail(
        "HYDRATED_MULTISYSTEM_FETCHER_INVALID",
        "下载器必须返回 bytes、(bytes, final_url) 或含 payload 的对象。",
    )


def _download_source(
    record: Mapping[str, Any],
    destination: Path,
    *,
    maximum_bytes: int,
    fetcher: Fetcher | None,
    timeout: int,
) -> dict[str, Any]:
    url = str(record["url"])
    callable_fetcher = fetcher or _default_fetcher
    try:
        raw_result = callable_fetcher(url, maximum_bytes, timeout)
    except HydratedMultisystemError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert network boundary.
        _fail(
            "HYDRATED_MULTISYSTEM_DOWNLOAD_FAILED",
            "RCSB 来源下载失败。",
            details={"url": url, "error": str(exc)},
        )
    payload, final_url = _coerce_fetch_result(raw_result, url)
    actual_size = len(payload)
    actual_sha = _sha256_bytes(payload)
    if not final_url.startswith("https://"):
        _fail(
            "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
            "RCSB 下载最终地址不是 HTTPS。",
            details={
                "requested_url": url,
                "final_url": final_url,
            },
        )
    if actual_size > maximum_bytes:
        _fail(
            "HYDRATED_MULTISYSTEM_DOWNLOAD_TOO_LARGE",
            "RCSB 响应超过清单大小上限。",
            details={"actual_size": actual_size, "maximum": maximum_bytes},
        )

    canonicalization = str(record.get("canonicalization") or "")
    canonical_evidence: dict[str, Any] = {}
    if canonicalization:
        if canonicalization != MODEL_SERVER_SDF_CANONICALIZATION:
            _fail(
                "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
                "ModelServer SDF canonicalization 标识不受支持。",
                details={"canonicalization": canonicalization},
            )
        canonical = _canonicalize_model_server_sdf(payload)
        canonical_size = len(canonical)
        canonical_sha = _sha256_bytes(canonical)
        expected_size = int(record["canonical_size_bytes"])
        expected_sha = str(record["canonical_sha256"])
        if canonical_size != expected_size or canonical_sha != expected_sha:
            _fail(
                "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
                "ModelServer SDF 的 canonical mol-block 身份与清单不一致。",
                details={
                    "requested_url": url,
                    "final_url": final_url,
                    "wire_size_bytes": actual_size,
                    "wire_sha256": actual_sha,
                    "canonicalization": canonicalization,
                    "expected_canonical_size_bytes": expected_size,
                    "actual_canonical_size_bytes": canonical_size,
                    "expected_canonical_sha256": expected_sha,
                    "actual_canonical_sha256": canonical_sha,
                },
            )
        identity_mode = "canonical_scientific_content_v1"
        canonical_evidence = {
            "canonicalization": canonicalization,
            "canonical_size_bytes": canonical_size,
            "canonical_sha256": canonical_sha,
        }
    else:
        expected_size = int(record["size_bytes"])
        expected_sha = str(record["sha256"])
        if actual_size != expected_size or actual_sha != expected_sha:
            _fail(
                "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
                "下载来源与清单固定 wire 身份不一致。",
                details={
                    "requested_url": url,
                    "final_url": final_url,
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                },
            )
        identity_mode = "exact_wire_size_sha256_v1"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return {
        "requested_url": url,
        "final_url": final_url,
        "downloaded_at": _utc_now(),
        "file_name": destination.name,
        "identity_mode": identity_mode,
        "size_bytes": actual_size,
        "sha256": actual_sha,
        **canonical_evidence,
        "path": str(destination),
    }


def _download_sources(
    system_id: str,
    system: Mapping[str, Any],
    download_root: Path,
    *,
    maximum_bytes: int,
    fetcher: Fetcher | None,
    timeout: int,
) -> tuple[dict[str, Path], dict[str, Any]]:
    system_root = download_root / system_id
    paths: dict[str, Path] = {}
    evidence: dict[str, Any] = {}
    for role, source_key in (
        ("receptor", "receptor_source"),
        ("ligand", "ligand_source"),
    ):
        record = system[source_key]
        assert isinstance(record, Mapping)
        destination = system_root / str(record["file_name"])
        evidence[role] = _download_source(
            record,
            destination,
            maximum_bytes=maximum_bytes,
            fetcher=fetcher,
            timeout=timeout,
        )
        paths[role] = destination
    return paths, evidence


def _module_probe(python_path: Path) -> dict[str, Any]:
    command = [
        str(python_path),
        "-I",
        "-B",
        "-c",
        (
            "import json,meeko,rdkit;"
            "print(json.dumps({"
            "'rdkit':{'version':getattr(rdkit,'__version__',''),"
            "'path':getattr(rdkit,'__file__','')},"
            "'meeko':{'version':getattr(meeko,'__version__',''),"
            "'path':getattr(meeko,'__file__','')}}))"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=60,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(
            "HYDRATED_MULTISYSTEM_MODULE_PROBE_FAILED",
            "无法读取调用者 Python 中的 RDKit/Meeko 模块身份。",
            details={"error": str(exc), "command": command},
        )
    if completed.returncode != 0:
        _fail(
            "HYDRATED_MULTISYSTEM_MODULE_PROBE_FAILED",
            "RDKit/Meeko 模块身份探测返回非零退出码。",
            details={
                "exit_code": completed.returncode,
                "stderr": completed.stderr,
            },
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _fail(
            "HYDRATED_MULTISYSTEM_MODULE_PROBE_FAILED",
            "RDKit/Meeko 模块身份探测未返回有效 JSON。",
            details={"error": str(exc), "stdout": completed.stdout},
        )
    evidence: dict[str, Any] = {}
    for key in ("rdkit", "meeko"):
        record = payload.get(key) if isinstance(payload, Mapping) else None
        if not isinstance(record, Mapping):
            _fail(
                "HYDRATED_MULTISYSTEM_MODULE_PROBE_FAILED",
                f"模块身份探测缺少 {key}。",
            )
        module_path = _regular_file(Path(str(record.get("path") or "")), f"{key} 模块")
        evidence[key] = {
            "version": str(record.get("version") or ""),
            "path": str(module_path),
            "size_bytes": module_path.stat().st_size,
            "sha256": _sha256(module_path),
        }
    evidence["command"] = command
    return evidence


def _verify_toolchain(
    python_executable: Path,
    autogrid_executable: Path,
    vina_executable: Path,
    manifest: Mapping[str, Any],
    *,
    isolated_root: Path,
) -> dict[str, Any]:
    python_path = _regular_file(python_executable, "调用者 Python")
    autogrid_path = _regular_file(autogrid_executable, "调用者 AutoGrid")
    vina_path = _regular_file(vina_executable, "调用者 Vina")
    expected = manifest["toolchain"]
    assert isinstance(expected, Mapping)

    disabled_python = isolated_root / "disabled-bundled-python.exe"
    disabled_vina = isolated_root / "disabled-bundled-vina.exe"
    python_result = python_adapter.detect(
        str(python_path),
        bundled_path=str(disabled_python),
        prefer_configured=True,
    )
    rdkit_result = rdkit_adapter.detect(str(python_path), source="configured")
    meeko_result = meeko_adapter.detect(str(python_path), source="configured")
    autogrid_result = autogrid_adapter.detect(str(autogrid_path))
    vina_result = vina_adapter.detect(
        str(vina_path),
        bundled_path=str(disabled_vina),
    )
    detections = {
        "python": python_result,
        "rdkit": rdkit_result,
        "meeko": meeko_result,
        "autogrid": autogrid_result,
        "vina": vina_result,
    }
    expected_versions = {
        key: str(expected[key]["version"])
        for key in ("python", "rdkit", "meeko", "autogrid", "vina")
    }
    actual_versions = {
        "python": str(getattr(python_result, "version", "")).removeprefix("Python "),
        "rdkit": str(getattr(rdkit_result, "version", "")),
        "meeko": str(getattr(meeko_result, "version", "")),
        "autogrid": str(getattr(autogrid_result, "version", "")),
        "vina": str(getattr(vina_result, "version", "")),
    }
    expected_paths = {
        "python": python_path,
        "rdkit": python_path,
        "meeko": python_path,
        "autogrid": autogrid_path,
        "vina": vina_path,
    }
    for key, detection in detections.items():
        if (
            getattr(detection, "status", "") != "ok"
            or actual_versions[key] != expected_versions[key]
            or _normalized_path(str(getattr(detection, "path", "") or ""))
            != _normalized_path(expected_paths[key])
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_TOOLCHAIN_MISMATCH",
                f"{key} 检测结果未满足固定版本和调用路径要求。",
                details={
                    "tool": key,
                    "expected_version": expected_versions[key],
                    "actual_version": actual_versions[key],
                    "expected_path": str(expected_paths[key]),
                    "detection": _tool_payload(detection),
                },
            )
    if (
        str(getattr(autogrid_result, "source", "")) != "configured"
        or bool(getattr(autogrid_result, "is_bundled", False))
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_AUTOGRID_NOT_EXTERNAL",
            "AutoGrid 必须是调用者显式配置的外部工具。",
            details=_tool_payload(autogrid_result),
        )
    vina_capabilities = getattr(vina_result, "capabilities", {}) or {}
    maps_feature = (
        ((vina_capabilities.get("features") or {}).get("maps"))
        if isinstance(vina_capabilities, Mapping)
        else None
    )
    if (
        not isinstance(maps_feature, Mapping)
        or maps_feature.get("status") != "supported"
        or maps_feature.get("supported") is not True
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_VINA_MAPS_UNSUPPORTED",
            "Vina 1.2.7 必须实际声明支持 --maps。",
            details=_tool_payload(vina_result),
        )

    modules = _module_probe(python_path)
    for key in ("rdkit", "meeko"):
        if modules[key]["version"] != expected_versions[key]:
            _fail(
                "HYDRATED_MULTISYSTEM_MODULE_VERSION_MISMATCH",
                f"{key} 模块文件版本与固定工具链不一致。",
                details={"module": modules[key], "expected": expected_versions[key]},
            )
    return {
        "python": {
            "path": str(python_path),
            "size_bytes": python_path.stat().st_size,
            "sha256": _sha256(python_path),
            "version": expected_versions["python"],
            "detection": _tool_payload(python_result),
        },
        "rdkit": {**modules["rdkit"], "detection": _tool_payload(rdkit_result)},
        "meeko": {**modules["meeko"], "detection": _tool_payload(meeko_result)},
        "autogrid": {
            "path": str(autogrid_path),
            "size_bytes": autogrid_path.stat().st_size,
            "sha256": _sha256(autogrid_path),
            "version": expected_versions["autogrid"],
            "detection": _tool_payload(autogrid_result),
        },
        "vina": {
            "path": str(vina_path),
            "size_bytes": vina_path.stat().st_size,
            "sha256": _sha256(vina_path),
            "version": expected_versions["vina"],
            "maps_capability": dict(maps_feature),
            "detection": _tool_payload(vina_result),
        },
        "module_probe_command": modules["command"],
    }


def _require_command_executable(
    command: Any,
    expected: Path,
    *,
    label: str,
) -> list[str]:
    if (
        not isinstance(command, Sequence)
        or isinstance(command, (str, bytes))
        or not command
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_COMMAND_INVALID",
            f"{label} 未记录命令数组。",
        )
    values = [str(item) for item in command]
    if _normalized_path(values[0]) != _normalized_path(expected):
        _fail(
            "HYDRATED_MULTISYSTEM_COMMAND_TOOL_SUBSTITUTED",
            f"{label} 未使用调用者固定工具路径。",
            details={"expected": str(expected), "command": values},
        )
    return values


def _validate_file_record(
    path: Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    actual = {
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    recorded_size = record.get("size_bytes", record.get("size"))
    if (
        int(recorded_size or 0) != actual["size_bytes"]
        or str(record.get("sha256") or "").lower() != actual["sha256"]
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_ARTIFACT_IDENTITY_MISMATCH",
            f"{label} 的 manifest 身份与实际文件不一致。",
            details={"record": dict(record), "actual": actual},
        )
    return actual


def _stable_file_identity(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        _fail(
            "HYDRATED_MULTISYSTEM_SOURCE_MUTATED",
            f"{label} 不再是普通文件。",
            details={"path": str(path)},
        )
    before = path.stat()
    sha256 = _sha256(path)
    after = path.stat()
    before_signature = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_signature = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_signature != after_signature or after.st_size <= 0:
        _fail(
            "HYDRATED_MULTISYSTEM_SOURCE_MUTATED",
            f"{label} 在身份校验期间发生变化或为空。",
            details={"path": str(path)},
        )
    return {"size_bytes": after.st_size, "sha256": sha256}


def _verify_downloaded_source(
    source_path: Path,
    source_evidence: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    expected = {
        "size_bytes": int(source_evidence.get("size_bytes") or 0),
        "sha256": str(source_evidence.get("sha256") or "").lower(),
    }
    actual = _stable_file_identity(source_path, label=label)
    if (
        _normalized_path(str(source_evidence.get("path") or ""))
        != _normalized_path(source_path)
        or expected != actual
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_SOURCE_MUTATED",
            f"{label} 不再匹配下载时审计的 wire 身份。",
            details={
                "path": str(source_path),
                "audited_path": source_evidence.get("path"),
                "expected": expected,
                "actual": actual,
            },
        )
    return actual


def _verify_raw_import(
    project_root: Path,
    result: Mapping[str, Any],
    source_path: Path,
    source_evidence: Mapping[str, Any],
    pre_import_identity: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    raw_relative = str(result.get("raw_file") or "")
    raw_path = _project_artifact(project_root, raw_relative, label)
    source_identity = _verify_downloaded_source(
        source_path,
        source_evidence,
        label=f"{label} source after import",
    )
    imported_identity = _stable_file_identity(raw_path, label=label)
    expected_pre_import = {
        "size_bytes": int(pre_import_identity.get("size_bytes") or 0),
        "sha256": str(pre_import_identity.get("sha256") or "").lower(),
    }
    if (
        expected_pre_import != source_identity
        or imported_identity != source_identity
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_RAW_IMPORT_CHANGED",
            f"{label} 改变了下载的科学字节。",
            details={
                "before_import": expected_pre_import,
                "source_after_import": source_identity,
                "imported": imported_identity,
            },
        )
    return {
        "relative_path": raw_relative,
        **imported_identity,
        "audited_download_wire_matches": True,
        "source_stable_across_import": True,
    }


def _validate_preparation_input_binding(
    project_root: Path,
    metadata: Mapping[str, Any],
    role: str,
    raw_input_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    expected_path = str(raw_input_evidence.get("relative_path") or "")
    expected_size = int(raw_input_evidence.get("size_bytes") or 0)
    expected_sha = str(raw_input_evidence.get("sha256") or "").lower()
    if (
        not expected_path
        or expected_size <= 0
        or not SHA256_PATTERN.fullmatch(expected_sha)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_PREPARATION_INPUT_UNBOUND",
            f"{role} 准备缺少已校验 raw 输入身份。",
        )

    def identity_matches(record: Any) -> bool:
        return (
            isinstance(record, Mapping)
            and record.get("ok") is True
            and str(record.get("canonical_relative_path") or "")
            == expected_path
            and int(record.get("size_bytes") or 0) == expected_size
            and str(record.get("sha256") or "").lower() == expected_sha
        )

    def verification_matches(record: Any) -> bool:
        return (
            isinstance(record, Mapping)
            and record.get("matches") is True
            and not list(record.get("reasons") or [])
            and str(record.get("recorded_raw_file") or "") == expected_path
            and identity_matches(record.get("claimed"))
            and identity_matches(record.get("current"))
        )

    snapshot_relative = str(metadata.get("input_snapshot_file") or "")
    snapshot_path = _project_artifact(
        project_root,
        snapshot_relative,
        f"{role} preparation input snapshot",
    )
    snapshot = _read_json(
        snapshot_path,
        f"{role} preparation input snapshot",
    )
    snapshot_file = snapshot.get("input")
    snapshot_file_matches = (
        isinstance(snapshot_file, Mapping)
        and snapshot_file.get("exists") is True
        and snapshot_file.get("is_file") is True
        and snapshot_file.get("non_empty") is True
        and str(snapshot_file.get("path") or "") == expected_path
        and int(snapshot_file.get("size") or 0) == expected_size
        and str(snapshot_file.get("sha256") or "").lower() == expected_sha
    )
    claimed_input = metadata.get("claimed_input")
    input_verification = metadata.get("input_verification")
    if (
        str(metadata.get("input_file") or "") != expected_path
        or not identity_matches(claimed_input)
        or snapshot.get("target") != role
        or str(snapshot.get("input_file") or "") != expected_path
        or str(snapshot.get("canonical_input_file") or "") != expected_path
        or str(snapshot.get("input_sha256") or "").lower() != expected_sha
        or not identity_matches(snapshot.get("claimed_input"))
        or not snapshot_file_matches
        or not verification_matches(snapshot.get("claim_verification"))
        or not verification_matches(input_verification)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_PREPARATION_INPUT_UNBOUND",
            f"{role} 准备输入快照未绑定到已校验的 raw 来源字节。",
            details={
                "expected": {
                    "relative_path": expected_path,
                    "size_bytes": expected_size,
                    "sha256": expected_sha,
                },
                "metadata_input_file": metadata.get("input_file"),
                "claimed_input": claimed_input,
                "input_verification": input_verification,
                "input_snapshot_file": snapshot_relative,
            },
        )
    return {
        "relative_path": expected_path,
        "size_bytes": expected_size,
        "sha256": expected_sha,
        "input_snapshot_file": snapshot_relative,
        "claimed_input_matches": True,
        "claim_verification_matches": True,
        "final_input_verification_matches": True,
    }


def _validate_standard_preparation(
    project_root: Path,
    result: Mapping[str, Any],
    role: str,
    toolchain: Mapping[str, Any],
    *,
    raw_input_evidence: Mapping[str, Any],
    receptor_preparation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_path = _project_artifact(
        project_root,
        str(result.get("metadata_file") or ""),
        f"{role} preparation metadata",
    )
    metadata = _read_json(metadata_path, f"{role} preparation metadata")
    output_path = _project_artifact(
        project_root,
        str(result.get("output_file") or f"prepared/{role}.pdbqt"),
        f"prepared {role} PDBQT",
    )
    python_expected = Path(str(toolchain["python"]["path"]))
    command = _require_command_executable(
        metadata.get("command"),
        python_expected,
        label=f"{role} preparation",
    )
    output_record = metadata.get("output")
    if (
        metadata.get("status") != "finished"
        or metadata.get("published") is not True
        or int(metadata.get("exit_code") or 0) != 0
        or _normalized_path(str(metadata.get("python_path") or ""))
        != _normalized_path(python_expected)
        or str(metadata.get("python_sha256") or "").lower()
        != str(toolchain["python"]["sha256"])
        or _sha256(python_expected) != str(toolchain["python"]["sha256"])
        or str(metadata.get("rdkit_version") or "")
        != str(toolchain["rdkit"]["version"])
        or str(metadata.get("meeko_version") or "")
        != str(toolchain["meeko"]["version"])
        or not isinstance(output_record, Mapping)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_STANDARD_PREPARATION_INVALID",
            f"{role} 的公开准备记录未满足完成、发布和工具链固定要求。",
            details={"metadata": metadata},
        )
    output_identity = _validate_file_record(
        output_path,
        output_record,
        label=f"prepared {role}",
    )
    input_binding = _validate_preparation_input_binding(
        project_root,
        metadata,
        role,
        raw_input_evidence,
    )
    receptor_controls_evidence: dict[str, Any] | None = None
    if role == "receptor":
        if not isinstance(receptor_preparation, Mapping):
            _fail(
                "HYDRATED_MULTISYSTEM_RECEPTOR_CONTROLS_MISSING",
                "受体准备验收缺少显式 receptor_controls 合同。",
            )
        expected_controls = receptor_preparation.get("receptor_controls")
        if not isinstance(expected_controls, Mapping):
            _fail(
                "HYDRATED_MULTISYSTEM_RECEPTOR_CONTROLS_MISSING",
                "受体准备验收缺少结构化 receptor_controls。",
            )
        normalized_controls = normalize_meeko_receptor_controls(expected_controls)
        expected_hash = _canonical_json_sha256(normalized_controls)
        expected_arguments = meeko_receptor_control_arguments(normalized_controls)
        options_record = metadata.get("options")
        options_record = (
            options_record if isinstance(options_record, Mapping) else {}
        )
        if (
            metadata.get("method") != "meeko_receptor_controls"
            or metadata.get("protocol") != "meeko_receptor_controls"
            or metadata.get("protocol_mode") != "reviewed"
            or metadata.get("receptor_controls") != normalized_controls
            or options_record.get("protocol") != "meeko_receptor_controls"
            or options_record.get("receptor_controls") != normalized_controls
            or metadata.get("receptor_controls_canonicalization")
            != MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION
            or str(metadata.get("receptor_controls_sha256") or "")
            != expected_hash
            or str(receptor_preparation.get("receptor_controls_sha256") or "")
            != expected_hash
            or "--default_altloc" in command
            or "--allow_bad_res" in command
            or "-p" not in command
            or command[command.index("-p") + 1 :] != expected_arguments
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_RECEPTOR_CONTROLS_INVALID",
                "受体准备没有精确冻结并执行清单中的逐残基 altloc 合同。",
                details={
                    "expected_controls": normalized_controls,
                    "expected_sha256": expected_hash,
                    "expected_arguments": expected_arguments,
                    "metadata_method": metadata.get("method"),
                    "metadata_protocol": metadata.get("protocol"),
                    "metadata_sha256": metadata.get("receptor_controls_sha256"),
                    "command": command,
                },
            )
        receptor_controls_evidence = {
            "protocol": "meeko_receptor_controls",
            "protocol_mode": "reviewed",
            "controls": normalized_controls,
            "canonicalization": MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION,
            "sha256": expected_hash,
            "command_arguments": expected_arguments,
            "default_altloc_used": False,
            "allow_bad_res_used": False,
        }
    return {
        "prep_id": str(result.get("prep_id") or ""),
        "metadata_file": str(result.get("metadata_file") or ""),
        "command": command,
        "output": {
            "relative_path": str(result.get("output_file") or f"prepared/{role}.pdbqt"),
            **output_identity,
        },
        "input_binding": input_binding,
        "public_api_only": True,
        "fallback_import_used": False,
        **(
            {"receptor_controls": receptor_controls_evidence}
            if receptor_controls_evidence is not None
            else {}
        ),
    }


def _pdbqt_atom_types(path: Path) -> list[str]:
    atom_types: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            parts = line.split()
            if parts:
                atom_types.append(parts[-1])
    return atom_types


def _snapshot_matches(
    record: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return (
        _normalized_path(str(record.get("path") or ""))
        == _normalized_path(str(expected.get("path") or ""))
        and int(record.get("size_bytes") or 0) == int(expected.get("size_bytes") or 0)
        and str(record.get("sha256") or "").lower()
        == str(expected.get("sha256") or "").lower()
    )


def _validate_hydrated_ligand(
    project_root: Path,
    result: Mapping[str, Any],
    system_id: str,
    system: Mapping[str, Any],
    toolchain: Mapping[str, Any],
    ligand_source_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    manifest, manifest_identity = _validated_result_manifest(
        project_root,
        result,
        label=f"{system_id} hydrated ligand",
    )
    outputs = manifest.get("outputs")
    hydrated_record = (
        outputs.get("hydrated_pdbqt")
        if isinstance(outputs, Mapping)
        else None
    )
    expected = system["hydrated_ligand"]
    assert isinstance(expected, Mapping)
    if not isinstance(hydrated_record, Mapping):
        _fail(
            "HYDRATED_MULTISYSTEM_HYDRATED_LIGAND_INVALID",
            f"{system_id} 水合配体 manifest 缺少输出记录。",
        )
    hydrated_path = _project_artifact(
        project_root,
        str(hydrated_record.get("path") or ""),
        f"{system_id} hydrated ligand",
    )
    identity = _validate_file_record(
        hydrated_path,
        hydrated_record,
        label=f"{system_id} hydrated ligand",
    )
    actual_types = _pdbqt_atom_types(hydrated_path)
    unique_types = sorted(set(actual_types))
    water_count = sum(item == "W" for item in actual_types)
    expected_types = sorted(str(item) for item in expected["atom_types"])
    tools = manifest.get("tools")
    tools = tools if isinstance(tools, Mapping) else {}
    before = tools.get("before")
    before = before if isinstance(before, Mapping) else {}
    probe = tools.get("probe")
    probe = probe if isinstance(probe, Mapping) else {}
    integrity = manifest.get("integrity")
    integrity = integrity if isinstance(integrity, Mapping) else {}
    source = manifest.get("source")
    source = source if isinstance(source, Mapping) else {}
    expected_source = system.get("ligand_source")
    expected_source = (
        expected_source if isinstance(expected_source, Mapping) else {}
    )
    tools_after = integrity.get("tools_after")
    tools_after = tools_after if isinstance(tools_after, Mapping) else {}
    if (
        manifest.get("status") != "finished"
        or str(manifest.get("protocol_id") or "")
        != str(result.get("protocol_id") or "hydrated_ad4_experimental")
        or int(result.get("water_count") or 0) != int(expected["water_count"])
        or int(hydrated_record.get("water_count") or 0) != int(expected["water_count"])
        or water_count != int(expected["water_count"])
        or unique_types != expected_types
        or sorted(str(item) for item in hydrated_record.get("atom_types") or [])
        != expected_types
        or tools.get("rdkit_version") != toolchain["rdkit"]["version"]
        or tools.get("meeko_version") != toolchain["meeko"]["version"]
        or not _snapshot_matches(before.get("python") or {}, toolchain["python"])
        or not _snapshot_matches(before.get("rdkit") or {}, toolchain["rdkit"])
        or not _snapshot_matches(before.get("meeko") or {}, toolchain["meeko"])
        or _normalized_path(str(probe.get("rdkit_module_file") or ""))
        != _normalized_path(str(toolchain["rdkit"]["path"]))
        or _normalized_path(str(probe.get("meeko_module_file") or ""))
        != _normalized_path(str(toolchain["meeko"]["path"]))
        or ligand_source_evidence.get("identity_mode")
        != "canonical_scientific_content_v1"
        or str(ligand_source_evidence.get("requested_url") or "")
        != str(expected_source.get("url") or "")
        or not str(ligand_source_evidence.get("final_url") or "").startswith(
            "https://"
        )
        or int(ligand_source_evidence.get("size_bytes") or 0) <= 0
        or not SHA256_PATTERN.fullmatch(
            str(ligand_source_evidence.get("sha256") or "").lower()
        )
        or ligand_source_evidence.get("canonicalization")
        != expected_source.get("canonicalization")
        or int(ligand_source_evidence.get("canonical_size_bytes") or 0)
        != int(expected_source.get("canonical_size_bytes") or 0)
        or str(ligand_source_evidence.get("canonical_sha256") or "").lower()
        != str(expected_source.get("canonical_sha256") or "").lower()
        or int(source.get("size_bytes") or 0)
        != int(ligand_source_evidence.get("size_bytes") or 0)
        or str(source.get("sha256") or "").lower()
        != str(ligand_source_evidence.get("sha256") or "").lower()
        or integrity.get("source_unchanged") is not True
        or integrity.get("tools_unchanged") is not True
        or integrity.get("script_unchanged") is not True
        or not _snapshot_matches(tools_after.get("python") or {}, toolchain["python"])
        or not _snapshot_matches(tools_after.get("rdkit") or {}, toolchain["rdkit"])
        or not _snapshot_matches(tools_after.get("meeko") or {}, toolchain["meeko"])
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_HYDRATED_LIGAND_INVALID",
            f"{system_id} 水合配体未满足水数、原子类型或固定工具链要求。",
            details={
                "expected_water_count": expected["water_count"],
                "actual_water_count": water_count,
                "expected_atom_types": expected_types,
                "actual_atom_types": unique_types,
            },
        )
    if system_id == "2ZJU" and (
        "Cl" not in unique_types or "CL" in unique_types
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_CHLORINE_NOT_CANONICAL",
            "2ZJU 水合配体必须使用 canonical Cl，禁止 legacy CL。",
            details={"atom_types": unique_types},
        )
    command = _require_command_executable(
        manifest.get("command"),
        Path(str(toolchain["python"]["path"])),
        label=f"{system_id} hydrated ligand preparation",
    )
    probe_command = _require_command_executable(
        tools.get("probe_command"),
        Path(str(toolchain["python"]["path"])),
        label=f"{system_id} hydrated ligand capability probe",
    )
    return {
        "preparation_id": str(result.get("preparation_id") or ""),
        "manifest": manifest_identity,
        "water_count": water_count,
        "atom_types": unique_types,
        "output": {**identity, "relative_path": str(hydrated_record.get("path") or "")},
        "command": command,
        "probe_command": probe_command,
        "canonical_chlorine_gate": True,
        "source_identity": {
            key: ligand_source_evidence[key]
            for key in (
                "requested_url",
                "final_url",
                "identity_mode",
                "size_bytes",
                "sha256",
                "canonicalization",
                "canonical_size_bytes",
                "canonical_sha256",
            )
            if key in ligand_source_evidence
        },
    }


def _validate_grid_coverage(
    coverage: Mapping[str, Any],
    grid: Mapping[str, Any],
    box: Mapping[str, Any],
) -> dict[str, Any]:
    expected_points = dict(grid["grid_points"])
    expected_size = dict(grid["actual_size_angstrom"])
    expected_requested_size = dict(grid["requested_size_angstrom"])
    expected_center = {
        axis: float(box[f"center_{axis}"])
        for axis in ("x", "y", "z")
    }
    actual_points = coverage.get("effective_grid_axis_intervals")
    actual_size = coverage.get("effective_grid_size_angstrom")
    actual_center = coverage.get("box_center_angstrom")
    requested_size = coverage.get("requested_box_size_angstrom")
    axis = coverage.get("axis_coverage")
    if (
        coverage.get("covers_requested_box") is not True
        or coverage.get("interval_semantics") != "closed"
        or dict(actual_points or {}) != expected_points
        or not isinstance(actual_center, Mapping)
        or any(
            abs(float(actual_center.get(key) or 0.0) - expected_center[key])
            > 1e-9
            for key in ("x", "y", "z")
        )
        or not isinstance(requested_size, Mapping)
        or any(
            abs(
                float(requested_size.get(key) or 0.0)
                - float(expected_requested_size[key])
            )
            > 1e-9
            for key in ("x", "y", "z")
        )
        or not isinstance(actual_size, Mapping)
        or any(
            abs(float(actual_size.get(key) or 0.0) - float(expected_size[key])) > 1e-9
            for key in ("x", "y", "z")
        )
        or abs(float(coverage.get("effective_grid_spacing_angstrom") or 0.0) - 0.375)
        > 1e-12
        or not isinstance(axis, Mapping)
        or any(
            not isinstance(axis.get(key), Mapping)
            or axis[key].get("covers_requested_interval") is not True
            or abs(float(axis[key].get("lower_margin_angstrom") or 0.0) - 0.125)
            > 1e-6
            or abs(float(axis[key].get("upper_margin_angstrom") or 0.0) - 0.125)
            > 1e-6
            or abs(float(axis[key].get("minimum_margin_angstrom") or 0.0) - 0.125)
            > 1e-6
            for key in ("x", "y", "z")
        )
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_GRID_COVERAGE_INVALID",
            "54³/0.375 Å 网格未完整覆盖 20 Å Box。",
            details={"coverage": dict(coverage)},
        )
    return {
        "grid_points": expected_points,
        "spacing": 0.375,
        "box_center_angstrom": expected_center,
        "requested_box_size_angstrom": expected_requested_size,
        "actual_size_angstrom": expected_size,
        "minimum_margin_angstrom": 0.125,
        "covers_requested_box": True,
    }


def _validate_maps(
    project_root: Path,
    result: Mapping[str, Any],
    system_id: str,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
    toolchain: Mapping[str, Any],
) -> dict[str, Any]:
    manifest, manifest_identity = _validated_result_manifest(
        project_root,
        result,
        label=f"{system_id} hydrated maps",
    )
    maps = manifest.get("maps")
    autogrid = manifest.get("autogrid")
    if (
        manifest.get("status") != "ready"
        or not isinstance(maps, Mapping)
        or not isinstance(autogrid, Mapping)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MAPS_INVALID",
            f"{system_id} maps manifest 未达到 ready。",
        )
    command = _require_command_executable(
        autogrid.get("command"),
        Path(str(toolchain["autogrid"]["path"])),
        label=f"{system_id} AutoGrid",
    )
    if (
        not _snapshot_matches(autogrid, toolchain["autogrid"])
        or str(autogrid.get("version") or "") != toolchain["autogrid"]["version"]
        or int(autogrid.get("exit_code") or 0) != 0
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_AUTOGRID_PROVENANCE_INVALID",
            f"{system_id} maps 未绑定调用者 AutoGrid 4.2.7 身份。",
            details={"autogrid": dict(autogrid)},
        )
    files = maps.get("files")
    required = maps.get("required_files")
    if not isinstance(files, list) or not isinstance(required, list):
        _fail(
            "HYDRATED_MULTISYSTEM_MAPS_INVALID",
            f"{system_id} maps 文件清单缺失。",
        )
    names: set[str] = set()
    observed_files: list[dict[str, Any]] = []
    for record in files:
        if not isinstance(record, Mapping):
            _fail(
                "HYDRATED_MULTISYSTEM_MAPS_INVALID",
                f"{system_id} maps 文件记录无效。",
            )
        name = str(record.get("name") or "")
        path = _project_artifact(
            project_root,
            str(record.get("relative_path") or ""),
            f"{system_id} map {name}",
        )
        identity = _validate_file_record(path, record, label=f"{system_id} map {name}")
        names.add(name)
        observed_files.append({"name": name, "relative_path": record["relative_path"], **identity})
    if names != set(str(item) for item in required):
        _fail(
            "HYDRATED_MULTISYSTEM_MAPS_INVALID",
            f"{system_id} maps required_files 与实际记录不一致。",
            details={"required": required, "actual": sorted(names)},
        )
    expected_types = sorted(str(item) for item in system["hydrated_ligand"]["atom_types"])
    hydrated_types = sorted(str(item) for item in maps.get("ligand_atom_types") or [])
    autogrid_types = sorted(str(item) for item in maps.get("autogrid_ligand_atom_types") or [])
    if (
        hydrated_types != expected_types
        or "W" in autogrid_types
        or sorted(item for item in expected_types if item != "W") != autogrid_types
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MAP_TYPES_INVALID",
            f"{system_id} hydrated/AutoGrid 原子类型清单不一致。",
            details={
                "expected": expected_types,
                "hydrated": hydrated_types,
                "autogrid": autogrid_types,
            },
        )
    if system_id == "2ZJU" and (
        "Cl" not in hydrated_types
        or "Cl" not in autogrid_types
        or "receptor.Cl.map" not in names
        or "CL" in hydrated_types
        or "CL" in autogrid_types
        or "receptor.CL.map" in names
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_CHLORINE_MAP_INVALID",
            "2ZJU 必须生成 receptor.Cl.map，禁止 legacy receptor.CL.map。",
            details={"hydrated_types": hydrated_types, "files": sorted(names)},
        )
    result_coverage = result.get("grid_coverage")
    manifest_coverage = manifest.get("grid_coverage")
    if (
        not isinstance(result_coverage, Mapping)
        or not isinstance(manifest_coverage, Mapping)
        or dict(result_coverage) != dict(manifest_coverage)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_GRID_COVERAGE_INVALID",
            f"{system_id} maps API 响应与发布 manifest 的网格覆盖不一致。",
        )
    coverage_sha256 = _canonical_json_sha256(manifest_coverage)
    if (
        str(manifest.get("grid_coverage_sha256") or "").lower()
        != coverage_sha256
        or str(result.get("grid_coverage_sha256") or "").lower()
        != coverage_sha256
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_GRID_COVERAGE_INVALID",
            f"{system_id} 网格覆盖 canonical SHA256 不一致。",
            details={
                "computed": coverage_sha256,
                "manifest": manifest.get("grid_coverage_sha256"),
                "response": result.get("grid_coverage_sha256"),
            },
        )
    coverage_evidence = _validate_grid_coverage(
        manifest_coverage,
        protocol["grid"],
        system["box"],
    )
    coverage_evidence["sha256"] = coverage_sha256
    water_record = maps.get("water_map")
    water_record = water_record if isinstance(water_record, Mapping) else {}
    water_path = _project_artifact(
        project_root,
        str(water_record.get("relative_path") or ""),
        f"{system_id} W map",
    )
    water_identity = _validate_file_record(
        water_path,
        water_record,
        label=f"{system_id} W map",
    )
    integrity = manifest.get("integrity")
    integrity = integrity if isinstance(integrity, Mapping) else {}
    if (
        integrity.get("sources_unchanged") is not True
        or integrity.get("autogrid_unchanged") is not True
        or not _snapshot_matches(
            integrity.get("autogrid_after") or {},
            toolchain["autogrid"],
        )
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_MAPS_INTEGRITY_INVALID",
            f"{system_id} maps 生成后来源或 AutoGrid 身份发生变化。",
            details={"integrity": dict(integrity)},
        )
    return {
        "map_set_id": str(result.get("map_set_id") or ""),
        "manifest": manifest_identity,
        "autogrid": {
            "path": autogrid["path"],
            "version": autogrid["version"],
            "sha256": autogrid["sha256"],
            "command": command,
        },
        "ligand_atom_types": hydrated_types,
        "autogrid_ligand_atom_types": autogrid_types,
        "files": observed_files,
        "water_map": {
            "relative_path": str(water_record.get("relative_path") or ""),
            **water_identity,
        },
        "grid_coverage": coverage_evidence,
        "canonical_chlorine_map_gate": True,
    }


def _split_models(text: str) -> list[list[str]]:
    models: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if MODEL_PATTERN.match(line):
            if current is not None:
                _fail(
                    "HYDRATED_MULTISYSTEM_MODEL_FORMAT_INVALID",
                    "Vina 输出出现嵌套 MODEL。",
                )
            current = [line]
        elif line.strip() == "ENDMDL":
            if current is None:
                _fail(
                    "HYDRATED_MULTISYSTEM_MODEL_FORMAT_INVALID",
                    "Vina 输出出现无起点 ENDMDL。",
                )
            current.append(line)
            models.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None:
        _fail(
            "HYDRATED_MULTISYSTEM_MODEL_FORMAT_INVALID",
            "Vina 输出最后一个 MODEL 未闭合。",
        )
    return models


def _validate_continuous_modes(
    models: Sequence[Sequence[str]],
    allowed_ids: Sequence[int],
) -> dict[str, Any]:
    ids: list[int] = []
    affinities: list[float] = []
    water_by_mode: list[int] = []
    for model in models:
        if not model:
            _fail(
                "HYDRATED_MULTISYSTEM_MODE_COUNT_INVALID",
                "Vina 输出包含空 MODEL。",
            )
        match = MODEL_PATTERN.match(str(model[0]))
        if not match:
            _fail(
                "HYDRATED_MULTISYSTEM_MODE_SEQUENCE_INVALID",
                "MODEL 首行无法解析。",
            )
        ids.append(int(match.group(1)))
        affinity: float | None = None
        water_count = 0
        for line in model:
            affinity_match = AFFINITY_PATTERN.match(str(line))
            if affinity_match:
                affinity = float(affinity_match.group(1))
            if str(line).startswith(("ATOM", "HETATM")):
                parts = str(line).split()
                if parts and parts[-1] == "W":
                    water_count += 1
        if affinity is None:
            _fail(
                "HYDRATED_MULTISYSTEM_AFFINITY_MISSING",
                f"MODEL {ids[-1]} 缺少 VINA RESULT。",
            )
        affinities.append(affinity)
        water_by_mode.append(water_count)
    allowed = [int(item) for item in allowed_ids]
    expected = allowed[: len(ids)]
    if not ids or len(ids) > len(allowed) or ids != expected:
        _fail(
            "HYDRATED_MULTISYSTEM_MODE_COUNT_INVALID",
            "Vina 实际输出必须包含 1–9 个从 MODEL 1 开始的连续构象。",
            details={
                "allowed": allowed,
                "expected_for_observed_count": expected,
                "actual": ids,
            },
        )
    return {
        "count": len(ids),
        "ids": ids,
        "continuous": True,
        "affinities": affinities,
        "water_by_mode": water_by_mode,
    }


def _validate_output_normalization(
    project_root: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    normalization = metadata.get("output_normalization")
    if not isinstance(normalization, Mapping):
        _fail(
            "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            "运行 metadata 缺少 Vina 输出规范化记录。",
        )
    status = str(normalization.get("status") or "")
    if (
        normalization.get("method") != "torsdof_endmdl_nul_padding_v1"
        or status not in {"not_required", "normalized"}
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            "Vina 输出规范化方法或状态不受支持。",
            details={"normalization": dict(normalization)},
        )
    normalized_path = _project_artifact(
        project_root,
        str(normalization.get("normalized_file") or metadata.get("output_file") or ""),
        "normalized Vina output",
    )
    normalized_payload = normalized_path.read_bytes()
    try:
        normalized_text = normalized_payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            "规范化 Vina 输出不是严格 UTF-8。",
            details={"error": str(exc)},
        )
    bad_controls = [
        byte for byte in normalized_payload if byte < 32 and byte not in {9, 10, 13}
    ]
    if (
        bad_controls
        or int(normalization.get("normalized_size_bytes") or 0) != len(normalized_payload)
        or str(normalization.get("normalized_sha256") or "") != _sha256_bytes(normalized_payload)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            "规范化 Vina 输出含控制字节或身份记录不一致。",
        )
    raw_path = _project_artifact(
        project_root,
        str(normalization.get("raw_output_file") or normalization.get("source_file") or ""),
        "raw Vina output",
    )
    raw_payload = raw_path.read_bytes()
    if (
        int(normalization.get("source_size_bytes") or 0) != len(raw_payload)
        or str(normalization.get("source_sha256") or "") != _sha256_bytes(raw_payload)
        or (status == "normalized" and normalization.get("changed") is not True)
        or (status == "not_required" and normalization.get("changed") is True)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            "原始 Vina 输出未被完整保留或规范化状态自相矛盾。",
        )
    return {
        "status": status,
        "method": normalization["method"],
        "raw": {
            "relative_path": str(normalization.get("raw_output_file") or ""),
            "size_bytes": len(raw_payload),
            "sha256": _sha256_bytes(raw_payload),
        },
        "normalized": {
            "relative_path": str(normalization.get("normalized_file") or ""),
            "size_bytes": len(normalized_payload),
            "sha256": _sha256_bytes(normalized_payload),
            "utf8_valid": True,
            "control_free": True,
        },
        "text": normalized_text,
    }


def _validate_water_conservation(
    summary: Mapping[str, Any],
    *,
    prepared_water_count: int,
    mode_count: int,
    modes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = int(summary.get("raw_water_count") or 0)
    candidate = int(summary.get("candidate_water_count", raw) or 0)
    retained = int(summary.get("retained_water_count") or 0)
    strong = int(summary.get("strong_water_count") or 0)
    weak = int(summary.get("weak_water_count") or 0)
    displaced = int(summary.get("displaced_water_count") or 0)
    if (
        int(summary.get("pose_count", mode_count) or 0) != mode_count
        or raw != prepared_water_count * mode_count
        or candidate != raw
        or retained != strong + weak
        or raw != retained + displaced
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_WATER_CONSERVATION_FAILED",
            "水合后处理未满足原始、保留、强/弱和置换水守恒。",
            details={
                "summary": dict(summary),
                "prepared_water_count": prepared_water_count,
                "mode_count": mode_count,
            },
        )
    if modes is not None:
        if len(modes) != mode_count:
            _fail(
                "HYDRATED_MULTISYSTEM_WATER_CONSERVATION_FAILED",
                "逐构象水记录数量与模式数不一致。",
            )
        per_mode_raw = 0
        per_mode_retained = 0
        per_mode_strong = 0
        per_mode_weak = 0
        per_mode_displaced = 0
        for mode in modes:
            mode_candidate = int(mode.get("candidate_water_count") or 0)
            mode_retained = int(mode.get("retained_water_count") or 0)
            mode_strong = int(mode.get("strong_water_count") or 0)
            mode_weak = int(mode.get("weak_water_count") or 0)
            mode_displaced = int(mode.get("displaced_water_count") or 0)
            if (
                mode_candidate != prepared_water_count
                or mode_retained != mode_strong + mode_weak
                or mode_candidate != mode_retained + mode_displaced
            ):
                _fail(
                    "HYDRATED_MULTISYSTEM_WATER_CONSERVATION_FAILED",
                    "单个构象的水分类不守恒。",
                    details={"mode": dict(mode)},
                )
            per_mode_raw += mode_candidate
            per_mode_retained += mode_retained
            per_mode_strong += mode_strong
            per_mode_weak += mode_weak
            per_mode_displaced += mode_displaced
        if (
            per_mode_raw,
            per_mode_retained,
            per_mode_strong,
            per_mode_weak,
            per_mode_displaced,
        ) != (raw, retained, strong, weak, displaced):
            _fail(
                "HYDRATED_MULTISYSTEM_WATER_CONSERVATION_FAILED",
                "逐构象水分类汇总与顶层 summary 不一致。",
            )
    return {
        "pose_count": mode_count,
        "prepared_water_count_per_pose": prepared_water_count,
        "raw_water_count": raw,
        "retained_water_count": retained,
        "strong_water_count": strong,
        "weak_water_count": weak,
        "displaced_water_count": displaced,
        "conserved": True,
    }


def _validate_report_semantics(
    report_text: str,
    report_contract: Mapping[str, Any],
) -> dict[str, Any]:
    required = report_contract.get("required_phrases")
    if not isinstance(required, list) or not required:
        _fail(
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
            "报告语义清单为空。",
        )
    missing = [str(item) for item in required if str(item) not in report_text]
    if missing:
        _fail(
            "HYDRATED_MULTISYSTEM_REPORT_SEMANTICS_MISSING",
            "水合报告缺少必要的科学边界声明。",
            details={"missing": missing},
        )
    return {"required_phrases": [str(item) for item in required], "all_present": True}


def _diagnostics(
    system: Mapping[str, Any],
    *,
    observed_best_affinity: float,
    observed_water_map_sha256: str,
    observed_hydrated_manifest_sha256: str,
) -> dict[str, Any]:
    reference = system["diagnostics"]
    assert isinstance(reference, Mapping)
    best_reference = float(reference["best_affinity_reference_kcal_mol"])
    return {
        "best_affinity": {
            "observed_kcal_mol": observed_best_affinity,
            "reference_kcal_mol": best_reference,
            "delta_kcal_mol": observed_best_affinity - best_reference,
            "gate": False,
            "interpretation": "同次运行 pose 排序诊断；不作跨体系、跨平台硬门禁。",
        },
        "pose_rmsd": {
            "reference_angstrom": float(reference["pose_rmsd_reference_angstrom"]),
            "classification": "known_pose_quality_non_positive",
            "gate": False,
            "scientific_success_oracle": False,
        },
        "historical_external_altloc_hashes": {
            "observed_water_map_sha256": observed_water_map_sha256,
            "reference_water_map_sha256": str(
                reference["previous_external_altloc_probe_water_map_sha256"]
            ),
            "observed_hydrated_manifest_sha256": observed_hydrated_manifest_sha256,
            "reference_hydrated_manifest_sha256": str(
                reference["previous_external_altloc_probe_hydrated_manifest_sha256"]
            ),
            "gate": False,
            "reason": (
                "历史探针额外使用 altloc 参数；当前 verifier 严格调用公开受体准备 API。"
            ),
        },
    }


def _validate_frozen_receptor(
    metadata: Mapping[str, Any],
    receptor_output: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots = metadata.get("snapshots")
    inputs = snapshots.get("inputs") if isinstance(snapshots, Mapping) else None
    frozen = inputs.get("receptor") if isinstance(inputs, Mapping) else None
    if (
        not isinstance(frozen, Mapping)
        or str(frozen.get("sha256") or "")
        != str(receptor_output.get("sha256") or "")
        or str(frozen.get("source_relative_path") or "")
        != str(receptor_output.get("relative_path") or "")
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_RECEPTOR_FREEZE_MISMATCH",
            "运行冻结受体与公开受体准备输出不一致或快照缺失。",
            details={
                "frozen_receptor": (
                    dict(frozen) if isinstance(frozen, Mapping) else None
                ),
                "prepared_receptor": dict(receptor_output),
            },
        )
    return {
        "source_relative_path": str(frozen["source_relative_path"]),
        "size_bytes": int(frozen.get("size_bytes") or 0),
        "sha256": str(frozen["sha256"]),
        "matches_public_preparation": True,
    }


def _validate_run_and_results(
    project_root: Path,
    prepared_run: Mapping[str, Any],
    executed: Mapping[str, Any],
    analyzed: Mapping[str, Any],
    hydrated: Mapping[str, Any],
    exported: Mapping[str, Any],
    *,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
    toolchain: Mapping[str, Any],
    maps_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    run_id = str(prepared_run.get("run_id") or executed.get("run_id") or "")
    metadata = executed.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata_path = _project_artifact(
            project_root,
            str(executed.get("metadata_file") or Path("runs", run_id, "metadata.json")),
            "run metadata",
        )
        metadata = _read_json(metadata_path, "run metadata")
    command = _require_command_executable(
        metadata.get("executed_command") or metadata.get("command"),
        Path(str(toolchain["vina"]["path"])),
        label="hydrated Vina execution",
    )
    command_lower = [item.lower() for item in command]
    if (
        "--maps" not in command
        or "--scoring" not in command
        or command[command.index("--scoring") + 1].lower() != "ad4"
        or "--receptor" in command_lower
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_VINA_COMMAND_INVALID",
            "水合 Vina 命令必须使用 --maps --scoring ad4 且不能传 receptor。",
            details={"command": command},
        )
    execution_vina = metadata.get("execution_vina")
    integrity = metadata.get("vina_binary_integrity")
    postprocess = metadata.get("hydrated_postprocess")
    input_integrity = metadata.get("input_snapshot_integrity")
    if (
        metadata.get("status") != "finished"
        or metadata.get("stage") != "finished"
        or not isinstance(execution_vina, Mapping)
        or not _snapshot_matches(execution_vina, toolchain["vina"])
        or str(execution_vina.get("version") or "") != toolchain["vina"]["version"]
        or not isinstance(integrity, Mapping)
        or integrity.get("match") is not True
        or not isinstance(postprocess, Mapping)
        or postprocess.get("status") != "finished"
        or not isinstance(input_integrity, Mapping)
        or input_integrity.get("ok") is not True
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_RUN_PROVENANCE_INVALID",
            "运行未满足 finished、输入冻结和 Vina 二进制完整性要求。",
            details={"run_id": run_id},
        )
    normalization = _validate_output_normalization(project_root, metadata)
    mode_evidence = _validate_continuous_modes(
        _split_models(normalization["text"]),
        protocol["vina"]["allowed_continuous_mode_ids"],
    )
    mode_count = int(mode_evidence["count"])
    prepared_water_count = int(system["hydrated_ligand"]["water_count"])
    if any(count != prepared_water_count for count in mode_evidence["water_by_mode"]):
        _fail(
            "HYDRATED_MULTISYSTEM_RAW_MODE_WATER_COUNT_INVALID",
            "每个原始构象必须保留水合配体准备产生的完整 W 数量。",
            details={"water_by_mode": mode_evidence["water_by_mode"]},
        )

    scores = hydrated.get("scores")
    modes = hydrated.get("modes")
    summary = hydrated.get("water_summary")
    if (
        not isinstance(scores, list)
        or len(scores) != mode_count
        or not isinstance(modes, list)
        or len(modes) != mode_count
        or not isinstance(summary, Mapping)
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_RESULTS_INVALID",
            "scores、modes 与 water_summary 未覆盖全部实际输出构象。",
        )
    score_modes = [
        int(item.get("mode") or item.get("rank") or 0)
        for item in scores
        if isinstance(item, Mapping)
    ]
    hydrated_mode_ids = [
        int(item.get("mode") or item.get("rank") or 0)
        for item in modes
        if isinstance(item, Mapping)
    ]
    if (
        score_modes != mode_evidence["ids"]
        or hydrated_mode_ids != mode_evidence["ids"]
    ):
        _fail(
            "HYDRATED_MULTISYSTEM_RESULT_MODE_SEQUENCE_INVALID",
            "评分表与逐构象水结果必须连续覆盖全部实际输出构象。",
            details={"score_modes": score_modes, "hydrated_modes": hydrated_mode_ids},
        )
    water_evidence = _validate_water_conservation(
        summary,
        prepared_water_count=prepared_water_count,
        mode_count=mode_count,
        modes=[
            dict(item.get("water_summary") or {})
            for item in modes
            if isinstance(item, Mapping)
        ],
    )
    for index, (mode, score) in enumerate(zip(modes, scores, strict=True)):
        if not isinstance(mode, Mapping) or not isinstance(score, Mapping):
            continue
        raw_affinity = float(mode.get("raw_affinity_kcal_mol"))
        score_affinity = float(score.get("affinity_kcal_mol"))
        output_affinity = float(mode_evidence["affinities"][index])
        if (
            abs(raw_affinity - output_affinity) > 1e-6
            or not vina_affinity_serialization_matches(
                str(score.get("affinity_text") or score_affinity),
                output_affinity,
                verbosity=int(protocol["vina"]["verbosity"]),
            )
        ):
            _fail(
                "HYDRATED_MULTISYSTEM_AFFINITY_CHANGED",
                "水后处理改变了 raw affinity，或日志/PDBQT 评分超出序列化精度。",
                details={
                    "mode": index + 1,
                    "raw_affinity_kcal_mol": raw_affinity,
                    "score_affinity_kcal_mol": score_affinity,
                    "output_affinity_kcal_mol": output_affinity,
                },
            )

    scores_path = _project_artifact(
        project_root,
        str(analyzed.get("scores_file") or Path("runs", run_id, "scores.csv")),
        "run scores.csv",
    )
    project_scores_path = _project_artifact(
        project_root,
        str(analyzed.get("project_scores_file") or "results/hydrated_ad4_scores.csv"),
        "project hydrated scores.csv",
    )
    if scores_path.read_bytes() != project_scores_path.read_bytes():
        _fail(
            "HYDRATED_MULTISYSTEM_SCORES_PUBLICATION_MISMATCH",
            "run 与 project 评分 CSV 字节不一致。",
        )
    report_path = _project_artifact(
        project_root,
        str(exported.get("report_file") or ""),
        "run hydrated report",
    )
    project_report_path = _project_artifact(
        project_root,
        str(exported.get("project_report_file") or ""),
        "project hydrated report",
    )
    report_bytes = report_path.read_bytes()
    if report_bytes != project_report_path.read_bytes():
        _fail(
            "HYDRATED_MULTISYSTEM_REPORT_PUBLICATION_MISMATCH",
            "run 与 project Markdown 报告字节不一致。",
        )
    report_text = report_bytes.decode("utf-8", errors="strict")
    report_evidence = _validate_report_semantics(report_text, protocol["report"])
    observed_best = float(
        analyzed.get("best_affinity")
        if analyzed.get("best_affinity") is not None
        else mode_evidence["affinities"][0]
    )
    diagnostics = _diagnostics(
        system,
        observed_best_affinity=observed_best,
        observed_water_map_sha256=str(maps_evidence["water_map"]["sha256"]),
        observed_hydrated_manifest_sha256=str(
            maps_evidence["manifest"]["sha256"]
        ),
    )
    return {
        "run_id": run_id,
        "command": command,
        "vina": {
            "path": execution_vina["path"],
            "version": execution_vina["version"],
            "sha256": execution_vina["sha256"],
            "binary_integrity_match": True,
        },
        "input_snapshot_integrity": True,
        "output_normalization": {
            key: value for key, value in normalization.items() if key != "text"
        },
        "modes": mode_evidence,
        "water_conservation": water_evidence,
        "scores": {
            "run_sha256": _sha256(scores_path),
            "project_sha256": _sha256(project_scores_path),
            "published_bytes_identical": True,
        },
        "report": {
            "run_sha256": _sha256(report_path),
            "project_sha256": _sha256(project_report_path),
            "published_bytes_identical": True,
            **report_evidence,
        },
        "diagnostics": diagnostics,
    }


def _run_system_workflow(
    system_id: str,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
    source_paths: Mapping[str, Path],
    source_evidence: Mapping[str, Any],
    *,
    project_parent: Path,
    toolchain: Mapping[str, Any],
) -> dict[str, Any]:
    created = _require_ok(
        "create_project",
        create_project(
            f"hydrated_{system_id.lower()}_multisystem_acceptance",
            str(project_parent),
        ),
    )
    project_root = Path(str(created.get("project_dir") or "")).resolve(strict=False)
    try:
        project_root.relative_to(project_parent.resolve(strict=False))
    except ValueError:
        _fail(
            "HYDRATED_MULTISYSTEM_PROJECT_OUTSIDE_TEMP",
            "公开 API 在临时工作区之外创建了项目。",
            details={"project": str(project_root), "parent": str(project_parent)},
        )

    receptor_before_import = _verify_downloaded_source(
        source_paths["receptor"],
        source_evidence["receptor"],
        label=f"{system_id} receptor source before import",
    )
    receptor_import = _require_ok(
        "import_receptor_raw_file",
        import_receptor_raw_file(str(project_root), str(source_paths["receptor"])),
    )
    receptor_raw_evidence = _verify_raw_import(
        project_root,
        receptor_import,
        source_paths["receptor"],
        source_evidence["receptor"],
        receptor_before_import,
        label=f"{system_id} receptor raw import",
    )
    ligand_before_import = _verify_downloaded_source(
        source_paths["ligand"],
        source_evidence["ligand"],
        label=f"{system_id} ligand source before import",
    )
    ligand_import = _require_ok(
        "import_ligand_raw_file",
        import_ligand_raw_file(str(project_root), str(source_paths["ligand"])),
    )
    ligand_raw_evidence = _verify_raw_import(
        project_root,
        ligand_import,
        source_paths["ligand"],
        source_evidence["ligand"],
        ligand_before_import,
        label=f"{system_id} ligand raw import",
    )
    raw_evidence = {
        "receptor": receptor_raw_evidence,
        "ligand": ligand_raw_evidence,
    }

    receptor_preparation = system.get("receptor_preparation")
    if not isinstance(receptor_preparation, Mapping):
        _fail(
            "HYDRATED_MULTISYSTEM_RECEPTOR_CONTROLS_MISSING",
            f"{system_id} 缺少显式受体准备合同。",
        )
    receptor_options = {
        "protocol": str(receptor_preparation["protocol"]),
        "receptor_controls": dict(receptor_preparation["receptor_controls"]),
    }
    receptor_prepared = _require_preparation_ok(
        "prepare_receptor_pdbqt",
        prepare_receptor_pdbqt(
            str(project_root),
            options=receptor_options,
        ),
        project_root,
    )
    ligand_prepared = _require_preparation_ok(
        "prepare_ligand_pdbqt",
        prepare_ligand_pdbqt(str(project_root)),
        project_root,
    )
    standard_preparation = {
        "receptor": _validate_standard_preparation(
            project_root,
            receptor_prepared,
            "receptor",
            toolchain,
            raw_input_evidence=raw_evidence["receptor"],
            receptor_preparation=receptor_preparation,
        ),
        "ligand": _validate_standard_preparation(
            project_root,
            ligand_prepared,
            "ligand",
            toolchain,
            raw_input_evidence=raw_evidence["ligand"],
        ),
    }

    _require_ok(
        "update_box_params",
        update_box_params(str(project_root), dict(system["box"])),
    )
    vina_values = {
        key: protocol["vina"][key]
        for key in (
            "exhaustiveness",
            "num_modes",
            "energy_range",
            "cpu",
            "seed",
            "verbosity",
        )
    }
    _require_ok(
        "update_vina_params",
        update_vina_params(str(project_root), vina_values),
    )

    hydrated_result = _require_ok(
        "prepare_hydrated_ligand",
        prepare_hydrated_ligand(str(project_root)),
    )
    hydrated_evidence = _validate_hydrated_ligand(
        project_root,
        hydrated_result,
        system_id,
        system,
        toolchain,
        source_evidence["ligand"],
    )
    pre_maps_status = _require_ok(
        "get_hydrated_status(pre-maps)",
        get_hydrated_status(str(project_root)),
    )
    if pre_maps_status.get("preparation_ready") is not True:
        _fail(
            "HYDRATED_MULTISYSTEM_PREPARATION_STATUS_INVALID",
            f"{system_id} 水合配体状态未达到 preparation_ready。",
            details=pre_maps_status,
        )
    maps_result = _require_ok(
        "generate_hydrated_maps",
        generate_hydrated_maps(
            str(project_root),
            {
                "spacing": protocol["grid"]["spacing"],
                "grid_points": dict(protocol["grid"]["grid_points"]),
            },
        ),
    )
    maps_evidence = _validate_maps(
        project_root,
        maps_result,
        system_id,
        system,
        protocol,
        toolchain,
    )
    post_maps_status = _require_ok(
        "get_hydrated_status(post-maps)",
        get_hydrated_status(str(project_root)),
    )
    if post_maps_status.get("maps_ready") is not True:
        _fail(
            "HYDRATED_MULTISYSTEM_MAPS_STATUS_INVALID",
            f"{system_id} 水合 maps 状态未达到 maps_ready。",
            details=post_maps_status,
        )
    preflight = _require_ok(
        "get_hydrated_run_preflight",
        get_hydrated_run_preflight(str(project_root)),
    )
    if preflight.get("ready") is False:
        _fail(
            "HYDRATED_MULTISYSTEM_RUN_PREFLIGHT_BLOCKED",
            f"{system_id} 水合运行预检未通过。",
            details=preflight,
        )
    prepared_run = _require_ok(
        "prepare_hydrated_run",
        prepare_hydrated_run(str(project_root)),
    )
    run_id = str(prepared_run.get("run_id") or "")
    executed = _require_ok(
        "execute_prepared_vina_run",
        execute_prepared_vina_run(str(project_root), run_id),
    )
    analyzed = _require_ok(
        "analyze_vina_run_results",
        analyze_vina_run_results(str(project_root), run_id),
    )
    hydrated_results = _require_ok(
        "load_hydrated_results",
        load_hydrated_results(str(project_root), run_id),
    )
    exported = _require_ok(
        "export_markdown_report",
        export_markdown_report(str(project_root), run_id),
    )
    run_evidence = _validate_run_and_results(
        project_root,
        prepared_run,
        executed,
        analyzed,
        hydrated_results,
        exported,
        system=system,
        protocol=protocol,
        toolchain=toolchain,
        maps_evidence=maps_evidence,
    )
    receptor_output = standard_preparation["receptor"]["output"]
    executed_metadata = executed.get("metadata")
    if not isinstance(executed_metadata, Mapping):
        _fail(
            "HYDRATED_MULTISYSTEM_RECEPTOR_FREEZE_MISMATCH",
            f"{system_id} 运行响应缺少 metadata，无法绑定受体准备输出。",
        )
    frozen_receptor = _validate_frozen_receptor(
        executed_metadata,
        receptor_output,
    )
    return {
        "system_id": system_id,
        "classification": system["scientific_classification"],
        "network_or_download_used": True,
        "sources": dict(source_evidence),
        "raw_imports": raw_evidence,
        "standard_preparation": standard_preparation,
        "frozen_receptor": frozen_receptor,
        "box": dict(system["box"]),
        "vina_parameters": vina_values,
        "hydrated_ligand": hydrated_evidence,
        "maps": maps_evidence,
        "run_and_report": run_evidence,
        "scientific_success_claimed": False,
    }


def _restore_environment(
    previous: Mapping[str, str | None],
) -> dict[str, Any]:
    errors: dict[str, str] = {}
    for name, value in previous.items():
        try:
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)
    return {
        "variables": list(previous),
        "restored": not errors,
        "errors": errors,
    }


def verify_hydrated_multisystem(
    *,
    python_executable: str | Path,
    autogrid_executable: str | Path,
    vina_executable: str | Path,
    fetcher: Fetcher | None = None,
    download_timeout: int = 60,
) -> dict[str, Any]:
    manifest = _load_manifest()
    protocol = manifest["protocol"]
    systems = manifest["systems"]
    source_policy = manifest["source_policy"]
    assert isinstance(protocol, Mapping)
    assert isinstance(systems, Mapping)
    assert isinstance(source_policy, Mapping)
    python_path = Path(python_executable).expanduser().resolve(strict=False)
    autogrid_path = Path(autogrid_executable).expanduser().resolve(strict=False)
    vina_path = Path(vina_executable).expanduser().resolve(strict=False)
    work_root = Path(tempfile.mkdtemp(prefix="DockStart_hydrated_multisystem_"))
    settings_path = work_root / "dockstart_settings.json"
    resource_root = work_root / "isolated_resources"
    project_parent = work_root / "projects"
    download_root = work_root / "downloads"
    previous_environment = {
        SETTINGS_ENV_VAR: os.environ.get(SETTINGS_ENV_VAR),
        RESOURCE_DIR_ENV_VAR: os.environ.get(RESOURCE_DIR_ENV_VAR),
        PREPARATION_TOOLS_SNAPSHOT_ENV_VAR: os.environ.get(
            PREPARATION_TOOLS_SNAPSHOT_ENV_VAR
        ),
    }
    steps: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    failure: BaseException | None = None
    restoration: dict[str, Any] = {
        "variables": list(previous_environment),
        "restored": False,
        "errors": {"lifecycle": "restoration not attempted"},
    }
    cleanup: dict[str, Any] = {
        "path": str(work_root),
        "cleanup_called": False,
        "removed": False,
        "error": "cleanup not attempted",
    }
    try:
        project_parent.mkdir()
        resource_root.mkdir()
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(resource_root)
        os.environ.pop(PREPARATION_TOOLS_SNAPSHOT_ENV_VAR, None)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    python=str(python_path),
                    autogrid4=str(autogrid_path),
                    vina=str(vina_path),
                )
            )
        )
        toolchain = _verify_toolchain(
            python_path,
            autogrid_path,
            vina_path,
            manifest,
            isolated_root=resource_root,
        )
        steps["toolchain"] = toolchain
        system_results: dict[str, Any] = {}
        for system_id in SYSTEM_IDS:
            system = systems[system_id]
            assert isinstance(system, Mapping)
            source_paths, source_evidence = _download_sources(
                system_id,
                system,
                download_root,
                maximum_bytes=int(source_policy["maximum_source_size_bytes"]),
                fetcher=fetcher,
                timeout=download_timeout,
            )
            system_results[system_id] = _run_system_workflow(
                system_id,
                system,
                protocol,
                source_paths,
                source_evidence,
                project_parent=project_parent,
                toolchain=toolchain,
            )
            steps[system_id] = system_results[system_id]
        result = {
            "ok": True,
            "fixture_id": manifest["fixture_id"],
            "distribution": "metadata_only",
            "network_or_download_used": True,
            "system_ids": list(SYSTEM_IDS),
            "toolchain": toolchain,
            "systems": system_results,
            "scientific_scope": {
                "engineering_chain_acceptance": True,
                "scientific_redocking_success_claimed": False,
                "best_affinity_hard_gate": False,
                "pose_rmsd_success_oracle": False,
            },
            "steps": steps,
        }
    except HydratedMultisystemError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - keep lifecycle structured.
        failure = HydratedMultisystemError(
            "HYDRATED_MULTISYSTEM_UNEXPECTED_ERROR",
            "水合多体系验收出现未分类错误。",
            details={"error": str(exc), "type": type(exc).__name__},
        )
    except BaseException as exc:
        failure = exc
    finally:
        restoration = _restore_environment(previous_environment)
        cleanup_error = ""
        try:
            shutil.rmtree(work_root)
        except Exception as exc:  # noqa: BLE001
            cleanup_error = str(exc)
        cleanup = {
            "path": str(work_root),
            "cleanup_called": True,
            "removed": not work_root.exists(),
            "error": cleanup_error,
        }
    if result is not None:
        result["environment_restoration"] = restoration
        result["temporary_cleanup"] = cleanup
    if isinstance(failure, HydratedMultisystemError):
        failure.steps = steps
        failure.details["environment_restoration"] = restoration
        failure.details["temporary_cleanup"] = cleanup
    if not restoration["restored"] or not cleanup["removed"]:
        lifecycle = HydratedMultisystemError(
            "HYDRATED_MULTISYSTEM_LIFECYCLE_CLEANUP_FAILED",
            "验收结束后环境恢复或临时目录清理失败。",
            details={
                "environment_restoration": restoration,
                "temporary_cleanup": cleanup,
                "primary_error": (
                    {"code": failure.code, "message": failure.message}
                    if isinstance(failure, HydratedMultisystemError)
                    else (
                        {
                            "type": type(failure).__name__,
                            "message": str(failure),
                        }
                        if failure is not None
                        else None
                    )
                ),
            },
        )
        lifecycle.steps = steps
        raise lifecycle
    if failure is not None:
        raise failure
    assert result is not None
    return result


def _error_payload(error: HydratedMultisystemError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
        "steps": error.steps,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download exact 2BYS/2ZJU sources and run DockStart's current "
            "hydrated AD4 public-API acceptance chain."
        )
    )
    parser.add_argument("--python", required=True, help="Python 3.11.15 executable")
    parser.add_argument("--autogrid", required=True, help="External AutoGrid 4.2.7")
    parser.add_argument("--vina", required=True, help="AutoDock Vina 1.2.7")
    parser.add_argument(
        "--download-timeout",
        type=int,
        default=60,
        help="Per-download timeout in seconds (default: 60)",
    )
    return parser


def _reconfigure_utf8_stream(stream: Any) -> bool:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    reconfigure(encoding="utf-8", errors="strict")
    return True


def _configure_utf8_standard_streams() -> dict[str, bool]:
    return {
        "stdout": _reconfigure_utf8_stream(sys.stdout),
        "stderr": _reconfigure_utf8_stream(sys.stderr),
    }


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_standard_streams()
    arguments = _build_parser().parse_args(argv)
    try:
        payload = verify_hydrated_multisystem(
            python_executable=arguments.python,
            autogrid_executable=arguments.autogrid,
            vina_executable=arguments.vina,
            download_timeout=arguments.download_timeout,
        )
    except HydratedMultisystemError as exc:
        print(json.dumps(_error_payload(exc), ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
