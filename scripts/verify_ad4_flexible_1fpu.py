#!/usr/bin/env python3
"""Run the official 1FPU/1IEP flexible AD4 workflow through DockStart.

The verifier is intentionally offline.  It reuses the repository's pinned
1FPU hydrogenated receptor, requires the caller to provide the exact official
1IEP ligand plus real Python/Meeko, AutoGrid4, and Vina runtimes, then executes
DockStart's public project APIs from raw import through report export.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
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
    AcceptanceError,
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
    no_fit_rmsd,
    option_values,
    pdbqt_atom_types,
    pdbqt_heavy_coordinates,
    require,
    require_api,
    require_output_separate_from_inputs,
    same_paths,
    unbound_preflight_result,
    write_result,
)


VERIFIER_ID = "dockstart_ad4_flexible_1fpu_v0141"
MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "flexible_ad4_1fpu"
    / "source_manifest.json"
)
DEFAULT_RECEPTOR = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "flexible_1fpu"
    / "1fpu_receptorH.pdb"
)
BAD_RESIDUE_CONTRACT = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "flexible_1fpu"
    / "expected_bad_residues.json"
)
DEFAULT_PYTHON = ROOT / "resources" / "python" / "python.exe"
DEFAULT_VINA = ROOT / "resources" / "vina" / "vina.exe"
_BACKEND_LOADED = False


def _load_backend_modules() -> None:
    global _BACKEND_LOADED
    if _BACKEND_LOADED:
        return
    from adapters import autogrid_adapter as loaded_autogrid_adapter
    from adapters import meeko_adapter as loaded_meeko_adapter
    from adapters import python_adapter as loaded_python_adapter
    from adapters import vina_adapter as loaded_vina_adapter
    from dockstart_core.autogrid import (
        generate_maps as loaded_generate_maps,
        set_scoring_protocol as loaded_set_scoring_protocol,
        validate_active_maps as loaded_validate_active_maps,
    )
    from dockstart_core.flexible_receptor import (
        get_flexible_receptor_status as loaded_get_flexible_receptor_status,
        prepare_flexible_receptor as loaded_prepare_flexible_receptor,
        set_receptor_docking_mode as loaded_set_receptor_docking_mode,
    )
    from dockstart_core.project import (
        analyze_vina_run_results as loaded_analyze_vina_run_results,
        create_project as loaded_create_project,
        execute_prepared_vina_run as loaded_execute_prepared_vina_run,
        export_markdown_report as loaded_export_markdown_report,
        generate_vina_config as loaded_generate_vina_config,
        import_ligand_pdbqt as loaded_import_ligand_pdbqt,
        prepare_vina_run as loaded_prepare_vina_run,
        update_box_params as loaded_update_box_params,
        update_vina_params as loaded_update_vina_params,
    )
    from dockstart_core.structure_fetch import (
        import_receptor_raw_file as loaded_import_receptor_raw_file,
    )

    globals().update(
        {
            "autogrid_adapter": loaded_autogrid_adapter,
            "meeko_adapter": loaded_meeko_adapter,
            "python_adapter": loaded_python_adapter,
            "vina_adapter": loaded_vina_adapter,
            "generate_maps": loaded_generate_maps,
            "set_scoring_protocol": loaded_set_scoring_protocol,
            "validate_active_maps": loaded_validate_active_maps,
            "get_flexible_receptor_status": loaded_get_flexible_receptor_status,
            "prepare_flexible_receptor": loaded_prepare_flexible_receptor,
            "set_receptor_docking_mode": loaded_set_receptor_docking_mode,
            "analyze_vina_run_results": loaded_analyze_vina_run_results,
            "create_project": loaded_create_project,
            "execute_prepared_vina_run": loaded_execute_prepared_vina_run,
            "export_markdown_report": loaded_export_markdown_report,
            "generate_vina_config": loaded_generate_vina_config,
            "import_ligand_pdbqt": loaded_import_ligand_pdbqt,
            "prepare_vina_run": loaded_prepare_vina_run,
            "update_box_params": loaded_update_box_params,
            "update_vina_params": loaded_update_vina_params,
            "import_receptor_raw_file": loaded_import_receptor_raw_file,
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


def _tool_record(result: Any, identity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(identity),
        "detected_status": str(getattr(result, "status", "")),
        "detected_version": str(getattr(result, "version", "")),
        "detected_path": str(getattr(result, "path", "")),
        "source": str(getattr(result, "source", "")),
    }


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _require_tools(
    python_path: Path,
    python_identity: Mapping[str, Any],
    vina_path: Path,
    vina_identity: Mapping[str, Any],
    autogrid_path: Path,
    autogrid_identity: Mapping[str, Any],
) -> dict[str, Any]:
    python_detection = python_adapter.detect(
        str(python_path),
        bundled_path="",
        prefer_configured=True,
    )
    require(
        getattr(python_detection, "status", "") == "ok"
        and same_paths(
            [str(getattr(python_detection, "path", ""))],
            [python_path],
        ),
        "AD4_FLEX_1FPU_PYTHON_DETECTION_FAILED",
        "DockStart did not resolve the caller-pinned Assisted Python runtime.",
        details={"detection": _tool_record(python_detection, python_identity)},
    )
    meeko_detection = meeko_adapter.detect(str(python_path), source="configured")
    require(
        getattr(meeko_detection, "status", "") == "ok"
        and getattr(meeko_detection, "version", "") == "0.7.1"
        and same_paths(
            [str(getattr(meeko_detection, "path", ""))],
            [python_path],
        ),
        "AD4_FLEX_1FPU_MEEKO_DETECTION_FAILED",
        "The caller-pinned Python runtime does not provide exact Meeko 0.7.1.",
        details={"detection": _tool_record(meeko_detection, python_identity)},
    )
    vina_detection = vina_adapter.detect(str(vina_path), bundled_path="")
    require(
        getattr(vina_detection, "status", "") == "ok"
        and getattr(vina_detection, "version", "") == "1.2.7"
        and same_paths([str(getattr(vina_detection, "path", ""))], [vina_path]),
        "AD4_FLEX_1FPU_VINA_DETECTION_FAILED",
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
        "AD4_FLEX_1FPU_AUTOGRID_DETECTION_FAILED",
        "DockStart did not resolve the caller-pinned AutoGrid4 4.2.6+ executable.",
        details={"detection": _tool_record(autogrid_detection, autogrid_identity)},
    )
    return {
        "python": _tool_record(python_detection, python_identity),
        "meeko": _tool_record(meeko_detection, python_identity),
        "vina": _tool_record(vina_detection, vina_identity),
        "autogrid4": _tool_record(autogrid_detection, autogrid_identity),
    }


def _load_bad_residue_contract(
    expected_identity: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = file_identity(
        BAD_RESIDUE_CONTRACT,
        label="1FPU bad-residue contract",
        expected=expected_identity,
    )
    try:
        payload = json.loads(BAD_RESIDUE_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(
            "AD4_FLEX_1FPU_BAD_RESIDUE_CONTRACT_INVALID",
            "The 1FPU bad-residue contract cannot be parsed.",
            details={"error": str(exc)},
        ) from exc
    require(
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and payload.get("source_sha256")
        == "896980690302f6b10c5d96d6f76d0a59d5127b76b231d00665b48ceaa15a80da"
        and payload.get("meeko_version") == "0.7.1"
        and payload.get("selected_flexible_residues") == ["A:315"],
        "AD4_FLEX_1FPU_BAD_RESIDUE_CONTRACT_INVALID",
        "The 1FPU bad-residue contract changed unexpectedly.",
    )
    return payload, identity


def verify(
    *,
    receptor_pdb: Path,
    ligand_pdbqt: Path,
    python_path: Path,
    vina_path: Path,
    autogrid4_path: Path,
    autogrid4_sha256: str,
    source_session: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if source_session is None:
        source_session = _capture_source_session()
    manifest = load_fixture_manifest(MANIFEST_PATH, "ad4_flexible_1fpu_external")
    source_contracts = manifest["source_files"]
    receptor_identity = file_identity(
        receptor_pdb,
        label="official 1FPU hydrogenated receptor",
        expected=source_contracts["receptor_h_pdb"],
    )
    ligand_identity = file_identity(
        ligand_pdbqt,
        label="official 1IEP ligand PDBQT",
        expected=source_contracts["ligand_pdbqt"],
    )
    python_identity = file_identity(python_path, label="Assisted Python runtime")
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
        "AD4_FLEX_1FPU_AUTOGRID_IDENTITY_MISMATCH",
        "AutoGrid4 does not match --autogrid4-sha256.",
        details={
            "expected_sha256": expected_autogrid_sha,
            "actual": autogrid_identity,
        },
    )
    bad_residue_contract, bad_residue_identity = _load_bad_residue_contract(
        manifest["preparation_contract"]["bad_residue_contract"]
    )
    steps: dict[str, Any] = {
        "source_identity": "passed",
        "tool_file_identity": "passed",
    }

    try:
        with tempfile.TemporaryDirectory(prefix="DockStart_ad4_flex_1fpu_") as temporary:
            work_root = Path(temporary)
            with isolated_settings(
                work_root,
                vina=Path(vina_identity["path"]),
                autogrid4=Path(autogrid_identity["path"]),
                python=Path(python_identity["path"]),
            ):
                tools = _require_tools(
                    Path(python_identity["path"]),
                    python_identity,
                    Path(vina_identity["path"]),
                    vina_identity,
                    Path(autogrid_identity["path"]),
                    autogrid_identity,
                )
                steps["tool_detection"] = "passed"

                created = require_api(
                    "create_project",
                    create_project("official_ad4_flexible_1fpu", str(work_root)),
                )
                project_root = Path(str(created["project_dir"])).resolve(strict=True)
                require_api(
                    "import_receptor_raw_file",
                    import_receptor_raw_file(project_root.as_posix(), receptor_identity["path"]),
                )
                require_api(
                    "import_ligand_pdbqt",
                    import_ligand_pdbqt(project_root.as_posix(), ligand_identity["path"]),
                )
                require_api(
                    "update_box_params",
                    update_box_params(project_root.as_posix(), dict(manifest["protocol"]["box"])),
                )
                require_api(
                    "update_vina_params",
                    update_vina_params(project_root.as_posix(), dict(manifest["protocol"]["vina"])),
                )
                require_api(
                    "set_scoring_protocol",
                    set_scoring_protocol(project_root.as_posix(), "ad4_maps"),
                )
                steps["project_setup"] = "passed"

                selections = list(manifest["preparation_contract"]["selected_flexible_residues"])
                expected_bad = list(bad_residue_contract["expected_bad_residues"])
                strict = prepare_flexible_receptor(project_root.as_posix(), selections)
                strict_review = strict.get("review") if isinstance(strict.get("review"), Mapping) else {}
                require(
                    strict.get("ok") is False
                    and (strict.get("error") or {}).get("code")
                    == "FLEX_BAD_RESIDUES_REVIEW_REQUIRED"
                    and list(strict_review.get("bad_residues") or []) == expected_bad,
                    "AD4_FLEX_1FPU_STRICT_REVIEW_MISMATCH",
                    "Strict Meeko preparation did not stop with the exact frozen bad-residue list.",
                    details={"expected": expected_bad, "result": strict},
                )
                prepared_flex = require_api(
                    "prepare_flexible_receptor_after_acknowledgement",
                    prepare_flexible_receptor(
                        project_root.as_posix(),
                        selections,
                        allow_bad_res=True,
                        acknowledged_bad_residues=expected_bad,
                    ),
                )
                expected_outputs = dict(manifest["preparation_contract"]["output_sha256"])
                require(
                    dict(prepared_flex.get("sha256") or {}) == expected_outputs,
                    "AD4_FLEX_1FPU_PREPARATION_IDENTITY_MISMATCH",
                    "Prepared 1FPU rigid/flex/JSON outputs changed.",
                    details={
                        "expected": expected_outputs,
                        "actual": prepared_flex.get("sha256"),
                    },
                )
                prepared_output_paths = (
                    prepared_flex.get("outputs")
                    if isinstance(prepared_flex.get("outputs"), Mapping)
                    else {}
                )
                prepared_output_evidence: dict[str, Any] = {}
                for output_key, expected_sha256 in expected_outputs.items():
                    output_path = contained_project_file(
                        project_root,
                        str(prepared_output_paths.get(output_key) or ""),
                        label=f"prepared flexible receptor {output_key}",
                    )
                    output_identity = file_identity(
                        output_path,
                        label=f"prepared flexible receptor {output_key}",
                    )
                    require(
                        output_identity["sha256"] == expected_sha256,
                        "AD4_FLEX_1FPU_PREPARATION_ARTIFACT_MISMATCH",
                        "A prepared 1FPU rigid/flex/JSON artifact differs from its frozen hash.",
                        details={
                            "output_key": output_key,
                            "expected_sha256": expected_sha256,
                            "actual": output_identity,
                        },
                    )
                    prepared_output_evidence[output_key] = output_identity
                require_api(
                    "set_receptor_docking_mode",
                    set_receptor_docking_mode(project_root.as_posix(), "flexible"),
                )
                flex_status = require_api(
                    "get_flexible_receptor_status",
                    get_flexible_receptor_status(project_root.as_posix()),
                )
                require(
                    flex_status.get("mode") == "flexible"
                    and flex_status.get("flexible_ready") is True,
                    "AD4_FLEX_1FPU_NOT_ACTIVE",
                    "The prepared 1FPU flexible receptor is not active and integrity-ready.",
                    details={"status": flex_status},
                )
                steps["strict_review_and_flexible_preparation"] = "passed"

                ligand_types = pdbqt_atom_types(Path(ligand_identity["path"]))
                maps_result = require_api(
                    "generate_maps",
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
                    "AD4_FLEX_1FPU_MAPS_VALIDATION_FAILED",
                    "Generated flexible AD4 maps are not active and ready.",
                    details={"validation": maps_validation},
                )
                maps_evidence = audit_generated_maps(
                    project_root,
                    maps_result,
                    autogrid4_sha256=expected_autogrid_sha,
                    required_ligand_types=ligand_types,
                )
                flexible_maps_record = maps_evidence.get("flexible_receptor") or {}
                require(
                    flexible_maps_record.get("rigid_sha256")
                    == expected_outputs["rigid_pdbqt"]
                    or maps_evidence.get("receptor", {}).get("source_sha256")
                    == expected_outputs["rigid_pdbqt"],
                    "AD4_FLEX_1FPU_MAPS_RECEPTOR_MISMATCH",
                    "AD4 maps were not generated from the frozen rigid receptor component.",
                    details={"maps": maps_evidence},
                )
                steps["autogrid_maps"] = "passed"

                require_api(
                    "generate_vina_config",
                    generate_vina_config(project_root.as_posix()),
                )
                prepared_run = require_api(
                    "prepare_vina_run",
                    prepare_vina_run(project_root.as_posix()),
                )
                run_id = str(prepared_run.get("run_id") or "")
                command = [str(value) for value in prepared_run.get("command") or []]
                require(
                    bool(run_id) and bool(command),
                    "AD4_FLEX_1FPU_PREPARED_RUN_INVALID",
                    "DockStart returned no prepared flexible AD4 command.",
                )
                config_values = option_values(command, "--config")
                flex_values = option_values(command, "--flex")
                maps_values = option_values(command, "--maps")
                scoring_values = option_values(command, "--scoring")
                require(
                    len(config_values) == 1
                    and len(flex_values) == 1
                    and len(maps_values) == 1
                    and scoring_values == ["ad4"]
                    and "--receptor" not in command,
                    "AD4_FLEX_1FPU_COMMAND_CONTRACT_INVALID",
                    "Prepared command is not a single-ligand flexible AD4 maps command.",
                    details={"command": command},
                )
                config_file = contained_project_file(
                    project_root,
                    config_values[0],
                    label="frozen flexible AD4 config",
                )
                config_text = config_file.read_text(encoding="utf-8", errors="strict")
                ligand_match = re.search(r"^ligand\s*=\s*(.+?)\s*$", config_text, re.MULTILINE)
                require(
                    ligand_match is not None
                    and "scoring = ad4" in config_text
                    and "receptor =" not in config_text,
                    "AD4_FLEX_1FPU_CONFIG_CONTRACT_INVALID",
                    "Frozen AD4 config does not contain exactly the maps-mode ligand/scoring contract.",
                    details={"config_file": str(config_file), "config_text": config_text},
                )
                frozen_ligand = file_identity(
                    contained_project_file(
                        project_root,
                        str(ligand_match.group(1)),
                        label="frozen run ligand",
                    ),
                    label="frozen run ligand",
                )
                frozen_flex = file_identity(
                    contained_project_file(
                        project_root,
                        flex_values[0],
                        label="frozen run flex receptor",
                    ),
                    label="frozen run flex receptor",
                )
                require(
                    frozen_ligand["sha256"] == ligand_identity["sha256"]
                    and frozen_flex["sha256"] == expected_outputs["flex_pdbqt"],
                    "AD4_FLEX_1FPU_RUN_INPUT_MISMATCH",
                    "Prepared run snapshots do not match the frozen ligand/flex identities.",
                    details={"ligand": frozen_ligand, "flex": frozen_flex},
                )
                steps["prepared_command"] = "passed"

                executed = require_api(
                    "execute_prepared_vina_run",
                    execute_prepared_vina_run(project_root.as_posix(), run_id),
                )
                analyzed = require_api(
                    "analyze_vina_run_results",
                    analyze_vina_run_results(project_root.as_posix(), run_id),
                )
                report = require_api(
                    "export_markdown_report",
                    export_markdown_report(project_root.as_posix(), run_id),
                )
                metadata = executed.get("metadata") if isinstance(executed.get("metadata"), Mapping) else {}
                require(
                    metadata.get("status") == "finished",
                    "AD4_FLEX_1FPU_RUN_INCOMPLETE",
                    "Flexible AD4 Vina run did not finish.",
                    details={"metadata": metadata},
                )
                scores = analyzed.get("scores") if isinstance(analyzed.get("scores"), list) else []
                require(
                    bool(scores)
                    and isinstance(scores[0], Mapping)
                    and scores[0].get("affinity_kcal_mol") is not None,
                    "AD4_FLEX_1FPU_SCORE_TABLE_INVALID",
                    "Finished flexible AD4 run contains no score rows.",
                    details={"scores": scores},
                )
                best_affinity = float(scores[0]["affinity_kcal_mol"])
                score_gate = manifest["acceptance"]["best_affinity_kcal_mol"]
                require(
                    float(score_gate["minimum"])
                    <= best_affinity
                    <= float(score_gate["maximum"]),
                    "AD4_FLEX_1FPU_SCORE_OUT_OF_RANGE",
                    "Best flexible AD4 affinity is outside the frozen tutorial tolerance.",
                    details={"best_affinity": best_affinity, "gate": score_gate},
                )
                output_file = contained_project_file(
                    project_root,
                    str(metadata.get("output_file") or ""),
                    label="flexible AD4 output PDBQT",
                )
                output_text = output_file.read_text(encoding="utf-8", errors="strict")
                marker = str(manifest["acceptance"]["required_flexible_output_marker"])
                require(
                    marker in output_text,
                    "AD4_FLEX_1FPU_THR315_OUTPUT_MISSING",
                    "Flexible AD4 output does not contain the frozen Thr315 residue block.",
                    details={"marker": marker},
                )
                pose_rmsd = no_fit_rmsd(
                    pdbqt_heavy_coordinates(Path(ligand_identity["path"])),
                    pdbqt_heavy_coordinates(
                        output_file,
                        first_model_only=True,
                        ligand_before_flex_only=True,
                    ),
                )
                rmsd_limit = float(
                    manifest["acceptance"][
                        "first_pose_no_fit_heavy_atom_rmsd_max_angstrom"
                    ]
                )
                require(
                    pose_rmsd <= rmsd_limit,
                    "AD4_FLEX_1FPU_POSE_RECOVERY_FAILED",
                    "First flexible AD4 pose is outside the frozen no-fit RMSD tolerance.",
                    details={"rmsd_angstrom": pose_rmsd, "maximum": rmsd_limit},
                )
                report_file = contained_project_file(
                    project_root,
                    str(report.get("report_file") or ""),
                    label="flexible AD4 Markdown report",
                )
                disclaimer = "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
                require(
                    disclaimer in report_file.read_text(encoding="utf-8", errors="strict"),
                    "AD4_FLEX_1FPU_REPORT_DISCLAIMER_MISSING",
                    "The exported report lacks the mandatory scientific disclaimer.",
                )
                steps["real_vina_run_and_report"] = "passed"

                return _bound_result({
                    "schema_version": 2,
                    "verifier_id": VERIFIER_ID,
                    "ok": True,
                    "fixture": {
                        "manifest": file_identity(MANIFEST_PATH, label="1FPU AD4 source manifest"),
                        "bad_residue_contract": bad_residue_identity,
                        "upstream_tag": manifest["upstream"]["tag"],
                    },
                    "inputs": {
                        "receptor": receptor_identity,
                        "ligand": ligand_identity,
                    },
                    "tools": tools,
                    "preparation": {
                        "selected_residues": selections,
                        "strict_bad_residues": expected_bad,
                        "preparation_id": prepared_flex.get("preparation_id"),
                        "output_sha256": prepared_flex.get("sha256"),
                        "outputs": prepared_output_evidence,
                    },
                    "maps": maps_evidence,
                    "run": {
                        "run_id": run_id,
                        "command": command,
                        "status": metadata.get("status"),
                        "best_affinity_kcal_mol": best_affinity,
                        "first_pose_no_fit_heavy_atom_rmsd_angstrom": pose_rmsd,
                        "output": file_identity(output_file, label="flexible AD4 output PDBQT"),
                    },
                    "report": file_identity(report_file, label="flexible AD4 Markdown report"),
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
        description="Run the offline 1FPU flexible AD4 external acceptance gate."
    )
    parser.add_argument("--receptor-pdb", type=Path, default=DEFAULT_RECEPTOR)
    parser.add_argument("--ligand-pdbqt", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--vina", type=Path, default=DEFAULT_VINA)
    parser.add_argument("--autogrid4", type=Path, required=True)
    parser.add_argument("--autogrid4-sha256", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        require_output_separate_from_inputs(
            arguments.output,
            {
                "receptor_pdb": arguments.receptor_pdb,
                "ligand_pdbqt": arguments.ligand_pdbqt,
                "python": arguments.python,
                "vina": arguments.vina,
                "autogrid4": arguments.autogrid4,
                "source_manifest": MANIFEST_PATH,
                "bad_residue_contract": BAD_RESIDUE_CONTRACT,
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
            receptor_pdb=arguments.receptor_pdb,
            ligand_pdbqt=arguments.ligand_pdbqt,
            python_path=arguments.python,
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
