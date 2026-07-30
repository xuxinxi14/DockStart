"""Run the metadata-only 4DM3 simultaneous-two-ligand scientific gate.

DockStart does not distribute the 4DM3 coordinates.  The caller supplies the
manifest-pinned mmCIF plus the chain-A SAH, RCO, and IMD instance SDF files.
The verifier prepares every PDBQT with the pinned RDKit/Meeko toolchain, runs
four DockStart project-level jobs, checks crystal-pose recovery and
repeatability, and removes the temporary project on every exit path.

The verifier never downloads data.  A network connection cannot turn a
missing or mismatched local source into an accepted result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import meeko_adapter, vina_adapter  # noqa: E402
from dockstart_core.multiple_ligands import (  # noqa: E402
    execute_multiple_ligand_run,
    parse_multiple_ligand_output_text,
    prepare_multiple_ligand_run,
)
from dockstart_core.project import (  # noqa: E402
    create_project,
    import_receptor_pdbqt,
    parse_vina_log_text,
    update_box_params,
    update_vina_params,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    save_settings,
)

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "multiple_ligands_4dm3"
    / "source_manifest.json"
)
DEFAULT_PYTHON = REPOSITORY_ROOT / "resources" / "python" / "python.exe"
DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
SDF_PORTABLE_IDENTITY_ALGORITHM = (
    "normalize_crlf_and_cr_to_lf_then_first_molblock_through_m_end_sha256_v1"
)
COORDINATE_IDENTITY_ALGORITHM = (
    "sorted_atom_name_element_xyz_3dp_json_sha256_v1"
)
RMSD_METHOD = (
    "common_receptor_frame_symmetry_aware_no_fit_heavy_atom_rmsd_v1"
)
SOURCE_KEYS = (
    "structure_mmcif",
    "sah_instance_sdf",
    "rco_instance_sdf",
    "imd_instance_sdf",
)
EXPECTED_SCIENTIFIC_BOUNDARIES = (
    (
        "The 4DM3 paper supplies a cooperative fragment context; this gate "
        "does not estimate or decompose cooperativity."
    ),
    (
        "A Vina joint score belongs to the complete RCO plus IMD pose and "
        "cannot be assigned to either member."
    ),
    (
        "Docking scores provide a structural ranking hypothesis and do not "
        "replace experimental validation."
    ),
    (
        "Top-five recovery is a non-blocking ranking diagnostic, not a "
        "DockStart software-correctness gate."
    ),
    (
        "Excluding the deposited pocket waters is a frozen dry-receptor "
        "modelling assumption, not a claim that those waters are unimportant."
    ),
    (
        "The neutral prepared SAH microstate is not presented as an "
        "experimentally unique protonation state."
    ),
    "This simultaneous multiple-ligand protocol remains Experimental.",
)
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])
Coordinate = tuple[float, float, float]
OUTPUT_BOX_TOLERANCE_ANGSTROM = 0.00051
PDBQT_COORDINATE_QUANTIZATION_TOLERANCE_ANGSTROM = 0.000500001
PDBQT_CHARGE_QUANTUM = Decimal("0.001")
EXPECTED_PREPARED_FORMAL_CHARGES = {
    "RCO": 0,
    "IMD": 1,
    "SAH": 0,
}
SCORES_CSV_HEADER = [
    "mode",
    "joint_affinity_kcal_mol",
    "rmsd_lb",
    "rmsd_ub",
    "pose_available",
    "score_scope",
]


class MultipleLigand4dm3AcceptanceError(RuntimeError):
    """Stable, JSON-serializable failure from the 4DM3 acceptance gate."""

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
    raise MultipleLigand4dm3AcceptanceError(
        code,
        message,
        details=details,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_lf_bytes(payload: bytes) -> bytes:
    return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _first_molblock_portable_bytes(payload: bytes) -> bytes:
    """Return the normalized first mol block, excluding volatile SDF fields."""

    if b"\x00" in payload:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_CONTROL_BYTE",
            "An instance SDF contains a NUL byte.",
        )
    canonical = _canonical_lf_bytes(payload)
    try:
        text = canonical.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_ENCODING_INVALID",
            "An instance SDF is not strict UTF-8/ASCII text.",
            details={"error": str(exc)},
        )
    lines = text.splitlines(keepends=True)
    end_index: int | None = None
    for index, line in enumerate(lines):
        if line.rstrip("\n") == "M  END":
            end_index = index
            break
    if end_index is None:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "An instance SDF has no exact M  END record.",
        )
    molblock = "".join(lines[: end_index + 1])
    if not molblock.endswith("\n"):
        molblock += "\n"
    if not molblock.strip():
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "An instance SDF has an empty first molecule block.",
        )
    return molblock.encode("utf-8")


def _load_manifest(
    manifest_path: str | Path = MANIFEST_PATH,
) -> dict[str, Any]:
    path = Path(manifest_path).expanduser()
    if not path.is_file() or path.is_symlink():
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_MISSING",
            "The metadata-only 4DM3 manifest is missing.",
            details={"path": str(path)},
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The metadata-only 4DM3 manifest cannot be parsed.",
            details={"path": str(path), "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 manifest must contain a JSON object.",
        )
    if (
        payload.get("schema_version") != 1
        or payload.get("distribution") != "metadata_only"
        or payload.get("contains_upstream_coordinate_files") is not False
        or payload.get("fixture_id") != "multiple_ligands_4dm3_external"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 manifest does not match the metadata-only schema.",
        )
    for key in (
        "source_files",
        "structure_selection",
        "reference_poses",
        "preparation_contract",
        "box_contract",
        "toolchain",
        "docking_protocol",
        "run_matrix",
        "acceptance",
    ):
        if not isinstance(payload.get(key), (dict, list)):
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                f"The 4DM3 manifest section {key} is missing or invalid.",
            )
    return payload


def _scientific_boundary_contract(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    raw_boundaries = manifest.get("scientific_boundaries")
    if not isinstance(raw_boundaries, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in raw_boundaries
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 scientific-boundary contract is invalid.",
        )
    boundaries = tuple(raw_boundaries)
    if boundaries != EXPECTED_SCIENTIFIC_BOUNDARIES:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The frozen 4DM3 scientific boundaries changed.",
            details={
                "expected": list(EXPECTED_SCIENTIFIC_BOUNDARIES),
                "actual": list(boundaries),
            },
        )
    canonical = _canonical_json_bytes(list(boundaries))
    return {
        "statements": list(boundaries),
        "sha256": _sha256_bytes(canonical),
        "dry_receptor_water_exclusion_is_modelling_assumption": True,
        "neutral_sah_is_not_unique_experimental_microstate": True,
        "protocol_status": "Experimental",
        "per_member_affinity_available": False,
    }


def _regular_local_file(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SOURCE_INVALID",
            f"{label} cannot be resolved.",
            details={"path": str(candidate), "error": str(exc)},
        )
    if (
        candidate.is_symlink()
        or not resolved.is_file()
        or resolved.stat().st_size <= 0
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SOURCE_INVALID",
            f"{label} is missing, empty, or symbolic.",
            details={"path": str(candidate)},
        )
    return resolved


def _source_filename(record: Mapping[str, Any], fallback: str) -> str:
    value = str(record.get("local_filename") or fallback)
    candidate = Path(value)
    if (
        not value
        or candidate.is_absolute()
        or len(candidate.parts) != 1
        or value in {".", ".."}
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "A source record has an unsafe local_filename.",
            details={"local_filename": value},
        )
    return value


def _resolve_source_paths(
    manifest: Mapping[str, Any],
    *,
    source_dir: str | Path | None,
    mmcif_path: str | Path | None,
    sah_sdf_path: str | Path | None,
    rco_sdf_path: str | Path | None,
    imd_sdf_path: str | Path | None,
) -> dict[str, Path]:
    source_files = manifest.get("source_files")
    if not isinstance(source_files, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The source_files section is invalid.",
        )
    explicit = {
        "structure_mmcif": mmcif_path,
        "sah_instance_sdf": sah_sdf_path,
        "rco_instance_sdf": rco_sdf_path,
        "imd_instance_sdf": imd_sdf_path,
    }
    provided_explicit = [value is not None for value in explicit.values()]
    if source_dir is not None and any(provided_explicit):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SOURCE_ARGUMENTS_INVALID",
            "Use --source-dir or four explicit source paths, not both.",
        )
    if source_dir is None and not all(provided_explicit):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SOURCE_ARGUMENTS_INVALID",
            "Four explicit sources are required when --source-dir is omitted.",
        )
    resolved: dict[str, Path] = {}
    if source_dir is not None:
        root = Path(source_dir).expanduser()
        try:
            root = root.resolve(strict=True)
        except OSError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_DIRECTORY_INVALID",
                "The source directory cannot be resolved.",
                details={"path": str(source_dir), "error": str(exc)},
            )
        if Path(source_dir).expanduser().is_symlink() or not root.is_dir():
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_DIRECTORY_INVALID",
                "The source directory must be a plain local directory.",
                details={"path": str(source_dir)},
            )
        fallbacks = {
            "structure_mmcif": "4DM3.cif",
            "sah_instance_sdf": "SAH_A.sdf",
            "rco_instance_sdf": "RCO_A.sdf",
            "imd_instance_sdf": "IMD_A.sdf",
        }
        for key in SOURCE_KEYS:
            record = source_files.get(key)
            if not isinstance(record, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                    f"The source record {key} is invalid.",
                )
            filename = _source_filename(record, fallbacks[key])
            resolved[key] = _regular_local_file(root / filename, key)
            try:
                resolved[key].relative_to(root)
            except ValueError:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_SOURCE_OUTSIDE_DIRECTORY",
                    f"The resolved source {key} is outside --source-dir.",
                )
    else:
        for key, value in explicit.items():
            assert value is not None
            resolved[key] = _regular_local_file(Path(value), key)
    return resolved


def _verify_sources(
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
    *,
    snapshot_root: Path | None = None,
) -> dict[str, Any]:
    source_files = manifest.get("source_files")
    if not isinstance(source_files, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The source_files section is invalid.",
        )
    frozen_root: Path | None = None
    if snapshot_root is not None:
        frozen_root = snapshot_root.resolve(strict=False)
        try:
            frozen_root.mkdir(parents=False, exist_ok=False)
        except OSError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_SNAPSHOT_FAILED",
                "The verified-source snapshot directory could not be created.",
                details={"path": str(frozen_root), "error": str(exc)},
            )
        if frozen_root.is_symlink() or not frozen_root.is_dir():
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_SNAPSHOT_FAILED",
                "The verified-source snapshot directory is unsafe.",
                details={"path": str(frozen_root)},
            )

    def read_once(path: Path, key: str) -> bytes:
        try:
            before = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or path.is_symlink():
                raise OSError("source is not a plain regular file")
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                payload = handle.read()
            after = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_CHANGED_DURING_VERIFICATION",
                f"The supplied {key} changed while it was being verified.",
                details={"path": str(path), "error": str(exc)},
            )
        identity_before = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_size),
        )
        identity_opened = (
            int(opened.st_dev),
            int(opened.st_ino),
            int(opened.st_size),
        )
        identity_after = (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
        )
        if (
            identity_before != identity_opened
            or identity_opened != identity_after
            or len(payload) != int(opened.st_size)
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_CHANGED_DURING_VERIFICATION",
                f"The supplied {key} changed while it was being verified.",
                details={"path": str(path)},
            )
        return payload

    def freeze(
        key: str,
        filename: str,
        payload: bytes,
        expected_sha256: str,
    ) -> Path | None:
        if frozen_root is None:
            return None
        target = frozen_root / filename
        try:
            with target.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            actual_size = target.stat().st_size
            actual_sha256 = _sha256(target)
        except OSError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_SNAPSHOT_FAILED",
                f"The verified {key} snapshot could not be written.",
                details={"path": str(target), "error": str(exc)},
            )
        if (
            target.is_symlink()
            or actual_size != len(payload)
            or actual_sha256 != expected_sha256
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_SNAPSHOT_FAILED",
                f"The verified {key} snapshot failed its byte audit.",
                details={
                    "path": str(target),
                    "expected_size_bytes": len(payload),
                    "actual_size_bytes": actual_size,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                },
            )
        return target

    evidence: dict[str, Any] = {}
    snapshot_paths: dict[str, str] = {}
    for key in SOURCE_KEYS:
        path = paths.get(key)
        record = source_files.get(key)
        if path is None or not isinstance(record, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                f"The source contract for {key} is incomplete.",
            )
        raw_payload = read_once(path, key)
        raw_size = len(raw_payload)
        raw_sha = _sha256_bytes(raw_payload)
        if key == "structure_mmcif":
            identity = record.get("content_identity")
            if not isinstance(identity, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                    "The structure mmCIF lacks content_identity.",
                )
            expected_size = int(identity.get("size_bytes") or 0)
            expected_sha = str(identity.get("sha256") or "").lower()
            if (
                identity.get("algorithm") != "raw_bytes_sha256_v1"
                or
                expected_size <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                or raw_size != expected_size
                or raw_sha != expected_sha
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_SOURCE_HASH_MISMATCH",
                    "The supplied 4DM3 mmCIF does not match the pinned bytes.",
                    details={
                        "key": key,
                        "expected_size_bytes": expected_size,
                        "actual_size_bytes": raw_size,
                        "expected_sha256": expected_sha,
                        "actual_sha256": raw_sha,
                    },
                )
            snapshot = freeze(
                key,
                "4DM3.cif",
                raw_payload,
                raw_sha,
            )
            if snapshot is not None:
                snapshot_paths[key] = str(snapshot)
            evidence[key] = {
                "source_path": str(path),
                "size_bytes": raw_size,
                "sha256": raw_sha,
                "identity": "exact_bytes_sha256",
                "snapshot_path": str(snapshot or ""),
            }
            continue
        identity = record.get("content_identity")
        if not isinstance(identity, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                f"The source record {key} lacks portable_identity.",
            )
        portable = _first_molblock_portable_bytes(raw_payload)
        expected_algorithm = str(identity.get("algorithm") or "")
        expected_size = int(identity.get("size_bytes") or 0)
        expected_sha = str(identity.get("sha256") or "").lower()
        portable_sha = _sha256_bytes(portable)
        if (
            expected_algorithm != SDF_PORTABLE_IDENTITY_ALGORITHM
            or expected_size <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            or len(portable) != expected_size
            or portable_sha != expected_sha
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SOURCE_HASH_MISMATCH",
                f"The supplied {key} first molecule does not match the pin.",
                details={
                    "key": key,
                    "algorithm": expected_algorithm,
                    "expected_size_bytes": expected_size,
                    "actual_size_bytes": len(portable),
                    "expected_sha256": expected_sha,
                    "actual_sha256": portable_sha,
                },
            )
        frozen_names = {
            "sah_instance_sdf": "SAH_A.sdf",
            "rco_instance_sdf": "RCO_A.sdf",
            "imd_instance_sdf": "IMD_A.sdf",
        }
        snapshot = freeze(
            key,
            frozen_names[key],
            portable,
            portable_sha,
        )
        if snapshot is not None:
            snapshot_paths[key] = str(snapshot)
        evidence[key] = {
            "source_path": str(path),
            "raw_size_bytes": raw_size,
            "raw_sha256": raw_sha,
            "portable_identity": {
                "algorithm": expected_algorithm,
                "size_bytes": len(portable),
                "sha256": portable_sha,
            },
            "snapshot_path": str(snapshot or ""),
        }
    return {
        "network_or_download_used": False,
        "files": evidence,
        "snapshot_paths": snapshot_paths,
    }


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    try:
        result = action()
    except MultipleLigand4dm3AcceptanceError as exc:
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
    except Exception as exc:  # noqa: BLE001
        wrapped = MultipleLigand4dm3AcceptanceError(
            "MULTIPLE_LIGAND_4DM3_STEP_UNEXPECTED_ERROR",
            f"The 4DM3 acceptance step {name} failed unexpectedly.",
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


def _require_api_ok(name: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    if result.get("ok") is True:
        return result
    error = result.get("error")
    detail = error if isinstance(error, Mapping) else {}
    _fail(
        "MULTIPLE_LIGAND_4DM3_PROJECT_API_FAILED",
        f"DockStart public project API step {name} failed.",
        details={
            "api_step": name,
            "project_error_code": str(detail.get("code") or ""),
            "project_error": dict(detail),
        },
    )
    raise AssertionError("unreachable")


_STRUCTURE_AUDIT_SCRIPT = r'''
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import gemmi


def altloc_text(value):
    text = str(value or "")
    return "" if not text or text == "\x00" else text.strip()


def atom_record(atom):
    return {
        "name": str(atom.name).strip(),
        "element": str(atom.element.name).strip().upper(),
        "altloc": altloc_text(atom.altloc),
        "occupancy": round(float(atom.occ), 6),
        "xyz": [
            round(float(atom.pos.x), 6),
            round(float(atom.pos.y), 6),
            round(float(atom.pos.z), 6),
        ],
    }


def coordinate_identity(records):
    canonical = [
        [
            item["name"],
            item["element"],
            *[f"{float(value):.3f}" for value in item["xyz"]],
        ]
        for item in records
    ]
    canonical.sort(key=lambda item: (item[0], item[1], item[2:]))
    payload = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: audit.py INPUT.cif PROTEIN_A.pdb AUDIT.json")
    source = Path(sys.argv[1])
    protein_pdb = Path(sys.argv[2])
    audit_json = Path(sys.argv[3])
    structure = gemmi.read_structure(str(source))
    models = list(structure)
    if len(models) != 1:
        raise RuntimeError(f"expected one model, found {len(models)}")
    model = models[0]
    chains = list(model)
    if sorted(chain.name for chain in chains) != ["A", "B"]:
        raise RuntimeError("expected author chains A and B")

    total_atoms = sum(len(residue) for chain in chains for residue in chain)
    chain_counts = {
        chain.name: sum(len(residue) for residue in chain)
        for chain in chains
    }
    polymer_a = []
    polymer_b_heavy = []
    water_a = []
    selected = {}
    for chain in chains:
        for residue in chain:
            if (
                chain.name == "A"
                and residue.entity_type == gemmi.EntityType.Polymer
            ):
                polymer_a.append(residue)
            if (
                chain.name == "B"
                and residue.entity_type == gemmi.EntityType.Polymer
            ):
                polymer_b_heavy.extend(
                    atom_record(atom)
                    for atom in residue
                    if str(atom.element.name).strip().upper() != "H"
                )
            if (
                chain.name == "A"
                and residue.entity_type == gemmi.EntityType.Water
            ):
                water_a.append(residue)
            if chain.name == "A" and residue.name in {"SAH", "RCO", "IMD"}:
                key = f"{residue.name}:{residue.subchain}:{residue.seqid.num}"
                selected[key] = [atom_record(atom) for atom in residue]

    temporary_full = protein_pdb.with_suffix(".full.pdb")
    structure.write_pdb(str(temporary_full))
    full_lines = temporary_full.read_text(
        encoding="utf-8",
        errors="strict",
    ).splitlines()
    protein_lines = [
        line
        for line in full_lines
        if line.startswith("ATOM") and line[21:22].strip() == "A"
    ]
    temporary_full.unlink()
    if len(protein_lines) != sum(len(residue) for residue in polymer_a):
        raise RuntimeError("Gemmi PDB bridge lost chain-A polymer atoms")
    protein_pdb.write_text(
        "\n".join(protein_lines)
        + f"\nTER   {len(protein_lines) + 1:5d}\nEND\n",
        encoding="utf-8",
        newline="\n",
    )

    sah_key = "SAH:C:2001"
    rco_key = "RCO:D:2002"
    imd_key = "IMD:E:2003"
    for key in (sah_key, rco_key, imd_key):
        if key not in selected:
            raise RuntimeError(f"missing target component {key}")
    rco_all = selected[rco_key]
    rco_by_altloc = {
        altloc: [item for item in rco_all if item["altloc"] == altloc]
        for altloc in sorted({item["altloc"] for item in rco_all})
    }
    imd = selected[imd_key]
    targets = [
        item
        for items in rco_by_altloc.values()
        for item in items
    ] + imd
    minimum_b_distance = min(
        math.dist(target["xyz"], atom["xyz"])
        for target in targets
        for atom in polymer_b_heavy
    )
    reference = {
        "SAH": selected[sah_key],
        "RCO": rco_by_altloc,
        "IMD": imd,
    }
    identities = {
        "SAH": coordinate_identity(reference["SAH"]),
        "RCO": {
            key: coordinate_identity(value)
            for key, value in reference["RCO"].items()
        },
        "IMD": coordinate_identity(reference["IMD"]),
    }
    payload = {
        "gemmi_version": str(gemmi.__version__),
        "model_id": str(model.num),
        "total_atom_site_rows": total_atoms,
        "chain_atom_counts": chain_counts,
        "chain_a_polymer_residue_count": len(polymer_a),
        "chain_a_polymer_atom_count": sum(len(residue) for residue in polymer_a),
        "chain_a_water_count": len(water_a),
        "selected_component_atom_counts": {
            "SAH": len(selected[sah_key]),
            "RCO": len(selected[rco_key]),
            "IMD": len(selected[imd_key]),
        },
        "retained_mmcif_atom_count": (
            sum(len(residue) for residue in polymer_a)
            + len(selected[sah_key])
        ),
        "excluded_chain_b_atom_count": chain_counts["B"],
        "minimum_target_to_chain_b_polymer_heavy_distance_angstrom": (
            minimum_b_distance
        ),
        "protein_pdb_atom_count": len(protein_lines),
        "reference": reference,
        "coordinate_identities": identities,
    }
    audit_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
'''


_RDKIT_ADD_H_SCRIPT = r'''
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from rdkit import Chem, rdBase


def main():
    if len(sys.argv) not in {4, 5}:
        raise SystemExit(
            "usage: add_h.py INPUT.sdf OUTPUT_H.sdf TOPOLOGY.json "
            "[HEAVY_COORDINATE_OVERRIDE.json]"
        )
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    topology_path = Path(sys.argv[3])
    payload = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    text = payload.decode("utf-8", errors="strict")
    lines = text.splitlines(keepends=True)
    end = next(
        index for index, line in enumerate(lines)
        if line.rstrip("\n") == "M  END"
    )
    molblock = "".join(lines[: end + 1])
    molecule = Chem.MolFromMolBlock(
        molblock,
        sanitize=True,
        removeHs=False,
        strictParsing=True,
    )
    if molecule is None:
        raise RuntimeError("RDKit rejected the pinned first molecule block")
    if molecule.GetNumConformers() != 1:
        raise RuntimeError("the source molecule must have one conformer")
    if any(atom.GetAtomicNum() == 1 for atom in molecule.GetAtoms()):
        raise RuntimeError("the source instance SDF must contain heavy atoms only")
    conformer = molecule.GetConformer()
    source_coordinates = []
    for atom in molecule.GetAtoms():
        position = conformer.GetAtomPosition(atom.GetIdx())
        xyz = [float(position.x), float(position.y), float(position.z)]
        if not all(math.isfinite(value) for value in xyz):
            raise RuntimeError("the source SDF has a non-finite coordinate")
        source_coordinates.append(
            {
                "source_index": atom.GetIdx() + 1,
                "element": atom.GetSymbol().upper(),
                "atomic_number": atom.GetAtomicNum(),
                "xyz": xyz,
            }
        )
    prepared_coordinates = [
        list(item["xyz"]) for item in source_coordinates
    ]
    if len(sys.argv) == 5:
        override_payload = json.loads(
            Path(sys.argv[4]).read_text(encoding="utf-8")
        )
        if (
            not isinstance(override_payload, list)
            or len(override_payload) != molecule.GetNumAtoms()
        ):
            raise RuntimeError("heavy-coordinate override has the wrong shape")
        prepared_coordinates = []
        for index, value in enumerate(override_payload):
            if not isinstance(value, list) or len(value) != 3:
                raise RuntimeError("heavy-coordinate override is incomplete")
            xyz = [float(item) for item in value]
            if not all(math.isfinite(item) for item in xyz):
                raise RuntimeError("heavy-coordinate override is non-finite")
            conformer.SetAtomPosition(index, xyz)
            prepared_coordinates.append(xyz)
    automorphisms = molecule.GetSubstructMatches(
        molecule,
        uniquify=False,
        useChirality=True,
        maxMatches=4096,
    )
    if not automorphisms:
        raise RuntimeError("RDKit returned no graph automorphism")
    unique_automorphisms = sorted(
        {
            tuple(int(value) for value in automorphism)
            for automorphism in automorphisms
        }
    )
    identity = tuple(range(molecule.GetNumAtoms()))
    if identity not in unique_automorphisms:
        raise RuntimeError("the graph automorphism set lacks the identity")

    with_hydrogens = Chem.AddHs(molecule, addCoords=True)
    with_hydrogens.GetConformer().Set3D(True)
    writer = Chem.SDWriter(str(output))
    writer.SetKekulize(True)
    writer.write(with_hydrogens)
    writer.close()
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("RDKit did not write the explicit-H SDF")

    topology = {
        "rdkit_version": str(rdBase.rdkitVersion),
        "heavy_atom_count": molecule.GetNumAtoms(),
        "atom_count_with_hydrogens": with_hydrogens.GetNumAtoms(),
        "explicit_h_conformer_is_3d": bool(
            with_hydrogens.GetConformer().Is3D()
        ),
        "formal_charge": int(
            sum(atom.GetFormalCharge() for atom in molecule.GetAtoms())
        ),
        "canonical_isomeric_smiles": Chem.MolToSmiles(
            molecule,
            isomericSmiles=True,
            canonical=True,
        ),
        "source_coordinates": source_coordinates,
        "prepared_heavy_coordinates": prepared_coordinates,
        "heavy_coordinate_override_applied": len(sys.argv) == 5,
        "automorphisms": [
            list(automorphism) for automorphism in unique_automorphisms
        ],
        "bond_count": molecule.GetNumBonds(),
    }
    topology_path.write_text(
        json.dumps(topology, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
'''


def _run_preparation(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    code: str,
    message: str,
) -> dict[str, Any]:
    try:
        completed = meeko_adapter.run_preparation_command(
            command,
            cwd,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(
            code,
            message,
            details={"command": command, "error": str(exc)},
        )
    if completed.returncode != 0:
        _fail(
            code,
            message,
            details={
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _tool_versions(
    python_executable: Path,
    cwd: Path,
) -> dict[str, str]:
    probe = (
        "import json, gemmi, meeko; "
        "from rdkit import rdBase; "
        "print(json.dumps({"
        "'python':__import__('platform').python_version(),"
        "'rdkit':str(rdBase.rdkitVersion),"
        "'meeko':str(getattr(meeko,'__version__','')),"
        "'gemmi':str(getattr(gemmi,'__version__',''))"
        "},sort_keys=True))"
    )
    evidence = _run_preparation(
        [str(python_executable), "-I", "-B", "-c", probe],
        cwd=cwd,
        timeout=60,
        code="MULTIPLE_LIGAND_4DM3_PYTHON_TOOLCHAIN_FAILED",
        message="The configured scientific Python toolchain could not run.",
    )
    try:
        payload = json.loads(str(evidence["stdout"]).strip())
    except json.JSONDecodeError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PYTHON_TOOLCHAIN_FAILED",
            "The scientific Python version probe returned invalid JSON.",
            details={"stdout": evidence["stdout"], "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PYTHON_TOOLCHAIN_FAILED",
            "The scientific Python version probe returned a non-object.",
        )
    return {str(key): str(value) for key, value in payload.items()}


def _validated_versions(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    toolchain = manifest.get("toolchain")
    if not isinstance(toolchain, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The toolchain section is invalid.",
        )
    packages = toolchain.get("packages")
    python_runtime = toolchain.get("python_runtime")
    vina = toolchain.get("vina")
    if (
        not isinstance(packages, Mapping)
        or not isinstance(python_runtime, Mapping)
        or not isinstance(vina, Mapping)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The pinned toolchain records are invalid.",
        )
    return {
        "python": str(python_runtime.get("version") or ""),
        "rdkit": str(packages.get("rdkit") or ""),
        "meeko": str(packages.get("meeko") or ""),
        "gemmi": str(packages.get("gemmi") or ""),
        "vina": str(vina.get("version") or ""),
    }


def _verify_toolchain(
    python_executable: Path,
    vina_executable: Path,
    manifest: Mapping[str, Any],
    work_root: Path,
) -> dict[str, Any]:
    python_path = _regular_local_file(python_executable, "Python executable")
    vina_path = _regular_local_file(vina_executable, "Vina executable")
    versions = _tool_versions(python_path, work_root)
    expected = _validated_versions(manifest)
    aliases = {
        "rdkit": ("rdkit", "RDKit"),
        "meeko": ("meeko", "Meeko"),
        "gemmi": ("gemmi", "Gemmi"),
    }
    for actual_key, manifest_keys in aliases.items():
        expected_value = ""
        for manifest_key in manifest_keys:
            if manifest_key in expected:
                expected_value = str(expected.get(manifest_key) or "")
                break
        if not expected_value or versions.get(actual_key) != expected_value:
            _fail(
                "MULTIPLE_LIGAND_4DM3_TOOL_VERSION_MISMATCH",
                f"The pinned {actual_key} version is not active.",
                details={
                    "tool": actual_key,
                    "expected": expected_value,
                    "actual": versions.get(actual_key, ""),
                },
            )
    toolchain = manifest.get("toolchain")
    python_record = (
        toolchain.get("python_runtime")
        if isinstance(toolchain, Mapping)
        else None
    )
    if not isinstance(python_record, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The Python runtime identity record is invalid.",
        )
    python_expected = {
        "version": str(python_record.get("version") or ""),
        "size_bytes": int(python_record.get("size_bytes") or 0),
        "sha256": str(python_record.get("sha256") or "").lower(),
    }
    python_actual = {
        "version": versions.get("python", ""),
        "size_bytes": python_path.stat().st_size,
        "sha256": _sha256(python_path),
    }
    if python_actual != python_expected:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PYTHON_BINARY_MISMATCH",
            "The scientific Python runtime does not match the validated binary.",
            details={"expected": python_expected, "actual": python_actual},
        )
    detection = vina_adapter.detect(
        configured_path=str(vina_path),
        bundled_path=str(vina_path),
    )
    if (
        detection.status != "ok"
        or Path(str(detection.path or "")).resolve(strict=False) != vina_path
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_VINA_DETECTION_FAILED",
            "The exact caller-supplied Vina executable was not detected.",
            details={
                "status": detection.status,
                "path": detection.path,
                "version": detection.version,
                "raw_error": detection.raw_error,
            },
        )
    expected_vina = str(
        expected.get("vina")
        or expected.get("Vina")
        or expected.get("autodock_vina")
        or ""
    )
    if detection.version != expected_vina:
        _fail(
            "MULTIPLE_LIGAND_4DM3_TOOL_VERSION_MISMATCH",
            "The pinned Vina version is not active.",
            details={
                "tool": "vina",
                "expected": expected_vina,
                "actual": detection.version,
            },
        )
    feature = (
        ((detection.capabilities or {}).get("features") or {}).get(
            "multiple_ligands"
        )
        if isinstance(detection.capabilities, dict)
        else None
    )
    if not isinstance(feature, Mapping) or feature.get("supported") is not True:
        _fail(
            "MULTIPLE_LIGAND_4DM3_VINA_CAPABILITY_MISSING",
            "The supplied Vina does not expose simultaneous ligands.",
            details={"capability": feature},
        )
    actual_vina_sha = _sha256(vina_path)
    reference = (
        toolchain.get("vina") if isinstance(toolchain, Mapping) else None
    )
    if not isinstance(reference, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The Vina executable identity record is invalid.",
        )
    expected_vina_identity = {
        "size_bytes": int(reference.get("size_bytes") or 0),
        "sha256": str(reference.get("sha256") or "").lower(),
    }
    actual_vina_identity = {
        "size_bytes": vina_path.stat().st_size,
        "sha256": actual_vina_sha,
    }
    if actual_vina_identity != expected_vina_identity:
        _fail(
            "MULTIPLE_LIGAND_4DM3_VINA_BINARY_MISMATCH",
            "The Vina executable does not match the validated binary.",
            details={
                "expected": expected_vina_identity,
                "actual": actual_vina_identity,
            },
        )
    return {
        "python": {
            "path": str(python_path),
            "size_bytes": python_path.stat().st_size,
            "sha256": python_actual["sha256"],
            "versions": versions,
        },
        "vina": {
            "path": str(vina_path),
            "size_bytes": vina_path.stat().st_size,
            "sha256": actual_vina_sha,
            "version": detection.version,
            "multiple_ligands_capability": dict(feature),
        },
    }


def _coordinate_identity(records: Sequence[Mapping[str, Any]]) -> str:
    canonical = [
        [
            str(item.get("name") or ""),
            str(item.get("element") or "").upper(),
            *[
                f"{float(value):.3f}"
                for value in list(item.get("xyz") or [])
            ],
        ]
        for item in records
    ]
    if any(len(item) != 5 for item in canonical):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "A reference atom does not have three coordinates.",
        )
    canonical.sort(
        key=lambda item: (item[0], item[1], item[2:])
    )
    return _sha256_bytes(
        json.dumps(
            canonical,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _selection_expected(
    selection: Mapping[str, Any],
    *keys: str,
) -> Any:
    current: Any = selection
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _verify_structure_audit(
    audit: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    selection = manifest.get("structure_selection")
    references = manifest.get("reference_poses")
    if not isinstance(selection, Mapping) or not isinstance(references, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The structure/reference contract is invalid.",
        )
    expected_facts = {
        "model_id": str(
            selection.get("model_number")
            or selection.get("model_id")
            or ""
        ),
        "chain_a_polymer_residue_count": int(
            selection.get("receptor_polymer_residue_count")
            or _selection_expected(
                selection,
                "receptor",
                "polymer",
                "residue_count",
            )
            or 0
        ),
        "chain_a_polymer_atom_count": int(
            selection.get("receptor_polymer_atom_count")
            or _selection_expected(
                selection,
                "receptor",
                "polymer",
                "atom_site_count",
            )
            or 0
        ),
        "chain_a_water_count": int(
            selection.get("excluded_chain_a_water_count")
            or _selection_expected(
                selection,
                "excluded",
                "author_chain_A_water_atom_site_count",
            )
            or 0
        ),
        "retained_mmcif_atom_count": int(
            selection.get("retained_atom_count")
            or _selection_expected(
                selection,
                "receptor",
                "retained_atom_site_count",
            )
            or 0
        ),
        "excluded_chain_b_atom_count": int(
            selection.get("excluded_chain_b_atom_count")
            or _selection_expected(
                selection,
                "excluded",
                "author_chain_B_all_atom_site_count",
            )
            or 0
        ),
        "total_atom_site_rows": int(
            selection.get("total_atom_site_rows")
            or _selection_expected(
                selection,
                "atom_accounting",
                "source_total_atom_site_count",
            )
            or 0
        ),
    }
    actual_facts = {
        key: (
            str(audit.get(key) or "")
            if key == "model_id"
            else int(audit.get(key) or 0)
        )
        for key in expected_facts
    }
    mismatches = {
        key: {"expected": expected, "actual": actual_facts[key]}
        for key, expected in expected_facts.items()
        if expected != actual_facts[key]
    }
    component_counts = audit.get("selected_component_atom_counts")
    component_counts = (
        component_counts if isinstance(component_counts, Mapping) else {}
    )
    expected_component_counts = {"SAH": 26, "RCO": 16, "IMD": 5}
    for key, expected in expected_component_counts.items():
        actual = int(component_counts.get(key) or 0)
        if actual != expected:
            mismatches[f"component_{key}_atom_count"] = {
                "expected": expected,
                "actual": actual,
            }
    expected_minimum = float(
        selection.get(
            "minimum_target_to_chain_b_polymer_heavy_distance_angstrom",
            _selection_expected(
                selection,
                "same_protomer_guard",
                "minimum_author_chain_B_polymer_to_reference_ligand_distance_angstrom",
            )
            or 17.76553,
        )
    )
    actual_minimum = float(
        audit.get(
            "minimum_target_to_chain_b_polymer_heavy_distance_angstrom",
            math.nan,
        )
    )
    tolerance = float(
        selection.get("coordinate_tolerance_angstrom") or 0.00001
    )
    if (
        not math.isfinite(actual_minimum)
        or abs(actual_minimum - expected_minimum) > tolerance
    ):
        mismatches["minimum_target_to_chain_b_distance"] = {
            "expected": expected_minimum,
            "actual": actual_minimum,
            "tolerance": tolerance,
        }
    coordinate_identity_contract = references.get("coordinate_identity")
    method = (
        str(coordinate_identity_contract.get("algorithm") or "")
        if isinstance(coordinate_identity_contract, Mapping)
        else ""
    )
    if method != COORDINATE_IDENTITY_ALGORITHM:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The reference coordinate identity algorithm is invalid.",
        )
    identities = audit.get("coordinate_identities")
    identities = identities if isinstance(identities, Mapping) else {}
    for component in ("RCO", "IMD"):
        expected_record = (
            references.get(component)
            or references.get(component.lower())
        )
        if not isinstance(expected_record, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                f"The {component} reference contract is missing.",
            )
        if component == "RCO":
            conformers = expected_record.get("conformers")
            expected_altlocs = (
                {
                    str(item.get("altloc") or ""): item
                    for item in conformers
                    if isinstance(item, Mapping)
                }
                if isinstance(conformers, list)
                else expected_record.get("accepted_altlocs")
            )
            actual_altlocs = identities.get("RCO")
            if not isinstance(expected_altlocs, Mapping) or not isinstance(
                actual_altlocs, Mapping
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                    "The RCO alternate-location contract is invalid.",
                )
            for altloc in ("A", "B"):
                record = expected_altlocs.get(altloc)
                if not isinstance(record, Mapping):
                    _fail(
                        "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                        f"The RCO altloc {altloc} contract is missing.",
                    )
                expected_sha = str(
                    record.get("coordinate_sha256")
                    or record.get("coordinate_identity_sha256")
                    or ""
                )
                actual_sha = str(actual_altlocs.get(altloc) or "")
                if expected_sha != actual_sha:
                    mismatches[f"RCO_altloc_{altloc}_identity"] = {
                        "expected": expected_sha,
                        "actual": actual_sha,
                    }
                reference_records = (
                    audit.get("reference", {}).get("RCO", {}).get(altloc)
                    if isinstance(audit.get("reference"), Mapping)
                    else None
                )
                occupancies = {
                    round(float(item.get("occupancy")), 6)
                    for item in reference_records or []
                    if isinstance(item, Mapping)
                }
                expected_occupancy = float(record.get("occupancy") or 0)
                if occupancies != {expected_occupancy}:
                    mismatches[f"RCO_altloc_{altloc}_occupancy"] = {
                        "expected": expected_occupancy,
                        "actual": sorted(occupancies),
                    }
            reference_records_all = (
                audit.get("reference", {}).get("RCO", {})
                if isinstance(audit.get("reference"), Mapping)
                else {}
            )
            rco_occupancy = {
                altloc: min(
                    float(item.get("occupancy"))
                    for item in reference_records_all.get(altloc, [])
                )
                for altloc in ("A", "B")
                if reference_records_all.get(altloc)
            }
            if (
                set(rco_occupancy) != {"A", "B"}
                or rco_occupancy["B"] <= rco_occupancy["A"]
            ):
                mismatches["RCO_highest_occupancy_altloc"] = {
                    "expected": "B",
                    "actual_occupancies": rco_occupancy,
                }
        else:
            expected_sha = str(
                expected_record.get("coordinate_sha256")
                or expected_record.get("coordinate_identity_sha256")
                or ""
            )
            actual_sha = str(identities.get("IMD") or "")
            if expected_sha != actual_sha:
                mismatches["IMD_identity"] = {
                    "expected": expected_sha,
                    "actual": actual_sha,
                }
    if mismatches:
        _fail(
            "MULTIPLE_LIGAND_4DM3_STRUCTURE_SELECTION_MISMATCH",
            "The supplied 4DM3 structure does not match the audited selection.",
            details={"mismatches": mismatches},
        )
    return {
        "selection_verified": True,
        "model_id": actual_facts["model_id"],
        "chain_a_polymer_residue_count": actual_facts[
            "chain_a_polymer_residue_count"
        ],
        "chain_a_polymer_atom_count": actual_facts[
            "chain_a_polymer_atom_count"
        ],
        "rigid_sah_atom_count": 26,
        "retained_mmcif_atom_count": actual_facts[
            "retained_mmcif_atom_count"
        ],
        "excluded_chain_b_atom_count": actual_facts[
            "excluded_chain_b_atom_count"
        ],
        "minimum_target_to_chain_b_polymer_heavy_distance_angstrom": (
            actual_minimum
        ),
        "reference_coordinate_identities": identities,
    }


def _prepare_structure(
    python_executable: Path,
    mmcif_path: Path,
    work_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    helper = work_root / "audit_4dm3_structure.py"
    protein_pdb = work_root / "4DM3_chain_A_polymer.pdb"
    audit_json = work_root / "4DM3_structure_audit.json"
    helper.write_text(
        _STRUCTURE_AUDIT_SCRIPT,
        encoding="utf-8",
        newline="\n",
    )
    command_evidence = _run_preparation(
        [
            str(python_executable),
            "-I",
            "-B",
            str(helper),
            str(mmcif_path),
            str(protein_pdb),
            str(audit_json),
        ],
        cwd=work_root,
        timeout=180,
        code="MULTIPLE_LIGAND_4DM3_STRUCTURE_AUDIT_FAILED",
        message="Gemmi could not build the audited chain-A polymer bridge.",
    )
    try:
        audit = json.loads(audit_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_STRUCTURE_AUDIT_FAILED",
            "The Gemmi structure audit is missing or invalid.",
            details={"error": str(exc)},
        )
    if not isinstance(audit, dict):
        _fail(
            "MULTIPLE_LIGAND_4DM3_STRUCTURE_AUDIT_FAILED",
            "The Gemmi structure audit is not a JSON object.",
        )
    selection_evidence = _verify_structure_audit(audit, manifest)
    return protein_pdb, audit, {
        **selection_evidence,
        "protein_pdb": {
            "size_bytes": protein_pdb.stat().st_size,
            "sha256": _sha256(protein_pdb),
        },
        "command": command_evidence,
    }


def _load_json_object(path: Path, code: str, message: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(code, message, details={"path": str(path), "error": str(exc)})
    if not isinstance(payload, dict):
        _fail(code, message, details={"path": str(path)})
    return payload


def _v2000_heavy_atom_records(source_sdf: Path) -> list[dict[str, Any]]:
    """Read the pinned legacy molfile atom table needed for coordinate mapping.

    RCSB ModelServer currently emits the original pre-version-tag counts line
    for these instance SDF files.  That is the same fixed-column atom-table
    dialect as V2000, but the literal ``V2000`` suffix is absent.  Accept that
    legacy form while rejecting any explicitly different molfile version.
    """

    molblock = _first_molblock_portable_bytes(
        source_sdf.read_bytes()
    ).decode("utf-8")
    lines = molblock.splitlines()
    if len(lines) < 4:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "The pinned instance SDF is missing its legacy counts line.",
            details={"path": str(source_sdf)},
        )
    version_match = re.search(r"\bV\d{4}\b", lines[3].upper())
    if version_match is not None and version_match.group(0) != "V2000":
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "The pinned instance SDF must use a legacy or V2000 molecule block.",
            details={
                "path": str(source_sdf),
                "molfile_version": version_match.group(0),
            },
        )
    try:
        atom_count = int(lines[3][0:3].strip())
    except ValueError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "The V2000 atom count cannot be parsed.",
            details={"path": str(source_sdf), "error": str(exc)},
        )
    if atom_count <= 0 or len(lines) < 4 + atom_count:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
            "The V2000 atom table is incomplete.",
            details={"path": str(source_sdf), "atom_count": atom_count},
        )
    records: list[dict[str, Any]] = []
    for source_index, line in enumerate(
        lines[4 : 4 + atom_count],
        start=1,
    ):
        try:
            xyz = (
                float(line[0:10].strip()),
                float(line[10:20].strip()),
                float(line[20:30].strip()),
            )
            element = line[31:34].strip().upper()
        except (ValueError, IndexError) as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
                "A V2000 atom record cannot be parsed.",
                details={
                    "path": str(source_sdf),
                    "source_index": source_index,
                    "line": line,
                    "error": str(exc),
                },
            )
        if not element or element == "H" or not all(
            math.isfinite(value) for value in xyz
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
                "The pinned instance SDF must contain finite heavy atoms only.",
                details={
                    "path": str(source_sdf),
                    "source_index": source_index,
                    "element": element,
                },
            )
        records.append(
            {
                "source_index": source_index,
                "element": element,
                "xyz": xyz,
            }
        )
    return records


def _rco_altloc_b_coordinate_override(
    source_sdf: Path,
    audit: Mapping[str, Any],
) -> tuple[list[Coordinate], list[str]]:
    """Map pinned RCO alt-A atoms to names, then substitute alt-B positions."""

    reference = audit.get("reference")
    rco = (
        reference.get("RCO")
        if isinstance(reference, Mapping)
        else None
    )
    if not isinstance(rco, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "The structure audit lacks RCO alternate locations.",
        )
    alt_a = rco.get("A")
    alt_b = rco.get("B")
    if not isinstance(alt_a, list) or not isinstance(alt_b, list):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "RCO alternate locations A and B are required.",
        )
    source_records = _v2000_heavy_atom_records(source_sdf)
    names = _match_source_to_reference(
        {"source_coordinates": source_records},
        alt_a,
        label="RCO pinned SDF altloc A",
    )
    by_name_b = {
        str(record.get("name") or ""): record
        for record in alt_b
        if isinstance(record, Mapping)
    }
    if len(by_name_b) != len(alt_b):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
            "RCO altloc B has duplicate atom names.",
        )
    override: list[Coordinate] = []
    for source_record, name in zip(source_records, names, strict=True):
        target = by_name_b.get(name)
        if not isinstance(target, Mapping) or str(
            target.get("element") or ""
        ).upper() != str(source_record["element"]).upper():
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"RCO altloc B atom {name} is missing or changed element.",
            )
        xyz = tuple(float(value) for value in target.get("xyz") or [])
        if len(xyz) != 3:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"RCO altloc B atom {name} lacks coordinates.",
            )
        override.append(xyz)
    return override, names


def _parse_index_map(pdbqt_path: Path) -> dict[int, int]:
    pairs: list[int] = []
    for line in pdbqt_path.read_text(
        encoding="utf-8",
        errors="strict",
    ).splitlines():
        if line.startswith("REMARK INDEX MAP "):
            try:
                pairs.extend(
                    int(value) for value in line.split()[3:]
                )
            except ValueError as exc:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
                    "A prepared PDBQT INDEX MAP is malformed.",
                    details={"path": str(pdbqt_path), "error": str(exc)},
                )
    if not pairs or len(pairs) % 2:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
            "A prepared PDBQT lacks a complete INDEX MAP.",
            details={"path": str(pdbqt_path)},
        )
    mapping = {
        pairs[index]: pairs[index + 1]
        for index in range(0, len(pairs), 2)
    }
    if len(mapping) != len(pairs) // 2 or len(set(mapping.values())) != len(
        mapping
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
            "A prepared PDBQT INDEX MAP is not one-to-one.",
            details={"path": str(pdbqt_path)},
        )
    return mapping


def _pdbqt_atom_records_from_text(
    text: str,
    *,
    label: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        try:
            record_type = line[0:6].strip()
            serial = int(line[6:11].strip())
            resnum = int(line[22:26].strip())
            xyz = (
                float(line[30:38].strip()),
                float(line[38:46].strip()),
                float(line[46:54].strip()),
            )
            fields = line.split()
            charge_text = (
                line[70:76].strip()
                if len(line) >= 76 and line[70:76].strip()
                else fields[-2]
            )
            atom_type_text = (
                line[77:].strip().split()[0]
                if len(line) > 77 and line[77:].strip()
                else fields[-1]
            )
            atom_type = atom_type_text.upper()
            charge_decimal = Decimal(charge_text)
            if (
                not charge_decimal.is_finite()
                or charge_decimal.quantize(PDBQT_CHARGE_QUANTUM)
                != charge_decimal
            ):
                raise ValueError(
                    "partial charge is not finite three-decimal PDBQT data"
                )
            charge_milliunits = int(charge_decimal * Decimal(1000))
            charge = float(charge_decimal)
        except (ValueError, IndexError, InvalidOperation) as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_PDBQT_ATOM_INVALID",
                "A prepared PDBQT atom record is invalid.",
                details={"path": label, "line": line, "error": str(exc)},
            )
        if not all(math.isfinite(value) for value in (*xyz, charge)):
            _fail(
                "MULTIPLE_LIGAND_4DM3_PDBQT_ATOM_INVALID",
                "A prepared PDBQT atom has a non-finite value.",
                details={"path": label, "line": line},
            )
        records.append(
            {
                "record_type": record_type,
                "serial": serial,
                "name": line[12:16].strip() or f"X{serial}",
                "resname": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "resnum": resnum,
                "insertion_code": line[26:27].strip(),
                "xyz": xyz,
                "charge": charge,
                "charge_text": format(charge_decimal, "f"),
                "charge_milliunits": charge_milliunits,
                "atom_type": atom_type,
                "is_hydrogen": atom_type in {"H", "HD", "HS"},
            }
        )
    if not records or len({item["serial"] for item in records}) != len(
        records
    ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_PDBQT_ATOM_INVALID",
                "A prepared PDBQT has no unique atom records.",
                details={"path": label},
            )
    return records


def _pdbqt_atom_records(path: Path) -> list[dict[str, Any]]:
    return _pdbqt_atom_records_from_text(
        path.read_text(encoding="utf-8", errors="strict"),
        label=str(path),
    )


def _pdb_atom_records(path: Path) -> list[dict[str, Any]]:
    """Parse fixed-column PDB identity needed for receptor preservation."""

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        try:
            record_type = line[0:6].strip()
            serial = int(line[6:11].strip())
            name = line[12:16].strip()
            resnum = int(line[22:26].strip())
            xyz = (
                float(line[30:38].strip()),
                float(line[38:46].strip()),
                float(line[46:54].strip()),
            )
            element = line[76:78].strip().upper()
            if not element:
                atom_name = re.sub(r"^[0-9]+", "", name).upper()
                element = atom_name[:1]
            if not name or not element:
                raise ValueError("atom name or element is missing")
        except (ValueError, IndexError) as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_SOURCE_ATOM_INVALID",
                "The chain-A PDB bridge contains an invalid atom record.",
                details={"path": str(path), "line": line, "error": str(exc)},
            )
        if not all(math.isfinite(value) for value in xyz):
            _fail(
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_SOURCE_ATOM_INVALID",
                "The chain-A PDB bridge contains a non-finite coordinate.",
                details={"path": str(path), "line": line},
            )
        records.append(
            {
                "record_type": record_type,
                "serial": serial,
                "name": name,
                "resname": line[17:20].strip(),
                "chain": line[21:22].strip(),
                "resnum": resnum,
                "insertion_code": line[26:27].strip(),
                "xyz": xyz,
                "element": element,
                "is_hydrogen": element in {"H", "D", "T"},
            }
        )
    if not records or len({item["serial"] for item in records}) != len(
        records
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_SOURCE_ATOM_INVALID",
            "The chain-A PDB bridge has no unique atom records.",
            details={"path": str(path)},
        )
    return records


def _receptor_atom_identity(
    record: Mapping[str, Any],
) -> tuple[str, int, str, str, str]:
    return (
        str(record.get("chain") or ""),
        int(record.get("resnum") or 0),
        str(record.get("insertion_code") or ""),
        str(record.get("resname") or ""),
        str(record.get("name") or ""),
    )


def _audit_receptor_heavy_atom_bijection(
    source_pdb: Path,
    prepared_pdbqt: Path,
) -> dict[str, Any]:
    """Prove identity and coordinate preservation across Meeko preparation."""

    source_records = _pdb_atom_records(source_pdb)
    if any(record["record_type"] != "ATOM" for record in source_records):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_SELECTION_CHANGED",
            "The receptor PDB bridge contains non-polymer atom records.",
            details={"path": str(source_pdb)},
        )
    prepared_records = _pdbqt_atom_records(prepared_pdbqt)
    source_heavy = [
        record for record in source_records if not record["is_hydrogen"]
    ]
    prepared_heavy = [
        record for record in prepared_records if not record["is_hydrogen"]
    ]
    if any(record["record_type"] != "ATOM" for record in prepared_heavy):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_HEAVY_ATOM_BIJECTION_FAILED",
            "Meeko introduced a non-polymer heavy atom into the protein PDBQT.",
            details={"path": str(prepared_pdbqt)},
        )

    def identity_map(
        records: Sequence[Mapping[str, Any]],
        *,
        source: str,
    ) -> dict[tuple[str, int, str, str, str], Mapping[str, Any]]:
        mapped: dict[
            tuple[str, int, str, str, str],
            Mapping[str, Any],
        ] = {}
        duplicates: list[tuple[str, int, str, str, str]] = []
        for record in records:
            identity = _receptor_atom_identity(record)
            if identity in mapped:
                duplicates.append(identity)
            mapped[identity] = record
        if duplicates:
            _fail(
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_HEAVY_ATOM_BIJECTION_FAILED",
                "The receptor heavy-atom identity key is not unique.",
                details={
                    "source": source,
                    "duplicates": [list(item) for item in duplicates[:20]],
                    "duplicate_count": len(duplicates),
                },
            )
        return mapped

    source_by_identity = identity_map(source_heavy, source="source_pdb")
    prepared_by_identity = identity_map(
        prepared_heavy,
        source="prepared_pdbqt",
    )
    missing = sorted(set(source_by_identity) - set(prepared_by_identity))
    extra = sorted(set(prepared_by_identity) - set(source_by_identity))
    if missing or extra:
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_HEAVY_ATOM_BIJECTION_FAILED",
            "Meeko did not preserve a one-to-one protein heavy-atom identity map.",
            details={
                "missing_count": len(missing),
                "extra_count": len(extra),
                "missing": [list(item) for item in missing[:20]],
                "extra": [list(item) for item in extra[:20]],
            },
        )

    maximum_axis_delta = 0.0
    correspondence: list[dict[str, Any]] = []
    for identity in sorted(source_by_identity):
        source_record = source_by_identity[identity]
        prepared_record = prepared_by_identity[identity]
        source_xyz = tuple(float(value) for value in source_record["xyz"])
        prepared_xyz = tuple(
            float(value) for value in prepared_record["xyz"]
        )
        axis_deltas = [
            abs(left - right)
            for left, right in zip(source_xyz, prepared_xyz, strict=True)
        ]
        maximum_axis_delta = max(maximum_axis_delta, *axis_deltas)
        if any(
            value > PDBQT_COORDINATE_QUANTIZATION_TOLERANCE_ANGSTROM
            for value in axis_deltas
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_COORDINATES_CHANGED",
                "Meeko changed a protein heavy-atom coordinate beyond PDBQT rounding.",
                details={
                    "identity": list(identity),
                    "source_xyz": list(source_xyz),
                    "prepared_xyz": list(prepared_xyz),
                    "absolute_axis_deltas_angstrom": axis_deltas,
                    "tolerance_angstrom": (
                        PDBQT_COORDINATE_QUANTIZATION_TOLERANCE_ANGSTROM
                    ),
                },
            )
        correspondence.append(
            {
                "identity": list(identity),
                "source_xyz": [
                    format(value, ".6f") for value in source_xyz
                ],
                "prepared_xyz": [
                    format(value, ".6f") for value in prepared_xyz
                ],
            }
        )

    source_identities = [
        list(identity) for identity in sorted(source_by_identity)
    ]
    prepared_identities = [
        list(identity) for identity in sorted(prepared_by_identity)
    ]
    return {
        "method": (
            "fixed_column_identity_bijection_and_per_axis_"
            "pdbqt_3dp_rounding_v1"
        ),
        "identity_fields": [
            "chain",
            "resnum",
            "insertion_code",
            "resname",
            "atom_name",
        ],
        "source_record_type": "ATOM",
        "source_atom_count": len(source_records),
        "source_hydrogen_atom_count": (
            len(source_records) - len(source_heavy)
        ),
        "source_heavy_atom_count": len(source_heavy),
        "prepared_atom_count": len(prepared_records),
        "prepared_hydrogen_atom_count": (
            len(prepared_records) - len(prepared_heavy)
        ),
        "hydrogen_policy": (
            "hydrogens may be added during preparation and are excluded "
            "from the heavy-atom bijection"
        ),
        "prepared_heavy_atom_count": len(prepared_heavy),
        "missing_heavy_atom_count": 0,
        "extra_heavy_atom_count": 0,
        "coordinate_comparison": "absolute_delta_per_cartesian_axis",
        "coordinate_tolerance_angstrom": (
            PDBQT_COORDINATE_QUANTIZATION_TOLERANCE_ANGSTROM
        ),
        "maximum_absolute_axis_delta_angstrom": maximum_axis_delta,
        "source_identity_sha256": _sha256_bytes(
            _canonical_json_bytes(source_identities)
        ),
        "prepared_identity_sha256": _sha256_bytes(
            _canonical_json_bytes(prepared_identities)
        ),
        "correspondence_sha256": _sha256_bytes(
            _canonical_json_bytes(correspondence)
        ),
    }


def _audit_prepared_partial_charge(
    label: str,
    topology: Mapping[str, Any],
    atom_records: Sequence[Mapping[str, Any]],
    *,
    path: Path,
) -> dict[str, Any]:
    """Audit formal-charge conservation after three-decimal PDBQT output."""

    normalized_label = label.upper()
    if normalized_label not in EXPECTED_PREPARED_FORMAL_CHARGES:
        _fail(
            "MULTIPLE_LIGAND_4DM3_FORMAL_CHARGE_CONTRACT_MISMATCH",
            "The prepared molecule has no frozen formal-charge contract.",
            details={"label": label},
        )
    expected_formal_charge = EXPECTED_PREPARED_FORMAL_CHARGES[
        normalized_label
    ]
    topology_formal_charge = topology.get("formal_charge")
    if (
        isinstance(topology_formal_charge, bool)
        or not isinstance(topology_formal_charge, int)
        or topology_formal_charge != expected_formal_charge
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FORMAL_CHARGE_CONTRACT_MISMATCH",
            f"The {normalized_label} topology formal charge changed.",
            details={
                "expected_formal_charge": expected_formal_charge,
                "topology_formal_charge": topology_formal_charge,
            },
        )
    if not atom_records:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_CHARGE_MISMATCH",
            f"The prepared {normalized_label} PDBQT has no charge records.",
            details={"path": str(path)},
        )
    try:
        observed_milliunits = sum(
            int(record["charge_milliunits"]) for record in atom_records
        )
    except (KeyError, TypeError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_CHARGE_MISMATCH",
            f"The prepared {normalized_label} PDBQT charge evidence is incomplete.",
            details={"path": str(path), "error": str(exc)},
        )
    expected_milliunits = expected_formal_charge * 1000
    absolute_error_milliunits = abs(
        observed_milliunits - expected_milliunits
    )
    atom_count = len(atom_records)
    # Each PDBQT charge is rounded independently to 0.001 e. The exact
    # worst-case error after summation is atom_count * 0.0005 e.
    passes_quantization_bound = (
        2 * absolute_error_milliunits <= atom_count
    )
    tolerance = atom_count / 2000.0
    if not passes_quantization_bound:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_CHARGE_MISMATCH",
            (
                f"The prepared {normalized_label} partial charges do not "
                "conserve formal charge."
            ),
            details={
                "path": str(path),
                "expected_formal_charge_e": expected_formal_charge,
                "observed_partial_charge_sum_e": observed_milliunits / 1000.0,
                "absolute_error_e": absolute_error_milliunits / 1000.0,
                "three_decimal_quantization_tolerance_e": tolerance,
                "prepared_atom_count": atom_count,
            },
        )
    return {
        "method": "sum_all_pdbqt_partial_charges_3dp_quantization_bound_v1",
        "label_contract": normalized_label,
        "topology_formal_charge_e": topology_formal_charge,
        "expected_formal_charge_e": expected_formal_charge,
        "prepared_atom_count": atom_count,
        "pdbqt_charge_decimal_places": 3,
        "per_atom_maximum_rounding_error_e": 0.0005,
        "total_quantization_tolerance_e": tolerance,
        "observed_partial_charge_sum_e": observed_milliunits / 1000.0,
        "absolute_error_e": absolute_error_milliunits / 1000.0,
        "passed": True,
    }


def _prepared_source_atom_contract(
    mapping: Mapping[int, int],
    atom_records: Sequence[Mapping[str, Any]],
    heavy_atom_count: int,
    *,
    path: Path,
) -> list[dict[str, Any]]:
    """Freeze source-index identity as a PDBQT structural-record ordinal.

    Vina preserves the member atom identity sequence but does not copy Meeko's
    ``REMARK INDEX MAP`` into output poses.  The input map is therefore
    converted once into structural-record ordinals and atom type/charge
    identities.  Output coordinates can then be mapped without using names,
    coordinates, or a missing output remark.
    """

    ordinal_by_serial = {
        int(record["serial"]): ordinal
        for ordinal, record in enumerate(atom_records)
    }
    contract: list[dict[str, Any]] = []
    for source_index in range(1, heavy_atom_count + 1):
        serial = mapping.get(source_index)
        ordinal = ordinal_by_serial.get(int(serial or 0))
        if ordinal is None:
            _fail(
                "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
                "A prepared PDBQT INDEX MAP points to a missing heavy atom.",
                details={
                    "path": str(path),
                    "source_index": source_index,
                    "pdbqt_serial": serial,
                },
            )
        record = atom_records[ordinal]
        if bool(record.get("is_hydrogen")):
            _fail(
                "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
                "A prepared PDBQT heavy source index maps to hydrogen.",
                details={
                    "path": str(path),
                    "source_index": source_index,
                    "pdbqt_serial": serial,
                },
            )
        contract.append(
            {
                "source_index": source_index,
                "pdbqt_atom_ordinal_zero_based": ordinal,
                "prepared_pdbqt_serial": int(serial),
                "atom_type": str(record.get("atom_type") or ""),
                "charge": float(record.get("charge") or 0.0),
            }
        )
    return contract


def _pdbqt_torsion_counts(path: Path) -> dict[str, int]:
    branch_count = 0
    torsdof_values: list[int] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        if line.startswith("BRANCH "):
            branch_count += 1
        if line.startswith("TORSDOF "):
            try:
                torsdof_values.append(int(line.split()[1]))
            except (IndexError, ValueError) as exc:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_PDBQT_TORSION_INVALID",
                    "A prepared PDBQT TORSDOF record is invalid.",
                    details={"path": str(path), "line": line, "error": str(exc)},
                )
    if len(torsdof_values) != 1:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_TORSION_INVALID",
            "A prepared ligand PDBQT must contain one TORSDOF record.",
            details={"path": str(path), "count": len(torsdof_values)},
        )
    return {
        "branch_count": branch_count,
        "torsdof": torsdof_values[0],
    }


def _prepare_ligand(
    label: str,
    source_sdf: Path,
    python_executable: Path,
    work_root: Path,
    manifest: Mapping[str, Any],
    *,
    heavy_coordinate_override: Sequence[Coordinate] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    helper = work_root / f"rdkit_add_h_{label.lower()}.py"
    explicit_h_sdf = work_root / f"{label}_A_explicit_H.sdf"
    topology_path = work_root / f"{label}_A_topology.json"
    output_pdbqt = work_root / f"{label}_A.pdbqt"
    helper.write_text(
        _RDKIT_ADD_H_SCRIPT,
        encoding="utf-8",
        newline="\n",
    )
    rdkit_command = [
            str(python_executable),
            "-I",
            "-B",
            str(helper),
            str(source_sdf),
            str(explicit_h_sdf),
            str(topology_path),
        ]
    override_path: Path | None = None
    if heavy_coordinate_override is not None:
        override_path = work_root / f"{label}_A_heavy_coordinate_override.json"
        override_path.write_text(
            json.dumps(
                [
                    [float(value) for value in coordinate]
                    for coordinate in heavy_coordinate_override
                ],
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        rdkit_command.append(str(override_path))
    rdkit_evidence = _run_preparation(
        rdkit_command,
        cwd=work_root,
        timeout=120,
        code="MULTIPLE_LIGAND_4DM3_RDKIT_PREPARATION_FAILED",
        message=f"RDKit could not deterministically add coordinates for {label}.",
    )
    meeko_command = meeko_adapter.build_module_command(
        str(python_executable),
        meeko_adapter.MEEKO_LIGAND_MODULE,
        [
            "-i",
            str(explicit_h_sdf),
            "-o",
            str(output_pdbqt),
            "--add_index_map",
            "--rename_atoms",
        ],
    )
    meeko_evidence = _run_preparation(
        meeko_command,
        cwd=work_root,
        timeout=180,
        code="MULTIPLE_LIGAND_4DM3_MEEKO_LIGAND_FAILED",
        message=f"Meeko 0.7.1 could not prepare {label}.",
    )
    topology = _load_json_object(
        topology_path,
        "MULTIPLE_LIGAND_4DM3_RDKIT_PREPARATION_FAILED",
        f"The RDKit topology evidence for {label} is invalid.",
    )
    override_applied = topology.get("heavy_coordinate_override_applied")
    if override_applied is not (heavy_coordinate_override is not None):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RDKIT_PREPARATION_FAILED",
            f"The {label} heavy-coordinate override evidence is inconsistent.",
        )
    expected = _validated_versions(manifest)
    expected_rdkit = str(
        expected.get("rdkit") or expected.get("RDKit") or ""
    )
    if str(topology.get("rdkit_version") or "") != expected_rdkit:
        _fail(
            "MULTIPLE_LIGAND_4DM3_TOOL_VERSION_MISMATCH",
            f"The RDKit helper for {label} used an unexpected version.",
            details={
                "expected": expected_rdkit,
                "actual": topology.get("rdkit_version"),
            },
        )
    mapping = _parse_index_map(output_pdbqt)
    atom_records = _pdbqt_atom_records(output_pdbqt)
    partial_charge_audit = _audit_prepared_partial_charge(
        label,
        topology,
        atom_records,
        path=output_pdbqt,
    )
    torsions = _pdbqt_torsion_counts(output_pdbqt)
    heavy_count = int(topology.get("heavy_atom_count") or 0)
    missing_heavy = [
        index for index in range(1, heavy_count + 1) if index not in mapping
    ]
    if missing_heavy:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PDBQT_INDEX_MAP_INVALID",
            f"Meeko omitted heavy atom mappings for {label}.",
            details={"missing_source_indices": missing_heavy},
        )
    topology["prepared_pdbqt_atom_count"] = len(atom_records)
    topology["prepared_pdbqt_partial_charge_audit"] = partial_charge_audit
    topology["prepared_pdbqt_source_atom_contract"] = (
        _prepared_source_atom_contract(
            mapping,
            atom_records,
            heavy_count,
            path=output_pdbqt,
        )
    )
    return output_pdbqt, topology, {
        "label": label,
        "source_sdf": {
            "size_bytes": source_sdf.stat().st_size,
            "sha256": _sha256(source_sdf),
        },
        "explicit_h_sdf": {
            "size_bytes": explicit_h_sdf.stat().st_size,
            "sha256": _sha256(explicit_h_sdf),
        },
        "pdbqt": {
            "size_bytes": output_pdbqt.stat().st_size,
            "sha256": _sha256(output_pdbqt),
            "atom_count": len(atom_records),
            "heavy_atom_count": heavy_count,
            "index_map_count": len(mapping),
            "partial_charge_audit": partial_charge_audit,
            **torsions,
        },
        "topology": {
            "canonical_isomeric_smiles": topology.get(
                "canonical_isomeric_smiles"
            ),
            "formal_charge": topology.get("formal_charge"),
            "prepared_pdbqt_partial_charge_audit": partial_charge_audit,
            "heavy_atom_count": heavy_count,
            "atom_count_with_hydrogens": topology.get(
                "atom_count_with_hydrogens"
            ),
            "explicit_h_conformer_is_3d": topology.get(
                "explicit_h_conformer_is_3d"
            ),
            "automorphism_count": len(topology.get("automorphisms") or []),
            "heavy_coordinate_override_applied": topology.get(
                "heavy_coordinate_override_applied"
            ),
        },
        "commands": {
            "rdkit_add_h": rdkit_evidence,
            "meeko": meeko_evidence,
        },
    }


def _match_source_to_reference(
    topology: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    label: str,
    tolerance: float = 0.002,
) -> list[str]:
    source = topology.get("source_coordinates")
    if not isinstance(source, list) or len(source) != len(records):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
            f"The {label} source/reference atom counts differ.",
            details={
                "source_count": len(source) if isinstance(source, list) else 0,
                "reference_count": len(records),
            },
        )
    unused = set(range(len(records)))
    names: list[str] = []
    for source_record in source:
        if not isinstance(source_record, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"The {label} source-coordinate record is invalid.",
            )
        element = str(source_record.get("element") or "").upper()
        xyz = tuple(float(value) for value in source_record.get("xyz") or [])
        if len(xyz) != 3:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"The {label} source-coordinate record is incomplete.",
            )
        matches = [
            index
            for index in unused
            if str(records[index].get("element") or "").upper() == element
            and math.dist(
                xyz,
                tuple(float(value) for value in records[index].get("xyz") or []),
            )
            <= tolerance
        ]
        if len(matches) != 1:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"The {label} source atom cannot be matched uniquely.",
                details={
                    "source_record": dict(source_record),
                    "candidate_count": len(matches),
                    "tolerance_angstrom": tolerance,
                },
            )
        selected = matches[0]
        unused.remove(selected)
        names.append(str(records[selected].get("name") or ""))
    if unused or len(set(names)) != len(names):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
            f"The {label} reference mapping is not one-to-one.",
        )
    return names


def _ordered_reference_coordinates(
    topology: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    source_atom_names: Sequence[str],
) -> list[Coordinate]:
    by_name = {str(item.get("name") or ""): item for item in records}
    if len(by_name) != len(records):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
            "A reference pose has duplicate atom names.",
        )
    coordinates: list[Coordinate] = []
    source = topology.get("source_coordinates")
    if not isinstance(source, list):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
            "The ligand topology lacks source coordinates.",
        )
    for source_record, name in zip(source, source_atom_names, strict=True):
        record = by_name.get(name)
        if not isinstance(record, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"Reference atom {name} is missing.",
            )
        if str(record.get("element") or "").upper() != str(
            source_record.get("element") or ""
        ).upper():
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"Reference atom {name} changed element.",
            )
        xyz = tuple(float(value) for value in record.get("xyz") or [])
        if len(xyz) != 3:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REFERENCE_MAPPING_FAILED",
                f"Reference atom {name} lacks coordinates.",
            )
        coordinates.append(xyz)
    return coordinates


def _prepare_reference_coordinates(
    audit: Mapping[str, Any],
    topologies: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    reference = audit.get("reference")
    if not isinstance(reference, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "The structure audit lacks reference poses.",
        )
    sah_records = reference.get("SAH")
    rco_records = reference.get("RCO")
    imd_records = reference.get("IMD")
    if (
        not isinstance(sah_records, list)
        or not isinstance(rco_records, Mapping)
        or not isinstance(imd_records, list)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "The structure audit reference-pose shape is invalid.",
        )
    if set(rco_records) != {"A", "B"}:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
            "RCO must expose exactly alternate locations A and B.",
            details={"actual_altlocs": sorted(rco_records)},
        )
    sah_names = _match_source_to_reference(
        topologies["SAH"],
        sah_records,
        label="SAH",
    )
    rco_names = _match_source_to_reference(
        topologies["RCO"],
        rco_records["A"],
        label="RCO altloc A",
    )
    imd_names = _match_source_to_reference(
        topologies["IMD"],
        imd_records,
        label="IMD",
    )
    return {
        "source_atom_names": {
            "SAH": sah_names,
            "RCO": rco_names,
            "IMD": imd_names,
        },
        "RCO": {
            altloc: _ordered_reference_coordinates(
                topologies["RCO"],
                records,
                rco_names,
            )
            for altloc, records in rco_records.items()
        },
        "IMD": _ordered_reference_coordinates(
            topologies["IMD"],
            imd_records,
            imd_names,
        ),
    }


def _verify_prepared_input_coordinates(
    prepared_paths: Mapping[str, Path],
    topologies: Mapping[str, Mapping[str, Any]],
    references: Mapping[str, Any],
) -> dict[str, Any]:
    expected = {
        "SAH": [
            tuple(float(value) for value in item.get("xyz") or [])
            for item in (
                topologies["SAH"].get("source_coordinates")
                if isinstance(
                    topologies["SAH"].get("source_coordinates"),
                    list,
                )
                else []
            )
        ],
        "RCO": list(references["RCO"]["B"]),
        "IMD": list(references["IMD"]),
    }
    evidence: dict[str, Any] = {}
    for label in ("SAH", "RCO", "IMD"):
        observed = _heavy_coordinates_from_member(
            prepared_paths[label],
            topologies[label],
        )
        target = expected[label]
        if len(observed) != len(target):
            _fail(
                "MULTIPLE_LIGAND_4DM3_PREPARED_COORDINATES_CHANGED",
                f"The prepared {label} heavy-atom count changed.",
            )
        displacements = [
            math.dist(left, right)
            for left, right in zip(observed, target, strict=True)
        ]
        maximum = max(displacements, default=math.inf)
        # PDBQT stores three decimal places; 0.0015 Å covers only rounding.
        if not math.isfinite(maximum) or maximum > 0.0015:
            _fail(
                "MULTIPLE_LIGAND_4DM3_PREPARED_COORDINATES_CHANGED",
                f"Meeko changed the pinned {label} heavy-atom coordinates.",
                details={
                    "maximum_displacement_angstrom": maximum,
                    "rounding_tolerance_angstrom": 0.0015,
                },
            )
        evidence[label] = {
            "coordinate_source": (
                "mmCIF RCO altloc B"
                if label == "RCO"
                else "pinned instance SDF"
            ),
            "heavy_atom_count": len(observed),
            "maximum_pdbqt_rounding_displacement_angstrom": maximum,
        }
    return evidence


def _add_imd_vina_resonance_symmetry(
    topology: dict[str, Any],
    imd_pdbqt: Path,
) -> dict[str, Any]:
    """Add the Vina-equivalent IMD resonance relabeling after verifying it."""

    heavy_count = int(topology.get("heavy_atom_count") or 0)
    if heavy_count != 5:
        _fail(
            "MULTIPLE_LIGAND_4DM3_IMD_SYMMETRY_INVALID",
            "The pinned IMD topology must contain five heavy atoms.",
            details={"heavy_atom_count": heavy_count},
        )
    permutation = (2, 1, 0, 4, 3)
    mapping = _parse_index_map(imd_pdbqt)
    atoms = {
        int(record["serial"]): record
        for record in _pdbqt_atom_records(imd_pdbqt)
    }
    source_atoms = [atoms.get(mapping.get(index, -1)) for index in range(1, 6)]
    if any(record is None for record in source_atoms):
        _fail(
            "MULTIPLE_LIGAND_4DM3_IMD_SYMMETRY_INVALID",
            "The IMD PDBQT lacks a mapped heavy atom.",
        )
    equivalence_pairs = ((0, 2), (3, 4))
    for left, right in equivalence_pairs:
        left_record = source_atoms[left]
        right_record = source_atoms[right]
        assert left_record is not None and right_record is not None
        if (
            left_record["atom_type"] != right_record["atom_type"]
            or abs(
                float(left_record["charge"]) - float(right_record["charge"])
            )
            > 0.001
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_IMD_SYMMETRY_INVALID",
                "The IMD resonance relabeling is not Vina-type/charge equivalent.",
                details={
                    "left_source_index": left + 1,
                    "right_source_index": right + 1,
                    "left_type": left_record["atom_type"],
                    "right_type": right_record["atom_type"],
                    "left_charge": left_record["charge"],
                    "right_charge": right_record["charge"],
                },
            )
    automorphisms = {
        tuple(int(value) for value in item)
        for item in topology.get("automorphisms") or []
        if isinstance(item, list)
    }
    automorphisms.add(tuple(range(5)))
    automorphisms.add(permutation)
    topology["automorphisms"] = [
        list(value) for value in sorted(automorphisms)
    ]
    topology["vina_resonance_symmetry"] = {
        "permutation_zero_based": list(permutation),
        "verified_equivalent_source_index_pairs_one_based": [[1, 3], [4, 5]],
        "equivalence_basis": "prepared_pdbqt_atom_type_and_charge_0.001",
    }
    return dict(topology["vina_resonance_symmetry"])


def _add_named_vina_symmetry(
    topology: dict[str, Any],
    pdbqt_path: Path,
    source_atom_names: Sequence[str],
    name_permutation: Mapping[str, str],
    *,
    label: str,
) -> dict[str, Any]:
    """Convert a frozen crystal atom-name mapping to source-index symmetry."""

    heavy_count = int(topology.get("heavy_atom_count") or 0)
    if (
        heavy_count <= 0
        or len(source_atom_names) != heavy_count
        or len(set(source_atom_names)) != heavy_count
        or set(name_permutation) != set(source_atom_names)
        or set(str(value) for value in name_permutation.values())
        != set(source_atom_names)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_EXPLICIT_SYMMETRY_INVALID",
            f"The explicit {label} atom-name symmetry is not a bijection.",
            details={
                "source_atom_names": list(source_atom_names),
                "name_permutation": dict(name_permutation),
            },
        )
    index_by_name = {
        name: index for index, name in enumerate(source_atom_names)
    }
    permutation = tuple(
        index_by_name[str(name_permutation[name])]
        for name in source_atom_names
    )
    mapping = _parse_index_map(pdbqt_path)
    atom_by_serial = {
        int(record["serial"]): record
        for record in _pdbqt_atom_records(pdbqt_path)
    }
    source_atoms = [
        atom_by_serial.get(mapping.get(index, -1))
        for index in range(1, heavy_count + 1)
    ]
    if any(record is None for record in source_atoms):
        _fail(
            "MULTIPLE_LIGAND_4DM3_EXPLICIT_SYMMETRY_INVALID",
            f"The prepared {label} PDBQT lacks an INDEX MAP heavy atom.",
        )
    for source_index, target_index in enumerate(permutation):
        left = source_atoms[source_index]
        right = source_atoms[target_index]
        assert left is not None and right is not None
        if (
            left["atom_type"] != right["atom_type"]
            or abs(float(left["charge"]) - float(right["charge"])) > 0.001
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_EXPLICIT_SYMMETRY_INVALID",
                f"The explicit {label} mapping is not Vina-type/charge equivalent.",
                details={
                    "source_name": source_atom_names[source_index],
                    "target_name": source_atom_names[target_index],
                    "source_type": left["atom_type"],
                    "target_type": right["atom_type"],
                    "source_charge": left["charge"],
                    "target_charge": right["charge"],
                },
            )
    automorphisms = {
        tuple(int(value) for value in item)
        for item in topology.get("automorphisms") or []
        if isinstance(item, list)
    }
    automorphisms.add(tuple(range(heavy_count)))
    automorphisms.add(permutation)
    topology["automorphisms"] = [
        list(value) for value in sorted(automorphisms)
    ]
    evidence = {
        "source_atom_names": list(source_atom_names),
        "name_permutation": {
            str(key): str(value) for key, value in name_permutation.items()
        },
        "permutation_zero_based": list(permutation),
        "output_atom_identity_source": "Meeko_REMARK_INDEX_MAP",
        "equivalence_basis": "prepared_pdbqt_atom_type_and_charge_0.001",
    }
    topology[f"{label.lower()}_explicit_vina_symmetry"] = evidence
    return evidence


def _manifest_nonidentity_name_permutation(
    manifest: Mapping[str, Any],
    label: str,
) -> dict[str, str]:
    references = manifest.get("reference_poses")
    record = (
        references.get(label.lower())
        if isinstance(references, Mapping)
        else None
    )
    oracle = (
        record.get("symmetry_oracle")
        if isinstance(record, Mapping)
        else None
    )
    permutations = (
        oracle.get("explicit_atom_name_permutations")
        if isinstance(oracle, Mapping)
        else None
    )
    if (
        not isinstance(permutations, list)
        or len(permutations) != 2
        or not all(isinstance(item, Mapping) for item in permutations)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            f"The explicit {label} symmetry oracle is invalid.",
        )
    parsed = [
        {str(key): str(value) for key, value in item.items()}
        for item in permutations
    ]
    identity = next(
        (item for item in parsed if all(key == value for key, value in item.items())),
        None,
    )
    nonidentity = next(
        (item for item in parsed if any(key != value for key, value in item.items())),
        None,
    )
    if identity is None or nonidentity is None or set(identity) != set(
        nonidentity
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            f"The explicit {label} symmetry oracle lacks identity/nonidentity pairs.",
        )
    return nonidentity


def _prepare_receptor(
    python_executable: Path,
    protein_pdb: Path,
    sah_pdbqt: Path,
    work_root: Path,
    expected_polymer_atoms: int,
) -> tuple[Path, dict[str, Any]]:
    protein_pdbqt = work_root / "4DM3_chain_A_polymer.pdbqt"
    protein_json = work_root / "4DM3_chain_A_polymer.json"
    command = meeko_adapter.build_module_command(
        str(python_executable),
        meeko_adapter.MEEKO_RECEPTOR_MODULE,
        [
            "--read_pdb",
            str(protein_pdb),
            "-p",
            str(protein_pdbqt),
            "-j",
            str(protein_json),
        ],
    )
    command_evidence = _run_preparation(
        command,
        cwd=work_root,
        timeout=600,
        code="MULTIPLE_LIGAND_4DM3_MEEKO_RECEPTOR_FAILED",
        message="Meeko 0.7.1 could not prepare the audited chain-A receptor.",
    )
    protein_atoms = _pdbqt_atom_records(protein_pdbqt)
    sah_atoms = _pdbqt_atom_records(sah_pdbqt)
    source_protein_atoms = _pdb_atom_records(protein_pdb)
    if len(source_protein_atoms) != expected_polymer_atoms:
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_SELECTION_CHANGED",
            "The chain-A PDB bridge changed before receptor preparation.",
        )
    heavy_atom_bijection = _audit_receptor_heavy_atom_bijection(
        protein_pdb,
        protein_pdbqt,
    )
    receptor = work_root / "4DM3_chain_A_polymer_plus_SAH_A.pdbqt"
    lines = [
        line
        for line in protein_pdbqt.read_text(
            encoding="utf-8",
            errors="strict",
        ).splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
    ]
    protein_serials = [
        int(line[6:11].strip())
        for line in lines
    ]
    if len(set(protein_serials)) != len(protein_serials):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_MERGE_FAILED",
            "The Meeko protein receptor contains duplicate atom serials.",
        )
    next_serial = max(protein_serials, default=0) + 1
    for offset, atom in enumerate(sah_atoms):
        serial = next_serial + offset
        name = str(atom["name"])[:4]
        x, y, z = atom["xyz"]
        lines.append(
            f"HETATM{serial:5d} {name:>4s} SAH A2001    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}"
            f"  1.00  0.00    {float(atom['charge']):6.3f} "
            f"{str(atom['atom_type']):<2s}"
        )
    receptor.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    merged_sah_lines = lines[-len(sah_atoms) :]
    merged_sah_serials = [
        int(line[6:11].strip()) for line in merged_sah_lines
    ]
    if (
        any(
            not line.startswith("HETATM")
            or line[17:20] != "SAH"
            or line[21:22] != "A"
            or line[22:26] != "2001"
            for line in merged_sah_lines
        )
        or merged_sah_serials
        != list(range(next_serial, next_serial + len(sah_atoms)))
        or set(merged_sah_serials).intersection(protein_serials)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_MERGE_FAILED",
            "The rigid SAH merge did not preserve HETATM SAH A:2001 identity.",
        )
    combined_atoms = _pdbqt_atom_records(receptor)
    if len(combined_atoms) != len(protein_atoms) + len(sah_atoms):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RECEPTOR_MERGE_FAILED",
            "The rigid SAH-A merge changed the prepared receptor atom count.",
        )
    return receptor, {
        "protein_pdbqt": {
            "size_bytes": protein_pdbqt.stat().st_size,
            "sha256": _sha256(protein_pdbqt),
            "atom_count": len(protein_atoms),
            "heavy_atom_bijection": heavy_atom_bijection,
        },
        "rigid_sah_pdbqt": {
            "size_bytes": sah_pdbqt.stat().st_size,
            "sha256": _sha256(sah_pdbqt),
            "atom_count": len(sah_atoms),
        },
        "combined_receptor": {
            "size_bytes": receptor.stat().st_size,
            "sha256": _sha256(receptor),
            "atom_count": len(combined_atoms),
            "polymer_chain": "A",
            "rigid_cofactor": {
                "record_type": "HETATM",
                "component_id": "SAH",
                "author_chain_id": "A",
                "author_sequence_id": 2001,
                "atom_count": len(sah_atoms),
                "first_atom_serial": next_serial,
                "last_atom_serial": next_serial + len(sah_atoms) - 1,
            },
            "excluded": ["chain B", "RCO", "IMD", "waters"],
        },
        "command": command_evidence,
    }


def _calculate_box(
    rco_altlocs: Mapping[str, Sequence[Coordinate]],
    imd: Sequence[Coordinate],
    *,
    padding_angstrom: float,
    spacing_angstrom: float,
) -> dict[str, Any]:
    coordinates = [
        coordinate
        for altloc in ("A", "B")
        for coordinate in rco_altlocs.get(altloc, [])
    ] + list(imd)
    if (
        len(rco_altlocs.get("A", [])) != 8
        or len(rco_altlocs.get("B", [])) != 8
        or len(imd) != 5
        or padding_angstrom <= 0
        or spacing_angstrom <= 0
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_BOX_REFERENCE_INVALID",
            "The fixed Box reference coordinates or constants are invalid.",
        )
    minima = tuple(min(point[axis] for point in coordinates) for axis in range(3))
    maxima = tuple(max(point[axis] for point in coordinates) for axis in range(3))
    centers = tuple(
        (minimum + maximum) / 2.0
        for minimum, maximum in zip(minima, maxima, strict=True)
    )
    requested_sizes = tuple(
        maximum - minimum + 2.0 * padding_angstrom
        for minimum, maximum in zip(minima, maxima, strict=True)
    )
    intervals: list[int] = []
    for requested in requested_sizes:
        count = math.ceil(requested / spacing_angstrom)
        if count % 2:
            count += 1
        intervals.append(count)
    effective_sizes = tuple(
        count * spacing_angstrom for count in intervals
    )
    return {
        "reference_bounds": {
            "minimum": {
                "x": round(minima[0], 6),
                "y": round(minima[1], 6),
                "z": round(minima[2], 6),
            },
            "maximum": {
                "x": round(maxima[0], 6),
                "y": round(maxima[1], 6),
                "z": round(maxima[2], 6),
            },
        },
        "center": {
            "x": round(centers[0], 6),
            "y": round(centers[1], 6),
            "z": round(centers[2], 6),
        },
        "requested_size": {
            "x": round(requested_sizes[0], 6),
            "y": round(requested_sizes[1], 6),
            "z": round(requested_sizes[2], 6),
        },
        "axis_intervals": {
            "x": intervals[0],
            "y": intervals[1],
            "z": intervals[2],
        },
        "effective_size": {
            "x": round(effective_sizes[0], 6),
            "y": round(effective_sizes[1], 6),
            "z": round(effective_sizes[2], 6),
        },
        "padding_angstrom": padding_angstrom,
        "spacing_angstrom": spacing_angstrom,
        "force_even_voxels": True,
    }


def _xyz_mapping(value: Any, label: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        try:
            return {axis: float(value[axis]) for axis in ("x", "y", "z")}
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(value, list) and len(value) == 3:
        return {
            axis: float(item)
            for axis, item in zip(("x", "y", "z"), value, strict=True)
        }
    _fail(
        "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
        f"The manifest {label} must contain x/y/z.",
    )
    raise AssertionError("unreachable")


def _int_xyz_mapping(value: Any, label: str) -> dict[str, int]:
    parsed = _xyz_mapping(value, label)
    if any(not float(number).is_integer() for number in parsed.values()):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            f"The manifest {label} must contain integer intervals.",
        )
    return {axis: int(value) for axis, value in parsed.items()}


def _verify_box_contract(
    references: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    contract = manifest.get("box_contract")
    if not isinstance(contract, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The box_contract section is invalid.",
        )
    if str(contract.get("algorithm") or "") != (
        "reference_union_plus_padding_even_voxel_v1"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The fixed 4DM3 Box algorithm identifier is invalid.",
        )
    padding = float(
        contract.get("padding_each_side_angstrom")
        or contract.get("padding_angstrom")
        or 0
    )
    spacing = float(contract.get("spacing_angstrom") or 0)
    if padding != 6.0 or spacing != 0.375:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The fixed 4DM3 Box must use 6 Å padding and 0.375 Å spacing.",
            details={"padding": padding, "spacing": spacing},
        )
    calculated = _calculate_box(
        references["RCO"],
        references["IMD"],
        padding_angstrom=padding,
        spacing_angstrom=spacing,
    )
    expected_center = _xyz_mapping(contract.get("center"), "box center")
    expected_size = _xyz_mapping(
        contract.get("size_angstrom")
        or contract.get("size")
        or contract.get("effective_size"),
        "box effective size",
    )
    expected_intervals = _int_xyz_mapping(
        contract.get("intervals") or contract.get("axis_intervals"),
        "box intervals",
    )
    expected_minimum = _xyz_mapping(
        (
            contract.get("reference_bounds", {}).get("minimum")
            if isinstance(contract.get("reference_bounds"), Mapping)
            else (
                contract.get("source_bounds", {}).get("minimum")
                if isinstance(contract.get("source_bounds"), Mapping)
                else contract.get("minimum")
            )
        ),
        "reference minimum",
    )
    expected_maximum = _xyz_mapping(
        (
            contract.get("reference_bounds", {}).get("maximum")
            if isinstance(contract.get("reference_bounds"), Mapping)
            else (
                contract.get("source_bounds", {}).get("maximum")
                if isinstance(contract.get("source_bounds"), Mapping)
                else contract.get("maximum")
            )
        ),
        "reference maximum",
    )
    mismatches: dict[str, Any] = {}
    for label, expected_value, actual_value, tolerance in (
        ("center", expected_center, calculated["center"], 0.000001),
        ("size", expected_size, calculated["effective_size"], 0.000001),
        (
            "minimum",
            expected_minimum,
            calculated["reference_bounds"]["minimum"],
            0.000001,
        ),
        (
            "maximum",
            expected_maximum,
            calculated["reference_bounds"]["maximum"],
            0.000001,
        ),
    ):
        for axis in ("x", "y", "z"):
            if abs(expected_value[axis] - actual_value[axis]) > tolerance:
                mismatches[f"{label}_{axis}"] = {
                    "expected": expected_value[axis],
                    "actual": actual_value[axis],
                }
    if expected_intervals != calculated["axis_intervals"]:
        mismatches["axis_intervals"] = {
            "expected": expected_intervals,
            "actual": calculated["axis_intervals"],
        }
    fixed_expected = {
        "center": {"x": 27.8735, "y": 44.103, "z": 17.7445},
        "effective_size": {"x": 15.75, "y": 16.5, "z": 19.5},
        "axis_intervals": {"x": 42, "y": 44, "z": 52},
    }
    for key, expected_value in fixed_expected.items():
        actual_value = calculated[key]
        if any(
            abs(float(actual_value[axis]) - float(expected_value[axis]))
            > 0.000001
            for axis in ("x", "y", "z")
        ):
            mismatches[f"frozen_{key}"] = {
                "expected": expected_value,
                "actual": actual_value,
            }
    if mismatches:
        _fail(
            "MULTIPLE_LIGAND_4DM3_BOX_CONTRACT_MISMATCH",
            "The crystal-derived fixed 4DM3 Box changed.",
            details={"mismatches": mismatches},
        )
    return calculated


def _automorphisms(topology: Mapping[str, Any]) -> list[tuple[int, ...]]:
    raw = topology.get("automorphisms")
    heavy_count = int(topology.get("heavy_atom_count") or 0)
    if not isinstance(raw, list) or heavy_count <= 0:
        _fail(
            "MULTIPLE_LIGAND_4DM3_TOPOLOGY_INVALID",
            "A ligand topology lacks graph automorphisms.",
        )
    parsed: list[tuple[int, ...]] = []
    for value in raw:
        if not isinstance(value, list):
            _fail(
                "MULTIPLE_LIGAND_4DM3_TOPOLOGY_INVALID",
                "A graph automorphism is not an index list.",
            )
        permutation = tuple(int(item) for item in value)
        if len(permutation) != heavy_count or set(permutation) != set(
            range(heavy_count)
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_TOPOLOGY_INVALID",
                "A graph automorphism is not a complete permutation.",
            )
        parsed.append(permutation)
    if len(set(parsed)) != len(parsed):
        _fail(
            "MULTIPLE_LIGAND_4DM3_TOPOLOGY_INVALID",
            "The graph automorphism list contains duplicates.",
        )
    return parsed


def _symmetry_no_fit_rmsd(
    observed: Sequence[Coordinate],
    reference: Sequence[Coordinate],
    automorphisms: Sequence[Sequence[int]],
) -> float:
    """Minimize atom identity only; never translate, rotate, or fit coordinates."""

    if not observed or len(observed) != len(reference):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RMSD_INPUT_INVALID",
            "No-fit RMSD requires equal non-empty heavy-atom coordinate sets.",
        )
    best = math.inf
    for permutation in automorphisms:
        if len(permutation) != len(observed):
            _fail(
                "MULTIPLE_LIGAND_4DM3_RMSD_INPUT_INVALID",
                "A no-fit RMSD permutation has the wrong atom count.",
            )
        squared = 0.0
        for index, target_index in enumerate(permutation):
            if target_index < 0 or target_index >= len(reference):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_RMSD_INPUT_INVALID",
                    "A no-fit RMSD permutation index is out of range.",
                )
            squared += math.dist(
                observed[index],
                reference[target_index],
            ) ** 2
        best = min(best, math.sqrt(squared / len(observed)))
    if not math.isfinite(best):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RMSD_INPUT_INVALID",
            "No valid graph automorphism was available for RMSD.",
        )
    return best


def _heavy_coordinates_from_member(
    member_path: Path,
    topology: Mapping[str, Any],
) -> list[Coordinate]:
    records = _pdbqt_atom_records(member_path)
    return _heavy_coordinates_from_records(
        records,
        topology,
        label=str(member_path),
    )


def _heavy_coordinates_from_text(
    text: str,
    topology: Mapping[str, Any],
    *,
    label: str,
) -> list[Coordinate]:
    return _heavy_coordinates_from_records(
        _pdbqt_atom_records_from_text(text, label=label),
        topology,
        label=label,
    )


def _heavy_coordinates_from_records(
    records: Sequence[Mapping[str, Any]],
    topology: Mapping[str, Any],
    *,
    label: str,
) -> list[Coordinate]:
    expected_atom_count = int(topology.get("prepared_pdbqt_atom_count") or 0)
    contract = topology.get("prepared_pdbqt_source_atom_contract")
    heavy_atom_count = int(topology.get("heavy_atom_count") or 0)
    if (
        expected_atom_count <= 0
        or len(records) != expected_atom_count
        or not isinstance(contract, list)
        or len(contract) != heavy_atom_count
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_IDENTITY_INVALID",
            "A joint pose does not match its frozen prepared-atom contract.",
            details={
                "path": label,
                "expected_atom_count": expected_atom_count,
                "actual_atom_count": len(records),
                "expected_heavy_atom_count": heavy_atom_count,
                "contract_count": (
                    len(contract) if isinstance(contract, list) else None
                ),
            },
        )
    coordinates: list[Coordinate] = []
    for expected_source_index, frozen in enumerate(contract, start=1):
        if not isinstance(frozen, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_IDENTITY_INVALID",
                "A frozen prepared-atom identity record is invalid.",
                details={"path": label},
            )
        source_index = int(frozen.get("source_index") or 0)
        ordinal = int(frozen.get("pdbqt_atom_ordinal_zero_based") or 0)
        if (
            source_index != expected_source_index
            or ordinal < 0
            or ordinal >= len(records)
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_IDENTITY_INVALID",
                "A frozen prepared-atom identity points outside the joint pose.",
                details={
                    "path": label,
                    "source_index": source_index,
                    "pdbqt_atom_ordinal_zero_based": ordinal,
                },
            )
        record = records[ordinal]
        if (
            record.get("is_hydrogen")
            or str(record.get("atom_type") or "")
            != str(frozen.get("atom_type") or "")
            or abs(
                float(record.get("charge") or 0.0)
                - float(frozen.get("charge") or 0.0)
            )
            > 0.001
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_IDENTITY_INVALID",
                "A joint pose changed a frozen heavy-atom type or charge.",
                details={
                    "path": label,
                    "source_index": source_index,
                    "expected_atom_type": frozen.get("atom_type"),
                    "actual_atom_type": record.get("atom_type"),
                    "expected_charge": frozen.get("charge"),
                    "actual_charge": record.get("charge"),
                },
            )
        coordinates.append(
            tuple(float(value) for value in record["xyz"])
        )
    return coordinates


def _project_artifact(
    project_root: Path,
    relative_path: str,
    label: str,
    *,
    allow_empty: bool = False,
) -> Path:
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute() or ".." in relative.parts:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_UNSAFE",
            f"The {label} path is unsafe.",
            details={"relative_path": relative_path},
        )
    try:
        root = project_root.resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_UNSAFE",
            "The temporary project root cannot be resolved.",
            details={"error": str(exc)},
        )
    lexical = root / relative
    current = lexical
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0
    )
    while current != root:
        try:
            details = os.lstat(current)
        except OSError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_MISSING",
                f"The {label} is missing.",
                details={"path": str(current), "error": str(exc)},
            )
        attributes = int(
            getattr(details, "st_file_attributes", 0) or 0
        )
        if stat.S_ISLNK(details.st_mode) or bool(
            attributes & reparse_flag
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_UNSAFE",
                f"The {label} path contains a link or reparse point.",
                details={"path": str(current)},
            )
        current = current.parent
    try:
        path = lexical.resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_MISSING",
            f"The {label} cannot be resolved.",
            details={"path": str(lexical), "error": str(exc)},
        )
    try:
        path.relative_to(root)
    except ValueError:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_UNSAFE",
            f"The {label} resolves outside the temporary project.",
        )
    if (
        not path.is_file()
        or path.is_symlink()
        or (not allow_empty and path.stat().st_size <= 0)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_MISSING",
            (
                f"The {label} is missing or symbolic."
                if allow_empty
                else f"The {label} is missing, empty, or symbolic."
            ),
            details={"path": str(path)},
        )
    return path


def _run_parameters(manifest: Mapping[str, Any]) -> dict[str, Any]:
    protocol = manifest.get("docking_protocol")
    if not isinstance(protocol, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The docking_protocol section is invalid.",
        )
    parameters = (
        protocol.get("parameters")
        if isinstance(protocol.get("parameters"), Mapping)
        else protocol
    )
    expected = {
        "scoring": "vina",
        "exhaustiveness": 32,
        "num_modes": 20,
        "min_rmsd": 1.0,
        "energy_range": 5,
        "cpu": 1,
        "spacing": 0.375,
        "force_even_voxels": True,
    }
    actual = {
        "scoring": str(parameters.get("scoring") or ""),
        "exhaustiveness": int(parameters.get("exhaustiveness") or 0),
        "num_modes": int(parameters.get("num_modes") or 0),
        "min_rmsd": float(parameters.get("min_rmsd") or 0),
        "energy_range": int(parameters.get("energy_range") or 0),
        "cpu": int(parameters.get("cpu") or 0),
        "spacing": float(parameters.get("spacing") or 0),
        "force_even_voxels": parameters.get("force_even_voxels"),
    }
    if actual != expected:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The frozen 4DM3 Vina protocol changed.",
            details={"expected": expected, "actual": actual},
        )
    return actual


def _run_matrix(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = manifest.get("run_matrix")
    if isinstance(value, Mapping):
        value = value.get("runs")
    if not isinstance(value, list) or len(value) != 4:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 run matrix must contain four runs.",
        )
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                "A 4DM3 run-matrix record is invalid.",
            )
        run_key = str(
            item.get("run_key")
            or item.get("run_id")
            or item.get("id")
            or ""
        )
        seed = int(item.get("seed") or 0)
        repeat_of = str(item.get("repeat_of") or "")
        if not run_key or seed <= 0:
            _fail(
                "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                "A 4DM3 run-matrix key or seed is invalid.",
            )
        records.append(
            {"run_key": run_key, "seed": seed, "repeat_of": repeat_of}
        )
    if len({item["run_key"] for item in records}) != 4:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 run-matrix keys are not unique.",
        )
    primary = [item for item in records if not item["repeat_of"]]
    repeats = [item for item in records if item["repeat_of"]]
    if (
        len(primary) != 3
        or len(repeats) != 1
        or len({item["seed"] for item in primary}) != 3
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The run matrix must contain three seeds and one exact repeat.",
        )
    target = next(
        (item for item in records if item["run_key"] == repeats[0]["repeat_of"]),
        None,
    )
    if target is None or target["seed"] != repeats[0]["seed"]:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The repeat run does not repeat the declared primary seed.",
        )
    return records


def _canonical_science_payload(
    *,
    scores: Sequence[Mapping[str, Any]],
    models: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    canonical_scores = [
        {
            "mode": int(row.get("mode") or 0),
            "joint_affinity_kcal_mol": float(
                row.get("joint_affinity_kcal_mol")
            ),
            "rmsd_lb": float(row.get("rmsd_lb")),
            "rmsd_ub": float(row.get("rmsd_ub")),
            "pose_available": row.get("pose_available") is True,
            "score_is_joint": row.get("score_is_joint") is True,
        }
        for row in scores
    ]
    canonical_models = []
    for model in models:
        members = []
        for member in model.get("members") or []:
            coordinates = [
                [round(float(value), 3) for value in coordinate]
                for coordinate in member.get("heavy_coordinates") or []
            ]
            members.append(
                {
                    "member_index": int(member.get("member_index") or 0),
                    "heavy_coordinates": coordinates,
                }
            )
        canonical_models.append(
            {
                "mode": int(model.get("mode") or 0),
                "joint_affinity_kcal_mol": float(
                    model.get("joint_affinity_kcal_mol")
                ),
                "rmsd_lb": float(model.get("rmsd_lb")),
                "rmsd_ub": float(model.get("rmsd_ub")),
                "members": members,
            }
        )
    return {
        "scores": canonical_scores,
        "models": canonical_models,
    }


def _audit_project_snapshot(
    project_root: Path,
    record: Any,
    *,
    label: str,
    expected_relative_path: str,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            f"The {label} snapshot is missing.",
        )
    relative_path = str(record.get("relative_path") or "")
    expected_sha256 = str(record.get("sha256") or "").lower()
    expected_size = int(record.get("size_bytes") or 0)
    if (
        relative_path != Path(expected_relative_path).as_posix()
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or expected_size <= 0
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_INVALID",
            f"The {label} snapshot attestation is invalid.",
            details={
                "expected_relative_path": expected_relative_path,
                "record": dict(record),
            },
        )
    path = _project_artifact(project_root, relative_path, label)
    actual_size = path.stat().st_size
    actual_sha256 = _sha256(path)
    if actual_size != expected_size or actual_sha256 != expected_sha256:
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
            f"The {label} bytes do not match their project snapshot.",
            details={
                "relative_path": relative_path,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": actual_size,
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
            },
        )
    return {
        "relative_path": relative_path,
        "size_bytes": actual_size,
        "sha256": actual_sha256,
    }


def _read_attested_project_bytes(
    project_root: Path,
    record: Any,
    *,
    label: str,
    expected_relative_path: str,
    allow_empty: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Read one immutable artifact payload and attest those exact bytes."""

    if not isinstance(record, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            f"The {label} snapshot is missing.",
        )
    relative_path = str(record.get("relative_path") or "")
    expected_sha256 = str(record.get("sha256") or "").lower()
    try:
        raw_size = record.get("size_bytes")
        if isinstance(raw_size, bool) or raw_size is None:
            raise ValueError("size_bytes is not an integer")
        expected_size = int(raw_size)
    except (TypeError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_INVALID",
            f"The {label} snapshot size is invalid.",
            details={"record": dict(record), "error": str(exc)},
        )
    if (
        relative_path != Path(expected_relative_path).as_posix()
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or expected_size < 0
        or (not allow_empty and expected_size == 0)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_INVALID",
            f"The {label} snapshot attestation is invalid.",
            details={
                "expected_relative_path": expected_relative_path,
                "allow_empty": allow_empty,
                "record": dict(record),
            },
        )
    path = _project_artifact(
        project_root,
        relative_path,
        label,
        allow_empty=allow_empty,
    )
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ARTIFACT_READ_FAILED",
            f"The {label} could not be read for byte-level attestation.",
            details={"path": str(path), "error": str(exc)},
        )
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(
        getattr(before, key, None) != getattr(after, key, None)
        for key in identity_fields
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_CHANGED_DURING_READ",
            f"The {label} changed while it was being audited.",
            details={"relative_path": relative_path},
        )
    actual_size = len(payload)
    actual_sha256 = _sha256_bytes(payload)
    if actual_size != expected_size or actual_sha256 != expected_sha256:
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
            f"The {label} bytes do not match their project snapshot.",
            details={
                "relative_path": relative_path,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": actual_size,
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
            },
        )
    return (
        {
            "relative_path": relative_path,
            "size_bytes": actual_size,
            "sha256": actual_sha256,
        },
        payload,
    )


