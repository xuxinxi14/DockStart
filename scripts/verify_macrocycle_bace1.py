"""Rebuild and verify the BACE_1 macrocycle workflow from pinned inputs.

The verifier deliberately ignores historical project and temporary outputs.  It
creates a fresh DockStart project, exercises only public project/protocol APIs,
runs the configured Meeko/RDKit and AutoDock Vina executables, exports the real
poses through ``mk_export``, and asks a separate RDKit process to re-read every
SDF record.

The positive claim is limited to workflow integrity and topology
reconstruction.  No pose-recovery claim is made because the fixture does not
pin an independently validated, same-coordinate-frame crystal-pose mapping.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
from dockstart_core.advanced_protocols import (  # noqa: E402
    inspect_meeko_ligand_pdbqt,
)
from dockstart_core.macrocycle import (  # noqa: E402
    confirm_selection,
    create_review,
)
from dockstart_core.preparation import (  # noqa: E402
    get_preparation_tool_status,
    prepare_ligand_pdbqt,
    prepare_receptor_pdbqt,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
)
from dockstart_core.result_export import (  # noqa: E402
    export_result_sdf,
    get_result_export_status,
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

FIXTURE_ROOT = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "macrocycle_bace1"
)
MANIFEST_PATH = FIXTURE_ROOT / "fixture_manifest.json"
DEFAULT_PYTHON = REPOSITORY_ROOT / "resources" / "python" / "python.exe"
DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
REQUIRED_FIXTURE_FILES = (
    "BACE_1_receptorH.pdb",
    "BACE_1_ligand.mol2",
    "BACE_1_ligand.sdf",
    "BACE_1_ligand_reference.pdbqt",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])


class MacrocycleAcceptanceError(RuntimeError):
    """Stable, JSON-serializable error boundary for the acceptance verifier."""

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
    raise MacrocycleAcceptanceError(code, message, details=details)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(value).resolve())))


def _regular_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        _fail(
            "MACROCYCLE_BACE1_REQUIRED_FILE_MISSING",
            f"{label} is missing or is not a regular file.",
            details={"label": label, "path": str(path)},
        )
    return path


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        _fail(
            "MACROCYCLE_BACE1_ORACLE_MISMATCH",
            f"{label} does not match the manifest-pinned value.",
            details={"label": label, "expected": expected, "actual": actual},
        )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            f"{label} must be a JSON object.",
            details={"label": label, "actual_type": type(value).__name__},
        )
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            f"{label} must be a JSON array.",
            details={"label": label, "actual_type": type(value).__name__},
        )
    return value


def _require_ok(
    label: str,
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    if payload.get("ok") is not True:
        _fail(
            "MACROCYCLE_BACE1_PUBLIC_API_FAILED",
            f"DockStart public API step {label} failed.",
            details={"label": label, "response": dict(payload)},
        )
    return payload


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        evidence = action()
    except MacrocycleAcceptanceError as exc:
        steps[name] = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
        exc.steps = copy.deepcopy(steps)
        raise
    steps[name] = {"ok": True, "evidence": dict(evidence)}
    return evidence


def _load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            "The BACE_1 fixture manifest cannot be read as UTF-8 JSON.",
            details={"path": str(path), "error": str(exc)},
        )
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 2
        or payload.get("fixture_id") != "macrocycle_bace1"
    ):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            "The BACE_1 fixture manifest header is invalid.",
        )

    files = _require_mapping(payload.get("files"), "files")
    if set(files) != set(REQUIRED_FIXTURE_FILES):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            "The fixture identity set is incomplete or contains an unknown input.",
            details={
                "expected": list(REQUIRED_FIXTURE_FILES),
                "actual": sorted(str(key) for key in files),
            },
        )
    for name in REQUIRED_FIXTURE_FILES:
        record = _require_mapping(files.get(name), f"files.{name}")
        sha = str(record.get("sha256") or "")
        size = record.get("size_bytes")
        if not SHA256_RE.fullmatch(sha) or isinstance(size, bool):
            _fail(
                "MACROCYCLE_BACE1_MANIFEST_INVALID",
                f"The identity record for {name} is invalid.",
            )
        try:
            if int(size) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            _fail(
                "MACROCYCLE_BACE1_MANIFEST_INVALID",
                f"The size record for {name} is invalid.",
            )

    expected = _require_mapping(payload.get("expected"), "expected")
    review = _require_mapping(expected.get("review"), "expected.review")
    candidate_key = _require_list(
        review.get("candidate_key"),
        "expected.review.candidate_key",
    )
    if (
        review.get("candidate_count_total") != 7
        or len(candidate_key) != 7
        or review.get("recommended_candidate_id")
        != "candidate_72113262ddb85177"
    ):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            "The pinned seven-candidate review contract is invalid.",
        )
    expected_ids: set[str] = set()
    for item in candidate_key:
        record = _require_mapping(item, "candidate_key item")
        candidate_id = str(record.get("candidate_id") or "")
        bonds = record.get("exact_bonds")
        if (
            not candidate_id.startswith("candidate_")
            or candidate_id in expected_ids
            or not isinstance(bonds, list)
            or len(bonds) != 1
            or not isinstance(bonds[0], list)
            or len(bonds[0]) != 2
        ):
            _fail(
                "MACROCYCLE_BACE1_MANIFEST_INVALID",
                "The pinned candidate key contains an invalid entry.",
                details={"entry": dict(record)},
            )
        expected_ids.add(candidate_id)

    reviewed = _require_mapping(
        expected.get("reviewed_preparation"),
        "expected.reviewed_preparation",
    )
    topology = _require_mapping(
        expected.get("exported_pose_topology"),
        "expected.exported_pose_topology",
    )
    run = _require_mapping(expected.get("run"), "expected.run")
    scope = _require_mapping(payload.get("scientific_scope"), "scientific_scope")
    if (
        reviewed.get("selection_mode") != "candidate"
        or reviewed.get("exact_bonds") != [[2, 3]]
        or reviewed.get("glue_pseudo_atom_count") != 2
        or reviewed.get("protocol") != "meeko_macrocycle"
        or reviewed.get("protocol_mode") != "reviewed"
        or reviewed.get("run_attribution") != "formal_reviewed"
        or topology.get("forbidden_atomic_numbers") != [0]
        or topology.get("forbidden_pseudo_atom_prefix") != "G"
        or scope.get("pose_recovery_claimed") is not False
        or run.get("minimum_mode_count") != 1
        or run.get("maximum_mode_count") != 20
    ):
        _fail(
            "MACROCYCLE_BACE1_MANIFEST_INVALID",
            "The reviewed preparation, export, run, or scientific-scope contract is invalid.",
        )
    return payload


def _verify_fixture_inputs(
    fixture_root: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    root = fixture_root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail(
            "MACROCYCLE_BACE1_FIXTURE_ROOT_INVALID",
            "The BACE_1 fixture root is missing or unsafe.",
            details={"path": str(root)},
        )
    files = _require_mapping(manifest.get("files"), "files")
    evidence: dict[str, Any] = {}
    for name in REQUIRED_FIXTURE_FILES:
        path = _regular_file(root / name, f"BACE_1 fixture {name}")
        record = _require_mapping(files.get(name), f"files.{name}")
        actual = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if (
            actual["size_bytes"] != int(record["size_bytes"])
            or actual["sha256"] != record["sha256"]
        ):
            _fail(
                "MACROCYCLE_BACE1_INPUT_IDENTITY_MISMATCH",
                f"The fixed fixture input {name} has changed.",
                details={
                    "name": name,
                    "expected": {
                        "size_bytes": record["size_bytes"],
                        "sha256": record["sha256"],
                    },
                    "actual": actual,
                },
            )
        evidence[name] = actual
    return {
        "fixture_root": str(root),
        "manifest": {
            "path": str(MANIFEST_PATH),
            "size_bytes": MANIFEST_PATH.stat().st_size,
            "sha256": _sha256(MANIFEST_PATH),
        },
        "files": evidence,
    }


def _project_artifact(
    project_root: Path,
    relative_value: Any,
    label: str,
) -> Path:
    relative = str(relative_value or "").strip()
    candidate = Path(relative)
    if not relative or candidate.is_absolute():
        _fail(
            "MACROCYCLE_BACE1_ARTIFACT_PATH_INVALID",
            f"{label} has no safe project-relative path.",
            details={"label": label, "relative_path": relative},
        )
    resolved = (project_root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        _fail(
            "MACROCYCLE_BACE1_ARTIFACT_OUTSIDE_PROJECT",
            f"{label} resolves outside the temporary project.",
            details={"label": label, "relative_path": relative},
        )
    if not resolved.is_file() or resolved.is_symlink():
        _fail(
            "MACROCYCLE_BACE1_ARTIFACT_MISSING",
            f"{label} is missing or unsafe.",
            details={"label": label, "relative_path": relative},
        )
    return resolved


def _snapshot(
    project_root: Path,
    relative_value: Any,
    label: str,
) -> dict[str, Any]:
    path = _project_artifact(project_root, relative_value, label)
    return {
        "relative_path": str(relative_value),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _read_json_artifact(
    project_root: Path,
    relative_value: Any,
    label: str,
) -> dict[str, Any]:
    path = _project_artifact(project_root, relative_value, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "MACROCYCLE_BACE1_ARTIFACT_JSON_INVALID",
            f"{label} is not valid UTF-8 JSON.",
            details={"label": label, "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "MACROCYCLE_BACE1_ARTIFACT_JSON_INVALID",
            f"{label} must contain a JSON object.",
        )
    return payload


def _verify_snapshot_unchanged(
    project_root: Path,
    record: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    actual = _snapshot(project_root, record.get("relative_path"), label)
    if (
        actual["sha256"] != record.get("sha256")
        or actual["size_bytes"] != record.get("size_bytes")
    ):
        _fail(
            "MACROCYCLE_BACE1_IMMUTABLE_EVIDENCE_CHANGED",
            f"{label} changed after it was frozen.",
            details={"expected": dict(record), "actual": actual},
        )
    return actual


def _error_code(payload: Mapping[str, Any]) -> str:
    error = payload.get("error")
    return (
        str(error.get("code") or "")
        if isinstance(error, Mapping)
        else ""
    )


def _verify_tools(
    project_root: Path,
    python_path: Path,
    vina_path: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    tool_versions = _require_mapping(
        expected.get("toolchain"),
        "expected.toolchain",
    )
    status = _require_ok(
        "get_preparation_tool_status",
        get_preparation_tool_status(str(project_root)),
    )
    tools = _require_mapping(status.get("tools"), "preparation tools")
    python = _require_mapping(tools.get("python"), "Python status")
    rdkit = _require_mapping(tools.get("rdkit"), "RDKit status")
    meeko = _require_mapping(tools.get("meeko"), "Meeko status")
    if (
        python.get("status") != "ok"
        or rdkit.get("status") != "ok"
        or meeko.get("status") != "ok"
    ):
        _fail(
            "MACROCYCLE_BACE1_TOOLCHAIN_NOT_READY",
            "The configured Python/RDKit/Meeko toolchain is not ready.",
            details={"tools": dict(tools)},
        )
    for label, record in (
        ("Python", python),
        ("RDKit", rdkit),
        ("Meeko", meeko),
    ):
        if _normalized_path(str(record.get("path") or "")) != _normalized_path(
            python_path
        ):
            _fail(
                "MACROCYCLE_BACE1_PYTHON_PATH_MISMATCH",
                f"{label} did not resolve through the supplied Python executable.",
                details={
                    "label": label,
                    "expected": str(python_path),
                    "actual": record.get("path"),
                },
            )
    _assert_equal(
        str(python.get("version") or "").removeprefix("Python "),
        tool_versions.get("python"),
        "Python version",
    )
    _assert_equal(rdkit.get("version"), tool_versions.get("rdkit"), "RDKit version")
    _assert_equal(meeko.get("version"), tool_versions.get("meeko"), "Meeko version")

    ligand_capability = (
        meeko.get("capabilities", {}).get("ligand_preparation", {})
        if isinstance(meeko.get("capabilities"), Mapping)
        else {}
    )
    receptor_capability = (
        meeko.get("capabilities", {}).get("receptor_preparation", {})
        if isinstance(meeko.get("capabilities"), Mapping)
        else {}
    )
    if (
        not isinstance(ligand_capability, Mapping)
        or ligand_capability.get("status") != "ok"
        or not isinstance(receptor_capability, Mapping)
        or receptor_capability.get("status") != "ok"
    ):
        _fail(
            "MACROCYCLE_BACE1_MEEKO_CAPABILITY_MISSING",
            "Meeko ligand or receptor preparation capability is missing.",
            details={
                "ligand_preparation": dict(ligand_capability),
                "receptor_preparation": dict(receptor_capability),
            },
        )

    detection = vina_adapter.detect(str(vina_path))
    if (
        detection.status != "ok"
        or not detection.path
        or _normalized_path(detection.path) != _normalized_path(vina_path)
    ):
        _fail(
            "MACROCYCLE_BACE1_VINA_DETECTION_FAILED",
            "The supplied Vina executable was not detected as the exact runtime.",
            details={"detection": detection.to_dict()},
        )
    _assert_equal(detection.version, tool_versions.get("vina"), "Vina version")
    return {
        "python": {
            "path": str(python_path),
            "source": python.get("source"),
            "version": python.get("version"),
            "size_bytes": python_path.stat().st_size,
            "sha256": _sha256(python_path),
        },
        "rdkit": {
            "path": rdkit.get("path"),
            "source": rdkit.get("source"),
            "version": rdkit.get("version"),
            "capabilities": rdkit.get("capabilities"),
        },
        "meeko": {
            "path": meeko.get("path"),
            "source": meeko.get("source"),
            "version": meeko.get("version"),
            "capabilities": meeko.get("capabilities"),
        },
        "vina": {
            "path": str(vina_path),
            "source": detection.source,
            "version": detection.version,
            "size_bytes": vina_path.stat().st_size,
            "sha256": _sha256(vina_path),
            "capabilities": detection.capabilities,
        },
    }


def _verify_imported_raw(
    project_root: Path,
    response: Mapping[str, Any],
    source: Path,
    label: str,
) -> dict[str, Any]:
    raw = _project_artifact(
        project_root,
        response.get("raw_file"),
        f"{label} imported raw file",
    )
    actual = {
        "relative_path": response.get("raw_file"),
        "size_bytes": raw.stat().st_size,
        "sha256": _sha256(raw),
    }
    expected = {
        "size_bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }
    if (
        actual["size_bytes"] != expected["size_bytes"]
        or actual["sha256"] != expected["sha256"]
    ):
        _fail(
            "MACROCYCLE_BACE1_RAW_IMPORT_CHANGED",
            f"The public API changed the bytes of the imported {label}.",
            details={"expected": expected, "actual": actual},
        )
    return actual


def _validate_standard_rejection(
    response: Mapping[str, Any],
    project_root: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    expected_code = str(expected.get("standard_preparation_rejection_code") or "")
    actual_code = _error_code(response)
    if response.get("ok") is not False or actual_code != expected_code:
        _fail(
            "MACROCYCLE_BACE1_STANDARD_PREPARATION_FAILED_OPEN",
            "Standard ligand preparation did not fail closed at macrocycle review.",
            details={
                "expected_error_code": expected_code,
                "response": dict(response),
            },
        )
    prepared = project_root / "prepared" / "ligand.pdbqt"
    if prepared.exists():
        _fail(
            "MACROCYCLE_BACE1_STANDARD_PREPARATION_PUBLISHED",
            "The rejected standard preparation published ligand.pdbqt.",
            details={"path": str(prepared)},
        )
    metadata = _read_json_artifact(
        project_root,
        response.get("metadata_file"),
        "rejected standard preparation metadata",
    )
    if (
        metadata.get("status") != "failed"
        or metadata.get("published") is not False
        or _error_code(metadata) != expected_code
    ):
        _fail(
            "MACROCYCLE_BACE1_STANDARD_REJECTION_RECORD_INVALID",
            "The failed standard preparation audit record is incomplete.",
            details={"metadata": metadata},
        )
    stderr = _snapshot(
        project_root,
        response.get("stderr_file"),
        "rejected standard preparation stderr",
    )
    return {
        "error_code": actual_code,
        "prepared_output_absent": True,
        "prep_id": response.get("prep_id"),
        "exit_code": response.get("exit_code"),
        "metadata": _snapshot(
            project_root,
            response.get("metadata_file"),
            "rejected standard preparation metadata",
        ),
        "stderr": stderr,
    }


def _candidate_key(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = review.get("candidate_sets")
    if not isinstance(candidates, list):
        _fail(
            "MACROCYCLE_BACE1_REVIEW_INVALID",
            "Macrocycle review has no candidate set.",
        )
    result: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            _fail(
                "MACROCYCLE_BACE1_REVIEW_INVALID",
                "Macrocycle review contains a non-object candidate.",
            )
        result.append(
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "exact_bonds": copy.deepcopy(item.get("exact_bonds")),
            }
        )
    return result


def _validate_review(
    review: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    oracle = _require_mapping(expected.get("review"), "expected.review")
    actual_key = _candidate_key(review)
    expected_key = copy.deepcopy(
        _require_list(oracle.get("candidate_key"), "candidate_key")
    )
    mismatches: dict[str, Any] = {}
    for key in (
        "candidate_count_total",
        "recommended_candidate_id",
        "atom_table_sha256",
        "bond_topology_sha256",
        "analysis_sha256",
    ):
        if review.get(key) != oracle.get(key):
            mismatches[key] = {
                "expected": oracle.get(key),
                "actual": review.get(key),
            }
    if actual_key != expected_key:
        mismatches["candidate_key"] = {
            "expected": expected_key,
            "actual": actual_key,
        }
    if (
        review.get("protocol_id") != "meeko_macrocycle"
        or review.get("is_macrocycle") is not True
        or review.get("flexible_supported") is not True
        or review.get("rigid_supported") is not True
    ):
        mismatches["capability"] = {
            "protocol_id": review.get("protocol_id"),
            "is_macrocycle": review.get("is_macrocycle"),
            "flexible_supported": review.get("flexible_supported"),
            "rigid_supported": review.get("rigid_supported"),
        }
    indexing = (
        review.get("atom_indexing")
        if isinstance(review.get("atom_indexing"), Mapping)
        else {}
    )
    topology = _require_mapping(
        expected.get("exported_pose_topology"),
        "expected.exported_pose_topology",
    )
    if (
        indexing.get("source_atom_count") != topology.get("source_atom_count")
        or indexing.get("prepared_atom_count") != topology.get("source_atom_count")
        or indexing.get("source_indices_preserved") is not True
    ):
        mismatches["atom_indexing"] = dict(indexing)
    if mismatches:
        _fail(
            "MACROCYCLE_BACE1_REVIEW_ORACLE_MISMATCH",
            "Fresh macrocycle review differs from the fixed seven-candidate oracle.",
            details={"mismatches": mismatches},
        )
    record = _require_mapping(review.get("record"), "review.record")
    source = _require_mapping(review.get("source"), "review.source")
    snapshot = _require_mapping(
        review.get("input_snapshot"),
        "review.input_snapshot",
    )
    if (
        source.get("sha256") != snapshot.get("sha256")
        or source.get("size_bytes") != snapshot.get("size_bytes")
    ):
        _fail(
            "MACROCYCLE_BACE1_REVIEW_INPUT_MISMATCH",
            "The review did not freeze the exact imported raw ligand.",
        )
    return {
        "review_id": review.get("review_id"),
        "candidate_count_total": review.get("candidate_count_total"),
        "recommended_candidate_id": review.get("recommended_candidate_id"),
        "candidate_key": actual_key,
        "atom_table_sha256": review.get("atom_table_sha256"),
        "bond_topology_sha256": review.get("bond_topology_sha256"),
        "analysis_sha256": review.get("analysis_sha256"),
        "tool_versions": review.get("tool_versions"),
        "record": dict(record),
        "input_snapshot": dict(snapshot),
    }


def _validate_confirmation(
    confirmation: Mapping[str, Any],
    review: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    oracle = _require_mapping(
        expected.get("reviewed_preparation"),
        "expected.reviewed_preparation",
    )
    review_oracle = _require_mapping(expected.get("review"), "expected.review")
    expected_values = {
        "review_id": review.get("review_id"),
        "review_analysis_sha256": review_oracle.get("analysis_sha256"),
        "selection_mode": oracle.get("selection_mode"),
        "candidate_id": review_oracle.get("recommended_candidate_id"),
        "exact_bonds": oracle.get("exact_bonds"),
        "atom_table_sha256": review_oracle.get("atom_table_sha256"),
        "bond_topology_sha256": review_oracle.get("bond_topology_sha256"),
    }
    mismatches = {
        key: {"expected": value, "actual": confirmation.get(key)}
        for key, value in expected_values.items()
        if confirmation.get(key) != value
    }
    record = (
        confirmation.get("record")
        if isinstance(confirmation.get("record"), Mapping)
        else {}
    )
    if not SHA256_RE.fullmatch(str(record.get("sha256") or "")):
        mismatches["record_sha256"] = record.get("sha256")
    if mismatches:
        _fail(
            "MACROCYCLE_BACE1_CONFIRMATION_ORACLE_MISMATCH",
            "The active macrocycle confirmation differs from the pinned selection.",
            details={"mismatches": mismatches},
        )
    return {
        "confirmation_id": confirmation.get("confirmation_id"),
        "selection_mode": confirmation.get("selection_mode"),
        "candidate_id": confirmation.get("candidate_id"),
        "exact_bonds": confirmation.get("exact_bonds"),
        "binding_sha256": confirmation.get("binding_sha256"),
        "record": dict(record),
    }


def _validate_reviewed_preparation_evidence(
    project_root: Path,
    response: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    review_id: str,
    confirmation_sha256: str,
) -> dict[str, Any]:
    metadata_relative = response.get("metadata_file")
    metadata = _read_json_artifact(
        project_root,
        metadata_relative,
        "reviewed macrocycle preparation metadata",
    )
    oracle = _require_mapping(
        expected.get("reviewed_preparation"),
        "expected.reviewed_preparation",
    )
    review_oracle = _require_mapping(expected.get("review"), "expected.review")
    if (
        response.get("ok") is not True
        or response.get("exit_code") != 0
        or metadata.get("status") != "finished"
        or metadata.get("published") is not True
        or metadata.get("protocol") != oracle.get("protocol")
        or metadata.get("protocol_mode") != oracle.get("protocol_mode")
        or metadata.get("method") != "meeko_macrocycle"
    ):
        _fail(
            "MACROCYCLE_BACE1_REVIEWED_PREPARATION_INVALID",
            "The reviewed macrocycle preparation did not finish and publish.",
            details={"response": dict(response), "metadata": metadata},
        )
    contract = _require_mapping(
        metadata.get("macrocycle_contract"),
        "macrocycle contract",
    )
    expected_contract = {
        "protocol_id": oracle.get("protocol"),
        "review_id": review_id,
        "confirmation_sha256": confirmation_sha256,
        "selection_mode": oracle.get("selection_mode"),
        "candidate_id": review_oracle.get("recommended_candidate_id"),
        "exact_bonds": oracle.get("exact_bonds"),
        "atom_table_sha256": review_oracle.get("atom_table_sha256"),
        "bond_topology_sha256": review_oracle.get("bond_topology_sha256"),
    }
    mismatches = {
        key: {"expected": value, "actual": contract.get(key)}
        for key, value in expected_contract.items()
        if contract.get(key) != value
    }
    if mismatches:
        _fail(
            "MACROCYCLE_BACE1_CONTRACT_MISMATCH",
            "The immutable preparation contract differs from the confirmed selection.",
            details={"mismatches": mismatches},
        )

    protocol_evidence = _require_mapping(
        metadata.get("protocol_evidence"),
        "protocol_evidence",
    )
    if (
        protocol_evidence.get("ok") is not True
        or protocol_evidence.get("mode") != "reviewed"
        or protocol_evidence.get("issues") != []
    ):
        _fail(
            "MACROCYCLE_BACE1_EVIDENCE_GATE_INVALID",
            "The reviewed macrocycle evidence gate is not a clean pass.",
            details={"protocol_evidence": dict(protocol_evidence)},
        )
    worker = _require_mapping(
        protocol_evidence.get("evidence"),
        "worker evidence",
    )
    expected_worker = {
        "ok": True,
        "protocol_id": oracle.get("protocol"),
        "selection_mode": oracle.get("selection_mode"),
        "candidate_id": review_oracle.get("recommended_candidate_id"),
        "expected_bonds": oracle.get("exact_bonds"),
        "actual_bonds": oracle.get("exact_bonds"),
        "glue_pseudo_atom_count": oracle.get("glue_pseudo_atom_count"),
        "atom_table_sha256": review_oracle.get("atom_table_sha256"),
        "bond_topology_sha256": review_oracle.get("bond_topology_sha256"),
        "review_id": review_id,
        "confirmation_sha256": confirmation_sha256,
    }
    worker_mismatches = {
        key: {"expected": value, "actual": worker.get(key)}
        for key, value in expected_worker.items()
        if worker.get(key) != value
    }
    tool_versions = _require_mapping(
        expected.get("toolchain"),
        "expected.toolchain",
    )
    for key in ("meeko", "rdkit"):
        worker_key = f"{key}_version"
        if worker.get(worker_key) != tool_versions.get(key):
            worker_mismatches[worker_key] = {
                "expected": tool_versions.get(key),
                "actual": worker.get(worker_key),
            }
    if worker_mismatches:
        _fail(
            "MACROCYCLE_BACE1_WORKER_EVIDENCE_MISMATCH",
            "The macrocycle worker evidence differs from the pinned selection.",
            details={"mismatches": worker_mismatches},
        )

    contract_relative = str(metadata.get("macrocycle_contract_file") or "")
    evidence_relative = str(metadata.get("macrocycle_evidence_file") or "")
    input_relative = str(metadata.get("macrocycle_input_file") or "")
    contract_payload = _read_json_artifact(
        project_root,
        contract_relative,
        "immutable macrocycle contract",
    )
    evidence_payload = _read_json_artifact(
        project_root,
        evidence_relative,
        "immutable macrocycle worker evidence",
    )
    if contract_payload != contract or evidence_payload != worker:
        _fail(
            "MACROCYCLE_BACE1_EVIDENCE_FILE_CONTENT_MISMATCH",
            "Persisted contract or worker evidence differs from preparation metadata.",
        )
    contract_snapshot = _snapshot(
        project_root,
        contract_relative,
        "immutable macrocycle contract",
    )
    evidence_snapshot = _snapshot(
        project_root,
        evidence_relative,
        "immutable macrocycle worker evidence",
    )
    input_snapshot = _snapshot(
        project_root,
        input_relative,
        "immutable macrocycle preparation input",
    )
    if (
        contract_snapshot["sha256"]
        != metadata.get("macrocycle_contract_sha256")
        or input_snapshot["sha256"]
        != contract.get("runtime_input", {}).get("sha256")
        or input_snapshot["size_bytes"]
        != contract.get("runtime_input", {}).get("size_bytes")
    ):
        _fail(
            "MACROCYCLE_BACE1_EVIDENCE_HASH_MISMATCH",
            "Contract or frozen-input hashes do not cross-bind.",
            details={
                "contract": contract_snapshot,
                "input": input_snapshot,
                "metadata_contract_sha256": metadata.get(
                    "macrocycle_contract_sha256"
                ),
                "runtime_input": contract.get("runtime_input"),
            },
        )

    prepared = _project_artifact(
        project_root,
        response.get("output_file"),
        "published reviewed ligand PDBQT",
    )
    prepared_snapshot = _snapshot(
        project_root,
        response.get("output_file"),
        "published reviewed ligand PDBQT",
    )
    if (
        worker.get("output_sha256") != prepared_snapshot["sha256"]
        or worker.get("output_size_bytes") != prepared_snapshot["size_bytes"]
    ):
        _fail(
            "MACROCYCLE_BACE1_PREPARED_OUTPUT_HASH_MISMATCH",
            "Published ligand PDBQT differs from the worker evidence.",
            details={
                "worker_sha256": worker.get("output_sha256"),
                "worker_size_bytes": worker.get("output_size_bytes"),
                "actual": prepared_snapshot,
            },
        )
    try:
        inspection = inspect_meeko_ligand_pdbqt(prepared)
    except Exception as exc:  # noqa: BLE001 - convert to stable verifier error.
        _fail(
            "MACROCYCLE_BACE1_PDBQT_INSPECTION_FAILED",
            "The published macrocycle PDBQT cannot be independently inspected.",
            details={"error": str(exc)},
        )
    glue = inspection.get("glue_pseudo_atoms")
    if (
        not isinstance(glue, list)
        or len(glue) != oracle.get("glue_pseudo_atom_count")
        or inspection.get("embedded_topology") is not True
    ):
        _fail(
            "MACROCYCLE_BACE1_GLUE_TOPOLOGY_MISMATCH",
            "The published PDBQT lacks the expected G* pair or embedded topology.",
            details={"inspection": inspection},
        )
    return {
        "prep_id": response.get("prep_id"),
        "metadata": _snapshot(
            project_root,
            metadata_relative,
            "reviewed macrocycle preparation metadata",
        ),
        "contract": contract_snapshot,
        "worker_evidence": evidence_snapshot,
        "frozen_input": input_snapshot,
        "prepared_ligand": prepared_snapshot,
        "selection": {
            "review_id": review_id,
            "confirmation_sha256": confirmation_sha256,
            "candidate_id": contract.get("candidate_id"),
            "exact_bonds": contract.get("exact_bonds"),
            "glue_pseudo_atom_count": len(glue),
        },
        "inspection": {
            "glue_pseudo_atom_count": len(glue),
            "glue_pseudo_atoms": glue,
            "embedded_topology": inspection.get("embedded_topology"),
            "smiles": inspection.get("smiles"),
            "smiles_index_pair_count": inspection.get(
                "smiles_index_pair_count"
            ),
        },
    }


def _validate_receptor_preparation(
    project_root: Path,
    response: Mapping[str, Any],
    source: Path,
    python_path: Path,
) -> dict[str, Any]:
    if (
        response.get("ok") is not True
        or response.get("exit_code") != 0
    ):
        _fail(
            "MACROCYCLE_BACE1_RECEPTOR_PREPARATION_INVALID",
            "The real receptor preparation did not finish.",
            details={"response": dict(response)},
        )
    metadata = _read_json_artifact(
        project_root,
        response.get("metadata_file"),
        "receptor preparation metadata",
    )
    prepared = _snapshot(
        project_root,
        response.get("output_file"),
        "prepared receptor PDBQT",
    )
    if (
        metadata.get("status") != "finished"
        or metadata.get("published") is not True
        or metadata.get("exit_code") != 0
        or _normalized_path(str(metadata.get("python_path") or ""))
        != _normalized_path(python_path)
    ):
        _fail(
            "MACROCYCLE_BACE1_RECEPTOR_PROVENANCE_INVALID",
            "Receptor preparation metadata lacks successful configured-tool provenance.",
            details={"metadata": metadata},
        )
    input_verification = (
        metadata.get("input_verification")
        if isinstance(metadata.get("input_verification"), Mapping)
        else {}
    )
    if (
        input_verification.get("actual_sha256") != _sha256(source)
        and input_verification.get("sha256") != _sha256(source)
    ):
        # The current metadata schema may nest the snapshot; accept only an
        # exact hash found in this explicit verification object.
        serialized = json.dumps(input_verification, sort_keys=True)
        if _sha256(source) not in serialized:
            _fail(
                "MACROCYCLE_BACE1_RECEPTOR_INPUT_PROVENANCE_INVALID",
                "Receptor preparation does not bind the imported source hash.",
                details={"input_verification": dict(input_verification)},
            )
    return {
        "prep_id": response.get("prep_id"),
        "metadata": _snapshot(
            project_root,
            response.get("metadata_file"),
            "receptor preparation metadata",
        ),
        "prepared_receptor": prepared,
        "python_path": metadata.get("python_path"),
        "meeko_version": metadata.get("meeko_version"),
        "input_sha256": _sha256(source),
    }


def _score_rows(
    scores: Any,
    run_expected: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(scores, list):
        _fail(
            "MACROCYCLE_BACE1_SCORE_TABLE_INVALID",
            "DockStart returned no score-row array.",
        )
    normalized: list[dict[str, Any]] = []
    for position, row in enumerate(scores, start=1):
        if not isinstance(row, Mapping):
            _fail(
                "MACROCYCLE_BACE1_SCORE_TABLE_INVALID",
                "DockStart returned a non-object score row.",
            )
        mode_raw = row.get("mode", row.get("rank", row.get("mode_index")))
        score_raw = row.get(
            "affinity_kcal_mol",
            row.get("affinity", row.get("score")),
        )
        try:
            mode = int(mode_raw)
            score = float(score_raw)
        except (TypeError, ValueError, OverflowError):
            _fail(
                "MACROCYCLE_BACE1_SCORE_TABLE_INVALID",
                "A score row has no numeric mode and affinity.",
                details={"row": dict(row)},
            )
        if not math.isfinite(score):
            _fail(
                "MACROCYCLE_BACE1_SCORE_TABLE_INVALID",
                "A score row contains a non-finite affinity.",
                details={"row": dict(row)},
            )
        normalized.append(
            {
                "mode": mode,
                "affinity_kcal_mol": score,
                "raw": dict(row),
                "position": position,
            }
        )
    minimum = int(run_expected.get("minimum_mode_count") or 0)
    maximum = int(run_expected.get("maximum_mode_count") or 0)
    if not minimum <= len(normalized) <= maximum:
        _fail(
            "MACROCYCLE_BACE1_MODE_COUNT_INVALID",
            "The Vina output mode count is outside the pinned structural range.",
            details={
                "minimum": minimum,
                "maximum": maximum,
                "actual": len(normalized),
            },
        )
    actual_modes = [row["mode"] for row in normalized]
    if (
        run_expected.get("require_continuous_modes") is True
        and actual_modes != list(range(1, len(normalized) + 1))
    ):
        _fail(
            "MACROCYCLE_BACE1_MODE_SEQUENCE_INVALID",
            "The Vina output mode numbers are not continuous from one.",
            details={"actual": actual_modes},
        )
    return normalized


def _validate_formal_attribution(
    metadata: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    review_id: str,
    confirmation_sha256: str,
) -> dict[str, Any]:
    attribution = (
        metadata.get("ligand_preparation")
        if isinstance(metadata.get("ligand_preparation"), Mapping)
        else {}
    )
    oracle = _require_mapping(
        expected.get("reviewed_preparation"),
        "expected.reviewed_preparation",
    )
    review_oracle = _require_mapping(expected.get("review"), "expected.review")
    summary = (
        attribution.get("macrocycle_summary")
        if isinstance(attribution.get("macrocycle_summary"), Mapping)
        else {}
    )
    expected_values = {
        "matched": True,
        "integrity": oracle.get("run_attribution"),
        "formal_reviewed": True,
        "protocol": oracle.get("protocol"),
        "protocol_mode": oracle.get("protocol_mode"),
    }
    mismatches = {
        key: {"expected": value, "actual": attribution.get(key)}
        for key, value in expected_values.items()
        if attribution.get(key) != value
    }
    expected_summary = {
        "selection_mode": oracle.get("selection_mode"),
        "review_id": review_id,
        "candidate_id": review_oracle.get("recommended_candidate_id"),
        "exact_bonds_zero_based": oracle.get("exact_bonds"),
        "glue_pseudo_atom_count": oracle.get("glue_pseudo_atom_count"),
        "embedded_topology": True,
    }
    summary_mismatches = {
        key: {"expected": value, "actual": summary.get(key)}
        for key, value in expected_summary.items()
        if summary.get(key) != value
    }
    options = (
        attribution.get("options")
        if isinstance(attribution.get("options"), Mapping)
        else {}
    )
    requested = (
        options.get("macrocycle")
        if isinstance(options.get("macrocycle"), Mapping)
        else {}
    )
    if (
        requested.get("review_id") != review_id
        or requested.get("confirmation_sha256") != confirmation_sha256
        or requested.get("mode") != "reviewed"
    ):
        mismatches["options"] = {
            "expected": {
                "mode": "reviewed",
                "review_id": review_id,
                "confirmation_sha256": confirmation_sha256,
            },
            "actual": dict(requested),
        }
    if mismatches or summary_mismatches:
        _fail(
            "MACROCYCLE_BACE1_FORMAL_ATTRIBUTION_INVALID",
            "The frozen run is not attributed to the complete formal-reviewed chain.",
            details={
                "attribution_mismatches": mismatches,
                "summary_mismatches": summary_mismatches,
                "attribution": dict(attribution),
            },
        )
    return {
        "matched": attribution.get("matched"),
        "integrity": attribution.get("integrity"),
        "formal_reviewed": attribution.get("formal_reviewed"),
        "metadata_file": attribution.get("metadata_file"),
        "metadata_sha256": attribution.get("metadata_sha256"),
        "ligand_sha256": attribution.get("ligand_sha256"),
        "prep_id": attribution.get("prep_id"),
        "protocol": attribution.get("protocol"),
        "protocol_mode": attribution.get("protocol_mode"),
        "macrocycle_summary": dict(summary),
    }


def _run_analyze_and_report(
    project_root: Path,
    expected: Mapping[str, Any],
    *,
    review_id: str,
    confirmation_sha256: str,
    vina_path: Path,
) -> dict[str, Any]:
    generated = _require_ok(
        "generate_vina_config",
        generate_vina_config(str(project_root)),
    )
    config = _snapshot(
        project_root,
        generated.get("config_file"),
        "generated Vina configuration",
    )
    prepared = _require_ok(
        "prepare_vina_run",
        prepare_vina_run(str(project_root)),
    )
    run_id = str(prepared.get("run_id") or "")
    if not re.fullmatch(r"run_\d{3}", run_id):
        _fail(
            "MACROCYCLE_BACE1_RUN_ID_INVALID",
            "DockStart returned an invalid run identifier.",
            details={"run_id": run_id},
        )
    prepared_metadata = (
        prepared.get("metadata")
        if isinstance(prepared.get("metadata"), Mapping)
        else {}
    )
    prepared_attribution = _validate_formal_attribution(
        prepared_metadata,
        expected,
        review_id=review_id,
        confirmation_sha256=confirmation_sha256,
    )
    executed = _require_ok(
        "execute_prepared_vina_run",
        execute_prepared_vina_run(str(project_root), run_id),
    )
    metadata = (
        executed.get("metadata")
        if isinstance(executed.get("metadata"), Mapping)
        else {}
    )
    if metadata.get("status") != "finished":
        _fail(
            "MACROCYCLE_BACE1_VINA_RUN_NOT_FINISHED",
            "The real Vina run did not finish.",
            details={"metadata": dict(metadata)},
        )
    if _normalized_path(str(metadata.get("vina_path") or "")) != _normalized_path(
        vina_path
    ):
        _fail(
            "MACROCYCLE_BACE1_VINA_RUNTIME_PATH_MISMATCH",
            "The frozen run did not use the supplied Vina executable.",
            details={
                "expected": str(vina_path),
                "actual": metadata.get("vina_path"),
            },
        )
    attribution = _validate_formal_attribution(
        metadata,
        expected,
        review_id=review_id,
        confirmation_sha256=confirmation_sha256,
    )
    analyzed = _require_ok(
        "analyze_vina_run_results",
        analyze_vina_run_results(str(project_root), run_id),
    )
    run_expected = _require_mapping(expected.get("run"), "expected.run")
    scores = _score_rows(analyzed.get("scores"), run_expected)
    analyzed_metadata = (
        analyzed.get("metadata")
        if isinstance(analyzed.get("metadata"), Mapping)
        else {}
    )
    analyzed_attribution = _validate_formal_attribution(
        analyzed_metadata,
        expected,
        review_id=review_id,
        confirmation_sha256=confirmation_sha256,
    )
    output_relative = str(
        analyzed_metadata.get("output_file")
        or metadata.get("output_file")
        or ""
    )
    output = _snapshot(
        project_root,
        output_relative,
        "real Vina output PDBQT",
    )
    scores_file = _snapshot(
        project_root,
        analyzed.get("scores_file"),
        "parsed Vina scores CSV",
    )
    report_response = _require_ok(
        "export_markdown_report",
        export_markdown_report(str(project_root), run_id),
    )
    report_path = _project_artifact(
        project_root,
        report_response.get("report_file"),
        "DockStart Markdown report",
    )
    report_text = report_path.read_text(encoding="utf-8", errors="strict")
    missing = [
        str(phrase)
        for phrase in run_expected.get("report_required_ascii_phrases") or []
        if str(phrase) not in report_text
    ]
    if missing:
        _fail(
            "MACROCYCLE_BACE1_REPORT_EVIDENCE_MISSING",
            "The Markdown report omits required macrocycle evidence.",
            details={"missing": missing},
        )
    metadata_relative = (
        analyzed_metadata.get("metadata_file")
        or metadata.get("metadata_file")
        or Path("runs", run_id, "metadata.json").as_posix()
    )
    metadata_snapshot = _snapshot(
        project_root,
        metadata_relative,
        "finished run metadata",
    )
    ligand_preparation_snapshot = _snapshot(
        project_root,
        analyzed_metadata.get("ligand_preparation_snapshot")
        or metadata.get("ligand_preparation_snapshot"),
        "frozen ligand-preparation attribution",
    )
    input_snapshots: dict[str, Any] = {}
    raw_snapshots = (
        analyzed_metadata.get("snapshots")
        if isinstance(analyzed_metadata.get("snapshots"), Mapping)
        else metadata.get("snapshots")
        if isinstance(metadata.get("snapshots"), Mapping)
        else {}
    )
    for key in ("receptor", "ligand"):
        record = (
            raw_snapshots.get(key)
            if isinstance(raw_snapshots.get(key), Mapping)
            else {}
        )
        relative = record.get("relative_path") or record.get("path")
        if relative:
            input_snapshots[key] = _snapshot(
                project_root,
                relative,
                f"frozen run {key} input",
            )
    return {
        "run_id": run_id,
        "status": metadata.get("status"),
        "mode_count": len(scores),
        "modes": [row["mode"] for row in scores],
        "affinities_kcal_mol": [
            row["affinity_kcal_mol"] for row in scores
        ],
        "best_affinity_kcal_mol": min(
            row["affinity_kcal_mol"] for row in scores
        ),
        "config": config,
        "output": output,
        "scores_csv": scores_file,
        "metadata": metadata_snapshot,
        "report": {
            "relative_path": report_response.get("report_file"),
            "size_bytes": report_path.stat().st_size,
            "sha256": _sha256(report_path),
            "required_ascii_phrases": list(
                run_expected.get("report_required_ascii_phrases") or []
            ),
        },
        "input_snapshots": input_snapshots,
        "ligand_preparation_snapshot": ligand_preparation_snapshot,
        "prepared_attribution": prepared_attribution,
        "finished_attribution": attribution,
        "analyzed_attribution": analyzed_attribution,
    }


RDKIT_TOPOLOGY_WORKER = r"""
import hashlib
import json
import sys
from pathlib import Path
from rdkit import Chem, rdBase

