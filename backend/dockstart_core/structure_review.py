"""Conservative, dependency-free structure facts for run preflight.

This module intentionally reports only facts that can be read from the input
files.  It does not choose a protonation state, tautomer, chain, cofactor, or
water policy and must not be treated as scientific validation.
"""

from __future__ import annotations

import json
import math
import re
import shlex
from pathlib import Path
from typing import Any, Iterable


STANDARD_RESIDUES = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
    "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
    "ASX", "GLX", "SEC", "PYL", "A", "C", "G", "U", "DA", "DC", "DG", "DT",
}
WATER_RESIDUES = {"HOH", "WAT", "H2O", "DOD"}
METAL_ELEMENTS = {
    "LI", "NA", "K", "RB", "CS", "MG", "CA", "SR", "BA", "AL", "GA", "IN",
    "TL", "MN", "FE", "CO", "NI", "CU", "ZN", "CD", "HG", "CR", "MO", "W",
    "V", "TI", "ZR", "HF", "AG", "AU", "PT", "PD", "RU", "RH", "IR", "OS",
}
PDBQT_HYDROGEN_TYPES = {"H", "HD", "HS"}
SDF_CHARGE_CODES = {1: 3, 2: 2, 3: 1, 5: -1, 6: -2, 7: -3}


def _check(
    key: str,
    role: str,
    name: str,
    status: str,
    message: str,
    *,
    detail: str = "",
    evidence: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "role": role,
        "name": name,
        "status": status,
        "message": message,
        "detail": detail,
        "evidence": evidence,
        "blocking": False,
        "requires_manual_review": status in {"warning", "unknown"},
    }


