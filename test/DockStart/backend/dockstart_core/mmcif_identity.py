"""Auditable residue-identity contracts for mmCIF receptor bridges.

The flexible-receptor workflow ultimately addresses residues through the
legacy PDB/Meeko identifier ``chain:auth_seq_id[:insertion_code]``.  PDBx/mmCIF
also carries label identifiers, model identifiers, alternate locations and
occupancies.  A plain mmCIF -> PDB conversion can therefore produce a
syntactically valid file while silently changing the identity used for a
flexible-sidechain selection.

This module is deliberately independent from project persistence and the UI.
It provides a fail-closed contract which can later be frozen beside a Gemmi
bridge and consumed by the project-level flexible-receptor workflow.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MAX_MMCIF_BYTES = 128 * 1024 * 1024
CONTRACT_SCHEMA_VERSION = 2
IDENTITY_BASIS = "pdbx_author_label_residue_bridge_v1"
PREPARATION_CONTROL_SCHEMA_VERSION = 1
PREPARATION_CONTROL_TYPE = "mmcif_receptor_preparation_controls"
PREPARATION_DELETION_REASONS = {
    "co_crystal_ligand",
    "crystallization_component",
    "water",
    "ion",
    "other_reviewed",
}
SELECTOR_PATTERN = re.compile(
    r"(?P<chain>[^:\s]*):(?P<number>-?\d+)(?::(?P<icode>[A-Za-z0-9]))?"
)
COMPACT_SELECTOR_PATTERN = re.compile(
    r"(?P<chain>[^:\s]*):(?P<number>-?\d+)(?P<icode>[A-Za-z])"
)


class MmcifIdentityError(ValueError):
    """A fail-closed mmCIF identity or bridge validation error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        suggestion: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": self.detail,
        }


