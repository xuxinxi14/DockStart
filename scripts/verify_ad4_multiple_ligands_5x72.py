#!/usr/bin/env python3
"""Run the official ordered 5X72 P59+P69 simultaneous AD4 workflow."""

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


VERIFIER_ID = "dockstart_ad4_multiple_ligands_5x72_v0141"
MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "multiple_ligands_ad4_5x72"
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
    from dockstart_core.multiple_ligands import (
        execute_multiple_ligand_run as loaded_execute_multiple_ligand_run,
        export_multiple_ligand_markdown_report as loaded_export_multiple_ligand_markdown_report,
        prepare_multiple_ligand_run as loaded_prepare_multiple_ligand_run,
    )
    from dockstart_core.project import (
        create_project as loaded_create_project,
        import_ligand_pdbqt as loaded_import_ligand_pdbqt,
        import_receptor_pdbqt as loaded_import_receptor_pdbqt,
        update_box_params as loaded_update_box_params,
        update_vina_params as loaded_update_vina_params,
    )

    globals().update(
        {
            "autogrid_adapter": loaded_autogrid_adapter,
            "vina_adapter": loaded_vina_adapter,
            "generate_maps": loaded_generate_maps,
            "set_scoring_protocol": loaded_set_scoring_protocol,
            "validate_active_maps": loaded_validate_active_maps,
            "execute_multiple_ligand_run": loaded_execute_multiple_ligand_run,
            "export_multiple_ligand_markdown_report": loaded_export_multiple_ligand_markdown_report,
            "prepare_multiple_ligand_run": loaded_prepare_multiple_ligand_run,
            "create_project": loaded_create_project,
            "import_ligand_pdbqt": loaded_import_ligand_pdbqt,
            "import_receptor_pdbqt": loaded_import_receptor_pdbqt,
            "update_box_params": loaded_update_box_params,
            "update_vina_params": loaded_update_vina_params,
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
        "AD4_MULTI_5X72_VINA_DETECTION_FAILED",
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
        "AD4_MULTI_5X72_AUTOGRID_DETECTION_FAILED",
        "DockStart did not resolve the caller-pinned AutoGrid4 4.2.6+ executable.",
        details={"detection": _tool_record(autogrid_detection, autogrid_identity)},
    )
    return {
        "vina": _tool_record(vina_detection, vina_identity),
        "autogrid4": _tool_record(autogrid_detection, autogrid_identity),
    }


def _assert_member_contract(
    members: Any,
    expected: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    require(
        isinstance(members, list) and len(members) == 2,
        "AD4_MULTI_5X72_MEMBER_CONTRACT_INVALID",
        "Prepared run does not contain exactly two ordered members.",
        details={"members": members},
    )
    normalized = [dict(value) for value in members if isinstance(value, Mapping)]
    require(
        len(normalized) == 2
        and [int(value.get("member_index") or 0) for value in normalized] == [1, 2]
        and [str(value.get("source_sha256") or "") for value in normalized]
        == [str(value["sha256"]) for value in expected],
        "AD4_MULTI_5X72_MEMBER_ORDER_MISMATCH",
        "Prepared member order or source identity differs from P59 then P69.",
        details={"members": normalized, "expected": expected},
    )
    return normalized


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
        "ad4_multiple_ligands_5x72_external",
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
        "AD4_MULTI_5X72_AUTOGRID_IDENTITY_MISMATCH",
        "AutoGrid4 does not match --autogrid4-sha256.",
        details={
            "expected_sha256": expected_autogrid_sha,
            "actual": autogrid_identity,
        },
    )
    ordered_members = [p59_identity, p69_identity]
    steps: dict[str, Any] = {
        "source_identity": "passed",
        "tool_file_identity": "passed",
    }
    try:
        with tempfile.TemporaryDirectory(prefix="DockStart_ad4_multi_5x72_") as temporary:
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
                    create_project("official_ad4_multi_5x72", str(work_root)),
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
                require_api(
                    "update_vina_params",
                    update_vina_params(project_root.as_posix(), dict(manifest["protocol"]["vina"])),
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
                    "generate_maps_for_member_union",
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
                    "AD4_MULTI_5X72_MAPS_VALIDATION_FAILED",
                    "Generated union AD4 maps are not active and ready.",
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
                    "AD4_MULTI_5X72_MAP_INPUT_MISMATCH",
                    "Generated union maps do not bind the frozen 5X72 receptor/reference ligand.",
                    details={"maps": maps_evidence},
                )
                steps["union_autogrid_maps"] = "passed"

                prepared = require_api(
                    "prepare_multiple_ligand_run",
                    prepare_multiple_ligand_run(
                        project_root.as_posix(),
                        [p59_identity["path"], p69_identity["path"]],
                    ),
                )
                run_id = str(prepared.get("run_id") or "")
                prepared_metadata = (
                    prepared.get("metadata")
                    if isinstance(prepared.get("metadata"), Mapping)
                    else {}
                )
                require(
                    bool(run_id)
                    and prepared.get("status") == "prepared"
                    and prepared_metadata.get("status") == "prepared"
                    and prepared_metadata.get("protocol_id")
                    == "simultaneous_multi_ligand",
                    "AD4_MULTI_5X72_PREPARED_RUN_INVALID",
                    "DockStart did not prepare the expected simultaneous two-ligand run.",
                    details={"prepared": prepared},
                )
                members = _assert_member_contract(
                    prepared_metadata.get("members"),
                    ordered_members,
                )
                command = [str(value) for value in prepared_metadata.get("command") or []]
                ligand_values = option_values(command, "--ligand")
                maps_values = option_values(command, "--maps")
                scoring_values = option_values(command, "--scoring")
                expected_member_files = [str(value["file"]) for value in members]
                require(
                    ligand_values == expected_member_files
                    and len(maps_values) == 1
                    and scoring_values == ["ad4"]
                    and "--receptor" not in command
                    and same_paths([command[0]], [Path(vina_identity["path"])]),
                    "AD4_MULTI_5X72_COMMAND_CONTRACT_INVALID",
                    "Prepared command is not one ordered P59+P69 AD4 maps search.",
                    details={
                        "command": command,
                        "expected_ligands": expected_member_files,
                    },
                )
                for index, (member, expected) in enumerate(
                    zip(members, ordered_members, strict=True),
                    start=1,
                ):
                    snapshot = file_identity(
                        contained_project_file(
                            project_root,
                            str(member["file"]),
                            label=f"frozen 5X72 member {index}",
                        ),
                        label=f"frozen 5X72 member {index}",
                    )
                    require(
                        snapshot["sha256"] == expected["sha256"]
                        and snapshot["size_bytes"] == expected["size_bytes"],
                        "AD4_MULTI_5X72_MEMBER_SNAPSHOT_MISMATCH",
                        "A frozen 5X72 member changed before execution.",
                        details={"member_index": index, "snapshot": snapshot},
                    )
                frozen_maps = (
                    (prepared_metadata.get("snapshots") or {}).get("ad4_maps")
                    if isinstance(prepared_metadata.get("snapshots"), Mapping)
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
                    and maps_values[0] == str(frozen_maps.get("prefix") or ""),
                    "AD4_MULTI_5X72_FROZEN_MAPS_INVALID",
                    "Prepared joint run did not freeze and command-bind the active map set.",
                    details={"frozen_maps": frozen_maps, "command": command},
                )
                run_map_evidence = [
                    verify_snapshot_record(
                        project_root,
                        record,
                        label=f"joint run frozen map {index}",
                    )
                    for index, record in enumerate(frozen_map_records, start=1)
                    if isinstance(record, Mapping)
                ]
                require(
                    len(run_map_evidence) == len(frozen_map_records),
                    "AD4_MULTI_5X72_FROZEN_MAPS_INVALID",
                    "One or more joint-run map snapshots lack identity evidence.",
                )
                prepared_vina = (
                    prepared_metadata.get("prepared_vina")
                    if isinstance(prepared_metadata.get("prepared_vina"), Mapping)
                    else {}
                )
                require(
                    str(prepared_vina.get("sha256") or "") == vina_identity["sha256"]
                    and int(prepared_vina.get("size_bytes") or 0)
                    == vina_identity["size_bytes"],
                    "AD4_MULTI_5X72_VINA_SNAPSHOT_MISMATCH",
                    "Prepared joint run does not bind the pinned Vina binary.",
                    details={"prepared_vina": prepared_vina},
                )
                steps["ordered_prepared_command"] = "passed"

                executed = require_api(
                    "execute_multiple_ligand_run",
                    execute_multiple_ligand_run(project_root.as_posix(), run_id),
                )
                metadata = (
                    executed.get("metadata")
                    if isinstance(executed.get("metadata"), Mapping)
                    else {}
                )
                scores = executed.get("scores") if isinstance(executed.get("scores"), list) else []
                require(
                    executed.get("status") == "finished"
                    and metadata.get("status") == "finished"
                    and int(executed.get("pose_count") or 0)
                    >= int(manifest["acceptance"]["minimum_joint_pose_count"])
                    and bool(scores),
                    "AD4_MULTI_5X72_RUN_INCOMPLETE",
                    "The ordered 5X72 joint AD4 run did not finish with joint poses.",
                    details={"executed": executed},
                )
                require(
                    metadata.get("score_scope") == "joint_two_ligand_pose"
                    and metadata.get("per_member_scores_available") is False
                    and scores[0].get("joint_affinity_kcal_mol") is not None
                    and all(
                        isinstance(row, Mapping)
                        and row.get("score_is_joint") is True
                        and row.get("per_member_scores_available") is False
                        and not any(
                            "member" in str(key).lower()
                            and "affinity" in str(key).lower()
                            for key in row
                        )
                        for row in scores
                    ),
                    "AD4_MULTI_5X72_SCORE_SCOPE_INVALID",
                    "The result exposed or implied a per-member affinity.",
                    details={"metadata": metadata, "scores": scores},
                )
                best_joint = float(scores[0]["joint_affinity_kcal_mol"])
                score_gate = manifest["acceptance"]["best_joint_affinity_kcal_mol"]
                require(
                    float(score_gate["minimum"])
                    <= best_joint
                    <= float(score_gate["maximum"]),
                    "AD4_MULTI_5X72_SCORE_OUT_OF_RANGE",
                    "Best 5X72 joint AD4 affinity is outside the frozen tutorial tolerance.",
                    details={"best_joint_affinity": best_joint, "gate": score_gate},
                )
                joint_file = contained_project_file(
                    project_root,
                    str(metadata.get("joint_poses_file") or ""),
                    label="5X72 joint poses manifest",
                )
                try:
                    joint = json.loads(joint_file.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"joint_poses.json cannot be parsed: {exc}") from exc
                models = joint.get("models") if isinstance(joint, Mapping) else []
                require(
                    isinstance(models, list)
                    and bool(models)
                    and joint.get("per_member_scores_available") is False
                    and all(
                        isinstance(model, Mapping)
                        and [
                            int(member.get("member_index") or 0)
                            for member in model.get("members") or []
                            if isinstance(member, Mapping)
                        ]
                        == [1, 2]
                        for model in models
                    ),
                    "AD4_MULTI_5X72_OUTPUT_MEMBER_ORDER_INVALID",
                    "Joint output does not preserve P59 then P69 in every pose.",
                    details={"joint_poses_file": str(joint_file)},
                )
                report = require_api(
                    "export_multiple_ligand_markdown_report",
                    export_multiple_ligand_markdown_report(
                        project_root.as_posix(),
                        run_id,
                    ),
                )
                report_file = contained_project_file(
                    project_root,
                    str(report.get("report_file") or ""),
                    label="5X72 joint AD4 Markdown report",
                )
                disclaimer = "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
                require(
                    disclaimer in report_file.read_text(encoding="utf-8", errors="strict"),
                    "AD4_MULTI_5X72_REPORT_DISCLAIMER_MISSING",
                    "The exported joint report lacks the mandatory scientific disclaimer.",
                )
                steps["real_joint_vina_run_and_report"] = "passed"

                output_file = contained_project_file(
                    project_root,
                    str(metadata.get("output_file") or ""),
                    label="5X72 joint output PDBQT",
                )
                return _bound_result({
                    "schema_version": 2,
                    "verifier_id": VERIFIER_ID,
                    "ok": True,
                    "fixture": {
                        "manifest": file_identity(MANIFEST_PATH, label="5X72 AD4 source manifest"),
                        "upstream_tag": manifest["upstream"]["tag"],
                    },
                    "inputs": {
                        "receptor": receptor_identity,
                        "ordered_members": ordered_members,
                    },
                    "tools": tools,
                    "maps": maps_evidence,
                    "run": {
                        "run_id": run_id,
                        "command": command,
                        "member_order": list(manifest["protocol"]["member_order"]),
                        "member_sha256": [value["sha256"] for value in ordered_members],
                        "frozen_map_files": run_map_evidence,
                        "pose_count": int(executed.get("pose_count") or 0),
                        "best_joint_affinity_kcal_mol": best_joint,
                        "score_scope": metadata.get("score_scope"),
                        "per_member_scores_available": False,
                        "output": file_identity(output_file, label="5X72 joint output PDBQT"),
                        "joint_poses": file_identity(joint_file, label="5X72 joint poses manifest"),
                    },
                    "report": file_identity(report_file, label="5X72 joint AD4 Markdown report"),
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
        description="Run the offline 5X72 P59+P69 simultaneous AD4 gate."
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
