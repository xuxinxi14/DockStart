"""AutoDock Vina detection and managed process execution.

All operating-system process calls for Vina live in this adapter.  The project
workflow therefore never needs to know how a Vina process is created,
streamed, queried, or terminated.
"""

from __future__ import annotations

import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dockstart_core.models import ToolCheckResult
from dockstart_core.process_utils import hidden_subprocess_kwargs
from dockstart_core.toolchain_paths import get_existing_bundled_vina_path

_VINA_CANDIDATES = ("vina", "vina.exe")


@dataclass(frozen=True)
class ManagedRunResult:
    """Result of a managed Vina process."""

    pid: int | None
    exit_code: int | None
    error: str = ""


RunStartedCallback = Callable[[int], None]
RunOutputCallback = Callable[[str, str], None]


def _find_vina() -> str:
    for candidate in _VINA_CANDIDATES:
        path = shutil.which(candidate)
        if path:
            return path
    return ""


def _parse_version(output: str) -> str:
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"(\d+(?:\.\d+)+)", first_line)
    return match.group(1) if match else first_line


def _run_version_check(path: str, source: str, bundled_path: str = "") -> ToolCheckResult:
    is_bundled = source == "bundled"

    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            path=path,
            message="检测到的 Vina 路径无法执行，请检查安装是否完整。",
            raw_error=str(exc),
            source=source,
            bundled_path=bundled_path,
            is_bundled=is_bundled,
        )
    except Exception as exc:  # noqa: BLE001 - convert detector failures to structured results.
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="error",
            path=path,
            message="检测 AutoDock Vina 时发生错误。",
            raw_error=str(exc),
            source=source,
            bundled_path=bundled_path,
            is_bundled=is_bundled,
        )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    raw_output = "\n".join(part for part in (stdout, stderr) if part)
    version = _parse_version(raw_output)

    if completed.returncode == 0:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version=version,
            path=path,
            message="已检测到 AutoDock Vina 命令行工具。",
            raw_error=stderr,
            source=source,
            bundled_path=bundled_path,
            is_bundled=is_bundled,
        )

    return ToolCheckResult(
        key="vina",
        name="AutoDock Vina",
        status="error",
        version=version,
        path=path,
        message="已找到 AutoDock Vina，但运行版本检测命令失败。",
        raw_error=raw_output,
        source=source,
        bundled_path=bundled_path,
        is_bundled=is_bundled,
    )


def detect(configured_path: str = "", bundled_path: str = "") -> ToolCheckResult:
    configured_path = configured_path.strip()
    resolved_bundled_path = (
        str(Path(bundled_path).expanduser())
        if bundled_path
        else str(get_existing_bundled_vina_path())
    )

    if Path(resolved_bundled_path).expanduser().is_file():
        return _run_version_check(
            resolved_bundled_path,
            "bundled",
            bundled_path=resolved_bundled_path,
        )

    if configured_path:
        configured = Path(configured_path).expanduser()
        if not configured.exists():
            return ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="missing",
                path=configured_path,
                message="用户配置的 vina.exe 路径不存在，请检查设置页中的 AutoDock Vina 路径。",
                source="configured",
                bundled_path=resolved_bundled_path,
                is_bundled=False,
            )
        return _run_version_check(
            str(configured),
            "configured",
            bundled_path=resolved_bundled_path,
        )

    path = _find_vina()
    if not path:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            message=(
                "未检测到内置 Vina，也未在 PATH 中检测到 vina 或 vina.exe。"
                "请放置 resources/tools/vina/vina.exe、在设置页配置路径，或将 Vina 加入 PATH。"
            ),
            source="missing",
            bundled_path=resolved_bundled_path,
            is_bundled=False,
        )

    return _run_version_check(
        path,
        "auto",
        bundled_path=resolved_bundled_path,
    )


def _normalized_executable(path: str) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        discovered = shutil.which(path)
        if discovered:
            candidate = Path(discovered)
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normcase(str(candidate))