def _fail(
    code: str,
    message: str,
    *,
    suggestion: str = "",
    detail: str = "",
) -> MmcifIdentityError:
    return MmcifIdentityError(
        code,
        message,
        suggestion=suggestion,
        detail=detail,
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_sha256(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(serialized)


def _read_mmcif(path: Path) -> tuple[bytes, str]:
    if not path.is_file() or path.is_symlink():
        raise _fail(
            "MMCIF_FILE_NOT_FOUND",
            f"没有找到可读取的普通 mmCIF 文件：{path}",
            suggestion="请重新选择项目内原始 mmCIF 文件。",
        )
    size = path.stat().st_size
    if size <= 0 or size > MAX_MMCIF_BYTES:
        raise _fail(
            "MMCIF_FILE_SIZE_INVALID",
            f"mmCIF 文件大小 {size} B 不在安全读取范围内。",
            suggestion="请确认文件非空，且只包含本次需要审查的结构。",
        )
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail(
            "MMCIF_ENCODING_INVALID",
            "mmCIF 不是有效的 UTF-8 文本。",
            suggestion="请重新从结构数据库下载未损坏的 mmCIF。",
            detail=str(exc),
        ) from exc
    return payload, text


def _atom_site_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Return one normalized ``_atom_site`` loop without guessing malformed rows."""

    lines = text.splitlines()
    found: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index < len(lines):
        if lines[index].strip().lower() != "loop_":
            index += 1
            continue
        index += 1
        headers: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("_"):
            headers.append(lines[index].strip().split()[0].lower())
            index += 1
        if not headers or not all(header.startswith("_atom_site.") for header in headers):
            continue

        tokens: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                index += 1
                continue
            if (
                stripped == "#"
                or stripped.lower() == "loop_"
                or stripped.startswith("_")
                or stripped.lower().startswith("data_")
                or stripped.lower().startswith("save_")
            ):
                break
            if lines[index].startswith(";"):
                raise _fail(
                    "MMCIF_ATOM_SITE_MULTILINE_UNSUPPORTED",
                    "_atom_site 表包含分号多行字段，无法在残基身份审计中安全解释。",
                    suggestion="请使用 Gemmi 重新规范化 mmCIF，或导出经过人工核对的 PDB。",
                )
            try:
                tokens.extend(shlex.split(stripped, comments=False, posix=True))
            except ValueError as exc:
                raise _fail(
                    "MMCIF_SYNTAX_INVALID",
                    "_atom_site 表包含未闭合引号或其他无效语法。",
                    suggestion="请重新下载原始 mmCIF。",
                    detail=str(exc),
                ) from exc
            index += 1
        if len(tokens) % len(headers) != 0:
            raise _fail(
                "MMCIF_ATOM_SITE_WIDTH_INVALID",
                "_atom_site 数据项数量不能被字段数整除。",
                suggestion="请重新下载原始 mmCIF；DockStart 不会补齐或丢弃列。",
            )
        rows = [
            tokens[offset : offset + len(headers)]
            for offset in range(0, len(tokens), len(headers))
        ]
        found.append((headers, rows))

    if not found:
        raise _fail(
            "MMCIF_ATOM_SITE_NOT_FOUND",
            "mmCIF 中没有找到坐标 _atom_site 表。",
            suggestion="请选择包含原子坐标的 PDBx/mmCIF 文件。",
        )
    if len(found) != 1:
        raise _fail(
            "MMCIF_MULTIPLE_ATOM_SITE_TABLES",
            f"mmCIF 中找到 {len(found)} 个 _atom_site 表，无法证明其身份关系唯一。",
            suggestion="请先用 Gemmi 规范化为单一坐标表。",
        )
    return found[0]


def _raw_value(
    row: Sequence[str],
    columns: Mapping[str, int],
    name: str,
) -> str:
    index = columns.get(name.lower())
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _value(
    row: Sequence[str],
    columns: Mapping[str, int],
    name: str,
) -> str:
    raw = _raw_value(row, columns, name)
    return "" if raw in {"", ".", "?"} else raw


def _required_columns(columns: Mapping[str, int]) -> None:
    required = {
        "_atom_site.group_pdb",
        "_atom_site.id",
        "_atom_site.type_symbol",
        "_atom_site.label_atom_id",
        "_atom_site.label_alt_id",
        "_atom_site.label_comp_id",
        "_atom_site.label_asym_id",
        "_atom_site.label_entity_id",
        "_atom_site.label_seq_id",
        "_atom_site.pdbx_pdb_ins_code",
        "_atom_site.cartn_x",
        "_atom_site.cartn_y",
        "_atom_site.cartn_z",
        "_atom_site.occupancy",
        "_atom_site.auth_seq_id",
        "_atom_site.auth_comp_id",
        "_atom_site.auth_asym_id",
        "_atom_site.auth_atom_id",
        "_atom_site.pdbx_pdb_model_num",
    }
    missing = sorted(required - set(columns))
    if missing:
        raise _fail(
            "MMCIF_IDENTITY_COLUMNS_MISSING",
            "mmCIF 缺少建立作者/标签残基映射所需字段。",
            suggestion="请使用完整的 PDBx/mmCIF 坐标文件；不要用删列后的表格作为柔性受体来源。",
            detail=", ".join(missing),
        )


def _parse_selector(value: str) -> tuple[str, int, str, str]:
    text = str(value).strip()
    match = SELECTOR_PATTERN.fullmatch(text) or COMPACT_SELECTOR_PATTERN.fullmatch(
        text
    )
    if match is None:
        raise _fail(
            "MMCIF_FLEX_SELECTOR_INVALID",
            f"无法解析柔性残基选择器：{text or '（空）'}。",
            suggestion="请使用 author_chain:auth_seq_id 或 author_chain:auth_seq_id:插入码。",
        )
    chain = match.group("chain")
    if chain in {".", "?"}:
        chain = ""
    if len(chain) > 8 or not re.fullmatch(r"[A-Za-z0-9_.-]*", chain):
        raise _fail(
            "MMCIF_FLEX_SELECTOR_INVALID",
            f"选择器链 ID“{chain}”包含不支持的字符或长度超过 8。",
            suggestion="请使用身份合同中记录的 auth_asym_id。",
        )
    number = int(match.group("number"))
    insertion = str(match.group("icode") or "").upper()
    canonical = f"{chain}:{number}" + (f":{insertion}" if insertion else "")
    return chain, number, insertion, canonical


def _author_sequence(
    raw_number: str,
    raw_insertion: str,
    *,
    residue_label: str,
) -> tuple[int, str]:
    number_match = re.fullmatch(r"(-?\d+)([A-Za-z0-9]?)", raw_number)
    if number_match is None:
        raise _fail(
            "MMCIF_AUTH_SEQ_ID_UNREPRESENTABLE",
            f"{residue_label} 的 auth_seq_id“{raw_number}”不能安全映射为 Meeko/PDB 残基号。",
            suggestion="请先在结构编辑工具中建立明确的作者残基编号。",
        )
    number = int(number_match.group(1))
    if number < -999 or number > 9999:
        raise _fail(
            "MMCIF_AUTH_SEQ_ID_OUT_OF_PDB_RANGE",
            f"{residue_label} 的作者残基号 {number} 超出传统 PDB 字段范围。",
            suggestion="请先选择目标链/区域并生成保留身份的 PDB。",
        )
    compact_insertion = number_match.group(2).upper()
    insertion = "" if raw_insertion in {"", ".", "?"} else raw_insertion.upper()
    if insertion and (
        len(insertion) != 1 or not re.fullmatch(r"[A-Za-z0-9]", insertion)
    ):
        raise _fail(
            "MMCIF_INSERTION_CODE_UNREPRESENTABLE",
            f"{residue_label} 的插入码“{raw_insertion}”不能无损写入传统 PDB。",
            suggestion="请先导出经过人工核对的 PDB。",
        )
    if compact_insertion and insertion and compact_insertion != insertion:
        raise _fail(
            "MMCIF_INSERTION_CODE_CONFLICT",
            f"{residue_label} 的 auth_seq_id 后缀与 pdbx_PDB_ins_code 不一致。",
            detail=f"auth_seq_id={raw_number}; pdbx_PDB_ins_code={raw_insertion}",
        )
    return number, insertion or compact_insertion


def _parse_finite_float(
    raw: str,
    *,
    code: str,
    label: str,
) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise _fail(code, f"{label}“{raw}”不是有效数值。") from exc
    if not math.isfinite(value):
        raise _fail(code, f"{label}不是有限数值。")
    return value


def _occupancy_summary(atoms: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for atom in atoms:
        grouped.setdefault(str(atom.get("altloc") or ""), []).append(
            float(atom["occupancy"])
        )
    return {
        ("shared" if altloc == "" else altloc): {
            "atom_count": len(values),
            "minimum": min(values),
            "maximum": max(values),
            "sum": round(sum(values), 6),
        }
        for altloc, values in sorted(grouped.items())
    }


def _finalize_residue_records(
    residues: Mapping[tuple[str, int, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in sorted(residues):
        residue = dict(residues[key])
        residue["atoms"] = sorted(
            [dict(atom) for atom in residue.get("atoms", [])],
            key=lambda atom: (
                str(atom["bridge_atom_name"]),
                str(atom["altloc"]),
                str(atom["atom_site_id"]),
            ),
        )
        altlocs = sorted(
            {
                str(atom["altloc"])
                for atom in residue["atoms"]
                if atom["altloc"]
            }
        )
        residue["atom_count"] = len(residue["atoms"])
        residue["alternate_locations"] = {
            "ids": altlocs,
            "requires_explicit_choice": bool(altlocs),
            "occupancy": _occupancy_summary(residue["atoms"]),
        }
        residue["atom_identity_sha256"] = _residue_atom_identity_hash(
            residue
        )
        records.append(residue)
    return records


def _identity_hash_payload(contract: Mapping[str, Any]) -> dict[str, Any]:
    source = contract.get("source") if isinstance(contract.get("source"), Mapping) else {}
    model = contract.get("model") if isinstance(contract.get("model"), Mapping) else {}
    bridge_constraints = (
        contract.get("bridge_constraints")
        if isinstance(contract.get("bridge_constraints"), Mapping)
        else {}
    )
    residues = contract.get("residues") if isinstance(contract.get("residues"), list) else []
    payload = {
        "schema_version": int(contract.get("schema_version") or 0),
        "identity_basis": str(contract.get("identity_basis") or ""),
        "source": {
            "size_bytes": int(source.get("size_bytes") or 0),
            "sha256": str(source.get("sha256") or ""),
        },
        "model": dict(model),
        "bridge_constraints": dict(bridge_constraints),
        "residue_count": int(contract.get("residue_count") or 0),
        "residues": residues,
    }
    if int(contract.get("schema_version") or 0) >= 2:
        nonpolymer_residues = (
            contract.get("nonpolymer_residues")
            if isinstance(contract.get("nonpolymer_residues"), list)
            else []
        )
        payload.update(
            {
                "coordinate_atom_count": int(
                    contract.get("coordinate_atom_count") or 0
                ),
                "polymer_atom_count": int(
                    contract.get("polymer_atom_count") or 0
                ),
                "nonpolymer_residue_count": int(
                    contract.get("nonpolymer_residue_count") or 0
                ),
                "nonpolymer_atom_count": int(
                    contract.get("nonpolymer_atom_count") or 0
                ),
                "nonpolymer_residues": nonpolymer_residues,
            }
        )
    return payload


def _residue_atom_identity_hash(residue: Mapping[str, Any]) -> str:
    return _stable_sha256(
        {
            "record_type": str(residue.get("record_type") or ""),
            "selector": str(residue.get("selector") or ""),
            "meeko_id": str(residue.get("meeko_id") or ""),
            "author": dict(residue.get("author") or {}),
            "label": dict(residue.get("label") or {}),
            "bridge": dict(residue.get("bridge") or {}),
            "atom_count": int(residue.get("atom_count") or 0),
            "atoms": list(residue.get("atoms") or []),
        }
    )


def _coordinate_identity_hash_payload(
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    source = (
        contract.get("source")
        if isinstance(contract.get("source"), Mapping)
        else {}
    )
    model = (
        contract.get("model")
        if isinstance(contract.get("model"), Mapping)
        else {}
    )
    return {
        "schema_version": int(contract.get("schema_version") or 0),
        "identity_basis": str(contract.get("identity_basis") or ""),
        "source": {
            "size_bytes": int(source.get("size_bytes") or 0),
            "sha256": str(source.get("sha256") or ""),
        },
        "model": dict(model),
        "coordinate_atom_count": int(
            contract.get("coordinate_atom_count") or 0
        ),
        "polymer_atom_count": int(contract.get("polymer_atom_count") or 0),
        "nonpolymer_atom_count": int(
            contract.get("nonpolymer_atom_count") or 0
        ),
        "residues": list(contract.get("residues") or []),
        "nonpolymer_residues": list(
            contract.get("nonpolymer_residues") or []
        ),
    }


def audit_mmcif_residue_identities(
    mmcif_path: str | Path,
) -> dict[str, Any]:
    """Parse one-model mmCIF and freeze author/label/bridge residue identities."""

    path = Path(mmcif_path)
    payload, text = _read_mmcif(path)
    headers, rows = _atom_site_table(text)
    columns = {name.lower(): index for index, name in enumerate(headers)}
    _required_columns(columns)

    coordinate_rows = [
        row
        for row in rows
        if _value(row, columns, "_atom_site.group_pdb").upper()
        in {"ATOM", "HETATM"}
    ]
    if not coordinate_rows:
        raise _fail(
            "MMCIF_COORDINATES_MISSING",
            "mmCIF 的 _atom_site 表中没有 ATOM/HETATM 坐标。",
        )
    if len(coordinate_rows) > 99999:
        raise _fail(
            "MMCIF_BRIDGE_ATOM_COUNT_OUT_OF_PDB_RANGE",
            f"mmCIF 含有 {len(coordinate_rows)} 个坐标原子，超过传统 PDB 原子序号容量。",
            suggestion="请先明确选择本次需要的链或区域；DockStart 不会截断或重编号原子。",
        )
    models = {
        _value(row, columns, "_atom_site.pdbx_pdb_model_num")
        for row in coordinate_rows
    }
    if "" in models:
        raise _fail(
            "MMCIF_MODEL_ID_MISSING",
            "至少一个坐标行没有 pdbx_PDB_model_num，不能证明模型身份。",
        )
    if len(models) != 1:
        raise _fail(
            "MMCIF_MULTIPLE_MODELS",
            f"mmCIF 包含 {len(models)} 个模型；DockStart 不会自动选择模型。",
            suggestion="请先明确保留一个模型，再重新执行残基身份审计。",
            detail=", ".join(sorted(models)),
        )
    model_id = next(iter(models))
    if not re.fullmatch(r"\d{1,4}", model_id) or not (1 <= int(model_id) <= 9999):
        raise _fail(
            "MMCIF_MODEL_ID_UNREPRESENTABLE",
            f"模型 ID“{model_id}”不能无损写入传统 PDB MODEL 字段。",
            suggestion="请先明确保留一个具有传统 PDB 可表示编号的模型。",
        )

    residues: dict[tuple[str, int, str], dict[str, Any]] = {}
    nonpolymer_residues: dict[
        tuple[str, int, str],
        dict[str, Any],
    ] = {}
    label_to_author: dict[tuple[str, str, str], tuple[str, int, str]] = {}
    atom_site_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        group = _value(row, columns, "_atom_site.group_pdb").upper()
        if group not in {"ATOM", "HETATM"}:
            continue
        row_model = _value(row, columns, "_atom_site.pdbx_pdb_model_num")
        if row_model != model_id:
            continue

        raw_auth_chain = _raw_value(row, columns, "_atom_site.auth_asym_id")
        if raw_auth_chain == "?":
            raise _fail(
                "MMCIF_AUTH_CHAIN_UNKNOWN",
                f"_atom_site 第 {row_number} 个 ATOM 行的 auth_asym_id 未知。",
            )
        auth_chain = "" if raw_auth_chain in {"", "."} else raw_auth_chain
        if len(auth_chain) > 1 or any(
            ord(character) < 33 or ord(character) > 126
            for character in auth_chain
        ):
            raise _fail(
                "MMCIF_BRIDGE_CHAIN_UNREPRESENTABLE",
                f"作者链 ID“{auth_chain}”不能无损写入传统 PDB。",
                suggestion="请先明确链选择并导出经过审计的 PDB；DockStart 不会截断链 ID。",
            )

        auth_number_raw = _value(row, columns, "_atom_site.auth_seq_id")
        auth_comp = _value(row, columns, "_atom_site.auth_comp_id").upper()
        label_chain = _value(row, columns, "_atom_site.label_asym_id")
        label_number = _value(row, columns, "_atom_site.label_seq_id")
        label_comp = _value(row, columns, "_atom_site.label_comp_id").upper()
        label_entity = _value(row, columns, "_atom_site.label_entity_id")
        identity_values = [
            auth_number_raw,
            auth_comp,
            label_chain,
            label_comp,
            label_entity,
        ]
        if group == "ATOM":
            identity_values.append(label_number)
        if not all(identity_values):
            raise _fail(
                (
                    "MMCIF_POLYMER_IDENTITY_INCOMPLETE"
                    if group == "ATOM"
                    else "MMCIF_NONPOLYMER_IDENTITY_INCOMPLETE"
                ),
                f"_atom_site 第 {row_number} 个 {group} 行缺少作者或标签残基身份。",
                detail=(
                    f"auth_seq_id={auth_number_raw!r}; auth_comp_id={auth_comp!r}; "
                    f"label_asym_id={label_chain!r}; label_seq_id={label_number!r}; "
                    f"label_comp_id={label_comp!r}; "
                    f"label_entity_id={label_entity!r}"
                ),
            )
        if (
            len(auth_comp) > 3
            or not re.fullmatch(r"[A-Z0-9]{1,3}", auth_comp)
        ):
            raise _fail(
                "MMCIF_COMPONENT_ID_UNREPRESENTABLE",
                (
                    f"{auth_chain}:{auth_number_raw} 的作者组件 ID"
                    f"“{auth_comp}”不能无损写入传统 PDB。"
                ),
                detail=f"label_comp_id={label_comp}",
            )
        atom_site_id = _value(row, columns, "_atom_site.id")
        if not atom_site_id or atom_site_id in atom_site_ids:
            raise _fail(
                "MMCIF_ATOM_SITE_ID_AMBIGUOUS",
                (
                    f"_atom_site 第 {row_number} 个 {group} 行的 id"
                    f"“{atom_site_id or '（空）'}”缺失或重复。"
                ),
            )
        atom_site_ids.add(atom_site_id)
        auth_number, insertion = _author_sequence(
            auth_number_raw,
            _raw_value(row, columns, "_atom_site.pdbx_pdb_ins_code"),
            residue_label=f"{auth_chain}:{auth_number_raw}",
        )
        auth_key = (auth_chain, auth_number, insertion)
        label_key = (label_chain, label_number, label_comp)
        if group == "ATOM":
            previous_author = label_to_author.get(label_key)
            if previous_author is not None and previous_author != auth_key:
                raise _fail(
                    "MMCIF_LABEL_AUTHOR_MAPPING_AMBIGUOUS",
                    f"标签残基 {label_chain}:{label_number} 映射到多个作者残基。",
                    detail=f"{previous_author!r}; {auth_key!r}",
                )
            label_to_author[label_key] = auth_key

        altloc_raw = _raw_value(row, columns, "_atom_site.label_alt_id")
        altloc = "" if altloc_raw in {"", ".", "?"} else altloc_raw.upper()
        if altloc and (
            len(altloc) != 1 or not re.fullmatch(r"[A-Za-z0-9]", altloc)
        ):
            raise _fail(
                "MMCIF_ALTLOC_UNREPRESENTABLE",
                f"{auth_chain}:{auth_number} 的 altloc“{altloc_raw}”不能无损写入 PDB。",
            )
        occupancy = _parse_finite_float(
            _value(row, columns, "_atom_site.occupancy"),
            code="MMCIF_OCCUPANCY_INVALID",
            label=f"{auth_chain}:{auth_number} 的 occupancy",
        )
        if occupancy < 0.0 or occupancy > 1.0:
            raise _fail(
                "MMCIF_OCCUPANCY_OUT_OF_RANGE",
                f"{auth_chain}:{auth_number} 的 occupancy={occupancy} 超出 0 到 1。",
            )
        coordinates = [
            _parse_finite_float(
                _value(row, columns, column),
                code="MMCIF_COORDINATE_INVALID",
                label=f"_atom_site {column}",
            )
            for column in (
                "_atom_site.cartn_x",
                "_atom_site.cartn_y",
                "_atom_site.cartn_z",
            )
        ]
        if any(value < -999.999 or value > 9999.999 for value in coordinates):
            raise _fail(
                "MMCIF_COORDINATE_OUT_OF_PDB_RANGE",
                f"{auth_chain}:{auth_number} 的坐标不能无损写入传统 PDB。",
            )
        label_atom = _value(row, columns, "_atom_site.label_atom_id")
        auth_atom = _value(row, columns, "_atom_site.auth_atom_id") or label_atom
        bridge_atom = auth_atom
        if not label_atom or not bridge_atom or len(bridge_atom) > 4:
            raise _fail(
                "MMCIF_ATOM_NAME_UNREPRESENTABLE",
                f"{auth_chain}:{auth_number} 的原子名不能无损写入传统 PDB。",
                detail=f"label_atom_id={label_atom!r}; auth_atom_id={auth_atom!r}",
            )

        target_residues = (
            residues if group == "ATOM" else nonpolymer_residues
        )
        other_residues = (
            nonpolymer_residues if group == "ATOM" else residues
        )
        if auth_key in other_residues:
            raise _fail(
                "MMCIF_AUTHOR_SELECTOR_AMBIGUOUS",
                (
                    f"作者残基选择器 {auth_chain}:{auth_number}"
                    f"{insertion} 同时对应 ATOM 与 HETATM。"
                ),
                suggestion=(
                    "请先在结构编辑工具中建立不会跨聚合物/非聚合物冲突的"
                    "作者残基编号。"
                ),
            )
        current = target_residues.get(auth_key)
        if current is None:
            current = {
                "record_type": group,
                "selector": f"{auth_chain}:{auth_number}"
                + (f":{insertion}" if insertion else ""),
                "meeko_id": f"{auth_chain}:{auth_number}{insertion}",
                "author": {
                    "chain_id": auth_chain,
                    "sequence_id": auth_number,
                    "insertion_code": insertion,
                    "component_id": auth_comp,
                },
                "label": {
                    "chain_id": label_chain,
                    "sequence_id": label_number,
                    "component_id": label_comp,
                    "entity_id": label_entity,
                },
                "bridge": {
                    "chain_id": auth_chain,
                    "residue_number": auth_number,
                    "insertion_code": insertion,
                    "residue_name": auth_comp,
                },
                "atoms": [],
            }
            target_residues[auth_key] = current
        elif (
            current["author"]["component_id"] != auth_comp
            or current["label"]["chain_id"] != label_chain
            or current["label"]["sequence_id"] != label_number
            or current["label"]["component_id"] != label_comp
            or current["label"]["entity_id"] != label_entity
        ):
            raise _fail(
                "MMCIF_AUTHOR_RESIDUE_MAPPING_AMBIGUOUS",
                f"作者残基 {current['selector']} 对应多个标签身份或残基名称。",
                detail=json.dumps(
                    {
                        "existing_author": current["author"],
                        "existing_label": current["label"],
                        "new_author_component": auth_comp,
                        "new_label": label_key,
                    },
                    ensure_ascii=False,
                ),
            )

        atom_key = (bridge_atom, altloc)
        if any(
            (atom["bridge_atom_name"], atom["altloc"]) == atom_key
            for atom in current["atoms"]
        ):
            raise _fail(
                "MMCIF_DUPLICATE_ATOM_IDENTITY",
                f"{current['selector']} 中原子 {bridge_atom!r}/altloc {altloc or '空'} 重复。",
            )
        current["atoms"].append(
            {
                "atom_site_id": atom_site_id,
                "label_atom_name": label_atom,
                "auth_atom_name": auth_atom,
                "bridge_atom_name": bridge_atom,
                "altloc": altloc,
                "occupancy": occupancy,
                "coordinates": coordinates,
                "element": _value(row, columns, "_atom_site.type_symbol").upper(),
            }
        )

    if not residues:
        raise _fail(
            "MMCIF_POLYMER_ATOMS_MISSING",
            "mmCIF 中没有可建立作者/标签映射的 ATOM 聚合物残基。",
        )

    residue_records = _finalize_residue_records(residues)
    nonpolymer_records = _finalize_residue_records(
        nonpolymer_residues
    )
    polymer_atom_count = sum(
        int(residue["atom_count"]) for residue in residue_records
    )
    nonpolymer_atom_count = sum(
        int(residue["atom_count"]) for residue in nonpolymer_records
    )
    if polymer_atom_count + nonpolymer_atom_count != len(coordinate_rows):
        raise _fail(
            "MMCIF_COORDINATE_IDENTITY_COUNT_CHANGED",
            "坐标行数量与完成身份聚合后的原子数量不一致。",
            detail=(
                f"rows={len(coordinate_rows)}; polymer={polymer_atom_count}; "
                f"nonpolymer={nonpolymer_atom_count}"
            ),
        )

    contract: dict[str, Any] = {
        "ok": True,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "identity_basis": IDENTITY_BASIS,
        "source": {
            "file": str(path),
            "size_bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        },
        "model": {
            "id": model_id,
            "count": 1,
            "selection_policy": "only_model_required",
        },
        "bridge_constraints": {
            "format": "pdb",
            "chain_basis": "auth_asym_id",
            "residue_number_basis": "auth_seq_id",
            "insertion_code_basis": "pdbx_PDB_ins_code",
            "atom_name_basis": "auth_atom_id_then_label_atom_id",
            "alternate_location_basis": "label_alt_id",
            "generator": "Gemmi",
            "verification_required": True,
        },
        "residue_count": len(residue_records),
        "coordinate_atom_count": len(coordinate_rows),
        "polymer_atom_count": polymer_atom_count,
        "nonpolymer_residue_count": len(nonpolymer_records),
        "nonpolymer_atom_count": nonpolymer_atom_count,
        "residues": residue_records,
        "nonpolymer_residues": nonpolymer_records,
        "warnings": [
            "occupancy 仅作为替代构象审计事实；DockStart 不会按 occupancy 自动选择 altloc。"
        ],
    }
    contract["coordinate_identity_sha256"] = _stable_sha256(
        _coordinate_identity_hash_payload(contract)
    )
    contract["identity_sha256"] = _stable_sha256(_identity_hash_payload(contract))
    return contract


def _validate_contract_hash(contract: Mapping[str, Any]) -> None:
    expected = str(contract.get("identity_sha256") or "")
    actual = _stable_sha256(_identity_hash_payload(contract))
    if not expected or expected != actual:
        raise _fail(
            "MMCIF_IDENTITY_CONTRACT_CHANGED",
            "mmCIF 残基身份合同缺少有效哈希，或内容已被修改。",
            suggestion="请从未变化的原始 mmCIF 重新生成身份合同。",
            detail=f"expected={expected}; actual={actual}",
        )
    if int(contract.get("schema_version") or 0) >= 2:
        coordinate_expected = str(
            contract.get("coordinate_identity_sha256") or ""
        )
        coordinate_actual = _stable_sha256(
            _coordinate_identity_hash_payload(contract)
        )
        if not coordinate_expected or coordinate_expected != coordinate_actual:
            raise _fail(
                "MMCIF_COORDINATE_IDENTITY_CONTRACT_CHANGED",
                "mmCIF 完整坐标身份合同缺少有效哈希，或内容已被修改。",
                suggestion="请从未变化的原始 mmCIF 重新生成身份合同。",
                detail=(
                    f"expected={coordinate_expected}; "
                    f"actual={coordinate_actual}"
                ),
            )


def resolve_mmcif_flexible_selections(
    contract: Mapping[str, Any],
    selections: Iterable[str],
    *,
    resolved_altlocs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve author-numbered flexible selections against a frozen contract."""

    _validate_contract_hash(contract)
    indexed: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    for residue in contract.get("residues", []):
        if not isinstance(residue, Mapping):
            continue
        author = residue.get("author")
        if not isinstance(author, Mapping):
            continue
        key = (
            str(author.get("chain_id") or ""),
            int(author.get("sequence_id")),
            str(author.get("insertion_code") or ""),
        )
        if key in indexed:
            raise _fail(
                "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                f"身份合同中作者残基 {key!r} 重复。",
            )
        indexed[key] = residue

    parsed_choices: dict[tuple[str, int, str], str] = {}
    for raw_selector, raw_altloc in (resolved_altlocs or {}).items():
        chain, number, insertion, _ = _parse_selector(raw_selector)
        altloc = str(raw_altloc or "").strip().upper()
        if len(altloc) != 1 or not re.fullmatch(r"[A-Za-z0-9]", altloc):
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_INVALID",
                f"{raw_selector} 的 altloc 选择“{raw_altloc}”无效。",
            )
        parsed_choices[(chain, number, insertion)] = altloc

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    used_choices: set[tuple[str, int, str]] = set()
    for raw_selector in selections:
        chain, number, insertion, canonical = _parse_selector(raw_selector)
        key = (chain, number, insertion)
        if key in seen:
            continue
        seen.add(key)
        residue = indexed.get(key)
        if residue is None:
            raise _fail(
                "MMCIF_FLEX_RESIDUE_NOT_FOUND",
                f"原始 mmCIF 身份合同中没有作者残基 {canonical}。",
                suggestion="请使用 auth_asym_id、auth_seq_id 和插入码选择；不要输入 label_asym_id/label_seq_id。",
            )
        alternate = (
            residue.get("alternate_locations")
            if isinstance(residue.get("alternate_locations"), Mapping)
            else {}
        )
        ids = [str(value) for value in alternate.get("ids", [])]
        chosen = parsed_choices.get(key, "")
        if ids and not chosen:
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_REQUIRED",
                f"{canonical} 含 altloc {', '.join(ids)}，必须明确选择一个构象。",
                suggestion="请在原始 mmCIF 审查结果中选择 altloc；DockStart 不会按 occupancy 自动决定。",
            )
        if chosen and chosen not in ids:
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_NOT_FOUND",
                f"{canonical} 不包含 altloc {chosen}。",
                detail=f"available={','.join(ids) or 'none'}",
            )
        if chosen:
            used_choices.add(key)
        selected.append(
            {
                "selector": str(residue.get("selector") or canonical),
                "meeko_id": str(residue.get("meeko_id") or ""),
                "model_id": str(
                    (contract.get("model") or {}).get("id")
                    if isinstance(contract.get("model"), Mapping)
                    else ""
                ),
                "author": dict(residue.get("author") or {}),
                "label": dict(residue.get("label") or {}),
                "bridge": dict(residue.get("bridge") or {}),
                "atom_count": int(residue.get("atom_count") or 0),
                "alternate_locations": dict(alternate),
                "selected_altloc": chosen,
            }
        )
    if not selected:
        raise _fail(
            "MMCIF_FLEX_SELECTION_EMPTY",
            "至少需要选择一个经过身份审计的柔性残基。",
        )
    unused = sorted(set(parsed_choices) - used_choices)
    if unused:
        raise _fail(
            "MMCIF_ALTLOC_CHOICE_UNUSED",
            "提交了未被当前柔性残基选择使用的 altloc 决定。",
            detail=", ".join(f"{chain}:{number}:{icode}" for chain, number, icode in unused),
        )

    result: dict[str, Any] = {
        "ok": True,
        "identity_contract_sha256": str(contract.get("identity_sha256") or ""),
        "model_id": str(
            (contract.get("model") or {}).get("id")
            if isinstance(contract.get("model"), Mapping)
            else ""
        ),
        "selected_residues": selected,
        "meeko_flexres": [str(item["meeko_id"]) for item in selected],
        "wanted_altlocs": [
            f"{item['meeko_id']}={item['selected_altloc']}"
            for item in selected
            if item["selected_altloc"]
        ],
    }
    result["selection_sha256"] = _stable_sha256(
        {
            "identity_contract_sha256": result["identity_contract_sha256"],
            "model_id": result["model_id"],
            "selected_residues": result["selected_residues"],
        }
    )
    return result


