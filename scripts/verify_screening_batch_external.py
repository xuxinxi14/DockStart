#!/usr/bin/env python3
"""Run a reproducible, real-process DockStart screening resource benchmark.

The default gate launches exactly 100 independent AutoDock Vina docking
processes through DockStart's public screening API.  Cancellation is requested
while item 20 is active, then the same queue is resumed and completed.

This is a software/resource benchmark.  Repeating one fixed ligand makes the
workload reproducible; it is not a chemically diverse virtual-screening study.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import vina_adapter  # noqa: E402
from dockstart_core.screening import (  # noqa: E402
    create_screening,
    export_screening_markdown_report,
    get_screening_status,
    request_screening_cancel,
    resume_screening,
    run_screening,
)


SCHEMA_VERSION = 1
VERIFIER_ID = "dockstart_screening_batch_external_v1"
DEFAULT_LIGAND_COUNT = 100
DEFAULT_CANCEL_AFTER = 20
MAX_LIGAND_COUNT = 500
DEFAULT_TOP_N = 20
MEMORY_POLL_INTERVAL_MS = 25

DEFAULT_VINA_PATH = ROOT / "resources" / "vina" / "vina.exe"
DEFAULT_RECEPTOR_PATH = (
    ROOT / "resources" / "examples" / "basic_pdbqt" / "receptor.pdbqt"
)
DEFAULT_LIGAND_PATH = (
    ROOT / "resources" / "examples" / "basic_pdbqt" / "ligand.pdbqt"
)

DEFAULT_VINA_CONTRACT = {
    "version": "1.2.7",
    "size_bytes": 1_233_920,
    "sha256": (
        "e0c4b2715e0c1a74f6e92d0f3be0328a"
        "c97542eafbc111e6b1efad897a73cce5"
    ),
}
DEFAULT_RECEPTOR_CONTRACT = {
    "size_bytes": 657,
    "sha256": (
        "2ef52a38914a61e275721928f1e8b611"
        "57233ed958b8a1cf6e54a73895c8c50a"
    ),
}
DEFAULT_LIGAND_CONTRACT = {
    "size_bytes": 512,
    "sha256": (
        "9071449d7d4af5ca2b750b2fb3d58872"
        "c7dec532e61a833479a9f616aff170be"
    ),
}

BOX = {
    "center_x": 0.0,
    "center_y": 0.0,
    "center_z": 0.0,
    "size_x": 8.0,
    "size_y": 8.0,
    "size_z": 8.0,
}
VINA_PARAMETERS = {
    "scoring": "vina",
    "exhaustiveness": 1,
    "max_evals": 0,
    "num_modes": 2,
    "min_rmsd": 1.0,
    "energy_range": 3.0,
    "cpu": 1,
    "spacing": 0.375,
    "verbosity": 1,
    "no_refine": False,
    "force_even_voxels": False,
    "seed": 12345,
}
EXPECTED_CONFIG_TEXT = """receptor = receptor.pdbqt
ligand = ligand.pdbqt
scoring = vina

center_x = 0
center_y = 0
center_z = 0
size_x = 8
size_y = 8
size_z = 8

