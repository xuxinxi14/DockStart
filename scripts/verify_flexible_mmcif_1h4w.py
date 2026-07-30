"""Run the external 1H4W flexible-receptor project acceptance gate.

The fixture contains metadata only.  The caller supplies the manifest-pinned
1H4W mmCIF and BEN SDF plus real Python/Meeko and AutoDock Vina runtimes.  The
verifier uses DockStart's public project APIs from raw import through report
export.  It never downloads data, fabricates a PDBQT, or accepts substitute
process output.
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

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
from dockstart_core.flexible_receptor import (  # noqa: E402
    get_flexible_receptor_identity_context,
    get_flexible_receptor_status,
    prepare_flexible_receptor,
    validate_flexible_receptor_preparation,
)
from dockstart_core.preparation import prepare_ligand_pdbqt  # noqa: E402
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
    / "flexible_mmcif_1h4w"
    / "source_manifest.json"
)
DEFAULT_PYTHON = REPOSITORY_ROOT / "resources" / "python" / "python.exe"
DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])


class FlexibleMmcifAcceptanceError(RuntimeError):
    """Stable JSON boundary for the external acceptance gate."""

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
    raise FlexibleMmcifAcceptanceError(code, message, details=details)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_lf(payload: bytes, label: str) -> bytes:
    unexpected = _unexpected_control_byte_counts(payload)
    if unexpected:
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            f"{label} contains unsupported control bytes.",
            details={"label": label, "control_byte_counts": unexpected},
        )
    return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _parse_single_sdf_response(path: Path) -> dict[str, Any]:
    """Freeze molecular bytes while auditing ModelServer response metadata.

    RCSB ModelServer appends request-specific job identifiers, timestamps, and
    timing values to otherwise identical SDF molecular bytes.  Raw response
    SHA256 therefore remains useful provenance, but it is not a stable
    molecular identity.  The scientific identity is the normalized first mol
    block through its sole ``M  END`` line.  The response must still contain
    exactly one SDF record and a well-formed, duplicate-free property section.
    """

    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            "The BEN SDF could not be read.",
            details={"path": str(path), "error": str(exc)},
        )
    normalized = _normalized_lf(raw, "BEN SDF")
    try:
        text = normalized.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            "The BEN SDF is not strict UTF-8 text.",
            details={"path": str(path), "error": str(exc)},
        )
    lines = text.splitlines(keepends=True)
    mol_end_indexes = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\n") == "M  END"
    ]
    if len(mol_end_indexes) != 1:
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            "The BEN SDF must contain exactly one mol block terminator.",
            details={"m_end_count": len(mol_end_indexes)},
        )
    record_terminators = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\n") == "$$$$"
    ]
    if len(record_terminators) != 1:
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            "The BEN SDF must contain exactly one complete SDF record.",
            details={"record_terminator_count": len(record_terminators)},
        )
    mol_end_index = mol_end_indexes[0]
    record_end_index = record_terminators[0]
    if record_end_index <= mol_end_index or any(
        line.strip() for line in lines[record_end_index + 1 :]
    ):
        _fail(
            "FLEX_1H4W_SOURCE_INVALID",
            "The BEN SDF has content outside its single declared record.",
        )
    molblock = "".join(lines[: mol_end_index + 1]).encode("utf-8")

    properties: dict[str, str] = {}
    index = mol_end_index + 1
    property_header = re.compile(r"^> <([^<>]+)>$")
    while index < record_end_index:
        line = lines[index].rstrip("\n")
        if not line:
            index += 1
            continue
        match = property_header.fullmatch(line)
        if match is None:
            _fail(
                "FLEX_1H4W_SOURCE_INVALID",
                "The BEN SDF property section is malformed.",
                details={"line_number": index + 1, "line": line},
            )
        name = match.group(1)
        if name in properties:
            _fail(
                "FLEX_1H4W_SOURCE_INVALID",
                "The BEN SDF contains a duplicate property.",
                details={"property": name},
            )
        index += 1
        value_lines: list[str] = []
        while index < record_end_index and lines[index].rstrip("\n"):
            value_lines.append(lines[index].rstrip("\n"))
            index += 1
        value = "\n".join(value_lines)
        if not value:
            _fail(
                "FLEX_1H4W_SOURCE_INVALID",
                "The BEN SDF contains an empty property value.",
                details={"property": name},
            )
        properties[name] = value

    return {
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_size_bytes": len(raw),
        "molblock_sha256": hashlib.sha256(molblock).hexdigest(),
        "molblock_size_bytes": len(molblock),
        "properties": properties,
    }


def _verify_ligand_source_identity(
    ligand_sdf: Path,
    ligand_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = _parse_single_sdf_response(ligand_sdf)
    identity = ligand_manifest.get("content_identity")
    if not isinstance(identity, Mapping):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest BEN molecular content identity is missing.",
        )
    _assert_equal(
        identity.get("algorithm"),
        "normalize_crlf_and_cr_to_lf_then_first_molblock_through_m_end_sha256_v1",
        "BEN content identity algorithm",
    )
    _assert_equal(
        parsed["molblock_sha256"],
        identity.get("sha256"),
        "BEN molecular content SHA256",
    )
    _assert_equal(
        parsed["molblock_size_bytes"],
        identity.get("size_bytes"),
        "BEN molecular content size",
    )

    expected_fixed = ligand_manifest.get("stable_properties")
    allowed_dynamic = ligand_manifest.get("allowed_dynamic_properties")
    if not isinstance(expected_fixed, Mapping) or not isinstance(
        allowed_dynamic, list
    ):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest BEN ModelServer property contract is missing.",
        )
    allowed_dynamic_set = {
        str(value)
        for value in allowed_dynamic
        if isinstance(value, str) and value
    }
    if len(allowed_dynamic_set) != len(allowed_dynamic):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest BEN dynamic property allowlist is invalid.",
        )
    properties = parsed["properties"]
    _assert_equal(
        set(properties),
        set(expected_fixed) | allowed_dynamic_set,
        "BEN ModelServer property names",
    )
    for name, expected_value in expected_fixed.items():
        _assert_equal(
            properties.get(str(name)),
            str(expected_value),
            f"BEN ModelServer property {name}",
        )
    for name in allowed_dynamic_set:
        value = properties.get(name)
        if not isinstance(value, str) or not value.strip():
            _fail(
                "FLEX_1H4W_ORACLE_MISMATCH",
                "A dynamic BEN ModelServer property is empty.",
                details={"property": name},
            )
    return parsed


def _unexpected_control_byte_counts(payload: bytes) -> dict[int, int]:
    counts: dict[int, int] = {}
    for value in payload:
        if value == 0 or (value < 32 and value not in {9, 10, 13}) or value == 127:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _load_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "The 1H4W source manifest is missing or invalid.",
            details={"path": str(MANIFEST_PATH), "error": str(exc)},
        )
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "The 1H4W source manifest must use schema_version 2.",
        )
    return payload


def _regular_file(path_value: str | Path, label: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    try:
        valid = (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size > 0
        )
    except OSError:
        valid = False
    if not valid:
        _fail(
            "FLEX_1H4W_INPUT_INVALID",
            f"{label} is missing, empty, or symbolic.",
            details={"path": str(path)},
        )
    return path


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        _fail(
            "FLEX_1H4W_ORACLE_MISMATCH",
            f"{label} does not match the manifest-pinned oracle.",
            details={"label": label, "expected": expected, "actual": actual},
        )


def _assert_close(actual: Any, expected: Any, label: str) -> None:
    try:
        equal = math.isclose(
            float(actual),
            float(expected),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    except (TypeError, ValueError):
        equal = False
    if not equal:
        _assert_equal(actual, expected, label)


def _require_ok(
    result: Mapping[str, Any],
    code: str,
    message: str,
) -> Mapping[str, Any]:
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        _fail(
            code,
            message,
            details={
                "result": dict(result) if isinstance(result, Mapping) else result,
            },
        )
    return result


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        result = action()
    except FlexibleMmcifAcceptanceError as exc:
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
    steps[name] = {"ok": True, **dict(result)}
    return result


def _project_artifact(
    project_root: Path,
    relative_value: Any,
    label: str,
) -> Path:
    relative = Path(str(relative_value or ""))
    if (
        not str(relative)
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        _fail(
            "FLEX_1H4W_ARTIFACT_PATH_INVALID",
            f"{label} has an unsafe project-relative path.",
            details={"relative_path": str(relative_value or "")},
        )
    path = (project_root / relative).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        _fail(
            "FLEX_1H4W_ARTIFACT_OUTSIDE_PROJECT",
            f"{label} resolves outside the temporary project.",
            details={"path": str(path)},
        )
    return _regular_file(path, label)


def _ligand_preparation_record(result: Mapping[str, Any]) -> Mapping[str, Any]:
    project = result.get("project")
    project = project if isinstance(project, Mapping) else {}
    preparation = project.get("preparation")
    preparation = preparation if isinstance(preparation, Mapping) else {}
    ligand = preparation.get("ligand")
    return ligand if isinstance(ligand, Mapping) else {}


def _movement_artifact_record(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts = metadata.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, Mapping) else {}
    movement = artifacts.get("flexible_movement")
    return movement if isinstance(movement, Mapping) else {}


def _verify_sources(
    mmcif: Path,
    ligand_sdf: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest sources are missing.",
        )
    receptor = sources.get("receptor")
    ligand = sources.get("ligand")
    if not isinstance(receptor, Mapping) or not isinstance(ligand, Mapping):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest receptor or ligand source is invalid.",
        )
    ligand_identity = _verify_ligand_source_identity(ligand_sdf, ligand)
    _assert_equal(_sha256(mmcif), receptor.get("sha256"), "receptor SHA256")
    _assert_equal(mmcif.stat().st_size, receptor.get("size_bytes"), "receptor size")
    return {
        "network_or_download_used": False,
        "receptor": {
            "path": str(mmcif),
            "sha256": _sha256(mmcif),
            "size_bytes": mmcif.stat().st_size,
            "provider": receptor.get("provider"),
            "url": receptor.get("url"),
        },
        "ligand": {
            "path": str(ligand_sdf),
            "raw_response_sha256": ligand_identity["raw_sha256"],
            "raw_response_size_bytes": ligand_identity["raw_size_bytes"],
            "molecular_content_sha256": ligand_identity["molblock_sha256"],
            "molecular_content_size_bytes": ligand_identity[
                "molblock_size_bytes"
            ],
            "provider": ligand.get("provider"),
            "url": ligand.get("url"),
            "entry_id": ligand.get("entry_id"),
            "label_asym_id": ligand.get("label_asym_id"),
            "auth_seq_id": ligand.get("auth_seq_id"),
        },
    }


def _python_probe(python_path: Path) -> dict[str, Any]:
    script = (
        "import importlib.metadata,json,platform;"
        "print(json.dumps({"
        "'python':platform.python_version(),"
        "'meeko':importlib.metadata.version('meeko'),"
        "'rdkit':importlib.metadata.version('rdkit'),"
        "'gemmi':importlib.metadata.version('gemmi')"
        "},sort_keys=True))"
    )
    try:
        completed = subprocess.run(
            [str(python_path), "-I", "-B", "-c", script],
            cwd=str(python_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(
            "FLEX_1H4W_PYTHON_PROBE_FAILED",
            "The selected Python runtime could not be probed.",
            details={"error": str(exc)},
        )
    if completed.returncode != 0:
        _fail(
            "FLEX_1H4W_PYTHON_TOOLCHAIN_INVALID",
            "Python must contain real Meeko, RDKit, and Gemmi packages.",
            details={
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        _fail(
            "FLEX_1H4W_PYTHON_PROBE_INVALID",
            "The Python toolchain probe did not return JSON.",
            details={"stdout": completed.stdout, "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "FLEX_1H4W_PYTHON_PROBE_INVALID",
            "The Python toolchain probe returned an invalid payload.",
        )
    return payload


def _verify_tools(
    python_executable: Path,
    vina_executable: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    python_path = _regular_file(python_executable, "Python executable")
    vina_path = _regular_file(vina_executable, "AutoDock Vina executable")
    expected_tools = expected.get("tools")
    if not isinstance(expected_tools, Mapping):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Manifest tool gates are missing.")

    python_versions = _python_probe(python_path)
    for key in ("python", "meeko", "rdkit", "gemmi"):
        _assert_equal(
            python_versions.get(key),
            expected_tools.get(key),
            f"{key} version",
        )

    detection = vina_adapter.detect(str(vina_path))
    if (
        detection.status != "ok"
        or not detection.path
        or Path(detection.path).resolve() != vina_path
    ):
        _fail(
            "FLEX_1H4W_VINA_INVALID",
            "The selected AutoDock Vina executable was not detected.",
            details=detection.to_dict(),
        )
    _assert_equal(
        detection.version,
        expected_tools.get("vina"),
        "AutoDock Vina version",
    )
    return {
        "python": {
            "path": str(python_path),
            "sha256": _sha256(python_path),
            "size_bytes": python_path.stat().st_size,
            "versions": python_versions,
        },
        "vina": {
            "path": detection.path,
            "version": detection.version,
            "source": detection.source,
            "is_bundled": detection.is_bundled,
            "sha256": _sha256(vina_path),
            "size_bytes": vina_path.stat().st_size,
        },
    }


def _create_import_and_prepare_ligand(
    work_root: Path,
    mmcif: Path,
    ligand_sdf: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    created = _require_ok(
        create_project("flexible_mmcif_1h4w_acceptance", str(work_root)),
        "FLEX_1H4W_PROJECT_CREATE_FAILED",
        "DockStart could not create the temporary project.",
    )
    project_root = Path(str(created.get("project_dir") or "")).resolve()
    try:
        project_root.relative_to(work_root.resolve())
    except ValueError:
        _fail(
            "FLEX_1H4W_PROJECT_OUTSIDE_TEMP",
            "DockStart created the project outside its temporary root.",
            details={"project_dir": str(project_root)},
        )

    receptor_import = _require_ok(
        import_receptor_raw_file(str(project_root), str(mmcif)),
        "FLEX_1H4W_RECEPTOR_IMPORT_FAILED",
        "DockStart could not import the raw 1H4W mmCIF.",
    )
    ligand_import = _require_ok(
        import_ligand_raw_file(str(project_root), str(ligand_sdf)),
        "FLEX_1H4W_LIGAND_IMPORT_FAILED",
        "DockStart could not import the raw BEN SDF.",
    )
    prepared = _require_ok(
        prepare_ligand_pdbqt(str(project_root)),
        "FLEX_1H4W_LIGAND_PREPARATION_FAILED",
        "DockStart could not prepare BEN through its real RDKit/Meeko workflow.",
    )
    ligand_expected = expected.get("ligand_preparation")
    if not isinstance(ligand_expected, Mapping):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest ligand preparation oracle is missing.",
        )
    preparation_record = _ligand_preparation_record(prepared)
    output_relative = str(prepared.get("output_file") or "")
    output = _project_artifact(project_root, output_relative, "prepared BEN PDBQT")
    _assert_equal(_sha256(output), ligand_expected.get("sha256"), "prepared BEN SHA256")
    _assert_equal(
        preparation_record.get("method"),
        ligand_expected.get("method"),
        "ligand method",
    )
    _assert_equal(
        preparation_record.get("status"),
        "finished",
        "ligand preparation status",
    )
    return {
        "project_dir": str(project_root),
        "receptor_raw_file": receptor_import.get("raw_file"),
        "ligand_raw_file": ligand_import.get("raw_file"),
        "ligand_preparation": {
            "prep_id": preparation_record.get("prep_id"),
            "method": preparation_record.get("method"),
            "output_file": output_relative,
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
            "python_path": preparation_record.get("python_path"),
            "rdkit_available": preparation_record.get("rdkit_available"),
            "meeko_available": preparation_record.get("meeko_available"),
            "exit_code": preparation_record.get("exit_code"),
        },
    }


def _residue_index(context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    polymer = context.get("residues")
    nonpolymer = context.get("nonpolymer_residues")
    if not isinstance(polymer, list) or not isinstance(nonpolymer, list):
        _fail(
            "FLEX_1H4W_IDENTITY_CONTEXT_INVALID",
            "The project identity context lacks polymer or nonpolymer residues.",
        )
    return {
        str(item.get("selector") or ""): item
        for item in [*polymer, *nonpolymer]
        if isinstance(item, Mapping)
    }


def _verify_identity_and_prepare_flexible(
    project_root: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    identity_expected = expected.get("identity")
    preparation_expected = expected.get("flexible_preparation")
    controls = expected.get("receptor_controls")
    if not all(
        isinstance(item, Mapping)
        for item in (identity_expected, preparation_expected, controls)
    ):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Manifest identity, controls, or flexible preparation oracle is invalid.",
        )
    assert isinstance(identity_expected, Mapping)
    assert isinstance(preparation_expected, Mapping)
    assert isinstance(controls, Mapping)

    context = _require_ok(
        get_flexible_receptor_identity_context(str(project_root)),
        "FLEX_1H4W_IDENTITY_FAILED",
        "DockStart could not build the 1H4W schema-v2 identity context.",
    )
    for key in (
        "source_sha256",
        "identity_contract_sha256",
        "coordinate_identity_sha256",
        "polymer_residue_count",
        "nonpolymer_residue_count",
        "coordinate_atom_count",
        "bridge_sha256",
        "bridge_verification_sha256",
    ):
        _assert_equal(context.get(key), identity_expected.get(key), f"identity {key}")
    _assert_equal(context.get("model"), identity_expected.get("model"), "identity model")

    indexed = _residue_index(context)
    global_altlocs = controls.get("alternate_locations")
    templates = controls.get("template_assignments")
    deletions = controls.get("deleted_residues")
    selections = preparation_expected.get("selections")
    if (
        not isinstance(global_altlocs, Mapping)
        or not isinstance(templates, Mapping)
        or not isinstance(deletions, list)
        or not isinstance(selections, list)
    ):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Manifest receptor controls are invalid.")
    observed_altloc_selectors = sorted(
        selector
        for selector, residue in indexed.items()
        if list(
            (
                residue.get("alternate_locations")
                if isinstance(residue.get("alternate_locations"), Mapping)
                else {}
            ).get("ids") or []
        )
    )
    _assert_equal(
        observed_altloc_selectors,
        sorted(global_altlocs),
        "global altloc selector coverage",
    )
    for selector, template in templates.items():
        residue = indexed.get(str(selector))
        _assert_equal(
            (
                residue.get("author", {}).get("component_id")
                if isinstance(residue, Mapping)
                and isinstance(residue.get("author"), Mapping)
                else None
            ),
            "CYS",
            f"{selector} source component",
        )
        _assert_equal(template, "CYX", f"{selector} assigned template")
    deletion = deletions[0] if deletions else {}
    ben_selector = str(deletion.get("selector") or "")
    ben = indexed.get(ben_selector)
    _assert_equal(ben.get("record_type") if ben else None, "HETATM", "BEN record type")
    _assert_equal(
        ben.get("author", {}).get("component_id")
        if ben and isinstance(ben.get("author"), Mapping)
        else None,
        deletion.get("expected_component_id"),
        "BEN author component",
    )
    _assert_equal(
        ben.get("label", {}).get("chain_id")
        if ben and isinstance(ben.get("label"), Mapping)
        else None,
        "B",
        "BEN label asym id",
    )

    validation = _require_ok(
        validate_flexible_receptor_preparation(
            str(project_root),
            [str(value) for value in selections],
            receptor_controls={
                "schema_version": int(controls.get("schema_version") or 0),
                "allow_bad_res": False,
                "alternate_locations": dict(global_altlocs),
                "template_assignments": dict(templates),
                "deleted_residues": [dict(item) for item in deletions],
            },
            expected_selection_context_sha256=str(
                context.get("selection_context_sha256") or ""
            ),
            require_selection_context=True,
        ),
        "FLEX_1H4W_FLEX_VALIDATION_FAILED",
        "DockStart rejected the manifest-pinned global controls or selections.",
    )
    contract = validation.get("selection_contract")
    if not isinstance(contract, Mapping):
        _fail(
            "FLEX_1H4W_SELECTION_CONTRACT_MISSING",
            "Flexible validation returned no selection contract.",
        )
    _assert_equal(
        contract.get("selection_sha256"),
        preparation_expected.get("selection_sha256"),
        "selection SHA256",
    )
    _assert_equal(
        validation.get("identity_preparation_controls_sha256"),
        controls.get("control_sha256"),
        "identity preparation controls SHA256",
    )

    prepared = _require_ok(
        prepare_flexible_receptor(
            str(project_root),
            [str(value) for value in selections],
            receptor_controls={
                "schema_version": int(controls.get("schema_version") or 0),
                "allow_bad_res": False,
                "alternate_locations": dict(global_altlocs),
                "template_assignments": dict(templates),
                "deleted_residues": [dict(item) for item in deletions],
            },
            expected_selection_context_sha256=str(
                context.get("selection_context_sha256") or ""
            ),
        ),
        "FLEX_1H4W_FLEX_PREPARATION_FAILED",
        "DockStart could not prepare the real 1H4W rigid/flex/JSON output set.",
    )
    partition = prepared.get("atom_partition")
    if not isinstance(partition, Mapping):
        _fail(
            "FLEX_1H4W_PARTITION_MISSING",
            "Flexible preparation returned no atom partition.",
        )
    partition_expected = preparation_expected.get("atom_partition")
    if not isinstance(partition_expected, Mapping):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Atom partition oracle is invalid.")
    for key, value in partition_expected.items():
        _assert_equal(partition.get(key), value, f"atom partition {key}")
    output_hashes = prepared.get("sha256")
    expected_hashes = preparation_expected.get("output_sha256")
    if not isinstance(output_hashes, Mapping) or not isinstance(expected_hashes, Mapping):
        _fail("FLEX_1H4W_OUTPUT_IDENTITY_MISSING", "Flexible output hashes are missing.")
    _assert_equal(dict(output_hashes), dict(expected_hashes), "flexible output SHA256")

    identity = prepared.get("identity")
    if not isinstance(identity, Mapping):
        _fail("FLEX_1H4W_FROZEN_IDENTITY_MISSING", "No frozen mmCIF identity was recorded.")
    for key in (
        "identity_contract_sha256",
        "coordinate_identity_sha256",
        "selection_sha256",
        "preparation_controls_sha256",
        "bridge_sha256",
        "bridge_verification_sha256",
    ):
        source = (
            preparation_expected
            if key == "selection_sha256"
            else controls
            if key == "preparation_controls_sha256"
            else identity_expected
        )
        expected_key = "control_sha256" if key == "preparation_controls_sha256" else key
        _assert_equal(identity.get(key), source.get(expected_key), f"frozen {key}")

    status = _require_ok(
        get_flexible_receptor_status(str(project_root)),
        "FLEX_1H4W_FLEX_STATUS_FAILED",
        "DockStart could not reload flexible receptor status.",
    )
    _assert_equal(status.get("mode"), "flexible", "active receptor mode")
    _assert_equal(status.get("flexible_ready"), True, "flexible readiness")
    return {
        "identity": {
            key: context.get(key)
            for key in (
                "source_sha256",
                "identity_contract_sha256",
                "coordinate_identity_sha256",
                "polymer_residue_count",
                "nonpolymer_residue_count",
                "coordinate_atom_count",
                "bridge_sha256",
                "bridge_verification_sha256",
            )
        },
        "selection_sha256": contract.get("selection_sha256"),
        "preparation_controls_sha256": validation.get(
            "identity_preparation_controls_sha256"
        ),
        "preparation_id": prepared.get("preparation_id"),
        "outputs": dict(prepared.get("outputs") or {}),
        "output_sha256": dict(output_hashes),
        "atom_partition": dict(partition),
        "active_mode": status.get("mode"),
    }


def _configure_project(
    project_root: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    box = expected.get("box")
    vina = expected.get("vina")
    if not isinstance(box, Mapping) or not isinstance(vina, Mapping):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Box or Vina oracle is missing.")
    _require_ok(
        update_box_params(str(project_root), dict(box)),
        "FLEX_1H4W_BOX_UPDATE_FAILED",
        "DockStart could not save the manifest-pinned box.",
    )
    _require_ok(
        update_vina_params(str(project_root), dict(vina)),
        "FLEX_1H4W_VINA_UPDATE_FAILED",
        "DockStart could not save the manifest-pinned Vina parameters.",
    )
    generated = _require_ok(
        generate_vina_config(str(project_root)),
        "FLEX_1H4W_CONFIG_FAILED",
        "DockStart could not generate vina_config.txt.",
    )
    config = _project_artifact(
        project_root,
        generated.get("config_file"),
        "generated Vina config",
    )
    return {
        "box": dict(box),
        "vina": dict(vina),
        "config_file": generated.get("config_file"),
        "config_sha256": _sha256(config),
    }


def _verify_movement_contract(
    project_root: Path,
    analyzed: Mapping[str, Any],
    metadata: Mapping[str, Any],
    score_count: int,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the project-level movement fields frozen by result analysis."""

    movement = analyzed.get("flexible_movement")
    movement_file_value = analyzed.get("flexible_movement_file")
    if not isinstance(movement, Mapping):
        _fail(
            "FLEX_1H4W_MOVEMENT_MISSING",
            "Project result analysis returned no flexible_movement object.",
            details={
                "required_fields": [
                    "flexible_movement",
                    "flexible_movement_file",
                    "metadata.artifacts.flexible_movement",
                ],
            },
        )
    artifact = _movement_artifact_record(metadata)
    movement_sha = artifact.get("sha256")
    movement_file = _project_artifact(
        project_root,
        movement_file_value,
        "flexible movement JSON",
    )
    try:
        persisted = json.loads(movement_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "FLEX_1H4W_MOVEMENT_ARTIFACT_INVALID",
            "The flexible movement JSON cannot be parsed.",
            details={"error": str(exc)},
        )
    _assert_equal(persisted, dict(movement), "persisted movement object")
    _assert_equal(
        _canonical_sha256(movement),
        movement_sha,
        "flexible movement canonical SHA256",
    )
    _assert_equal(
        metadata.get("flexible_movement_file"),
        movement_file_value,
        "metadata movement file",
    )
    _assert_equal(
        artifact.get("relative_path"),
        movement_file_value,
        "movement artifact relative path",
    )
    _assert_equal(
        artifact.get("size_bytes"),
        movement_file.stat().st_size,
        "movement artifact size",
    )
    _assert_equal(movement.get("ok"), True, "movement status")
    _assert_equal(
        movement.get("schema_id"),
        expected.get("schema_id"),
        "movement schema id",
    )
    _assert_equal(movement.get("mode_count"), score_count, "movement mode count")
    input_contract = movement.get("input_contract")
    if not isinstance(input_contract, Mapping):
        _fail(
            "FLEX_1H4W_MOVEMENT_CONTRACT_INVALID",
            "Movement input_contract is missing.",
        )
    _assert_equal(
        input_contract.get("flexible_residue_count"),
        expected.get("flexible_residue_count"),
        "movement flexible residue count",
    )
    modes = movement.get("modes")
    if not isinstance(modes, list) or len(modes) != score_count:
        _fail(
            "FLEX_1H4W_MOVEMENT_MODE_SET_INVALID",
            "Movement modes do not match the score table.",
        )
    expected_residues = sorted(str(item) for item in expected.get("residues") or [])
    for mode in modes:
        residues = mode.get("flexible_residues") if isinstance(mode, Mapping) else None
        if not isinstance(residues, list):
            _fail(
                "FLEX_1H4W_MOVEMENT_MODE_INVALID",
                "A movement mode has no flexible residue metrics.",
            )
        observed = sorted(
            str(
                (
                    item.get("residue")
                    if isinstance(item, Mapping)
                    and isinstance(item.get("residue"), Mapping)
                    else {}
                ).get("canonical_id")
                or ""
            )
            for item in residues
        )
        _assert_equal(observed, expected_residues, "movement residue identities")
    return {
        "schema_id": movement.get("schema_id"),
        "mode_count": movement.get("mode_count"),
        "flexible_residue_count": input_contract.get("flexible_residue_count"),
        "file": movement_file_value,
        "sha256": movement_sha,
    }