def _modern_coordinate_residue_index(
    contract: Mapping[str, Any],
) -> dict[tuple[str, int, str], Mapping[str, Any]]:
    _validate_contract_hash(contract)
    if int(contract.get("schema_version") or 0) < 2:
        raise _fail(
            "MMCIF_COORDINATE_IDENTITY_CONTRACT_REQUIRED",
            "受体准备控制必须绑定包含聚合物与非聚合物坐标身份的新版 mmCIF 合同。",
            suggestion="请从未变化的原始 mmCIF 重新执行身份审计。",
        )
    source = (
        contract.get("source")
        if isinstance(contract.get("source"), Mapping)
        else {}
    )
    source_sha256 = str(source.get("sha256") or "")
    if (
        int(source.get("size_bytes") or 0) <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
    ):
        raise _fail(
            "MMCIF_IDENTITY_SOURCE_EVIDENCE_INVALID",
            "mmCIF 身份合同缺少有效的源文件大小或 SHA256。",
        )

    indexed: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    polymer_atom_count = 0
    nonpolymer_atom_count = 0
    record_sets = (
        ("ATOM", contract.get("residues")),
        ("HETATM", contract.get("nonpolymer_residues")),
    )
    for expected_record_type, raw_records in record_sets:
        if not isinstance(raw_records, list):
            raise _fail(
                "MMCIF_COORDINATE_IDENTITY_RECORDS_INVALID",
                f"身份合同的 {expected_record_type} 残基列表无效。",
            )
        for residue in raw_records:
            if not isinstance(residue, Mapping):
                raise _fail(
                    "MMCIF_COORDINATE_IDENTITY_RECORDS_INVALID",
                    f"身份合同含非对象的 {expected_record_type} 残基记录。",
                )
            record_type = str(residue.get("record_type") or "")
            if record_type != expected_record_type:
                raise _fail(
                    "MMCIF_COORDINATE_RECORD_TYPE_CHANGED",
                    "身份合同中的 ATOM/HETATM 分类与残基列表不一致。",
                    detail=(
                        f"selector={residue.get('selector')}; "
                        f"expected={expected_record_type}; actual={record_type}"
                    ),
                )
            author = (
                residue.get("author")
                if isinstance(residue.get("author"), Mapping)
                else {}
            )
            label = (
                residue.get("label")
                if isinstance(residue.get("label"), Mapping)
                else {}
            )
            bridge = (
                residue.get("bridge")
                if isinstance(residue.get("bridge"), Mapping)
                else {}
            )
            selector = str(residue.get("selector") or "")
            chain, number, insertion, canonical = _parse_selector(selector)
            key = (chain, number, insertion)
            expected_meeko_id = f"{chain}:{number}{insertion}"
            try:
                author_number = int(author.get("sequence_id"))
                bridge_number = int(bridge.get("residue_number"))
            except (TypeError, ValueError) as exc:
                raise _fail(
                    "MMCIF_AUTHOR_IDENTITY_CHANGED",
                    f"{selector or '（无选择器）'} 的作者或桥接残基号无效。",
                    detail=str(exc),
                ) from exc
            if (
                str(author.get("chain_id") or "") != chain
                or author_number != number
                or str(author.get("insertion_code") or "") != insertion
                or canonical != selector
                or str(residue.get("meeko_id") or "") != expected_meeko_id
            ):
                raise _fail(
                    "MMCIF_AUTHOR_IDENTITY_CHANGED",
                    f"{selector or '（无选择器）'} 的作者身份或 Meeko ID 不一致。",
                )
            author_component = str(author.get("component_id") or "")
            label_component = str(label.get("component_id") or "")
            if (
                not author_component
                or not label_component
                or not str(label.get("chain_id") or "")
                or not str(label.get("entity_id") or "")
                or (
                    expected_record_type == "ATOM"
                    and not str(label.get("sequence_id") or "")
                )
            ):
                raise _fail(
                    "MMCIF_AUTHOR_LABEL_IDENTITY_INCOMPLETE",
                    f"{selector} 缺少完整的作者或标签组件身份。",
                )
            if (
                str(bridge.get("chain_id") or "") != chain
                or bridge_number != number
                or str(bridge.get("insertion_code") or "") != insertion
                or str(bridge.get("residue_name") or "")
                != author_component
            ):
                raise _fail(
                    "MMCIF_BRIDGE_RESIDUE_IDENTITY_CHANGED",
                    f"{selector} 的桥接残基身份与作者身份不一致。",
                )
            atoms = residue.get("atoms")
            if not isinstance(atoms, list) or not atoms:
                raise _fail(
                    "MMCIF_RESIDUE_ATOMS_INVALID",
                    f"{selector} 没有可审计的原子身份列表。",
                )
            atom_count = int(residue.get("atom_count") or 0)
            if atom_count != len(atoms):
                raise _fail(
                    "MMCIF_RESIDUE_ATOM_COUNT_CHANGED",
                    f"{selector} 的原子计数与原子身份列表不一致。",
                    detail=f"recorded={atom_count}; actual={len(atoms)}",
                )
            atom_hash = str(residue.get("atom_identity_sha256") or "")
            actual_atom_hash = _residue_atom_identity_hash(residue)
            if (
                not re.fullmatch(r"[0-9a-f]{64}", atom_hash)
                or atom_hash != actual_atom_hash
            ):
                raise _fail(
                    "MMCIF_RESIDUE_ATOM_IDENTITY_CHANGED",
                    f"{selector} 的逐残基原子身份哈希无效或内容已变化。",
                    detail=f"expected={atom_hash}; actual={actual_atom_hash}",
                )
            alternate = (
                residue.get("alternate_locations")
                if isinstance(residue.get("alternate_locations"), Mapping)
                else {}
            )
            recorded_altlocs = alternate.get("ids")
            derived_altlocs = sorted(
                {
                    str(atom.get("altloc") or "")
                    for atom in atoms
                    if str(atom.get("altloc") or "")
                }
            )
            if (
                not isinstance(recorded_altlocs, list)
                or recorded_altlocs != derived_altlocs
                or bool(alternate.get("requires_explicit_choice"))
                != bool(derived_altlocs)
            ):
                raise _fail(
                    "MMCIF_ALTLOC_IDENTITY_CHANGED",
                    f"{selector} 的 altloc 事实与原子身份列表不一致。",
                )
            if key in indexed:
                raise _fail(
                    "MMCIF_CONTROL_SELECTOR_AMBIGUOUS",
                    f"身份合同中的作者选择器 {canonical} 不唯一。",
                )
            indexed[key] = residue
            if expected_record_type == "ATOM":
                polymer_atom_count += atom_count
            else:
                nonpolymer_atom_count += atom_count

    if not indexed:
        raise _fail(
            "MMCIF_COORDINATE_IDENTITY_RECORDS_INVALID",
            "身份合同中没有可用于受体准备控制的残基。",
        )
    expected_counts = (
        int(contract.get("residue_count") or 0),
        int(contract.get("nonpolymer_residue_count") or 0),
        int(contract.get("polymer_atom_count") or 0),
        int(contract.get("nonpolymer_atom_count") or 0),
        int(contract.get("coordinate_atom_count") or 0),
    )
    actual_counts = (
        len(contract.get("residues") or []),
        len(contract.get("nonpolymer_residues") or []),
        polymer_atom_count,
        nonpolymer_atom_count,
        polymer_atom_count + nonpolymer_atom_count,
    )
    if expected_counts != actual_counts:
        raise _fail(
            "MMCIF_COORDINATE_IDENTITY_COUNT_CHANGED",
            "身份合同的残基或原子汇总计数与逐残基事实不一致。",
            detail=f"recorded={expected_counts}; actual={actual_counts}",
        )
    return indexed