exhaustiveness = 1
max_evals = 0
num_modes = 2
min_rmsd = 1
energy_range = 3
cpu = 1
spacing = 0.375
verbosity = 1
seed = 12345
"""
EXPECTED_ATTEMPT_ORACLE = {
    "config_sha256": hashlib.sha256(
        EXPECTED_CONFIG_TEXT.encode("utf-8")
    ).hexdigest(),
    "config_size_bytes": len(EXPECTED_CONFIG_TEXT.encode("utf-8")),
    "output_sha256": (
        "7638dc55934f89a136f2560166a30c62"
        "b4910bcaf1c11c3910024919f5783d9e"
    ),
    "output_size_bytes": 750,
    "best_affinity_kcal_mol": -0.6914,
}

SCORE_ROW = re.compile(
    r"^\s*(\d+)\s+(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)
POSE_RESULT = re.compile(
    r"^REMARK VINA RESULT:\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
)


class ScreeningBenchmarkError(RuntimeError):
    """Structured verifier failure."""

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


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ScreeningBenchmarkError(
            "SCREENING_BENCHMARK_FILE_INVALID",
            f"Expected a regular file: {resolved}",
        )
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _assert_file_contract(
    path: Path,
    contract: Mapping[str, Any],
    *,
    code: str,
    label: str,
) -> dict[str, Any]:
    try:
        identity = _file_identity(path)
    except (OSError, RuntimeError) as exc:
        raise ScreeningBenchmarkError(
            "SCREENING_BENCHMARK_FILE_MISSING",
            f"{label} is unavailable.",
            details={"path": str(path), "error": str(exc)},
        ) from exc
    expected = {
        "size_bytes": int(contract["size_bytes"]),
        "sha256": str(contract["sha256"]).lower(),
    }
    actual = {
        "size_bytes": int(identity["size_bytes"]),
        "sha256": str(identity["sha256"]).lower(),
    }
    if actual != expected:
        raise ScreeningBenchmarkError(
            code,
            f"{label} does not match the fixed benchmark contract.",
            details={"path": identity["path"], "expected": expected, "actual": actual},
        )
    return identity


def _snapshot_file(
    source: Path,
    destination: Path,
    contract: Mapping[str, Any],
    *,
    code: str,
    label: str,
) -> dict[str, Any]:
    before = _assert_file_contract(
        source,
        contract,
        code=code,
        label=label,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    snapshot = _assert_file_contract(
        destination,
        contract,
        code=code,
        label=f"{label} private snapshot",
    )
    after = _assert_file_contract(
        source,
        contract,
        code=code,
        label=label,
    )
    if (
        before["size_bytes"] != after["size_bytes"]
        or before["sha256"] != after["sha256"]
    ):
        raise ScreeningBenchmarkError(
            "SCREENING_BENCHMARK_SOURCE_CHANGED_DURING_SNAPSHOT",
            f"{label} changed while its private snapshot was created.",
            details={"before": before, "after": after},
        )
    return {"source_before": before, "snapshot": snapshot, "source_after": after}


def _resolved_equal(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve(strict=True))) == os.path.normcase(
            str(right.resolve(strict=True))
        )
    except OSError:
        return False


def _acceptance_profile(
    vina_path: Path,
    receptor_path: Path,
    ligand_path: Path,
    *,
    ligand_count: int,
    cancel_after: int,
) -> dict[str, Any]:
    fixed_paths = {
        "vina": _resolved_equal(vina_path, DEFAULT_VINA_PATH),
        "receptor": _resolved_equal(receptor_path, DEFAULT_RECEPTOR_PATH),
        "ligand": _resolved_equal(ligand_path, DEFAULT_LIGAND_PATH),
    }
    formal = (
        all(fixed_paths.values())
        and ligand_count == DEFAULT_LIGAND_COUNT
        and cancel_after == DEFAULT_CANCEL_AFTER
    )
    return {
        "profile": (
            "pinned_100_item_acceptance"
            if formal
            else (
                "confirmed_large_capacity_probe"
                if ligand_count > DEFAULT_LIGAND_COUNT
                else "custom_screening_probe"
            )
        ),
        "formal_100_item_acceptance": formal,
        "fixed_paths": fixed_paths,
        "ligand_count": ligand_count,
        "cancel_after": cancel_after,
    }


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_process_pairs() -> list[tuple[int, int]]:
    if sys.platform != "win32":
        raise ScreeningBenchmarkError(
            "SCREENING_BENCHMARK_UNSUPPORTED_PLATFORM",
            "This benchmark currently requires Windows process-memory APIs.",
            details={"platform": sys.platform},
        )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESSENTRY32W),
    ]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid_handle):
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    pairs: list[tuple[int, int]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if process_first(snapshot, ctypes.byref(entry)):
            while True:
                pairs.append(
                    (int(entry.th32ProcessID), int(entry.th32ParentProcessID))
                )
                entry.dwSize = ctypes.sizeof(entry)
                if not process_next(snapshot, ctypes.byref(entry)):
                    break
    finally:
        close_handle(snapshot)
    return pairs


def _windows_process_memory(pid: int) -> dict[str, int] | None:
    if pid <= 0:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_memory = psapi.GetProcessMemoryInfo
    get_memory.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    get_memory.restype = wintypes.BOOL

    handle = open_process(0x1000 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = _PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if not get_memory(
            handle,
            ctypes.byref(counters),
            ctypes.sizeof(counters),
        ):
            return None
        return {
            "working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
            "private_usage_bytes": int(counters.PrivateUsage),
        }
    finally:
        close_handle(handle)


def _process_tree_pids(root_pid: int) -> set[int]:
    pairs = _windows_process_pairs()
    children: dict[int, set[int]] = {}
    for pid, parent in pairs:
        children.setdefault(parent, set()).add(pid)
    result = {root_pid}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, set()):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


class WindowsProcessTreeMonitor:
    """Poll Windows working sets for one live process tree."""

    def __init__(self, root_pid: int, *, poll_interval_ms: int) -> None:
        self.root_pid = int(root_pid)
        self.poll_interval_ms = int(poll_interval_ms)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sample_count = 0
        self._peak_tree_working_set_bytes = 0
        self._peak_tree_private_usage_bytes = 0
        self._peak_pid_count = 0
        self._root_peak_working_set_bytes = 0
        self._sampling_errors: list[str] = []

    def _sample(self) -> None:
        try:
            pids = _process_tree_pids(self.root_pid)
            tree_working_set = 0
            tree_private_usage = 0
            sampled_pids = 0
            root_peak = 0
            for pid in sorted(pids):
                memory = _windows_process_memory(pid)
                if memory is None:
                    continue
                sampled_pids += 1
                tree_working_set += memory["working_set_bytes"]
                tree_private_usage += memory["private_usage_bytes"]
                if pid == self.root_pid:
                    root_peak = memory["peak_working_set_bytes"]
            if sampled_pids == 0:
                return
            with self._lock:
                self._sample_count += 1
                self._peak_tree_working_set_bytes = max(
                    self._peak_tree_working_set_bytes,
                    tree_working_set,
                )
                self._peak_tree_private_usage_bytes = max(
                    self._peak_tree_private_usage_bytes,
                    tree_private_usage,
                )
                self._peak_pid_count = max(
                    self._peak_pid_count,
                    sampled_pids,
                )
                self._root_peak_working_set_bytes = max(
                    self._root_peak_working_set_bytes,
                    root_peak,
                )
        except Exception as exc:  # noqa: BLE001 - retain auditable monitor status.
            with self._lock:
                if len(self._sampling_errors) < 20:
                    self._sampling_errors.append(
                        f"{type(exc).__name__}: {exc}"
                    )

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("process-tree monitor has already started")
        self._sample()

        def poll() -> None:
            interval = max(self.poll_interval_ms, 1) / 1000.0
            while not self._stop.wait(interval):
                self._sample()

        self._thread = threading.Thread(
            target=poll,
            name=f"process-tree-monitor-{self.root_pid}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, sample_after_stop: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if sample_after_stop:
            self._sample()

    def evidence(self) -> dict[str, Any]:
        with self._lock:
            return {
                "root_pid": self.root_pid,
                "poll_interval_ms": self.poll_interval_ms,
                "sample_count": self._sample_count,
                "peak_tree_working_set_bytes": (
                    self._peak_tree_working_set_bytes
                ),
                "peak_tree_private_usage_bytes": (
                    self._peak_tree_private_usage_bytes
                ),
                "peak_pid_count": self._peak_pid_count,
                "root_peak_working_set_bytes": (
                    self._root_peak_working_set_bytes
                ),
                "sampling_errors": list(self._sampling_errors),
                "source": (
                    "Windows Toolhelp32 process tree plus "
                    "GetProcessMemoryInfo"
                ),
            }


class AuditedScreeningRunner:
    """Delegate to the real adapter while recording one event per ligand."""

    def __init__(
        self,
        project_root: Path,
        vina_path: Path,
        *,
        cancel_after: int,
    ) -> None:
        self.project_root = project_root
        self.vina_path = vina_path.resolve(strict=True)
        self.cancel_after = int(cancel_after)
        self.events: list[dict[str, Any]] = []
        self.cancel_evidence: dict[str, Any] | None = None
        self._cancel_requested = False

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        item = dict(kwargs.get("item") or {})
        item_id = str(item.get("item_id") or "")
        item_order = int(item.get("order") or 0)
        attempt = int(kwargs.get("attempt") or 0)
        command = [str(value) for value in kwargs.get("command") or []]
        expected_command = [
            str(self.vina_path),
            "--config",
            "config.txt",
            "--out",
            "out.pdbqt",
        ]
        if command != expected_command:
            raise ScreeningBenchmarkError(
                "SCREENING_BENCHMARK_COMMAND_MISMATCH",
                "The screening runner received an unexpected command.",
                details={"expected": expected_command, "actual": command},
            )
        before = _assert_file_contract(
            self.vina_path,
            DEFAULT_VINA_CONTRACT,
            code="SCREENING_BENCHMARK_VINA_CHANGED",
            label="private AutoDock Vina",
        )
        started_ns = time.perf_counter_ns()
        pid: int | None = None
        identity: dict[str, Any] | None = None
        process_monitor: WindowsProcessTreeMonitor | None = None
        on_started_calls = 0
        original_on_started = kwargs.get("on_started")

        def on_started(value: int) -> None:
            nonlocal pid, identity, process_monitor, on_started_calls
            on_started_calls += 1
            if on_started_calls != 1:
                raise RuntimeError("Vina on_started callback fired more than once")
            pid = int(value)
            if pid <= 0:
                raise RuntimeError("Vina reported an invalid PID")
            process_monitor = WindowsProcessTreeMonitor(
                pid,
                poll_interval_ms=MEMORY_POLL_INTERVAL_MS,
            )
            process_monitor.start()
            raw_identity = vina_adapter.get_process_identity(pid)
            identity = dict(raw_identity) if isinstance(raw_identity, dict) else None
            if original_on_started is not None:
                original_on_started(pid)
            if item_order == self.cancel_after and not self._cancel_requested:
                alive_before = vina_adapter.is_process_running(pid)
                cancel_started_ns = time.perf_counter_ns()
                try:
                    response = request_screening_cancel(
                        str(self.project_root)
                    )
                except Exception as exc:  # noqa: BLE001
                    response = {
                        "ok": False,
                        "error": {
                            "code": "SCREENING_CANCEL_EXCEPTION",
                            "message": str(exc),
                        },
                    }
                cancel_finished_ns = time.perf_counter_ns()
                alive_after = vina_adapter.is_process_running(pid)
                self.cancel_evidence = {
                    "requested_during_item_id": item_id,
                    "requested_during_item_order": item_order,
                    "pid": pid,
                    "process_alive_before_request": alive_before,
                    "process_alive_after_request": alive_after,
                    "response_ok": bool(response.get("ok")),
                    "response_status": str(
                        (response.get("screening") or {}).get("status") or ""
                    ),
                    "response_error": response.get("error"),
                    "latency_ms": (
                        cancel_finished_ns - cancel_started_ns
                    )
                    / 1_000_000.0,
                }
                self._cancel_requested = True

        result = vina_adapter.run_managed(
            command,
            kwargs["cwd"],
            kwargs["stdout_path"],
            kwargs["stderr_path"],
            kwargs["log_path"],
            on_started=on_started,
        )
        finished_ns = time.perf_counter_ns()
        if process_monitor is not None:
            process_monitor.stop()
            memory = process_monitor.evidence()
        else:
            memory = {
                "root_pid": pid,
                "poll_interval_ms": MEMORY_POLL_INTERVAL_MS,
                "sample_count": 0,
                "peak_tree_working_set_bytes": 0,
                "peak_tree_private_usage_bytes": 0,
                "peak_pid_count": 0,
                "root_peak_working_set_bytes": 0,
                "sampling_errors": ["process monitor was not started"],
                "source": (
                    "Windows Toolhelp32 process tree plus "
                    "GetProcessMemoryInfo"
                ),
            }
        after = _assert_file_contract(
            self.vina_path,
            DEFAULT_VINA_CONTRACT,
            code="SCREENING_BENCHMARK_VINA_CHANGED",
            label="private AutoDock Vina",
        )
        event = {
            "sequence": len(self.events) + 1,
            "item_id": item_id,
            "item_order": item_order,
            "attempt": attempt,
            "command": command,
            "cwd": str(Path(kwargs["cwd"]).resolve(strict=True)),
            "pid": result.pid if result.pid is not None else pid,
            "exit_code": result.exit_code,
            "error": result.error,
            "on_started_calls": on_started_calls,
            "process_identity": identity,
            "started_monotonic_ns": started_ns,
            "finished_monotonic_ns": finished_ns,
            "duration_ms": (finished_ns - started_ns) / 1_000_000.0,
            "memory": memory,
            "vina_before": before,
            "vina_after": after,
        }
        self.events.append(event)
        return {
            "pid": result.pid,
            "exit_code": result.exit_code,
            "error": result.error,
        }


def _parse_log_scores(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = SCORE_ROW.match(line)
        if match:
            rows.append(
                {
                    "mode": int(match.group(1)),
                    "affinity_kcal_mol": float(match.group(2)),
                    "rmsd_lb": float(match.group(3)),
                    "rmsd_ub": float(match.group(4)),
                }
            )
    return rows


def _parse_pose_results(text: str) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for line in text.splitlines():
        match = POSE_RESULT.match(line)
        if match:
            rows.append(
                {
                    "affinity_kcal_mol": float(match.group(1)),
                    "rmsd_lb": float(match.group(2)),
                    "rmsd_ub": float(match.group(3)),
                }
            )
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _assert_true(
    condition: Any,
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    if not condition:
        raise ScreeningBenchmarkError(code, message, details=details)


def _validate_results(
    project_root: Path,
    state: Mapping[str, Any],
    runner: AuditedScreeningRunner,
    *,
    ligand_count: int,
    cancel_after: int,
) -> dict[str, Any]:
    items = list(state.get("items") or [])
    _assert_true(
        state.get("status") == "completed",
        "SCREENING_BENCHMARK_FINAL_STATUS",
        "The resumed screening did not finish as completed.",
        details={"status": state.get("status")},
    )
    _assert_true(
        len(items) == ligand_count,
        "SCREENING_BENCHMARK_ITEM_COUNT",
        "The final screening item count changed.",
    )
    _assert_true(
        list(state.get("queue") or []) == [],
        "SCREENING_BENCHMARK_QUEUE_NOT_EMPTY",
        "The completed screening queue is not empty.",
    )
    expected_ids = [
        f"ligand_{index:04d}" for index in range(1, ligand_count + 1)
    ]
    actual_ids = [str(item.get("item_id") or "") for item in items]
    _assert_true(
        actual_ids == expected_ids,
        "SCREENING_BENCHMARK_ORDER_CHANGED",
        "Stable screening item order changed.",
        details={"expected": expected_ids, "actual": actual_ids},
    )
    _assert_true(
        len(runner.events) == ligand_count,
        "SCREENING_BENCHMARK_PROCESS_COUNT",
        "The number of audited Vina processes does not equal the ligand count.",
        details={"expected": ligand_count, "actual": len(runner.events)},
    )

    output_hashes: set[str] = set()
    affinities: set[float] = set()
    log_hashes: set[str] = set()
    attempt_evidence: list[dict[str, Any]] = []
    for index, (item, event) in enumerate(
        zip(items, runner.events, strict=True),
        start=1,
    ):
        item_id = expected_ids[index - 1]
        _assert_true(
            item.get("status") == "succeeded"
            and int(item.get("attempt_count") or 0) == 1,
            "SCREENING_BENCHMARK_ITEM_FAILED",
            f"{item_id} did not succeed in exactly one attempt.",
            details={"item": item},
        )
        attempts = list(item.get("attempts") or [])
        _assert_true(
            len(attempts) == 1,
            "SCREENING_BENCHMARK_ATTEMPT_COUNT",
            f"{item_id} has an unexpected attempt history.",
        )
        attempt = attempts[0]
        _assert_true(
            attempt.get("status") == "succeeded"
            and attempt.get("exit_code") == 0
            and (attempt.get("integrity") or {}).get("status") == "verified",
            "SCREENING_BENCHMARK_ATTEMPT_INVALID",
            f"{item_id} attempt evidence is not successful and verified.",
            details={"attempt": attempt},
        )
        _assert_true(
            event["sequence"] == index
            and event["item_id"] == item_id
            and event["item_order"] == index
            and event["attempt"] == 1,
            "SCREENING_BENCHMARK_PROCESS_ORDER",
            "Audited Vina process order does not match the stable queue.",
            details={"event": event},
        )
        _assert_true(
            isinstance(event.get("pid"), int)
            and int(event["pid"]) > 0
            and event.get("exit_code") == 0
            and not event.get("error")
            and event.get("on_started_calls") == 1,
            "SCREENING_BENCHMARK_PROCESS_EVIDENCE",
            f"{item_id} lacks successful real-process evidence.",
            details={"event": event},
        )
        process_identity = event.get("process_identity")
        _assert_true(
            isinstance(process_identity, dict)
            and int(process_identity.get("pid") or 0) == int(event["pid"])
            and bool(process_identity.get("executable_path"))
            and bool(process_identity.get("creation_token")),
            "SCREENING_BENCHMARK_PROCESS_IDENTITY",
            f"{item_id} lacks an auditable live process identity.",
            details={"event": event},
        )
        _assert_true(
            os.path.normcase(
                str(Path(str(process_identity["executable_path"])).resolve())
            )
            == os.path.normcase(str(runner.vina_path))
            and int(attempt.get("pid") or 0) == int(event["pid"]),
            "SCREENING_BENCHMARK_PROCESS_BINDING",
            f"{item_id} process identity is not bound to its attempt and Vina.",
            details={
                "attempt_pid": attempt.get("pid"),
                "event_pid": event.get("pid"),
                "process_identity": process_identity,
                "expected_vina": str(runner.vina_path),
            },
        )
        memory = event.get("memory") or {}
        _assert_true(
            int(memory.get("sample_count") or 0) >= 1
            and int(memory.get("peak_tree_working_set_bytes") or 0) > 0,
            "SCREENING_BENCHMARK_PROCESS_MEMORY",
            f"{item_id} lacks a real Vina process-memory sample.",
            details={"memory": memory},
        )
        if index > 1:
            previous = runner.events[index - 2]
            _assert_true(
                int(event["started_monotonic_ns"])
                >= int(previous["finished_monotonic_ns"]),
                "SCREENING_BENCHMARK_NOT_SERIAL",
                "Vina process intervals overlap; the queue was not serial.",
                details={"previous": previous, "current": event},
            )

        attempt_dir = project_root / Path(str(attempt["directory"]))
        config_path = project_root / Path(
            str((attempt.get("config_snapshot") or {}).get("file") or "")
        )
        output_path = project_root / Path(str(attempt.get("output_file") or ""))
        stdout_path = attempt_dir / "stdout.txt"
        stderr_path = attempt_dir / "stderr.txt"
        log_path = attempt_dir / "log.txt"
        config_bytes = config_path.read_bytes()
        output_bytes = output_path.read_bytes()
        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        log_bytes = log_path.read_bytes()
        config_sha256 = hashlib.sha256(config_bytes).hexdigest()
        output_sha256 = hashlib.sha256(output_bytes).hexdigest()
        log_sha256 = hashlib.sha256(log_bytes).hexdigest()
        input_snapshots = attempt.get("input_snapshots") or {}
        receptor_input = input_snapshots.get("receptor") or {}
        ligand_input = input_snapshots.get("ligand") or {}
        _assert_true(
            str(item.get("sha256") or "")
            == DEFAULT_LIGAND_CONTRACT["sha256"]
            and int(item.get("size_bytes") or 0)
            == DEFAULT_LIGAND_CONTRACT["size_bytes"]
            and str(receptor_input.get("sha256") or "")
            == DEFAULT_RECEPTOR_CONTRACT["sha256"]
            and int(receptor_input.get("size_bytes") or 0)
            == DEFAULT_RECEPTOR_CONTRACT["size_bytes"]
            and str(ligand_input.get("sha256") or "")
            == DEFAULT_LIGAND_CONTRACT["sha256"]
            and int(ligand_input.get("size_bytes") or 0)
            == DEFAULT_LIGAND_CONTRACT["size_bytes"],
            "SCREENING_BENCHMARK_INPUT_BINDING",
            f"{item_id} is not bound to the fixed receptor/ligand identities.",
            details={
                "item": {
                    "sha256": item.get("sha256"),
                    "size_bytes": item.get("size_bytes"),
                },
                "receptor": receptor_input,
                "ligand": ligand_input,
            },
        )
        _assert_true(
            config_bytes == EXPECTED_CONFIG_TEXT.encode("utf-8")
            and config_sha256
            == EXPECTED_ATTEMPT_ORACLE["config_sha256"]
            and len(config_bytes)
            == EXPECTED_ATTEMPT_ORACLE["config_size_bytes"],
            "SCREENING_BENCHMARK_CONFIG_ORACLE",
            f"{item_id} config does not match the independent fixed oracle.",
        )
        _assert_true(
            output_sha256 == EXPECTED_ATTEMPT_ORACLE["output_sha256"]
            and len(output_bytes)
            == EXPECTED_ATTEMPT_ORACLE["output_size_bytes"],
            "SCREENING_BENCHMARK_OUTPUT_ORACLE",
            f"{item_id} output does not match the fixed Vina oracle.",
            details={
                "actual_sha256": output_sha256,
                "actual_size_bytes": len(output_bytes),
            },
        )
        _assert_true(
            output_sha256 == str(attempt.get("output_sha256") or "")
            == str(item.get("best_output_sha256") or "")
            and len(output_bytes) == int(attempt.get("output_size_bytes") or 0)
            == int(item.get("best_output_size_bytes") or 0),
            "SCREENING_BENCHMARK_OUTPUT_IDENTITY",
            f"{item_id} output identities disagree.",
        )
        _assert_true(
            stdout_bytes == log_bytes and stderr_bytes == b"",
            "SCREENING_BENCHMARK_STREAM_INCONSISTENCY",
            f"{item_id} stdout/log/stderr evidence is inconsistent.",
        )
        log_scores = _parse_log_scores(
            log_bytes.decode("utf-8", errors="replace")
        )
        pose_scores = _parse_pose_results(
            output_bytes.decode("utf-8", errors="replace")
        )
        _assert_true(
            [row["mode"] for row in log_scores] == [1]
            and len(pose_scores) == 1,
            "SCREENING_BENCHMARK_SCORE_MODES",
            (
                f"{item_id} does not match the fixed one-mode result oracle "
                "(Vina may return fewer poses than num_modes)."
            ),
            details={"log_scores": log_scores, "pose_scores": pose_scores},
        )
        best = min(row["affinity_kcal_mol"] for row in log_scores)
        _assert_true(
            math.isclose(
                best,
                float(attempt.get("best_affinity_kcal_mol")),
                abs_tol=1e-9,
            )
            and math.isclose(
                best,
                float(item.get("best_affinity_kcal_mol")),
                abs_tol=1e-9,
            )
            and math.isclose(
                best,
                EXPECTED_ATTEMPT_ORACLE["best_affinity_kcal_mol"],
                abs_tol=1e-9,
            )
            and math.isclose(
                pose_scores[0]["affinity_kcal_mol"],
                round(best, 3),
                abs_tol=0.001,
            ),
            "SCREENING_BENCHMARK_AFFINITY_ORACLE",
            f"{item_id} affinity sources disagree.",
            details={
                "log_scores": log_scores,
                "pose_scores": pose_scores,
                "attempt_best": attempt.get("best_affinity_kcal_mol"),
                "item_best": item.get("best_affinity_kcal_mol"),
            },
        )
        output_hashes.add(output_sha256)
        affinities.add(best)
        log_hashes.add(log_sha256)
        attempt_evidence.append(
            {
                "item_id": item_id,
                "pid": event["pid"],
                "exit_code": event["exit_code"],
                "duration_ms": event["duration_ms"],
                "command": list(event["command"]),
                "cwd": event["cwd"],
                "process_identity": dict(process_identity),
                "peak_process_tree_working_set_bytes": memory[
                    "peak_tree_working_set_bytes"
                ],
                "process_memory_sample_count": memory["sample_count"],
                "config_sha256": config_sha256,
                "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
                "output_sha256": output_sha256,
                "log_sha256": log_sha256,
                "best_affinity_kcal_mol": best,
            }
        )

    _assert_true(
        len(output_hashes) == 1 and len(affinities) == 1,
        "SCREENING_BENCHMARK_RECOVERY_INCONSISTENT",
        "Deterministic results differ across the cancel/resume boundary.",
        details={
            "output_hashes": sorted(output_hashes),
            "affinities": sorted(affinities),
        },
    )
    pre_cancel = attempt_evidence[:cancel_after]
    post_resume = attempt_evidence[cancel_after:]
    _assert_true(
        pre_cancel
        and post_resume
        and {row["output_sha256"] for row in pre_cancel}
        == {row["output_sha256"] for row in post_resume}
        and {row["best_affinity_kcal_mol"] for row in pre_cancel}
        == {row["best_affinity_kcal_mol"] for row in post_resume},
        "SCREENING_BENCHMARK_BOUNDARY_MISMATCH",
        "Pre-cancel and post-resume results are not identical.",
    )

    outputs = state.get("outputs") or {}
    summary_path = project_root / Path(str(outputs.get("summary_csv") or ""))
    top_path = project_root / Path(str(outputs.get("top_n_csv") or ""))
    report_path = project_root / Path(str(outputs.get("report_md") or ""))
    summary_rows = _read_csv(summary_path)
    top_rows = _read_csv(top_path)
    report_text = report_path.read_text(encoding="utf-8")
    _assert_true(
        len(summary_rows) == ligand_count
        and [row["item_id"] for row in summary_rows] == expected_ids
        and all(row["status"] == "succeeded" for row in summary_rows),
        "SCREENING_BENCHMARK_SUMMARY_INVALID",
        "The final summary CSV does not match the completed queue.",
    )
    expected_top_count = min(DEFAULT_TOP_N, ligand_count)
    _assert_true(
        len(top_rows) == expected_top_count
        and [row["item_id"] for row in top_rows]
        == expected_ids[:expected_top_count],
        "SCREENING_BENCHMARK_TOP_N_INVALID",
        "The deterministic Top N CSV is inconsistent.",
    )
    _assert_true(
        "Docking score 仅供结构结合趋势参考，不能替代实验验证。"
        in report_text,
        "SCREENING_BENCHMARK_REPORT_DISCLAIMER",
        "The screening report is missing the required scientific disclaimer.",
    )
    for key, path in (
        ("summary", summary_path),
        ("top_n", top_path),
        ("report", report_path),
    ):
        recorded_sha = str(
            outputs.get(
                {
                    "summary": "summary_sha256",
                    "top_n": "top_n_sha256",
                    "report": "report_sha256",
                }[key]
            )
            or ""
        )
        recorded_size = int(
            outputs.get(
                {
                    "summary": "summary_size_bytes",
                    "top_n": "top_n_size_bytes",
                    "report": "report_size_bytes",
                }[key]
            )
            or 0
        )
        _assert_true(
            _sha256(path) == recorded_sha
            and path.stat().st_size == recorded_size,
            "SCREENING_BENCHMARK_SUMMARY_IDENTITY",
            f"The {key} artifact identity does not match screening.json.",
        )

    return {
        "item_count": ligand_count,
        "succeeded_count": ligand_count,
        "failed_count": 0,
        "attempt_count": ligand_count,
        "external_vina_process_count": len(runner.events),
        "serial_non_overlapping_processes": True,
        "independent_docking_semantics": (
            "one frozen receptor + one frozen ligand snapshot + one config + "
            "one separate Vina process per queue item; no joint ligand scoring"
        ),
        "fixed_attempt_oracle": dict(EXPECTED_ATTEMPT_ORACLE),
        "unique_output_sha256": sorted(output_hashes),
        "unique_log_sha256": sorted(log_hashes),
        "unique_best_affinity_kcal_mol": sorted(affinities),
        "cancel_resume_boundary": {
            "cancel_after": cancel_after,
            "pre_cancel_succeeded": len(pre_cancel),
            "post_resume_succeeded": len(post_resume),
            "output_and_affinity_identical": True,
        },
        "artifacts": {
            "summary_csv": _file_identity(summary_path),
            "top_n_csv": _file_identity(top_path),
            "report_md": _file_identity(report_path),
        },
        "processes": attempt_evidence,
    }


def _large_run_guard(
    ligand_count: int,
    *,
    confirm_large_run: bool,
) -> dict[str, Any] | None:
    if ligand_count <= DEFAULT_LIGAND_COUNT or confirm_large_run:
        return None
    estimated_seconds = ligand_count * 0.6339
    return {
        "schema_version": SCHEMA_VERSION,
        "verifier_id": VERIFIER_ID,
        "ok": False,
        "error": {
            "code": "SCREENING_BENCHMARK_CONFIRMATION_REQUIRED",
            "message": (
                "Runs above 100 ligands require --confirm-large-run because "
                "they launch one real Vina process per ligand."
            ),
            "details": {
                "ligand_count": ligand_count,
                "estimated_seconds_from_three_item_pilot": round(
                    estimated_seconds,
                    1,
                ),
                "estimated_minutes_from_three_item_pilot": round(
                    estimated_seconds / 60.0,
                    2,
                ),
                "external_processes_started": 0,
            },
        },
        "scope": {
            "requested_ligand_count": ligand_count,
            "maximum_ligand_count": MAX_LIGAND_COUNT,
            "large_run_confirmed": False,
        },
    }


def verify_screening_batch_external(
    *,
    vina_path: str | Path = DEFAULT_VINA_PATH,
    receptor_path: str | Path = DEFAULT_RECEPTOR_PATH,
    ligand_path: str | Path = DEFAULT_LIGAND_PATH,
    ligand_count: int = DEFAULT_LIGAND_COUNT,
    cancel_after: int = DEFAULT_CANCEL_AFTER,
    confirm_large_run: bool = False,
    keep_workdir: bool = False,
) -> dict[str, Any]:
    """Execute the real serial screening benchmark and return bounded JSON."""

    if isinstance(ligand_count, bool) or not isinstance(ligand_count, int):
        return {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "ok": False,
            "error": {
                "code": "SCREENING_BENCHMARK_COUNT_INVALID",
                "message": "ligand_count must be an integer.",
            },
        }
    if ligand_count < 2 or ligand_count > MAX_LIGAND_COUNT:
        return {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "ok": False,
            "error": {
                "code": "SCREENING_BENCHMARK_COUNT_INVALID",
                "message": (
                    f"ligand_count must be between 2 and {MAX_LIGAND_COUNT}."
                ),
            },
        }
    if (
        isinstance(cancel_after, bool)
        or not isinstance(cancel_after, int)
        or cancel_after < 1
        or cancel_after >= ligand_count
    ):
        return {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "ok": False,
            "error": {
                "code": "SCREENING_BENCHMARK_CANCEL_POINT_INVALID",
                "message": "cancel_after must be between 1 and ligand_count - 1.",
            },
        }
    guard = _large_run_guard(
        ligand_count,
        confirm_large_run=confirm_large_run,
    )
    if guard is not None:
        return guard

    supplied_vina = Path(vina_path).expanduser()
    supplied_receptor = Path(receptor_path).expanduser()
    supplied_ligand = Path(ligand_path).expanduser()
    profile = _acceptance_profile(
        supplied_vina,
        supplied_receptor,
        supplied_ligand,
        ligand_count=ligand_count,
        cancel_after=cancel_after,
    )
    work_root = Path(
        tempfile.mkdtemp(prefix="DockStart_screening_batch_external_")
    ).resolve(strict=True)
    project_root = work_root / "project"
    previous_environment = {
        "DOCKSTART_SETTINGS_PATH": os.environ.get("DOCKSTART_SETTINGS_PATH"),
        "DOCKSTART_RESOURCES_DIR": os.environ.get("DOCKSTART_RESOURCES_DIR"),
    }
    result: dict[str, Any] | None = None
    caught: ScreeningBenchmarkError | None = None
    global_monitor: WindowsProcessTreeMonitor | None = None
    global_started_ns = time.perf_counter_ns()
    phase_seconds: dict[str, float] = {}
    process_events: list[dict[str, Any]] = []
    try:
        if sys.platform != "win32":
            raise ScreeningBenchmarkError(
                "SCREENING_BENCHMARK_UNSUPPORTED_PLATFORM",
                "This benchmark requires Windows process-memory APIs.",
                details={"platform": sys.platform},
            )
        tool_snapshot = _snapshot_file(
            supplied_vina,
            work_root / "fixed_snapshots" / "tool" / "vina.exe",
            DEFAULT_VINA_CONTRACT,
            code="SCREENING_BENCHMARK_VINA_CONTRACT_MISMATCH",
            label="AutoDock Vina",
        )
        receptor_snapshot = _snapshot_file(
            supplied_receptor,
            work_root / "fixed_snapshots" / "inputs" / "receptor.pdbqt",
            DEFAULT_RECEPTOR_CONTRACT,
            code="SCREENING_BENCHMARK_RECEPTOR_CONTRACT_MISMATCH",
            label="fixed receptor",
        )
        ligand_snapshot = _snapshot_file(
            supplied_ligand,
            work_root / "fixed_snapshots" / "inputs" / "ligand.pdbqt",
            DEFAULT_LIGAND_CONTRACT,
            code="SCREENING_BENCHMARK_LIGAND_CONTRACT_MISMATCH",
            label="fixed ligand",
        )
        private_vina = Path(tool_snapshot["snapshot"]["path"])
        private_receptor = Path(receptor_snapshot["snapshot"]["path"])
        private_ligand = Path(ligand_snapshot["snapshot"]["path"])

        global_monitor = WindowsProcessTreeMonitor(
            os.getpid(),
            poll_interval_ms=MEMORY_POLL_INTERVAL_MS,
        )
        global_monitor.start()

        prepared = project_root / "prepared"
        prepared.mkdir(parents=True)
        shutil.copyfile(private_receptor, prepared / "receptor.pdbqt")
        ligand_files: list[str] = []
        for index in range(1, ligand_count + 1):
            destination = prepared / f"ligand_{index:04d}.pdbqt"
            shutil.copyfile(private_ligand, destination)
            ligand_files.append(destination.relative_to(project_root).as_posix())

        workload_contract = {
            "ligand_count": ligand_count,
            "cancel_after": cancel_after,
            "box": BOX,
            "vina": VINA_PARAMETERS,
            "max_retries": 0,
            "top_n": min(DEFAULT_TOP_N, ligand_count),
            "serial": True,
            "input_semantics": (
                "fixed ligand repeated solely as a deterministic resource "
                "workload"
            ),
        }
        create_started = time.perf_counter()
        created = create_screening(
            str(project_root),
            "prepared/receptor.pdbqt",
            ligand_files,
            vina_path=str(private_vina),
            box=dict(BOX),
            vina=dict(VINA_PARAMETERS),
            max_retries=0,
            top_n=min(DEFAULT_TOP_N, ligand_count),
        )
        phase_seconds["create_screening"] = (
            time.perf_counter() - create_started
        )
        _assert_true(
            created.get("ok"),
            "SCREENING_BENCHMARK_CREATE_FAILED",
            "Public create_screening failed.",
            details={"error": created.get("error")},
        )
        created_state = created.get("screening") or {}
        created_tool = (
            (created_state.get("tools") or {}).get("vina") or {}
        )
        _assert_true(
            str(created_tool.get("version") or "")
            == DEFAULT_VINA_CONTRACT["version"]
            and str(created_tool.get("sha256") or "").lower()
            == DEFAULT_VINA_CONTRACT["sha256"]
            and int(created_tool.get("size_bytes") or 0)
            == DEFAULT_VINA_CONTRACT["size_bytes"],
            "SCREENING_BENCHMARK_TOOL_SNAPSHOT",
            "create_screening did not freeze the fixed Vina identity.",
            details={"tool": created_tool},
        )

        runner = AuditedScreeningRunner(
            project_root,
            private_vina,
            cancel_after=cancel_after,
        )
        first_started = time.perf_counter()
        first = run_screening(str(project_root), runner=runner)
        phase_seconds["run_until_cancel"] = (
            time.perf_counter() - first_started
        )
        _assert_true(
            first.get("ok")
            and (first.get("screening") or {}).get("status") == "canceled",
            "SCREENING_BENCHMARK_CANCEL_FAILED",
            "The first public run_screening call did not stop as canceled.",
            details={"response": first},
        )
        first_state = first["screening"]
        first_succeeded = sum(
            item.get("status") == "succeeded"
            for item in first_state.get("items") or []
        )
        expected_remaining = [
            f"ligand_{index:04d}"
            for index in range(cancel_after + 1, ligand_count + 1)
        ]
        _assert_true(
            first_succeeded == cancel_after
            and list(first_state.get("queue") or []) == expected_remaining,
            "SCREENING_BENCHMARK_CANCEL_BOUNDARY",
            "Cancellation did not preserve the expected remaining queue.",
            details={
                "succeeded": first_succeeded,
                "queue": first_state.get("queue"),
            },
        )
        cancel_evidence = runner.cancel_evidence
        _assert_true(
            isinstance(cancel_evidence, dict)
            and cancel_evidence.get("response_ok")
            and cancel_evidence.get("response_status")
            == "cancel_requested"
            and cancel_evidence.get("process_alive_before_request") is True,
            "SCREENING_BENCHMARK_CANCEL_RESPONSE",
            "Cancellation was not proven against a live Vina process.",
            details={"cancel": cancel_evidence},
        )

        resume_started = time.perf_counter()
        resumed = resume_screening(str(project_root))
        phase_seconds["resume_screening"] = (
            time.perf_counter() - resume_started
        )
        _assert_true(
            resumed.get("ok")
            and (resumed.get("screening") or {}).get("status") == "ready"
            and list(
                (resumed.get("screening") or {}).get("queue") or []
            )
            == expected_remaining,
            "SCREENING_BENCHMARK_RESUME_FAILED",
            "Public resume_screening did not restore the exact remaining queue.",
            details={"response": resumed},
        )

        final_run_started = time.perf_counter()
        final_run = run_screening(str(project_root), runner=runner)
        phase_seconds["run_after_resume"] = (
            time.perf_counter() - final_run_started
        )
        _assert_true(
            final_run.get("ok")
            and (final_run.get("screening") or {}).get("status")
            == "completed",
            "SCREENING_BENCHMARK_FINAL_RUN_FAILED",
            "The resumed public run_screening call did not complete.",
            details={"error": final_run.get("error")},
        )
        report_started = time.perf_counter()
        reported = export_screening_markdown_report(str(project_root))
        phase_seconds["export_report"] = (
            time.perf_counter() - report_started
        )
        _assert_true(
            reported.get("ok"),
            "SCREENING_BENCHMARK_REPORT_FAILED",
            "Public screening report export failed.",
            details={"error": reported.get("error")},
        )
        status_response = get_screening_status(str(project_root))
        _assert_true(
            status_response.get("ok")
            and isinstance(status_response.get("screening"), dict),
            "SCREENING_BENCHMARK_STATUS_FAILED",
            "Public get_screening_status failed after completion.",
            details={"error": status_response.get("error")},
        )
        final_state = status_response["screening"]
        process_events = runner.events
        result_validation = _validate_results(
            project_root,
            final_state,
            runner,
            ligand_count=ligand_count,
            cancel_after=cancel_after,
        )

        for path, contract, code, label in (
            (
                private_vina,
                DEFAULT_VINA_CONTRACT,
                "SCREENING_BENCHMARK_VINA_CHANGED",
                "private AutoDock Vina",
            ),
            (
                private_receptor,
                DEFAULT_RECEPTOR_CONTRACT,
                "SCREENING_BENCHMARK_RECEPTOR_CHANGED",
                "private receptor",
            ),
            (
                private_ligand,
                DEFAULT_LIGAND_CONTRACT,
                "SCREENING_BENCHMARK_LIGAND_CHANGED",
                "private ligand",
            ),
            (
                supplied_vina,
                DEFAULT_VINA_CONTRACT,
                "SCREENING_BENCHMARK_VINA_CHANGED",
                "source AutoDock Vina",
            ),
            (
                supplied_receptor,
                DEFAULT_RECEPTOR_CONTRACT,
                "SCREENING_BENCHMARK_RECEPTOR_CHANGED",
                "source receptor",
            ),
            (
                supplied_ligand,
                DEFAULT_LIGAND_CONTRACT,
                "SCREENING_BENCHMARK_LIGAND_CHANGED",
                "source ligand",
            ),
        ):
            _assert_file_contract(
                path,
                contract,
                code=code,
                label=label,
            )

        global_monitor.stop(sample_after_stop=True)
        benchmark_memory = global_monitor.evidence()
        global_monitor = None
        _assert_true(
            benchmark_memory["sample_count"] >= 1
            and benchmark_memory["peak_tree_working_set_bytes"] > 0
            and not benchmark_memory["sampling_errors"],
            "SCREENING_BENCHMARK_MEMORY_UNAVAILABLE",
            "Windows process-tree peak memory could not be measured.",
            details={"memory": benchmark_memory},
        )
        finished_ns = time.perf_counter_ns()
        wall_seconds = (finished_ns - global_started_ns) / 1_000_000_000.0
        process_durations = [
            float(event["duration_ms"]) for event in process_events
        ]
        per_process_peaks = [
            int(event["memory"]["peak_tree_working_set_bytes"])
            for event in process_events
        ]
        performance = {
            "wall_clock_seconds": wall_seconds,
            "phase_seconds": phase_seconds,
            "vina_process_duration_ms": {
                "minimum": min(process_durations),
                "maximum": max(process_durations),
                "mean": sum(process_durations) / len(process_durations),
            },
            "throughput_ligands_per_second": ligand_count / wall_seconds,
            "benchmark_process_tree_memory": benchmark_memory,
            "maximum_single_vina_process_tree_working_set_bytes": max(
                per_process_peaks
            ),
            "memory_measurement": (
                "No third-party dependency: Windows Toolhelp32 process "
                "enumeration and GetProcessMemoryInfo sampled every "
                f"{MEMORY_POLL_INTERVAL_MS} ms."
            ),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "verifier_id": VERIFIER_ID,
            "ok": True,
            "scope": {
                "software_resource_benchmark": True,
                "scientific_screening_claim": False,
                "network_or_download_used": False,
                "large_run_confirmed": bool(confirm_large_run),
                "maximum_supported_ligands": MAX_LIGAND_COUNT,
            },
            "acceptance": {
                **profile,
                "workflow_completed": True,
            },
            "fixed_contract": {
                "tool": tool_snapshot,
                "receptor": receptor_snapshot,
                "ligand": ligand_snapshot,
                "workload": workload_contract,
                "workload_sha256": _canonical_json_sha256(
                    workload_contract
                ),
            },
            "cancellation_and_recovery": {
                "request": cancel_evidence,
                "canceled_status": first_state["status"],
                "succeeded_before_cancel": first_succeeded,
                "remaining_queue_count": len(expected_remaining),
                "resume_status": resumed["screening"]["status"],
                "final_status": final_state["status"],
                "queue_order_preserved": True,
            },
            "results": result_validation,
            "performance": performance,
            "process_audit": {
                "event_count": len(process_events),
                "all_pids_positive": all(
                    isinstance(event.get("pid"), int)
                    and int(event["pid"]) > 0
                    for event in process_events
                ),
                "all_exit_codes_zero": all(
                    event.get("exit_code") == 0 for event in process_events
                ),
                "events_sha256": _canonical_json_sha256(process_events),
            },
            "scientific_interpretation": (
                "The repeated ligand is a deterministic resource workload. "
                "Every item is docked independently in stable serial order; "
                "scores are not experimental binding evidence."
            ),
        }
    except ScreeningBenchmarkError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve structured CLI boundary.
        caught = ScreeningBenchmarkError(
            "SCREENING_BENCHMARK_UNEXPECTED_ERROR",
            "The external screening benchmark failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
    finally:
        if global_monitor is not None:
            global_monitor.stop(sample_after_stop=True)
        environment_restored = {
            key: os.environ.get(key) == value
            if value is not None
            else key not in os.environ
            for key, value in previous_environment.items()
        }
        cleaned = False
        cleanup_error = ""
        if keep_workdir:
            cleaned = False
        else:
            try:
                shutil.rmtree(work_root)
                cleaned = not work_root.exists()
            except Exception as exc:  # noqa: BLE001
                cleanup_error = str(exc)
        environment = {
            "variables_restored": environment_restored,
            "work_directory": str(work_root),
            "work_directory_kept": bool(keep_workdir),
            "work_directory_cleaned": cleaned,
            "cleanup_error": cleanup_error,
        }
        if result is None:
            assert caught is not None
            result = {
                "schema_version": SCHEMA_VERSION,
                "verifier_id": VERIFIER_ID,
                "ok": False,
                "error": {
                    "code": caught.code,
                    "message": caught.message,
                    "details": caught.details,
                },
                "acceptance": {
                    **profile,
                    "workflow_completed": False,
                },
                "process_audit": {
                    "event_count": len(process_events),
                    "events_sha256": _canonical_json_sha256(process_events),
                },
            }
        result["environment"] = environment
        if (
            not all(environment_restored.values())
            or (not keep_workdir and not cleaned)
        ):
            previous_error = result.get("error")
            result["ok"] = False
            result["error"] = {
                "code": "SCREENING_BENCHMARK_CLEANUP_FAILED",
                "message": (
                    "The benchmark could not restore its environment or "
                    "remove its temporary project."
                ),
                "details": {
                    "environment": environment,
                    "previous_error": previous_error,
                },
            }
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    destination = path.expanduser().resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run DockStart's serial screening queue with real Vina processes, "
            "a live cancellation, recovery, and Windows process-tree memory "
            "measurement."
        )
    )
    parser.add_argument("--vina", default=str(DEFAULT_VINA_PATH))
    parser.add_argument("--receptor", default=str(DEFAULT_RECEPTOR_PATH))
    parser.add_argument("--ligand", default=str(DEFAULT_LIGAND_PATH))
    parser.add_argument(
        "--ligand-count",
        type=int,
        default=DEFAULT_LIGAND_COUNT,
    )
    parser.add_argument(
        "--cancel-after",
        type=int,
        default=DEFAULT_CANCEL_AFTER,
    )
    parser.add_argument(
        "--confirm-large-run",
        action="store_true",
        help="Required when --ligand-count exceeds 100.",
    )
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = verify_screening_batch_external(
        vina_path=arguments.vina,
        receptor_path=arguments.receptor,
        ligand_path=arguments.ligand,
        ligand_count=arguments.ligand_count,
        cancel_after=arguments.cancel_after,
        confirm_large_run=arguments.confirm_large_run,
        keep_workdir=arguments.keep_workdir,
    )
    if arguments.output is not None:
        _write_json(arguments.output, result)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
