"""Audited, review-first Meeko macrocycle preparation domain.

The normal ligand preparation path may decide that a molecule is macrocyclic,
but it must not silently choose a ring break on the user's behalf.  This
module therefore separates the workflow into four durable stages:

1. freeze the current raw ligand and analyse Meeko's complete candidate sets;
2. let the user confirm one candidate identifier (or an explicit rigid mode);
3. resolve a hash-bound preparation contract;
4. prepare only from a preparation-owned copy of the reviewed input.

Review and confirmation JSON files are immutable.  ``project.json`` contains
only pointers and hashes for the currently active records.  All public
project-level mutations are serialized with the ligand preparation lock and
recheck the live raw ligand before activating a record.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from dockstart_core.persistence import atomic_write_bytes, atomic_write_text
from dockstart_core.project import (
    _preparation_target_lock,
    _project_from_dict,
    load_project,
    save_project,
)

PROTOCOL_ID = "meeko_macrocycle"
STATE_KEY = "macrocycle_protocol"
STATE_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 2
CONFIRMATION_SCHEMA_VERSION = 2
CONTRACT_SCHEMA_VERSION = 2
ANALYSIS_VERSION = "2.0"
MEEKO_API_PROFILE = "meeko_0_7_explicit_ring_break_topology_v2"
HYDROGEN_POLICY = "rdkit_add_hs_preserve_source_indices_v1"
SUPPORTED_MEEKO_VERSIONS = {"0.7.1"}
BACKEND_ROOT = Path(__file__).resolve().parents[1]
ISOLATED_MODULE_RUNNER = (
    "import runpy,sys;"
    "sys.path.insert(0,sys.argv.pop(1));"
    "sys.argv[0]='dockstart_core.macrocycle';"
    "runpy.run_module('dockstart_core.macrocycle',run_name='__main__')"
)

REVIEW_ROOT = Path("preparation", "macrocycle_reviews")
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_CANDIDATE_SETS = 256
DEFAULT_MAX_RING_SIZE = 33
DEFAULT_MAX_BREAKS = 4
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEW_ID_RE = re.compile(r"^review_[0-9]{3,}$")
_CONFIRMATION_ID_RE = re.compile(r"^confirmation_[0-9]{3,}$")


class MacrocycleError(ValueError):
    """Stable domain error converted into DockStart's structured error shape."""

    def __init__(
        self,
        code: str,
        title: str,
        message: str,
        *,
        raw_error: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.title = title
        self.message = message
        self.raw_error = raw_error
        self.suggestion = suggestion


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _error_object(
    code: str,
    title: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, str]:
    return {
        "code": code,
        "title": title,
        "message": message,
        "raw_error": raw_error,
        "suggestion": suggestion,
    }


def _error(
    code: str,
    title: str,
    message: str,
    raw_error: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "project_dir": "",
        "error": _error_object(code, title, message, raw_error, suggestion),
    }


def _error_from_exception(exc: MacrocycleError) -> dict[str, Any]:
    return _error(
        exc.code,
        exc.title,
        exc.message,
        exc.raw_error,
        exc.suggestion,
    )


def _issue(
    code: str,
    title: str,
    message: str,
    *,
    raw_error: str = "",
    suggestion: str = "",
    blocking: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = _error_object(
        code,
        title,
        message,
        raw_error,
        suggestion,
    )
    payload["blocking"] = blocking
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_json_sha256(payload: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value.lower()))


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _assert_plain_path(path: Path, *, label: str) -> None:
    if path.is_symlink() or _is_reparse_point(path):
        raise MacrocycleError(
            "MACROCYCLE_PATH_UNSAFE",
            f"{label}路径不安全",
            f"{label}不能是符号链接或重解析点。",
            raw_error=str(path),
            suggestion="请恢复项目目录中的普通文件或文件夹后重试。",
        )


def _relative_project_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise MacrocycleError(
            "MACROCYCLE_PATH_OUTSIDE_PROJECT",
            "路径超出项目目录",
            "大环协议只能访问当前项目目录内的文件。",
            raw_error=str(path),
        ) from exc


def _project_member(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool,
    expect_file: bool = True,
) -> Path:
    text = str(relative_path or "").strip()
    relative = Path(text)
    if not text or relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise MacrocycleError(
            "MACROCYCLE_PROJECT_PATH_INVALID",
            "项目文件路径无效",
            "大环协议记录必须使用项目内相对路径。",
            raw_error=text,
        )
    root = root.resolve()
    candidate = root / relative
    resolved = candidate.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MacrocycleError(
            "MACROCYCLE_PATH_OUTSIDE_PROJECT",
            "项目文件路径越界",
            "大环协议记录指向了项目目录之外。",
            raw_error=text,
        ) from exc

    current = root
    for part in relative.parts:
        current = current / part
        if current.exists():
            _assert_plain_path(current, label="项目文件")
    if must_exist:
        if expect_file and not resolved.is_file():
            raise MacrocycleError(
                "MACROCYCLE_FILE_NOT_FOUND",
                "没有找到项目文件",
                "大环协议记录的文件不存在或不是普通文件。",
                raw_error=text,
            )
        if not expect_file and not resolved.is_dir():
            raise MacrocycleError(
                "MACROCYCLE_DIRECTORY_NOT_FOUND",
                "没有找到项目目录",
                "大环协议记录的目录不存在。",
                raw_error=text,
            )
    return resolved


def _ensure_project_directory(root: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise MacrocycleError(
            "MACROCYCLE_PROJECT_PATH_INVALID",
            "项目目录路径无效",
            "大环协议目录必须位于当前项目内。",
            raw_error=str(relative_path),
        )
    root = root.resolve()
    current = root
    for part in relative_path.parts:
        current = current / part
        if current.exists():
            _assert_plain_path(current, label="项目目录")
            if not current.is_dir():
                raise MacrocycleError(
                    "MACROCYCLE_DIRECTORY_CONFLICT",
                    "项目目录存在同名文件",
                    "无法创建大环协议记录目录。",
                    raw_error=str(current),
                )
        else:
            current.mkdir()
        try:
            current.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise MacrocycleError(
                "MACROCYCLE_PATH_OUTSIDE_PROJECT",
                "项目目录路径越界",
                "大环协议目录解析到了项目之外。",
                raw_error=str(current),
            ) from exc
    return current.resolve(strict=True)


def _read_stable_bytes(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    _assert_plain_path(path, label=label)
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read(maximum_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise MacrocycleError(
            "MACROCYCLE_FILE_READ_FAILED",
            f"无法读取{label}",
            f"{label}无法读取。",
            raw_error=str(exc),
        ) from exc
    if len(payload) > maximum_bytes:
        raise MacrocycleError(
            "MACROCYCLE_FILE_TOO_LARGE",
            f"{label}过大",
            f"{label}超过 {maximum_bytes} bytes 的安全限制。",
            raw_error=str(path),
        )
    if not payload:
        raise MacrocycleError(
            "MACROCYCLE_FILE_EMPTY",
            f"{label}为空",
            f"{label}没有可分析的内容。",
            raw_error=str(path),
        )
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_size != len(payload)
    ):
        raise MacrocycleError(
            "MACROCYCLE_FILE_CHANGED_DURING_READ",
            f"{label}正在变化",
            f"读取{label}时检测到文件内容发生变化，本次操作已停止。",
            raw_error=str(path),
            suggestion="请等待文件写入完成后重试。",
        )
    return payload


def _read_json_file(path: Path, *, maximum_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    payload = _read_stable_bytes(path, maximum_bytes=maximum_bytes, label="JSON 记录")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MacrocycleError(
            "MACROCYCLE_RECORD_JSON_INVALID",
            "大环协议记录无效",
            "大环协议 JSON 记录无法解析。",
            raw_error=str(exc),
        ) from exc
    if not isinstance(decoded, dict):
        raise MacrocycleError(
            "MACROCYCLE_RECORD_JSON_INVALID",
            "大环协议记录无效",
            "大环协议 JSON 顶层必须是对象。",
            raw_error=type(decoded).__name__,
        )
    return decoded


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        raise MacrocycleError(
            "MACROCYCLE_IMMUTABLE_RECORD_EXISTS",
            "不可变记录已存在",
            "DockStart 不会覆盖已有的大环审查记录。",
            raw_error=str(path),
        )
    atomic_write_bytes(path, payload)
    if _sha256(path) != _sha256_bytes(payload):
        raise MacrocycleError(
            "MACROCYCLE_RECORD_PUBLICATION_FAILED",
            "大环协议记录写入校验失败",
            "写入后的记录与待保存内容不一致。",
            raw_error=str(path),
        )


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    _write_immutable_bytes(path, encoded)
    return _sha256_bytes(encoded)


def _load_project_model(
    project_dir: str,
) -> tuple[Any | None, Path | None, dict[str, Any] | None]:
    loaded = load_project(project_dir)
    if not loaded.get("ok"):
        return None, None, loaded
    root = Path(project_dir).expanduser().resolve()
    try:
        return _project_from_dict(loaded["project"], root), root, None
    except Exception as exc:  # noqa: BLE001 - stable public error.
        return None, None, _error(
            "MACROCYCLE_PROJECT_INVALID",
            "项目数据无效",
            "project.json 无法用于大环协议。",
            str(exc),
            "请先修复或迁移项目文件。",
        )


def _state_from_project(project: Any) -> dict[str, Any]:
    raw = project.preserved_data.get(STATE_KEY)
    state = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    state["protocol_id"] = PROTOCOL_ID
    state["schema_version"] = STATE_SCHEMA_VERSION
    return state


def _store_state(project: Any, state: Mapping[str, Any]) -> None:
    payload = copy.deepcopy(dict(state))
    payload["protocol_id"] = PROTOCOL_ID
    payload["schema_version"] = STATE_SCHEMA_VERSION
    payload["updated_at"] = _now_iso()
    project.preserved_data[STATE_KEY] = payload


def _active_source(project: Any, root: Path) -> dict[str, Any]:
    relative_path = str(project.ligand.raw_file or "").strip()
    if not relative_path:
        raise MacrocycleError(
            "MACROCYCLE_LIGAND_RAW_NOT_SET",
            "尚未选择配体原始结构",
            "当前项目没有可用于大环审查的 ligand raw 文件。",
            suggestion="请先导入单分子 SDF 或 MOL 文件。",
        )
    path = _project_member(root, relative_path, must_exist=True)
    suffix = path.suffix.lower()
    if suffix not in {".sdf", ".mol"}:
        raise MacrocycleError(
            "MACROCYCLE_LIGAND_FORMAT_UNSUPPORTED",
            "配体格式不受支持",
            "正式大环审查当前仅支持单分子 SDF 或 MOL。",
            raw_error=suffix or "无扩展名",
            suggestion="请使用保留三维坐标的 SDF 或 MOL 文件。",
        )
    payload = _read_stable_bytes(path, maximum_bytes=MAX_SOURCE_BYTES, label="配体原始结构")
    return {
        "relative_path": Path(relative_path).as_posix(),
        "absolute_path": str(path),
        "suffix": suffix,
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "bytes": payload,
    }


def _same_source(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        str(left.get("relative_path") or "") == str(right.get("relative_path") or "")
        and int(left.get("size_bytes") or -1) == int(right.get("size_bytes") or -2)
        and str(left.get("sha256") or "").lower() == str(right.get("sha256") or "").lower()
    )


def _normalize_options(options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if options is None:
        raw: dict[str, Any] = {}
    elif isinstance(options, Mapping):
        raw = dict(options)
    else:
        raise MacrocycleError(
            "MACROCYCLE_OPTIONS_INVALID",
            "大环分析参数无效",
            "大环分析参数必须是 JSON 对象。",
            raw_error=type(options).__name__,
        )
    allowed = {
        "min_ring_size",
        "double_bond_penalty",
        "allow_atom_type_a_endpoints",
        "keep_chorded_rings",
        "keep_equivalent_rings",
        "max_breaks",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise MacrocycleError(
            "MACROCYCLE_OPTIONS_UNKNOWN",
            "存在未知大环参数",
            "大环分析参数包含当前版本不支持的字段。",
            raw_error=", ".join(unknown),
        )
    min_ring_size = raw.get("min_ring_size", 7)
    if (
        isinstance(min_ring_size, bool)
        or not isinstance(min_ring_size, int)
        or not 7 <= min_ring_size <= DEFAULT_MAX_RING_SIZE
    ):
        raise MacrocycleError(
            "MACROCYCLE_MIN_RING_SIZE_INVALID",
            "最小环尺寸无效",
            f"min_ring_size 必须是 7 到 {DEFAULT_MAX_RING_SIZE} 之间的整数。",
            raw_error=repr(min_ring_size),
        )
    penalty = raw.get("double_bond_penalty", 50)
    if isinstance(penalty, bool) or not isinstance(penalty, int) or penalty != 50:
        raise MacrocycleError(
            "MACROCYCLE_DOUBLE_BOND_PENALTY_UNSUPPORTED",
            "当前 Meeko 不支持调整双键惩罚",
            "Meeko 0.7.1 没有在大环断键评分中实际使用该参数；正式协议固定记录兼容值 50。",
            raw_error=repr(penalty),
            suggestion="请移除自定义 double_bond_penalty；待上游实现并完成回归后再开放。",
        )
    max_breaks = raw.get("max_breaks", DEFAULT_MAX_BREAKS)
    if (
        isinstance(max_breaks, bool)
        or not isinstance(max_breaks, int)
        or not 1 <= max_breaks <= DEFAULT_MAX_BREAKS
    ):
        raise MacrocycleError(
            "MACROCYCLE_MAX_BREAKS_INVALID",
            "最大断环数无效",
            f"max_breaks 必须是 1 到 {DEFAULT_MAX_BREAKS} 之间的整数。",
            raw_error=repr(max_breaks),
        )
    normalized: dict[str, Any] = {
        "min_ring_size": min_ring_size,
        "max_ring_size": DEFAULT_MAX_RING_SIZE,
        "double_bond_penalty": penalty,
        "max_breaks": max_breaks,
    }
    for key in (
        "keep_chorded_rings",
        "keep_equivalent_rings",
    ):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise MacrocycleError(
                "MACROCYCLE_BOOLEAN_OPTION_INVALID",
                "大环布尔参数无效",
                f"{key} 必须是 true 或 false。",
                raw_error=repr(value),
            )
        normalized[key] = value
    allow_a = raw.get("allow_atom_type_a_endpoints", False)
    if not isinstance(allow_a, bool):
        raise MacrocycleError(
            "MACROCYCLE_BOOLEAN_OPTION_INVALID",
            "大环布尔参数无效",
            "allow_atom_type_a_endpoints 必须是 true 或 false。",
            raw_error=repr(allow_a),
        )
    normalized["allow_atom_type_a_endpoints"] = allow_a
    if normalized["keep_chorded_rings"] and not normalized["keep_equivalent_rings"]:
        raise MacrocycleError(
            "MACROCYCLE_RING_POLICY_CONFLICT",
            "大环环系策略互相冲突",
            "Meeko 0.7.1 在保留弦环时会强制保留等价环；正式协议不记录表面上关闭、实际被忽略的设置。",
            suggestion="同时开启“保留弦环”和“保留等价环”，或关闭“保留弦环”。",
        )
    return normalized


def _toolkit() -> dict[str, Any]:
    try:
        import meeko
        import rdkit
        from meeko import MoleculePreparation, PDBQTWriterLegacy
        from meeko.atomtyper import AtomTyper
        from meeko.molsetup import RDKitMoleculeSetup
        from rdkit import Chem
    except Exception as exc:  # noqa: BLE001 - optional external toolkit.
        raise MacrocycleError(
            "MACROCYCLE_TOOLKIT_UNAVAILABLE",
            "大环分析工具不可用",
            "正式大环协议需要兼容的 RDKit 与 Meeko。",
            raw_error=str(exc),
            suggestion="请在工具链页面配置包含 RDKit 和 Meeko 0.7.x 的 Python。",
        ) from exc

    meeko_version = str(getattr(meeko, "__version__", "") or "")
    if meeko_version not in SUPPORTED_MEEKO_VERSIONS:
        raise MacrocycleError(
            "MACROCYCLE_MEEKO_VERSION_UNSUPPORTED",
            "Meeko 版本不兼容",
            "正式大环协议当前只支持经过真实回归的 Meeko 0.7.1。",
            raw_error=meeko_version or "未获取版本",
            suggestion="请使用 DockStart Assisted 随附的 Meeko 0.7.1；其他版本需先完成独立回归。",
        )
    return {
        "Chem": Chem,
        "MoleculePreparation": MoleculePreparation,
        "PDBQTWriterLegacy": PDBQTWriterLegacy,
        "AtomTyper": AtomTyper,
        "RDKitMoleculeSetup": RDKitMoleculeSetup,
        "rdkit_version": str(getattr(rdkit, "__version__", "") or ""),
        "meeko_version": meeko_version,
    }


def _load_single_molecule(
    path: Path,
    toolkit: Mapping[str, Any],
    *,
    source_bytes: bytes | None = None,
) -> Any:
    """Parse one molecule without passing a caller path into RDKit's C++ layer."""

    Chem = toolkit["Chem"]
    suffix = path.suffix.lower()
    payload = (
        source_bytes
        if source_bytes is not None
        else _read_stable_bytes(
            path,
            maximum_bytes=MAX_SOURCE_BYTES,
            label="配体输入",
        )
    )
    try:
        if suffix == ".sdf":
            supplier = Chem.ForwardSDMolSupplier(
                io.BytesIO(payload),
                removeHs=False,
                sanitize=True,
                strictParsing=True,
            )
            records = list(supplier)
            if len(records) != 1:
                raise MacrocycleError(
                    "MACROCYCLE_MULTIPLE_MOLECULES_UNSUPPORTED",
                    "SDF 必须只包含一个分子",
                    "大环审查不会从多分子 SDF 中静默选择第一条记录。",
                    raw_error=f"record_count={len(records)}",
                    suggestion="请把目标分子单独保存为一个 SDF 文件。",
                )
            molecule = records[0]
        elif suffix == ".mol":
            molecule = Chem.MolFromMolBlock(
                payload.decode("utf-8-sig", errors="replace"),
                removeHs=False,
                sanitize=True,
                strictParsing=True,
            )
        else:
            raise MacrocycleError(
                "MACROCYCLE_LIGAND_FORMAT_UNSUPPORTED",
                "配体格式不受支持",
                "正式大环协议当前仅支持单分子 SDF 或 MOL。",
                raw_error=suffix or "无扩展名",
            )
    except MacrocycleError:
        raise
    except Exception as exc:  # noqa: BLE001 - RDKit parsing errors vary by release.
        raise MacrocycleError(
            "MACROCYCLE_MOLECULE_PARSE_FAILED",
            "无法解析配体结构",
            "RDKit 无法读取或清理当前配体。",
            raw_error=str(exc),
            suggestion="请检查 SDF/MOL 的价态、键级和文件完整性。",
        ) from exc
    if molecule is None:
        raise MacrocycleError(
            "MACROCYCLE_MOLECULE_PARSE_FAILED",
            "无法解析配体结构",
            "RDKit 未能从当前文件获得有效分子。",
            raw_error=str(path),
            suggestion="请检查 SDF/MOL 的价态、键级和文件完整性。",
        )
    return molecule


def _prepare_explicit_hydrogens(
    molecule: Any,
    toolkit: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Apply one version-bound AddHs rule while preserving every source index."""

    Chem = toolkit["Chem"]
    source_atoms = list(molecule.GetAtoms())
    source_count = len(source_atoms)
    source_atomic_numbers = [int(atom.GetAtomicNum()) for atom in source_atoms]
    source_heavy_indices = [
        index
        for index, atomic_number in enumerate(source_atomic_numbers)
        if atomic_number > 1
    ]
    try:
        prepared = Chem.AddHs(molecule, addCoords=True)
        Chem.SanitizeMol(prepared)
    except Exception as exc:  # noqa: BLE001 - RDKit exceptions vary by release.
        raise MacrocycleError(
            "MACROCYCLE_EXPLICIT_HYDROGENS_FAILED",
            "无法补全显式氢",
            "RDKit 未能按正式大环协议补全配体显式氢。",
            raw_error=str(exc),
            suggestion="请检查配体价态、形式电荷和三维结构。",
        ) from exc

    prepared_count = int(prepared.GetNumAtoms())
    if prepared_count < source_count:
        raise MacrocycleError(
            "MACROCYCLE_SOURCE_ATOM_MAPPING_CHANGED",
            "配体原子映射发生变化",
            "显式加氢后的原子数量少于原始输入，无法保持原子索引。",
        )
    prepared_atomic_numbers = [
        int(prepared.GetAtomWithIdx(index).GetAtomicNum())
        for index in range(prepared_count)
    ]
    if (
        prepared_atomic_numbers[:source_count] != source_atomic_numbers
        or any(
            atomic_number != 1
            for atomic_number in prepared_atomic_numbers[source_count:]
        )
    ):
        raise MacrocycleError(
            "MACROCYCLE_SOURCE_ATOM_MAPPING_CHANGED",
            "配体原子映射发生变化",
            "RDKit 显式加氢没有保留原始原子顺序，正式审查已停止。",
        )

    return prepared, {
        "hydrogen_policy": HYDROGEN_POLICY,
        "source_atom_count": source_count,
        "prepared_atom_count": prepared_count,
        "source_heavy_atom_indices_zero_based": source_heavy_indices,
        "source_to_prepared_indices_zero_based": list(range(source_count)),
        "added_hydrogen_indices_zero_based": list(
            range(source_count, prepared_count)
        ),
        "source_indices_preserved": True,
    }


def _atom_name(atom: Any, index: int) -> str:
    if atom.HasProp("_TriposAtomName"):
        value = atom.GetProp("_TriposAtomName").strip()
        if value:
            return value
    info = atom.GetPDBResidueInfo()
    if info is not None:
        value = info.GetName().strip()
        if value:
            return value
    return f"{atom.GetSymbol()}{index + 1}"


def _coordinate_list(conformer: Any, index: int) -> list[float]:
    position = conformer.GetAtomPosition(index)
    values = [float(position.x), float(position.y), float(position.z)]
    if not all(math.isfinite(value) for value in values):
        raise MacrocycleError(
            "MACROCYCLE_COORDINATES_INVALID",
            "配体坐标无效",
            "大环审查检测到非有限三维坐标。",
            raw_error=f"atom_index={index}",
        )
    return [round(value, 6) for value in values]


def _atom_table(molecule: Any) -> tuple[list[dict[str, Any]], str, Any]:
    if molecule.GetNumConformers() != 1:
        raise MacrocycleError(
            "MACROCYCLE_CONFORMER_COUNT_UNSUPPORTED",
            "配体构象数量不受支持",
            "正式大环审查要求输入文件恰好包含一个三维构象。",
            raw_error=f"conformer_count={molecule.GetNumConformers()}",
            suggestion="请把待对接构象单独保存为 SDF 或 MOL。",
        )
    conformer = molecule.GetConformer()
    if not conformer.Is3D():
        raise MacrocycleError(
            "MACROCYCLE_3D_COORDINATES_REQUIRED",
            "配体缺少三维坐标",
            "大环断环与胶合伪原子需要明确的三维坐标。",
            suggestion="请先生成并检查目标配体的三维构象。",
        )
    table: list[dict[str, Any]] = []
    for index, atom in enumerate(molecule.GetAtoms()):
        table.append(
            {
                "index_zero_based": index,
                "number_one_based": index + 1,
                "name": _atom_name(atom, index),
                "element": atom.GetSymbol(),
                "atomic_number": int(atom.GetAtomicNum()),
                "formal_charge": int(atom.GetFormalCharge()),
                "is_aromatic": bool(atom.GetIsAromatic()),
                "chiral_tag": str(atom.GetChiralTag()),
                "coordinates": _coordinate_list(conformer, index),
            }
        )
    return table, _canonical_json_sha256(table), conformer


def _bond_pair(value: Sequence[int]) -> tuple[int, int]:
    if len(value) != 2:
        raise MacrocycleError(
            "MACROCYCLE_BOND_INVALID",
            "断环键记录无效",
            "每条断环键必须恰好包含两个原子索引。",
            raw_error=repr(value),
        )
    left, right = int(value[0]), int(value[1])
    return (left, right) if left < right else (right, left)


def _bond_topology(molecule: Any) -> tuple[list[dict[str, Any]], str]:
    """Return a canonical connectivity/order/stereo table for the prepared mol."""

    records: list[dict[str, Any]] = []
    for bond in molecule.GetBonds():
        left, right = _bond_pair(
            [int(bond.GetBeginAtomIdx()), int(bond.GetEndAtomIdx())]
        )
        records.append(
            {
                "atom_indices_zero_based": [left, right],
                "bond_type": str(bond.GetBondType()),
                "bond_order": float(bond.GetBondTypeAsDouble()),
                "is_aromatic": bool(bond.GetIsAromatic()),
                "is_conjugated": bool(bond.GetIsConjugated()),
                "stereo": str(bond.GetStereo()),
                "stereo_atom_indices_zero_based": sorted(
                    int(index) for index in bond.GetStereoAtoms()
                ),
            }
        )
    records.sort(
        key=lambda item: tuple(item["atom_indices_zero_based"])
    )
    return records, _canonical_json_sha256(records)


def _bond_detail(
    molecule: Any,
    atom_table: Sequence[Mapping[str, Any]],
    pair: Sequence[int],
) -> dict[str, Any]:
    left, right = _bond_pair(pair)
    bond = molecule.GetBondBetweenAtoms(left, right)
    if bond is None:
        raise MacrocycleError(
            "MACROCYCLE_BOND_NOT_FOUND",
            "断环键不存在",
            "候选断环键不属于当前配体。",
            raw_error=f"{left}-{right}",
        )
    endpoints = [copy.deepcopy(dict(atom_table[left])), copy.deepcopy(dict(atom_table[right]))]
    return {
        "atom_indices_zero_based": [left, right],
        "atom_numbers_one_based": [left + 1, right + 1],
        "atom_labels": [str(endpoints[0]["name"]), str(endpoints[1]["name"])],
        "endpoints": endpoints,
        "bond_type": str(bond.GetBondType()),
        "bond_order": float(bond.GetBondTypeAsDouble()),
        "is_aromatic": bool(bond.GetIsAromatic()),
        "stereo": str(bond.GetStereo()),
        "is_conjugated": bool(bond.GetIsConjugated()),
    }


def _meeko_preparator(
    toolkit: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    rigid: bool = False,
) -> Any:
    MoleculePreparation = toolkit["MoleculePreparation"]
    return MoleculePreparation(
        rigid_macrocycles=rigid,
        min_ring_size=int(options["min_ring_size"]),
        max_ring_size=int(options["max_ring_size"]),
        keep_chorded_rings=bool(options["keep_chorded_rings"]),
        keep_equivalent_rings=bool(options["keep_equivalent_rings"]),
        double_bond_penalty=int(options["double_bond_penalty"]),
        macrocycle_allow_A=bool(options["allow_atom_type_a_endpoints"]),
    )


def _candidate_setup(
    molecule: Any,
    toolkit: Mapping[str, Any],
    options: Mapping[str, Any],
) -> tuple[Any, Any, dict[str, Any], set[tuple[int, int]]]:
    preparator = _meeko_preparator(toolkit, options)
    setup_class = toolkit["RDKitMoleculeSetup"]
    setup = setup_class.from_mol(
        molecule,
        keep_chorded_rings=bool(options["keep_chorded_rings"]),
        keep_equivalent_rings=bool(options["keep_equivalent_rings"]),
        compute_gasteiger_charges=True,
    )
    toolkit["AtomTyper"].type_everything(
        setup,
        preparator.atom_params,
        preparator.charge_model,
        preparator.offatom_params,
        preparator.dihedral_params,
    )
    merge_indices = {
        atom.index
        for atom in setup.atoms
        if atom.atom_type in preparator.merge_these_atom_types
    }
    setup.merge_terminal_atoms(merge_indices)
    preparator._bond_typer(  # noqa: SLF001 - pinned, audited Meeko API profile.
        setup,
        preparator.flexible_amides,
        preparator.rigidify_bonds_smarts,
        preparator.rigidify_bonds_indices,
    )
    preparator._macrocycle_typer.max_breaks = int(options["max_breaks"])  # noqa: SLF001
    candidate_data, rigid_ring_bonds = preparator._macrocycle_typer.search_macrocycle(  # noqa: SLF001
        setup
    )
    return setup, preparator, candidate_data, set(rigid_ring_bonds)


def _ring_key(indices: Sequence[int]) -> tuple[int, ...]:
    """Return a rotation/direction independent key for one simple ring."""

    values = tuple(int(item) for item in indices)
    if not values:
        return ()
    rotations: list[tuple[int, ...]] = []
    for source in (values, tuple(reversed(values))):
        for offset in range(len(source)):
            rotations.append(source[offset:] + source[:offset])
    return min(rotations)


def _candidate_id(
    atom_table_sha256: str,
    bond_topology_sha256: str,
    options: Mapping[str, Any],
    exact_bonds: Sequence[Sequence[int]],
) -> str:
    payload = {
        "protocol_id": PROTOCOL_ID,
        "analysis_version": ANALYSIS_VERSION,
        "atom_table_sha256": atom_table_sha256,
        "bond_topology_sha256": bond_topology_sha256,
        "options": copy.deepcopy(dict(options)),
        "exact_bonds": [list(_bond_pair(pair)) for pair in exact_bonds],
    }
    return f"candidate_{_canonical_json_sha256(payload)[:16]}"


def _analysis_payload(review_like: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in review_like.items()
        if key
        not in {
            "review_id",
            "created_at",
            "source",
            "input_snapshot",
            "analysis_sha256",
        }
    }


def analyze_macrocycle_file(
    input_path: str | Path,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyse one SDF/MOL and return deterministic Meeko candidate combinations.

    This pure worker neither reads nor writes ``project.json``.  Atom indices
    are always persisted in both Meeko/RDKit zero-based form and UI one-based
    form so that later confirmation cannot depend on display conversion.
    """

    path = Path(input_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise MacrocycleError(
            "MACROCYCLE_INPUT_NOT_FOUND",
            "没有找到配体输入文件",
            "大环分析输入不存在或不是普通文件。",
            raw_error=str(path),
        )
    _assert_plain_path(path, label="配体输入")
    if path.stat().st_size <= 0 or path.stat().st_size > MAX_SOURCE_BYTES:
        raise MacrocycleError(
            "MACROCYCLE_INPUT_SIZE_INVALID",
            "配体输入大小无效",
            "配体输入为空或超过大环分析安全限制。",
            raw_error=f"{path.stat().st_size} bytes",
        )
    normalized = _normalize_options(options)
    toolkit = _toolkit()
    source_bytes = _read_stable_bytes(
        path,
        maximum_bytes=MAX_SOURCE_BYTES,
        label="配体输入",
    )
    molecule = _load_single_molecule(
        path,
        toolkit,
        source_bytes=source_bytes,
    )
    molecule, atom_indexing = _prepare_explicit_hydrogens(
        molecule,
        toolkit,
    )
    atom_table, atom_table_sha256, _ = _atom_table(molecule)
    bond_topology, bond_topology_sha256 = _bond_topology(molecule)

    try:
        setup, preparator, candidate_data, _ = _candidate_setup(
            molecule,
            toolkit,
            normalized,
        )
    except MacrocycleError:
        raise
    except Exception as exc:  # noqa: BLE001 - Meeko internal failure is evidence.
        raise MacrocycleError(
            "MACROCYCLE_CANDIDATE_ANALYSIS_FAILED",
            "大环候选分析失败",
            "Meeko 未能生成可审查的断环候选。",
            raw_error=str(exc),
            suggestion="请检查配体价态、显式氢、键级和三维坐标。",
        ) from exc

    breakable_keys = {
        _ring_key(ring)
        for ring in (preparator._macrocycle_typer.breakable_rings or [])  # noqa: SLF001
    }
    ring_records: list[dict[str, Any]] = []
    ring_id_by_key: dict[tuple[int, ...], str] = {}
    for ring_index, ring in enumerate(sorted(set(setup.rings), key=_ring_key), start=1):
        key = _ring_key(ring)
        ring_id = f"ring_{ring_index:03d}"
        ring_id_by_key[key] = ring_id
        aromatic = all(
            bool(molecule.GetAtomWithIdx(index).GetIsAromatic())
            for index in key
        )
        ring_records.append(
            {
                "ring_id": ring_id,
                "size": len(key),
                "atom_indices_zero_based": list(key),
                "atom_numbers_one_based": [index + 1 for index in key],
                "is_aromatic": aromatic,
                "meeko_breakable_size": key in breakable_keys,
                "over_supported_size": len(key) > int(normalized["max_ring_size"]),
            }
        )

    macrocycle_rings = [
        item for item in ring_records if int(item["size"]) >= int(normalized["min_ring_size"])
    ]
    oversized = [item for item in macrocycle_rings if item["over_supported_size"]]
    is_macrocycle = bool(macrocycle_rings)

    raw_combos = list(candidate_data.get("bond_break_combos") or [])
    raw_scores = list(candidate_data.get("bond_break_scores") or [])
    raw_unbroken = list(candidate_data.get("unbroken_rings") or [])
    combo_rows: list[tuple[tuple[tuple[int, int], ...], float, list[Any]]] = []
    for index, raw_combo in enumerate(raw_combos):
        pairs = tuple(sorted({_bond_pair(pair) for pair in raw_combo}))
        score = float(raw_scores[index]) if index < len(raw_scores) else 0.0
        unbroken = list(raw_unbroken[index]) if index < len(raw_unbroken) else []
        combo_rows.append((pairs, score, unbroken))
    combo_rows.sort(key=lambda row: (row[0], -row[1]))

    too_many_candidates = len(combo_rows) > MAX_CANDIDATE_SETS
    visible_rows = combo_rows[:MAX_CANDIDATE_SETS]
    candidate_sets: list[dict[str, Any]] = []
    for pairs, score, unbroken in visible_rows:
        candidate_id = _candidate_id(
            atom_table_sha256,
            bond_topology_sha256,
            normalized,
            pairs,
        )
        unbroken_ids = [
            ring_id_by_key[key]
            for key in sorted({_ring_key(ring) for ring in unbroken})
            if key in ring_id_by_key
        ]
        candidate_sets.append(
            {
                "candidate_id": candidate_id,
                "exact_bonds": [list(pair) for pair in pairs],
                "bonds": [
                    _bond_detail(molecule, atom_table, pair)
                    for pair in pairs
                ],
                "bond_score": score,
                "unbroken_ring_ids": unbroken_ids,
                "ring_coverage_complete": not unbroken_ids,
            }
        )

    recommended_pairs: list[list[int]] = []
    recommended_error = ""
    if is_macrocycle and candidate_sets and not oversized:
        try:
            automatic = _meeko_preparator(toolkit, normalized)
            automatic._macrocycle_typer.max_breaks = int(normalized["max_breaks"])  # noqa: SLF001
            automatic_setups = automatic.prepare(molecule)
            if len(automatic_setups) != 1:
                raise RuntimeError(f"setup_count={len(automatic_setups)}")
            recommended_pairs = [
                list(pair)
                for pair in sorted(
                    {
                        _bond_pair(pair)
                        for pair in automatic_setups[0].ring_closure_info.bonds_removed
                    }
                )
            ]
        except Exception as exc:  # noqa: BLE001 - recorded as unsupported reason.
            recommended_error = str(exc)

    recommended_candidate_id = ""
    if recommended_pairs:
        expected_id = _candidate_id(
            atom_table_sha256,
            bond_topology_sha256,
            normalized,
            recommended_pairs,
        )
        if any(item["candidate_id"] == expected_id for item in candidate_sets):
            recommended_candidate_id = expected_id
        else:
            recommended_error = (
                "Meeko 自动选择未出现在冻结的候选集合中："
                f"{recommended_pairs}"
            )

    unsupported_reasons: list[dict[str, Any]] = []
    if oversized:
        unsupported_reasons.append(
            _issue(
                "MACROCYCLE_RING_TOO_LARGE",
                "大环尺寸超出当前支持范围",
                f"当前 Meeko 协议最多支持 {normalized['max_ring_size']} 元环的柔性断环。",
                raw_error=", ".join(
                    f"{item['ring_id']}={item['size']}" for item in oversized
                ),
                suggestion="可以明确选择刚性大环，或使用经过独立验证的外部协议。",
            )
        )
    if is_macrocycle and not candidate_sets:
        unsupported_reasons.append(
            _issue(
                "MACROCYCLE_NO_BREAKABLE_CANDIDATE",
                "没有可用断环候选",
                "Meeko 没有找到满足当前原子类型与成环约束的断环组合。",
                suggestion="可以明确选择刚性大环，或核对键级与配体化学结构。",
            )
        )
    if too_many_candidates:
        unsupported_reasons.append(
            _issue(
                "MACROCYCLE_CANDIDATE_SPACE_TOO_LARGE",
                "断环候选数量过多",
                "候选组合超过当前审查记录的安全上限，未开放柔性准备。",
                raw_error=f"{len(combo_rows)} > {MAX_CANDIDATE_SETS}",
                suggestion="请收紧大环分析参数或使用经过验证的外部准备流程。",
            )
        )
    if is_macrocycle and candidate_sets and not recommended_candidate_id:
        unsupported_reasons.append(
            _issue(
                "MACROCYCLE_RECOMMENDATION_UNAVAILABLE",
                "无法确定 Meeko 默认候选",
                "候选列表已生成，但无法把 Meeko 自动选择稳定映射到其中。",
                raw_error=recommended_error,
                suggestion="当前记录不能进入柔性大环准备；可以选择刚性大环。",
            )
        )

    result: dict[str, Any] = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": REVIEW_SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "meeko_api_profile": MEEKO_API_PROFILE,
        "options": normalized,
        "tool_versions": {
            "python": sys.version.split()[0],
            "rdkit": toolkit["rdkit_version"],
            "meeko": toolkit["meeko_version"],
        },
        "hydrogen_policy": HYDROGEN_POLICY,
        "atom_indexing": atom_indexing,
        "molecule": {
            "name": (
                molecule.GetProp("_Name").strip()
                if molecule.HasProp("_Name")
                else path.stem
            ),
            "source_atom_count": int(
                atom_indexing["source_atom_count"]
            ),
            "atom_count": int(molecule.GetNumAtoms()),
            "bond_count": int(molecule.GetNumBonds()),
            "conformer_count": int(molecule.GetNumConformers()),
            "record_count": 1,
            "has_3d_coordinates": True,
        },
        "atom_table_sha256": atom_table_sha256,
        "atoms": atom_table,
        "bond_topology_sha256": bond_topology_sha256,
        "bond_topology": bond_topology,
        "rings": ring_records,
        "macrocycle_ring_ids": [item["ring_id"] for item in macrocycle_rings],
        "is_macrocycle": is_macrocycle,
        "candidate_sets": candidate_sets,
        "candidate_count_total": len(combo_rows),
        "recommended_candidate_id": recommended_candidate_id,
        "flexible_supported": (
            is_macrocycle
            and bool(candidate_sets)
            and bool(recommended_candidate_id)
            and not unsupported_reasons
        ),
        "rigid_supported": is_macrocycle,
        "unsupported_reasons": unsupported_reasons,
        "parameter_notes": [
            "double_bond_penalty=50 仅作为 Meeko 0.7.1 兼容记录；该版本未在候选断键评分中实际使用此值。"
        ],
    }
    result["analysis_sha256"] = _canonical_json_sha256(_analysis_payload(result))
    return result


def _next_review_id(root: Path) -> str:
    review_root = _ensure_project_directory(root, REVIEW_ROOT)
    highest = 0
    for child in review_root.iterdir():
        if not child.is_dir() or child.is_symlink() or _is_reparse_point(child):
            continue
        match = _REVIEW_ID_RE.fullmatch(child.name)
        if match:
            highest = max(highest, int(child.name.rsplit("_", 1)[1]))
    return f"review_{highest + 1:03d}"


def _next_confirmation_id(review_dir: Path) -> str:
    highest = 0
    for child in review_dir.iterdir():
        if not child.is_file() or child.is_symlink() or _is_reparse_point(child):
            continue
        match = re.fullmatch(r"confirmation_([0-9]{3,})\.json", child.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"confirmation_{highest + 1:03d}"


def _review_pointer(
    review: Mapping[str, Any],
    *,
    relative_path: str,
    file_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    return {
        "review_id": str(review["review_id"]),
        "relative_path": Path(relative_path).as_posix(),
        "sha256": file_sha256,
        "size_bytes": int(size_bytes),
        "analysis_sha256": str(review["analysis_sha256"]),
        "source_sha256": str((review.get("source") or {}).get("sha256") or ""),
        "created_at": str(review["created_at"]),
    }


def _confirmation_pointer(
    confirmation: Mapping[str, Any],
    *,
    relative_path: str,
    file_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    return {
        "confirmation_id": str(confirmation["confirmation_id"]),
        "review_id": str(confirmation["review_id"]),
        "relative_path": Path(relative_path).as_posix(),
        "sha256": file_sha256,
        "size_bytes": int(size_bytes),
        "binding_sha256": str(confirmation["binding_sha256"]),
        "created_at": str(confirmation["created_at"]),
    }


def _validate_pointer_file(
    root: Path,
    pointer: Any,
    *,
    kind: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(pointer, Mapping):
        raise MacrocycleError(
            f"MACROCYCLE_{kind.upper()}_NOT_RECORDED",
            "尚未记录大环协议文件",
            f"当前项目没有有效的{kind}指针。",
        )
    relative_path = str(pointer.get("relative_path") or "")
    expected_sha = str(pointer.get("sha256") or "").lower()
    try:
        expected_size = int(pointer.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise MacrocycleError(
            f"MACROCYCLE_{kind.upper()}_POINTER_INVALID",
            f"{kind}指针无效",
            f"{kind}记录缺少有效的文件大小。",
            raw_error=repr(pointer.get("size_bytes")),
        ) from exc
    if not _is_sha256(expected_sha) or expected_size <= 0:
        raise MacrocycleError(
            f"MACROCYCLE_{kind.upper()}_POINTER_INVALID",
            f"{kind}指针无效",
            f"{kind}记录缺少有效的 SHA256 或文件大小。",
        )
    path = _project_member(root, relative_path, must_exist=True)
    try:
        actual_size = path.stat().st_size
    except OSError as exc:
        raise MacrocycleError(
            f"MACROCYCLE_{kind.upper()}_READ_FAILED",
            f"无法读取{kind}记录",
            f"{kind}记录无法读取。",
            raw_error=str(exc),
        ) from exc
    if actual_size != expected_size or _sha256(path) != expected_sha:
        raise MacrocycleError(
            f"MACROCYCLE_{kind.upper()}_HASH_MISMATCH",
            f"{kind}记录完整性校验失败",
            f"{kind}记录的大小或 SHA256 已发生变化。",
            raw_error=relative_path,
            suggestion="请保留原记录并重新创建大环审查。",
        )
    return path, _read_json_file(path)


def _validate_review_semantics(
    review: Mapping[str, Any],
    *,
    pointer: Mapping[str, Any],
    root: Path,
    current_source: Mapping[str, Any] | None,
) -> None:
    review_id = str(review.get("review_id") or "")
    if (
        review.get("protocol_id") != PROTOCOL_ID
        or int(review.get("schema_version") or 0) != REVIEW_SCHEMA_VERSION
        or review.get("analysis_version") != ANALYSIS_VERSION
        or review.get("meeko_api_profile") != MEEKO_API_PROFILE
        or review.get("hydrogen_policy") != HYDROGEN_POLICY
        or not _REVIEW_ID_RE.fullmatch(review_id)
        or review_id != str(pointer.get("review_id") or "")
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SCHEMA_INVALID",
            "大环审查记录格式无效",
            "审查记录不属于当前正式大环协议或标识不一致。",
            raw_error=review_id,
        )
    expected_analysis = _canonical_json_sha256(_analysis_payload(review))
    if (
        str(review.get("analysis_sha256") or "") != expected_analysis
        or str(pointer.get("analysis_sha256") or "") != expected_analysis
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_ANALYSIS_HASH_MISMATCH",
            "大环分析摘要无效",
            "候选、原子映射或分析参数已发生变化。",
        )
    source = review.get("source")
    snapshot = review.get("input_snapshot")
    if not isinstance(source, Mapping) or not isinstance(snapshot, Mapping):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SOURCE_INVALID",
            "大环审查来源记录无效",
            "审查记录缺少原始配体或冻结输入信息。",
        )
    source_sha = str(source.get("sha256") or "").lower()
    snapshot_sha = str(snapshot.get("sha256") or "").lower()
    try:
        source_size = int(source.get("size_bytes"))
        snapshot_size = int(snapshot.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SOURCE_INVALID",
            "大环审查来源记录无效",
            "审查记录的来源文件大小无效。",
        ) from exc
    if (
        not _is_sha256(source_sha)
        or source_sha != snapshot_sha
        or source_size <= 0
        or source_size != snapshot_size
        or str(pointer.get("source_sha256") or "").lower() != source_sha
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SOURCE_BINDING_INVALID",
            "大环审查来源绑定无效",
            "原始配体、冻结输入与审查指针没有绑定到同一内容。",
        )
    snapshot_path = _project_member(
        root,
        str(snapshot.get("relative_path") or ""),
        must_exist=True,
    )
    expected_parent = REVIEW_ROOT / review_id
    if (
        Path(_relative_project_path(root, snapshot_path)).parent.as_posix()
        != expected_parent.as_posix()
        or snapshot_path.stat().st_size != snapshot_size
        or _sha256(snapshot_path) != snapshot_sha
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SNAPSHOT_HASH_MISMATCH",
            "大环冻结输入完整性校验失败",
            "审查使用的冻结配体已缺失或被修改。",
        )
    if current_source is not None and not _same_source(source, current_source):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SOURCE_STALE",
            "大环审查已过期",
            "当前 ligand raw 文件与审查时的配体不一致。",
            suggestion="请针对当前配体重新创建大环审查。",
        )

    atom_table_sha = str(review.get("atom_table_sha256") or "").lower()
    if not _is_sha256(atom_table_sha):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_ATOM_TABLE_INVALID",
            "大环原子映射摘要无效",
            "审查记录缺少有效的原子表 SHA256。",
        )
    atoms = review.get("atoms")
    if (
        not isinstance(atoms, list)
        or not all(isinstance(atom, Mapping) for atom in atoms)
        or _canonical_json_sha256(atoms) != atom_table_sha
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_ATOM_TABLE_HASH_MISMATCH",
            "大环原子映射已变化",
            "审查记录中的原子编号、名称或坐标与原子表摘要不一致。",
        )
    atom_indexing = review.get("atom_indexing")
    try:
        source_atom_count = int(
            atom_indexing.get("source_atom_count")
            if isinstance(atom_indexing, Mapping)
            else -1
        )
        prepared_atom_count = int(
            atom_indexing.get("prepared_atom_count")
            if isinstance(atom_indexing, Mapping)
            else -1
        )
    except (TypeError, ValueError) as exc:
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_ATOM_INDEXING_INVALID",
            "大环原子索引映射无效",
            "审查记录中的显式加氢原子映射无法解析。",
        ) from exc
    expected_source_mapping = list(range(source_atom_count))
    expected_added_hydrogens = list(
        range(source_atom_count, prepared_atom_count)
    )
    expected_source_heavy_indices = [
        index
        for index in range(min(source_atom_count, len(atoms)))
        if int(atoms[index].get("atomic_number") or 0) > 1
    ]
    if (
        not isinstance(atom_indexing, Mapping)
        or atom_indexing.get("hydrogen_policy") != HYDROGEN_POLICY
        or atom_indexing.get("source_indices_preserved") is not True
        or source_atom_count <= 0
        or prepared_atom_count != len(atoms)
        or source_atom_count > prepared_atom_count
        or atom_indexing.get("source_to_prepared_indices_zero_based")
        != expected_source_mapping
        or atom_indexing.get("source_heavy_atom_indices_zero_based")
        != expected_source_heavy_indices
        or atom_indexing.get("added_hydrogen_indices_zero_based")
        != expected_added_hydrogens
        or any(
            int(atoms[index].get("atomic_number") or 0) != 1
            for index in expected_added_hydrogens
        )
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_ATOM_INDEXING_INVALID",
            "大环原子索引映射无效",
            "审查记录没有证明显式加氢保留了原始原子索引。",
        )

    bond_topology = review.get("bond_topology")
    bond_topology_sha = str(
        review.get("bond_topology_sha256") or ""
    ).lower()
    molecule_record = (
        review.get("molecule")
        if isinstance(review.get("molecule"), Mapping)
        else {}
    )
    try:
        recorded_atom_count = int(molecule_record.get("atom_count"))
        recorded_bond_count = int(molecule_record.get("bond_count"))
    except (TypeError, ValueError):
        recorded_atom_count = -1
        recorded_bond_count = -1
    if (
        not isinstance(bond_topology, list)
        or not _is_sha256(bond_topology_sha)
        or _canonical_json_sha256(bond_topology) != bond_topology_sha
        or recorded_atom_count != len(atoms)
        or recorded_bond_count != len(bond_topology)
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_BOND_TOPOLOGY_INVALID",
            "大环键拓扑摘要无效",
            "审查记录缺少规范的键连接、键级或立体化学摘要。",
        )
    topology_pairs: list[list[int]] = []
    for record in bond_topology:
        if not isinstance(record, Mapping):
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_BOND_TOPOLOGY_INVALID",
                "大环键拓扑摘要无效",
                "键拓扑包含非对象记录。",
            )
        pair = record.get("atom_indices_zero_based")
        stereo_atoms = record.get("stereo_atom_indices_zero_based")
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(
                isinstance(index, bool) or not isinstance(index, int)
                for index in pair
            )
            or pair != list(_bond_pair(pair))
            or pair[0] < 0
            or pair[1] >= len(atoms)
            or not isinstance(stereo_atoms, list)
            or any(
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index >= len(atoms)
                for index in stereo_atoms
            )
        ):
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_BOND_TOPOLOGY_INVALID",
                "大环键拓扑摘要无效",
                "键拓扑包含无效的原子索引。",
            )
        topology_pairs.append(list(pair))
    if (
        topology_pairs != sorted(topology_pairs)
        or len({tuple(pair) for pair in topology_pairs})
        != len(topology_pairs)
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_BOND_TOPOLOGY_INVALID",
            "大环键拓扑摘要无效",
            "键拓扑没有使用规范顺序或包含重复键。",
        )

    tool_versions = review.get("tool_versions")
    if (
        not isinstance(tool_versions, Mapping)
        or str(tool_versions.get("meeko") or "")
        not in SUPPORTED_MEEKO_VERSIONS
        or not str(tool_versions.get("rdkit") or "")
        or not str(tool_versions.get("python") or "")
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_TOOL_VERSIONS_INVALID",
            "大环工具版本记录无效",
            "审查记录缺少正式协议要求的 Python、RDKit 或 Meeko 版本。",
        )
    options = review.get("options")
    option_input = (
        {key: value for key, value in options.items() if key != "max_ring_size"}
        if isinstance(options, Mapping)
        else None
    )
    if (
        not isinstance(options, Mapping)
        or int(options.get("max_ring_size") or 0) != DEFAULT_MAX_RING_SIZE
        or _normalize_options(option_input) != dict(options)
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_OPTIONS_INVALID",
            "大环审查参数无效",
            "审查记录中的 Meeko 参数不属于当前支持范围。",
        )
    candidates = review.get("candidate_sets")
    if not isinstance(candidates, list):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_CANDIDATES_INVALID",
            "大环候选记录无效",
            "审查记录中的候选集合不是列表。",
        )
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_CANDIDATES_INVALID",
                "大环候选记录无效",
                "候选集合包含非对象记录。",
            )
        pairs = candidate.get("exact_bonds")
        if not isinstance(pairs, list):
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_CANDIDATES_INVALID",
                "大环候选记录无效",
                "候选记录缺少精确断环键。",
            )
        normalized_pairs = [list(_bond_pair(pair)) for pair in pairs]
        if normalized_pairs != sorted(normalized_pairs):
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_CANDIDATES_INVALID",
                "大环候选记录无效",
                "候选断环键没有使用规范顺序。",
            )
        expected_id = _candidate_id(
            atom_table_sha,
            bond_topology_sha,
            options,
            normalized_pairs,
        )
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id != expected_id or candidate_id in candidate_ids:
            raise MacrocycleError(
                "MACROCYCLE_REVIEW_CANDIDATE_ID_INVALID",
                "大环候选标识无效",
                "候选标识与断环键、原子表或参数不一致。",
                raw_error=candidate_id,
            )
        candidate_ids.add(candidate_id)
    recommended = str(review.get("recommended_candidate_id") or "")
    if recommended and recommended not in candidate_ids:
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_RECOMMENDATION_INVALID",
            "Meeko 推荐候选无效",
            "推荐候选不属于当前冻结的候选集合。",
            raw_error=recommended,
        )
    if review.get("flexible_supported") is True and (
        not review.get("is_macrocycle") or not candidates or not recommended
    ):
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_SUPPORT_FLAG_INVALID",
            "大环可处理性记录无效",
            "审查记录错误地把不完整候选标记为可柔性准备。",
        )