def _bound_residue_identity(residue: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_type": str(residue.get("record_type") or ""),
        "selector": str(residue.get("selector") or ""),
        "meeko_id": str(residue.get("meeko_id") or ""),
        "author": dict(residue.get("author") or {}),
        "label": dict(residue.get("label") or {}),
        "bridge": dict(residue.get("bridge") or {}),
        "source_component_id": str(
            (
                residue.get("author")
                if isinstance(residue.get("author"), Mapping)
                else {}
            ).get("component_id")
            or ""
        ),
        "atom_count": int(residue.get("atom_count") or 0),
        "atom_identity_sha256": str(
            residue.get("atom_identity_sha256") or ""
        ),
    }


def _selected_altloc_identity(
    residue: Mapping[str, Any],
    selected_altloc: str,
) -> dict[str, Any]:
    selected_atoms = [
        dict(atom)
        for atom in residue.get("atoms", [])
        if str(atom.get("altloc") or "") in {"", selected_altloc}
    ]
    bridge_atom_names: set[str] = set()
    for atom in selected_atoms:
        atom_name = str(atom.get("bridge_atom_name") or "")
        if not atom_name or atom_name in bridge_atom_names:
            raise _fail(
                "MMCIF_SELECTED_ALTLOC_ATOMS_AMBIGUOUS",
                (
                    f"{residue.get('selector')} 选择 altloc {selected_altloc}"
                    " 后仍有重复或缺失的桥接原子名。"
                ),
            )
        bridge_atom_names.add(atom_name)
    if not selected_atoms:
        raise _fail(
            "MMCIF_SELECTED_ALTLOC_ATOMS_MISSING",
            (
                f"{residue.get('selector')} 选择 altloc {selected_altloc}"
                " 后没有原子。"
            ),
        )
    bound = _bound_residue_identity(residue)
    alternate = (
        residue.get("alternate_locations")
        if isinstance(residue.get("alternate_locations"), Mapping)
        else {}
    )
    bound.update(
        {
            "available_altlocs": list(alternate.get("ids") or []),
            "selected_altloc": selected_altloc,
            "selected_atom_count": len(selected_atoms),
            "selected_atom_identity_sha256": _stable_sha256(
                {
                    "selector": bound["selector"],
                    "selected_altloc": selected_altloc,
                    "atoms": selected_atoms,
                }
            ),
        }
    )
    return bound