def _strict_utf8(payload: bytes, *, label: str) -> str:
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_RESULT_ENCODING_INVALID",
            f"The {label} is not strict UTF-8.",
            details={"error": str(exc)},
        )


def _parse_scores_csv_bytes(payload: bytes) -> list[dict[str, Any]]:
    text = _strict_utf8(payload, label="scores.csv")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
            "scores.csv is not valid CSV.",
            details={"error": str(exc)},
        )
    if not rows or rows[0] != SCORES_CSV_HEADER:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
            "scores.csv does not have the exact simultaneous-ligand header.",
            details={
                "expected_header": SCORES_CSV_HEADER,
                "actual_header": rows[0] if rows else None,
            },
        )
    parsed: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(SCORES_CSV_HEADER):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
                "scores.csv contains a malformed or blank row.",
                details={"row_number": row_number, "row": row},
            )
        (
            mode_text,
            affinity_text,
            rmsd_lb_text,
            rmsd_ub_text,
            pose_available_text,
            score_scope,
        ) = row
        try:
            mode = int(mode_text)
            affinity = float(affinity_text)
            rmsd_lb = float(rmsd_lb_text)
            rmsd_ub = float(rmsd_ub_text)
        except ValueError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
                "scores.csv contains a non-numeric score field.",
                details={"row_number": row_number, "row": row, "error": str(exc)},
            )
        if (
            not all(math.isfinite(value) for value in (affinity, rmsd_lb, rmsd_ub))
            or pose_available_text not in {"true", "false"}
            or score_scope != "joint_two_ligand_pose"
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
                "scores.csv changed its joint-score value semantics.",
                details={"row_number": row_number, "row": row},
            )
        parsed.append(
            {
                "mode": mode,
                "joint_affinity_kcal_mol": affinity,
                "rmsd_lb": rmsd_lb,
                "rmsd_ub": rmsd_ub,
                "pose_available": pose_available_text == "true",
                "score_scope": score_scope,
            }
        )
    if not parsed:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORES_CSV_INVALID",
            "scores.csv contains no score rows.",
        )
    return parsed


