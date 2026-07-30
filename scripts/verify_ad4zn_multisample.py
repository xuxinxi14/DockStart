"""Run the external DockStart AD4Zn 2OI0 / 1R1J acceptance gate.

The fixture is metadata only. Every scientific input and executable is
caller-supplied, verified before preparation, and used only inside a temporary
project tree that is removed in ``finally``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
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
    generate_maps,
    set_scoring_protocol,
    validate_active_maps,
)
from dockstart_core.hydrated_maps import (  # noqa: E402
    HydratedMapError,
    parse_autogrid_map,
)
from dockstart_core.preparation import prepare_ligand_pdbqt  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    import_receptor_pdbqt,
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
from dockstart_core.structure_fetch import import_ligand_raw_file  # noqa: E402

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "ad4zn_multisample"
    / "source_manifest.json"
)
SOURCE_KEYS = frozenset(
    {
        "2oi0_pdb",
        "2oi0_ligand_sdf",
        "1r1j_pdb",
        "1r1j_ligand_sdf",
        "ad4zn_parameter",
    }
)
SYSTEM_KEYS = ("2OI0", "1R1J")
CONTAINS_FLAGS = (
    "contains_upstream_structure_files",
    "contains_ligand_files",
    "contains_parameter_files",
    "contains_maps_or_outputs",
    "contains_executables",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)")
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])


class AD4ZnMultisampleAcceptanceError(RuntimeError):
    """Stable JSON-serializable failure raised by the external gate."""

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
    raise AD4ZnMultisampleAcceptanceError(code, message, details=details)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_lf(payload: bytes) -> bytes:
    return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _line_ending_profile(payload: bytes) -> dict[str, Any]:
    crlf = payload.count(b"\r\n")
    remainder = payload.replace(b"\r\n", b"")
    lf = remainder.count(b"\n")
    cr = remainder.count(b"\r")
    styles = [
        name
        for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr))
        if count
    ]
    return {
        "style": styles[0] if len(styles) == 1 else "mixed" if styles else "none",
        "crlf_count": crlf,
        "lf_count": lf,
        "cr_count": cr,
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
            "AD4ZN_MULTISAMPLE_EVIDENCE_NOT_CANONICAL",
            "Acceptance evidence cannot be serialized canonically.",
            details={"error": str(exc)},
        )
    return _sha256_bytes(payload)


def _normalized_path(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        candidate = candidate.resolve(strict=False)
    return os.path.normcase(str(candidate))


def _regular_file(path: str | Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
        valid = (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.stat().st_size > 0
            and resolved == candidate.absolute()
        )
    except OSError:
        valid = False
        resolved = candidate.resolve(strict=False)
    if not valid:
        _fail(
            "AD4ZN_MULTISAMPLE_FILE_INVALID",
            f"{label} is missing, empty, symbolic, or a reparse path.",
            details={"path": str(resolved)},
        )
    return resolved


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        result = action()
    except AD4ZnMultisampleAcceptanceError as exc:
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
    except Exception as exc:  # noqa: BLE001 - preserve JSON boundary.
        wrapped = AD4ZnMultisampleAcceptanceError(
            "AD4ZN_MULTISAMPLE_STEP_UNEXPECTED_ERROR",
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
    details: dict[str, Any] = {}
    error = payload.get("error")
    if isinstance(error, Mapping):
        details["dockstart_error"] = dict(error)
    for key in ("message", "run_id", "output_file", "report_file"):
        if payload.get(key) not in (None, ""):
            details[key] = payload.get(key)
    _fail(code, message, details=details)
    raise AssertionError("unreachable")


def _expanded_residue_numbers(record: Mapping[str, Any]) -> list[int]:
    residue_numbers = record.get("residue_numbers")
    if not isinstance(residue_numbers, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "A receptor deletion record has no residue_numbers object.",
        )
    singles = residue_numbers.get("single")
    ranges = residue_numbers.get("ranges")
    if not isinstance(singles, list) or not isinstance(ranges, list):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "A receptor deletion record must pin single residues and ranges.",
        )
    values: set[int] = set()
    for raw in singles:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                "A receptor deletion residue is invalid.",
            )
        values.add(raw)
    for item in ranges:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
            or item[0] <= 0
            or item[1] < item[0]
        ):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                "A receptor deletion range is invalid.",
                details={"range": item},
            )
        values.update(range(item[0], item[1] + 1))
    return sorted(values)


def _deletion_spec(system: Mapping[str, Any]) -> str:
    record = system.get("receptor_deletions")
    if not isinstance(record, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "System receptor_deletions is invalid.",
        )
    chain = str(record.get("chain") or "")
    if chain != "A":
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The reviewed receptor deletion chain must be A.",
        )
    return f"{chain}:{','.join(str(item) for item in _expanded_residue_numbers(record))}"


def _validate_text_record(key: str, record: Any) -> None:
    if not isinstance(record, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"Source file record {key} is invalid.",
        )
    canonical_size = record.get("canonical_size_bytes")
    canonical_sha = str(record.get("canonical_sha256") or "").lower()
    variants = record.get("checkout_variants")
    if (
        record.get("canonical_line_endings") != "LF"
        or isinstance(canonical_size, bool)
        or not isinstance(canonical_size, int)
        or canonical_size <= 0
        or not SHA256_RE.fullmatch(canonical_sha)
        or not isinstance(variants, Mapping)
        or set(variants) != {"lf", "crlf"}
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"Source file record {key} violates the LF identity contract.",
        )
    normalized: dict[str, tuple[int, str]] = {}
    for variant_name in ("lf", "crlf"):
        variant = variants.get(variant_name)
        if not isinstance(variant, Mapping):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                f"Source file record {key} has an invalid checkout variant.",
            )
        size = variant.get("size_bytes")
        sha = str(variant.get("sha256") or "").lower()
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or not SHA256_RE.fullmatch(sha)
        ):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                f"Source file record {key} has an invalid checkout identity.",
            )
        normalized[variant_name] = (size, sha)
    if normalized["lf"] != (canonical_size, canonical_sha):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"Source file record {key} does not bind its LF identity.",
        )


def _validate_manifest_contract(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Path,
) -> None:
    if (
        manifest.get("schema_version") != 1
        or manifest.get("fixture_id") != "ad4zn_multisample_external"
        or manifest.get("distribution") != "metadata_only"
        or manifest.get("network_access_allowed") is not False
        or any(manifest.get(flag) is not False for flag in CONTAINS_FLAGS)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The multisample fixture must be the pinned metadata-only schema v1.",
        )
    source_files = manifest.get("source_files")
    systems = manifest.get("systems")
    tools = manifest.get("external_tools")
    protocol = manifest.get("protocol")
    if (
        not isinstance(source_files, Mapping)
        or set(source_files) != SOURCE_KEYS
        or not isinstance(systems, Mapping)
        or tuple(systems) != SYSTEM_KEYS
        or not isinstance(tools, Mapping)
        or not isinstance(protocol, Mapping)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The source, system, tool, or protocol manifest set is incomplete.",
        )
    for key, record in source_files.items():
        _validate_text_record(str(key), record)

    for tool_key in ("python", "mk_prepare_receptor", "autogrid", "vina"):
        record = tools.get(tool_key)
        expected_sha = (
            str(record.get("validated_windows_x86_64_sha256") or "").lower()
            if isinstance(record, Mapping)
            else ""
        )
        if not isinstance(record, Mapping) or not SHA256_RE.fullmatch(expected_sha):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                f"Tool record {tool_key} does not pin Windows evidence.",
            )

    if _expanded_residue_numbers(systems["2OI0"]["receptor_deletions"]) != [
        1,
        *range(482, 623),
    ]:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "2OI0 receptor deletions differ from the reviewed dataset contract.",
        )
    if _expanded_residue_numbers(systems["1R1J"]["receptor_deletions"]) != [
        505,
        752,
        753,
        754,
        2001,
        *range(2002, 2090),
    ]:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "1R1J receptor deletions differ from the reviewed dataset contract.",
        )
    distant = systems["1R1J"].get("distant_incomplete_residue")
    if (
        not isinstance(distant, Mapping)
        or distant.get("chain") != "A"
        or distant.get("residue_number") != 505
        or float(distant.get("required_minimum_distance_angstrom") or 0.0) != 35.0
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "1R1J must pin the remote A:505 distance contract.",
        )

    try:
        entries = {item.name for item in manifest_path.parent.iterdir()}
    except OSError as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The metadata-only fixture directory cannot be inspected.",
            details={"error": str(exc)},
        )
    if entries != {"README.md", "source_manifest.json"}:
        _fail(
            "AD4ZN_MULTISAMPLE_DISTRIBUTION_INVALID",
            "The metadata-only fixture contains an unexpected artifact.",
            details={"actual": sorted(entries)},
        )


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_MISSING",
            "The AD4Zn multisample source manifest is missing.",
            details={"path": str(MANIFEST_PATH)},
        )
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The AD4Zn multisample source manifest cannot be parsed.",
            details={"error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The AD4Zn multisample source manifest must be a JSON object.",
        )
    _validate_manifest_contract(payload, manifest_path=MANIFEST_PATH)
    return payload


def _verify_text_identity(
    path: str | Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    source = _regular_file(path, label)
    payload = source.read_bytes()
    profile = _line_ending_profile(payload)
    if profile["style"] not in {"lf", "crlf"}:
        _fail(
            "AD4ZN_MULTISAMPLE_LINE_ENDINGS_UNSUPPORTED",
            f"{label} must use one consistent pinned LF or CRLF form.",
            details={"path": str(source), "line_endings": profile},
        )
    canonical = _canonical_lf(payload)
    canonical_sha = _sha256_bytes(canonical)
    expected_canonical_sha = str(record.get("canonical_sha256") or "").lower()
    expected_canonical_size = int(record.get("canonical_size_bytes") or 0)
    if (
        len(canonical) != expected_canonical_size
        or canonical_sha != expected_canonical_sha
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_CANONICAL_HASH_MISMATCH",
            f"{label} does not match the pinned scientific text identity.",
            details={
                "path": str(source),
                "expected_size": expected_canonical_size,
                "actual_size": len(canonical),
                "expected_sha256": expected_canonical_sha,
                "actual_sha256": canonical_sha,
            },
        )
    variants = record.get("checkout_variants")
    variant = variants.get(profile["style"]) if isinstance(variants, Mapping) else None
    actual_sha = _sha256_bytes(payload)
    if (
        not isinstance(variant, Mapping)
        or len(payload) != int(variant.get("size_bytes") or 0)
        or actual_sha != str(variant.get("sha256") or "").lower()
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_CHECKOUT_HASH_MISMATCH",
            f"{label} does not match its pinned {profile['style']} checkout form.",
            details={"path": str(source), "actual_sha256": actual_sha},
        )
    return {
        "path": str(source),
        "size_bytes": len(payload),
        "sha256": actual_sha,
        "canonical_size_bytes": len(canonical),
        "canonical_sha256": canonical_sha,
        "checkout_variant": profile["style"],
        "line_endings": profile,
    }


def _parse_pdb_atoms(path: Path) -> list[dict[str, Any]]:
    try:
        text = _canonical_lf(path.read_bytes()).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_PDB_ENCODING_INVALID",
            "A supplied PDB is not strict UTF-8/ASCII text.",
            details={"path": str(path), "error": str(exc)},
        )
    atoms: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        record_type = line[0:6].strip().upper()
        if record_type not in {"ATOM", "HETATM"}:
            continue
        if len(line) < 54:
            _fail(
                "AD4ZN_MULTISAMPLE_PDB_RECORD_INVALID",
                "A PDB atom record is too short.",
                details={"path": str(path), "line_number": line_number},
            )
        try:
            coordinate = (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            )
            residue_number = int(line[22:26].strip())
        except ValueError:
            _fail(
                "AD4ZN_MULTISAMPLE_PDB_RECORD_INVALID",
                "A PDB atom coordinate or residue number is invalid.",
                details={"path": str(path), "line_number": line_number},
            )
        if not all(math.isfinite(value) for value in coordinate):
            _fail(
                "AD4ZN_MULTISAMPLE_PDB_RECORD_INVALID",
                "A PDB atom coordinate is non-finite.",
                details={"path": str(path), "line_number": line_number},
            )
        atoms.append(
            {
                "record_type": record_type,
                "name": line[12:16].strip(),
                "residue_name": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "residue_number": residue_number,
                "coordinate": coordinate,
                "element": line[76:78].strip().upper() if len(line) >= 78 else "",
            }
        )
    if not atoms:
        _fail(
            "AD4ZN_MULTISAMPLE_PDB_EMPTY",
            "A supplied PDB contains no atom records.",
            details={"path": str(path)},
        )
    return atoms


def _parse_sdf_coordinates(path: Path) -> tuple[str, list[tuple[float, float, float]]]:
    try:
        lines = _canonical_lf(path.read_bytes()).decode(
            "utf-8",
            errors="strict",
        ).splitlines()
    except UnicodeError as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_SDF_ENCODING_INVALID",
            "A supplied ligand SDF is not strict UTF-8/ASCII text.",
            details={"path": str(path), "error": str(exc)},
        )
    if len(lines) < 4 or len(lines[3]) < 6:
        _fail(
            "AD4ZN_MULTISAMPLE_SDF_INVALID",
            "A supplied ligand SDF has no V2000 counts line.",
            details={"path": str(path)},
        )
    try:
        atom_count = int(lines[3][0:3])
    except ValueError:
        _fail(
            "AD4ZN_MULTISAMPLE_SDF_INVALID",
            "A supplied ligand SDF atom count is invalid.",
            details={"path": str(path)},
        )
    if atom_count <= 0 or len(lines) < 4 + atom_count:
        _fail(
            "AD4ZN_MULTISAMPLE_SDF_INVALID",
            "A supplied ligand SDF atom block is incomplete.",
            details={"path": str(path), "atom_count": atom_count},
        )
    coordinates: list[tuple[float, float, float]] = []
    for offset, line in enumerate(lines[4 : 4 + atom_count], start=5):
        try:
            coordinate = (
                float(line[0:10]),
                float(line[10:20]),
                float(line[20:30]),
            )
        except (ValueError, IndexError):
            _fail(
                "AD4ZN_MULTISAMPLE_SDF_INVALID",
                "A supplied ligand SDF coordinate is invalid.",
                details={"path": str(path), "line_number": offset},
            )
        if not all(math.isfinite(value) for value in coordinate):
            _fail(
                "AD4ZN_MULTISAMPLE_SDF_INVALID",
                "A supplied ligand SDF coordinate is non-finite.",
                details={"path": str(path), "line_number": offset},
            )
        coordinates.append(coordinate)
    return lines[0].strip(), coordinates


def _distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right, strict=True)))


def _validate_raw_system(
    system_id: str,
    pdb_path: Path,
    sdf_path: Path,
    system: Mapping[str, Any],
) -> dict[str, Any]:
    atoms = _parse_pdb_atoms(pdb_path)
    ligand = system.get("ligand_component")
    zinc_expected = system.get("zinc")
    if not isinstance(ligand, Mapping) or not isinstance(zinc_expected, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} ligand or zinc contract is invalid.",
        )
    zinc_atoms = [
        atom
        for atom in atoms
        if atom["residue_name"].upper() == "ZN"
        and atom["chain"] == str(zinc_expected.get("chain") or "")
        and atom["residue_number"] == int(zinc_expected.get("residue_number") or 0)
    ]
    expected_zinc_coordinate = tuple(float(value) for value in zinc_expected["coordinate"])
    if (
        len(zinc_atoms) != 1
        or _distance(zinc_atoms[0]["coordinate"], expected_zinc_coordinate) > 0.001
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_RAW_ZINC_MISMATCH",
            f"{system_id} does not contain the pinned single Zn atom.",
            details={"count": len(zinc_atoms)},
        )
    ligand_atoms = [
        atom
        for atom in atoms
        if atom["residue_name"] == str(ligand.get("residue_name") or "")
        and atom["chain"] == str(ligand.get("chain") or "")
        and atom["residue_number"] == int(ligand.get("residue_number") or 0)
    ]
    expected_heavy = int(ligand.get("heavy_atom_count") or 0)
    sdf_name, sdf_coordinates = _parse_sdf_coordinates(sdf_path)
    if (
        len(ligand_atoms) != expected_heavy
        or len(sdf_coordinates) != expected_heavy
        or sdf_name != str(ligand.get("residue_name") or "")
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_RAW_LIGAND_MISMATCH",
            f"{system_id} PDB and ligand-instance SDF do not match the pinned component.",
            details={
                "pdb_atom_count": len(ligand_atoms),
                "sdf_atom_count": len(sdf_coordinates),
                "sdf_name": sdf_name,
            },
        )
    instance_rmsd = math.sqrt(
        sum(
            _distance(atom["coordinate"], coordinate) ** 2
            for atom, coordinate in zip(ligand_atoms, sdf_coordinates, strict=True)
        )
        / expected_heavy
    )
    if instance_rmsd > 0.001:
        _fail(
            "AD4ZN_MULTISAMPLE_LIGAND_INSTANCE_COORDINATES_MISMATCH",
            f"{system_id} SDF is not the pinned bound ligand instance.",
            details={"direct_rmsd_angstrom": instance_rmsd},
        )

    waters = [atom for atom in atoms if atom["residue_name"] in {"HOH", "WAT"}]
    if len(waters) != int(system.get("raw_water_count") or 0):
        _fail(
            "AD4ZN_MULTISAMPLE_RAW_WATER_COUNT_MISMATCH",
            f"{system_id} raw water count differs from the pinned source.",
            details={"actual": len(waters)},
        )

    evidence: dict[str, Any] = {
        "pdb_atom_count": len(atoms),
        "ligand_instance_heavy_atom_count": expected_heavy,
        "ligand_instance_direct_rmsd_angstrom": instance_rmsd,
        "zinc_coordinate": list(zinc_atoms[0]["coordinate"]),
        "raw_water_atom_count": len(waters),
        "receptor_deletion_spec": _deletion_spec(system),
    }
    if system_id == "1R1J":
        glycan = system.get("raw_glycan_residues")
        distant = system.get("distant_incomplete_residue")
        if not isinstance(glycan, Mapping) or not isinstance(distant, Mapping):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                "1R1J glycan or A:505 contract is invalid.",
            )
        nag_atoms = [
            atom
            for atom in atoms
            if atom["residue_name"] == "NAG"
            and atom["chain"] == "A"
            and int(glycan["range"][0])
            <= atom["residue_number"]
            <= int(glycan["range"][1])
        ]
        if len(nag_atoms) != int(glycan.get("atom_count") or 0):
            _fail(
                "AD4ZN_MULTISAMPLE_1R1J_NAG_MISMATCH",
                "1R1J NAG 752-754 source evidence is incomplete.",
                details={"actual_atom_count": len(nag_atoms)},
            )
        residue_505 = [
            atom
            for atom in atoms
            if atom["chain"] == "A" and atom["residue_number"] == 505
        ]
        if not residue_505:
            _fail(
                "AD4ZN_MULTISAMPLE_1R1J_A505_MISSING",
                "1R1J does not contain the pinned remote A:505 residue.",
            )
        minimum_distance = min(
            _distance(atom["coordinate"], zinc_atoms[0]["coordinate"])
            for atom in residue_505
        )
        required_minimum = float(
            distant.get("required_minimum_distance_angstrom") or 0.0
        )
        if minimum_distance <= required_minimum:
            _fail(
                "AD4ZN_MULTISAMPLE_1R1J_A505_TOO_CLOSE",
                "1R1J A:505 is not demonstrably remote from the reviewed Zn site.",
                details={
                    "required_greater_than": required_minimum,
                    "actual": minimum_distance,
                },
            )
        evidence.update(
            {
                "nag_atom_count": len(nag_atoms),
                "a505_atom_count": len(residue_505),
                "a505_minimum_distance_to_zinc_angstrom": minimum_distance,
                "a505_required_minimum_distance_angstrom": required_minimum,
            }
        )
    return evidence


def _verify_source_inputs(
    supplied: Mapping[str, Path],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Path], dict[str, Any]]:
    records = manifest.get("source_files")
    systems = manifest.get("systems")
    if not isinstance(records, Mapping) or not isinstance(systems, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The source or system manifest section is invalid.",
        )
    if set(supplied) != SOURCE_KEYS:
        _fail(
            "AD4ZN_MULTISAMPLE_INPUT_SET_INVALID",
            "All four RCSB inputs and AD4Zn.dat must be supplied explicitly.",
            details={"actual": sorted(supplied)},
        )
    resolved: dict[str, Path] = {}
    identities: dict[str, Any] = {}
    for key in sorted(SOURCE_KEYS):
        record = records.get(key)
        if not isinstance(record, Mapping):
            _fail(
                "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
                f"Source record {key} is invalid.",
            )
        identity = _verify_text_identity(
            supplied[key],
            record,
            label=key,
        )
        resolved[key] = Path(identity["path"])
        identities[key] = identity
    raw_contracts = {
        system_id: _validate_raw_system(
            system_id,
            resolved[str(systems[system_id]["pdb_source_key"])],
            resolved[str(systems[system_id]["ligand_source_key"])],
            systems[system_id],
        )
        for system_id in SYSTEM_KEYS
    }
    return resolved, {
        "network_or_download_used": False,
        "files": identities,
        "raw_structure_contracts": raw_contracts,
    }


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
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
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_TOOL_PROBE_FAILED",
            "An external tool probe could not be executed safely.",
            details={"command": [str(item) for item in command], "error": str(exc)},
        )
    raise AssertionError("unreachable")


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(str(value or ""))
    if not match:
        return None
    parts = [int(item) for item in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _tool_dict(value: Any) -> dict[str, Any]:
    return {
        key: getattr(value, key, None)
        for key in (
            "status",
            "path",
            "version",
            "source",
            "message",
            "raw_error",
            "capabilities",
        )
    }


def _enforce_windows_tool_hash(
    tool_key: str,
    path: Path,
    record: Mapping[str, Any],
    *,
    platform_name: str,
) -> dict[str, Any]:
    actual = _sha256(path)
    expected = str(record.get("validated_windows_x86_64_sha256") or "").lower()
    is_windows = platform_name.strip().lower().startswith("win")
    if is_windows and actual != expected:
        _fail(
            "AD4ZN_MULTISAMPLE_WINDOWS_TOOL_HASH_MISMATCH",
            f"{tool_key} does not match the pinned Windows executable.",
            details={
                "tool": tool_key,
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
            },
        )
    return {
        "path": str(path),
        "sha256": actual,
        "hash_policy": "mandatory_exact" if is_windows else "recorded_platform_specific",
        "validated_windows_sha256": expected,
        "matches_validated_windows_sha256": actual == expected,
    }


def _verify_external_tools(
    *,
    python_executable: Path,
    mk_prepare_receptor: Path,
    autogrid_executable: Path,
    vina_executable: Path,
    manifest: Mapping[str, Any],
    platform_name: str | None = None,
) -> tuple[Any, Any, dict[str, Any]]:
    tools = manifest.get("external_tools")
    if not isinstance(tools, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The external_tools manifest section is invalid.",
        )
    platform_label = platform_name or platform.system()
    paths = {
        "python": _regular_file(python_executable, "Python executable"),
        "mk_prepare_receptor": _regular_file(
            mk_prepare_receptor,
            "mk_prepare_receptor launcher",
        ),
        "autogrid": _regular_file(autogrid_executable, "AutoGrid executable"),
        "vina": _regular_file(vina_executable, "Vina executable"),
    }
    evidence = {
        key: _enforce_windows_tool_hash(
            key,
            path,
            tools[key],
            platform_name=platform_label,
        )
        for key, path in paths.items()
    }

    python_probe = _run_command(
        [str(paths["python"]), "--version"],
        cwd=paths["python"].parent,
    )
    python_output = "\n".join(
        part for part in (python_probe.stdout, python_probe.stderr) if part
    )
    expected_python = str(tools["python"].get("required_version") or "")
    if (
        python_probe.returncode != 0
        or _version_tuple(python_output) != _version_tuple(expected_python)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_PYTHON_VERSION_MISMATCH",
            "The supplied Python does not match the pinned version.",
            details={"expected": expected_python, "actual": python_output.strip()},
        )

    dependency_probe = _run_command(
        [
            str(paths["python"]),
            "-I",
            "-c",
            (
                "import json, meeko, rdkit; "
                "print(json.dumps({'meeko': meeko.__version__, "
                "'rdkit': rdkit.__version__}))"
            ),
        ],
        cwd=paths["python"].parent,
    )
    try:
        dependency_versions = json.loads(dependency_probe.stdout.strip())
    except json.JSONDecodeError:
        dependency_versions = {}
    mk_record = tools["mk_prepare_receptor"]
    if (
        dependency_probe.returncode != 0
        or not isinstance(dependency_versions, Mapping)
        or str(dependency_versions.get("meeko") or "")
        != str(mk_record.get("required_meeko_version") or "")
        or str(dependency_versions.get("rdkit") or "")
        != str(mk_record.get("required_rdkit_version") or "")
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_PREPARATION_TOOLCHAIN_VERSION_MISMATCH",
            "The supplied Python does not contain the pinned Meeko/RDKit versions.",
            details={
                "actual": dict(dependency_versions)
                if isinstance(dependency_versions, Mapping)
                else {},
                "stderr": dependency_probe.stderr,
            },
        )

    mk_help = _run_command(
        [str(paths["mk_prepare_receptor"]), "--help"],
        cwd=paths["mk_prepare_receptor"].parent,
    )
    help_text = "\n".join(part for part in (mk_help.stdout, mk_help.stderr) if part)
    required_options = mk_record.get("required_help_options")
    missing_options = (
        [str(item) for item in required_options if str(item) not in help_text]
        if isinstance(required_options, list)
        else ["<manifest-invalid>"]
    )
    if mk_help.returncode != 0 or missing_options:
        _fail(
            "AD4ZN_MULTISAMPLE_MK_PREPARE_RECEPTOR_IDENTITY_MISMATCH",
            "The supplied mk_prepare_receptor launcher lacks the required CLI.",
            details={
                "returncode": mk_help.returncode,
                "missing_options": missing_options,
            },
        )

    autogrid_detection = autogrid_adapter.detect(str(paths["autogrid"]))
    autogrid_record = tools["autogrid"]
    detected_autogrid = _tool_dict(autogrid_detection)
    if (
        detected_autogrid["status"] != "ok"
        or _normalized_path(detected_autogrid["path"] or "")
        != _normalized_path(paths["autogrid"])
        or _version_tuple(str(detected_autogrid["version"] or ""))
        is None
        or _version_tuple(str(detected_autogrid["version"] or ""))
        < _version_tuple(str(autogrid_record.get("minimum_version") or ""))  # type: ignore[operator]
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_AUTOGRID_IDENTITY_MISMATCH",
            "The supplied AutoGrid path or version is not accepted.",
            details={"detection": detected_autogrid},
        )

    never_bundled = paths["vina"].parent / "__dockstart_no_bundled_vina__"
    vina_detection = vina_adapter.detect(
        str(paths["vina"]),
        bundled_path=str(never_bundled),
    )
    detected_vina = _tool_dict(vina_detection)
    capabilities = detected_vina.get("capabilities")
    features = (
        capabilities.get("features")
        if isinstance(capabilities, Mapping)
        else None
    )
    maps_capability = features.get("maps") if isinstance(features, Mapping) else None
    vina_record = tools["vina"]
    if (
        detected_vina["status"] != "ok"
        or _normalized_path(detected_vina["path"] or "")
        != _normalized_path(paths["vina"])
        or str(detected_vina["version"] or "")
        != str(vina_record.get("required_version") or "")
        or not isinstance(maps_capability, Mapping)
        or maps_capability.get("supported") is not True
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_VINA_IDENTITY_MISMATCH",
            "The supplied Vina path, version, or --maps capability is invalid.",
            details={"detection": detected_vina},
        )

    is_windows = platform_label.strip().lower().startswith("win")
    fixed_windows = is_windows and all(
        item["matches_validated_windows_sha256"] for item in evidence.values()
    )
    evidence["python"].update(
        {
            "version": expected_python,
            "meeko_version": dependency_versions["meeko"],
            "rdkit_version": dependency_versions["rdkit"],
        }
    )
    evidence["mk_prepare_receptor"]["help_options_verified"] = list(required_options)
    evidence["autogrid"]["version"] = detected_autogrid["version"]
    evidence["vina"].update(
        {
            "version": detected_vina["version"],
            "maps_capability": True,
        }
    )
    return autogrid_detection, vina_detection, {
        "platform": platform_label,
        "fixed_windows_toolchain": fixed_windows,
        "tools": evidence,
    }


def _parse_pdbqt_atoms(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_PDBQT_INVALID",
            "A generated PDBQT cannot be read as strict UTF-8.",
            details={"path": str(path), "error": str(exc)},
        )
    atoms: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line[0:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        parts = line.split()
        if len(line) < 54 or len(parts) < 3:
            _fail(
                "AD4ZN_MULTISAMPLE_PDBQT_INVALID",
                "A generated PDBQT atom record is malformed.",
                details={"path": str(path), "line_number": line_number},
            )
        try:
            coordinate = (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            )
            charge = float(parts[-2])
        except ValueError:
            _fail(
                "AD4ZN_MULTISAMPLE_PDBQT_INVALID",
                "A generated PDBQT coordinate or charge is invalid.",
                details={"path": str(path), "line_number": line_number},
            )
        residue_number_text = line[22:26].strip()
        try:
            residue_number = int(residue_number_text)
        except ValueError:
            residue_number = 0
        atoms.append(
            {
                "name": line[12:16].strip(),
                "residue_name": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "residue_number": residue_number,
                "coordinate": coordinate,
                "charge": charge,
                "atom_type": parts[-1],
                "line_number": line_number,
            }
        )
    if not atoms:
        _fail(
            "AD4ZN_MULTISAMPLE_PDBQT_EMPTY",
            "A generated PDBQT contains no atom records.",
            details={"path": str(path)},
        )
    return atoms


def _is_hydrogen_type(atom_type: str) -> bool:
    return atom_type.strip().upper() in {"H", "HD", "HS"}


def _heavy_pdbqt_coordinates(path: Path) -> list[tuple[float, float, float]]:
    return [
        tuple(atom["coordinate"])
        for atom in _parse_pdbqt_atoms(path)
        if not _is_hydrogen_type(str(atom["atom_type"]))
    ]


def _direct_rmsd(
    reference: Sequence[Sequence[float]],
    observed: Sequence[Sequence[float]],
) -> float:
    if not reference or len(reference) != len(observed):
        _fail(
            "AD4ZN_MULTISAMPLE_RMSD_ATOM_ORDER_MISMATCH",
            "Direct RMSD requires the same non-empty atom count and order.",
            details={"reference_count": len(reference), "observed_count": len(observed)},
        )
    return math.sqrt(
        sum(_distance(left, right) ** 2 for left, right in zip(reference, observed, strict=True))
        / len(reference)
    )


def _prepare_raw_receptor(
    system_id: str,
    source_pdb: Path,
    mk_prepare_receptor: Path,
    work_dir: Path,
    system: Mapping[str, Any],
    *,
    fixed_windows_toolchain: bool,
) -> tuple[Path, dict[str, Any]]:
    output = work_dir / f"{system_id.lower()}_receptor.pdbqt"
    deletion_spec = _deletion_spec(system)
    command = [
        str(mk_prepare_receptor),
        "--read_pdb",
        str(source_pdb),
        "--delete_residues",
        deletion_spec,
        "--write_pdbqt",
        str(output),
    ]
    completed = _run_command(command, cwd=work_dir, timeout=600)
    (work_dir / "mk_prepare_receptor.stdout.txt").write_text(
        completed.stdout,
        encoding="utf-8",
    )
    (work_dir / "mk_prepare_receptor.stderr.txt").write_text(
        completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode != 0 or not output.is_file():
        _fail(
            "AD4ZN_MULTISAMPLE_RECEPTOR_PREPARATION_FAILED",
            f"Meeko could not prepare the reviewed {system_id} receptor.",
            details={
                "returncode": completed.returncode,
                "command": command,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            },
        )
    output = _regular_file(output, f"{system_id} prepared receptor")
    atoms = _parse_pdbqt_atoms(output)
    prepared_expected = system.get("prepared_receptor")
    zinc_expected = system.get("zinc")
    if not isinstance(prepared_expected, Mapping) or not isinstance(zinc_expected, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} prepared receptor contract is invalid.",
        )
    zinc_atoms = [
        atom for atom in atoms if str(atom["atom_type"]).upper() == "ZN"
    ]
    tz_atoms = [
        atom for atom in atoms if str(atom["atom_type"]).upper() == "TZ"
    ]
    water_atoms = [
        atom for atom in atoms if atom["residue_name"].upper() in {"HOH", "WAT"}
    ]
    deleted = set(_expanded_residue_numbers(system["receptor_deletions"]))
    deleted_remaining = sorted(
        {
            atom["residue_number"]
            for atom in atoms
            if atom["chain"] == "A" and atom["residue_number"] in deleted
        }
    )
    expected_coordinate = tuple(float(value) for value in zinc_expected["coordinate"])
    if (
        len(atoms) != int(prepared_expected.get("atom_count") or 0)
        or len(zinc_atoms) != 1
        or tz_atoms
        or water_atoms
        or deleted_remaining
        or abs(float(zinc_atoms[0]["charge"]) - 2.0) > 1e-6
        or _distance(zinc_atoms[0]["coordinate"], expected_coordinate) > 0.001
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_PREPARED_RECEPTOR_CONTRACT_FAILED",
            f"{system_id} prepared receptor violates the pinned structure contract.",
            details={
                "atom_count": len(atoms),
                "zinc_count": len(zinc_atoms),
                "tz_count": len(tz_atoms),
                "water_count": len(water_atoms),
                "deleted_residues_remaining": deleted_remaining,
            },
        )
    identity = {
        "size_bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "canonical_lf_sha256": _sha256_bytes(_canonical_lf(output.read_bytes())),
    }
    if fixed_windows_toolchain and (
        identity["sha256"]
        != str(prepared_expected.get("fixed_windows_sha256") or "")
        or identity["canonical_lf_sha256"]
        != str(prepared_expected.get("fixed_windows_canonical_lf_sha256") or "")
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_WINDOWS_RECEPTOR_OUTPUT_MISMATCH",
            f"{system_id} prepared receptor differs from fixed Windows evidence.",
            details={"actual": identity},
        )
    return output, {
        "command": command,
        "deletion_spec": deletion_spec,
        "atom_count": len(atoms),
        "zinc_count": len(zinc_atoms),
        "zinc_charge": zinc_atoms[0]["charge"],
        "water_atom_count": len(water_atoms),
        "identity": identity,
        "exact_windows_identity_enforced": fixed_windows_toolchain,
    }


def _create_project_and_prepare_ligand(
    system_id: str,
    project_parent: Path,
    receptor_pdbqt: Path,
    ligand_sdf: Path,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    python_executable: Path,
    fixed_windows_toolchain: bool,
) -> tuple[Path, dict[str, Any]]:
    created = _require_ok(
        create_project(f"ad4zn_{system_id.lower()}_acceptance", str(project_parent)),
        "AD4ZN_MULTISAMPLE_PROJECT_CREATE_FAILED",
        f"DockStart could not create the {system_id} temporary project.",
    )
    project_dir = Path(str(created["project_dir"])).resolve(strict=True)
    try:
        project_dir.relative_to(project_parent.resolve(strict=True))
    except ValueError:
        _fail(
            "AD4ZN_MULTISAMPLE_PROJECT_OUTSIDE_TEMP",
            "DockStart created a project outside the temporary root.",
            details={"project_dir": str(project_dir)},
        )
    _require_ok(
        import_receptor_pdbqt(str(project_dir), str(receptor_pdbqt)),
        "AD4ZN_MULTISAMPLE_RECEPTOR_IMPORT_FAILED",
        f"DockStart could not import the prepared {system_id} receptor.",
    )
    _require_ok(
        import_ligand_raw_file(str(project_dir), str(ligand_sdf)),
        "AD4ZN_MULTISAMPLE_LIGAND_RAW_IMPORT_FAILED",
        f"DockStart could not import the {system_id} ligand-instance SDF.",
    )
    prepared = _require_ok(
        prepare_ligand_pdbqt(str(project_dir)),
        "AD4ZN_MULTISAMPLE_LIGAND_PREPARATION_FAILED",
        f"DockStart's public API could not prepare the {system_id} ligand.",
    )
    project_payload = prepared.get("project")
    preparation = (
        project_payload.get("preparation")
        if isinstance(project_payload, Mapping)
        else None
    )
    ligand_preparation = (
        preparation.get("ligand")
        if isinstance(preparation, Mapping)
        else None
    )
    if (
        not isinstance(ligand_preparation, Mapping)
        or ligand_preparation.get("status") != "finished"
        or _normalized_path(str(ligand_preparation.get("python_path") or ""))
        != _normalized_path(python_executable)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_LIGAND_PREPARATION_PROVENANCE_INVALID",
            "Public ligand preparation did not use the supplied Python path.",
            details={"preparation": dict(ligand_preparation or {})},
        )
    ligand_pdbqt = _regular_file(
        project_dir / "prepared" / "ligand.pdbqt",
        f"{system_id} prepared ligand",
    )
    _name, source_coordinates = _parse_sdf_coordinates(ligand_sdf)
    prepared_coordinates = _heavy_pdbqt_coordinates(ligand_pdbqt)
    rounded_source_coordinates = sorted(
        tuple(round(float(value), 3) for value in coordinate)
        for coordinate in source_coordinates
    )
    rounded_prepared_coordinates = sorted(
        tuple(round(float(value), 3) for value in coordinate)
        for coordinate in prepared_coordinates
    )
    if rounded_source_coordinates != rounded_prepared_coordinates:
        _fail(
            "AD4ZN_MULTISAMPLE_LIGAND_COORDINATES_CHANGED",
            (
                "Public ligand preparation did not preserve the bound "
                "heavy-atom coordinate multiset."
            ),
            details={
                "system_id": system_id,
                "source_count": len(source_coordinates),
                "prepared_count": len(prepared_coordinates),
            },
        )
    ligand_expected = system.get("prepared_ligand")
    if not isinstance(ligand_expected, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} prepared ligand identity is invalid.",
        )
    ligand_identity = {
        "size_bytes": ligand_pdbqt.stat().st_size,
        "sha256": _sha256(ligand_pdbqt),
        "canonical_lf_sha256": _sha256_bytes(
            _canonical_lf(ligand_pdbqt.read_bytes())
        ),
    }
    if fixed_windows_toolchain and (
        ligand_identity["sha256"]
        != str(ligand_expected.get("fixed_windows_sha256") or "")
        or ligand_identity["canonical_lf_sha256"]
        != str(ligand_expected.get("fixed_windows_canonical_lf_sha256") or "")
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_WINDOWS_LIGAND_OUTPUT_MISMATCH",
            f"{system_id} prepared ligand differs from fixed Windows evidence.",
            details={"actual": ligand_identity},
        )

    box = system.get("box")
    vina = protocol.get("vina")
    if not isinstance(box, Mapping) or not isinstance(vina, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} Box or Vina contract is invalid.",
        )
    center = box.get("center")
    size = box.get("size")
    if not isinstance(center, list) or not isinstance(size, list):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} Box vectors are invalid.",
        )
    _require_ok(
        update_box_params(
            str(project_dir),
            {
                "center_x": center[0],
                "center_y": center[1],
                "center_z": center[2],
                "size_x": size[0],
                "size_y": size[1],
                "size_z": size[2],
            },
        ),
        "AD4ZN_MULTISAMPLE_BOX_UPDATE_FAILED",
        f"DockStart could not save the {system_id} requested Box.",
    )
    _require_ok(
        update_vina_params(
            str(project_dir),
            {
                key: vina[key]
                for key in (
                    "exhaustiveness",
                    "num_modes",
                    "energy_range",
                    "cpu",
                    "seed",
                )
            },
        ),
        "AD4ZN_MULTISAMPLE_VINA_PARAMS_FAILED",
        f"DockStart could not save the {system_id} Vina parameters.",
    )
    _require_ok(
        set_scoring_protocol(str(project_dir), "ad4zn_beta"),
        "AD4ZN_MULTISAMPLE_PROTOCOL_ACTIVATION_FAILED",
        f"DockStart could not activate AD4Zn for {system_id}.",
    )
    return project_dir, {
        "project_dir_policy": "temporary_directory_only",
        "prepared_ligand_identity": ligand_identity,
        "ligand_heavy_atom_count": len(prepared_coordinates),
        "bound_coordinate_multiset_preserved": True,
        "coordinate_comparison_precision_angstrom": 0.001,
        "prepared_atom_order_is_frozen_rmsd_reference": True,
        "python_path": str(python_executable),
        "exact_windows_identity_enforced": fixed_windows_toolchain,
    }


def _pdbqt_marker_atoms(path: Path, atom_type: str) -> list[dict[str, Any]]:
    expected = atom_type.upper()
    return [
        atom
        for atom in _parse_pdbqt_atoms(path)
        if str(atom["atom_type"]).upper() == expected
    ]


def _prepare_ad4zn(
    system_id: str,
    project_dir: Path,
    parameter_file: Path,
    system: Mapping[str, Any],
    *,
    fixed_windows_toolchain: bool,
) -> dict[str, Any]:
    status = _require_ok(
        get_ad4zn_status(str(project_dir)),
        "AD4ZN_MULTISAMPLE_SITE_ANALYSIS_FAILED",
        f"DockStart could not analyze the {system_id} zinc site.",
    )
    sites = status.get("sites")
    site_expected = system.get("ad4zn_site")
    zinc_expected = system.get("zinc")
    if (
        not isinstance(sites, list)
        or not isinstance(site_expected, Mapping)
        or not isinstance(zinc_expected, Mapping)
        or len(sites) != int(site_expected.get("site_count") or 0)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_SITE_COUNT_MISMATCH",
            f"{system_id} does not expose exactly one reviewed Zn site.",
            details={"site_count": len(sites or [])},
        )
    selected = sites[0]
    if (
        not isinstance(selected, Mapping)
        or selected.get("can_generate") is not True
        or int(selected.get("coordination_number") or 0)
        != int(site_expected.get("coordination_number") or 0)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_SITE_GEOMETRY_MISMATCH",
            f"{system_id} zinc geometry is outside the AD4Zn beta boundary.",
            details={"site": dict(selected or {})},
        )
    selected_id = str(selected.get("site_id") or "")
    _require_ok(
        save_review(
            str(project_dir),
            {
                "selected_site_id": selected_id,
                "confirmations": {key: True for key in REQUIRED_CONFIRMATIONS},
            },
        ),
        "AD4ZN_MULTISAMPLE_REVIEW_FAILED",
        f"DockStart could not bind AD4Zn confirmations for {system_id}.",
    )
    prepared = _require_ok(
        prepare_ad4zn_receptor(str(project_dir), {}),
        "AD4ZN_MULTISAMPLE_TZ_PREPARATION_FAILED",
        f"DockStart could not generate the {system_id} TZ receptor.",
    )
    recorded = _require_ok(
        record_parameter_file(str(project_dir), parameter_file),
        "AD4ZN_MULTISAMPLE_PARAMETER_RECORD_FAILED",
        "DockStart rejected the pinned AD4Zn.dat.",
    )
    parameter = recorded.get("parameter_file")
    if (
        recorded.get("preparation_ready") is not True
        or not isinstance(parameter, Mapping)
        or parameter.get("valid") is not True
        or parameter.get("matches_reference_sha256") is not True
        or parameter.get("license_id") != "GPL-2.0-or-later"
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_PARAMETER_IDENTITY_INVALID",
            "The recorded AD4Zn.dat did not satisfy the supported v1.2.7 profile.",
            details={"parameter_file": dict(parameter or {})},
        )
    receptor_record = prepared.get("prepared_receptor")
    if not isinstance(receptor_record, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_TZ_RECORD_MISSING",
            f"{system_id} TZ preparation returned no receptor record.",
        )
    receptor_path = _regular_file(
        project_dir / str(receptor_record.get("relative_path") or ""),
        f"{system_id} TZ receptor",
    )
    zn_atoms = _pdbqt_marker_atoms(receptor_path, "ZN")
    tz_atoms = _pdbqt_marker_atoms(receptor_path, "TZ")
    expected_zn = tuple(float(item) for item in zinc_expected["coordinate"])
    expected_tz = tuple(float(item) for item in site_expected["tz_coordinate"])
    tolerance = float(site_expected.get("coordinate_tolerance_angstrom") or 0.0)
    if (
        len(zn_atoms) != 1
        or len(tz_atoms) != 1
        or abs(float(zn_atoms[0]["charge"])) > 1e-9
        or abs(float(tz_atoms[0]["charge"])) > 1e-9
        or _distance(zn_atoms[0]["coordinate"], expected_zn) > tolerance
        or _distance(tz_atoms[0]["coordinate"], expected_tz) > tolerance
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_ZN_TZ_CONTRACT_FAILED",
            f"{system_id} generated ZN/TZ atoms violate the pinned contract.",
            details={"zn_count": len(zn_atoms), "tz_count": len(tz_atoms)},
        )
    zn_tz_distance = _distance(zn_atoms[0]["coordinate"], tz_atoms[0]["coordinate"])
    if (
        abs(
            zn_tz_distance
            - float(site_expected.get("tz_distance_angstrom") or 0.0)
        )
        > float(site_expected.get("tz_distance_tolerance_angstrom") or 0.0)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_TZ_DISTANCE_MISMATCH",
            f"{system_id} TZ is not at the pinned distance from Zn.",
            details={"distance_angstrom": zn_tz_distance},
        )
    receptor_sha = _sha256(receptor_path)
    if fixed_windows_toolchain and receptor_sha != str(
        site_expected.get("fixed_windows_tz_receptor_sha256") or ""
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_WINDOWS_TZ_OUTPUT_MISMATCH",
            f"{system_id} TZ receptor differs from fixed Windows evidence.",
            details={"actual_sha256": receptor_sha},
        )
    return {
        "site_id": selected_id,
        "coordination_number": selected.get("coordination_number"),
        "confirmation_count": len(REQUIRED_CONFIRMATIONS),
        "zn_coordinate": list(zn_atoms[0]["coordinate"]),
        "tz_coordinate": list(tz_atoms[0]["coordinate"]),
        "zn_charge": zn_atoms[0]["charge"],
        "tz_charge": tz_atoms[0]["charge"],
        "zn_tz_distance_angstrom": zn_tz_distance,
        "tz_receptor_sha256": receptor_sha,
        "parameter_canonical_sha256": parameter.get("canonical_sha256"),
        "exact_windows_identity_enforced": fixed_windows_toolchain,
    }


def _axis_mapping(values: Sequence[Any]) -> dict[str, float]:
    if len(values) != 3:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "A three-axis vector has the wrong length.",
        )
    return {
        axis: float(value)
        for axis, value in zip(("x", "y", "z"), values, strict=True)
    }


def _mapping_close(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    tolerance: float = 1e-6,
) -> bool:
    try:
        return all(
            math.isclose(
                float(actual[axis]),
                float(expected[axis]),
                rel_tol=0.0,
                abs_tol=tolerance,
            )
            for axis in ("x", "y", "z")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _validate_box_coverage(
    maps_manifest: Mapping[str, Any],
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    ad4zn = maps_manifest.get("ad4zn")
    grid = maps_manifest.get("grid")
    coverage = ad4zn.get("box_coverage") if isinstance(ad4zn, Mapping) else None
    recorded_sha = (
        str(ad4zn.get("box_coverage_sha256") or "")
        if isinstance(ad4zn, Mapping)
        else ""
    )
    if not isinstance(coverage, Mapping) or not isinstance(grid, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_BOX_COVERAGE_MISSING",
            "The maps manifest has no AD4Zn Box coverage evidence.",
        )
    actual_sha = _canonical_json_sha256(coverage)
    if recorded_sha != actual_sha:
        _fail(
            "AD4ZN_MULTISAMPLE_BOX_COVERAGE_SHA256_MISMATCH",
            "The AD4Zn Box coverage evidence hash is invalid.",
            details={"expected": recorded_sha, "actual": actual_sha},
        )
    box = system.get("box")
    zinc = system.get("zinc")
    site = system.get("ad4zn_site")
    if (
        not isinstance(box, Mapping)
        or not isinstance(zinc, Mapping)
        or not isinstance(site, Mapping)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "A system Box, zinc, or TZ contract is invalid.",
        )
    expected_center = _axis_mapping(box["center"])
    expected_size = _axis_mapping(box["size"])
    expected_intervals = {
        axis: int(value)
        for axis, value in zip(
            ("x", "y", "z"),
            protocol["effective_grid_intervals"],
            strict=True,
        )
    }
    expected_actual_size = _axis_mapping(protocol["effective_grid_size_angstrom"])
    markers = coverage.get("markers")
    grid_requested = grid.get("requested_box")
    if (
        coverage.get("method") != AD4ZN_BOX_COVERAGE_METHOD
        or coverage.get("all_zn_tz_inside_requested_box") is not True
        or coverage.get("all_zn_tz_inside_effective_grid") is not True
        or not _mapping_close(
            coverage.get("box_center_angstrom", {}),
            expected_center,
        )
        or not _mapping_close(
            coverage.get("requested_box_size_angstrom", {}),
            expected_size,
        )
        or coverage.get("effective_grid_axis_intervals") != expected_intervals
        or not _mapping_close(
            coverage.get("effective_grid_size_angstrom", {}),
            expected_actual_size,
        )
        or not math.isclose(
            float(coverage.get("effective_grid_spacing_angstrom") or 0.0),
            float(protocol.get("spacing") or 0.0),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        or not isinstance(markers, Mapping)
        or set(markers) != {"ZN", "TZ"}
        or not isinstance(grid_requested, Mapping)
        or not _mapping_close(grid_requested.get("center", {}), expected_center)
        or not _mapping_close(grid_requested.get("size", {}), expected_size)
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_BOX_COVERAGE_GEOMETRY_MISMATCH",
            "Requested Box or effective-grid coverage differs from the pinned contract.",
        )
    expected_marker_coordinates = {
        "ZN": _axis_mapping(zinc["coordinate"]),
        "TZ": _axis_mapping(site["tz_coordinate"]),
    }
    for marker_type, expected_coordinate in expected_marker_coordinates.items():
        marker = markers.get(marker_type)
        if (
            not isinstance(marker, Mapping)
            or marker.get("inside_requested_box") is not True
            or marker.get("inside_effective_grid") is not True
            or not _mapping_close(
                marker.get("coordinate_angstrom", {}),
                expected_coordinate,
                tolerance=0.002,
            )
        ):
            _fail(
                "AD4ZN_MULTISAMPLE_BOX_COVERAGE_MARKER_FAILED",
                f"{marker_type} is not covered by both requested and effective grids.",
            )
    return {
        **dict(coverage),
        "box_coverage_sha256": actual_sha,
    }


def _generate_and_validate_maps(
    system_id: str,
    project_dir: Path,
    autogrid_detection: Any,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    intervals = protocol.get("effective_grid_intervals")
    if not isinstance(intervals, list) or len(intervals) != 3:
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The effective grid interval vector is invalid.",
        )
    options = {
        "spacing": float(protocol["spacing"]),
        "grid_points": {
            axis: int(value)
            for axis, value in zip(("x", "y", "z"), intervals, strict=True)
        },
    }
    with patch(
        "dockstart_core.autogrid.autogrid_adapter.detect",
        return_value=autogrid_detection,
    ):
        generated = _require_ok(
            generate_maps(str(project_dir), options),
            "AD4ZN_MULTISAMPLE_MAP_GENERATION_FAILED",
            f"DockStart could not generate {system_id} AD4Zn maps.",
        )
        status = _require_ok(
            validate_active_maps(str(project_dir)),
            "AD4ZN_MULTISAMPLE_MAP_VALIDATION_FAILED",
            f"DockStart rejected its generated {system_id} AD4Zn maps.",
        )
    if status.get("ready") is not True:
        _fail(
            "AD4ZN_MULTISAMPLE_MAPS_NOT_READY",
            f"{system_id} maps did not reach the run gate.",
            details={"issues": status.get("issues")},
        )
    manifest = generated.get("manifest")
    if not isinstance(manifest, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MAP_MANIFEST_MISSING",
            f"{system_id} map generation returned no manifest.",
        )
    autogrid = manifest.get("autogrid")
    log_summary = (
        autogrid.get("log_summary") if isinstance(autogrid, Mapping) else None
    )
    if (
        manifest.get("protocol_id") != "ad4zn_beta"
        or manifest.get("status") != "ready"
        or not isinstance(log_summary, Mapping)
        or log_summary.get("successful_completion") is not True
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_AUTOGRID_COMPLETION_INVALID",
            f"{system_id} map manifest lacks successful AutoGrid evidence.",
        )
    coverage = _validate_box_coverage(manifest, system, protocol)
    maps_expected = system.get("maps")
    maps = manifest.get("maps")
    files = maps.get("files") if isinstance(maps, Mapping) else None
    if (
        not isinstance(maps_expected, Mapping)
        or not isinstance(files, list)
        or list(maps.get("ligand_atom_types") or [])
        != list(maps_expected.get("ligand_atom_types") or [])
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_MAP_TYPES_MISMATCH",
            f"{system_id} ligand atom types do not match the pinned map profile.",
        )
    by_name = {
        str(item.get("name") or ""): item
        for item in files
        if isinstance(item, Mapping)
    }
    required_names = [str(item) for item in maps_expected["required_map_names"]]
    missing = [name for name in required_names if name not in by_name]
    if missing:
        _fail(
            "AD4ZN_MULTISAMPLE_MAP_FILES_MISSING",
            f"{system_id} map set is incomplete.",
            details={"missing": missing},
        )
    point_count = math.prod(int(value) + 1 for value in intervals)
    parsed_map_count = 0
    for name in required_names:
        record = by_name[name]
        path = _regular_file(
            project_dir / str(record.get("relative_path") or ""),
            f"{system_id} map {name}",
        )
        if (
            path.stat().st_size != int(record.get("size_bytes") or 0)
            or _sha256(path) != str(record.get("sha256") or "")
        ):
            _fail(
                "AD4ZN_MULTISAMPLE_MAP_FILE_IDENTITY_MISMATCH",
                f"{system_id} map {name} differs from its manifest record.",
            )
        if not name.endswith(".map"):
            continue
        try:
            parsed = parse_autogrid_map(path)
        except HydratedMapError as exc:
            _fail(
                "AD4ZN_MULTISAMPLE_MAP_PARSE_FAILED",
                f"{system_id} map {name} could not be parsed.",
                details={"code": exc.code, "message": exc.message},
            )
        if len(parsed.values) != point_count or any(
            not math.isfinite(float(value)) for value in parsed.values
        ):
            _fail(
                "AD4ZN_MULTISAMPLE_MAP_NUMERIC_INVALID",
                f"{system_id} map {name} has invalid geometry or values.",
                details={
                    "expected_point_count": point_count,
                    "actual_point_count": len(parsed.values),
                },
            )
        parsed_map_count += 1
    return {
        "map_set_id": generated.get("map_set_id"),
        "manifest_file": generated.get("manifest_file"),
        "autogrid_version": autogrid.get("version"),
        "autogrid_sha256": autogrid.get("sha256"),
        "successful_completion": True,
        "ligand_atom_types": maps.get("ligand_atom_types"),
        "required_map_count": len(required_names),
        "parsed_numeric_map_count": parsed_map_count,
        "point_count_per_numeric_map": point_count,
        "box_coverage": coverage,
    }


def _split_pdbqt_models(path: Path) -> list[list[str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_OUTPUT_INVALID",
            "Vina output cannot be read as strict UTF-8.",
            details={"path": str(path), "error": str(exc)},
        )
    models: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("MODEL"):
            if current is not None:
                _fail(
                    "AD4ZN_MULTISAMPLE_OUTPUT_MODEL_INVALID",
                    "Vina output contains nested MODEL records.",
                )
            current = [line]
        elif line.startswith("ENDMDL"):
            if current is None:
                _fail(
                    "AD4ZN_MULTISAMPLE_OUTPUT_MODEL_INVALID",
                    "Vina output contains ENDMDL without MODEL.",
                )
            current.append(line)
            models.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None or not models:
        _fail(
            "AD4ZN_MULTISAMPLE_OUTPUT_MODEL_INVALID",
            "Vina output has no complete MODEL blocks.",
        )
    return models


def _model_heavy_coordinates(lines: Sequence[str]) -> list[tuple[float, float, float]]:
    coordinates: list[tuple[float, float, float]] = []
    for line in lines:
        if line[0:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        parts = line.split()
        if not parts or _is_hydrogen_type(parts[-1]):
            continue
        try:
            coordinates.append(
                (
                    float(line[30:38]),
                    float(line[38:46]),
                    float(line[46:54]),
                )
            )
        except ValueError:
            _fail(
                "AD4ZN_MULTISAMPLE_OUTPUT_ATOM_INVALID",
                "Vina output contains an invalid atom coordinate.",
            )
    return coordinates


def _score_rows(scores: Any) -> list[dict[str, Any]]:
    if not isinstance(scores, list) or not scores:
        _fail(
            "AD4ZN_MULTISAMPLE_SCORES_INVALID",
            "DockStart returned no score rows.",
        )
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(scores, start=1):
        if not isinstance(row, Mapping):
            _fail(
                "AD4ZN_MULTISAMPLE_SCORES_INVALID",
                "DockStart returned a non-object score row.",
            )
        try:
            mode = int(row.get("mode") or index)
            affinity = float(row.get("affinity_kcal_mol"))
        except (TypeError, ValueError):
            _fail(
                "AD4ZN_MULTISAMPLE_SCORES_INVALID",
                "DockStart returned an invalid score value.",
            )
        if mode != index or not math.isfinite(affinity):
            _fail(
                "AD4ZN_MULTISAMPLE_SCORES_INVALID",
                "DockStart score modes are not finite and sequential.",
            )
        normalized.append({"mode": mode, "affinity": affinity})
    return normalized


def _direct_heavy_rmsd_diagnostics(
    reference_ligand_pdbqt: Path,
    output_pdbqt: Path,
    score_rows: Sequence[Mapping[str, Any]],
    *,
    maximum_minimum_rmsd: float,
) -> dict[str, Any]:
    reference = _heavy_pdbqt_coordinates(reference_ligand_pdbqt)
    models = _split_pdbqt_models(output_pdbqt)
    if len(models) != len(score_rows):
        _fail(
            "AD4ZN_MULTISAMPLE_OUTPUT_SCORE_MODE_MISMATCH",
            "Vina MODEL and score row counts differ.",
            details={"models": len(models), "scores": len(score_rows)},
        )
    rows: list[dict[str, Any]] = []
    for index, (model, score) in enumerate(zip(models, score_rows, strict=True), start=1):
        coordinates = _model_heavy_coordinates(model)
        rmsd = _direct_rmsd(reference, coordinates)
        rows.append(
            {
                "mode": index,
                "affinity": float(score["affinity"]),
                "direct_heavy_rmsd_angstrom": rmsd,
            }
        )
    best_score = min(rows, key=lambda item: item["affinity"])
    best_rmsd = min(rows, key=lambda item: item["direct_heavy_rmsd_angstrom"])
    if best_rmsd["direct_heavy_rmsd_angstrom"] > maximum_minimum_rmsd:
        _fail(
            "AD4ZN_MULTISAMPLE_RMSD_DIAGNOSTIC_OUTSIDE_THRESHOLD",
            "No output mode satisfies the loose direct heavy-atom RMSD threshold.",
            details={
                "threshold": maximum_minimum_rmsd,
                "actual": best_rmsd["direct_heavy_rmsd_angstrom"],
            },
        )
    return {
        "method": (
            "same_atom_order_direct_heavy_atom_rmsd_without_alignment_"
            "or_symmetry_correction"
        ),
        "symmetry_corrected": False,
        "alignment_applied": False,
        "reference": "frozen prepared ligand PDBQT heavy-atom order",
        "best_score_mode_is_not_required_to_be_best_rmsd_mode": True,
        "source_heavy_atom_count": len(reference),
        "best_score_mode": best_score,
        "lowest_direct_rmsd_mode": best_rmsd,
        "modes": rows,
    }


def _validate_repeatability(
    first: bytes,
    second: bytes,
    *,
    fixed_windows_expected_sha256: str,
    enforce_fixed_windows_sha256: bool,
) -> dict[str, Any]:
    first_sha = _sha256_bytes(first)
    second_sha = _sha256_bytes(second)
    if first != second or first_sha != second_sha:
        _fail(
            "AD4ZN_MULTISAMPLE_REPEAT_OUTPUT_MISMATCH",
            "Two Vina runs with the same frozen inputs and seed are not byte-identical.",
            details={"first_sha256": first_sha, "second_sha256": second_sha},
        )
    if enforce_fixed_windows_sha256 and first_sha != fixed_windows_expected_sha256:
        _fail(
            "AD4ZN_MULTISAMPLE_WINDOWS_OUTPUT_HASH_MISMATCH",
            "Vina output differs from fixed Windows toolchain evidence.",
            details={
                "expected_sha256": fixed_windows_expected_sha256,
                "actual_sha256": first_sha,
            },
        )
    return {
        "byte_identical": True,
        "sha256": first_sha,
        "fixed_windows_sha256_enforced": enforce_fixed_windows_sha256,
    }


def _load_frozen_maps_manifest(
    project_dir: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    snapshots = metadata.get("snapshots")
    ad4_maps = snapshots.get("ad4_maps") if isinstance(snapshots, Mapping) else None
    manifest_record = (
        ad4_maps.get("manifest") if isinstance(ad4_maps, Mapping) else None
    )
    relative_text = (
        str(manifest_record.get("relative_path") or "")
        if isinstance(manifest_record, Mapping)
        else ""
    )
    relative = Path(relative_text)
    if not relative_text or relative.is_absolute() or ".." in relative.parts:
        _fail(
            "AD4ZN_MULTISAMPLE_FROZEN_MAP_MANIFEST_MISSING",
            "A finished run has no safe frozen maps manifest.",
        )
    path = _regular_file(
        project_dir / relative,
        "frozen AD4Zn maps manifest",
    )
    try:
        path.relative_to(project_dir.resolve(strict=True))
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "AD4ZN_MULTISAMPLE_FROZEN_MAP_MANIFEST_INVALID",
            "A frozen maps manifest cannot be verified.",
            details={"error": str(exc)},
        )
    if (
        not isinstance(payload, dict)
        or path.stat().st_size != int(manifest_record.get("size_bytes") or 0)
        or _sha256(path) != str(manifest_record.get("sha256") or "")
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_FROZEN_MAP_MANIFEST_INVALID",
            "A frozen maps manifest differs from its run snapshot.",
        )
    return payload


def _coverage_report_rows(coverage: Mapping[str, Any]) -> list[str]:
    markers = coverage.get("markers")
    if not isinstance(markers, Mapping):
        return []
    zn = markers.get("ZN")
    tz = markers.get("TZ")
    if not isinstance(zn, Mapping) or not isinstance(tz, Mapping):
        return []

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    return [
        f"| ZN 坐标 | {compact(zn.get('coordinate_angstrom'))} |",
        f"| TZ 坐标 | {compact(tz.get('coordinate_angstrom'))} |",
        (
            "| 请求 Box 边界 | "
            f"{compact(coverage.get('requested_box_bounds_angstrom'))} |"
        ),
        "| ZN/TZ 位于请求 Box | 通过 |",
        "| ZN/TZ 位于实际网格 | 通过 |",
    ]


def _validate_report_evidence(
    report_text: str,
    required_substrings: Sequence[str],
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    required = [str(item) for item in required_substrings]
    required.extend(_coverage_report_rows(coverage))
    missing = [item for item in required if item not in report_text]
    if missing:
        _fail(
            "AD4ZN_MULTISAMPLE_REPORT_EVIDENCE_MISSING",
            "The AD4Zn report omits required protocol or Box evidence.",
            details={"missing": missing},
        )
    return {
        "required_evidence_count": len(required),
        "all_required_evidence_present": True,
    }


def _run_once(
    project_dir: Path,
    vina_detection: Any,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any], Path]:
    with patch(
        "dockstart_core.project.vina_adapter.detect",
        return_value=vina_detection,
    ):
        prepared = _require_ok(
            prepare_vina_run(str(project_dir)),
            "AD4ZN_MULTISAMPLE_RUN_PREPARATION_FAILED",
            "DockStart could not freeze an AD4Zn run.",
        )
        run_id = str(prepared.get("run_id") or "")
        executed = _require_ok(
            execute_prepared_vina_run(str(project_dir), run_id),
            "AD4ZN_MULTISAMPLE_VINA_RUN_FAILED",
            "DockStart could not execute a frozen AD4Zn run.",
        )
    metadata = executed.get("metadata")
    if not isinstance(metadata, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_RUN_METADATA_MISSING",
            "A completed AD4Zn run has no metadata.",
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
            "AD4ZN_MULTISAMPLE_RUN_METADATA_INVALID",
            "A finished run lacks the complete frozen AD4Zn evidence set.",
        )
    output = _regular_file(
        project_dir / str(metadata.get("output_file") or ""),
        "AD4Zn Vina output",
    )
    analyzed = _require_ok(
        analyze_vina_run_results(str(project_dir), run_id),
        "AD4ZN_MULTISAMPLE_RESULT_ANALYSIS_FAILED",
        "DockStart could not analyze a finished AD4Zn run.",
    )
    return run_id, metadata, analyzed, output


def _run_repeatability_and_report(
    system_id: str,
    project_dir: Path,
    vina_detection: Any,
    system: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    fixed_windows_toolchain: bool,
) -> dict[str, Any]:
    _require_ok(
        generate_vina_config(str(project_dir)),
        "AD4ZN_MULTISAMPLE_CONFIG_GENERATION_FAILED",
        f"DockStart could not generate the {system_id} Vina config.",
    )
    first_id, first_metadata, first_analysis, first_output = _run_once(
        project_dir,
        vina_detection,
    )
    second_id, second_metadata, second_analysis, second_output = _run_once(
        project_dir,
        vina_detection,
    )
    result_expected = system.get("result")
    vina_expected = protocol.get("vina")
    if not isinstance(result_expected, Mapping) or not isinstance(vina_expected, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            f"{system_id} result or Vina contract is invalid.",
        )
    repeatability = _validate_repeatability(
        first_output.read_bytes(),
        second_output.read_bytes(),
        fixed_windows_expected_sha256=str(
            result_expected.get("fixed_windows_output_sha256") or ""
        ),
        enforce_fixed_windows_sha256=fixed_windows_toolchain,
    )
    first_scores = _score_rows(first_analysis.get("scores"))
    second_scores = _score_rows(second_analysis.get("scores"))
    minimum_modes = int(vina_expected.get("minimum_output_modes") or 0)
    if (
        len(first_scores) < minimum_modes
        or first_scores != second_scores
        or len(first_scores) != len(_split_pdbqt_models(first_output))
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_SCORE_REPEATABILITY_FAILED",
            f"{system_id} score rows or output modes are not repeatable.",
            details={
                "first_count": len(first_scores),
                "second_count": len(second_scores),
                "minimum": minimum_modes,
            },
        )
    best_affinity = min(row["affinity"] for row in first_scores)
    lower = float(result_expected.get("accepted_best_affinity_min"))
    upper = float(result_expected.get("accepted_best_affinity_max"))
    if not lower <= best_affinity <= upper:
        _fail(
            "AD4ZN_MULTISAMPLE_AFFINITY_OUTSIDE_WINDOW",
            f"{system_id} best affinity is outside the pinned cross-platform window.",
            details={"minimum": lower, "maximum": upper, "actual": best_affinity},
        )
    frozen_manifest = _load_frozen_maps_manifest(project_dir, first_metadata)
    frozen_coverage = _validate_box_coverage(frozen_manifest, system, protocol)
    second_frozen_manifest = _load_frozen_maps_manifest(project_dir, second_metadata)
    second_coverage = _validate_box_coverage(
        second_frozen_manifest,
        system,
        protocol,
    )
    if (
        frozen_coverage["box_coverage_sha256"]
        != second_coverage["box_coverage_sha256"]
    ):
        _fail(
            "AD4ZN_MULTISAMPLE_FROZEN_COVERAGE_NOT_REPEATABLE",
            f"{system_id} frozen Box coverage changed between identical runs.",
        )
    rmsd = _direct_heavy_rmsd_diagnostics(
        _regular_file(
            project_dir / "prepared" / "ligand.pdbqt",
            f"{system_id} frozen prepared ligand",
        ),
        first_output,
        first_scores,
        maximum_minimum_rmsd=float(
            result_expected.get("maximum_minimum_direct_heavy_rmsd_angstrom")
        ),
    )
    report = _require_ok(
        export_markdown_report(str(project_dir), first_id),
        "AD4ZN_MULTISAMPLE_REPORT_EXPORT_FAILED",
        f"DockStart could not export the {system_id} AD4Zn report.",
    )
    report_path = _regular_file(
        project_dir / str(report.get("report_file") or ""),
        f"{system_id} AD4Zn Markdown report",
    )
    report_text = report_path.read_text(encoding="utf-8", errors="strict")
    report_evidence = _validate_report_evidence(
        report_text,
        protocol.get("report_required_substrings")
        if isinstance(protocol.get("report_required_substrings"), list)
        else [],
        frozen_coverage,
    )
    return {
        "run_ids": [first_id, second_id],
        "mode_count": len(first_scores),
        "best_affinity": best_affinity,
        "score_window": [lower, upper],
        "repeatability": repeatability,
        "direct_heavy_rmsd_diagnostic": rmsd,
        "frozen_box_coverage_sha256": frozen_coverage[
            "box_coverage_sha256"
        ],
        "report_file": report.get("report_file"),
        "report_sha256": _sha256(report_path),
        "report_evidence": report_evidence,
        "vina_versions": [
            first_metadata.get("vina_version"),
            second_metadata.get("vina_version"),
        ],
    }


def _run_sample_workflow(
    system_id: str,
    *,
    work_root: Path,
    files: Mapping[str, Path],
    mk_prepare_receptor: Path,
    python_executable: Path,
    autogrid_detection: Any,
    vina_detection: Any,
    manifest: Mapping[str, Any],
    fixed_windows_toolchain: bool,
) -> dict[str, Any]:
    systems = manifest.get("systems")
    protocol = manifest.get("protocol")
    if not isinstance(systems, Mapping) or not isinstance(protocol, Mapping):
        _fail(
            "AD4ZN_MULTISAMPLE_MANIFEST_INVALID",
            "The system or protocol manifest section is invalid.",
        )
    system = systems[system_id]
    system_work = work_root / system_id.lower()
    system_work.mkdir(parents=True, exist_ok=False)
    receptor, receptor_evidence = _prepare_raw_receptor(
        system_id,
        files[str(system["pdb_source_key"])],
        mk_prepare_receptor,
        system_work,
        system,
        fixed_windows_toolchain=fixed_windows_toolchain,
    )
    project_dir, project_evidence = _create_project_and_prepare_ligand(
        system_id,
        system_work,
        receptor,
        files[str(system["ligand_source_key"])],
        system,
        protocol,
        python_executable=python_executable,
        fixed_windows_toolchain=fixed_windows_toolchain,
    )
    ad4zn_evidence = _prepare_ad4zn(
        system_id,
        project_dir,
        files["ad4zn_parameter"],
        system,
        fixed_windows_toolchain=fixed_windows_toolchain,
    )
    maps_evidence = _generate_and_validate_maps(
        system_id,
        project_dir,
        autogrid_detection,
        system,
        protocol,
    )
    result_evidence = _run_repeatability_and_report(
        system_id,
        project_dir,
        vina_detection,
        system,
        protocol,
        fixed_windows_toolchain=fixed_windows_toolchain,
    )
    return {
        "system_id": system_id,
        "raw_receptor_preparation": receptor_evidence,
        "public_ligand_project_setup": project_evidence,
        "ad4zn_preparation": ad4zn_evidence,
        "autogrid_maps": maps_evidence,
        "vina_repeatability_result_report": result_evidence,
    }


def verify_ad4zn_multisample(
    *,
    pdb_2oi0: str | Path,
    pdb_1r1j: str | Path,
    ligand_2oi0_sdf: str | Path,
    ligand_1r1j_sdf: str | Path,
    mk_prepare_receptor: str | Path,
    python_executable: str | Path,
    autogrid_executable: str | Path,
    vina_executable: str | Path,
    ad4zn_dat: str | Path,
) -> dict[str, Any]:
    manifest = _load_manifest()
    supplied_sources = {
        "2oi0_pdb": Path(pdb_2oi0),
        "2oi0_ligand_sdf": Path(ligand_2oi0_sdf),
        "1r1j_pdb": Path(pdb_1r1j),
        "1r1j_ligand_sdf": Path(ligand_1r1j_sdf),
        "ad4zn_parameter": Path(ad4zn_dat),
    }
    steps: dict[str, Any] = {}
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="dockstart-ad4zn-multisample-",
    )
    temporary_root = Path(temporary_handle.name).resolve(strict=True)
    settings_path = temporary_root / "dockstart_settings.json"
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    settings_changed = False
    result_payload: dict[str, Any] | None = None
    caught: AD4ZnMultisampleAcceptanceError | None = None
    try:
        source_holder: dict[str, Path] = {}

        def source_step() -> Mapping[str, Any]:
            resolved, evidence = _verify_source_inputs(
                supplied_sources,
                manifest,
            )
            source_holder.update(resolved)
            return evidence

        _execute_step(steps, "caller_supplied_source_identity", source_step)
        tool_holder: dict[str, Any] = {}

        def tool_step() -> Mapping[str, Any]:
            autogrid_detection, vina_detection, evidence = _verify_external_tools(
                python_executable=Path(python_executable),
                mk_prepare_receptor=Path(mk_prepare_receptor),
                autogrid_executable=Path(autogrid_executable),
                vina_executable=Path(vina_executable),
                manifest=manifest,
            )
            tool_holder["autogrid_detection"] = autogrid_detection
            tool_holder["vina_detection"] = vina_detection
            tool_holder["evidence"] = evidence
            return evidence

        _execute_step(steps, "caller_supplied_tool_identity", tool_step)

        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        settings_changed = True
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    vina=str(Path(vina_executable).expanduser().resolve(strict=True)),
                    python=str(
                        Path(python_executable).expanduser().resolve(strict=True)
                    ),
                    autogrid4=str(
                        Path(autogrid_executable).expanduser().resolve(strict=True)
                    ),
                )
            )
        )
        fixed_windows = bool(
            tool_holder["evidence"].get("fixed_windows_toolchain")
        )
        for system_id in SYSTEM_KEYS:
            _execute_step(
                steps,
                f"{system_id.lower()}_full_project_chain",
                lambda system_id=system_id: _run_sample_workflow(
                    system_id,
                    work_root=temporary_root,
                    files=source_holder,
                    mk_prepare_receptor=Path(mk_prepare_receptor)
                    .expanduser()
                    .resolve(strict=True),
                    python_executable=Path(python_executable)
                    .expanduser()
                    .resolve(strict=True),
                    autogrid_detection=tool_holder["autogrid_detection"],
                    vina_detection=tool_holder["vina_detection"],
                    manifest=manifest,
                    fixed_windows_toolchain=fixed_windows,
                ),
            )
        result_payload = {
            "ok": True,
            "fixture_id": str(manifest.get("fixture_id") or ""),
            "distribution": str(manifest.get("distribution") or ""),
            "verification_scope": (
                "two_distinct_real_single_zinc_public_project_chains"
            ),
            "network_or_download_used": False,
            "fixed_windows_toolchain": fixed_windows,
            "exact_generated_sha256_is_cross_platform_gate": False,
            "steps": steps,
            "error": None,
        }
    except AD4ZnMultisampleAcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve JSON boundary.
        caught = AD4ZnMultisampleAcceptanceError(
            "AD4ZN_MULTISAMPLE_UNEXPECTED_ERROR",
            "The AD4Zn multisample verifier failed unexpectedly.",
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
        except Exception as exc:  # noqa: BLE001
            settings_restore_error = str(exc)
        settings_restored = (
            SETTINGS_ENV_VAR not in os.environ
            if previous_settings_path is None
            else os.environ.get(SETTINGS_ENV_VAR) == previous_settings_path
        )
        settings_evidence = {
            "variable": SETTINGS_ENV_VAR,
            "changed": settings_changed,
            "previously_set": previous_settings_path is not None,
            "isolated_path": str(settings_path),
            "restored": settings_restored,
            "error": settings_restore_error,
        }
        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001
            cleanup_error = str(exc)
        cleanup_evidence = {
            "path": str(temporary_root),
            "cleanup_called": True,
            "removed": not temporary_root.exists(),
            "error": cleanup_error,
        }

    lifecycle = {
        "temporary_cleanup": cleanup_evidence,
        "settings_environment": settings_evidence,
    }
    if caught is not None:
        caught.details = {**caught.details, **lifecycle}
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
        lifecycle_error = AD4ZnMultisampleAcceptanceError(
            "AD4ZN_MULTISAMPLE_TEMPORARY_LIFECYCLE_FAILED",
            "Temporary projects were not removed or settings were not restored.",
            details=lifecycle,
        )
        lifecycle_error.steps = dict(steps)
        raise lifecycle_error
    result_payload.update(lifecycle)
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify DockStart's external 2OI0 and 1R1J AD4Zn project chains. "
            "The command never downloads or installs scientific assets."
        )
    )
    parser.add_argument("--pdb-2oi0", type=Path, required=True)
    parser.add_argument("--pdb-1r1j", type=Path, required=True)
    parser.add_argument("--ligand-2oi0-sdf", type=Path, required=True)
    parser.add_argument("--ligand-1r1j-sdf", type=Path, required=True)
    parser.add_argument("--mk-prepare-receptor", type=Path, required=True)
    parser.add_argument("--python", dest="python_executable", type=Path, required=True)
    parser.add_argument("--autogrid", type=Path, required=True)
    parser.add_argument("--vina", type=Path, required=True)
    parser.add_argument("--ad4zn-dat", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        result = verify_ad4zn_multisample(
            pdb_2oi0=args.pdb_2oi0,
            pdb_1r1j=args.pdb_1r1j,
            ligand_2oi0_sdf=args.ligand_2oi0_sdf,
            ligand_1r1j_sdf=args.ligand_1r1j_sdf,
            mk_prepare_receptor=args.mk_prepare_receptor,
            python_executable=args.python_executable,
            autogrid_executable=args.autogrid,
            vina_executable=args.vina,
            ad4zn_dat=args.ad4zn_dat,
        )
    except AD4ZnMultisampleAcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "ad4zn_multisample_external",
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