def _preparation_control_hash_payload(
    controls: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": int(controls.get("schema_version") or 0),
        "contract_type": str(controls.get("contract_type") or ""),
        "identity_contract_sha256": str(
            controls.get("identity_contract_sha256") or ""
        ),
        "coordinate_identity_sha256": str(
            controls.get("coordinate_identity_sha256") or ""
        ),
        "source": dict(controls.get("source") or {}),
        "model_id": str(controls.get("model_id") or ""),
        "policy": dict(controls.get("policy") or {}),
        "alternate_locations": list(
            controls.get("alternate_locations") or []
        ),
        "template_assignments": list(
            controls.get("template_assignments") or []
        ),
        "deleted_residues": list(controls.get("deleted_residues") or []),
        "meeko": dict(controls.get("meeko") or {}),
    }


def resolve_mmcif_receptor_preparation_controls(
    identity_contract: Mapping[str, Any],
    *,
    alternate_locations: Mapping[str, str],
    template_assignments: Mapping[str, str] | None = None,
    deleted_residues: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind every receptor-preparation decision to audited mmCIF identities.

    ``alternate_locations`` is deliberately mandatory, including when the
    correct value is an empty mapping.  Every residue with alternate
    coordinates must appear exactly once.  Occupancy is never used to choose
    a branch.  Template and deletion decisions are explicit, identity-bound
    controls rather than inferred chemistry.
    """

    indexed = _modern_coordinate_residue_index(identity_contract)
    if not isinstance(alternate_locations, Mapping):
        raise _fail(
            "MMCIF_GLOBAL_ALTLOC_DECISIONS_REQUIRED",
            "必须显式提交全局 altloc 决定；没有替代构象时也应提交空对象。",
            suggestion="DockStart 不会按 occupancy 自动选择 altloc。",
        )

    parsed_altlocs: dict[tuple[str, int, str], str] = {}
    for raw_selector, raw_altloc in alternate_locations.items():
        chain, number, insertion, canonical = _parse_selector(raw_selector)
        key = (chain, number, insertion)
        if key in parsed_altlocs:
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_DUPLICATE",
                f"同一作者残基 {canonical} 被不同写法重复指定。",
            )
        if not isinstance(raw_altloc, str):
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_INVALID",
                f"{canonical} 的 altloc 选择必须是单字符文本。",
            )
        altloc = raw_altloc.strip().upper()
        if len(altloc) != 1 or not re.fullmatch(r"[A-Z0-9]", altloc):
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_INVALID",
                f"{canonical} 的 altloc 选择“{raw_altloc}”无效。",
            )
        parsed_altlocs[key] = altloc

    required_altlocs: dict[tuple[str, int, str], list[str]] = {}
    for key, residue in indexed.items():
        alternate = (
            residue.get("alternate_locations")
            if isinstance(residue.get("alternate_locations"), Mapping)
            else {}
        )
        ids = [str(value) for value in alternate.get("ids", [])]
        if ids:
            required_altlocs[key] = ids
    missing_altlocs = sorted(set(required_altlocs) - set(parsed_altlocs))
    if missing_altlocs:
        raise _fail(
            "MMCIF_GLOBAL_ALTLOC_CHOICE_REQUIRED",
            "至少一个含替代构象的残基没有显式 altloc 决定。",
            suggestion="请逐个选择；DockStart 不会采用最高 occupancy 或首个构象。",
            detail=", ".join(
                str(indexed[key].get("selector") or key)
                for key in missing_altlocs
            ),
        )
    extra_altlocs = sorted(set(parsed_altlocs) - set(required_altlocs))
    if extra_altlocs:
        first_key = extra_altlocs[0]
        if first_key not in indexed:
            raise _fail(
                "MMCIF_ALTLOC_RESIDUE_NOT_FOUND",
                "提交了身份合同中不存在的全局 altloc 残基。",
                detail=", ".join(
                    f"{chain}:{number}"
                    + (f":{insertion}" if insertion else "")
                    for chain, number, insertion in extra_altlocs
                ),
            )
        raise _fail(
            "MMCIF_ALTLOC_CHOICE_NOT_REQUIRED",
            "提交了没有替代构象的残基 altloc 决定。",
            detail=", ".join(
                str(indexed[key].get("selector") or key)
                for key in extra_altlocs
            ),
        )

    bound_altlocs: list[dict[str, Any]] = []
    for key in sorted(required_altlocs):
        chosen = parsed_altlocs[key]
        available = required_altlocs[key]
        residue = indexed[key]
        if chosen not in available:
            raise _fail(
                "MMCIF_ALTLOC_CHOICE_NOT_FOUND",
                (
                    f"{residue.get('selector')} 不包含 altloc {chosen}；"
                    f"可用值为 {', '.join(available)}。"
                ),
            )
        bound_altlocs.append(
            _selected_altloc_identity(residue, chosen)
        )

    normalized_templates: dict[
        tuple[str, int, str],
        str,
    ] = {}
    if template_assignments is not None and not isinstance(
        template_assignments,
        Mapping,
    ):
        raise _fail(
            "MMCIF_TEMPLATE_ASSIGNMENTS_INVALID",
            "残基模板赋值必须是“作者残基选择器 → 模板名”的对象。",
        )
    for raw_selector, raw_template in (
        template_assignments or {}
    ).items():
        chain, number, insertion, canonical = _parse_selector(raw_selector)
        key = (chain, number, insertion)
        if key in normalized_templates:
            raise _fail(
                "MMCIF_TEMPLATE_ASSIGNMENT_DUPLICATE",
                f"同一作者残基 {canonical} 被不同写法重复指定模板。",
            )
        if not isinstance(raw_template, str):
            raise _fail(
                "MMCIF_TEMPLATE_NAME_INVALID",
                f"{canonical} 的模板名必须是文本。",
            )
        template = raw_template.strip()
        if (
            not re.fullmatch(r"[A-Z0-9][A-Z0-9_+-]{0,15}", template)
            or template != raw_template
        ):
            raise _fail(
                "MMCIF_TEMPLATE_NAME_INVALID",
                f"{canonical} 的模板名“{raw_template}”无效。",
                suggestion="请使用 Meeko 明确支持的大小写精确模板名，例如 CYX。",
            )
        residue = indexed.get(key)
        if residue is None:
            raise _fail(
                "MMCIF_TEMPLATE_RESIDUE_NOT_FOUND",
                f"身份合同中没有模板赋值目标 {canonical}。",
            )
        if str(residue.get("record_type") or "") != "ATOM":
            raise _fail(
                "MMCIF_TEMPLATE_TARGET_NOT_POLYMER",
                f"{canonical} 不是可赋模板的聚合物 ATOM 残基。",
            )
        normalized_templates[key] = template

    normalized_deletions: dict[
        tuple[str, int, str],
        tuple[str, str],
    ] = {}
    deletion_items = list(deleted_residues or [])
    for raw_item in deletion_items:
        if not isinstance(raw_item, Mapping):
            raise _fail(
                "MMCIF_DELETED_RESIDUE_CONTROL_INVALID",
                "每个删除控制都必须是包含 selector、expected_component_id 和 reason 的对象。",
            )
        allowed_keys = {
            "selector",
            "expected_component_id",
            "reason",
        }
        unknown_keys = sorted(set(raw_item) - allowed_keys)
        missing_keys = sorted(allowed_keys - set(raw_item))
        if unknown_keys or missing_keys:
            raise _fail(
                "MMCIF_DELETED_RESIDUE_CONTROL_INVALID",
                "删除控制字段不完整或包含未知字段。",
                detail=(
                    f"missing={missing_keys}; unknown={unknown_keys}"
                ),
            )
        chain, number, insertion, canonical = _parse_selector(
            str(raw_item["selector"])
        )
        key = (chain, number, insertion)
        if key in normalized_deletions:
            raise _fail(
                "MMCIF_DELETED_RESIDUE_DUPLICATE",
                f"同一作者残基 {canonical} 被重复删除。",
            )
        raw_expected_component = raw_item["expected_component_id"]
        if not isinstance(raw_expected_component, str):
            raise _fail(
                "MMCIF_DELETED_COMPONENT_ID_INVALID",
                f"{canonical} 的 expected_component_id 必须是文本。",
            )
        expected_component = raw_expected_component.strip()
        if (
            not re.fullmatch(r"[A-Z0-9]{1,3}", expected_component)
            or expected_component
            != raw_expected_component
        ):
            raise _fail(
                "MMCIF_DELETED_COMPONENT_ID_INVALID",
                f"{canonical} 的 expected_component_id 无效。",
            )
        raw_reason = raw_item["reason"]
        if not isinstance(raw_reason, str):
            raise _fail(
                "MMCIF_DELETION_REASON_INVALID",
                f"{canonical} 的删除原因必须是受控文本值。",
            )
        reason = raw_reason.strip()
        if reason not in PREPARATION_DELETION_REASONS:
            raise _fail(
                "MMCIF_DELETION_REASON_INVALID",
                f"{canonical} 的删除原因“{reason}”不在受控枚举中。",
                detail=", ".join(sorted(PREPARATION_DELETION_REASONS)),
            )
        residue = indexed.get(key)
        if residue is None:
            raise _fail(
                "MMCIF_DELETED_RESIDUE_NOT_FOUND",
                f"身份合同中没有删除目标 {canonical}。",
            )
        if str(residue.get("record_type") or "") != "HETATM":
            raise _fail(
                "MMCIF_DELETED_RESIDUE_NOT_NONPOLYMER",
                f"{canonical} 不是 HETATM 非聚合物，不能通过该控制删除。",
            )
        author = (
            residue.get("author")
            if isinstance(residue.get("author"), Mapping)
            else {}
        )
        label = (
            residue.get("label")
            if isinstance(residue.get("label"), Mapping)
            else {}
        )
        actual_components = {
            str(author.get("component_id") or ""),
            str(label.get("component_id") or ""),
        }
        if actual_components != {expected_component}:
            raise _fail(
                "MMCIF_DELETED_COMPONENT_ID_CHANGED",
                f"{canonical} 的作者/标签组件与删除控制不一致。",
                detail=(
                    f"expected={expected_component}; "
                    f"actual={sorted(actual_components)}"
                ),
            )
        normalized_deletions[key] = (expected_component, reason)

    overlap = sorted(
        set(normalized_templates) & set(normalized_deletions)
    )
    if overlap:
        raise _fail(
            "MMCIF_PREPARATION_CONTROLS_CONFLICT",
            "同一残基不能同时被赋模板并删除。",
            detail=", ".join(
                str(indexed[key].get("selector") or key)
                for key in overlap
            ),
        )

    bound_templates: list[dict[str, Any]] = []
    for key in sorted(normalized_templates):
        bound = _bound_residue_identity(indexed[key])
        bound["assigned_template"] = normalized_templates[key]
        bound_templates.append(bound)
    bound_deletions: list[dict[str, Any]] = []
    for key in sorted(normalized_deletions):
        expected_component, reason = normalized_deletions[key]
        bound = _bound_residue_identity(indexed[key])
        bound.update(
            {
                "expected_component_id": expected_component,
                "reason": reason,
            }
        )
        bound_deletions.append(bound)

    source = (
        identity_contract.get("source")
        if isinstance(identity_contract.get("source"), Mapping)
        else {}
    )
    model = (
        identity_contract.get("model")
        if isinstance(identity_contract.get("model"), Mapping)
        else {}
    )
    controls: dict[str, Any] = {
        "ok": True,
        "schema_version": PREPARATION_CONTROL_SCHEMA_VERSION,
        "contract_type": PREPARATION_CONTROL_TYPE,
        "identity_contract_sha256": str(
            identity_contract.get("identity_sha256") or ""
        ),
        "coordinate_identity_sha256": str(
            identity_contract.get("coordinate_identity_sha256") or ""
        ),
        "source": {
            "size_bytes": int(source.get("size_bytes") or 0),
            "sha256": str(source.get("sha256") or ""),
        },
        "model_id": str(model.get("id") or ""),
        "policy": {
            "alternate_locations": "explicit_global_exact",
            "occupancy": "audit_only_never_auto_select",
            "template_assignments": "explicit_only",
            "deleted_residues": "explicit_nonpolymer_identity_bound",
            "allow_bad_res": False,
        },
        "alternate_locations": bound_altlocs,
        "template_assignments": bound_templates,
        "deleted_residues": bound_deletions,
        "meeko": {
            "wanted_altlocs": [
                f"{item['meeko_id']}={item['selected_altloc']}"
                for item in bound_altlocs
            ],
            "set_templates": [
                f"{item['meeko_id']}={item['assigned_template']}"
                for item in bound_templates
            ],
            "delete_residues": [
                str(item["meeko_id"]) for item in bound_deletions
            ],
            "allow_bad_res": False,
        },
    }
    controls["control_sha256"] = _stable_sha256(
        _preparation_control_hash_payload(controls)
    )
    return controls


def validate_mmcif_receptor_preparation_controls(
    identity_contract: Mapping[str, Any],
    controls: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebind a serialized preparation-control contract and reject changes."""

    if not isinstance(controls, Mapping):
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_INVALID",
            "mmCIF 受体准备控制合同必须是 JSON 对象。",
        )
    expected_keys = {
        "ok",
        "schema_version",
        "contract_type",
        "identity_contract_sha256",
        "coordinate_identity_sha256",
        "source",
        "model_id",
        "policy",
        "alternate_locations",
        "template_assignments",
        "deleted_residues",
        "meeko",
        "control_sha256",
    }
    if set(controls) != expected_keys:
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_FIELDS_CHANGED",
            "受体准备控制合同缺少字段或包含未知字段。",
            detail=(
                f"missing={sorted(expected_keys - set(controls))}; "
                f"unknown={sorted(set(controls) - expected_keys)}"
            ),
        )
    if (
        not isinstance(controls.get("source"), Mapping)
        or not isinstance(controls.get("policy"), Mapping)
        or not isinstance(controls.get("meeko"), Mapping)
        or not isinstance(controls.get("alternate_locations"), list)
        or not isinstance(controls.get("template_assignments"), list)
        or not isinstance(controls.get("deleted_residues"), list)
    ):
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_INVALID",
            "受体准备控制合同中的对象或决定列表格式无效。",
        )
    expected_hash = str(controls.get("control_sha256") or "")
    actual_hash = _stable_sha256(
        _preparation_control_hash_payload(controls)
    )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or expected_hash != actual_hash
    ):
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_CHANGED",
            "mmCIF 受体准备控制合同哈希无效或内容已变化。",
            detail=f"expected={expected_hash}; actual={actual_hash}",
        )

    alternate_entries = controls.get("alternate_locations")
    template_entries = controls.get("template_assignments")
    deletion_entries = controls.get("deleted_residues")
    if not all(
        isinstance(value, list)
        for value in (
            alternate_entries,
            template_entries,
            deletion_entries,
        )
    ):
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_INVALID",
            "受体准备控制合同中的决定列表格式无效。",
        )
    try:
        alternate_locations = {
            str(item["selector"]): str(item["selected_altloc"])
            for item in alternate_entries
            if isinstance(item, Mapping)
        }
        template_assignments = {
            str(item["selector"]): str(item["assigned_template"])
            for item in template_entries
            if isinstance(item, Mapping)
        }
        deleted_residues = [
            {
                "selector": str(item["selector"]),
                "expected_component_id": str(
                    item["expected_component_id"]
                ),
                "reason": str(item["reason"]),
            }
            for item in deletion_entries
            if isinstance(item, Mapping)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_INVALID",
            "受体准备控制合同中的决定项缺少必要字段。",
            detail=str(exc),
        ) from exc
    if (
        len(alternate_locations) != len(alternate_entries)
        or len(template_assignments) != len(template_entries)
        or len(deleted_residues) != len(deletion_entries)
    ):
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_INVALID",
            "受体准备控制合同含非对象决定项或重复选择器。",
        )

    rebound = resolve_mmcif_receptor_preparation_controls(
        identity_contract,
        alternate_locations=alternate_locations,
        template_assignments=template_assignments,
        deleted_residues=deleted_residues,
    )
    if dict(controls) != rebound:
        raise _fail(
            "MMCIF_PREPARATION_CONTROL_BINDING_CHANGED",
            "受体准备控制合同与当前作者/标签/原子身份不能逐字段重新绑定。",
            suggestion="请从未变化的原始 mmCIF 重新生成准备控制合同。",
        )
    return rebound