def _score_triplet(record: Mapping[str, Any], *, affinity_key: str) -> tuple[float, float, float]:
    try:
        values = (
            float(record.get(affinity_key)),
            float(record.get("rmsd_lb")),
            float(record.get("rmsd_ub")),
        )
    except (TypeError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
            "A result score row contains a non-numeric value.",
            details={"record": dict(record), "error": str(exc)},
        )
    if not all(math.isfinite(value) for value in values):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
            "A result score row contains a non-finite value.",
            details={"record": dict(record)},
        )
    return values


def _score_triplets_match(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    left_affinity_key: str,
    right_affinity_key: str,
    tolerance: float = 0.0,
) -> bool:
    left_values = _score_triplet(left, affinity_key=left_affinity_key)
    right_values = _score_triplet(right, affinity_key=right_affinity_key)
    return all(
        abs(left_value - right_value) <= tolerance
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )


def _audit_vina_config_contract(
    payload: bytes,
    *,
    run_id: str,
    box: Mapping[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Independently parse and freeze the exact 4DM3 Vina config contract."""

    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            "The frozen Vina config is not strict UTF-8.",
            details={"error": str(exc)},
        )
    if "\x00" in text:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            "The frozen Vina config contains a NUL byte.",
        )

    key_order = (
        "receptor",
        "scoring",
        "center_x",
        "center_y",
        "center_z",
        "size_x",
        "size_y",
        "size_z",
        "exhaustiveness",
        "max_evals",
        "num_modes",
        "min_rmsd",
        "energy_range",
        "cpu",
        "spacing",
        "force_even_voxels",
        "verbosity",
        "seed",
    )
    allowed_keys = set(key_order)
    raw_values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line == "":
            continue
        matched = re.fullmatch(r"([a-z][a-z0-9_]*) = (\S+)", line)
        if matched is None:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains a malformed line.",
                details={"line_number": line_number},
            )
        key, value = matched.groups()
        if key not in allowed_keys:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains an unknown key.",
                details={"line_number": line_number, "key": key},
            )
        if key in raw_values:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains a duplicate key.",
                details={"line_number": line_number, "key": key},
            )
        raw_values[key] = value

    missing = [key for key in key_order if key not in raw_values]
    if missing:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            "The frozen Vina config is missing required keys.",
            details={"missing_keys": missing},
        )

    try:
        center = box["center"]
        size = box["effective_size"]
        if not isinstance(center, Mapping) or not isinstance(size, Mapping):
            raise TypeError("box center/effective_size must be mappings")
        expected = {
            "receptor": f"runs/{run_id}/inputs/receptor.pdbqt",
            "scoring": "vina",
            "center_x": float(center["x"]),
            "center_y": float(center["y"]),
            "center_z": float(center["z"]),
            "size_x": float(size["x"]),
            "size_y": float(size["y"]),
            "size_z": float(size["z"]),
            "exhaustiveness": 32,
            "max_evals": 0,
            "num_modes": 20,
            "min_rmsd": 1.0,
            "energy_range": 5.0,
            "cpu": 1,
            "spacing": 0.375,
            "force_even_voxels": True,
            "verbosity": 1,
            "seed": int(seed),
        }
    except (KeyError, TypeError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_EXPECTATION_INVALID",
            "The expected 4DM3 Box or seed contract is invalid.",
            details={"error": str(exc)},
        )
    if expected["seed"] <= 0 or not all(
        math.isfinite(float(expected[key]))
        for key in (
            "center_x",
            "center_y",
            "center_z",
            "size_x",
            "size_y",
            "size_z",
        )
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_EXPECTATION_INVALID",
            "The expected 4DM3 Box or seed contract is not finite and positive.",
        )

    actual: dict[str, Any] = {
        "receptor": raw_values["receptor"],
        "scoring": raw_values["scoring"],
    }
    integer_keys = (
        "exhaustiveness",
        "max_evals",
        "num_modes",
        "cpu",
        "verbosity",
        "seed",
    )
    number_keys = (
        "center_x",
        "center_y",
        "center_z",
        "size_x",
        "size_y",
        "size_z",
        "min_rmsd",
        "energy_range",
        "spacing",
    )
    for key in integer_keys:
        value = raw_values[key]
        if re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains an invalid integer.",
                details={"key": key, "value": value},
            )
        actual[key] = int(value)
    for key in number_keys:
        value = raw_values[key]
        if re.fullmatch(
            r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?",
            value,
        ) is None:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains an invalid number.",
                details={"key": key, "value": value},
            )
        actual[key] = float(value)
        if not math.isfinite(actual[key]):
            _fail(
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
                "The frozen Vina config contains a non-finite number.",
                details={"key": key, "value": value},
            )
    if raw_values["force_even_voxels"] not in {"true", "false"}:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            "The frozen Vina config contains an invalid Boolean.",
            details={
                "key": "force_even_voxels",
                "value": raw_values["force_even_voxels"],
            },
        )
    actual["force_even_voxels"] = (
        raw_values["force_even_voxels"] == "true"
    )

    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in key_order
        if actual[key] != expected[key]
    }
    if mismatches:
        _fail(
            "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_MISMATCH",
            "The frozen Vina config differs from the fixed 4DM3 protocol.",
            details={"mismatches": mismatches},
        )
    return {
        "schema": "dockstart_4dm3_strict_vina_config_v1",
        "keys": list(key_order),
        "values": {key: actual[key] for key in key_order},
    }


def _audit_vina_snapshot(
    record: Any,
    *,
    expected_vina_path: Path,
    expected_vina_identity: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_VINA_ATTESTATION_MISSING",
            f"The {label} Vina snapshot is missing.",
        )
    recorded_path = Path(str(record.get("path") or "")).expanduser()
    try:
        recorded_path = recorded_path.resolve(strict=True)
        expected_path = expected_vina_path.expanduser().resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_VINA_ATTESTATION_INVALID",
            f"The {label} Vina path cannot be resolved.",
            details={"error": str(exc)},
        )
    recorded_sha256 = str(record.get("sha256") or "").lower()
    recorded_size = int(record.get("size_bytes") or 0)
    recorded_version = str(record.get("version") or "")
    expected_sha256 = str(
        expected_vina_identity.get("sha256") or ""
    ).lower()
    expected_size = int(
        expected_vina_identity.get("size_bytes") or 0
    )
    expected_version = str(
        expected_vina_identity.get("version") or ""
    )
    if (
        recorded_path != expected_path
        or recorded_path.is_symlink()
        or not recorded_path.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", recorded_sha256)
        or recorded_size <= 0
        or recorded_sha256 != expected_sha256
        or recorded_size != expected_size
        or recorded_version != expected_version
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_VINA_IDENTITY_CHANGED",
            f"The {label} Vina snapshot changed.",
            details={
                "expected_path": str(expected_path),
                "recorded_path": str(recorded_path),
                "expected_identity": {
                    "version": expected_version,
                    "size_bytes": expected_size,
                    "sha256": expected_sha256,
                },
                "recorded_identity": {
                    "version": recorded_version,
                    "size_bytes": recorded_size,
                    "sha256": recorded_sha256,
                },
            },
        )
    actual_size = recorded_path.stat().st_size
    actual_sha256 = _sha256(recorded_path)
    if actual_size != recorded_size or actual_sha256 != recorded_sha256:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_VINA_IDENTITY_CHANGED",
            f"The {label} Vina bytes changed.",
            details={
                "expected_size_bytes": recorded_size,
                "actual_size_bytes": actual_size,
                "expected_sha256": recorded_sha256,
                "actual_sha256": actual_sha256,
            },
        )
    return {
        "path": str(recorded_path),
        "version": recorded_version,
        "size_bytes": actual_size,
        "sha256": actual_sha256,
    }


def _audit_prepared_run_inputs(
    project_root: Path,
    run_id: str,
    metadata: Any,
    *,
    receptor_pdbqt: Path,
    rco_pdbqt: Path,
    imd_pdbqt: Path,
    box: Mapping[str, Any],
    expected_seed: int,
    expected_vina_path: Path,
    expected_vina_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(metadata, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_METADATA_INVALID",
            "The prepared project run lacks metadata.",
        )
    snapshots = metadata.get("snapshots")
    if not isinstance(snapshots, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            "The prepared project run lacks input snapshots.",
        )
    prefix = Path("runs", run_id).as_posix()
    receptor = _audit_project_snapshot(
        project_root,
        snapshots.get("receptor"),
        label="frozen receptor input",
        expected_relative_path=f"{prefix}/inputs/receptor.pdbqt",
    )
    receptor_source = {
        "size_bytes": receptor_pdbqt.stat().st_size,
        "sha256": _sha256(receptor_pdbqt),
    }
    if (
        receptor["size_bytes"] != receptor_source["size_bytes"]
        or receptor["sha256"] != receptor_source["sha256"]
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
            "The frozen receptor input differs from receptor preparation.",
            details={
                "source_path": str(receptor_pdbqt),
                "source": receptor_source,
                "snapshot": receptor,
            },
        )
    config, config_payload = _read_attested_project_bytes(
        project_root,
        snapshots.get("config"),
        label="frozen Vina config",
        expected_relative_path=f"{prefix}/config_snapshot.txt",
    )
    config_contract = _audit_vina_config_contract(
        config_payload,
        run_id=run_id,
        box=box,
        seed=expected_seed,
    )
    members = snapshots.get("members")
    if not isinstance(members, list) or len(members) != 2:
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            "The prepared project run must freeze exactly two ligand inputs.",
        )
    source_paths = (rco_pdbqt, imd_pdbqt)
    audited_members = []
    for member_index, (record, source_path) in enumerate(
        zip(members, source_paths, strict=True),
        start=1,
    ):
        if (
            not isinstance(record, Mapping)
            or int(record.get("member_index") or 0) != member_index
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_INVALID",
                "The frozen ligand input order changed.",
            )
        evidence = _audit_project_snapshot(
            project_root,
            record,
            label=f"frozen ligand member {member_index}",
            expected_relative_path=(
                f"{prefix}/inputs/ligand_{member_index:03d}.pdbqt"
            ),
        )
        source_size = source_path.stat().st_size
        source_sha256 = _sha256(source_path)
        if (
            evidence["size_bytes"] != source_size
            or evidence["sha256"] != source_sha256
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
                f"Frozen ligand member {member_index} differs from preparation.",
                details={
                    "source_path": str(source_path),
                    "source_size_bytes": source_size,
                    "source_sha256": source_sha256,
                    "snapshot": evidence,
                },
            )
        audited_members.append(
            {"member_index": member_index, **evidence}
        )
    vina = _audit_vina_snapshot(
        metadata.get("prepared_vina"),
        expected_vina_path=expected_vina_path,
        expected_vina_identity=expected_vina_identity,
        label="prepared",
    )
    expected_command = [
        str(expected_vina_path.resolve(strict=True)),
        "--config",
        f"{prefix}/config_snapshot.txt",
        "--ligand",
        f"{prefix}/inputs/ligand_001.pdbqt",
        f"{prefix}/inputs/ligand_002.pdbqt",
        "--out",
        f"{prefix}/out.pdbqt",
    ]
    actual_command = list(metadata.get("command") or [])
    if (
        len(actual_command) != len(expected_command)
        or actual_command[1:] != expected_command[1:]
        or Path(str(actual_command[0])).resolve(strict=False)
        != expected_vina_path.resolve(strict=False)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_COMMAND_CONTRACT_MISMATCH",
            "The project API did not build the expected simultaneous-ligand CLI.",
            details={
                "expected": expected_command,
                "actual": actual_command,
            },
        )
    return {
        "receptor": receptor,
        "members": audited_members,
        "config": config,
        "config_contract": config_contract,
        "prepared_vina": vina,
        "command": actual_command,
    }


def _audit_finished_run_artifacts(
    project_root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
    *,
    expected_vina_path: Path,
    expected_vina_identity: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = Path("runs", run_id).as_posix()
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            "The finished project run lacks artifact snapshots.",
        )
    expected_artifacts = {
        "output": f"{prefix}/out.pdbqt",
        "scores": f"{prefix}/scores.csv",
        "joint_poses": f"{prefix}/joint_poses.json",
        "report": f"{prefix}/multi_ligand_report.md",
    }
    audited = {
        key: _audit_project_snapshot(
            project_root,
            artifacts.get(key),
            label=key,
            expected_relative_path=relative_path,
        )
        for key, relative_path in expected_artifacts.items()
    }
    execution_vina = _audit_vina_snapshot(
        metadata.get("execution_vina"),
        expected_vina_path=expected_vina_path,
        expected_vina_identity=expected_vina_identity,
        label="executed",
    )
    normalization = metadata.get("output_normalization")
    if not isinstance(normalization, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_NORMALIZATION_INVALID",
            "The finished run lacks Vina raw-output provenance.",
        )
    source_sha256 = str(normalization.get("source_sha256") or "").lower()
    source_size = int(normalization.get("source_size_bytes") or 0)
    normalized_sha256 = str(
        normalization.get("normalized_sha256") or ""
    ).lower()
    normalized_size = int(normalization.get("normalized_size_bytes") or 0)
    changed = normalization.get("changed")
    status = str(normalization.get("status") or "")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        or source_size <= 0
        or normalized_sha256 != audited["output"]["sha256"]
        or normalized_size != audited["output"]["size_bytes"]
        or changed not in {True, False}
        or status not in {"normalized", "not_required"}
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_NORMALIZATION_INVALID",
            "The Vina raw/normalized output provenance is incomplete.",
            details={"record": dict(normalization)},
        )
    raw_output: dict[str, Any] = {
        "size_bytes": source_size,
        "sha256": source_sha256,
        "archived": False,
        "relative_path": audited["output"]["relative_path"],
    }
    if changed is True:
        raw_relative = str(normalization.get("raw_output_file") or "")
        expected_raw_relative = f"{prefix}/out.vina_raw.pdbqt"
        if status != "normalized" or raw_relative != expected_raw_relative:
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_NORMALIZATION_INVALID",
                "A normalized Vina output lacks its fixed raw archive path.",
                details={"record": dict(normalization)},
            )
        raw_snapshot = (
            artifacts.get("out_vina_raw")
            if isinstance(artifacts.get("out_vina_raw"), Mapping)
            else artifacts.get("raw_output")
        )
        raw_evidence = _audit_project_snapshot(
            project_root,
            raw_snapshot,
            label="raw Vina output",
            expected_relative_path=expected_raw_relative,
        )
        if (
            raw_evidence["sha256"] != source_sha256
            or raw_evidence["size_bytes"] != source_size
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_NORMALIZATION_INVALID",
                "The raw Vina archive differs from source provenance.",
                details={
                    "normalization": dict(normalization),
                    "raw_artifact": raw_evidence,
                },
            )
        raw_output.update(
            {
                **raw_evidence,
                "archived": True,
            }
        )
    elif (
        status != "not_required"
        or str(normalization.get("raw_output_file") or "")
        or source_sha256 != audited["output"]["sha256"]
        or source_size != audited["output"]["size_bytes"]
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_NORMALIZATION_INVALID",
            "An unchanged Vina output has inconsistent raw provenance.",
            details={"record": dict(normalization)},
        )
    return {
        "artifacts": audited,
        "execution_vina": execution_vina,
        "output_normalization": dict(normalization),
        "raw_output": raw_output,
    }


def _audit_completed_result_provenance(
    project_root: Path,
    run_id: str,
    metadata: Mapping[str, Any],
    *,
    frozen_members: Any,
    api_scores: Any,
    expected_box: Mapping[str, Any],
    frozen_num_modes: int,
) -> dict[str, Any]:
    """Independently rebuild the completed result contract from attested bytes."""

    prefix = Path("runs", run_id).as_posix()
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            "The finished run lacks result artifact snapshots.",
        )
    artifact_contract = {
        "output": (f"{prefix}/out.pdbqt", False),
        "scores": (f"{prefix}/scores.csv", False),
        "joint_poses": (f"{prefix}/joint_poses.json", False),
        "report": (f"{prefix}/multi_ligand_report.md", False),
        "stdout": (f"{prefix}/stdout.txt", False),
        "stderr": (f"{prefix}/stderr.txt", True),
        "log": (f"{prefix}/log.txt", False),
    }
    metadata_paths = {
        "output": "output_file",
        "scores": "scores_file",
        "joint_poses": "joint_poses_file",
        "report": "report_file",
        "stdout": "stdout_file",
        "stderr": "stderr_file",
        "log": "log_file",
    }
    audited_artifacts: dict[str, dict[str, Any]] = {}
    artifact_bytes: dict[str, bytes] = {}
    for key, (relative_path, allow_empty) in artifact_contract.items():
        if str(metadata.get(metadata_paths[key]) or "") != relative_path:
            _fail(
                "MULTIPLE_LIGAND_4DM3_RUN_RESULT_CONTRACT_MISMATCH",
                f"The finished run changed its fixed {key} path.",
                details={
                    "field": metadata_paths[key],
                    "expected": relative_path,
                    "actual": metadata.get(metadata_paths[key]),
                },
            )
        evidence, payload = _read_attested_project_bytes(
            project_root,
            artifacts.get(key),
            label=key,
            expected_relative_path=relative_path,
            allow_empty=allow_empty,
        )
        audited_artifacts[key] = evidence
        artifact_bytes[key] = payload
    if artifact_bytes["stdout"] != artifact_bytes["log"]:
        _fail(
            "MULTIPLE_LIGAND_4DM3_STDOUT_LOG_MISMATCH",
            "The durable Vina stdout and log bytes differ.",
            details={
                "stdout_sha256": audited_artifacts["stdout"]["sha256"],
                "log_sha256": audited_artifacts["log"]["sha256"],
            },
        )

    if (
        not isinstance(frozen_members, list)
        or len(frozen_members) != 2
        or metadata.get("members") != frozen_members
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_INPUT_SNAPSHOT_CHANGED",
            "The finished run changed its frozen ligand member contract.",
        )
    output_text = _strict_utf8(
        artifact_bytes["output"],
        label="normalized out.pdbqt",
    )
    parsed_output = parse_multiple_ligand_output_text(
        output_text,
        [dict(member) for member in frozen_members],
    )
    if not isinstance(parsed_output, Mapping) or parsed_output.get("ok") is not True:
        error = (
            parsed_output.get("error")
            if isinstance(parsed_output, Mapping)
            and isinstance(parsed_output.get("error"), Mapping)
            else {}
        )
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_REPARSE_FAILED",
            "The attested normalized out.pdbqt failed independent re-parsing.",
            details={"parser_error": dict(error)},
        )
    parsed_models = parsed_output.get("models")
    if not isinstance(parsed_models, list) or not parsed_models:
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_REPARSE_FAILED",
            "The independently parsed output contains no joint models.",
        )
    output_modes = [
        int(model.get("mode") or 0)
        for model in parsed_models
        if isinstance(model, Mapping)
    ]
    if (
        len(output_modes) != len(parsed_models)
        or output_modes != list(range(1, len(output_modes) + 1))
        or any(mode > frozen_num_modes for mode in output_modes)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_MODE_SEQUENCE_INVALID",
            "The attested output modes are not unique, continuous, or bounded.",
            details={
                "modes": output_modes,
                "frozen_num_modes": frozen_num_modes,
            },
        )

    try:
        joint = json.loads(
            _strict_utf8(
                artifact_bytes["joint_poses"],
                label="joint_poses.json",
            )
        )
    except json.JSONDecodeError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
            "The attested joint_poses.json is invalid.",
            details={"error": str(exc)},
        )
    if not isinstance(joint, dict):
        _fail(
            "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
            "The attested joint_poses.json must contain an object.",
        )
    raw_models = joint.get("models")
    joint_members = joint.get("members")
    if (
        not isinstance(raw_models, list)
        or not isinstance(joint_members, list)
        or len(joint_members) != 2
        or [int(model.get("mode") or 0) for model in raw_models
            if isinstance(model, Mapping)] != output_modes
        or joint.get("available_modes") != output_modes
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
            "The joint manifest does not describe the re-parsed output modes.",
        )

    required_member_stats = (
        "identity_sha256",
        "atom_count",
        "heavy_atom_count",
        "branch_count",
        "effective_branch_count",
        "degenerate_branch_count",
        "torsdof",
    )
    member_pose_artifacts: dict[str, dict[str, Any]] = {}
    parsed_models_by_mode: dict[int, Mapping[str, Any]] = {}
    audited_pose_count = 0
    for parsed_model, raw_model in zip(parsed_models, raw_models, strict=True):
        if not isinstance(parsed_model, Mapping) or not isinstance(raw_model, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                "A parsed or persisted joint model record is invalid.",
            )
        mode = int(parsed_model.get("mode") or 0)
        parsed_models_by_mode[mode] = parsed_model
        if (
            int(raw_model.get("mode") or 0) != mode
            or not _score_triplets_match(
                parsed_model,
                raw_model,
                left_affinity_key="joint_affinity_kcal_mol",
                right_affinity_key="joint_affinity_kcal_mol",
            )
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_MANIFEST_MISMATCH",
                f"Mode {mode} in joint_poses.json differs from out.pdbqt.",
            )
        parsed_members = parsed_model.get("members")
        raw_members = raw_model.get("members")
        if (
            not isinstance(parsed_members, list)
            or not isinstance(raw_members, list)
            or len(parsed_members) != 2
            or len(raw_members) != 2
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                f"Mode {mode} does not contain exactly two members.",
            )
        for member_index, (
            parsed_member,
            raw_member,
            frozen_member,
            joint_member,
        ) in enumerate(
            zip(
                parsed_members,
                raw_members,
                frozen_members,
                joint_members,
                strict=True,
            ),
            start=1,
        ):
            if not all(
                isinstance(value, Mapping)
                for value in (
                    parsed_member,
                    raw_member,
                    frozen_member,
                    joint_member,
                )
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                    f"Mode {mode} member {member_index} is invalid.",
                )
            expected_member_id = str(
                frozen_member.get("member_id") or ""
            )
            if (
                int(parsed_member.get("member_index") or 0) != member_index
                or int(raw_member.get("member_index") or 0) != member_index
                or int(joint_member.get("member_index") or 0) != member_index
                or not expected_member_id
                or str(raw_member.get("member_id") or "")
                != expected_member_id
                or str(joint_member.get("member_id") or "")
                != expected_member_id
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_MEMBER_IDENTITY_MISMATCH",
                    f"Mode {mode} member {member_index} changed member_id or order.",
                )
            frozen_stats = frozen_member.get("stats")
            joint_stats = joint_member.get("stats")
            if not isinstance(frozen_stats, Mapping) or not isinstance(
                joint_stats,
                Mapping,
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_MEMBER_IDENTITY_MISMATCH",
                    "A frozen or joint member stats contract is missing.",
                )
            for key in required_member_stats:
                parsed_value = parsed_member.get(key)
                frozen_value = frozen_stats.get(key)
                joint_value = joint_stats.get(key)
                if (
                    parsed_value is None
                    or frozen_value is None
                    or joint_value is None
                    or parsed_value != frozen_value
                    or parsed_value != joint_value
                ):
                    _fail(
                        "MULTIPLE_LIGAND_4DM3_OUTPUT_MEMBER_IDENTITY_MISMATCH",
                        (
                            f"Mode {mode} member {member_index} changed "
                            f"{key}."
                        ),
                        details={
                            "parsed": parsed_value,
                            "frozen": frozen_value,
                            "joint": joint_value,
                        },
                    )
            for key in (
                "identity_sha256",
                "atom_count",
                "torsdof",
            ):
                if raw_member.get(key) != parsed_member.get(key):
                    _fail(
                        "MULTIPLE_LIGAND_4DM3_OUTPUT_MEMBER_IDENTITY_MISMATCH",
                        (
                            f"Mode {mode} member {member_index} manifest "
                            f"changed {key}."
                        ),
                    )
            identity_sha256 = str(
                parsed_member.get("identity_sha256") or ""
            ).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", identity_sha256):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_MEMBER_IDENTITY_MISMATCH",
                    f"Mode {mode} member {member_index} identity is invalid.",
                )
            member_relative = (
                f"{prefix}/poses/mode_{mode:03d}/"
                f"member_{member_index:03d}.pdbqt"
            )
            member_evidence, member_payload = _read_attested_project_bytes(
                project_root,
                {
                    "relative_path": raw_member.get("file"),
                    "size_bytes": raw_member.get("size_bytes"),
                    "sha256": raw_member.get("sha256"),
                },
                label=f"mode {mode} member {member_index}",
                expected_relative_path=member_relative,
            )
            parsed_member_payload = str(
                parsed_member.get("content") or ""
            ).encode("utf-8")
            if member_payload != parsed_member_payload:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_POSE_PROVENANCE_MISMATCH",
                    (
                        f"Mode {mode} member {member_index} pose bytes were "
                        "not derived from the attested normalized output."
                    ),
                    details={
                        "pose_sha256": member_evidence["sha256"],
                        "parsed_sha256": _sha256_bytes(parsed_member_payload),
                    },
                )
            member_pose_artifacts[
                f"mode_{mode:03d}_member_{member_index:03d}"
            ] = member_evidence
            audited_pose_count += 1

    log_text = _strict_utf8(artifact_bytes["log"], label="log.txt")
    log_scores = parse_vina_log_text(log_text)
    if isinstance(log_scores, Mapping) or not isinstance(log_scores, list):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
            "The attested log.txt failed independent score parsing.",
            details={
                "parser_result": (
                    dict(log_scores)
                    if isinstance(log_scores, Mapping)
                    else str(log_scores)
                )
            },
        )
    csv_scores = _parse_scores_csv_bytes(artifact_bytes["scores"])
    if not isinstance(api_scores, list) or not api_scores:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
            "The completed API result contains no score rows.",
        )
    report_semantics = _audit_report_bytes(
        artifact_bytes["report"],
        scores=api_scores,
        expected_run_id=run_id,
    )
    log_modes = [
        int(row.get("mode") or 0)
        for row in log_scores
        if isinstance(row, Mapping)
    ]
    if (
        len(log_modes) != len(log_scores)
        or log_modes != list(range(1, len(log_modes) + 1))
        or any(mode > frozen_num_modes for mode in log_modes)
        or [int(row.get("mode") or 0) for row in api_scores
            if isinstance(row, Mapping)] != log_modes
        or [int(row.get("mode") or 0) for row in csv_scores] != log_modes
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_MODE_SEQUENCE_INVALID",
            "Result modes are not unique, continuous, bounded, and aligned.",
            details={
                "log_modes": log_modes,
                "output_modes": output_modes,
                "frozen_num_modes": frozen_num_modes,
            },
        )
    output_by_mode = {
        int(model["mode"]): model
        for model in parsed_models
        if isinstance(model, Mapping)
    }
    for log_row, api_row, csv_row in zip(
        log_scores,
        api_scores,
        csv_scores,
        strict=True,
    ):
        if not isinstance(log_row, Mapping) or not isinstance(api_row, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
                "A log or API score row is invalid.",
            )
        mode = int(log_row.get("mode") or 0)
        pose_available = mode in output_by_mode
        if (
            int(api_row.get("mode") or 0) != mode
            or int(csv_row.get("mode") or 0) != mode
            or api_row.get("pose_available") is not pose_available
            or csv_row.get("pose_available") is not pose_available
            or api_row.get("score_is_joint") is not True
            or api_row.get("per_member_scores_available") is not False
            or csv_row.get("score_scope") != "joint_two_ligand_pose"
            or not _score_triplets_match(
                log_row,
                api_row,
                left_affinity_key="affinity_kcal_mol",
                right_affinity_key="joint_affinity_kcal_mol",
            )
            or not _score_triplets_match(
                api_row,
                csv_row,
                left_affinity_key="joint_affinity_kcal_mol",
                right_affinity_key="joint_affinity_kcal_mol",
            )
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORE_ARTIFACT_MISMATCH",
                f"Mode {mode} log, API, and scores.csv rows differ.",
            )
        output_model = output_by_mode.get(mode)
        if output_model is not None and not _score_triplets_match(
            log_row,
            output_model,
            left_affinity_key="affinity_kcal_mol",
            right_affinity_key="joint_affinity_kcal_mol",
            tolerance=0.002,
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORE_OUTPUT_MISMATCH",
                f"Mode {mode} log scores differ from out.pdbqt.",
            )
    if set(output_modes) - set(log_modes):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_SCORE_ROW_MISSING",
            "An output model has no corresponding log score row.",
        )

    center = expected_box.get("center")
    effective_size = expected_box.get("effective_size")
    frozen_box = metadata.get("box")
    if (
        not isinstance(center, Mapping)
        or not isinstance(effective_size, Mapping)
        or not isinstance(frozen_box, Mapping)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
            "The frozen scientific Box contract is missing.",
        )
    bounds: dict[str, tuple[float, float]] = {}
    for axis in ("x", "y", "z"):
        try:
            center_value = float(center[axis])
            size_value = float(effective_size[axis])
            frozen_center = float(frozen_box[f"center_{axis}"])
            frozen_size = float(frozen_box[f"size_{axis}"])
        except (KeyError, TypeError, ValueError) as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                "The frozen scientific Box contains an invalid value.",
                details={"axis": axis, "error": str(exc)},
            )
        if (
            not all(
                math.isfinite(value)
                for value in (
                    center_value,
                    size_value,
                    frozen_center,
                    frozen_size,
                )
            )
            or size_value <= 0
            or abs(frozen_center - center_value) > 0.000001
            or abs(frozen_size - size_value) > 0.000001
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                "The project did not freeze the expected effective Box.",
                details={
                    "axis": axis,
                    "expected_center": center_value,
                    "actual_center": frozen_center,
                    "expected_size": size_value,
                    "actual_size": frozen_size,
                },
            )
        bounds[axis] = (
            center_value - size_value / 2.0,
            center_value + size_value / 2.0,
        )
    heavy_atom_count = 0
    for model in parsed_models:
        assert isinstance(model, Mapping)
        for member in model.get("members") or []:
            if not isinstance(member, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                    "A parsed output member is invalid.",
                )
            count = int(member.get("heavy_coordinate_count") or 0)
            atom_count = int(member.get("heavy_atom_count") or 0)
            member_bounds = member.get("heavy_coordinate_bounds")
            if (
                count <= 0
                or count != atom_count
                or not isinstance(member_bounds, Mapping)
                or not isinstance(member_bounds.get("min"), Mapping)
                or not isinstance(member_bounds.get("max"), Mapping)
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                    "A parsed member lacks complete movable-heavy-atom bounds.",
                )
            heavy_atom_count += count
            for axis, (lower, upper) in bounds.items():
                try:
                    observed_min = float(member_bounds["min"][axis])
                    observed_max = float(member_bounds["max"][axis])
                except (KeyError, TypeError, ValueError) as exc:
                    _fail(
                        "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                        "A parsed member has invalid heavy-atom bounds.",
                        details={"axis": axis, "error": str(exc)},
                    )
                if (
                    not math.isfinite(observed_min)
                    or not math.isfinite(observed_max)
                    or observed_min < lower - OUTPUT_BOX_TOLERANCE_ANGSTROM
                    or observed_max > upper + OUTPUT_BOX_TOLERANCE_ANGSTROM
                ):
                    _fail(
                        "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
                        "An independently parsed movable heavy atom is outside the Box.",
                        details={
                            "mode": model.get("mode"),
                            "member_index": member.get("member_index"),
                            "axis": axis,
                            "observed_min": observed_min,
                            "observed_max": observed_max,
                            "box_min": lower,
                            "box_max": upper,
                            "tolerance_angstrom": (
                                OUTPUT_BOX_TOLERANCE_ANGSTROM
                            ),
                        },
                    )
    return {
        "artifacts": audited_artifacts,
        "joint": joint,
        "parsed_output": parsed_output,
        "parsed_models_by_mode": parsed_models_by_mode,
        "member_pose_artifacts": member_pose_artifacts,
        "parsed_log_scores": [dict(row) for row in log_scores],
        "parsed_csv_scores": csv_scores,
        "report_semantics": report_semantics,
        "box_coverage": {
            "method": (
                "independent_attested_output_heavy_bounds_vs_"
                "frozen_effective_box_v1"
            ),
            "tolerance_angstrom": OUTPUT_BOX_TOLERANCE_ANGSTROM,
            "audited_model_count": len(parsed_models),
            "audited_member_pose_count": audited_pose_count,
            "audited_movable_heavy_atom_count": heavy_atom_count,
            "all_output_movable_heavy_atoms_inside_effective_grid": True,
        },
    }


def _audit_report_bytes(
    payload: bytes,
    *,
    scores: Sequence[Mapping[str, Any]],
    expected_run_id: str,
) -> dict[str, Any]:
    """Validate the exact scientific semantics rendered in the run report."""

    report_text = _strict_utf8(payload, label="multi_ligand_report.md")
    if "\x00" in report_text:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
            "The 4DM3 report contains a NUL byte.",
        )
    required_single_lines = (
        "# 多配体共同对接（实验性）报告",
        f"- Run：`{expected_run_id}`",
        "## 协议边界",
        "本报告来自一次 Vina 搜索中的两个配体共同优化，不是串行批量筛选。",
        (
            "本协议的 affinity 与 RMSD 均属于两个配体组成的联合构象；"
            "DockStart 不提供单个配体的独立评分贡献。"
        ),
        (
            "当前实验版本仅支持两个唯一 PDBQT 配体、刚性受体、"
            "Vina/Vinardo 评分和全局搜索。"
        ),
        "## 联合评分",
        (
            "RMSD 表示整个两配体联合构象相对最佳联合模式的差异，"
            "不是任一成员的单独 RMSD。"
        ),
        "## 科学说明",
        "Docking score 仅供结构结合趋势参考，不能替代实验验证。",
        (
            "共同对接结果不能直接证明协同结合、同时占位、药效、"
            "安全性或临床价值。"
        ),
    )
    lines = report_text.splitlines()
    missing_or_repeated = {
        line: lines.count(line)
        for line in required_single_lines
        if lines.count(line) != 1
    }
    if missing_or_repeated:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
            "The 4DM3 report is missing or repeating a required boundary.",
            details={"line_counts": missing_or_repeated},
        )
    forbidden_patterns = (
        r"(?i)(?:member|成员)[^\r\n|]{0,24}(?:affinity|亲和力)",
        r"(?i)(?:affinity|亲和力)[^\r\n|]{0,24}(?:member|成员)",
    )
    forbidden = [
        pattern
        for pattern in forbidden_patterns
        if re.search(pattern, report_text)
    ]
    if forbidden:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
            "The 4DM3 report exposes or implies a per-member affinity.",
            details={"matched_patterns": forbidden},
        )

    table_header = (
        "| Mode | 联合 affinity (kcal/mol) | RMSD l.b. | RMSD u.b. | "
        "构象可加载 |"
    )
    try:
        header_index = lines.index(table_header)
    except ValueError:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
            "The 4DM3 report lacks the exact joint-score table header.",
        )
    if (
        header_index + 1 >= len(lines)
        or lines[header_index + 1] != "|---:|---:|---:|---:|---|"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
            "The 4DM3 joint-score table separator changed.",
        )
    parsed_rows: list[dict[str, Any]] = []
    for line in lines[header_index + 2 :]:
        if not line:
            break
        if not line.startswith("|") or not line.endswith("|"):
            _fail(
                "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
                "The 4DM3 joint-score table contains malformed content.",
                details={"line": line},
            )
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 5 or cells[4] not in {"是", "否"}:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
                "A 4DM3 joint-score report row has invalid columns.",
                details={"line": line},
            )
        try:
            row = {
                "mode": int(cells[0]),
                "joint_affinity_kcal_mol": float(cells[1]),
                "rmsd_lb": float(cells[2]),
                "rmsd_ub": float(cells[3]),
                "pose_available": cells[4] == "是",
            }
        except ValueError as exc:
            _fail(
                "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
                "A 4DM3 joint-score report row is not numeric.",
                details={"line": line, "error": str(exc)},
            )
        if not all(
            math.isfinite(float(row[key]))
            for key in (
                "joint_affinity_kcal_mol",
                "rmsd_lb",
                "rmsd_ub",
            )
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_REPORT_SEMANTICS_INVALID",
                "A 4DM3 joint-score report row is non-finite.",
                details={"line": line},
            )
        parsed_rows.append(row)

    expected_rows: list[dict[str, Any]] = []
    for score in scores:
        if not isinstance(score, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
                "A score row used for report validation is invalid.",
            )
        affinity, rmsd_lb, rmsd_ub = _score_triplet(
            score,
            affinity_key="joint_affinity_kcal_mol",
        )
        expected_rows.append(
            {
                "mode": int(score.get("mode") or 0),
                "joint_affinity_kcal_mol": affinity,
                "rmsd_lb": rmsd_lb,
                "rmsd_ub": rmsd_ub,
                "pose_available": score.get("pose_available") is True,
            }
        )
    if parsed_rows != expected_rows:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPORT_SCORE_MISMATCH",
            "The 4DM3 report joint-score rows differ from attested results.",
            details={
                "expected_row_count": len(expected_rows),
                "actual_row_count": len(parsed_rows),
            },
        )
    return {
        "run_id": expected_run_id,
        "protocol_status": "Experimental",
        "score_scope": "joint_two_ligand_pose",
        "per_member_affinity_available": False,
        "reported_joint_score_row_count": len(parsed_rows),
        "reported_pose_available_count": sum(
            1 for row in parsed_rows if row["pose_available"]
        ),
        "exact_scientific_disclaimer_present": True,
    }


def _run_one_project_job(
    project_root: Path,
    run_record: Mapping[str, Any],
    receptor_pdbqt: Path,
    rco_pdbqt: Path,
    imd_pdbqt: Path,
    topologies: Mapping[str, Mapping[str, Any]],
    references: Mapping[str, Any],
    protocol_parameters: Mapping[str, Any],
    expected_box: Mapping[str, Any],
    expected_vina_path: Path,
    expected_vina_identity: Mapping[str, Any],
) -> dict[str, Any]:
    seed = int(run_record["seed"])
    parameters = dict(protocol_parameters)
    parameters["seed"] = seed
    _require_api_ok(
        "update_vina_params",
        update_vina_params(str(project_root), parameters),
    )
    prepared = _require_api_ok(
        "prepare_multiple_ligand_run",
        prepare_multiple_ligand_run(
            str(project_root),
            [str(rco_pdbqt), str(imd_pdbqt)],
        ),
    )
    run_id = str(prepared.get("run_id") or "")
    if (
        not run_id
        or prepared.get("status") != "prepared"
        or prepared.get("project_dir") != str(project_root)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PREPARED_RUN_INVALID",
            "The project API returned an invalid prepared-run identity.",
            details={
                "run_id": run_id,
                "status": prepared.get("status"),
                "project_dir": prepared.get("project_dir"),
            },
        )
    prepared_metadata = prepared.get("metadata")
    if (
        not isinstance(prepared_metadata, Mapping)
        or prepared_metadata.get("run_id") != run_id
        or prepared_metadata.get("status") != "prepared"
        or prepared_metadata.get("protocol_id")
        != "simultaneous_multi_ligand"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PREPARED_RUN_INVALID",
            "The prepared metadata identity or protocol is invalid.",
        )
    frozen_vina = prepared_metadata.get("vina")
    expected_parameters = {**protocol_parameters, "seed": seed}
    if not isinstance(frozen_vina, Mapping) or any(
        frozen_vina.get(key) != expected_value
        for key, expected_value in expected_parameters.items()
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PARAMETER_SNAPSHOT_MISMATCH",
            "The project API did not freeze the requested Vina parameters.",
            details={
                "expected": expected_parameters,
                "actual": dict(frozen_vina or {}),
            },
        )
    prepared_input_evidence = _audit_prepared_run_inputs(
        project_root,
        run_id,
        prepared_metadata,
        receptor_pdbqt=receptor_pdbqt,
        rco_pdbqt=rco_pdbqt,
        imd_pdbqt=imd_pdbqt,
        box=expected_box,
        expected_seed=seed,
        expected_vina_path=expected_vina_path,
        expected_vina_identity=expected_vina_identity,
    )
    executed = _require_api_ok(
        "execute_multiple_ligand_run",
        execute_multiple_ligand_run(str(project_root), run_id),
    )
    if (
        executed.get("status") != "finished"
        or int(executed.get("pose_count") or 0) <= 0
        or executed.get("run_id") != run_id
        or executed.get("project_dir") != str(project_root)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_INCOMPLETE",
            "A 4DM3 DockStart run did not finish with joint poses.",
            details={
                "run_key": run_record["run_key"],
                "run_id": run_id,
                "status": executed.get("status"),
                "pose_count": executed.get("pose_count"),
            },
        )
    metadata = executed.get("metadata")
    if not isinstance(metadata, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_METADATA_INVALID",
            "A finished 4DM3 run lacks metadata.",
        )
    if (
        metadata.get("run_id") != run_id
        or metadata.get("protocol_id") != "simultaneous_multi_ligand"
        or metadata.get("status") != "finished"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_METADATA_INVALID",
            "The finished metadata identity or protocol is invalid.",
        )
    finished_input_evidence = _audit_prepared_run_inputs(
        project_root,
        run_id,
        metadata,
        receptor_pdbqt=receptor_pdbqt,
        rco_pdbqt=rco_pdbqt,
        imd_pdbqt=imd_pdbqt,
        box=expected_box,
        expected_seed=seed,
        expected_vina_path=expected_vina_path,
        expected_vina_identity=expected_vina_identity,
    )
    if finished_input_evidence != prepared_input_evidence:
        _fail(
            "MULTIPLE_LIGAND_4DM3_INPUT_SNAPSHOT_CHANGED",
            "A prepared input snapshot changed during execution.",
        )
    finished_artifact_evidence = _audit_finished_run_artifacts(
        project_root,
        run_id,
        metadata,
        expected_vina_path=expected_vina_path,
        expected_vina_identity=expected_vina_identity,
    )
    coverage = metadata.get("output_box_coverage")
    if (
        not isinstance(coverage, Mapping)
        or coverage.get(
            "all_output_movable_heavy_atoms_inside_effective_grid"
        )
        is not True
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_OUTPUT_GRID_GATE_FAILED",
            "A finished 4DM3 run has a heavy atom outside the effective grid.",
            details={"run_key": run_record["run_key"], "coverage": coverage},
        )
    scores = executed.get("scores")
    if not isinstance(scores, list) or not scores:
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
            "A finished 4DM3 run lacks joint score rows.",
        )
    available_modes = executed.get("available_modes")
    metadata_modes = metadata.get("available_modes")
    metadata_scores = metadata.get("scores")
    if (
        not isinstance(available_modes, list)
        or not available_modes
        or available_modes != metadata_modes
        or scores != metadata_scores
        or int(metadata.get("pose_count") or 0)
        != int(executed.get("pose_count") or 0)
        or len(available_modes) != int(executed.get("pose_count") or 0)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_RESULT_CONTRACT_MISMATCH",
            "The execute API and finished metadata disagree.",
            details={
                "executed_available_modes": available_modes,
                "metadata_available_modes": metadata_modes,
                "executed_pose_count": executed.get("pose_count"),
                "metadata_pose_count": metadata.get("pose_count"),
            },
        )
    fixed_result_paths = {
        "output_file": f"runs/{run_id}/out.pdbqt",
        "scores_file": f"runs/{run_id}/scores.csv",
        "joint_poses_file": f"runs/{run_id}/joint_poses.json",
        "report_file": f"runs/{run_id}/multi_ligand_report.md",
    }
    if any(
        str(metadata.get(key) or "") != expected
        for key, expected in fixed_result_paths.items()
    ) or any(
        str(executed.get(key) or "") != expected
        for key, expected in fixed_result_paths.items()
        if key != "output_file"
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_RESULT_CONTRACT_MISMATCH",
            "The execute API returned an unexpected result path.",
            details={
                "expected": fixed_result_paths,
                "metadata": {
                    key: metadata.get(key) for key in fixed_result_paths
                },
                "executed": {
                    key: executed.get(key) for key in fixed_result_paths
                },
            },
        )
    if (
        metadata.get("per_member_scores_available") is not False
        or metadata.get("score_scope") != "joint_two_ligand_pose"
        or metadata.get("rmsd_scope")
        != "joint_pose_relative_to_best_mode"
        or any(
            not isinstance(row, Mapping)
            or row.get("score_is_joint") is not True
            or row.get("per_member_scores_available") is not False
            or any("member" in str(key).lower() and "affinity" in str(key).lower()
                   for key in row)
            for row in scores
        )
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_SEMANTICS_INVALID",
            "The 4DM3 result exposed or implied per-member affinity.",
        )
    result_provenance = _audit_completed_result_provenance(
        project_root,
        run_id,
        metadata,
        frozen_members=prepared_metadata.get("members"),
        api_scores=scores,
        expected_box=expected_box,
        frozen_num_modes=int(expected_parameters["num_modes"]),
    )
    finished_artifact_evidence["artifacts"].update(
        result_provenance["artifacts"]
    )
    joint = result_provenance["joint"]
    if (
        joint.get("run_id") != run_id
        or joint.get("protocol_id") != "simultaneous_multi_ligand"
        or int(joint.get("member_count") or 0) != 2
        or
        joint.get("per_member_scores_available") is not False
        or joint.get("affinity_scope") != "joint_two_ligand_pose"
        or joint.get("rmsd_scope")
        != "joint_pose_relative_to_best_mode"
        or joint.get("output_model_is_pose_authority") is not True
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_SCORE_SEMANTICS_INVALID",
            "The joint pose manifest changed score semantics.",
        )
    if (
        joint.get("available_modes") != available_modes
        or joint.get("scores") != scores
        or joint.get("output_box_coverage") != coverage
        or joint.get("output_normalization")
        != metadata.get("output_normalization")
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_RESULT_CONTRACT_MISMATCH",
            "The joint-pose manifest disagrees with finished metadata.",
        )
    raw_models = joint.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        _fail(
            "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
            "The joint pose manifest contains no models.",
        )
    if (
        len(raw_models) != int(executed.get("pose_count") or 0)
        or [
            int(model.get("mode") or 0)
            for model in raw_models
            if isinstance(model, Mapping)
        ]
        != available_modes
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_RESULT_CONTRACT_MISMATCH",
            "The joint-pose model count or mode list is inconsistent.",
        )
    topological = [topologies["RCO"], topologies["IMD"]]
    models: list[dict[str, Any]] = []
    for raw_model in raw_models:
        if not isinstance(raw_model, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                "A joint model record is invalid.",
            )
        mode = int(raw_model.get("mode") or 0)
        raw_members = raw_model.get("members")
        parsed_model = result_provenance["parsed_models_by_mode"].get(mode)
        parsed_members = (
            parsed_model.get("members")
            if isinstance(parsed_model, Mapping)
            else None
        )
        if (
            not isinstance(raw_members, list)
            or len(raw_members) != 2
            or not isinstance(parsed_members, list)
            or len(parsed_members) != 2
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                "A joint model does not contain exactly two members.",
            )
        members: list[dict[str, Any]] = []
        for expected_index, (raw_member, topology, parsed_member) in enumerate(
            zip(raw_members, topological, parsed_members, strict=True),
            start=1,
        ):
            if (
                not isinstance(raw_member, Mapping)
                or not isinstance(parsed_member, Mapping)
                or int(raw_member.get("member_index") or 0) != expected_index
                or int(parsed_member.get("member_index") or 0)
                != expected_index
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_JOINT_MANIFEST_INVALID",
                    "A joint model member order changed.",
                )
            coordinates = _heavy_coordinates_from_text(
                str(parsed_member.get("content") or ""),
                topology,
                label=f"attested out.pdbqt mode {mode} member {expected_index}",
            )
            members.append(
                {
                    "member_index": expected_index,
                    "heavy_coordinates": coordinates,
                }
            )
        rco_automorphisms = _automorphisms(topologies["RCO"])
        imd_automorphisms = _automorphisms(topologies["IMD"])
        rco_rmsd_by_altloc = {
            altloc: _symmetry_no_fit_rmsd(
                members[0]["heavy_coordinates"],
                reference_coordinates,
                rco_automorphisms,
            )
            for altloc, reference_coordinates in references["RCO"].items()
        }
        imd_rmsd = _symmetry_no_fit_rmsd(
            members[1]["heavy_coordinates"],
            references["IMD"],
            imd_automorphisms,
        )
        models.append(
            {
                "mode": mode,
                "joint_affinity_kcal_mol": float(
                    raw_model.get("joint_affinity_kcal_mol")
                ),
                "rmsd_lb": float(raw_model.get("rmsd_lb")),
                "rmsd_ub": float(raw_model.get("rmsd_ub")),
                "members": members,
                "crystal_no_fit_rmsd": {
                    "RCO": min(rco_rmsd_by_altloc.values()),
                    "RCO_by_altloc": rco_rmsd_by_altloc,
                    "IMD": imd_rmsd,
                },
            }
        )
    science_payload = _canonical_science_payload(
        scores=scores,
        models=models,
    )
    return {
        "run_key": str(run_record["run_key"]),
        "run_id": run_id,
        "seed": seed,
        "repeat_of": str(run_record.get("repeat_of") or ""),
        "status": "finished",
        "pose_count": len(models),
        "scores": scores,
        "models": models,
        "raw_output": finished_artifact_evidence["raw_output"],
        "normalized_output": finished_artifact_evidence["artifacts"][
            "output"
        ],
        "canonical_science_payload": science_payload,
        "canonical_science_payload_sha256": _sha256_bytes(
            _canonical_json_bytes(science_payload)
        ),
        "output_box_coverage": dict(coverage),
        "independent_output_box_coverage": result_provenance[
            "box_coverage"
        ],
        "project_api_evidence": {
            "input_snapshots": finished_input_evidence,
            "artifacts": finished_artifact_evidence["artifacts"],
            "prepared_vina": finished_input_evidence["prepared_vina"],
            "execution_vina": finished_artifact_evidence["execution_vina"],
            "output_normalization": finished_artifact_evidence[
                "output_normalization"
            ],
            "completed_result_provenance": {
                "artifacts": result_provenance["artifacts"],
                "member_pose_artifacts": result_provenance[
                    "member_pose_artifacts"
                ],
                "parsed_log_scores": result_provenance[
                    "parsed_log_scores"
                ],
                "parsed_csv_scores": result_provenance[
                    "parsed_csv_scores"
                ],
                "report_semantics": result_provenance[
                    "report_semantics"
                ],
                "box_coverage": result_provenance["box_coverage"],
            },
        },
        "report": finished_artifact_evidence["artifacts"]["report"],
    }


def _acceptance_thresholds(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    acceptance = manifest.get("acceptance")
    if not isinstance(acceptance, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The acceptance section is invalid.",
        )
    recovery = acceptance.get("crystal_recovery")
    ranking = acceptance.get("ranking_diagnostic")
    repeatability = acceptance.get("same_seed_repeatability")
    stability = acceptance.get("cross_seed_stability")
    completion = acceptance.get("run_completion")
    if not all(
        isinstance(value, Mapping)
        for value in (
            recovery,
            ranking,
            repeatability,
            stability,
            completion,
        )
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The acceptance gate sections are invalid.",
        )
    member_thresholds = recovery.get("member_threshold_angstrom")
    pairwise_thresholds = stability.get(
        "selected_pose_pairwise_diameter_max_angstrom"
    )
    if not isinstance(member_thresholds, Mapping) or not isinstance(
        pairwise_thresholds, Mapping
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The member-specific RMSD thresholds are invalid.",
        )
    thresholds = {
        "rmsd_method": str(recovery.get("method") or ""),
        "maximum_per_member_rmsd_angstrom": {
            label: float(member_thresholds.get(label) or 0)
            for label in ("RCO", "IMD")
        },
        "distinct_seed_count": int(
            completion.get("required_distinct_seed_count") or 0
        ),
        "minimum_top_n_seed_recoveries": int(
            ranking.get("target_distinct_seed_count")
            or 0
        ),
        "top_n": int(ranking.get("top_k") or 0),
        "maximum_rank_considered": int(
            recovery.get("maximum_rank_considered") or 0
        ),
        "selected_pose_policy": str(
            recovery.get("selected_recovered_pose_policy") or ""
        ),
        "maximum_best_affinity_range_kcal_mol": float(
            stability.get("mode_1_joint_affinity_range_max_kcal_mol")
            or 0
        ),
        "maximum_cross_seed_member_rmsd_angstrom": {
            label: float(pairwise_thresholds.get(label) or 0)
            for label in ("RCO", "IMD")
        },
        "ranking_is_nonblocking": (
            ranking.get("hard_gate") is False
            and ranking.get("failure_must_not_fail_overall_acceptance")
            is True
        ),
        "repeat_raw_output_sha256_exact": (
            repeatability.get("raw_output_pdbqt_sha256_must_match") is True
        ),
        "repeat_normalized_output_sha256_exact": (
            repeatability.get(
                "normalized_output_pdbqt_sha256_must_match"
            )
            is True
        ),
        "repeat_canonical_science_payload_exact": (
            repeatability.get(
                "canonical_scientific_payload_must_match"
            )
            is True
            and repeatability.get("parsed_score_rows_must_match") is True
        ),
    }
    expected = {
        "rmsd_method": RMSD_METHOD,
        "maximum_per_member_rmsd_angstrom": {"RCO": 2.0, "IMD": 2.0},
        "distinct_seed_count": 3,
        "minimum_top_n_seed_recoveries": 2,
        "top_n": 5,
        "maximum_rank_considered": 20,
        "selected_pose_policy": (
            "minimum_joint_member_rmsd_quadratic_mean_then_"
            "lowest_rank_tiebreak_v1"
        ),
        "maximum_best_affinity_range_kcal_mol": 1.0,
        "maximum_cross_seed_member_rmsd_angstrom": {
            "RCO": 2.0,
            "IMD": 2.0,
        },
        "ranking_is_nonblocking": True,
        "repeat_raw_output_sha256_exact": True,
        "repeat_normalized_output_sha256_exact": True,
        "repeat_canonical_science_payload_exact": True,
    }
    if thresholds != expected:
        _fail(
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
            "The 4DM3 scientific acceptance thresholds changed.",
            details={"expected": expected, "actual": thresholds},
        )
    return thresholds


def _evaluate_scientific_acceptance(
    runs: Sequence[Mapping[str, Any]],
    topologies: Mapping[str, Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    primaries = [run for run in runs if not run.get("repeat_of")]
    repeats = [run for run in runs if run.get("repeat_of")]
    if len(primaries) != 3 or len(repeats) != 1:
        _fail(
            "MULTIPLE_LIGAND_4DM3_RUN_MATRIX_INVALID",
            "Scientific acceptance received the wrong run matrix.",
        )
    maximum_values = thresholds["maximum_per_member_rmsd_angstrom"]
    if isinstance(maximum_values, Mapping):
        maximum = {
            label: float(maximum_values[label])
            for label in ("RCO", "IMD")
        }
    else:
        maximum = {"RCO": float(maximum_values), "IMD": float(maximum_values)}
    maximum_rank = int(thresholds.get("maximum_rank_considered") or 20)
    selected: list[dict[str, Any]] = []
    for run in primaries:
        recovered = [
            model
            for model in run.get("models") or []
            if int(model.get("mode") or 0) <= maximum_rank
            and float(model["crystal_no_fit_rmsd"]["RCO"]) <= maximum["RCO"]
            and float(model["crystal_no_fit_rmsd"]["IMD"]) <= maximum["IMD"]
        ]
        if not recovered:
            _fail(
                "MULTIPLE_LIGAND_4DM3_CRYSTAL_POSE_NOT_RECOVERED",
                "A distinct seed did not recover both crystallographic members.",
                details={
                    "run_key": run.get("run_key"),
                    "seed": run.get("seed"),
                    "threshold_angstrom": maximum,
                    "maximum_rank_considered": maximum_rank,
                    "mode_rmsd": [
                        {
                            "mode": model.get("mode"),
                            **dict(model.get("crystal_no_fit_rmsd") or {}),
                        }
                        for model in run.get("models") or []
                    ],
                },
            )
        for model in recovered:
            model["joint_recovery_metric_angstrom"] = math.sqrt(
                (
                    float(model["crystal_no_fit_rmsd"]["RCO"]) ** 2
                    + float(model["crystal_no_fit_rmsd"]["IMD"]) ** 2
                )
                / 2.0
            )
        chosen = min(
            recovered,
            key=lambda model: (
                float(model["joint_recovery_metric_angstrom"]),
                int(model["mode"]),
            ),
        )
        selected.append(
            {
                "run": run,
                "model": chosen,
                "first_recovered_rank": min(
                    int(model["mode"]) for model in recovered
                ),
                "recovered_pose_count": len(recovered),
            }
        )
    top_n = int(thresholds["top_n"])
    top_n_count = sum(
        int(item["first_recovered_rank"]) <= top_n for item in selected
    )
    ranking_target_met = top_n_count >= int(
        thresholds["minimum_top_n_seed_recoveries"]
    )
    mode_one_affinities: list[float] = []
    for run in primaries:
        row = next(
            (
                score
                for score in run.get("scores") or []
                if int(score.get("mode") or 0) == 1
            ),
            None,
        )
        if not isinstance(row, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_SCORE_TABLE_INVALID",
                "A distinct seed lacks a Mode 1 score.",
            )
        mode_one_affinities.append(
            float(row.get("joint_affinity_kcal_mol"))
        )
    affinity_range = max(mode_one_affinities) - min(mode_one_affinities)
    if affinity_range > float(
        thresholds["maximum_best_affinity_range_kcal_mol"]
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_AFFINITY_STABILITY_FAILED",
            "Mode 1 joint affinities vary beyond the frozen threshold.",
            details={
                "values": mode_one_affinities,
                "range": affinity_range,
                "maximum": thresholds[
                    "maximum_best_affinity_range_kcal_mol"
                ],
            },
        )
    pairwise: dict[str, list[dict[str, Any]]] = {"RCO": [], "IMD": []}
    for label, member_index in (("RCO", 0), ("IMD", 1)):
        automorphisms = _automorphisms(topologies[label])
        for left, right in combinations(selected, 2):
            left_coordinates = left["model"]["members"][member_index][
                "heavy_coordinates"
            ]
            right_coordinates = right["model"]["members"][member_index][
                "heavy_coordinates"
            ]
            rmsd = _symmetry_no_fit_rmsd(
                left_coordinates,
                right_coordinates,
                automorphisms,
            )
            pairwise[label].append(
                {
                    "left_run_key": left["run"]["run_key"],
                    "right_run_key": right["run"]["run_key"],
                    "rmsd_angstrom": rmsd,
                }
            )
            maximum_pairwise_value = thresholds[
                "maximum_cross_seed_member_rmsd_angstrom"
            ]
            maximum_pairwise = (
                float(maximum_pairwise_value[label])
                if isinstance(maximum_pairwise_value, Mapping)
                else float(maximum_pairwise_value)
            )
            if rmsd > maximum_pairwise:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_CROSS_SEED_STABILITY_FAILED",
                    f"Selected {label} poses are not stable across seeds.",
                    details={
                        "left_run_key": left["run"]["run_key"],
                        "right_run_key": right["run"]["run_key"],
                        "rmsd_angstrom": rmsd,
                        "maximum": maximum_pairwise,
                    },
                )
    repeat = repeats[0]
    primary = next(
        (
            run
            for run in runs
            if run.get("run_key") == repeat.get("repeat_of")
        ),
        None,
    )
    if not isinstance(primary, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPEAT_TARGET_MISSING",
            "The repeat run's primary target is missing.",
        )
    repeat_mismatches = {}
    primary_raw = primary.get("raw_output")
    repeat_raw = repeat.get("raw_output")
    primary_raw_sha = (
        str(primary_raw.get("sha256") or "").lower()
        if isinstance(primary_raw, Mapping)
        else ""
    )
    repeat_raw_sha = (
        str(repeat_raw.get("sha256") or "").lower()
        if isinstance(repeat_raw, Mapping)
        else ""
    )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", primary_raw_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", repeat_raw_sha)
        or int(primary_raw.get("size_bytes") or 0) <= 0
        or int(repeat_raw.get("size_bytes") or 0) <= 0
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPEATABILITY_EVIDENCE_MISSING",
            "The same-seed runs lack valid raw-output identity evidence.",
            details={
                "primary": dict(primary_raw or {}),
                "repeat": dict(repeat_raw or {}),
            },
        )
    if (
        primary_raw_sha != repeat_raw_sha
        or int(primary_raw["size_bytes"]) != int(repeat_raw["size_bytes"])
    ):
        repeat_mismatches["raw_output_sha256"] = {
            "primary": primary_raw_sha,
            "repeat": repeat_raw_sha,
        }
    primary_normalized = primary.get("normalized_output")
    repeat_normalized = repeat.get("normalized_output")
    primary_normalized_sha = (
        str(primary_normalized.get("sha256") or "").lower()
        if isinstance(primary_normalized, Mapping)
        else ""
    )
    repeat_normalized_sha = (
        str(repeat_normalized.get("sha256") or "").lower()
        if isinstance(repeat_normalized, Mapping)
        else ""
    )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", primary_normalized_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", repeat_normalized_sha)
        or int(primary_normalized.get("size_bytes") or 0) <= 0
        or int(repeat_normalized.get("size_bytes") or 0) <= 0
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPEATABILITY_EVIDENCE_MISSING",
            "The same-seed runs lack normalized-output identity evidence.",
        )
    if (
        repeat_normalized_sha != primary_normalized_sha
        or int(repeat_normalized["size_bytes"])
        != int(primary_normalized["size_bytes"])
    ):
        repeat_mismatches["normalized_output_sha256"] = {
            "primary": primary_normalized_sha,
            "repeat": repeat_normalized_sha,
        }
    if (
        repeat.get("canonical_science_payload")
        != primary.get("canonical_science_payload")
    ):
        repeat_mismatches["canonical_science_payload_sha256"] = {
            "primary": primary.get("canonical_science_payload_sha256"),
            "repeat": repeat.get("canonical_science_payload_sha256"),
        }
    if repeat_mismatches:
        _fail(
            "MULTIPLE_LIGAND_4DM3_REPEATABILITY_FAILED",
            "The exact repeat seed changed normalized scientific output.",
            details={"mismatches": repeat_mismatches},
        )
    return {
        "distinct_seeds": [
            {
                "run_key": item["run"]["run_key"],
                "seed": item["run"]["seed"],
                "first_recovered_rank": item["first_recovered_rank"],
                "recovered_pose_count": item["recovered_pose_count"],
                "selected_mode": item["model"]["mode"],
                "selected_joint_recovery_metric_angstrom": item["model"][
                    "joint_recovery_metric_angstrom"
                ],
                "RCO_crystal_rmsd_angstrom": item["model"][
                    "crystal_no_fit_rmsd"
                ]["RCO"],
                "RCO_crystal_rmsd_by_altloc_angstrom": item["model"][
                    "crystal_no_fit_rmsd"
                ]["RCO_by_altloc"],
                "IMD_crystal_rmsd_angstrom": item["model"][
                    "crystal_no_fit_rmsd"
                ]["IMD"],
            }
            for item in selected
        ],
        "top_n": top_n,
        "top_n_recovery_seed_count": top_n_count,
        "ranking_target_required_seed_count": thresholds[
            "minimum_top_n_seed_recoveries"
        ],
        "ranking_target_met": ranking_target_met,
        "ranking_diagnostic_only": True,
        "ranking_interpretation": (
            "The Top-N target is diagnostic, not a hard sampling gate. "
            "A passing result does not establish ranking accuracy."
        ),
        "mode_one_joint_affinities_kcal_mol": mode_one_affinities,
        "mode_one_affinity_range_kcal_mol": affinity_range,
        "cross_seed_pairwise_no_fit_rmsd": pairwise,
        "repeatability": {
            "primary_run_key": primary["run_key"],
            "repeat_run_key": repeat["run_key"],
            "raw_output_sha256": repeat_raw_sha,
            "normalized_output_sha256": repeat_normalized_sha,
            "canonical_science_payload_sha256": repeat[
                "canonical_science_payload_sha256"
            ],
            "run_id_path_timestamp_fields_compared": False,
            "logs_required_to_be_identical": False,
        },
        "all_scientific_gates_passed": True,
    }


def _stat_is_link_or_reparse(details: os.stat_result) -> bool:
    reparse_flag = int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or 0x400
    )
    attributes = int(
        getattr(details, "st_file_attributes", 0) or 0
    )
    return stat.S_ISLNK(details.st_mode) or bool(
        attributes & reparse_flag
    )


def _configure_project(
    work_root: Path,
    receptor_pdbqt: Path,
    box: Mapping[str, Any],
) -> Path:
    project_name = "multiple_ligands_4dm3"
    lexical_work_root = Path(work_root).expanduser()
    if not lexical_work_root.is_absolute():
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The temporary work root must be an absolute path.",
            details={"work_root": str(lexical_work_root)},
        )
    lexical_work_root = lexical_work_root.absolute()
    try:
        work_root_details = os.lstat(lexical_work_root)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The temporary work root does not exist.",
            details={"work_root": str(lexical_work_root), "error": str(exc)},
        )
    if (
        not stat.S_ISDIR(work_root_details.st_mode)
        or _stat_is_link_or_reparse(work_root_details)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The temporary work root is not a plain directory.",
            details={"work_root": str(lexical_work_root)},
        )
    try:
        resolved_work_root = lexical_work_root.resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The temporary work root cannot be resolved.",
            details={"work_root": str(lexical_work_root), "error": str(exc)},
        )
    if resolved_work_root != lexical_work_root:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The temporary work root resolves through another path.",
            details={
                "work_root": str(lexical_work_root),
                "resolved_work_root": str(resolved_work_root),
            },
        )

    expected_project_root = resolved_work_root / project_name
    try:
        os.lstat(expected_project_root)
    except FileNotFoundError:
        pass
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The expected project path cannot be inspected.",
            details={
                "project_dir": str(expected_project_root),
                "error": str(exc),
            },
        )
    else:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_DIR_PREEXISTS",
            "The 4DM3 project directory existed before create_project.",
            details={"project_dir": str(expected_project_root)},
        )

    created = _require_api_ok(
        "create_project",
        create_project(project_name, str(resolved_work_root)),
    )
    returned_text = str(created.get("project_dir") or "")
    returned_project_root = Path(returned_text).expanduser()
    if (
        not returned_text
        or not returned_project_root.is_absolute()
        or returned_project_root != expected_project_root
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_MISMATCH",
            "create_project returned a different project directory.",
            details={
                "expected_project_dir": str(expected_project_root),
                "returned_project_dir": returned_text,
            },
        )
    try:
        project_details = os.lstat(returned_project_root)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The created 4DM3 project directory is missing.",
            details={
                "project_dir": str(returned_project_root),
                "error": str(exc),
            },
        )
    if (
        not stat.S_ISDIR(project_details.st_mode)
        or _stat_is_link_or_reparse(project_details)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The created 4DM3 project path is a link or reparse point.",
            details={"project_dir": str(returned_project_root)},
        )
    try:
        project_root = returned_project_root.resolve(strict=True)
        project_root.relative_to(resolved_work_root)
    except (OSError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            "The created 4DM3 project resolves outside the work root.",
            details={
                "project_dir": str(returned_project_root),
                "error": str(exc),
            },
        )
    if (
        project_root != expected_project_root
        or project_root.parent != resolved_work_root
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_MISMATCH",
            "The created 4DM3 project identity changed after resolution.",
            details={
                "expected_project_dir": str(expected_project_root),
                "resolved_project_dir": str(project_root),
            },
        )
    _require_api_ok(
        "import_receptor_pdbqt",
        import_receptor_pdbqt(str(project_root), str(receptor_pdbqt)),
    )
    center = box["center"]
    size = box["effective_size"]
    _require_api_ok(
        "update_box_params",
        update_box_params(
            str(project_root),
            {
                "center_x": center["x"],
                "center_y": center["y"],
                "center_z": center["z"],
                "size_x": size["x"],
                "size_y": size["y"],
                "size_z": size["z"],
            },
        ),
    )
    return project_root


def _read_attested_local_file(
    path: Path,
    record: Mapping[str, Any],
    *,
    label: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Re-read one non-project input from a stable handle and verify identity."""

    candidate = Path(path).expanduser()
    try:
        lexical = candidate.absolute()
        details = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The final {label} cannot be resolved.",
            details={"path": str(candidate), "error": str(exc)},
        )
    if (
        not stat.S_ISREG(details.st_mode)
        or _stat_is_link_or_reparse(details)
        or not resolved.is_file()
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The final {label} is not a plain regular file.",
            details={"path": str(candidate)},
        )
    try:
        raw_size = record.get("size_bytes")
        if isinstance(raw_size, bool) or raw_size is None:
            raise ValueError("size_bytes is not an integer")
        expected_size = int(raw_size)
        expected_sha256 = str(record.get("sha256") or "").lower()
    except (TypeError, ValueError) as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The expected {label} identity is invalid.",
            details={"record": dict(record), "error": str(exc)},
        )
    if (
        expected_size < 0
        or (not allow_empty and expected_size == 0)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The expected {label} identity is incomplete.",
            details={"record": dict(record)},
        )
    try:
        with resolved.open("rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The final {label} could not be read.",
            details={"path": str(resolved), "error": str(exc)},
        )
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field, None) != getattr(after, field, None)
        for field in identity_fields
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The final {label} changed while it was being read.",
            details={"path": str(resolved)},
        )
    actual = {
        "path": str(resolved),
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
    }
    if (
        actual["size_bytes"] != expected_size
        or actual["sha256"] != expected_sha256
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            f"The final {label} bytes changed after their first audit.",
            details={
                "expected_size_bytes": expected_size,
                "actual_size_bytes": actual["size_bytes"],
                "expected_sha256": expected_sha256,
                "actual_sha256": actual["sha256"],
            },
        )
    return actual