def _load_active_review(
    project: Any,
    root: Path,
    current_source: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, MacrocycleError | None]:
    state = _state_from_project(project)
    pointer = state.get("active_review")
    if not isinstance(pointer, Mapping):
        return None, None, None
    try:
        _, review = _validate_pointer_file(root, pointer, kind="review")
        _validate_review_semantics(
            review,
            pointer=pointer,
            root=root,
            current_source=current_source,
        )
        return review, copy.deepcopy(dict(pointer)), None
    except MacrocycleError as exc:
        return None, copy.deepcopy(dict(pointer)), exc


def create_review(
    project_dir: str,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze and analyse the current raw ligand, then activate one review."""

    try:
        normalized = _normalize_options(options)
        with _preparation_target_lock(project_dir, "ligand"):
            project, root, load_error = _load_project_model(project_dir)
            if load_error:
                return load_error
            assert project is not None and root is not None
            source = _active_source(project, root)
            review_id = _next_review_id(root)
            review_dir = root / REVIEW_ROOT / review_id
            review_dir.mkdir(exist_ok=False)
            _assert_plain_path(review_dir, label="大环审查目录")

            snapshot_path = review_dir / f"input{source['suffix']}"
            _write_immutable_bytes(snapshot_path, source["bytes"])
            snapshot_relative = _relative_project_path(root, snapshot_path)
            # ``analyze_macrocycle_file`` accepts only caller-facing options.
            # ``normalized`` also contains the fixed ``max_ring_size`` evidence
            # field, so passing it back through the public validator would make
            # every project review fail as an unknown-option request.
            analysis = analyze_macrocycle_file(snapshot_path, options)
            if analysis.get("options") != normalized:
                raise MacrocycleError(
                    "MACROCYCLE_OPTIONS_NORMALIZATION_MISMATCH",
                    "大环参数规范化不一致",
                    "项目审查与纯分析 worker 得到了不同的有效参数。",
                )

            latest, latest_root, latest_error = _load_project_model(project_dir)
            if latest_error:
                return latest_error
            assert latest is not None and latest_root == root
            current_source = _active_source(latest, root)
            if not _same_source(source, current_source):
                return _error(
                    "MACROCYCLE_SOURCE_CHANGED_DURING_REVIEW",
                    "配体在审查过程中发生变化",
                    "候选分析完成前 ligand raw 文件或记录已改变，本次审查未激活。",
                    raw_error=review_id,
                    suggestion="请确认配体文件稳定后重新创建审查。",
                )

            review = {
                **analysis,
                "review_id": review_id,
                "created_at": _now_iso(),
                "source": {
                    "relative_path": source["relative_path"],
                    "sha256": source["sha256"],
                    "size_bytes": source["size_bytes"],
                },
                "input_snapshot": {
                    "relative_path": snapshot_relative,
                    "sha256": source["sha256"],
                    "size_bytes": source["size_bytes"],
                },
            }
            review["analysis_sha256"] = _canonical_json_sha256(
                _analysis_payload(review)
            )
            review_path = review_dir / "review.json"
            review_file_sha = _write_immutable_json(review_path, review)

            # Recheck after publication.  An external editor is not governed by
            # DockStart's target lock, so activation still needs this comparison.
            newest, newest_root, newest_error = _load_project_model(project_dir)
            if newest_error:
                return newest_error
            assert newest is not None and newest_root == root
            newest_source = _active_source(newest, root)
            if not _same_source(source, newest_source):
                return _error(
                    "MACROCYCLE_SOURCE_CHANGED_DURING_REVIEW",
                    "配体在审查过程中发生变化",
                    "审查记录已保留，但不会绑定到已经变化的 ligand raw 文件。",
                    raw_error=review_id,
                    suggestion="请针对当前配体重新创建审查。",
                )
            review_relative = _relative_project_path(root, review_path)
            state = _state_from_project(newest)
            state["active_review"] = _review_pointer(
                review,
                relative_path=review_relative,
                file_sha256=review_file_sha,
                size_bytes=review_path.stat().st_size,
            )
            state["active_confirmation"] = None
            _store_state(newest, state)
            saved = save_project(newest)
            if not saved.get("ok"):
                return saved

        status = get_status(project_dir)
        if status.get("ok"):
            status["review_created"] = True
            status["message"] = "大环候选审查已创建并绑定当前配体 SHA256。"
        return status
    except MacrocycleError as exc:
        return _error_from_exception(exc)
    except Exception as exc:  # noqa: BLE001 - stable public API boundary.
        return _error(
            "MACROCYCLE_REVIEW_FAILED",
            "创建大环审查失败",
            "大环候选审查未完成。",
            str(exc),
            "请检查项目目录权限、配体文件与 RDKit/Meeko 工具链。",
        )


def _confirmation_binding_payload(confirmation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "review_id": str(confirmation.get("review_id") or ""),
        "review_sha256": str(confirmation.get("review_sha256") or ""),
        "review_analysis_sha256": str(
            confirmation.get("review_analysis_sha256") or ""
        ),
        "source": copy.deepcopy(confirmation.get("source")),
        "input_snapshot": copy.deepcopy(confirmation.get("input_snapshot")),
        "hydrogen_policy": str(confirmation.get("hydrogen_policy") or ""),
        "atom_indexing": copy.deepcopy(confirmation.get("atom_indexing")),
        "atom_table_sha256": str(confirmation.get("atom_table_sha256") or ""),
        "bond_topology_sha256": str(
            confirmation.get("bond_topology_sha256") or ""
        ),
        "selection_mode": str(confirmation.get("selection_mode") or ""),
        "candidate_id": str(confirmation.get("candidate_id") or ""),
        "exact_bonds": copy.deepcopy(confirmation.get("exact_bonds")),
        "selected_bonds": copy.deepcopy(confirmation.get("selected_bonds")),
    }


def _validate_confirmation_semantics(
    confirmation: Mapping[str, Any],
    *,
    pointer: Mapping[str, Any],
    review: Mapping[str, Any],
    review_pointer: Mapping[str, Any],
) -> None:
    confirmation_id = str(confirmation.get("confirmation_id") or "")
    mode = str(confirmation.get("selection_mode") or "")
    if (
        confirmation.get("protocol_id") != PROTOCOL_ID
        or int(confirmation.get("schema_version") or 0)
        != CONFIRMATION_SCHEMA_VERSION
        or not _CONFIRMATION_ID_RE.fullmatch(confirmation_id)
        or confirmation_id != str(pointer.get("confirmation_id") or "")
        or str(confirmation.get("review_id") or "")
        != str(review.get("review_id") or "")
        or str(pointer.get("review_id") or "")
        != str(review.get("review_id") or "")
        or str(confirmation.get("review_sha256") or "")
        != str(review_pointer.get("sha256") or "")
        or str(confirmation.get("review_analysis_sha256") or "")
        != str(review.get("analysis_sha256") or "")
        or mode not in {"candidate", "rigid"}
    ):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_SCHEMA_INVALID",
            "大环确认记录无效",
            "确认记录与当前审查、协议版本或选择模式不一致。",
        )
    expected_binding = _canonical_json_sha256(
        _confirmation_binding_payload(confirmation)
    )
    if (
        str(confirmation.get("binding_sha256") or "") != expected_binding
        or str(pointer.get("binding_sha256") or "") != expected_binding
    ):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_BINDING_MISMATCH",
            "大环确认绑定无效",
            "用户选择、断环键或来源 SHA256 已发生变化。",
        )
    if confirmation.get("source") != review.get("source") or confirmation.get(
        "input_snapshot"
    ) != review.get("input_snapshot"):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_SOURCE_MISMATCH",
            "大环确认来源不一致",
            "确认记录没有绑定到当前审查的同一份冻结输入。",
        )
    if str(confirmation.get("atom_table_sha256") or "") != str(
        review.get("atom_table_sha256") or ""
    ) or str(confirmation.get("bond_topology_sha256") or "") != str(
        review.get("bond_topology_sha256") or ""
    ):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_TOPOLOGY_MISMATCH",
            "大环确认结构映射不一致",
            "确认记录与审查记录的原子表或键拓扑 SHA256 不一致。",
        )
    if (
        confirmation.get("hydrogen_policy") != review.get("hydrogen_policy")
        or confirmation.get("atom_indexing") != review.get("atom_indexing")
    ):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_HYDROGEN_MAPPING_MISMATCH",
            "大环确认显式氢映射不一致",
            "确认记录没有绑定审查时的显式加氢规则和原子索引。",
        )

    exact_bonds = confirmation.get("exact_bonds")
    selected_bonds = confirmation.get("selected_bonds")
    if not isinstance(exact_bonds, list) or not isinstance(selected_bonds, list):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_BONDS_INVALID",
            "大环确认断环键无效",
            "确认记录缺少服务器冻结的断环键。",
        )
    if mode == "rigid":
        if confirmation.get("candidate_id") not in {"", None} or exact_bonds or selected_bonds:
            raise MacrocycleError(
                "MACROCYCLE_CONFIRMATION_RIGID_INVALID",
                "刚性大环确认无效",
                "刚性模式不得包含候选标识或断环键。",
            )
        return
    candidate_id = str(confirmation.get("candidate_id") or "")
    candidates = {
        str(item.get("candidate_id") or ""): item
        for item in review.get("candidate_sets") or []
        if isinstance(item, Mapping)
    }
    candidate = candidates.get(candidate_id)
    if candidate is None:
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_CANDIDATE_MISSING",
            "确认的候选已不存在",
            "确认记录中的候选不属于当前审查。",
            raw_error=candidate_id,
        )
    if (
        exact_bonds != candidate.get("exact_bonds")
        or selected_bonds != candidate.get("bonds")
    ):
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_BONDS_MISMATCH",
            "确认的断环键不一致",
            "确认记录没有逐字复制服务器审查记录中的候选键。",
        )


def _load_active_confirmation(
    project: Any,
    root: Path,
    review: Mapping[str, Any] | None,
    review_pointer: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, MacrocycleError | None]:
    state = _state_from_project(project)
    pointer = state.get("active_confirmation")
    if not isinstance(pointer, Mapping):
        return None, None, None
    if review is None or review_pointer is None:
        return (
            None,
            copy.deepcopy(dict(pointer)),
            MacrocycleError(
                "MACROCYCLE_CONFIRMATION_REVIEW_INVALID",
                "大环确认已失效",
                "确认记录对应的审查不再有效。",
            ),
        )
    try:
        _, confirmation = _validate_pointer_file(
            root,
            pointer,
            kind="confirmation",
        )
        _validate_confirmation_semantics(
            confirmation,
            pointer=pointer,
            review=review,
            review_pointer=review_pointer,
        )
        return confirmation, copy.deepcopy(dict(pointer)), None
    except MacrocycleError as exc:
        return None, copy.deepcopy(dict(pointer)), exc


def get_status(project_dir: str) -> dict[str, Any]:
    """Return current review/confirmation readiness with integrity evidence."""

    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        return load_error
    assert project is not None and root is not None
    source: dict[str, Any] | None = None
    source_error: MacrocycleError | None = None
    try:
        source = _active_source(project, root)
    except MacrocycleError as exc:
        source_error = exc

    review, review_pointer, review_error = _load_active_review(
        project,
        root,
        source,
    )
    confirmation, confirmation_pointer, confirmation_error = (
        _load_active_confirmation(
            project,
            root,
            review,
            review_pointer,
        )
    )
    issues: list[dict[str, Any]] = []
    for exc in (source_error, review_error, confirmation_error):
        if exc is not None:
            issues.append(
                _issue(
                    exc.code,
                    exc.title,
                    exc.message,
                    raw_error=exc.raw_error,
                    suggestion=exc.suggestion,
                )
            )

    if source is None:
        state_name = "not_reviewed"
    elif review_error is not None or confirmation_error is not None:
        state_name = "stale"
    elif review is None:
        state_name = "not_reviewed"
    elif confirmation is not None:
        state_name = "confirmed"
    elif not review.get("is_macrocycle"):
        state_name = "not_macrocycle"
    elif not review.get("flexible_supported"):
        state_name = "unsupported"
    else:
        state_name = "reviewed"

    can_review = source is not None
    can_confirm_candidate = bool(
        review is not None and review.get("flexible_supported") is True
    )
    can_confirm_rigid = bool(
        review is not None
        and review.get("is_macrocycle") is True
        and review.get("rigid_supported") is True
    )
    source_view = None
    if source is not None:
        source_view = {
            "relative_path": source["relative_path"],
            "sha256": source["sha256"],
            "size_bytes": source["size_bytes"],
            "suffix": source["suffix"],
        }
    review_view = copy.deepcopy(review)
    if review_view is not None:
        review_view["record"] = copy.deepcopy(review_pointer)
        review_view["valid"] = True
    elif review_pointer is not None:
        review_view = {
            "record": copy.deepcopy(review_pointer),
            "valid": False,
            "invalid_reason": review_error.message if review_error else "",
        }
    confirmation_view = copy.deepcopy(confirmation)
    if confirmation_view is not None:
        confirmation_view["record"] = copy.deepcopy(confirmation_pointer)
        confirmation_view["valid"] = True
    elif confirmation_pointer is not None:
        confirmation_view = {
            "record": copy.deepcopy(confirmation_pointer),
            "valid": False,
            "invalid_reason": (
                confirmation_error.message if confirmation_error else ""
            ),
        }
    return {
        "ok": True,
        "project_dir": str(root),
        "project": project.to_dict(),
        "protocol_id": PROTOCOL_ID,
        "schema_version": STATE_SCHEMA_VERSION,
        "state": state_name,
        "source": source_view,
        "review": review_view,
        "confirmation": confirmation_view,
        "integrity": {"ok": not issues, "issues": issues},
        "can_review": can_review,
        "can_confirm": can_confirm_candidate or can_confirm_rigid,
        "can_confirm_candidate": can_confirm_candidate,
        "can_confirm_rigid": can_confirm_rigid,
        "can_prepare": confirmation is not None and not issues,
        "message": {
            "not_reviewed": "请先创建大环候选审查。",
            "reviewed": "候选已就绪，请确认一个断环组合或刚性模式。",
            "confirmed": "大环选择已确认，可创建受审查的准备任务。",
            "stale": "大环审查或确认已失效，请检查完整性问题。",
            "unsupported": "当前结构不能按候选柔性断环；仍可明确选择刚性大环。",
            "not_macrocycle": "当前结构未检测到达到阈值的大环。",
        }.get(state_name, ""),
        "error": None,
    }


def confirm_selection(
    project_dir: str,
    review_id: str,
    candidate_id: str | None = None,
    *,
    rigid: bool = False,
) -> dict[str, Any]:
    """Confirm only a server-issued candidate identifier or explicit rigid mode."""

    if bool(candidate_id) == bool(rigid):
        return _error(
            "MACROCYCLE_SELECTION_REQUIRED",
            "大环选择无效",
            "必须且只能选择一个候选标识或刚性大环模式。",
        )
    try:
        with _preparation_target_lock(project_dir, "ligand"):
            project, root, load_error = _load_project_model(project_dir)
            if load_error:
                return load_error
            assert project is not None and root is not None
            source = _active_source(project, root)
            review, review_pointer, review_error = _load_active_review(
                project,
                root,
                source,
            )
            if review_error is not None:
                return _error_from_exception(review_error)
            if review is None or review_pointer is None:
                return _error(
                    "MACROCYCLE_REVIEW_REQUIRED",
                    "尚未完成大环审查",
                    "确认选择前必须先创建有效的大环候选审查。",
                )
            if str(review.get("review_id") or "") != str(review_id or ""):
                return _error(
                    "MACROCYCLE_REVIEW_ID_MISMATCH",
                    "大环审查标识已变化",
                    "提交的 review_id 不是当前活动审查。",
                    raw_error=str(review_id),
                    suggestion="请刷新候选列表后重新确认。",
                )
            if not review.get("is_macrocycle"):
                return _error(
                    "MACROCYCLE_NOT_DETECTED",
                    "当前配体未检测到大环",
                    "非大环配体不需要保存大环断环确认。",
                )

            selected_candidate: Mapping[str, Any] | None = None
            if not rigid:
                if review.get("flexible_supported") is not True:
                    return _error(
                        "MACROCYCLE_FLEXIBLE_UNSUPPORTED",
                        "当前大环不能柔性断环",
                        "审查记录没有形成完整、可验证的断环候选。",
                    )
                selected_candidate = next(
                    (
                        item
                        for item in review.get("candidate_sets") or []
                        if isinstance(item, Mapping)
                        and str(item.get("candidate_id") or "")
                        == str(candidate_id or "")
                    ),
                    None,
                )
                if selected_candidate is None:
                    return _error(
                        "MACROCYCLE_CANDIDATE_ID_INVALID",
                        "断环候选无效",
                        "提交的 candidate_id 不属于当前审查。",
                        raw_error=str(candidate_id or ""),
                        suggestion="请刷新候选列表后重新选择。",
                    )

            review_dir = _project_member(
                root,
                (REVIEW_ROOT / str(review["review_id"])).as_posix(),
                must_exist=True,
                expect_file=False,
            )
            confirmation_id = _next_confirmation_id(review_dir)
            confirmation: dict[str, Any] = {
                "protocol_id": PROTOCOL_ID,
                "schema_version": CONFIRMATION_SCHEMA_VERSION,
                "confirmation_id": confirmation_id,
                "created_at": _now_iso(),
                "review_id": str(review["review_id"]),
                "review_sha256": str(review_pointer["sha256"]),
                "review_analysis_sha256": str(review["analysis_sha256"]),
                "source": copy.deepcopy(review["source"]),
                "input_snapshot": copy.deepcopy(review["input_snapshot"]),
                "hydrogen_policy": str(review["hydrogen_policy"]),
                "atom_indexing": copy.deepcopy(review["atom_indexing"]),
                "atom_table_sha256": str(review["atom_table_sha256"]),
                "bond_topology_sha256": str(
                    review["bond_topology_sha256"]
                ),
                "selection_mode": "rigid" if rigid else "candidate",
                "candidate_id": "" if rigid else str(candidate_id),
                "exact_bonds": (
                    []
                    if rigid
                    else copy.deepcopy(selected_candidate["exact_bonds"])
                ),
                "selected_bonds": (
                    [] if rigid else copy.deepcopy(selected_candidate["bonds"])
                ),
            }
            confirmation["binding_sha256"] = _canonical_json_sha256(
                _confirmation_binding_payload(confirmation)
            )
            confirmation_path = review_dir / f"{confirmation_id}.json"
            file_sha = _write_immutable_json(confirmation_path, confirmation)

            latest, latest_root, latest_error = _load_project_model(project_dir)
            if latest_error:
                return latest_error
            assert latest is not None and latest_root == root
            latest_source = _active_source(latest, root)
            latest_review, latest_pointer, latest_review_error = _load_active_review(
                latest,
                root,
                latest_source,
            )
            if (
                latest_review_error is not None
                or latest_review is None
                or latest_pointer is None
                or latest_pointer != review_pointer
                or not _same_source(source, latest_source)
            ):
                return _error(
                    "MACROCYCLE_STATE_CHANGED_DURING_CONFIRM",
                    "大环审查在确认过程中发生变化",
                    "确认记录已保留，但不会激活到已经变化的配体或审查。",
                    raw_error=confirmation_id,
                    suggestion="请刷新状态后重新确认。",
                )
            state = _state_from_project(latest)
            state["active_confirmation"] = _confirmation_pointer(
                confirmation,
                relative_path=_relative_project_path(root, confirmation_path),
                file_sha256=file_sha,
                size_bytes=confirmation_path.stat().st_size,
            )
            _store_state(latest, state)
            saved = save_project(latest)
            if not saved.get("ok"):
                return saved
        status = get_status(project_dir)
        if status.get("ok"):
            status["confirmation_created"] = True
            status["message"] = (
                "已确认刚性大环模式。"
                if rigid
                else "已确认断环候选并冻结精确原子键。"
            )
        return status
    except MacrocycleError as exc:
        return _error_from_exception(exc)
    except Exception as exc:  # noqa: BLE001
        return _error(
            "MACROCYCLE_CONFIRM_FAILED",
            "保存大环确认失败",
            "大环选择未能完成。",
            str(exc),
        )


def reset_selection(project_dir: str) -> dict[str, Any]:
    """Deactivate the current confirmation without deleting immutable records."""

    try:
        with _preparation_target_lock(project_dir, "ligand"):
            project, _, load_error = _load_project_model(project_dir)
            if load_error:
                return load_error
            assert project is not None
            state = _state_from_project(project)
            state["active_confirmation"] = None
            _store_state(project, state)
            saved = save_project(project)
            if not saved.get("ok"):
                return saved
        status = get_status(project_dir)
        if status.get("ok"):
            status["selection_reset"] = True
            status["message"] = "已取消当前大环选择；不可变审查记录仍保留。"
        return status
    except Exception as exc:  # noqa: BLE001
        return _error(
            "MACROCYCLE_RESET_FAILED",
            "取消大环选择失败",
            "当前大环确认未能重置。",
            str(exc),
        )


def _resolve_confirmed_unlocked(
    project_dir: str,
    *,
    expected_review_id: str | None,
    expected_confirmation_sha256: str | None,
) -> tuple[dict[str, Any], Path]:
    project, root, load_error = _load_project_model(project_dir)
    if load_error:
        error = load_error.get("error") or {}
        raise MacrocycleError(
            str(error.get("code") or "MACROCYCLE_PROJECT_INVALID"),
            str(error.get("title") or "项目数据无效"),
            str(error.get("message") or "无法读取项目。"),
            raw_error=str(error.get("raw_error") or ""),
            suggestion=str(error.get("suggestion") or ""),
        )
    assert project is not None and root is not None
    source = _active_source(project, root)
    review, review_pointer, review_error = _load_active_review(project, root, source)
    if review_error is not None:
        raise review_error
    if review is None or review_pointer is None:
        raise MacrocycleError(
            "MACROCYCLE_REVIEW_REQUIRED",
            "尚未完成大环审查",
            "创建准备任务前必须存在有效的大环审查。",
        )
    confirmation, confirmation_pointer, confirmation_error = (
        _load_active_confirmation(project, root, review, review_pointer)
    )
    if confirmation_error is not None:
        raise confirmation_error
    if confirmation is None or confirmation_pointer is None:
        raise MacrocycleError(
            "MACROCYCLE_CONFIRMATION_REQUIRED",
            "尚未确认大环选择",
            "创建准备任务前必须确认断环候选或刚性大环模式。",
        )
    if expected_review_id and str(review["review_id"]) != expected_review_id:
        raise MacrocycleError(
            "MACROCYCLE_EXPECTED_REVIEW_MISMATCH",
            "大环审查标识不一致",
            "准备请求绑定的 review_id 已不是当前活动审查。",
        )
    if expected_confirmation_sha256 and (
        str(confirmation_pointer["sha256"]).lower()
        != expected_confirmation_sha256.lower()
    ):
        raise MacrocycleError(
            "MACROCYCLE_EXPECTED_CONFIRMATION_MISMATCH",
            "大环确认摘要不一致",
            "准备请求绑定的确认文件 SHA256 已不是当前活动确认。",
        )
    contract = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "meeko_api_profile": MEEKO_API_PROFILE,
        "review_id": review["review_id"],
        "review_file": review_pointer["relative_path"],
        "review_sha256": review_pointer["sha256"],
        "review_analysis_sha256": review["analysis_sha256"],
        "confirmation_id": confirmation["confirmation_id"],
        "confirmation_file": confirmation_pointer["relative_path"],
        "confirmation_sha256": confirmation_pointer["sha256"],
        "confirmation_binding_sha256": confirmation["binding_sha256"],
        "source": copy.deepcopy(review["source"]),
        "review_input": copy.deepcopy(review["input_snapshot"]),
        "options": copy.deepcopy(review["options"]),
        "hydrogen_policy": review["hydrogen_policy"],
        "atom_indexing": copy.deepcopy(review["atom_indexing"]),
        "atom_table_sha256": review["atom_table_sha256"],
        "bond_topology_sha256": review["bond_topology_sha256"],
        "bond_topology": copy.deepcopy(review["bond_topology"]),
        "selection_mode": confirmation["selection_mode"],
        "candidate_id": confirmation["candidate_id"],
        "exact_bonds": copy.deepcopy(confirmation["exact_bonds"]),
        "selected_bonds": copy.deepcopy(confirmation["selected_bonds"]),
        "tool_versions": copy.deepcopy(review["tool_versions"]),
        "resolved_at": _now_iso(),
    }
    contract["contract_binding_sha256"] = _canonical_json_sha256(
        {key: value for key, value in contract.items() if key != "resolved_at"}
    )
    return contract, root


def resolve_confirmed_contract(
    project_dir: str,
    expected_review_id: str | None = None,
    expected_confirmation_sha256: str | None = None,
    *,
    target_lock_held: bool = False,
) -> dict[str, Any]:
    """Resolve the current confirmation into a strict preparation contract."""

    try:
        lock = (
            nullcontext()
            if target_lock_held
            else _preparation_target_lock(project_dir, "ligand")
        )
        with lock:
            contract, root = _resolve_confirmed_unlocked(
                project_dir,
                expected_review_id=expected_review_id,
                expected_confirmation_sha256=expected_confirmation_sha256,
            )
        return {
            "ok": True,
            "project_dir": str(root),
            "contract": contract,
            "error": None,
        }
    except MacrocycleError as exc:
        return _error_from_exception(exc)


def build_reviewed_preparation_plan(
    project_dir: str,
    python_executable: str | Path,
    output_pdbqt: str | Path,
    *,
    record_dir: str | Path,
    expected_review_id: str | None = None,
    expected_confirmation_sha256: str | None = None,
    target_lock_held: bool = False,
) -> dict[str, Any]:
    """Freeze the reviewed input into a preparation record and build argv."""

    try:
        lock = (
            nullcontext()
            if target_lock_held
            else _preparation_target_lock(project_dir, "ligand")
        )
        with lock:
            contract, root = _resolve_confirmed_unlocked(
                project_dir,
                expected_review_id=expected_review_id,
                expected_confirmation_sha256=expected_confirmation_sha256,
            )
            record = Path(record_dir).expanduser().resolve()
            output = Path(output_pdbqt).expanduser().resolve()
            record_relative = _relative_project_path(root, record)
            _project_member(root, record_relative, must_exist=True, expect_file=False)
            _relative_project_path(root, output)
            if output.suffix.lower() != ".pdbqt":
                raise MacrocycleError(
                    "MACROCYCLE_OUTPUT_SUFFIX_INVALID",
                    "大环准备输出格式无效",
                    "受审查的大环准备输出必须是 PDBQT。",
                )
            python_path = Path(python_executable).expanduser().resolve()
            if not python_path.is_file():
                raise MacrocycleError(
                    "MACROCYCLE_PYTHON_NOT_FOUND",
                    "没有找到 Python",
                    "大环准备计划需要可执行的 Python 文件。",
                    raw_error=str(python_path),
                )
            snapshot_record = contract["review_input"]
            snapshot = _project_member(
                root,
                str(snapshot_record["relative_path"]),
                must_exist=True,
            )
            snapshot_bytes = _read_stable_bytes(
                snapshot,
                maximum_bytes=MAX_SOURCE_BYTES,
                label="大环审查冻结输入",
            )
            if _sha256_bytes(snapshot_bytes) != snapshot_record["sha256"]:
                raise MacrocycleError(
                    "MACROCYCLE_REVIEW_SNAPSHOT_HASH_MISMATCH",
                    "大环冻结输入完整性校验失败",
                    "准备计划读取的冻结输入与审查 SHA256 不一致。",
                )
            frozen_input = record / f"macrocycle_input{snapshot.suffix.lower()}"
            _write_immutable_bytes(frozen_input, snapshot_bytes)
            contract["runtime_input"] = {
                "relative_path": _relative_project_path(root, frozen_input),
                "sha256": _sha256_bytes(snapshot_bytes),
                "size_bytes": len(snapshot_bytes),
            }
            contract_file = record / "macrocycle_contract.json"
            contract_sha = _write_immutable_json(contract_file, contract)
            evidence_file = record / "macrocycle_evidence.json"
            latest_project, _, latest_error = _load_project_model(project_dir)
            if latest_error:
                return latest_error
            assert latest_project is not None
            if not _same_source(contract["source"], _active_source(latest_project, root)):
                raise MacrocycleError(
                    "MACROCYCLE_SOURCE_CHANGED_DURING_PLAN",
                    "配体在准备计划创建时发生变化",
                    "准备输入已冻结，但不会运行已过期的大环确认。",
                )
        return {
            "ok": True,
            "project_dir": str(root),
            "protocol": PROTOCOL_ID,
            "argv": [
                str(python_path),
                "-I",
                "-B",
                "-c",
                ISOLATED_MODULE_RUNNER,
                str(BACKEND_ROOT),
                "prepare-worker",
                "--input",
                str(frozen_input),
                "--output",
                str(output),
                "--contract",
                str(contract_file),
                "--evidence",
                str(evidence_file),
            ],
            "contract": contract,
            "contract_file": _relative_project_path(root, contract_file),
            "contract_sha256": contract_sha,
            "input_snapshot_file": _relative_project_path(root, frozen_input),
            "evidence_file": _relative_project_path(root, evidence_file),
            "expected_output_evidence": {
                "selection_mode": contract["selection_mode"],
                "candidate_id": contract["candidate_id"],
                "exact_bonds": copy.deepcopy(contract["exact_bonds"]),
                "atom_table_sha256": contract["atom_table_sha256"],
                "bond_topology_sha256": contract[
                    "bond_topology_sha256"
                ],
                "confirmation_sha256": contract["confirmation_sha256"],
            },
            "error": None,
        }
    except MacrocycleError as exc:
        return _error_from_exception(exc)


def prepare_reviewed_macrocycle(
    input_path: str | Path,
    output_path: str | Path,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Worker that applies only the exact bonds in a verified contract."""

    try:
        if (
            not isinstance(contract, Mapping)
            or contract.get("protocol_id") != PROTOCOL_ID
            or isinstance(contract.get("schema_version"), bool)
            or contract.get("schema_version") != CONTRACT_SCHEMA_VERSION
            or contract.get("analysis_version") != ANALYSIS_VERSION
            or contract.get("meeko_api_profile") != MEEKO_API_PROFILE
            or contract.get("hydrogen_policy") != HYDROGEN_POLICY
        ):
            raise MacrocycleError(
                "MACROCYCLE_CONTRACT_INVALID",
                "大环准备合同无效",
                "准备 worker 只接受当前版本的正式大环协议合同。",
            )
        expected_contract_sha = _canonical_json_sha256(
            {
                key: value
                for key, value in contract.items()
                if key
                not in {
                    "resolved_at",
                    "runtime_input",
                    "contract_binding_sha256",
                }
            }
        )
        if contract.get("contract_binding_sha256") != expected_contract_sha:
            raise MacrocycleError(
                "MACROCYCLE_CONTRACT_BINDING_MISMATCH",
                "大环准备合同摘要无效",
                "确认选择或断环键在计划创建后发生变化。",
            )
        path = Path(input_path).expanduser().resolve()
        runtime_input = contract.get("runtime_input")
        if not isinstance(runtime_input, Mapping):
            raise MacrocycleError(
                "MACROCYCLE_RUNTIME_INPUT_INVALID",
                "大环准备输入记录无效",
                "准备合同缺少冻结运行输入。",
            )
        payload = _read_stable_bytes(
            path,
            maximum_bytes=MAX_SOURCE_BYTES,
            label="大环准备输入",
        )
        if (
            _sha256_bytes(payload) != runtime_input.get("sha256")
            or len(payload) != int(runtime_input.get("size_bytes") or 0)
            or runtime_input.get("sha256") != (contract.get("review_input") or {}).get("sha256")
        ):
            raise MacrocycleError(
                "MACROCYCLE_RUNTIME_INPUT_HASH_MISMATCH",
                "大环准备输入完整性校验失败",
                "worker 输入与审查冻结输入 SHA256 不一致。",
            )
        toolkit = _toolkit()
        contract_tool_versions = contract.get("tool_versions")
        if not isinstance(contract_tool_versions, Mapping):
            raise MacrocycleError(
                "MACROCYCLE_CONTRACT_TOOL_VERSIONS_INVALID",
                "大环合同工具版本无效",
                "准备合同没有绑定审查时的 RDKit 与 Meeko 版本。",
            )
        version_mismatches = {
            name: {
                "review": str(contract_tool_versions.get(name) or ""),
                "worker": str(toolkit.get(f"{name}_version") or ""),
            }
            for name in ("rdkit", "meeko")
            if str(contract_tool_versions.get(name) or "")
            != str(toolkit.get(f"{name}_version") or "")
        }
        if version_mismatches:
            raise MacrocycleError(
                "MACROCYCLE_TOOLKIT_VERSION_MISMATCH",
                "大环工具版本发生变化",
                "准备 worker 的 RDKit 或 Meeko 版本与审查时不一致。",
                raw_error=json.dumps(
                    version_mismatches,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                suggestion="请使用同一 Python 工具链重新审查并确认配体。",
            )
        molecule = _load_single_molecule(
            path,
            toolkit,
            source_bytes=payload,
        )
        molecule, atom_indexing = _prepare_explicit_hydrogens(
            molecule,
            toolkit,
        )
        atom_table, atom_sha, conformer = _atom_table(molecule)
        if atom_sha != contract.get("atom_table_sha256"):
            raise MacrocycleError(
                "MACROCYCLE_ATOM_TABLE_HASH_MISMATCH",
                "大环原子映射不一致",
                "准备时的原子编号或坐标与用户确认时不同。",
            )
        if atom_indexing != contract.get("atom_indexing"):
            raise MacrocycleError(
                "MACROCYCLE_ATOM_INDEXING_MISMATCH",
                "大环显式氢映射不一致",
                "准备时的显式加氢结果或原始原子索引与审查时不同。",
            )
        bond_topology, bond_topology_sha = _bond_topology(molecule)
        if (
            bond_topology_sha != contract.get("bond_topology_sha256")
            or bond_topology != contract.get("bond_topology")
        ):
            raise MacrocycleError(
                "MACROCYCLE_BOND_TOPOLOGY_MISMATCH",
                "大环键拓扑不一致",
                "准备时的原子连接、键级或立体化学与审查时不同。",
            )
        options = contract.get("options")
        normalized_options = _normalize_options(
            {
                key: value
                for key, value in dict(options or {}).items()
                if key != "max_ring_size"
            }
        )
        mode = str(contract.get("selection_mode") or "")
        raw_exact = contract.get("exact_bonds")
        if not isinstance(raw_exact, list) or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(
                isinstance(index, bool) or not isinstance(index, int)
                for index in pair
            )
            for pair in raw_exact
        ):
            raise MacrocycleError(
                "MACROCYCLE_CONTRACT_BONDS_INVALID",
                "大环合同断环键无效",
                "准备合同中的精确断环键不是规范的原子索引对。",
            )
        exact = [list(_bond_pair(pair)) for pair in raw_exact]
        if (
            exact != raw_exact
            or exact != sorted(exact)
            or len({tuple(pair) for pair in exact}) != len(exact)
        ):
            raise MacrocycleError(
                "MACROCYCLE_CONTRACT_BONDS_INVALID",
                "大环合同断环键无效",
                "准备合同中的精确断环键未按规范顺序记录或包含重复项。",
            )
        if mode == "candidate":
            try:
                _, _, candidate_data, _ = _candidate_setup(
                    molecule,
                    toolkit,
                    normalized_options,
                )
                current_candidates = {
                    tuple(
                        sorted(
                            {
                                _bond_pair(pair)
                                for pair in raw_combo
                            }
                        )
                    )
                    for raw_combo in (
                        candidate_data.get("bond_break_combos") or []
                    )
                }
            except MacrocycleError:
                raise
            except Exception as exc:  # noqa: BLE001 - pinned Meeko internals.
                raise MacrocycleError(
                    "MACROCYCLE_CANDIDATE_REVALIDATION_FAILED",
                    "无法复核大环断环候选",
                    "准备 worker 未能重新生成审查时的 Meeko 候选集合。",
                    raw_error=str(exc),
                ) from exc
            exact_key = tuple(tuple(pair) for pair in exact)
            expected_candidate_id = _candidate_id(
                atom_sha,
                bond_topology_sha,
                normalized_options,
                exact,
            )
            if (
                not exact
                or exact_key not in current_candidates
                or str(contract.get("candidate_id") or "")
                != expected_candidate_id
            ):
                raise MacrocycleError(
                    "MACROCYCLE_CONFIRMED_CANDIDATE_MISMATCH",
                    "已确认断环候选不再有效",
                    "worker 重新分析后，精确断环键不属于同一工具链生成的确认候选。",
                    raw_error=(
                        f"candidate_id={contract.get('candidate_id')!r}; "
                        f"expected={expected_candidate_id!r}; bonds={exact!r}"
                    ),
                )
        preparator = _meeko_preparator(
            toolkit,
            normalized_options,
            rigid=mode == "rigid",
        )
        preparator._macrocycle_typer.max_breaks = int(normalized_options["max_breaks"])  # noqa: SLF001
        glue: dict[int, list[float]] = {}
        if mode == "candidate":
            for left, right in exact:
                if molecule.GetBondBetweenAtoms(left, right) is None:
                    raise MacrocycleError(
                        "MACROCYCLE_CONFIRMED_BOND_MISSING",
                        "已确认的断环键不存在",
                        "冻结输入中找不到用户确认的原子键。",
                        raw_error=f"{left}-{right}",
                    )
                glue[left] = _coordinate_list(conformer, right)
                glue[right] = _coordinate_list(conformer, left)
            setups = preparator.prepare(
                molecule,
                delete_ring_bonds=[tuple(pair) for pair in exact],
                glue_pseudo_atoms=glue,
            )
        elif (
            mode == "rigid"
            and not exact
            and not str(contract.get("candidate_id") or "")
        ):
            setups = preparator.prepare(molecule)
        else:
            raise MacrocycleError(
                "MACROCYCLE_SELECTION_MODE_INVALID",
                "大环选择模式无效",
                "准备合同必须是候选断环或不含断环键的刚性模式。",
            )
        if len(setups) != 1:
            raise MacrocycleError(
                "MACROCYCLE_SETUP_COUNT_INVALID",
                "Meeko 生成数量异常",
                "正式大环 worker 要求恰好生成一个分子设置。",
                raw_error=f"setup_count={len(setups)}",
            )
        setup = setups[0]
        actual = [
            list(pair)
            for pair in sorted(
                {_bond_pair(pair) for pair in setup.ring_closure_info.bonds_removed}
            )
        ]
        if actual != exact:
            raise MacrocycleError(
                "MACROCYCLE_ACTUAL_BONDS_MISMATCH",
                "Meeko 实际断环键不一致",
                "Meeko 没有严格采用用户确认的断环键，输出已拒绝。",
                raw_error=f"expected={exact}; actual={actual}",
            )
        pdbqt, success, writer_error = toolkit["PDBQTWriterLegacy"].write_string(
            setup,
            add_index_map=True,
        )
        if not success or not pdbqt.strip():
            raise MacrocycleError(
                "MACROCYCLE_PDBQT_WRITE_FAILED",
                "无法生成大环 PDBQT",
                "Meeko PDBQT writer 未生成有效输出。",
                raw_error=str(writer_error),
            )
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".pdbqt" or output.exists():
            raise MacrocycleError(
                "MACROCYCLE_OUTPUT_PATH_INVALID",
                "大环输出路径无效",
                "worker 只写入尚不存在的 .pdbqt 候选输出。",
                raw_error=str(output),
            )
        atomic_write_text(output, pdbqt, encoding="utf-8")
        pseudos = [
            atom
            for atom in setup.atoms
            if atom.is_pseudo_atom and str(atom.atom_type).startswith("G")
        ]
        if len(pseudos) != 2 * len(exact):
            output.unlink(missing_ok=True)
            raise MacrocycleError(
                "MACROCYCLE_GLUE_ATOM_COUNT_MISMATCH",
                "胶合伪原子数量不一致",
                "Meeko 输出的 G* 伪原子数量与断环键不匹配。",
            )
        return {
            "ok": True,
            "protocol_id": PROTOCOL_ID,
            "output_file": str(output),
            "output_sha256": _sha256(output),
            "output_size_bytes": output.stat().st_size,
            "selection_mode": mode,
            "candidate_id": str(contract.get("candidate_id") or ""),
            "expected_bonds": exact,
            "actual_bonds": actual,
            "glue_pseudo_atom_count": len(pseudos),
            "atom_table_sha256": atom_sha,
            "bond_topology_sha256": bond_topology_sha,
            "hydrogen_policy": HYDROGEN_POLICY,
            "atom_indexing": atom_indexing,
            "review_id": str(contract.get("review_id") or ""),
            "confirmation_sha256": str(contract.get("confirmation_sha256") or ""),
            "meeko_version": toolkit["meeko_version"],
            "rdkit_version": toolkit["rdkit_version"],
            "error": None,
        }
    except MacrocycleError as exc:
        return _error_from_exception(exc)
    except Exception as exc:  # noqa: BLE001
        return _error(
            "MACROCYCLE_PREPARATION_FAILED",
            "大环准备失败",
            "受审查的大环准备 worker 未完成。",
            str(exc),
        )


def _parse_options_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    if len(value.encode("utf-8")) > 64 * 1024:
        raise ValueError("options JSON 过大")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("options JSON 顶层必须是对象")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockStart 正式大环协议")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "review", "reset"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name == "review":
            command.add_argument("--options-json")
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--project", required=True)
    confirm.add_argument("--review-id", required=True)
    selection = confirm.add_mutually_exclusive_group(required=True)
    selection.add_argument("--candidate-id")
    selection.add_argument("--rigid", action="store_true")
    worker = commands.add_parser("prepare-worker")
    worker.add_argument("--input", required=True)
    worker.add_argument("--output", required=True)
    worker.add_argument("--contract", required=True)
    worker.add_argument("--evidence", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "status":
            result = get_status(args.project)
        elif args.command == "review":
            result = create_review(
                args.project,
                _parse_options_json(args.options_json),
            )
        elif args.command == "confirm":
            result = confirm_selection(
                args.project,
                args.review_id,
                args.candidate_id,
                rigid=args.rigid,
            )
        elif args.command == "reset":
            result = reset_selection(args.project)
        else:
            contract_path = Path(args.contract).expanduser().resolve()
            contract = _read_json_file(contract_path)
            result = prepare_reviewed_macrocycle(
                args.input,
                args.output,
                contract,
            )
            if result.get("ok"):
                evidence_path = Path(args.evidence).expanduser().resolve()
                if evidence_path.exists():
                    raise MacrocycleError(
                        "MACROCYCLE_EVIDENCE_EXISTS",
                        "大环证据文件已存在",
                        "worker 不会覆盖已有的证据记录。",
                        raw_error=str(evidence_path),
                    )
                atomic_write_text(
                    evidence_path,
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                )
    except MacrocycleError as exc:
        result = _error_from_exception(exc)
    except Exception as exc:  # noqa: BLE001 - CLI always emits one JSON response.
        result = _error(
            "MACROCYCLE_CLI_ERROR",
            "大环协议命令失败",
            "命令参数或记录文件无效。",
            str(exc),
        )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


status = get_status
review = create_review
confirm = confirm_selection
reset = reset_selection


if __name__ == "__main__":
    raise SystemExit(main())
