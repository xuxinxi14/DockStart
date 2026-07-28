"""Per-pose hydrated PDBQT water filtering and annotation.

This clean-room postprocessor keeps every Vina ``MODEL`` independent.  It
never changes ``REMARK VINA RESULT`` values, pose order, torsion-tree records
or non-water atom records.  Water identity is determined only from the final
PDBQT atom-type column (``W``), never from the atom name.

For each pose, candidate waters are removed when they are closer than 2.03 Å
to a ligand heavy atom or to a receptor atom whose PDBQT type is not ``HD``.
Remaining candidates sample the minimum W-map value in an index-space cube of
``±round(1 Å / spacing)``.  Values below -0.5 are strong, values below -0.3
are weak, and all others are displaced.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dockstart_core.hydrated_maps import AutoGridMap, parse_autogrid_map
from dockstart_core.persistence import atomic_write_text

WATER_ATOM_TYPE = "W"
LIGAND_HYDROGEN_TYPES = frozenset({"H", "HD", "HS"})
RECEPTOR_EXCLUDED_TYPE = "HD"

OVERLAP_DISTANCE_ANGSTROM = 2.03
MAP_SAMPLE_RADIUS_ANGSTROM = 1.0
STRONG_THRESHOLD = -0.5
WEAK_THRESHOLD = -0.3
MAX_PDBQT_FILE_BYTES = 256 * 1024 * 1024

VINA_RESULT_PATTERN = re.compile(
    r"^\s*REMARK\s+VINA\s+RESULT:\s*"
    r"(?P<affinity>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    r"(?:\s+(?P<rmsd_lb>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?))?"
    r"(?:\s+(?P<rmsd_ub>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?))?"
)


class HydratedPostprocessError(ValueError):
    """A fail-closed PDBQT validation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _PdbqtAtom:
    line_index: int
    line_number: int
    serial: int
    atom_name: str
    atom_type: str
    x: float
    y: float
    z: float

    @property
    def coordinate(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class _Pose:
    mode: int
    rank: int
    start_index: int
    end_index: int
    atoms: tuple[_PdbqtAtom, ...]
    waters: tuple[_PdbqtAtom, ...]
    ligand_heavy_atoms: tuple[_PdbqtAtom, ...]
    raw_affinity: float | None
    rmsd_lb: float | None
    rmsd_ub: float | None


def _error(code: str, message: str) -> HydratedPostprocessError:
    return HydratedPostprocessError(code, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pdbqt(path: str | Path, *, label: str) -> tuple[Path, str]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise _error(
            "HYDRATED_PDBQT_FILE_INVALID",
            f"{label} is not a regular file: {candidate}",
        )
    try:
        size_bytes = candidate.stat().st_size
    except OSError as exc:
        raise _error(
            "HYDRATED_PDBQT_FILE_INVALID",
            f"Unable to inspect {label}: {candidate}",
        ) from exc
    if size_bytes <= 0:
        raise _error(
            "HYDRATED_PDBQT_FILE_EMPTY",
            f"{label} is empty: {candidate}",
        )
    if size_bytes > MAX_PDBQT_FILE_BYTES:
        raise _error(
            "HYDRATED_PDBQT_FILE_TOO_LARGE",
            f"{label} exceeds {MAX_PDBQT_FILE_BYTES} bytes.",
        )
    try:
        return candidate.resolve(), candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _error(
            "HYDRATED_PDBQT_READ_FAILED",
            f"Unable to read {label} as UTF-8 text: {candidate}",
        ) from exc


def _parse_finite_number(token: str, *, label: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise _error(
            "HYDRATED_PDBQT_NUMBER_INVALID",
            f"{label} is not a floating-point number.",
        ) from exc
    if not math.isfinite(value):
        raise _error(
            "HYDRATED_PDBQT_NUMBER_NONFINITE",
            f"{label} must be finite.",
        )
    return value


def _parse_atom(
    line: str,
    *,
    line_index: int,
    line_number: int,
    label: str,
) -> _PdbqtAtom | None:
    record = line[:6].strip().upper()
    if record not in {"ATOM", "HETATM"}:
        return None
    fields = line.split()
    if len(line) < 54 or len(fields) < 2:
        raise _error(
            "HYDRATED_PDBQT_ATOM_INVALID",
            f"{label} line {line_number} is shorter than the PDBQT coordinate "
            "columns.",
        )
    try:
        serial = int(line[6:11].strip())
    except ValueError as exc:
        raise _error(
            "HYDRATED_PDBQT_ATOM_INVALID",
            f"{label} line {line_number} has an invalid atom serial.",
        ) from exc
    if serial <= 0:
        raise _error(
            "HYDRATED_PDBQT_ATOM_INVALID",
            f"{label} line {line_number} has a non-positive atom serial.",
        )
    atom_name = line[12:16].strip()
    if not atom_name:
        raise _error(
            "HYDRATED_PDBQT_ATOM_INVALID",
            f"{label} line {line_number} has an empty atom name.",
        )
    x = _parse_finite_number(
        line[30:38].strip(),
        label=f"{label} line {line_number} X",
    )
    y = _parse_finite_number(
        line[38:46].strip(),
        label=f"{label} line {line_number} Y",
    )
    z = _parse_finite_number(
        line[46:54].strip(),
        label=f"{label} line {line_number} Z",
    )
    atom_type = fields[-1].strip().upper()
    if not atom_type or not re.fullmatch(r"[A-Z][A-Z0-9]*", atom_type):
        raise _error(
            "HYDRATED_PDBQT_ATOM_INVALID",
            f"{label} line {line_number} has an invalid final PDBQT atom type.",
        )
    return _PdbqtAtom(
        line_index=line_index,
        line_number=line_number,
        serial=serial,
        atom_name=atom_name,
        atom_type=atom_type,
        x=x,
        y=y,
        z=z,
    )


def _optional_finite(token: str | None, *, label: str) -> float | None:
    if token is None:
        return None
    return _parse_finite_number(token, label=label)


def _parse_vina_result(
    line: str,
    *,
    line_number: int,
) -> tuple[float, float | None, float | None] | None:
    match = VINA_RESULT_PATTERN.match(line)
    if match is None:
        return None
    affinity = _parse_finite_number(
        match.group("affinity"),
        label=f"hydrated output line {line_number} affinity",
    )
    rmsd_lb = _optional_finite(
        match.group("rmsd_lb"),
        label=f"hydrated output line {line_number} RMSD lower bound",
    )
    rmsd_ub = _optional_finite(
        match.group("rmsd_ub"),
        label=f"hydrated output line {line_number} RMSD upper bound",
    )
    return affinity, rmsd_lb, rmsd_ub


def _parse_branch_pair(
    line: str,
    *,
    line_number: int,
) -> tuple[int, int]:
    fields = line.split()
    if len(fields) != 3:
        raise _error(
            "HYDRATED_PDBQT_TORSION_INVALID",
            f"Hydrated output line {line_number} has an invalid BRANCH record.",
        )
    try:
        first = int(fields[1])
        second = int(fields[2])
    except ValueError as exc:
        raise _error(
            "HYDRATED_PDBQT_TORSION_INVALID",
            f"Hydrated output line {line_number} has non-integer BRANCH "
            "endpoints.",
        ) from exc
    if first <= 0 or second <= 0:
        raise _error(
            "HYDRATED_PDBQT_TORSION_INVALID",
            f"Hydrated output line {line_number} has non-positive BRANCH "
            "endpoints.",
        )
    return first, second


def _parse_hydrated_models(text: str) -> tuple[list[str], tuple[_Pose, ...]]:
    lines = text.splitlines()
    poses: list[_Pose] = []
    seen_modes: set[int] = set()
    current_mode: int | None = None
    current_start = -1
    current_atoms: list[_PdbqtAtom] = []
    current_serials: set[int] = set()
    current_branch_stack: list[tuple[int, int]] = []
    current_branch_endpoints: set[int] = set()
    current_vina_result: tuple[float, float | None, float | None] | None = None
    root_count = 0
    endroot_count = 0
    torsdof_count = 0

    for line_index, line in enumerate(lines):
        line_number = line_index + 1
        stripped = line.strip()
        if stripped.startswith("MODEL"):
            fields = stripped.split()
            if current_mode is not None:
                raise _error(
                    "HYDRATED_PDBQT_MODEL_NESTED",
                    f"Hydrated output line {line_number} starts a nested MODEL.",
                )
            if len(fields) != 2:
                raise _error(
                    "HYDRATED_PDBQT_MODEL_INVALID",
                    f"Hydrated output line {line_number} has an invalid MODEL "
                    "record.",
                )
            try:
                mode = int(fields[1])
            except ValueError as exc:
                raise _error(
                    "HYDRATED_PDBQT_MODEL_INVALID",
                    f"Hydrated output line {line_number} has a non-integer mode.",
                ) from exc
            if mode <= 0 or mode in seen_modes:
                raise _error(
                    "HYDRATED_PDBQT_MODEL_INVALID",
                    "Hydrated output MODEL identifiers must be unique positive "
                    "integers.",
                )
            current_mode = mode
            current_start = line_index
            current_atoms = []
            current_serials = set()
            current_branch_stack = []
            current_branch_endpoints = set()
            current_vina_result = None
            root_count = 0
            endroot_count = 0
            torsdof_count = 0
            seen_modes.add(mode)
            continue

        if stripped == "ENDMDL":
            if current_mode is None:
                raise _error(
                    "HYDRATED_PDBQT_MODEL_INVALID",
                    f"Hydrated output line {line_number} has ENDMDL outside a "
                    "MODEL.",
                )
            if current_branch_stack:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} has unclosed BRANCH records.",
                )
            if root_count != 1 or endroot_count != 1:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} must contain one ROOT/ENDROOT pair.",
                )
            if torsdof_count != 1:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} must contain one TORSDOF record.",
                )
            waters = tuple(
                atom
                for atom in current_atoms
                if atom.atom_type == WATER_ATOM_TYPE
            )
            if not waters:
                raise _error(
                    "HYDRATED_PDBQT_WATER_MISSING",
                    f"MODEL {current_mode} contains no final-type W atoms.",
                )
            water_serials = {atom.serial for atom in waters}
            if water_serials & current_branch_endpoints:
                raise _error(
                    "HYDRATED_PDBQT_WATER_BRANCH_ENDPOINT",
                    f"MODEL {current_mode} contains a W atom used as a BRANCH "
                    "endpoint; topology-safe filtering is not possible.",
                )
            ligand_heavy_atoms = tuple(
                atom
                for atom in current_atoms
                if atom.atom_type != WATER_ATOM_TYPE
                and atom.atom_type not in LIGAND_HYDROGEN_TYPES
            )
            if not ligand_heavy_atoms:
                raise _error(
                    "HYDRATED_PDBQT_LIGAND_HEAVY_ATOM_MISSING",
                    f"MODEL {current_mode} contains no ligand heavy atoms.",
                )
            raw_affinity = (
                current_vina_result[0]
                if current_vina_result is not None
                else None
            )
            rmsd_lb = (
                current_vina_result[1]
                if current_vina_result is not None
                else None
            )
            rmsd_ub = (
                current_vina_result[2]
                if current_vina_result is not None
                else None
            )
            poses.append(
                _Pose(
                    mode=current_mode,
                    rank=len(poses) + 1,
                    start_index=current_start,
                    end_index=line_index,
                    atoms=tuple(current_atoms),
                    waters=waters,
                    ligand_heavy_atoms=ligand_heavy_atoms,
                    raw_affinity=raw_affinity,
                    rmsd_lb=rmsd_lb,
                    rmsd_ub=rmsd_ub,
                )
            )
            current_mode = None
            continue

        atom = _parse_atom(
            line,
            line_index=line_index,
            line_number=line_number,
            label="hydrated output",
        )
        if atom is not None:
            if current_mode is None:
                raise _error(
                    "HYDRATED_PDBQT_ATOM_OUTSIDE_MODEL",
                    f"Hydrated output line {line_number} contains an atom "
                    "outside MODEL/ENDMDL.",
                )
            if atom.serial in current_serials:
                raise _error(
                    "HYDRATED_PDBQT_ATOM_SERIAL_DUPLICATED",
                    f"MODEL {current_mode} repeats atom serial {atom.serial}.",
                )
            current_serials.add(atom.serial)
            current_atoms.append(atom)
            continue

        if current_mode is None:
            continue
        if stripped == "ROOT":
            root_count += 1
        elif stripped == "ENDROOT":
            endroot_count += 1
        elif stripped.startswith("BRANCH"):
            pair = _parse_branch_pair(line, line_number=line_number)
            current_branch_stack.append(pair)
            current_branch_endpoints.update(pair)
        elif stripped.startswith("ENDBRANCH"):
            pair = _parse_branch_pair(line, line_number=line_number)
            if not current_branch_stack or current_branch_stack[-1] != pair:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} has a mismatched ENDBRANCH at line "
                    f"{line_number}.",
                )
            current_branch_stack.pop()
        elif stripped.startswith("TORSDOF"):
            fields = stripped.split()
            if len(fields) != 2:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} has an invalid TORSDOF record.",
                )
            try:
                torsdof = int(fields[1])
            except ValueError as exc:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} has a non-integer TORSDOF.",
                ) from exc
            if torsdof < 0:
                raise _error(
                    "HYDRATED_PDBQT_TORSION_INVALID",
                    f"MODEL {current_mode} has a negative TORSDOF.",
                )
            torsdof_count += 1
        else:
            vina_result = _parse_vina_result(line, line_number=line_number)
            if vina_result is not None:
                if current_vina_result is not None:
                    raise _error(
                        "HYDRATED_PDBQT_VINA_RESULT_DUPLICATED",
                        f"MODEL {current_mode} contains multiple VINA RESULT "
                        "records.",
                    )
                current_vina_result = vina_result

    if current_mode is not None:
        raise _error(
            "HYDRATED_PDBQT_MODEL_UNCLOSED",
            f"MODEL {current_mode} has no ENDMDL.",
        )
    if not poses:
        raise _error(
            "HYDRATED_PDBQT_MODEL_MISSING",
            "Hydrated output contains no complete MODEL/ENDMDL blocks.",
        )
    return lines, tuple(poses)