def _reaudit_final_immutable_state(
    *,
    project_root: Path,
    manifest: Mapping[str, Any],
    verified_paths: Mapping[str, Path],
    toolchain_evidence: Mapping[str, Any],
    structure_evidence: Mapping[str, Any],
    ligand_preparation_evidence: Mapping[str, Any],
    receptor_preparation_evidence: Mapping[str, Any],
    prepared_paths: Mapping[str, Path],
    protein_pdb: Path,
    receptor_pdbqt: Path,
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Re-hash every parsed input and run artifact immediately before success."""

    source_files = manifest.get("source_files")
    if not isinstance(source_files, Mapping):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            "The source contract is unavailable for final re-audit.",
        )
    audited_sources: dict[str, Any] = {}
    for key in SOURCE_KEYS:
        source_record = source_files.get(key)
        identity = (
            source_record.get("content_identity")
            if isinstance(source_record, Mapping)
            else None
        )
        path = verified_paths.get(key)
        if not isinstance(identity, Mapping) or not isinstance(path, Path):
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                f"The verified source {key} lacks final identity evidence.",
            )
        audited_sources[key] = _read_attested_local_file(
            path,
            {
                "size_bytes": identity.get("size_bytes"),
                "sha256": identity.get("sha256"),
            },
            label=f"verified source {key}",
        )

    audited_tools: dict[str, Any] = {}
    for key in ("python", "vina"):
        record = toolchain_evidence.get(key)
        if not isinstance(record, Mapping):
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                f"The {key} tool identity is missing at final re-audit.",
            )
        audited_tools[key] = _read_attested_local_file(
            Path(str(record.get("path") or "")),
            record,
            label=f"{key} executable",
        )

    protein_record = structure_evidence.get("protein_pdb")
    combined_record = receptor_preparation_evidence.get(
        "combined_receptor"
    )
    if not isinstance(protein_record, Mapping) or not isinstance(
        combined_record,
        Mapping,
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
            "The receptor preparation identities are incomplete.",
        )
    audited_preparation: dict[str, Any] = {
        "protein_pdb": _read_attested_local_file(
            protein_pdb,
            protein_record,
            label="audited chain-A PDB bridge",
        ),
        "combined_receptor": _read_attested_local_file(
            receptor_pdbqt,
            combined_record,
            label="prepared chain-A plus SAH receptor",
        ),
    }
    for label in ("SAH", "RCO", "IMD"):
        item = ligand_preparation_evidence.get(label)
        record = item.get("pdbqt") if isinstance(item, Mapping) else None
        path = prepared_paths.get(label)
        if not isinstance(record, Mapping) or not isinstance(path, Path):
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                f"The prepared {label} identity is incomplete.",
            )
        audited_preparation[label] = _read_attested_local_file(
            path,
            record,
            label=f"prepared {label} PDBQT",
        )

    audited_runs: list[dict[str, Any]] = []
    seen_relative_paths: set[str] = set()
    for run in runs:
        run_id = str(run.get("run_id") or "")
        project_evidence = run.get("project_api_evidence")
        input_snapshots = (
            project_evidence.get("input_snapshots")
            if isinstance(project_evidence, Mapping)
            else None
        )
        completed = (
            project_evidence.get("completed_result_provenance")
            if isinstance(project_evidence, Mapping)
            else None
        )
        if (
            not run_id
            or not isinstance(input_snapshots, Mapping)
            or not isinstance(completed, Mapping)
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                "A completed run lacks final immutable evidence.",
                details={"run_id": run_id},
            )
        records: list[tuple[str, Mapping[str, Any], bool]] = []
        for key in ("receptor", "config"):
            record = input_snapshots.get(key)
            if not isinstance(record, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                    f"Run {run_id} lacks final {key} evidence.",
                )
            records.append((f"input_{key}", record, False))
        member_inputs = input_snapshots.get("members")
        if not isinstance(member_inputs, list) or len(member_inputs) != 2:
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                f"Run {run_id} lacks two frozen member inputs.",
            )
        for index, record in enumerate(member_inputs, start=1):
            if not isinstance(record, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                    f"Run {run_id} member input {index} is invalid.",
                )
            records.append((f"input_member_{index}", record, False))
        artifacts = completed.get("artifacts")
        member_poses = completed.get("member_pose_artifacts")
        if not isinstance(artifacts, Mapping) or not isinstance(
            member_poses,
            Mapping,
        ):
            _fail(
                "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                f"Run {run_id} result artifact evidence is incomplete.",
            )
        for key, record in artifacts.items():
            if not isinstance(record, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                    f"Run {run_id} artifact {key} is invalid.",
                )
            records.append((str(key), record, str(key) == "stderr"))
        for key, record in member_poses.items():
            if not isinstance(record, Mapping):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                    f"Run {run_id} pose artifact {key} is invalid.",
                )
            records.append((str(key), record, False))
        raw_output = run.get("raw_output")
        if (
            isinstance(raw_output, Mapping)
            and raw_output.get("archived") is True
        ):
            records.append(("raw_output", raw_output, False))

        run_artifacts: dict[str, Any] = {}
        for label, record, allow_empty in records:
            relative_path = str(record.get("relative_path") or "")
            if not relative_path or relative_path in seen_relative_paths:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_FINAL_REAUDIT_FAILED",
                    "Final run evidence contains a missing or duplicate path.",
                    details={
                        "run_id": run_id,
                        "label": label,
                        "relative_path": relative_path,
                    },
                )
            seen_relative_paths.add(relative_path)
            evidence, _payload = _read_attested_project_bytes(
                project_root,
                record,
                label=f"final {run_id} {label}",
                expected_relative_path=relative_path,
                allow_empty=allow_empty,
            )
            run_artifacts[label] = evidence
        audited_runs.append(
            {
                "run_id": run_id,
                "artifact_count": len(run_artifacts),
                "artifacts": run_artifacts,
            }
        )
    return {
        "method": "single_handle_final_rehash_before_success_v1",
        "sources": audited_sources,
        "tools": audited_tools,
        "preparation": audited_preparation,
        "run_count": len(audited_runs),
        "runs": audited_runs,
    }


def verify_multiple_ligands_4dm3(
    source_dir: str | Path | None = None,
    *,
    mmcif_path: str | Path | None = None,
    sah_sdf_path: str | Path | None = None,
    rco_sdf_path: str | Path | None = None,
    imd_sdf_path: str | Path | None = None,
    python_executable: str | Path = DEFAULT_PYTHON,
    vina_executable: str | Path = DEFAULT_VINA,
    manifest_path: str | Path = MANIFEST_PATH,
) -> dict[str, Any]:
    """Execute the complete local 4DM3 acceptance gate."""

    manifest = _load_manifest(manifest_path)
    scientific_boundaries = _scientific_boundary_contract(manifest)
    paths = _resolve_source_paths(
        manifest,
        source_dir=source_dir,
        mmcif_path=mmcif_path,
        sah_sdf_path=sah_sdf_path,
        rco_sdf_path=rco_sdf_path,
        imd_sdf_path=imd_sdf_path,
    )
    steps: dict[str, Any] = {}
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="DockStart_multiple_ligands_4DM3_",
    )
    work_root = Path(temporary_handle.name)
    settings_path = work_root / "dockstart_settings.json"
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    caught: MultipleLigand4dm3AcceptanceError | None = None
    result_payload: dict[str, Any] | None = None
    try:
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    vina=str(Path(vina_executable).expanduser().resolve()),
                    python=str(Path(python_executable).expanduser().resolve()),
                )
            )
        )
        verified_paths: dict[str, Path] = {}

        def verify_sources_step() -> Mapping[str, Any]:
            evidence = _verify_sources(
                manifest,
                paths,
                snapshot_root=work_root / "verified_sources",
            )
            raw_snapshot_paths = evidence.get("snapshot_paths")
            if (
                not isinstance(raw_snapshot_paths, Mapping)
                or set(raw_snapshot_paths) != set(SOURCE_KEYS)
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_SOURCE_SNAPSHOT_FAILED",
                    "The verified source snapshot set is incomplete.",
                )
            for key in SOURCE_KEYS:
                snapshot = _regular_local_file(
                    Path(str(raw_snapshot_paths[key])),
                    f"verified {key} snapshot",
                )
                verified_paths[key] = snapshot
            return evidence

        _execute_step(
            steps,
            "pinned_local_sources",
            verify_sources_step,
        )
        toolchain_evidence: dict[str, Any] = {}

        def toolchain_step() -> Mapping[str, Any]:
            evidence = _verify_toolchain(
                Path(python_executable),
                Path(vina_executable),
                manifest,
                work_root,
            )
            toolchain_evidence.update(evidence)
            return evidence

        _execute_step(
            steps,
            "exact_scientific_toolchain",
            toolchain_step,
        )
        structure: dict[str, Any] = {}
        audit: dict[str, Any] = {}
        protein_pdb_holder: dict[str, Path] = {}

        def prepare_structure_step() -> Mapping[str, Any]:
            protein_pdb, loaded_audit, evidence = _prepare_structure(
                Path(python_executable).expanduser().resolve(),
                verified_paths["structure_mmcif"],
                work_root,
                manifest,
            )
            protein_pdb_holder["path"] = protein_pdb
            audit.update(loaded_audit)
            structure.update(evidence)
            return evidence

        _execute_step(
            steps,
            "audited_chain_a_structure_selection",
            prepare_structure_step,
        )
        prepared_paths: dict[str, Path] = {}
        topologies: dict[str, dict[str, Any]] = {}
        ligand_preparation_evidence: dict[str, Any] = {}

        def prepare_ligands_step() -> Mapping[str, Any]:
            evidence: dict[str, Any] = {}
            rco_altloc_b_override, rco_source_atom_names = (
                _rco_altloc_b_coordinate_override(
                    verified_paths["rco_instance_sdf"],
                    audit,
                )
            )
            for label, source_key in (
                ("SAH", "sah_instance_sdf"),
                ("RCO", "rco_instance_sdf"),
                ("IMD", "imd_instance_sdf"),
            ):
                pdbqt, topology, item_evidence = _prepare_ligand(
                    label,
                    verified_paths[source_key],
                    Path(python_executable).expanduser().resolve(),
                    work_root,
                    manifest,
                    heavy_coordinate_override=(
                        rco_altloc_b_override if label == "RCO" else None
                    ),
                )
                prepared_paths[label] = pdbqt
                topologies[label] = topology
                evidence[label] = item_evidence
            imd_reference_records = (
                audit.get("reference", {}).get("IMD")
                if isinstance(audit.get("reference"), Mapping)
                else None
            )
            if not isinstance(imd_reference_records, list):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_REFERENCE_INVALID",
                    "The IMD crystal reference is missing.",
                )
            imd_source_atom_names = _match_source_to_reference(
                topologies["IMD"],
                imd_reference_records,
                label="IMD pinned SDF",
            )
            rco_symmetry = _add_named_vina_symmetry(
                topologies["RCO"],
                prepared_paths["RCO"],
                rco_source_atom_names,
                _manifest_nonidentity_name_permutation(manifest, "RCO"),
                label="RCO",
            )
            imd_symmetry = _add_named_vina_symmetry(
                topologies["IMD"],
                prepared_paths["IMD"],
                imd_source_atom_names,
                _manifest_nonidentity_name_permutation(manifest, "IMD"),
                label="IMD",
            )
            preparation_contract = manifest.get("preparation_contract")
            ligand_contract = (
                preparation_contract.get("ligands")
                if isinstance(preparation_contract, Mapping)
                else None
            )
            expected_torsions = (
                ligand_contract.get("expected_effective_torsions")
                if isinstance(ligand_contract, Mapping)
                else None
            )
            if not isinstance(expected_torsions, Mapping) or {
                "RCO": int(expected_torsions.get("RCO") or 0),
                "IMD": int(expected_torsions.get("IMD") or 0),
            } != {"RCO": 2, "IMD": 0}:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                    "The prepared-ligand torsion contract must be RCO=2, IMD=0.",
                )
            actual_torsions = {
                label: int(
                    evidence[label]["pdbqt"]["branch_count"]
                )
                for label in ("RCO", "IMD")
            }
            declared_torsdof = {
                label: int(evidence[label]["pdbqt"]["torsdof"])
                for label in ("RCO", "IMD")
            }
            if actual_torsions != {"RCO": 2, "IMD": 0} or (
                declared_torsdof != {"RCO": 2, "IMD": 0}
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_PDBQT_TORSION_MISMATCH",
                    "Meeko did not preserve the frozen RCO/IMD torsion contract.",
                    details={
                        "branch_count": actual_torsions,
                        "torsdof": declared_torsdof,
                    },
                )
            hydrogenation = (
                ligand_contract.get("hydrogenation")
                if isinstance(ligand_contract, Mapping)
                else None
            )
            chemical_states = (
                ligand_contract.get("chemical_states")
                if isinstance(ligand_contract, Mapping)
                else None
            )
            if (
                not isinstance(hydrogenation, Mapping)
                or hydrogenation.get("method") != "RDKit_AddHs"
                or hydrogenation.get("add_coordinates") is not True
                or hydrogenation.get("require_3d_conformer_flag") is not True
                or not isinstance(chemical_states, Mapping)
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
                    "The frozen hydrogenation/chemical-state contract is invalid.",
                )
            actual_chemical_states = {
                label: {
                    "canonical_smiles": str(
                        topologies[label].get(
                            "canonical_isomeric_smiles"
                        )
                        or ""
                    ),
                    "formal_charge": int(
                        topologies[label].get("formal_charge") or 0
                    ),
                    "explicit_h_conformer_is_3d": (
                        topologies[label].get(
                            "explicit_h_conformer_is_3d"
                        )
                        is True
                    ),
                }
                for label in ("RCO", "IMD")
            }
            expected_chemical_states = {
                label: {
                    "canonical_smiles": str(
                        chemical_states.get(label, {}).get(
                            "canonical_smiles"
                        )
                        or ""
                    ),
                    "formal_charge": int(
                        chemical_states.get(label, {}).get(
                            "formal_charge"
                        )
                        or 0
                    ),
                    "explicit_h_conformer_is_3d": True,
                }
                for label in ("RCO", "IMD")
            }
            if actual_chemical_states != expected_chemical_states:
                _fail(
                    "MULTIPLE_LIGAND_4DM3_CHEMICAL_STATE_MISMATCH",
                    "RDKit/Meeko preparation changed the frozen ligand microstate.",
                    details={
                        "expected": expected_chemical_states,
                        "actual": actual_chemical_states,
                    },
                )
            receptor_contract = (
                preparation_contract.get("receptor")
                if isinstance(preparation_contract, Mapping)
                else None
            )
            sah_preparation = (
                receptor_contract.get("sah_preparation")
                if isinstance(receptor_contract, Mapping)
                else None
            )
            if (
                not isinstance(sah_preparation, Mapping)
                or int(sah_preparation.get("prepared_formal_charge") or 0)
                != int(topologies["SAH"].get("formal_charge") or 0)
                or topologies["SAH"].get("explicit_h_conformer_is_3d")
                is not True
            ):
                _fail(
                    "MULTIPLE_LIGAND_4DM3_CHEMICAL_STATE_MISMATCH",
                    "SAH preparation changed the frozen neutral 3D microstate.",
                )
            evidence["RCO"]["prepared_coordinate_source"] = (
                "mmCIF highest-occupancy altloc B"
            )
            evidence["RCO"]["source_atom_names"] = rco_source_atom_names
            evidence["RCO"]["explicit_vina_symmetry"] = rco_symmetry
            evidence["IMD"]["source_atom_names"] = imd_source_atom_names
            evidence["IMD"]["explicit_vina_symmetry"] = imd_symmetry
            evidence["chemical_states"] = actual_chemical_states
            ligand_preparation_evidence.update(evidence)
            return {"members_and_cofactor": evidence}

        _execute_step(
            steps,
            "rdkit_add_h_and_meeko_ligand_preparation",
            prepare_ligands_step,
        )
        references = _prepare_reference_coordinates(audit, topologies)
        _execute_step(
            steps,
            "prepared_input_coordinate_identity",
            lambda: _verify_prepared_input_coordinates(
                prepared_paths,
                topologies,
                references,
            ),
        )
        box = _execute_step(
            steps,
            "crystal_reference_box",
            lambda: _verify_box_contract(references, manifest),
        )
        receptor_holder: dict[str, Path] = {}
        receptor_preparation_evidence: dict[str, Any] = {}

        def prepare_receptor_step() -> Mapping[str, Any]:
            receptor, evidence = _prepare_receptor(
                Path(python_executable).expanduser().resolve(),
                protein_pdb_holder["path"],
                prepared_paths["SAH"],
                work_root,
                int(structure["chain_a_polymer_atom_count"]),
            )
            receptor_holder["path"] = receptor
            receptor_preparation_evidence.update(evidence)
            return evidence

        _execute_step(
            steps,
            "meeko_chain_a_plus_rigid_sah_receptor",
            prepare_receptor_step,
        )
        project_holder: dict[str, Path] = {}

        def project_step() -> Mapping[str, Any]:
            project_root = _configure_project(
                work_root,
                receptor_holder["path"],
                box,
            )
            project_holder["path"] = project_root
            return {
                "project_dir": str(project_root),
                "public_apis": [
                    "create_project",
                    "import_receptor_pdbqt",
                    "update_box_params",
                ],
            }

        _execute_step(
            steps,
            "project_create_import_and_box",
            project_step,
        )
        protocol_parameters = _run_parameters(manifest)
        matrix = _run_matrix(manifest)
        runs: list[dict[str, Any]] = []

        def runs_step() -> Mapping[str, Any]:
            for run_record in matrix:
                runs.append(
                    _run_one_project_job(
                        project_holder["path"],
                        run_record,
                        receptor_holder["path"],
                        prepared_paths["RCO"],
                        prepared_paths["IMD"],
                        topologies,
                        references,
                        protocol_parameters,
                        box,
                        Path(vina_executable).expanduser().resolve(),
                        toolchain_evidence["vina"],
                    )
                )
            return {
                "run_count": len(runs),
                "runs": [
                    {
                        "run_key": run["run_key"],
                        "run_id": run["run_id"],
                        "seed": run["seed"],
                        "repeat_of": run["repeat_of"],
                        "status": run["status"],
                        "pose_count": run["pose_count"],
                        "raw_output": run["raw_output"],
                        "normalized_output": run["normalized_output"],
                        "canonical_science_payload_sha256": run[
                            "canonical_science_payload_sha256"
                        ],
                    }
                    for run in runs
                ],
                "public_apis": [
                    "update_vina_params",
                    "prepare_multiple_ligand_run",
                    "execute_multiple_ligand_run",
                ],
            }

        _execute_step(
            steps,
            "four_project_level_vina_runs",
            runs_step,
        )
        acceptance = _execute_step(
            steps,
            "pose_recovery_stability_and_repeatability",
            lambda: _evaluate_scientific_acceptance(
                runs,
                topologies,
                _acceptance_thresholds(manifest),
            ),
        )
        final_reaudit = _execute_step(
            steps,
            "final_immutable_reaudit",
            lambda: _reaudit_final_immutable_state(
                project_root=project_holder["path"],
                manifest=manifest,
                verified_paths=verified_paths,
                toolchain_evidence=toolchain_evidence,
                structure_evidence=structure,
                ligand_preparation_evidence=(
                    ligand_preparation_evidence
                ),
                receptor_preparation_evidence=(
                    receptor_preparation_evidence
                ),
                prepared_paths=prepared_paths,
                protein_pdb=protein_pdb_holder["path"],
                receptor_pdbqt=receptor_holder["path"],
                runs=runs,
            ),
        )
        result_payload = {
            "ok": True,
            "fixture_id": str(manifest.get("fixture_id") or ""),
            "verification_scope": (
                "metadata_only_public_project_api_real_4dm3_multiseed_gate"
            ),
            "network_or_download_used": False,
            "scientific_interpretation": (
                "One score belongs to one joint RCO+IMD pose; no per-member "
                "affinity is inferred."
            ),
            "scientific_boundaries": scientific_boundaries,
            "acceptance": dict(acceptance),
            "final_immutable_reaudit": dict(final_reaudit),
            "steps": steps,
        }
    except MultipleLigand4dm3AcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001
        caught = MultipleLigand4dm3AcceptanceError(
            "MULTIPLE_LIGAND_4DM3_UNEXPECTED_ERROR",
            "The project-level 4DM3 verifier failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
        caught.steps = dict(steps)
    finally:
        if previous_settings_path is None:
            os.environ.pop(SETTINGS_ENV_VAR, None)
        else:
            os.environ[SETTINGS_ENV_VAR] = previous_settings_path
        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001
            cleanup_error = str(exc)
        cleanup = {
            "path": str(work_root),
            "cleanup_called": True,
            "removed": not work_root.exists(),
            "error": cleanup_error,
        }
    if caught is not None:
        caught.details = {**caught.details, "temporary_cleanup": cleanup}
        if not caught.steps:
            caught.steps = dict(steps)
        raise caught
    if (
        result_payload is None
        or cleanup_error
        or cleanup["removed"] is not True
    ):
        _fail(
            "MULTIPLE_LIGAND_4DM3_TEMPORARY_CLEANUP_FAILED",
            "The temporary 4DM3 project could not be removed.",
            details={"temporary_cleanup": cleanup},
        )
    result_payload["temporary_cleanup"] = cleanup
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the metadata-only 4DM3 simultaneous RCO+IMD scientific gate."
        ),
    )
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument(
        "--source-dir",
        help=(
            "Directory containing manifest-named 4DM3.cif, SAH_A.sdf, "
            "RCO_A.sdf, and IMD_A.sdf files."
        ),
    )
    sources.add_argument(
        "--mmcif",
        help=(
            "Pinned 4DM3.cif. When used, --sah-sdf, --rco-sdf, and "
            "--imd-sdf are also required."
        ),
    )
    parser.add_argument("--sah-sdf")
    parser.add_argument("--rco-sdf")
    parser.add_argument("--imd-sdf")
    parser.add_argument("--python", default=str(DEFAULT_PYTHON))
    parser.add_argument("--vina", default=str(DEFAULT_VINA))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.mmcif and not all(
        (arguments.sah_sdf, arguments.rco_sdf, arguments.imd_sdf)
    ):
        _parser().error(
            "--mmcif requires --sah-sdf, --rco-sdf, and --imd-sdf"
        )
    if arguments.source_dir and any(
        (arguments.sah_sdf, arguments.rco_sdf, arguments.imd_sdf)
    ):
        _parser().error(
            "--source-dir cannot be combined with explicit SDF paths"
        )
    try:
        result = verify_multiple_ligands_4dm3(
            arguments.source_dir,
            mmcif_path=arguments.mmcif,
            sah_sdf_path=arguments.sah_sdf,
            rco_sdf_path=arguments.rco_sdf,
            imd_sdf_path=arguments.imd_sdf,
            python_executable=arguments.python,
            vina_executable=arguments.vina,
        )
    except MultipleLigand4dm3AcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "multiple_ligands_4dm3_external",
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
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "multiple_ligands_4dm3_external",
                    "error": {
                        "code": "MULTIPLE_LIGAND_4DM3_UNEXPECTED_ERROR",
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
