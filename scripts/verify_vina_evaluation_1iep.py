"""Run the external 1IEP evaluation-mode acceptance gate.

The caller supplies the two manifest-pinned PDBQT files published with
AutoDock Vina v1.2.7.  The verifier then uses DockStart's public project APIs
to execute a real seeded global run with non-default advanced parameters, a
real score-only run, and a real modern two-stage local-only run with the
selected Vina executable. Missing files, identity drift, tool drift, process
failures, and incomplete capability evidence are all blocking.

This script never downloads scientific inputs, fabricates process output, or
silently skips a run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
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
    update_vina_run_protocol,
    validate_vina_runtime_capabilities,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    save_settings,
)
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR  # noqa: E402

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "vina_evaluation_1iep"
    / "source_manifest.json"
)
DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])


class VinaEvaluationAcceptanceError(RuntimeError):
    """Stable JSON error boundary for the external acceptance gate."""

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
    raise VinaEvaluationAcceptanceError(code, message, details=details)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        _fail(
            "VINA_EVALUATION_REQUIRED_FILE_MISSING",
            f"{label} is missing or is not a regular file.",
            details={"label": label, "path": str(path)},
        )
    return path


def _load_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The 1IEP evaluation manifest cannot be read.",
            details={"path": str(MANIFEST_PATH), "error": str(exc)},
        )
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("fixture_id") != "vina_evaluation_1iep_external"
    ):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The 1IEP evaluation manifest header is invalid.",
        )
    return payload


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        _fail(
            "VINA_EVALUATION_ORACLE_MISMATCH",
            f"{label} does not match the manifest-pinned value.",
            details={"label": label, "expected": expected, "actual": actual},
        )


def _assert_close(
    actual: Any,
    expected: Any,
    tolerance: Any,
    label: str,
) -> float:
    try:
        actual_number = float(actual)
        expected_number = float(expected)
        tolerance_number = float(tolerance)
    except (TypeError, ValueError, OverflowError):
        _fail(
            "VINA_EVALUATION_NUMERIC_RESULT_INVALID",
            f"{label} is not numeric.",
            details={
                "label": label,
                "expected": expected,
                "actual": actual,
                "tolerance": tolerance,
            },
        )
    if (
        not math.isfinite(actual_number)
        or not math.isfinite(expected_number)
        or not math.isfinite(tolerance_number)
        or tolerance_number < 0
        or abs(actual_number - expected_number) > tolerance_number
    ):
        _fail(
            "VINA_EVALUATION_NUMERIC_RESULT_MISMATCH",
            f"{label} is outside the manifest-pinned tolerance.",
            details={
                "label": label,
                "expected": expected_number,
                "actual": actual_number,
                "tolerance": tolerance_number,
            },
        )
    return actual_number


def _require_ok(
    payload: Mapping[str, Any],
    code: str,
    message: str,
) -> Mapping[str, Any]:
    if payload.get("ok") is not True:
        _fail(
            code,
            message,
            details={"response": dict(payload)},
        )
    return payload


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        evidence = action()
    except VinaEvaluationAcceptanceError as exc:
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
    steps[name] = {"ok": True, "evidence": dict(evidence)}
    return evidence


def _identity_evidence(
    path: Path,
    expected: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    size_bytes = path.stat().st_size
    sha256 = _sha256(path)
    _assert_equal(size_bytes, expected.get("size_bytes"), f"{label} size")
    _assert_equal(sha256, expected.get("sha256"), f"{label} SHA256")
    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "upstream_project": expected.get("upstream_project"),
        "upstream_tag": expected.get("upstream_tag"),
        "upstream_path": expected.get("upstream_path"),
        "url": expected.get("url"),
    }


def _verify_sources(
    receptor_path: Path,
    ligand_path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The manifest source identities are missing.",
        )
    receptor_expected = sources.get("receptor")
    ligand_expected = sources.get("ligand")
    if not isinstance(receptor_expected, Mapping) or not isinstance(
        ligand_expected,
        Mapping,
    ):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The receptor or ligand identity is invalid.",
        )
    return {
        "receptor": _identity_evidence(
            receptor_path,
            receptor_expected,
            "1IEP receptor",
        ),
        "ligand": _identity_evidence(
            ligand_path,
            ligand_expected,
            "1IEP ligand",
        ),
    }


def _capability_gate_evidence(
    capabilities: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    gate = manifest.get("capability_gate")
    if not isinstance(gate, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The capability-gate oracle is missing.",
        )
    requested = gate.get("requested_features")
    if not isinstance(requested, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The requested capability set is invalid.",
        )

    accepted = validate_vina_runtime_capabilities(
        dict(requested),
        "vina",
        dict(capabilities),
        run_mode="score_only",
        autobox=True,
    )
    if accepted.get("ok") is not True:
        _fail(
            "VINA_EVALUATION_REAL_CAPABILITY_REJECTED",
            "The real Vina capability evidence rejected the pinned request.",
            details={"response": accepted},
        )

    rejected = validate_vina_runtime_capabilities(
        dict(requested),
        "vina",
        {},
        run_mode="score_only",
        autobox=True,
    )
    if rejected.get("ok") is not False:
        _fail(
            "VINA_EVALUATION_CAPABILITY_GATE_FAILED_OPEN",
            "The expert-option gate accepted a request without capability evidence.",
            details={"response": rejected},
        )
    error = rejected.get("error")
    error_code = error.get("code") if isinstance(error, Mapping) else None
    _assert_equal(
        error_code,
        gate.get("negative_error_code"),
        "missing-evidence capability error code",
    )

    features = capabilities.get("features")
    feature_evidence: dict[str, Any] = {}
    if isinstance(features, Mapping):
        for key in requested:
            feature = features.get(key)
            feature_evidence[str(key)] = (
                dict(feature) if isinstance(feature, Mapping) else None
            )
    return {
        "positive": {
            "status": gate.get("positive_status"),
            "accepted": True,
            "features": feature_evidence,
        },
        "negative": {
            "case": gate.get("negative_case"),
            "accepted": False,
            "error_code": error_code,
        },
    }


def _verify_vina(
    vina_path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    expected = manifest.get("tool")
    if not isinstance(expected, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The Vina tool oracle is missing.",
        )
    size_bytes = vina_path.stat().st_size
    sha256 = _sha256(vina_path)
    _assert_equal(size_bytes, expected.get("size_bytes"), "Vina binary size")
    _assert_equal(sha256, expected.get("sha256"), "Vina binary SHA256")

    detection = vina_adapter.detect(str(vina_path))
    detected_path = Path(detection.path).resolve() if detection.path else None
    if (
        detection.status != "ok"
        or detected_path is None
        or detected_path != vina_path
        or detection.source != "bundled"
        or detection.is_bundled is not True
    ):
        _fail(
            "VINA_EVALUATION_VINA_DETECTION_FAILED",
            "The selected executable was not detected as the exact repository-bundled Vina.",
            details={"detection": detection.to_dict()},
        )
    _assert_equal(detection.version, expected.get("version"), "Vina version")
    capabilities = detection.capabilities
    if not isinstance(capabilities, Mapping):
        _fail(
            "VINA_EVALUATION_CAPABILITY_EVIDENCE_MISSING",
            "The real Vina detection returned no capability evidence.",
        )
    _assert_equal(
        capabilities.get("help_sha256"),
        expected.get("help_advanced_sha256"),
        "Vina --help_advanced SHA256",
    )
    gate_evidence = _capability_gate_evidence(capabilities, manifest)
    return {
        "path": str(vina_path),
        "version": detection.version,
        "source": detection.source,
        "is_bundled": detection.is_bundled,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "capabilities": dict(capabilities),
        "capability_gate": gate_evidence,
    }


def _same_existing_file(left: str | Path, right: str | Path) -> bool:
    """Compare executable paths without trusting textual path equality."""

    try:
        return os.path.samefile(Path(left), Path(right))
    except (OSError, ValueError, TypeError):
        try:
            return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
                str(Path(right).resolve())
            )
        except (OSError, ValueError, TypeError):
            return False


def _assert_process_record(
    record: Mapping[str, Any],
    expected_command: Sequence[str],
    vina_path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    command = record.get("command")
    if not isinstance(command, list):
        command = record.get("executed_command")
    identity = record.get("process_identity")
    pid = record.get("pid")
    executable = (
        identity.get("executable_path")
        if isinstance(identity, Mapping)
        else None
    )
    valid = bool(
        isinstance(command, list)
        and [str(value) for value in command]
        == [str(value) for value in expected_command]
        and command
        and _same_existing_file(str(command[0]), vina_path)
        and isinstance(pid, int)
        and pid > 0
        and isinstance(identity, Mapping)
        and identity.get("pid") == pid
        and bool(str(identity.get("creation_token") or ""))
        and executable
        and _same_existing_file(str(executable), vina_path)
    )
    if not valid:
        _fail(
            "VINA_EVALUATION_EXECUTION_PROCESS_INVALID",
            f"{label} is not bound to the fixed Vina process and command.",
            details={
                "label": label,
                "expected_command": [str(value) for value in expected_command],
                "recorded_command": command,
                "pid": pid,
                "process_identity": dict(identity)
                if isinstance(identity, Mapping)
                else identity,
                "expected_vina": str(vina_path),
            },
        )
    return {
        "pid": pid,
        "creation_token": identity.get("creation_token"),
        "executable_path": str(executable),
        "command": [str(value) for value in command],
    }


def _assert_execution_vina_identity(
    metadata: Mapping[str, Any],
    manifest: Mapping[str, Any],
    vina_path: Path,
    expected_commands: Sequence[Sequence[str]],
    *,
    label: str,
) -> dict[str, Any]:
    """Bind every completed stage to the manifest-pinned Vina executable."""

    expected_tool = manifest.get("tool")
    if not isinstance(expected_tool, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The Vina execution oracle is missing.",
        )
    expected_sha = str(expected_tool.get("sha256") or "").lower()
    expected_size = expected_tool.get("size_bytes")
    expected_version = str(expected_tool.get("version") or "")
    _assert_equal(_sha256(vina_path), expected_sha, f"{label} current Vina SHA256")
    _assert_equal(
        vina_path.stat().st_size,
        expected_size,
        f"{label} current Vina size",
    )

    prepared_tool = metadata.get("vina_tool")
    execution_tool = metadata.get("execution_vina")
    if not isinstance(prepared_tool, Mapping) or not isinstance(
        execution_tool,
        Mapping,
    ):
        _fail(
            "VINA_EVALUATION_EXECUTION_TOOL_MISSING",
            f"{label} has no complete prepared/execution Vina provenance.",
        )
    for stage_name, tool in (
        ("prepared", prepared_tool),
        ("execution", execution_tool),
    ):
        _assert_equal(
            str(tool.get("sha256") or "").lower(),
            expected_sha,
            f"{label} {stage_name} Vina SHA256",
        )
        _assert_equal(
            tool.get("size_bytes"),
            expected_size,
            f"{label} {stage_name} Vina size",
        )
        _assert_equal(
            str(tool.get("version") or ""),
            expected_version,
            f"{label} {stage_name} Vina version",
        )
    if not _same_existing_file(str(execution_tool.get("path") or ""), vina_path):
        _fail(
            "VINA_EVALUATION_EXECUTION_TOOL_PATH_MISMATCH",
            f"{label} execution Vina path does not identify the fixed executable.",
            details={
                "recorded": execution_tool.get("path"),
                "expected": str(vina_path),
            },
        )

    commands = [[str(value) for value in command] for command in expected_commands]
    recorded_commands = metadata.get("executed_commands")
    if len(commands) == 1:
        _assert_equal(
            metadata.get("executed_command"),
            commands[0],
            f"{label} executed command",
        )
    else:
        _assert_equal(
            recorded_commands,
            commands,
            f"{label} executed command sequence",
        )
        _assert_equal(
            metadata.get("executed_command"),
            commands[-1],
            f"{label} final executed command",
        )
    for index, command in enumerate(commands, start=1):
        if not command or not _same_existing_file(command[0], vina_path):
            _fail(
                "VINA_EVALUATION_EXECUTED_COMMAND_TOOL_MISMATCH",
                f"{label} command {index} does not invoke the fixed Vina.",
                details={"command": command, "expected_vina": str(vina_path)},
            )

    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, Mapping):
        _fail(
            "VINA_EVALUATION_EXECUTION_ARTIFACTS_MISSING",
            f"{label} has no execution artifact evidence.",
        )
    if len(commands) == 1:
        required_binary_artifacts = {
            "vina_binary_prepared",
            "vina_binary_executed",
            "vina_binary_observed_after_execution",
        }
    else:
        required_binary_artifacts = {
            "vina_binary_prepared",
            "vina_baseline_start",
            "vina_baseline_end",
            "vina_local_start",
            "vina_local_end",
        }
    binary_artifacts: dict[str, dict[str, Any]] = {}
    for key in sorted(required_binary_artifacts):
        snapshot = artifacts.get(key)
        if not isinstance(snapshot, Mapping):
            _fail(
                "VINA_EVALUATION_EXECUTION_ARTIFACTS_MISSING",
                f"{label} is missing {key}.",
                details={"available": sorted(str(value) for value in artifacts)},
            )
        _assert_equal(
            str(snapshot.get("sha256") or "").lower(),
            expected_sha,
            f"{label} {key} SHA256",
        )
        _assert_equal(
            snapshot.get("size_bytes"),
            expected_size,
            f"{label} {key} size",
        )
        binary_artifacts[key] = {
            "size_bytes": snapshot.get("size_bytes"),
            "sha256": str(snapshot.get("sha256") or "").lower(),
        }

    process_records: list[dict[str, Any]] = []
    integrity_records: list[dict[str, Any]] = []
    if len(commands) == 1:
        integrity = metadata.get("vina_binary_integrity")
        if not isinstance(integrity, Mapping):
            _fail(
                "VINA_EVALUATION_EXECUTION_INTEGRITY_MISSING",
                f"{label} has no execution-time Vina integrity record.",
            )
        for key in ("start_sha256", "end_sha256"):
            _assert_equal(
                str(integrity.get(key) or "").lower(),
                expected_sha,
                f"{label} {key}",
            )
        _assert_equal(integrity.get("match"), True, f"{label} Vina integrity")
        process_records.append(
            _assert_process_record(
                metadata,
                commands[0],
                vina_path,
                label=f"{label} process",
            )
        )
        integrity_records.append(dict(integrity))
    else:
        phases = metadata.get("execution_phases")
        if not isinstance(phases, Mapping):
            _fail(
                "VINA_EVALUATION_EXECUTION_PHASES_MISSING",
                f"{label} has no two-stage execution evidence.",
            )
        for phase_id, command in zip(
            ("input_score", "local_optimization"),
            commands,
            strict=True,
        ):
            phase = phases.get(phase_id)
            if not isinstance(phase, Mapping):
                _fail(
                    "VINA_EVALUATION_EXECUTION_PHASES_MISSING",
                    f"{label} is missing the {phase_id} phase.",
                )
            _assert_equal(
                phase.get("status"),
                "finished",
                f"{label} {phase_id} status",
            )
            _assert_equal(
                phase.get("exit_code"),
                0,
                f"{label} {phase_id} exit code",
            )
            integrity = phase.get("vina_binary_integrity")
            if not isinstance(integrity, Mapping):
                _fail(
                    "VINA_EVALUATION_EXECUTION_INTEGRITY_MISSING",
                    f"{label} {phase_id} has no Vina integrity record.",
                )
            for key in ("initial_sha256", "start_sha256", "end_sha256"):
                _assert_equal(
                    str(integrity.get(key) or "").lower(),
                    expected_sha,
                    f"{label} {phase_id} {key}",
                )
            _assert_equal(
                integrity.get("match"),
                True,
                f"{label} {phase_id} Vina integrity",
            )
            process_records.append(
                _assert_process_record(
                    phase,
                    command,
                    vina_path,
                    label=f"{label} {phase_id} process",
                )
            )
            integrity_records.append(dict(integrity))

    return {
        "execution_vina": {
            "path": str(execution_tool.get("path") or ""),
            "version": execution_tool.get("version"),
            "size_bytes": execution_tool.get("size_bytes"),
            "sha256": str(execution_tool.get("sha256") or "").lower(),
        },
        "commands": commands,
        "processes": process_records,
        "integrity": integrity_records,
        "binary_artifacts": binary_artifacts,
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
            "VINA_EVALUATION_ARTIFACT_PATH_INVALID",
            f"{label} has no safe project-relative path.",
            details={"label": label, "relative_path": relative},
        )
    resolved = (project_root / candidate).resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        _fail(
            "VINA_EVALUATION_ARTIFACT_OUTSIDE_PROJECT",
            f"{label} resolves outside the temporary project.",
            details={"label": label, "relative_path": relative},
        )
    if not resolved.is_file():
        _fail(
            "VINA_EVALUATION_ARTIFACT_MISSING",
            f"{label} is missing.",
            details={"label": label, "relative_path": relative},
        )
    return resolved


def _artifact_evidence(
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


def _assert_config_applicability(
    config_text: str,
    *,
    required_lines: Sequence[str],
    forbidden_fields: Sequence[str],
    label: str,
) -> dict[str, Any]:
    lines = config_text.splitlines()
    missing = [line for line in required_lines if line not in lines]
    present_forbidden = [
        field
        for field in forbidden_fields
        if any(line.strip().startswith(f"{field} =") for line in lines)
    ]
    if missing or present_forbidden:
        _fail(
            "VINA_EVALUATION_CONFIG_APPLICABILITY_INVALID",
            f"{label} does not respect the parameter applicability contract.",
            details={
                "missing_required_lines": missing,
                "present_forbidden_fields": present_forbidden,
                "config_text": config_text,
            },
        )
    return {
        "required_lines": list(required_lines),
        "forbidden_fields": list(forbidden_fields),
        "verified": True,
    }


def _read_json_artifact(
    project_root: Path,
    relative_value: Any,
    label: str,
) -> dict[str, Any]:
    path = _project_artifact(project_root, relative_value, label)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "VINA_EVALUATION_ARTIFACT_JSON_INVALID",
            f"{label} is not valid UTF-8 JSON.",
            details={"label": label, "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "VINA_EVALUATION_ARTIFACT_JSON_INVALID",
            f"{label} must contain a JSON object.",
        )
    return payload


def _energy_balance(evaluation: Mapping[str, Any], label: str) -> dict[str, Any]:
    terms = evaluation.get("energy_terms")
    if not isinstance(terms, list):
        _fail(
            "VINA_EVALUATION_ENERGY_TERMS_MISSING",
            f"{label} has no parsed energy terms.",
        )
    numbered: dict[int, float] = {}
    for item in terms:
        if not isinstance(item, Mapping):
            continue
        term_number = item.get("term_number")
        if term_number in (1, 2, 3, 4):
            try:
                value = float(item.get("value_kcal_mol"))
            except (TypeError, ValueError, OverflowError):
                _fail(
                    "VINA_EVALUATION_ENERGY_TERMS_INVALID",
                    f"{label} contains a non-numeric numbered term.",
                )
            if not math.isfinite(value) or int(term_number) in numbered:
                _fail(
                    "VINA_EVALUATION_ENERGY_TERMS_INVALID",
                    f"{label} contains invalid or duplicate numbered terms.",
                )
            numbered[int(term_number)] = value
    if set(numbered) != {1, 2, 3, 4}:
        _fail(
            "VINA_EVALUATION_ENERGY_TERMS_INCOMPLETE",
            f"{label} does not contain exactly terms (1) through (4).",
            details={"term_numbers": sorted(numbered)},
        )
    try:
        primary = float(evaluation.get("primary_score_kcal_mol"))
    except (TypeError, ValueError, OverflowError):
        _fail(
            "VINA_EVALUATION_ENERGY_TERMS_INVALID",
            f"{label} has no finite primary score.",
        )
    if not math.isfinite(primary):
        _fail(
            "VINA_EVALUATION_ENERGY_TERMS_INVALID",
            f"{label} has no finite primary score.",
        )
    reconstructed = (
        numbered[1] + numbered[2] + numbered[3] - numbered[4]
    )
    if abs(primary - reconstructed) > 0.0015:
        _fail(
            "VINA_EVALUATION_ENERGY_BALANCE_INVALID",
            f"{label} does not satisfy (1) + (2) + (3) - (4).",
            details={
                "primary_score_kcal_mol": primary,
                "reconstructed_score_kcal_mol": reconstructed,
                "terms": numbered,
            },
        )
    return {
        "primary_score_kcal_mol": primary,
        "term_values_kcal_mol": {
            str(key): value for key, value in sorted(numbered.items())
        },
        "reconstructed_score_kcal_mol": reconstructed,
        "absolute_rounding_difference_kcal_mol": abs(primary - reconstructed),
    }


def _prepare_project(
    work_root: Path,
    receptor_path: Path,
    ligand_path: Path,
    protocol: Mapping[str, Any],
) -> tuple[Path, Mapping[str, Any], dict[str, Any]]:
    run_mode = str(protocol.get("run_mode") or "")
    project_name = f"vina_evaluation_1iep_{run_mode}"
    created = _require_ok(
        create_project(project_name, str(work_root)),
        "VINA_EVALUATION_PROJECT_CREATE_FAILED",
        f"DockStart could not create the {run_mode} temporary project.",
    )
    project_root = Path(str(created.get("project_dir") or "")).resolve()
    try:
        project_root.relative_to(work_root.resolve())
    except ValueError:
        _fail(
            "VINA_EVALUATION_PROJECT_OUTSIDE_TEMP",
            "DockStart created a project outside the verifier's temporary root.",
            details={"project_dir": str(project_root)},
        )

    _require_ok(
        import_receptor_pdbqt(str(project_root), str(receptor_path)),
        "VINA_EVALUATION_RECEPTOR_IMPORT_FAILED",
        f"DockStart could not import the 1IEP receptor for {run_mode}.",
    )
    _require_ok(
        import_ligand_pdbqt(str(project_root), str(ligand_path)),
        "VINA_EVALUATION_LIGAND_IMPORT_FAILED",
        f"DockStart could not import the 1IEP ligand for {run_mode}.",
    )
    _require_ok(
        update_vina_run_protocol(
            str(project_root),
            run_mode,
            bool(protocol.get("autobox")),
            bool(protocol.get("confirm_pose_input")),
        ),
        "VINA_EVALUATION_PROTOCOL_UPDATE_FAILED",
        f"DockStart could not save the {run_mode} protocol.",
    )
    box_parameters = protocol.get("box")
    if isinstance(box_parameters, Mapping):
        _require_ok(
            update_box_params(str(project_root), dict(box_parameters)),
            "VINA_EVALUATION_BOX_UPDATE_FAILED",
            f"DockStart could not save the {run_mode} box.",
        )
    vina_parameters = protocol.get("vina")
    if not isinstance(vina_parameters, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            f"The {run_mode} Vina parameter oracle is invalid.",
        )
    _require_ok(
        update_vina_params(str(project_root), dict(vina_parameters)),
        "VINA_EVALUATION_PARAMETER_UPDATE_FAILED",
        f"DockStart could not save the {run_mode} Vina parameters.",
    )
    generated = _require_ok(
        generate_vina_config(str(project_root)),
        "VINA_EVALUATION_CONFIG_GENERATION_FAILED",
        f"DockStart could not generate the {run_mode} Vina config.",
    )
    config_evidence = _artifact_evidence(
        project_root,
        generated.get("config_file"),
        f"{run_mode} generated config",
    )
    expected = protocol.get("expected")
    if not isinstance(expected, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            f"The {run_mode} result oracle is invalid.",
        )
    _assert_equal(
        config_evidence["sha256"],
        expected.get("generated_config_sha256"),
        f"{run_mode} generated config SHA256",
    )
    prepared = _require_ok(
        prepare_vina_run(str(project_root)),
        "VINA_EVALUATION_RUN_PREPARATION_FAILED",
        f"DockStart could not prepare the {run_mode} run.",
    )
    return project_root, prepared, config_evidence


def _verify_frozen_inputs(
    project_root: Path,
    run_id: str,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping):
        _fail("VINA_EVALUATION_MANIFEST_INVALID", "Source identities are missing.")
    evidence: dict[str, Any] = {}
    for key in ("receptor", "ligand"):
        expected = sources.get(key)
        if not isinstance(expected, Mapping):
            _fail(
                "VINA_EVALUATION_MANIFEST_INVALID",
                f"The {key} identity is invalid.",
            )
        relative = f"runs/{run_id}/inputs/{key}.pdbqt"
        observed = _artifact_evidence(
            project_root,
            relative,
            f"frozen {key} input",
        )
        _assert_equal(
            observed["size_bytes"],
            expected.get("size_bytes"),
            f"frozen {key} size",
        )
        _assert_equal(
            observed["sha256"],
            expected.get("sha256"),
            f"frozen {key} SHA256",
        )
        evidence[key] = observed
    return evidence


def _verify_global_dock(
    work_root: Path,
    receptor_path: Path,
    ligand_path: Path,
    protocol: Mapping[str, Any],
    manifest: Mapping[str, Any],
    vina_path: Path,
) -> dict[str, Any]:
    project_root, prepared, generated_config = _prepare_project(
        work_root,
        receptor_path,
        ligand_path,
        protocol,
    )
    run_id = str(prepared.get("run_id") or "")
    commands = prepared.get("commands")
    if not isinstance(commands, list) or len(commands) != 1:
        _fail(
            "VINA_EVALUATION_DOCK_PLAN_INVALID",
            "The advanced-parameter global run must prepare exactly one command.",
            details={"commands": commands},
        )
    command = commands[0]
    if (
        not isinstance(command, list)
        or "--out" not in command
        or "--score_only" in command
        or "--local_only" in command
        or "--autobox" in command
    ):
        _fail(
            "VINA_EVALUATION_DOCK_PLAN_INVALID",
            "The advanced-parameter global command has invalid mode flags.",
            details={"command": command},
        )

    expected = protocol.get("expected")
    vina_parameters = protocol.get("vina")
    if not isinstance(expected, Mapping) or not isinstance(
        vina_parameters,
        Mapping,
    ):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The global advanced-parameter oracle is invalid.",
        )
    config_snapshot = _artifact_evidence(
        project_root,
        f"runs/{run_id}/config_snapshot.txt",
        "global docking frozen config",
    )
    _assert_equal(
        config_snapshot["sha256"],
        expected.get("config_snapshot_sha256"),
        "global docking frozen config SHA256",
    )
    generated_text = _project_artifact(
        project_root,
        generated_config["relative_path"],
        "global docking generated config",
    ).read_text(encoding="utf-8")
    snapshot_text = _project_artifact(
        project_root,
        config_snapshot["relative_path"],
        "global docking frozen config",
    ).read_text(encoding="utf-8")
    required_lines = expected.get("required_config_lines")
    forbidden_fields = expected.get("forbidden_config_fields")
    if not isinstance(required_lines, list) or not isinstance(
        forbidden_fields,
        list,
    ):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The global config applicability oracle is invalid.",
        )
    generated_applicability = _assert_config_applicability(
        generated_text,
        required_lines=[str(value) for value in required_lines],
        forbidden_fields=[str(value) for value in forbidden_fields],
        label="global generated config",
    )
    snapshot_applicability = _assert_config_applicability(
        snapshot_text,
        required_lines=[str(value) for value in required_lines],
        forbidden_fields=[str(value) for value in forbidden_fields],
        label="global frozen config",
    )
    frozen_inputs = _verify_frozen_inputs(project_root, run_id, manifest)

    executed = _require_ok(
        execute_prepared_vina_run(str(project_root), run_id),
        "VINA_EVALUATION_DOCK_EXECUTION_FAILED",
        "The real advanced-parameter global Vina process failed.",
    )
    executed_metadata = executed.get("metadata")
    if (
        not isinstance(executed_metadata, Mapping)
        or executed_metadata.get("status") != "finished"
        or executed_metadata.get("exit_code") != 0
    ):
        _fail(
            "VINA_EVALUATION_DOCK_EXECUTION_INCOMPLETE",
            "The advanced-parameter global run did not finish cleanly.",
            details={"metadata": executed_metadata},
        )
    snapshots = executed_metadata.get("snapshots")
    frozen_vina = snapshots.get("vina") if isinstance(snapshots, Mapping) else None
    if not isinstance(frozen_vina, Mapping):
        _fail(
            "VINA_EVALUATION_DOCK_SNAPSHOT_MISSING",
            "The advanced-parameter global run has no frozen Vina settings.",
        )
    for key in (
        "max_evals",
        "min_rmsd",
        "spacing",
        "verbosity",
        "unbound_energy",
    ):
        _assert_equal(
            frozen_vina.get(key),
            vina_parameters.get(key),
            f"global frozen {key}",
        )

    analyzed = _require_ok(
        analyze_vina_run_results(str(project_root), run_id),
        "VINA_EVALUATION_DOCK_ANALYSIS_FAILED",
        "DockStart could not analyze the advanced-parameter global run.",
    )
    scores = analyzed.get("scores")
    if not isinstance(scores, list):
        _fail(
            "VINA_EVALUATION_DOCK_SCORE_TABLE_MISSING",
            "The advanced-parameter global run returned no score table.",
        )
    _assert_equal(
        len(scores),
        expected.get("mode_count"),
        "global docking mode count",
    )
    observed_modes = [
        row.get("mode") if isinstance(row, Mapping) else None for row in scores
    ]
    _assert_equal(
        observed_modes,
        list(range(1, len(scores) + 1)),
        "global docking mode order",
    )
    mode_1 = scores[0] if scores and isinstance(scores[0], Mapping) else {}
    affinity = _assert_close(
        mode_1.get("affinity_kcal_mol"),
        expected.get("mode_1_affinity_kcal_mol"),
        expected.get("score_tolerance_kcal_mol"),
        "global docking Mode 1 affinity",
    )

    metadata = analyzed.get("metadata")
    if not isinstance(metadata, Mapping):
        _fail(
            "VINA_EVALUATION_DOCK_METADATA_MISSING",
            "Global result analysis returned no metadata.",
        )
    output = _artifact_evidence(
        project_root,
        metadata.get("output_file"),
        "global docking output pose",
    )
    _assert_equal(
        output["sha256"],
        expected.get("output_pose_sha256"),
        "global docking output SHA256",
    )
    exported = _require_ok(
        export_markdown_report(str(project_root), run_id),
        "VINA_EVALUATION_DOCK_REPORT_FAILED",
        "DockStart could not export the advanced-parameter global report.",
    )
    metadata_relative = f"runs/{run_id}/metadata.json"
    final_metadata = _read_json_artifact(
        project_root,
        metadata_relative,
        "global docking metadata",
    )
    execution_identity = _assert_execution_vina_identity(
        final_metadata,
        manifest,
        vina_path,
        [command],
        label="global docking",
    )

    artifacts = {
        "metadata": _artifact_evidence(
            project_root,
            metadata_relative,
            "global docking metadata",
        ),
        "config": generated_config,
        "config_snapshot": config_snapshot,
        "stdout": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stdout.txt",
            "global docking stdout",
        ),
        "stderr": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stderr.txt",
            "global docking stderr",
        ),
        "log": _artifact_evidence(
            project_root,
            f"runs/{run_id}/log.txt",
            "global docking log",
        ),
        "output_pose": output,
        "scores": _artifact_evidence(
            project_root,
            analyzed.get("scores_file"),
            "global docking scores CSV",
        ),
        "report": _artifact_evidence(
            project_root,
            exported.get("report_file"),
            "global docking report",
        ),
        "frozen_inputs": frozen_inputs,
    }
    if artifacts["stderr"]["size_bytes"] != 0:
        _fail(
            "VINA_EVALUATION_DOCK_STDERR_NOT_EMPTY",
            "The real advanced-parameter global process wrote stderr.",
            details={"stderr": artifacts["stderr"]},
        )
    return {
        "run_id": run_id,
        "run_mode": "dock",
        "command": list(command),
        "status": final_metadata.get("status"),
        "exit_code": final_metadata.get("exit_code"),
        "execution_identity": execution_identity,
        "advanced_parameters": {
            key: frozen_vina.get(key)
            for key in (
                "max_evals",
                "min_rmsd",
                "spacing",
                "verbosity",
            )
        },
        "non_applicable_saved_parameter": {
            "unbound_energy": frozen_vina.get("unbound_energy"),
            "written_to_config": False,
        },
        "config_applicability": {
            "generated": generated_applicability,
            "frozen": snapshot_applicability,
        },
        "mode_count": len(scores),
        "mode_1_affinity_kcal_mol": affinity,
        "artifacts": artifacts,
    }


def _verify_score_only(
    work_root: Path,
    receptor_path: Path,
    ligand_path: Path,
    protocol: Mapping[str, Any],
    manifest: Mapping[str, Any],
    vina_path: Path,
) -> dict[str, Any]:
    project_root, prepared, generated_config = _prepare_project(
        work_root,
        receptor_path,
        ligand_path,
        protocol,
    )
    run_id = str(prepared.get("run_id") or "")
    commands = prepared.get("commands")
    if not isinstance(commands, list) or len(commands) != 1:
        _fail(
            "VINA_EVALUATION_SCORE_PLAN_INVALID",
            "score_only must prepare exactly one real Vina command.",
            details={"commands": commands},
        )
    command = commands[0]
    if (
        not isinstance(command, list)
        or "--score_only" not in command
        or "--autobox" not in command
        or "--out" in command
    ):
        _fail(
            "VINA_EVALUATION_SCORE_PLAN_INVALID",
            "score_only prepared an invalid command or a pose output.",
            details={"command": command},
        )
    expected = protocol.get("expected")
    assert isinstance(expected, Mapping)
    config_snapshot = _artifact_evidence(
        project_root,
        f"runs/{run_id}/config_snapshot.txt",
        "score_only frozen config",
    )
    _assert_equal(
        config_snapshot["sha256"],
        expected.get("config_snapshot_sha256"),
        "score_only frozen config SHA256",
    )
    score_required = (
        "spacing = 0.375",
        "unbound_energy = 0",
        "no_refine = true",
        "force_even_voxels = true",
        "verbosity = 2",
    )
    score_forbidden = (
        "max_evals",
        "min_rmsd",
        "exhaustiveness",
        "num_modes",
        "energy_range",
        "seed",
    )
    generated_applicability = _assert_config_applicability(
        _project_artifact(
            project_root,
            generated_config["relative_path"],
            "score_only generated config",
        ).read_text(encoding="utf-8"),
        required_lines=score_required,
        forbidden_fields=score_forbidden,
        label="score_only generated config",
    )
    snapshot_applicability = _assert_config_applicability(
        _project_artifact(
            project_root,
            config_snapshot["relative_path"],
            "score_only frozen config",
        ).read_text(encoding="utf-8"),
        required_lines=score_required,
        forbidden_fields=score_forbidden,
        label="score_only frozen config",
    )
    frozen_inputs = _verify_frozen_inputs(project_root, run_id, manifest)

    executed = _require_ok(
        execute_prepared_vina_run(str(project_root), run_id),
        "VINA_EVALUATION_SCORE_EXECUTION_FAILED",
        "The real score_only Vina process failed.",
    )
    executed_metadata = executed.get("metadata")
    if not isinstance(executed_metadata, Mapping):
        _fail(
            "VINA_EVALUATION_SCORE_METADATA_MISSING",
            "score_only execution returned no metadata.",
        )
    if (
        executed_metadata.get("status") != "finished"
        or executed_metadata.get("exit_code") != 0
        or str(executed_metadata.get("output_file") or "")
    ):
        _fail(
            "VINA_EVALUATION_SCORE_OUTPUT_SEMANTICS_INVALID",
            "score_only did not finish cleanly without a pose output.",
            details={"metadata": dict(executed_metadata)},
        )

    analyzed = _require_ok(
        analyze_vina_run_results(str(project_root), run_id),
        "VINA_EVALUATION_SCORE_ANALYSIS_FAILED",
        "DockStart could not analyze the real score_only result.",
    )
    evaluation = analyzed.get("evaluation")
    if not isinstance(evaluation, Mapping):
        _fail(
            "VINA_EVALUATION_SCORE_RESULT_MISSING",
            "score_only analysis returned no evaluation object.",
        )
    if (
        evaluation.get("run_mode") != "score_only"
        or evaluation.get("output_pose_generated") is not False
        or str(evaluation.get("output_pose_file") or "")
        or evaluation.get("input_pose_file")
        != f"runs/{run_id}/inputs/ligand.pdbqt"
        or evaluation.get("pose_file")
        != f"runs/{run_id}/inputs/ligand.pdbqt"
        or analyzed.get("scores") != []
        or analyzed.get("scores_file") not in (None, "")
    ):
        _fail(
            "VINA_EVALUATION_SCORE_OUTPUT_SEMANTICS_INVALID",
            "score_only published a pose or a docking ranking table.",
            details={"evaluation": dict(evaluation)},
        )
    run_root = project_root / "runs" / run_id
    published_pdbqt = [
        str(path.relative_to(run_root))
        for path in run_root.rglob("*.pdbqt")
        if "inputs" not in path.relative_to(run_root).parts
    ]
    if published_pdbqt:
        _fail(
            "VINA_EVALUATION_SCORE_UNEXPECTED_POSE",
            "score_only created one or more PDBQT files outside its frozen inputs.",
            details={"unexpected_pdbqt": published_pdbqt},
        )

    score = _assert_close(
        evaluation.get("primary_score_kcal_mol"),
        expected.get("primary_score_kcal_mol"),
        expected.get("score_tolerance_kcal_mol"),
        "score_only primary score",
    )
    unbound = evaluation.get("unbound_energy")
    if not isinstance(unbound, Mapping):
        _fail(
            "VINA_EVALUATION_UNBOUND_EVIDENCE_MISSING",
            "score_only did not preserve explicit unbound-energy evidence.",
        )
    _assert_equal(unbound.get("mode"), "explicit", "unbound-energy mode")
    _assert_close(
        unbound.get("value_kcal_mol"),
        expected.get("unbound_energy_kcal_mol"),
        0,
        "unbound-energy value",
    )
    energy_balance = _energy_balance(evaluation, "score_only")

    exported = _require_ok(
        export_markdown_report(str(project_root), run_id),
        "VINA_EVALUATION_SCORE_REPORT_FAILED",
        "DockStart could not export the score_only report.",
    )
    metadata_relative = f"runs/{run_id}/metadata.json"
    final_metadata = _read_json_artifact(
        project_root,
        metadata_relative,
        "score_only metadata",
    )
    execution_identity = _assert_execution_vina_identity(
        final_metadata,
        manifest,
        vina_path,
        [command],
        label="score_only",
    )

    artifacts = {
        "metadata": _artifact_evidence(
            project_root,
            metadata_relative,
            "score_only metadata",
        ),
        "config": generated_config,
        "config_snapshot": config_snapshot,
        "stdout": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stdout.txt",
            "score_only stdout",
        ),
        "stderr": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stderr.txt",
            "score_only stderr",
        ),
        "log": _artifact_evidence(
            project_root,
            f"runs/{run_id}/log.txt",
            "score_only log",
        ),
        "evaluation": _artifact_evidence(
            project_root,
            analyzed.get("evaluation_file"),
            "score_only evaluation",
        ),
        "report": _artifact_evidence(
            project_root,
            exported.get("report_file"),
            "score_only report",
        ),
        "frozen_inputs": frozen_inputs,
    }
    if artifacts["stderr"]["size_bytes"] != 0:
        _fail(
            "VINA_EVALUATION_SCORE_STDERR_NOT_EMPTY",
            "The real score_only process wrote stderr.",
            details={"stderr": artifacts["stderr"]},
        )
    return {
        "run_id": run_id,
        "run_mode": "score_only",
        "command": list(command),
        "status": final_metadata.get("status"),
        "exit_code": final_metadata.get("exit_code"),
        "execution_identity": execution_identity,
        "primary_score_kcal_mol": score,
        "output_pose_generated": False,
        "pose_file": evaluation.get("pose_file"),
        "config_applicability": {
            "generated": generated_applicability,
            "frozen": snapshot_applicability,
        },
        "energy_balance": energy_balance,
        "artifacts": artifacts,
    }


def _verify_local_only(
    work_root: Path,
    receptor_path: Path,
    ligand_path: Path,
    protocol: Mapping[str, Any],
    manifest: Mapping[str, Any],
    vina_path: Path,
) -> dict[str, Any]:
    project_root, prepared, generated_config = _prepare_project(
        work_root,
        receptor_path,
        ligand_path,
        protocol,
    )
    run_id = str(prepared.get("run_id") or "")
    commands = prepared.get("commands")
    if not isinstance(commands, list) or len(commands) != 2:
        _fail(
            "VINA_EVALUATION_LOCAL_PLAN_INVALID",
            "Modern local_only must prepare exactly two real Vina commands.",
            details={"commands": commands},
        )
    baseline_command, optimization_command = commands
    if (
        not isinstance(baseline_command, list)
        or "--score_only" not in baseline_command
        or "--autobox" not in baseline_command
        or "--out" in baseline_command
        or not isinstance(optimization_command, list)
        or "--local_only" not in optimization_command
        or "--autobox" not in optimization_command
        or "--out" not in optimization_command
    ):
        _fail(
            "VINA_EVALUATION_LOCAL_PLAN_INVALID",
            "The local_only baseline/optimization command order is invalid.",
            details={"commands": commands},
        )
    plan = (
        prepared.get("metadata", {}).get("execution_plan")
        if isinstance(prepared.get("metadata"), Mapping)
        else None
    )
    if not isinstance(plan, Mapping) or plan.get("kind") != "local_only_with_baseline":
        _fail(
            "VINA_EVALUATION_LOCAL_PLAN_INVALID",
            "The prepared local_only run lacks two-stage execution semantics.",
            details={"execution_plan": plan},
        )

    expected = protocol.get("expected")
    assert isinstance(expected, Mapping)
    config_snapshot = _artifact_evidence(
        project_root,
        f"runs/{run_id}/config_snapshot.txt",
        "local_only frozen config",
    )
    _assert_equal(
        config_snapshot["sha256"],
        expected.get("config_snapshot_sha256"),
        "local_only frozen config SHA256",
    )
    config_text = _project_artifact(
        project_root,
        f"runs/{run_id}/config_snapshot.txt",
        "local_only frozen config",
    ).read_text(encoding="utf-8")
    if "unbound_energy" in config_text:
        _fail(
            "VINA_EVALUATION_LOCAL_UNBOUND_ENERGY_INVALID",
            "local_only config must not contain score-only unbound energy.",
        )
    local_required = (
        "spacing = 0.375",
        "no_refine = true",
        "force_even_voxels = true",
        "verbosity = 2",
    )
    local_forbidden = (
        "max_evals",
        "min_rmsd",
        "exhaustiveness",
        "num_modes",
        "energy_range",
        "seed",
        "unbound_energy",
    )
    generated_applicability = _assert_config_applicability(
        _project_artifact(
            project_root,
            generated_config["relative_path"],
            "local_only generated config",
        ).read_text(encoding="utf-8"),
        required_lines=local_required,
        forbidden_fields=local_forbidden,
        label="local_only generated config",
    )
    snapshot_applicability = _assert_config_applicability(
        config_text,
        required_lines=local_required,
        forbidden_fields=local_forbidden,
        label="local_only frozen config",
    )
    frozen_inputs = _verify_frozen_inputs(project_root, run_id, manifest)

    executed = _require_ok(
        execute_prepared_vina_run(str(project_root), run_id),
        "VINA_EVALUATION_LOCAL_EXECUTION_FAILED",
        "The real two-stage local_only Vina execution failed.",
    )
    executed_metadata = executed.get("metadata")
    if not isinstance(executed_metadata, Mapping):
        _fail(
            "VINA_EVALUATION_LOCAL_METADATA_MISSING",
            "local_only execution returned no metadata.",
        )
    phases = executed_metadata.get("execution_phases")
    if not isinstance(phases, Mapping):
        _fail(
            "VINA_EVALUATION_LOCAL_PHASES_MISSING",
            "local_only metadata contains no execution phases.",
        )
    baseline_phase = phases.get("input_score")
    optimization_phase = phases.get("local_optimization")
    if (
        not isinstance(baseline_phase, Mapping)
        or baseline_phase.get("status") != "finished"
        or baseline_phase.get("exit_code") != 0
        or not isinstance(optimization_phase, Mapping)
        or optimization_phase.get("status") != "finished"
        or optimization_phase.get("exit_code") != 0
    ):
        _fail(
            "VINA_EVALUATION_LOCAL_PHASES_INCOMPLETE",
            "Both local_only phases must finish successfully.",
            details={"execution_phases": dict(phases)},
        )

    analyzed = _require_ok(
        analyze_vina_run_results(str(project_root), run_id),
        "VINA_EVALUATION_LOCAL_ANALYSIS_FAILED",
        "DockStart could not analyze the two-stage local_only result.",
    )
    evaluation = analyzed.get("evaluation")
    if not isinstance(evaluation, Mapping):
        _fail(
            "VINA_EVALUATION_LOCAL_RESULT_MISSING",
            "local_only analysis returned no evaluation object.",
        )
    comparison = evaluation.get("comparison")
    if (
        evaluation.get("run_mode") != "local_only"
        or evaluation.get("output_pose_generated") is not True
        or not isinstance(comparison, Mapping)
        or comparison.get("comparable") is not True
    ):
        _fail(
            "VINA_EVALUATION_LOCAL_COMPARISON_INVALID",
            "local_only did not preserve a comparable baseline and optimized pose.",
            details={"evaluation": dict(evaluation)},
        )
    tolerance = expected.get("score_tolerance_kcal_mol")
    input_score = _assert_close(
        comparison.get("input_score_kcal_mol"),
        expected.get("input_score_kcal_mol"),
        tolerance,
        "local_only input score",
    )
    optimized_score = _assert_close(
        comparison.get("optimized_score_kcal_mol"),
        expected.get("optimized_score_kcal_mol"),
        tolerance,
        "local_only optimized score",
    )
    delta_score = _assert_close(
        comparison.get("delta_score_kcal_mol"),
        expected.get("delta_score_kcal_mol"),
        tolerance,
        "local_only optimized-minus-input score",
    )
    _assert_close(
        delta_score,
        optimized_score - input_score,
        1e-12,
        "local_only delta definition",
    )
    _assert_close(
        baseline_phase.get("primary_score_kcal_mol"),
        input_score,
        0,
        "local_only baseline phase score",
    )
    _assert_close(
        optimization_phase.get("primary_score_kcal_mol"),
        optimized_score,
        0,
        "local_only optimization phase score",
    )
    baseline_terms = evaluation.get("input_energy_terms")
    if not isinstance(baseline_terms, list):
        _fail(
            "VINA_EVALUATION_LOCAL_BASELINE_TERMS_MISSING",
            "local_only analysis contains no baseline energy terms.",
        )
    baseline_energy_balance = _energy_balance(
        {
            "energy_terms": baseline_terms,
            "primary_score_kcal_mol": input_score,
        },
        "local_only input baseline",
    )
    energy_balance = _energy_balance(evaluation, "local_only optimized stage")

    geometry = comparison.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("ok") is not True:
        _fail(
            "VINA_EVALUATION_LOCAL_GEOMETRY_INVALID",
            "local_only did not produce a verified same-frame geometry comparison.",
            details={"geometry": geometry},
        )
    _assert_equal(
        geometry.get("heavy_atom_count"),
        expected.get("heavy_atom_count"),
        "local_only heavy-atom count",
    )
    heavy_atom_rmsd = _assert_close(
        geometry.get("heavy_atom_rmsd_no_alignment_angstrom"),
        expected.get("heavy_atom_rmsd_no_alignment_angstrom"),
        expected.get("geometry_tolerance_angstrom"),
        "local_only heavy-atom RMSD",
    )

    output_relative = evaluation.get("output_pose_file")
    output = _artifact_evidence(
        project_root,
        output_relative,
        "local_only optimized pose",
    )
    _assert_equal(
        output["sha256"],
        expected.get("optimized_pose_sha256"),
        "local_only optimized pose SHA256",
    )
    if output["sha256"] == frozen_inputs["ligand"]["sha256"]:
        _fail(
            "VINA_EVALUATION_LOCAL_POSE_UNCHANGED",
            "local_only published a byte-identical input pose as optimized output.",
        )

    exported = _require_ok(
        export_markdown_report(str(project_root), run_id),
        "VINA_EVALUATION_LOCAL_REPORT_FAILED",
        "DockStart could not export the local_only report.",
    )
    metadata_relative = f"runs/{run_id}/metadata.json"
    final_metadata = _read_json_artifact(
        project_root,
        metadata_relative,
        "local_only metadata",
    )
    execution_identity = _assert_execution_vina_identity(
        final_metadata,
        manifest,
        vina_path,
        [baseline_command, optimization_command],
        label="local_only",
    )
    artifacts = {
        "metadata": _artifact_evidence(
            project_root,
            metadata_relative,
            "local_only metadata",
        ),
        "config": generated_config,
        "config_snapshot": config_snapshot,
        "baseline_stdout": _artifact_evidence(
            project_root,
            f"runs/{run_id}/baseline_stdout.txt",
            "local_only baseline stdout",
        ),
        "baseline_stderr": _artifact_evidence(
            project_root,
            f"runs/{run_id}/baseline_stderr.txt",
            "local_only baseline stderr",
        ),
        "baseline_log": _artifact_evidence(
            project_root,
            f"runs/{run_id}/baseline_log.txt",
            "local_only baseline log",
        ),
        "stdout": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stdout.txt",
            "local_only optimization stdout",
        ),
        "stderr": _artifact_evidence(
            project_root,
            f"runs/{run_id}/stderr.txt",
            "local_only optimization stderr",
        ),
        "log": _artifact_evidence(
            project_root,
            f"runs/{run_id}/log.txt",
            "local_only optimization log",
        ),
        "optimized_pose": output,
        "evaluation": _artifact_evidence(
            project_root,
            analyzed.get("evaluation_file"),
            "local_only evaluation",
        ),
        "report": _artifact_evidence(
            project_root,
            exported.get("report_file"),
            "local_only report",
        ),
        "frozen_inputs": frozen_inputs,
    }
    for key in ("baseline_stderr", "stderr"):
        if artifacts[key]["size_bytes"] != 0:
            _fail(
                "VINA_EVALUATION_LOCAL_STDERR_NOT_EMPTY",
                f"The real local_only {key} artifact is not empty.",
                details={key: artifacts[key]},
            )
    return {
        "run_id": run_id,
        "run_mode": "local_only",
        "execution_plan": "input_score_then_local_optimization",
        "commands": [list(baseline_command), list(optimization_command)],
        "status": final_metadata.get("status"),
        "exit_code": final_metadata.get("exit_code"),
        "execution_identity": execution_identity,
        "input_score_kcal_mol": input_score,
        "optimized_score_kcal_mol": optimized_score,
        "delta_score_kcal_mol": delta_score,
        "output_pose_generated": True,
        "config_applicability": {
            "generated": generated_applicability,
            "frozen": snapshot_applicability,
        },
        "geometry": {
            "method": geometry.get("method"),
            "alignment_applied": geometry.get("alignment_applied"),
            "heavy_atom_count": geometry.get("heavy_atom_count"),
            "heavy_atom_rmsd_no_alignment_angstrom": heavy_atom_rmsd,
            "max_heavy_atom_displacement_angstrom": geometry.get(
                "max_heavy_atom_displacement_angstrom"
            ),
            "centroid_displacement_angstrom": geometry.get(
                "centroid_displacement_angstrom"
            ),
        },
        "baseline_energy_balance": baseline_energy_balance,
        "optimized_energy_balance": energy_balance,
        "artifacts": artifacts,
    }


def verify_vina_evaluation_1iep(
    receptor_path: str | Path,
    ligand_path: str | Path,
    *,
    vina_executable: str | Path = DEFAULT_VINA,
) -> dict[str, Any]:
    """Execute the real global and both evaluation-mode acceptance chains."""

    manifest = _load_manifest()
    receptor = _regular_file(receptor_path, "1IEP receptor PDBQT")
    ligand = _regular_file(ligand_path, "1IEP ligand PDBQT")
    vina_path = _regular_file(vina_executable, "AutoDock Vina executable")
    protocols = manifest.get("protocols")
    if not isinstance(protocols, Mapping):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "The protocol oracles are missing.",
        )
    global_protocol = protocols.get("global_dock")
    score_protocol = protocols.get("score_only")
    local_protocol = protocols.get("local_only")
    if (
        not isinstance(global_protocol, Mapping)
        or not isinstance(score_protocol, Mapping)
        or not isinstance(
            local_protocol,
            Mapping,
        )
    ):
        _fail(
            "VINA_EVALUATION_MANIFEST_INVALID",
            "A global, score_only, or local_only protocol oracle is invalid.",
        )

    steps: dict[str, Any] = {}
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="DockStart_vina_evaluation_1iep_",
    )
    work_root = Path(temporary_handle.name)
    settings_path = work_root / "dockstart_settings.json"
    pinned_resource_root = REPOSITORY_ROOT
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    previous_resource_dir = os.environ.get(RESOURCE_DIR_ENV_VAR)
    result_payload: dict[str, Any] | None = None
    caught: VinaEvaluationAcceptanceError | None = None
    try:
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(pinned_resource_root)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(vina=str(vina_path)),
            )
        )
        source_evidence = _execute_step(
            steps,
            "manifest_pinned_public_inputs",
            lambda: _verify_sources(receptor, ligand, manifest),
        )
        tool_evidence = _execute_step(
            steps,
            "real_bundled_vina_and_capability_gates",
            lambda: _verify_vina(vina_path, manifest),
        )
        global_evidence = _execute_step(
            steps,
            "public_project_api_global_advanced_parameters",
            lambda: _verify_global_dock(
                work_root,
                receptor,
                ligand,
                global_protocol,
                manifest,
                vina_path,
            ),
        )
        score_evidence = _execute_step(
            steps,
            "public_project_api_score_only",
            lambda: _verify_score_only(
                work_root,
                receptor,
                ligand,
                score_protocol,
                manifest,
                vina_path,
            ),
        )
        local_evidence = _execute_step(
            steps,
            "public_project_api_two_stage_local_only",
            lambda: _verify_local_only(
                work_root,
                receptor,
                ligand,
                local_protocol,
                manifest,
                vina_path,
            ),
        )
        result_payload = {
            "ok": True,
            "fixture_id": manifest.get("fixture_id"),
            "verification_scope": (
                "public_project_api_real_vina_global_advanced_score_only_"
                "and_two_stage_local_only"
            ),
            "network_or_download_used": False,
            "synthetic_vina_output_accepted": False,
            "sources": source_evidence,
            "tool": tool_evidence,
            "global_advanced_parameters": global_evidence,
            "score_only": score_evidence,
            "local_only": local_evidence,
            "steps": steps,
        }
    except VinaEvaluationAcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve the JSON CLI boundary.
        caught = VinaEvaluationAcceptanceError(
            "VINA_EVALUATION_UNEXPECTED_ERROR",
            "The project-level evaluation verifier failed unexpectedly.",
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
        except Exception as exc:  # noqa: BLE001 - lifecycle evidence.
            settings_restore_error = str(exc)
        settings_restored = (
            SETTINGS_ENV_VAR not in os.environ
            if previous_settings_path is None
            else os.environ.get(SETTINGS_ENV_VAR) == previous_settings_path
        )
        settings_evidence = {
            "variable": SETTINGS_ENV_VAR,
            "previously_set": previous_settings_path is not None,
            "isolated_path": str(settings_path),
            "restored": settings_restored,
            "error": settings_restore_error,
        }

        resource_restore_error = ""
        try:
            if previous_resource_dir is None:
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
            else:
                os.environ[RESOURCE_DIR_ENV_VAR] = previous_resource_dir
        except Exception as exc:  # noqa: BLE001 - lifecycle evidence.
            resource_restore_error = str(exc)
        resource_restored = (
            RESOURCE_DIR_ENV_VAR not in os.environ
            if previous_resource_dir is None
            else os.environ.get(RESOURCE_DIR_ENV_VAR) == previous_resource_dir
        )
        resource_evidence = {
            "variable": RESOURCE_DIR_ENV_VAR,
            "previously_set": previous_resource_dir is not None,
            "verification_path": str(pinned_resource_root),
            "restored": resource_restored,
            "error": resource_restore_error,
        }

        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001 - lifecycle evidence.
            cleanup_error = str(exc)
        cleanup = {
            "path": str(work_root),
            "cleanup_called": True,
            "removed": not work_root.exists(),
            "error": cleanup_error,
        }

    lifecycle_evidence = {
        "temporary_cleanup": cleanup,
        "settings_environment": settings_evidence,
        "toolchain_resource_environment": resource_evidence,
    }
    if caught is not None:
        caught.details = {**caught.details, **lifecycle_evidence}
        if not caught.steps:
            caught.steps = dict(steps)
        raise caught
    if (
        result_payload is None
        or cleanup["removed"] is not True
        or cleanup["error"]
        or settings_evidence["restored"] is not True
        or settings_evidence["error"]
        or resource_evidence["restored"] is not True
        or resource_evidence["error"]
    ):
        _fail(
            "VINA_EVALUATION_TEMPORARY_LIFECYCLE_FAILED",
            "The temporary projects or isolated environment were not restored.",
            details=lifecycle_evidence,
        )
    result_payload.update(lifecycle_evidence)
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run DockStart's public project API with the manifest-pinned "
            "AutoDock Vina v1.2.7 1IEP PDBQT inputs through a real global "
            "advanced-parameter run, score_only, and two-stage local_only "
            "acceptance."
        ),
    )
    parser.add_argument(
        "--receptor",
        required=True,
        help="Path to the manifest-pinned v1.2.7 1iep_receptor.pdbqt.",
    )
    parser.add_argument(
        "--ligand",
        required=True,
        help="Path to the manifest-pinned v1.2.7 1iep_ligand.pdbqt.",
    )
    parser.add_argument(
        "--vina",
        default=str(DEFAULT_VINA),
        help="Path to the manifest-pinned repository-bundled Vina 1.2.7 executable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_vina_evaluation_1iep(
            arguments.receptor,
            arguments.ligand,
            vina_executable=arguments.vina,
        )
    except VinaEvaluationAcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "vina_evaluation_1iep_external",
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                    "steps": exc.steps,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    except Exception as exc:  # noqa: BLE001 - acceptance must fail as JSON.
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "vina_evaluation_1iep_external",
                    "error": {
                        "code": "VINA_EVALUATION_UNEXPECTED_ERROR",
                        "message": str(exc),
                        "details": {"type": type(exc).__name__},
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