def _parse_pdb_bridge(path: Path) -> tuple[list[dict[str, Any]], str, int]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise _fail(
            "MMCIF_BRIDGE_PDB_NOT_FOUND",
            f"没有找到非空的普通桥接 PDB：{path}",
        )
    atoms: list[dict[str, Any]] = []
    model_markers = 0
    current_model = "1"
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8", errors="strict").splitlines(),
        start=1,
    ):
        record = line[0:6].strip().upper()
        if record == "MODEL":
            model_markers += 1
            current_model = line[10:14].strip() or str(model_markers)
            continue
        if record not in {"ATOM", "HETATM"}:
            continue
        if len(line) < 60:
            raise _fail(
                "MMCIF_BRIDGE_PDB_ATOM_TRUNCATED",
                f"桥接 PDB 第 {line_number} 行 {record} 字段不完整。",
            )
        try:
            residue_number = int(line[22:26].strip())
            coordinates = [
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            ]
            occupancy = float(line[54:60])
        except ValueError as exc:
            raise _fail(
                "MMCIF_BRIDGE_PDB_NUMERIC_INVALID",
                f"桥接 PDB 第 {line_number} 行含无效编号、坐标或 occupancy。",
                detail=str(exc),
            ) from exc
        if not all(math.isfinite(value) for value in [*coordinates, occupancy]):
            raise _fail(
                "MMCIF_BRIDGE_PDB_NUMERIC_INVALID",
                f"桥接 PDB 第 {line_number} 行含非有限数值。",
            )
        atoms.append(
            {
                "record_type": record,
                "model": current_model,
                "chain_id": line[21:22].strip(),
                "residue_number": residue_number,
                "insertion_code": line[26:27].strip().upper(),
                "residue_name": line[17:20].strip().upper(),
                "atom_name": line[12:16].strip(),
                "altloc": line[16:17].strip().upper(),
                "occupancy": occupancy,
                "coordinates": coordinates,
            }
        )
    if not atoms:
        raise _fail(
            "MMCIF_BRIDGE_PDB_ATOMS_MISSING",
            "桥接 PDB 中没有 ATOM/HETATM 坐标原子。",
        )
    model_count = model_markers or 1
    if model_count != 1:
        raise _fail(
            "MMCIF_BRIDGE_PDB_MULTIPLE_MODELS",
            f"桥接 PDB 包含 {model_count} 个模型。",
        )
    return atoms, current_model, model_count