source_path = Path(sys.argv[1]).resolve()
poses_path = Path(sys.argv[2]).resolve()

def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def facts(molecule):
    if molecule is None:
        raise ValueError("RDKit returned an empty molecule record")
    Chem.SanitizeMol(molecule)
    atomic_numbers = [atom.GetAtomicNum() for atom in molecule.GetAtoms()]
    symbols = [atom.GetSymbol() for atom in molecule.GetAtoms()]
    heavy_bonds = sum(
        1
        for bond in molecule.GetBonds()
        if bond.GetBeginAtom().GetAtomicNum() > 1
        and bond.GetEndAtom().GetAtomicNum() > 1
    )
    heavy = Chem.RemoveHs(molecule)
    smiles = Chem.MolToSmiles(
        heavy,
        canonical=True,
        isomericSmiles=True,
    )
    return {
        "atom_count": molecule.GetNumAtoms(),
        "heavy_atom_count": molecule.GetNumHeavyAtoms(),
        "heavy_bond_count": heavy_bonds,
        "total_formal_charge": sum(
            atom.GetFormalCharge() for atom in molecule.GetAtoms()
        ),
        "canonical_isomeric_smiles": smiles,
        "canonical_isomeric_smiles_sha256": sha256_text(smiles),
        "forbidden_atomic_numbers_present": sorted(
            {number for number in atomic_numbers if number == 0}
        ),
        "pseudo_atom_symbols_present": sorted(
            {symbol for symbol in symbols if symbol.upper().startswith("G")}
        ),
    }

