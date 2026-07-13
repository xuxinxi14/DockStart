"""Install the Assisted NSIS artifact in isolation and verify the real layout.

This is deliberately a release gate rather than an installer convenience tool.
It refuses to run when another DockStart installation is detected, installs only
below ``.release/install-gate``, runs the existing Assisted end-to-end verifier,
and then proves that the installed runtime was removed again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Iterable


class InstalledAssistedGateError(RuntimeError):
    """Raised when the real-install release gate cannot complete safely."""


@dataclass(frozen=True)
class GatePaths:
    repo_root: Path
    gate_root: Path
    install_root: Path
    diagnostics_root: Path
    verification_work: Path
    result_json: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _assert_path_within_gate(path: Path, gate_root: Path, *, allow_gate_root: bool = False) -> None:
    resolved_path = path.resolve(strict=False)
    resolved_gate = gate_root.resolve(strict=False)
    if not _is_relative_to(resolved_path, resolved_gate):
        raise InstalledAssistedGateError(f"Refusing a path outside .release/install-gate: {resolved_path}")
    if not allow_gate_root and resolved_path == resolved_gate:
        raise InstalledAssistedGateError("Refusing to use .release/install-gate itself as the install directory.")


def _resolve_gate_paths(repo_root: str | Path) -> GatePaths:
    repo = Path(repo_root).expanduser().resolve(strict=True)
    if not (repo / "scripts" / "verify_assisted_release.py").is_file():
        raise InstalledAssistedGateError(f"Not a DockStart repository root: {repo}")

    lexical_gate = Path(os.path.abspath(repo / ".release" / "install-gate"))
    resolved_gate = lexical_gate.resolve(strict=False)
    if not _is_relative_to(resolved_gate, repo):
        raise InstalledAssistedGateError(
            ".release/install-gate resolves outside the repository; remove the junction or symbolic link first.",
        )

    install_root = lexical_gate / "installed"
    diagnostics_root = lexical_gate / "diagnostics"
    verification_work = diagnostics_root / "verification-work"
    result_json = lexical_gate / "post-install-gate.json"
    for path in (install_root, diagnostics_root, verification_work, result_json):
        _assert_path_within_gate(path, lexical_gate)
    return GatePaths(repo, lexical_gate, install_root, diagnostics_root, verification_work, result_json)


def _directory_has_entries(path: Path) -> bool:
    return path.is_dir() and next(path.iterdir(), None) is not None


def _assert_install_root_empty(paths: GatePaths) -> None:
    _assert_path_within_gate(paths.install_root, paths.gate_root)
    if paths.install_root.exists() and not paths.install_root.is_dir():
        raise InstalledAssistedGateError(f"Install gate path is not a directory: {paths.install_root}")
    if _directory_has_entries(paths.install_root):
        raise InstalledAssistedGateError(
            "The isolated install directory is not empty. Inspect it and remove it manually before rerunning: "
            f"{paths.install_root}",
        )


def _normalized_location(value: str | None) -> str:
    if not value:
        return ""
    return os.path.normcase(os.path.abspath(os.path.expandvars(value.strip().strip('"'))))


def _registry_records() -> list[dict[str, str]]:
    if sys.platform != "win32":
        raise InstalledAssistedGateError("The installed Assisted release gate only runs on Windows.")
    import winreg  # pylint: disable=import-outside-toplevel

    roots = (("HKCU", winreg.HKEY_CURRENT_USER), ("HKLM", winreg.HKEY_LOCAL_MACHINE))
    views = (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    uninstall_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for root_name, root in roots:
        for view in views:
            try:
                parent = winreg.OpenKey(root, uninstall_path, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with parent:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(parent, child_name, 0, winreg.KEY_READ | view)
                    except OSError:
                        continue
                    with child:
                        try:
                            display_name = str(winreg.QueryValueEx(child, "DisplayName")[0] or "")
                        except OSError:
                            display_name = ""
                        if display_name.casefold() != "dockstart":
                            continue
                        try:
                            location = str(winreg.QueryValueEx(child, "InstallLocation")[0] or "")
                        except OSError:
                            location = ""
                        identity = (root_name, child_name, _normalized_location(location))
                        if identity in seen:
                            continue
                        seen.add(identity)
                        records.append(
                            {
                                "root": root_name,
                                "key": f"{uninstall_path}\\{child_name}",
                                "display_name": display_name,
                                "install_location": location,
                            },
                        )
    return records


def _publisher(repo_root: Path) -> str:
    config_path = repo_root / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        publisher = str(config["bundle"]["publisher"]).strip()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InstalledAssistedGateError(f"Cannot read the Tauri bundle publisher from {config_path}.") from exc
    if not publisher or "\\" in publisher or "/" in publisher:
        raise InstalledAssistedGateError("The Tauri bundle publisher is empty or unsafe for a registry key.")
    return publisher


def _manufacturer_registry_path(repo_root: Path) -> str:
    return rf"Software\{_publisher(repo_root)}\DockStart"


def _manufacturer_location(repo_root: Path) -> str:
    if sys.platform != "win32":
        return ""
    import winreg  # pylint: disable=import-outside-toplevel

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _manufacturer_registry_path(repo_root)) as key:
            return str(winreg.QueryValue(key, None) or "")
    except OSError:
        return ""


def _default_install_directories() -> Iterable[Path]:
    for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            yield Path(base) / "DockStart"


def _dockstart_process_is_running() -> bool:
    completed = subprocess.run(
        ["tasklist.exe", "/NH", "/FI", "IMAGENAME eq dockstart-desktop.exe"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise InstalledAssistedGateError("Unable to confirm whether DockStart is currently running.")
    return "dockstart-desktop.exe" in completed.stdout.casefold()


def _assert_no_existing_installation(paths: GatePaths) -> None:
    if _dockstart_process_is_running():
        raise InstalledAssistedGateError("DockStart is running. Close it before the isolated install gate.")

    records = _registry_records()
    manufacturer = _manufacturer_location(paths.repo_root)
    nonempty_defaults = [str(path) for path in _default_install_directories() if _directory_has_entries(path)]
    if records or manufacturer or nonempty_defaults:
        evidence = {
            "uninstall_records": records,
            "manufacturer_location": manufacturer,
            "nonempty_default_directories": nonempty_defaults,
        }
        raise InstalledAssistedGateError(
            "An existing DockStart installation was detected. The release gate refuses to overwrite its registry, "
            "shortcuts, or files. Uninstall it or run the gate in a disposable Windows account. Evidence: "
            + json.dumps(evidence, ensure_ascii=False),
        )

    _assert_install_root_empty(paths)


def _nsis_install_command(installer: Path, install_root: Path) -> list[str]:
    # /D must remain the final NSIS argument. /NS prevents shortcut creation.
    return [str(installer), "/S", "/NS", f"/D={install_root}"]


def _nsis_uninstall_command(uninstaller_copy: Path, install_root: Path) -> list[str]:
    # Run a copy outside $INSTDIR so the real uninstaller and directory can be deleted.
    # NSIS requires _?= to be the final argument.
    return [str(uninstaller_copy), "/S", f"_?={install_root}"]


def _run_logged(
    command: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return completed


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _assert_installed_layout(paths: GatePaths) -> None:
    required = (
        paths.install_root / "dockstart-desktop.exe",
        paths.install_root / "uninstall.exe",
        paths.install_root / "backend" / "dockstart_core" / "project.py",
        paths.install_root / "resources" / "python" / "python.exe",
        paths.install_root / "resources" / "vina" / "vina.exe",
        paths.install_root / "resources" / "toolchain_manifest.json",
    )
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size <= 0]
    if missing:
        raise InstalledAssistedGateError(
            "NSIS did not create the expected Assisted layout in .release/install-gate: "
            + json.dumps(missing, ensure_ascii=False),
        )

    expected = _normalized_location(str(paths.install_root))
    installed_locations = {
        _normalized_location(record.get("install_location")) for record in _registry_records()
    }
    manufacturer = _normalized_location(_manufacturer_location(paths.repo_root))
    if expected not in installed_locations or manufacturer != expected:
        raise InstalledAssistedGateError(
            "NSIS registry provenance does not point to the isolated install directory; refusing to continue.",
        )


def _run_post_install_verifier(paths: GatePaths) -> dict[str, Any]:
    verifier = paths.repo_root / "scripts" / "verify_assisted_release.py"
    paths.verification_work.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        str(verifier),
        str(paths.install_root),
        "--work-dir",
        str(paths.verification_work),
        "--gate",
        "post-install",
        "--keep-work-dir",
    ]
    completed = _run_logged(
        command,
        cwd=paths.install_root,
        stdout_path=paths.diagnostics_root / "verify.stdout.log",
        stderr_path=paths.diagnostics_root / "verify.stderr.log",
        timeout=900,
    )
    if completed.returncode != 0:
        raise InstalledAssistedGateError(
            f"verify_assisted_release.py --gate post-install failed with exit code {completed.returncode}. "
            f"See {paths.diagnostics_root / 'verify.stderr.log'}.",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstalledAssistedGateError("The post-install verifier did not return JSON.") from exc
    if payload.get("ok") is not True or payload.get("gate") != "post-install":
        raise InstalledAssistedGateError(f"Unexpected post-install verifier result: {payload!r}")
    return payload


def _remove_gate_manufacturer_key(repo_root: Path, install_root: Path) -> bool:
    if sys.platform != "win32":
        return False
    import winreg  # pylint: disable=import-outside-toplevel

    if _normalized_location(_manufacturer_location(repo_root)) != _normalized_location(str(install_root)):
        return False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _manufacturer_registry_path(repo_root))
    except FileNotFoundError:
        return False
    return True


def _wait_for_uninstall(install_root: Path, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    runtime = install_root / "resources" / "python"
    while time.monotonic() < deadline:
        if not install_root.exists() and not runtime.exists():
            return
        time.sleep(0.5)


def _capture_residue(install_root: Path, limit: int = 200) -> list[str]:
    if not install_root.exists():
        return []
    if install_root.is_file():
        return [install_root.name]
    return [path.relative_to(install_root).as_posix() for path in islice(install_root.rglob("*"), limit)]


def _cleanup_installed_layout(paths: GatePaths) -> dict[str, Any]:
    _assert_path_within_gate(paths.install_root, paths.gate_root)
    uninstaller = paths.install_root / "uninstall.exe"
    uninstaller_exit_code: int | None = None
    cleanup_error = ""
    if uninstaller.is_file():
        uninstaller_copy = paths.diagnostics_root / "uninstall-gate.exe"
        shutil.copy2(uninstaller, uninstaller_copy)
        try:
            completed = _run_logged(
                _nsis_uninstall_command(uninstaller_copy, paths.install_root),
                cwd=paths.diagnostics_root,
                stdout_path=paths.diagnostics_root / "uninstall.stdout.log",
                stderr_path=paths.diagnostics_root / "uninstall.stderr.log",
                timeout=600,
            )
            uninstaller_exit_code = completed.returncode
        except Exception as exc:  # noqa: BLE001 - cleanup diagnostics must survive the primary failure.
            cleanup_error = str(exc)
        finally:
            uninstaller_copy.unlink(missing_ok=True)
        _wait_for_uninstall(paths.install_root)

    manufacturer_key_removed = _remove_gate_manufacturer_key(paths.repo_root, paths.install_root)
    residue = _capture_residue(paths.install_root)
    runtime_residue = (paths.install_root / "resources" / "python").exists()
    uninstall_record_residue = [
        record
        for record in _registry_records()
        if _normalized_location(record.get("install_location")) == _normalized_location(str(paths.install_root))
    ]
    clean = (
        uninstaller_exit_code == 0
        and not cleanup_error
        and not residue
        and not runtime_residue
        and not uninstall_record_residue
    )

    # A failed uninstaller must not leave an executable runtime behind. Preserve
    # the residue inventory in diagnostics, then remove only the path already
    # proven to be below .release/install-gate.
    forced_cleanup = False
    if residue:
        _atomic_write_json(
            paths.diagnostics_root / "uninstall-residue.json",
            {"captured_at": _utc_now(), "files": residue, "runtime_residue": runtime_residue},
        )
        shutil.rmtree(paths.install_root, ignore_errors=True)
        forced_cleanup = not paths.install_root.exists()
    elif paths.install_root.is_dir():
        paths.install_root.rmdir()

    return {
        "exit_code": uninstaller_exit_code,
        "clean": clean,
        "cleanup_error": cleanup_error,
        "install_directory_removed": not paths.install_root.exists(),
        "runtime_residue_detected": runtime_residue,
        "uninstall_record_residue": bool(uninstall_record_residue),
        "manufacturer_key_removed": manufacturer_key_removed,
        "forced_cleanup_after_failure": forced_cleanup,
        "residue_count": len(residue),
    }


def run_installed_gate(repo_root: str | Path, installer_path: str | Path) -> dict[str, Any]:
    paths = _resolve_gate_paths(repo_root)
    installer = Path(installer_path).expanduser().resolve(strict=True)
    if installer.suffix.casefold() != ".exe" or "setup" not in installer.name.casefold():
        raise InstalledAssistedGateError(f"Expected an NSIS setup executable, got: {installer}")
    if _is_relative_to(installer, paths.install_root.resolve(strict=False)):
        raise InstalledAssistedGateError("The installer artifact must not be inside the isolated install directory.")

    paths.gate_root.mkdir(parents=True, exist_ok=True)
    _assert_no_existing_installation(paths)
    if paths.diagnostics_root.exists():
        # Diagnostics belong to a prior completed attempt, not an installed
        # runtime. Rotate them without touching the guarded install directory.
        archive = paths.gate_root / ("diagnostics-previous-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        paths.diagnostics_root.rename(archive)
    paths.diagnostics_root.mkdir(parents=True, exist_ok=False)

    installer_hash = _sha256(installer)
    result: dict[str, Any] = {
        "status": "failed",
        "gate": "post-install",
        "started_at": _utc_now(),
        "installer_name": installer.name,
        "installer_sha256": installer_hash,
        "install_method": "nsis_silent_custom_directory",
        "install_root": ".release/install-gate/installed",
        "diagnostics": ".release/install-gate/diagnostics",
        "verification": None,
        "uninstall": None,
        "error": "",
    }
    primary_error: Exception | None = None
    installed = False
    try:
        completed = _run_logged(
            _nsis_install_command(installer, paths.install_root),
            cwd=paths.repo_root,
            stdout_path=paths.diagnostics_root / "install.stdout.log",
            stderr_path=paths.diagnostics_root / "install.stderr.log",
            timeout=900,
        )
        if completed.returncode != 0:
            raise InstalledAssistedGateError(f"NSIS silent install failed with exit code {completed.returncode}.")
        installed = (paths.install_root / "uninstall.exe").is_file()
        _assert_installed_layout(paths)
        installed = True
        verification = _run_post_install_verifier(paths)
        result["verification"] = {
            "ok": True,
            "gate": verification.get("gate"),
            "run_status": verification.get("run_status"),
            "run_id": verification.get("run_id"),
            "best_affinity": verification.get("best_affinity"),
            "offline": verification.get("offline"),
            "no_generated_bytecode": verification.get("no_generated_bytecode"),
        }
    except Exception as exc:  # noqa: BLE001 - gate reports structured diagnostics for every failure.
        primary_error = exc
        result["error"] = str(exc)
    finally:
        cleanup = _cleanup_installed_layout(paths) if installed or paths.install_root.exists() else {
            "exit_code": None,
            "clean": True,
            "cleanup_error": "",
            "install_directory_removed": True,
            "runtime_residue_detected": False,
            "uninstall_record_residue": False,
            "manufacturer_key_removed": False,
            "forced_cleanup_after_failure": False,
            "residue_count": 0,
        }
        result["uninstall"] = cleanup

    if primary_error is None and result.get("verification", {}).get("ok") is True and cleanup["clean"]:
        result["status"] = "passed"
        result["error"] = ""
        shutil.rmtree(paths.verification_work, ignore_errors=True)
    elif primary_error is None and not cleanup["clean"]:
        result["error"] = "Silent uninstall left files, runtime, registry records, or returned a failure code."

    result["completed_at"] = _utc_now()
    _atomic_write_json(paths.result_json, result)
    if result["status"] != "passed":
        raise InstalledAssistedGateError(result["error"] or "Installed Assisted gate failed.")
    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real NSIS install/uninstall gate for Assisted Stable.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--installer", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    paths: GatePaths | None = None
    try:
        paths = _resolve_gate_paths(args.repo_root)
        result = run_installed_gate(args.repo_root, args.installer)
    except Exception as exc:  # noqa: BLE001 - CLI must preserve a machine-readable failure.
        payload = {"status": "failed", "gate": "post-install", "error": str(exc)}
        if paths is not None and not paths.result_json.exists():
            _atomic_write_json(paths.result_json, {**payload, "completed_at": _utc_now()})
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