def _parse_receptor_atoms(text: str) -> tuple[_PdbqtAtom, ...]:
    atoms: list[_PdbqtAtom] = []
    for line_index, line in enumerate(text.splitlines()):
        atom = _parse_atom(
            line,
            line_index=line_index,
            line_number=line_index + 1,
            label="receptor",
        )
        if atom is not None and atom.atom_type != RECEPTOR_EXCLUDED_TYPE:
            atoms.append(atom)
    if not atoms:
        raise _error(
            "HYDRATED_RECEPTOR_ATOMS_MISSING",
            "Receptor contains no non-HD atoms for water-overlap filtering.",
        )
    return tuple(atoms)


def _overlaps(
    water: _PdbqtAtom,
    atoms: Iterable[_PdbqtAtom],
    *,
    cutoff_squared: float,
) -> bool:
    for atom in atoms:
        dx = water.x - atom.x
        dy = water.y - atom.y
        dz = water.z - atom.z
        if (dx * dx) + (dy * dy) + (dz * dz) < cutoff_squared:
            return True
    return False


def _water_record(
    water: _PdbqtAtom,
    *,
    mode: int,
    ligand_heavy_atoms: tuple[_PdbqtAtom, ...],
    receptor_atoms: tuple[_PdbqtAtom, ...],
    water_map: AutoGridMap,
    radius_steps: int,
) -> dict[str, Any]:
    cutoff_squared = OVERLAP_DISTANCE_ANGSTROM**2
    removal_reasons: list[str] = []
    if _overlaps(
        water,
        ligand_heavy_atoms,
        cutoff_squared=cutoff_squared,
    ):
        removal_reasons.append("ligand_heavy_atom_overlap")
    if _overlaps(
        water,
        receptor_atoms,
        cutoff_squared=cutoff_squared,
    ):
        removal_reasons.append("receptor_non_hd_atom_overlap")

    map_sample: dict[str, Any] | None = None
    classification = "displaced"
    if not removal_reasons:
        map_sample = water_map.minimum_near(
            water.x,
            water.y,
            water.z,
            radius_steps=radius_steps,
        )
        if map_sample is None:
            removal_reasons.append("outside_water_map")
        else:
            map_affinity = float(map_sample["value"])
            if map_affinity < STRONG_THRESHOLD:
                classification = "strong"
            elif map_affinity < WEAK_THRESHOLD:
                classification = "weak"
            else:
                removal_reasons.append("water_map_not_favorable")

    retained = classification in {"strong", "weak"}
    return {
        "id": f"mode_{mode}:serial_{water.serial}",
        "mode": mode,
        "serial": water.serial,
        "atom_name": water.atom_name,
        "atom_type": water.atom_type,
        "coordinate": {
            "x": water.x,
            "y": water.y,
            "z": water.z,
        },
        "classification": classification,
        "retained": retained,
        "map_affinity": (
            float(map_sample["value"])
            if map_sample is not None
            else None
        ),
        "map_sample": map_sample,
        "removal_reasons": removal_reasons,
        "raw_line_number": water.line_number,
    }


