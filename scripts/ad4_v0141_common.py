"""Shared fail-closed evidence helpers for the v0.14.1 AD4 acceptance gates.

The helpers deliberately do not download fixtures or execute scientific tools.
Each verifier supplies its own workflow, while this module freezes the common
file, command, maps-manifest, and JSON failure contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
SOURCE_PROVENANCE_SCHEMA_VERSION = 1
SOURCE_TREE_ALGORITHM = "sha256_canonical_json_sorted_path_size_sha256_role_v1"
SOURCE_SESSION_ALGORITHM = "sha256_canonical_json_source_context_v1"
EVIDENCE_PAYLOAD_ALGORITHM = "sha256_canonical_json_without_top_level_provenance_v1"
IMPORT_ORIGIN_ALGORITHM = (
    "sha256_canonical_json_sorted_module_repository_path_size_sha256_v1"
)
GATE_EVIDENCE_SCHEMA_VERSION = 2
SOURCE_BOUND_GATE_VERIFIER_IDS = frozenset(
    {
        "dockstart_ad4_flexible_1fpu_v0141",
        "dockstart_ad4_multiple_ligands_5x72_v0141",
        "dockstart_ad4_serial_screening_v0141",
    }
)
BACKEND_VALIDATION_TREE_ALGORITHM = (
    "sha256_canonical_json_sorted_repository_path_size_sha256_v1"
)
BACKEND_VALIDATION_ROOTS = (
    ("backend/adapters", "**/*.py"),
    ("backend/dockstart_core", "**/*.py"),
)
BACKEND_IMPORT_ROOTS = (
    ("adapters", "backend/adapters"),
    ("dockstart_core", "backend/dockstart_core"),
)
VINA_1_2_7_CONTRACT = {
    "version": "1.2.7",
    "size_bytes": 1_233_920,
    "sha256": "e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5",
}


class AcceptanceError(RuntimeError):
    """Stable, JSON-serializable v0.14.1 acceptance failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


def fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise AcceptanceError(code, message, details=details)