def verify_mmcif_pdb_bridge(
    contract: Mapping[str, Any],
    pdb_path: str | Path,
    *,
    coordinate_tolerance: float = 0.0011,
    occupancy_tolerance: float = 0.0051,
) -> dict[str, Any]:
    """Verify that a Gemmi PDB preserves the contract's coordinate identities."""

    _validate_contract_hash(contract)
    if (
        not math.isfinite(coordinate_tolerance)
        or coordinate_tolerance < 0.0
        or not math.isfinite(occupancy_tolerance)
        or occupancy_tolerance < 0.0
    ):
        raise _fail(
            "MMCIF_BRIDGE_TOLERANCE_INVALID",
            "桥接 PDB 验证容差必须是非负有限数值。",
        )
    path = Path(pdb_path)
    bridge_atoms, bridge_model_id, model_count = _parse_pdb_bridge(path)
    contract_model = (
        contract.get("model") if isinstance(contract.get("model"), Mapping) else {}
    )
    expected_model_id = str(contract_model.get("id") or "")
    if not expected_model_id or bridge_model_id != expected_model_id:
        raise _fail(
            "MMCIF_BRIDGE_PDB_MODEL_CHANGED",
            "桥接 PDB 的模型 ID 与 mmCIF 身份合同不一致。",
            detail=f"expected={expected_model_id}; actual={bridge_model_id}",
        )
    verify_nonpolymer = int(contract.get("schema_version") or 0) >= 2
    relevant_bridge_atoms = [
        atom
        for atom in bridge_atoms
        if atom["record_type"] == "ATOM"
        or (verify_nonpolymer and atom["record_type"] == "HETATM")
    ]
    indexed: dict[
        tuple[str, str, int, str, str, str],
        dict[str, Any],
    ] = {}
    for atom in relevant_bridge_atoms:
        key = (
            str(atom["record_type"]),
            str(atom["chain_id"]),
            int(atom["residue_number"]),
            str(atom["insertion_code"]),
            str(atom["atom_name"]),
            str(atom["altloc"]),
        )
        if key in indexed:
            raise _fail(
                "MMCIF_BRIDGE_PDB_ATOM_AMBIGUOUS",
                f"桥接 PDB 中原子身份 {key!r} 重复。",
            )
        indexed[key] = atom

    expected_keys: set[tuple[str, str, int, str, str, str]] = set()
    verified_counts = {"ATOM": 0, "HETATM": 0}
    contract_groups: list[tuple[str, Any]] = [
        ("ATOM", contract.get("residues")),
    ]
    if verify_nonpolymer:
        contract_groups.append(
            ("HETATM", contract.get("nonpolymer_residues"))
        )
    for expected_record_type, raw_residues in contract_groups:
        if not isinstance(raw_residues, list):
            raise _fail(
                "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                f"身份合同的 {expected_record_type} 残基列表无效。",
            )
        for residue in raw_residues:
            if not isinstance(residue, Mapping):
                raise _fail(
                    "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                    "身份合同包含非对象的残基记录。",
                )
            if (
                verify_nonpolymer
                and str(residue.get("record_type") or "")
                != expected_record_type
            ):
                raise _fail(
                    "MMCIF_COORDINATE_RECORD_TYPE_CHANGED",
                    "身份合同中的 ATOM/HETATM 分类发生变化。",
                )
            bridge = (
                residue.get("bridge")
                if isinstance(residue.get("bridge"), Mapping)
                else {}
            )
            residue_name = str(bridge.get("residue_name") or "")
            raw_atoms = residue.get("atoms")
            if not isinstance(raw_atoms, list):
                raise _fail(
                    "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                    "身份合同中的残基原子列表无效。",
                )
            for raw_atom in raw_atoms:
                if not isinstance(raw_atom, Mapping):
                    raise _fail(
                        "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                        "身份合同包含非对象的原子记录。",
                    )
                key = (
                    expected_record_type,
                    str(bridge.get("chain_id") or ""),
                    int(bridge.get("residue_number")),
                    str(bridge.get("insertion_code") or ""),
                    str(raw_atom.get("bridge_atom_name") or ""),
                    str(raw_atom.get("altloc") or ""),
                )
                if key in expected_keys:
                    raise _fail(
                        "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                        f"身份合同中的桥接原子身份 {key!r} 重复。",
                    )
                expected_keys.add(key)
                actual = indexed.get(key)
                if actual is None:
                    raise _fail(
                        "MMCIF_BRIDGE_PDB_ATOM_MISSING",
                        f"桥接 PDB 缺少身份合同原子 {key!r}。",
                    )
                if actual["residue_name"] != residue_name:
                    raise _fail(
                        "MMCIF_BRIDGE_PDB_RESIDUE_CHANGED",
                        f"桥接 PDB 中 {key!r} 的残基名发生变化。",
                        detail=(
                            f"expected={residue_name}; "
                            f"actual={actual['residue_name']}"
                        ),
                    )
                coordinates = raw_atom.get("coordinates")
                if (
                    not isinstance(coordinates, list)
                    or len(coordinates) != 3
                ):
                    raise _fail(
                        "MMCIF_IDENTITY_CONTRACT_AMBIGUOUS",
                        f"身份合同中 {key!r} 的坐标字段无效。",
                    )
                coordinate_deltas = [
                    abs(float(left) - float(right))
                    for left, right in zip(
                        coordinates,
                        actual["coordinates"],
                        strict=True,
                    )
                ]
                if any(
                    delta > coordinate_tolerance
                    for delta in coordinate_deltas
                ):
                    raise _fail(
                        "MMCIF_BRIDGE_PDB_COORDINATES_CHANGED",
                        f"桥接 PDB 中 {key!r} 的坐标超过允许的 PDB 舍入误差。",
                        detail=f"deltas={coordinate_deltas}",
                    )
                occupancy_delta = abs(
                    float(raw_atom.get("occupancy"))
                    - float(actual["occupancy"])
                )
                if occupancy_delta > occupancy_tolerance:
                    raise _fail(
                        "MMCIF_BRIDGE_PDB_OCCUPANCY_CHANGED",
                        (
                            f"桥接 PDB 中 {key!r} 的 occupancy"
                            " 超过允许的 PDB 舍入误差。"
                        ),
                        detail=f"delta={occupancy_delta}",
                    )
                verified_counts[expected_record_type] += 1

    extra = sorted(set(indexed) - expected_keys)
    if extra:
        raise _fail(
            (
                "MMCIF_BRIDGE_PDB_COORDINATE_ATOMS_EXTRA"
                if verify_nonpolymer
                else "MMCIF_BRIDGE_PDB_POLYMER_ATOMS_EXTRA"
            ),
            (
                "桥接 PDB 含有身份合同未记录的 ATOM/HETATM 原子。"
                if verify_nonpolymer
                else "桥接 PDB 含有身份合同未记录的聚合物 ATOM 原子。"
            ),
            detail=json.dumps(extra[:20], ensure_ascii=False),
        )
    bridge_polymer_atom_count = sum(
        atom["record_type"] == "ATOM" for atom in bridge_atoms
    )
    bridge_nonpolymer_atom_count = sum(
        atom["record_type"] == "HETATM" for atom in bridge_atoms
    )
    result = {
        "ok": True,
        "identity_contract_sha256": str(contract.get("identity_sha256") or ""),
        "bridge": {
            "file": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_bytes(path.read_bytes()),
            "model_count": model_count,
            "model_id": bridge_model_id,
            "coordinate_atom_count": len(bridge_atoms),
            "polymer_atom_count": bridge_polymer_atom_count,
            "nonpolymer_atom_count": bridge_nonpolymer_atom_count,
        },
        "verified_residue_count": int(contract.get("residue_count") or 0),
        "verified_nonpolymer_residue_count": (
            int(contract.get("nonpolymer_residue_count") or 0)
            if verify_nonpolymer
            else 0
        ),
        "verified_atom_count": verified_counts["ATOM"],
        "verified_nonpolymer_atom_count": verified_counts["HETATM"],
        "verified_coordinate_atom_count": (
            verified_counts["ATOM"] + verified_counts["HETATM"]
        ),
        "coordinate_tolerance_angstrom": coordinate_tolerance,
        "occupancy_tolerance": occupancy_tolerance,
    }
    verification_hash_payload = {
        "identity_contract_sha256": result["identity_contract_sha256"],
        "bridge_sha256": result["bridge"]["sha256"],
        "verified_residue_count": result["verified_residue_count"],
        "verified_atom_count": result["verified_atom_count"],
    }
    if verify_nonpolymer:
        verification_hash_payload.update(
            {
                "verified_nonpolymer_residue_count": result[
                    "verified_nonpolymer_residue_count"
                ],
                "verified_nonpolymer_atom_count": result[
                    "verified_nonpolymer_atom_count"
                ],
                "verified_coordinate_atom_count": result[
                    "verified_coordinate_atom_count"
                ],
            }
        )
    result["verification_sha256"] = _stable_sha256(
        verification_hash_payload
    )
    return result