def _annotation(record: dict[str, Any]) -> str:
    return (
        "REMARK DOCKSTART WATER "
        f"MODE {record['mode']} "
        f"SERIAL {record['serial']} "
        f"CLASS {str(record['classification']).upper()} "
        f"MAP_AFFINITY {float(record['map_affinity']):.4f}"
    )


def _validate_outputs(
    retained_output_path: str | Path,
    water_free_output_path: str | Path,
    *,
    source_paths: set[Path],
) -> tuple[Path, Path]:
    retained = Path(retained_output_path).expanduser()
    water_free = Path(water_free_output_path).expanduser()
    retained_resolved = retained.resolve(strict=False)
    water_free_resolved = water_free.resolve(strict=False)
    if retained_resolved == water_free_resolved:
        raise _error(
            "HYDRATED_POSTPROCESS_OUTPUT_COLLISION",
            "Retained-water and water-free outputs must be different files.",
        )
    if retained_resolved in source_paths or water_free_resolved in source_paths:
        raise _error(
            "HYDRATED_POSTPROCESS_OUTPUT_COLLISION",
            "Postprocessing outputs must not replace an input file.",
        )
    for output in (retained, water_free):
        if output.exists() and (output.is_symlink() or not output.is_file()):
            raise _error(
                "HYDRATED_POSTPROCESS_OUTPUT_INVALID",
                f"Postprocessing destination is not a regular file path: "
                f"{output}",
            )
    return retained, water_free


