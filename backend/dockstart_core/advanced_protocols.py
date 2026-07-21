"""Validated plans and transactional runners for advanced docking protocols.

Plan builders remain side-effect free.  The optional execution helpers run an
already reviewed Meeko command in isolated staging paths, validate every
declared output, and only then publish the complete output set.  This keeps the
scientific assumptions and the process boundary independently testable.
"""

from __future__ import annotations

import argparse
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
    max_residues: int = 8,
) -> dict[str, Any]:
    """Build a Meeko flexible-receptor command and its three required outputs."""

    python_path = _validate_python_executable(python_executable)
    source_path = Path(structure_path)
    validation = validate_flexible_residues(
        source_path,
        selections,
        resolved_altlocs=resolved_altlocs,
        max_residues=max_residues,
    )
    basename = _normalize_output_basename(output_basename)
    command = [
        python_path,
        "-I",
        "-B",
        "-m",
        "meeko.cli.mk_prepare_receptor",
    ]
    if validation["source_format"] == "pdb":
        command.extend(["--read_pdb", str(source_path)])
        requires_prody = False
    else:
        command.extend(["--read_with_prody", str(source_path)])
        requires_prody = True
    command.extend(["--output_basename", str(basename), "--write_pdbqt", "--write_json"])
    for residue_id in validation["meeko_flexres"]:
        command.extend(["--flexres", residue_id])
    if validation["wanted_altlocs"]:
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
    if requires_prody:
        warnings.append("Meeko 直接读取 mmCIF 需要可用的 ProDy；否则应先通过审计的转换流程生成 PDB。")
    return {
        "protocol": "flexible_sidechains",
        "argv": command,
        "selected_residues": validation["residues"],
        "outputs": outputs,
        "requires_prody": requires_prody,
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


def _cleanup_staged_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            if path.is_symlink() or (path.exists() and path.is_file()):
                path.unlink()
            elif path.exists() and path.is_dir():
                path.rmdir()
        except OSError:
            pass


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
        if exit_code != 0:
            raise _validation_error(
                "PROTOCOL_COMMAND_FAILED",
                "高级协议工具执行失败",
                f"Meeko 子进程退出码为 {exit_code}。",
                "请查看本次记录目录中的 stderr.txt；DockStart 未发布任何半成品。",
            )

        for key, staged_path in staged_outputs.items():
            output_validation[key] = _validate_generated_output(staged_path, key)

        payload.update(
            {
                "status": "validated",
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output_validation": output_validation,
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
    max_residues: int = 8,
    runner: ProtocolRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Safely prepare rigid/flexible receptor outputs and a Meeko JSON together."""

    python_path = Path(_validate_python_executable(python_executable)).resolve()
    source_path = Path(structure_path).resolve()
    final_basename = Path(output_basename).resolve()
    selection_values = list(selections)
    final_plan = build_meeko_receptor_flex_plan(
        python_path,
        source_path,
        final_basename,
        selection_values,
        resolved_altlocs=resolved_altlocs,
        max_residues=max_residues,
    )
    token = uuid.uuid4().hex
    staged_basename = final_basename.parent / f".dockstart-{token}-receptor"
    staged_plan = build_meeko_receptor_flex_plan(
        python_path,
        source_path,
        staged_basename,
        selection_values,
        resolved_altlocs=resolved_altlocs,
        max_residues=max_residues,
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