def _windows_kernel32() -> object:
    """Return kernel32 with pointer-safe process API signatures."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32


def _windows_identity_from_handle(handle: int, pid: int) -> dict[str, object] | None:
    """Read identity from an already-open Windows process handle."""

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = _windows_kernel32()
        buffer_size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(buffer_size.value)
        if not kernel32.QueryFullProcessImageNameW(  # type: ignore[attr-defined]
            handle,
            0,
            buffer,
            ctypes.byref(buffer_size),
        ):
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(  # type: ignore[attr-defined]
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        creation_token = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return {
            "pid": pid,
            "executable_path": _normalized_executable(buffer.value),
            "creation_token": str(creation_token),
        }
    except Exception:
        return None


def get_process_identity(pid: int) -> dict[str, object] | None:
    """Read executable image and creation identity for a live process."""

    if pid <= 0 or not is_process_running(pid):
        return None
    if sys.platform == "win32":
        try:
            process_query_limited_information = 0x1000
            kernel32 = _windows_kernel32()
            handle = kernel32.OpenProcess(  # type: ignore[attr-defined]
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return None
            try:
                return _windows_identity_from_handle(handle, pid)
            finally:
                kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        except Exception:
            return None

    proc_dir = Path("/proc") / str(pid)
    try:
        executable = (proc_dir / "exe").resolve(strict=True)
        stat_parts = (proc_dir / "stat").read_text(encoding="utf-8").split()
        creation_token = stat_parts[21] if len(stat_parts) > 21 else ""
        if not creation_token:
            return None
        return {
            "pid": pid,
            "executable_path": _normalized_executable(str(executable)),
            "creation_token": creation_token,
        }
    except (OSError, IndexError):
        return None


def _verify_identity_value(
    pid: int,
    expected_executable: str,
    recorded_identity: dict[str, object] | None,
    current: dict[str, object] | None,
    *,
    running: bool,
) -> dict[str, object]:
    if not recorded_identity:
        return {
            "ok": False,
            "running": running,
            "identity": current,
            "message": "运行记录缺少进程创建身份，拒绝对该 PID 执行终止操作。",
        }
    if current is None:
        return {
            "ok": False,
            "running": running,
            "identity": None,
            "message": "无法确认 Vina 进程身份，拒绝终止该 PID。",
        }
    expected_path = _normalized_executable(expected_executable)
    actual_path = str(current.get("executable_path") or "")
    if not expected_path or actual_path != expected_path:
        return {
            "ok": False,
            "running": True,
            "identity": current,
            "message": "PID 对应的可执行文件与本次 Vina 运行记录不一致。",
        }
    recorded_path = str(recorded_identity.get("executable_path") or "")
    recorded_token = str(recorded_identity.get("creation_token") or "")
    if not recorded_path or not recorded_token:
        return {
            "ok": False,
            "running": True,
            "identity": current,
            "message": "运行记录中的进程身份不完整，拒绝终止。",
        }
    if recorded_path != actual_path or recorded_token != str(current.get("creation_token") or ""):
        return {
            "ok": False,
            "running": True,
            "identity": current,
            "message": "PID 已被其他进程复用，拒绝终止。",
        }
    return {"ok": True, "running": True, "identity": current, "message": "Vina 进程身份已确认。"}


def verify_process_identity(
    pid: int,
    expected_executable: str,
    recorded_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    """Verify a live PID before a read-only runtime status operation."""

    current = get_process_identity(pid)
    return _verify_identity_value(
        pid,
        expected_executable,
        recorded_identity,
        current,
        running=is_process_running(pid) if current is None else True,
    )


def _terminate_owned_process(process: subprocess.Popen[str], timeout_seconds: float = 5.0) -> None:
    """Best-effort cleanup for a process created by this adapter instance."""

    if process.poll() is not None:
        return
    try:
        # Popen.terminate()/kill() use the process handle owned by this Popen
        # instance on Windows.  Avoid taskkill-by-PID: a short-lived child can
        # exit and have its PID reused between poll() and an external taskkill.
        process.terminate()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=max(1.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            pass


def run_managed(
    command: list[str],
    cwd: str | Path,
    stdout_path: str | Path,
    stderr_path: str | Path,
    log_path: str | Path,
    *,
    on_started: RunStartedCallback | None = None,
    on_output: RunOutputCallback | None = None,
) -> ManagedRunResult:
    """Run Vina with durable streaming and guaranteed child cleanup."""

    if not command or any(not isinstance(part, str) or not part for part in command):
        return ManagedRunResult(None, None, "Vina command must be a non-empty argument array.")

    working_dir = Path(cwd).expanduser()
    stdout_file = Path(stdout_path).expanduser()
    stderr_file = Path(stderr_path).expanduser()
    log_file = Path(log_path).expanduser()
    process: subprocess.Popen[str] | None = None
    threads: list[threading.Thread] = []
    thread_errors: list[str] = []
    thread_error_event = threading.Event()
    exit_code: int | None = None

    try:
        for path in (stdout_file, stderr_file, log_file):
            path.parent.mkdir(parents=True, exist_ok=True)
        # Open every durable destination before spawning.  A permission or
        # path failure therefore cannot leave an untracked Vina process.
        # Exclusive creation also closes the validate-to-open symlink/hardlink
        # race for these files: a path inserted after validation fails before
        # Popen, while an already-open inode cannot be redirected by renaming.
        with (
            stdout_file.open("x", encoding="utf-8", newline="") as stdout_handle,
            stderr_file.open("x", encoding="utf-8", newline="") as stderr_handle,
            log_file.open("x", encoding="utf-8", newline="") as log_handle,
        ):
            popen_kwargs: dict[str, object] = hidden_subprocess_kwargs()
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(
                command,
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **popen_kwargs,
            )
            assert process.stdout is not None
            assert process.stderr is not None

            def drain(stream_name: str) -> None:
                source = process.stdout if stream_name == "stdout" else process.stderr
                destination = stdout_handle if stream_name == "stdout" else stderr_handle
                try:
                    for chunk in iter(lambda: source.read(1), ""):
                        destination.write(chunk)
                        destination.flush()
                        if stream_name == "stdout":
                            log_handle.write(chunk)
                            log_handle.flush()
                        if on_output is not None:
                            on_output(stream_name, chunk)
                except Exception as exc:  # noqa: BLE001 - main thread terminates the child.
                    thread_errors.append(f"{stream_name}: {exc}")
                    thread_error_event.set()
                finally:
                    try:
                        source.close()
                    except Exception:
                        pass

            threads = [
                threading.Thread(target=drain, args=("stdout",), daemon=True),
                threading.Thread(target=drain, args=("stderr",), daemon=True),
            ]
            for thread in threads:
                thread.start()

            if on_started is not None:
                on_started(process.pid)

            while process.poll() is None:
                if thread_error_event.wait(0.05):
                    raise RuntimeError("输出流处理失败：" + "; ".join(thread_errors))
            exit_code = process.returncode
            for thread in threads:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError("Vina 输出流线程未能正常结束。")
            if thread_errors:
                raise RuntimeError("输出流处理失败：" + "; ".join(thread_errors))
        return ManagedRunResult(process.pid, exit_code)
    except Exception as exc:  # noqa: BLE001 - adapter returns process failures as data.
        if process is not None:
            _terminate_owned_process(process)
        for thread in threads:
            thread.join(timeout=5)
        return ManagedRunResult(process.pid if process is not None else None, process.returncode if process else None, str(exc))


def is_process_running(pid: int) -> bool:
    """Return whether a recorded Vina PID is still active."""

    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = _windows_kernel32()
            handle = kernel32.OpenProcess(  # type: ignore[attr-defined]
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):  # type: ignore[attr-defined]
                    return False
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        except Exception:
            return False

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _terminate_windows_process_by_handle(
    pid: int,
    expected_executable: str,
    recorded_identity: dict[str, object] | None,
    timeout_seconds: float,
) -> dict[str, object]:
    """Verify and terminate the same Windows process object via one HANDLE."""

    try:
        import ctypes

        kernel32 = _windows_kernel32()
        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        access = process_terminate | process_query_limited_information | synchronize
        handle = kernel32.OpenProcess(access, False, pid)  # type: ignore[attr-defined]
        if not handle:
            return {
                "ok": False,
                "pid": pid,
                "message": "无法打开并锁定原 Vina 进程对象。",
                "raw_error": str(ctypes.get_last_error()),
            }
        try:
            current = _windows_identity_from_handle(handle, pid)
            verification = _verify_identity_value(
                pid,
                expected_executable,
                recorded_identity,
                current,
                running=current is not None,
            )
            if not verification.get("ok"):
                return {
                    "ok": False,
                    "pid": pid,
                    "message": str(verification.get("message") or "无法确认 Vina 进程身份。"),
                    "raw_error": "",
                    "identity": verification.get("identity"),
                }
            if not kernel32.TerminateProcess(handle, 1):  # type: ignore[attr-defined]
                return {
                    "ok": False,
                    "pid": pid,
                    "message": "无法终止已验证的 Vina 进程。",
                    "raw_error": str(ctypes.get_last_error()),
                }
            timeout_ms = max(1, int(max(1.0, timeout_seconds) * 1000))
            wait_result = kernel32.WaitForSingleObject(handle, timeout_ms)  # type: ignore[attr-defined]
            if wait_result != wait_object_0:
                return {
                    "ok": False,
                    "pid": pid,
                    "message": "已发送终止请求，但 Vina 进程未在期限内退出。",
                    "raw_error": f"WaitForSingleObject={wait_result}",
                }
            return {"ok": True, "pid": pid, "message": "Vina 进程已终止。", "raw_error": ""}
        finally:
            kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - cancellation is returned as structured data.
        return {"ok": False, "pid": pid, "message": "终止 Vina 进程时发生错误。", "raw_error": str(exc)}


def _terminate_unix_process(
    pid: int,
    expected_executable: str,
    recorded_identity: dict[str, object] | None,
    timeout_seconds: float,
) -> dict[str, object]:
    """Terminate a verified Unix process, preferring a PID file descriptor."""

    pidfd: int | None = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            pidfd = os.pidfd_open(pid)  # type: ignore[attr-defined]
        verification = verify_process_identity(pid, expected_executable, recorded_identity)
        if not verification.get("ok"):
            return {
                "ok": False,
                "pid": pid,
                "message": str(verification.get("message") or "无法确认 Vina 进程身份。"),
                "raw_error": "",
                "identity": verification.get("identity"),
            }

        def send(sig: int) -> bool:
            if pidfd is not None:
                signal.pidfd_send_signal(pidfd, sig)  # type: ignore[attr-defined]
                return True
            # Fallback platforms cannot pin a PID. Recheck the creation token
            # immediately before every signal and fail closed on any change.
            checked = verify_process_identity(pid, expected_executable, recorded_identity)
            if not checked.get("ok"):
                return False
            os.kill(pid, sig)
            return True

        if not send(signal.SIGTERM):
            return {"ok": False, "pid": pid, "message": "信号发送前进程身份发生变化，已拒绝终止。", "raw_error": ""}
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            if pidfd is not None:
                import select

                readable, _, _ = select.select([pidfd], [], [], 0.05)
                if readable:
                    return {"ok": True, "pid": pid, "message": "Vina 进程已终止。", "raw_error": ""}
            elif not is_process_running(pid):
                return {"ok": True, "pid": pid, "message": "Vina 进程已终止。", "raw_error": ""}
            else:
                time.sleep(0.05)
        if not send(signal.SIGKILL):
            return {"ok": False, "pid": pid, "message": "强制终止前进程身份发生变化，已拒绝操作。", "raw_error": ""}
        time.sleep(0.1)
        if pidfd is not None:
            import select

            readable, _, _ = select.select([pidfd], [], [], 1.0)
            gone = bool(readable)
        else:
            gone = not is_process_running(pid)
        if not gone:
            return {"ok": False, "pid": pid, "message": "已发送终止请求，但 Vina 进程仍在运行。", "raw_error": ""}
        return {"ok": True, "pid": pid, "message": "Vina 进程已终止。", "raw_error": ""}
    except ProcessLookupError:
        return {"ok": True, "pid": pid, "message": "原 Vina 进程已退出。", "raw_error": ""}
    except Exception as exc:  # noqa: BLE001 - cancellation is returned as structured data.
        return {"ok": False, "pid": pid, "message": "终止 Vina 进程时发生错误。", "raw_error": str(exc)}
    finally:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass


def terminate_process(
    pid: int,
    *,
    expected_executable: str,
    recorded_identity: dict[str, object] | None,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """Terminate a verified Vina process without signaling a reused PID."""

    if pid <= 0:
        return {"ok": False, "pid": pid, "message": "无效的 Vina 进程 PID。", "raw_error": ""}
    if sys.platform == "win32":
        return _terminate_windows_process_by_handle(
            pid,
            expected_executable,
            recorded_identity,
            timeout_seconds,
        )
    return _terminate_unix_process(
        pid,
        expected_executable,
        recorded_identity,
        timeout_seconds,
    )
