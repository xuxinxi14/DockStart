"""Replay Vina/Vinardo precomputed-maps workflows with a real Vina 1.2.7.

This verifier is intentionally independent from the GUI and never injects a
mock runner.  For each scoring function it uses DockStart's public project API
to:

1. create and configure an isolated project;
2. run ``vina --write_maps``;
3. activate the ready map set through a real ``--maps --score_only`` probe;
4. freeze the manifest and maps into a prepared run;
5. execute a real ``--maps`` docking process;
6. parse scores and export the Markdown report.

The Vina workflow additionally corrupts one active map, proves that config/run
preparation fails closed without allocating a new run, then regenerates a new
map set and completes a retry.  A separate project feeds Vina a fixed invalid
atom type, observes a real non-zero process exit, proves no partial map set was
published, and retries with the valid ligand.  All temporary projects are
removed by default.  No network access, structure preparation, or
scientific-success claim is part of this software-correctness gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    export_markdown_report,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_project,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
    validate_run_prerequisites,
    execute_prepared_vina_run,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    save_settings,
)
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR  # noqa: E402
from dockstart_core.vina_maps import (  # noqa: E402
    activate_map_set,
    generate_maps,
    validate_active_maps,
)

VERIFIER_ID = "dockstart_vina_maps_external_v1"
SCHEMA_VERSION = 1
REQUIRED_VINA_VERSION = "1.2.7"
SUPPORTED_SCORING = ("vina", "vinardo")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
DEFAULT_RECEPTOR = (
    REPOSITORY_ROOT
    / "resources"
    / "examples"
    / "basic_pdbqt"
    / "receptor.pdbqt"
)
DEFAULT_LIGAND = (
    REPOSITORY_ROOT
    / "resources"
    / "examples"
    / "basic_pdbqt"
    / "ligand.pdbqt"
)
DEFAULT_INPUT_CONTRACT = {
    "receptor": {
        "size_bytes": 657,
        "sha256": (
            "2ef52a38914a61e275721928f1e8b611"
            "57233ed958b8a1cf6e54a73895c8c50a"
        ),
    },
    "ligand": {
        "size_bytes": 512,
        "sha256": (
            "9071449d7d4af5ca2b750b2fb3d58872"
            "c7dec532e61a833479a9f616aff170be"
        ),
    },
}
DEFAULT_VINA_CONTRACT = {
    "version": REQUIRED_VINA_VERSION,
    "size_bytes": 1233920,
    "sha256": (
        "e0c4b2715e0c1a74f6e92d0f3be0328a"
        "c97542eafbc111e6b1efad897a73cce5"
    ),
    "help_advanced_sha256": (
        "fc85023362623eaf979e262257f8b9490"
        "19b5ae235a106ca9035a22f3dc368ab"
    ),
}
INVALID_ATOM_TYPE_LIGAND = (
    "REMARK verifier-only invalid ligand; ZZ must be rejected by Vina 1.2.7\n"
    "ROOT\n"
    "ATOM      1  C   LIG     1       0.000   0.000   0.000"
    "  1.00  0.00     0.000 ZZ\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)
DEFAULT_BOX = {
    "center_x": 0.0,
    "center_y": 0.0,
    "center_z": 0.0,
    "size_x": 8.0,
    "size_y": 8.0,
    "size_z": 8.0,
}
DEFAULT_VINA_PARAMETERS = {
    "exhaustiveness": 1,
    "num_modes": 3,
    "min_rmsd": 1.0,
    "energy_range": 3.0,
    "spacing": 0.375,
    "cpu": 1,
    "seed": 20260730,
    "verbosity": 1,
}
REQUIRED_CAPABILITIES = (
    "maps",
    "write_maps",
    "force_even_voxels",
    "no_refine",
)
DEFAULT_ACCEPTANCE_ORACLE = {
    "vina": {
        "generation_stdout_sha256": (
            "706cb008e28d3fa8e90c2c39984dd863"
            "e4ac49e5c8783f2fcaa0ae474442ff30"
        ),
        "probe_stdout_sha256": (
            "862799160d1a236561eab8d253f68892"
            "cd26639d7efc382ea7bc97ae2fa1ac04"
        ),
        "maps_payload_sha256": (
            "945daff3d8de04b89436ac52890208e7d"
            "3027fa0709ed90b80e1fb13a7dabe8c"
        ),
        "maps": {
            "receptor.C_P.map": {
                "atom_type": "C_P",
                "size_bytes": 104311,
                "sha256": (
                    "949da5d693e8de516075385e68aebcee1"
                    "50704b2a1fe61a485d398fe8f1c40ec"
                ),
            },
            "receptor.O_DA.map": {
                "atom_type": "O_DA",
                "size_bytes": 107304,
                "sha256": (
                    "c0d9789783afd48708bab9f6a17ed268"
                    "531b8276ebdab232e7e0f31ab2fe934f"
                ),
            },
        },
        "grid": {
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "requested_size": {"x": 8.0, "y": 8.0, "z": 8.0},
            "spacing": 0.375,
            "nelements": {"x": 22, "y": 22, "z": 22},
            "actual_size": {"x": 8.25, "y": 8.25, "z": 8.25},
        },
        "config_snapshot_sha256": (
            "81b287fbf6ca4a5ae53e26acba42fd76"
            "78b25c57abd2a62a2b8ec44acead754b"
        ),
        "log_sha256": (
            "007ff50a00eef242594b988f3133992b"
            "f0c5ea81ab4b07b277afc890e5c5b4e8"
        ),
        "output_sha256": (
            "f2be3cd50c5a4eee1ac96f1cad9e6dfa"
            "e03e4964a47e439d6a9543f7db33b962"
        ),
        "scores_csv_sha256": (
            "5e5cda5f9c27ab98978c2bfca29da684"
            "894bf4d196bb628de9a526636268932b"
        ),
        "scores": [
            {
                "mode": 1,
                "affinity_kcal_mol": -0.6447,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
            },
            {
                "mode": 2,
                "affinity_kcal_mol": -0.5742,
                "rmsd_lb": 5.371,
                "rmsd_ub": 5.371,
            },
        ],
    },
    "vinardo": {
        "generation_stdout_sha256": (
            "3414130cf88566ca0418cf19317fbc245"
            "7a8b0b43159a694b7595599ce816d32"
        ),
        "probe_stdout_sha256": (
            "d9040eb5e89fa22a709fa1c678697165"
            "c05141059fbef5e4d425a9fea91b1608"
        ),
        "maps_payload_sha256": (
            "c6044ff772ee5f58ac01954167d5111c"
            "7160a3cf07368fa459b34f8c039636d9"
        ),
        "maps": {
            "receptor.C_P.map": {
                "atom_type": "C_P",
                "size_bytes": 104082,
                "sha256": (
                    "9396c42a9cd4fd23f2b93ef714df157a"
                    "4f633c5474380eb6fea609e49cc92839"
                ),
            },
            "receptor.O_DA.map": {
                "atom_type": "O_DA",
                "size_bytes": 110500,
                "sha256": (
                    "c7e93a04f0626f24b7717ecd3c163249"
                    "d0a2dce5a2f45b94f3bdeec28fa8c6ae"
                ),
            },
        },
        "grid": {
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "requested_size": {"x": 8.0, "y": 8.0, "z": 8.0},
            "spacing": 0.375,
            "nelements": {"x": 22, "y": 22, "z": 22},
            "actual_size": {"x": 8.25, "y": 8.25, "z": 8.25},
        },
        "config_snapshot_sha256": (
            "495da5aaab9c01634e04270e0b4ff3f3"
            "9d6f2f00ec69fc362cf97ee985e1c53b"
        ),
        "log_sha256": (
            "d403d4edc85acb05e528028844d8c149"
            "e656b1d0b130b8b76b4e17257b1dc309"
        ),
        "output_sha256": (
            "94dd7082833b215bbace224a13a2b375"
            "3f0a0590d3a6bf1b9151f02365a10937"
        ),
        "scores_csv_sha256": (
            "7293c0ce5f5c16743c246c95afabdae"
            "6c70021698308909f598cd7c271109c12"
        ),
        "scores": [
            {
                "mode": 1,
                "affinity_kcal_mol": -0.8898,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
            },
            {
                "mode": 2,
                "affinity_kcal_mol": -0.8333,
                "rmsd_lb": 3.27,
                "rmsd_ub": 3.27,
            },
        ],
    },
}


class VinaMapsExternalVerificationError(RuntimeError):
    """Stable JSON error from the external Vina maps verifier."""

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


def _fail(
    condition: bool,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    if not condition:
        raise VinaMapsExternalVerificationError(
            code,
            message,
            details=details,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _regular_file(
    path: str | Path,
    *,
    label: str,
    allow_empty: bool = False,
) -> Path:
    supplied = Path(path).expanduser()
    _fail(
        not supplied.is_symlink(),
        "VINA_MAPS_VERIFIER_FILE_INVALID",
        f"{label} must not be a symlink.",
        details={"path": str(supplied)},
    )
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_FILE_MISSING",
            f"{label} does not exist.",
            details={"path": str(supplied), "error": str(exc)},
        ) from exc
    _fail(
        resolved.is_file() and not resolved.is_symlink(),
        "VINA_MAPS_VERIFIER_FILE_INVALID",
        f"{label} must be a regular non-symlink file.",
        details={"path": str(resolved)},
    )
    _fail(
        allow_empty or resolved.stat().st_size > 0,
        "VINA_MAPS_VERIFIER_FILE_EMPTY",
        f"{label} must not be empty.",
        details={"path": str(resolved)},
    )
    return resolved


def _project_file(
    project_root: Path,
    relative_path: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> Path:
    supplied = Path(relative_path)
    _fail(
        bool(relative_path) and not supplied.is_absolute(),
        "VINA_MAPS_VERIFIER_ARTIFACT_PATH_INVALID",
        f"{label} must use a non-empty project-relative path.",
        details={"path": relative_path},
    )
    candidate = project_root / supplied
    _fail(
        not candidate.is_symlink(),
        "VINA_MAPS_VERIFIER_ARTIFACT_PATH_INVALID",
        f"{label} must not be a symlink.",
        details={"path": relative_path},
    )
    path = candidate.resolve(strict=False)
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_ARTIFACT_PATH_ESCAPE",
            f"{label} escapes the isolated project.",
            details={"path": relative_path},
        ) from exc
    _fail(
        path.is_file() and not path.is_symlink(),
        "VINA_MAPS_VERIFIER_ARTIFACT_MISSING",
        f"{label} is not a regular file.",
        details={"path": str(path)},
    )
    _fail(
        allow_empty or path.stat().st_size > 0,
        "VINA_MAPS_VERIFIER_ARTIFACT_EMPTY",
        f"{label} is empty.",
        details={"path": str(path)},
    )
    return path


def _file_evidence(
    path: Path,
    *,
    relative_to: Path | None = None,
) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    value = (
        resolved.relative_to(relative_to.resolve()).as_posix()
        if relative_to is not None
        else str(resolved)
    )
    return {
        "path": value,
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }


def _assert_file_contract(
    path: Path,
    contract: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    evidence = _file_evidence(path)
    _fail(
        evidence["size_bytes"] == contract.get("size_bytes")
        and evidence["sha256"] == contract.get("sha256"),
        "VINA_MAPS_VERIFIER_FILE_CONTRACT_MISMATCH",
        f"{label} no longer matches its initial fixed identity.",
        details={
            "label": label,
            "expected": {
                "size_bytes": contract.get("size_bytes"),
                "sha256": contract.get("sha256"),
            },
            "actual": evidence,
        },
    )
    return evidence


def _snapshot_file(
    source: Path,
    destination: Path,
    contract: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    before = _assert_file_contract(source, contract, label=f"{label} source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _fail(
        not destination.exists(),
        "VINA_MAPS_VERIFIER_SNAPSHOT_EXISTS",
        f"{label} snapshot destination already exists.",
        details={"path": str(destination)},
    )
    shutil.copyfile(source, destination)
    snapshot = _assert_file_contract(
        destination,
        contract,
        label=f"{label} private snapshot",
    )
    after = _assert_file_contract(
        source,
        contract,
        label=f"{label} source after snapshot",
    )
    return {
        "source_before": before,
        "source_after": after,
        "snapshot": snapshot,
    }


def _captured_stream_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "sha256": "",
            "text": "",
            "text_truncated": False,
        }
    payload = path.read_bytes()
    limit = 16384
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "text": payload[:limit].decode("utf-8", errors="replace"),
        "text_truncated": len(payload) > limit,
    }


def _process_kind(command: Sequence[str]) -> str:
    if "--write_maps" in command:
        return "write_maps"
    if "--maps" in command and "--score_only" in command:
        return "maps_score_only_probe"
    return "unknown_vina_command"


class _AuditedVinaRunner:
    """Delegate to the real adapter while retaining independent process evidence."""

    def __init__(
        self,
        vina_path: Path,
        vina_contract: Mapping[str, Any],
        *,
        run_impl: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self.vina_path = vina_path.resolve(strict=True)
        self.vina_contract = dict(vina_contract)
        self._run_impl = run_impl or vina_adapter.run_command
        self.events: list[dict[str, Any]] = []

    def __call__(
        self,
        command: list[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
    ) -> Mapping[str, Any]:
        sequence = len(self.events) + 1
        command_copy = list(command)
        executable = Path(command_copy[0]).resolve(strict=True)
        _fail(
            _same_file(executable, self.vina_path),
            "VINA_MAPS_VERIFIER_AUDITED_EXECUTABLE_MISMATCH",
            "A public maps API attempted to execute a different Vina binary.",
            details={
                "expected": str(self.vina_path),
                "actual": str(executable),
                "command": command_copy,
            },
        )
        before = _assert_file_contract(
            executable,
            self.vina_contract,
            label=f"audited process {sequence} Vina before execution",
        )
        result: Mapping[str, Any] = {}
        raised = ""
        try:
            result = self._run_impl(
                command_copy,
                cwd,
                stdout_path,
                stderr_path,
                log_path,
            )
            return result
        except Exception as exc:
            raised = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            after = _assert_file_contract(
                executable,
                self.vina_contract,
                label=f"audited process {sequence} Vina after execution",
            )
            event = {
                "sequence": sequence,
                "kind": _process_kind(command_copy),
                "adapter": "adapters.vina_adapter.run_command",
                "command": command_copy,
                "cwd": str(Path(cwd).resolve(strict=False)),
                "pid": result.get("pid") if isinstance(result, Mapping) else None,
                "exit_code": (
                    result.get("exit_code")
                    if isinstance(result, Mapping)
                    else None
                ),
                "adapter_ok": (
                    result.get("ok") if isinstance(result, Mapping) else None
                ),
                "adapter_error": (
                    str(result.get("error") or "")
                    if isinstance(result, Mapping)
                    else raised
                ),
                "raised": raised,
                "vina_before": before,
                "vina_after": after,
                "stdout": _captured_stream_evidence(Path(stdout_path)),
                "stderr": _captured_stream_evidence(Path(stderr_path)),
                "log": _captured_stream_evidence(Path(log_path)),
            }
            self.events.append(event)

    def one_event_since(
        self,
        marker: int,
        *,
        expected_kind: str,
        expected_exit_zero: bool,
    ) -> dict[str, Any]:
        observed = self.events[marker:]
        _fail(
            len(observed) == 1,
            "VINA_MAPS_VERIFIER_PROCESS_COUNT_INVALID",
            "A public maps API did not invoke exactly one audited Vina process.",
            details={
                "marker": marker,
                "event_count": len(self.events),
                "observed": observed,
            },
        )
        event = observed[0]
        exit_code = event.get("exit_code")
        _fail(
            event.get("kind") == expected_kind
            and isinstance(event.get("pid"), int)
            and int(event["pid"]) > 0
            and isinstance(exit_code, int)
            and ((exit_code == 0) if expected_exit_zero else (exit_code != 0))
            and event.get("raised") == "",
            "VINA_MAPS_VERIFIER_PROCESS_EVIDENCE_INVALID",
            "The audited adapter evidence does not prove the expected real process.",
            details={
                "expected_kind": expected_kind,
                "expected_exit_zero": expected_exit_zero,
                "event": event,
            },
        )
        return event


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return (
            os.path.normcase(str(left.resolve(strict=False)))
            == os.path.normcase(str(right.resolve(strict=False)))
        )


def _pinned_default_file_evidence(
    path: Path,
    *,
    role: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _file_evidence(path)
    _fail(
        evidence["size_bytes"] == contract.get("size_bytes")
        and evidence["sha256"] == contract.get("sha256"),
        "VINA_MAPS_VERIFIER_DEFAULT_FIXTURE_MISMATCH",
        f"The pinned default {role} fixture bytes changed.",
        details={
            "role": role,
            "expected": dict(contract),
            "actual": evidence,
        },
    )
    return evidence


def _input_acceptance_profile(
    receptor_path: Path,
    ligand_path: Path,
    box: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    receptor_is_default = _same_file(receptor_path, DEFAULT_RECEPTOR)
    ligand_is_default = _same_file(ligand_path, DEFAULT_LIGAND)
    receptor = (
        _pinned_default_file_evidence(
            receptor_path,
            role="receptor",
            contract=DEFAULT_INPUT_CONTRACT["receptor"],
        )
        if receptor_is_default
        else _file_evidence(receptor_path)
    )
    ligand = (
        _pinned_default_file_evidence(
            ligand_path,
            role="ligand",
            contract=DEFAULT_INPUT_CONTRACT["ligand"],
        )
        if ligand_is_default
        else _file_evidence(ligand_path)
    )
    effective_box = dict(DEFAULT_BOX if box is None else box)
    box_is_default = effective_box == DEFAULT_BOX
    pinned_default = (
        receptor_is_default
        and ligand_is_default
        and box_is_default
    )
    return {
        "kind": (
            "pinned_default_acceptance"
            if pinned_default
            else "custom_input_probe"
        ),
        "counts_as_default_acceptance": pinned_default,
        "receptor_uses_pinned_default_path": receptor_is_default,
        "ligand_uses_pinned_default_path": ligand_is_default,
        "box_uses_pinned_default_values": box_is_default,
        "receptor": receptor,
        "ligand": ligand,
        "box": effective_box,
        "pinned_default_contract": DEFAULT_INPUT_CONTRACT,
        "qualification": (
            "Both repository fixtures and the fixed Box match the independent "
            "acceptance oracle."
            if pinned_default
            else (
                "Custom input or Box probe only; this completed workflow cannot "
                "replace the pinned default-fixture acceptance gate."
            )
        ),
    }


def _require_ok(
    payload: Any,
    *,
    step: str,
) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("ok") is True:
        return payload
    error = (
        payload.get("error")
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict)
        else {}
    )
    raise VinaMapsExternalVerificationError(
        str(error.get("code") or "VINA_MAPS_VERIFIER_PROJECT_API_FAILED"),
        f"DockStart project API step failed: {step}.",
        details={
            "step": step,
            "message": str(error.get("message") or ""),
            "raw_error": str(error.get("raw_error") or ""),
            "suggestion": str(error.get("suggestion") or ""),
        },
    )


def _error_code(payload: Any) -> str:
    error = (
        payload.get("error")
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict)
        else {}
    )
    return str(error.get("code") or "")


def _require_exact_vina_127(
    vina_path: Path,
    *,
    isolated_resource_dir: Path,
) -> dict[str, Any]:
    binary = _assert_file_contract(
        vina_path,
        DEFAULT_VINA_CONTRACT,
        label="fixed AutoDock Vina 1.2.7 binary",
    )
    detection = vina_adapter.detect(
        str(vina_path),
        bundled_path=str(
            isolated_resource_dir / "resources" / "vina" / "vina.exe"
        ),
    )
    evidence = detection.to_dict()
    _fail(
        detection.status == "ok",
        "VINA_MAPS_VERIFIER_VINA_UNUSABLE",
        "The configured AutoDock Vina executable is not usable.",
        details=evidence,
    )
    _fail(
        detection.version == REQUIRED_VINA_VERSION,
        "VINA_MAPS_VERIFIER_VERSION_MISMATCH",
        "This verifier requires exactly AutoDock Vina 1.2.7.",
        details={
            "expected": REQUIRED_VINA_VERSION,
            "actual": detection.version,
            "detection": evidence,
        },
    )
    capabilities = (
        detection.capabilities
        if isinstance(detection.capabilities, dict)
        else {}
    )
    _fail(
        capabilities.get("help_sha256")
        == DEFAULT_VINA_CONTRACT["help_advanced_sha256"],
        "VINA_MAPS_VERIFIER_HELP_HASH_MISMATCH",
        "The selected Vina --help_advanced output does not match the fixed oracle.",
        details={
            "expected": DEFAULT_VINA_CONTRACT["help_advanced_sha256"],
            "actual": capabilities.get("help_sha256"),
        },
    )
    try:
        same_binary = os.path.samefile(detection.path, vina_path)
    except OSError:
        same_binary = (
            os.path.normcase(str(Path(detection.path).resolve(strict=False)))
            == os.path.normcase(str(vina_path.resolve(strict=False)))
        )
    _fail(
        same_binary,
        "VINA_MAPS_VERIFIER_BINARY_SELECTION_MISMATCH",
        "DockStart did not select the exact Vina executable supplied to the verifier.",
        details={
            "requested": str(vina_path),
            "selected": detection.path,
            "source": detection.source,
        },
    )
    features = (
        capabilities.get("features")
        if isinstance(capabilities.get("features"), dict)
        else {}
    )
    unsupported = [
        key
        for key in REQUIRED_CAPABILITIES
        if not isinstance(features.get(key), dict)
        or features[key].get("status") != "supported"
        or features[key].get("supported") is not True
    ]
    _fail(
        not unsupported,
        "VINA_MAPS_VERIFIER_CAPABILITY_MISSING",
        "Vina 1.2.7 did not provide the required maps capability evidence.",
        details={"unsupported": unsupported, "detection": evidence},
    )
    return {
        **evidence,
        "size_bytes": binary["size_bytes"],
        "sha256": binary["sha256"],
        "fixed_contract": dict(DEFAULT_VINA_CONTRACT),
        "required_capabilities": list(REQUIRED_CAPABILITIES),
        "exact_version_required": REQUIRED_VINA_VERSION,
    }


def _create_configured_project(
    *,
    work_root: Path,
    project_name: str,
    receptor_path: Path,
    ligand_path: Path,
    scoring: str,
    box: Mapping[str, float],
    expected_inputs: Mapping[str, Mapping[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    created = _require_ok(
        create_project(project_name, str(work_root)),
        step=f"{scoring}: create project",
    )
    project_root = Path(str(created["project_dir"])).resolve(strict=True)
    _assert_file_contract(
        receptor_path,
        expected_inputs["receptor"],
        label=f"{scoring} receptor snapshot before import",
    )
    _require_ok(
        import_receptor_pdbqt(str(project_root), str(receptor_path)),
        step=f"{scoring}: import receptor",
    )
    _assert_file_contract(
        receptor_path,
        expected_inputs["receptor"],
        label=f"{scoring} receptor snapshot after import",
    )
    _assert_file_contract(
        ligand_path,
        expected_inputs["ligand"],
        label=f"{scoring} ligand snapshot before import",
    )
    _require_ok(
        import_ligand_pdbqt(str(project_root), str(ligand_path)),
        step=f"{scoring}: import ligand",
    )
    _assert_file_contract(
        ligand_path,
        expected_inputs["ligand"],
        label=f"{scoring} ligand snapshot after import",
    )
    _require_ok(
        update_box_params(str(project_root), dict(box)),
        step=f"{scoring}: set box",
    )
    parameters = {
        **DEFAULT_VINA_PARAMETERS,
        "scoring": scoring,
    }
    _require_ok(
        update_vina_params(str(project_root), parameters),
        step=f"{scoring}: set Vina parameters",
    )
    prepared_receptor = _project_file(
        project_root,
        "prepared/receptor.pdbqt",
        label="imported receptor",
    )
    prepared_ligand = _project_file(
        project_root,
        "prepared/ligand.pdbqt",
        label="imported ligand",
    )
    prepared_receptor_evidence = _assert_file_contract(
        prepared_receptor,
        expected_inputs["receptor"],
        label=f"{scoring} imported receptor",
    )
    prepared_ligand_evidence = _assert_file_contract(
        prepared_ligand,
        expected_inputs["ligand"],
        label=f"{scoring} imported ligand",
    )
    return project_root, {
        "project_name": project_name,
        "project_file": _file_evidence(
            project_root / "project.json",
            relative_to=project_root,
        ),
        "prepared_receptor": {
            **prepared_receptor_evidence,
            "path": prepared_receptor.relative_to(project_root).as_posix(),
        },
        "prepared_ligand": {
            **prepared_ligand_evidence,
            "path": prepared_ligand.relative_to(project_root).as_posix(),
        },
        "box": dict(box),
        "vina_parameters": parameters,
    }


def _expected_generation_command(
    vina_path: Path,
    *,
    scoring: str,
    box: Mapping[str, float],
) -> list[str]:
    return [
        str(vina_path),
        "--receptor",
        "inputs/receptor.pdbqt",
        "--ligand",
        "inputs/ligand.pdbqt",
        "--scoring",
        scoring,
        "--center_x",
        f"{box['center_x']:.15g}",
        "--center_y",
        f"{box['center_y']:.15g}",
        "--center_z",
        f"{box['center_z']:.15g}",
        "--size_x",
        f"{box['size_x']:.15g}",
        "--size_y",
        f"{box['size_y']:.15g}",
        "--size_z",
        f"{box['size_z']:.15g}",
        "--spacing",
        f"{DEFAULT_VINA_PARAMETERS['spacing']:.15g}",
        "--force_even_voxels",
        "--no_refine",
        "--write_maps",
        "receptor",
        "--score_only",
        "--cpu",
        "1",
        "--verbosity",
        "1",
    ]


def _expected_probe_command(
    vina_path: Path,
    *,
    map_set_id: str,
    scoring: str,
) -> list[str]:
    return [
        str(vina_path),
        "--maps",
        f"maps/{map_set_id}/receptor",
        "--ligand",
        "prepared/ligand.pdbqt",
        "--scoring",
        scoring,
        "--score_only",
        "--cpu",
        "1",
        "--verbosity",
        "1",
    ]


def _expected_dock_command(
    vina_path: Path,
    *,
    run_id: str,
    scoring: str,
) -> list[str]:
    return [
        str(vina_path),
        "--config",
        f"runs/{run_id}/config_snapshot.txt",
        "--maps",
        f"runs/{run_id}/inputs/maps/receptor",
        "--scoring",
        scoring,
        "--out",
        f"runs/{run_id}/out.pdbqt",
    ]


def _expected_config_text(*, run_id: str, scoring: str) -> str:
    return (
        f"ligand = runs/{run_id}/inputs/ligand.pdbqt\n"
        f"scoring = {scoring}\n"
        "\n"
        f"exhaustiveness = {DEFAULT_VINA_PARAMETERS['exhaustiveness']}\n"
        "max_evals = 0\n"
        f"num_modes = {DEFAULT_VINA_PARAMETERS['num_modes']}\n"
        f"min_rmsd = {DEFAULT_VINA_PARAMETERS['min_rmsd']:.15g}\n"
        f"energy_range = {DEFAULT_VINA_PARAMETERS['energy_range']:.15g}\n"
        f"cpu = {DEFAULT_VINA_PARAMETERS['cpu']}\n"
        f"verbosity = {DEFAULT_VINA_PARAMETERS['verbosity']}\n"
        f"seed = {DEFAULT_VINA_PARAMETERS['seed']}\n"
    )


def _scoring_oracle(scoring: str) -> Mapping[str, Any]:
    oracle = DEFAULT_ACCEPTANCE_ORACLE.get(scoring)
    _fail(
        isinstance(oracle, Mapping),
        "VINA_MAPS_VERIFIER_ORACLE_MISSING",
        f"No independent fixed oracle exists for scoring={scoring}.",
    )
    return oracle


def _assert_generation_oracle(
    evidence: Mapping[str, Any],
    *,
    scoring: str,
) -> None:
    oracle = _scoring_oracle(scoring)
    expected_maps = oracle.get("maps")
    _fail(
        isinstance(expected_maps, Mapping),
        "VINA_MAPS_VERIFIER_ORACLE_MISSING",
        f"The independent {scoring} map oracle is invalid.",
    )
    actual_maps = {
        str(item.get("name") or ""): {
            "atom_type": item.get("atom_type"),
            "size_bytes": item.get("size_bytes"),
            "sha256": item.get("sha256"),
        }
        for item in evidence.get("maps", [])
        if isinstance(item, Mapping)
    }
    process = (
        evidence.get("write_maps_process")
        if isinstance(evidence.get("write_maps_process"), Mapping)
        else {}
    )
    stdout = (
        process.get("audit", {}).get("stdout", {})
        if isinstance(process.get("audit"), Mapping)
        else {}
    )
    _fail(
        actual_maps == dict(expected_maps)
        and evidence.get("payload_sha256")
        == oracle.get("maps_payload_sha256")
        and stdout.get("sha256")
        == oracle.get("generation_stdout_sha256"),
        "VINA_MAPS_VERIFIER_GENERATION_ORACLE_MISMATCH",
        f"The generated {scoring} maps differ from the independent byte oracle.",
        details={
            "expected_maps": expected_maps,
            "actual_maps": actual_maps,
            "expected_payload_sha256": oracle.get("maps_payload_sha256"),
            "actual_payload_sha256": evidence.get("payload_sha256"),
            "expected_stdout_sha256": oracle.get(
                "generation_stdout_sha256"
            ),
            "actual_stdout_sha256": stdout.get("sha256"),
        },
    )


def _map_set_evidence(
    project_root: Path,
    generated: Mapping[str, Any],
    *,
    scoring: str,
    box: Mapping[str, float],
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    process_event: Mapping[str, Any],
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    map_set_id = str(generated.get("map_set_id") or "")
    manifest_relative = str(generated.get("manifest_file") or "")
    manifest_path = _project_file(
        project_root,
        manifest_relative,
        label=f"{map_set_id} manifest",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_MANIFEST_INVALID",
            "Generated maps manifest is not readable JSON.",
            details={"path": str(manifest_path), "error": str(exc)},
        ) from exc
    _fail(
        isinstance(manifest, dict)
        and manifest.get("status") == "ready"
        and manifest.get("map_set_id") == map_set_id
        and manifest.get("scoring_function") == scoring,
        "VINA_MAPS_VERIFIER_MANIFEST_BINDING_INVALID",
        "Generated manifest is not a ready record for the returned map_set_id.",
    )
    receptor = (
        manifest.get("receptor")
        if isinstance(manifest.get("receptor"), dict)
        else {}
    )
    ligand = (
        manifest.get("ligand_at_generation")
        if isinstance(manifest.get("ligand_at_generation"), dict)
        else {}
    )
    expected_receptor_sha = str(expected_inputs["receptor"].get("sha256") or "")
    expected_ligand_sha = str(expected_inputs["ligand"].get("sha256") or "")
    _fail(
        receptor.get("sha256") == expected_receptor_sha
        and receptor.get("source_sha256") == expected_receptor_sha
        and receptor.get("size_bytes")
        == expected_inputs["receptor"].get("size_bytes")
        and ligand.get("sha256") == expected_ligand_sha
        and ligand.get("source_sha256") == expected_ligand_sha
        and ligand.get("size_bytes")
        == expected_inputs["ligand"].get("size_bytes"),
        "VINA_MAPS_VERIFIER_MANIFEST_INPUT_BINDING_INVALID",
        "Generated maps are not bound to the initial private input snapshots.",
        details={
            "receptor": receptor,
            "ligand_at_generation": ligand,
            "expected_inputs": {
                "receptor": dict(expected_inputs["receptor"]),
                "ligand": dict(expected_inputs["ligand"]),
            },
        },
    )
    grid = manifest.get("grid") if isinstance(manifest.get("grid"), dict) else {}
    requested_box = (
        grid.get("requested_box")
        if isinstance(grid.get("requested_box"), dict)
        else {}
    )
    expected_requested_box = {
        "center": {
            "x": box["center_x"],
            "y": box["center_y"],
            "z": box["center_z"],
        },
        "size": {
            "x": box["size_x"],
            "y": box["size_y"],
            "z": box["size_z"],
        },
    }
    semantics = (
        manifest.get("semantics")
        if isinstance(manifest.get("semantics"), dict)
        else {}
    )
    _fail(
        requested_box == expected_requested_box
        and grid.get("center") == expected_requested_box["center"]
        and grid.get("spacing") == DEFAULT_VINA_PARAMETERS["spacing"]
        and grid.get("force_even_voxels") is True
        and semantics
        == {
            "grid_only": True,
            "no_refine_equivalent": True,
            "rigid_receptor_only": True,
        },
        "VINA_MAPS_VERIFIER_GRID_OR_SEMANTICS_INVALID",
        "Generated maps do not match the independently requested Box/grid semantics.",
        details={
            "expected_requested_box": expected_requested_box,
            "grid": grid,
            "semantics": semantics,
        },
    )
    maps = manifest.get("maps") if isinstance(manifest.get("maps"), dict) else {}
    files = maps.get("files") if isinstance(maps.get("files"), list) else []
    map_evidence: list[dict[str, Any]] = []
    for item in files:
        _fail(
            isinstance(item, dict) and bool(str(item.get("name") or "")),
            "VINA_MAPS_VERIFIER_MAP_RECORD_INVALID",
            "Generated manifest contains an invalid map file record.",
        )
        name = str(item["name"])
        path = _project_file(
            project_root,
            Path("maps", map_set_id, name).as_posix(),
            label=f"{map_set_id} map {name}",
        )
        evidence = {
            **_file_evidence(path, relative_to=project_root),
            "name": name,
            "atom_type": str(item.get("atom_type") or ""),
        }
        _fail(
            evidence["sha256"] == str(item.get("sha256") or "").lower()
            and evidence["size_bytes"] == item.get("size_bytes"),
            "VINA_MAPS_VERIFIER_MAP_HASH_MISMATCH",
            "Generated map bytes do not match the ready manifest.",
            details={"map": evidence, "manifest_record": item},
        )
        map_evidence.append(evidence)
    _fail(
        bool(map_evidence) and len(map_evidence) == maps.get("map_count"),
        "VINA_MAPS_VERIFIER_MAP_INVENTORY_INVALID",
        "Generated map inventory is empty or has the wrong count.",
    )
    vina = manifest.get("vina") if isinstance(manifest.get("vina"), dict) else {}
    command = vina.get("command") if isinstance(vina.get("command"), list) else []
    expected_command = _expected_generation_command(
        vina_path,
        scoring=scoring,
        box=box,
    )
    _fail(
        command == expected_command
        and list(process_event.get("command") or []) == expected_command
        and process_event.get("exit_code") == 0
        and vina.get("exit_code") == 0
        and vina.get("sha256") == DEFAULT_VINA_CONTRACT["sha256"]
        and vina.get("size_bytes") == DEFAULT_VINA_CONTRACT["size_bytes"],
        "VINA_MAPS_VERIFIER_WRITE_MAPS_PROCESS_INVALID",
        "Map generation command/tool metadata disagrees with audited process evidence.",
        details={
            "expected_command": expected_command,
            "manifest_vina": vina,
            "process_event": dict(process_event),
        },
    )
    result = {
        "map_set_id": map_set_id,
        "manifest": _file_evidence(manifest_path, relative_to=project_root),
        "manifest_sha256": _sha256_file(manifest_path),
        "payload_sha256": str(maps.get("payload_sha256") or ""),
        "map_count": len(map_evidence),
        "maps": map_evidence,
        "grid": grid,
        "input_binding": {
            "receptor_sha256": receptor.get("sha256"),
            "ligand_sha256": ligand.get("sha256"),
        },
        "write_maps_process": {
            "command": command,
            "exit_code": vina.get("exit_code"),
            "stdout_file": str(vina.get("stdout_file") or ""),
            "stderr_file": str(vina.get("stderr_file") or ""),
            "log_file": str(vina.get("log_file") or ""),
            "audit": dict(process_event),
        },
        "_manifest": manifest,
    }
    if enforce_fixed_oracle:
        oracle_grid = _scoring_oracle(scoring).get("grid")
        _fail(
            isinstance(oracle_grid, Mapping)
            and grid.get("center") == oracle_grid.get("center")
            and expected_requested_box["size"]
            == oracle_grid.get("requested_size")
            and grid.get("spacing") == oracle_grid.get("spacing")
            and grid.get("nelements") == oracle_grid.get("nelements")
            and grid.get("actual_size") == oracle_grid.get("actual_size"),
            "VINA_MAPS_VERIFIER_GRID_ORACLE_MISMATCH",
            f"The generated {scoring} grid differs from the fixed real-tool oracle.",
            details={"expected": oracle_grid, "actual": grid},
        )
        _assert_generation_oracle(result, scoring=scoring)
    return result


def _assert_generation_command(
    evidence: Mapping[str, Any],
    *,
    scoring: str,
    box: Mapping[str, float],
    vina_path: Path,
) -> None:
    process = (
        evidence.get("write_maps_process")
        if isinstance(evidence.get("write_maps_process"), dict)
        else {}
    )
    command = process.get("command") if isinstance(process.get("command"), list) else []
    _fail(
        command
        == _expected_generation_command(
            vina_path,
            scoring=scoring,
            box=box,
        )
        and process.get("exit_code") == 0,
        "VINA_MAPS_VERIFIER_WRITE_MAPS_COMMAND_INVALID",
        "The frozen map-generation command does not prove the expected real workflow.",
        details={"scoring": scoring, "process": process},
    )


def _activate_ready_set(
    project_root: Path,
    *,
    map_set_id: str,
    scoring: str,
    source_map_set: Mapping[str, Any],
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    auditor: _AuditedVinaRunner,
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    marker = len(auditor.events)
    activated = _require_ok(
        activate_map_set(
            str(project_root),
            map_set_id,
            runner=auditor,
        ),
        step=f"{scoring}: activate map set",
    )
    process_event = auditor.one_event_since(
        marker,
        expected_kind="maps_score_only_probe",
        expected_exit_zero=True,
    )
    probe = (
        activated.get("compatibility_probe")
        if isinstance(activated.get("compatibility_probe"), dict)
        else {}
    )
    command = probe.get("command") if isinstance(probe.get("command"), list) else []
    expected_command = _expected_probe_command(
        vina_path,
        map_set_id=map_set_id,
        scoring=scoring,
    )
    _fail(
        probe.get("ok") is True
        and probe.get("status") == "compatible"
        and probe.get("exit_code") == 0
        and command == expected_command
        and list(process_event.get("command") or []) == expected_command
        and probe.get("ligand_sha256")
        == expected_inputs["ligand"].get("sha256")
        and probe.get("vina_binary_sha256")
        == DEFAULT_VINA_CONTRACT["sha256"]
        and probe.get("maps_payload_sha256")
        == source_map_set.get("payload_sha256"),
        "VINA_MAPS_VERIFIER_ACTIVATION_PROBE_INVALID",
        "Map activation did not preserve a successful real compatibility probe.",
        details={"probe": probe},
    )
    status = _require_ok(
        validate_active_maps(str(project_root)),
        step=f"{scoring}: validate active maps",
    )
    _fail(
        status.get("ready") is True
        and status.get("protocol_active") is True
        and status.get("map_set_id") == map_set_id,
        "VINA_MAPS_VERIFIER_ACTIVE_SET_INVALID",
        "The ready map set was not activated as the current project grid.",
        details={"status": status},
    )
    if enforce_fixed_oracle:
        oracle = _scoring_oracle(scoring)
        _fail(
            process_event.get("stdout", {}).get("sha256")
            == oracle.get("probe_stdout_sha256")
            and source_map_set.get("payload_sha256")
            == oracle.get("maps_payload_sha256"),
            "VINA_MAPS_VERIFIER_PROBE_ORACLE_MISMATCH",
            f"The {scoring} compatibility probe differs from the fixed oracle.",
            details={
                "expected_stdout_sha256": oracle.get(
                    "probe_stdout_sha256"
                ),
                "actual_stdout": process_event.get("stdout"),
                "expected_maps_payload_sha256": oracle.get(
                    "maps_payload_sha256"
                ),
                "actual_maps_payload_sha256": source_map_set.get(
                    "payload_sha256"
                ),
            },
        )
    return {
        "map_set_id": map_set_id,
        "protocol_active": True,
        "probe": {
            "probe_id": str(probe.get("probe_id") or ""),
            "command": command,
            "exit_code": probe.get("exit_code"),
            "ligand_sha256": str(probe.get("ligand_sha256") or ""),
            "vina_binary_sha256": str(
                probe.get("vina_binary_sha256") or ""
            ),
            "maps_payload_sha256": str(
                probe.get("maps_payload_sha256") or ""
            ),
            "probe_file": str(probe.get("probe_file") or ""),
            "audit": process_event,
        },
    }


def _frozen_maps_evidence(
    project_root: Path,
    *,
    run_id: str,
    source_map_set: Mapping[str, Any],
    scoring: str,
    expected_inputs: Mapping[str, Mapping[str, Any]],
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    frozen_dir = project_root / "runs" / run_id / "inputs" / "maps"
    frozen_manifest = _project_file(
        project_root,
        Path("runs", run_id, "inputs", "maps", "manifest.json").as_posix(),
        label=f"{run_id} frozen maps manifest",
    )
    source_manifest = source_map_set.get("manifest")
    _fail(
        isinstance(source_manifest, dict)
        and _sha256_file(frozen_manifest) == source_manifest.get("sha256"),
        "VINA_MAPS_VERIFIER_FROZEN_MANIFEST_MISMATCH",
        "Prepared run did not freeze the exact active maps manifest.",
    )
    try:
        frozen_manifest_payload = json.loads(
            frozen_manifest.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_FROZEN_MANIFEST_INVALID",
            "The frozen maps manifest is not valid UTF-8 JSON.",
            details={"error": str(exc)},
        ) from exc
    frozen_receptor = (
        frozen_manifest_payload.get("receptor")
        if isinstance(frozen_manifest_payload, dict)
        and isinstance(frozen_manifest_payload.get("receptor"), dict)
        else {}
    )
    frozen_ligand = (
        frozen_manifest_payload.get("ligand_at_generation")
        if isinstance(frozen_manifest_payload, dict)
        and isinstance(
            frozen_manifest_payload.get("ligand_at_generation"),
            dict,
        )
        else {}
    )
    _fail(
        frozen_receptor.get("sha256")
        == expected_inputs["receptor"].get("sha256")
        and frozen_ligand.get("sha256")
        == expected_inputs["ligand"].get("sha256")
        and frozen_manifest_payload.get("scoring_function") == scoring,
        "VINA_MAPS_VERIFIER_FROZEN_INPUT_BINDING_INVALID",
        "Frozen maps do not preserve the initial input/scoring identity.",
        details={
            "receptor": frozen_receptor,
            "ligand": frozen_ligand,
            "scoring": frozen_manifest_payload.get("scoring_function"),
        },
    )
    source_files = {
        str(item.get("name") or ""): item
        for item in source_map_set.get("maps", [])
        if isinstance(item, dict)
    }
    frozen_files: list[dict[str, Any]] = []
    for name, source in sorted(source_files.items()):
        path = _regular_file(
            frozen_dir / name,
            label=f"{run_id} frozen map {name}",
        )
        evidence = _file_evidence(path, relative_to=project_root)
        _fail(
            evidence["sha256"] == source.get("sha256")
            and evidence["size_bytes"] == source.get("size_bytes"),
            "VINA_MAPS_VERIFIER_FROZEN_MAP_MISMATCH",
            "Prepared run did not freeze the exact active map bytes.",
            details={"source": source, "frozen": evidence},
        )
        frozen_files.append(evidence)
    _fail(
        bool(frozen_files),
        "VINA_MAPS_VERIFIER_FROZEN_MAPS_EMPTY",
        "Prepared run has no frozen map files.",
    )
    if enforce_fixed_oracle:
        oracle_maps = _scoring_oracle(scoring).get("maps")
        frozen_by_name = {
            Path(str(item.get("path") or "")).name: {
                "size_bytes": item.get("size_bytes"),
                "sha256": item.get("sha256"),
            }
            for item in frozen_files
        }
        expected_by_name = {
            str(name): {
                "size_bytes": record.get("size_bytes"),
                "sha256": record.get("sha256"),
            }
            for name, record in (
                oracle_maps.items()
                if isinstance(oracle_maps, Mapping)
                else []
            )
            if isinstance(record, Mapping)
        }
        _fail(
            frozen_by_name == expected_by_name,
            "VINA_MAPS_VERIFIER_FROZEN_MAP_ORACLE_MISMATCH",
            "Frozen run maps differ from the independent fixed oracle.",
            details={
                "expected": expected_by_name,
                "actual": frozen_by_name,
            },
        )
    return {
        "manifest": _file_evidence(
            frozen_manifest,
            relative_to=project_root,
        ),
        "maps": frozen_files,
        "source_payload_sha256": str(
            source_map_set.get("payload_sha256") or ""
        ),
    }


def _score_rows(value: Any, *, label: str) -> list[dict[str, Any]]:
    _fail(
        isinstance(value, list) and bool(value),
        "VINA_MAPS_VERIFIER_SCORES_INVALID",
        f"{label} contains no score rows.",
        details={"value": value},
    )
    rows: list[dict[str, Any]] = []
    for item in value:
        _fail(
            isinstance(item, Mapping),
            "VINA_MAPS_VERIFIER_SCORES_INVALID",
            f"{label} contains a non-object score row.",
            details={"row": item},
        )
        try:
            row = {
                "mode": int(item.get("mode")),
                "affinity_kcal_mol": float(item.get("affinity_kcal_mol")),
                "rmsd_lb": float(item.get("rmsd_lb")),
                "rmsd_ub": float(item.get("rmsd_ub")),
            }
        except (TypeError, ValueError, OverflowError) as exc:
            raise VinaMapsExternalVerificationError(
                "VINA_MAPS_VERIFIER_SCORES_INVALID",
                f"{label} contains a non-numeric score row.",
                details={"row": dict(item)},
            ) from exc
        _fail(
            all(
                math.isfinite(row[key])
                for key in (
                    "affinity_kcal_mol",
                    "rmsd_lb",
                    "rmsd_ub",
                )
            ),
            "VINA_MAPS_VERIFIER_SCORES_INVALID",
            f"{label} contains a non-finite score row.",
            details={"row": row},
        )
        rows.append(row)
    modes = [row["mode"] for row in rows]
    _fail(
        modes == list(range(1, len(rows) + 1))
        and len(set(modes)) == len(modes),
        "VINA_MAPS_VERIFIER_SCORE_MODE_ORDER_INVALID",
        f"{label} modes are not unique and sequential from 1.",
        details={"modes": modes},
    )
    return rows


def _read_scores_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return _score_rows(rows, label="scores CSV")


def _parse_log_scores(text: str) -> list[dict[str, Any]]:
    rows = [
        {
            "mode": int(match.group(1)),
            "affinity_kcal_mol": float(match.group(2)),
            "rmsd_lb": float(match.group(3)),
            "rmsd_ub": float(match.group(4)),
        }
        for match in re.finditer(
            r"(?m)^\s*(\d+)\s+"
            r"(-?\d+(?:\.\d+)?)\s+"
            r"(-?\d+(?:\.\d+)?)\s+"
            r"(-?\d+(?:\.\d+)?)\s*$",
            text,
        )
    ]
    return _score_rows(rows, label="Vina log")


def _parse_pose_scores(text: str) -> list[dict[str, Any]]:
    models = [int(value) for value in re.findall(r"(?m)^MODEL\s+(\d+)\s*$", text)]
    results = [
        {
            "mode": index,
            "affinity_kcal_mol": float(match.group(1)),
            "rmsd_lb": float(match.group(2)),
            "rmsd_ub": float(match.group(3)),
        }
        for index, match in enumerate(
            re.finditer(
                r"(?m)^REMARK VINA RESULT:\s+"
                r"(-?\d+(?:\.\d+)?)\s+"
                r"(-?\d+(?:\.\d+)?)\s+"
                r"(-?\d+(?:\.\d+)?)\s*$",
                text,
            ),
            start=1,
        )
    ]
    _fail(
        models == list(range(1, len(models) + 1))
        and len(models) == len(results),
        "VINA_MAPS_VERIFIER_POSE_MODE_ORDER_INVALID",
        "PDBQT MODEL and VINA RESULT records are incomplete or out of order.",
        details={"models": models, "result_count": len(results)},
    )
    return _score_rows(results, label="output PDBQT")


def _rows_close(
    actual: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
    *,
    tolerance: float,
) -> bool:
    if len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected, strict=True):
        if left.get("mode") != right.get("mode"):
            return False
        for key in ("affinity_kcal_mol", "rmsd_lb", "rmsd_ub"):
            try:
                if abs(float(left.get(key)) - float(right.get(key))) > tolerance:
                    return False
            except (TypeError, ValueError, OverflowError):
                return False
    return True


def _run_maps_docking(
    project_root: Path,
    *,
    scoring: str,
    source_map_set: Mapping[str, Any],
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    configured = _require_ok(
        generate_vina_config(str(project_root)),
        step=f"{scoring}: generate config",
    )
    config_relative = str(configured.get("config_file") or "")
    _fail(
        bool(config_relative),
        "VINA_MAPS_VERIFIER_CONFIG_PATH_MISSING",
        "Config generation returned no project-relative path.",
    )
    project_config = _project_file(
        project_root,
        config_relative,
        label=f"{scoring} project config",
    )
    config_text = project_config.read_text(encoding="utf-8")
    prepared = _require_ok(
        prepare_vina_run(str(project_root)),
        step=f"{scoring}: prepare run",
    )
    run_id = str(prepared.get("run_id") or "")
    command = (
        prepared.get("command")
        if isinstance(prepared.get("command"), list)
        else []
    )
    expected_command = _expected_dock_command(
        vina_path,
        run_id=run_id,
        scoring=scoring,
    )
    expected_config = _expected_config_text(
        run_id=run_id,
        scoring=scoring,
    )
    run_dir = project_root / "runs" / run_id
    config_snapshot_path = _regular_file(
        run_dir / "config_snapshot.txt",
        label=f"{run_id} config snapshot",
    )
    config_snapshot_text = config_snapshot_path.read_text(encoding="utf-8")
    _fail(
        bool(run_id)
        and command == expected_command
        and "ligand = prepared/ligand.pdbqt" in config_text
        and f"scoring = {scoring}" in config_text
        and "receptor =" not in config_text
        and "center_x =" not in config_text
        and "size_x =" not in config_text
        and config_snapshot_text == expected_config,
        "VINA_MAPS_VERIFIER_DOCK_COMMAND_INVALID",
        "Prepared docking command/config differs from the independent contract.",
        details={
            "run_id": run_id,
            "expected_command": expected_command,
            "actual_command": command,
            "expected_config": expected_config,
            "actual_project_config": config_text,
            "actual_config_snapshot": config_snapshot_text,
        },
    )
    metadata = (
        prepared.get("metadata")
        if isinstance(prepared.get("metadata"), dict)
        else {}
    )
    grid_execution = (
        metadata.get("grid_execution")
        if isinstance(metadata.get("grid_execution"), dict)
        else {}
    )
    _fail(
        grid_execution.get("grid_only") is True
        and grid_execution.get("receptor_argument_used") is False
        and grid_execution.get("no_refine_equivalent") is True,
        "VINA_MAPS_VERIFIER_GRID_SEMANTICS_INVALID",
        "Prepared run did not freeze grid-only/no-refine semantics.",
        details={"grid_execution": grid_execution},
    )
    frozen_maps = _frozen_maps_evidence(
        project_root,
        run_id=run_id,
        source_map_set=source_map_set,
        scoring=scoring,
        expected_inputs=expected_inputs,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    frozen_receptor = _assert_file_contract(
        run_dir / "inputs" / "receptor.pdbqt",
        expected_inputs["receptor"],
        label=f"{run_id} frozen receptor",
    )
    frozen_ligand = _assert_file_contract(
        run_dir / "inputs" / "ligand.pdbqt",
        expected_inputs["ligand"],
        label=f"{run_id} frozen ligand",
    )
    expected_outputs = [
        run_dir / "stdout.txt",
        run_dir / "stderr.txt",
        run_dir / "log.txt",
        run_dir / "out.pdbqt",
    ]
    _fail(
        not any(path.exists() for path in expected_outputs),
        "VINA_MAPS_VERIFIER_DOCK_OUTPUT_PREEXISTED",
        "Prepared run already contained process output before execution.",
        details={"paths": [str(path) for path in expected_outputs]},
    )
    _assert_file_contract(
        vina_path,
        DEFAULT_VINA_CONTRACT,
        label=f"{run_id} Vina immediately before docking",
    )
    executed = _require_ok(
        execute_prepared_vina_run(str(project_root), run_id),
        step=f"{scoring}: execute run",
    )
    _assert_file_contract(
        vina_path,
        DEFAULT_VINA_CONTRACT,
        label=f"{run_id} Vina immediately after docking",
    )
    execution_metadata = (
        executed.get("metadata")
        if isinstance(executed.get("metadata"), dict)
        else {}
    )
    execution_vina = (
        execution_metadata.get("execution_vina")
        if isinstance(execution_metadata.get("execution_vina"), dict)
        else {}
    )
    process_identity = (
        execution_metadata.get("process_identity")
        if isinstance(execution_metadata.get("process_identity"), dict)
        else {}
    )
    binary_integrity = (
        execution_metadata.get("vina_binary_integrity")
        if isinstance(
            execution_metadata.get("vina_binary_integrity"),
            dict,
        )
        else {}
    )
    metadata_artifacts = (
        execution_metadata.get("artifacts")
        if isinstance(execution_metadata.get("artifacts"), dict)
        else {}
    )
    fixed_binary_hashes = {
        key: (
            value.get("sha256")
            if isinstance(value, Mapping)
            else None
        )
        for key, value in metadata_artifacts.items()
        if key
        in {
            "vina_binary_prepared",
            "vina_binary_executed",
            "vina_binary_observed_after_execution",
        }
    }
    process_executable = str(process_identity.get("executable_path") or "")
    process_path_matches = False
    if process_executable:
        try:
            process_path_matches = _same_file(
                Path(process_executable),
                vina_path,
            )
        except OSError:
            process_path_matches = False
    _fail(
        execution_metadata.get("status") == "finished"
        and execution_metadata.get("exit_code") == 0
        and isinstance(execution_metadata.get("pid"), int)
        and int(execution_metadata["pid"]) > 0
        and process_identity.get("pid") == execution_metadata.get("pid")
        and bool(str(process_identity.get("creation_token") or ""))
        and process_path_matches
        and execution_metadata.get("executed_command") == expected_command
        and execution_vina.get("sha256")
        == DEFAULT_VINA_CONTRACT["sha256"]
        and execution_vina.get("size_bytes")
        == DEFAULT_VINA_CONTRACT["size_bytes"]
        and binary_integrity.get("start_sha256")
        == DEFAULT_VINA_CONTRACT["sha256"]
        and binary_integrity.get("end_sha256")
        == DEFAULT_VINA_CONTRACT["sha256"]
        and binary_integrity.get("match") is True
        and set(fixed_binary_hashes.values())
        == {DEFAULT_VINA_CONTRACT["sha256"]}
        and len(fixed_binary_hashes) == 3,
        "VINA_MAPS_VERIFIER_DOCK_PROCESS_INVALID",
        "Dock metadata does not independently bind a real process to the fixed Vina.",
        details={
            "status": execution_metadata.get("status"),
            "exit_code": execution_metadata.get("exit_code"),
            "pid": execution_metadata.get("pid"),
            "process_identity": process_identity,
            "executed_command": execution_metadata.get("executed_command"),
            "execution_vina": execution_vina,
            "vina_binary_integrity": binary_integrity,
            "fixed_binary_hashes": fixed_binary_hashes,
        },
    )
    analyzed = _require_ok(
        analyze_vina_run_results(str(project_root), run_id),
        step=f"{scoring}: analyze scores",
    )
    scores = _score_rows(
        analyzed.get("scores")
        if isinstance(analyzed.get("scores"), list)
        else [],
        label="DockStart analyzed scores",
    )
    best_affinity = analyzed.get("best_affinity")
    try:
        best_affinity_number = float(best_affinity)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_BEST_AFFINITY_INVALID",
            "DockStart did not publish a numeric best affinity.",
            details={"best_affinity": best_affinity},
        ) from exc
    _fail(
        math.isfinite(best_affinity_number)
        and best_affinity_number
        == min(row["affinity_kcal_mol"] for row in scores)
        == scores[0]["affinity_kcal_mol"],
        "VINA_MAPS_VERIFIER_BEST_AFFINITY_INVALID",
        "Best affinity is inconsistent with the ordered score table.",
        details={
            "best_affinity": best_affinity,
            "scores": scores,
        },
    )
    exported = _require_ok(
        export_markdown_report(str(project_root), run_id),
        step=f"{scoring}: export report",
    )
    report_relative = str(exported.get("report_file") or "")
    report_path = _project_file(
        project_root,
        report_relative,
        label=f"{run_id} Markdown report",
    )
    report_text = report_path.read_text(encoding="utf-8")
    _fail(
        "Vina / Vinardo（预计算 maps，grid-only）" in report_text
        and "grid-only / no-refine 等价" in report_text
        and "受体 PDBQT 仅作为来源与 SHA256 溯源快照" in report_text
        and (
            "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
            in report_text
        )
        and DEFAULT_VINA_CONTRACT["sha256"] in report_text
        and expected_inputs["receptor"]["sha256"] in report_text
        and expected_inputs["ligand"]["sha256"] in report_text,
        "VINA_MAPS_VERIFIER_REPORT_SEMANTICS_INVALID",
        "Markdown report does not disclose maps semantics and scientific limits.",
    )
    artifacts = {
        "config_snapshot": _file_evidence(
            _regular_file(
                run_dir / "config_snapshot.txt",
                label=f"{run_id} config snapshot",
            ),
            relative_to=project_root,
        ),
        "stdout": _file_evidence(
            _regular_file(
                run_dir / "stdout.txt",
                label=f"{run_id} stdout",
            ),
            relative_to=project_root,
        ),
        "stderr": _file_evidence(
            _regular_file(
                run_dir / "stderr.txt",
                label=f"{run_id} stderr",
                allow_empty=True,
            ),
            relative_to=project_root,
        ),
        "log": _file_evidence(
            _regular_file(run_dir / "log.txt", label=f"{run_id} log"),
            relative_to=project_root,
        ),
        "output": _file_evidence(
            _regular_file(
                run_dir / "out.pdbqt",
                label=f"{run_id} output",
            ),
            relative_to=project_root,
        ),
        "scores": _file_evidence(
            _project_file(
                project_root,
                str(analyzed.get("scores_file") or ""),
                label=f"{run_id} scores",
            ),
            relative_to=project_root,
        ),
        "project_scores": _file_evidence(
            _project_file(
                project_root,
                "results/scores.csv",
                label=f"{run_id} project scores",
            ),
            relative_to=project_root,
        ),
        "report": _file_evidence(
            report_path,
            relative_to=project_root,
        ),
        "project_report": _file_evidence(
            _project_file(
                project_root,
                "reports/docking_report.md",
                label=f"{run_id} project report",
            ),
            relative_to=project_root,
        ),
        "metadata": _file_evidence(
            _regular_file(
                run_dir / "metadata.json",
                label=f"{run_id} metadata",
            ),
            relative_to=project_root,
        ),
    }
    csv_path = _project_file(
        project_root,
        str(analyzed.get("scores_file") or ""),
        label=f"{run_id} scores",
    )
    log_path = run_dir / "log.txt"
    output_path = run_dir / "out.pdbqt"
    csv_rows = _read_scores_csv(csv_path)
    log_rows = _parse_log_scores(log_path.read_text(encoding="utf-8"))
    pose_rows = _parse_pose_scores(output_path.read_text(encoding="utf-8"))
    _fail(
        _rows_close(scores, csv_rows, tolerance=0)
        and _rows_close(scores, log_rows, tolerance=0)
        and _rows_close(scores, pose_rows, tolerance=0.0005)
        and artifacts["stderr"]["size_bytes"] == 0
        and artifacts["stdout"]["sha256"] == artifacts["log"]["sha256"]
        and artifacts["scores"]["sha256"]
        == artifacts["project_scores"]["sha256"]
        and artifacts["report"]["sha256"]
        == artifacts["project_report"]["sha256"],
        "VINA_MAPS_VERIFIER_RESULT_CONSISTENCY_INVALID",
        "Log, PDBQT, CSV, report, or parsed scores disagree.",
        details={
            "analyzed_scores": scores,
            "csv_scores": csv_rows,
            "log_scores": log_rows,
            "pose_scores": pose_rows,
            "artifacts": artifacts,
        },
    )
    for row in scores:
        report_row = (
            f"| {row['mode']} | {row['affinity_kcal_mol']:g} | "
            f"{row['rmsd_lb']:g} | {row['rmsd_ub']:g} |"
        )
        _fail(
            report_row in report_text,
            "VINA_MAPS_VERIFIER_REPORT_SCORE_MISMATCH",
            "Markdown report does not contain the independently verified score row.",
            details={"expected_row": report_row},
        )
    oracle_evidence: dict[str, Any] = {
        "enforced": enforce_fixed_oracle,
    }
    if enforce_fixed_oracle:
        oracle = _scoring_oracle(scoring)
        oracle_scores = _score_rows(
            oracle.get("scores"),
            label=f"{scoring} fixed oracle scores",
        )
        expected_artifact_hashes = {
            "output": oracle.get("output_sha256"),
            "scores": oracle.get("scores_csv_sha256"),
        }
        actual_artifact_hashes = {
            key: artifacts[key]["sha256"]
            for key in expected_artifact_hashes
        }
        _fail(
            _rows_close(scores, oracle_scores, tolerance=0)
            and actual_artifact_hashes == expected_artifact_hashes,
            "VINA_MAPS_VERIFIER_DOCK_ORACLE_MISMATCH",
            f"The {scoring} docking result differs from the fixed real-tool oracle.",
            details={
                "expected_scores": oracle_scores,
                "actual_scores": scores,
                "expected_artifact_hashes": expected_artifact_hashes,
                "actual_artifact_hashes": actual_artifact_hashes,
            },
        )
        if run_id == "run_001":
            _fail(
                artifacts["config_snapshot"]["sha256"]
                == oracle.get("config_snapshot_sha256")
                and artifacts["log"]["sha256"]
                == oracle.get("log_sha256"),
                "VINA_MAPS_VERIFIER_DOCK_ORACLE_MISMATCH",
                f"The initial {scoring} config/log differs from the fixed oracle.",
                details={
                    "config_snapshot": artifacts["config_snapshot"],
                    "log": artifacts["log"],
                    "oracle": {
                        "config_snapshot_sha256": oracle.get(
                            "config_snapshot_sha256"
                        ),
                        "log_sha256": oracle.get("log_sha256"),
                    },
                },
            )
        oracle_evidence = {
            "enforced": True,
            "scores": oracle_scores,
            "output_sha256": oracle.get("output_sha256"),
            "scores_csv_sha256": oracle.get("scores_csv_sha256"),
        }
    return {
        "run_id": run_id,
        "prepared_command": command,
        "executed_command": execution_metadata.get("executed_command"),
        "process": {
            "pid": execution_metadata.get("pid"),
            "exit_code": execution_metadata.get("exit_code"),
            "status": execution_metadata.get("status"),
            "process_identity": process_identity,
            "vina_binary_sha256": str(
                (
                    execution_metadata.get("execution_vina")
                    if isinstance(
                        execution_metadata.get("execution_vina"),
                        dict,
                    )
                    else {}
                ).get("sha256")
                or ""
            ),
        },
        "grid_execution": grid_execution,
        "frozen_maps": frozen_maps,
        "frozen_inputs": {
            "receptor": frozen_receptor,
            "ligand": frozen_ligand,
        },
        "scores": scores,
        "score_sources": {
            "csv": csv_rows,
            "log": log_rows,
            "pdbqt": pose_rows,
        },
        "best_affinity_kcal_mol": best_affinity_number,
        "fixed_oracle": oracle_evidence,
        "artifacts": artifacts,
        "artifacts_sha256": _canonical_json_sha256(artifacts),
    }


def _run_scoring_workflow(
    *,
    work_root: Path,
    receptor_path: Path,
    ligand_path: Path,
    scoring: str,
    box: Mapping[str, float],
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    auditor: _AuditedVinaRunner,
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    project_root, setup = _create_configured_project(
        work_root=work_root,
        project_name=f"vina_maps_external_{scoring}",
        receptor_path=receptor_path,
        ligand_path=ligand_path,
        scoring=scoring,
        box=box,
        expected_inputs=expected_inputs,
    )
    marker = len(auditor.events)
    generated = _require_ok(
        generate_maps(
            str(project_root),
            {
                "scoring_function": scoring,
                "activate": False,
            },
            runner=auditor,
        ),
        step=f"{scoring}: write maps",
    )
    generation_event = auditor.one_event_since(
        marker,
        expected_kind="write_maps",
        expected_exit_zero=True,
    )
    map_set = _map_set_evidence(
        project_root,
        generated,
        scoring=scoring,
        box=box,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        process_event=generation_event,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    _assert_generation_command(
        map_set,
        scoring=scoring,
        box=box,
        vina_path=vina_path,
    )
    activation = _activate_ready_set(
        project_root,
        map_set_id=str(map_set["map_set_id"]),
        scoring=scoring,
        source_map_set=map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        auditor=auditor,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    docking = _run_maps_docking(
        project_root,
        scoring=scoring,
        source_map_set=map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    public_map_set = {
        key: value
        for key, value in map_set.items()
        if not key.startswith("_")
    }
    return {
        "scoring_function": scoring,
        "project_setup": setup,
        "generation": public_map_set,
        "activation": activation,
        "docking": docking,
        "evidence_kind": "real_vina_1.2.7_project_api",
        "_project_root": project_root,
        "_map_set": map_set,
    }


def _mutate_map_bytes(payload: bytes) -> bytes:
    mutated = bytearray(payload)
    for index in range(len(mutated) - 1, -1, -1):
        value = mutated[index]
        if value == ord("0"):
            mutated[index] = ord("1")
            return bytes(mutated)
        if value == ord("1"):
            mutated[index] = ord("0")
            return bytes(mutated)
    raise VinaMapsExternalVerificationError(
        "VINA_MAPS_VERIFIER_MAP_NOT_MUTABLE",
        "Selected map contains no ASCII digit that can be changed in place.",
    )


def _run_corruption_and_retry_gate(
    workflow: Mapping[str, Any],
    *,
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    box: Mapping[str, float],
    auditor: _AuditedVinaRunner,
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    project_root = workflow.get("_project_root")
    source_map_set = workflow.get("_map_set")
    _fail(
        isinstance(project_root, Path) and isinstance(source_map_set, dict),
        "VINA_MAPS_VERIFIER_INTERNAL_STATE_INVALID",
        "Vina workflow state is unavailable for the reliability gate.",
    )
    maps = (
        source_map_set.get("maps")
        if isinstance(source_map_set.get("maps"), list)
        else []
    )
    _fail(
        bool(maps) and isinstance(maps[0], dict),
        "VINA_MAPS_VERIFIER_MAP_INVENTORY_INVALID",
        "No map is available for the corruption gate.",
    )
    relative_path = str(maps[0].get("path") or "")
    target = _project_file(
        project_root,
        relative_path,
        label="corruption-gate target map",
    )
    original = target.read_bytes()
    corrupted = _mutate_map_bytes(original)
    _fail(
        len(corrupted) == len(original) and corrupted != original,
        "VINA_MAPS_VERIFIER_CORRUPTION_INVALID",
        "The corruption gate must change bytes without changing file size.",
    )
    target.write_bytes(corrupted)
    before_runs = sorted(
        path.name
        for path in (project_root / "runs").iterdir()
        if path.is_dir()
    )
    process_events_before_rejection = len(auditor.events)
    rejected = {
        "validate_active_maps": validate_active_maps(str(project_root)),
        "generate_vina_config": generate_vina_config(str(project_root)),
        "validate_run_prerequisites": validate_run_prerequisites(
            str(project_root)
        ),
        "prepare_vina_run": prepare_vina_run(str(project_root)),
    }
    process_events_after_rejection = len(auditor.events)
    rejection_codes = {
        key: _error_code(value)
        for key, value in rejected.items()
    }
    _fail(
        all(
            isinstance(value, dict) and value.get("ok") is False
            for value in rejected.values()
        )
        and set(rejection_codes.values())
        == {"VINA_MAPS_VALIDATION_FAILED"},
        "VINA_MAPS_VERIFIER_CORRUPTION_NOT_FAIL_CLOSED",
        "A corrupted active map did not block every config/run gate.",
        details={"codes": rejection_codes},
    )
    after_rejection_runs = sorted(
        path.name
        for path in (project_root / "runs").iterdir()
        if path.is_dir()
    )
    _fail(
        after_rejection_runs == before_runs,
        "VINA_MAPS_VERIFIER_CORRUPTION_ALLOCATED_RUN",
        "Fail-closed validation allocated a new run directory.",
        details={
            "before": before_runs,
            "after": after_rejection_runs,
        },
    )
    _fail(
        process_events_after_rejection == process_events_before_rejection,
        "VINA_MAPS_VERIFIER_CORRUPTION_STARTED_PROCESS",
        "A fail-closed corrupted-map gate invoked the audited Vina adapter.",
        details={
            "events_before": process_events_before_rejection,
            "events_after": process_events_after_rejection,
            "new_events": auditor.events[process_events_before_rejection:],
        },
    )
    marker = len(auditor.events)
    retry_generated = _require_ok(
        generate_maps(
            str(project_root),
            {
                "scoring_function": "vina",
                "activate": False,
            },
            runner=auditor,
        ),
        step="corruption retry: regenerate maps",
    )
    retry_generation_event = auditor.one_event_since(
        marker,
        expected_kind="write_maps",
        expected_exit_zero=True,
    )
    retry_map_set = _map_set_evidence(
        project_root,
        retry_generated,
        scoring="vina",
        box=box,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        process_event=retry_generation_event,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    _assert_generation_command(
        retry_map_set,
        scoring="vina",
        box=box,
        vina_path=vina_path,
    )
    _fail(
        retry_map_set["map_set_id"] != source_map_set["map_set_id"],
        "VINA_MAPS_VERIFIER_RETRY_REUSED_CORRUPT_SET",
        "Retry must publish and activate a new ready map set.",
    )
    retry_activation = _activate_ready_set(
        project_root,
        map_set_id=str(retry_map_set["map_set_id"]),
        scoring="vina",
        source_map_set=retry_map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        auditor=auditor,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    retry_docking = _run_maps_docking(
        project_root,
        scoring="vina",
        source_map_set=retry_map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    corrupt_reactivation = activate_map_set(
        str(project_root),
        str(source_map_set["map_set_id"]),
    )
    _fail(
        corrupt_reactivation.get("ok") is False,
        "VINA_MAPS_VERIFIER_CORRUPT_REACTIVATION_ACCEPTED",
        "The old corrupted map set was accepted after recovery.",
    )
    loaded = _require_ok(
        load_project(str(project_root)),
        step="corruption retry: reload project",
    )
    project = (
        loaded.get("project")
        if isinstance(loaded.get("project"), dict)
        else {}
    )
    record = (
        project.get("vina_maps")
        if isinstance(project.get("vina_maps"), dict)
        else {}
    )
    _fail(
        record.get("map_set_id") == retry_map_set["map_set_id"],
        "VINA_MAPS_VERIFIER_RETRY_ACTIVE_SET_CHANGED",
        "Rejected reactivation changed the recovered active map set.",
        details={"record": record},
    )
    public_retry_set = {
        key: value
        for key, value in retry_map_set.items()
        if not key.startswith("_")
    }
    return {
        "scenario": "same_size_map_byte_corruption_then_regenerate",
        "target_map": relative_path,
        "original": {
            "size_bytes": len(original),
            "sha256": hashlib.sha256(original).hexdigest(),
        },
        "corrupted": {
            "size_bytes": len(corrupted),
            "sha256": hashlib.sha256(corrupted).hexdigest(),
        },
        "fail_closed": {
            "api_error_codes": rejection_codes,
            "run_directories_before": before_runs,
            "run_directories_after": after_rejection_runs,
            "new_run_allocated": False,
            "audited_process_event_count_before": (
                process_events_before_rejection
            ),
            "audited_process_event_count_after": (
                process_events_after_rejection
            ),
            "external_process_started_by_rejected_gates": (
                process_events_after_rejection
                != process_events_before_rejection
            ),
        },
        "retry": {
            "generation": public_retry_set,
            "activation": retry_activation,
            "docking": retry_docking,
            "old_corrupt_reactivation_error_code": _error_code(
                corrupt_reactivation
            ),
            "active_map_set_id": str(record.get("map_set_id") or ""),
        },
        "evidence_kind": (
            "real_map_corruption_fail_closed_then_real_vina_1.2.7_retry"
        ),
    }


def _run_real_process_failure_and_retry_gate(
    *,
    work_root: Path,
    receptor_path: Path,
    valid_ligand_path: Path,
    box: Mapping[str, float],
    expected_inputs: Mapping[str, Mapping[str, Any]],
    vina_path: Path,
    auditor: _AuditedVinaRunner,
    enforce_fixed_oracle: bool,
) -> dict[str, Any]:
    invalid_ligand_path = work_root / "invalid_atom_type_ligand.pdbqt"
    invalid_ligand_path.write_text(
        INVALID_ATOM_TYPE_LIGAND,
        encoding="ascii",
        newline="\n",
    )
    invalid_ligand_evidence = _file_evidence(invalid_ligand_path)
    failure_inputs = {
        "receptor": expected_inputs["receptor"],
        "ligand": invalid_ligand_evidence,
    }
    project_root, setup = _create_configured_project(
        work_root=work_root,
        project_name="vina_maps_external_real_process_failure",
        receptor_path=receptor_path,
        ligand_path=invalid_ligand_path,
        scoring="vina",
        box=box,
        expected_inputs=failure_inputs,
    )
    maps_dir = project_root / "maps"
    before_entries = sorted(path.name for path in maps_dir.iterdir())
    marker = len(auditor.events)
    failed = generate_maps(
        str(project_root),
        {
            "scoring_function": "vina",
            "activate": False,
        },
        runner=auditor,
    )
    failure_event = auditor.one_event_since(
        marker,
        expected_kind="write_maps",
        expected_exit_zero=False,
    )
    error = (
        failed.get("error")
        if isinstance(failed, dict)
        and isinstance(failed.get("error"), dict)
        else {}
    )
    exit_code = failure_event.get("exit_code")
    after_entries = sorted(path.name for path in maps_dir.iterdir())
    published_sets = sorted(
        path.name
        for path in maps_dir.iterdir()
        if path.is_dir() and path.name.startswith("vina_")
    )
    staging_entries = sorted(
        path.name
        for path in maps_dir.iterdir()
        if path.name.startswith(".vina-maps-staging-")
    )
    _fail(
        isinstance(failed, dict)
        and failed.get("ok") is False
        and _error_code(failed) == "VINA_MAPS_GENERATION_FAILED"
        and exit_code is not None
        and exit_code != 0
        and failure_event.get("command")
        == _expected_generation_command(
            vina_path,
            scoring="vina",
            box=box,
        )
        and failure_event.get("stdout", {}).get("exists") is True
        and failure_event.get("stderr", {}).get("exists") is True
        and failure_event.get("log", {}).get("exists") is True
        and str(failed.get("manifest_file") or "") == "",
        "VINA_MAPS_VERIFIER_REAL_FAILURE_NOT_OBSERVED",
        "Invalid atom type did not produce a real non-zero Vina generation failure.",
        details={"failure": failed, "parsed_exit_code": exit_code},
    )
    _fail(
        not published_sets
        and not staging_entries
        and after_entries == before_entries,
        "VINA_MAPS_VERIFIER_REAL_FAILURE_PUBLISHED_PARTIAL_SET",
        "A failed real Vina process left a published or staged map set.",
        details={
            "before_entries": before_entries,
            "after_entries": after_entries,
            "published_sets": published_sets,
            "staging_entries": staging_entries,
        },
    )
    loaded_after_failure = _require_ok(
        load_project(str(project_root)),
        step="real process failure: reload project",
    )
    project_after_failure = (
        loaded_after_failure.get("project")
        if isinstance(loaded_after_failure.get("project"), dict)
        else {}
    )
    record_after_failure = (
        project_after_failure.get("vina_maps")
        if isinstance(project_after_failure.get("vina_maps"), dict)
        else {}
    )
    protocol_after_failure = (
        project_after_failure.get("docking_protocol")
        if isinstance(
            project_after_failure.get("docking_protocol"),
            dict,
        )
        else {}
    )
    _fail(
        not record_after_failure
        and str(protocol_after_failure.get("grid_source") or "receptor")
        == "receptor",
        "VINA_MAPS_VERIFIER_REAL_FAILURE_CHANGED_PROJECT",
        "Failed map generation changed the active project grid.",
        details={
            "vina_maps": record_after_failure,
            "docking_protocol": protocol_after_failure,
        },
    )
    _assert_file_contract(
        valid_ligand_path,
        expected_inputs["ligand"],
        label="real process failure valid retry ligand before import",
    )
    _require_ok(
        import_ligand_pdbqt(
            str(project_root),
            str(valid_ligand_path),
        ),
        step="real process failure: import valid retry ligand",
    )
    prepared_retry_ligand = _project_file(
        project_root,
        "prepared/ligand.pdbqt",
        label="real process failure imported retry ligand",
    )
    _assert_file_contract(
        prepared_retry_ligand,
        expected_inputs["ligand"],
        label="real process failure imported retry ligand",
    )
    _assert_file_contract(
        valid_ligand_path,
        expected_inputs["ligand"],
        label="real process failure valid retry ligand after import",
    )
    marker = len(auditor.events)
    retry_generated = _require_ok(
        generate_maps(
            str(project_root),
            {
                "scoring_function": "vina",
                "activate": False,
            },
            runner=auditor,
        ),
        step="real process failure: regenerate maps",
    )
    retry_generation_event = auditor.one_event_since(
        marker,
        expected_kind="write_maps",
        expected_exit_zero=True,
    )
    retry_map_set = _map_set_evidence(
        project_root,
        retry_generated,
        scoring="vina",
        box=box,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        process_event=retry_generation_event,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    _assert_generation_command(
        retry_map_set,
        scoring="vina",
        box=box,
        vina_path=vina_path,
    )
    _fail(
        retry_map_set["map_set_id"]
        == str(failed.get("map_set_id") or ""),
        "VINA_MAPS_VERIFIER_REAL_FAILURE_ID_NOT_REUSABLE",
        "A failed unpublished set consumed the next public map_set_id.",
        details={
            "failed_id": failed.get("map_set_id"),
            "retry_id": retry_map_set["map_set_id"],
        },
    )
    retry_activation = _activate_ready_set(
        project_root,
        map_set_id=str(retry_map_set["map_set_id"]),
        scoring="vina",
        source_map_set=retry_map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        auditor=auditor,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    retry_docking = _run_maps_docking(
        project_root,
        scoring="vina",
        source_map_set=retry_map_set,
        expected_inputs=expected_inputs,
        vina_path=vina_path,
        enforce_fixed_oracle=enforce_fixed_oracle,
    )
    public_retry_set = {
        key: value
        for key, value in retry_map_set.items()
        if not key.startswith("_")
    }
    return {
        "scenario": (
            "invalid_ZZ_ligand_real_vina_nonzero_exit_then_valid_retry"
        ),
        "project_setup": setup,
        "invalid_input": invalid_ligand_evidence,
        "failure": {
            "project_api": "generate_maps",
            "error_code": _error_code(failed),
            "exit_code": exit_code,
            "map_set_id_reserved_but_not_published": str(
                failed.get("map_set_id") or ""
            ),
            "manifest_file": str(failed.get("manifest_file") or ""),
            "raw_error": str(error.get("raw_error") or ""),
            "audited_process": failure_event,
            "published_sets_after_failure": published_sets,
            "staging_entries_after_failure": staging_entries,
            "project_grid_source_after_failure": str(
                protocol_after_failure.get("grid_source") or "receptor"
            ),
            "command_and_logs_retained_in_json": True,
            "retention_note": (
                "Core staging cleanup removes failed private files; the verifier "
                "captures command, PID, exit code, and bounded stdout/stderr/log "
                "evidence before the staging directory is removed."
            ),
        },
        "retry": {
            "generation": public_retry_set,
            "activation": retry_activation,
            "docking": retry_docking,
        },
        "evidence_kind": (
            "real_vina_1.2.7_nonzero_process_failure_no_publication_"
            "then_real_project_api_retry"
        ),
    }


def _validated_box(box: Mapping[str, Any] | None) -> dict[str, float]:
    supplied = dict(DEFAULT_BOX)
    if box is not None:
        supplied.update(dict(box))
    keys = set(DEFAULT_BOX)
    _fail(
        set(supplied) == keys,
        "VINA_MAPS_VERIFIER_BOX_INVALID",
        "Box must contain exactly center_x/y/z and size_x/y/z.",
        details={"keys": sorted(supplied)},
    )
    parsed: dict[str, float] = {}
    for key in sorted(keys):
        try:
            value = float(supplied[key])
        except (TypeError, ValueError) as exc:
            raise VinaMapsExternalVerificationError(
                "VINA_MAPS_VERIFIER_BOX_INVALID",
                f"Box value {key} is not numeric.",
                details={"value": supplied[key]},
            ) from exc
        _fail(
            math.isfinite(value),
            "VINA_MAPS_VERIFIER_BOX_INVALID",
            f"Box value {key} must be finite.",
        )
        if key.startswith("size_"):
            _fail(
                value > 0,
                "VINA_MAPS_VERIFIER_BOX_INVALID",
                f"Box value {key} must be positive.",
            )
        parsed[key] = value
    return parsed


def verify_vina_maps_external(
    *,
    vina_executable: str | Path = DEFAULT_VINA,
    receptor_pdbqt: str | Path = DEFAULT_RECEPTOR,
    ligand_pdbqt: str | Path = DEFAULT_LIGAND,
    box: Mapping[str, Any] | None = None,
    keep_workdir: bool = False,
) -> dict[str, Any]:
    """Run the complete real-process gate and return JSON-serializable evidence."""

    steps: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    caught: VinaMapsExternalVerificationError | None = None
    work_root = Path(
        tempfile.mkdtemp(prefix="DockStart_vina_maps_external_")
    )
    previous_settings = os.environ.get(SETTINGS_ENV_VAR)
    previous_resources = os.environ.get(RESOURCE_DIR_ENV_VAR)
    settings_path = work_root / "dockstart_settings.json"
    isolated_resources = work_root / "no_bundled_toolchain"
    try:
        vina_path = _regular_file(
            vina_executable,
            label="AutoDock Vina executable",
        )
        receptor_path = _regular_file(
            receptor_pdbqt,
            label="receptor PDBQT",
        )
        ligand_path = _regular_file(
            ligand_pdbqt,
            label="ligand PDBQT",
        )
        parsed_box = _validated_box(box)
        input_profile = _input_acceptance_profile(
            receptor_path,
            ligand_path,
            parsed_box,
        )
        formal_acceptance = bool(
            input_profile["counts_as_default_acceptance"]
        )
        snapshot_root = work_root / "fixed_snapshots"
        receptor_snapshot_path = snapshot_root / "inputs" / "receptor.pdbqt"
        ligand_snapshot_path = snapshot_root / "inputs" / "ligand.pdbqt"
        vina_snapshot_path = snapshot_root / "tool" / "vina.exe"
        receptor_snapshot = _snapshot_file(
            receptor_path,
            receptor_snapshot_path,
            input_profile["receptor"],
            label="receptor PDBQT",
        )
        ligand_snapshot = _snapshot_file(
            ligand_path,
            ligand_snapshot_path,
            input_profile["ligand"],
            label="ligand PDBQT",
        )
        vina_source = _assert_file_contract(
            vina_path,
            DEFAULT_VINA_CONTRACT,
            label="supplied AutoDock Vina 1.2.7 binary",
        )
        vina_snapshot = _snapshot_file(
            vina_path,
            vina_snapshot_path,
            DEFAULT_VINA_CONTRACT,
            label="AutoDock Vina 1.2.7 binary",
        )
        expected_inputs = {
            "receptor": receptor_snapshot["snapshot"],
            "ligand": ligand_snapshot["snapshot"],
        }
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(isolated_resources)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(vina=str(vina_snapshot_path)),
            )
        )
        tool = _require_exact_vina_127(
            vina_snapshot_path,
            isolated_resource_dir=isolated_resources,
        )
        auditor = _AuditedVinaRunner(
            vina_snapshot_path,
            DEFAULT_VINA_CONTRACT,
        )
        steps["tool_and_inputs"] = {
            "ok": True,
            "network_or_download_used": False,
            "tool": {
                **tool,
                "supplied": vina_source,
                "private_snapshot": vina_snapshot,
            },
            "inputs": {
                "profile": input_profile,
                "receptor": input_profile["receptor"],
                "ligand": input_profile["ligand"],
                "private_snapshots": {
                    "receptor": receptor_snapshot,
                    "ligand": ligand_snapshot,
                },
                "box": parsed_box,
            },
        }
        workflows: dict[str, Any] = {}
        internal_workflows: dict[str, Any] = {}
        for scoring in SUPPORTED_SCORING:
            workflow = _run_scoring_workflow(
                work_root=work_root,
                receptor_path=receptor_snapshot_path,
                ligand_path=ligand_snapshot_path,
                scoring=scoring,
                box=parsed_box,
                expected_inputs=expected_inputs,
                vina_path=vina_snapshot_path,
                auditor=auditor,
                enforce_fixed_oracle=formal_acceptance,
            )
            internal_workflows[scoring] = workflow
            workflows[scoring] = {
                key: value
                for key, value in workflow.items()
                if not key.startswith("_")
            }
        steps["scoring_workflows"] = {
            "ok": True,
            "workflows": workflows,
        }
        process_failure = _run_real_process_failure_and_retry_gate(
            work_root=work_root,
            receptor_path=receptor_snapshot_path,
            valid_ligand_path=ligand_snapshot_path,
            box=parsed_box,
            expected_inputs=expected_inputs,
            vina_path=vina_snapshot_path,
            auditor=auditor,
            enforce_fixed_oracle=formal_acceptance,
        )
        steps["real_process_failure_and_retry"] = {
            "ok": True,
            **process_failure,
        }
        reliability = _run_corruption_and_retry_gate(
            internal_workflows["vina"],
            expected_inputs=expected_inputs,
            vina_path=vina_snapshot_path,
            box=parsed_box,
            auditor=auditor,
            enforce_fixed_oracle=formal_acceptance,
        )
        steps["corruption_and_retry"] = {
            "ok": True,
            **reliability,
        }
        event_kinds = [
            str(event.get("kind") or "")
            for event in auditor.events
        ]
        _fail(
            len(auditor.events) == 9
            and event_kinds.count("write_maps") == 5
            and event_kinds.count("maps_score_only_probe") == 4,
            "VINA_MAPS_VERIFIER_AUDITED_PROCESS_COVERAGE_INVALID",
            "The expected real map-generation/probe process set was incomplete.",
            details={
                "event_count": len(auditor.events),
                "event_kinds": event_kinds,
                "events": auditor.events,
            },
        )
        steps["audited_adapter_processes"] = {
            "ok": True,
            "event_count": len(auditor.events),
            "events": auditor.events,
        }
        acceptance = {
            "input_profile": input_profile["kind"],
            "default_fixture_gate_satisfied": formal_acceptance,
            "workflow_completed": True,
            "formal_acceptance": formal_acceptance,
            "qualification": input_profile["qualification"],
        }
        coverage = {
            "real_vina_and_vinardo_write_maps_probe_freeze_dock": (
                set(workflows) == set(SUPPORTED_SCORING)
                and all(
                    workflow.get("docking", {})
                    .get("process", {})
                    .get("status")
                    == "finished"
                    for workflow in workflows.values()
                )
            ),
            "real_vina_nonzero_generation_exit_and_retry": (
                process_failure.get("failure", {}).get("exit_code") not in (0, None)
                and process_failure.get("retry", {})
                .get("docking", {})
                .get("process", {})
                .get("status")
                == "finished"
            ),
            "real_same_size_map_corruption_fail_closed_and_retry": (
                reliability.get("fail_closed", {})
                .get("external_process_started_by_rejected_gates")
                is False
                and reliability.get("retry", {})
                .get("docking", {})
                .get("process", {})
                .get("status")
                == "finished"
            ),
            "real_concurrent_vina_generation_processes": False,
            "concurrency_boundary": (
                "Concurrent allocation/publication/activation is covered by "
                "backend.tests.test_vina_maps_reliability with concurrent "
                "public API calls and a simulated runner; this external "
                "verifier does not claim real concurrent Vina processes."
            ),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "ok": formal_acceptance,
            "scope": (
                "real_vina_1.2.7_write_maps_activation_freeze_dock_"
                "results_report_nonzero_process_failure_corruption_and_retry"
            ),
            "network_or_download_used": False,
            "acceptance": acceptance,
            "coverage": coverage,
            "scientific_interpretation": (
                "software workflow and provenance gate only; scores are not "
                "experimental binding evidence"
            ),
            "steps": steps,
            "evidence_sha256": _canonical_json_sha256(
                {
                    "acceptance": acceptance,
                    "coverage": coverage,
                    "steps": steps,
                }
            ),
        }
        if not formal_acceptance:
            result["error"] = {
                "code": "VINA_MAPS_VERIFIER_CUSTOM_PROBE_NOT_ACCEPTANCE",
                "message": (
                    "The custom input/Box workflow completed, but it cannot "
                    "satisfy the pinned default-fixture acceptance gate."
                ),
                "details": acceptance,
            }
    except VinaMapsExternalVerificationError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve structured CLI boundary.
        caught = VinaMapsExternalVerificationError(
            "VINA_MAPS_VERIFIER_UNEXPECTED_ERROR",
            "The real Vina maps verifier failed unexpectedly.",
            details={
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
    finally:
        restore_errors: list[str] = []
        try:
            if previous_settings is None:
                os.environ.pop(SETTINGS_ENV_VAR, None)
            else:
                os.environ[SETTINGS_ENV_VAR] = previous_settings
        except Exception as exc:  # noqa: BLE001
            restore_errors.append(f"{SETTINGS_ENV_VAR}: {exc}")
        try:
            if previous_resources is None:
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
            else:
                os.environ[RESOURCE_DIR_ENV_VAR] = previous_resources
        except Exception as exc:  # noqa: BLE001
            restore_errors.append(f"{RESOURCE_DIR_ENV_VAR}: {exc}")
        cleaned = False
        cleanup_error = ""
        if keep_workdir:
            cleaned = False
        else:
            try:
                import shutil

                shutil.rmtree(work_root)
                cleaned = not work_root.exists()
            except Exception as exc:  # noqa: BLE001
                cleanup_error = str(exc)
        environment = {
            "settings_variable_restored": (
                os.environ.get(SETTINGS_ENV_VAR) == previous_settings
                if previous_settings is not None
                else SETTINGS_ENV_VAR not in os.environ
            ),
            "resources_variable_restored": (
                os.environ.get(RESOURCE_DIR_ENV_VAR) == previous_resources
                if previous_resources is not None
                else RESOURCE_DIR_ENV_VAR not in os.environ
            ),
            "restore_errors": restore_errors,
            "work_directory": str(work_root),
            "work_directory_kept": keep_workdir,
            "work_directory_cleaned": cleaned,
            "cleanup_error": cleanup_error,
        }
        if result is None:
            assert caught is not None
            result = {
                "schema_version": SCHEMA_VERSION,
                "verifier_id": VERIFIER_ID,
                "ok": False,
                "error": {
                    "code": caught.code,
                    "message": caught.message,
                    "details": caught.details,
                },
                "steps": steps,
            }
        result["environment"] = environment
        if (
            restore_errors
            or not environment["settings_variable_restored"]
            or not environment["resources_variable_restored"]
            or (not keep_workdir and not cleaned)
        ):
            result["ok"] = False
            result["error"] = {
                "code": "VINA_MAPS_VERIFIER_CLEANUP_FAILED",
                "message": (
                    "The verifier could not restore its process environment "
                    "or remove the temporary projects."
                ),
                "details": environment,
            }
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    destination = path.expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay Vina and Vinardo precomputed-maps workflows with an "
            "exact external AutoDock Vina 1.2.7 executable."
        )
    )
    parser.add_argument("--vina", default=str(DEFAULT_VINA))
    parser.add_argument("--receptor", default=str(DEFAULT_RECEPTOR))
    parser.add_argument("--ligand", default=str(DEFAULT_LIGAND))
    parser.add_argument("--center-x", type=float, default=DEFAULT_BOX["center_x"])
    parser.add_argument("--center-y", type=float, default=DEFAULT_BOX["center_y"])
    parser.add_argument("--center-z", type=float, default=DEFAULT_BOX["center_z"])
    parser.add_argument("--size-x", type=float, default=DEFAULT_BOX["size_x"])
    parser.add_argument("--size-y", type=float, default=DEFAULT_BOX["size_y"])
    parser.add_argument("--size-z", type=float, default=DEFAULT_BOX["size_z"])
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep isolated projects for manual inspection.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path; stdout always receives the same payload.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = verify_vina_maps_external(
        vina_executable=args.vina,
        receptor_pdbqt=args.receptor,
        ligand_pdbqt=args.ligand,
        box={
            "center_x": args.center_x,
            "center_y": args.center_y,
            "center_z": args.center_z,
            "size_x": args.size_x,
            "size_y": args.size_y,
            "size_z": args.size_z,
        },
        keep_workdir=bool(args.keep_workdir),
    )
    if args.output:
        _write_json(Path(args.output), result)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