def postprocess_hydrated_pdbqt(
    hydrated_output_path: str | Path,
    receptor_pdbqt_path: str | Path,
    water_map_path: str | Path,
    retained_output_path: str | Path,
    water_free_output_path: str | Path,
) -> dict[str, Any]:
    """Filter W atoms per MODEL and atomically publish two derived PDBQTs.

    The returned ``manifest`` is JSON-serializable evidence for the caller to
    publish alongside its run metadata.  This core function intentionally does
    not write project state or invoke external tools.
    """

    hydrated_path, hydrated_text = _read_pdbqt(
        hydrated_output_path,
        label="hydrated docking output",
    )
    receptor_path, receptor_text = _read_pdbqt(
        receptor_pdbqt_path,
        label="receptor PDBQT",
    )
    water_map = parse_autogrid_map(water_map_path)
    retained_path, water_free_path = _validate_outputs(
        retained_output_path,
        water_free_output_path,
        source_paths={
            hydrated_path,
            receptor_path,
            water_map.path,
        },
    )

    raw_lines, poses = _parse_hydrated_models(hydrated_text)
    receptor_atoms = _parse_receptor_atoms(receptor_text)
    radius_steps = round(
        MAP_SAMPLE_RADIUS_ANGSTROM / water_map.geometry.spacing
    )

    retained_records_by_line: dict[int, dict[str, Any]] = {}
    water_line_indices: set[int] = set()
    pose_records: list[dict[str, Any]] = []
    total_candidate = 0
    total_retained = 0
    total_strong = 0
    total_weak = 0

    for pose in poses:
        waters: list[dict[str, Any]] = []
        for water in pose.waters:
            record = _water_record(
                water,
                mode=pose.mode,
                ligand_heavy_atoms=pose.ligand_heavy_atoms,
                receptor_atoms=receptor_atoms,
                water_map=water_map,
                radius_steps=radius_steps,
            )
            waters.append(record)
            water_line_indices.add(water.line_index)
            if record["retained"]:
                retained_records_by_line[water.line_index] = record

        strong_count = sum(
            record["classification"] == "strong"
            for record in waters
        )
        weak_count = sum(
            record["classification"] == "weak"
            for record in waters
        )
        retained_count = strong_count + weak_count
        candidate_count = len(waters)
        total_candidate += candidate_count
        total_retained += retained_count
        total_strong += strong_count
        total_weak += weak_count
        pose_records.append(
            {
                "mode": pose.mode,
                "rank": pose.rank,
                "raw_affinity": pose.raw_affinity,
                "rmsd_lb": pose.rmsd_lb,
                "rmsd_ub": pose.rmsd_ub,
                "candidate_water_count": candidate_count,
                "retained_water_count": retained_count,
                "strong_water_count": strong_count,
                "weak_water_count": weak_count,
                "displaced_water_count": candidate_count - retained_count,
                "waters": waters,
            }
        )

    retained_lines: list[str] = []
    water_free_lines: list[str] = []
    for line_index, line in enumerate(raw_lines):
        if line_index not in water_line_indices:
            retained_lines.append(line)
            water_free_lines.append(line)
            continue
        record = retained_records_by_line.get(line_index)
        if record is not None:
            retained_lines.append(_annotation(record))
            retained_lines.append(line)

    retained_payload = "\n".join(retained_lines) + "\n"
    water_free_payload = "\n".join(water_free_lines) + "\n"
    atomic_write_text(retained_path, retained_payload, encoding="utf-8")
    atomic_write_text(water_free_path, water_free_payload, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "method": "dockstart_hydrated_water_filter_v1",
        "semantics": {
            "affinity": "original_ad4_hydrated_affinity_unchanged",
            "pose_order": "original_model_order_unchanged",
            "retained_output": "strong_and_weak_waters_only",
            "water_free_output": "all_final_type_W_atoms_removed",
        },
        "parameters": {
            "overlap_distance_angstrom": OVERLAP_DISTANCE_ANGSTROM,
            "overlap_rule": "strictly_less_than",
            "receptor_excluded_atom_type": RECEPTOR_EXCLUDED_TYPE,
            "map_sample_radius_angstrom": MAP_SAMPLE_RADIUS_ANGSTROM,
            "map_sample_radius_steps": radius_steps,
            "map_sample_rule": "minimum_in_clipped_index_cube",
            "strong_threshold": STRONG_THRESHOLD,
            "weak_threshold": WEAK_THRESHOLD,
            "threshold_rule": "strictly_less_than",
        },
        "sources": {
            "hydrated_output": {
                "path": str(hydrated_path),
                "size_bytes": hydrated_path.stat().st_size,
                "sha256": _sha256(hydrated_path),
            },
            "receptor": {
                "path": str(receptor_path),
                "size_bytes": receptor_path.stat().st_size,
                "sha256": _sha256(receptor_path),
                "non_hd_atom_count": len(receptor_atoms),
            },
            "water_map": water_map.to_metadata(),
        },
        "summary": {
            "pose_count": len(pose_records),
            "raw_water_count": total_candidate,
            "candidate_water_count": total_candidate,
            "retained_water_count": total_retained,
            "strong_water_count": total_strong,
            "weak_water_count": total_weak,
            "displaced_water_count": total_candidate - total_retained,
        },
        "poses": pose_records,
        "outputs": {
            "retained_water_annotated": {
                "path": str(retained_path.resolve()),
                "size_bytes": retained_path.stat().st_size,
                "sha256": _sha256(retained_path),
            },
            "water_free_ligand": {
                "path": str(water_free_path.resolve()),
                "size_bytes": water_free_path.stat().st_size,
                "sha256": _sha256(water_free_path),
            },
        },
    }
    return {
        "ok": True,
        "manifest": manifest,
        "modes": pose_records,
        "poses": pose_records,
        "summary": manifest["summary"],
        "outputs": manifest["outputs"],
    }


def postprocess_hydrated_output(
    hydrated_output_path: str | Path,
    receptor_pdbqt_path: str | Path,
    water_map_path: str | Path,
    retained_output_path: str | Path,
    water_free_output_path: str | Path,
) -> dict[str, Any]:
    """Compatibility entry point named for the full raw docking output."""

    return postprocess_hydrated_pdbqt(
        hydrated_output_path,
        receptor_pdbqt_path,
        water_map_path,
        retained_output_path,
        water_free_output_path,
    )


__all__ = [
    "MAP_SAMPLE_RADIUS_ANGSTROM",
    "OVERLAP_DISTANCE_ANGSTROM",
    "STRONG_THRESHOLD",
    "WEAK_THRESHOLD",
    "HydratedPostprocessError",
    "postprocess_hydrated_output",
    "postprocess_hydrated_pdbqt",
]
