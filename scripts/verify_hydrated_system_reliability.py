"""Verify hydrated AutoGrid map reliability through DockStart's project API.

The caller supplies:

* a prepared DockStart project whose hydrated ligand is already valid;
* the exact Python executable used for independent worker processes; and
* the exact external AutoGrid executable.

The source project is never modified.  Every scenario runs against a byte-for-
byte copy inside a system temporary directory.  The normal verifier always
uses the public ``generate_hydrated_maps`` API with a 126 x 126 x 126 grid and
0.375 Angstrom spacing.  Unit tests may call the internal helpers with a small
grid and a clearly labelled simulated runner.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import autogrid_adapter  # noqa: E402
from dockstart_core.autogrid import (  # noqa: E402
    HYDRATED_PROTOCOL_ID,
    autogrid_version_supported,
)
from dockstart_core.hydrated import (  # noqa: E402
    HYDRATED_MAPS_MANIFEST_NAME,
    generate_hydrated_maps,
    get_status as get_hydrated_status,
)
from dockstart_core.hydrated_maps import parse_autogrid_map  # noqa: E402
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    load_settings,
    save_settings,
)
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR  # noqa: E402

VERIFIER_ID = "dockstart_hydrated_system_reliability_v1"
SCHEMA_VERSION = 1
MAX_GRID_POINTS = 126
GRID_SPACING = 0.375
DEFAULT_MINIMUM_FREE_GIB = 4.0
DEFAULT_WORKER_TIMEOUT_SECONDS = 2400.0
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

REAL_PROJECT_API_EVIDENCE = "real_external_tool_project_api"
REAL_PROCESS_EVIDENCE = "real_os_process_termination"
SIMULATED_RUNNER_EVIDENCE = "simulated_runner_project_api"
SIMULATED_FAULT_EVIDENCE = "simulated_fault_injection"
OBSERVED_HOST_EVIDENCE = "observed_host_precondition"
NOT_EXECUTED_EVIDENCE = "not_executed"

Runner = Callable[..., dict[str, Any]]


class ReliabilityVerificationError(RuntimeError):
    """A structured verifier failure."""

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


def _fail(
    condition: bool,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    if not condition:
        raise ReliabilityVerificationError(code, message, details=details)


def _now_epoch() -> float:
    return time.time()


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReliabilityVerificationError(
            "RELIABILITY_JSON_INVALID",
            f"{label} is not readable JSON.",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    _fail(
        isinstance(value, dict),
        "RELIABILITY_JSON_INVALID",
        f"{label} must be a JSON object.",
        details={"path": str(path)},
    )
    return value


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_project_artifact(
    project_root: Path,
    relative: str,
    *,
    label: str,
    allow_empty: bool = False,
) -> Path:
    supplied = Path(relative)
    _fail(
        bool(relative) and not supplied.is_absolute(),
        "RELIABILITY_ARTIFACT_PATH_INVALID",
        f"{label} path must be a non-empty project-relative path.",
        details={"path": relative},
    )
    path = (project_root / supplied).resolve(strict=False)
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ReliabilityVerificationError(
            "RELIABILITY_ARTIFACT_PATH_ESCAPE",
            f"{label} escapes the temporary project.",
            details={"path": relative},
        ) from exc
    _fail(
        path.is_file() and not path.is_symlink(),
        "RELIABILITY_ARTIFACT_MISSING",
        f"{label} is not a regular file.",
        details={"path": str(path)},
    )
    _fail(
        allow_empty or path.stat().st_size > 0,
        "RELIABILITY_ARTIFACT_EMPTY",
        f"{label} is empty.",
        details={"path": str(path)},
    )
    return path


def _tree_snapshot(root: Path) -> dict[str, Any]:
    _fail(
        root.is_dir() and not root.is_symlink(),
        "RELIABILITY_PROJECT_DIRECTORY_INVALID",
        "The project snapshot root must be a regular directory.",
        details={"path": str(root)},
    )
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        _fail(
            not path.is_symlink(),
            "RELIABILITY_PROJECT_SYMLINK_REJECTED",
            "Project snapshots containing symlinks or reparse links are rejected.",
            details={"path": str(path)},
        )
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    identity = {
        "file_count": len(files),
        "total_bytes": sum(int(item["size_bytes"]) for item in files),
        "files": files,
    }
    identity["tree_sha256"] = _canonical_sha256(files)
    return identity


def _tree_total_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        _fail(
            not path.is_symlink(),
            "RELIABILITY_PROJECT_SYMLINK_REJECTED",
            "Temporary project statistics reject symlinks or reparse links.",
            details={"path": str(path)},
        )
        if path.is_file():
            total += path.stat().st_size
    return total


def _copy_verified_tree(source: Path, destination: Path) -> dict[str, Any]:
    before = _tree_snapshot(source)
    shutil.copytree(source, destination)
    source_after = _tree_snapshot(source)
    copied = _tree_snapshot(destination)
    _fail(
        before["tree_sha256"] == source_after["tree_sha256"],
        "RELIABILITY_SOURCE_PROJECT_CHANGED",
        "The source project changed while its temporary snapshot was created.",
        details={
            "before": before["tree_sha256"],
            "after": source_after["tree_sha256"],
        },
    )
    _fail(
        before["tree_sha256"] == copied["tree_sha256"],
        "RELIABILITY_PROJECT_COPY_MISMATCH",
        "The temporary project snapshot is not byte-identical to its source.",
        details={
            "source": before["tree_sha256"],
            "copy": copied["tree_sha256"],
        },
    )
    return {
        "source": str(source),
        "destination": str(destination),
        "file_count": before["file_count"],
        "total_bytes": before["total_bytes"],
        "tree_sha256": before["tree_sha256"],
    }


def _map_set_ids(project_root: Path) -> set[str]:
    maps_root = project_root / "maps"
    if not maps_root.is_dir():
        return set()
    return {
        item.name
        for item in maps_root.iterdir()
        if item.is_dir()
        and not item.is_symlink()
        and re.fullmatch(r"hydrated_\d{3,}", item.name)
    }


def _active_maps_pointer(project_root: Path) -> dict[str, Any] | None:
    project = _read_json_object(project_root / "project.json", label="project.json")
    state = project.get("hydrated_docking")
    state = state if isinstance(state, Mapping) else {}
    pointer = state.get("active_maps_manifest")
    if isinstance(pointer, Mapping):
        return dict(pointer)
    if isinstance(pointer, str) and pointer:
        return {"path": pointer}
    return None


def _compact_api_result(result: Mapping[str, Any]) -> dict[str, Any]:
    error = result.get("error")
    error = dict(error) if isinstance(error, Mapping) else None
    return {
        "ok": result.get("ok") is True,
        "map_set_id": str(result.get("map_set_id") or ""),
        "manifest_file": str(result.get("manifest_file") or ""),
        "manifest_sha256": str(result.get("manifest_sha256") or ""),
        "active_maps_manifest": (
            dict(result["active_maps_manifest"])
            if isinstance(result.get("active_maps_manifest"), Mapping)
            else None
        ),
        "error": error,
    }


def _fault_evidence(
    scenario: str,
    *,
    mechanism: str = "none",
    observed: bool = False,
    note: str = "",
) -> dict[str, Any]:
    if mechanism == "real_os":
        level = REAL_PROCESS_EVIDENCE if observed else NOT_EXECUTED_EVIDENCE
    elif mechanism == "injected":
        level = SIMULATED_FAULT_EVIDENCE
    else:
        level = NOT_EXECUTED_EVIDENCE
    return {
        "scenario": scenario,
        "evidence_level": level,
        "observed": bool(observed),
        "mechanism": mechanism,
        "note": note,
        "real_system_fault_claimed": level == REAL_PROCESS_EVIDENCE,
    }


def _disk_budget_evidence(
    root: Path,
    *,
    minimum_free_bytes: int,
) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    passed = usage.free >= minimum_free_bytes
    evidence = {
        "evidence_level": OBSERVED_HOST_EVIDENCE,
        "scope": "verifier_preflight_only",
        "core_preflight_implemented_or_proven": False,
        "filesystem_root": str(root.anchor or root),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "minimum_free_bytes": minimum_free_bytes,
        "passed": passed,
    }
    _fail(
        passed,
        "RELIABILITY_DISK_BUDGET_INSUFFICIENT",
        "The temporary filesystem does not meet the verifier free-space budget.",
        details=evidence,
    )
    return evidence


def _validate_python_executable(path: str | Path) -> dict[str, Any]:
    executable = Path(path).expanduser().resolve(strict=False)
    _fail(
        executable.is_file() and not executable.is_symlink(),
        "RELIABILITY_PYTHON_INVALID",
        "The explicitly supplied Python executable is not a regular file.",
        details={"path": str(executable)},
    )
    command = [
        str(executable),
        "-c",
        (
            "import json,platform,sys;"
            "print(json.dumps({'executable':sys.executable,"
            "'version':platform.python_version()}))"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReliabilityVerificationError(
            "RELIABILITY_PYTHON_PROBE_FAILED",
            "The explicitly supplied Python executable could not be probed.",
            details={"path": str(executable), "error": str(exc)},
        ) from exc
    _fail(
        completed.returncode == 0,
        "RELIABILITY_PYTHON_PROBE_FAILED",
        "The explicitly supplied Python executable returned an error.",
        details={
            "path": str(executable),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
    )
    try:
        probe = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ReliabilityVerificationError(
            "RELIABILITY_PYTHON_PROBE_INVALID",
            "The Python probe did not return valid JSON.",
            details={"stdout": completed.stdout},
        ) from exc
    _fail(
        isinstance(probe, Mapping)
        and _normalized_path(str(probe.get("executable") or ""))
        == _normalized_path(executable),
        "RELIABILITY_PYTHON_PATH_SUBSTITUTED",
        "The Python process did not preserve the caller-supplied executable path.",
        details={"supplied": str(executable), "probe": dict(probe or {})},
    )
    try:
        executable_sha256 = _sha256_file(executable)
    except OSError as exc:
        raise ReliabilityVerificationError(
            "RELIABILITY_PYTHON_IDENTITY_UNREADABLE",
            "Python started successfully, but its executable bytes could not be hashed.",
            details={"path": str(executable), "error": str(exc)},
        ) from exc
    return {
        "path": str(executable),
        "version": str(probe.get("version") or ""),
        "size_bytes": executable.stat().st_size,
        "sha256": executable_sha256,
        "probe_command": command,
        "caller_path_preserved": True,
    }


def _validate_autogrid_executable(path: str | Path) -> dict[str, Any]:
    executable = Path(path).expanduser().resolve(strict=False)
    _fail(
        executable.is_file() and not executable.is_symlink(),
        "RELIABILITY_AUTOGRID_INVALID",
        "The explicitly supplied AutoGrid executable is not a regular file.",
        details={"path": str(executable)},
    )
    detection = autogrid_adapter.detect(str(executable))
    details = detection.to_dict()
    _fail(
        detection.status == "ok"
        and bool(detection.path)
        and _normalized_path(detection.path) == _normalized_path(executable),
        "RELIABILITY_AUTOGRID_DETECTION_FAILED",
        "AutoGrid detection did not preserve the caller-supplied executable.",
        details=details,
    )
    _fail(
        not detection.is_bundled,
        "RELIABILITY_AUTOGRID_MUST_BE_EXTERNAL",
        "The reliability verifier requires a caller-supplied external AutoGrid.",
        details=details,
    )
    _fail(
        autogrid_version_supported(detection.version, HYDRATED_PROTOCOL_ID),
        "RELIABILITY_AUTOGRID_VERSION_UNSUPPORTED",
        "The supplied AutoGrid version does not pass the hydrated protocol gate.",
        details=details,
    )
    return {
        "path": str(executable),
        "version": detection.version,
        "source": detection.source,
        "is_bundled": detection.is_bundled,
        "size_bytes": executable.stat().st_size,
        "sha256": _sha256_file(executable),
        "caller_path_preserved": True,
        "version_gate_kind": "minimum_version_not_validation_matrix",
    }


@contextlib.contextmanager
def _isolated_tool_environment(
    work_root: Path,
    *,
    python_executable: Path,
    autogrid_executable: Path,
):
    settings_path = work_root / "dockstart_settings.json"
    isolated_resources = work_root / "no_bundled_toolchain"
    previous_settings = os.environ.get(SETTINGS_ENV_VAR)
    previous_resources = os.environ.get(RESOURCE_DIR_ENV_VAR)
    os.environ[SETTINGS_ENV_VAR] = str(settings_path)
    os.environ[RESOURCE_DIR_ENV_VAR] = str(isolated_resources)
    save_settings(
        DockStartSettings(
            tool_paths=ToolPaths(
                python=str(python_executable),
                autogrid4=str(autogrid_executable),
            )
        )
    )
    try:
        yield {
            "settings_path": str(settings_path),
            "resource_dir": str(isolated_resources),
            "caller_paths_only": True,
        }
    finally:
        if previous_settings is None:
            os.environ.pop(SETTINGS_ENV_VAR, None)
        else:
            os.environ[SETTINGS_ENV_VAR] = previous_settings
        if previous_resources is None:
            os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
        else:
            os.environ[RESOURCE_DIR_ENV_VAR] = previous_resources


def _current_rss_bytes() -> int | None:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        ):
            return int(counters.WorkingSetSize)
        return None
    if sys.platform.startswith("linux"):
        try:
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return pages * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            return None
    return None


class _MemorySampler:
    def __init__(self) -> None:
        self.baseline = _current_rss_bytes()
        self.peak = self.baseline
        self.samples = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            value = _current_rss_bytes()
            if value is not None:
                self.peak = value if self.peak is None else max(self.peak, value)
                self.samples += 1
            self._stop.wait(0.01)

    def __enter__(self) -> "_MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": "verifier_python_process_only",
            "sampling_interval_seconds": 0.01,
            "baseline_rss_bytes": self.baseline,
            "sampled_peak_rss_bytes": self.peak,
            "sampled_rss_delta_bytes": (
                max(0, self.peak - self.baseline)
                if self.peak is not None and self.baseline is not None
                else None
            ),
            "sample_count": self.samples,
            "includes_autogrid_child": False,
        }


def _validate_prepared_project(project_root: Path) -> dict[str, Any]:
    _fail(
        (project_root / "project.json").is_file(),
        "RELIABILITY_SOURCE_PROJECT_INVALID",
        "The source project does not contain project.json.",
        details={"path": str(project_root)},
    )
    status = get_hydrated_status(str(project_root))
    _fail(
        status.get("ok") is True
        and status.get("preparation_ready") is True
        and status.get("valid") is True,
        "RELIABILITY_HYDRATED_LIGAND_NOT_READY",
        "The source project must contain a valid active hydrated ligand.",
        details={
            "status": status.get("status"),
            "issues": status.get("issues"),
        },
    )
    incomplete: list[str] = []
    maps_root = project_root / "maps"
    if maps_root.is_dir():
        for item in maps_root.glob("hydrated_*"):
            if item.is_dir() and not (
                item / HYDRATED_MAPS_MANIFEST_NAME
            ).is_file():
                incomplete.append(item.name)
    _fail(
        not incomplete,
        "RELIABILITY_SOURCE_PROJECT_HAS_INCOMPLETE_MAPS",
        "The source project contains incomplete hydrated map directories.",
        details={"map_set_ids": sorted(incomplete)},
    )
    return {
        "preparation_ready": True,
        "active_ligand_manifest": status.get("active_ligand_manifest"),
        "active_maps_manifest": status.get("active_maps_manifest"),
        "existing_map_set_ids": sorted(_map_set_ids(project_root)),
    }


def _audit_published_manifest(
    project_root: Path,
    api_result: Mapping[str, Any],
    *,
    expected_grid_points: int,
    expected_spacing: float,
    expected_autogrid: Path | None,
    require_active: bool,
) -> dict[str, Any]:
    compact = _compact_api_result(api_result)
    _fail(
        compact["ok"],
        "RELIABILITY_MAP_GENERATION_FAILED",
        "The hydrated map project API did not succeed.",
        details=compact,
    )
    map_set_id = str(compact["map_set_id"])
    manifest_relative = str(compact["manifest_file"])
    expected_relative = Path(
        "maps",
        map_set_id,
        HYDRATED_MAPS_MANIFEST_NAME,
    ).as_posix()
    _fail(
        bool(map_set_id) and manifest_relative == expected_relative,
        "RELIABILITY_MANIFEST_PATH_INVALID",
        "The published hydrated manifest path is not canonical.",
        details={
            "map_set_id": map_set_id,
            "manifest_file": manifest_relative,
            "expected": expected_relative,
        },
    )
    manifest_path = _safe_project_artifact(
        project_root,
        manifest_relative,
        label="hydrated maps manifest",
    )
    manifest_sha256 = _sha256_file(manifest_path)
    manifest = _read_json_object(
        manifest_path,
        label="hydrated maps manifest",
    )
    _fail(
        manifest.get("status") == "ready"
        and manifest.get("protocol_id") == HYDRATED_PROTOCOL_ID
        and manifest.get("map_set_id") == map_set_id,
        "RELIABILITY_MANIFEST_NOT_READY",
        "The hydrated maps manifest is not a ready record for this map set.",
        details={
            "status": manifest.get("status"),
            "protocol_id": manifest.get("protocol_id"),
            "map_set_id": manifest.get("map_set_id"),
        },
    )
    _fail(
        SHA256_PATTERN.fullmatch(manifest_sha256) is not None
        and manifest_sha256 == str(compact["manifest_sha256"]),
        "RELIABILITY_MANIFEST_HASH_MISMATCH",
        "The project API manifest hash does not match published bytes.",
        details={
            "api": compact["manifest_sha256"],
            "actual": manifest_sha256,
        },
    )

    maps = manifest.get("maps")
    maps = maps if isinstance(maps, Mapping) else {}
    geometry = maps.get("geometry")
    geometry = geometry if isinstance(geometry, Mapping) else {}
    nelements = geometry.get("nelements")
    nelements = nelements if isinstance(nelements, Mapping) else {}
    _fail(
        all(
            int(nelements.get(axis) or -1) == expected_grid_points
            for axis in ("x", "y", "z")
        )
        and math.isclose(
            float(geometry.get("spacing") or 0.0),
            expected_spacing,
            rel_tol=0.0,
            abs_tol=1e-9,
        ),
        "RELIABILITY_GRID_GEOMETRY_MISMATCH",
        "The published map geometry does not match the requested reliability grid.",
        details={
            "expected_grid_points": expected_grid_points,
            "expected_spacing": expected_spacing,
            "geometry": dict(geometry),
        },
    )
    coverage = manifest.get("grid_coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    _fail(
        coverage.get("covers_requested_box") is True
        and SHA256_PATTERN.fullmatch(
            str(manifest.get("grid_coverage_sha256") or "")
        )
        is not None,
        "RELIABILITY_GRID_COVERAGE_INVALID",
        "The published manifest lacks a valid requested-box coverage contract.",
        details={"grid_coverage": dict(coverage)},
    )

    file_records = maps.get("files")
    file_records = file_records if isinstance(file_records, list) else []
    required_names = maps.get("required_files")
    required_names = required_names if isinstance(required_names, list) else []
    recorded_names = [
        str(item.get("name") or "")
        for item in file_records
        if isinstance(item, Mapping)
    ]
    _fail(
        bool(file_records)
        and len(recorded_names) == len(set(recorded_names))
        and set(recorded_names) == {str(item) for item in required_names}
        and "receptor.W.map" in recorded_names,
        "RELIABILITY_MAP_FILE_SET_INVALID",
        "The published map file set is incomplete or contains duplicate names.",
        details={
            "required": required_names,
            "recorded": recorded_names,
        },
    )
    audited_files: list[dict[str, Any]] = []
    for item in file_records:
        _fail(
            isinstance(item, Mapping),
            "RELIABILITY_MAP_RECORD_INVALID",
            "A map file record is not an object.",
        )
        relative = str(item.get("relative_path") or item.get("path") or "")
        path = _safe_project_artifact(
            project_root,
            relative,
            label=f"map {item.get('name') or ''}",
        )
        actual_sha256 = _sha256_file(path)
        actual_size = path.stat().st_size
        _fail(
            actual_sha256 == str(item.get("sha256") or "")
            and actual_size == int(item.get("size_bytes") or -1),
            "RELIABILITY_MAP_HASH_MISMATCH",
            "A published map does not match its manifest.",
            details={
                "name": item.get("name"),
                "relative_path": relative,
                "actual_sha256": actual_sha256,
                "recorded_sha256": item.get("sha256"),
                "actual_size": actual_size,
                "recorded_size": item.get("size_bytes"),
            },
        )
        audited_files.append(
            {
                "name": str(item.get("name") or ""),
                "relative_path": relative,
                "size_bytes": actual_size,
                "sha256": actual_sha256,
            }
        )

    water_record = maps.get("water_map")
    water_record = water_record if isinstance(water_record, Mapping) else {}
    water_path = _safe_project_artifact(
        project_root,
        str(
            water_record.get("relative_path")
            or water_record.get("path")
            or ""
        ),
        label="W map",
    )
    parsed_water = parse_autogrid_map(water_path)
    expected_value_count = (expected_grid_points + 1) ** 3
    _fail(
        parsed_water.geometry.point_count == expected_value_count,
        "RELIABILITY_W_MAP_POINT_COUNT_MISMATCH",
        "The W map point count does not match the requested npts.",
        details={
            "expected": expected_value_count,
            "actual": parsed_water.geometry.point_count,
        },
    )

    base_record = manifest.get("base_maps_manifest")
    base_record = base_record if isinstance(base_record, Mapping) else {}
    base_relative = str(
        base_record.get("relative_path") or base_record.get("path") or ""
    )
    base_path = _safe_project_artifact(
        project_root,
        base_relative,
        label="base maps manifest",
    )
    _fail(
        _sha256_file(base_path) == str(base_record.get("sha256") or ""),
        "RELIABILITY_BASE_MANIFEST_HASH_MISMATCH",
        "The hydrated manifest does not bind the current base manifest bytes.",
    )
    base_manifest = _read_json_object(base_path, label="base maps manifest")
    gpf_record = base_manifest.get("gpf")
    gpf_record = gpf_record if isinstance(gpf_record, Mapping) else {}
    gpf_relative = str(
        gpf_record.get("relative_path") or gpf_record.get("path") or ""
    )
    gpf_path = _safe_project_artifact(
        project_root,
        gpf_relative,
        label="AutoGrid GPF",
    )
    gpf_lines = gpf_path.read_text(encoding="utf-8").splitlines()
    _fail(
        f"npts {expected_grid_points} {expected_grid_points} "
        f"{expected_grid_points}" in gpf_lines
        and any(
            line.startswith("spacing ")
            and math.isclose(
                float(line.split()[1]),
                expected_spacing,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for line in gpf_lines
        ),
        "RELIABILITY_GPF_GRID_MISMATCH",
        "The AutoGrid GPF does not contain the requested reliability grid.",
        details={"gpf": gpf_relative},
    )
    autogrid = base_manifest.get("autogrid")
    autogrid = autogrid if isinstance(autogrid, Mapping) else {}
    if expected_autogrid is not None:
        _fail(
            _normalized_path(str(autogrid.get("path") or ""))
            == _normalized_path(expected_autogrid)
            and isinstance(autogrid.get("command"), list)
            and bool(autogrid.get("command"))
            and _normalized_path(str(autogrid["command"][0]))
            == _normalized_path(expected_autogrid),
            "RELIABILITY_AUTOGRID_PATH_SUBSTITUTED",
            "The project manifest did not preserve the supplied AutoGrid path.",
            details={
                "expected": str(expected_autogrid),
                "recorded": dict(autogrid),
            },
        )

    active_pointer = _active_maps_pointer(project_root)
    if require_active:
        _fail(
            isinstance(active_pointer, Mapping)
            and active_pointer.get("path") == manifest_relative
            and active_pointer.get("sha256") == manifest_sha256
            and active_pointer.get("grid_coverage_sha256")
            == manifest.get("grid_coverage_sha256"),
            "RELIABILITY_ACTIVE_POINTER_INVALID",
            "project.json does not point to the complete generated manifest.",
            details={
                "active_pointer": active_pointer,
                "manifest_file": manifest_relative,
            },
        )
        status = get_hydrated_status(str(project_root))
        _fail(
            status.get("ok") is True
            and status.get("maps_ready") is True
            and status.get("active_maps_manifest") == manifest_relative,
            "RELIABILITY_ACTIVE_STATUS_INVALID",
            "The public hydrated status API did not validate the active maps.",
            details={
                "maps_status": status.get("maps_status"),
                "maps_issues": status.get("maps_issues"),
                "active_maps_manifest": status.get("active_maps_manifest"),
            },
        )

    audit_records = manifest.get("audit_files")
    audit_records = audit_records if isinstance(audit_records, list) else []
    audit_bytes = 0
    for item in audit_records:
        if not isinstance(item, Mapping):
            continue
        relative = str(item.get("relative_path") or item.get("path") or "")
        path = _safe_project_artifact(
            project_root,
            relative,
            label=f"audit file {item.get('name') or ''}",
            allow_empty=True,
        )
        _fail(
            path.stat().st_size == int(item.get("size_bytes") or 0)
            and _sha256_file(path) == str(item.get("sha256") or ""),
            "RELIABILITY_AUDIT_FILE_MISMATCH",
            "An AutoGrid audit file does not match its manifest.",
            details={"path": relative},
        )
        audit_bytes += path.stat().st_size

    return {
        "map_set_id": map_set_id,
        "manifest_file": manifest_relative,
        "manifest_sha256": manifest_sha256,
        "manifest_status": manifest.get("status"),
        "grid": {
            "npts": [expected_grid_points] * 3,
            "spacing_angstrom": expected_spacing,
            "values_per_map": expected_value_count,
            "effective_span_angstrom": expected_grid_points
            * expected_spacing,
            "coverage": dict(coverage),
            "grid_coverage_sha256": manifest.get("grid_coverage_sha256"),
        },
        "maps": {
            "file_count": len(audited_files),
            "total_bytes": sum(item["size_bytes"] for item in audited_files),
            "files": audited_files,
            "water_map": {
                "relative_path": water_path.relative_to(project_root).as_posix(),
                "size_bytes": water_path.stat().st_size,
                "sha256": parsed_water.sha256,
                "point_count": parsed_water.geometry.point_count,
                "statistics": dict(water_record.get("statistics") or {}),
                "parameters": dict(water_record.get("parameters") or {}),
            },
        },
        "base_manifest": {
            "relative_path": base_relative,
            "size_bytes": base_path.stat().st_size,
            "sha256": _sha256_file(base_path),
            "gpf": {
                "relative_path": gpf_relative,
                "size_bytes": gpf_path.stat().st_size,
                "sha256": _sha256_file(gpf_path),
            },
            "autogrid": {
                key: autogrid.get(key)
                for key in (
                    "path",
                    "version",
                    "source",
                    "sha256",
                    "command",
                    "exit_code",
                )
            },
        },
        "audit_file_count": len(audit_records),
        "audit_file_bytes": audit_bytes,
        "active_pointer_verified": require_active,
    }


def _generate_and_audit(
    project_root: Path,
    *,
    grid_points: int,
    spacing: float,
    expected_autogrid: Path | None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    before_ids = _map_set_ids(project_root)
    before_size = _tree_total_bytes(project_root)
    started = time.perf_counter()
    with _MemorySampler() as memory:
        result = generate_hydrated_maps(
            str(project_root),
            {
                "spacing": spacing,
                "grid_points": {
                    "x": grid_points,
                    "y": grid_points,
                    "z": grid_points,
                },
            },
            runner=runner,
        )
    elapsed = time.perf_counter() - started
    compact = _compact_api_result(result)
    _fail(
        compact["ok"],
        "RELIABILITY_MAX_GRID_API_FAILED",
        "The public project API did not publish the requested hydrated maps.",
        details=compact,
    )
    publication = _audit_published_manifest(
        project_root,
        compact,
        expected_grid_points=grid_points,
        expected_spacing=spacing,
        expected_autogrid=expected_autogrid,
        require_active=True,
    )
    after_ids = _map_set_ids(project_root)
    new_ids = after_ids - before_ids
    _fail(
        new_ids == {publication["map_set_id"]},
        "RELIABILITY_UNEXPECTED_MAP_SET_PUBLICATION",
        "A single project API call did not create exactly one unique map set.",
        details={
            "before": sorted(before_ids),
            "after": sorted(after_ids),
            "new": sorted(new_ids),
            "result": publication["map_set_id"],
        },
    )
    after_size = _tree_total_bytes(project_root)
    return {
        "evidence_level": (
            REAL_PROJECT_API_EVIDENCE
            if runner is None
            else SIMULATED_RUNNER_EVIDENCE
        ),
        "public_api": "dockstart_core.hydrated.generate_hydrated_maps",
        "wall_seconds": elapsed,
        "project_bytes_before": before_size,
        "project_bytes_after": after_size,
        "project_bytes_added": after_size - before_size,
        "memory": memory.to_dict(),
        "publication": publication,
    }


def _signalable_autogrid_runner(marker_path: Path) -> Runner:
    def run(
        executable: str,
        gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        *,
        timeout_seconds: int = 1800,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        command = [executable, "-p", gpf_file, "-l", log_file]
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return {
                "ok": False,
                "command": command,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": str(exc),
            }
        _atomic_write_json(
            marker_path,
            {
                "pid": process.pid,
                "command": command,
                "working_directory": str(Path(working_directory).resolve()),
                "started_at_epoch": _now_epoch(),
            },
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return {
                "ok": False,
                "command": command,
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "error": f"AutoGrid exceeded {timeout_seconds} seconds.",
            }
        return {
            "ok": process.returncode == 0,
            "command": command,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": (
                ""
                if process.returncode == 0
                else f"AutoGrid was terminated with exit code {process.returncode}."
            ),
        }

    return run


def _worker_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--autogrid", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--grid-points", type=int, required=True)
    parser.add_argument("--spacing", type=float, required=True)
    parser.add_argument("--ready-file")
    parser.add_argument("--start-file")
    parser.add_argument(
        "--runner-mode",
        choices=("adapter", "signalable"),
        default="adapter",
    )
    parser.add_argument("--child-pid-file")
    parser.add_argument("--barrier-timeout", type=float, default=120.0)
    arguments = parser.parse_args(argv)
    result_path = Path(arguments.result).resolve()
    wrapper: dict[str, Any]
    try:
        supplied_autogrid = Path(arguments.autogrid).resolve(strict=False)
        configured_path = load_settings().tool_paths.autogrid4
        configured = autogrid_adapter.detect(configured_path)
        explicit = autogrid_adapter.detect(str(supplied_autogrid))
        _fail(
            configured.status == "ok"
            and explicit.status == "ok"
            and bool(configured.path)
            and bool(explicit.path)
            and _normalized_path(configured.path)
            == _normalized_path(supplied_autogrid)
            and _normalized_path(explicit.path)
            == _normalized_path(supplied_autogrid),
            "RELIABILITY_WORKER_AUTOGRID_SUBSTITUTED",
            "The worker did not resolve the caller-supplied AutoGrid.",
            details={
                "configured": configured.to_dict(),
                "explicit": explicit.to_dict(),
            },
        )
        if arguments.ready_file:
            _atomic_write_json(
                Path(arguments.ready_file),
                {
                    "worker_id": arguments.worker_id,
                    "pid": os.getpid(),
                    "ready_at_epoch": _now_epoch(),
                },
            )
        if arguments.start_file:
            deadline = time.monotonic() + arguments.barrier_timeout
            start_file = Path(arguments.start_file)
            while not start_file.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            _fail(
                start_file.is_file(),
                "RELIABILITY_WORKER_BARRIER_TIMEOUT",
                "The worker did not receive its start barrier.",
            )
        runner: Runner | None = None
        if arguments.runner_mode == "signalable":
            _fail(
                bool(arguments.child_pid_file),
                "RELIABILITY_WORKER_PID_MARKER_REQUIRED",
                "The signalable runner requires a child PID marker.",
            )
            runner = _signalable_autogrid_runner(
                Path(arguments.child_pid_file).resolve()
            )
        started = time.perf_counter()
        result = generate_hydrated_maps(
            str(Path(arguments.project).resolve()),
            {
                "spacing": arguments.spacing,
                "grid_points": {
                    "x": arguments.grid_points,
                    "y": arguments.grid_points,
                    "z": arguments.grid_points,
                },
            },
            runner=runner,
        )
        wrapper = {
            "ok": result.get("ok") is True,
            "worker_id": arguments.worker_id,
            "worker_pid": os.getpid(),
            "runner_mode": arguments.runner_mode,
            "wall_seconds": time.perf_counter() - started,
            "api_result": _compact_api_result(result),
            "exception": None,
        }
    except BaseException as exc:  # noqa: BLE001 - worker audit boundary.
        wrapper = {
            "ok": False,
            "worker_id": arguments.worker_id,
            "worker_pid": os.getpid(),
            "runner_mode": arguments.runner_mode,
            "api_result": None,
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc),
                "code": getattr(exc, "code", ""),
                "details": dict(getattr(exc, "details", {}) or {}),
            },
        }
    _atomic_write_json(result_path, wrapper)
    return 0 if wrapper["ok"] else 2


@dataclass
class _WorkerHandle:
    worker_id: str
    command: list[str]
    process: subprocess.Popen[str]
    result_path: Path
    stdout_path: Path
    stderr_path: Path
    stdout_handle: Any
    stderr_handle: Any


def _launch_worker(
    *,
    python_executable: Path,
    project_root: Path,
    autogrid_executable: Path,
    scenario_root: Path,
    worker_id: str,
    grid_points: int,
    spacing: float,
    start_file: Path | None,
    runner_mode: str = "adapter",
    child_pid_file: Path | None = None,
) -> _WorkerHandle:
    result_path = scenario_root / f"{worker_id}.result.json"
    ready_path = scenario_root / f"{worker_id}.ready.json"
    stdout_path = scenario_root / f"{worker_id}.stdout.txt"
    stderr_path = scenario_root / f"{worker_id}.stderr.txt"
    command = [
        str(python_executable),
        str(Path(__file__).resolve()),
        "_worker",
        "--project",
        str(project_root),
        "--autogrid",
        str(autogrid_executable),
        "--result",
        str(result_path),
        "--worker-id",
        worker_id,
        "--grid-points",
        str(grid_points),
        "--spacing",
        str(spacing),
        "--ready-file",
        str(ready_path),
        "--runner-mode",
        runner_mode,
    ]
    if start_file is not None:
        command.extend(["--start-file", str(start_file)])
    if child_pid_file is not None:
        command.extend(["--child-pid-file", str(child_pid_file)])
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            env=os.environ.copy(),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
        )
    except BaseException:
        stdout_handle.close()
        stderr_handle.close()
        raise
    return _WorkerHandle(
        worker_id=worker_id,
        command=command,
        process=process,
        result_path=result_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_handle=stdout_handle,
        stderr_handle=stderr_handle,
    )


def _wait_for_path(path: Path, *, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    _fail(
        path.is_file(),
        "RELIABILITY_WAIT_TIMEOUT",
        f"Timed out waiting for {label}.",
        details={"path": str(path), "timeout_seconds": timeout_seconds},
    )


def _worker_ready_path(handle: _WorkerHandle) -> Path:
    return handle.result_path.with_name(
        handle.result_path.name.replace(".result.json", ".ready.json")
    )


def _close_worker_logs(handle: _WorkerHandle) -> None:
    if not handle.stdout_handle.closed:
        handle.stdout_handle.close()
    if not handle.stderr_handle.closed:
        handle.stderr_handle.close()


def _collect_worker(
    handle: _WorkerHandle,
    *,
    timeout_seconds: float,
    require_result: bool = True,
) -> dict[str, Any]:
    try:
        exit_code = handle.process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        handle.process.kill()
        handle.process.wait(timeout=10)
        raise ReliabilityVerificationError(
            "RELIABILITY_WORKER_TIMEOUT",
            "A reliability worker exceeded its timeout.",
            details={
                "worker_id": handle.worker_id,
                "command": handle.command,
                "timeout_seconds": timeout_seconds,
            },
        ) from exc
    finally:
        _close_worker_logs(handle)
    if not require_result and not handle.result_path.is_file():
        return {
            "worker_id": handle.worker_id,
            "exit_code": exit_code,
            "result_missing": True,
            "command": handle.command,
            "stdout_file": str(handle.stdout_path),
            "stderr_file": str(handle.stderr_path),
        }
    _fail(
        handle.result_path.is_file(),
        "RELIABILITY_WORKER_RESULT_MISSING",
        "A worker exited without its atomic result record.",
        details={
            "worker_id": handle.worker_id,
            "exit_code": exit_code,
            "stdout": handle.stdout_path.read_text(
                encoding="utf-8",
                errors="replace",
            ),
            "stderr": handle.stderr_path.read_text(
                encoding="utf-8",
                errors="replace",
            ),
        },
    )
    wrapper = _read_json_object(
        handle.result_path,
        label=f"worker {handle.worker_id} result",
    )
    wrapper.update(
        {
            "exit_code": exit_code,
            "command": handle.command,
            "stdout_file": str(handle.stdout_path),
            "stderr_file": str(handle.stderr_path),
        }
    )
    return wrapper


def _audit_competition_publications(
    project_root: Path,
    *,
    before_ids: set[str],
    worker_results: Sequence[Mapping[str, Any]],
    grid_points: int,
    spacing: float,
    expected_autogrid: Path | None,
) -> dict[str, Any]:
    api_results: list[Mapping[str, Any]] = []
    for worker in worker_results:
        api_result = worker.get("api_result")
        _fail(
            worker.get("ok") is True and isinstance(api_result, Mapping),
            "RELIABILITY_COMPETITION_WORKER_FAILED",
            "A competing project API worker did not succeed.",
            details=dict(worker),
        )
        api_results.append(api_result)
    summaries = [
        _audit_published_manifest(
            project_root,
            result,
            expected_grid_points=grid_points,
            expected_spacing=spacing,
            expected_autogrid=expected_autogrid,
            require_active=False,
        )
        for result in api_results
    ]
    result_ids = [str(item["map_set_id"]) for item in summaries]
    _fail(
        len(result_ids) == len(set(result_ids)) == len(worker_results),
        "RELIABILITY_COMPETITION_DUPLICATE_PUBLICATION",
        "Competing workers did not publish unique map set IDs.",
        details={"map_set_ids": result_ids},
    )
    after_ids = _map_set_ids(project_root)
    new_ids = after_ids - before_ids
    _fail(
        new_ids == set(result_ids),
        "RELIABILITY_COMPETITION_EXTRA_PUBLICATION",
        "The competition created an unexpected or incomplete map set directory.",
        details={
            "before": sorted(before_ids),
            "after": sorted(after_ids),
            "new": sorted(new_ids),
            "result_ids": sorted(result_ids),
        },
    )
    active = _active_maps_pointer(project_root)
    manifest_paths = {str(item["manifest_file"]) for item in summaries}
    _fail(
        isinstance(active, Mapping)
        and str(active.get("path") or "") in manifest_paths,
        "RELIABILITY_COMPETITION_ACTIVE_POINTER_INVALID",
        "The final active pointer is not one of the complete competing publications.",
        details={
            "active_pointer": active,
            "complete_manifests": sorted(manifest_paths),
        },
    )
    active_summary = next(
        item for item in summaries if item["manifest_file"] == active["path"]
    )
    _fail(
        active.get("sha256") == active_summary["manifest_sha256"],
        "RELIABILITY_COMPETITION_ACTIVE_HASH_INVALID",
        "The competition active pointer hash does not match manifest bytes.",
    )
    status = get_hydrated_status(str(project_root))
    _fail(
        status.get("maps_ready") is True
        and status.get("active_maps_manifest") == active["path"],
        "RELIABILITY_COMPETITION_STATUS_INVALID",
        "The public status API rejected the final competing publication.",
        details={"maps_issues": status.get("maps_issues")},
    )
    return {
        "unique_publication_count": len(summaries),
        "map_set_ids": result_ids,
        "publications": summaries,
        "active_pointer": dict(active),
        "only_complete_new_directories": True,
    }


def _audit_competition_failure_state(
    project_root: Path,
    *,
    before_ids: set[str],
    before_pointer: Mapping[str, Any] | None,
    worker_results: Sequence[Mapping[str, Any]],
    grid_points: int,
    spacing: float,
    expected_autogrid: Path,
) -> dict[str, Any]:
    successful: list[dict[str, Any]] = []
    for worker in worker_results:
        api_result = worker.get("api_result")
        if worker.get("ok") is True and isinstance(api_result, Mapping):
            successful.append(
                _audit_published_manifest(
                    project_root,
                    api_result,
                    expected_grid_points=grid_points,
                    expected_spacing=spacing,
                    expected_autogrid=expected_autogrid,
                    require_active=False,
                )
            )
    success_ids = {str(item["map_set_id"]) for item in successful}
    after_ids = _map_set_ids(project_root)
    new_ids = after_ids - before_ids
    active = _active_maps_pointer(project_root)
    status = get_hydrated_status(str(project_root))
    complete_paths = {str(item["manifest_file"]) for item in successful}
    no_unexpected_directory = new_ids == success_ids
    if successful:
        active_safe = (
            isinstance(active, Mapping)
            and str(active.get("path") or "") in complete_paths
            and status.get("maps_ready") is True
        )
    else:
        active_safe = active == before_pointer
    directories: list[dict[str, Any]] = []
    for map_set_id in sorted(new_ids):
        manifest_path = (
            project_root
            / "maps"
            / map_set_id
            / HYDRATED_MAPS_MANIFEST_NAME
        )
        directories.append(
            {
                "map_set_id": map_set_id,
                "hydrated_manifest_exists": manifest_path.is_file(),
                "status": (
                    _read_json_object(
                        manifest_path,
                        label=f"competition map set {map_set_id}",
                    ).get("status")
                    if manifest_path.is_file()
                    else None
                ),
            }
        )
    return {
        "successful_publication_count": len(successful),
        "successful_publications": successful,
        "new_map_set_ids": sorted(new_ids),
        "directories": directories,
        "active_pointer_before": (
            dict(before_pointer) if isinstance(before_pointer, Mapping) else None
        ),
        "active_pointer_after": (
            dict(active) if isinstance(active, Mapping) else None
        ),
        "public_status": {
            "maps_ready": status.get("maps_ready"),
            "maps_status": status.get("maps_status"),
            "maps_issues": status.get("maps_issues"),
        },
        "only_successful_publications_created_directories": (
            no_unexpected_directory
        ),
        "active_pointer_not_corrupted": active_safe,
        "state_integrity_preserved": no_unexpected_directory and active_safe,
    }


def _terminate_pid(pid: int, *, target_kind: str) -> dict[str, Any]:
    _fail(
        pid > 0 and pid != os.getpid(),
        "RELIABILITY_TERMINATION_PID_INVALID",
        "Refusing to terminate an invalid or current-process PID.",
        details={"pid": pid},
    )
    started = time.perf_counter()
    if os.name == "nt":
        process_terminate = 0x0001
        synchronize = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_terminate | synchronize,
            False,
            pid,
        )
        _fail(
            bool(handle),
            "RELIABILITY_TERMINATION_OPEN_FAILED",
            "Could not open the recorded process for termination.",
            details={"pid": pid, "target_kind": target_kind},
        )
        try:
            terminated = kernel32.TerminateProcess(handle, 130)
            waited = kernel32.WaitForSingleObject(handle, 10000)
            verified = bool(terminated) and waited == 0
        finally:
            kernel32.CloseHandle(handle)
        method = "TerminateProcess"
    else:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10
        verified = False
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                verified = True
                break
            time.sleep(0.02)
        if not verified:
            os.kill(pid, signal.SIGKILL)
        method = "SIGTERM" if verified else "SIGTERM_then_SIGKILL"
        if not verified:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    verified = True
                    break
                time.sleep(0.02)
    _fail(
        verified,
        "RELIABILITY_TERMINATION_NOT_VERIFIED",
        "The recorded process could not be verified as terminated.",
        details={"pid": pid, "target_kind": target_kind, "method": method},
    )
    return {
        "evidence_level": REAL_PROCESS_EVIDENCE,
        "target_kind": target_kind,
        "pid": pid,
        "method": method,
        "verified_exited": True,
        "wall_seconds": time.perf_counter() - started,
    }


def _start_barrier_workers(
    handles: Sequence[_WorkerHandle],
    start_file: Path,
    *,
    timeout_seconds: float,
) -> None:
    for handle in handles:
        _wait_for_path(
            _worker_ready_path(handle),
            timeout_seconds=timeout_seconds,
            label=f"worker {handle.worker_id} readiness",
        )
    start_file.write_text("start\n", encoding="ascii")


def _run_competition_scenario(
    project_root: Path,
    *,
    python_executable: Path,
    autogrid_executable: Path,
    scenario_root: Path,
    timeout_seconds: float,
    grid_points: int,
    spacing: float,
) -> dict[str, Any]:
    before_ids = _map_set_ids(project_root)
    before_pointer = _active_maps_pointer(project_root)
    start_file = scenario_root / "competition.start"
    handles = [
        _launch_worker(
            python_executable=python_executable,
            project_root=project_root,
            autogrid_executable=autogrid_executable,
            scenario_root=scenario_root,
            worker_id=f"competition_{index}",
            grid_points=grid_points,
            spacing=spacing,
            start_file=start_file,
        )
        for index in (1, 2)
    ]
    started = time.perf_counter()
    try:
        _start_barrier_workers(
            handles,
            start_file,
            timeout_seconds=min(timeout_seconds, 120),
        )
        results = [
            _collect_worker(handle, timeout_seconds=timeout_seconds)
            for handle in handles
        ]
    finally:
        for handle in handles:
            if handle.process.poll() is None:
                handle.process.kill()
                handle.process.wait(timeout=10)
            _close_worker_logs(handle)
    try:
        audit = _audit_competition_publications(
            project_root,
            before_ids=before_ids,
            worker_results=results,
            grid_points=grid_points,
            spacing=spacing,
            expected_autogrid=autogrid_executable,
        )
    except ReliabilityVerificationError as exc:
        failure_state = _audit_competition_failure_state(
            project_root,
            before_ids=before_ids,
            before_pointer=before_pointer,
            worker_results=results,
            grid_points=grid_points,
            spacing=spacing,
            expected_autogrid=autogrid_executable,
        )
        raise ReliabilityVerificationError(
            "RELIABILITY_PROCESS_COMPETITION_FAILED",
            "Independent project API workers did not both complete safely.",
            details={
                "evidence_level": REAL_PROJECT_API_EVIDENCE,
                "process_model": "two_independent_python_processes",
                "real_autogrid": True,
                "cause": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "workers": results,
                "post_failure_state": failure_state,
            },
        ) from exc
    return {
        "evidence_level": REAL_PROJECT_API_EVIDENCE,
        "process_model": "two_independent_python_processes",
        "real_autogrid": True,
        "barrier_used": True,
        "wall_seconds": time.perf_counter() - started,
        "workers": results,
        **audit,
    }


def _run_single_worker(
    project_root: Path,
    *,
    python_executable: Path,
    autogrid_executable: Path,
    scenario_root: Path,
    worker_id: str,
    timeout_seconds: float,
    grid_points: int,
    spacing: float,
    runner_mode: str = "adapter",
    child_pid_file: Path | None = None,
) -> tuple[_WorkerHandle, Path]:
    start_file = scenario_root / f"{worker_id}.start"
    handle = _launch_worker(
        python_executable=python_executable,
        project_root=project_root,
        autogrid_executable=autogrid_executable,
        scenario_root=scenario_root,
        worker_id=worker_id,
        grid_points=grid_points,
        spacing=spacing,
        start_file=start_file,
        runner_mode=runner_mode,
        child_pid_file=child_pid_file,
    )
    _start_barrier_workers(
        [handle],
        start_file,
        timeout_seconds=min(timeout_seconds, 120),
    )
    return handle, start_file


def _assert_pointer_unchanged(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    scenario: str,
) -> None:
    _fail(
        before == after,
        "RELIABILITY_FAILED_RUN_CHANGED_ACTIVE_POINTER",
        f"{scenario} changed the active pointer before a successful retry.",
        details={"before": before, "after": after},
    )


def _run_child_termination_scenario(
    project_root: Path,
    *,
    python_executable: Path,
    autogrid_executable: Path,
    scenario_root: Path,
    timeout_seconds: float,
    grid_points: int,
    spacing: float,
) -> dict[str, Any]:
    before_pointer = _active_maps_pointer(project_root)
    child_marker = scenario_root / "terminated_child.pid.json"
    handle, _ = _run_single_worker(
        project_root,
        python_executable=python_executable,
        autogrid_executable=autogrid_executable,
        scenario_root=scenario_root,
        worker_id="terminate_autogrid_child",
        timeout_seconds=timeout_seconds,
        grid_points=grid_points,
        spacing=spacing,
        runner_mode="signalable",
        child_pid_file=child_marker,
    )
    try:
        _wait_for_path(
            child_marker,
            timeout_seconds=min(timeout_seconds, 120),
            label="real AutoGrid child PID marker",
        )
        marker = _read_json_object(child_marker, label="AutoGrid child PID marker")
        termination = _terminate_pid(
            int(marker.get("pid") or 0),
            target_kind="autogrid_child",
        )
        failed_worker = _collect_worker(
            handle,
            timeout_seconds=timeout_seconds,
        )
    finally:
        if handle.process.poll() is None:
            handle.process.kill()
            handle.process.wait(timeout=10)
        _close_worker_logs(handle)
    _fail(
        failed_worker.get("ok") is False
        and isinstance(failed_worker.get("api_result"), Mapping)
        and failed_worker["api_result"].get("ok") is False,
        "RELIABILITY_TERMINATED_CHILD_NOT_RECORDED_FAILED",
        "Terminating the real AutoGrid child did not produce a failed API record.",
        details=failed_worker,
    )
    after_failure = _active_maps_pointer(project_root)
    _assert_pointer_unchanged(
        before_pointer,
        after_failure,
        scenario="Real AutoGrid child termination",
    )
    retry_handle, _ = _run_single_worker(
        project_root,
        python_executable=python_executable,
        autogrid_executable=autogrid_executable,
        scenario_root=scenario_root,
        worker_id="retry_after_child_termination",
        timeout_seconds=timeout_seconds,
        grid_points=grid_points,
        spacing=spacing,
    )
    retry = _collect_worker(retry_handle, timeout_seconds=timeout_seconds)
    _fail(
        retry.get("ok") is True
        and isinstance(retry.get("api_result"), Mapping),
        "RELIABILITY_CHILD_TERMINATION_RETRY_FAILED",
        "The project could not retry after real AutoGrid child termination.",
        details=retry,
    )
    publication = _audit_published_manifest(
        project_root,
        retry["api_result"],
        expected_grid_points=grid_points,
        expected_spacing=spacing,
        expected_autogrid=autogrid_executable,
        require_active=True,
    )
    return {
        "evidence_level": REAL_PROCESS_EVIDENCE,
        "termination": termination,
        "failed_worker": failed_worker,
        "active_pointer_preserved_until_retry": True,
        "retry_worker": retry,
        "retry_publication": publication,
    }


def _run_worker_termination_scenario(
    project_root: Path,
    *,
    python_executable: Path,
    autogrid_executable: Path,
    scenario_root: Path,
    timeout_seconds: float,
    grid_points: int,
    spacing: float,
) -> dict[str, Any]:
    before_ids = _map_set_ids(project_root)
    before_pointer = _active_maps_pointer(project_root)
    child_marker = scenario_root / "orphaned_child.pid.json"
    handle, _ = _run_single_worker(
        project_root,
        python_executable=python_executable,
        autogrid_executable=autogrid_executable,
        scenario_root=scenario_root,
        worker_id="terminate_worker",
        timeout_seconds=timeout_seconds,
        grid_points=grid_points,
        spacing=spacing,
        runner_mode="signalable",
        child_pid_file=child_marker,
    )
    _wait_for_path(
        child_marker,
        timeout_seconds=min(timeout_seconds, 120),
        label="worker-owned AutoGrid PID marker",
    )
    marker = _read_json_object(child_marker, label="worker-owned child marker")
    worker_pid = handle.process.pid
    handle.process.terminate()
    try:
        worker_exit = handle.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        handle.process.kill()
        worker_exit = handle.process.wait(timeout=10)
    finally:
        _close_worker_logs(handle)
    child_termination = _terminate_pid(
        int(marker.get("pid") or 0),
        target_kind="autogrid_child_orphaned_by_worker_termination",
    )
    _assert_pointer_unchanged(
        before_pointer,
        _active_maps_pointer(project_root),
        scenario="Reliability worker hard termination",
    )

    retry_handle, _ = _run_single_worker(
        project_root,
        python_executable=python_executable,
        autogrid_executable=autogrid_executable,
        scenario_root=scenario_root,
        worker_id="retry_after_worker_termination",
        timeout_seconds=timeout_seconds,
        grid_points=grid_points,
        spacing=spacing,
    )
    retry = _collect_worker(retry_handle, timeout_seconds=timeout_seconds)
    _fail(
        retry.get("ok") is True
        and isinstance(retry.get("api_result"), Mapping),
        "RELIABILITY_WORKER_TERMINATION_RETRY_FAILED",
        "The project could not retry after its lock-owning worker was terminated.",
        details=retry,
    )
    publication = _audit_published_manifest(
        project_root,
        retry["api_result"],
        expected_grid_points=grid_points,
        expected_spacing=spacing,
        expected_autogrid=autogrid_executable,
        require_active=True,
    )
    new_ids = _map_set_ids(project_root) - before_ids
    recovered: list[dict[str, Any]] = []
    for map_set_id in sorted(new_ids - {publication["map_set_id"]}):
        manifest_path = (
            project_root
            / "maps"
            / map_set_id
            / HYDRATED_MAPS_MANIFEST_NAME
        )
        if manifest_path.is_file():
            manifest = _read_json_object(
                manifest_path,
                label=f"recovered map set {map_set_id}",
            )
            if manifest.get("status") == "interrupted":
                recovered.append(
                    {
                        "map_set_id": map_set_id,
                        "manifest_file": manifest_path.relative_to(
                            project_root
                        ).as_posix(),
                        "manifest_sha256": _sha256_file(manifest_path),
                        "status": manifest.get("status"),
                        "error_code": (
                            (manifest.get("error") or {}).get("code")
                            if isinstance(manifest.get("error"), Mapping)
                            else ""
                        ),
                    }
                )
    _fail(
        bool(recovered),
        "RELIABILITY_ORPHAN_RECOVERY_NOT_RECORDED",
        "Retry succeeded but no interrupted orphan record was recovered.",
        details={"new_map_set_ids": sorted(new_ids)},
    )
    active = _active_maps_pointer(project_root)
    _fail(
        all(item["manifest_file"] != active.get("path") for item in recovered),
        "RELIABILITY_RECOVERED_ORPHAN_ACTIVATED",
        "A recovered interrupted map set became active.",
        details={"active": active, "recovered": recovered},
    )
    return {
        "evidence_level": REAL_PROCESS_EVIDENCE,
        "worker_termination": {
            "evidence_level": REAL_PROCESS_EVIDENCE,
            "target_kind": "lock_owning_python_worker",
            "pid": worker_pid,
            "method": "Popen.terminate",
            "exit_code": worker_exit,
            "verified_exited": handle.process.poll() is not None,
        },
        "child_termination": child_termination,
        "active_pointer_preserved_until_retry": True,
        "retry_worker": retry,
        "retry_publication": publication,
        "recovered_non_active_orphans": recovered,
    }


def _record_scenario(
    scenarios: dict[str, Any],
    key: str,
    operation: Callable[[], Mapping[str, Any]],
) -> bool:
    try:
        value = dict(operation())
        value["ok"] = True
        scenarios[key] = value
        return True
    except ReliabilityVerificationError as exc:
        scenarios[key] = {
            "ok": False,
            "evidence_level": exc.details.get(
                "evidence_level",
                NOT_EXECUTED_EVIDENCE,
            ),
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
        return False
    except BaseException as exc:  # noqa: BLE001 - independent scenario boundary.
        scenarios[key] = {
            "ok": False,
            "error": {
                "code": "RELIABILITY_SCENARIO_UNEXPECTED_ERROR",
                "message": str(exc),
                "details": {"type": type(exc).__name__},
            },
        }
        return False


def verify_hydrated_system_reliability(
    source_project: str | Path,
    *,
    python_executable: str | Path,
    autogrid_executable: str | Path,
    minimum_free_bytes: int = int(
        DEFAULT_MINIMUM_FREE_GIB * 1024 * 1024 * 1024
    ),
    worker_timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
    keep_temporary: bool = False,
) -> dict[str, Any]:
    source = Path(source_project).expanduser().resolve(strict=False)
    python_path = Path(python_executable).expanduser().resolve(strict=False)
    autogrid_path = Path(autogrid_executable).expanduser().resolve(strict=False)
    work_root = Path(
        tempfile.mkdtemp(prefix="DockStart_hydrated_system_reliability_")
    ).resolve()
    result: dict[str, Any] = {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "verifier_id": VERIFIER_ID,
        "parameters": {
            "grid_points": [MAX_GRID_POINTS] * 3,
            "spacing_angstrom": GRID_SPACING,
            "values_per_map": (MAX_GRID_POINTS + 1) ** 3,
            "worker_timeout_seconds": worker_timeout_seconds,
        },
        "temporary": {
            "path": str(work_root),
            "kept": keep_temporary,
            "removed": False,
        },
        "scenarios": {},
        "error": None,
    }
    try:
        _fail(
            source.is_dir() and not source.is_symlink(),
            "RELIABILITY_SOURCE_PROJECT_INVALID",
            "The explicitly supplied source project is not a regular directory.",
            details={"path": str(source)},
        )
        result["disk_budget"] = _disk_budget_evidence(
            work_root,
            minimum_free_bytes=minimum_free_bytes,
        )
        result["environment"] = {
            "python": _validate_python_executable(python_path),
            "autogrid": _validate_autogrid_executable(autogrid_path),
        }
        source_snapshot = work_root / "source_project_snapshot"
        result["source_snapshot"] = _copy_verified_tree(
            source,
            source_snapshot,
        )
        result["source_snapshot"]["hydrated_status"] = (
            _validate_prepared_project(source_snapshot)
        )

        with _isolated_tool_environment(
            work_root,
            python_executable=python_path,
            autogrid_executable=autogrid_path,
        ) as environment:
            result["isolated_environment"] = environment
            scenario_results: list[bool] = []

            def maximum_grid() -> Mapping[str, Any]:
                maximum_project = (
                    work_root / "scenario_maximum_grid" / "project"
                )
                maximum_project.parent.mkdir(parents=True)
                _copy_verified_tree(source_snapshot, maximum_project)
                return _generate_and_audit(
                    maximum_project,
                    grid_points=MAX_GRID_POINTS,
                    spacing=GRID_SPACING,
                    expected_autogrid=autogrid_path,
                )

            scenario_results.append(
                _record_scenario(
                    result["scenarios"],
                    "maximum_grid_project_api",
                    maximum_grid,
                )
            )

            def process_competition() -> Mapping[str, Any]:
                competition_root = (
                    work_root / "scenario_process_competition"
                )
                competition_project = competition_root / "project"
                competition_root.mkdir()
                _copy_verified_tree(source_snapshot, competition_project)
                return _run_competition_scenario(
                    competition_project,
                    python_executable=python_path,
                    autogrid_executable=autogrid_path,
                    scenario_root=competition_root,
                    timeout_seconds=worker_timeout_seconds,
                    grid_points=MAX_GRID_POINTS,
                    spacing=GRID_SPACING,
                )

            scenario_results.append(
                _record_scenario(
                    result["scenarios"],
                    "two_process_competition",
                    process_competition,
                )
            )

            def child_termination() -> Mapping[str, Any]:
                child_root = work_root / "scenario_child_termination"
                child_project = child_root / "project"
                child_root.mkdir()
                _copy_verified_tree(source_snapshot, child_project)
                return _run_child_termination_scenario(
                    child_project,
                    python_executable=python_path,
                    autogrid_executable=autogrid_path,
                    scenario_root=child_root,
                    timeout_seconds=worker_timeout_seconds,
                    grid_points=MAX_GRID_POINTS,
                    spacing=GRID_SPACING,
                )

            scenario_results.append(
                _record_scenario(
                    result["scenarios"],
                    "real_autogrid_child_termination",
                    child_termination,
                )
            )

            def worker_termination() -> Mapping[str, Any]:
                worker_root = work_root / "scenario_worker_termination"
                worker_project = worker_root / "project"
                worker_root.mkdir()
                _copy_verified_tree(source_snapshot, worker_project)
                return _run_worker_termination_scenario(
                    worker_project,
                    python_executable=python_path,
                    autogrid_executable=autogrid_path,
                    scenario_root=worker_root,
                    timeout_seconds=worker_timeout_seconds,
                    grid_points=MAX_GRID_POINTS,
                    spacing=GRID_SPACING,
                )

            scenario_results.append(
                _record_scenario(
                    result["scenarios"],
                    "lock_owner_worker_termination",
                    worker_termination,
                )
            )

        final_disk = shutil.disk_usage(work_root)
        result["disk_budget"]["free_bytes_after"] = final_disk.free
        result["disk_budget"]["observed_consumed_bytes"] = max(
            0,
            int(result["disk_budget"]["free_bytes"]) - final_disk.free,
        )
        result["filesystem_fault_evidence"] = {
            "disk_budget": result["disk_budget"],
            "real_disk_full": _fault_evidence(
                "disk_full",
                note=(
                    "Not executed: exhausting a host volume is unsafe. "
                    "The verifier records free-space observations only."
                ),
            ),
            "read_only_filesystem": _fault_evidence(
                "read_only_filesystem",
                note=(
                    "Not executed: chmod/read-only flags are not equivalent "
                    "to a real read-only mount or Windows ACL denial."
                ),
            ),
            "write_failure_injection": _fault_evidence(
                "write_failure_injection",
                note=(
                    "Not executed by the real verifier. Unit-test fault "
                    "injection must be labelled simulated_fault_injection."
                ),
            ),
        }
        result["scenario_summary"] = {
            "passed": sum(1 for item in scenario_results if item),
            "failed": sum(1 for item in scenario_results if not item),
            "total": len(scenario_results),
        }
        result["ok"] = all(scenario_results)
        if not result["ok"]:
            result["error"] = {
                "code": "RELIABILITY_SCENARIOS_FAILED",
                "message": "One or more independent reliability scenarios failed.",
                "details": {
                    "failed_scenarios": [
                        key
                        for key, value in result["scenarios"].items()
                        if value.get("ok") is not True
                    ]
                },
            }
    except ReliabilityVerificationError as exc:
        result["error"] = {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    except BaseException as exc:  # noqa: BLE001 - top-level audit boundary.
        result["error"] = {
            "code": "RELIABILITY_UNEXPECTED_ERROR",
            "message": str(exc),
            "details": {"type": type(exc).__name__},
        }
    finally:
        result_path = work_root / "reliability-result.json"
        try:
            result["temporary"]["result_file"] = str(result_path)
            _atomic_write_json(result_path, result)
        except OSError as exc:
            result["temporary"]["result_write_error"] = str(exc)
        if not keep_temporary:
            try:
                shutil.rmtree(work_root)
                result["temporary"]["removed"] = not work_root.exists()
            except OSError as exc:
                result["temporary"]["cleanup_error"] = str(exc)
        else:
            result["temporary"]["removed"] = False
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the hydrated AutoGrid system reliability gate against a "
            "temporary copy of a prepared DockStart project."
        )
    )
    parser.add_argument(
        "--source-project",
        required=True,
        help=(
            "Prepared DockStart project with a valid active hydrated ligand. "
            "The source is hashed, copied, and never modified."
        ),
    )
    parser.add_argument(
        "--python",
        required=True,
        help="Exact Python executable for independent reliability workers.",
    )
    parser.add_argument(
        "--autogrid",
        required=True,
        help="Exact caller-supplied external AutoGrid 4.2.6+ executable.",
    )
    parser.add_argument(
        "--minimum-free-gib",
        type=float,
        default=DEFAULT_MINIMUM_FREE_GIB,
        help=(
            "Verifier-only free-space precondition. This is not evidence that "
            "DockStart core implements a disk preflight gate."
        ),
    )
    parser.add_argument(
        "--worker-timeout-seconds",
        type=float,
        default=DEFAULT_WORKER_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep generated evidence inside the system temporary directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "_worker":
        return _worker_main(arguments[1:])
    parsed = _parser().parse_args(arguments)
    if (
        not math.isfinite(parsed.minimum_free_gib)
        or parsed.minimum_free_gib <= 0
        or not math.isfinite(parsed.worker_timeout_seconds)
        or parsed.worker_timeout_seconds <= 0
    ):
        _parser().error(
            "--minimum-free-gib and --worker-timeout-seconds must be positive."
        )
    result = verify_hydrated_system_reliability(
        parsed.source_project,
        python_executable=parsed.python,
        autogrid_executable=parsed.autogrid,
        minimum_free_bytes=int(
            parsed.minimum_free_gib * 1024 * 1024 * 1024
        ),
        worker_timeout_seconds=parsed.worker_timeout_seconds,
        keep_temporary=parsed.keep_temp,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