def _safe_project_file(project_root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    relative = Path(relative_path)
    if relative.is_absolute():
        return None
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _element_from_pdb_line(line: str) -> str:
    element = line[76:78].strip().upper() if len(line) >= 78 else ""
    if element:
        return element
    atom_name = line[12:16].strip() if len(line) >= 16 else ""
    letters = re.sub(r"[^A-Za-z]", "", atom_name)
    if not letters:
        return ""
    two = letters[:2].upper()
    return two if two in METAL_ELEMENTS else letters[:1].upper()


def _coordinate_bounds(coordinates: list[tuple[float, float, float]]) -> dict[str, Any] | None:
    if not coordinates:
        return None
    xs, ys, zs = zip(*coordinates)
    return {
        "x": [round(min(xs), 3), round(max(xs), 3)],
        "y": [round(min(ys), 3), round(max(ys), 3)],
        "z": [round(min(zs), 3), round(max(zs), 3)],
    }


def _summarize_receptor_rows(rows: Iterable[dict[str, Any]], *, model_markers: int) -> dict[str, Any]:
    atom_count = 0
    heavy_atom_count = 0
    hydrogen_atom_count = 0
    coordinates: list[tuple[float, float, float]] = []
    chains: set[str] = set()
    models: set[str] = set()
    waters: set[tuple[str, str, str]] = set()
    metals: set[tuple[str, str, str, str]] = set()
    nonstandard: set[tuple[str, str, str]] = set()
    non_polymer_candidates: set[tuple[str, str, str]] = set()
    altlocs: set[str] = set()
    interrupted: set[tuple[str, str, str, str]] = set()
    seen_residues: set[tuple[str, str, str, str]] = set()
    closed_residues: set[tuple[str, str, str, str]] = set()
    previous: tuple[str, str, str, str] | None = None

    for row in rows:
        atom_count += 1
        chain = row.get("chain", "") or "(空)"
        model = row.get("model", "") or "1"
        residue_name = row.get("residue_name", "").upper()
        residue_number = row.get("residue_number", "")
        insertion = row.get("insertion", "")
        group = row.get("group", "ATOM").upper()
        element = row.get("element", "").upper()
        is_hydrogen = element in {"H", "D", "T"}
        heavy_atom_count += not is_hydrogen
        hydrogen_atom_count += is_hydrogen
        coordinate = row.get("coordinate")
        if (
            isinstance(coordinate, tuple)
            and len(coordinate) == 3
            and all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in coordinate)
        ):
            coordinates.append((float(coordinate[0]), float(coordinate[1]), float(coordinate[2])))
        altloc = row.get("altloc", "")
        key = (model, chain, residue_number, insertion)
        if previous is not None and key != previous:
            closed_residues.add(previous)
        if key in closed_residues and key != previous:
            interrupted.add((chain, residue_number, insertion, residue_name))
        previous = key
        seen_residues.add(key)
        chains.add(chain)
        models.add(model)
        if altloc not in {"", ".", "?"}:
            altlocs.add(altloc)
        residue_identity = (chain, residue_number, residue_name)
        if residue_name in WATER_RESIDUES:
            waters.add(residue_identity)
        elif group == "HETATM" and (element in METAL_ELEMENTS or residue_name in METAL_ELEMENTS):
            metals.add((chain, residue_number, residue_name, element or residue_name))
        else:
            if group == "HETATM":
                non_polymer_candidates.add(residue_identity)
            if residue_name not in STANDARD_RESIDUES:
                nonstandard.add(residue_identity)

    model_count = len(models) if atom_count else 0
    if atom_count and model_markers > model_count:
        model_count = model_markers
    return {
        "atom_count": atom_count,
        "heavy_atom_count": heavy_atom_count,
        "hydrogen_atom_count": hydrogen_atom_count,
        "coordinate_count": len(coordinates),
        "has_3d_coordinates": atom_count > 0 and len(coordinates) == atom_count,
        "coordinate_bounds": _coordinate_bounds(coordinates) if atom_count > 0 and len(coordinates) == atom_count else None,
        "residue_count": len(seen_residues),
        "chains": sorted(chains),
        "model_count": model_count,
        "water_residue_count": len(waters),
        "water_residues": [f"{chain}:{number}:{name}" for chain, number, name in sorted(waters)],
        "metals": [
            {"chain": chain, "residue_number": number, "residue_name": name, "element": element}
            for chain, number, name, element in sorted(metals)
        ],
        "nonstandard_residues": [
            {"chain": chain, "residue_number": number, "residue_name": name}
            for chain, number, name in sorted(nonstandard)
        ],
        "alternate_locations": sorted(altlocs),
        "interrupted_residues": [
            {"chain": chain, "residue_number": number, "insertion_code": insertion, "residue_name": name}
            for chain, number, insertion, name in sorted(interrupted)
        ],
        "ion_non_polymer_components": [
            *[
                {"kind": "water", "chain": chain, "residue_number": number, "residue_name": name}
                for chain, number, name in sorted(waters)
            ],
            *[
                {
                    "kind": "metal",
                    "chain": chain,
                    "residue_number": number,
                    "residue_name": name,
                    "element": element,
                }
                for chain, number, name, element in sorted(metals)
            ],
            *[
                {"kind": "non_polymer", "chain": chain, "residue_number": number, "residue_name": name}
                for chain, number, name in sorted(non_polymer_candidates)
            ],
        ],
        # PDB/mmCIF records alone do not prove whether a residue matches a
        # Meeko template.  Keep observable non-standard/interrupted records
        # separate and leave template validation explicitly unperformed.
        "residue_template_anomalies": None,
        "residue_template_check_status": "not_run",
        "nonstandard_chirality_geometry": None,
        "fact_sources": {
            "atom_count": "原始结构",
            "heavy_atom_count": "原始结构",
            "hydrogen_atom_count": "原始结构",
            "coordinate_count": "原始结构",
            "has_3d_coordinates": "原始结构",
            "coordinate_bounds": "原始结构",
            "chains": "原始结构",
            "residue_count": "原始结构",
            "ion_non_polymer_components": "原始结构",
            "alternate_locations": "原始结构",
            "residue_template_anomalies": "Meeko",
            "nonstandard_chirality_geometry": "原始结构",
        },
    }


