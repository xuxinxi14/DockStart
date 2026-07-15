"""Install and verify a DockStart Basic NSIS artifact in isolation.

The gate refuses to run over an existing DockStart installation, installs only
below ``.release/basic-install-gate``, runs the Basic packaged-layout verifier
against the actual installed files, and then proves that silent uninstall left
no application runtime or uninstall record behind.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


class InstalledBasicGateError(RuntimeError):
    """Raised when the real-install Basic release gate cannot complete safely."""


def _load_shared_install_helpers() -> Any:
    module_name = "_dockstart_installed_release_helpers"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    script_path = Path(__file__).with_name("verify_installed_assisted_release.py")
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise InstalledBasicGateError(f"Cannot load shared NSIS gate helpers: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_SHARED = _load_shared_install_helpers()
GatePaths = _SHARED.GatePaths
_utc_now = _SHARED._utc_now
_sha256 = _SHARED._sha256
_is_relative_to = _SHARED._is_relative_to
_directory_has_entries = _SHARED._directory_has_entries
_normalized_location = _SHARED._normalized_location
_registry_records = _SHARED._registry_records
_manufacturer_location = _SHARED._manufacturer_location
_default_install_directories = _SHARED._default_install_directories
_dockstart_process_is_running = _SHARED._dockstart_process_is_running
_nsis_install_command = _SHARED._nsis_install_command
_nsis_uninstall_command = _SHARED._nsis_uninstall_command
_run_logged = _SHARED._run_logged
_atomic_write_json = _SHARED._atomic_write_json
_cleanup_installed_layout = _SHARED._cleanup_installed_layout


def _assert_path_within_gate(path: Path, gate_root: Path, *, allow_gate_root: bool = False) -> None:
    resolved_path = path.resolve(strict=False)
    resolved_gate = gate_root.resolve(strict=False)
    if not _is_relative_to(resolved_path, resolved_gate):
        raise InstalledBasicGateError(
            f"Refusing a path outside .release/basic-install-gate: {resolved_path}",
        )
    if not allow_gate_root and resolved_path == resolved_gate:
        raise InstalledBasicGateError(
            "Refusing to use .release/basic-install-gate itself as the install directory.",
        )


def _resolve_gate_paths(repo_root: str | Path) -> GatePaths:
    repo = Path(repo_root).expanduser().resolve(strict=True)
    if not (repo / "scripts" / "verify_basic_release.py").is_file():
        raise InstalledBasicGateError(f"Not a DockStart repository root: {repo}")

    lexical_gate = Path(os.path.abspath(repo / ".release" / "basic-install-gate"))
    resolved_gate = lexical_gate.resolve(strict=False)
    if not _is_relative_to(resolved_gate, repo):
        raise InstalledBasicGateError(
            ".release/basic-install-gate resolves outside the repository; "
            "remove the junction or symbolic link first.",
        )

    install_root = lexical_gate / "installed"
    diagnostics_root = lexical_gate / "diagnostics"
    verification_work = diagnostics_root / "verification-work"
    result_json = lexical_gate / "post-install-gate.json"
    for path in (install_root, diagnostics_root, verification_work, result_json):
        _assert_path_within_gate(path, lexical_gate)
    return GatePaths(repo, lexical_gate, install_root, diagnostics_root, verification_work, result_json)


def _assert_install_root_empty(paths: GatePaths) -> None:
    _assert_path_within_gate(paths.install_root, paths.gate_root)
    if paths.install_root.exists() and not paths.install_root.is_dir():
        raise InstalledBasicGateError(f"Install gate path is not a directory: {paths.install_root}")
    if _directory_has_entries(paths.install_root):
        raise InstalledBasicGateError(
            "The isolated Basic install directory is not empty. Inspect it and remove it manually before rerunning: "
            f"{paths.install_root}",
        )


def _assert_no_existing_installation(paths: GatePaths) -> None:
    if _dockstart_process_is_running():
        raise InstalledBasicGateError("DockStart is running. Close it before the isolated Basic install gate.")

    records = _registry_records()
    manufacturer = _manufacturer_location(paths.repo_root)
    nonempty_defaults = [str(path) for path in _default_install_directories() if _directory_has_entries(path)]
    if records or manufacturer or nonempty_defaults:
        evidence = {
            "uninstall_records": records,
            "manufacturer_location": manufacturer,
            "nonempty_default_directories": nonempty_defaults,
        }
        raise InstalledBasicGateError(
            "An existing DockStart installation was detected. The Basic release gate refuses to overwrite its "
            "registry, shortcuts, or files. Uninstall it or run the gate in a disposable Windows account. Evidence: "
            + json.dumps(evidence, ensure_ascii=False),
        )

    _assert_install_root_empty(paths)


def _assert_installed_layout(paths: GatePaths) -> None:
    manifest_path = paths.install_root / "resources" / "toolchain_manifest.json"
    required = (
        paths.install_root / "dockstart-desktop.exe",
        paths.install_root / "uninstall.exe",
        paths.install_root / "backend" / "dockstart_core" / "project.py",
        paths.install_root / "resources" / "python" / "python.exe",
        paths.install_root / "resources" / "vina" / "vina.exe",
        manifest_path,
    )
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size <= 0]
    if missing:
        raise InstalledBasicGateError(
            "NSIS did not create the expected Basic layout in .release/basic-install-gate: "
            + json.dumps(missing, ensure_ascii=False),
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstalledBasicGateError(f"Cannot read the installed Basic manifest: {manifest_path}") from exc
    if manifest.get("release_profile") != "basic_stable":
        raise InstalledBasicGateError("The installed NSIS layout is not the basic_stable profile.")
    if manifest.get("includes_bundled_rdkit") is not False or manifest.get("includes_bundled_meeko") is not False:
        raise InstalledBasicGateError("The installed Basic manifest incorrectly claims RDKit/Meeko are bundled.")

    expected = _normalized_location(str(paths.install_root))
    installed_locations = {
        _normalized_location(record.get("install_location")) for record in _registry_records()
    }
    manufacturer = _normalized_location(_manufacturer_location(paths.repo_root))
    if expected not in installed_locations or manufacturer != expected:
        raise InstalledBasicGateError(
            "NSIS registry provenance does not point to the isolated Basic install directory; refusing to continue.",
        )


def _generated_bytecode_entries(root: Path, limit: int = 50) -> list[str]:
    entries: list[str] = []
    for path in root.rglob("*"):
        if path.name == "__pycache__" or (path.is_file() and path.suffix.casefold() in {".pyc", ".pyo"}):
            entries.append(path.relative_to(root).as_posix())
            if len(entries) >= limit:
                break
    return entries


def _run_post_install_verifier(paths: GatePaths) -> dict[str, Any]:
    verifier = paths.repo_root / "scripts" / "verify_basic_release.py"
    paths.verification_work.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        str(verifier),
        str(paths.install_root),
        "--work-dir",
        str(paths.verification_work),
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
        raise InstalledBasicGateError(
            f"verify_basic_release.py failed with exit code {completed.returncode}. "
            f"See {paths.diagnostics_root / 'verify.stderr.log'}.",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstalledBasicGateError("The installed Basic verifier did not return JSON.") from exc
    if payload.get("ok") is not True or payload.get("profile") != "basic_stable":
        raise InstalledBasicGateError(f"Unexpected installed Basic verifier result: {payload!r}")
    if payload.get("run_status") != "finished" or payload.get("repeat_run_status") != "finished":
        raise InstalledBasicGateError("The installed Basic verifier did not complete two real Vina runs.")
    if payload.get("exit_code") != 0:
        raise InstalledBasicGateError("The first installed Basic Vina run did not return exit code 0.")
    if payload.get("basic_mode_available") is not True or payload.get("assisted_mode_available") is not False:
        raise InstalledBasicGateError(
            "The installed runtime exposed an unexpected Basic/Assisted capability boundary.",
        )
    bytecode = _generated_bytecode_entries(paths.install_root)
    if bytecode:
        raise InstalledBasicGateError(
            "The installed Basic verification generated Python cache files: "
            + json.dumps(bytecode, ensure_ascii=False),
        )
    return payload


def _clean_uninstall_result() -> dict[str, Any]:
    return {
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


def _failed_cleanup_result(paths: GatePaths, error: Exception) -> dict[str, Any]:
    return {
        "exit_code": None,
        "clean": False,
        "cleanup_error": str(error),
        "install_directory_removed": not paths.install_root.exists(),
        "runtime_residue_detected": (paths.install_root / "resources" / "python").exists(),
        "uninstall_record_residue": True,
        "manufacturer_key_removed": False,
        "forced_cleanup_after_failure": False,
        "residue_count": -1,
    }


def run_installed_gate(repo_root: str | Path, installer_path: str | Path) -> dict[str, Any]:
    paths = _resolve_gate_paths(repo_root)
    installer = Path(installer_path).expanduser().resolve(strict=True)
    installer_name = installer.name.casefold()
    if installer.suffix.casefold() != ".exe" or "setup" not in installer_name or "basic" not in installer_name:
        raise InstalledBasicGateError(f"Expected a Basic NSIS setup executable, got: {installer}")
    if _is_relative_to(installer, paths.install_root.resolve(strict=False)):
        raise InstalledBasicGateError("The installer artifact must not be inside the isolated install directory.")

    paths.gate_root.mkdir(parents=True, exist_ok=True)
    if paths.result_json.exists():
        previous_result = paths.gate_root / (
            "post-install-gate-previous-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"
        )
        paths.result_json.rename(previous_result)
    _assert_no_existing_installation(paths)
    if paths.diagnostics_root.exists():
        archive = paths.gate_root / ("diagnostics-previous-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        paths.diagnostics_root.rename(archive)
    paths.diagnostics_root.mkdir(parents=True, exist_ok=False)

    result: dict[str, Any] = {
        "status": "failed",
        "gate": "post-install",
        "profile": "basic_stable",
        "started_at": _utc_now(),
        "installer_name": installer.name,
        "installer_sha256": _sha256(installer),
        "install_method": "nsis_silent_custom_directory",
        "install_root": ".release/basic-install-gate/installed",
        "diagnostics": ".release/basic-install-gate/diagnostics",
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
            raise InstalledBasicGateError(f"NSIS silent install failed with exit code {completed.returncode}.")
        installed = (paths.install_root / "uninstall.exe").is_file()
        _assert_installed_layout(paths)
        installed = True
        verification = _run_post_install_verifier(paths)
        result["verification"] = {
            "ok": True,
            "profile": verification.get("profile"),
            "run_status": verification.get("run_status"),
            "run_id": verification.get("run_id"),
            "best_affinity": verification.get("best_affinity"),
            "pose_count": verification.get("pose_count"),
            "repeat_run_id": verification.get("repeat_run_id"),
            "repeat_run_status": verification.get("repeat_run_status"),
            "basic_mode_available": verification.get("basic_mode_available"),
            "assisted_mode_available": verification.get("assisted_mode_available"),
            "no_generated_bytecode": True,
        }
    except Exception as exc:  # noqa: BLE001 - the result must preserve every release-gate failure.
        primary_error = exc
        result["error"] = str(exc)
    finally:
        try:
            cleanup = (
                _cleanup_installed_layout(paths)
                if installed or paths.install_root.exists()
                else _clean_uninstall_result()
            )
        except Exception as cleanup_error:  # noqa: BLE001 - cleanup failure must remain machine-readable.
            cleanup = _failed_cleanup_result(paths, cleanup_error)
            if primary_error is None:
                primary_error = cleanup_error
                result["error"] = f"Silent uninstall cleanup failed: {cleanup_error}"
            else:
                result["error"] += f" Cleanup also failed: {cleanup_error}"
        result["uninstall"] = cleanup

    verification_result = result.get("verification")
    verification_ok = isinstance(verification_result, dict) and verification_result.get("ok") is True
    if primary_error is None and verification_ok and cleanup["clean"]:
        result["status"] = "passed"
        result["error"] = ""
        shutil.rmtree(paths.verification_work, ignore_errors=True)
    elif primary_error is None and not cleanup["clean"]:
        result["error"] = "Silent uninstall left files, runtime, registry records, or returned a failure code."

    result["completed_at"] = _utc_now()
    _atomic_write_json(paths.result_json, result)
    if result["status"] != "passed":
        raise InstalledBasicGateError(result["error"] or "Installed Basic gate failed.")
    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real NSIS install/uninstall gate for Basic Stable.")
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
        payload: dict[str, Any] = {
            "status": "failed",
            "gate": "post-install",
            "profile": "basic_stable",
            "error": str(exc),
        }
        if paths is not None:
            if paths.result_json.exists():
                try:
                    payload = json.loads(paths.result_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            else:
                _atomic_write_json(paths.result_json, {**payload, "completed_at": _utc_now()})
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