def require(
    condition: bool,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    if not condition:
        fail(code, message, details=details)


def require_api(
    step: str,
    result: Mapping[str, Any],
    *,
    code: str = "AD4_ACCEPTANCE_API_STEP_FAILED",
) -> dict[str, Any]:
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        fail(
            code,
            f"DockStart public API step failed: {step}",
            details={"step": step, "result": dict(result or {})},
        )
    return dict(result)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_file_identity(
    repo_root: str | Path,
    path: str | Path,
    *,
    role: str,
) -> dict[str, Any]:
    """Return a repository-relative source identity without leaking local paths."""

    root = Path(repo_root).resolve(strict=True)
    supplied = Path(path)
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        fail(
            "AD4_ACCEPTANCE_SOURCE_FILE_INVALID",
            f"The {role} source file is missing or outside the repository.",
            details={"role": role, "name": supplied.name, "error_type": type(exc).__name__},
        )
    require(
        resolved.is_file(),
        "AD4_ACCEPTANCE_SOURCE_FILE_INVALID",
        f"The {role} source path is not a regular file.",
        details={"role": role, "path": relative},
    )
    return {
        "role": str(role),
        "path": relative,
        "size_bytes": int(resolved.stat().st_size),
        "sha256": sha256_file(resolved),
    }


def _git_output(repo_root: Path, arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        fail(
            "AD4_ACCEPTANCE_GIT_IDENTITY_UNAVAILABLE",
            "Git identity could not be read for the source-bound evidence.",
            details={"error_type": type(exc).__name__},
        )
    require(
        completed.returncode == 0,
        "AD4_ACCEPTANCE_GIT_IDENTITY_UNAVAILABLE",
        "Git identity could not be read for the source-bound evidence.",
        details={"returncode": int(completed.returncode)},
    )
    return completed.stdout


def git_worktree_identity(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=True)
    commit = _git_output(root, ["rev-parse", "HEAD"]).strip().lower()
    require(
        GIT_COMMIT_PATTERN.fullmatch(commit) is not None,
        "AD4_ACCEPTANCE_GIT_IDENTITY_INVALID",
        "Git returned an invalid commit identity.",
    )
    status = _git_output(
        root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    )
    return {
        "commit": commit,
        "dirty": bool(status.strip()),
    }


def backend_validation_tree(repo_root: str | Path) -> dict[str, Any]:
    """Hash the Python implementation exercised by the AD4 acceptance gates."""

    root = Path(repo_root).resolve(strict=True)
    identities: list[dict[str, Any]] = []
    roots: list[dict[str, str]] = []
    for relative_root, pattern in BACKEND_VALIDATION_ROOTS:
        source_root = (root / relative_root).resolve(strict=True)
        try:
            source_root.relative_to(root)
        except ValueError:
            fail(
                "AD4_ACCEPTANCE_BACKEND_TREE_INVALID",
                "A backend validation root escapes the repository.",
                details={"root": relative_root},
            )
        require(
            source_root.is_dir() and not source_root.is_symlink(),
            "AD4_ACCEPTANCE_BACKEND_TREE_INVALID",
            "A backend validation root is not a regular repository directory.",
            details={"root": relative_root},
        )
        matched = sorted(source_root.glob(pattern), key=lambda value: value.as_posix())
        matched = [
            path
            for path in matched
            if path.is_file() and not path.is_symlink()
        ]
        require(
            bool(matched),
            "AD4_ACCEPTANCE_BACKEND_TREE_EMPTY",
            "A backend validation root contains no Python source files.",
            details={"root": relative_root, "pattern": pattern},
        )
        roots.append({"path": relative_root, "pattern": pattern})
        for path in matched:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
            identities.append(
                {
                    "path": relative,
                    "size_bytes": int(resolved.stat().st_size),
                    "sha256": sha256_file(resolved),
                }
            )
    identities.sort(key=lambda value: str(value["path"]))
    require(
        len({str(value["path"]) for value in identities}) == len(identities),
        "AD4_ACCEPTANCE_BACKEND_TREE_DUPLICATE",
        "The backend validation tree contains a duplicate repository path.",
    )
    canonical_tree = json.dumps(
        identities,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return {
        "algorithm": BACKEND_VALIDATION_TREE_ALGORITHM,
        "roots": roots,
        "file_count": len(identities),
        "sha256": hashlib.sha256(canonical_tree).hexdigest(),
    }


def _repository_records(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if isinstance(value.get("repository_path"), str):
            yield value
        for child in value.values():
            yield from _repository_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _repository_records(child)


def fixture_repository_identities(
    repo_root: str | Path,
    manifest_paths: Sequence[str | Path],
) -> list[dict[str, Any]]:
    """Resolve every manifest-declared local fixture and enforce its contract."""

    root = Path(repo_root).resolve(strict=True)
    identities_by_path: dict[str, dict[str, Any]] = {}
    for manifest_path in manifest_paths:
        manifest_identity = repository_file_identity(
            root,
            manifest_path,
            role="fixture_manifest",
        )
        resolved_manifest = root / str(manifest_identity["path"])
        try:
            manifest_bytes = resolved_manifest.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            fail(
                "AD4_ACCEPTANCE_FIXTURE_MANIFEST_INVALID",
                "A fixture source manifest cannot be parsed for local-file binding.",
                details={
                    "manifest": manifest_identity["path"],
                    "error_type": type(exc).__name__,
                },
            )
        require(
            isinstance(manifest, Mapping),
            "AD4_ACCEPTANCE_FIXTURE_MANIFEST_INVALID",
            "A fixture source manifest root is not an object.",
            details={"manifest": manifest_identity["path"]},
        )
        for record in _repository_records(manifest):
            relative_path = str(record.get("repository_path") or "")
            expected_size = record.get("size_bytes")
            expected_sha256 = str(record.get("sha256") or "").lower()
            require(
                isinstance(expected_size, int)
                and not isinstance(expected_size, bool)
                and expected_size > 0
                and SHA256_PATTERN.fullmatch(expected_sha256) is not None,
                "AD4_ACCEPTANCE_FIXTURE_CONTRACT_INCOMPLETE",
                "A repository fixture lacks a frozen size/SHA256 contract.",
                details={
                    "manifest": manifest_identity["path"],
                    "repository_path": relative_path,
                },
            )
            identity = repository_file_identity(
                root,
                relative_path,
                role="fixture_repository_file",
            )
            require(
                identity["size_bytes"] == expected_size
                and identity["sha256"] == expected_sha256,
                "AD4_ACCEPTANCE_FIXTURE_IDENTITY_MISMATCH",
                "A repository fixture differs from its source-manifest contract.",
                details={
                    "manifest": manifest_identity["path"],
                    "repository_path": relative_path,
                },
            )
            previous = identities_by_path.get(str(identity["path"]))
            require(
                previous is None or previous == identity,
                "AD4_ACCEPTANCE_FIXTURE_CONTRACT_CONFLICT",
                "Two source manifests disagree about one repository fixture.",
                details={"repository_path": relative_path},
            )
            identities_by_path[str(identity["path"])] = identity
    return [identities_by_path[path] for path in sorted(identities_by_path)]


def canonical_evidence_payload_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    canonical_payload = {
        str(key): value for key, value in payload.items() if str(key) != "provenance"
    }
    try:
        encoded = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        fail(
            "AD4_ACCEPTANCE_EVIDENCE_PAYLOAD_INVALID",
            "The evidence payload cannot be canonically serialized.",
            details={"error_type": type(exc).__name__},
        )
    return {
        "algorithm": EVIDENCE_PAYLOAD_ALGORITHM,
        "canonical_size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def loaded_backend_module_origins(repo_root: str | Path) -> dict[str, Any]:
    """Fail if loaded DockStart backend modules came from a shadow import root."""

    root = Path(repo_root).resolve(strict=True)
    identities: list[dict[str, Any]] = []
    roots: list[dict[str, str]] = []
    for module_prefix, relative_root in BACKEND_IMPORT_ROOTS:
        expected_root = (root / relative_root).resolve(strict=True)
        roots.append({"module": module_prefix, "path": relative_root})
        matched = [
            (name, module)
            for name, module in sorted(sys.modules.items())
            if name == module_prefix or name.startswith(f"{module_prefix}.")
        ]
        require(
            bool(matched),
            "AD4_ACCEPTANCE_IMPORT_ORIGIN_MISSING",
            "A required DockStart backend package was not loaded.",
            details={"module": module_prefix},
        )
        for module_name, module in matched:
            origin = getattr(module, "__file__", None)
            require(
                isinstance(origin, str) and bool(origin),
                "AD4_ACCEPTANCE_IMPORT_ORIGIN_INVALID",
                "A loaded DockStart backend module has no file origin.",
                details={"module": module_name},
            )
            try:
                origin_path = Path(origin)
                require(
                    origin_path.is_file() and not origin_path.is_symlink(),
                    "AD4_ACCEPTANCE_IMPORT_ORIGIN_INVALID",
                    "A loaded DockStart backend module is not a regular source file.",
                    details={"module": module_name},
                )
                resolved = origin_path.resolve(strict=True)
                resolved.relative_to(expected_root)
                relative = resolved.relative_to(root).as_posix()
            except (OSError, ValueError) as exc:
                fail(
                    "AD4_ACCEPTANCE_IMPORT_ORIGIN_MISMATCH",
                    "A loaded DockStart backend module came from an unexpected root.",
                    details={"module": module_name, "error_type": type(exc).__name__},
                )
            module_suffix = module_name[len(module_prefix) :].lstrip(".")
            module_relative = module_suffix.replace(".", "/")
            expected_paths = {
                f"{relative_root}/__init__.py"
                if not module_relative
                else f"{relative_root}/{module_relative}.py",
                f"{relative_root}/{module_relative}/__init__.py"
                if module_relative
                else f"{relative_root}/__init__.py",
            }
            require(
                relative in expected_paths,
                "AD4_ACCEPTANCE_IMPORT_ORIGIN_MISMATCH",
                "A loaded backend module path does not match its Python module name.",
                details={"module": module_name, "path": relative},
            )
            identities.append(
                {
                    "module": module_name,
                    "path": relative,
                    "size_bytes": int(resolved.stat().st_size),
                    "sha256": sha256_file(resolved),
                }
            )
    identities.sort(key=lambda value: str(value["module"]))
    require(
        len({str(value["module"]) for value in identities}) == len(identities),
        "AD4_ACCEPTANCE_IMPORT_ORIGIN_DUPLICATE",
        "A backend module appears more than once in the import-origin evidence.",
    )
    encoded = json.dumps(
        identities,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return {
        "status": "verified",
        "algorithm": IMPORT_ORIGIN_ALGORITHM,
        "roots": roots,
        "module_count": len(identities),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "modules": identities,
    }


def attach_loaded_import_origins(
    source_session: Mapping[str, Any],
    *,
    repo_root: str | Path,
    verifier_id: str,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
) -> dict[str, Any]:
    """Complete a pre-import session only after backend imports are verified."""

    result = dict(source_session)
    require(
        result.get("schema_version") == 1
        and result.get("verifier_id") == verifier_id
        and isinstance(result.get("source_context"), Mapping)
        and isinstance(result.get("source_fingerprint"), Mapping)
        and isinstance(result.get("import_origins"), Mapping)
        and result.get("phase") == "pre_import"
        and result["import_origins"].get("status") == "not_loaded",
        "AD4_ACCEPTANCE_SOURCE_SESSION_INVALID",
        "A pre-import source session is missing or already finalized.",
        details={"verifier_id": verifier_id},
    )
    require(
        result["source_fingerprint"]
        == source_context_fingerprint(result["source_context"]),
        "AD4_ACCEPTANCE_SOURCE_SESSION_INVALID",
        "The pre-import session context does not match its fingerprint.",
        details={"verifier_id": verifier_id},
    )
    imports_session = capture_source_bound_session(
        repo_root=repo_root,
        verifier_id=verifier_id,
        verifier_path=verifier_path,
        fixture_manifest_paths=fixture_manifest_paths,
        validate_import_origins=True,
    )
    require(
        imports_session["source_fingerprint"] == result["source_fingerprint"],
        "AD4_ACCEPTANCE_SOURCE_CHANGED_DURING_IMPORT",
        "Verifier, fixture, Git, or backend source identity changed while importing the backend.",
        details={"verifier_id": verifier_id},
    )
    pre_import_time = _parse_explicit_utc(
        result.get("captured_at_utc"),
        field="source_session.pre_import.captured_at_utc",
    )
    imports_time = _parse_explicit_utc(
        imports_session.get("captured_at_utc"),
        field="source_session.imports_verified.captured_at_utc",
    )
    require(
        pre_import_time <= imports_time,
        "AD4_ACCEPTANCE_SOURCE_SESSION_TIME_INVALID",
        "Backend import verification predates the pre-import source snapshot.",
        details={"verifier_id": verifier_id},
    )
    result["phase"] = "imports_verified"
    result["import_origins"] = dict(imports_session["import_origins"])
    result["imports_verified_at_utc"] = imports_session["captured_at_utc"]
    result["imports_verified_source_fingerprint"] = dict(
        imports_session["source_fingerprint"]
    )
    return result


def _source_context(
    *,
    repo_root: str | Path,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=True)
    manifest_identities = [
        repository_file_identity(root, manifest_path, role="fixture_manifest")
        for manifest_path in fixture_manifest_paths
    ]
    source_files = [
        repository_file_identity(root, verifier_path, role="verifier"),
        repository_file_identity(root, Path(__file__), role="common"),
        *manifest_identities,
        *fixture_repository_identities(root, fixture_manifest_paths),
    ]
    source_files.sort(key=lambda value: (str(value["path"]), str(value["role"])))
    require(
        len({str(value["path"]) for value in source_files}) == len(source_files),
        "AD4_ACCEPTANCE_SOURCE_TREE_DUPLICATE",
        "The verification source tree contains a duplicate repository path.",
    )
    canonical_tree = json.dumps(
        source_files,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "git": git_worktree_identity(root),
        "source_files": source_files,
        "verification_source_tree": {
            "algorithm": SOURCE_TREE_ALGORITHM,
            "file_count": len(source_files),
            "sha256": hashlib.sha256(canonical_tree).hexdigest(),
        },
        "backend_validation_tree": backend_validation_tree(root),
    }


def current_source_context(
    *,
    repo_root: str | Path,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
) -> dict[str, Any]:
    """Return current source identity without making an execution-phase claim."""

    return _source_context(
        repo_root=repo_root,
        verifier_path=verifier_path,
        fixture_manifest_paths=fixture_manifest_paths,
    )


def source_context_fingerprint(context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            context,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        fail(
            "AD4_ACCEPTANCE_SOURCE_SESSION_INVALID",
            "The verifier source context cannot be canonically serialized.",
            details={"error_type": type(exc).__name__},
        )
    return {
        "algorithm": SOURCE_SESSION_ALGORITHM,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _parse_explicit_utc(value: Any, *, field: str) -> datetime:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        fail(
            "AD4_ACCEPTANCE_SOURCE_SESSION_TIME_INVALID",
            "A source-session timestamp is invalid.",
            details={"field": field},
        )
    require(
        text.endswith("Z")
        and parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0,
        "AD4_ACCEPTANCE_SOURCE_SESSION_TIME_INVALID",
        "A source-session timestamp is not explicit UTC.",
        details={"field": field},
    )
    return parsed


def capture_source_bound_session(
    *,
    repo_root: str | Path,
    verifier_id: str,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
    validate_import_origins: bool | None = None,
) -> dict[str, Any]:
    validate_origins = (
        verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS
        if validate_import_origins is None
        else bool(validate_import_origins)
    )
    if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS and not validate_origins:
        loaded_backend_names = sorted(
            name
            for name in sys.modules
            if any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix, _ in BACKEND_IMPORT_ROOTS
            )
        )
        require(
            not loaded_backend_names,
            "AD4_ACCEPTANCE_PREIMPORT_MODULE_ALREADY_LOADED",
            "A gate pre-import snapshot requires a fresh process with no DockStart backend modules loaded.",
            details={"loaded_module_count": len(loaded_backend_names)},
        )
    context = _source_context(
        repo_root=repo_root,
        verifier_path=verifier_path,
        fixture_manifest_paths=fixture_manifest_paths,
    )
    return {
        "schema_version": 1,
        "verifier_id": str(verifier_id),
        "phase": (
            "imports_snapshot"
            if validate_origins
            else (
                "pre_import"
                if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS
                else "source_snapshot"
            )
        ),
        "captured_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "source_context": context,
        "source_fingerprint": source_context_fingerprint(context),
        "import_origins": (
            loaded_backend_module_origins(repo_root)
            if validate_origins
            else {
                "status": (
                    "not_loaded"
                    if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS
                    else "not_required"
                )
            }
        ),
    }


def build_source_provenance(
    *,
    repo_root: str | Path,
    verifier_id: str,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
    evidence_payload: Mapping[str, Any],
    source_session: Mapping[str, Any] | None = None,
    validate_import_origins: bool | None = None,
) -> dict[str, Any]:
    """Bind evidence to its payload and an unchanged source execution session."""

    if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS:
        require(
            validate_import_origins is not False,
            "AD4_ACCEPTANCE_GATE_IMPORT_VALIDATION_REQUIRED",
            "AD4 gate provenance cannot disable backend import-origin validation.",
            details={"verifier_id": verifier_id},
        )
        validate_origins = True
    else:
        validate_origins = bool(validate_import_origins)
    if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS:
        require(
            isinstance(source_session, Mapping),
            "AD4_ACCEPTANCE_SOURCE_SESSION_REQUIRED",
            "Source-bound AD4 gate evidence requires a pre-workflow source session.",
            details={"verifier_id": verifier_id},
        )
    end_session = capture_source_bound_session(
        repo_root=repo_root,
        verifier_id=verifier_id,
        verifier_path=verifier_path,
        fixture_manifest_paths=fixture_manifest_paths,
        validate_import_origins=validate_origins,
    )
    start_session = dict(source_session) if isinstance(source_session, Mapping) else dict(end_session)
    start_context = (
        start_session.get("source_context")
        if isinstance(start_session.get("source_context"), Mapping)
        else {}
    )
    require(
        start_session.get("schema_version") == 1
        and start_session.get("verifier_id") == verifier_id
        and (
            verifier_id not in SOURCE_BOUND_GATE_VERIFIER_IDS
            or start_session.get("phase") == "imports_verified"
        )
        and isinstance(start_session.get("source_fingerprint"), Mapping),
        "AD4_ACCEPTANCE_SOURCE_SESSION_INVALID",
        "The verifier source session is missing or belongs to another verifier.",
    )
    start_fingerprint = dict(start_session["source_fingerprint"])
    imports_verified_fingerprint = (
        start_session.get("imports_verified_source_fingerprint")
        if isinstance(start_session.get("imports_verified_source_fingerprint"), Mapping)
        else {}
    )
    end_fingerprint = dict(end_session["source_fingerprint"])
    recomputed_start_fingerprint = source_context_fingerprint(start_context)
    recomputed_end_fingerprint = source_context_fingerprint(end_session["source_context"])
    require(
        start_fingerprint == recomputed_start_fingerprint
        and end_fingerprint == recomputed_end_fingerprint,
        "AD4_ACCEPTANCE_SOURCE_SESSION_INVALID",
        "A recorded source-session fingerprint does not match its source context.",
        details={"verifier_id": verifier_id},
    )
    start_imports = (
        start_session.get("import_origins")
        if isinstance(start_session.get("import_origins"), Mapping)
        else {}
    )
    end_imports = (
        end_session.get("import_origins")
        if isinstance(end_session.get("import_origins"), Mapping)
        else {}
    )
    if validate_origins:
        require(
            start_imports.get("status") == "verified"
            and end_imports.get("status") == "verified"
            and start_imports == end_imports,
            "AD4_ACCEPTANCE_IMPORT_ORIGIN_CHANGED_DURING_RUN",
            "Loaded DockStart backend module origins changed during execution.",
            details={"verifier_id": verifier_id},
        )
    start_captured_at = str(start_session.get("captured_at_utc") or "")
    imports_verified_at = str(
        start_session.get("imports_verified_at_utc") or start_captured_at
    )
    end_captured_at = str(end_session.get("captured_at_utc") or "")
    start_time = _parse_explicit_utc(
        start_captured_at,
        field="source_session.start.captured_at_utc",
    )
    end_time = _parse_explicit_utc(
        end_captured_at,
        field="source_session.end.captured_at_utc",
    )
    imports_time = _parse_explicit_utc(
        imports_verified_at,
        field="source_session.imports_verified.captured_at_utc",
    )
    require(
        start_time <= imports_time <= end_time,
        "AD4_ACCEPTANCE_SOURCE_SESSION_TIME_INVALID",
        "The source-session start timestamp is later than its end timestamp.",
        details={"verifier_id": verifier_id},
    )
    require(
        start_fingerprint == end_fingerprint
        and (
            not validate_origins
            or imports_verified_fingerprint
            == start_fingerprint
        ),
        "AD4_ACCEPTANCE_SOURCE_CHANGED_DURING_RUN",
        "Verifier, fixture, Git, or backend source identity changed during execution.",
        details={
            "start_sha256": start_fingerprint.get("sha256"),
            "end_sha256": end_fingerprint.get("sha256"),
        },
    )
    context = dict(end_session["source_context"])
    return {
        "schema_version": SOURCE_PROVENANCE_SCHEMA_VERSION,
        "binding_status": "source_bound",
        "verifier_id": str(verifier_id),
        "generated_at_utc": end_session["captured_at_utc"],
        "evidence_payload": canonical_evidence_payload_identity(evidence_payload),
        **context,
        "execution_source_guard": {
            "algorithm": SOURCE_SESSION_ALGORITHM,
            "pre_import_sha256": start_fingerprint["sha256"],
            "imports_verified_sha256": (
                imports_verified_fingerprint.get("sha256")
                if validate_origins
                else start_fingerprint["sha256"]
            ),
            "end_sha256": end_fingerprint["sha256"],
            "pre_import_captured_at_utc": start_captured_at,
            "imports_verified_at_utc": imports_verified_at,
            "end_captured_at_utc": end_captured_at,
            "unchanged": True,
            "start_import_origins": dict(start_imports),
            "end_import_origins": dict(end_imports),
        },
    }


def bind_source_provenance(
    payload: Mapping[str, Any],
    *,
    repo_root: str | Path,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
    source_session: Mapping[str, Any] | None = None,
    validate_import_origins: bool | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    verifier_id = str(result.get("verifier_id") or "")
    require(
        bool(verifier_id),
        "AD4_ACCEPTANCE_VERIFIER_ID_MISSING",
        "Cannot bind evidence without a verifier identity.",
    )
    require(
        "provenance" not in result,
        "AD4_ACCEPTANCE_PROVENANCE_ALREADY_PRESENT",
        "Evidence with an existing provenance block cannot be rebound.",
    )
    if verifier_id in SOURCE_BOUND_GATE_VERIFIER_IDS:
        require(
            result.get("schema_version") == GATE_EVIDENCE_SCHEMA_VERSION,
            "AD4_ACCEPTANCE_GATE_SCHEMA_REQUIRES_V2",
            "Source-bound AD4 gate evidence must use schema version 2.",
            details={"verifier_id": verifier_id},
        )
    result["provenance"] = build_source_provenance(
        repo_root=repo_root,
        verifier_id=verifier_id,
        verifier_path=verifier_path,
        fixture_manifest_paths=fixture_manifest_paths,
        evidence_payload=result,
        source_session=source_session,
        validate_import_origins=validate_import_origins,
    )
    return result


def _provenance_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, AcceptanceError):
        return {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    return {
        "code": "AD4_ACCEPTANCE_PROVENANCE_UNEXPECTED_ERROR",
        "message": "Source provenance could not be generated.",
        "details": {"error_type": type(exc).__name__},
    }


def bind_source_provenance_or_error(
    payload: Mapping[str, Any],
    *,
    repo_root: str | Path,
    verifier_path: str | Path,
    fixture_manifest_paths: Sequence[str | Path],
    source_session: Mapping[str, Any] | None = None,
    validate_import_origins: bool | None = None,
    expected_verifier_id: str | None = None,
) -> dict[str, Any]:
    """Never let provenance construction escape an evidence failure handler."""

    expected_id = str(expected_verifier_id or payload.get("verifier_id") or "")
    try:
        require(
            bool(expected_id) and str(payload.get("verifier_id") or "") == expected_id,
            "AD4_ACCEPTANCE_VERIFIER_ID_MISMATCH",
            "Evidence verifier identity does not match the expected gate.",
            details={"expected_verifier_id": expected_id},
        )
        return bind_source_provenance(
            payload,
            repo_root=repo_root,
            verifier_path=verifier_path,
            fixture_manifest_paths=fixture_manifest_paths,
            source_session=source_session,
            validate_import_origins=validate_import_origins,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = dict(payload)
        result.pop("provenance", None)
        result["verifier_id"] = expected_id
        result["ok"] = False
        provenance_error = _provenance_error(exc)
        original_error = result.get("error")
        if isinstance(original_error, Mapping):
            result["gate_error"] = dict(original_error)
        result["error"] = {
            "code": "AD4_ACCEPTANCE_SOURCE_BINDING_FAILED",
            "title": "Source-bound evidence could not be finalized",
            "message": "The gate result is unbound because provenance validation failed.",
            "raw_error": "",
            "suggestion": "Resolve the provenance error and rerun the complete gate.",
        }
        result["provenance_error"] = provenance_error
        try:
            payload_identity = canonical_evidence_payload_identity(result)
        except BaseException:
            payload_identity = {
                "algorithm": EVIDENCE_PAYLOAD_ALGORITHM,
                "status": "unavailable",
            }
        result["provenance"] = {
            "schema_version": SOURCE_PROVENANCE_SCHEMA_VERSION,
            "binding_status": "unbound",
            "verifier_id": expected_id,
            "generated_at_utc": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "evidence_payload": payload_identity,
            "provenance_error": provenance_error,
        }
        return result


def require_output_separate_from_inputs(
    output_path: str | Path | None,
    input_paths: Mapping[str, str | Path | None],
) -> None:
    """Reject an evidence output that could replace a scientific input/tool."""

    if output_path is None:
        return
    try:
        resolved_output = Path(output_path).expanduser().resolve(strict=False)
        collisions = [
            str(label)
            for label, path in input_paths.items()
            if path is not None
            and Path(path).expanduser().resolve(strict=False) == resolved_output
        ]
    except OSError as exc:
        fail(
            "AD4_ACCEPTANCE_OUTPUT_PATH_INVALID",
            "The evidence output path could not be resolved safely.",
            details={"error_type": type(exc).__name__},
        )
    require(
        not collisions,
        "AD4_ACCEPTANCE_OUTPUT_INPUT_COLLISION",
        "The evidence output path resolves to a scientific input or executable.",
        details={"colliding_inputs": collisions},
    )


def unbound_preflight_result(
    payload: Mapping[str, Any],
    *,
    expected_verifier_id: str,
) -> dict[str, Any]:
    """Return a structured unbound result for a fail-before-import preflight."""

    result = dict(payload)
    result.pop("provenance", None)
    result["verifier_id"] = str(expected_verifier_id)
    result["ok"] = False
    result["provenance"] = {
        "schema_version": SOURCE_PROVENANCE_SCHEMA_VERSION,
        "binding_status": "unbound",
        "verifier_id": str(expected_verifier_id),
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "evidence_payload": canonical_evidence_payload_identity(result),
        "reason": "preflight_failed_before_backend_import",
    }
    return result


def file_identity(
    path: str | Path,
    *,
    label: str,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    supplied = Path(path).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        fail(
            "AD4_ACCEPTANCE_FILE_MISSING",
            f"{label} is missing.",
            details={"path": str(supplied), "error": str(exc)},
        )
    require(
        resolved.is_file(),
        "AD4_ACCEPTANCE_FILE_INVALID",
        f"{label} is not a regular file.",
        details={"path": str(resolved)},
    )
    actual = {
        "path": str(resolved),
        "size_bytes": int(resolved.stat().st_size),
        "sha256": sha256_file(resolved),
    }
    require(
        actual["size_bytes"] > 0,
        "AD4_ACCEPTANCE_FILE_EMPTY",
        f"{label} is empty.",
        details=actual,
    )
    if expected is not None:
        expected_size = int(expected.get("size_bytes") or 0)
        expected_sha256 = str(expected.get("sha256") or "").lower()
        require(
            expected_size > 0 and SHA256_PATTERN.fullmatch(expected_sha256) is not None,
            "AD4_ACCEPTANCE_CONTRACT_INVALID",
            f"{label} has an invalid frozen identity contract.",
            details={"expected": dict(expected)},
        )
        require(
            actual["size_bytes"] == expected_size
            and actual["sha256"] == expected_sha256,
            "AD4_ACCEPTANCE_FILE_IDENTITY_MISMATCH",
            f"{label} does not match the frozen identity contract.",
            details={
                "expected": {
                    "size_bytes": expected_size,
                    "sha256": expected_sha256,
                },
                "actual": actual,
            },
        )
    return actual


def load_fixture_manifest(path: str | Path, fixture_id: str) -> dict[str, Any]:
    identity = file_identity(path, label=f"{fixture_id} source manifest")
    manifest_path = Path(identity["path"])
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(
            "AD4_ACCEPTANCE_MANIFEST_INVALID",
            f"{fixture_id} source manifest cannot be parsed.",
            details={"path": str(manifest_path), "error": str(exc)},
        )
    require(
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and payload.get("fixture_id") == fixture_id
        and payload.get("distribution") == "metadata_only"
        and payload.get("contains_autogrid_maps") is False
        and payload.get("contains_executables") is False,
        "AD4_ACCEPTANCE_MANIFEST_INVALID",
        f"{fixture_id} source manifest does not match the metadata-only contract.",
        details={"path": str(manifest_path)},
    )
    return payload


@contextmanager
def isolated_settings(
    root: Path,
    *,
    vina: Path,
    autogrid4: Path,
    python: Path | None = None,
) -> Iterator[Path]:
    """Point DockStart at one temporary, verifier-owned settings file."""

    from dockstart_core.settings import (  # Imported after verifier sets sys.path.
        SETTINGS_ENV_VAR,
        DockStartSettings,
        ToolPaths,
        save_settings,
    )
    from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR

    previous_settings = os.environ.get(SETTINGS_ENV_VAR)
    previous_resources = os.environ.get(RESOURCE_DIR_ENV_VAR)
    settings_path = root / "dockstart_acceptance_settings.json"
    isolated_resources = root / "no_bundled_resources"
    os.environ[SETTINGS_ENV_VAR] = str(settings_path)
    os.environ[RESOURCE_DIR_ENV_VAR] = str(isolated_resources)
    save_settings(
        DockStartSettings(
            tool_paths=ToolPaths(
                vina=str(vina.resolve()),
                autogrid4=str(autogrid4.resolve()),
                python=str(python.resolve()) if python is not None else "",
            )
        )
    )
    try:
        yield settings_path
    finally:
        if previous_settings is None:
            os.environ.pop(SETTINGS_ENV_VAR, None)
        else:
            os.environ[SETTINGS_ENV_VAR] = previous_settings
        if previous_resources is None:
            os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
        else:
            os.environ[RESOURCE_DIR_ENV_VAR] = previous_resources


def contained_project_file(
    project_root: Path,
    relative_path: str,
    *,
    label: str,
) -> Path:
    relative = Path(str(relative_path or ""))
    require(
        str(relative_path or "").strip() != "" and not relative.is_absolute(),
        "AD4_ACCEPTANCE_ARTIFACT_PATH_INVALID",
        f"{label} path is missing or absolute.",
        details={"relative_path": str(relative_path)},
    )
    root = project_root.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError:
        fail(
            "AD4_ACCEPTANCE_ARTIFACT_PATH_ESCAPE",
            f"{label} escapes the temporary project.",
            details={"relative_path": str(relative_path), "resolved": str(candidate)},
        )
    require(
        candidate.is_file(),
        "AD4_ACCEPTANCE_ARTIFACT_MISSING",
        f"{label} is not a regular file.",
        details={"path": str(candidate)},
    )
    return candidate


def verify_snapshot_record(
    project_root: Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    relative_path = str(record.get("relative_path") or record.get("file") or "")
    path = contained_project_file(project_root, relative_path, label=label)
    actual = file_identity(path, label=label)
    require(
        actual["size_bytes"] == int(record.get("size_bytes") or 0)
        and actual["sha256"] == str(record.get("sha256") or "").lower(),
        "AD4_ACCEPTANCE_ARTIFACT_IDENTITY_MISMATCH",
        f"{label} differs from its frozen manifest record.",
        details={"record": dict(record), "actual": actual},
    )
    return actual


def audit_generated_maps(
    project_root: Path,
    maps_result: Mapping[str, Any],
    *,
    autogrid4_sha256: str,
    required_ligand_types: Sequence[str],
) -> dict[str, Any]:
    """Re-read every generated AD4 artifact instead of trusting API flags."""

    manifest_relative = str(maps_result.get("manifest_file") or "")
    manifest_path = contained_project_file(
        project_root,
        manifest_relative,
        label="generated maps manifest",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(
            "AD4_ACCEPTANCE_MAPS_MANIFEST_INVALID",
            "Generated maps manifest cannot be parsed.",
            details={"path": str(manifest_path), "error": str(exc)},
        )
    require(
        isinstance(manifest, dict)
        and manifest.get("schema_version") == 1
        and manifest.get("status") == "ready"
        and manifest.get("source") == "generated"
        and manifest.get("protocol_id") == "ad4_maps"
        and (manifest.get("validation") or {}).get("complete") is True,
        "AD4_ACCEPTANCE_MAPS_NOT_READY",
        "Generated maps manifest is not a complete standard AD4 set.",
        details={"manifest_file": manifest_relative},
    )
    gpf = manifest.get("gpf") if isinstance(manifest.get("gpf"), dict) else {}
    gpf_identity = verify_snapshot_record(project_root, gpf, label="generated GPF")
    gpf_path = Path(gpf_identity["path"])
    autogrid = manifest.get("autogrid") if isinstance(manifest.get("autogrid"), dict) else {}
    log_path = contained_project_file(
        project_root,
        str(autogrid.get("log_file") or ""),
        label="AutoGrid GLG",
    )
    log_identity = file_identity(log_path, label="AutoGrid GLG")

    expected_autogrid_sha = str(autogrid4_sha256 or "").lower()
    autogrid_identity = file_identity(
        str(autogrid.get("path") or ""),
        label="generated-map AutoGrid4 executable",
    )
    autogrid_command = [str(value) for value in autogrid.get("command") or []]
    log_summary = (
        autogrid.get("log_summary")
        if isinstance(autogrid.get("log_summary"), Mapping)
        else {}
    )
    require(
        SHA256_PATTERN.fullmatch(expected_autogrid_sha) is not None
        and str(autogrid.get("sha256") or "").lower() == expected_autogrid_sha
        and autogrid_identity["sha256"] == expected_autogrid_sha
        and autogrid.get("exit_code") is not None
        and int(autogrid.get("exit_code")) == 0
        and len(autogrid_command) == 5
        and same_paths([autogrid_command[0]], [Path(autogrid_identity["path"])])
        and autogrid_command[1:3] == ["-p", gpf_path.name]
        and autogrid_command[3:5] == ["-l", log_path.name]
        and log_summary.get("successful_completion") is True
        and log_summary.get("has_error") is False,
        "AD4_ACCEPTANCE_AUTOGRID_EVIDENCE_MISMATCH",
        "Generated maps do not bind the expected AutoGrid4 executable and successful command.",
        details={"expected_sha256": expected_autogrid_sha, "autogrid": autogrid},
    )

    maps = manifest.get("maps") if isinstance(manifest.get("maps"), dict) else {}
    recorded_types = [str(value).strip() for value in maps.get("ligand_atom_types") or []]
    required_types = sorted({str(value).strip() for value in required_ligand_types if str(value).strip()})
    gpf_lines = [
        line.strip()
        for line in gpf_path.read_text(encoding="utf-8", errors="strict").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    gpf_ligand_types = [
        line.split()[1:]
        for line in gpf_lines
        if line.split()[0].lower() == "ligand_types"
    ]
    gpf_receptors = [
        line.split(maxsplit=1)[1]
        for line in gpf_lines
        if line.split()[0].lower() == "receptor" and len(line.split(maxsplit=1)) == 2
    ]
    require(
        bool(required_types)
        and len(recorded_types) == len(required_types)
        and set(recorded_types) == set(required_types)
        and len(gpf_ligand_types) == 1
        and len(gpf_ligand_types[0]) == len(required_types)
        and set(gpf_ligand_types[0]) == set(required_types)
        and len(gpf_receptors) == 1,
        "AD4_ACCEPTANCE_MAP_TYPES_MISMATCH",
        "Generated GPF/maps do not exactly cover the frozen ligand atom-type union.",
        details={
            "expected": required_types,
            "manifest_types": recorded_types,
            "gpf_types": gpf_ligand_types,
            "gpf_receptors": gpf_receptors,
        },
    )
    receptor_record = (
        manifest.get("receptor")
        if isinstance(manifest.get("receptor"), Mapping)
        else {}
    )
    ligand_record = (
        manifest.get("ligand")
        if isinstance(manifest.get("ligand"), Mapping)
        else {}
    )
    receptor_identity = verify_snapshot_record(
        project_root,
        receptor_record,
        label="generated-map receptor snapshot",
    )
    ligand_identity = verify_snapshot_record(
        project_root,
        ligand_record,
        label="generated-map ligand snapshot",
    )
    gpf_receptor_path = (gpf_path.parent / gpf_receptors[0]).resolve(strict=True)
    require(
        same_paths([str(gpf_receptor_path)], [Path(receptor_identity["path"])]),
        "AD4_ACCEPTANCE_GPF_RECEPTOR_MISMATCH",
        "Generated GPF does not reference the manifest-bound receptor snapshot.",
        details={
            "gpf_receptor": str(gpf_receptor_path),
            "manifest_receptor": receptor_identity,
        },
    )
    flexible_record = (
        manifest.get("flexible_receptor")
        if isinstance(manifest.get("flexible_receptor"), Mapping)
        else {}
    )
    flexible_identity: dict[str, Any] | None = None
    if flexible_record:
        flex_path = contained_project_file(
            project_root,
            str(flexible_record.get("flex_file") or ""),
            label="generated-map flexible receptor snapshot",
        )
        flexible_identity = file_identity(
            flex_path,
            label="generated-map flexible receptor snapshot",
        )
        require(
            str(flexible_record.get("rigid_sha256") or "").lower()
            == receptor_identity["sha256"]
            and str(flexible_record.get("flex_sha256") or "").lower()
            == flexible_identity["sha256"],
            "AD4_ACCEPTANCE_FLEXIBLE_MAP_INPUT_MISMATCH",
            "Generated flexible maps do not bind the frozen rigid/flex receptor pair.",
            details={
                "record": dict(flexible_record),
                "rigid": receptor_identity,
                "flex": flexible_identity,
            },
        )
    records = maps.get("files") if isinstance(maps.get("files"), list) else []
    required_names = [str(value) for value in maps.get("required_files") or []]
    require(
        bool(records) and bool(required_names),
        "AD4_ACCEPTANCE_MAPS_EMPTY",
        "Generated maps manifest contains no frozen map files.",
    )
    audited_files = [
        verify_snapshot_record(project_root, record, label=f"AD4 map {record.get('name') or index}")
        for index, record in enumerate(records, start=1)
        if isinstance(record, Mapping)
    ]
    recorded_names = {
        str(record.get("name") or Path(str(record.get("relative_path") or "")).name)
        for record in records
        if isinstance(record, Mapping)
    }
    require(
        set(required_names).issubset(recorded_names)
        and len(audited_files) == len(records),
        "AD4_ACCEPTANCE_MAPS_INCOMPLETE",
        "One or more required AD4 map files lack an audited manifest record.",
        details={"required": required_names, "recorded": sorted(recorded_names)},
    )
    return {
        "map_set_id": manifest.get("map_set_id"),
        "manifest": file_identity(manifest_path, label="generated maps manifest"),
        "gpf": gpf_identity,
        "glg": log_identity,
        "autogrid": {
            "path": autogrid.get("path"),
            "version": autogrid.get("version"),
            "sha256": autogrid.get("sha256"),
            "command": list(autogrid.get("command") or []),
            "exit_code": autogrid.get("exit_code"),
            "identity": autogrid_identity,
            "log_summary": dict(log_summary),
        },
        "ligand_atom_types": recorded_types,
        "required_files": required_names,
        "files": audited_files,
        "receptor": {**dict(receptor_record), "verified_identity": receptor_identity},
        "ligand": {**dict(ligand_record), "verified_identity": ligand_identity},
        "flexible_receptor": {
            **dict(flexible_record),
            **(
                {"verified_identity": flexible_identity}
                if flexible_identity is not None
                else {}
            ),
        },
    }


def option_values(command: Sequence[Any], option: str) -> list[str]:
    values = [str(value) for value in command]
    positions = [index for index, value in enumerate(values) if value == option]
    require(
        len(positions) == 1,
        "AD4_ACCEPTANCE_COMMAND_OPTION_COUNT_INVALID",
        f"Command must contain {option} exactly once.",
        details={"option": option, "positions": positions, "command": values},
    )
    result: list[str] = []
    for value in values[positions[0] + 1 :]:
        if value.startswith("--"):
            break
        result.append(value)
    return result


def require_flag_once(command: Sequence[Any], option: str) -> None:
    values = [str(value) for value in command]
    positions = [index for index, value in enumerate(values) if value == option]
    require(
        len(positions) == 1,
        "AD4_ACCEPTANCE_COMMAND_OPTION_COUNT_INVALID",
        f"Command must contain {option} exactly once.",
        details={"option": option, "positions": positions, "command": values},
    )


def same_paths(actual: Sequence[str], expected: Sequence[Path]) -> bool:
    if len(actual) != len(expected):
        return False
    try:
        return all(
            os.path.normcase(str(Path(left).resolve(strict=True)))
            == os.path.normcase(str(right.resolve(strict=True)))
            for left, right in zip(actual, expected, strict=True)
        )
    except OSError:
        return False


def pdbqt_atom_types(path: Path) -> list[str]:
    values: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            fields = line.split()
            require(
                len(fields) >= 2,
                "AD4_ACCEPTANCE_PDBQT_INVALID",
                "A PDBQT atom record lacks an AutoDock atom type.",
                details={"path": str(path), "line": line},
            )
            values.add(fields[-1])
    require(
        bool(values),
        "AD4_ACCEPTANCE_PDBQT_INVALID",
        "PDBQT contains no atom types.",
        details={"path": str(path)},
    )
    return sorted(values)


def pdbqt_heavy_coordinates(
    path: Path,
    *,
    first_model_only: bool = False,
    ligand_before_flex_only: bool = False,
) -> list[tuple[float, float, float]]:
    coordinates: list[tuple[float, float, float]] = []
    seen_model = False
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith("MODEL"):
            if seen_model and first_model_only:
                break
            seen_model = True
            continue
        if first_model_only and seen_model and line.startswith("ENDMDL"):
            break
        if ligand_before_flex_only and line.startswith("BEGIN_RES"):
            break
        if not line.startswith(("ATOM", "HETATM")):
            continue
        fields = line.split()
        atom_type = fields[-1] if fields else ""
        if atom_type.upper().startswith("H"):
            continue
        try:
            coordinates.append(
                (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            )
        except (TypeError, ValueError):
            fail(
                "AD4_ACCEPTANCE_PDBQT_COORDINATE_INVALID",
                "A PDBQT atom coordinate cannot be parsed.",
                details={"path": str(path), "line": line},
            )
    require(
        bool(coordinates),
        "AD4_ACCEPTANCE_PDBQT_COORDINATE_MISSING",
        "PDBQT contains no heavy-atom coordinates.",
        details={"path": str(path)},
    )
    return coordinates


def no_fit_rmsd(
    reference: Sequence[tuple[float, float, float]],
    observed: Sequence[tuple[float, float, float]],
) -> float:
    require(
        len(reference) == len(observed) and bool(reference),
        "AD4_ACCEPTANCE_POSE_ATOM_COUNT_MISMATCH",
        "Reference and output poses do not contain the same heavy-atom count.",
        details={"reference_count": len(reference), "observed_count": len(observed)},
    )
    squared = sum(
        (left[0] - right[0]) ** 2
        + (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
        for left, right in zip(reference, observed, strict=True)
    )
    return math.sqrt(squared / len(reference))


def error_payload(
    verifier_id: str,
    exc: BaseException,
    *,
    steps: Mapping[str, Any] | None = None,
    schema_version: int = 1,
) -> dict[str, Any]:
    if isinstance(exc, AcceptanceError):
        error = {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    else:
        error = {
            "code": "AD4_ACCEPTANCE_UNEXPECTED_ERROR",
            "message": "The external AD4 acceptance verifier failed unexpectedly.",
            "details": {"type": type(exc).__name__, "error": str(exc)},
        }
    return {
        "schema_version": int(schema_version),
        "verifier_id": verifier_id,
        "ok": False,
        "steps": dict(steps or {}),
        "error": error,
    }


def write_result(payload: Mapping[str, Any], output: Path | None) -> int:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output is not None:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, output)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
    print(encoded, end="")
    return 0 if payload.get("ok") is True else 1
