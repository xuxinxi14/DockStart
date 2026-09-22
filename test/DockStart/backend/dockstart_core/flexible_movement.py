"""Strict, dependency-free movement analysis for Vina flexible docking.

The prepared ligand, flexible-receptor input, and Vina output all share the
receptor coordinate frame.  Coordinates are therefore compared directly and
are never used to infer atom correspondence.  PDBQT serials and complete atom
identity must be preserved for every output mode.

The module is intentionally independent from project persistence and receptor
preparation.  It accepts either in-memory UTF-8 PDBQT text or three frozen
``.pdbqt`` files and returns a deterministic, JSON-serializable dictionary.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

MAX_PDBQT_BYTES = 25 * 1024 * 1024
HYDROGEN_PDBQT_TYPES = frozenset({"H", "HD", "HS"})
NON_PHYSICAL_PDBQT_TYPES = frozenset({"W", "XX"})
GLUE_PSEUDO_TYPE_PATTERN = re.compile(r"^G\d+$", re.IGNORECASE)
PDBQT_ATOM_TYPE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
RESIDUE_NAME_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_+-]{0,7}$")
RESIDUE_TOKEN_PATTERN = re.compile(r"^(?P<number>-?\d+)(?P<icode>[A-Za-z0-9]?)$")
CHAIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{0,8}$")


class _MovementValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_error: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw_error = raw_error
        self.suggestion = suggestion


@dataclass(frozen=True)
class _ResidueIdentity:
    residue_name: str
    chain_id: str
    residue_number: int
    insertion_code: str

    @property
    def location_key(self) -> tuple[str, int, str]:
        return self.chain_id, self.residue_number, self.insertion_code

    @property
    def canonical_id(self) -> str:
        suffix = f":{self.insertion_code}" if self.insertion_code else ""
        return f"{self.chain_id}:{self.residue_number}{suffix}"

    @property
    def sort_key(self) -> tuple[str, int, str, str]:
        return (
            self.chain_id,
            self.residue_number,
            self.insertion_code,
            self.residue_name,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "residue_name": self.residue_name,
            "chain_id": self.chain_id,
            "residue_number": self.residue_number,
            "insertion_code": self.insertion_code,
        }


@dataclass(frozen=True)
class _Atom:
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
        normalized_type = self.atom_type.upper()
        if normalized_type in HYDROGEN_PDBQT_TYPES:
            return "hydrogen"
        if (
            normalized_type in NON_PHYSICAL_PDBQT_TYPES
            or GLUE_PSEUDO_TYPE_PATTERN.fullmatch(normalized_type)
        ):
            return "pseudo"
        return ""

    @property
    def identity_without_charge(self) -> tuple[Any, ...]:
        return (
            self.record,
            self.serial,
            self.atom_name,
            self.alternate_location,
            self.residue_name,
            self.chain_id,
            self.residue_sequence,
            self.insertion_code,
            self.atom_type,
        )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "record": self.record,
            "serial": self.serial,
            "atom_name": self.atom_name,
            "alternate_location": self.alternate_location,
            "residue_name": self.residue_name,
            "chain_id": self.chain_id,
            "residue_sequence": self.residue_sequence,
            "insertion_code": self.insertion_code,
            "atom_type": self.atom_type,
            "partial_charge": _rounded(self.partial_charge),
        }


@dataclass(frozen=True)
class _Component:
    atoms: tuple[_Atom, ...]
    topology: tuple[str, ...]
    torsdof: int | None

    @property
    def atom_by_serial(self) -> dict[int, _Atom]:
        return {atom.serial: atom for atom in self.atoms}


@dataclass(frozen=True)
class _FlexibleInput:
    residues: tuple[tuple[_ResidueIdentity, _Component], ...]

    @property
    def by_location(
        self,
    ) -> dict[tuple[str, int, str], tuple[_ResidueIdentity, _Component]]:
        return {identity.location_key: (identity, component) for identity, component in self.residues}


@dataclass(frozen=True)
class _OutputMode:
    mode: int
    ligand: _Component
    flexible: _FlexibleInput


def _error(exc: _MovementValidationError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": exc.message,
            "raw_error": exc.raw_error,
            "suggestion": exc.suggestion,
        },
    }


def _raise(
    code: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
) -> None:
    raise _MovementValidationError(
        code,
        message,
        raw_error=raw_error,
        suggestion=suggestion,
    )


def _rounded(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if rounded == 0 else rounded


def _coordinate_payload(coordinate: tuple[float, float, float]) -> dict[str, float]:
    return {
        "x": _rounded(coordinate[0]),
        "y": _rounded(coordinate[1]),
        "z": _rounded(coordinate[2]),
    }


def _distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return math.sqrt(
        math.fsum(
            (left_value - right_value) ** 2
            for left_value, right_value in zip(left, right)
        )
    )


def _centroid(atoms: Sequence[_Atom]) -> tuple[float, float, float]:
    count = len(atoms)
    return (
        math.fsum(atom.x for atom in atoms) / count,
        math.fsum(atom.y for atom in atoms) / count,
        math.fsum(atom.z for atom in atoms) / count,
    )


def _validate_text_bytes(
    value: str,
    *,
    role_code: str,
    label: str,
) -> bytes:
    if not isinstance(value, str):
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TEXT_INVALID",
            f"{label}必须是 PDBQT 文本。",
        )
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TEXT_INVALID",
            f"{label}不能编码为严格 UTF-8。",
            raw_error=str(exc),
        )
    if not raw or len(raw) > MAX_PDBQT_BYTES:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_SIZE_INVALID",
            f"{label}为空或超过 25 MB。",
            raw_error=f"size_bytes={len(raw)}",
        )
    for index, character in enumerate(value):
        if ord(character) < 32 and character not in {"\t", "\r", "\n"}:
            _raise(
                f"FLEX_MOVEMENT_{role_code}_TEXT_INVALID",
                f"{label}包含不允许的控制字符。",
                raw_error=f"character_index={index}; codepoint={ord(character)}",
            )
    if value.startswith("\ufeff"):
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TEXT_INVALID",
            f"{label}不能包含 UTF-8 BOM。",
        )
    return raw


def _parse_atom_line(
    line: str,
    *,
    role_code: str,
    label: str,
    line_number: int,
    residue_boundary: _ResidueIdentity | None,
) -> _Atom:
    try:
        if len(line) < 54:
            raise ValueError("原子记录短于坐标字段")
        record = line[:6].strip()
        if record not in {"ATOM", "HETATM"}:
            raise ValueError("记录类型不是 ATOM/HETATM")
        serial = int(line[6:11].strip())
        if serial <= 0:
            raise ValueError("原子序号必须大于 0")
        atom_name = line[12:16].strip()
        if not atom_name:
            raise ValueError("原子名为空")
        residue_name = line[17:20].strip().upper()
        chain_id = line[21:22].strip()
        residue_sequence = line[22:26].strip()
        insertion_code = line[26:27].strip().upper()
        coordinates = (
            float(line[30:38].strip()),
            float(line[38:46].strip()),
            float(line[46:54].strip()),
        )
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("坐标不是有限数值")
        fields = line.split()
        if len(fields) < 2:
            raise ValueError("缺少部分电荷或 PDBQT 原子类型")
        partial_charge = float(fields[-2])
        if not math.isfinite(partial_charge):
            raise ValueError("部分电荷不是有限数值")
        atom_type = fields[-1]
        if PDBQT_ATOM_TYPE_PATTERN.fullmatch(atom_type) is None:
            raise ValueError("PDBQT 原子类型无效")
    except (IndexError, TypeError, ValueError) as exc:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_ATOM_INVALID",
            f"{label}第 {line_number} 行原子记录无法可靠解析。",
            raw_error=f"{line!r}: {exc}",
            suggestion="请确认 PDBQT 使用标准列宽并包含有限坐标、部分电荷和原子类型。",
        )

    if residue_boundary is not None:
        conflicts: list[str] = []
        try:
            residue_number = int(residue_sequence)
        except ValueError:
            residue_number = 0
            conflicts.append(f"residue_number={residue_sequence!r}")
        if residue_name != residue_boundary.residue_name:
            conflicts.append(
                f"residue_name={residue_name!r}->{residue_boundary.residue_name!r}"
            )
        if chain_id != residue_boundary.chain_id:
            conflicts.append(
                f"chain_id={chain_id!r}->{residue_boundary.chain_id!r}"
            )
        if residue_number != residue_boundary.residue_number:
            conflicts.append(
                f"residue_number={residue_number!r}->{residue_boundary.residue_number!r}"
            )
        if insertion_code and insertion_code != residue_boundary.insertion_code:
            conflicts.append(
                f"insertion_code={insertion_code!r}->{residue_boundary.insertion_code!r}"
            )
        if conflicts:
            _raise(
                f"FLEX_MOVEMENT_{role_code}_RESIDUE_ATOM_CONFLICT",
                f"{label}第 {line_number} 行原子身份与当前柔性残基边界冲突。",
                raw_error="; ".join(conflicts),
                suggestion="请勿用边界信息覆盖发生冲突的原子身份。",
            )
        residue_name = residue_boundary.residue_name
        chain_id = residue_boundary.chain_id
        residue_sequence = str(residue_boundary.residue_number)
        insertion_code = residue_boundary.insertion_code

    return _Atom(
        record=record,
        serial=serial,
        atom_name=atom_name,
        alternate_location=line[16:17].strip(),
        residue_name=residue_name,
        chain_id=chain_id,
        residue_sequence=residue_sequence,
        insertion_code=insertion_code,
        atom_type=atom_type,
        partial_charge=partial_charge,
        x=coordinates[0],
        y=coordinates[1],
        z=coordinates[2],
    )


def _parse_branch_record(
    stripped: str,
    *,
    keyword: str,
    role_code: str,
    label: str,
    line_number: int,
) -> tuple[int, int]:
    fields = stripped.split()
    if len(fields) != 3 or fields[0] != keyword:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}第 {line_number} 行 {keyword} 记录格式无效。",
            raw_error=stripped,
        )
    try:
        pair = int(fields[1]), int(fields[2])
    except ValueError:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}第 {line_number} 行 {keyword} 原子序号无效。",
            raw_error=stripped,
        )
    if pair[0] <= 0 or pair[1] <= 0 or pair[0] == pair[1]:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}第 {line_number} 行 {keyword} 原子序号无效。",
            raw_error=stripped,
        )
    return pair


def _parse_component(
    lines: Sequence[tuple[int, str]],
    *,
    role_code: str,
    label: str,
    residue_boundary: _ResidueIdentity | None,
    require_torsdof: bool,
) -> _Component:
    atoms: list[_Atom] = []
    serials: set[int] = set()
    root_seen = False
    root_open = False
    root_closed = False
    root_atom_count = 0
    branch_stack: list[tuple[int, int]] = []
    branch_pairs: set[tuple[int, int]] = set()
    topology: list[str] = []
    torsdof: int | None = None

    for line_number, line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("REMARK", "WARNING")):
            continue
        record = line[:6].strip()

        if stripped == "ROOT":
            if root_seen or atoms or branch_stack or torsdof is not None:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行出现重复或错位的 ROOT。",
                )
            root_seen = True
            root_open = True
            topology.append("ROOT")
            continue

        if stripped == "ENDROOT":
            if not root_open or branch_stack:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 ENDROOT 没有有效 ROOT。",
                )
            root_open = False
            root_closed = True
            topology.append("ENDROOT")
            continue

        if stripped.startswith("BRANCH"):
            if not root_closed or root_open or torsdof is not None:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 BRANCH 位置无效。",
                )
            pair = _parse_branch_record(
                stripped,
                keyword="BRANCH",
                role_code=role_code,
                label=label,
                line_number=line_number,
            )
            if pair in branch_pairs or pair[0] not in serials or pair[1] in serials:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 BRANCH 不能绑定到已解析拓扑。",
                    raw_error=f"pair={pair}; parsed_serials={sorted(serials)}",
                )
            branch_pairs.add(pair)
            branch_stack.append(pair)
            topology.append(f"BRANCH {pair[0]} {pair[1]}")
            continue

        if stripped.startswith("ENDBRANCH"):
            pair = _parse_branch_record(
                stripped,
                keyword="ENDBRANCH",
                role_code=role_code,
                label=label,
                line_number=line_number,
            )
            if not branch_stack or branch_stack[-1] != pair or pair[1] not in serials:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 ENDBRANCH 与当前 BRANCH 不一致。",
                    raw_error=f"pair={pair}; stack={branch_stack}",
                )
            branch_stack.pop()
            topology.append(f"ENDBRANCH {pair[0]} {pair[1]}")
            continue

        if stripped.startswith("TORSDOF"):
            fields = stripped.split()
            if (
                not require_torsdof
                or len(fields) != 2
                or fields[0] != "TORSDOF"
                or torsdof is not None
                or not root_closed
                or branch_stack
            ):
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 TORSDOF 记录无效。",
                    raw_error=stripped,
                )
            try:
                parsed_torsdof = int(fields[1])
            except ValueError:
                parsed_torsdof = -1
            if parsed_torsdof < 0:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行 TORSDOF 必须是非负整数。",
                    raw_error=stripped,
                )
            torsdof = parsed_torsdof
            topology.append(f"TORSDOF {torsdof}")
            continue

        if record in {"ATOM", "HETATM"}:
            if not root_open and not branch_stack:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
                    f"{label}第 {line_number} 行原子不在 ROOT 或 BRANCH 中。",
                )
            atom = _parse_atom_line(
                line,
                role_code=role_code,
                label=label,
                line_number=line_number,
                residue_boundary=residue_boundary,
            )
            if atom.serial in serials:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_ATOM_ID_DUPLICATE",
                    f"{label}中原子序号 {atom.serial} 重复。",
                    raw_error=f"line={line_number}",
                )
            serials.add(atom.serial)
            atoms.append(atom)
            if root_open:
                root_atom_count += 1
            continue

        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}第 {line_number} 行包含不支持或错位的 PDBQT 记录。",
            raw_error=line,
        )

    if not root_seen or root_open or not root_closed or branch_stack:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}的 ROOT/BRANCH 结构不完整。",
            raw_error=f"root_seen={root_seen}; root_open={root_open}; branches={branch_stack}",
        )
    if root_atom_count <= 0 or not atoms:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_ATOMS_MISSING",
            f"{label}没有完整的 ROOT 原子集合。",
        )
    if require_torsdof and torsdof is None:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_TOPOLOGY_INVALID",
            f"{label}缺少 TORSDOF。",
        )
    return _Component(atoms=tuple(atoms), topology=tuple(topology), torsdof=torsdof)


def _parse_residue_boundary(
    line: str,
    *,
    keyword: str,
    role_code: str,
    label: str,
    line_number: int,
) -> _ResidueIdentity:
    fields = line.strip().split()
    if not fields or fields[0] != keyword or len(fields) not in {3, 4}:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_INVALID",
            f"{label}第 {line_number} 行不是规范的 {keyword} 记录。",
            raw_error=line,
        )
    residue_name = fields[1].upper()
    if RESIDUE_NAME_PATTERN.fullmatch(residue_name) is None:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_INVALID",
            f"{label}第 {line_number} 行残基名无效。",
            raw_error=fields[1],
        )
    chain_id = fields[2] if len(fields) == 4 else ""
    if chain_id in {".", "?"}:
        chain_id = ""
    if CHAIN_ID_PATTERN.fullmatch(chain_id) is None:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_INVALID",
            f"{label}第 {line_number} 行链 ID 无效。",
            raw_error=chain_id,
        )
    residue_token = fields[3] if len(fields) == 4 else fields[2]
    match = RESIDUE_TOKEN_PATTERN.fullmatch(residue_token)
    if match is None:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_INVALID",
            f"{label}第 {line_number} 行残基编号或插入码无效。",
            raw_error=residue_token,
        )
    residue_number = int(match.group("number"))
    if residue_number < -9999 or residue_number > 999999:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_INVALID",
            f"{label}第 {line_number} 行残基编号超出支持范围。",
            raw_error=residue_token,
        )
    return _ResidueIdentity(
        residue_name=residue_name,
        chain_id=chain_id,
        residue_number=residue_number,
        insertion_code=(match.group("icode") or "").upper(),
    )


def _split_flexible_residues(
    lines: Sequence[tuple[int, str]],
    *,
    role_code: str,
    label: str,
) -> tuple[list[tuple[int, str]], _FlexibleInput]:
    outside: list[tuple[int, str]] = []
    residues: list[tuple[_ResidueIdentity, _Component]] = []
    seen_locations: set[tuple[str, int, str]] = set()
    active_identity: _ResidueIdentity | None = None
    active_lines: list[tuple[int, str]] = []

    for line_number, line in lines:
        stripped = line.strip()
        first = stripped.split(maxsplit=1)[0] if stripped else ""
        if first == "BEGIN_RES":
            if active_identity is not None:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_BOUNDARY_NESTED",
                    f"{label}第 {line_number} 行在当前柔性残基结束前再次出现 BEGIN_RES。",
                )
            identity = _parse_residue_boundary(
                line,
                keyword="BEGIN_RES",
                role_code=role_code,
                label=label,
                line_number=line_number,
            )
            if identity.location_key in seen_locations:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_BOUNDARY_DUPLICATE",
                    f"{label}重复包含柔性残基 {identity.canonical_id}。",
                )
            active_identity = identity
            active_lines = []
            continue
        if first == "END_RES":
            if active_identity is None:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_BOUNDARY_UNMATCHED",
                    f"{label}第 {line_number} 行 END_RES 没有对应 BEGIN_RES。",
                )
            end_identity = _parse_residue_boundary(
                line,
                keyword="END_RES",
                role_code=role_code,
                label=label,
                line_number=line_number,
            )
            if end_identity != active_identity:
                _raise(
                    f"FLEX_MOVEMENT_{role_code}_BOUNDARY_MISMATCH",
                    f"{label}第 {line_number} 行 END_RES 与当前 BEGIN_RES 身份不一致。",
                    raw_error=(
                        f"begin={active_identity.residue_name} {active_identity.canonical_id}; "
                        f"end={end_identity.residue_name} {end_identity.canonical_id}"
                    ),
                )
            component = _parse_component(
                active_lines,
                role_code=role_code,
                label=f"{label} {active_identity.residue_name} {active_identity.canonical_id}",
                residue_boundary=active_identity,
                require_torsdof=False,
            )
            seen_locations.add(active_identity.location_key)
            residues.append((active_identity, component))
            active_identity = None
            active_lines = []
            continue
        if active_identity is None:
            outside.append((line_number, line))
        else:
            active_lines.append((line_number, line))

    if active_identity is not None:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_BOUNDARY_UNTERMINATED",
            f"{label}的柔性残基 {active_identity.canonical_id} 缺少 END_RES。",
        )
    residues.sort(key=lambda item: item[0].sort_key)
    return outside, _FlexibleInput(tuple(residues))


def _parse_ligand_input(text: str) -> _Component:
    lines = list(enumerate(text.splitlines(), start=1))
    for line_number, line in lines:
        first = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if first in {"MODEL", "ENDMDL"}:
            _raise(
                "FLEX_MOVEMENT_LIGAND_INPUT_MODEL_FORBIDDEN",
                "冻结配体输入不能包含 MODEL/ENDMDL。",
                raw_error=f"line={line_number}: {line!r}",
            )
        if first in {"BEGIN_RES", "END_RES"}:
            _raise(
                "FLEX_MOVEMENT_LIGAND_INPUT_BOUNDARY_FORBIDDEN",
                "冻结配体输入不能包含柔性受体残基边界。",
                raw_error=f"line={line_number}: {line!r}",
            )
    return _parse_component(
        lines,
        role_code="LIGAND_INPUT",
        label="冻结配体输入",
        residue_boundary=None,
        require_torsdof=True,
    )


def _parse_flexible_input(text: str) -> _FlexibleInput:
    lines = list(enumerate(text.splitlines(), start=1))
    for line_number, line in lines:
        first = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if first in {"MODEL", "ENDMDL"}:
            _raise(
                "FLEX_MOVEMENT_FLEX_INPUT_MODEL_FORBIDDEN",
                "柔性受体输入不能包含 MODEL/ENDMDL。",
                raw_error=f"line={line_number}: {line!r}",
            )
    outside, parsed = _split_flexible_residues(
        lines,
        role_code="FLEX_INPUT",
        label="柔性受体输入",
    )
    unexpected = [
        (line_number, line)
        for line_number, line in outside
        if line.strip() and not line.strip().startswith(("REMARK", "WARNING"))
    ]
    if unexpected:
        line_number, line = unexpected[0]
        _raise(
            "FLEX_MOVEMENT_FLEX_INPUT_ATOM_OUTSIDE_RESIDUE",
            "柔性受体输入包含 BEGIN_RES/END_RES 之外的结构记录。",
            raw_error=f"line={line_number}: {line!r}",
        )
    if not parsed.residues:
        _raise(
            "FLEX_MOVEMENT_FLEX_INPUT_RESIDUES_MISSING",
            "柔性受体输入没有可分析的 BEGIN_RES/END_RES 残基。",
        )
    return parsed


def _parse_output_model(
    mode: int,
    lines: Sequence[tuple[int, str]],
) -> _OutputMode:
    ligand_lines, flexible = _split_flexible_residues(
        lines,
        role_code="OUTPUT",
        label=f"Vina 输出 Mode {mode}",
    )
    if not flexible.residues:
        _raise(
            "FLEX_MOVEMENT_OUTPUT_RESIDUES_MISSING",
            f"Vina 输出 Mode {mode} 没有柔性受体残基。",
        )
    ligand = _parse_component(
        ligand_lines,
        role_code="OUTPUT",
        label=f"Vina 输出 Mode {mode} 配体",
        residue_boundary=None,
        require_torsdof=True,
    )
    return _OutputMode(mode=mode, ligand=ligand, flexible=flexible)


def _parse_output(text: str) -> tuple[_OutputMode, ...]:
    modes: list[_OutputMode] = []
    active_mode: int | None = None
    active_lines: list[tuple[int, str]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        first = stripped.split(maxsplit=1)[0] if stripped else ""
        if first == "MODEL":
            if active_mode is not None:
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_MODEL_NESTED",
                    f"Vina 输出第 {line_number} 行在 Mode {active_mode} 结束前再次出现 MODEL。",
                )
            match = re.fullmatch(r"MODEL\s+(\d+)", stripped)
            if match is None:
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_MODEL_INVALID",
                    f"Vina 输出第 {line_number} 行 MODEL 记录无效。",
                    raw_error=line,
                )
            mode = int(match.group(1))
            expected_mode = len(modes) + 1
            if mode != expected_mode:
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_MODEL_SEQUENCE_INVALID",
                    "Vina 输出 MODEL 编号必须从 1 开始连续递增。",
                    raw_error=f"expected={expected_mode}; actual={mode}",
                )
            active_mode = mode
            active_lines = []
            continue
        if first == "ENDMDL":
            if stripped != "ENDMDL" or active_mode is None:
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_MODEL_INVALID",
                    f"Vina 输出第 {line_number} 行 ENDMDL 没有有效 MODEL。",
                    raw_error=line,
                )
            modes.append(_parse_output_model(active_mode, active_lines))
            active_mode = None
            active_lines = []
            continue
        if active_mode is None:
            if stripped and not stripped.startswith(("REMARK", "WARNING")):
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_RECORD_OUTSIDE_MODEL",
                    "Vina 输出在 MODEL/ENDMDL 之外包含结构记录。",
                    raw_error=f"line={line_number}: {line!r}",
                )
            continue
        active_lines.append((line_number, line))

    if active_mode is not None:
        _raise(
            "FLEX_MOVEMENT_OUTPUT_MODEL_UNTERMINATED",
            f"Vina 输出 Mode {active_mode} 缺少 ENDMDL。",
        )
    if not modes:
        _raise(
            "FLEX_MOVEMENT_OUTPUT_MODELS_MISSING",
            "Vina 输出没有可分析的 MODEL/ENDMDL 构象。",
        )
    return tuple(modes)


def _identity_difference(reference: _Atom, observed: _Atom) -> str:
    fields = (
        "record",
        "serial",
        "atom_name",
        "alternate_location",
        "residue_name",
        "chain_id",
        "residue_sequence",
        "insertion_code",
        "atom_type",
    )
    differences = [
        f"{field}={getattr(reference, field)!r}->{getattr(observed, field)!r}"
        for field in fields
        if getattr(reference, field) != getattr(observed, field)
    ]
    if not math.isclose(
        reference.partial_charge,
        observed.partial_charge,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        differences.append(
            f"partial_charge={reference.partial_charge!r}->{observed.partial_charge!r}"
        )
    return "; ".join(differences)


def _validate_component_identity(
    reference: _Component,
    observed: _Component,
    *,
    component_label: str,
) -> list[tuple[_Atom, _Atom]]:
    if reference.torsdof != observed.torsdof or reference.topology != observed.topology:
        _raise(
            "FLEX_MOVEMENT_OUTPUT_TOPOLOGY_MISMATCH",
            f"{component_label}的 PDBQT ROOT/BRANCH/TORSDOF 拓扑与冻结输入不一致。",
            raw_error=(
                f"reference={reference.topology!r}; observed={observed.topology!r}"
            ),
        )
    reference_by_serial = reference.atom_by_serial
    observed_by_serial = observed.atom_by_serial
    if len(reference.atoms) != len(observed.atoms) or set(reference_by_serial) != set(
        observed_by_serial
    ):
        _raise(
            "FLEX_MOVEMENT_OUTPUT_ATOM_SET_MISMATCH",
            f"{component_label}的原子数量或序号集合与冻结输入不一致。",
            raw_error=(
                f"reference={sorted(reference_by_serial)}; "
                f"observed={sorted(observed_by_serial)}"
            ),
        )
    matches: list[tuple[_Atom, _Atom]] = []
    for serial in sorted(reference_by_serial):
        reference_atom = reference_by_serial[serial]
        observed_atom = observed_by_serial[serial]
        difference = _identity_difference(reference_atom, observed_atom)
        if difference:
            _raise(
                "FLEX_MOVEMENT_OUTPUT_ATOM_IDENTITY_MISMATCH",
                f"{component_label}原子序号 {serial} 的完整身份与冻结输入不一致。",
                raw_error=difference,
                suggestion="请确认 out.pdbqt 来自当前冻结 ligand/flex 输入，且文件未被手工修改。",
            )
        matches.append((reference_atom, observed_atom))
    return matches


def _movement_metrics(
    matches: Sequence[tuple[_Atom, _Atom]],
    *,
    exclude_ca: bool,
    component_label: str,
) -> dict[str, Any]:
    selected: list[tuple[_Atom, _Atom]] = []
    excluded = {"hydrogen": 0, "pseudo": 0, "ca_root": 0}
    for reference_atom, observed_atom in matches:
        if reference_atom.excluded_kind:
            excluded[reference_atom.excluded_kind] += 1
            continue
        if exclude_ca and reference_atom.atom_name.upper() == "CA":
            excluded["ca_root"] += 1
            continue
        selected.append((reference_atom, observed_atom))
    if not selected:
        _raise(
            "FLEX_MOVEMENT_NO_COMPARABLE_HEAVY_ATOMS",
            f"{component_label}在排除氢、伪原子和可选 CA 根后没有可比较重原子。",
        )

    rows: list[dict[str, Any]] = []
    distances: list[float] = []
    for reference_atom, observed_atom in selected:
        displacement = _distance(reference_atom.coordinate, observed_atom.coordinate)
        distances.append(displacement)
        rows.append(
            {
                "identity": reference_atom.identity_payload(),
                "reference_coordinate_angstrom": _coordinate_payload(
                    reference_atom.coordinate
                ),
                "mode_coordinate_angstrom": _coordinate_payload(
                    observed_atom.coordinate
                ),
                "displacement_angstrom": _rounded(displacement),
            }
        )

    reference_centroid = _centroid([item[0] for item in selected])
    mode_centroid = _centroid([item[1] for item in selected])
    count = len(selected)
    max_index = max(range(count), key=lambda index: distances[index])
    return {
        "alignment_applied": False,
        "heavy_atom_count": count,
        "excluded_atom_counts": excluded,
        "heavy_atom_rmsd_no_alignment_angstrom": _rounded(
            math.sqrt(math.fsum(value * value for value in distances) / count)
        ),
        "mean_heavy_atom_displacement_angstrom": _rounded(
            math.fsum(distances) / count
        ),
        "max_heavy_atom_displacement_angstrom": _rounded(distances[max_index]),
        "centroid_displacement_angstrom": _rounded(
            _distance(reference_centroid, mode_centroid)
        ),
        "reference_centroid_angstrom": _coordinate_payload(reference_centroid),
        "mode_centroid_angstrom": _coordinate_payload(mode_centroid),
        "max_displacement_atom": {
            **rows[max_index]["identity"],
            "displacement_angstrom": _rounded(distances[max_index]),
        },
        "atoms": rows,
    }


def _source_evidence(raw: bytes) -> dict[str, Any]:
    return {
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _analyze_bytes(
    ligand_text: str,
    flex_text: str,
    output_text: str,
    *,
    ligand_raw: bytes,
    flex_raw: bytes,
    output_raw: bytes,
    exclude_flexible_ca: bool,
) -> dict[str, Any]:
    ligand = _parse_ligand_input(ligand_text)
    flexible = _parse_flexible_input(flex_text)
    modes = _parse_output(output_text)

    reference_flexible = flexible.by_location
    mode_payloads: list[dict[str, Any]] = []
    for mode in modes:
        ligand_matches = _validate_component_identity(
            ligand,
            mode.ligand,
            component_label=f"Mode {mode.mode} 配体",
        )
        observed_flexible = mode.flexible.by_location
        if set(reference_flexible) != set(observed_flexible):
            _raise(
                "FLEX_MOVEMENT_OUTPUT_RESIDUE_SET_MISMATCH",
                f"Mode {mode.mode} 的柔性残基集合与冻结 flex 输入不一致。",
                raw_error=(
                    f"reference={sorted(reference_flexible)}; "
                    f"observed={sorted(observed_flexible)}"
                ),
            )

        residue_payloads: list[dict[str, Any]] = []
        for location_key in sorted(
            reference_flexible,
            key=lambda item: (item[0], item[1], item[2]),
        ):
            reference_identity, reference_component = reference_flexible[location_key]
            observed_identity, observed_component = observed_flexible[location_key]
            if reference_identity != observed_identity:
                _raise(
                    "FLEX_MOVEMENT_OUTPUT_RESIDUE_IDENTITY_MISMATCH",
                    f"Mode {mode.mode} 柔性残基 {reference_identity.canonical_id} 身份不一致。",
                    raw_error=(
                        f"reference={reference_identity!r}; observed={observed_identity!r}"
                    ),
                )
            residue_matches = _validate_component_identity(
                reference_component,
                observed_component,
                component_label=(
                    f"Mode {mode.mode} 柔性残基 "
                    f"{reference_identity.residue_name} {reference_identity.canonical_id}"
                ),
            )
            residue_payloads.append(
                {
                    "residue": reference_identity.payload(),
                    "movement": _movement_metrics(
                        residue_matches,
                        exclude_ca=exclude_flexible_ca,
                        component_label=(
                            f"柔性残基 {reference_identity.residue_name} "
                            f"{reference_identity.canonical_id}"
                        ),
                    ),
                }
            )

        mode_payloads.append(
            {
                "mode": mode.mode,
                "ligand": _movement_metrics(
                    ligand_matches,
                    exclude_ca=False,
                    component_label="配体",
                ),
                "flexible_residues": residue_payloads,
            }
        )

    ligand_heavy_count = sum(not atom.excluded_kind for atom in ligand.atoms)
    residue_contracts = []
    for identity, component in flexible.residues:
        residue_contracts.append(
            {
                "residue": identity.payload(),
                "atom_count": len(component.atoms),
                "analyzed_heavy_atom_count": sum(
                    not atom.excluded_kind
                    and not (
                        exclude_flexible_ca and atom.atom_name.upper() == "CA"
                    )
                    for atom in component.atoms
                ),
            }
        )

    return {
        "ok": True,
        "schema_version": 1,
        "schema_id": "dockstart.flexible_movement.v1",
        "method": "same_receptor_frame_heavy_atom_displacement_no_alignment",
        "alignment_applied": False,
        "same_receptor_coordinate_frame_required": True,
        "exclude_flexible_ca_root": exclude_flexible_ca,
        "source_evidence": {
            "ligand_input": _source_evidence(ligand_raw),
            "flex_input": _source_evidence(flex_raw),
            "vina_output": _source_evidence(output_raw),
        },
        "input_contract": {
            "ligand_atom_count": len(ligand.atoms),
            "ligand_heavy_atom_count": ligand_heavy_count,
            "ligand_torsdof": ligand.torsdof,
            "flexible_residue_count": len(flexible.residues),
            "flexible_residues": residue_contracts,
        },
        "mode_count": len(mode_payloads),
        "modes": mode_payloads,
        "warnings": [
            (
                "柔性残基指标已排除 PDBQT 类型 H/HD/HS 和非物理伪原子；"
                + ("同时排除名为 CA 的根原子。" if exclude_flexible_ca else "保留名为 CA 的根原子。")
            )
        ],
        "scientific_note": (
            "配体与各柔性侧链在同一受体坐标系中分别按冻结 PDBQT 原子身份直接比较，"
            "没有进行刚体对齐。数值包含整体平移、旋转和内部构象变化，"
            "不应解释为晶体参考 RMSD，也不能单独证明真实结合或构象合理性。"
        ),
        "error": None,
    }


def analyze_flexible_movement_texts(
    ligand_pdbqt: str,
    flex_pdbqt: str,
    output_pdbqt: str,
    *,
    exclude_flexible_ca: bool = True,
) -> dict[str, Any]:
    """Analyze frozen in-memory ligand/flex inputs against all Vina modes."""

    if not isinstance(exclude_flexible_ca, bool):
        return _error(
            _MovementValidationError(
                "FLEX_MOVEMENT_OPTION_INVALID",
                "exclude_flexible_ca 必须是布尔值。",
            )
        )
    try:
        ligand_raw = _validate_text_bytes(
            ligand_pdbqt,
            role_code="LIGAND_INPUT",
            label="冻结配体输入",
        )
        flex_raw = _validate_text_bytes(
            flex_pdbqt,
            role_code="FLEX_INPUT",
            label="柔性受体输入",
        )
        output_raw = _validate_text_bytes(
            output_pdbqt,
            role_code="OUTPUT",
            label="Vina flexible 输出",
        )
        return _analyze_bytes(
            ligand_pdbqt,
            flex_pdbqt,
            output_pdbqt,
            ligand_raw=ligand_raw,
            flex_raw=flex_raw,
            output_raw=output_raw,
            exclude_flexible_ca=exclude_flexible_ca,
        )
    except _MovementValidationError as exc:
        return _error(exc)


def _read_pdbqt_file(
    path_value: str | Path,
    *,
    role_code: str,
    label: str,
) -> tuple[str, bytes]:
    path = Path(path_value).expanduser()
    if path.suffix.lower() != ".pdbqt":
        _raise(
            f"FLEX_MOVEMENT_{role_code}_FORMAT_INVALID",
            f"{label}必须是 .pdbqt 文件。",
            raw_error=str(path),
        )
    if not path.is_file() or path.is_symlink():
        _raise(
            f"FLEX_MOVEMENT_{role_code}_NOT_FOUND",
            f"没有找到可读取的{label}。",
            raw_error=str(path),
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_READ_ERROR",
            f"无法读取{label}。",
            raw_error=str(exc),
        )
    if not raw or len(raw) > MAX_PDBQT_BYTES:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_SIZE_INVALID",
            f"{label}为空或超过 25 MB。",
            raw_error=f"path={path}; size_bytes={len(raw)}",
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _raise(
            f"FLEX_MOVEMENT_{role_code}_READ_ERROR",
            f"{label}不是严格 UTF-8 文本。",
            raw_error=str(exc),
        )
    _validate_text_bytes(text, role_code=role_code, label=label)
    return text, raw


def analyze_flexible_movement(
    ligand_pdbqt_path: str | Path,
    flex_pdbqt_path: str | Path,
    output_pdbqt_path: str | Path,
    *,
    exclude_flexible_ca: bool = True,
) -> dict[str, Any]:
    """Analyze three frozen PDBQT files without modifying project state."""

    if not isinstance(exclude_flexible_ca, bool):
        return _error(
            _MovementValidationError(
                "FLEX_MOVEMENT_OPTION_INVALID",
                "exclude_flexible_ca 必须是布尔值。",
            )
        )
    try:
        ligand_text, ligand_raw = _read_pdbqt_file(
            ligand_pdbqt_path,
            role_code="LIGAND_INPUT",
            label="冻结配体输入 PDBQT",
        )
        flex_text, flex_raw = _read_pdbqt_file(
            flex_pdbqt_path,
            role_code="FLEX_INPUT",
            label="柔性受体输入 PDBQT",
        )
        output_text, output_raw = _read_pdbqt_file(
            output_pdbqt_path,
            role_code="OUTPUT",
            label="Vina flexible 输出 PDBQT",
        )
        return _analyze_bytes(
            ligand_text,
            flex_text,
            output_text,
            ligand_raw=ligand_raw,
            flex_raw=flex_raw,
            output_raw=output_raw,
            exclude_flexible_ca=exclude_flexible_ca,
        )
    except _MovementValidationError as exc:
        return _error(exc)


def canonical_flexible_movement_json(payload: Mapping[str, Any]) -> str:
    """Serialize a movement result with a stable UTF-8 JSON representation."""

    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def flexible_movement_sha256(payload: Mapping[str, Any]) -> str:
    """Return SHA256 of :func:`canonical_flexible_movement_json` bytes."""

    canonical = canonical_flexible_movement_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "analyze_flexible_movement",
    "analyze_flexible_movement_texts",
    "canonical_flexible_movement_json",
    "flexible_movement_sha256",
]