source_supplier = Chem.SDMolSupplier(
    str(source_path),
    removeHs=False,
    sanitize=True,
    strictParsing=True,
)
source_records = list(source_supplier)
if len(source_records) != 1 or source_records[0] is None:
    raise ValueError(
        "The pinned source SDF must contain exactly one valid molecule"
    )

pose_supplier = Chem.SDMolSupplier(
    str(poses_path),
    removeHs=False,
    sanitize=True,
    strictParsing=True,
)
pose_records = list(pose_supplier)
if not pose_records or any(molecule is None for molecule in pose_records):
    raise ValueError("Every exported SDF record must be RDKit-readable")

payload = {
    "ok": True,
    "rdkit_version": rdBase.rdkitVersion,
    "source": facts(source_records[0]),
    "pose_count": len(pose_records),
    "poses": [
        {"pose_index": index, **facts(molecule)}
        for index, molecule in enumerate(pose_records, start=1)
    ],
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""


def _validate_exported_pose_topology(
    payload: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    expected_mode_count: int,
) -> dict[str, Any]:
    topology = _require_mapping(
        expected.get("exported_pose_topology"),
        "expected.exported_pose_topology",
    )
    toolchain = _require_mapping(expected.get("toolchain"), "expected.toolchain")
    source = _require_mapping(payload.get("source"), "RDKit source facts")
    poses = _require_list(payload.get("poses"), "RDKit pose facts")
    common_expected = {
        "heavy_atom_count": topology.get("heavy_atom_count"),
        "heavy_bond_count": topology.get("heavy_bond_count"),
        "total_formal_charge": topology.get("total_formal_charge"),
        "canonical_isomeric_smiles_sha256": topology.get(
            "canonical_isomeric_smiles_sha256"
        ),
        "forbidden_atomic_numbers_present": [],
        "pseudo_atom_symbols_present": [],
    }
    mismatches: list[dict[str, Any]] = []
    if payload.get("ok") is not True:
        mismatches.append({"field": "ok", "actual": payload.get("ok")})
    if payload.get("rdkit_version") != toolchain.get("rdkit"):
        mismatches.append(
            {
                "field": "rdkit_version",
                "expected": toolchain.get("rdkit"),
                "actual": payload.get("rdkit_version"),
            }
        )
    if payload.get("pose_count") != expected_mode_count or len(poses) != expected_mode_count:
        mismatches.append(
            {
                "field": "pose_count",
                "expected": expected_mode_count,
                "actual": payload.get("pose_count"),
                "records": len(poses),
            }
        )
    if source.get("atom_count") != topology.get("source_atom_count"):
        mismatches.append(
            {
                "record": "source",
                "field": "atom_count",
                "expected": topology.get("source_atom_count"),
                "actual": source.get("atom_count"),
            }
        )
    for field, value in common_expected.items():
        if source.get(field) != value:
            mismatches.append(
                {
                    "record": "source",
                    "field": field,
                    "expected": value,
                    "actual": source.get(field),
                }
            )
    for index, item in enumerate(poses, start=1):
        if not isinstance(item, Mapping):
            mismatches.append(
                {"record": index, "field": "record_type", "actual": type(item).__name__}
            )
            continue
        if item.get("pose_index") != index:
            mismatches.append(
                {
                    "record": index,
                    "field": "pose_index",
                    "expected": index,
                    "actual": item.get("pose_index"),
                }
            )
        for field, value in common_expected.items():
            if item.get(field) != value:
                mismatches.append(
                    {
                        "record": index,
                        "field": field,
                        "expected": value,
                        "actual": item.get(field),
                    }
                )
    if mismatches:
        _fail(
            "MACROCYCLE_BACE1_EXPORTED_TOPOLOGY_MISMATCH",
            "Independent RDKit re-read found a topology, charge, or pseudo-atom mismatch.",
            details={"mismatches": mismatches},
        )
    return {
        "rdkit_version": payload.get("rdkit_version"),
        "source": dict(source),
        "pose_count": len(poses),
        "poses": [dict(item) for item in poses if isinstance(item, Mapping)],
        "all_records_rdkit_readable": True,
        "all_records_without_g_pseudo_atoms": True,
        "all_records_preserve_heavy_topology_charge_and_smiles": True,
    }


def _independent_rdkit_reread(
    python_path: Path,
    source_sdf: Path,
    output_sdf: Path,
    expected: Mapping[str, Any],
    *,
    expected_mode_count: int,
) -> dict[str, Any]:
    command = [
        str(python_path),
        "-I",
        "-B",
        "-c",
        RDKIT_TOPOLOGY_WORKER,
        str(source_sdf),
        str(output_sdf),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=120,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - stable verifier error.
        _fail(
            "MACROCYCLE_BACE1_RDKIT_REREAD_FAILED",
            "The independent RDKit topology process could not be executed.",
            details={"error": str(exc)},
        )
    if completed.returncode != 0:
        _fail(
            "MACROCYCLE_BACE1_RDKIT_REREAD_FAILED",
            "The independent RDKit topology process failed.",
            details={
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        _fail(
            "MACROCYCLE_BACE1_RDKIT_REREAD_JSON_INVALID",
            "The independent RDKit topology process returned invalid JSON.",
            details={
                "error": str(exc),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
    if not isinstance(payload, Mapping):
        _fail(
            "MACROCYCLE_BACE1_RDKIT_REREAD_JSON_INVALID",
            "The independent RDKit topology process did not return an object.",
        )
    validated = _validate_exported_pose_topology(
        payload,
        expected,
        expected_mode_count=expected_mode_count,
    )
    return {
        **validated,
        "command": [
            str(python_path),
            "-I",
            "-B",
            "-c",
            "<independent-rdkit-topology-worker>",
            str(source_sdf),
            str(output_sdf),
        ],
        "worker_sha256": _sha256_text(RDKIT_TOPOLOGY_WORKER),
        "exit_code": completed.returncode,
        "stdout_sha256": _sha256_text(completed.stdout),
        "stderr_sha256": _sha256_text(completed.stderr),
        "stderr": completed.stderr,
    }


def _validate_export_command_record(
    command_record: Mapping[str, Any],
    python_path: Path,
    input_path: Path,
) -> list[str]:
    requested = command_record.get("requested_command")
    if (
        command_record.get("status") != "success"
        or command_record.get("exit_code") != 0
        or not isinstance(requested, list)
        or not requested
        or _normalized_path(str(requested[0])) != _normalized_path(python_path)
        or "meeko.cli.mk_export" not in requested
        or str(input_path) not in requested
    ):
        _fail(
            "MACROCYCLE_BACE1_EXPORT_COMMAND_INVALID",
            "The audited mk_export command is incomplete or used another runtime.",
            details={"command_record": dict(command_record)},
        )
    return [str(value) for value in requested]


def _export_and_verify_sdf(
    project_root: Path,
    run_evidence: Mapping[str, Any],
    source_sdf: Path,
    python_path: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    run_id = str(run_evidence.get("run_id") or "")
    status = _require_ok(
        "get_result_export_status",
        get_result_export_status(str(project_root), run_id),
    )
    inspection = (
        status.get("inspection")
        if isinstance(status.get("inspection"), Mapping)
        else {}
    )
    if (
        status.get("ready") is not True
        or inspection.get("embedded_topology") is not True
    ):
        _fail(
            "MACROCYCLE_BACE1_RESULT_EXPORT_NOT_READY",
            "The finished result lacks embedded topology for safe SDF export.",
            details={"status": dict(status)},
        )
    exported = _require_ok(
        "export_result_sdf",
        export_result_sdf(str(project_root), run_id),
    )
    record = _require_mapping(exported.get("export"), "SDF export record")
    input_path = _project_artifact(
        project_root,
        record.get("input_file"),
        "SDF export input PDBQT",
    )
    output_path = _project_artifact(
        project_root,
        record.get("output_file"),
        "exported poses SDF",
    )
    if (
        record.get("protocol") != "meeko_result_export"
        or _sha256(input_path) != record.get("input_sha256")
        or _sha256(output_path) != record.get("output_sha256")
        or _normalized_path(str(record.get("python_path") or ""))
        != _normalized_path(python_path)
    ):
        _fail(
            "MACROCYCLE_BACE1_EXPORT_RECORD_INVALID",
            "The real SDF export record does not bind its input, output, or Python.",
            details={"record": dict(record)},
        )
    command_record = _read_json_artifact(
        project_root,
        record.get("execution_record"),
        "mk_export command record",
    )
    requested = _validate_export_command_record(
        command_record,
        python_path,
        input_path,
    )
    topology = _independent_rdkit_reread(
        python_path,
        source_sdf,
        output_path,
        expected,
        expected_mode_count=int(run_evidence.get("mode_count") or 0),
    )
    return {
        "export_id": record.get("export_id"),
        "input": {
            "relative_path": record.get("input_file"),
            "size_bytes": input_path.stat().st_size,
            "sha256": _sha256(input_path),
        },
        "output": {
            "relative_path": record.get("output_file"),
            "size_bytes": output_path.stat().st_size,
            "sha256": _sha256(output_path),
        },
        "python_path": record.get("python_path"),
        "execution_record": _snapshot(
            project_root,
            record.get("execution_record"),
            "mk_export command record",
        ),
        "requested_command": requested,
        "topology_evidence": record.get("topology_evidence"),
        "independent_rdkit_reread": topology,
    }


def verify_macrocycle_bace1(
    *,
    fixture_root: str | Path = FIXTURE_ROOT,
    python_executable: str | Path = DEFAULT_PYTHON,
    vina_executable: str | Path = DEFAULT_VINA,
) -> dict[str, Any]:
    """Execute the complete fresh-project BACE_1 acceptance workflow."""

    manifest = _load_manifest()
    expected = _require_mapping(manifest.get("expected"), "expected")
    fixture_path = Path(fixture_root).expanduser().resolve()
    python_path = _regular_file(python_executable, "configured Python")
    vina_path = _regular_file(vina_executable, "configured AutoDock Vina")
    steps: dict[str, Any] = {}
    temporary_root = Path(
        tempfile.mkdtemp(prefix="dockstart_macrocycle_bace1_")
    ).resolve()
    settings_path = temporary_root / "settings.json"
    empty_resources = temporary_root / "empty_resources"
    empty_resources.mkdir()
    original_settings = os.environ.get(SETTINGS_ENV_VAR)
    original_resources = os.environ.get(RESOURCE_DIR_ENV_VAR)
    cleanup_error = ""
    failure: MacrocycleAcceptanceError | None = None
    result: dict[str, Any] | None = None

    try:
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(empty_resources)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    vina=str(vina_path),
                    python=str(python_path),
                )
            )
        )

        source_evidence = _execute_step(
            steps,
            "fixed_input_identity",
            lambda: _verify_fixture_inputs(fixture_path, manifest),
        )
        sources = source_evidence["files"]
        receptor_source = Path(
            sources["BACE_1_receptorH.pdb"]["path"]
        )
        ligand_source = Path(
            sources["BACE_1_ligand.sdf"]["path"]
        )

        project_parent = temporary_root / "projects"
        project_parent.mkdir()
        created = _require_ok(
            "create_project",
            create_project(
                "macrocycle_bace1_acceptance",
                str(project_parent),
            ),
        )
        project_root = Path(str(created.get("project_dir") or "")).resolve()
        try:
            project_root.relative_to(project_parent.resolve())
        except ValueError:
            _fail(
                "MACROCYCLE_BACE1_PROJECT_OUTSIDE_TEMP",
                "DockStart created the project outside the verifier root.",
                details={"project_dir": str(project_root)},
            )

        tool_evidence = _execute_step(
            steps,
            "configured_toolchain",
            lambda: _verify_tools(
                project_root,
                python_path,
                vina_path,
                expected,
            ),
        )

        def import_inputs() -> Mapping[str, Any]:
            receptor_import = _require_ok(
                "import_receptor_raw_file",
                import_receptor_raw_file(
                    str(project_root),
                    str(receptor_source),
                ),
            )
            ligand_import = _require_ok(
                "import_ligand_raw_file",
                import_ligand_raw_file(
                    str(project_root),
                    str(ligand_source),
                ),
            )
            return {
                "project_dir": str(project_root),
                "receptor": _verify_imported_raw(
                    project_root,
                    receptor_import,
                    receptor_source,
                    "receptor",
                ),
                "ligand": _verify_imported_raw(
                    project_root,
                    ligand_import,
                    ligand_source,
                    "ligand",
                ),
            }

        imported = _execute_step(
            steps,
            "public_project_create_and_raw_import",
            import_inputs,
        )

        standard_rejection = _execute_step(
            steps,
            "standard_preparation_fail_closed",
            lambda: _validate_standard_rejection(
                prepare_ligand_pdbqt(str(project_root)),
                project_root,
                expected,
            ),
        )

        review_state: dict[str, Any] = {}

        def review_step() -> Mapping[str, Any]:
            reviewed = _require_ok(
                "create_review",
                create_review(str(project_root)),
            )
            if (
                reviewed.get("state") != "reviewed"
                or reviewed.get("can_prepare") is not False
                or reviewed.get("integrity", {}).get("ok") is not True
            ):
                _fail(
                    "MACROCYCLE_BACE1_REVIEW_STATE_INVALID",
                    "Fresh candidate analysis did not enter the reviewed state.",
                    details={"response": dict(reviewed)},
                )
            review = _require_mapping(reviewed.get("review"), "review")
            evidence = _validate_review(review, expected)
            review_record = _snapshot(
                project_root,
                evidence["record"]["relative_path"],
                "immutable macrocycle review record",
            )
            review_input = _snapshot(
                project_root,
                evidence["input_snapshot"]["relative_path"],
                "immutable macrocycle review input",
            )
            if (
                review_record["sha256"] != evidence["record"]["sha256"]
                or review_record["size_bytes"]
                != evidence["record"]["size_bytes"]
                or review_input["sha256"]
                != evidence["input_snapshot"]["sha256"]
                or review_input["size_bytes"]
                != evidence["input_snapshot"]["size_bytes"]
            ):
                _fail(
                    "MACROCYCLE_BACE1_REVIEW_RECORD_HASH_MISMATCH",
                    "Review pointers do not match their immutable files.",
                )
            review_state["payload"] = dict(review)
            review_state["record"] = review_record
            review_state["input"] = review_input
            return {
                **evidence,
                "record": review_record,
                "input_snapshot": review_input,
            }

        review_evidence = _execute_step(
            steps,
            "fresh_seven_candidate_review",
            review_step,
        )

        confirmation_state: dict[str, Any] = {}

        def confirmation_step() -> Mapping[str, Any]:
            review = review_state["payload"]
            confirmed = _require_ok(
                "confirm_selection",
                confirm_selection(
                    str(project_root),
                    str(review["review_id"]),
                    str(review["recommended_candidate_id"]),
                ),
            )
            if (
                confirmed.get("state") != "confirmed"
                or confirmed.get("can_prepare") is not True
                or confirmed.get("integrity", {}).get("ok") is not True
            ):
                _fail(
                    "MACROCYCLE_BACE1_CONFIRMATION_STATE_INVALID",
                    "The recommended candidate did not enter a confirmed state.",
                    details={"response": dict(confirmed)},
                )
            confirmation = _require_mapping(
                confirmed.get("confirmation"),
                "confirmation",
            )
            evidence = _validate_confirmation(
                confirmation,
                review,
                expected,
            )
            record = _snapshot(
                project_root,
                evidence["record"]["relative_path"],
                "immutable macrocycle confirmation record",
            )
            if (
                record["sha256"] != evidence["record"]["sha256"]
                or record["size_bytes"] != evidence["record"]["size_bytes"]
            ):
                _fail(
                    "MACROCYCLE_BACE1_CONFIRMATION_RECORD_HASH_MISMATCH",
                    "Confirmation pointer does not match its immutable file.",
                )
            confirmation_state["payload"] = dict(confirmation)
            confirmation_state["record"] = record
            return {**evidence, "record": record}

        confirmation_evidence = _execute_step(
            steps,
            "recommended_candidate_confirmation",
            confirmation_step,
        )

        reviewed_preparation_state: dict[str, Any] = {}

        def reviewed_preparation_step() -> Mapping[str, Any]:
            review = review_state["payload"]
            confirmation = confirmation_state["payload"]
            confirmation_sha256 = str(
                confirmation["record"]["sha256"]
            )
            prepared = prepare_ligand_pdbqt(
                str(project_root),
                options={
                    "protocol": "meeko_macrocycle",
                    "macrocycle": {
                        "mode": "reviewed",
                        "review_id": str(review["review_id"]),
                        "confirmation_sha256": confirmation_sha256,
                    },
                },
            )
            if prepared.get("ok") is not True:
                _fail(
                    "MACROCYCLE_BACE1_REVIEWED_PREPARATION_FAILED",
                    "The real Meeko reviewed-macrocycle preparation failed.",
                    details={"response": prepared},
                )
            evidence = _validate_reviewed_preparation_evidence(
                project_root,
                prepared,
                expected,
                review_id=str(review["review_id"]),
                confirmation_sha256=confirmation_sha256,
            )
            reviewed_preparation_state.update(evidence)
            return evidence

        reviewed_preparation = _execute_step(
            steps,
            "real_meeko_reviewed_macrocycle_preparation",
            reviewed_preparation_step,
        )

        receptor_preparation = _execute_step(
            steps,
            "real_meeko_receptor_preparation",
            lambda: _validate_receptor_preparation(
                project_root,
                prepare_receptor_pdbqt(str(project_root)),
                receptor_source,
                python_path,
            ),
        )

        def parameter_step() -> Mapping[str, Any]:
            box = _require_mapping(
                manifest.get("official_vina_box"),
                "official_vina_box",
            )
            vina = _require_mapping(
                manifest.get("full_acceptance_parameters"),
                "full_acceptance_parameters",
            )
            saved_box = _require_ok(
                "update_box_params",
                update_box_params(str(project_root), dict(box)),
            )
            saved_vina = _require_ok(
                "update_vina_params",
                update_vina_params(str(project_root), dict(vina)),
            )
            project = (
                saved_vina.get("project")
                if isinstance(saved_vina.get("project"), Mapping)
                else {}
            )
            if (
                project.get("box") != dict(box)
                or any(
                    project.get("vina", {}).get(key) != value
                    for key, value in vina.items()
                )
            ):
                _fail(
                    "MACROCYCLE_BACE1_PARAMETERS_NOT_PERSISTED",
                    "The official box or full acceptance parameters were not saved exactly.",
                    details={"project": dict(project)},
                )
            return {
                "box": dict(box),
                "vina": dict(vina),
                "project_revision": project.get("revision"),
                "box_response_ok": saved_box.get("ok"),
            }

        parameters = _execute_step(
            steps,
            "official_box_and_full_acceptance_parameters",
            parameter_step,
        )

        review_id = str(review_state["payload"]["review_id"])
        confirmation_sha256 = str(
            confirmation_state["payload"]["record"]["sha256"]
        )
        run_evidence = _execute_step(
            steps,
            "real_vina_run_results_and_report",
            lambda: _run_analyze_and_report(
                project_root,
                expected,
                review_id=review_id,
                confirmation_sha256=confirmation_sha256,
                vina_path=vina_path,
            ),
        )
        export_evidence = _execute_step(
            steps,
            "real_mk_export_and_independent_rdkit_reread",
            lambda: _export_and_verify_sdf(
                project_root,
                run_evidence,
                ligand_source,
                python_path,
                expected,
            ),
        )

        immutable_records = {
            "review_record": review_state["record"],
            "review_input": review_state["input"],
            "confirmation_record": confirmation_state["record"],
            "macrocycle_contract": reviewed_preparation_state["contract"],
            "macrocycle_worker_evidence": reviewed_preparation_state[
                "worker_evidence"
            ],
            "macrocycle_frozen_input": reviewed_preparation_state[
                "frozen_input"
            ],
            "prepared_ligand": reviewed_preparation_state["prepared_ligand"],
            "imported_receptor": imported["receptor"],
            "imported_ligand": imported["ligand"],
        }

        def final_integrity_step() -> Mapping[str, Any]:
            checked = {
                label: _verify_snapshot_unchanged(
                    project_root,
                    record,
                    label.replace("_", " "),
                )
                for label, record in immutable_records.items()
            }
            output_record = run_evidence["output"]
            checked["vina_output_after_export"] = _verify_snapshot_unchanged(
                project_root,
                output_record,
                "Vina output after SDF export",
            )
            return {
                "all_immutable_records_unchanged": True,
                "records": checked,
            }

        final_integrity = _execute_step(
            steps,
            "post_export_immutable_evidence_recheck",
            final_integrity_step,
        )

        scope = _require_mapping(
            manifest.get("scientific_scope"),
            "scientific_scope",
        )
        result = {
            "ok": True,
            "verifier": "macrocycle_bace1",
            "manifest_schema_version": manifest.get("schema_version"),
            "fixture_id": manifest.get("fixture_id"),
            "scientific_scope": {
                **dict(scope),
                "pose_recovery_scientific_success_claimed": False,
                "pose_recovery_evaluated": False,
            },
            "historical_temp_outputs_trusted": False,
            "fresh_project_public_api_only": True,
            "project_api": {
                "project_created": True,
                "standard_preparation_rejected": True,
                "reviewed_preparation_finished": True,
                "receptor_preparation_finished": True,
                "vina_run_finished": True,
                "markdown_report_exported": True,
                "result_sdf_exported": True,
            },
            "toolchain": tool_evidence,
            "inputs": source_evidence,
            "standard_rejection": standard_rejection,
            "review": review_evidence,
            "confirmation": confirmation_evidence,
            "reviewed_preparation": reviewed_preparation,
            "receptor_preparation": receptor_preparation,
            "parameters": parameters,
            "run": run_evidence,
            "result_sdf_export": export_evidence,
            "final_integrity": final_integrity,
            "steps": copy.deepcopy(steps),
            "temporary_project": {
                "path": str(project_root),
                "retained": False,
            },
        }
    except MacrocycleAcceptanceError as exc:
        failure = exc
    except Exception as exc:  # noqa: BLE001 - stable top-level boundary.
        failure = MacrocycleAcceptanceError(
            "MACROCYCLE_BACE1_UNEXPECTED_FAILURE",
            "The BACE_1 verifier failed unexpectedly.",
            details={"error": str(exc), "type": type(exc).__name__},
        )
        failure.steps = copy.deepcopy(steps)
    finally:
        if original_settings is None:
            os.environ.pop(SETTINGS_ENV_VAR, None)
        else:
            os.environ[SETTINGS_ENV_VAR] = original_settings
        if original_resources is None:
            os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
        else:
            os.environ[RESOURCE_DIR_ENV_VAR] = original_resources
        try:
            shutil.rmtree(temporary_root)
        except OSError as exc:
            cleanup_error = str(exc)

    cleanup = {
        "path": str(temporary_root),
        "cleanup_called": True,
        "removed": not temporary_root.exists(),
        "error": cleanup_error,
    }
    environment = {
        "settings_restored": os.environ.get(SETTINGS_ENV_VAR)
        == original_settings,
        "resource_dir_restored": os.environ.get(RESOURCE_DIR_ENV_VAR)
        == original_resources,
    }
    if cleanup_error or not cleanup["removed"]:
        if failure is None:
            failure = MacrocycleAcceptanceError(
                "MACROCYCLE_BACE1_TEMP_CLEANUP_FAILED",
                "The verifier completed but could not remove its temporary project.",
                details={"temporary_cleanup": cleanup},
            )
            failure.steps = copy.deepcopy(steps)
    if failure is not None:
        failure.details = {
            **failure.details,
            "temporary_cleanup": cleanup,
            "environment_restoration": environment,
        }
        if not failure.steps:
            failure.steps = copy.deepcopy(steps)
        raise failure
    assert result is not None
    result["temporary_cleanup"] = cleanup
    result["environment_restoration"] = environment
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild the pinned BACE_1 macrocycle workflow through DockStart's "
            "public project APIs and emit fail-closed JSON evidence."
        )
    )
    parser.add_argument(
        "--fixture-root",
        default=str(FIXTURE_ROOT),
        help="Directory containing the manifest-pinned BACE_1 files.",
    )
    parser.add_argument(
        "--python",
        default=str(DEFAULT_PYTHON),
        help="Python executable containing the pinned RDKit/Meeko toolchain.",
    )
    parser.add_argument(
        "--vina",
        default=str(DEFAULT_VINA),
        help="AutoDock Vina executable used for the real run.",
    )
    parser.add_argument(
        "--output",
        help="Optional UTF-8 JSON evidence file. Existing files are not overwritten.",
    )
    return parser


def _write_result(path_value: str, payload: Mapping[str, Any]) -> Path:
    path = Path(path_value).expanduser().resolve()
    if path.exists():
        _fail(
            "MACROCYCLE_BACE1_OUTPUT_EXISTS",
            "The requested evidence JSON already exists.",
            details={"path": str(path)},
        )
    if not path.parent.is_dir():
        _fail(
            "MACROCYCLE_BACE1_OUTPUT_PARENT_MISSING",
            "The evidence JSON parent directory does not exist.",
            details={"path": str(path)},
        )
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    path.write_text(serialized, encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_macrocycle_bace1(
            fixture_root=args.fixture_root,
            python_executable=args.python,
            vina_executable=args.vina,
        )
        if args.output:
            evidence_path = _write_result(args.output, result)
            result = {
                **result,
                "evidence_json": {
                    "path": str(evidence_path),
                    "size_bytes": evidence_path.stat().st_size,
                    "sha256": _sha256(evidence_path),
                },
            }
        payload: Mapping[str, Any] = result
        exit_code = 0
    except MacrocycleAcceptanceError as exc:
        payload = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "steps": exc.steps,
        }
        exit_code = 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
