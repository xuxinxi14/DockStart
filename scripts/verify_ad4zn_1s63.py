"""Run the external DockStart AutoDock4Zn 1S63 acceptance gate.

No upstream scientific data, AD4Zn parameter file, AutoGrid executable, or
Vina executable is distributed by this fixture. The caller supplies all of
them explicitly. Generated artifacts live only in an automatically removed
temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import autogrid_adapter, vina_adapter  # noqa: E402
from dockstart_core.ad4zn import (  # noqa: E402
    REQUIRED_CONFIRMATIONS,
    get_status as get_ad4zn_status,
    prepare_receptor as prepare_ad4zn_receptor,
    record_parameter_file,
    save_review,
)
from dockstart_core.autogrid import (  # noqa: E402
    AD4ZN_BOX_COVERAGE_METHOD,
    AD4ZN_NBP_R_EPS,
    generate_maps,
    set_scoring_protocol,
    validate_active_maps,
)
from dockstart_core.hydrated_maps import (  # noqa: E402
    HydratedMapError,
    parse_autogrid_map,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
)
from dockstart_core.settings import SETTINGS_ENV_VAR  # noqa: E402

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "ad4zn_1s63"
    / "source_manifest.json"
)
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_BLOB_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MANIFEST_REQUIRED_FILES = frozenset(
    {
        "receptor_input",
        "official_tz_receptor",
        "ligand_input",
        "parameter_file",
        "official_output",
        "official_map_A",
        "official_map_C",
        "official_map_Cl",
        "official_map_HD",
        "official_map_N",
        "official_map_NA",
        "official_map_OA",
        "official_map_e",
        "official_map_d",
    }
)
MANIFEST_CONTAINS_FLAGS = (
    "contains_upstream_structure_files",
    "contains_parameter_files",
    "contains_maps_or_outputs",
    "contains_executables",
)


class AD4ZnAcceptanceError(RuntimeError):
    """A stable, JSON-serializable acceptance failure."""

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
    raise AD4ZnAcceptanceError(code, message, details=details)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_lf(payload: bytes) -> bytes:
    return payload.replace(b"\r\n", b"\n")


def _line_ending_profile(payload: bytes) -> dict[str, Any]:
    crlf_count = payload.count(b"\r\n")
    without_crlf = payload.replace(b"\r\n", b"")
    lf_count = without_crlf.count(b"\n")
    cr_count = without_crlf.count(b"\r")
    styles = [
        name
        for name, count in (
            ("crlf", crlf_count),
            ("lf", lf_count),
            ("cr", cr_count),
        )
        if count
    ]
    return {
        "style": (
            styles[0]
            if len(styles) == 1
            else "mixed"
            if styles
            else "none"
        ),
        "crlf_count": crlf_count,
        "lf_count": lf_count,
        "cr_count": cr_count,
    }


def _canonical_json_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "AD4Zn evidence cannot be serialized canonically.",
            details={"error": str(exc)},
        )
    return _sha256_bytes(payload)


def _validate_manifest_contract(
    payload: Mapping[str, Any],
    *,
    manifest_path: Path,
) -> None:
    if (
        payload.get("schema_version") != 1
        or payload.get("fixture_id") != "ad4zn_1s63_external"
        or payload.get("distribution") != "metadata_only"
        or any(payload.get(key) is not False for key in MANIFEST_CONTAINS_FLAGS)
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            (
                "AD4Zn 1S63 source manifest must be the pinned metadata-only "
                "schema v1 contract."
            ),
            details={
                "schema_version": payload.get("schema_version"),
                "fixture_id": payload.get("fixture_id"),
                "distribution": payload.get("distribution"),
                "contains_flags": {
                    key: payload.get(key) for key in MANIFEST_CONTAINS_FLAGS
                },
            },
        )

    required_files = payload.get("required_files")
    if (
        not isinstance(required_files, Mapping)
        or set(required_files) != MANIFEST_REQUIRED_FILES
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "AD4Zn 1S63 required_files does not match the pinned file set.",
            details={
                "expected": sorted(MANIFEST_REQUIRED_FILES),
                "actual": (
                    sorted(str(key) for key in required_files)
                    if isinstance(required_files, Mapping)
                    else None
                ),
            },
        )

    seen_paths: set[str] = set()
    for key in sorted(MANIFEST_REQUIRED_FILES):
        record = required_files.get(key)
        if not isinstance(record, Mapping):
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} is invalid.",
            )
        relative_text = str(record.get("path") or "")
        relative = Path(relative_text)
        canonical_size = record.get("canonical_size_bytes")
        canonical_sha = str(record.get("canonical_sha256") or "").lower()
        git_blob_sha = str(record.get("git_blob_sha1") or "").lower()
        variants = record.get("checkout_variants")
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in seen_paths
            or record.get("canonical_line_endings") != "LF"
            or isinstance(canonical_size, bool)
            or not isinstance(canonical_size, int)
            or canonical_size <= 0
            or not SHA256_PATTERN.fullmatch(canonical_sha)
            or not GIT_BLOB_SHA1_PATTERN.fullmatch(git_blob_sha)
            or not isinstance(variants, Mapping)
            or set(variants) != {"lf", "crlf"}
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} violates the pinned identity contract.",
                details={"record": dict(record)},
            )
        seen_paths.add(relative.as_posix())

        normalized_variants: dict[str, tuple[int, str]] = {}
        for variant_name in ("lf", "crlf"):
            variant = variants.get(variant_name)
            if not isinstance(variant, Mapping):
                _fail(
                    "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                    f"Manifest file record {key} has an invalid {variant_name} variant.",
                )
            size = variant.get("size_bytes")
            sha = str(variant.get("sha256") or "").lower()
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
                or not SHA256_PATTERN.fullmatch(sha)
            ):
                _fail(
                    "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                    f"Manifest file record {key} has an invalid {variant_name} identity.",
                    details={"variant": dict(variant)},
                )
            normalized_variants[variant_name] = (size, sha)
        if normalized_variants["lf"] != (canonical_size, canonical_sha):
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} does not bind its LF canonical identity.",
            )

    try:
        fixture_entries = {
            item.name
            for item in manifest_path.parent.iterdir()
        }
    except OSError as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "AD4Zn fixture directory cannot be inspected.",
            details={"path": str(manifest_path.parent), "error": str(exc)},
        )
    expected_entries = {"README.md", "source_manifest.json"}
    if fixture_entries != expected_entries:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_DISTRIBUTION_INVALID",
            "The metadata-only AD4Zn fixture contains an unexpected artifact.",
            details={
                "expected": sorted(expected_entries),
                "actual": sorted(fixture_entries),
            },
        )


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_MISSING",
            "AD4Zn 1S63 source manifest is missing.",
            details={"path": str(MANIFEST_PATH)},
        )
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "AD4Zn 1S63 source manifest cannot be parsed.",
            details={"path": str(MANIFEST_PATH), "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "AD4Zn 1S63 source manifest must contain a JSON object.",
        )
    _validate_manifest_contract(payload, manifest_path=MANIFEST_PATH)
    return payload


def _regular_file(path: Path, label: str) -> Path:
    try:
        valid = (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size > 0
            and path.resolve(strict=True) == path.absolute()
        )
    except OSError:
        valid = False
    if not valid:
        _fail(
            "AD4ZN_ACCEPTANCE_FILE_INVALID",
            f"{label} is missing, empty, symbolic, or a reparse path.",
            details={"path": str(path)},
        )
    return path


def _normalized_path(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        candidate = candidate.resolve(strict=False)
    return os.path.normcase(str(candidate))


def _run_probe(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        result = action()
    except AD4ZnAcceptanceError as exc:
        steps[name] = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
        exc.steps = dict(steps)
        raise
    except Exception as exc:  # noqa: BLE001 - normalize the public gate error.
        wrapped = AD4ZnAcceptanceError(
            "AD4ZN_ACCEPTANCE_STEP_UNEXPECTED_ERROR",
            f"Acceptance step {name} failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
        steps[name] = {
            "ok": False,
            "error": {
                "code": wrapped.code,
                "message": wrapped.message,
                "details": wrapped.details,
            },
        }
        wrapped.steps = dict(steps)
        raise wrapped from exc
    steps[name] = {"ok": True, **dict(result)}
    return result


def _require_ok(
    payload: Mapping[str, Any],
    code: str,
    message: str,
) -> Mapping[str, Any]:
    if payload.get("ok"):
        return payload
    error = payload.get("error")
    details: dict[str, Any] = {
        key: payload.get(key)
        for key in ("run_id", "message", "output_file", "log_file")
        if payload.get(key) not in (None, "")
    }
    if isinstance(error, Mapping):
        details["dockstart_error"] = dict(error)
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        details["metadata"] = {
            key: metadata.get(key)
            for key in (
                "run_id",
                "status",
                "stage",
                "exit_code",
                "error_code",
                "error_message",
                "output_file",
                "output_normalization",
            )
            if metadata.get(key) not in (None, "")
        }
    files = payload.get("files")
    if isinstance(files, list):
        details["files"] = [
            {
                key: item.get(key)
                for key in ("key", "path", "status", "size")
            }
            for item in files
            if isinstance(item, Mapping)
        ]
    _fail(code, message, details=details)
    raise AssertionError("unreachable")


def _verify_upstream(
    upstream_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Path], dict[str, Any]]:
    try:
        root = upstream_root.expanduser().resolve(strict=True)
    except OSError as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_UPSTREAM_INVALID",
            "--upstream-root cannot be resolved.",
            details={"path": str(upstream_root), "error": str(exc)},
        )
    if upstream_root.is_symlink() or not root.is_dir():
        _fail(
            "AD4ZN_ACCEPTANCE_UPSTREAM_INVALID",
            "--upstream-root must be a plain AutoDock Vina source directory.",
            details={"path": str(root)},
        )

    upstream = manifest.get("upstream")
    required_files = manifest.get("required_files")
    if not isinstance(upstream, Mapping) or not isinstance(required_files, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest upstream or required_files section is invalid.",
        )

    expected_commit = str(upstream.get("commit") or "").lower()
    expected_tag = str(upstream.get("tag") or "")
    detected_commit = ""
    detected_tag = ""
    if (root / ".git").exists():
        commit_probe = _run_probe(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            cwd=root,
        )
        if commit_probe is not None and commit_probe.returncode == 0:
            detected_commit = commit_probe.stdout.strip().lower()
            if detected_commit != expected_commit:
                _fail(
                    "AD4ZN_ACCEPTANCE_UPSTREAM_COMMIT_MISMATCH",
                    "The supplied AutoDock Vina checkout is not the pinned commit.",
                    details={
                        "expected": expected_commit,
                        "actual": detected_commit,
                    },
                )
            tag_probe = _run_probe(
                ["git", "-C", str(root), "describe", "--tags", "--exact-match"],
                cwd=root,
            )
            if tag_probe is not None and tag_probe.returncode == 0:
                detected_tag = tag_probe.stdout.strip()
                if detected_tag != expected_tag:
                    _fail(
                        "AD4ZN_ACCEPTANCE_UPSTREAM_TAG_MISMATCH",
                        "The supplied checkout tag does not match the manifest.",
                        details={"expected": expected_tag, "actual": detected_tag},
                    )

    resolved: dict[str, Path] = {}
    verified: dict[str, Any] = {}
    for key, value in required_files.items():
        if not isinstance(value, Mapping):
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} is invalid.",
            )
        relative_text = str(value.get("path") or "")
        relative = Path(relative_text)
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} has an unsafe path.",
                details={"path": relative_text},
            )
        path = _regular_file(root / relative, str(key))
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError):
            _fail(
                "AD4ZN_ACCEPTANCE_FILE_OUTSIDE_UPSTREAM",
                f"Manifest file {key} resolves outside --upstream-root.",
                details={"path": str(path)},
            )

        payload = path.read_bytes()
        line_endings = _line_ending_profile(payload)
        if line_endings["style"] not in {"lf", "crlf"}:
            _fail(
                "AD4ZN_ACCEPTANCE_UPSTREAM_LINE_ENDINGS_UNSUPPORTED",
                (
                    f"Upstream file {key} must use one consistent pinned LF "
                    "or CRLF checkout form."
                ),
                details={
                    "path": relative.as_posix(),
                    "accepted": ["crlf", "lf"],
                    "actual": line_endings,
                },
            )
        canonical = _canonical_lf(payload)
        actual_sha = _sha256_bytes(payload)
        canonical_sha = _sha256_bytes(canonical)
        expected_canonical_size = int(value.get("canonical_size_bytes") or 0)
        expected_canonical_sha = str(value.get("canonical_sha256") or "").lower()
        variants = value.get("checkout_variants")
        if (
            value.get("canonical_line_endings") != "LF"
            or not isinstance(variants, Mapping)
            or set(variants) != {"lf", "crlf"}
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} must pin exactly LF and CRLF.",
            )
        if (
            len(canonical) != expected_canonical_size
            or canonical_sha != expected_canonical_sha
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_UPSTREAM_CANONICAL_HASH_MISMATCH",
                f"Upstream file {key} does not match the pinned LF identity.",
                details={
                    "path": relative.as_posix(),
                    "expected_size": expected_canonical_size,
                    "actual_size": len(canonical),
                    "expected_sha256": expected_canonical_sha,
                    "actual_sha256": canonical_sha,
                },
            )

        matched_variant = ""
        for variant_name in ("lf", "crlf"):
            variant_value = variants.get(variant_name)
            if not isinstance(variant_value, Mapping):
                _fail(
                    "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
                    f"Manifest file record {key} has an invalid {variant_name} variant.",
                )
            if (
                len(payload) == int(variant_value.get("size_bytes") or 0)
                and actual_sha
                == str(variant_value.get("sha256") or "").lower()
            ):
                matched_variant = variant_name
                break
        if not matched_variant or matched_variant != line_endings["style"]:
            _fail(
                "AD4ZN_ACCEPTANCE_UPSTREAM_CHECKOUT_HASH_MISMATCH",
                f"Upstream file {key} is neither the pinned LF nor CRLF form.",
                details={
                    "path": relative.as_posix(),
                    "line_endings": line_endings,
                    "matched_variant": matched_variant,
                    "actual_size": len(payload),
                    "actual_sha256": actual_sha,
                },
            )

        detected_blob = ""
        expected_blob = str(value.get("git_blob_sha1") or "").lower()
        if detected_commit:
            blob_probe = _run_probe(
                ["git", "-C", str(root), "rev-parse", f"HEAD:{relative.as_posix()}"],
                cwd=root,
            )
            if blob_probe is None or blob_probe.returncode != 0:
                _fail(
                    "AD4ZN_ACCEPTANCE_UPSTREAM_BLOB_UNAVAILABLE",
                    f"Git could not resolve the pinned blob for {key}.",
                    details={"path": relative.as_posix()},
                )
            detected_blob = blob_probe.stdout.strip().lower()
            if detected_blob != expected_blob:
                _fail(
                    "AD4ZN_ACCEPTANCE_UPSTREAM_BLOB_MISMATCH",
                    f"The repository blob for {key} is not pinned.",
                    details={
                        "path": relative.as_posix(),
                        "expected": expected_blob,
                        "actual": detected_blob,
                    },
                )

        resolved[str(key)] = path
        verified[str(key)] = {
            "path": relative.as_posix(),
            "checkout_variant": matched_variant,
            "size_bytes": len(payload),
            "sha256": actual_sha,
            "canonical_size_bytes": len(canonical),
            "canonical_sha256": canonical_sha,
            "line_endings": line_endings,
            "git_blob_sha1": detected_blob or "not_available_hashes_verified",
        }

    parameter_path = resolved.get("parameter_file")
    if parameter_path is None or parameter_path.parent != root / "data":
        _fail(
            "AD4ZN_ACCEPTANCE_PARAMETER_SOURCE_INVALID",
            "The verifier must use the repository-level data/AD4Zn.dat.",
        )
    return resolved, {
        "repository": str(upstream.get("repository") or ""),
        "tag": expected_tag,
        "detected_tag": detected_tag or "not_available_hashes_verified",
        "expected_commit": expected_commit,
        "detected_commit": detected_commit or "not_available_hashes_verified",
        "files": verified,
    }


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", str(value or ""))
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _verify_tools(
    autogrid_executable: Path,
    vina_executable: Path,
    manifest: Mapping[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    autogrid_path = _regular_file(
        autogrid_executable.expanduser().resolve(strict=False),
        "AutoGrid executable",
    )
    vina_path = _regular_file(
        vina_executable.expanduser().resolve(strict=False),
        "Vina executable",
    )
    external_tools = manifest.get("external_tools")
    if not isinstance(external_tools, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest external_tools section is invalid.",
        )

    autogrid = autogrid_adapter.detect(str(autogrid_path))
    if autogrid.path and _normalized_path(autogrid.path) != _normalized_path(
        autogrid_path
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_AUTOGRID_PATH_MISMATCH",
            "AutoGrid detection did not preserve the caller-supplied executable.",
            details={
                "supplied": str(autogrid_path),
                "detected": str(autogrid.path or ""),
            },
        )
    autogrid_record = external_tools.get("autogrid")
    minimum = (
        str(autogrid_record.get("minimum_version") or "")
        if isinstance(autogrid_record, Mapping)
        else ""
    )
    if (
        autogrid.status != "ok"
        or not autogrid.path
        or _version_tuple(autogrid.version) is None
        or _version_tuple(autogrid.version) < _version_tuple(minimum)
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_AUTOGRID_VERSION_UNSUPPORTED",
            "The selected executable must be AutoGrid 4.2.7 or newer.",
            details={
                "path": str(autogrid_path),
                "status": autogrid.status,
                "version": autogrid.version,
                "minimum": minimum,
                "message": autogrid.message,
                "raw_error": autogrid.raw_error,
            },
        )

    disabled_bundled_candidate = vina_path.with_name(
        f".{vina_path.name}.dockstart_acceptance_disabled_bundled"
    )
    vina = vina_adapter.detect(
        str(vina_path),
        bundled_path=str(disabled_bundled_candidate),
    )
    if vina.path and _normalized_path(vina.path) != _normalized_path(
        vina_path
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_VINA_PATH_MISMATCH",
            "Vina detection did not preserve the caller-supplied executable.",
            details={
                "supplied": str(vina_path),
                "detected": str(vina.path or ""),
            },
        )
    vina_record = external_tools.get("vina")
    required_vina = (
        str(vina_record.get("required_version") or "")
        if isinstance(vina_record, Mapping)
        else ""
    )
    maps_capability = (
        ((vina.capabilities or {}).get("features") or {}).get("maps")
        if isinstance(vina.capabilities, dict)
        else None
    )
    if (
        vina.status != "ok"
        or not vina.path
        or vina.version != required_vina
        or not isinstance(maps_capability, Mapping)
        or maps_capability.get("supported") is not True
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_VINA_VERSION_UNSUPPORTED",
            "The selected executable must be AutoDock Vina 1.2.7 with --maps.",
            details={
                "path": str(vina_path),
                "status": vina.status,
                "version": vina.version,
                "required": required_vina,
                "maps_capability": dict(maps_capability or {}),
                "message": vina.message,
                "raw_error": vina.raw_error,
            },
        )

    return autogrid, vina, {
        "autogrid": {
            "path": str(autogrid_path),
            "version": autogrid.version,
            "sha256": _sha256(autogrid_path),
            "source": autogrid.source,
        },
        "vina": {
            "path": str(vina_path),
            "version": vina.version,
            "sha256": _sha256(vina_path),
            "source": vina.source,
            "maps_capability": dict(maps_capability),
        },
    }


def _pdbqt_type_coordinates(
    path: Path,
    atom_type: str,
) -> list[tuple[float, float, float]]:
    coordinates: list[tuple[float, float, float]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_PDBQT_INVALID",
            "A PDBQT file could not be read as UTF-8 text.",
            details={"path": str(path), "error": str(exc)},
        )
    expected = atom_type.upper()
    for line in lines:
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        fields = line.split()
        if not fields or fields[-1].upper() != expected:
            continue
        try:
            coordinate = (
                float(line[30:38].strip()),
                float(line[38:46].strip()),
                float(line[46:54].strip()),
            )
        except ValueError as exc:
            _fail(
                "AD4ZN_ACCEPTANCE_PDBQT_INVALID",
                f"A {atom_type} atom has invalid PDBQT coordinates.",
                details={"path": str(path), "line": line, "error": str(exc)},
            )
        if not all(math.isfinite(item) for item in coordinate):
            _fail(
                "AD4ZN_ACCEPTANCE_PDBQT_INVALID",
                f"A {atom_type} atom has non-finite coordinates.",
                details={"path": str(path), "line": line},
            )
        coordinates.append(coordinate)
    return coordinates


def _validate_text_artifact(path: Path, label: str) -> dict[str, Any]:
    artifact = _regular_file(path, label)
    payload = artifact.read_bytes()
    invalid_offsets = [
        index
        for index, value in enumerate(payload)
        if value == 127 or (value < 32 and value not in {9, 10, 13})
    ]
    if invalid_offsets:
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_CONTROL_BYTES_INVALID",
            f"{label} contains NUL or another invalid control byte.",
            details={
                "path": str(artifact),
                "invalid_byte_count": len(invalid_offsets),
                "first_invalid_offsets": invalid_offsets[:20],
                "contains_nul": 0 in payload,
            },
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_TEXT_INVALID",
            f"{label} is not valid UTF-8 text.",
            details={"path": str(artifact), "error": str(exc)},
        )
    invalid_codepoints = [
        index
        for index, character in enumerate(text)
        if (
            ord(character) == 127
            or ord(character) == 0
            or (ord(character) < 32 and ord(character) not in {9, 10, 13})
            or 127 <= ord(character) <= 159
        )
    ]
    if invalid_codepoints:
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_CONTROL_CODEPOINTS_INVALID",
            f"{label} contains an invalid Unicode control codepoint.",
            details={
                "path": str(artifact),
                "invalid_codepoint_count": len(invalid_codepoints),
                "first_invalid_offsets": invalid_codepoints[:20],
            },
        )
    return {
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "invalid_control_byte_count": 0,
    }


def _validate_output_normalization(
    project_dir: Path,
    metadata: Mapping[str, Any],
    output_identity: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    vina_expected = expected.get("vina")
    normalization_expected = (
        vina_expected.get("output_normalization")
        if isinstance(vina_expected, Mapping)
        else None
    )
    record = metadata.get("output_normalization")
    if not isinstance(normalization_expected, Mapping) or not isinstance(
        record,
        Mapping,
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_NORMALIZATION_MISSING",
            "The run lacks the pinned Vina output-normalization record.",
        )
    method = str(normalization_expected.get("method") or "")
    status = str(record.get("status") or "")
    output_file = str(metadata.get("output_file") or "")
    if record.get("method") != method:
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_NORMALIZATION_METHOD_MISMATCH",
            "The run used an unpinned output-normalization method.",
            details={"expected": method, "actual": record.get("method")},
        )
    output_sha = str(output_identity.get("sha256") or "")
    if (
        str(record.get("normalized_file") or "") != output_file
        or str(record.get("normalized_sha256") or "") != output_sha
        or int(record.get("normalized_size_bytes") or 0)
        != int(output_identity.get("size_bytes") or 0)
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_NORMALIZED_IDENTITY_MISMATCH",
            "Published out.pdbqt is not bound to the normalization record.",
            details={
                "output_file": output_file,
                "output_identity": dict(output_identity),
                "record": dict(record),
            },
        )

    if status == "not_required":
        if (
            record.get("changed") is not False
            or record.get("raw_output_file")
            or int(record.get("nul_bytes_detected") or 0) != 0
            or int(record.get("nul_bytes_removed") or 0) != 0
            or str(record.get("source_file") or "") != output_file
            or str(record.get("source_sha256") or "") != output_sha
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_OUTPUT_NOT_REQUIRED_INVALID",
                "A control-free Vina output has an inconsistent record.",
                details={"record": dict(record)},
            )
        return {
            "status": status,
            "method": method,
            "changed": False,
            "raw_output_file": "",
            "normalized_sha256": output_sha,
            "recognized_padding_blocks": 0,
            "nul_bytes_removed": 0,
        }

    if status != "normalized":
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_NORMALIZATION_FAILED",
            "The Vina output-normalization record is not successful.",
            details={"record": dict(record)},
        )
    observation = normalization_expected.get(
        "windows_vina_1_2_7_observation"
    )
    if not isinstance(observation, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Expected Windows Vina output observation is invalid.",
        )
    blocks = int(observation.get("recognized_padding_blocks") or 0)
    padding_length = int(observation.get("padding_bytes_per_block") or 0)
    detected = int(observation.get("nul_bytes_detected") or 0)
    removed = int(observation.get("nul_bytes_removed") or 0)
    raw_relative = str(record.get("raw_output_file") or "")
    raw_relative_path = Path(raw_relative)
    if (
        record.get("changed") is not True
        or int(record.get("recognized_padding_blocks") or 0) != blocks
        or list(record.get("recognized_padding_lengths") or [])
        != [padding_length] * blocks
        or int(record.get("nul_bytes_detected") or 0) != detected
        or int(record.get("nul_bytes_removed") or 0) != removed
        or not raw_relative
        or raw_relative_path.is_absolute()
        or ".." in raw_relative_path.parts
        or str(record.get("source_file") or "") != raw_relative
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_NORMALIZATION_COUNT_MISMATCH",
            "Windows Vina NUL padding does not match the pinned observation.",
            details={"expected": dict(observation), "record": dict(record)},
        )
    raw_path = (project_dir / raw_relative_path).resolve(strict=True)
    try:
        raw_path.relative_to(project_dir.resolve(strict=True))
    except ValueError:
        _fail(
            "AD4ZN_ACCEPTANCE_RAW_OUTPUT_OUTSIDE_PROJECT",
            "The preserved raw Vina output resolves outside the project.",
            details={"path": str(raw_path)},
        )
    raw_payload = _regular_file(raw_path, "preserved raw Vina output").read_bytes()
    raw_sha = _sha256_bytes(raw_payload)
    invalid_non_nul = [
        index
        for index, value in enumerate(raw_payload)
        if value != 0 and value < 32 and value not in {9, 10, 13}
    ]
    artifact_sha256 = metadata.get("artifact_sha256")
    recorded_artifact_sha = (
        str(artifact_sha256.get("out_vina_raw") or "")
        if isinstance(artifact_sha256, Mapping)
        else ""
    )
    if (
        raw_payload.count(b"\x00") != detected
        or invalid_non_nul
        or str(record.get("source_sha256") or "") != raw_sha
        or int(record.get("source_size_bytes") or 0) != len(raw_payload)
        or recorded_artifact_sha != raw_sha
        or raw_sha == output_sha
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_RAW_OUTPUT_IDENTITY_MISMATCH",
            "Preserved raw Vina bytes are not bound to the normalized output.",
            details={
                "expected_nul_count": detected,
                "actual_nul_count": raw_payload.count(b"\x00"),
                "invalid_non_nul_control_count": len(invalid_non_nul),
                "raw_sha256": raw_sha,
                "record_source_sha256": record.get("source_sha256"),
                "artifact_sha256": recorded_artifact_sha,
                "normalized_sha256": output_sha,
            },
        )
    return {
        "status": status,
        "method": method,
        "changed": True,
        "raw_output_file": raw_relative,
        "raw_size_bytes": len(raw_payload),
        "raw_sha256": raw_sha,
        "normalized_sha256": output_sha,
        "recognized_padding_blocks": blocks,
        "recognized_padding_lengths": [padding_length] * blocks,
        "nul_bytes_detected": detected,
        "nul_bytes_removed": removed,
    }


def _distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return math.sqrt(
        sum(
            (float(left_item) - float(right_item)) ** 2
            for left_item, right_item in zip(left, right, strict=True)
        )
    )


def _coordinate_oracle(
    site_expected: Mapping[str, Any],
    key: str,
) -> tuple[float, float, float]:
    value = site_expected.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(isinstance(item, bool) for item in value)
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            f"Manifest expected.site.{key} must contain three coordinates.",
        )
    try:
        coordinate = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            f"Manifest expected.site.{key} contains an invalid coordinate.",
        )
    if not all(math.isfinite(item) for item in coordinate):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            f"Manifest expected.site.{key} contains a non-finite coordinate.",
        )
    return coordinate


def _validate_site_coordinate_oracle(
    generated_zn: Sequence[float],
    official_zn: Sequence[float],
    generated_tz: Sequence[float],
    official_tz: Sequence[float],
    site_expected: Mapping[str, Any],
) -> dict[str, Any]:
    expected_zn = _coordinate_oracle(site_expected, "zinc_coordinate")
    expected_tz = _coordinate_oracle(site_expected, "tz_coordinate")
    try:
        coordinate_tolerance = float(
            site_expected.get("coordinate_tolerance_angstrom")
        )
        expected_tz_distance = float(
            site_expected.get("tz_distance_angstrom")
        )
        tz_distance_tolerance = float(
            site_expected.get("tz_distance_tolerance_angstrom")
        )
    except (TypeError, ValueError):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.site coordinate tolerances are invalid.",
        )
    if (
        not all(
            math.isfinite(value)
            for value in (
                coordinate_tolerance,
                expected_tz_distance,
                tz_distance_tolerance,
            )
        )
        or coordinate_tolerance <= 0
        or expected_tz_distance <= 0
        or tz_distance_tolerance <= 0
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.site coordinate tolerances must be positive.",
        )

    observed = {
        "generated_zn": _distance(generated_zn, expected_zn),
        "official_zn": _distance(official_zn, expected_zn),
        "generated_tz": _distance(generated_tz, expected_tz),
        "official_tz": _distance(official_tz, expected_tz),
    }
    expected_pair_distance = _distance(expected_zn, expected_tz)
    generated_pair_distance = _distance(generated_zn, generated_tz)
    official_pair_distance = _distance(official_zn, official_tz)
    if (
        any(delta > coordinate_tolerance for delta in observed.values())
        or abs(expected_pair_distance - expected_tz_distance)
        > tz_distance_tolerance
        or abs(generated_pair_distance - expected_tz_distance)
        > tz_distance_tolerance
        or abs(official_pair_distance - expected_tz_distance)
        > tz_distance_tolerance
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_SITE_COORDINATE_ORACLE_MISMATCH",
            (
                "Generated and official ZN/TZ coordinates do not both match "
                "the independent manifest oracle."
            ),
            details={
                "expected_zn": list(expected_zn),
                "expected_tz": list(expected_tz),
                "coordinate_tolerance_angstrom": coordinate_tolerance,
                "coordinate_deltas_angstrom": observed,
                "expected_tz_distance_angstrom": expected_tz_distance,
                "manifest_oracle_distance_angstrom": expected_pair_distance,
                "generated_distance_angstrom": generated_pair_distance,
                "official_distance_angstrom": official_pair_distance,
                "tz_distance_tolerance_angstrom": tz_distance_tolerance,
            },
        )
    return {
        "expected_zn": list(expected_zn),
        "expected_tz": list(expected_tz),
        "coordinate_tolerance_angstrom": coordinate_tolerance,
        "coordinate_deltas_angstrom": observed,
        "expected_tz_distance_angstrom": expected_tz_distance,
        "manifest_oracle_distance_angstrom": expected_pair_distance,
        "generated_distance_angstrom": generated_pair_distance,
        "official_distance_angstrom": official_pair_distance,
    }


def _validate_score_oracle(
    official_output: Path,
    parsed_affinities: Sequence[float],
    recorded_best: float,
    vina_expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the pinned official scores and DockStart's parsed minimum."""

    try:
        official_lines = official_output.read_text(
            encoding="utf-8",
            errors="strict",
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_OFFICIAL_OUTPUT_INVALID",
            "The pinned official output cannot be read as UTF-8 text.",
            details={"path": str(official_output), "error": str(exc)},
        )

    official_scores: list[float] = []
    for line in official_lines:
        if line.startswith("REMARK VINA RESULT:"):
            try:
                official_scores.append(float(line.split()[3]))
            except (IndexError, ValueError):
                _fail(
                    "AD4ZN_ACCEPTANCE_OFFICIAL_OUTPUT_INVALID",
                    "The pinned official output contains an invalid affinity.",
                    details={"line": line},
                )
    if not official_scores:
        _fail(
            "AD4ZN_ACCEPTANCE_OFFICIAL_OUTPUT_INVALID",
            "The pinned official output contains no Vina affinities.",
        )

    try:
        parsed_scores = [float(value) for value in parsed_affinities]
        observed_best = float(recorded_best)
        expected_modes = int(vina_expected.get("num_modes") or 0)
        official_reference = float(
            vina_expected.get("official_reference_best_affinity")
        )
    except (TypeError, ValueError):
        _fail(
            "AD4ZN_ACCEPTANCE_SCORE_ORACLE_INCONSISTENT",
            "The expected or parsed score oracle contains an invalid value.",
        )
    if (
        not parsed_scores
        or not all(math.isfinite(value) for value in parsed_scores)
        or not math.isfinite(observed_best)
        or len(official_scores) != expected_modes
        or not math.isclose(
            min(official_scores),
            official_reference,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        or not math.isclose(
            min(parsed_scores),
            observed_best,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_SCORE_ORACLE_INCONSISTENT",
            "The official or parsed score oracle is internally inconsistent.",
            details={
                "official_mode_count": len(official_scores),
                "expected_mode_count": expected_modes,
                "official_best": min(official_scores),
                "expected_official_best": official_reference,
                "parsed_best": min(parsed_scores) if parsed_scores else None,
                "recorded_best": observed_best,
            },
        )

    return {
        "official_mode_count": len(official_scores),
        "official_best": min(official_scores),
        "expected_official_best": official_reference,
        "parsed_mode_count": len(parsed_scores),
        "parsed_best": min(parsed_scores),
        "recorded_best": observed_best,
    }


def _prepare_project(
    temporary_root: Path,
    files: Mapping[str, Path],
    expected: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    created = _require_ok(
        create_project("ad4zn_1s63_acceptance", str(temporary_root)),
        "AD4ZN_ACCEPTANCE_PROJECT_CREATE_FAILED",
        "DockStart could not create the temporary acceptance project.",
    )
    project_dir = Path(str(created["project_dir"])).resolve(strict=True)
    try:
        project_dir.relative_to(temporary_root.resolve(strict=True))
    except ValueError:
        _fail(
            "AD4ZN_ACCEPTANCE_PROJECT_OUTSIDE_TEMP",
            "DockStart created the project outside the temporary directory.",
            details={"project_dir": str(project_dir)},
        )

    _require_ok(
        import_receptor_pdbqt(str(project_dir), str(files["receptor_input"])),
        "AD4ZN_ACCEPTANCE_RECEPTOR_IMPORT_FAILED",
        "DockStart could not import the official 1S63 receptor PDBQT.",
    )
    _require_ok(
        import_ligand_pdbqt(str(project_dir), str(files["ligand_input"])),
        "AD4ZN_ACCEPTANCE_LIGAND_IMPORT_FAILED",
        "DockStart could not import the official 1S63 ligand PDBQT.",
    )

    grid = expected.get("grid")
    vina_expected = expected.get("vina")
    if not isinstance(grid, Mapping) or not isinstance(vina_expected, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.grid or expected.vina section is invalid.",
        )
    center = grid.get("center")
    points = grid.get("grid_points")
    spacing = float(grid.get("spacing") or 0.0)
    if (
        not isinstance(center, list)
        or len(center) != 3
        or not isinstance(points, list)
        or len(points) != 3
        or spacing <= 0
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest grid geometry is invalid.",
        )
    _require_ok(
        update_box_params(
            str(project_dir),
            {
                "center_x": center[0],
                "center_y": center[1],
                "center_z": center[2],
                "size_x": float(points[0]) * spacing,
                "size_y": float(points[1]) * spacing,
                "size_z": float(points[2]) * spacing,
            },
        ),
        "AD4ZN_ACCEPTANCE_BOX_UPDATE_FAILED",
        "DockStart could not save the pinned 1S63 grid box.",
    )
    _require_ok(
        update_vina_params(
            str(project_dir),
            {
                "exhaustiveness": int(vina_expected["exhaustiveness"]),
                "num_modes": int(vina_expected["num_modes"]),
                "energy_range": int(vina_expected["energy_range"]),
                "cpu": int(vina_expected["cpu"]),
                "seed": int(vina_expected["seed"]),
            },
        ),
        "AD4ZN_ACCEPTANCE_VINA_PARAMS_FAILED",
        "DockStart could not save the pinned Vina parameters.",
    )
    _require_ok(
        set_scoring_protocol(str(project_dir), "ad4zn_beta"),
        "AD4ZN_ACCEPTANCE_PROTOCOL_ACTIVATION_FAILED",
        "DockStart could not activate the AD4Zn beta protocol.",
    )
    return project_dir, {
        "project_dir_policy": "temporary_directory_only",
        "project_name": "ad4zn_1s63_acceptance",
        "center": list(center),
        "grid_points": list(points),
        "spacing": spacing,
        "vina": {
            key: vina_expected[key]
            for key in (
                "scoring",
                "exhaustiveness",
                "num_modes",
                "energy_range",
                "cpu",
                "seed",
            )
        },
    }


def _prepare_ad4zn(
    project_dir: Path,
    files: Mapping[str, Path],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    site_expected = expected.get("site")
    if not isinstance(site_expected, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.site section is invalid.",
        )
    status = _require_ok(
        get_ad4zn_status(str(project_dir)),
        "AD4ZN_ACCEPTANCE_SITE_ANALYSIS_FAILED",
        "DockStart could not analyze the 1S63 zinc site.",
    )
    sites = status.get("sites")
    expected_count = int(site_expected.get("site_count") or 0)
    if not isinstance(sites, list) or len(sites) != expected_count:
        _fail(
            "AD4ZN_ACCEPTANCE_SITE_COUNT_MISMATCH",
            "DockStart did not identify the pinned number of zinc sites.",
            details={"expected": expected_count, "actual": len(sites or [])},
        )
    selected_id = str(site_expected.get("selected_site_id") or "")
    selected = next(
        (
            item
            for item in sites
            if isinstance(item, Mapping) and item.get("site_id") == selected_id
        ),
        None,
    )
    if (
        selected is None
        or selected.get("can_generate") is not True
        or int(selected.get("coordination_number") or 0)
        != int(site_expected.get("coordination_number") or 0)
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_SITE_GEOMETRY_MISMATCH",
            "The detected 1S63 zinc site is outside the pinned AD4Zn boundary.",
            details={"expected": dict(site_expected), "actual": dict(selected or {})},
        )

    _require_ok(
        save_review(
            str(project_dir),
            {
                "selected_site_id": selected_id,
                "confirmations": {
                    key: True for key in REQUIRED_CONFIRMATIONS
                },
            },
        ),
        "AD4ZN_ACCEPTANCE_REVIEW_FAILED",
        "DockStart could not bind the seven confirmations to the 1S63 receptor.",
    )
    prepared = _require_ok(
        prepare_ad4zn_receptor(str(project_dir), {}),
        "AD4ZN_ACCEPTANCE_TZ_PREPARATION_FAILED",
        "DockStart could not generate the 1S63 TZ receptor.",
    )
    recorded = _require_ok(
        record_parameter_file(str(project_dir), files["parameter_file"]),
        "AD4ZN_ACCEPTANCE_PARAMETER_RECORD_FAILED",
        "DockStart rejected the pinned repository-level AD4Zn.dat.",
    )
    if not recorded.get("preparation_ready"):
        _fail(
            "AD4ZN_ACCEPTANCE_PREPARATION_NOT_READY",
            "AD4Zn preparation did not reach the project run gate.",
            details={"issues": recorded.get("issues")},
        )
    parameter = recorded.get("parameter_file")
    if (
        not isinstance(parameter, Mapping)
        or parameter.get("valid") is not True
        or parameter.get("matches_reference_sha256") is not True
        or parameter.get("license_id") != "GPL-2.0-or-later"
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_PARAMETER_IDENTITY_MISMATCH",
            "The recorded AD4Zn.dat identity or license boundary is invalid.",
            details={"parameter_file": dict(parameter or {})},
        )

    receptor_record = prepared.get("prepared_receptor")
    if not isinstance(receptor_record, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_TZ_RECORD_MISSING",
            "DockStart did not return a TZ receptor record.",
        )
    generated_path = project_dir / str(receptor_record.get("relative_path") or "")
    _regular_file(generated_path, "DockStart TZ receptor")
    generated_tz = _pdbqt_type_coordinates(generated_path, "TZ")
    official_tz = _pdbqt_type_coordinates(files["official_tz_receptor"], "TZ")
    generated_zn = _pdbqt_type_coordinates(generated_path, "ZN")
    official_zn = _pdbqt_type_coordinates(files["official_tz_receptor"], "ZN")
    if not all(len(items) == 1 for items in (generated_tz, official_tz, generated_zn, official_zn)):
        _fail(
            "AD4ZN_ACCEPTANCE_TZ_ATOM_COUNT_MISMATCH",
            "Generated and official receptors must each contain one ZN and one TZ.",
            details={
                "generated_zn": len(generated_zn),
                "generated_tz": len(generated_tz),
                "official_zn": len(official_zn),
                "official_tz": len(official_tz),
            },
        )
    coordinate_oracle = _validate_site_coordinate_oracle(
        generated_zn[0],
        official_zn[0],
        generated_tz[0],
        official_tz[0],
        site_expected,
    )
    coordinate_tolerance = float(
        site_expected.get("coordinate_tolerance_angstrom") or 0.0
    )
    zinc_delta = _distance(generated_zn[0], official_zn[0])
    tz_delta = _distance(generated_tz[0], official_tz[0])
    tz_distance = _distance(generated_zn[0], generated_tz[0])
    expected_tz_distance = float(site_expected.get("tz_distance_angstrom") or 0.0)
    tz_distance_tolerance = float(
        site_expected.get("tz_distance_tolerance_angstrom") or 0.0
    )
    if (
        zinc_delta > coordinate_tolerance
        or tz_delta > coordinate_tolerance
        or abs(tz_distance - expected_tz_distance) > tz_distance_tolerance
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_TZ_COORDINATE_MISMATCH",
            "DockStart's ZN/TZ geometry does not reproduce the v1.2.7 oracle.",
            details={
                "generated_zn": generated_zn[0],
                "official_zn": official_zn[0],
                "generated_tz": generated_tz[0],
                "official_tz": official_tz[0],
                "zinc_delta_angstrom": zinc_delta,
                "tz_delta_angstrom": tz_delta,
                "tz_distance_angstrom": tz_distance,
            },
        )
    return {
        "site_id": selected_id,
        "coordination_number": selected["coordination_number"],
        "confirmation_count": len(REQUIRED_CONFIRMATIONS),
        "algorithm_name": receptor_record.get("algorithm_name"),
        "algorithm_version": receptor_record.get("algorithm_version"),
        "generated_receptor_sha256": receptor_record.get("sha256"),
        "generated_zn": list(generated_zn[0]),
        "generated_tz": list(generated_tz[0]),
        "official_zn": list(official_zn[0]),
        "official_tz": list(official_tz[0]),
        "zinc_delta_angstrom": zinc_delta,
        "tz_delta_angstrom": tz_delta,
        "tz_distance_angstrom": tz_distance,
        "manifest_coordinate_oracle": coordinate_oracle,
        "parameter_canonical_sha256": parameter.get("canonical_sha256"),
        "parameter_license_id": parameter.get("license_id"),
    }


def _xyz_mapping(
    value: Any,
    *,
    label: str,
    error_code: str,
) -> dict[str, float]:
    if not isinstance(value, Mapping):
        _fail(error_code, f"{label} must contain x/y/z values.")
    try:
        parsed = {axis: float(value[axis]) for axis in ("x", "y", "z")}
    except (KeyError, TypeError, ValueError):
        _fail(error_code, f"{label} must contain finite x/y/z values.")
    if not all(math.isfinite(item) for item in parsed.values()):
        _fail(error_code, f"{label} must contain finite x/y/z values.")
    return parsed


def _bounds_for(center: Mapping[str, float], size: Mapping[str, float]) -> dict[str, dict[str, float]]:
    return {
        "min": {
            axis: float(center[axis]) - float(size[axis]) / 2.0
            for axis in ("x", "y", "z")
        },
        "max": {
            axis: float(center[axis]) + float(size[axis]) / 2.0
            for axis in ("x", "y", "z")
        },
    }


def _mappings_close(
    actual: Mapping[str, float],
    expected: Mapping[str, float],
    *,
    tolerance: float = 1e-9,
) -> bool:
    return all(
        math.isclose(
            float(actual[axis]),
            float(expected[axis]),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
        for axis in ("x", "y", "z")
    )


def _validate_ad4zn_box_coverage(
    maps_manifest: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    grid_expected = expected.get("grid")
    site_expected = expected.get("site")
    if not isinstance(grid_expected, Mapping) or not isinstance(
        site_expected,
        Mapping,
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.grid or expected.site section is invalid.",
        )
    center_values = grid_expected.get("center")
    point_values = grid_expected.get("grid_points")
    try:
        spacing = float(grid_expected.get("spacing"))
        center = {
            axis: float(value)
            for axis, value in zip(
                ("x", "y", "z"),
                center_values,
                strict=True,
            )
        }
        points = {
            axis: int(value)
            for axis, value in zip(
                ("x", "y", "z"),
                point_values,
                strict=True,
            )
        }
    except (TypeError, ValueError):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.grid geometry is invalid.",
        )
    if (
        not isinstance(center_values, list)
        or len(center_values) != 3
        or not isinstance(point_values, list)
        or len(point_values) != 3
        or not all(math.isfinite(value) for value in center.values())
        or not math.isfinite(spacing)
        or spacing <= 0
        or any(value <= 0 for value in points.values())
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.grid geometry is invalid.",
        )
    requested_size = {
        axis: float(points[axis]) * spacing
        for axis in ("x", "y", "z")
    }
    effective_size = dict(requested_size)

    grid = maps_manifest.get("grid")
    ad4zn = maps_manifest.get("ad4zn")
    if not isinstance(grid, Mapping) or not isinstance(ad4zn, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "AD4Zn maps manifest lacks grid or AD4Zn evidence.",
        )
    requested_box = grid.get("requested_box")
    coverage = ad4zn.get("box_coverage")
    coverage_sha = str(ad4zn.get("box_coverage_sha256") or "").lower()
    if not isinstance(requested_box, Mapping) or not isinstance(
        coverage,
        Mapping,
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "AD4Zn maps manifest lacks requested Box or coverage evidence.",
        )
    calculated_sha = _canonical_json_sha256(coverage)
    if (
        not SHA256_PATTERN.fullmatch(coverage_sha)
        or coverage_sha != calculated_sha
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_SHA256_MISMATCH",
            "AD4Zn Box coverage is not bound to its canonical SHA256.",
            details={
                "recorded": coverage_sha,
                "calculated": calculated_sha,
            },
        )

    grid_center = _xyz_mapping(
        grid.get("center"),
        label="maps manifest grid.center",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    requested_center = _xyz_mapping(
        requested_box.get("center"),
        label="maps manifest grid.requested_box.center",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    requested_box_size = _xyz_mapping(
        requested_box.get("size"),
        label="maps manifest grid.requested_box.size",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    coverage_center = _xyz_mapping(
        coverage.get("box_center_angstrom"),
        label="AD4Zn coverage Box center",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    coverage_requested_size = _xyz_mapping(
        coverage.get("requested_box_size_angstrom"),
        label="AD4Zn coverage requested Box size",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    coverage_effective_size = _xyz_mapping(
        coverage.get("effective_grid_size_angstrom"),
        label="AD4Zn coverage effective grid size",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    coverage_points = _xyz_mapping(
        coverage.get("effective_grid_axis_intervals"),
        label="AD4Zn coverage effective grid intervals",
        error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
    )
    try:
        coverage_spacing = float(
            coverage.get("effective_grid_spacing_angstrom")
        )
    except (TypeError, ValueError):
        coverage_spacing = math.nan

    numeric_contract_ok = (
        _mappings_close(grid_center, center)
        and _mappings_close(requested_center, center)
        and _mappings_close(coverage_center, center)
        and _mappings_close(requested_box_size, requested_size)
        and _mappings_close(coverage_requested_size, requested_size)
        and _mappings_close(coverage_effective_size, effective_size)
        and _mappings_close(
            coverage_points,
            {axis: float(points[axis]) for axis in ("x", "y", "z")},
        )
        and math.isclose(
            coverage_spacing,
            spacing,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    )

    requested_bounds = coverage.get("requested_box_bounds_angstrom")
    effective_bounds = coverage.get("effective_grid_bounds_angstrom")
    parsed_requested_bounds: dict[str, dict[str, float]] = {}
    parsed_effective_bounds: dict[str, dict[str, float]] = {}
    for label, raw_bounds, target in (
        (
            "requested Box bounds",
            requested_bounds,
            parsed_requested_bounds,
        ),
        (
            "effective grid bounds",
            effective_bounds,
            parsed_effective_bounds,
        ),
    ):
        if not isinstance(raw_bounds, Mapping):
            _fail(
                "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
                f"AD4Zn coverage {label} is missing.",
            )
        for boundary in ("min", "max"):
            target[boundary] = _xyz_mapping(
                raw_bounds.get(boundary),
                label=f"AD4Zn coverage {label}.{boundary}",
                error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            )
    expected_requested_bounds = _bounds_for(center, requested_size)
    expected_effective_bounds = _bounds_for(center, effective_size)
    bounds_ok = all(
        _mappings_close(
            parsed_requested_bounds[boundary],
            expected_requested_bounds[boundary],
        )
        and _mappings_close(
            parsed_effective_bounds[boundary],
            expected_effective_bounds[boundary],
        )
        for boundary in ("min", "max")
    )

    markers = coverage.get("markers")
    if not isinstance(markers, Mapping) or set(markers) != {"ZN", "TZ"}:
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "AD4Zn coverage must contain exactly one ZN and one TZ marker.",
        )
    coordinate_tolerance = float(
        site_expected.get("coordinate_tolerance_angstrom") or 0.0
    )
    expected_coordinates = {
        "ZN": _coordinate_oracle(site_expected, "zinc_coordinate"),
        "TZ": _coordinate_oracle(site_expected, "tz_coordinate"),
    }
    marker_evidence: dict[str, Any] = {}
    for marker_type in ("ZN", "TZ"):
        marker = markers.get(marker_type)
        if not isinstance(marker, Mapping):
            _fail(
                "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
                f"AD4Zn coverage marker {marker_type} is invalid.",
            )
        coordinate = _xyz_mapping(
            marker.get("coordinate_angstrom"),
            label=f"AD4Zn coverage marker {marker_type}",
            error_code="AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
        )
        coordinate_sequence = tuple(
            coordinate[axis] for axis in ("x", "y", "z")
        )
        delta = _distance(
            coordinate_sequence,
            expected_coordinates[marker_type],
        )
        if (
            delta > coordinate_tolerance
            or marker.get("inside_requested_box") is not True
            or marker.get("inside_effective_grid") is not True
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_BOX_COVERAGE_FAILED",
                (
                    f"AD4Zn {marker_type} does not match the 1S63 coordinate "
                    "oracle or is outside the requested/effective grid."
                ),
                details={
                    "marker": dict(marker),
                    "expected_coordinate": list(
                        expected_coordinates[marker_type]
                    ),
                    "delta_angstrom": delta,
                    "coordinate_tolerance_angstrom": coordinate_tolerance,
                },
            )
        marker_evidence[marker_type] = {
            "coordinate_angstrom": dict(coordinate),
            "oracle_delta_angstrom": delta,
            "inside_requested_box": True,
            "inside_effective_grid": True,
        }

    if (
        coverage.get("method") != AD4ZN_BOX_COVERAGE_METHOD
        or coverage.get("interval_semantics") != "closed"
        or coverage.get("all_zn_tz_inside_requested_box") is not True
        or coverage.get("all_zn_tz_inside_effective_grid") is not True
        or not numeric_contract_ok
        or not bounds_ok
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "AD4Zn Box coverage does not reproduce the pinned 1S63 grid.",
            details={
                "method": coverage.get("method"),
                "interval_semantics": coverage.get("interval_semantics"),
                "all_inside_requested": coverage.get(
                    "all_zn_tz_inside_requested_box"
                ),
                "all_inside_effective": coverage.get(
                    "all_zn_tz_inside_effective_grid"
                ),
                "numeric_contract_ok": numeric_contract_ok,
                "bounds_ok": bounds_ok,
            },
        )
    return {
        "box_coverage_sha256": coverage_sha,
        "method": coverage.get("method"),
        "requested_box_bounds_angstrom": parsed_requested_bounds,
        "effective_grid_bounds_angstrom": parsed_effective_bounds,
        "markers": marker_evidence,
        "all_zn_tz_inside_requested_box": True,
        "all_zn_tz_inside_effective_grid": True,
    }


def _gpf_profile_lines(
    grid: Mapping[str, Any],
    points: Sequence[int],
) -> list[str]:
    center = grid.get("center")
    if not isinstance(center, list) or len(center) != 3:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest grid center must contain three values.",
        )
    try:
        formatted_center = " ".join(f"{float(value):g}" for value in center)
        formatted_spacing = f"{float(grid['spacing']):g}"
    except (KeyError, TypeError, ValueError):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest grid center or spacing is invalid.",
        )
    return [
        "parameter_file inputs/AD4Zn.dat",
        f"npts {int(points[0])} {int(points[1])} {int(points[2])}",
        f"gridcenter {formatted_center}",
        f"spacing {formatted_spacing}",
        "dielectric -0.1465",
        *AD4ZN_NBP_R_EPS,
    ]


def _report_box_coverage_rows(
    box_coverage: Mapping[str, Any],
) -> list[str]:
    markers = box_coverage.get("markers")
    if not isinstance(markers, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "Verified AD4Zn Box coverage has no marker evidence.",
        )
    zn = markers.get("ZN")
    tz = markers.get("TZ")
    if not isinstance(zn, Mapping) or not isinstance(tz, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_BOX_COVERAGE_INVALID",
            "Verified AD4Zn Box coverage lacks ZN or TZ evidence.",
        )
    return [
        (
            "| ZN 坐标 | "
            + json.dumps(
                zn.get("coordinate_angstrom"),
                ensure_ascii=False,
            )
            + " |"
        ),
        (
            "| TZ 坐标 | "
            + json.dumps(
                tz.get("coordinate_angstrom"),
                ensure_ascii=False,
            )
            + " |"
        ),
        (
            "| 请求 Box 边界 | "
            + json.dumps(
                box_coverage.get("requested_box_bounds_angstrom"),
                ensure_ascii=False,
                sort_keys=True,
            )
            + " |"
        ),
        (
            "| 实际 AutoGrid 网格边界 | "
            + json.dumps(
                box_coverage.get("effective_grid_bounds_angstrom"),
                ensure_ascii=False,
                sort_keys=True,
            )
            + " |"
        ),
        "| ZN/TZ 位于请求 Box | 通过 |",
        "| ZN/TZ 位于实际网格 | 通过 |",
    ]


def _validate_report_box_coverage(
    report_text: str,
    box_coverage: Mapping[str, Any],
) -> dict[str, Any]:
    required_rows = _report_box_coverage_rows(box_coverage)
    missing = [row for row in required_rows if row not in report_text]
    if missing:
        _fail(
            "AD4ZN_ACCEPTANCE_REPORT_BOX_COVERAGE_MISSING",
            "The AD4Zn report omits frozen ZN/TZ Box coverage evidence.",
            details={"missing": missing},
        )
    return {
        "required_row_count": len(required_rows),
        "all_rows_present": True,
    }


def _generate_ad4zn_maps(
    project_dir: Path,
    autogrid_detection: Any,
    files: Mapping[str, Path],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    grid = expected.get("grid")
    if not isinstance(grid, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.grid section is invalid.",
        )
    points = grid.get("grid_points")
    if not isinstance(points, list) or len(points) != 3:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest grid_points must contain three values.",
        )
    options = {
        "spacing": float(grid["spacing"]),
        "grid_points": {
            "x": int(points[0]),
            "y": int(points[1]),
            "z": int(points[2]),
        },
    }
    with patch(
        "dockstart_core.autogrid.autogrid_adapter.detect",
        return_value=autogrid_detection,
    ):
        generated = _require_ok(
            generate_maps(str(project_dir), options),
            "AD4ZN_ACCEPTANCE_MAP_GENERATION_FAILED",
            "DockStart could not generate the 1S63 AD4Zn maps.",
        )
        status = _require_ok(
            validate_active_maps(str(project_dir)),
            "AD4ZN_ACCEPTANCE_MAP_VALIDATION_FAILED",
            "DockStart rejected its generated AD4Zn maps.",
        )
    if not status.get("ready"):
        _fail(
            "AD4ZN_ACCEPTANCE_MAPS_NOT_READY",
            "Generated AD4Zn maps did not reach the run gate.",
            details={"status": dict(status)},
        )
    manifest = generated.get("manifest")
    if not isinstance(manifest, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MAP_MANIFEST_MISSING",
            "Map generation returned no manifest.",
        )
    log_summary = (
        manifest.get("autogrid", {}).get("log_summary")
        if isinstance(manifest.get("autogrid"), Mapping)
        else None
    )
    if (
        manifest.get("protocol_id") != "ad4zn_beta"
        or manifest.get("status") != "ready"
        or not isinstance(log_summary, Mapping)
        or log_summary.get("successful_completion") is not True
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_MAP_MANIFEST_INVALID",
            "The AD4Zn map manifest lacks a successful AutoGrid completion.",
            details={"manifest": dict(manifest)},
        )
    box_coverage = _validate_ad4zn_box_coverage(manifest, expected)
    gpf_record = manifest.get("gpf")
    gpf_path = project_dir / str(
        gpf_record.get("relative_path") if isinstance(gpf_record, Mapping) else ""
    )
    _regular_file(gpf_path, "generated AD4Zn GPF")
    gpf_text = gpf_path.read_text(encoding="utf-8", errors="strict")
    required_lines = _gpf_profile_lines(grid, points)
    missing_lines = [line for line in required_lines if line not in gpf_text]
    if missing_lines:
        _fail(
            "AD4ZN_ACCEPTANCE_GPF_PROFILE_MISMATCH",
            "The generated GPF does not contain the pinned AD4Zn profile.",
            details={"missing_lines": missing_lines, "gpf": str(gpf_path)},
        )
    maps = manifest.get("maps")
    map_files = maps.get("files") if isinstance(maps, Mapping) else None
    if not isinstance(map_files, list) or not map_files:
        _fail(
            "AD4ZN_ACCEPTANCE_MAP_FILES_MISSING",
            "The map manifest contains no generated affinity maps.",
        )
    map_equivalence = expected.get("map_numeric_equivalence")
    if not isinstance(map_equivalence, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.map_numeric_equivalence is invalid.",
        )
    map_types = map_equivalence.get("map_types")
    if not isinstance(map_types, list) or not map_types:
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest map_types must be a non-empty list.",
        )
    point_count = int(map_equivalence.get("point_count_per_map") or 0)
    tolerance = float(map_equivalence.get("absolute_tolerance") or 0.0)
    generated_by_name = {
        str(item.get("name") or ""): project_dir
        / str(item.get("relative_path") or "")
        for item in map_files
        if isinstance(item, Mapping)
    }
    numeric_comparison: dict[str, Any] = {}
    for raw_map_type in map_types:
        map_type = str(raw_map_type)
        generated_name = f"receptor.{map_type}.map"
        generated_map_path = generated_by_name.get(generated_name)
        official_map_path = files.get(f"official_map_{map_type}")
        if generated_map_path is None or official_map_path is None:
            _fail(
                "AD4ZN_ACCEPTANCE_MAP_COMPARISON_INPUT_MISSING",
                f"Map {map_type} is missing from the generated or official set.",
                details={
                    "generated_name": generated_name,
                    "official_key": f"official_map_{map_type}",
                },
            )
        try:
            generated_map = parse_autogrid_map(generated_map_path)
            official_map = parse_autogrid_map(official_map_path)
        except HydratedMapError as exc:
            _fail(
                "AD4ZN_ACCEPTANCE_MAP_PARSE_FAILED",
                f"Map {map_type} could not be parsed for numeric comparison.",
                details={"code": exc.code, "message": exc.message},
            )
        if (
            generated_map.geometry != official_map.geometry
            or len(generated_map.values) != point_count
            or len(official_map.values) != point_count
        ):
            _fail(
                "AD4ZN_ACCEPTANCE_MAP_GEOMETRY_MISMATCH",
                f"Map {map_type} geometry or point count differs from 1S63.",
                details={
                    "expected_point_count": point_count,
                    "generated": generated_map.geometry.to_dict(),
                    "generated_point_count": len(generated_map.values),
                    "official": official_map.geometry.to_dict(),
                    "official_point_count": len(official_map.values),
                },
            )
        maximum_error = 0.0
        differing_points = 0
        outside_tolerance = 0
        for generated_value, official_value in zip(
            generated_map.values,
            official_map.values,
            strict=True,
        ):
            error = abs(float(generated_value) - float(official_value))
            maximum_error = max(maximum_error, error)
            if error > 0:
                differing_points += 1
            if error > tolerance + 1e-12:
                outside_tolerance += 1
        if outside_tolerance:
            _fail(
                "AD4ZN_ACCEPTANCE_MAP_NUMERIC_MISMATCH",
                f"Map {map_type} differs numerically from the official solution.",
                details={
                    "point_count": point_count,
                    "absolute_tolerance": tolerance,
                    "maximum_absolute_error": maximum_error,
                    "outside_tolerance_count": outside_tolerance,
                },
            )
        numeric_comparison[map_type] = {
            "point_count": point_count,
            "absolute_tolerance": tolerance,
            "maximum_absolute_error": maximum_error,
            "differing_point_count": differing_points,
            "outside_tolerance_count": 0,
        }
    return {
        "map_set_id": generated.get("map_set_id"),
        "manifest_file": generated.get("manifest_file"),
        "protocol_id": manifest.get("protocol_id"),
        "autogrid_version": manifest.get("autogrid", {}).get("version"),
        "autogrid_sha256": manifest.get("autogrid", {}).get("sha256"),
        "successful_completion": log_summary.get("successful_completion"),
        "gpf_sha256": gpf_record.get("sha256"),
        "map_file_count": len(map_files),
        "ligand_atom_types": maps.get("ligand_atom_types"),
        "box_coverage": box_coverage,
        "numeric_comparison": numeric_comparison,
    }


def _run_and_report(
    project_dir: Path,
    vina_detection: Any,
    files: Mapping[str, Path],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    _require_ok(
        generate_vina_config(str(project_dir)),
        "AD4ZN_ACCEPTANCE_CONFIG_GENERATION_FAILED",
        "DockStart could not generate the AD4Zn Vina configuration.",
    )
    with patch(
        "dockstart_core.project.vina_adapter.detect",
        return_value=vina_detection,
    ):
        prepared = _require_ok(
            prepare_vina_run(str(project_dir)),
            "AD4ZN_ACCEPTANCE_RUN_PREPARATION_FAILED",
            "DockStart could not freeze the AD4Zn run.",
        )
        run_id = str(prepared.get("run_id") or "")
        executed = _require_ok(
            execute_prepared_vina_run(str(project_dir), run_id),
            "AD4ZN_ACCEPTANCE_VINA_RUN_FAILED",
            "DockStart could not complete the AD4Zn Vina run.",
        )
    metadata = executed.get("metadata")
    if not isinstance(metadata, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_RUN_METADATA_MISSING",
            "The completed run has no metadata record.",
        )
    docking_protocol = metadata.get("docking_protocol")
    snapshots = metadata.get("snapshots")
    ad4zn_snapshots = (
        snapshots.get("ad4zn") if isinstance(snapshots, Mapping) else None
    )
    if (
        metadata.get("status") != "finished"
        or metadata.get("scoring_protocol") != "ad4_maps"
        or metadata.get("scoring_function") != "ad4"
        or not isinstance(docking_protocol, Mapping)
        or docking_protocol.get("protocol_id") != "ad4zn_beta"
        or not isinstance(ad4zn_snapshots, Mapping)
        or set(ad4zn_snapshots)
        != {"original_receptor", "parameter_file", "gpf", "protocol_record"}
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_RUN_METADATA_INVALID",
            "The finished run did not preserve the complete AD4Zn evidence set.",
            details={
                "status": metadata.get("status"),
                "scoring_protocol": metadata.get("scoring_protocol"),
                "scoring_function": metadata.get("scoring_function"),
                "docking_protocol": dict(docking_protocol or {}),
                "ad4zn_snapshot_keys": sorted((ad4zn_snapshots or {}).keys()),
            },
        )

    ad4_maps_snapshot = (
        snapshots.get("ad4_maps")
        if isinstance(snapshots, Mapping)
        else None
    )
    frozen_manifest_record = (
        ad4_maps_snapshot.get("manifest")
        if isinstance(ad4_maps_snapshot, Mapping)
        else None
    )
    frozen_manifest_relative = str(
        frozen_manifest_record.get("relative_path") or ""
        if isinstance(frozen_manifest_record, Mapping)
        else ""
    )
    frozen_manifest_relative_path = Path(frozen_manifest_relative)
    if (
        not frozen_manifest_relative
        or frozen_manifest_relative_path.is_absolute()
        or ".." in frozen_manifest_relative_path.parts
    ):
        _fail(
            "AD4ZN_ACCEPTANCE_RUN_MAP_MANIFEST_MISSING",
            "The finished run does not identify a safe frozen maps manifest.",
        )
    frozen_manifest_path = (
        project_dir / frozen_manifest_relative_path
    ).resolve(strict=True)
    try:
        frozen_manifest_path.relative_to(project_dir.resolve(strict=True))
        frozen_manifest_payload = json.loads(
            _regular_file(
                frozen_manifest_path,
                "frozen AD4Zn maps manifest",
            ).read_text(encoding="utf-8", errors="strict")
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _fail(
            "AD4ZN_ACCEPTANCE_RUN_MAP_MANIFEST_INVALID",
            "The frozen AD4Zn maps manifest cannot be verified.",
            details={"path": str(frozen_manifest_path), "error": str(exc)},
        )
    if not isinstance(frozen_manifest_payload, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_RUN_MAP_MANIFEST_INVALID",
            "The frozen AD4Zn maps manifest must contain a JSON object.",
        )
    frozen_box_coverage = _validate_ad4zn_box_coverage(
        frozen_manifest_payload,
        expected,
    )

    output_path = project_dir / str(metadata.get("output_file") or "")
    output_identity = _validate_text_artifact(
        output_path,
        "AD4Zn Vina out.pdbqt",
    )
    output_normalization = _validate_output_normalization(
        project_dir,
        metadata,
        output_identity,
        expected,
    )
    analyzed = _require_ok(
        analyze_vina_run_results(str(project_dir), run_id),
        "AD4ZN_ACCEPTANCE_RESULT_ANALYSIS_FAILED",
        "DockStart could not parse the completed AD4Zn result.",
    )
    scores = analyzed.get("scores")
    vina_expected = expected.get("vina")
    if not isinstance(scores, list) or not isinstance(vina_expected, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_SCORES_INVALID",
            "The parsed score table or expected Vina record is invalid.",
        )
    minimum_modes = int(vina_expected.get("minimum_output_modes") or 0)
    if len(scores) < minimum_modes:
        _fail(
            "AD4ZN_ACCEPTANCE_OUTPUT_MODE_COUNT_LOW",
            "Vina returned fewer modes than the acceptance minimum.",
            details={"minimum": minimum_modes, "actual": len(scores)},
        )
    parsed_affinities: list[float] = []
    for row in scores:
        if not isinstance(row, Mapping):
            _fail(
                "AD4ZN_ACCEPTANCE_SCORES_INVALID",
                "The parsed score table contains a non-object row.",
            )
        try:
            affinity = float(row.get("affinity_kcal_mol"))
        except (TypeError, ValueError):
            _fail(
                "AD4ZN_ACCEPTANCE_SCORES_INVALID",
                "The parsed score table contains an invalid affinity.",
                details={"row": dict(row)},
            )
        if not math.isfinite(affinity):
            _fail(
                "AD4ZN_ACCEPTANCE_SCORES_INVALID",
                "The parsed score table contains a non-finite affinity.",
                details={"row": dict(row)},
            )
        parsed_affinities.append(affinity)
    best_affinity = float(analyzed.get("best_affinity"))
    lower = float(vina_expected.get("accepted_best_affinity_min"))
    upper = float(vina_expected.get("accepted_best_affinity_max"))
    if not math.isfinite(best_affinity) or not lower <= best_affinity <= upper:
        _fail(
            "AD4ZN_ACCEPTANCE_AFFINITY_OUTSIDE_REFERENCE",
            "The best AD4Zn affinity is outside the pinned 1S63 neighborhood.",
            details={
                "official_reference": vina_expected.get(
                    "official_reference_best_affinity"
                ),
                "minimum": lower,
                "maximum": upper,
                "actual": best_affinity,
            },
        )

    report = _require_ok(
        export_markdown_report(str(project_dir), run_id),
        "AD4ZN_ACCEPTANCE_REPORT_EXPORT_FAILED",
        "DockStart could not export the AD4Zn Markdown experiment record.",
    )
    report_file = project_dir / str(report.get("report_file") or "")
    _regular_file(report_file, "AD4Zn Markdown report")
    report_text = report_file.read_text(encoding="utf-8", errors="strict")
    report_expected = expected.get("report")
    required_substrings = (
        report_expected.get("required_substrings")
        if isinstance(report_expected, Mapping)
        else None
    )
    if not isinstance(required_substrings, list):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.report.required_substrings is invalid.",
        )
    missing_report_text = [
        str(item) for item in required_substrings if str(item) not in report_text
    ]
    if missing_report_text:
        _fail(
            "AD4ZN_ACCEPTANCE_REPORT_CONTENT_MISSING",
            "The AD4Zn report omits required protocol or disclaimer text.",
            details={"missing": missing_report_text},
        )
    report_coverage_evidence = _validate_report_box_coverage(
        report_text,
        frozen_box_coverage,
    )

    score_oracle = _validate_score_oracle(
        files["official_output"],
        parsed_affinities,
        best_affinity,
        vina_expected,
    )
    official_reference = score_oracle["expected_official_best"]

    return {
        "run_id": run_id,
        "status": metadata.get("status"),
        "protocol_id": docking_protocol.get("protocol_id"),
        "scoring_protocol": metadata.get("scoring_protocol"),
        "scoring_function": metadata.get("scoring_function"),
        "vina_version": metadata.get("vina_version"),
        "vina_sha256": metadata.get("vina_sha256"),
        "mode_count": len(scores),
        "best_affinity": best_affinity,
        "official_reference_best_affinity": official_reference,
        "output_file": metadata.get("output_file"),
        "output_identity": output_identity,
        "output_normalization": output_normalization,
        "scores_file": analyzed.get("scores_file"),
        "report_file": report.get("report_file"),
        "report_sha256": _sha256(report_file),
        "ad4zn_snapshot_keys": sorted(ad4zn_snapshots),
        "frozen_box_coverage": frozen_box_coverage,
        "report_box_coverage_rows_verified": report_coverage_evidence[
            "required_row_count"
        ],
    }


def verify_ad4zn_1s63(
    upstream_root: Path,
    autogrid_executable: Path,
    vina_executable: Path,
) -> dict[str, Any]:
    manifest = _load_manifest()
    expected = manifest.get("expected")
    if not isinstance(expected, Mapping):
        _fail(
            "AD4ZN_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected section is invalid.",
        )
    steps: dict[str, Any] = {}
    resolved_files: dict[str, Path] = {}
    autogrid_detection: Any = None
    vina_detection: Any = None
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="dockstart-ad4zn-1s63-",
    )
    temporary_root = Path(temporary_handle.name).resolve(strict=True)
    isolated_settings_path = temporary_root / "dockstart_settings.json"
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    result_payload: dict[str, Any] | None = None
    caught: AD4ZnAcceptanceError | None = None
    try:
        os.environ[SETTINGS_ENV_VAR] = str(isolated_settings_path)

        def verify_sources() -> Mapping[str, Any]:
            nonlocal resolved_files
            resolved_files, evidence = _verify_upstream(
                Path(upstream_root),
                manifest,
            )
            return evidence

        def verify_external_tools() -> Mapping[str, Any]:
            nonlocal autogrid_detection, vina_detection
            autogrid_detection, vina_detection, evidence = _verify_tools(
                Path(autogrid_executable),
                Path(vina_executable),
                manifest,
            )
            return evidence

        _execute_step(steps, "upstream_identity", verify_sources)
        _execute_step(steps, "external_tools", verify_external_tools)
        project_holder: dict[str, Path] = {}

        def prepare_project_step() -> Mapping[str, Any]:
            project_dir, evidence = _prepare_project(
                temporary_root,
                resolved_files,
                expected,
            )
            project_holder["path"] = project_dir
            return evidence

        _execute_step(steps, "project_setup", prepare_project_step)
        project_dir = project_holder["path"]
        _execute_step(
            steps,
            "ad4zn_preparation",
            lambda: _prepare_ad4zn(project_dir, resolved_files, expected),
        )
        _execute_step(
            steps,
            "autogrid_maps",
            lambda: _generate_ad4zn_maps(
                project_dir,
                autogrid_detection,
                resolved_files,
                expected,
            ),
        )
        _execute_step(
            steps,
            "vina_result_report",
            lambda: _run_and_report(
                project_dir,
                vina_detection,
                resolved_files,
                expected,
            ),
        )
        result_payload = {
            "ok": True,
            "fixture_id": str(manifest.get("fixture_id") or ""),
            "distribution": str(manifest.get("distribution") or ""),
            "steps": steps,
            "message": (
                "DockStart completed the external 1S63 AD4Zn project chain "
                "with new AutoGrid 4.2.7+ maps and Vina 1.2.7 results."
            ),
            "error": None,
        }
    except AD4ZnAcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve the JSON gate boundary.
        caught = AD4ZnAcceptanceError(
            "AD4ZN_ACCEPTANCE_UNEXPECTED_ERROR",
            "The project-level 1S63 AD4Zn verifier failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
        caught.steps = dict(steps)
    finally:
        settings_restore_error = ""
        try:
            if previous_settings_path is None:
                os.environ.pop(SETTINGS_ENV_VAR, None)
            else:
                os.environ[SETTINGS_ENV_VAR] = previous_settings_path
        except Exception as exc:  # noqa: BLE001 - report restoration failure.
            settings_restore_error = str(exc)
        settings_restored = (
            SETTINGS_ENV_VAR not in os.environ
            if previous_settings_path is None
            else os.environ.get(SETTINGS_ENV_VAR) == previous_settings_path
        )
        settings_evidence = {
            "variable": SETTINGS_ENV_VAR,
            "previously_set": previous_settings_path is not None,
            "isolated_path": str(isolated_settings_path),
            "restored": settings_restored,
            "error": settings_restore_error,
        }

        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001 - report cleanup failure.
            cleanup_error = str(exc)
        cleanup_evidence = {
            "path": str(temporary_root),
            "cleanup_called": True,
            "removed": not temporary_root.exists(),
            "error": cleanup_error,
        }

    lifecycle_evidence = {
        "temporary_cleanup": cleanup_evidence,
        "settings_environment": settings_evidence,
    }
    if caught is not None:
        caught.details = {
            **caught.details,
            **lifecycle_evidence,
        }
        if not caught.steps:
            caught.steps = dict(steps)
        raise caught
    if (
        result_payload is None
        or cleanup_error
        or cleanup_evidence["removed"] is not True
        or settings_restore_error
        or settings_evidence["restored"] is not True
    ):
        lifecycle_error = AD4ZnAcceptanceError(
            "AD4ZN_ACCEPTANCE_TEMPORARY_LIFECYCLE_FAILED",
            (
                "The temporary DockStart project could not be removed or "
                "the settings environment could not be restored."
            ),
            details=lifecycle_evidence,
        )
        lifecycle_error.steps = dict(steps)
        raise lifecycle_error
    result_payload.update(lifecycle_evidence)
    result_payload["temporary_directory_removed"] = True
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify DockStart's full external 1S63 AD4Zn project chain. "
            "The command never downloads or installs scientific assets."
        ),
    )
    parser.add_argument(
        "--upstream-root",
        type=Path,
        required=True,
        help="Local AutoDock Vina v1.2.7 checkout.",
    )
    parser.add_argument(
        "--autogrid",
        type=Path,
        required=True,
        help="User-supplied AutoGrid 4.2.7+ executable.",
    )
    parser.add_argument(
        "--vina",
        type=Path,
        required=True,
        help="User-supplied AutoDock Vina 1.2.7 executable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        result = verify_ad4zn_1s63(
            args.upstream_root,
            args.autogrid,
            args.vina,
        )
    except AD4ZnAcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "ad4zn_1s63_external",
                    "steps": exc.steps,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
