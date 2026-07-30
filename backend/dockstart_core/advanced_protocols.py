"""Validated plans and transactional runners for advanced docking protocols.

Plan builders remain side-effect free.  The optional execution helpers run an
already reviewed Meeko command in isolated staging paths, validate every
declared output, and only then publish the complete output set.  This keeps the
scientific assumptions and the process boundary independently testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


MAX_STRUCTURE_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
SUPPORTED_RECEPTOR_SUFFIXES = frozenset({".pdb", ".cif", ".mmcif"})
WATER_RESIDUES = frozenset({"HOH", "WAT", "H2O", "DOD", "SOL", "TIP", "TIP3"})
GLUE_TYPE_PATTERN = re.compile(r"^G\d+$")
CLOSURE_ANCHOR_PATTERN = re.compile(r"^CG\d+$")
MEEKO_BAD_RESIDUE_PATTERN = re.compile(
    r"No template matched for residue_key=['\"]([^'\"]+)['\"]"
)
MEEKO_BAD_RESIDUE_SUMMARY_PATTERN = re.compile(
    r"Template matching failed for:\s*\[(.*?)\]",
    re.DOTALL,
)
MEEKO_CONTROL_TOKEN_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_+-]{0,7}$")
MEEKO_DELETION_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MEEKO_RECEPTOR_CONTROLS_SCHEMA_VERSION = 1
MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION = "json_utf8_sort_keys_compact_v1"


class ProtocolValidationError(ValueError):
    """A user-correctable protocol validation failure."""

    def __init__(
        self,
        code: str,
        title: str,
        message: str,
        *,
        suggestion: str = "",
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.title = title
        self.message = message
        self.suggestion = suggestion
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class FlexibleResidueSelector:
    """A normalized ``chain:resnum[:icode]`` residue selection."""

    chain: str
    residue_number: int
    insertion_code: str = ""

    @property
    def canonical(self) -> str:
        suffix = f":{self.insertion_code}" if self.insertion_code else ""
        return f"{self.chain}:{self.residue_number}{suffix}"

    @property
    def meeko_id(self) -> str:
        # Meeko 0.7.x encodes an insertion code immediately after the number.
        return f"{self.chain}:{self.residue_number}{self.insertion_code}"

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.chain, self.residue_number, self.insertion_code)


@dataclass
class _ResidueRecord:
    chain: str
    residue_number: int
    insertion_code: str
    residue_name: str
    record_types: set[str]
    altlocs: set[str]
    atom_count: int = 0

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.chain, self.residue_number, self.insertion_code)


def _validation_error(
    code: str,
    title: str,
    message: str,
    suggestion: str,
    *,
    detail: str = "",
) -> ProtocolValidationError:
    return ProtocolValidationError(
        code,
        title,
        message,
        suggestion=suggestion,
        detail=detail,
    )


def _read_text_file(path: Path, *, max_bytes: int, label: str) -> str:
    if not path.exists() or not path.is_file():
        raise _validation_error(
            "INPUT_FILE_NOT_FOUND",
            f"没有找到{label}",
            f"没有找到文件：{path}",
            "请重新选择存在的本地文件。",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _validation_error(
            "INPUT_FILE_UNREADABLE",
            f"无法读取{label}",
            f"无法读取文件属性：{path}",
            "请检查文件权限后重试。",
            detail=str(exc),
        ) from exc
    if size > max_bytes:
        raise _validation_error(
            "INPUT_FILE_TOO_LARGE",
            f"{label}过大",
            f"文件大小为 {size} B，超过当前安全读取上限 {max_bytes} B。",
            "请确认所选文件正确，或先生成只包含目标结构的副本。",
        )
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise _validation_error(
            "INPUT_FILE_UNREADABLE",
            f"无法读取{label}",
            f"无法读取文件：{path}",
            "请检查文件权限后重试。",
            detail=str(exc),
        ) from exc


def _validate_output_path(path: Path, label: str) -> Path:
    if path.exists() and path.is_dir():
        raise _validation_error(
            "OUTPUT_IS_DIRECTORY",
            f"{label}不是文件路径",
            f"输出路径指向目录：{path}",
            "请选择具体的输出文件名。",
        )
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise _validation_error(
            "OUTPUT_DIRECTORY_NOT_FOUND",
            f"{label}目录不存在",
            f"输出目录不存在：{parent}",
            "请先创建输出目录，或选择已有目录。",
        )
    if not os.access(parent, os.W_OK):
        raise _validation_error(
            "OUTPUT_DIRECTORY_NOT_WRITABLE",
            f"{label}目录不可写",
            f"当前进程不能写入目录：{parent}",
            "请更换输出目录或修复目录权限。",
        )
    return path


def _validate_python_executable(value: str | Path) -> str:
    text = str(value).strip()
    if not text:
        text = sys.executable
    candidate = Path(text)
    if not candidate.exists() or not candidate.is_file():
        raise _validation_error(
            "PYTHON_NOT_FOUND",
            "没有找到 Python",
            f"Python 可执行文件不存在：{candidate}",
            "请在工具链设置中选择可用的 Assisted Python。",
        )
    return str(candidate)


def parse_flexible_residue(value: str) -> FlexibleResidueSelector:
    """Parse and normalize a safe ``chain:resnum[:icode]`` selector.

    The compact Meeko form (for example ``A:42B``) is also accepted so a
    previously persisted Meeko selection can be reopened without guessing.
    """

    text = str(value).strip()
    explicit = re.fullmatch(
        r"(?P<chain>[^:\s]*):(?P<resnum>-?\d+)(?::(?P<icode>[A-Za-z0-9]))?",
        text,
    )
    compact = None
    if explicit is None:
        compact = re.fullmatch(
            r"(?P<chain>[^:\s]*):(?P<resnum>-?\d+)(?P<icode>[A-Za-z])",
            text,
        )
    match = explicit or compact
    if match is None:
        raise _validation_error(
            "INVALID_FLEX_RESIDUE_ID",
            "柔性残基格式无效",
            f"无法解析柔性残基：{text or '（空）'}。",
            "请使用 chain:resnum 或 chain:resnum:icode，例如 A:315 或 A:315:B。",
        )

    chain = match.group("chain")
    if chain in {".", "?"}:
        chain = ""
    if len(chain) > 8 or not re.fullmatch(r"[A-Za-z0-9_.-]*", chain):
        raise _validation_error(
            "INVALID_CHAIN_ID",
            "链 ID 无效",
            f"链 ID“{chain}”包含不支持的字符或长度超过 8。",
            "请从原始结构中选择实际存在的链 ID。",
        )
    residue_number = int(match.group("resnum"))
    if residue_number < -9999 or residue_number > 999999:
        raise _validation_error(
            "INVALID_RESIDUE_NUMBER",
            "残基编号超出范围",
            f"残基编号 {residue_number} 超出当前支持范围。",
            "请核对原始 PDB/mmCIF 的残基编号。",
        )
    insertion_code = (match.group("icode") or "").upper()
    return FlexibleResidueSelector(chain, residue_number, insertion_code)


def extract_meeko_bad_residues(output: str) -> list[str]:
    """Extract Meeko residue IDs that failed template matching.

    Meeko 0.7.x writes detailed diagnostics to stderr and a shorter summary to
    stdout.  Parse both forms without executing or evaluating text emitted by
    the external process.
    """

    text = str(output or "")
    values = MEEKO_BAD_RESIDUE_PATTERN.findall(text)
    for summary in MEEKO_BAD_RESIDUE_SUMMARY_PATTERN.findall(text):
        values.extend(re.findall(r"['\"]([^'\"]+)['\"]", summary))
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    return unique


def _normalize_bad_residue_acknowledgements(
    values: Iterable[str] | None,
    *,
    allow_bad_res: bool,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        residue_id = parse_flexible_residue(value).meeko_id
        if residue_id not in seen:
            normalized.append(residue_id)
            seen.add(residue_id)
    if allow_bad_res and not normalized:
        raise _validation_error(
            "FLEX_BAD_RESIDUE_ACKNOWLEDGEMENT_REQUIRED",
            "尚未确认将被忽略的残基",
            "启用 --allow_bad_res 前必须提供已审阅的坏残基清单。",
            "请先按严格模式运行，审阅 Meeko 返回的完整残基列表，再明确确认。",
        )
    if not allow_bad_res and normalized:
        raise _validation_error(
            "FLEX_BAD_RESIDUE_ACKNOWLEDGEMENT_UNUSED",
            "坏残基确认与严格模式冲突",
            "严格模式下不能提交坏残基确认清单。",
            "请保持默认严格模式，或明确启用允许忽略坏残基。",
        )
    return normalized


def _parse_pdb_residues(text: str) -> dict[tuple[str, int, str], _ResidueRecord]:
    residues: dict[tuple[str, int, str], _ResidueRecord] = {}
    for line in text.splitlines():
        record_type = line[0:6].strip().upper()
        if record_type not in {"ATOM", "HETATM"}:
            continue
        if len(line) < 27:
            continue
        residue_number_text = line[22:26].strip()
        try:
            residue_number = int(residue_number_text)
        except ValueError:
            continue
        chain = line[21:22].strip()
        insertion_code = line[26:27].strip().upper()
        residue_name = line[17:20].strip().upper()
        altloc = line[16:17].strip().upper()
        key = (chain, residue_number, insertion_code)
        current = residues.get(key)
        if current is None:
            current = _ResidueRecord(
                chain=chain,
                residue_number=residue_number,
                insertion_code=insertion_code,
                residue_name=residue_name,
                record_types=set(),
                altlocs=set(),
            )
            residues[key] = current
        current.record_types.add(record_type)
        current.atom_count += 1
        if altloc:
            current.altlocs.add(altloc)
    return residues


def _normal_cif_value(value: str) -> str:
    return "" if value in {".", "?"} else value.strip()


def _cif_column(row: Sequence[str], columns: Mapping[str, int], *names: str) -> str:
    for name in names:
        index = columns.get(name)
        if index is not None and index < len(row):
            value = _normal_cif_value(row[index])
            if value:
                return value
    return ""


def _find_atom_site_table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()
    for start, raw_line in enumerate(lines):
        if raw_line.strip().lower() != "loop_":
            continue
        headers: list[str] = []
        cursor = start + 1
        while cursor < len(lines) and lines[cursor].strip().startswith("_"):
            headers.append(lines[cursor].strip().split()[0].lower())
            cursor += 1
        if not headers or not all(header.startswith("_atom_site.") for header in headers):
            continue
        tokens: list[str] = []
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if not stripped:
                cursor += 1
                continue
            if stripped == "#" or stripped.lower() == "loop_" or stripped.startswith("_"):
                break
            if stripped.startswith(";"):
                raise _validation_error(
                    "UNSUPPORTED_MMCIF_ATOM_TABLE",
                    "mmCIF 原子表无法安全解析",
                    "_atom_site 表中包含多行文本字段，当前轻量解析器无法可靠处理。",
                    "请将结构转换为规范 PDB，或使用经过审计的 mmCIF 转换流程。",
                )
            try:
                tokens.extend(shlex.split(stripped, comments=False, posix=True))
            except ValueError as exc:
                raise _validation_error(
                    "INVALID_MMCIF_SYNTAX",
                    "mmCIF 语法无效",
                    "_atom_site 表包含未闭合引号或其他语法错误。",
                    "请重新下载原始 mmCIF，或转换为规范 PDB。",
                    detail=str(exc),
                ) from exc
            cursor += 1
        if len(tokens) % len(headers) != 0:
            raise _validation_error(
                "INVALID_MMCIF_ATOM_TABLE",
                "mmCIF 原子表列数不一致",
                "_atom_site 数据行与字段数量不匹配，无法安全验证柔性残基。",
                "请重新下载原始 mmCIF，或转换为规范 PDB。",
            )
        rows = [tokens[index : index + len(headers)] for index in range(0, len(tokens), len(headers))]
        return headers, rows
    raise _validation_error(
        "MMCIF_ATOM_SITE_NOT_FOUND",
        "mmCIF 中没有原子表",
        "没有找到可解析的 _atom_site 表。",
        "请确认所选文件是包含坐标的 PDBx/mmCIF 结构。",
    )


def _parse_mmcif_residues(text: str) -> dict[tuple[str, int, str], _ResidueRecord]:
    headers, rows = _find_atom_site_table(text)
    columns = {name: index for index, name in enumerate(headers)}
    residues: dict[tuple[str, int, str], _ResidueRecord] = {}
    for row in rows:
        record_type = _cif_column(row, columns, "_atom_site.group_pdb").upper()
        if record_type not in {"ATOM", "HETATM"}:
            continue
        chain = _cif_column(
            row,
            columns,
            "_atom_site.auth_asym_id",
            "_atom_site.label_asym_id",
        )
        residue_number_text = _cif_column(
            row,
            columns,
            "_atom_site.auth_seq_id",
            "_atom_site.label_seq_id",
        )
        match = re.fullmatch(r"(-?\d+)([A-Za-z0-9]?)", residue_number_text)
        if match is None:
            continue
        residue_number = int(match.group(1))
        insertion_code = _cif_column(row, columns, "_atom_site.pdbx_pdb_ins_code").upper()
        if not insertion_code:
            insertion_code = match.group(2).upper()
        residue_name = _cif_column(
            row,
            columns,
            "_atom_site.auth_comp_id",
            "_atom_site.label_comp_id",
        ).upper()
        altloc = _cif_column(
            row,
            columns,
            "_atom_site.label_alt_id",
            "_atom_site.auth_alt_id",
        ).upper()
        key = (chain, residue_number, insertion_code)
        current = residues.get(key)
        if current is None:
            current = _ResidueRecord(
                chain=chain,
                residue_number=residue_number,
                insertion_code=insertion_code,
                residue_name=residue_name,
                record_types=set(),
                altlocs=set(),
            )
            residues[key] = current
        current.record_types.add(record_type)
        current.atom_count += 1
        if altloc:
            current.altlocs.add(altloc)
    return residues


def _load_receptor_residues(path: Path) -> tuple[str, dict[tuple[str, int, str], _ResidueRecord]]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_RECEPTOR_SUFFIXES:
        raise _validation_error(
            "UNSUPPORTED_RECEPTOR_FORMAT",
            "柔性残基需要原始结构",
            f"不支持使用 {suffix or '无扩展名'} 文件验证柔性残基。",
            "请选择原始 PDB 或 mmCIF；不能仅从最终 PDBQT 推断聚合物与替代构象。",
        )
    text = _read_text_file(path, max_bytes=MAX_STRUCTURE_BYTES, label="原始受体结构")
    if suffix == ".pdb":
        source_format = "pdb"
        residues = _parse_pdb_residues(text)
    else:
        source_format = "mmcif"
        residues = _parse_mmcif_residues(text)
    if not residues:
        raise _validation_error(
            "RECEPTOR_HAS_NO_RESIDUES",
            "原始受体中没有可验证的残基",
            "没有从原始结构中解析到 ATOM/HETATM 残基。",
            "请确认文件包含三维原子坐标且格式完整。",
        )
    return source_format, residues


def _normalize_altloc_choices(
    choices: Mapping[str, str] | None,
) -> dict[tuple[str, int, str], str]:
    normalized: dict[tuple[str, int, str], str] = {}
    for raw_selector, raw_altloc in (choices or {}).items():
        selector = parse_flexible_residue(raw_selector)
        altloc = str(raw_altloc).strip().upper()
        if len(altloc) != 1 or not altloc.isalnum():
            raise _validation_error(
                "INVALID_ALTLOC_CHOICE",
                "替代构象选择无效",
                f"残基 {selector.canonical} 的替代构象“{raw_altloc}”无效。",
                "请选择原始结构中实际存在的单字符 altloc ID。",
            )
        normalized[selector.key] = altloc
    return normalized


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_control_selector_mapping(
    raw: Any,
    *,
    field_name: str,
    value_label: str,
    value_normalizer: Callable[[Any, FlexibleResidueSelector], str],
) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_CONTROL_MAPPING",
            "受体控制合同字段无效",
            f"{field_name} 必须是 selector 到 {value_label} 的对象映射。",
            "请使用结构化 JSON 对象，不要传入字符串、参数数组或自由命令行。",
        )
    normalized: dict[str, str] = {}
    original_by_selector: dict[str, str] = {}
    for raw_selector, raw_value in raw.items():
        selector = parse_flexible_residue(str(raw_selector))
        canonical = selector.canonical
        if canonical in normalized:
            raise _validation_error(
                "DUPLICATE_MEEKO_RECEPTOR_CONTROL_SELECTOR",
                "受体控制合同含重复残基",
                f"{field_name} 中的 {raw_selector!r} 与 "
                f"{original_by_selector[canonical]!r} 指向同一残基 {canonical}。",
                "每个规范化残基在同一控制字段中只能出现一次。",
            )
        normalized[canonical] = value_normalizer(raw_value, selector)
        original_by_selector[canonical] = str(raw_selector)
    return {key: normalized[key] for key in sorted(normalized)}


def _normalize_control_altloc(
    raw_value: Any,
    selector: FlexibleResidueSelector,
) -> str:
    value = str(raw_value).strip().upper()
    if len(value) != 1 or not value.isalnum():
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_ALTLOC",
            "受体替代构象控制无效",
            f"残基 {selector.canonical} 的 altloc {raw_value!r} 不是单个字母或数字。",
            "请明确选择原始结构中实际存在的单字符 altloc ID。",
        )
    return value


def _normalize_control_template(
    raw_value: Any,
    selector: FlexibleResidueSelector,
) -> str:
    value = str(raw_value).strip().upper()
    if MEEKO_CONTROL_TOKEN_PATTERN.fullmatch(value) is None:
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_TEMPLATE",
            "受体残基模板控制无效",
            f"残基 {selector.canonical} 的模板名 {raw_value!r} 无效。",
            "请使用已审阅的 Meeko 模板标识，例如 CYX、HID、HIE 或 HIP。",
        )
    return value


def _normalize_deleted_residue_controls(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_DELETION_LIST",
            "受体删除控制字段无效",
            "deleted_residues 必须是结构化对象列表。",
            "每项都必须明确 selector、expected_component_id 和 reason。",
        )
    normalized: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    required = {"selector", "expected_component_id", "reason"}
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise _validation_error(
                "INVALID_MEEKO_RECEPTOR_DELETION",
                "受体删除控制项无效",
                f"deleted_residues[{index}] 不是对象。",
                "请为每个待删除对象提供明确的残基身份、预期组分和删除理由。",
            )
        unknown = sorted(str(key) for key in set(item) - required)
        missing = sorted(required - set(item))
        if unknown or missing:
            detail_parts: list[str] = []
            if unknown:
                detail_parts.append(f"未知字段：{', '.join(unknown)}")
            if missing:
                detail_parts.append(f"缺少字段：{', '.join(missing)}")
            raise _validation_error(
                "INVALID_MEEKO_RECEPTOR_DELETION",
                "受体删除控制项字段不完整",
                f"deleted_residues[{index}] 未通过严格字段校验。",
                "每项只能包含 selector、expected_component_id 和 reason。",
                detail="；".join(detail_parts),
            )
        selector = parse_flexible_residue(str(item["selector"]))
        canonical = selector.canonical
        if canonical in seen:
            raise _validation_error(
                "DUPLICATE_MEEKO_RECEPTOR_CONTROL_SELECTOR",
                "受体控制合同含重复删除对象",
                f"deleted_residues[{index}] 与 {seen[canonical]} "
                f"指向同一残基 {canonical}。",
                "同一残基只能声明一次删除。",
            )
        component = str(item["expected_component_id"]).strip().upper()
        if MEEKO_CONTROL_TOKEN_PATTERN.fullmatch(component) is None:
            raise _validation_error(
                "INVALID_MEEKO_RECEPTOR_COMPONENT_ID",
                "待删除组分标识无效",
                f"{canonical} 的 expected_component_id {item['expected_component_id']!r} 无效。",
                "请填写原始结构中实际记录的组分 ID，例如 BEN。",
            )
        reason = str(item["reason"]).strip().lower()
        if MEEKO_DELETION_REASON_PATTERN.fullmatch(reason) is None:
            raise _validation_error(
                "INVALID_MEEKO_RECEPTOR_DELETION_REASON",
                "受体删除理由无效",
                f"{canonical} 的 reason {item['reason']!r} 无效。",
                "请使用稳定的机器可读标识，例如 co_crystal_ligand。",
            )
        normalized.append(
            {
                "selector": canonical,
                "expected_component_id": component,
                "reason": reason,
            }
        )
        seen[canonical] = f"deleted_residues[{index}]"
    return sorted(normalized, key=lambda item: item["selector"])


def _validate_normalized_receptor_controls(
    controls: Mapping[str, Any],
    *,
    structure_path: str | Path,
    flexible_selections: Iterable[str],
) -> None:
    _, available = _load_receptor_residues(Path(structure_path))
    alternate_locations = controls["alternate_locations"]
    template_assignments = controls["template_assignments"]
    deleted_residues = controls["deleted_residues"]
    deleted_selectors = {str(item["selector"]) for item in deleted_residues}

    conflicts = sorted(
        deleted_selectors
        & (set(alternate_locations) | set(template_assignments))
    )
    selected_flexible = {
        parse_flexible_residue(value).canonical for value in flexible_selections
    }
    conflicts.extend(sorted(deleted_selectors & selected_flexible))
    if conflicts:
        raise _validation_error(
            "MEEKO_RECEPTOR_CONTROL_CONFLICT",
            "受体控制合同含冲突操作",
            "同一残基不能在被删除的同时选择 altloc、指定模板或设为柔性侧链。",
            "请保留一个明确、可执行的受体处理决定。",
            detail=json.dumps(sorted(set(conflicts)), ensure_ascii=False),
        )

    for canonical, altloc in alternate_locations.items():
        selector = parse_flexible_residue(canonical)
        residue = available.get(selector.key)
        if residue is None:
            raise _validation_error(
                "MEEKO_RECEPTOR_CONTROL_SELECTOR_NOT_FOUND",
                "受体控制残基不存在",
                f"原始结构中没有找到 alternate_locations 的 {canonical}。",
                "请核对链 ID、残基编号和插入码。",
            )
        if altloc not in residue.altlocs:
            raise _validation_error(
                "ALTLOC_NOT_FOUND",
                "所选替代构象不存在",
                f"{canonical} 不包含 altloc {altloc}。",
                f"可用 altloc：{', '.join(sorted(residue.altlocs)) or '无'}。",
            )

    for canonical in template_assignments:
        selector = parse_flexible_residue(canonical)
        residue = available.get(selector.key)
        if residue is None:
            raise _validation_error(
                "MEEKO_RECEPTOR_CONTROL_SELECTOR_NOT_FOUND",
                "受体控制残基不存在",
                f"原始结构中没有找到 template_assignments 的 {canonical}。",
                "请核对链 ID、残基编号和插入码。",
            )
        if residue.record_types != {"ATOM"}:
            raise _validation_error(
                "MEEKO_RECEPTOR_TEMPLATE_NOT_POLYMER",
                "模板指定对象不是聚合物残基",
                f"{canonical}（{residue.residue_name}）不是单纯的 ATOM 聚合物记录。",
                "模板覆盖只用于经过审阅的聚合物残基；非聚合物对象应单独决定是否保留。",
            )

    for item in deleted_residues:
        canonical = item["selector"]
        selector = parse_flexible_residue(canonical)
        residue = available.get(selector.key)
        if residue is None:
            raise _validation_error(
                "MEEKO_RECEPTOR_CONTROL_SELECTOR_NOT_FOUND",
                "待删除受体对象不存在",
                f"原始结构中没有找到 deleted_residues 的 {canonical}。",
                "请核对链 ID、残基编号和插入码；DockStart 不会忽略未知删除项。",
            )
        if residue.residue_name != item["expected_component_id"]:
            raise _validation_error(
                "MEEKO_RECEPTOR_COMPONENT_MISMATCH",
                "待删除对象身份与合同不一致",
                f"{canonical} 的实际组分是 {residue.residue_name}，"
                f"合同预期为 {item['expected_component_id']}。",
                "请重新审阅原始结构；DockStart 不会删除身份不一致的对象。",
            )
        if residue.record_types != {"HETATM"}:
            raise _validation_error(
                "MEEKO_RECEPTOR_DELETE_NOT_NONPOLYMER",
                "拒绝删除聚合物残基",
                f"{canonical}（{residue.residue_name}）不是单纯的 HETATM 非聚合物记录。",
                "请修正删除合同；复杂受体控制不会删除 ATOM 聚合物残基。",
            )


def normalize_meeko_receptor_controls(
    controls: Mapping[str, Any],
    *,
    structure_path: str | Path | None = None,
    flexible_selections: Iterable[str] = (),
) -> dict[str, Any]:
    """Normalize a closed, reproducible ``mk_prepare_receptor`` control contract.

    No command fragments are accepted.  Every supported Meeko switch is
    derived from typed residue controls, and ``allow_bad_res`` is fixed to
    ``False`` so a complex receptor contract can never silently discard an
    unreviewed residue.
    """

    if not isinstance(controls, Mapping):
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_CONTROLS",
            "受体控制合同无效",
            "receptor_controls 必须是结构化对象。",
            "请传入 schema_version、alternate_locations、template_assignments 和 deleted_residues。",
        )
    allowed = {
        "schema_version",
        "allow_bad_res",
        "alternate_locations",
        "template_assignments",
        "deleted_residues",
    }
    unknown = sorted(str(key) for key in set(controls) - allowed)
    if unknown:
        raise _validation_error(
            "UNKNOWN_MEEKO_RECEPTOR_CONTROL",
            "受体控制合同含未知字段",
            f"未知字段：{', '.join(unknown)}。",
            "不接受 argv、extra_args 或其他自由命令行；请只使用已审计的结构化字段。",
        )
    schema_version = controls.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != MEEKO_RECEPTOR_CONTROLS_SCHEMA_VERSION
    ):
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_CONTROLS_SCHEMA",
            "受体控制合同版本无效",
            f"schema_version 必须是 {MEEKO_RECEPTOR_CONTROLS_SCHEMA_VERSION}。",
            "请迁移或重新生成受体控制合同，不要猜测未知版本语义。",
        )
    allow_bad_res = controls.get("allow_bad_res", False)
    if not isinstance(allow_bad_res, bool):
        raise _validation_error(
            "INVALID_MEEKO_RECEPTOR_ALLOW_BAD_RES",
            "allow_bad_res 类型无效",
            "allow_bad_res 必须是布尔值 false。",
            "复杂受体合同不允许使用字符串或数字代替布尔值。",
        )
    if allow_bad_res:
        raise _validation_error(
            "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
            "复杂受体合同禁止静默删除坏残基",
            "receptor_controls 明确要求 allow_bad_res=false。",
            "请修复无法匹配模板的残基，或通过显式 set_template/delete_residues 记录每项决定。",
        )

    alternate_locations = _normalize_control_selector_mapping(
        controls.get("alternate_locations", {}),
        field_name="alternate_locations",
        value_label="altloc",
        value_normalizer=_normalize_control_altloc,
    )
    template_assignments = _normalize_control_selector_mapping(
        controls.get("template_assignments", {}),
        field_name="template_assignments",
        value_label="Meeko 模板",
        value_normalizer=_normalize_control_template,
    )
    deleted_residues = _normalize_deleted_residue_controls(
        controls.get("deleted_residues", [])
    )
    normalized: dict[str, Any] = {
        "schema_version": MEEKO_RECEPTOR_CONTROLS_SCHEMA_VERSION,
        "allow_bad_res": False,
        "alternate_locations": alternate_locations,
        "template_assignments": template_assignments,
        "deleted_residues": deleted_residues,
    }
    if structure_path is not None:
        _validate_normalized_receptor_controls(
            normalized,
            structure_path=structure_path,
            flexible_selections=flexible_selections,
        )
    return normalized


def _legacy_receptor_controls(
    *,
    resolved_altlocs: Mapping[str, str] | None,
    allow_bad_res: bool,
) -> dict[str, Any]:
    choices = _normalize_altloc_choices(resolved_altlocs)
    alternate_locations = {
        FlexibleResidueSelector(*key).canonical: value
        for key, value in sorted(choices.items())
    }
    return {
        "schema_version": MEEKO_RECEPTOR_CONTROLS_SCHEMA_VERSION,
        "allow_bad_res": bool(allow_bad_res),
        "alternate_locations": alternate_locations,
        "template_assignments": {},
        "deleted_residues": [],
    }


def meeko_receptor_control_arguments(
    controls: Mapping[str, Any],
) -> list[str]:
    """Translate an already-normalized receptor control contract to argv.

    The caller must obtain ``controls`` from
    :func:`normalize_meeko_receptor_controls`.  Keeping this translation
    public lets rigid and flexible receptor preparation share the same closed
    schema instead of accepting free-form Meeko arguments.
    """

    arguments: list[str] = []
    alternate_locations = controls.get("alternate_locations", {})
    if alternate_locations:
        wanted_altloc = ",".join(
            f"{parse_flexible_residue(selector).meeko_id}={altloc}"
            for selector, altloc in alternate_locations.items()
        )
        arguments.extend(["--wanted_altloc", wanted_altloc])
    template_assignments = controls.get("template_assignments", {})
    if template_assignments:
        set_template = ",".join(
            f"{parse_flexible_residue(selector).meeko_id}={template}"
            for selector, template in template_assignments.items()
        )
        arguments.extend(["--set_template", set_template])
    deleted_residues = controls.get("deleted_residues", [])
    if deleted_residues:
        delete_residues = ",".join(
            parse_flexible_residue(item["selector"]).meeko_id
            for item in deleted_residues
        )
        arguments.extend(["--delete_residues", delete_residues])
    return arguments


def validate_flexible_residues(
    structure_path: str | Path,
    selections: Iterable[str],
    *,
    resolved_altlocs: Mapping[str, str] | None = None,
    max_residues: int = 8,
) -> dict[str, Any]:
    """Validate flexible sidechains against the original PDB/mmCIF.

    Water, HETATM/non-polymer records and unresolved alternate locations are
    rejected.  The function intentionally does not infer those facts from a
    PDBQT file because that format no longer contains sufficient provenance.
    """

    if isinstance(max_residues, bool) or not isinstance(max_residues, int) or max_residues < 1:
        raise _validation_error(
            "INVALID_FLEX_RESIDUE_LIMIT",
            "柔性残基上限无效",
            "柔性残基上限必须是大于 0 的整数。",
            "请使用较小的明确上限；默认值为 8。",
        )
    parsed: list[FlexibleResidueSelector] = []
    seen: set[tuple[str, int, str]] = set()
    for value in selections:
        selector = parse_flexible_residue(value)
        if selector.key not in seen:
            parsed.append(selector)
            seen.add(selector.key)
    if not parsed:
        raise _validation_error(
            "NO_FLEXIBLE_RESIDUES",
            "尚未选择柔性残基",
            "柔性侧链协议至少需要一个经过验证的残基。",
            "请在原始受体结构中选择少量口袋侧链。",
        )
    if len(parsed) > max_residues:
        raise _validation_error(
            "TOO_MANY_FLEXIBLE_RESIDUES",
            "柔性残基数量过多",
            f"已选择 {len(parsed)} 个残基，超过当前上限 {max_residues}。",
            "请只保留与研究问题直接相关的少量口袋侧链。",
        )

    path = Path(structure_path)
    source_format, available = _load_receptor_residues(path)
    altloc_choices = _normalize_altloc_choices(resolved_altlocs)
    reviewed: list[dict[str, Any]] = []
    wanted_altlocs: list[str] = []
    for selector in parsed:
        residue = available.get(selector.key)
        if residue is None:
            raise _validation_error(
                "FLEX_RESIDUE_NOT_FOUND",
                "原始结构中没有所选残基",
                f"原始结构中没有找到 {selector.canonical}。",
                "请核对链 ID、残基编号和插入码。",
            )
        if residue.residue_name in WATER_RESIDUES:
            raise _validation_error(
                "FLEX_RESIDUE_IS_WATER",
                "水分子不能作为柔性侧链",
                f"{selector.canonical} 是水分子 {residue.residue_name}。",
                "请选择蛋白质或核酸聚合物残基。",
            )
        if residue.record_types != {"ATOM"}:
            raise _validation_error(
                "FLEX_RESIDUE_NOT_POLYMER",
                "所选对象不是可确认的聚合物残基",
                f"{selector.canonical}（{residue.residue_name}）来自 "
                f"{', '.join(sorted(residue.record_types))} 记录。",
                "请从原始 PDB/mmCIF 的标准聚合物 ATOM 残基中选择侧链；不要选择配体、离子或辅因子。",
            )
        chosen_altloc = altloc_choices.get(selector.key, "")
        if residue.altlocs and not chosen_altloc:
            raise _validation_error(
                "UNRESOLVED_ALTERNATE_LOCATION",
                "柔性残基存在未决替代构象",
                f"{selector.canonical} 包含 altloc：{', '.join(sorted(residue.altlocs))}。",
                "请先明确选择一个替代构象，再生成柔性受体。",
                detail=json.dumps(
                    {
                        "selector": selector.canonical,
                        "altlocs": sorted(residue.altlocs),
                    },
                    ensure_ascii=False,
                ),
            )
        if chosen_altloc and chosen_altloc not in residue.altlocs:
            raise _validation_error(
                "ALTLOC_NOT_FOUND",
                "所选替代构象不存在",
                f"{selector.canonical} 不包含 altloc {chosen_altloc}。",
                f"可用 altloc：{', '.join(sorted(residue.altlocs)) or '无'}。",
            )
        if chosen_altloc:
            wanted_altlocs.append(f"{selector.meeko_id}={chosen_altloc}")
        reviewed.append(
            {
                "selector": selector.canonical,
                "meeko_id": selector.meeko_id,
                "chain_id": selector.chain,
                "residue_number": selector.residue_number,
                "insertion_code": selector.insertion_code,
                "residue_name": residue.residue_name,
                "atom_count": residue.atom_count,
                "altlocs": sorted(residue.altlocs),
                "selected_altloc": chosen_altloc,
                "source": "原始结构",
            }
        )
    return {
        "source_structure": str(path),
        "source_format": source_format,
        "residues": reviewed,
        "meeko_flexres": [item["meeko_id"] for item in reviewed],
        "wanted_altlocs": wanted_altlocs,
        "max_residues": max_residues,
    }


def _normalize_output_basename(value: str | Path) -> Path:
    path = Path(value)
    if path.suffix.lower() in {".pdbqt", ".json"}:
        path = path.with_suffix("")
    _validate_output_path(path, "受体准备输出")
    return path


def build_meeko_receptor_flex_plan(
    python_executable: str | Path,
    structure_path: str | Path,
    output_basename: str | Path,
    selections: Iterable[str],
    *,
    resolved_altlocs: Mapping[str, str] | None = None,
    receptor_controls: Mapping[str, Any] | None = None,
    max_residues: int = 8,
    allow_bad_res: bool = False,
    acknowledged_bad_residues: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a Meeko flexible-receptor command and its three required outputs."""

    python_path = _validate_python_executable(python_executable)
    source_path = Path(structure_path)
    selection_values = list(selections)
    structured_controls = receptor_controls is not None
    if structured_controls and resolved_altlocs:
        raise _validation_error(
            "MEEKO_RECEPTOR_ALTLOC_SOURCE_CONFLICT",
            "替代构象控制来源冲突",
            "使用 receptor_controls 时不能再同时传入 resolved_altlocs。",
            "请只保留 receptor_controls.alternate_locations 这一份可冻结的结构化决定。",
        )
    if structured_controls and allow_bad_res:
        raise _validation_error(
            "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
            "复杂受体合同禁止静默删除坏残基",
            "receptor_controls 存在时 allow_bad_res 必须保持 false。",
            "请修复坏残基，或使用显式 set_template/delete_residues 记录每项决定。",
        )
    if structured_controls and list(acknowledged_bad_residues or ()):
        raise _validation_error(
            "MEEKO_RECEPTOR_BAD_RES_ACKNOWLEDGEMENT_FORBIDDEN",
            "复杂受体合同不接受坏残基删除确认",
            "receptor_controls 不使用 acknowledged_bad_residues。",
            "请通过显式模板指定或显式删除合同处理每个受体对象。",
        )
    if structured_controls:
        normalized_controls = normalize_meeko_receptor_controls(
            receptor_controls,
            structure_path=source_path,
            flexible_selections=selection_values,
        )
        effective_altlocs: Mapping[str, str] | None = normalized_controls[
            "alternate_locations"
        ]
    else:
        normalized_controls = _legacy_receptor_controls(
            resolved_altlocs=resolved_altlocs,
            allow_bad_res=allow_bad_res,
        )
        effective_altlocs = resolved_altlocs
    validation = validate_flexible_residues(
        source_path,
        selection_values,
        resolved_altlocs=effective_altlocs,
        max_residues=max_residues,
    )
    acknowledged = _normalize_bad_residue_acknowledgements(
        acknowledged_bad_residues,
        allow_bad_res=allow_bad_res,
    )
    basename = _normalize_output_basename(output_basename)
    command = [
        python_path,
        "-I",
        "-B",
        "-m",
        "meeko.cli.mk_prepare_receptor",
    ]
    if validation["source_format"] != "pdb":
        raise _validation_error(
            "FLEX_MMCIF_VERIFIED_BRIDGE_REQUIRED",
            "mmCIF 必须先生成并验证 PDB 身份桥接",
            "Meeko 柔性受体准备只读取已经通过身份合同验证的 PDB，不会调用 ProDy 读取 mmCIF。",
            "请通过项目级柔性受体流程生成 Gemmi 桥接并完成作者/标签残基身份校验。",
        )
    command.extend(["--read_pdb", str(source_path)])
    command.extend(["--output_basename", str(basename), "--write_pdbqt", "--write_json"])
    if allow_bad_res:
        command.append("--allow_bad_res")
    for residue_id in validation["meeko_flexres"]:
        command.extend(["--flexres", residue_id])
    if structured_controls:
        command.extend(meeko_receptor_control_arguments(normalized_controls))
    elif validation["wanted_altlocs"]:
        command.extend(["--wanted_altloc", ",".join(validation["wanted_altlocs"])])

    outputs = {
        "rigid_pdbqt": str(basename) + "_rigid.pdbqt",
        "flex_pdbqt": str(basename) + "_flex.pdbqt",
        "receptor_json": str(basename) + ".json",
    }
    warnings = [
        "柔性侧链会显著扩大搜索空间；应使用少量、具有明确依据的口袋残基。",
        "执行后必须同时验证 rigid PDBQT、flex PDBQT 和 receptor JSON 三个输出。",
    ]
    return {
        "protocol": "flexible_sidechains",
        "argv": command,
        "selected_residues": validation["residues"],
        "outputs": outputs,
        "requires_prody": False,
        "receptor_controls_mode": (
            "structured_contract" if structured_controls else "legacy_compatibility"
        ),
        "receptor_controls": normalized_controls,
        "receptor_controls_sha256": _canonical_json_sha256(normalized_controls),
        "receptor_controls_fingerprint": {
            "algorithm": "sha256",
            "canonicalization": MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION,
            "sha256": _canonical_json_sha256(normalized_controls),
        },
        "scientific_review": {
            "allow_bad_res": allow_bad_res,
            "acknowledged_bad_residues": acknowledged,
            "receptor_controls_mode": (
                "structured_contract"
                if structured_controls
                else "legacy_compatibility"
            ),
        },
        "warnings": warnings,
    }


