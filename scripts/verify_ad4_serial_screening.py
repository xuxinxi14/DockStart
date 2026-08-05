#!/usr/bin/env python3
"""Exercise DockStart's serial AD4 screening reliability contract offline.

Twelve deterministic audit ligands alternate the exact official 5X72 P59 and
P69 bytes with one unique REMARK line.  They are intentionally not presented
as a chemically diverse library.  The verifier proves frozen-map reuse, live
cancellation, tamper-blocked resume, explicit resume, one isolated failure,
queue continuation, ranking, report, archive, and read-only ZIP export.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
SCRIPT_ROOT = Path(__file__).resolve().parent
for import_root in (SCRIPT_ROOT, BACKEND_ROOT):
    while str(import_root) in sys.path:
        sys.path.remove(str(import_root))
    sys.path.insert(0, str(import_root))

from ad4_v0141_common import (  # noqa: E402
    SHA256_PATTERN,
    attach_loaded_import_origins,
    audit_generated_maps,
    bind_source_provenance_or_error,
    capture_source_bound_session,
    contained_project_file,
    error_payload,
    file_identity,
    isolated_settings,
    load_fixture_manifest,
    option_values,
    pdbqt_atom_types,
    require,
    require_api,
    require_output_separate_from_inputs,
    same_paths,
    unbound_preflight_result,
    verify_snapshot_record,
    write_result,
)


VERIFIER_ID = "dockstart_ad4_serial_screening_v0141"
MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "serial_screening_ad4"
    / "source_manifest.json"
)
DEFAULT_VINA = ROOT / "resources" / "vina" / "vina.exe"
_BACKEND_LOADED = False


def _load_backend_modules() -> None:
    global _BACKEND_LOADED
    if _BACKEND_LOADED:
        return
    from adapters import autogrid_adapter as loaded_autogrid_adapter
    from adapters import vina_adapter as loaded_vina_adapter
    from dockstart_core.autogrid import (
        generate_maps as loaded_generate_maps,
        set_scoring_protocol as loaded_set_scoring_protocol,
        validate_active_maps as loaded_validate_active_maps,
    )
    from dockstart_core.project import (
        create_project as loaded_create_project,
        import_ligand_pdbqt as loaded_import_ligand_pdbqt,
        import_receptor_pdbqt as loaded_import_receptor_pdbqt,
        update_box_params as loaded_update_box_params,
        update_vina_params as loaded_update_vina_params,
    )
    from dockstart_core.screening import (
        archive_screening as loaded_archive_screening,
        create_screening as loaded_create_screening,
        export_screening_archive_zip as loaded_export_screening_archive_zip,
        export_screening_markdown_report as loaded_export_screening_markdown_report,
        request_screening_cancel as loaded_request_screening_cancel,
        resume_screening as loaded_resume_screening,
        run_screening as loaded_run_screening,
    )

    globals().update(
        {
            "autogrid_adapter": loaded_autogrid_adapter,
            "vina_adapter": loaded_vina_adapter,
            "generate_maps": loaded_generate_maps,
            "set_scoring_protocol": loaded_set_scoring_protocol,
            "validate_active_maps": loaded_validate_active_maps,
            "create_project": loaded_create_project,
            "import_ligand_pdbqt": loaded_import_ligand_pdbqt,
            "import_receptor_pdbqt": loaded_import_receptor_pdbqt,
            "update_box_params": loaded_update_box_params,
            "update_vina_params": loaded_update_vina_params,
            "archive_screening": loaded_archive_screening,
            "create_screening": loaded_create_screening,
            "export_screening_archive_zip": loaded_export_screening_archive_zip,
            "export_screening_markdown_report": loaded_export_screening_markdown_report,
            "request_screening_cancel": loaded_request_screening_cancel,
            "resume_screening": loaded_resume_screening,
            "run_screening": loaded_run_screening,
        }
    )
    _BACKEND_LOADED = True


def _capture_source_session() -> dict[str, Any]:
    source_session = capture_source_bound_session(
        repo_root=ROOT,
        verifier_id=VERIFIER_ID,
        verifier_path=Path(__file__),
        fixture_manifest_paths=[MANIFEST_PATH],
        validate_import_origins=False,
    )
    _load_backend_modules()
    return attach_loaded_import_origins(
        source_session,
        repo_root=ROOT,
        verifier_id=VERIFIER_ID,
        verifier_path=Path(__file__),
        fixture_manifest_paths=[MANIFEST_PATH],
    )


def _bound_result(
    payload: Mapping[str, Any],
    source_session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return bind_source_provenance_or_error(
        payload,
        repo_root=ROOT,
        verifier_path=Path(__file__),
        fixture_manifest_paths=[MANIFEST_PATH],
        source_session=source_session,
        expected_verifier_id=VERIFIER_ID,
    )


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _tool_record(result: Any, identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(identity),
        "detected_status": str(getattr(result, "status", "")),
        "detected_version": str(getattr(result, "version", "")),
        "detected_path": str(getattr(result, "path", "")),
        "source": str(getattr(result, "source", "")),
    }


def _require_tools(
    vina_path: Path,
    vina_identity: Mapping[str, Any],
    autogrid_path: Path,
    autogrid_identity: Mapping[str, Any],
) -> dict[str, Any]:
    vina_detection = vina_adapter.detect(str(vina_path), bundled_path="")
    require(
        getattr(vina_detection, "status", "") == "ok"
        and getattr(vina_detection, "version", "") == "1.2.7"
        and same_paths([str(getattr(vina_detection, "path", ""))], [vina_path]),
        "AD4_SCREENING_VINA_DETECTION_FAILED",
        "DockStart did not resolve the exact pinned Vina 1.2.7 executable.",
        details={"detection": _tool_record(vina_detection, vina_identity)},
    )
    autogrid_detection = autogrid_adapter.detect(str(autogrid_path))
    require(
        getattr(autogrid_detection, "status", "") == "ok"
        and _version_tuple(str(getattr(autogrid_detection, "version", "")))
        >= (4, 2, 6)
        and same_paths(
            [str(getattr(autogrid_detection, "path", ""))],
            [autogrid_path],
        ),
        "AD4_SCREENING_AUTOGRID_DETECTION_FAILED",
        "DockStart did not resolve the caller-pinned AutoGrid4 4.2.6+ executable.",
        details={"detection": _tool_record(autogrid_detection, autogrid_identity)},
    )
    return {
        "vina": _tool_record(vina_detection, vina_identity),
        "autogrid4": _tool_record(autogrid_detection, autogrid_identity),
    }


class _AuditRunner:
    def __init__(
        self,
        project_root: Path,
        *,
        cancel_order: int,
        failed_order: int,
        failed_exit_code: int,
    ) -> None:
        self.project_root = project_root
        self.cancel_order = int(cancel_order)
        self.failed_order = int(failed_order)
        self.failed_exit_code = int(failed_exit_code)
        self.cancel_enabled = True
        self.failure_enabled = False
        self.cancel_evidence: dict[str, Any] = {}
        self.commands: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        item = kwargs.get("item") if isinstance(kwargs.get("item"), Mapping) else {}
        order = int(item.get("order") or 0)
        command = [str(value) for value in kwargs.get("command") or []]
        self.commands.append({"order": order, "command": command})
        if self.failure_enabled and order == self.failed_order:
            return {
                "exit_code": self.failed_exit_code,
                "error": "intentional v0.14.1 serial-screening continuation audit failure",
            }

        original_on_started = kwargs.get("on_started")

        def on_started(pid: int) -> None:
            if callable(original_on_started):
                original_on_started(pid)
            if (
                self.cancel_enabled
                and order == self.cancel_order
                and not self.cancel_evidence
            ):
                alive = vina_adapter.is_process_running(int(pid))
                response = request_screening_cancel(self.project_root.as_posix())
                self.cancel_evidence = {
                    "item_order": order,
                    "pid": int(pid),
                    "process_alive_before_request": alive,
                    "response": response,
                }

        result = vina_adapter.run_managed(
            command,
            kwargs["cwd"],
            kwargs["stdout_path"],
            kwargs["stderr_path"],
            kwargs["log_path"],
            on_started=on_started,
        )
        return {
            "pid": result.pid,
            "exit_code": result.exit_code,
            "error": result.error,
        }


def _derived_ligands(
    project_root: Path,
    p59_path: Path,
    p69_path: Path,
    source_order: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    output_root = project_root / "prepared" / "v0141_serial_audit"
    output_root.mkdir(parents=True, exist_ok=False)
    source_lookup = {"P59": p59_path, "P69": p69_path}
    relative_paths: list[str] = []
    evidence: list[dict[str, Any]] = []
    for order, source_key in enumerate(source_order, start=1):
        require(
            source_key in source_lookup,
            "AD4_SCREENING_WORKLOAD_CONTRACT_INVALID",
            "Serial-screening source order contains an unknown ligand key.",
            details={"order": order, "source_key": source_key},
        )
        source = source_lookup[source_key]
        marker = (
            f"REMARK DOCKSTART V0141 SERIAL AUDIT ITEM {order:02d} SOURCE {source_key}\n"
        ).encode("ascii")
        destination = output_root / f"audit_{order:02d}_{source_key.lower()}.pdbqt"
        destination.write_bytes(marker + source.read_bytes())
        relative = destination.relative_to(project_root).as_posix()
        relative_paths.append(relative)
        evidence.append(
            {
                "order": order,
                "source": source_key,
                "marker": marker.decode("ascii").rstrip("\n"),
                **file_identity(destination, label=f"derived audit ligand {order}"),
                "relative_path": relative,
            }
        )
    require(
        len({value["sha256"] for value in evidence}) == len(evidence),
        "AD4_SCREENING_WORKLOAD_NOT_UNIQUE",
        "Derived serial-screening audit ligand bytes are not unique.",
    )
    return relative_paths, evidence


def _audit_attempts(
    project_root: Path,
    state: Mapping[str, Any],
    *,
    vina_identity: Mapping[str, Any],
    expected_count: int,
    expected_map_prefix: str,
    expected_project_map_prefix: str,
    failed_order: int,
    failed_exit_code: int,
) -> dict[str, Any]:
    items = state.get("items") if isinstance(state.get("items"), list) else []
    require(
        len(items) == expected_count,
        "AD4_SCREENING_ITEM_COUNT_MISMATCH",
        "Final queue does not contain the frozen audit workload.",
        details={"expected": expected_count, "actual": len(items)},
    )
    map_prefixes: set[str] = set()
    attempts_evidence: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: int(value.get("order") or 0)):
        order = int(item.get("order") or 0)
        attempts = item.get("attempts") if isinstance(item.get("attempts"), list) else []
        require(
            int(item.get("attempt_count") or 0) == 1 and len(attempts) == 1,
            "AD4_SCREENING_ATTEMPT_COUNT_INVALID",
            "Every audit ligand must have exactly one independent attempt.",
            details={"order": order, "item": item},
        )
        attempt = attempts[0] if isinstance(attempts[0], Mapping) else {}
        command = [str(value) for value in attempt.get("command") or []]
        config_values = option_values(command, "--config")
        maps_values = option_values(command, "--maps")
        scoring_values = option_values(command, "--scoring")
        require(
            config_values == ["config.txt"]
            and len(maps_values) == 1
            and scoring_values == ["ad4"]
            and "--receptor" not in command
            and same_paths([command[0]], [Path(vina_identity["path"])]),
            "AD4_SCREENING_COMMAND_CONTRACT_INVALID",
            "An attempt did not use the pinned Vina with frozen AD4 maps.",
            details={"order": order, "command": command},
        )
        map_prefixes.add(maps_values[0])
        attempt_directory = str(attempt.get("directory") or "")
        config_file = contained_project_file(
            project_root,
            (Path(attempt_directory) / config_values[0]).as_posix(),
            label=f"screening attempt {order} config",
        )
        resolved_map_prefix = (config_file.parent / maps_values[0]).resolve()
        frozen_map_prefix = (
            project_root / expected_project_map_prefix
        ).resolve()
        require(
            resolved_map_prefix == frozen_map_prefix,
            "AD4_SCREENING_MAP_PREFIX_TARGET_MISMATCH",
            "An attempt map prefix does not resolve to the frozen screening maps.",
            details={
                "order": order,
                "resolved_map_prefix": str(resolved_map_prefix),
                "frozen_map_prefix": str(frozen_map_prefix),
            },
        )
        config_text = config_file.read_text(encoding="utf-8", errors="strict")
        require(
            "ligand = ligand.pdbqt" in config_text
            and "scoring = ad4" in config_text
            and "receptor =" not in config_text,
            "AD4_SCREENING_CONFIG_CONTRACT_INVALID",
            "An attempt config is not an independent ligand-only AD4 maps config.",
            details={"order": order, "config_text": config_text},
        )
        snapshots = (
            attempt.get("input_snapshots")
            if isinstance(attempt.get("input_snapshots"), Mapping)
            else {}
        )
        receptor_snapshot = verify_snapshot_record(
            project_root,
            snapshots.get("receptor") or {},
            label=f"screening attempt {order} receptor",
        )
        ligand_snapshot = verify_snapshot_record(
            project_root,
            snapshots.get("ligand") or {},
            label=f"screening attempt {order} ligand",
        )
        config_snapshot = verify_snapshot_record(
            project_root,
            attempt.get("config_snapshot") or {},
            label=f"screening attempt {order} config snapshot",
        )
        vina_snapshot = (
            attempt.get("vina_snapshot")
            if isinstance(attempt.get("vina_snapshot"), Mapping)
            else {}
        )
        expected_status = "failed" if order == failed_order else "succeeded"
        expected_exit = failed_exit_code if order == failed_order else 0
        require(
            attempt.get("status") == expected_status
            and item.get("status") == expected_status
            and int(attempt.get("exit_code") or 0) == expected_exit
            and (attempt.get("integrity") or {}).get("status") == "verified"
            and str(vina_snapshot.get("sha256") or "") == vina_identity["sha256"]
            and int(vina_snapshot.get("size_bytes") or 0)
            == vina_identity["size_bytes"],
            "AD4_SCREENING_ATTEMPT_EVIDENCE_INVALID",
            "An attempt did not converge to the expected terminal state with verified evidence.",
            details={"order": order, "attempt": attempt, "item": item},
        )
        output: dict[str, Any] | None = None
        if expected_status == "succeeded":
            output_path = contained_project_file(
                project_root,
                str(item.get("best_output_file") or ""),
                label=f"screening attempt {order} best output",
            )
            output = file_identity(output_path, label=f"screening attempt {order} best output")
            require(
                output["sha256"] == str(item.get("best_output_sha256") or "")
                and output["size_bytes"] == int(item.get("best_output_size_bytes") or 0),
                "AD4_SCREENING_OUTPUT_IDENTITY_MISMATCH",
                "A successful screening output differs from its frozen item evidence.",
                details={"order": order, "output": output, "item": item},
            )
        attempts_evidence.append(
            {
                "order": order,
                "item_id": item.get("item_id"),
                "status": expected_status,
                "exit_code": int(attempt.get("exit_code") or 0),
                "command": command,
                "receptor": receptor_snapshot,
                "ligand": ligand_snapshot,
                "config": config_snapshot,
                "output": output,
                "best_affinity_kcal_mol": item.get("best_affinity_kcal_mol"),
            }
        )
    require(
        map_prefixes == {str(expected_map_prefix)},
        "AD4_SCREENING_MAP_PREFIX_NOT_REUSED",
        "Serial attempts did not reuse one frozen AD4 map prefix.",
        details={
            "expected_map_prefix": str(expected_map_prefix),
            "map_prefixes": sorted(map_prefixes),
        },
    )
    return {
        "attempts": attempts_evidence,
        "frozen_attempt_map_prefix": next(iter(map_prefixes)),
    }


def _audit_ranked_csv(project_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    outputs = state.get("outputs") if isinstance(state.get("outputs"), Mapping) else {}
    summary_path = contained_project_file(
        project_root,
        str(outputs.get("summary_csv") or ""),
        label="screening summary CSV",
    )
    top_path = contained_project_file(
        project_root,
        str(outputs.get("top_n_csv") or ""),
        label="screening ranked Top N CSV",
    )
    with top_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    affinities = [float(row["best_affinity_kcal_mol"]) for row in rows]
    require(
        len(rows) == 11
        and [int(row["rank"]) for row in rows] == list(range(1, 12))
        and affinities == sorted(affinities),
        "AD4_SCREENING_RANKING_INVALID",
        "Top N CSV is not a complete ascending-affinity ranking of successful items.",
        details={"rows": rows},
    )
    return {
        "summary": file_identity(summary_path, label="screening summary CSV"),
        "ranked_top_n": file_identity(top_path, label="screening ranked Top N CSV"),
        "ranked_item_ids": [row["item_id"] for row in rows],
        "ranked_affinities_kcal_mol": affinities,
    }


def verify(
    *,
    receptor_pdbqt: Path,
    ligand_p59_pdbqt: Path,
    ligand_p69_pdbqt: Path,
    vina_path: Path,
    autogrid4_path: Path,
    autogrid4_sha256: str,
    source_session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if source_session is None:
        source_session = _capture_source_session()
    manifest = load_fixture_manifest(
        MANIFEST_PATH,
        "ad4_serial_screening_5x72_external",
    )
    source_contracts = manifest["source_files"]
    receptor_identity = file_identity(
        receptor_pdbqt,
        label="official 5X72 receptor PDBQT",
        expected=source_contracts["receptor_pdbqt"],
    )
    p59_identity = file_identity(
        ligand_p59_pdbqt,
        label="official 5X72 P59 ligand PDBQT",
        expected=source_contracts["ligand_p59_pdbqt"],
    )
    p69_identity = file_identity(
        ligand_p69_pdbqt,
        label="official 5X72 P69 ligand PDBQT",
        expected=source_contracts["ligand_p69_pdbqt"],
    )
    vina_identity = file_identity(
        vina_path,
        label="AutoDock Vina 1.2.7",
        expected=manifest["toolchain"]["vina"],
    )
    autogrid_identity = file_identity(
        autogrid4_path,
        label="caller-pinned AutoGrid4",
    )
    expected_autogrid_sha = str(autogrid4_sha256 or "").strip().lower()
    require(
        SHA256_PATTERN.fullmatch(expected_autogrid_sha) is not None
        and autogrid_identity["sha256"] == expected_autogrid_sha,
        "AD4_SCREENING_AUTOGRID_IDENTITY_MISMATCH",
        "AutoGrid4 does not match --autogrid4-sha256.",
        details={
            "expected_sha256": expected_autogrid_sha,
            "actual": autogrid_identity,
        },
    )
    steps: dict[str, Any] = {
        "source_identity": "passed",
        "tool_file_identity": "passed",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="DockStart_ad4_serial_screening_") as temporary:
            work_root = Path(temporary)
            with isolated_settings(
                work_root,
                vina=Path(vina_identity["path"]),
                autogrid4=Path(autogrid_identity["path"]),
            ):
                tools = _require_tools(
                    Path(vina_identity["path"]),
                    vina_identity,
                    Path(autogrid_identity["path"]),
                    autogrid_identity,
                )
                steps["tool_detection"] = "passed"
                created = require_api(
                    "create_project",
                    create_project("official_ad4_serial_screening", str(work_root)),
                )
                project_root = Path(str(created["project_dir"])).resolve(strict=True)
                require_api(
                    "import_receptor_pdbqt",
                    import_receptor_pdbqt(project_root.as_posix(), receptor_identity["path"]),
                )
                require_api(
                    "import_reference_ligand_pdbqt",
                    import_ligand_pdbqt(project_root.as_posix(), p59_identity["path"]),
                )
                require_api(
                    "update_box_params",
                    update_box_params(project_root.as_posix(), dict(manifest["protocol"]["box"])),
                )
                project_vina = dict(manifest["protocol"]["vina"])
                project_vina.pop("scoring", None)
                require_api(
                    "update_vina_params",
                    update_vina_params(project_root.as_posix(), project_vina),
                )
                require_api(
                    "set_scoring_protocol",
                    set_scoring_protocol(project_root.as_posix(), "ad4_maps"),
                )
                steps["project_setup"] = "passed"

                ligand_types = sorted(
                    set(pdbqt_atom_types(Path(p59_identity["path"])))
                    | set(pdbqt_atom_types(Path(p69_identity["path"])))
                )
                maps_result = require_api(
                    "generate_maps_for_serial_union",
                    generate_maps(
                        project_root.as_posix(),
                        {"ligand_atom_types": ligand_types},
                    ),
                )
                maps_validation = require_api(
                    "validate_active_maps",
                    validate_active_maps(project_root.as_posix()),
                )
                require(
                    maps_validation.get("ready") is True
                    and maps_validation.get("protocol_active") is True,
                    "AD4_SCREENING_MAPS_VALIDATION_FAILED",
                    "Generated serial-screening AD4 maps are not active and ready.",
                    details={"validation": maps_validation},
                )
                maps_evidence = audit_generated_maps(
                    project_root,
                    maps_result,
                    autogrid4_sha256=expected_autogrid_sha,
                    required_ligand_types=ligand_types,
                )
                require(
                    maps_evidence.get("receptor", {}).get("source_sha256")
                    == receptor_identity["sha256"]
                    and maps_evidence.get("ligand", {}).get("source_sha256")
                    == p59_identity["sha256"],
                    "AD4_SCREENING_MAP_INPUT_MISMATCH",
                    "Generated serial maps do not bind the frozen 5X72 receptor/reference ligand.",
                    details={"maps": maps_evidence},
                )
                steps["single_union_map_set"] = "passed"

                source_order = [str(value) for value in manifest["workload"]["source_order"]]
                ligand_files, workload_evidence = _derived_ligands(
                    project_root,
                    Path(p59_identity["path"]),
                    Path(p69_identity["path"]),
                    source_order,
                )
                require(
                    len(ligand_files) == int(manifest["workload"]["ligand_count"]),
                    "AD4_SCREENING_WORKLOAD_COUNT_INVALID",
                    "Derived serial-screening workload count differs from the manifest.",
                )
                created_screening = require_api(
                    "create_screening",
                    create_screening(
                        project_root.as_posix(),
                        "prepared/receptor.pdbqt",
                        ligand_files,
                        vina_path=vina_identity["path"],
                        box=dict(manifest["protocol"]["box"]),
                        vina=dict(manifest["protocol"]["vina"]),
                        max_retries=int(manifest["protocol"]["max_retries"]),
                        top_n=int(manifest["protocol"]["top_n"]),
                    ),
                )
                initial_state = created_screening.get("screening")
                require(
                    isinstance(initial_state, Mapping)
                    and initial_state.get("status") == "ready"
                    and initial_state.get("scoring_protocol") == "ad4_maps"
                    and len(initial_state.get("queue") or []) == len(ligand_files),
                    "AD4_SCREENING_INITIAL_STATE_INVALID",
                    "Created serial AD4 screening did not freeze the complete queue and maps.",
                    details={"screening": initial_state},
                )
                frozen_maps = (
                    (initial_state.get("inputs") or {}).get("ad4_maps")
                    if isinstance(initial_state.get("inputs"), Mapping)
                    else {}
                )
                frozen_map_records = (
                    frozen_maps.get("files")
                    if isinstance(frozen_maps, Mapping)
                    and isinstance(frozen_maps.get("files"), list)
                    else []
                )
                require(
                    bool(frozen_map_records)
                    and bool(str(frozen_maps.get("prefix") or "")),
                    "AD4_SCREENING_FROZEN_MAPS_MISSING",
                    "Screening state contains no frozen AD4 map records.",
                )
                frozen_map_evidence = [
                    verify_snapshot_record(
                        project_root,
                        record,
                        label=f"screening frozen map {index}",
                    )
                    for index, record in enumerate(frozen_map_records, start=1)
                    if isinstance(record, Mapping)
                ]
                require(
                    len(frozen_map_evidence) == len(frozen_map_records),
                    "AD4_SCREENING_FROZEN_MAPS_MISSING",
                    "A frozen screening map lacks an identity record.",
                )
                steps["screening_queue_freeze"] = "passed"

                fault = manifest["fault_injection"]
                runner = _AuditRunner(
                    project_root,
                    cancel_order=int(fault["request_cancel_while_item_order_is_running"]),
                    failed_order=int(fault["intentionally_fail_item_order"]),
                    failed_exit_code=int(fault["failed_item_exit_code"]),
                )
                first_run = require_api(
                    "run_screening_until_live_cancel",
                    run_screening(project_root.as_posix(), runner=runner),
                )
                first_state = first_run.get("screening")
                require(
                    isinstance(first_state, Mapping)
                    and first_state.get("status")
                    == manifest["acceptance"]["first_terminal_status"]
                    and runner.cancel_evidence.get("process_alive_before_request") is True
                    and (runner.cancel_evidence.get("response") or {}).get("ok") is True,
                    "AD4_SCREENING_LIVE_CANCEL_NOT_PROVEN",
                    "Serial screening was not canceled while the configured Vina process was live.",
                    details={
                        "screening": first_state,
                        "cancel_evidence": runner.cancel_evidence,
                    },
                )
                remaining_queue = list(first_state.get("queue") or [])
                require(
                    len(remaining_queue)
                    == len(ligand_files)
                    - int(fault["request_cancel_while_item_order_is_running"]),
                    "AD4_SCREENING_CANCEL_QUEUE_INVALID",
                    "Live cancellation did not preserve the exact remaining queue.",
                    details={"remaining_queue": remaining_queue},
                )
                steps["live_cancel"] = "passed"

                tamper_record = frozen_map_records[0]
                tamper_path = contained_project_file(
                    project_root,
                    str(tamper_record.get("relative_path") or ""),
                    label="screening map selected for tamper audit",
                )
                original_map_bytes = tamper_path.read_bytes()
                try:
                    tamper_path.write_bytes(original_map_bytes + b"\nDOCKSTART_V0141_TAMPER\n")
                    tampered_resume = resume_screening(project_root.as_posix())
                finally:
                    tamper_path.write_bytes(original_map_bytes)
                require(
                    tampered_resume.get("ok") is False
                    and (tampered_resume.get("error") or {}).get("code")
                    == manifest["acceptance"]["tampered_resume_error_code"],
                    "AD4_SCREENING_TAMPER_RESUME_NOT_BLOCKED",
                    "Resume did not fail closed after one frozen map changed.",
                    details={"response": tampered_resume},
                )
                verify_snapshot_record(
                    project_root,
                    tamper_record,
                    label="restored screening map",
                )
                resumed = require_api(
                    "resume_screening_after_map_restore",
                    resume_screening(project_root.as_posix()),
                )
                require(
                    list((resumed.get("screening") or {}).get("queue") or [])
                    == remaining_queue,
                    "AD4_SCREENING_RESUME_QUEUE_CHANGED",
                    "Explicit resume did not restore the exact canceled queue.",
                    details={"expected": remaining_queue, "resumed": resumed},
                )
                steps["map_tamper_fail_closed_and_resume"] = "passed"

                runner.cancel_enabled = False
                runner.failure_enabled = True
                final_run = require_api(
                    "run_screening_after_resume",
                    run_screening(project_root.as_posix(), runner=runner),
                )
                final_state = final_run.get("screening")
                require(
                    isinstance(final_state, Mapping)
                    and final_state.get("status") == manifest["acceptance"]["final_status"],
                    "AD4_SCREENING_FINAL_STATUS_INVALID",
                    "Serial queue did not complete with exactly the expected isolated failure.",
                    details={"screening": final_state},
                )
                items = final_state.get("items") if isinstance(final_state.get("items"), list) else []
                succeeded = [item for item in items if item.get("status") == "succeeded"]
                failed = [item for item in items if item.get("status") == "failed"]
                require(
                    len(succeeded) == int(manifest["acceptance"]["succeeded_count"])
                    and len(failed) == int(manifest["acceptance"]["failed_count"])
                    and [int(item.get("order") or 0) for item in failed]
                    == [int(fault["intentionally_fail_item_order"])]
                    and not final_state.get("queue"),
                    "AD4_SCREENING_FAILURE_CONTINUATION_INVALID",
                    "The intentional item failure did not remain isolated while later items continued.",
                    details={"succeeded": len(succeeded), "failed": failed},
                )
                attempt_evidence = _audit_attempts(
                    project_root,
                    final_state,
                    vina_identity=vina_identity,
                    expected_count=int(manifest["acceptance"]["attempt_count"]),
                    expected_map_prefix=str(frozen_maps["attempt_prefix"]),
                    expected_project_map_prefix=str(frozen_maps["prefix"]),
                    failed_order=int(fault["intentionally_fail_item_order"]),
                    failed_exit_code=int(fault["failed_item_exit_code"]),
                )
                ranking_evidence = _audit_ranked_csv(project_root, final_state)
                steps["failure_continuation_attempts_and_ranking"] = "passed"

                report = require_api(
                    "export_screening_markdown_report",
                    export_screening_markdown_report(project_root.as_posix()),
                )
                report_path = contained_project_file(
                    project_root,
                    str(report.get("report_file") or ""),
                    label="serial AD4 screening Markdown report",
                )
                report_text = report_path.read_text(encoding="utf-8", errors="strict")
                require(
                    "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
                    in report_text,
                    "AD4_SCREENING_REPORT_DISCLAIMER_MISSING",
                    "The serial-screening report lacks the scientific disclaimer.",
                )
                report_evidence = file_identity(
                    report_path,
                    label="serial AD4 screening Markdown report",
                )
                archived = require_api(
                    "archive_screening",
                    archive_screening(project_root.as_posix()),
                )
                archive_id = str(archived.get("archive_id") or "")
                require(
                    bool(archive_id),
                    "AD4_SCREENING_ARCHIVE_ID_MISSING",
                    "Serial-screening archive returned no archive identity.",
                )
                export_path = work_root / f"{archive_id}.zip"
                exported = require_api(
                    "export_screening_archive_zip",
                    export_screening_archive_zip(
                        project_root.as_posix(),
                        archive_id,
                        str(export_path),
                    ),
                )
                zip_evidence = file_identity(export_path, label="serial-screening archive ZIP")
                require(
                    zip_evidence["sha256"] == exported.get("zip_sha256")
                    and zip_evidence["size_bytes"] == int(exported.get("size_bytes") or 0),
                    "AD4_SCREENING_EXPORT_IDENTITY_MISMATCH",
                    "Exported screening ZIP differs from the public API evidence.",
                    details={"exported": exported, "actual": zip_evidence},
                )
                with zipfile.ZipFile(export_path, "r") as archive:
                    names = archive.namelist()
                    require(
                        "dockstart_screening_export.json" in names
                        and any(name.endswith("screening_report.md") for name in names)
                        and sum(name.endswith("attempt.json") for name in names)
                        == int(manifest["acceptance"]["attempt_count"]),
                        "AD4_SCREENING_EXPORT_CONTENT_INVALID",
                        "Exported ZIP lacks the manifest, report, or complete attempt evidence.",
                        details={"entries": names},
                    )
                steps["report_archive_and_zip"] = "passed"

                return _bound_result({
                    "schema_version": 2,
                    "verifier_id": VERIFIER_ID,
                    "ok": True,
                    "fixture": {
                        "manifest": file_identity(MANIFEST_PATH, label="serial AD4 source manifest"),
                        "upstream_tag": manifest["upstream"]["tag"],
                        "workload_purpose": manifest["workload"]["purpose"],
                    },
                    "inputs": {
                        "receptor": receptor_identity,
                        "source_ligands": [p59_identity, p69_identity],
                        "derived_workload": workload_evidence,
                    },
                    "tools": tools,
                    "maps": {
                        "generated": maps_evidence,
                        "screening_frozen_files": frozen_map_evidence,
                        "tamper_resume_rejection": tampered_resume,
                    },
                    "cancellation": runner.cancel_evidence,
                    "screening": {
                        "status": final_state.get("status"),
                        "succeeded_count": len(succeeded),
                        "failed_count": len(failed),
                        **attempt_evidence,
                        "ranking": ranking_evidence,
                    },
                    "report": report_evidence,
                    "archive": {
                        "archive_id": archive_id,
                        "relative_path": archived.get("archive"),
                        "zip": zip_evidence,
                        "zip_entry_count": exported.get("entry_count"),
                        "payload_tree_sha256": exported.get("payload_tree_sha256"),
                    },
                    "scientific_boundaries": list(manifest["scientific_boundaries"]),
                    "steps": steps,
                }, source_session)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _bound_result(
            error_payload(VERIFIER_ID, exc, steps=steps, schema_version=2),
            source_session,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline 12-item serial AD4 reliability gate."
    )
    parser.add_argument("--receptor-pdbqt", type=Path, required=True)
    parser.add_argument("--ligand-p59-pdbqt", type=Path, required=True)
    parser.add_argument("--ligand-p69-pdbqt", type=Path, required=True)
    parser.add_argument("--vina", type=Path, default=DEFAULT_VINA)
    parser.add_argument("--autogrid4", type=Path, required=True)
    parser.add_argument("--autogrid4-sha256", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        require_output_separate_from_inputs(
            arguments.output,
            {
                "receptor_pdbqt": arguments.receptor_pdbqt,
                "ligand_p59_pdbqt": arguments.ligand_p59_pdbqt,
                "ligand_p69_pdbqt": arguments.ligand_p69_pdbqt,
                "vina": arguments.vina,
                "autogrid4": arguments.autogrid4,
                "source_manifest": MANIFEST_PATH,
            },
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = unbound_preflight_result(
            error_payload(VERIFIER_ID, exc, schema_version=2),
            expected_verifier_id=VERIFIER_ID,
        )
        return write_result(result, None)
    source_session: Mapping[str, Any] | None = None
    try:
        source_session = _capture_source_session()
        result = verify(
            receptor_pdbqt=arguments.receptor_pdbqt,
            ligand_p59_pdbqt=arguments.ligand_p59_pdbqt,
            ligand_p69_pdbqt=arguments.ligand_p69_pdbqt,
            vina_path=arguments.vina,
            autogrid4_path=arguments.autogrid4,
            autogrid4_sha256=arguments.autogrid4_sha256,
            source_session=source_session,
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = _bound_result(
            error_payload(VERIFIER_ID, exc, schema_version=2),
            source_session,
        )
    return write_result(result, arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
