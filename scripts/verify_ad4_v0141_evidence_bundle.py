#!/usr/bin/env python3
"""Validate the three v0.14.1 AD4 gate JSON files as one evidence bundle.

This verifier never runs AutoGrid4 or Vina.  It validates the already-produced
gate evidence, each gate's frozen scientific oracle, and (when present) the
source provenance against the current checkout.  Evidence created before
source provenance existed remains explicitly ``legacy_unbound`` and cannot
pass ``--require-source-bound``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from ad4_v0141_common import (  # noqa: E402
    AcceptanceError,
    IMPORT_ORIGIN_ALGORITHM,
    SHA256_PATTERN,
    VINA_1_2_7_CONTRACT,
    bind_source_provenance_or_error,
    canonical_evidence_payload_identity,
    current_source_context,
    error_payload,
    require,
    source_context_fingerprint,
    write_result,
)


VERIFIER_ID = "dockstart_ad4_v0141_evidence_bundle"
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024

FLEXIBLE_ID = "dockstart_ad4_flexible_1fpu_v0141"
MULTIPLE_ID = "dockstart_ad4_multiple_ligands_5x72_v0141"
SCREENING_ID = "dockstart_ad4_serial_screening_v0141"

FLEXIBLE_MANIFEST = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "scientific"
    / "flexible_ad4_1fpu"
    / "source_manifest.json"
)
MULTIPLE_MANIFEST = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "scientific"
    / "multiple_ligands_ad4_5x72"
    / "source_manifest.json"
)
SCREENING_MANIFEST = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "scientific"
    / "serial_screening_ad4"
    / "source_manifest.json"
)

FLEXIBLE_STEPS = (
    "source_identity",
    "tool_file_identity",
    "tool_detection",
    "project_setup",
    "strict_review_and_flexible_preparation",
    "autogrid_maps",
    "prepared_command",
    "real_vina_run_and_report",
)
MULTIPLE_STEPS = (
    "source_identity",
    "tool_file_identity",
    "tool_detection",
    "project_setup",
    "union_autogrid_maps",
    "ordered_prepared_command",
    "real_joint_vina_run_and_report",
)
SCREENING_STEPS = (
    "source_identity",
    "tool_file_identity",
    "tool_detection",
    "project_setup",
    "single_union_map_set",
    "screening_queue_freeze",
    "live_cancel",
    "map_tamper_fail_closed_and_resume",
    "failure_continuation_attempts_and_ranking",
    "report_archive_and_zip",
)


def _load_manifest(path: Path, expected_fixture_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(
            "AD4_EVIDENCE_FIXTURE_MANIFEST_INVALID",
            "A source-bound fixture manifest cannot be parsed.",
            details={
                "fixture_id": expected_fixture_id,
                "error_type": type(exc).__name__,
            },
        ) from exc
    require(
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and payload.get("fixture_id") == expected_fixture_id,
        "AD4_EVIDENCE_FIXTURE_MANIFEST_INVALID",
        "A source-bound fixture manifest does not match its frozen identity.",
        details={"fixture_id": expected_fixture_id},
    )
    return payload


def _number(value: Any, *, verifier_id: str, field: str) -> float:
    require(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A gate oracle contains a missing or non-finite number.",
        details={"verifier_id": verifier_id, "field": field},
    )
    return float(value)


def _positive_int(value: Any, *, verifier_id: str, field: str) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A gate oracle contains an invalid positive integer.",
        details={"verifier_id": verifier_id, "field": field},
    )
    return int(value)


def _require_identity(
    record: Any,
    expected: Mapping[str, Any],
    *,
    verifier_id: str,
    field: str,
) -> None:
    actual_size = -1
    if isinstance(record, Mapping):
        try:
            actual_size = int(record.get("size_bytes") or 0)
        except (TypeError, ValueError):
            actual_size = -1
    require(
        isinstance(record, Mapping)
        and actual_size == int(expected.get("size_bytes") or 0)
        and str(record.get("sha256") or "").lower()
        == str(expected.get("sha256") or "").lower(),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A gate input identity differs from its frozen source contract.",
        details={"verifier_id": verifier_id, "field": field},
    )


def _version_tuple(value: Any) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def _same_recorded_path(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _command_option_values(
    command: Any,
    option: str,
    *,
    verifier_id: str,
) -> list[str]:
    require(
        isinstance(command, list) and all(isinstance(value, str) for value in command),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A recorded command is not a string argument array.",
        details={"verifier_id": verifier_id, "field": "command"},
    )
    positions = [index for index, value in enumerate(command) if value == option]
    require(
        len(positions) == 1,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A recorded command option is missing or duplicated.",
        details={"verifier_id": verifier_id, "option": option},
    )
    values: list[str] = []
    for value in command[positions[0] + 1 :]:
        if value.startswith("--"):
            break
        values.append(value)
    return values


def _identity_key(record: Any) -> tuple[int, str] | None:
    if not isinstance(record, Mapping):
        return None
    size = record.get("size_bytes")
    sha256 = str(record.get("sha256") or "").lower()
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or SHA256_PATTERN.fullmatch(sha256) is None
    ):
        return None
    return int(size), sha256


def _require_valid_identity(
    record: Any,
    *,
    verifier_id: str,
    field: str,
) -> tuple[int, str]:
    identity = _identity_key(record)
    require(
        identity is not None,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A recorded output or snapshot lacks a complete immutable identity.",
        details={"verifier_id": verifier_id, "field": field},
    )
    return identity


def _recorded_basename(record: Any) -> str:
    if not isinstance(record, Mapping):
        return ""
    path = str(record.get("path") or "").replace("\\", "/")
    return path.rsplit("/", 1)[-1]


def _identity_map_by_basename(
    records: Any,
    *,
    verifier_id: str,
    field: str,
) -> dict[str, tuple[int, str]]:
    require(
        isinstance(records, list),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A frozen file collection is missing.",
        details={"verifier_id": verifier_id, "field": field},
    )
    identities: dict[str, tuple[int, str]] = {}
    for index, record in enumerate(records):
        basename = _recorded_basename(record)
        identity = _require_valid_identity(
            record,
            verifier_id=verifier_id,
            field=f"{field}[{index}]",
        )
        require(
            bool(basename) and basename not in identities,
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A frozen file collection has an empty or duplicate basename.",
            details={
                "verifier_id": verifier_id,
                "field": field,
                "basename": basename,
            },
        )
        identities[basename] = identity
    return identities


def _validate_maps_input_identity(
    maps_record: Mapping[str, Any],
    key: str,
    expected: Any,
    *,
    verifier_id: str,
    field: str,
) -> None:
    record = maps_record.get(key) if isinstance(maps_record.get(key), Mapping) else {}
    expected_key = _require_valid_identity(
        expected,
        verifier_id=verifier_id,
        field=f"{field}.expected",
    )
    verified = (
        record.get("verified_identity")
        if isinstance(record.get("verified_identity"), Mapping)
        else {}
    )
    require(
        _identity_key(record) == expected_key
        and str(record.get("source_sha256") or "").lower() == expected_key[1]
        and _identity_key(verified) == expected_key,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "A generated map input is not linked to its frozen source snapshot.",
        details={"verifier_id": verifier_id, "field": field},
    )


def _validate_toolchain_and_maps(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
    maps_record: Any,
    *,
    verifier_id: str,
) -> dict[str, Any]:
    tools = payload.get("tools") if isinstance(payload.get("tools"), Mapping) else {}
    vina = tools.get("vina") if isinstance(tools.get("vina"), Mapping) else {}
    _require_identity(
        vina,
        VINA_1_2_7_CONTRACT,
        verifier_id=verifier_id,
        field="tools.vina",
    )
    require(
        vina.get("detected_status") == "ok"
        and vina.get("detected_version") == VINA_1_2_7_CONTRACT["version"]
        and _same_recorded_path(vina.get("path"), vina.get("detected_path")),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The Vina tool evidence is not the frozen detected 1.2.7 executable.",
        details={"verifier_id": verifier_id, "field": "tools.vina"},
    )

    autogrid = (
        tools.get("autogrid4")
        if isinstance(tools.get("autogrid4"), Mapping)
        else {}
    )
    autogrid_key = _identity_key(autogrid)
    minimum_version = str(manifest["toolchain"]["autogrid4"]["minimum_version"])
    require(
        autogrid_key is not None
        and autogrid.get("detected_status") == "ok"
        and _version_tuple(autogrid.get("detected_version"))
        >= _version_tuple(minimum_version)
        and _same_recorded_path(
            autogrid.get("path"),
            autogrid.get("detected_path"),
        ),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "AutoGrid4 tool evidence is missing its caller-frozen detected identity.",
        details={"verifier_id": verifier_id, "field": "tools.autogrid4"},
    )

    maps = maps_record if isinstance(maps_record, Mapping) else {}
    maps_autogrid = (
        maps.get("autogrid") if isinstance(maps.get("autogrid"), Mapping) else {}
    )
    maps_identity = (
        maps_autogrid.get("identity")
        if isinstance(maps_autogrid.get("identity"), Mapping)
        else {}
    )
    autogrid_command = maps_autogrid.get("command")
    expected_autogrid_command = [
        str(autogrid.get("path") or ""),
        "-p",
        "receptor.gpf",
        "-l",
        "autogrid.glg",
    ]
    require(
        autogrid_key is not None
        and _identity_key(maps_identity) == autogrid_key
        and _same_recorded_path(maps_identity.get("path"), autogrid.get("path"))
        and _same_recorded_path(maps_autogrid.get("path"), autogrid.get("path"))
        and str(maps_autogrid.get("sha256") or "").lower() == autogrid_key[1]
        and _version_tuple(maps_autogrid.get("version"))
        == _version_tuple(autogrid.get("detected_version"))
        and maps_autogrid.get("exit_code") == 0
        and autogrid_command == expected_autogrid_command
        and isinstance(maps_autogrid.get("log_summary"), Mapping)
        and maps_autogrid["log_summary"].get("successful_completion") is True
        and maps_autogrid["log_summary"].get("has_error") is False,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "AutoGrid4 maps evidence does not match the caller-frozen tool identity and command.",
        details={"verifier_id": verifier_id, "field": "maps.autogrid"},
    )
    ligand_types = maps.get("ligand_atom_types")
    required_files = maps.get("required_files")
    map_files = maps.get("files")
    ligand_type_values = ligand_types if isinstance(ligand_types, list) else []
    expected_required = [
        "receptor.maps.fld",
        *(f"receptor.{atom_type}.map" for atom_type in ligand_type_values),
        "receptor.e.map",
        "receptor.d.map",
    ]
    expected_files = [*expected_required, "receptor.maps.xyz"]
    file_identities = _identity_map_by_basename(
        map_files,
        verifier_id=verifier_id,
        field="maps.files",
    ) if isinstance(map_files, list) else {}
    require(
        isinstance(ligand_types, list)
        and bool(ligand_types)
        and all(isinstance(value, str) and value for value in ligand_types)
        and ligand_types == sorted(set(ligand_types))
        and required_files == expected_required
        and isinstance(map_files, list)
        and list(file_identities) == expected_files
        and _identity_key(maps.get("manifest")) is not None
        and _identity_key(maps.get("gpf")) is not None
        and _identity_key(maps.get("glg")) is not None,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The generated maps evidence is incomplete or lacks immutable file identities.",
        details={"verifier_id": verifier_id, "field": "maps.files"},
    )
    return {
        "vina_sha256": str(vina["sha256"]),
        "autogrid4_sha256": autogrid_key[1],
        "autogrid4_version": str(maps_autogrid["version"]),
        "map_file_count": len(map_files),
        "map_files": file_identities,
    }


def _validate_vina_command_executable(
    command: Any,
    tools: Any,
    *,
    verifier_id: str,
) -> list[str]:
    require(
        isinstance(command, list)
        and bool(command)
        and all(isinstance(value, str) for value in command)
        and isinstance(tools, Mapping)
        and isinstance(tools.get("vina"), Mapping)
        and _same_recorded_path(command[0], tools["vina"].get("path")),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The recorded Vina command does not use the frozen Vina executable.",
        details={"verifier_id": verifier_id, "field": "run.command"},
    )
    return list(command)


def _flexible_oracle(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest(FLEXIBLE_MANIFEST, "ad4_flexible_1fpu_external")
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), Mapping) else {}
    contracts = manifest["source_files"]
    _require_identity(
        inputs.get("receptor"),
        contracts["receptor_h_pdb"],
        verifier_id=FLEXIBLE_ID,
        field="inputs.receptor",
    )
    _require_identity(
        inputs.get("ligand"),
        contracts["ligand_pdbqt"],
        verifier_id=FLEXIBLE_ID,
        field="inputs.ligand",
    )
    fixture = payload.get("fixture") if isinstance(payload.get("fixture"), Mapping) else {}
    _require_identity(
        fixture.get("bad_residue_contract"),
        manifest["preparation_contract"]["bad_residue_contract"],
        verifier_id=FLEXIBLE_ID,
        field="fixture.bad_residue_contract",
    )
    preparation = (
        payload.get("preparation")
        if isinstance(payload.get("preparation"), Mapping)
        else {}
    )
    selected_residues = (
        preparation.get("selected_residues")
        if isinstance(preparation.get("selected_residues"), list)
        else []
    )
    output_sha256 = preparation.get("output_sha256")
    preparation_outputs = (
        preparation.get("outputs")
        if isinstance(preparation.get("outputs"), Mapping)
        else {}
    )
    frozen_output_hashes = manifest["preparation_contract"]["output_sha256"]
    for output_name, expected_sha256 in frozen_output_hashes.items():
        output_identity = _require_valid_identity(
            preparation_outputs.get(output_name),
            verifier_id=FLEXIBLE_ID,
            field=f"preparation.outputs.{output_name}",
        )
        require(
            output_identity[1] == str(expected_sha256).lower(),
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A flexible preparation output differs from its frozen hash contract.",
            details={
                "verifier_id": FLEXIBLE_ID,
                "field": f"preparation.outputs.{output_name}",
            },
        )
    require(
        selected_residues
        == list(manifest["preparation_contract"]["selected_flexible_residues"])
        and isinstance(output_sha256, Mapping)
        and dict(output_sha256)
        == dict(manifest["preparation_contract"]["output_sha256"]),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The flexible-receptor preparation oracle differs from its frozen contract.",
        details={"verifier_id": FLEXIBLE_ID, "field": "preparation"},
    )
    tools = payload.get("tools") if isinstance(payload.get("tools"), Mapping) else {}
    meeko = tools.get("meeko") if isinstance(tools.get("meeko"), Mapping) else {}
    require(
        meeko.get("detected_status") == "ok"
        and meeko.get("detected_version")
        == manifest["preparation_contract"]["meeko_version"]
        and _same_recorded_path(meeko.get("path"), meeko.get("detected_path")),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "Flexible preparation was not recorded with the frozen Meeko version.",
        details={"verifier_id": FLEXIBLE_ID, "field": "tools.meeko"},
    )
    maps = payload.get("maps") if isinstance(payload.get("maps"), Mapping) else {}
    tool_summary = _validate_toolchain_and_maps(
        payload,
        manifest,
        maps,
        verifier_id=FLEXIBLE_ID,
    )
    require(
        maps.get("ligand_atom_types") == ["A", "C", "HD", "N", "NA", "OA"],
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The flexible gate map atom types differ from the frozen ligand contract.",
        details={"verifier_id": FLEXIBLE_ID, "field": "maps.ligand_atom_types"},
    )
    _validate_maps_input_identity(
        maps,
        "receptor",
        preparation_outputs.get("rigid_pdbqt"),
        verifier_id=FLEXIBLE_ID,
        field="maps.receptor",
    )
    _validate_maps_input_identity(
        maps,
        "ligand",
        inputs.get("ligand"),
        verifier_id=FLEXIBLE_ID,
        field="maps.ligand",
    )
    flexible_maps = (
        maps.get("flexible_receptor")
        if isinstance(maps.get("flexible_receptor"), Mapping)
        else {}
    )
    flex_selected = flexible_maps.get("selected_residues")
    flex_selectors = [
        value.get("selector")
        for value in flex_selected
        if isinstance(value, Mapping)
    ] if isinstance(flex_selected, list) else []
    require(
        flexible_maps.get("mode") == "flexible"
        and flexible_maps.get("preparation_id") == preparation.get("preparation_id")
        and str(flexible_maps.get("rigid_sha256") or "").lower()
        == str(frozen_output_hashes["rigid_pdbqt"]).lower()
        and str(flexible_maps.get("flex_sha256") or "").lower()
        == str(frozen_output_hashes["flex_pdbqt"]).lower()
        and _identity_key(flexible_maps.get("verified_identity"))
        == _identity_key(preparation_outputs.get("flex_pdbqt"))
        and flex_selectors == selected_residues,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "Flexible-map evidence is not linked to the frozen rigid/flex preparation.",
        details={"verifier_id": FLEXIBLE_ID, "field": "maps.flexible_receptor"},
    )
    run = payload.get("run") if isinstance(payload.get("run"), Mapping) else {}
    command = _validate_vina_command_executable(
        run.get("command"),
        tools,
        verifier_id=FLEXIBLE_ID,
    )
    config_values = _command_option_values(command, "--config", verifier_id=FLEXIBLE_ID)
    map_values = _command_option_values(command, "--maps", verifier_id=FLEXIBLE_ID)
    scoring_values = _command_option_values(command, "--scoring", verifier_id=FLEXIBLE_ID)
    output_values = _command_option_values(command, "--out", verifier_id=FLEXIBLE_ID)
    flex_values = _command_option_values(command, "--flex", verifier_id=FLEXIBLE_ID)
    require(
        len(command) == 11
        and command[1::2] == ["--config", "--maps", "--scoring", "--out", "--flex"]
        and len(config_values) == len(map_values) == len(output_values) == len(flex_values) == 1
        and scoring_values == ["ad4"]
        and config_values[0].replace("\\", "/").endswith("/config_snapshot.txt")
        and map_values[0].replace("\\", "/").endswith("/inputs/maps/receptor")
        and output_values[0].replace("\\", "/").endswith("/out.pdbqt")
        and flex_values[0].replace("\\", "/").endswith("/inputs/flex.pdbqt")
        and "--receptor" not in command
        and "--ligand" not in command,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The flexible gate command does not implement the frozen AD4 protocol.",
        details={"verifier_id": FLEXIBLE_ID, "field": "run.command"},
    )
    affinity = _number(
        run.get("best_affinity_kcal_mol"),
        verifier_id=FLEXIBLE_ID,
        field="run.best_affinity_kcal_mol",
    )
    rmsd = _number(
        run.get("first_pose_no_fit_heavy_atom_rmsd_angstrom"),
        verifier_id=FLEXIBLE_ID,
        field="run.first_pose_no_fit_heavy_atom_rmsd_angstrom",
    )
    score_gate = manifest["acceptance"]["best_affinity_kcal_mol"]
    require(
        run.get("status") == "finished"
        and float(score_gate["minimum"]) <= affinity <= float(score_gate["maximum"])
        and rmsd
        <= float(
            manifest["acceptance"][
                "first_pose_no_fit_heavy_atom_rmsd_max_angstrom"
            ]
        ),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The flexible 1FPU scientific oracle is outside its frozen acceptance range.",
        details={"verifier_id": FLEXIBLE_ID, "field": "run"},
    )
    _require_valid_identity(run.get("output"), verifier_id=FLEXIBLE_ID, field="run.output")
    _require_valid_identity(payload.get("report"), verifier_id=FLEXIBLE_ID, field="report")
    return {
        "status": "passed",
        "best_affinity_kcal_mol": affinity,
        "first_pose_no_fit_heavy_atom_rmsd_angstrom": rmsd,
        "toolchain_and_maps": tool_summary,
    }


def _multiple_oracle(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest(
        MULTIPLE_MANIFEST,
        "ad4_multiple_ligands_5x72_external",
    )
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), Mapping) else {}
    contracts = manifest["source_files"]
    _require_identity(
        inputs.get("receptor"),
        contracts["receptor_pdbqt"],
        verifier_id=MULTIPLE_ID,
        field="inputs.receptor",
    )
    ordered_members = (
        inputs.get("ordered_members")
        if isinstance(inputs.get("ordered_members"), list)
        else []
    )
    require(
        len(ordered_members) == 2,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The joint gate does not contain exactly two ordered input identities.",
        details={"verifier_id": MULTIPLE_ID, "field": "inputs.ordered_members"},
    )
    _require_identity(
        ordered_members[0],
        contracts["ligand_p59_pdbqt"],
        verifier_id=MULTIPLE_ID,
        field="inputs.ordered_members[0]",
    )
    _require_identity(
        ordered_members[1],
        contracts["ligand_p69_pdbqt"],
        verifier_id=MULTIPLE_ID,
        field="inputs.ordered_members[1]",
    )
    maps = payload.get("maps") if isinstance(payload.get("maps"), Mapping) else {}
    tool_summary = _validate_toolchain_and_maps(
        payload,
        manifest,
        maps,
        verifier_id=MULTIPLE_ID,
    )
    require(
        maps.get("ligand_atom_types") == ["A", "C", "F", "HD", "N", "OA"],
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The joint gate map atom types differ from the frozen P59/P69 union.",
        details={"verifier_id": MULTIPLE_ID, "field": "maps.ligand_atom_types"},
    )
    _validate_maps_input_identity(
        maps,
        "receptor",
        inputs.get("receptor"),
        verifier_id=MULTIPLE_ID,
        field="maps.receptor",
    )
    _validate_maps_input_identity(
        maps,
        "ligand",
        ordered_members[0],
        verifier_id=MULTIPLE_ID,
        field="maps.ligand",
    )
    run = payload.get("run") if isinstance(payload.get("run"), Mapping) else {}
    tools = payload.get("tools") if isinstance(payload.get("tools"), Mapping) else {}
    command = _validate_vina_command_executable(
        run.get("command"),
        tools,
        verifier_id=MULTIPLE_ID,
    )
    config_values = _command_option_values(command, "--config", verifier_id=MULTIPLE_ID)
    ligand_values = _command_option_values(command, "--ligand", verifier_id=MULTIPLE_ID)
    map_values = _command_option_values(command, "--maps", verifier_id=MULTIPLE_ID)
    scoring_values = _command_option_values(command, "--scoring", verifier_id=MULTIPLE_ID)
    output_values = _command_option_values(command, "--out", verifier_id=MULTIPLE_ID)
    ligand_basenames = [value.replace("\\", "/").rsplit("/", 1)[-1] for value in ligand_values]
    require(
        len(command) == 12
        and command[1] == "--config"
        and command[3] == "--ligand"
        and command[6] == "--maps"
        and command[8] == "--scoring"
        and command[10] == "--out"
        and len(config_values) == len(map_values) == len(output_values) == 1
        and ligand_basenames == ["ligand_001.pdbqt", "ligand_002.pdbqt"]
        and scoring_values == ["ad4"]
        and config_values[0].replace("\\", "/").endswith("/config_snapshot.txt")
        and map_values[0].replace("\\", "/").endswith("/inputs/ad4_maps/receptor")
        and output_values[0].replace("\\", "/").endswith("/out.pdbqt")
        and "--receptor" not in command
        and "--flex" not in command,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The joint gate command does not preserve the ordered two-ligand AD4 protocol.",
        details={"verifier_id": MULTIPLE_ID, "field": "run.command"},
    )
    generated_files = tool_summary["map_files"]
    frozen_files = _identity_map_by_basename(
        run.get("frozen_map_files"),
        verifier_id=MULTIPLE_ID,
        field="run.frozen_map_files",
    )
    require(
        frozen_files == generated_files,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The joint run did not freeze the exact generated AD4 map set.",
        details={"verifier_id": MULTIPLE_ID, "field": "run.frozen_map_files"},
    )
    affinity = _number(
        run.get("best_joint_affinity_kcal_mol"),
        verifier_id=MULTIPLE_ID,
        field="run.best_joint_affinity_kcal_mol",
    )
    pose_count = _positive_int(
        run.get("pose_count"),
        verifier_id=MULTIPLE_ID,
        field="run.pose_count",
    )
    expected_member_hashes = [
        str(contracts["ligand_p59_pdbqt"]["sha256"]),
        str(contracts["ligand_p69_pdbqt"]["sha256"]),
    ]
    score_gate = manifest["acceptance"]["best_joint_affinity_kcal_mol"]
    member_order = run.get("member_order") if isinstance(run.get("member_order"), list) else []
    member_sha256 = (
        run.get("member_sha256") if isinstance(run.get("member_sha256"), list) else []
    )
    require(
        member_order == list(manifest["protocol"]["member_order"])
        and member_sha256 == expected_member_hashes
        and pose_count >= int(manifest["acceptance"]["minimum_joint_pose_count"])
        and float(score_gate["minimum"]) <= affinity <= float(score_gate["maximum"])
        and run.get("score_scope") == manifest["acceptance"]["score_scope"]
        and run.get("per_member_scores_available") is False,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The 5X72 joint scientific oracle differs from its frozen contract.",
        details={"verifier_id": MULTIPLE_ID, "field": "run"},
    )
    _require_valid_identity(run.get("output"), verifier_id=MULTIPLE_ID, field="run.output")
    _require_valid_identity(
        run.get("joint_poses"),
        verifier_id=MULTIPLE_ID,
        field="run.joint_poses",
    )
    _require_valid_identity(payload.get("report"), verifier_id=MULTIPLE_ID, field="report")
    return {
        "status": "passed",
        "pose_count": pose_count,
        "best_joint_affinity_kcal_mol": affinity,
        "score_scope": str(run.get("score_scope")),
        "toolchain_and_maps": tool_summary,
    }


def _screening_oracle(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest(
        SCREENING_MANIFEST,
        "ad4_serial_screening_5x72_external",
    )
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), Mapping) else {}
    contracts = manifest["source_files"]
    _require_identity(
        inputs.get("receptor"),
        contracts["receptor_pdbqt"],
        verifier_id=SCREENING_ID,
        field="inputs.receptor",
    )
    source_ligands = (
        inputs.get("source_ligands")
        if isinstance(inputs.get("source_ligands"), list)
        else []
    )
    require(
        len(source_ligands) == 2,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial gate does not contain its two frozen source ligands.",
        details={"verifier_id": SCREENING_ID, "field": "inputs.source_ligands"},
    )
    _require_identity(
        source_ligands[0],
        contracts["ligand_p59_pdbqt"],
        verifier_id=SCREENING_ID,
        field="inputs.source_ligands[0]",
    )
    _require_identity(
        source_ligands[1],
        contracts["ligand_p69_pdbqt"],
        verifier_id=SCREENING_ID,
        field="inputs.source_ligands[1]",
    )
    workload_contract = manifest["workload"]
    derived_workload = (
        inputs.get("derived_workload")
        if isinstance(inputs.get("derived_workload"), list)
        else []
    )
    expected_workload_count = int(workload_contract["ligand_count"])
    workload_identities: dict[int, tuple[int, str]] = {}
    require(
        len(derived_workload) == expected_workload_count
        and len(workload_contract["source_order"]) == expected_workload_count,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial derived workload does not contain the frozen item count.",
        details={"verifier_id": SCREENING_ID, "field": "inputs.derived_workload"},
    )
    for index, record in enumerate(derived_workload, start=1):
        require(
            isinstance(record, Mapping),
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A serial workload identity is not an object.",
            details={
                "verifier_id": SCREENING_ID,
                "field": f"inputs.derived_workload[{index - 1}]",
            },
        )
        expected_source = str(workload_contract["source_order"][index - 1])
        expected_marker = (
            f"REMARK DOCKSTART V0141 SERIAL AUDIT ITEM {index:02d} "
            f"SOURCE {expected_source}"
        )
        identity = _require_valid_identity(
            record,
            verifier_id=SCREENING_ID,
            field=f"inputs.derived_workload[{index - 1}]",
        )
        require(
            record.get("order") == index
            and record.get("source") == expected_source
            and record.get("marker") == expected_marker,
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A serial workload item changed order, source, or audit marker.",
            details={
                "verifier_id": SCREENING_ID,
                "field": f"inputs.derived_workload[{index - 1}]",
            },
        )
        workload_identities[index] = identity
    require(
        len(set(workload_identities.values())) == expected_workload_count,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "Serial workload snapshots are not all uniquely identified.",
        details={"verifier_id": SCREENING_ID, "field": "inputs.derived_workload"},
    )

    maps = payload.get("maps") if isinstance(payload.get("maps"), Mapping) else {}
    generated_maps = (
        maps.get("generated") if isinstance(maps.get("generated"), Mapping) else {}
    )
    tool_summary = _validate_toolchain_and_maps(
        payload,
        manifest,
        generated_maps,
        verifier_id=SCREENING_ID,
    )
    require(
        generated_maps.get("ligand_atom_types")
        == ["A", "C", "F", "HD", "N", "OA"],
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial gate map atom types differ from the frozen workload union.",
        details={
            "verifier_id": SCREENING_ID,
            "field": "maps.generated.ligand_atom_types",
        },
    )
    _validate_maps_input_identity(
        generated_maps,
        "receptor",
        inputs.get("receptor"),
        verifier_id=SCREENING_ID,
        field="maps.generated.receptor",
    )
    _validate_maps_input_identity(
        generated_maps,
        "ligand",
        source_ligands[0],
        verifier_id=SCREENING_ID,
        field="maps.generated.ligand",
    )
    frozen_map_files = _identity_map_by_basename(
        maps.get("screening_frozen_files"),
        verifier_id=SCREENING_ID,
        field="maps.screening_frozen_files",
    )
    require(
        frozen_map_files == tool_summary["map_files"],
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial queue did not freeze the exact generated AD4 map set.",
        details={
            "verifier_id": SCREENING_ID,
            "field": "maps.screening_frozen_files",
        },
    )
    screening = (
        payload.get("screening")
        if isinstance(payload.get("screening"), Mapping)
        else {}
    )
    acceptance = manifest["acceptance"]
    attempts = screening.get("attempts") if isinstance(screening.get("attempts"), list) else []
    expected_attempt_count = int(acceptance["attempt_count"])
    expected_failed_order = int(manifest["fault_injection"]["intentionally_fail_item_order"])
    succeeded_count = _positive_int(
        screening.get("succeeded_count"),
        verifier_id=SCREENING_ID,
        field="screening.succeeded_count",
    )
    failed_count = _positive_int(
        screening.get("failed_count"),
        verifier_id=SCREENING_ID,
        field="screening.failed_count",
    )
    require(
        screening.get("status") == acceptance["final_status"]
        and succeeded_count == acceptance["succeeded_count"]
        and failed_count == acceptance["failed_count"]
        and len(attempts) == expected_attempt_count
        and all(isinstance(value, Mapping) for value in attempts)
        and all(
            isinstance(value.get("order"), int)
            and not isinstance(value.get("order"), bool)
            for value in attempts
        )
        and [int(value.get("order") or 0) for value in attempts]
        == list(range(1, expected_attempt_count + 1))
        and [
            int(value.get("order") or 0)
            for value in attempts
            if value.get("status") == "failed"
        ]
        == [expected_failed_order]
        and all(
            value.get("status")
            == ("failed" if int(value.get("order") or 0) == expected_failed_order else "succeeded")
            for value in attempts
        ),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial queue terminal-state oracle differs from its frozen contract.",
        details={"verifier_id": SCREENING_ID, "field": "screening"},
    )
    frozen_attempt_map_prefix = str(screening.get("frozen_attempt_map_prefix") or "")
    tools = payload.get("tools") if isinstance(payload.get("tools"), Mapping) else {}
    config_identities: set[tuple[int, str]] = set()
    successful_attempts: list[tuple[float, int, str]] = []
    for attempt in attempts:
        order = int(attempt.get("order") or 0)
        expected_status = "failed" if order == expected_failed_order else "succeeded"
        expected_item_id = f"ligand_{order:04d}"
        command = _validate_vina_command_executable(
            attempt.get("command"),
            tools,
            verifier_id=SCREENING_ID,
        )
        require(
            len(command) == 9
            and command[1::2] == ["--config", "--maps", "--scoring", "--out"]
            and _command_option_values(command, "--config", verifier_id=SCREENING_ID)
            == ["config.txt"]
            and _command_option_values(command, "--maps", verifier_id=SCREENING_ID)
            == [frozen_attempt_map_prefix]
            and _command_option_values(command, "--scoring", verifier_id=SCREENING_ID)
            == ["ad4"]
            and _command_option_values(command, "--out", verifier_id=SCREENING_ID)
            == ["out.pdbqt"]
            and "--receptor" not in command
            and "--ligand" not in command
            and "--flex" not in command,
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A serial attempt command does not use the one frozen AD4 map prefix.",
            details={
                "verifier_id": SCREENING_ID,
                "field": f"screening.attempts[{order - 1}].command",
            },
        )
        require(
            attempt.get("item_id") == expected_item_id
            and attempt.get("status") == expected_status
            and _identity_key(attempt.get("receptor")) == _identity_key(inputs.get("receptor"))
            and _identity_key(attempt.get("ligand")) == workload_identities.get(order),
            "AD4_EVIDENCE_ORACLE_MISMATCH",
            "A serial attempt is not linked to its ordered receptor and ligand snapshots.",
            details={
                "verifier_id": SCREENING_ID,
                "field": f"screening.attempts[{order - 1}]",
            },
        )
        config_identities.add(
            _require_valid_identity(
                attempt.get("config"),
                verifier_id=SCREENING_ID,
                field=f"screening.attempts[{order - 1}].config",
            )
        )
        if payload.get("schema_version") == 2:
            expected_exit = int(manifest["fault_injection"]["failed_item_exit_code"]) if order == expected_failed_order else 0
            require(
                attempt.get("exit_code") == expected_exit,
                "AD4_EVIDENCE_ORACLE_MISMATCH",
                "A schema-v2 serial attempt lacks the frozen exit-code evidence.",
                details={
                    "verifier_id": SCREENING_ID,
                    "field": f"screening.attempts[{order - 1}].exit_code",
                },
            )
        if expected_status == "succeeded":
            affinity = _number(
                attempt.get("best_affinity_kcal_mol"),
                verifier_id=SCREENING_ID,
                field=f"screening.attempts[{order - 1}].best_affinity_kcal_mol",
            )
            _require_valid_identity(
                attempt.get("output"),
                verifier_id=SCREENING_ID,
                field=f"screening.attempts[{order - 1}].output",
            )
            successful_attempts.append((affinity, order, expected_item_id))
        else:
            require(
                attempt.get("best_affinity_kcal_mol") is None
                and attempt.get("output") is None,
                "AD4_EVIDENCE_ORACLE_MISMATCH",
                "The intentionally failed serial item unexpectedly has score or output evidence.",
                details={
                    "verifier_id": SCREENING_ID,
                    "field": f"screening.attempts[{order - 1}]",
                },
            )
    require(
        bool(frozen_attempt_map_prefix)
        and len(config_identities) == 1
        and len(successful_attempts) == int(acceptance["succeeded_count"]),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "Serial attempts did not share one frozen config identity and map prefix.",
        details={"verifier_id": SCREENING_ID, "field": "screening.attempts"},
    )
    ranking = (
        screening.get("ranking")
        if isinstance(screening.get("ranking"), Mapping)
        else {}
    )
    ranked_ids = (
        ranking.get("ranked_item_ids")
        if isinstance(ranking.get("ranked_item_ids"), list)
        else []
    )
    raw_affinities = (
        ranking.get("ranked_affinities_kcal_mol")
        if isinstance(ranking.get("ranked_affinities_kcal_mol"), list)
        else []
    )
    affinities = [
        _number(
            value,
            verifier_id=SCREENING_ID,
            field=f"screening.ranking.affinity[{index}]",
        )
        for index, value in enumerate(raw_affinities)
    ]
    expected_ranking = sorted(successful_attempts, key=lambda value: (value[0], value[1]))
    require(
        len(ranked_ids) == int(acceptance["succeeded_count"])
        and len(set(str(value) for value in ranked_ids)) == len(ranked_ids)
        and len(affinities) == len(ranked_ids)
        and affinities == sorted(affinities),
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial queue ranking oracle is incomplete or not ascending by affinity.",
        details={"verifier_id": SCREENING_ID, "field": "screening.ranking"},
    )
    require(
        list(zip(affinities, ranked_ids))
        == [(value[0], value[2]) for value in expected_ranking]
        and f"ligand_{expected_failed_order:04d}" not in ranked_ids,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial ranking is not the exact affinity/order ranking of successful attempts.",
        details={"verifier_id": SCREENING_ID, "field": "screening.ranking"},
    )
    _require_valid_identity(
        ranking.get("summary"),
        verifier_id=SCREENING_ID,
        field="screening.ranking.summary",
    )
    _require_valid_identity(
        ranking.get("ranked_top_n"),
        verifier_id=SCREENING_ID,
        field="screening.ranking.ranked_top_n",
    )
    cancellation = (
        payload.get("cancellation")
        if isinstance(payload.get("cancellation"), Mapping)
        else {}
    )
    tamper = (
        maps.get("tamper_resume_rejection")
        if isinstance(maps.get("tamper_resume_rejection"), Mapping)
        else {}
    )
    require(
        cancellation.get("item_order")
        == int(manifest["fault_injection"]["request_cancel_while_item_order_is_running"])
        and cancellation.get("process_alive_before_request") is True
        and isinstance(cancellation.get("response"), Mapping)
        and cancellation["response"].get("ok") is True
        and tamper.get("ok") is False
        and isinstance(tamper.get("error"), Mapping)
        and tamper["error"].get("code") == acceptance["tampered_resume_error_code"],
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial cancel/tamper fail-closed oracle differs from its frozen contract.",
        details={"verifier_id": SCREENING_ID, "field": "cancellation_or_tamper"},
    )
    archive = payload.get("archive") if isinstance(payload.get("archive"), Mapping) else {}
    zip_record = archive.get("zip") if isinstance(archive.get("zip"), Mapping) else {}
    require(
        bool(str(archive.get("archive_id") or ""))
        and archive.get("relative_path")
        == f"screening/archive/{archive.get('archive_id')}"
        and _positive_int(
            archive.get("zip_entry_count"),
            verifier_id=SCREENING_ID,
            field="archive.zip_entry_count",
        )
        > expected_attempt_count
        and _positive_int(
            zip_record.get("size_bytes"),
            verifier_id=SCREENING_ID,
            field="archive.zip.size_bytes",
        )
        > 0
        and SHA256_PATTERN.fullmatch(str(zip_record.get("sha256") or "").lower())
        is not None
        and SHA256_PATTERN.fullmatch(
            str(archive.get("payload_tree_sha256") or "").lower()
        )
        is not None,
        "AD4_EVIDENCE_ORACLE_MISMATCH",
        "The serial archive/ZIP oracle is missing complete identity evidence.",
        details={"verifier_id": SCREENING_ID, "field": "archive"},
    )
    _require_valid_identity(payload.get("report"), verifier_id=SCREENING_ID, field="report")
    return {
        "status": "passed",
        "screening_status": str(screening.get("status")),
        "attempt_count": len(attempts),
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "toolchain_and_maps": tool_summary,
    }


GATE_CONTRACTS: dict[str, dict[str, Any]] = {
    FLEXIBLE_ID: {
        "name": "flexible_1fpu",
        "verifier_path": ROOT / "scripts" / "verify_ad4_flexible_1fpu.py",
        "manifest_path": FLEXIBLE_MANIFEST,
        "steps": FLEXIBLE_STEPS,
        "oracle": _flexible_oracle,
    },
    MULTIPLE_ID: {
        "name": "multiple_ligands_5x72",
        "verifier_path": ROOT / "scripts" / "verify_ad4_multiple_ligands_5x72.py",
        "manifest_path": MULTIPLE_MANIFEST,
        "steps": MULTIPLE_STEPS,
        "oracle": _multiple_oracle,
    },
    SCREENING_ID: {
        "name": "serial_screening",
        "verifier_path": ROOT / "scripts" / "verify_ad4_serial_screening.py",
        "manifest_path": SCREENING_MANIFEST,
        "steps": SCREENING_STEPS,
        "oracle": _screening_oracle,
    },
}
GATE_ORDER = (FLEXIBLE_ID, MULTIPLE_ID, SCREENING_ID)


def _bundle_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    return bind_source_provenance_or_error(
        payload,
        repo_root=ROOT,
        verifier_path=Path(__file__),
        fixture_manifest_paths=[
            FLEXIBLE_MANIFEST,
            MULTIPLE_MANIFEST,
            SCREENING_MANIFEST,
        ],
        validate_import_origins=False,
        expected_verifier_id=VERIFIER_ID,
    )


def _read_evidence(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied = Path(path).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceError(
            "AD4_EVIDENCE_FILE_MISSING",
            "An evidence JSON file is missing.",
            details={"file_name": supplied.name, "error_type": type(exc).__name__},
        ) from exc
    require(
        resolved.is_file(),
        "AD4_EVIDENCE_FILE_INVALID",
        "An evidence path is not a regular file.",
        details={"file_name": supplied.name},
    )
    try:
        evidence_bytes = resolved.read_bytes()
    except OSError as exc:
        raise AcceptanceError(
            "AD4_EVIDENCE_FILE_READ_FAILED",
            "An evidence JSON file could not be read.",
            details={"file_name": supplied.name, "error_type": type(exc).__name__},
        ) from exc
    size_bytes = len(evidence_bytes)
    require(
        0 < size_bytes <= MAX_EVIDENCE_BYTES,
        "AD4_EVIDENCE_FILE_SIZE_INVALID",
        "An evidence JSON file is empty or exceeds the bundle size limit.",
        details={"file_name": supplied.name, "size_bytes": size_bytes},
    )
    try:
        payload = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(
            "AD4_EVIDENCE_JSON_INVALID",
            "An evidence JSON file cannot be parsed.",
            details={"file_name": supplied.name, "error_type": type(exc).__name__},
        ) from exc
    require(
        isinstance(payload, dict),
        "AD4_EVIDENCE_JSON_INVALID",
        "An evidence JSON root must be an object.",
        details={"file_name": supplied.name},
    )
    return payload, {
        "file_name": supplied.name,
        "size_bytes": size_bytes,
        "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
    }


def _validate_gate_shape(
    payload: Mapping[str, Any],
    *,
    verifier_id: str,
    expected_steps: Sequence[str],
) -> None:
    require(
        payload.get("schema_version") in {1, 2},
        "AD4_EVIDENCE_SCHEMA_INVALID",
        "A gate evidence file has an unsupported schema.",
        details={"verifier_id": verifier_id},
    )
    require(
        payload.get("verifier_id") == verifier_id,
        "AD4_EVIDENCE_VERIFIER_ID_MISMATCH",
        "A gate evidence file changed verifier identity during validation.",
        details={"verifier_id": verifier_id},
    )
    require(
        payload.get("ok") is True,
        "AD4_EVIDENCE_GATE_NOT_OK",
        "A required AD4 gate did not pass.",
        details={"verifier_id": verifier_id},
    )
    steps = payload.get("steps") if isinstance(payload.get("steps"), Mapping) else {}
    require(
        set(steps) == set(expected_steps)
        and all(steps.get(step) == "passed" for step in expected_steps),
        "AD4_EVIDENCE_STEPS_INVALID",
        "A required AD4 gate is missing a passed verification step.",
        details={
            "verifier_id": verifier_id,
            "expected_steps": list(expected_steps),
            "actual_steps": sorted(str(value) for value in steps),
        },
    )


def _validate_source_binding(
    payload: Mapping[str, Any],
    *,
    verifier_id: str,
    verifier_path: Path,
    manifest_path: Path,
) -> str:
    schema_version = payload.get("schema_version")
    provenance = payload.get("provenance")
    if schema_version == 1:
        require(
            provenance is None,
            "AD4_EVIDENCE_V1_PROVENANCE_FORBIDDEN",
            "Schema-v1 evidence predates source binding and cannot carry provenance.",
            details={"verifier_id": verifier_id},
        )
        return "legacy_unbound"
    require(
        schema_version == 2,
        "AD4_EVIDENCE_SCHEMA_INVALID",
        "Only schema-v1 legacy or schema-v2 source-bound gate evidence is supported.",
        details={"verifier_id": verifier_id},
    )
    require(
        isinstance(provenance, Mapping),
        "AD4_EVIDENCE_V2_PROVENANCE_REQUIRED",
        "Schema-v2 gate evidence requires a complete provenance object.",
        details={"verifier_id": verifier_id},
    )
    actual_payload_identity = canonical_evidence_payload_identity(payload)
    require(
        provenance.get("evidence_payload") == actual_payload_identity,
        "AD4_EVIDENCE_PAYLOAD_IDENTITY_MISMATCH",
        "Gate evidence changed after its provenance payload hash was created.",
        details={"verifier_id": verifier_id},
    )
    generated_at = str(provenance.get("generated_at_utc") or "")
    try:
        parsed_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AcceptanceError(
            "AD4_EVIDENCE_SOURCE_PROVENANCE_INVALID",
            "Gate provenance has an invalid UTC timestamp.",
            details={"verifier_id": verifier_id},
        ) from exc
    require(
        generated_at.endswith("Z") and parsed_time.tzinfo is not None,
        "AD4_EVIDENCE_SOURCE_PROVENANCE_INVALID",
        "Gate provenance timestamp is not explicit UTC.",
        details={"verifier_id": verifier_id},
    )
    current_context = current_source_context(
        repo_root=ROOT,
        verifier_path=verifier_path,
        fixture_manifest_paths=[manifest_path],
    )
    current = {
        "schema_version": 1,
        "binding_status": "source_bound",
        "verifier_id": verifier_id,
        "evidence_payload": actual_payload_identity,
        **current_context,
    }
    comparable_keys = {
        "schema_version",
        "binding_status",
        "verifier_id",
        "evidence_payload",
        "python",
        "platform",
        "git",
        "source_files",
        "verification_source_tree",
        "backend_validation_tree",
    }
    mismatched = sorted(
        key
        for key in comparable_keys
        if provenance.get(key) != current.get(key)
    )
    execution_guard = (
        provenance.get("execution_source_guard")
        if isinstance(provenance.get("execution_source_guard"), Mapping)
        else {}
    )
    start_imports = (
        execution_guard.get("start_import_origins")
        if isinstance(execution_guard.get("start_import_origins"), Mapping)
        else {}
    )
    end_imports = (
        execution_guard.get("end_import_origins")
        if isinstance(execution_guard.get("end_import_origins"), Mapping)
        else {}
    )
    expected_import_roots = [
        {"module": "adapters", "path": "backend/adapters"},
        {"module": "dockstart_core", "path": "backend/dockstart_core"},
    ]
    provenance_context = {
        key: provenance.get(key)
        for key in (
            "python",
            "platform",
            "git",
            "source_files",
            "verification_source_tree",
            "backend_validation_tree",
        )
    }
    expected_source_fingerprint = source_context_fingerprint(provenance_context)

    def valid_import_origins(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        modules = value.get("modules")
        if not isinstance(modules, list) or not modules:
            return False
        canonical_modules: list[dict[str, Any]] = []
        names: set[str] = set()
        for record in modules:
            if not isinstance(record, Mapping):
                return False
            name = str(record.get("module") or "")
            path = str(record.get("path") or "")
            identity = _identity_key(record)
            matching_root = next(
                (
                    root
                    for root in expected_import_roots
                    if name == root["module"]
                    or name.startswith(f"{root['module']}.")
                ),
                None,
            )
            if identity is None or not name or name in names or matching_root is None:
                return False
            suffix = name[len(str(matching_root["module"])) :].lstrip(".")
            module_relative = suffix.replace(".", "/")
            root_path = str(matching_root["path"])
            expected_paths = {
                f"{root_path}/__init__.py"
                if not module_relative
                else f"{root_path}/{module_relative}.py",
                f"{root_path}/{module_relative}/__init__.py"
                if module_relative
                else f"{root_path}/__init__.py",
            }
            if path not in expected_paths:
                return False
            try:
                candidate = ROOT / path
                if candidate.is_symlink() or not candidate.is_file():
                    return False
                resolved = candidate.resolve(strict=True)
                if resolved.relative_to(ROOT.resolve(strict=True)).as_posix() != path:
                    return False
                if (
                    int(resolved.stat().st_size) != identity[0]
                    or hashlib.sha256(resolved.read_bytes()).hexdigest() != identity[1]
                ):
                    return False
            except (OSError, ValueError):
                return False
            names.add(name)
            canonical_modules.append(
                {
                    "module": name,
                    "path": path,
                    "size_bytes": identity[0],
                    "sha256": identity[1],
                }
            )
        canonical_modules.sort(key=lambda record: str(record["module"]))
        encoded = json.dumps(
            canonical_modules,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return (
            value.get("status") == "verified"
            and value.get("algorithm") == IMPORT_ORIGIN_ALGORITHM
            and value.get("roots") == expected_import_roots
            and value.get("module_count") == len(canonical_modules)
            and str(value.get("sha256") or "").lower()
            == hashlib.sha256(encoded).hexdigest()
            and list(modules) == canonical_modules
        )

    expected_provenance_keys = {
        *set(current),
        "generated_at_utc",
        "execution_source_guard",
    }
    pre_import_time = str(execution_guard.get("pre_import_captured_at_utc") or "")
    imports_verified_time = str(execution_guard.get("imports_verified_at_utc") or "")
    end_time = str(execution_guard.get("end_captured_at_utc") or "")
    try:
        parsed_pre_import = datetime.fromisoformat(pre_import_time.replace("Z", "+00:00"))
        parsed_imports = datetime.fromisoformat(imports_verified_time.replace("Z", "+00:00"))
        parsed_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError:
        parsed_pre_import = parsed_imports = parsed_end = None
    explicit_utc_times = all(
        isinstance(text_value, str)
        and text_value.endswith("Z")
        and parsed_value is not None
        and parsed_value.tzinfo is not None
        and parsed_value.utcoffset() is not None
        and parsed_value.utcoffset().total_seconds() == 0
        for text_value, parsed_value in (
            (pre_import_time, parsed_pre_import),
            (imports_verified_time, parsed_imports),
            (end_time, parsed_end),
        )
    )
    require(
        set(provenance) == expected_provenance_keys
        and not mismatched
        and execution_guard.get("unchanged") is True
        and execution_guard.get("algorithm")
        == expected_source_fingerprint["algorithm"]
        and execution_guard.get("pre_import_sha256")
        == expected_source_fingerprint["sha256"]
        and execution_guard.get("imports_verified_sha256")
        == expected_source_fingerprint["sha256"]
        and execution_guard.get("end_sha256")
        == expected_source_fingerprint["sha256"]
        and explicit_utc_times
        and parsed_pre_import <= parsed_imports <= parsed_end
        and provenance.get("generated_at_utc") == end_time
        and valid_import_origins(start_imports)
        and start_imports == end_imports,
        "AD4_EVIDENCE_SOURCE_IDENTITY_MISMATCH",
        "Gate provenance does not match the current verifier source identity.",
        details={"verifier_id": verifier_id, "mismatched_fields": mismatched},
    )
    return "source_bound"


def verify_bundle(
    evidence_paths: Sequence[Path],
    *,
    require_source_bound: bool = False,
) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    try:
        loaded = [_read_evidence(Path(path)) for path in evidence_paths]
        by_id: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for payload, identity in loaded:
            gate_id = str(payload.get("verifier_id") or "")
            by_id.setdefault(gate_id, []).append((payload, identity))

        unknown = sorted(value for value in by_id if value not in GATE_CONTRACTS)
        require(
            not unknown,
            "AD4_EVIDENCE_UNKNOWN_VERIFIER",
            "The bundle contains an unknown verifier identity.",
            details={"verifier_ids": unknown},
        )
        duplicates = sorted(value for value, records in by_id.items() if len(records) > 1)
        require(
            not duplicates,
            "AD4_EVIDENCE_DUPLICATE_VERIFIER",
            "The bundle contains duplicate evidence for one AD4 gate.",
            details={"verifier_ids": duplicates},
        )
        missing = sorted(value for value in GATE_ORDER if value not in by_id)
        require(
            not missing and len(loaded) == len(GATE_ORDER),
            "AD4_EVIDENCE_MISSING_VERIFIER",
            "The bundle must contain exactly one result for each of the three AD4 gates.",
            details={"missing_verifier_ids": missing, "evidence_count": len(loaded)},
        )
        steps["exact_gate_set"] = "passed"

        gate_summaries: list[dict[str, Any]] = []
        binding_statuses: list[str] = []
        for gate_id in GATE_ORDER:
            payload, evidence_identity = by_id[gate_id][0]
            contract = GATE_CONTRACTS[gate_id]
            _validate_gate_shape(
                payload,
                verifier_id=gate_id,
                expected_steps=contract["steps"],
            )
            binding_status = _validate_source_binding(
                payload,
                verifier_id=gate_id,
                verifier_path=contract["verifier_path"],
                manifest_path=contract["manifest_path"],
            )
            oracle: Callable[[Mapping[str, Any]], dict[str, Any]] = contract["oracle"]
            oracle_summary = oracle(payload)
            binding_statuses.append(binding_status)
            gate_summaries.append(
                {
                    "verifier_id": gate_id,
                    "gate": contract["name"],
                    "status": "passed",
                    "binding_status": binding_status,
                    "evidence": evidence_identity,
                    "oracle": oracle_summary,
                }
            )
        steps["gate_schema_steps_and_oracles"] = "passed"

        if all(value == "source_bound" for value in binding_statuses):
            bundle_binding = "source_bound"
        elif all(value == "legacy_unbound" for value in binding_statuses):
            bundle_binding = "legacy_unbound"
        else:
            bundle_binding = "mixed"
        require(
            not require_source_bound or bundle_binding == "source_bound",
            "AD4_EVIDENCE_SOURCE_BINDING_REQUIRED",
            "The bundle contains legacy or mixed evidence and cannot satisfy source-bound mode.",
            details={
                "bundle_binding_status": bundle_binding,
                "legacy_verifier_ids": [
                    gate["verifier_id"]
                    for gate in gate_summaries
                    if gate["binding_status"] != "source_bound"
                ],
            },
        )
        steps["source_binding_policy"] = "passed"
        return _bundle_result(
            {
                "schema_version": 1,
                "verifier_id": VERIFIER_ID,
                "ok": True,
                "require_source_bound": bool(require_source_bound),
                "binding_status": bundle_binding,
                "gate_count": len(gate_summaries),
                "gates": gate_summaries,
                "steps": steps,
            }
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _bundle_result(error_payload(VERIFIER_ID, exc, steps=steps))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate exactly three v0.14.1 AD4 external-gate evidence JSON files."
    )
    parser.add_argument("evidence", type=Path, nargs="*")
    parser.add_argument("--require-source-bound", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.output is not None:
        try:
            resolved_output = arguments.output.expanduser().resolve(strict=False)
            colliding_inputs = [
                path.name
                for path in arguments.evidence
                if path.expanduser().resolve(strict=False) == resolved_output
            ]
        except OSError as exc:
            result = _bundle_result(
                error_payload(
                    VERIFIER_ID,
                    AcceptanceError(
                        "AD4_EVIDENCE_OUTPUT_PATH_INVALID",
                        "The bundle output path could not be resolved safely.",
                        details={"error_type": type(exc).__name__},
                    ),
                )
            )
            return write_result(result, None)
        if colliding_inputs:
            result = _bundle_result(
                error_payload(
                    VERIFIER_ID,
                    AcceptanceError(
                        "AD4_EVIDENCE_OUTPUT_INPUT_COLLISION",
                        "The bundle output path resolves to an evidence input file.",
                        details={"input_file_names": colliding_inputs},
                    ),
                )
            )
            return write_result(result, None)
    result = verify_bundle(
        arguments.evidence,
        require_source_bound=arguments.require_source_bound,
    )
    return write_result(result, arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