def build_vina_flex_arguments(flex_pdbqt: str | Path) -> list[str]:
    """Return the Vina argument fragment for a prepared flexible receptor."""

    path = Path(flex_pdbqt)
    _read_text_file(path, max_bytes=MAX_STRUCTURE_BYTES, label="柔性受体 PDBQT")
    if path.suffix.lower() != ".pdbqt":
        raise _validation_error(
            "INVALID_FLEX_PDBQT_SUFFIX",
            "柔性受体格式无效",
            "Vina --flex 输入必须是 PDBQT 文件。",
            "请选择 Meeko 生成的 *_flex.pdbqt。",
        )
    return ["--flex", str(path)]


def validate_macrocycle_options(options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate Meeko macrocycle preparation options without running Meeko."""

    raw = dict(options or {})
    allowed = {
        "mode",
        "min_ring_size",
        "double_bond_penalty",
        "allow_aromatic_breaks",
        "keep_chorded_rings",
        "keep_equivalent_rings",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _validation_error(
            "UNKNOWN_MACROCYCLE_OPTION",
            "存在未知大环参数",
            f"未知参数：{', '.join(unknown)}。",
            "请只使用 DockStart 已审计的大环参数。",
        )
    mode = str(raw.get("mode", "auto")).strip().lower()
    if mode not in {"auto", "rigid"}:
        raise _validation_error(
            "INVALID_MACROCYCLE_MODE",
            "大环模式无效",
            f"不支持的大环模式：{mode or '（空）'}。",
            "请选择 auto（允许 Meeko 断环）或 rigid（保持刚性）。",
        )

    min_ring_size = raw.get("min_ring_size", 7)
    if isinstance(min_ring_size, bool) or not isinstance(min_ring_size, int) or not 3 <= min_ring_size <= 33:
        raise _validation_error(
            "INVALID_MIN_RING_SIZE",
            "最小环尺寸无效",
            "min_ring_size 必须是 3 到 33 之间的整数。",
            "通常保持 Meeko 默认值 7；更改后应记录科学依据。",
        )
    double_bond_penalty = raw.get("double_bond_penalty", 50)
    if (
        isinstance(double_bond_penalty, bool)
        or not isinstance(double_bond_penalty, int)
        or not 0 <= double_bond_penalty <= 1000
    ):
        raise _validation_error(
            "INVALID_DOUBLE_BOND_PENALTY",
            "双键断裂惩罚无效",
            "double_bond_penalty 必须是 0 到 1000 之间的整数。",
            "Meeko 默认值为 50；大于 100 通常会阻止双键断裂。",
        )

    normalized: dict[str, Any] = {
        "mode": mode,
        "min_ring_size": min_ring_size,
        "double_bond_penalty": double_bond_penalty,
    }
    for key in ("allow_aromatic_breaks", "keep_chorded_rings", "keep_equivalent_rings"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise _validation_error(
                "INVALID_MACROCYCLE_BOOLEAN",
                "大环布尔参数无效",
                f"{key} 必须是 true 或 false。",
                "请不要使用字符串或数字代替布尔值。",
            )
        normalized[key] = value
    return normalized


def build_meeko_macrocycle_plan(
    python_executable: str | Path,
    ligand_path: str | Path,
    output_pdbqt: str | Path,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Meeko ligand command with explicit macrocycle policy."""

    python_path = _validate_python_executable(python_executable)
    input_path = Path(ligand_path)
    _read_text_file(input_path, max_bytes=MAX_STRUCTURE_BYTES, label="原始配体")
    output_path = _validate_output_path(Path(output_pdbqt), "配体 PDBQT 输出")
    if output_path.suffix.lower() != ".pdbqt":
        raise _validation_error(
            "INVALID_LIGAND_OUTPUT_SUFFIX",
            "配体输出格式无效",
            "Meeko 配体输出文件必须使用 .pdbqt 扩展名。",
            "请设置明确的 PDBQT 输出文件名。",
        )
    normalized = validate_macrocycle_options(options)
    command = [
        python_path,
        "-I",
        "-B",
        "-m",
        "meeko.cli.mk_prepare_ligand",
        "--mol",
        str(input_path),
        "--out",
        str(output_path),
    ]
    if normalized["mode"] == "rigid":
        command.append("--rigid_macrocycles")
    else:
        command.extend(["--min_ring_size", str(normalized["min_ring_size"])])
        command.extend(["--double_bond_penalty", str(normalized["double_bond_penalty"])])
        if normalized["allow_aromatic_breaks"]:
            command.append("--macrocycle_allow_A")
        if normalized["keep_chorded_rings"]:
            command.append("--keep_chorded_rings")
        if normalized["keep_equivalent_rings"]:
            command.append("--keep_equivalent_rings")

    warnings = ["大环准备输出仍需检查断环位置、G* 伪原子和导出后的闭环拓扑。"]
    if normalized["mode"] == "rigid":
        warnings.append("刚性大环不会搜索环构象；结果依赖输入构象。")
    if normalized["allow_aromatic_breaks"]:
        warnings.append("已允许在芳香型 A 原子处断环；该高级选项必须有明确结构依据。")
    return {
        "protocol": "macrocycle_preparation",
        "argv": command,
        "options": normalized,
        "input_ligand": str(input_path),
        "outputs": {"ligand_pdbqt": str(output_path)},
        "warnings": warnings,
    }


def _parse_pdbqt_atom(line: str) -> dict[str, Any] | None:
    fields = line.split()
    if len(fields) < 3:
        return None
    try:
        serial = int(line[6:11].strip())
    except (ValueError, IndexError):
        try:
            serial = int(fields[1])
        except (ValueError, IndexError):
            return None
    atom_name = line[12:16].strip() if len(line) >= 16 else fields[2]
    atom_type = fields[-1]
    coordinates: tuple[float, float, float] | None = None
    if len(line) >= 54:
        try:
            candidate = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
            if all(math.isfinite(value) for value in candidate):
                coordinates = candidate
        except ValueError:
            pass
    return {
        "serial": serial,
        "atom_name": atom_name,
        "atom_type": atom_type,
        "coordinates": list(coordinates) if coordinates is not None else None,
    }


def _metadata_ring_breaks(payload: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []

    def walk(value: Any, path: tuple[str, ...], depth: int) -> None:
        if depth > 24:
            return
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_text = str(key)
                lower_key = key_text.lower()
                child_path = path + (key_text,)
                if lower_key in {"bonds_removed", "bonds_to_break", "broken_bonds", "ring_breaks"}:
                    if isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                        for item in child:
                            if (
                                isinstance(item, Sequence)
                                and not isinstance(item, (str, bytes, bytearray))
                                and len(item) == 2
                            ):
                                try:
                                    pair = [int(item[0]), int(item[1])]
                                except (TypeError, ValueError):
                                    continue
                                evidence.append(
                                    {
                                        "atom_indices": pair,
                                        "source": "Meeko metadata",
                                        "metadata_path": ".".join(child_path),
                                        "index_semantics": "Meeko setup atom indices；是否为零基需结合对应 metadata schema 核对",
                                    }
                                )
                walk(child, child_path, depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                walk(child, path + (str(index),), depth + 1)

    walk(payload, (), 0)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for item in evidence:
        pair = item["atom_indices"]
        key = (pair[0], pair[1], item["metadata_path"])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def _load_optional_metadata(metadata: str | Path | Mapping[str, Any] | None) -> Any:
    if metadata is None:
        return None
    if isinstance(metadata, Mapping):
        return dict(metadata)
    path = Path(metadata)
    text = _read_text_file(path, max_bytes=MAX_METADATA_BYTES, label="Meeko metadata")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _validation_error(
            "INVALID_MEEKO_METADATA",
            "Meeko metadata 不是有效 JSON",
            f"无法解析 metadata：{path}",
            "请选择与该配体准备任务对应的完整 JSON metadata。",
            detail=str(exc),
        ) from exc


def inspect_meeko_ligand_pdbqt(
    pdbqt_path: str | Path,
    metadata: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect macrocycle evidence in a Meeko ligand PDBQT and metadata.

    Exact broken bonds are only reported when Meeko metadata records them.
    G*/CG* atom types in PDBQT are retained as evidence, not converted into a
    guessed original bond.
    """

    path = Path(pdbqt_path)
    text = _read_text_file(path, max_bytes=MAX_STRUCTURE_BYTES, label="Meeko 配体 PDBQT")
    if path.suffix.lower() != ".pdbqt":
        raise _validation_error(
            "INVALID_LIGAND_PDBQT_SUFFIX",
            "配体检查格式无效",
            "大环证据检查需要 .pdbqt 文件。",
            "请选择 Meeko 生成的 ligand PDBQT。",
        )

    atoms: dict[int, dict[str, Any]] = {}
    glue_atoms: list[dict[str, Any]] = []
    closure_anchors: list[dict[str, Any]] = []
    branches: list[list[int]] = []
    endbranch_count = 0
    torsdof: int | None = None
    smiles = ""
    smiles_index_pairs = 0
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith(("ATOM  ", "HETATM")):
            atom = _parse_pdbqt_atom(line)
            if atom is None:
                continue
            atoms[atom["serial"]] = atom
            if GLUE_TYPE_PATTERN.fullmatch(atom["atom_type"]):
                glue_atoms.append(atom)
            elif CLOSURE_ANCHOR_PATTERN.fullmatch(atom["atom_type"]):
                closure_anchors.append(atom)
        elif stripped.startswith("BRANCH "):
            fields = stripped.split()
            if len(fields) >= 3:
                try:
                    branches.append([int(fields[1]), int(fields[2])])
                except ValueError:
                    pass
        elif stripped.startswith("ENDBRANCH "):
            endbranch_count += 1
        elif stripped.startswith("TORSDOF "):
            fields = stripped.split()
            if len(fields) >= 2:
                try:
                    torsdof = int(fields[1])
                except ValueError:
                    torsdof = None
        elif line.startswith("REMARK SMILES ") and not line.startswith("REMARK SMILES IDX"):
            smiles = line[len("REMARK SMILES ") :].strip()
        elif line.startswith("REMARK SMILES IDX"):
            fields = line.split()[3:]
            smiles_index_pairs += len(fields) // 2

    metadata_payload = _load_optional_metadata(metadata)
    exact_breaks = _metadata_ring_breaks(metadata_payload) if metadata_payload is not None else []
    embedded_topology = bool(smiles and smiles_index_pairs > 0)
    macrocycle_evidence = bool(glue_atoms or closure_anchors or exact_breaks)
    warnings: list[str] = []
    if (glue_atoms or closure_anchors) and not exact_breaks:
        warnings.append("PDBQT 含 G*/CG* 闭环证据，但没有 metadata 可确认原始断环原子对；不得猜测具体键。")
    if macrocycle_evidence and not embedded_topology:
        warnings.append("缺少 REMARK SMILES/SMILES IDX；不能可靠将对接结果重建为原始闭环 SDF。")
    return {
        "pdbqt_file": str(path),
        "atom_count": len(atoms),
        "glue_pseudo_atoms": glue_atoms,
        "closure_anchor_atoms": closure_anchors,
        "branch_count": len(branches),
        "branches": branches,
        "endbranch_count": endbranch_count,
        "torsdof": torsdof,
        "embedded_topology": embedded_topology,
        "smiles": smiles,
        "smiles_index_pair_count": smiles_index_pairs,
        "macrocycle_evidence": macrocycle_evidence,
        "metadata_ring_breaks": exact_breaks,
        "warnings": warnings,
    }


def build_mk_export_plan(
    python_executable: str | Path,
    docking_result_pdbqt: str | Path,
    output_sdf: str | Path,
    *,
    receptor_json: str | Path | None = None,
    output_receptor_pdb: str | Path | None = None,
    keep_flexres_sdf: bool = False,
) -> dict[str, Any]:
    """Build a topology-preserving ``mk_export`` command.

    ``mk_export`` reconstructs ligand topology from the Meeko REMARK records
    embedded in PDBQT.  Without those records DockStart refuses to create a
    seemingly valid SDF from guessed bonds.
    """

    if not isinstance(keep_flexres_sdf, bool):
        raise _validation_error(
            "INVALID_KEEP_FLEXRES_FLAG",
            "柔性残基导出参数无效",
            "keep_flexres_sdf 必须是 true 或 false。",
            "请使用明确的布尔值。",
        )
    python_path = _validate_python_executable(python_executable)
    result_path = Path(docking_result_pdbqt)
    inspection = inspect_meeko_ligand_pdbqt(result_path)
    if not inspection["embedded_topology"]:
        raise _validation_error(
            "MISSING_ORIGINAL_TOPOLOGY",
            "不能安全导出 SDF",
            "对接结果中缺少 Meeko REMARK SMILES/SMILES IDX 原始拓扑映射。",
            "请使用由 Meeko 准备且保留 REMARK 的配体重新对接；DockStart 不会根据距离猜测键级或闭环。",
        )
    sdf_path = _validate_output_path(Path(output_sdf), "SDF 导出")
    if sdf_path.suffix.lower() != ".sdf":
        raise _validation_error(
            "INVALID_SDF_OUTPUT_SUFFIX",
            "SDF 输出格式无效",
            "mk_export 的配体结果输出必须使用 .sdf 扩展名。",
            "请设置明确的 SDF 输出文件名。",
        )

    command = [
        python_path,
        "-I",
        "-B",
        "-m",
        "meeko.cli.mk_export",
        str(result_path),
        "--write_sdf",
        str(sdf_path),
    ]
    outputs: dict[str, str] = {"ligand_sdf": str(sdf_path)}
    if output_receptor_pdb is not None and receptor_json is None:
        raise _validation_error(
            "RECEPTOR_JSON_REQUIRED",
            "导出柔性受体需要 receptor JSON",
            "指定受体 PDB 输出时必须同时提供 Meeko receptor JSON。",
            "请选择 mk_prepare_receptor --write_json 生成的对应 JSON。",
        )
    if receptor_json is not None:
        receptor_json_path = Path(receptor_json)
        _read_text_file(receptor_json_path, max_bytes=MAX_METADATA_BYTES, label="Meeko receptor JSON")
        command.extend(["--read_json", str(receptor_json_path)])
        if output_receptor_pdb is None:
            raise _validation_error(
                "RECEPTOR_OUTPUT_REQUIRED",
                "缺少受体 PDB 输出路径",
                "提供 receptor JSON 时必须明确指定更新后的受体 PDB 输出路径。",
                "请设置 output_receptor_pdb，避免 mk_export 写入不可追踪的默认位置。",
            )
        receptor_output = _validate_output_path(Path(output_receptor_pdb), "受体 PDB 导出")
        if receptor_output.suffix.lower() != ".pdb":
            raise _validation_error(
                "INVALID_RECEPTOR_PDB_SUFFIX",
                "受体导出格式无效",
                "更新后的柔性受体必须导出为 .pdb。",
                "请设置明确的 PDB 输出文件名。",
            )
        command.extend(["--write_pdb", str(receptor_output)])
        outputs["updated_receptor_pdb"] = str(receptor_output)
    if keep_flexres_sdf:
        command.append("--keep_flexres_sdf")
    return {
        "protocol": "meeko_result_export",
        "argv": command,
        "outputs": outputs,
        "topology_evidence": {
            "source": "对接结果 PDBQT 的 Meeko REMARK",
            "smiles": inspection["smiles"],
            "smiles_index_pair_count": inspection["smiles_index_pair_count"],
        },
        "warnings": inspection["warnings"],
    }


ProtocolRunner = Callable[..., Any]
_RECORD_FILENAMES = ("stdout.txt", "stderr.txt", "command_result.json")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _prepare_record_directory(value: str | Path) -> Path:
    text = str(value).strip()
    if not text:
        raise _validation_error(
            "RECORD_DIRECTORY_REQUIRED",
            "缺少执行记录目录",
            "执行高级协议时必须指定独立的记录目录。",
            "请为本次执行选择一个新的本地目录，用于保存命令、stdout、stderr 和退出码。",
        )
    path = Path(text).expanduser()
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise _validation_error(
                "UNSAFE_RECORD_DIRECTORY",
                "执行记录目录不安全",
                f"记录路径不是普通目录，或其本身是符号链接：{path}",
                "请选择普通的本地目录，不要使用文件或符号链接。",
            )
    else:
        parent = path.parent
        if not parent.exists() or not parent.is_dir() or parent.is_symlink():
            raise _validation_error(
                "RECORD_PARENT_NOT_FOUND",
                "执行记录目录的父目录不可用",
                f"无法安全创建记录目录：{path}",
                "请先创建普通的父目录，再为本次执行指定一个新的子目录。",
            )
        if not os.access(parent, os.W_OK):
            raise _validation_error(
                "RECORD_PARENT_NOT_WRITABLE",
                "执行记录目录不可写",
                f"当前进程不能写入目录：{parent}",
                "请更换记录目录或修复目录权限。",
            )
        try:
            path.mkdir()
        except OSError as exc:
            raise _validation_error(
                "RECORD_DIRECTORY_CREATE_FAILED",
                "无法创建执行记录目录",
                f"无法创建目录：{path}",
                "请检查目录权限后重试。",
                detail=str(exc),
            ) from exc
    path = path.resolve()
    occupied = [name for name in _RECORD_FILENAMES if (path / name).exists()]
    if occupied:
        raise _validation_error(
            "RECORD_FILES_ALREADY_EXIST",
            "执行记录目录已包含结果",
            f"以下记录文件已经存在：{', '.join(occupied)}。",
            "请为本次执行使用新的记录目录，避免覆盖既有实验记录。",
        )
    return path


def _validate_execution_cwd(value: str | Path | None, record_dir: Path) -> Path:
    path = record_dir if value is None else Path(value).expanduser()
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise _validation_error(
            "INVALID_EXECUTION_DIRECTORY",
            "命令工作目录不可用",
            f"命令工作目录不是普通的本地目录：{path}",
            "请使用存在且可访问的普通目录。",
        )
    return path.resolve()


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_execution_record(record_dir: Path, payload: Mapping[str, Any]) -> None:
    stdout_text = str(payload.get("stdout", ""))
    stderr_text = str(payload.get("stderr", ""))
    _atomic_write_text(record_dir / "stdout.txt", stdout_text)
    _atomic_write_text(record_dir / "stderr.txt", stderr_text)
    serialized = json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(record_dir / "command_result.json", serialized)


def _write_execution_record_checked(record_dir: Path, payload: Mapping[str, Any]) -> None:
    try:
        _write_execution_record(record_dir, payload)
    except OSError as exc:
        raise _validation_error(
            "EXECUTION_RECORD_WRITE_FAILED",
            "无法保存高级协议执行记录",
            f"无法完整写入记录目录：{record_dir}",
            "请检查磁盘空间和目录权限；DockStart 不会发布未记录的输出。",
            detail=str(exc),
        ) from exc


def _validate_generated_output(path: Path, key: str) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise _validation_error(
            "DECLARED_OUTPUT_MISSING",
            "高级协议输出不完整",
            f"命令成功结束，但没有生成声明的输出 {key}：{path}",
            "请检查 Meeko 版本、命令 stderr 和输入结构；DockStart 未发布任何半成品。",
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _validation_error(
            "DECLARED_OUTPUT_UNREADABLE",
            "无法读取高级协议输出",
            f"无法读取声明的输出 {key}：{path}",
            "请检查目录权限；DockStart 未发布任何半成品。",
            detail=str(exc),
        ) from exc
    if size <= 0:
        raise _validation_error(
            "DECLARED_OUTPUT_EMPTY",
            "高级协议输出为空",
            f"声明的输出 {key} 是空文件：{path}",
            "请查看 stderr 并重新检查输入结构；DockStart 未发布任何半成品。",
        )
    maximum = MAX_METADATA_BYTES if path.suffix.lower() == ".json" else MAX_STRUCTURE_BYTES
    if size > maximum:
        raise _validation_error(
            "DECLARED_OUTPUT_TOO_LARGE",
            "高级协议输出异常过大",
            f"声明的输出 {key} 为 {size} B，超过安全检查上限 {maximum} B：{path}",
            "请确认工具没有写入错误文件；DockStart 未发布任何半成品。",
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise _validation_error(
            "DECLARED_OUTPUT_UNREADABLE",
            "无法读取高级协议输出",
            f"无法读取声明的输出 {key}：{path}",
            "请检查目录权限；DockStart 未发布任何半成品。",
            detail=str(exc),
        ) from exc
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise _validation_error(
                "DECLARED_OUTPUT_INVALID_JSON",
                "Meeko JSON 输出无效",
                f"声明的 JSON 输出无法解析：{path}",
                "请检查 Meeko stderr；DockStart 未发布任何半成品。",
                detail=str(exc),
            ) from exc
    elif suffix in {".pdbqt", ".pdb"} and not any(
        line.startswith(("ATOM", "HETATM")) for line in text.splitlines()
    ):
        raise _validation_error(
            "DECLARED_STRUCTURE_OUTPUT_INVALID",
            "结构输出不包含原子记录",
            f"声明的输出 {key} 不包含 ATOM/HETATM 记录：{path}",
            "请检查 Meeko stderr 和输入结构；DockStart 未发布任何半成品。",
        )
    elif suffix == ".sdf" and "$$$$" not in text:
        raise _validation_error(
            "DECLARED_SDF_OUTPUT_INVALID",
            "SDF 输出不完整",
            f"声明的 SDF 输出缺少记录结束标记：{path}",
            "请检查 mk_export stderr；DockStart 未发布任何半成品。",
        )
    return {"path": str(path), "size_bytes": size}


def _partition_atom_key(
    chain: str,
    residue_number: int,
    insertion_code: str,
    residue_name: str,
    atom_name: str,
) -> tuple[str, int, str, str, str]:
    return (
        str(chain).strip(),
        int(residue_number),
        str(insertion_code).strip().upper(),
        str(residue_name).strip().upper(),
        str(atom_name).strip(),
    )


def _parse_flex_residue_boundary(
    line: str,
    *,
    keyword: str,
    line_number: int,
    label: str,
) -> tuple[str, FlexibleResidueSelector]:
    fields = line.strip().split()
    if not fields or fields[0] != keyword or len(fields) not in {3, 4}:
        raise _validation_error(
            "FLEX_PARTITION_BOUNDARY_INVALID",
            "柔性受体残基边界无效",
            f"{label} 第 {line_number} 行不是规范的 {keyword} 记录。",
            "请检查 Meeko 输出；DockStart 不会猜测柔性残基身份。",
            detail=line,
        )
    residue_name = fields[1].strip().upper()
    if MEEKO_CONTROL_TOKEN_PATTERN.fullmatch(residue_name) is None:
        raise _validation_error(
            "FLEX_PARTITION_BOUNDARY_INVALID",
            "柔性受体残基边界无效",
            f"{label} 第 {line_number} 行的残基名 {fields[1]!r} 无效。",
            "请检查 Meeko 输出；DockStart 不会猜测柔性残基身份。",
        )
    chain = fields[2] if len(fields) == 4 else ""
    residue_token = fields[3] if len(fields) == 4 else fields[2]
    residue_match = re.fullmatch(r"(?P<number>-?\d+)(?P<icode>[A-Za-z0-9]?)", residue_token)
    if residue_match is None:
        raise _validation_error(
            "FLEX_PARTITION_BOUNDARY_INVALID",
            "柔性受体残基边界无效",
            f"{label} 第 {line_number} 行的残基编号 {residue_token!r} 无效。",
            "请检查 Meeko 输出；插入码必须紧跟残基编号且最多一个字符。",
        )
    selector_text = (
        f"{chain}:{residue_match.group('number')}"
        f"{residue_match.group('icode')}"
    )
    try:
        selector = parse_flexible_residue(selector_text)
    except ProtocolValidationError as exc:
        raise _validation_error(
            "FLEX_PARTITION_BOUNDARY_INVALID",
            "柔性受体残基边界无效",
            f"{label} 第 {line_number} 行无法解析为安全的残基身份。",
            "请检查 Meeko 输出；DockStart 不会猜测柔性残基身份。",
            detail=exc.message,
        ) from exc
    return residue_name, selector


def _parse_partition_pdbqt(path: Path, label: str) -> dict[tuple[str, int, str, str, str], list[float]]:
    text = _read_text_file(path, max_bytes=MAX_STRUCTURE_BYTES, label=label)
    atoms: dict[tuple[str, int, str, str, str], list[float]] = {}
    active_boundary: tuple[str, FlexibleResidueSelector] | None = None
    active_boundary_atom_count = 0
    completed_boundaries: set[tuple[str, tuple[str, int, str]]] = set()
    saw_boundary = False
    atoms_outside_boundaries: list[int] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("BEGIN_RES"):
            boundary = _parse_flex_residue_boundary(
                stripped,
                keyword="BEGIN_RES",
                line_number=line_number,
                label=label,
            )
            if active_boundary is not None:
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_NESTED",
                    "柔性受体残基边界发生嵌套",
                    f"{label} 第 {line_number} 行在上一个 BEGIN_RES 尚未结束时再次开始残基。",
                    "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                )
            boundary_key = (boundary[0], boundary[1].key)
            if boundary_key in completed_boundaries:
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_DUPLICATE",
                    "柔性受体残基边界重复",
                    f"{label} 第 {line_number} 行重复开始 "
                    f"{boundary[0]} {boundary[1].canonical}。",
                    "每个柔性残基只能有一组 BEGIN_RES/END_RES 边界。",
                )
            active_boundary = boundary
            active_boundary_atom_count = 0
            saw_boundary = True
            continue
        if stripped.startswith("END_RES"):
            boundary = _parse_flex_residue_boundary(
                stripped,
                keyword="END_RES",
                line_number=line_number,
                label=label,
            )
            if active_boundary is None:
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_UNMATCHED",
                    "柔性受体残基结束边界没有起点",
                    f"{label} 第 {line_number} 行出现没有对应 BEGIN_RES 的 END_RES。",
                    "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                )
            if boundary != active_boundary:
                expected_name, expected_selector = active_boundary
                actual_name, actual_selector = boundary
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_MISMATCH",
                    "柔性受体残基边界身份不一致",
                    f"{label} 第 {line_number} 行结束的是 "
                    f"{actual_name} {actual_selector.canonical}，"
                    f"但当前开始边界是 {expected_name} {expected_selector.canonical}。",
                    "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                )
            if active_boundary_atom_count <= 0:
                expected_name, expected_selector = active_boundary
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_EMPTY",
                    "柔性受体残基边界为空",
                    f"{label} 的 {expected_name} {expected_selector.canonical} "
                    "在 BEGIN_RES/END_RES 之间没有原子。",
                    "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                )
            completed_boundaries.add(
                (active_boundary[0], active_boundary[1].key)
            )
            active_boundary = None
            active_boundary_atom_count = 0
            continue
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        if active_boundary is None:
            atoms_outside_boundaries.append(line_number)
        if len(line) < 54:
            raise _validation_error(
                "FLEX_PARTITION_PDBQT_TRUNCATED",
                "柔性受体原子划分无法验证",
                f"{label} 第 {line_number} 行的 PDBQT 原子字段不完整。",
                "请检查 Meeko 输出；DockStart 未发布本次三件套。",
            )
        try:
            residue_number = int(line[22:26].strip())
            coordinates = [
                float(line[30:38]),
                float(line[38:46]),
                float(line[46:54]),
            ]
        except ValueError as exc:
            raise _validation_error(
                "FLEX_PARTITION_PDBQT_NUMERIC_INVALID",
                "柔性受体原子划分无法验证",
                f"{label} 第 {line_number} 行含无效残基号或坐标。",
                "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                detail=str(exc),
            ) from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise _validation_error(
                "FLEX_PARTITION_PDBQT_NUMERIC_INVALID",
                "柔性受体原子划分无法验证",
                f"{label} 第 {line_number} 行含非有限坐标。",
                "请检查 Meeko 输出；DockStart 未发布本次三件套。",
            )
        chain = line[21:22].strip()
        insertion_code = line[26:27].strip().upper()
        residue_name = line[17:20].strip().upper()
        if active_boundary is not None:
            boundary_name, boundary_selector = active_boundary
            identity_conflicts: dict[str, Any] = {}
            if residue_name != boundary_name:
                identity_conflicts["residue_name"] = {
                    "atom": residue_name,
                    "boundary": boundary_name,
                }
            if chain != boundary_selector.chain:
                identity_conflicts["chain"] = {
                    "atom": chain,
                    "boundary": boundary_selector.chain,
                }
            if residue_number != boundary_selector.residue_number:
                identity_conflicts["residue_number"] = {
                    "atom": residue_number,
                    "boundary": boundary_selector.residue_number,
                }
            if (
                insertion_code
                and insertion_code != boundary_selector.insertion_code
            ):
                identity_conflicts["insertion_code"] = {
                    "atom": insertion_code,
                    "boundary": boundary_selector.insertion_code,
                }
            if identity_conflicts:
                raise _validation_error(
                    "FLEX_PARTITION_BOUNDARY_ATOM_CONFLICT",
                    "柔性受体原子身份与残基边界冲突",
                    f"{label} 第 {line_number} 行的 ATOM/HETATM 身份与当前 "
                    f"{boundary_name} {boundary_selector.canonical} 不一致。",
                    "请检查 Meeko 输出；DockStart 不会用边界覆盖冲突的原子身份。",
                    detail=json.dumps(identity_conflicts, ensure_ascii=False),
                )
            insertion_code = boundary_selector.insertion_code
            active_boundary_atom_count += 1
        key = _partition_atom_key(
            chain,
            residue_number,
            insertion_code,
            residue_name,
            line[12:16],
        )
        if key in atoms:
            raise _validation_error(
                "FLEX_PARTITION_OUTPUT_DUPLICATE_ATOM",
                "柔性受体输出含重复原子",
                f"{label} 中原子身份 {key!r} 出现多次。",
                "请检查 Meeko 输入与输出；DockStart 未发布本次三件套。",
            )
        atoms[key] = coordinates
    if active_boundary is not None:
        residue_name, selector = active_boundary
        raise _validation_error(
            "FLEX_PARTITION_BOUNDARY_UNTERMINATED",
            "柔性受体残基边界没有结束",
            f"{label} 的 {residue_name} {selector.canonical} 缺少 END_RES。",
            "请检查 Meeko 输出；DockStart 未发布本次三件套。",
        )
    if saw_boundary and atoms_outside_boundaries:
        raise _validation_error(
            "FLEX_PARTITION_ATOM_OUTSIDE_BOUNDARY",
            "柔性受体含边界外原子",
            f"{label} 使用 BEGIN_RES/END_RES，但仍有原子位于任何残基边界之外。",
            "请检查 Meeko 输出；DockStart 不会混合有边界和无边界的柔性原子。",
            detail=json.dumps(atoms_outside_boundaries[:20]),
        )
    if not atoms:
        raise _validation_error(
            "FLEX_PARTITION_OUTPUT_ATOMS_MISSING",
            "柔性受体输出没有可验证原子",
            f"{label} 中没有 ATOM/HETATM 原子。",
            "请检查 Meeko stderr；DockStart 未发布本次三件套。",
        )
    return atoms


def _parse_receptor_json_partition(
    path: Path,
) -> tuple[
    dict[tuple[str, int, str, str, str], list[float]],
    dict[tuple[str, int, str, str, str], list[float]],
    list[str],
]:
    text = _read_text_file(path, max_bytes=MAX_METADATA_BYTES, label="Meeko receptor JSON")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _validation_error(
            "FLEX_PARTITION_JSON_INVALID",
            "Meeko receptor JSON 无法用于原子划分",
            "receptor JSON 不是有效 JSON。",
            "请检查 Meeko stderr；DockStart 未发布本次三件套。",
            detail=str(exc),
        ) from exc
    monomers = payload.get("monomers") if isinstance(payload, Mapping) else None
    if not isinstance(monomers, Mapping) or not monomers:
        raise _validation_error(
            "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
            "Meeko receptor JSON 缺少原子划分证据",
            "receptor JSON 没有非空 monomers 映射，无法证明 rigid/flex 原子划分。",
            "请使用支持逐原子 is_flexres_atom 记录的 Meeko 版本。",
        )

    expected_rigid: dict[tuple[str, int, str, str, str], list[float]] = {}
    expected_flex: dict[tuple[str, int, str, str, str], list[float]] = {}
    excluded_monomers: list[str] = []
    for monomer_id, raw_monomer in monomers.items():
        if not isinstance(raw_monomer, Mapping):
            raise _validation_error(
                "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                "Meeko receptor JSON 缺少原子划分证据",
                f"monomer {monomer_id!r} 不是对象。",
                "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
            )
        setup = raw_monomer.get("molsetup")
        atoms = setup.get("atoms") if isinstance(setup, Mapping) else None
        flex_flags = raw_monomer.get("is_flexres_atom")
        if setup is None and flex_flags is None:
            excluded_monomers.append(str(monomer_id))
            continue
        if not isinstance(atoms, list) or not isinstance(flex_flags, list):
            raise _validation_error(
                "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                "Meeko receptor JSON 缺少原子划分证据",
                f"monomer {monomer_id!r} 缺少 molsetup.atoms 或 is_flexres_atom。",
                "请使用支持逐原子划分记录的 Meeko 版本。",
            )
        for raw_atom in atoms:
            if not isinstance(raw_atom, Mapping):
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子记录无效",
                    f"monomer {monomer_id!r} 含非对象原子记录。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            if raw_atom.get("is_ignore") is True:
                continue
            index = raw_atom.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(flex_flags)):
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子索引无效",
                    f"monomer {monomer_id!r} 含无法映射到 is_flexres_atom 的原子索引 {index!r}。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            if not isinstance(flex_flags[index], bool):
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子划分标记无效",
                    f"monomer {monomer_id!r} 的 is_flexres_atom[{index}] 不是布尔值。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            pdbinfo = raw_atom.get("pdbinfo")
            coordinates = raw_atom.get("coord")
            if (
                not isinstance(pdbinfo, list)
                or len(pdbinfo) < 5
                or not isinstance(coordinates, list)
                or len(coordinates) != 3
            ):
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子身份不完整",
                    f"monomer {monomer_id!r} 的原子 {index} 缺少 pdbinfo 或三维坐标。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            try:
                residue_number = int(pdbinfo[2])
                normalized_coordinates = [float(value) for value in coordinates]
            except (TypeError, ValueError) as exc:
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子身份无效",
                    f"monomer {monomer_id!r} 的原子 {index} 含无效残基号或坐标。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                    detail=str(exc),
                ) from exc
            if not all(math.isfinite(value) for value in normalized_coordinates):
                raise _validation_error(
                    "FLEX_PARTITION_JSON_SCHEMA_UNSUPPORTED",
                    "Meeko receptor JSON 原子坐标无效",
                    f"monomer {monomer_id!r} 的原子 {index} 含非有限坐标。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            key = _partition_atom_key(
                str(pdbinfo[4]),
                residue_number,
                str(pdbinfo[3]),
                str(pdbinfo[1]),
                str(pdbinfo[0]),
            )
            target = expected_flex if flex_flags[index] else expected_rigid
            other = expected_rigid if flex_flags[index] else expected_flex
            if key in target or key in other:
                raise _validation_error(
                    "FLEX_PARTITION_JSON_DUPLICATE_ATOM",
                    "Meeko receptor JSON 含重复原子身份",
                    f"receptor JSON 中原子身份 {key!r} 重复或同时属于 rigid/flex。",
                    "请检查 Meeko receptor JSON；DockStart 未发布本次三件套。",
                )
            target[key] = normalized_coordinates
    if not expected_rigid or not expected_flex:
        raise _validation_error(
            "FLEX_PARTITION_JSON_EMPTY_SIDE",
            "Meeko receptor JSON 的 rigid/flex 划分不完整",
            "receptor JSON 没有同时记录至少一个 rigid 原子和一个 flex 原子。",
            "请检查柔性残基选择和 Meeko 输出；DockStart 未发布本次三件套。",
        )
    return expected_rigid, expected_flex, sorted(excluded_monomers)


def validate_meeko_receptor_atom_partition(
    rigid_pdbqt: str | Path,
    flex_pdbqt: str | Path,
    receptor_json: str | Path,
    *,
    coordinate_tolerance: float = 0.0011,
) -> dict[str, Any]:
    """Prove that Meeko JSON partitions every published atom exactly once."""

    if coordinate_tolerance <= 0 or not math.isfinite(coordinate_tolerance):
        raise ValueError("coordinate_tolerance must be a positive finite number")
    rigid_path = Path(rigid_pdbqt)
    flex_path = Path(flex_pdbqt)
    json_path = Path(receptor_json)
    actual_rigid = _parse_partition_pdbqt(rigid_path, "rigid PDBQT")
    actual_flex = _parse_partition_pdbqt(flex_path, "flex PDBQT")
    expected_rigid, expected_flex, excluded_monomers = (
        _parse_receptor_json_partition(json_path)
    )

    overlap = sorted(set(actual_rigid) & set(actual_flex))
    if overlap:
        raise _validation_error(
            "FLEX_PARTITION_OUTPUT_OVERLAP",
            "rigid 与 flex PDBQT 含重复原子",
            "至少一个原子同时出现在 rigid 与 flex PDBQT。",
            "请检查 Meeko 输出；DockStart 未发布本次三件套。",
            detail=json.dumps(overlap[:20], ensure_ascii=False),
        )

    def compare(
        label: str,
        expected: Mapping[tuple[str, int, str, str, str], list[float]],
        actual: Mapping[tuple[str, int, str, str, str], list[float]],
    ) -> None:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing or extra:
            raise _validation_error(
                "FLEX_PARTITION_ATOM_SET_MISMATCH",
                "柔性受体原子划分与 receptor JSON 不一致",
                f"{label} 的原子集合存在缺失或多余记录。",
                "请检查 Meeko 版本与输出；DockStart 未发布本次三件套。",
                detail=json.dumps(
                    {"missing": missing[:20], "extra": extra[:20]},
                    ensure_ascii=False,
                ),
            )
        changed: list[dict[str, Any]] = []
        for key in sorted(expected):
            deltas = [
                abs(float(left) - float(right))
                for left, right in zip(expected[key], actual[key], strict=True)
            ]
            if any(delta > coordinate_tolerance for delta in deltas):
                changed.append({"atom": key, "deltas": deltas})
        if changed:
            raise _validation_error(
                "FLEX_PARTITION_COORDINATE_MISMATCH",
                "柔性受体原子坐标与 receptor JSON 不一致",
                f"{label} 至少一个原子的坐标超出 PDBQT 舍入误差。",
                "请检查 Meeko 输出；DockStart 未发布本次三件套。",
                detail=json.dumps(changed[:20], ensure_ascii=False),
            )

    compare("rigid PDBQT", expected_rigid, actual_rigid)
    compare("flex PDBQT", expected_flex, actual_flex)
    evidence = {
        "schema_version": 1,
        "authority": "receptor_json.monomers[*].is_flexres_atom",
        "rigid_atom_count": len(actual_rigid),
        "flex_atom_count": len(actual_flex),
        "total_atom_count": len(actual_rigid) + len(actual_flex),
        "overlap_atom_count": 0,
        "missing_atom_count": 0,
        "extra_atom_count": 0,
        "excluded_monomers": excluded_monomers,
        "coordinate_tolerance_angstrom": coordinate_tolerance,
    }
    evidence["partition_sha256"] = hashlib.sha256(
        json.dumps(
            {
                **evidence,
                "rigid_atoms": sorted(actual_rigid),
                "flex_atoms": sorted(actual_flex),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return evidence


def _cleanup_staged_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            if path.is_symlink() or (path.exists() and path.is_file()):
                path.unlink()
            elif path.exists() and path.is_dir():
                path.rmdir()
        except OSError:
            pass


def _validated_plan_receptor_controls(
    plan: Mapping[str, Any],
) -> tuple[dict[str, Any], str, dict[str, str]]:
    raw_controls = plan.get("receptor_controls")
    recorded_sha256 = str(plan.get("receptor_controls_sha256") or "").lower()
    raw_fingerprint = plan.get("receptor_controls_fingerprint")
    if not isinstance(raw_controls, Mapping):
        raise _validation_error(
            "INVALID_EXECUTION_PLAN_RECEPTOR_CONTROLS",
            "柔性受体执行计划缺少控制合同",
            "执行计划没有可冻结的 receptor_controls。",
            "请重新调用 DockStart 计划构造器，不要手工拼接 Meeko argv。",
        )
    controls = json.loads(
        json.dumps(raw_controls, ensure_ascii=False, sort_keys=True)
    )
    actual_sha256 = _canonical_json_sha256(controls)
    if recorded_sha256 != actual_sha256:
        raise _validation_error(
            "RECEPTOR_CONTROLS_FINGERPRINT_MISMATCH",
            "柔性受体控制合同指纹不一致",
            "执行计划中的 receptor_controls 与记录的 SHA256 不匹配。",
            "请丢弃被修改的计划并重新生成。",
        )
    if not isinstance(raw_fingerprint, Mapping):
        raise _validation_error(
            "INVALID_EXECUTION_PLAN_RECEPTOR_CONTROLS",
            "柔性受体执行计划缺少控制指纹",
            "执行计划没有结构化 receptor_controls_fingerprint。",
            "请重新调用 DockStart 计划构造器。",
        )
    fingerprint = {
        "algorithm": str(raw_fingerprint.get("algorithm") or ""),
        "canonicalization": str(raw_fingerprint.get("canonicalization") or ""),
        "sha256": str(raw_fingerprint.get("sha256") or "").lower(),
    }
    if fingerprint != {
        "algorithm": "sha256",
        "canonicalization": MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION,
        "sha256": actual_sha256,
    }:
        raise _validation_error(
            "RECEPTOR_CONTROLS_FINGERPRINT_MISMATCH",
            "柔性受体控制合同指纹描述不一致",
            "执行计划中的控制指纹算法、规范化规则或 SHA256 不匹配。",
            "请丢弃被修改的计划并重新生成。",
        )
    return controls, actual_sha256, fingerprint


def _execute_staged_plan(
    final_plan: Mapping[str, Any],
    staged_plan: Mapping[str, Any],
    *,
    record_dir: str | Path,
    runner: ProtocolRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Execute one plan transactionally and publish only a complete output set."""

    protocol = str(final_plan.get("protocol", "advanced_protocol"))
    final_raw = final_plan.get("outputs")
    staged_raw = staged_plan.get("outputs")
    if not isinstance(final_raw, Mapping) or not isinstance(staged_raw, Mapping):
        raise _validation_error(
            "INVALID_EXECUTION_PLAN",
            "高级协议执行计划无效",
            "执行计划没有声明结构化输出。",
            "请重新构建协议计划，不要手工修改 plan。",
        )
    if set(final_raw) != set(staged_raw):
        raise _validation_error(
            "STAGED_OUTPUT_MISMATCH",
            "暂存输出与最终输出不一致",
            "暂存计划没有覆盖全部声明输出。",
            "请重新构建协议计划；DockStart 未执行外部命令。",
        )
    final_outputs = {str(key): Path(str(value)).resolve() for key, value in final_raw.items()}
    staged_outputs = {str(key): Path(str(value)).resolve() for key, value in staged_raw.items()}
    if len(set(final_outputs.values())) != len(final_outputs):
        raise _validation_error(
            "DUPLICATE_OUTPUT_TARGET",
            "高级协议输出路径重复",
            "两个或更多声明输出指向同一个文件。",
            "请为每项输出设置独立路径。",
        )

    receptor_controls: dict[str, Any] | None = None
    receptor_controls_sha256 = ""
    receptor_controls_fingerprint: dict[str, str] | None = None
    receptor_controls_mode = ""
    if protocol == "flexible_sidechains":
        receptor_controls_mode = str(
            final_plan.get("receptor_controls_mode") or ""
        )
        staged_controls_mode = str(
            staged_plan.get("receptor_controls_mode") or ""
        )
        if (
            receptor_controls_mode
            not in {"structured_contract", "legacy_compatibility"}
            or staged_controls_mode != receptor_controls_mode
        ):
            raise _validation_error(
                "RECEPTOR_CONTROLS_MODE_MISMATCH",
                "柔性受体控制模式不一致",
                "最终计划与暂存计划没有冻结同一受体控制模式。",
                "请重新生成执行计划，不要手工修改 plan。",
            )
        (
            receptor_controls,
            receptor_controls_sha256,
            receptor_controls_fingerprint,
        ) = _validated_plan_receptor_controls(final_plan)
        (
            staged_controls,
            staged_controls_sha256,
            staged_controls_fingerprint,
        ) = _validated_plan_receptor_controls(staged_plan)
        if (
            staged_controls != receptor_controls
            or staged_controls_sha256 != receptor_controls_sha256
            or staged_controls_fingerprint != receptor_controls_fingerprint
        ):
            raise _validation_error(
                "STAGED_RECEPTOR_CONTROLS_MISMATCH",
                "暂存计划与最终受体控制合同不一致",
                "暂存计划没有使用与最终计划完全相同的规范化 receptor_controls。",
                "请重新生成执行计划；DockStart 未运行 Meeko。",
            )

    record_path = _prepare_record_directory(record_dir)
    execution_cwd = _validate_execution_cwd(cwd, record_path)
    argv = [str(value) for value in staged_plan.get("argv", [])]
    requested_argv = [str(value) for value in final_plan.get("argv", [])]
    started_at = _utc_timestamp()
    stdout_text = ""
    stderr_text = ""
    exit_code: int | None = None
    published: list[Path] = []
    output_validation: dict[str, dict[str, Any]] = {}

    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": protocol,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "cwd": str(execution_cwd),
        "command": argv,
        "requested_command": requested_argv,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "stdout_file": str(record_path / "stdout.txt"),
        "stderr_file": str(record_path / "stderr.txt"),
        "result_file": str(record_path / "command_result.json"),
        "declared_outputs": {key: str(path) for key, path in final_outputs.items()},
        "published_outputs": {},
        "output_validation": {},
        "atom_partition": None,
        "scientific_review": {},
        "receptor_controls": receptor_controls,
        "receptor_controls_sha256": receptor_controls_sha256,
        "receptor_controls_fingerprint": receptor_controls_fingerprint,
        "receptor_controls_mode": receptor_controls_mode,
        "error": None,
    }

    try:
        if not argv:
            raise _validation_error(
                "EMPTY_PROTOCOL_COMMAND",
                "高级协议命令为空",
                "执行计划没有可运行的参数数组。",
                "请重新构建协议计划，不要手工修改 plan。",
            )
        for key, final_path in final_outputs.items():
            if final_path.exists():
                raise _validation_error(
                    "OUTPUT_ALREADY_EXISTS",
                    "高级协议输出已经存在",
                    f"为避免覆盖既有结果，DockStart 拒绝写入 {key}：{final_path}",
                    "请选择新的输出名称，或由上层工作流明确归档旧结果。",
                )
            if not final_path.parent.exists() or not final_path.parent.is_dir():
                raise _validation_error(
                    "OUTPUT_DIRECTORY_NOT_FOUND",
                    "高级协议输出目录不存在",
                    f"输出目录不存在：{final_path.parent}",
                    "请先创建输出目录。",
                )
        for staged_path in staged_outputs.values():
            if staged_path.exists():
                raise _validation_error(
                    "STAGING_OUTPUT_COLLISION",
                    "高级协议暂存路径冲突",
                    f"暂存文件已经存在：{staged_path}",
                    "请重试；DockStart 会生成新的随机暂存名称。",
                )

        process_runner = subprocess.run if runner is None else runner
        try:
            completed = process_runner(
                argv,
                capture_output=True,
                text=True,
                cwd=str(execution_cwd),
                shell=False,
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary must be structured.
            raise _validation_error(
                "PROTOCOL_PROCESS_START_FAILED",
                "无法启动高级协议工具",
                "无法启动 Meeko 子进程。",
                "请检查 Assisted Python、Meeko 安装和目录权限。",
                detail=str(exc),
            ) from exc
        stdout_text = str(getattr(completed, "stdout", "") or "")
        stderr_text = str(getattr(completed, "stderr", "") or "")
        try:
            exit_code = int(getattr(completed, "returncode"))
        except (TypeError, ValueError) as exc:
            raise _validation_error(
                "INVALID_RUNNER_RESULT",
                "执行器返回结果无效",
                "执行器没有返回可解析的 exit code。",
                "请检查 runner adapter 的实现。",
                detail=str(exc),
            ) from exc
        combined_output = "\n".join(value for value in (stdout_text, stderr_text) if value)
        bad_residues = extract_meeko_bad_residues(combined_output)
        if protocol == "flexible_sidechains":
            plan_review = (
                staged_plan.get("scientific_review")
                if isinstance(staged_plan.get("scientific_review"), Mapping)
                else {}
            )
            allow_bad_res = bool(plan_review.get("allow_bad_res"))
            acknowledged = [
                str(value)
                for value in plan_review.get("acknowledged_bad_residues", [])
                if str(value)
            ]
            payload["scientific_review"] = {
                "allow_bad_res": allow_bad_res,
                "acknowledged_bad_residues": acknowledged,
                "detected_bad_residues": bad_residues,
                "receptor_controls_mode": receptor_controls_mode,
            }
            if exit_code != 0 and bad_residues:
                raise _validation_error(
                    "FLEX_BAD_RESIDUES_REVIEW_REQUIRED",
                    "受体包含 Meeko 无法匹配模板的残基",
                    f"严格模式检测到 {len(bad_residues)} 个不完整或无法匹配模板的残基。",
                    "请优先修复受体；如确认可以删除这些残基，请完整审阅列表后再显式启用 --allow_bad_res。",
                    detail=json.dumps(
                        {"bad_residues": bad_residues},
                        ensure_ascii=False,
                    ),
                )
            if exit_code == 0 and allow_bad_res:
                if set(bad_residues) != set(acknowledged):
                    raise _validation_error(
                        "FLEX_BAD_RESIDUES_CHANGED",
                        "Meeko 实际忽略的残基与确认清单不一致",
                        "受体诊断结果在确认后发生变化，DockStart 已拒绝发布输出。",
                        "请重新按严格模式检查并审阅最新残基列表。",
                        detail=json.dumps(
                            {
                                "acknowledged_bad_residues": acknowledged,
                                "bad_residues": bad_residues,
                            },
                            ensure_ascii=False,
                        ),
                    )
                selected_ids = {
                    str(item.get("meeko_id") or "")
                    for item in staged_plan.get("selected_residues", [])
                    if isinstance(item, Mapping)
                }
                ignored_selected = sorted(selected_ids & set(bad_residues))
                if ignored_selected:
                    raise _validation_error(
                        "FLEX_SELECTED_RESIDUE_WOULD_BE_IGNORED",
                        "所选柔性残基将被 Meeko 删除",
                        "允许坏残基后，至少一个所选柔性残基也会被忽略。",
                        "请先修复这些目标残基，不能把被删除的残基作为柔性侧链。",
                        detail=", ".join(ignored_selected),
                    )
        if exit_code != 0:
            raise _validation_error(
                "PROTOCOL_COMMAND_FAILED",
                "高级协议工具执行失败",
                f"Meeko 子进程退出码为 {exit_code}。",
                "请查看本次记录目录中的 stderr.txt；DockStart 未发布任何半成品。",
            )

        for key, staged_path in staged_outputs.items():
            output_validation[key] = _validate_generated_output(staged_path, key)
        atom_partition = None
        if protocol == "flexible_sidechains":
            atom_partition = validate_meeko_receptor_atom_partition(
                staged_outputs["rigid_pdbqt"],
                staged_outputs["flex_pdbqt"],
                staged_outputs["receptor_json"],
            )

        payload.update(
            {
                "status": "validated",
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output_validation": output_validation,
                "atom_partition": atom_partition,
            }
        )
        _write_execution_record_checked(record_path, payload)

        try:
            for key in final_outputs:
                os.replace(staged_outputs[key], final_outputs[key])
                published.append(final_outputs[key])
        except OSError as exc:
            for path in reversed(published):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            published.clear()
            raise _validation_error(
                "PROTOCOL_OUTPUT_PUBLISH_FAILED",
                "无法发布高级协议输出",
                "全部暂存输出已经验证，但原子发布失败。",
                "请检查输出目录权限；DockStart 已回滚本次新建文件。",
                detail=str(exc),
            ) from exc

        payload.update(
            {
                "status": "success",
                "finished_at": _utc_timestamp(),
                "published_outputs": {key: str(path) for key, path in final_outputs.items()},
                "output_validation": {
                    key: {**output_validation[key], "path": str(final_outputs[key])}
                    for key in final_outputs
                },
            }
        )
        _write_execution_record_checked(record_path, payload)
        return payload
    except ProtocolValidationError as exc:
        if published:
            for path in reversed(published):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            published.clear()
        payload.update(
            {
                "status": "failed",
                "finished_at": _utc_timestamp(),
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "published_outputs": {},
                "output_validation": output_validation,
                "error": exc.to_dict(),
            }
        )
        try:
            _write_execution_record(record_path, payload)
        except OSError:
            pass
        raise
    except Exception as exc:  # noqa: BLE001 - preserve a structured adapter boundary.
        if published:
            for path in reversed(published):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            published.clear()
        wrapped = _validation_error(
            "PROTOCOL_EXECUTION_ERROR",
            "高级协议执行发生未预期错误",
            "高级协议没有安全完成。",
            "请查看执行记录并检查本地工具链；DockStart 已回滚本次新建输出。",
            detail=str(exc),
        )
        payload.update(
            {
                "status": "failed",
                "finished_at": _utc_timestamp(),
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "published_outputs": {},
                "output_validation": output_validation,
                "error": wrapped.to_dict(),
            }
        )
        try:
            _write_execution_record(record_path, payload)
        except OSError:
            pass
        raise wrapped from exc
    finally:
        _cleanup_staged_outputs(staged_outputs.values())


def execute_meeko_receptor_flex(
    python_executable: str | Path,
    structure_path: str | Path,
    output_basename: str | Path,
    selections: Iterable[str],
    *,
    record_dir: str | Path,
    resolved_altlocs: Mapping[str, str] | None = None,
    receptor_controls: Mapping[str, Any] | None = None,
    max_residues: int = 8,
    allow_bad_res: bool = False,
    acknowledged_bad_residues: Iterable[str] | None = None,
    runner: ProtocolRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Safely prepare rigid/flexible receptor outputs and a Meeko JSON together."""

    python_path = Path(_validate_python_executable(python_executable)).resolve()
    source_path = Path(structure_path).resolve()
    final_basename = Path(output_basename).resolve()
    selection_values = list(selections)
    acknowledged_values = list(acknowledged_bad_residues or ())
    final_plan = build_meeko_receptor_flex_plan(
        python_path,
        source_path,
        final_basename,
        selection_values,
        resolved_altlocs=resolved_altlocs,
        receptor_controls=receptor_controls,
        max_residues=max_residues,
        allow_bad_res=allow_bad_res,
        acknowledged_bad_residues=acknowledged_values,
    )
    token = uuid.uuid4().hex
    staged_basename = final_basename.parent / f".dockstart-{token}-receptor"
    staged_plan = build_meeko_receptor_flex_plan(
        python_path,
        source_path,
        staged_basename,
        selection_values,
        resolved_altlocs=resolved_altlocs,
        receptor_controls=receptor_controls,
        max_residues=max_residues,
        allow_bad_res=allow_bad_res,
        acknowledged_bad_residues=acknowledged_values,
    )
    return _execute_staged_plan(
        final_plan,
        staged_plan,
        record_dir=record_dir,
        runner=runner,
        cwd=cwd,
    )


def execute_meeko_macrocycle(
    python_executable: str | Path,
    ligand_path: str | Path,
    output_pdbqt: str | Path,
    options: Mapping[str, Any] | None = None,
    *,
    record_dir: str | Path,
    runner: ProtocolRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Safely prepare a macrocycle PDBQT through a validated staging output."""

    python_path = Path(_validate_python_executable(python_executable)).resolve()
    input_path = Path(ligand_path).resolve()
    final_output = Path(output_pdbqt).resolve()
    final_plan = build_meeko_macrocycle_plan(python_path, input_path, final_output, options)
    staged_output = final_output.parent / f".dockstart-{uuid.uuid4().hex}-ligand.pdbqt"
    staged_plan = build_meeko_macrocycle_plan(python_path, input_path, staged_output, options)
    return _execute_staged_plan(
        final_plan,
        staged_plan,
        record_dir=record_dir,
        runner=runner,
        cwd=cwd,
    )


def execute_mk_export(
    python_executable: str | Path,
    docking_result_pdbqt: str | Path,
    output_sdf: str | Path,
    *,
    record_dir: str | Path,
    receptor_json: str | Path | None = None,
    output_receptor_pdb: str | Path | None = None,
    keep_flexres_sdf: bool = False,
    runner: ProtocolRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Safely export Meeko results without ever guessing missing topology."""

    python_path = Path(_validate_python_executable(python_executable)).resolve()
    result_path = Path(docking_result_pdbqt).resolve()
    final_sdf = Path(output_sdf).resolve()
    receptor_json_path = Path(receptor_json).resolve() if receptor_json is not None else None
    final_receptor = Path(output_receptor_pdb).resolve() if output_receptor_pdb is not None else None
    final_plan = build_mk_export_plan(
        python_path,
        result_path,
        final_sdf,
        receptor_json=receptor_json_path,
        output_receptor_pdb=final_receptor,
        keep_flexres_sdf=keep_flexres_sdf,
    )
    token = uuid.uuid4().hex
    staged_sdf = final_sdf.parent / f".dockstart-{token}-poses.sdf"
    staged_receptor = (
        final_receptor.parent / f".dockstart-{token}-receptor.pdb"
        if final_receptor is not None
        else None
    )
    staged_plan = build_mk_export_plan(
        python_path,
        result_path,
        staged_sdf,
        receptor_json=receptor_json_path,
        output_receptor_pdb=staged_receptor,
        keep_flexres_sdf=keep_flexres_sdf,
    )
    return _execute_staged_plan(
        final_plan,
        staged_plan,
        record_dir=record_dir,
        runner=runner,
        cwd=cwd,
    )


def _parse_altloc_cli(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise _validation_error(
                "INVALID_ALTLOC_ASSIGNMENT",
                "替代构象参数无效",
                f"无法解析：{value}",
                "请使用 chain:resnum[:icode]=altloc，例如 A:315=B。",
            )
        selector, altloc = value.rsplit("=", 1)
        result[selector] = altloc
    return result


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart 高级对接协议参数规划器")
    subparsers = parser.add_subparsers(dest="command", required=True)

    flex = subparsers.add_parser("flex-plan", help="验证柔性侧链并构造 Meeko 参数")
    flex.add_argument("--python", default=sys.executable)
    flex.add_argument("--structure", required=True)
    flex.add_argument("--output-basename", required=True)
    flex.add_argument("--residue", action="append", required=True)
    flex.add_argument("--resolved-altloc", action="append", default=[])
    flex.add_argument("--max-residues", type=int, default=8)
    flex.add_argument("--execute", action="store_true", help="执行并事务性发布全部声明输出")
    flex.add_argument("--record-dir", help="本次执行的独立记录目录")

    macrocycle = subparsers.add_parser("macrocycle-plan", help="构造 Meeko 大环配体准备参数")
    macrocycle.add_argument("--python", default=sys.executable)
    macrocycle.add_argument("--ligand", required=True)
    macrocycle.add_argument("--output-pdbqt", required=True)
    macrocycle.add_argument("--mode", choices=("auto", "rigid"), default="auto")
    macrocycle.add_argument("--min-ring-size", type=int, default=7)
    macrocycle.add_argument("--double-bond-penalty", type=int, default=50)
    macrocycle.add_argument("--allow-aromatic-breaks", action="store_true")
    macrocycle.add_argument("--keep-chorded-rings", action="store_true")
    macrocycle.add_argument("--keep-equivalent-rings", action="store_true")
    macrocycle.add_argument("--execute", action="store_true", help="执行并事务性发布 PDBQT")
    macrocycle.add_argument("--record-dir", help="本次执行的独立记录目录")

    inspect = subparsers.add_parser("inspect-ligand", help="读取配体 PDBQT 的大环证据")
    inspect.add_argument("--pdbqt", required=True)
    inspect.add_argument("--metadata")

    export = subparsers.add_parser("export-plan", help="构造不猜测拓扑的 mk_export 参数")
    export.add_argument("--python", default=sys.executable)
    export.add_argument("--result", required=True)
    export.add_argument("--output-sdf", required=True)
    export.add_argument("--receptor-json")
    export.add_argument("--output-receptor-pdb")
    export.add_argument("--keep-flexres-sdf", action="store_true")
    export.add_argument("--execute", action="store_true", help="执行并事务性发布全部声明输出")
    export.add_argument("--record-dir", help="本次执行的独立记录目录")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _cli_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "flex-plan":
            kwargs = {
                "resolved_altlocs": _parse_altloc_cli(args.resolved_altloc),
                "max_residues": args.max_residues,
            }
            if args.execute:
                payload = execute_meeko_receptor_flex(
                    args.python,
                    args.structure,
                    args.output_basename,
                    args.residue,
                    record_dir=args.record_dir or "",
                    **kwargs,
                )
            else:
                payload = build_meeko_receptor_flex_plan(
                    args.python,
                    args.structure,
                    args.output_basename,
                    args.residue,
                    **kwargs,
                )
        elif args.command == "macrocycle-plan":
            options = {
                "mode": args.mode,
                "min_ring_size": args.min_ring_size,
                "double_bond_penalty": args.double_bond_penalty,
                "allow_aromatic_breaks": args.allow_aromatic_breaks,
                "keep_chorded_rings": args.keep_chorded_rings,
                "keep_equivalent_rings": args.keep_equivalent_rings,
            }
            if args.execute:
                payload = execute_meeko_macrocycle(
                    args.python,
                    args.ligand,
                    args.output_pdbqt,
                    options,
                    record_dir=args.record_dir or "",
                )
            else:
                payload = build_meeko_macrocycle_plan(
                    args.python,
                    args.ligand,
                    args.output_pdbqt,
                    options,
                )
        elif args.command == "inspect-ligand":
            payload = inspect_meeko_ligand_pdbqt(args.pdbqt, args.metadata)
        else:
            export_builder = execute_mk_export if args.execute else build_mk_export_plan
            export_kwargs = {
                "receptor_json": args.receptor_json,
                "output_receptor_pdb": args.output_receptor_pdb,
                "keep_flexres_sdf": args.keep_flexres_sdf,
            }
            if args.execute:
                export_kwargs["record_dir"] = args.record_dir or ""
            payload = export_builder(
                args.python,
                args.result,
                args.output_sdf,
                **export_kwargs,
            )
        print(json.dumps({"ok": True, "result": payload}, ensure_ascii=False))
        return 0
    except ProtocolValidationError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must always return structured JSON.
        error = {
            "code": "ADVANCED_PROTOCOL_ERROR",
            "title": "高级协议处理失败",
            "message": "高级协议参数处理发生未预期错误。",
            "suggestion": "请保存当前输入并查看本地诊断日志。",
            "detail": str(exc),
        }
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