def _verify_output_normalization(
    project_root: Path,
    metadata: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    record = metadata.get("output_normalization")
    if not isinstance(record, Mapping):
        _fail(
            "FLEX_1H4W_OUTPUT_NORMALIZATION_MISSING",
            "The finished run contains no output-normalization record.",
        )
    status = str(record.get("status") or "")
    if status not in {"normalized", "not_required"}:
        _fail(
            "FLEX_1H4W_OUTPUT_NORMALIZATION_INVALID",
            "The Vina output was neither clean nor safely normalized.",
            details={"record": dict(record)},
        )
    output = _project_artifact(
        project_root,
        record.get("normalized_file"),
        "normalized Vina output",
    )
    published = output.read_bytes()
    _assert_equal(
        _unexpected_control_byte_counts(published),
        {},
        "published Vina control bytes",
    )
    _assert_equal(
        _sha256(output),
        record.get("normalized_sha256"),
        "normalized Vina SHA256",
    )
    _assert_equal(
        output.stat().st_size,
        record.get("normalized_size_bytes"),
        "normalized Vina size",
    )
    if status == "not_required":
        _assert_equal(record.get("nul_bytes_detected"), 0, "clean Vina NUL count")
        return {
            "status": status,
            "method": record.get("method"),
            "normalized_file": record.get("normalized_file"),
            "normalized_sha256": record.get("normalized_sha256"),
            "nul_bytes_detected": 0,
            "nul_bytes_removed": 0,
        }

    _assert_equal(record.get("method"), expected.get("method"), "normalization method")
    _assert_equal(
        record.get("recognized_padding_blocks"),
        expected.get("recognized_padding_blocks"),
        "recognized flexible padding blocks",
    )
    _assert_equal(
        record.get("recognized_padding_lengths"),
        expected.get("recognized_padding_lengths"),
        "recognized flexible padding lengths",
    )
    _assert_equal(
        record.get("nul_bytes_detected"),
        expected.get("nul_bytes_removed"),
        "raw Vina NUL count",
    )
    _assert_equal(
        record.get("nul_bytes_removed"),
        expected.get("nul_bytes_removed"),
        "removed Vina NUL count",
    )
    raw = _project_artifact(
        project_root,
        record.get("raw_output_file"),
        "preserved raw Vina output",
    )
    raw_bytes = raw.read_bytes()
    _assert_equal(_sha256(raw), record.get("source_sha256"), "raw Vina SHA256")
    _assert_equal(raw.stat().st_size, record.get("source_size_bytes"), "raw Vina size")
    _assert_equal(
        _unexpected_control_byte_counts(raw_bytes),
        {0: int(expected.get("nul_bytes_removed") or 0)},
        "raw Vina control-byte profile",
    )
    return {
        "status": status,
        "method": record.get("method"),
        "raw_output_file": record.get("raw_output_file"),
        "raw_sha256": record.get("source_sha256"),
        "normalized_file": record.get("normalized_file"),
        "normalized_sha256": record.get("normalized_sha256"),
        "recognized_padding_blocks": record.get("recognized_padding_blocks"),
        "recognized_padding_lengths": record.get("recognized_padding_lengths"),
        "nul_bytes_removed": record.get("nul_bytes_removed"),
    }


def _run_analyze_and_report(
    project_root: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    prepared = _require_ok(
        prepare_vina_run(str(project_root)),
        "FLEX_1H4W_RUN_PREPARATION_FAILED",
        "DockStart could not freeze a flexible Vina run.",
    )
    run_id = str(prepared.get("run_id") or "")
    executed = _require_ok(
        execute_prepared_vina_run(str(project_root), run_id),
        "FLEX_1H4W_RUN_FAILED",
        "The real AutoDock Vina flexible run did not finish.",
    )
    executed_metadata = executed.get("metadata")
    if not isinstance(executed_metadata, Mapping):
        _fail("FLEX_1H4W_RUN_METADATA_MISSING", "The Vina run returned no metadata.")
    _assert_equal(executed_metadata.get("status"), "finished", "run status")
    docking_protocol = (
        executed_metadata.get("docking_protocol")
        if isinstance(executed_metadata.get("docking_protocol"), Mapping)
        else {}
    )
    _assert_equal(
        docking_protocol.get("receptor_mode") or docking_protocol.get("mode"),
        "flexible",
        "run receptor mode",
    )

    analyzed = _require_ok(
        analyze_vina_run_results(str(project_root), run_id),
        "FLEX_1H4W_ANALYSIS_FAILED",
        "DockStart could not analyze the real flexible Vina output.",
    )
    scores = analyzed.get("scores")
    if not isinstance(scores, list) or not scores:
        _fail("FLEX_1H4W_SCORES_MISSING", "The finished run contains no score rows.")
    run_expected = expected.get("run")
    movement_expected = expected.get("movement")
    if not isinstance(run_expected, Mapping) or not isinstance(movement_expected, Mapping):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Run or movement oracle is missing.")
    normalization_expected = run_expected.get("output_normalization")
    if not isinstance(normalization_expected, Mapping):
        _fail(
            "FLEX_1H4W_MANIFEST_INVALID",
            "Output-normalization oracle is missing.",
        )
    normalization = _verify_output_normalization(
        project_root,
        executed_metadata,
        normalization_expected,
    )
    minimum_modes = int(run_expected.get("minimum_mode_count") or 0)
    if len(scores) < minimum_modes:
        _fail(
            "FLEX_1H4W_MODE_COUNT_LOW",
            "Vina returned fewer modes than the acceptance minimum.",
            details={"minimum": minimum_modes, "actual": len(scores)},
        )
    affinities = [float(row["affinity_kcal_mol"]) for row in scores]
    if (
        not all(math.isfinite(value) for value in affinities)
        or affinities != sorted(affinities)
    ):
        _fail(
            "FLEX_1H4W_SCORE_TABLE_INVALID",
            "The parsed affinity table is non-finite or not sorted.",
        )
    metadata = analyzed.get("metadata")
    if not isinstance(metadata, Mapping):
        _fail("FLEX_1H4W_ANALYSIS_METADATA_MISSING", "Analysis returned no metadata.")
    movement = _verify_movement_contract(
        project_root,
        analyzed,
        metadata,
        len(scores),
        movement_expected,
    )

    exported = _require_ok(
        export_markdown_report(str(project_root), run_id),
        "FLEX_1H4W_REPORT_FAILED",
        "DockStart could not export the flexible docking Markdown report.",
    )
    report = _project_artifact(
        project_root,
        exported.get("report_file"),
        "run Markdown report",
    )
    report_text = report.read_text(encoding="utf-8")
    missing_phrases = [
        str(phrase)
        for phrase in run_expected.get("report_required_phrases") or []
        if str(phrase) not in report_text
    ]
    if missing_phrases:
        _fail(
            "FLEX_1H4W_REPORT_CONTENT_MISSING",
            "The report lacks required flexible-docking evidence.",
            details={"missing_phrases": missing_phrases},
        )
    output = _project_artifact(
        project_root,
        metadata.get("output_file"),
        "Vina out.pdbqt",
    )
    scores_file = _project_artifact(
        project_root,
        analyzed.get("scores_file"),
        "run scores.csv",
    )
    return {
        "run_id": run_id,
        "status": metadata.get("status"),
        "mode_count": len(scores),
        "best_affinity": affinities[0],
        "output": {
            "file": metadata.get("output_file"),
            "sha256": _sha256(output),
            "size_bytes": output.stat().st_size,
        },
        "scores": {
            "file": analyzed.get("scores_file"),
            "sha256": _sha256(scores_file),
            "size_bytes": scores_file.stat().st_size,
        },
        "output_normalization": normalization,
        "movement": movement,
        "report": {
            "file": exported.get("report_file"),
            "sha256": _sha256(report),
            "size_bytes": report.stat().st_size,
        },
    }


def verify_flexible_mmcif_1h4w(
    mmcif_path: str | Path,
    ligand_sdf_path: str | Path,
    *,
    python_executable: str | Path = DEFAULT_PYTHON,
    vina_executable: str | Path = DEFAULT_VINA,
) -> dict[str, Any]:
    """Execute the complete external 1H4W public-project-API acceptance chain."""

    manifest = _load_manifest()
    expected = manifest.get("expected")
    if not isinstance(expected, Mapping):
        _fail("FLEX_1H4W_MANIFEST_INVALID", "Manifest expected section is missing.")
    mmcif = _regular_file(mmcif_path, "1H4W mmCIF")
    ligand_sdf = _regular_file(ligand_sdf_path, "1H4W BEN SDF")
    python_path = _regular_file(python_executable, "Python executable")
    vina_path = _regular_file(vina_executable, "AutoDock Vina executable")

    steps: dict[str, Any] = {}
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="DockStart_flexible_mmcif_1h4w_project_",
    )
    work_root = Path(temporary_handle.name)
    settings_path = work_root / "dockstart_settings.json"
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    previous_resource_dir = os.environ.get(RESOURCE_DIR_ENV_VAR)
    isolated_resource_dir = work_root / "no_bundled_toolchain"
    result_payload: dict[str, Any] | None = None
    caught: FlexibleMmcifAcceptanceError | None = None
    try:
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(isolated_resource_dir)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    python=str(python_path),
                    vina=str(vina_path),
                ),
            )
        )
        source_evidence = _execute_step(
            steps,
            "pinned_external_sources",
            lambda: _verify_sources(mmcif, ligand_sdf, manifest),
        )
        tool_evidence = _execute_step(
            steps,
            "real_python_meeko_rdkit_gemmi_and_vina",
            lambda: _verify_tools(python_path, vina_path, expected),
        )
        setup = _execute_step(
            steps,
            "project_create_raw_import_and_real_ligand_preparation",
            lambda: _create_import_and_prepare_ligand(
                work_root,
                mmcif,
                ligand_sdf,
                expected,
            ),
        )
        project_root = Path(str(setup["project_dir"])).resolve()
        _execute_step(
            steps,
            "project_mmcif_controls_and_real_flexible_preparation",
            lambda: _verify_identity_and_prepare_flexible(
                project_root,
                expected,
            ),
        )
        _execute_step(
            steps,
            "project_box_vina_and_config",
            lambda: _configure_project(project_root, expected),
        )
        _execute_step(
            steps,
            "project_prepare_execute_analyze_movement_and_report",
            lambda: _run_analyze_and_report(project_root, expected),
        )
        result_payload = {
            "ok": True,
            "fixture_id": manifest.get("fixture_id"),
            "verification_scope": (
                "public_project_api_raw_to_flexible_vina_movement_and_report"
            ),
            "network_or_download_used": False,
            "synthetic_outputs_accepted": False,
            "sources": source_evidence,
            "tools": tool_evidence,
            "steps": steps,
        }
    except FlexibleMmcifAcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve JSON CLI boundary.
        caught = FlexibleMmcifAcceptanceError(
            "FLEX_1H4W_UNEXPECTED_ERROR",
            "The project-level 1H4W verifier failed unexpectedly.",
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
        except Exception as exc:  # noqa: BLE001 - acceptance evidence.
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
        except Exception as exc:  # noqa: BLE001 - acceptance evidence.
            resource_restore_error = str(exc)
        resource_restored = (
            RESOURCE_DIR_ENV_VAR not in os.environ
            if previous_resource_dir is None
            else os.environ.get(RESOURCE_DIR_ENV_VAR) == previous_resource_dir
        )
        resource_evidence = {
            "variable": RESOURCE_DIR_ENV_VAR,
            "previously_set": previous_resource_dir is not None,
            "isolated_path": str(isolated_resource_dir),
            "restored": resource_restored,
            "error": resource_restore_error,
        }

        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001 - cleanup is acceptance evidence.
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
            "FLEX_1H4W_TEMPORARY_LIFECYCLE_FAILED",
            (
                "The temporary 1H4W project or its isolated environment "
                "could not be restored."
            ),
            details=lifecycle_evidence,
        )
    result_payload.update(lifecycle_evidence)
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run DockStart's public project API from external 1H4W mmCIF and "
            "BEN SDF through real preparation, flexible Vina, analysis, and report."
        ),
    )
    parser.add_argument(
        "--mmcif",
        required=True,
        help="Path to the manifest-pinned RCSB 1H4W.cif.",
    )
    parser.add_argument(
        "--ligand-sdf",
        required=True,
        help="Path to the manifest-pinned RCSB ModelServer BEN SDF.",
    )
    parser.add_argument(
        "--python",
        default=str(DEFAULT_PYTHON),
        help="Python executable containing the manifest-pinned Meeko/RDKit/Gemmi.",
    )
    parser.add_argument(
        "--vina",
        default=str(DEFAULT_VINA),
        help="Manifest-pinned AutoDock Vina executable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_flexible_mmcif_1h4w(
            arguments.mmcif,
            arguments.ligand_sdf,
            python_executable=arguments.python,
            vina_executable=arguments.vina,
        )
    except FlexibleMmcifAcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "flexible_mmcif_1h4w_external",
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
                    "fixture_id": "flexible_mmcif_1h4w_external",
                    "error": {
                        "code": "FLEX_1H4W_UNEXPECTED_ERROR",
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
