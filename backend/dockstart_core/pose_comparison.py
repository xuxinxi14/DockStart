"""Dependency-free geometry comparison for Vina ``local_only`` poses.

The input ligand snapshot and optimized ligand already share the receptor
coordinate frame.  Consequently this module never superposes either pose:
translation, rotation, and internal motion are all part of the reported
displacement.

Atom correspondence must be provable.  A complete Meeko ``REMARK SMILES`` /
``REMARK SMILES IDX`` map is preferred.  Files without that evidence fall
back to PDBQT serials only when every ligand atom keeps its full identity.
Coordinates are never used to guess correspondence.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_PDBQT_BYTES = 25 * 1024 * 1024
HYDROGEN_PDBQT_TYPES = frozenset({"H", "HD", "HS"})
NON_PHYSICAL_PDBQT_TYPES = frozenset({"W", "XX"})
GLUE_PSEUDO_TYPE_PATTERN = re.compile(r"^G\d+$", re.IGNORECASE)
PDBQT_ATOM_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*$")


@dataclass(frozen=True)
class _PdbqtAtom:
    record: str
    serial: int
    atom_name: str
    alternate_location: str
    residue_name: str
    chain_id: str
    residue_sequence: str
    insertion_code: str
    atom_type: str
    partial_charge: float
    x: float
    y: float
    z: float

    @property
    def coordinate(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z

    @property
    def excluded_kind(self) -> str:
        if self.atom_type in HYDROGEN_PDBQT_TYPES:
            return "hydrogen"
        if (
            self.atom_type in NON_PHYSICAL_PDBQT_TYPES
            or GLUE_PSEUDO_TYPE_PATTERN.fullmatch(self.atom_type)
        ):
            return "pseudo"
        return ""

    @property
    def identity_without_serial(self) -> tuple[Any, ...]:
        return (
            self.record,
            self.atom_name,
            self.alternate_location,
            self.residue_name,
            self.chain_id,
            self.residue_sequence,
            self.insertion_code,
            self.atom_type,
            round(self.partial_charge, 6),
        )


@dataclass(frozen=True)
class _ParsedPose:
    atoms: tuple[_PdbqtAtom, ...]
    smiles: str
    smiles_index_to_serial: dict[int, int]
    flex_atom_count: int
    source_label: str

    @property
    def atom_by_serial(self) -> dict[int, _PdbqtAtom]:
        return {atom.serial: atom for atom in self.atoms}

    @property
    def heavy_atoms(self) -> tuple[_PdbqtAtom, ...]:
        return tuple(atom for atom in self.atoms if not atom.excluded_kind)

    @property
    def hydrogen_count(self) -> int:
        return sum(atom.excluded_kind == "hydrogen" for atom in self.atoms)

    @property
    def pseudo_atom_count(self) -> int:
        return sum(atom.excluded_kind == "pseudo" for atom in self.atoms)

    @property
    def has_smiles_mapping(self) -> bool:
        return bool(self.smiles and self.smiles_index_to_serial)


def _error(
    code: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "raw_error": raw_error,
            "suggestion": suggestion,
        },
    }


def _role_code(role: str, suffix: str) -> str:
    return f"LOCAL_POSE_{role.upper()}_{suffix}"


def _parse_partial_charge(line: str) -> float:
    fixed_width = line[70:76].strip() if len(line) >= 76 else ""
    candidate = fixed_width
    if not candidate:
        fields = line.split()
        if len(fields) < 2:
            raise ValueError("缺少 PDBQT 部分电荷字段")
        candidate = fields[-2]
    value = float(candidate)
    if not math.isfinite(value):
        raise ValueError("部分电荷不是有限数值")
    return value


def _parse_atom_line(
    line: str,
    *,
    role: str,
    label: str,
    line_number: int,
) -> tuple[_PdbqtAtom | None, dict[str, Any] | None]:
    try:
        if len(line) < 54:
            raise ValueError("原子记录短于坐标字段")
        record = line[:6].strip().upper()
        serial = int(line[6:11].strip())
        if serial <= 0:
            raise ValueError("原子序号必须大于 0")
        atom_name = line[12:16].strip()
        if not atom_name:
            raise ValueError("原子名为空")
        coordinates = (
            float(line[30:38].strip()),
            float(line[38:46].strip()),
            float(line[46:54].strip()),
        )
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("坐标不是有限数值")
        fields = line.split()
        atom_type = fields[-1].strip().upper() if fields else ""
        if not PDBQT_ATOM_TYPE_PATTERN.fullmatch(atom_type):
            raise ValueError("PDBQT 原子类型为空或格式无效")
        partial_charge = _parse_partial_charge(line)
    except (IndexError, TypeError, ValueError) as exc:
        return None, _error(
            _role_code(role, "ATOM_INVALID"),
            f"{label} PDBQT 第 {line_number} 行原子记录无法可靠解析。",
            raw_error=f"{line!r}: {exc}",
            suggestion="请确认文件是完整、标准列宽且包含有限三维坐标的 ligand PDBQT。",
        )

    return (
        _PdbqtAtom(
            record=record,
            serial=serial,
            atom_name=atom_name,
            alternate_location=line[16:17].strip() if len(line) > 16 else "",
            residue_name=line[17:20].strip() if len(line) > 19 else "",
            chain_id=line[21:22].strip() if len(line) > 21 else "",
            residue_sequence=line[22:26].strip() if len(line) > 25 else "",
            insertion_code=line[26:27].strip() if len(line) > 26 else "",
            atom_type=atom_type,
            partial_charge=partial_charge,
            x=coordinates[0],
            y=coordinates[1],
            z=coordinates[2],
        ),
        None,
    )


def _parse_single_ligand_pose_text(
    text: str,
    *,
    role: str,
    label: str,
) -> tuple[_ParsedPose | None, dict[str, Any] | None]:
    """Parse exactly one ligand pose and exclude flexible receptor records."""

    atoms: list[_PdbqtAtom] = []
    serials: set[int] = set()
    smiles_values: list[str] = []
    smiles_index_to_serial: dict[int, int] = {}
    mapped_serials: set[int] = set()
    model_count = 0
    inside_model = False
    model_closed = False
    pose_record_before_model = False
    inside_flex = False
    flex_atom_count = 0
    ligand_root_count = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        upper = stripped.upper()
        record = line[:6].strip().upper()

        if record == "MODEL":
            if inside_flex or inside_model or model_closed or model_count:
                return None, _error(
                    _role_code(role, "MULTIPLE_MODELS"),
                    f"{label} PDBQT 必须只包含一个 ligand pose。",
                    raw_error=f"line {line_number}: {line!r}",
                    suggestion="请使用本次 local_only 的单一输入快照或 optimized.pdbqt。",
                )
            if pose_record_before_model:
                return None, _error(
                    _role_code(role, "MODEL_INVALID"),
                    f"{label} PDBQT 在 MODEL 之前已经出现 ligand 结构记录。",
                    raw_error=f"line {line_number}",
                )
            model_count = 1
            inside_model = True
            continue

        if record == "ENDMDL":
            if not inside_model or inside_flex:
                return None, _error(
                    _role_code(role, "MODEL_INVALID"),
                    f"{label} PDBQT 的 MODEL/ENDMDL 结构不完整。",
                    raw_error=f"line {line_number}: {line!r}",
                )
            inside_model = False
            model_closed = True
            continue

        in_active_pose = (model_count == 0 and not model_closed) or inside_model

        if upper.startswith("BEGIN_RES"):
            if not in_active_pose or inside_flex:
                return None, _error(
                    _role_code(role, "FLEX_BLOCK_INVALID"),
                    f"{label} PDBQT 的 BEGIN_RES/END_RES 结构不完整。",
                    raw_error=f"line {line_number}: {line!r}",
                )
            inside_flex = True
            continue

        if upper.startswith("END_RES"):
            if not inside_flex:
                return None, _error(
                    _role_code(role, "FLEX_BLOCK_INVALID"),
                    f"{label} PDBQT 出现了没有对应 BEGIN_RES 的 END_RES。",
                    raw_error=f"line {line_number}: {line!r}",
                )
            inside_flex = False
            continue

        if record in {"ATOM", "HETATM"}:
            if not in_active_pose:
                return None, _error(
                    _role_code(role, "MULTIPLE_MODELS"),
                    f"{label} PDBQT 在唯一 pose 之外仍包含原子记录。",
                    raw_error=f"line {line_number}: {line!r}",
                )
            if inside_flex:
                flex_atom_count += 1
                continue
            pose_record_before_model = pose_record_before_model or model_count == 0
            atom, atom_error = _parse_atom_line(
                line,
                role=role,
                label=label,
                line_number=line_number,
            )
            if atom_error:
                return None, atom_error
            assert atom is not None
            if atom.serial in serials:
                return None, _error(
                    _role_code(role, "ATOM_ID_DUPLICATE"),
                    f"{label} PDBQT 中存在重复 ligand 原子序号 {atom.serial}。",
                    raw_error=f"line {line_number}",
                )
            serials.add(atom.serial)
            atoms.append(atom)
            continue

        if inside_flex:
            continue

        if upper == "ROOT":
            if not in_active_pose:
                return None, _error(
                    _role_code(role, "MODEL_INVALID"),
                    f"{label} PDBQT 在唯一 pose 之外包含 ROOT。",
                    raw_error=f"line {line_number}",
                )
            pose_record_before_model = pose_record_before_model or model_count == 0
            ligand_root_count += 1
            if ligand_root_count > 1:
                return None, _error(
                    _role_code(role, "MULTIPLE_LIGANDS"),
                    f"{label} PDBQT 包含多个 ligand ROOT，不能视为单一姿势。",
                    raw_error=f"line {line_number}",
                )
            continue

        if upper.startswith("REMARK SMILES IDX"):
            fields = stripped[len("REMARK SMILES IDX") :].split()
            if not fields or len(fields) % 2:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 的 REMARK SMILES IDX 不是完整索引对。",
                    raw_error=f"line {line_number}: {line!r}",
                )
            try:
                pairs = [
                    (int(fields[index]), int(fields[index + 1]))
                    for index in range(0, len(fields), 2)
                ]
            except ValueError as exc:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 的 REMARK SMILES IDX 含非整数值。",
                    raw_error=f"line {line_number}: {exc}",
                )
            for smiles_index, serial in pairs:
                if (
                    smiles_index <= 0
                    or serial <= 0
                    or smiles_index in smiles_index_to_serial
                    or serial in mapped_serials
                ):
                    return None, _error(
                        _role_code(role, "SMILES_MAPPING_INVALID"),
                        f"{label} PDBQT 的 REMARK SMILES IDX 含无效或重复映射。",
                        raw_error=(
                            f"line {line_number}: smiles_index={smiles_index}; serial={serial}"
                        ),
                    )
                smiles_index_to_serial[smiles_index] = serial
                mapped_serials.add(serial)
            continue

        if upper.startswith("REMARK SMILES ") and not upper.startswith(
            "REMARK SMILES IDX"
        ):
            smiles = stripped[len("REMARK SMILES ") :].strip()
            if not smiles:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 的 REMARK SMILES 为空。",
                    raw_error=f"line {line_number}",
                )
            smiles_values.append(smiles)
            if len(smiles_values) > 1:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 包含多个 REMARK SMILES，不能确认唯一 ligand。",
                    raw_error=f"line {line_number}",
                )

    if inside_model:
        return None, _error(
            _role_code(role, "MODEL_INVALID"),
            f"{label} PDBQT 的 MODEL 没有对应 ENDMDL。",
        )
    if inside_flex:
        return None, _error(
            _role_code(role, "FLEX_BLOCK_INVALID"),
            f"{label} PDBQT 的 BEGIN_RES 没有对应 END_RES。",
        )
    if not atoms:
        return None, _error(
            _role_code(role, "NO_ATOMS"),
            f"{label} PDBQT 中没有可比较的 ligand ATOM/HETATM 记录。",
        )

    smiles = smiles_values[0] if smiles_values else ""
    if bool(smiles) != bool(smiles_index_to_serial):
        return None, _error(
            _role_code(role, "SMILES_MAPPING_INVALID"),
            f"{label} PDBQT 的 REMARK SMILES 与 SMILES IDX 不完整。",
            suggestion="请保留 Meeko 写入的完整 REMARK；否则应同时不包含这两类映射记录。",
        )

    pose = _ParsedPose(
        atoms=tuple(atoms),
        smiles=smiles,
        smiles_index_to_serial=smiles_index_to_serial,
        flex_atom_count=flex_atom_count,
        source_label=label,
    )
    if pose.has_smiles_mapping:
        expected_indices = list(range(1, len(smiles_index_to_serial) + 1))
        if sorted(smiles_index_to_serial) != expected_indices:
            return None, _error(
                _role_code(role, "SMILES_MAPPING_INVALID"),
                f"{label} PDBQT 的 SMILES 原子索引不是从 1 开始的连续序列。",
                raw_error=f"indices={sorted(smiles_index_to_serial)}",
            )
        atom_by_serial = pose.atom_by_serial
        mapped_atoms: list[_PdbqtAtom] = []
        for smiles_index in expected_indices:
            serial = smiles_index_to_serial[smiles_index]
            atom = atom_by_serial.get(serial)
            if atom is None:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 的 SMILES 映射指向不存在的原子序号 {serial}。",
                    raw_error=f"smiles_index={smiles_index}",
                )
            if atom.excluded_kind:
                return None, _error(
                    _role_code(role, "SMILES_MAPPING_INVALID"),
                    f"{label} PDBQT 的 SMILES 映射指向氢或伪原子 {serial}。",
                    raw_error=f"atom_type={atom.atom_type}",
                )
            mapped_atoms.append(atom)
        mapped_serial_set = {atom.serial for atom in mapped_atoms}
        heavy_serial_set = {atom.serial for atom in pose.heavy_atoms}
        if mapped_serial_set != heavy_serial_set:
            return None, _error(
                _role_code(role, "SMILES_MAPPING_INVALID"),
                f"{label} PDBQT 的 SMILES 映射没有完整覆盖所有真实重原子。",
                raw_error=(
                    f"mapped={sorted(mapped_serial_set)}; heavy={sorted(heavy_serial_set)}"
                ),
            )

    if not pose.heavy_atoms:
        return None, _error(
            _role_code(role, "NO_HEAVY_ATOMS"),
            f"{label} PDBQT 中没有可比较的真实重原子。",
        )
    return pose, None


def _identity_mismatch(
    input_atom: _PdbqtAtom,
    optimized_atom: _PdbqtAtom,
) -> str:
    fields = (
        "record",
        "atom_name",
        "alternate_location",
        "residue_name",
        "chain_id",
        "residue_sequence",
        "insertion_code",
        "atom_type",
    )
    differences = [
        f"{field}: {getattr(input_atom, field)!r} -> {getattr(optimized_atom, field)!r}"
        for field in fields
        if getattr(input_atom, field) != getattr(optimized_atom, field)
    ]
    if not math.isclose(
        input_atom.partial_charge,
        optimized_atom.partial_charge,
        rel_tol=0.0,
        abs_tol=1e-4,
    ):
        differences.append(
            "partial_charge: "
            f"{input_atom.partial_charge!r} -> {optimized_atom.partial_charge!r}"
        )
    return "; ".join(differences)


def _distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return math.sqrt(
        sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right))
    )


def _centroid(atoms: list[_PdbqtAtom]) -> tuple[float, float, float]:
    count = len(atoms)
    return (
        sum(atom.x for atom in atoms) / count,
        sum(atom.y for atom in atoms) / count,
        sum(atom.z for atom in atoms) / count,
    )


def _rounded(value: float) -> float:
    return round(value, 6)


def _centroid_payload(value: tuple[float, float, float]) -> dict[str, float]:
    return {
        "x": _rounded(value[0]),
        "y": _rounded(value[1]),
        "z": _rounded(value[2]),
    }


def _compare_parsed_poses(
    input_pose: _ParsedPose,
    optimized_pose: _ParsedPose,
) -> dict[str, Any]:
    if len(input_pose.atoms) != len(optimized_pose.atoms):
        return _error(
            "LOCAL_POSE_ATOM_SET_MISMATCH",
            "输入与优化后 PDBQT 的 ligand 原子总数不一致。",
            raw_error=f"input={len(input_pose.atoms)}; optimized={len(optimized_pose.atoms)}",
            suggestion="请确认 optimized.pdbqt 由本次 local_only 的输入快照直接生成。",
        )

    input_has_mapping = input_pose.has_smiles_mapping
    optimized_has_mapping = optimized_pose.has_smiles_mapping
    if input_has_mapping != optimized_has_mapping:
        return _error(
            "LOCAL_POSE_TOPOLOGY_MAPPING_MISMATCH",
            "输入与优化后 PDBQT 的 Meeko SMILES 映射证据不一致。",
            suggestion="请勿混用其他任务的 ligand PDBQT 或手工删改 REMARK。",
        )

    matches: list[tuple[str, int | None, _PdbqtAtom, _PdbqtAtom]] = []
    mapping_method = ""
    warnings: list[str] = []
    if input_has_mapping:
        if input_pose.smiles != optimized_pose.smiles:
            return _error(
                "LOCAL_POSE_TOPOLOGY_MAPPING_MISMATCH",
                "输入与优化后 PDBQT 记录的 SMILES 不一致。",
                raw_error=(
                    f"input={input_pose.smiles!r}; optimized={optimized_pose.smiles!r}"
                ),
            )
        input_indices = set(input_pose.smiles_index_to_serial)
        optimized_indices = set(optimized_pose.smiles_index_to_serial)
        if input_indices != optimized_indices:
            return _error(
                "LOCAL_POSE_TOPOLOGY_MAPPING_MISMATCH",
                "输入与优化后 PDBQT 的 SMILES 原子索引集合不一致。",
                raw_error=(
                    f"input={sorted(input_indices)}; optimized={sorted(optimized_indices)}"
                ),
            )
        input_by_serial = input_pose.atom_by_serial
        optimized_by_serial = optimized_pose.atom_by_serial
        for smiles_index in sorted(input_indices):
            input_atom = input_by_serial[
                input_pose.smiles_index_to_serial[smiles_index]
            ]
            optimized_atom = optimized_by_serial[
                optimized_pose.smiles_index_to_serial[smiles_index]
            ]
            mismatch = _identity_mismatch(input_atom, optimized_atom)
            if mismatch:
                return _error(
                    "LOCAL_POSE_ATOM_IDENTITY_MISMATCH",
                    f"SMILES 原子 {smiles_index} 的 PDBQT 身份在优化前后不一致。",
                    raw_error=mismatch,
                )
            matches.append(
                (
                    f"smiles:{smiles_index}",
                    smiles_index,
                    input_atom,
                    optimized_atom,
                )
            )

        input_excluded = Counter(
            atom.identity_without_serial
            for atom in input_pose.atoms
            if atom.excluded_kind
        )
        optimized_excluded = Counter(
            atom.identity_without_serial
            for atom in optimized_pose.atoms
            if atom.excluded_kind
        )
        if input_excluded != optimized_excluded:
            return _error(
                "LOCAL_POSE_ATOM_IDENTITY_MISMATCH",
                "输入与优化后 PDBQT 的氢或伪原子身份集合不一致。",
            )
        mapping_method = "meeko_smiles_index"
    else:
        input_by_serial = input_pose.atom_by_serial
        optimized_by_serial = optimized_pose.atom_by_serial
        if set(input_by_serial) != set(optimized_by_serial):
            return _error(
                "LOCAL_POSE_ATOM_SET_MISMATCH",
                "缺少 Meeko 映射时，输入与优化后 PDBQT 的原子序号集合必须完全一致。",
                raw_error=(
                    f"input={sorted(input_by_serial)}; "
                    f"optimized={sorted(optimized_by_serial)}"
                ),
                suggestion="请确认输出来自当前 local_only 任务；不能用坐标近邻猜测原子对应。",
            )
        for serial in sorted(input_by_serial):
            input_atom = input_by_serial[serial]
            optimized_atom = optimized_by_serial[serial]
            mismatch = _identity_mismatch(input_atom, optimized_atom)
            if mismatch:
                return _error(
                    "LOCAL_POSE_ATOM_IDENTITY_MISMATCH",
                    f"原子序号 {serial} 的 PDBQT 身份在优化前后不一致。",
                    raw_error=mismatch,
                )
            if not input_atom.excluded_kind:
                matches.append(
                    (
                        f"serial:{serial}",
                        None,
                        input_atom,
                        optimized_atom,
                    )
                )
        mapping_method = "pdbqt_serial_full_identity"
        warnings.append(
            "PDBQT 未包含完整 Meeko SMILES 映射；本次仅在原子序号和完整身份均一致时按 serial 比较。"
        )

    if not matches:
        return _error(
            "LOCAL_POSE_NO_HEAVY_ATOMS",
            "输入与优化后 PDBQT 中没有可比较的真实重原子。",
        )

    input_heavy = [match[2] for match in matches]
    optimized_heavy = [match[3] for match in matches]
    displacement_rows: list[
        tuple[float, str, int | None, _PdbqtAtom, _PdbqtAtom]
    ] = []
    for match_key, smiles_index, input_atom, optimized_atom in matches:
        displacement_rows.append(
            (
                _distance(input_atom.coordinate, optimized_atom.coordinate),
                match_key,
                smiles_index,
                input_atom,
                optimized_atom,
            )
        )

    squared_sum = sum(row[0] ** 2 for row in displacement_rows)
    displacement_sum = sum(row[0] for row in displacement_rows)
    count = len(displacement_rows)
    input_centroid = _centroid(input_heavy)
    optimized_centroid = _centroid(optimized_heavy)
    max_row = max(displacement_rows, key=lambda row: row[0])

    if input_pose.flex_atom_count or optimized_pose.flex_atom_count:
        warnings.append(
            "已从配体比较中排除 BEGIN_RES/END_RES 内的柔性受体原子："
            f"输入 {input_pose.flex_atom_count} 个，优化后 {optimized_pose.flex_atom_count} 个。"
        )
    if input_pose.hydrogen_count or optimized_pose.hydrogen_count:
        warnings.append(
            "重原子指标已排除 PDBQT 类型 H/HD/HS："
            f"输入 {input_pose.hydrogen_count} 个，优化后 {optimized_pose.hydrogen_count} 个。"
        )
    if input_pose.pseudo_atom_count or optimized_pose.pseudo_atom_count:
        warnings.append(
            "重原子指标已排除 G*、W、XX 伪原子："
            f"输入 {input_pose.pseudo_atom_count} 个，优化后 {optimized_pose.pseudo_atom_count} 个。"
        )

    max_displacement, match_key, smiles_index, input_atom, optimized_atom = max_row
    return {
        "ok": True,
        "schema_version": 1,
        "method": "same_receptor_frame_heavy_atom_displacement_no_alignment",
        "mapping_method": mapping_method,
        "alignment_applied": False,
        "same_receptor_coordinate_frame_required": True,
        "heavy_atom_count": count,
        "excluded_atom_counts": {
            "input_hydrogen": input_pose.hydrogen_count,
            "optimized_hydrogen": optimized_pose.hydrogen_count,
            "input_pseudo": input_pose.pseudo_atom_count,
            "optimized_pseudo": optimized_pose.pseudo_atom_count,
            "input_flexible_receptor": input_pose.flex_atom_count,
            "optimized_flexible_receptor": optimized_pose.flex_atom_count,
        },
        "heavy_atom_rmsd_no_alignment_angstrom": _rounded(
            math.sqrt(squared_sum / count)
        ),
        "mean_heavy_atom_displacement_angstrom": _rounded(
            displacement_sum / count
        ),
        "max_heavy_atom_displacement_angstrom": _rounded(max_displacement),
        "centroid_displacement_angstrom": _rounded(
            _distance(input_centroid, optimized_centroid)
        ),
        "input_centroid_angstrom": _centroid_payload(input_centroid),
        "optimized_centroid_angstrom": _centroid_payload(optimized_centroid),
        "max_displacement_atom": {
            "match_key": match_key,
            "smiles_atom_index": smiles_index,
            "input_serial": input_atom.serial,
            "optimized_serial": optimized_atom.serial,
            "atom_name": input_atom.atom_name,
            "atom_type": input_atom.atom_type,
            "displacement_angstrom": _rounded(max_displacement),
        },
        "warnings": warnings,
        "scientific_note": (
            "这些数值在同一受体坐标系中按可证明的原子身份直接计算，未进行刚体对齐；"
            "因此包含配体整体平移、旋转和内部构象变化。它们不是共晶参考 RMSD，"
            "也不能单独证明优化结果更合理。"
        ),
        "error": None,
    }


def compare_local_only_pose_texts(
    input_pose_text: str,
    optimized_pose_text: str,
    *,
    input_label: str = "输入姿势",
    optimized_label: str = "优化后姿势",
) -> dict[str, Any]:
    """Compare two in-memory PDBQT texts containing one ligand pose each."""

    if not isinstance(input_pose_text, str) or not isinstance(
        optimized_pose_text, str
    ):
        return _error(
            "LOCAL_POSE_TEXT_INVALID",
            "姿势比较输入必须是 PDBQT 文本。",
        )
    input_pose, input_error = _parse_single_ligand_pose_text(
        input_pose_text,
        role="input",
        label=input_label,
    )
    if input_error:
        return input_error
    optimized_pose, optimized_error = _parse_single_ligand_pose_text(
        optimized_pose_text,
        role="optimized",
        label=optimized_label,
    )
    if optimized_error:
        return optimized_error
    assert input_pose is not None
    assert optimized_pose is not None
    return _compare_parsed_poses(input_pose, optimized_pose)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pose_file(
    path_value: str | Path,
    *,
    role: str,
    label: str,
) -> tuple[Path | None, str | None, dict[str, Any] | None]:
    path = Path(path_value).expanduser()
    if path.suffix.lower() != ".pdbqt":
        return None, None, _error(
            _role_code(role, "FORMAT_INVALID"),
            f"{label}必须是 .pdbqt 文件。",
            raw_error=str(path),
        )
    if not path.is_file() or path.is_symlink():
        return None, None, _error(
            _role_code(role, "NOT_FOUND"),
            f"没有找到可读取的{label} PDBQT。",
            raw_error=str(path),
        )
    size = path.stat().st_size
    if size <= 0 or size > MAX_PDBQT_BYTES:
        return None, None, _error(
            _role_code(role, "SIZE_INVALID"),
            f"{label} PDBQT 为空或超过 25 MB。",
            raw_error=f"path={path}; size={size}",
        )
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, _error(
            _role_code(role, "READ_ERROR"),
            f"无法按 UTF-8 读取{label} PDBQT。",
            raw_error=str(exc),
        )
    return path, text, None


def compare_local_only_poses(
    input_pose_path: str | Path,
    optimized_pose_path: str | Path,
) -> dict[str, Any]:
    """Compare an immutable ligand snapshot with ``optimized.pdbqt``."""

    input_path, input_text, input_error = _read_pose_file(
        input_pose_path,
        role="input",
        label="输入姿势",
    )
    if input_error:
        return input_error
    optimized_path, optimized_text, optimized_error = _read_pose_file(
        optimized_pose_path,
        role="optimized",
        label="优化后姿势",
    )
    if optimized_error:
        return optimized_error
    assert input_path is not None and input_text is not None
    assert optimized_path is not None and optimized_text is not None

    result = compare_local_only_pose_texts(input_text, optimized_text)
    if not result.get("ok"):
        return result
    return {
        **result,
        "input_pose_path": str(input_path.resolve()),
        "optimized_pose_path": str(optimized_path.resolve()),
        "input_sha256": _sha256(input_path),
        "optimized_sha256": _sha256(optimized_path),
    }