def _parse_pdb_receptor(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    model = "1"
    model_markers = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        record = line[:6].strip().upper()
        if record == "MODEL":
            model_markers += 1
            model = line[10:14].strip() or str(model_markers)
            continue
        if record not in {"ATOM", "HETATM"}:
            continue
        try:
            coordinate: tuple[float, float, float] | None = (
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            )
        except (TypeError, ValueError):
            coordinate = None
        rows.append(
            {
                "group": record,
                "model": model,
                "chain": line[21:22].strip() if len(line) > 21 else "",
                "residue_name": line[17:20].strip() if len(line) > 19 else "",
                "residue_number": line[22:26].strip() if len(line) > 25 else "",
                "insertion": line[26:27].strip() if len(line) > 26 else "",
                "altloc": line[16:17].strip() if len(line) > 16 else "",
                "element": _element_from_pdb_line(line),
                "coordinate": coordinate,
            },
        )
    return _summarize_receptor_rows(rows, model_markers=model_markers)


def _parse_cif_receptor(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip().lower() != "loop_":
            index += 1
            continue
        index += 1
        headers: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("_"):
            headers.append(lines[index].strip().split()[0])
            index += 1
        if not headers or not any(header.startswith("_atom_site.") for header in headers):
            continue
        header_index = {name: position for position, name in enumerate(headers)}

        def value(tokens: list[str], *names: str) -> str:
            for name in names:
                position = header_index.get(name)
                if position is not None and position < len(tokens):
                    token = tokens[position]
                    return "" if token in {".", "?"} else token
            return ""

        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith("#") or stripped.lower() == "loop_" or stripped.startswith("_"):
                break
            try:
                tokens = shlex.split(stripped, posix=True)
            except ValueError:
                tokens = stripped.split()
            if len(tokens) >= len(headers):
                try:
                    coordinate: tuple[float, float, float] | None = (
                        float(value(tokens, "_atom_site.Cartn_x")),
                        float(value(tokens, "_atom_site.Cartn_y")),
                        float(value(tokens, "_atom_site.Cartn_z")),
                    )
                except ValueError:
                    coordinate = None
                rows.append(
                    {
                        "group": value(tokens, "_atom_site.group_PDB") or "ATOM",
                        "model": value(tokens, "_atom_site.pdbx_PDB_model_num") or "1",
                        "chain": value(tokens, "_atom_site.auth_asym_id", "_atom_site.label_asym_id"),
                        "residue_name": value(tokens, "_atom_site.auth_comp_id", "_atom_site.label_comp_id"),
                        "residue_number": value(tokens, "_atom_site.auth_seq_id", "_atom_site.label_seq_id"),
                        "insertion": value(tokens, "_atom_site.pdbx_PDB_ins_code"),
                        "altloc": value(tokens, "_atom_site.label_alt_id", "_atom_site.auth_alt_id"),
                        "element": value(tokens, "_atom_site.type_symbol"),
                        "coordinate": coordinate,
                    },
                )
            index += 1
        break
    return _summarize_receptor_rows(rows, model_markers=0)


def _parse_sdf_properties(block: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^>\s*<([^>]+)>", lines[index].strip())
        if not match:
            index += 1
            continue
        name = match.group(1).strip()
        index += 1
        values: list[str] = []
        while index < len(lines) and lines[index].strip():
            values.append(lines[index].strip())
            index += 1
        properties[name] = "\n".join(values)
    return properties


def _connected_components(atom_count: int, bonds: list[tuple[int, int]]) -> int | None:
    if atom_count <= 0:
        return None
    adjacency = [set() for _ in range(atom_count)]
    for first, second in bonds:
        if 1 <= first <= atom_count and 1 <= second <= atom_count:
            adjacency[first - 1].add(second - 1)
            adjacency[second - 1].add(first - 1)
    seen: set[int] = set()
    components = 0
    for atom in range(atom_count):
        if atom in seen:
            continue
        components += 1
        stack = [atom]
        seen.add(atom)
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
    return components


def _parse_v2000_mol(block: str) -> dict[str, Any]:
    lines = block.splitlines()
    facts: dict[str, Any] = {
        "format": "mol/sdf-v2000",
        "atom_count": 0,
        "heavy_atom_count": 0,
        "formal_charge": None,
        "fragment_count": None,
        "stereochemistry_encoded": False,
        "undefined_stereochemistry": None,
        "has_3d_coordinates": False,
    }
    if len(lines) < 4:
        return facts
    counts = lines[3]
    try:
        atom_count = int(counts[:3])
        bond_count = int(counts[3:6])
    except ValueError:
        fields = counts.split()
        if len(fields) < 2:
            return facts
        try:
            atom_count, bond_count = int(fields[0]), int(fields[1])
        except ValueError:
            return facts
    atom_lines = lines[4 : 4 + atom_count]
    bond_lines = lines[4 + atom_count : 4 + atom_count + bond_count]
    elements: list[str] = []
    atom_charge_codes: dict[int, int] = {}
    stereo_encoded = False
    has_nonzero_z = False
    for atom_index, line in enumerate(atom_lines, 1):
        fields = line.split()
        element = (line[31:34].strip() if len(line) >= 34 else "") or (fields[3] if len(fields) > 3 else "")
        elements.append(element.upper())
        try:
            z_coordinate = float(line[20:30]) if len(line) >= 30 else float(fields[2])
        except (ValueError, IndexError):
            z_coordinate = 0.0
        has_nonzero_z = has_nonzero_z or abs(z_coordinate) > 1e-6
        try:
            charge_code = int(line[36:39]) if len(line) >= 39 else int(fields[5])
        except (ValueError, IndexError):
            charge_code = 0
        if charge_code in SDF_CHARGE_CODES:
            atom_charge_codes[atom_index] = SDF_CHARGE_CODES[charge_code]
        try:
            parity = int(line[39:42]) if len(line) >= 42 else int(fields[6])
        except (ValueError, IndexError):
            parity = 0
        stereo_encoded = stereo_encoded or parity != 0
    bonds: list[tuple[int, int]] = []
    for line in bond_lines:
        fields = line.split()
        try:
            first = int(line[:3])
            second = int(line[3:6])
        except ValueError:
            if len(fields) < 2:
                continue
            try:
                first, second = int(fields[0]), int(fields[1])
            except ValueError:
                continue
        bonds.append((first, second))
        try:
            stereo = int(line[9:12]) if len(line) >= 12 else int(fields[3])
        except (ValueError, IndexError):
            stereo = 0
        stereo_encoded = stereo_encoded or stereo != 0
    explicit_charges: dict[int, int] = {}
    for line in lines[4 + atom_count + bond_count :]:
        if not line.startswith("M  CHG"):
            continue
        fields = line.split()
        try:
            pair_count = int(fields[2])
            for pair_index in range(pair_count):
                explicit_charges[int(fields[3 + pair_index * 2])] = int(fields[4 + pair_index * 2])
        except (ValueError, IndexError):
            continue
    charges = explicit_charges or atom_charge_codes
    facts.update(
        {
            "atom_count": atom_count,
            "heavy_atom_count": sum(element not in {"H", "D", "T"} for element in elements),
            "formal_charge": sum(charges.values()),
            "fragment_count": _connected_components(atom_count, bonds),
            "stereochemistry_encoded": stereo_encoded,
            "undefined_stereochemistry": None,
            "has_3d_coordinates": "3D" in " ".join(lines[:3]).upper() or has_nonzero_z,
        },
    )
    facts["contains_salt"] = bool(
        isinstance(facts.get("fragment_count"), int) and facts["fragment_count"] > 1
    )
    return facts


def _parse_v3000_mol(block: str) -> dict[str, Any]:
    atom_count = 0
    heavy_atom_count = 0
    charges: list[int] = []
    bonds: list[tuple[int, int]] = []
    stereo_encoded = False
    has_nonzero_z = False
    for line in block.splitlines():
        stripped = line.strip()
        if "M  V30 COUNTS" in stripped:
            fields = stripped.split()
            try:
                atom_count = int(fields[3])
            except (ValueError, IndexError):
                pass
        atom_match = re.match(r"^M\s+V30\s+(\d+)\s+([A-Za-z][A-Za-z]?)\s+[-+0-9.eE]+\s+[-+0-9.eE]+\s+[-+0-9.eE]+", stripped)
        if atom_match:
            element = atom_match.group(2).upper()
            heavy_atom_count += element not in {"H", "D", "T"}
            coordinate_fields = stripped.split()
            try:
                has_nonzero_z = has_nonzero_z or abs(float(coordinate_fields[6])) > 1e-6
            except (ValueError, IndexError):
                pass
            charge_match = re.search(r"\bCHG=([+-]?\d+)", stripped)
            if charge_match:
                charges.append(int(charge_match.group(1)))
            stereo_encoded = stereo_encoded or " CFG=" in stripped
            continue
        bond_match = re.match(r"^M\s+V30\s+\d+\s+\d+\s+(\d+)\s+(\d+)", stripped)
        if bond_match:
            bonds.append((int(bond_match.group(1)), int(bond_match.group(2))))
            stereo_encoded = stereo_encoded or " CFG=" in stripped
    fragment_count = _connected_components(atom_count, bonds)
    return {
        "format": "mol/sdf-v3000",
        "atom_count": atom_count,
        "heavy_atom_count": heavy_atom_count,
        "formal_charge": sum(charges),
        "fragment_count": fragment_count,
        "stereochemistry_encoded": stereo_encoded,
        "undefined_stereochemistry": None,
        "has_3d_coordinates": "3D" in " ".join(block.splitlines()[:3]).upper() or has_nonzero_z,
        "contains_salt": bool(isinstance(fragment_count, int) and fragment_count > 1),
    }


def _parse_ligand_mol(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = [block for block in text.split("$$$$") if block.strip()]
    first = blocks[0] if blocks else text
    facts = _parse_v3000_mol(first) if "V3000" in first else _parse_v2000_mol(first)
    properties = _parse_sdf_properties(first)
    for key in ("PUBCHEM_TOTAL_CHARGE", "FORMAL_CHARGE", "TOTAL_CHARGE"):
        if key in properties:
            try:
                facts["formal_charge"] = int(properties[key].strip())
                facts["formal_charge_source"] = key
            except ValueError:
                pass
            break
    facts["record_count"] = len(blocks) if blocks else 1
    facts["contains_salt"] = bool(
        facts["record_count"] > 1
        or (isinstance(facts.get("fragment_count"), int) and facts["fragment_count"] > 1)
    )
    facts["title"] = first.splitlines()[0].strip() if first.splitlines() else ""
    return facts


def _formal_charge_from_smiles(smiles: str) -> int | None:
    if not smiles:
        return None
    total = 0
    found = False
    for bracket in re.findall(r"\[[^\]]+\]", smiles):
        for sign, digits in re.findall(r"([+-])(\d*)", bracket):
            found = True
            total += (1 if sign == "+" else -1) * (int(digits) if digits else 1)
    return total if found else 0


def _parse_ligand_pdbqt(path: Path) -> dict[str, Any]:
    atom_count = 0
    heavy_atom_count = 0
    torsdof: int | None = None
    roots = 0
    models = 0
    partial_charges: list[float] = []
    smiles = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM"}:
            atom_count += 1
            fields = line.split()
            atom_type = fields[-1].upper() if fields else ""
            heavy_atom_count += atom_type not in PDBQT_HYDROGEN_TYPES
            if len(fields) >= 2:
                try:
                    charge = float(fields[-2])
                    if not math.isfinite(charge):
                        raise ValueError("non-finite partial charge")
                    partial_charges.append(charge)
                except ValueError:
                    pass
        elif upper.startswith("TORSDOF"):
            try:
                torsdof = int(stripped.split()[1])
            except (ValueError, IndexError):
                torsdof = None
        elif upper == "ROOT":
            roots += 1
        elif upper.startswith("MODEL"):
            models += 1
        elif upper.startswith("REMARK SMILES ") and not upper.startswith("REMARK SMILES IDX"):
            smiles = stripped[len("REMARK SMILES ") :].strip()
    fragment_count = len([part for part in smiles.split(".") if part]) if smiles else (roots or None)
    return {
        "atom_count": atom_count,
        "heavy_atom_count": heavy_atom_count,
        "torsdof": torsdof,
        "root_count": roots,
        "model_count": models or (1 if atom_count else 0),
        "remark_smiles": smiles,
        "formal_charge": _formal_charge_from_smiles(smiles),
        "formal_charge_source": "PDBQT REMARK SMILES" if smiles else "",
        "fragment_count": fragment_count,
        "contains_salt": bool(isinstance(fragment_count, int) and fragment_count > 1),
        "partial_charge_sum": round(sum(partial_charges), 4) if partial_charges else None,
        "stereochemistry_encoded": ("@" in smiles) if smiles else None,
        "undefined_stereochemistry": None,
        "has_3d_coordinates": atom_count > 0,
    }


def _parse_receptor_pdbqt(path: Path) -> dict[str, Any]:
    """Read receptor facts that PDBQT actually carries, without inventing chemistry."""

    atom_count = 0
    heavy_atom_count = 0
    hydrogen_atom_count = 0
    coordinates: list[tuple[float, float, float]] = []
    chains: set[str] = set()
    residues: set[tuple[str, str, str, str]] = set()
    atom_types: set[str] = set()
    partial_charges: list[float] = []
    models = 0
    torsdof: int | None = None
    branch_count = 0
    flexible_residue_markers = 0

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        record = line[:6].strip().upper()
        if record in {"ATOM", "HETATM"}:
            atom_count += 1
            fields = line.split()
            atom_type = fields[-1].upper() if fields else ""
            atom_types.add(atom_type)
            is_hydrogen = atom_type in PDBQT_HYDROGEN_TYPES
            heavy_atom_count += not is_hydrogen
            hydrogen_atom_count += is_hydrogen
            chain = line[21:22].strip() if len(line) > 21 else ""
            residue_name = line[17:20].strip() if len(line) > 19 else ""
            residue_number = line[22:26].strip() if len(line) > 25 else ""
            insertion = line[26:27].strip() if len(line) > 26 else ""
            chains.add(chain or "(空)")
            residues.add((chain or "(空)", residue_number, insertion, residue_name))
            try:
                coordinate = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
                if not all(math.isfinite(value) for value in coordinate):
                    raise ValueError("non-finite coordinate")
                coordinates.append(coordinate)
            except (TypeError, ValueError):
                pass
            if len(fields) >= 2:
                try:
                    charge = float(fields[-2])
                    if not math.isfinite(charge):
                        raise ValueError("non-finite partial charge")
                    partial_charges.append(charge)
                except ValueError:
                    pass
        elif upper.startswith("MODEL"):
            models += 1
        elif upper.startswith("TORSDOF"):
            try:
                torsdof = int(stripped.split()[1])
            except (ValueError, IndexError):
                torsdof = None
        elif upper.startswith("BRANCH"):
            branch_count += 1
        elif upper.startswith("BEGIN_RES"):
            flexible_residue_markers += 1

    flexible = flexible_residue_markers > 0 or branch_count > 0
    return {
        "format": "pdbqt",
        "atom_count": atom_count,
        "heavy_atom_count": heavy_atom_count,
        "hydrogen_atom_count": hydrogen_atom_count,
        "coordinate_count": len(coordinates),
        "has_3d_coordinates": atom_count > 0 and len(coordinates) == atom_count,
        "coordinate_bounds": _coordinate_bounds(coordinates) if atom_count > 0 and len(coordinates) == atom_count else None,
        "chains": sorted(chains),
        "residue_count": len(residues),
        "model_count": models or (1 if atom_count else 0),
        "autodock_atom_types": sorted(atom_type for atom_type in atom_types if atom_type),
        "partial_charge_sum": round(sum(partial_charges), 4) if len(partial_charges) == atom_count else None,
        "partial_charge_count": len(partial_charges),
        "receptor_pdbqt_mode": "flexible" if flexible else "rigid",
        "active_torsions": branch_count if flexible else None,
        "activity_torsion_applicable": flexible,
        "flexible_residue_count": flexible_residue_markers,
        "branch_count": branch_count,
        # These fields are deliberately unknown in PDBQT.  They are included so
        # callers cannot mistake a missing key for a negative scientific result.
        "ion_non_polymer_components": None,
        "alternate_locations": None,
        "interrupted_residues": None,
        "water_residue_count": None,
        "metals": None,
        "nonstandard_residues": None,
        "residue_template_anomalies": None,
        "nonstandard_chirality_geometry": None,
        "bond_orders": None,
        "stereochemistry": None,
        "fact_sources": {
            "atom_count": "最终 PDBQT",
            "heavy_atom_count": "最终 PDBQT",
            "hydrogen_atom_count": "最终 PDBQT",
            "coordinate_count": "最终 PDBQT",
            "has_3d_coordinates": "最终 PDBQT",
            "coordinate_bounds": "最终 PDBQT",
            "chains": "最终 PDBQT",
            "residue_count": "最终 PDBQT",
            "autodock_atom_types": "最终 PDBQT",
            "partial_charge_sum": "最终 PDBQT",
            "receptor_pdbqt_mode": "最终 PDBQT",
            "active_torsions": "最终 PDBQT",
        },
    }


def _load_preparation_provenance(project_root: Path, metadata_file: str) -> dict[str, Any]:
    path = _safe_project_file(project_root, metadata_file)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"metadata_file": metadata_file, "readable": False}
    if not isinstance(payload, dict):
        return {"metadata_file": metadata_file, "readable": False}
    return {
        "metadata_file": metadata_file,
        "readable": True,
        "prep_id": str(payload.get("prep_id") or ""),
        "method": str(payload.get("method") or ""),
        "status": str(payload.get("status") or ""),
        "rdkit_version": str(payload.get("rdkit_version") or ""),
        "meeko_version": str(payload.get("meeko_version") or ""),
        "python_source": str(payload.get("python_source") or "unknown"),
        "input_file": str(payload.get("input_file") or ""),
        "output_file": str(payload.get("output_file") or ""),
    }


def _receptor_checks(
    raw: dict[str, Any],
    pdbqt: dict[str, Any],
    raw_evidence: str,
    prepared_evidence: str,
) -> list[dict[str, Any]]:
    facts = pdbqt or raw
    if not facts or not facts.get("atom_count"):
        return [_check("receptor_structure_unavailable", "receptor", "受体结构审查", "unknown", "没有足够的受体结构记录可供审查。", evidence=prepared_evidence or raw_evidence)]
    checks = [
        _check(
            "receptor_pdbqt_geometry",
            "receptor",
            "受体 PDBQT 几何",
            "ok" if pdbqt.get("has_3d_coordinates") else "unknown",
            (
                f"最终 PDBQT 含 {pdbqt.get('atom_count')} 个原子，全部原子均具有可解析三维坐标。"
                if pdbqt.get("has_3d_coordinates")
                else "最终 PDBQT 不存在，或部分原子的三维坐标无法解析。"
            ),
            evidence=prepared_evidence,
        ),
        _check(
            "receptor_chain_model",
            "receptor",
            "链与模型",
            "warning" if facts["model_count"] > 1 else "ok",
            f"读取到 {len(facts['chains'])} 条链、{facts['model_count']} 个模型。" + (" 多模型输入需要人工确认实际使用范围。" if facts["model_count"] > 1 else ""),
            detail=", ".join(facts["chains"]),
            evidence=prepared_evidence or raw_evidence,
        ),
    ]
    if raw:
        interrupted = raw.get("interrupted_residues") or []
        checks.append(
            _check(
                "receptor_continuity",
                "receptor",
                "残基记录连续性",
                "warning" if interrupted else "ok",
                (
                    f"检测到 {len(interrupted)} 个残基记录被不连续片段打断，请在准备前修复。"
                    if interrupted
                    else "未检测到同一残基记录被分散在多个不连续片段。"
                ),
                evidence=raw_evidence,
            ),
        )
    else:
        checks.append(
            _check(
                "receptor_continuity",
                "receptor",
                "残基记录连续性",
                "unknown",
                "当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查残基记录连续性。",
                evidence=prepared_evidence,
            ),
        )
    for key, name, count_key, noun in (
        ("receptor_water", "水分子", "water_residue_count", "个水残基"),
        ("receptor_metals", "金属离子", "metals", "个金属记录"),
        ("receptor_nonstandard", "非标准残基与辅因子", "nonstandard_residues", "个非标准残基/辅因子"),
        ("receptor_altloc", "替代构象", "alternate_locations", "种 altloc 标记"),
    ):
        if not raw:
            checks.append(
                _check(
                    key,
                    "receptor",
                    name,
                    "unknown",
                    f"当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查{name}。",
                    evidence=prepared_evidence,
                ),
            )
            continue
        value = raw.get(count_key, [] if count_key != "water_residue_count" else 0)
        count = value if isinstance(value, int) else len(value)
        checks.append(
            _check(
                key,
                "receptor",
                name,
                "warning" if count else "ok",
                f"检测到 {count} {noun}；请人工决定保留或处理策略。" if count else f"未检测到{name}记录。",
                detail=json.dumps(value, ensure_ascii=False) if count else "",
                evidence=raw_evidence,
            ),
        )
    return checks


def _ligand_checks(raw: dict[str, Any], pdbqt: dict[str, Any], raw_evidence: str, prepared_evidence: str) -> list[dict[str, Any]]:
    source = raw or pdbqt
    checks: list[dict[str, Any]] = []
    heavy_atoms = source.get("heavy_atom_count")
    checks.append(
        _check(
            "ligand_completeness",
            "ligand",
            "配体完整性事实",
            "ok" if isinstance(heavy_atoms, int) and heavy_atoms > 0 else "unknown",
            (
                f"读取到 {heavy_atoms} 个重原子；PDBQT TORSDOF={pdbqt.get('torsdof') if pdbqt.get('torsdof') is not None else '未记录'}。"
                if isinstance(heavy_atoms, int) and heavy_atoms > 0
                else "无法从当前文件确认配体重原子数量。"
            ),
            evidence=raw_evidence or prepared_evidence,
        ),
    )
    charge = raw.get("formal_charge") if raw else pdbqt.get("formal_charge")
    charge_source = raw.get("formal_charge_source") if raw else pdbqt.get("formal_charge_source")
    checks.append(
        _check(
            "ligand_formal_charge",
            "ligand",
            "形式电荷与质子化",
            "ok" if isinstance(charge, int) else "unknown",
            (
                f"来源文件记录的形式电荷为 {charge:+d}；这只是当前输入状态，不代表其适合目标 pH。"
                if isinstance(charge, int)
                else "当前文件未提供可可靠读取的形式电荷；质子化状态需要人工确认。"
            ),
            detail=str(charge_source or ""),
            evidence=raw_evidence or prepared_evidence,
        ),
    )
    raw_charge = raw.get("formal_charge") if raw else None
    pdbqt_charge = pdbqt.get("formal_charge")
    if isinstance(raw_charge, int) and isinstance(pdbqt_charge, int) and raw_charge != pdbqt_charge:
        checks.append(
            _check(
                "ligand_charge_mismatch",
                "ligand",
                "准备前后形式电荷",
                "warning",
                f"raw 文件形式电荷 {raw_charge:+d}，PDBQT REMARK SMILES 为 {pdbqt_charge:+d}；请确认质子化是否符合预期。",
                evidence=f"{raw_evidence}; {prepared_evidence}",
            ),
        )
    fragments = raw.get("fragment_count") if raw else pdbqt.get("fragment_count")
    record_count = int(raw.get("record_count") or 1) if raw else 1
    fragment_warning = isinstance(fragments, int) and fragments > 1 or record_count > 1
    checks.append(
        _check(
            "ligand_fragments",
            "ligand",
            "盐与多片段",
            "warning" if fragment_warning else ("ok" if fragments == 1 and record_count == 1 else "unknown"),
            (
                f"检测到 {fragments if fragments is not None else '未知'} 个连接组分、{record_count} 条分子记录；自动准备只取首条记录，请检查拆盐与主成分选择。"
                if fragment_warning
                else ("当前首条分子记录包含 1 个连接组分。" if fragments == 1 else "无法可靠判断是否包含盐或多个不相连组分。")
            ),
            evidence=raw_evidence or prepared_evidence,
        ),
    )
    stereo = raw.get("stereochemistry_encoded") if raw else pdbqt.get("stereochemistry_encoded")
    checks.append(
        _check(
            "ligand_stereochemistry",
            "ligand",
            "立体化学",
            "ok" if stereo is True else "unknown",
            "来源文件包含立体化学标记；仍需核对其是否对应实验结构。" if stereo is True else "未读到明确立体标记；这不等于分子不存在立体异构，需要人工核对。",
            evidence=raw_evidence or prepared_evidence,
        ),
    )
    checks.append(
        _check(
            "ligand_tautomer",
            "ligand",
            "互变异构状态",
            "unknown",
            "DockStart 未枚举或判定互变异构体；当前结构按来源文件原样进入准备流程。",
            evidence=raw_evidence or prepared_evidence,
        ),
    )
    return checks


def build_structure_review(
    project_dir: str | Path,
    *,
    receptor_file: str,
    ligand_file: str,
    receptor_raw_file: str = "",
    ligand_raw_file: str = "",
    receptor_metadata_file: str = "",
    ligand_metadata_file: str = "",
) -> dict[str, Any]:
    """Build non-blocking, Chinese-friendly structure diagnostics."""

    project_root = Path(project_dir).expanduser().resolve()
    receptor_prepared_path = _safe_project_file(project_root, receptor_file)
    ligand_prepared_path = _safe_project_file(project_root, ligand_file)
    receptor_raw_path = _safe_project_file(project_root, receptor_raw_file)
    ligand_raw_path = _safe_project_file(project_root, ligand_raw_file)

    receptor_raw_facts: dict[str, Any] = {}
    if receptor_raw_path is not None:
        try:
            receptor_raw_facts = _parse_cif_receptor(receptor_raw_path) if receptor_raw_path.suffix.lower() in {".cif", ".mmcif"} else _parse_pdb_receptor(receptor_raw_path)
        except OSError:
            receptor_raw_facts = {}
    receptor_pdbqt_facts: dict[str, Any] = {}
    if receptor_prepared_path is not None:
        try:
            receptor_pdbqt_facts = _parse_receptor_pdbqt(receptor_prepared_path)
        except OSError:
            receptor_pdbqt_facts = {}

    ligand_raw_facts: dict[str, Any] = {}
    if ligand_raw_path is not None and ligand_raw_path.suffix.lower() in {".sdf", ".mol"}:
        try:
            ligand_raw_facts = _parse_ligand_mol(ligand_raw_path)
        except OSError:
            ligand_raw_facts = {}
    ligand_pdbqt_facts: dict[str, Any] = {}
    if ligand_prepared_path is not None:
        try:
            ligand_pdbqt_facts = _parse_ligand_pdbqt(ligand_prepared_path)
        except OSError:
            ligand_pdbqt_facts = {}

    receptor_raw_evidence = str(receptor_raw_path.relative_to(project_root).as_posix()) if receptor_raw_path else ""
    receptor_prepared_evidence = str(receptor_prepared_path.relative_to(project_root).as_posix()) if receptor_prepared_path else ""
    ligand_raw_evidence = str(ligand_raw_path.relative_to(project_root).as_posix()) if ligand_raw_path else ""
    ligand_prepared_evidence = str(ligand_prepared_path.relative_to(project_root).as_posix()) if ligand_prepared_path else ""
    checks = _receptor_checks(
        receptor_raw_facts,
        receptor_pdbqt_facts,
        receptor_raw_evidence,
        receptor_prepared_evidence,
    )
    checks.extend(_ligand_checks(ligand_raw_facts, ligand_pdbqt_facts, ligand_raw_evidence, ligand_prepared_evidence))

    return {
        "scientific_validation": False,
        "disclaimer": "这些检查只汇总文件中可观察的结构事实，不能自动判断质子化、互变异构、链选择或辅因子处理是否科学正确。",
        "receptor": {
            "raw_file": receptor_raw_evidence,
            "prepared_file": receptor_prepared_evidence,
            "source_file": receptor_prepared_evidence or receptor_raw_evidence,
            "source_kind": "prepared" if receptor_prepared_path else ("raw" if receptor_raw_path else "missing"),
            "raw": receptor_raw_facts,
            "pdbqt": receptor_pdbqt_facts,
            # Keep the historical flattened view for report/API compatibility.
            **(receptor_pdbqt_facts or receptor_raw_facts),
        },
        "ligand": {
            "raw_file": ligand_raw_evidence,
            "prepared_file": ligand_prepared_evidence,
            "raw": ligand_raw_facts,
            "pdbqt": ligand_pdbqt_facts,
        },
        "provenance": {
            "receptor": _load_preparation_provenance(project_root, receptor_metadata_file),
            "ligand": _load_preparation_provenance(project_root, ligand_metadata_file),
        },
        "checks": checks,
        "warning_count": sum(item["status"] == "warning" for item in checks),
        "unknown_count": sum(item["status"] == "unknown" for item in checks),
    }
