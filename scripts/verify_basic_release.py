"""Run an installation-layout Basic docking regression against a packaged DockStart build."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:  # Direct script execution from the scripts directory.
    from dependency_license_bundle import DependencyLicenseBundleError, verify_dependency_license_bundle
except ModuleNotFoundError:  # Import through tests/tools from the repository root.
    from scripts.dependency_license_bundle import DependencyLicenseBundleError, verify_dependency_license_bundle


class BasicReleaseVerificationError(RuntimeError):
    """Raised when a packaged Basic release fails its acceptance gate."""


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.stat().st_size <= 0:
        raise BasicReleaseVerificationError(f"{label} is missing or empty: {path}")
    return path


def _require_existing_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise BasicReleaseVerificationError(f"{label} is missing: {path}")
    return path


def _run_json_module(
    python_exe: Path,
    backend_dir: Path,
    env: dict[str, str],
    module: str,
    *arguments: str,
    timeout: int = 120,
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(python_exe), "-B", "-m", module, *arguments],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise BasicReleaseVerificationError(
            f"{module} {' '.join(arguments)} failed with exit code {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise BasicReleaseVerificationError(
            f"{module} returned invalid JSON: {completed.stdout!r}",
        ) from exc
    if not isinstance(payload, dict):
        raise BasicReleaseVerificationError(f"{module} returned a non-object JSON payload.")
    if payload.get("ok") is False:
        raise BasicReleaseVerificationError(
            f"{module} {' '.join(arguments)} reported failure: {json.dumps(payload, ensure_ascii=False)}",
        )
    return payload


def _verify_runtime_boundary(python_exe: Path, resources_dir: Path) -> dict[str, Any]:
    manifest = json.loads(_require_file(resources_dir / "toolchain_manifest.json", "Basic manifest").read_text(encoding="utf-8"))
    if manifest.get("release_profile") != "basic_stable":
        raise BasicReleaseVerificationError("Packaged manifest is not the basic_stable profile.")
    if manifest.get("includes_bundled_rdkit") is not False or manifest.get("includes_bundled_meeko") is not False:
        raise BasicReleaseVerificationError("Packaged manifest incorrectly claims RDKit/Meeko are bundled.")
    for filename in (
        "DockStart-Apache-2.0.txt",
        "AutoDock-Vina_LICENSE.txt",
        "Python_LICENSE.txt",
        "3Dmol_LICENSE.txt",
        "React_LICENSE.txt",
        "React-DOM_LICENSE.txt",
        "Phosphor-Icons_LICENSE.txt",
        "Tauri_LICENSE_APACHE-2.0.txt",
        "Tauri_LICENSE_MIT.txt",
        "Tauri-plugin-dialog_LICENSE.spdx",
        "Serde_LICENSE-MIT.txt",
        "THIRD_PARTY_NOTICES.md",
    ):
        _require_file(resources_dir / "licenses" / filename, f"Packaged license {filename}")
    if (resources_dir / "python" / "Lib" / "ensurepip").exists():
        raise BasicReleaseVerificationError("Basic package contains untracked ensurepip wheels.")
    try:
        dependency_licenses = verify_dependency_license_bundle(resources_dir / "licenses" / "dependencies")
    except DependencyLicenseBundleError as exc:
        raise BasicReleaseVerificationError(f"Packaged dependency license bundle is invalid: {exc}") from exc

    probe = (
        "import importlib.util,json,pathlib,subprocess,datetime;"
        "print(json.dumps({name:importlib.util.find_spec(name) is not None "
        "for name in ('meeko','rdkit','numpy','scipy')}))"
    )
    completed = subprocess.run(
        [str(python_exe), "-I", "-B", "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise BasicReleaseVerificationError(f"Packaged Python probe failed: {completed.stderr.strip()}")
    modules = json.loads(completed.stdout.strip())
    unexpected = [name for name, available in modules.items() if available]
    if unexpected:
        raise BasicReleaseVerificationError(
            "Basic package contains preparation packages that must remain external: " + ", ".join(unexpected),
        )
    return {
        "manifest_profile": manifest.get("release_profile"),
        "scientific_modules": modules,
        "dependency_licenses": dependency_licenses,
    }


def verify_basic_release(
    release_dir: str | Path,
    work_dir: str | Path | None = None,
    *,
    keep_work_dir: bool = False,
) -> dict[str, Any]:
    release = Path(release_dir).expanduser().resolve()
    backend_dir = release / "backend"
    resources_dir = release / "resources"
    python_exe = _require_file(resources_dir / "python" / "python.exe", "Packaged backend Python")
    vina_exe = _require_file(resources_dir / "vina" / "vina.exe", "Packaged AutoDock Vina")
    _require_file(backend_dir / "dockstart_core" / "project.py", "Packaged DockStart backend")
    _require_file(resources_dir / "examples" / "basic_pdbqt" / "project.json", "Packaged Basic demo")

    runtime_boundary = _verify_runtime_boundary(python_exe, resources_dir)
    created_temp = work_dir is None
    work_root = (
        Path(tempfile.mkdtemp(prefix="DockStart Basic 稳定性 回归 "))
        if created_temp
        else Path(work_dir).expanduser().resolve()
    )
    work_root.mkdir(parents=True, exist_ok=True)
    settings_path = work_root / "empty settings.json"
    env = os.environ.copy()
    env.update(
        {
            "DOCKSTART_RESOURCE_DIR": str(release),
            "DOCKSTART_SETTINGS_PATH": str(settings_path),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )

    try:
        capabilities = _run_json_module(python_exe, backend_dir, env, "dockstart_core.capabilities", timeout=90)
        if not capabilities.get("basic_mode_available"):
            raise BasicReleaseVerificationError("Packaged capabilities do not expose Basic Mode.")
        if capabilities.get("assisted_mode_available"):
            raise BasicReleaseVerificationError("Basic package unexpectedly exposes Assisted Mode as bundled-ready.")
        if capabilities.get("vina_status", {}).get("source") != "bundled":
            raise BasicReleaseVerificationError("Basic package did not resolve the packaged Vina binary.")
        if capabilities.get("python_status", {}).get("source") != "bundled":
            raise BasicReleaseVerificationError("Basic package did not resolve the packaged backend Python.")

        demo = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.demo_projects",
            "create",
            str(work_root),
            "basic_pdbqt",
        )
        project_dir_text = str(demo.get("project_dir") or "")
        if not project_dir_text:
            project = demo.get("project") if isinstance(demo.get("project"), dict) else {}
            project_dir_text = str(project.get("project_dir") or "")
        if not project_dir_text:
            raise BasicReleaseVerificationError("Basic demo creation did not return project_dir.")
        project_dir = Path(project_dir_text).resolve()

        _run_json_module(python_exe, backend_dir, env, "dockstart_core.demo_projects", "validate", str(project_dir))
        _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "validate-run", str(project_dir))
        config = _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "generate-config", str(project_dir))
        prepared = _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "prepare-run", str(project_dir))
        run_id = str(prepared.get("run_id") or "")
        if not run_id:
            metadata = prepared.get("metadata") if isinstance(prepared.get("metadata"), dict) else {}
            run_id = str(metadata.get("run_id") or "")
        if not run_id:
            raise BasicReleaseVerificationError("prepare-run did not return a run_id.")

        executed = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "execute-run",
            str(project_dir),
            run_id,
            timeout=180,
        )
        metadata = executed.get("metadata") if isinstance(executed.get("metadata"), dict) else {}
        if metadata.get("status") != "finished" or metadata.get("exit_code") != 0:
            raise BasicReleaseVerificationError(f"Vina run did not finish successfully: {metadata}")
        if not metadata.get("command") or not metadata.get("started_at") or not metadata.get("finished_at"):
            raise BasicReleaseVerificationError("Run metadata is missing command or timestamp provenance.")

        analyzed = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "analyze-results",
            str(project_dir),
            run_id,
        )
        _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "export-report",
            str(project_dir),
            run_id,
        )
        pose = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.viewer",
            "load-pose",
            str(project_dir),
            run_id,
            "1",
        )
        if not pose.get("content") and not pose.get("pdbqt"):
            pose_data = pose.get("pose") if isinstance(pose.get("pose"), dict) else {}
            if not pose_data.get("content") and not pose_data.get("pdbqt"):
                raise BasicReleaseVerificationError("The first docking pose could not be loaded.")
        _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "load", str(project_dir))
        refreshed = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "load-run-metadata",
            str(project_dir),
            run_id,
        )
        refreshed_metadata = (
            refreshed.get("metadata") if isinstance(refreshed.get("metadata"), dict) else refreshed
        )
        if refreshed_metadata.get("best_affinity") is None:
            raise BasicReleaseVerificationError("Run metadata did not persist best_affinity after analysis.")
        if refreshed_metadata.get("vina_source") != "bundled":
            raise BasicReleaseVerificationError("Run metadata did not record bundled Vina provenance.")
        if not refreshed_metadata.get("vina_version"):
            raise BasicReleaseVerificationError("Run metadata did not record the Vina version.")
        command = refreshed_metadata.get("command")
        if not isinstance(command, list) or not command:
            raise BasicReleaseVerificationError("Run metadata did not record the executed command array.")
        if Path(str(command[0])).resolve() != vina_exe.resolve():
            raise BasicReleaseVerificationError("Run metadata command does not point to the packaged Vina binary.")
        input_hashes = refreshed_metadata.get("input_sha256")
        if not isinstance(input_hashes, dict) or any(
            len(str(input_hashes.get(key) or "")) != 64 for key in ("receptor", "ligand", "config")
        ):
            raise BasicReleaseVerificationError("Run metadata is missing input SHA256 provenance.")

        run_dir = project_dir / "runs" / run_id
        expected_files = {
            "config": project_dir / "configs" / "vina_config.txt",
            "metadata": run_dir / "metadata.json",
            "config_snapshot": run_dir / "config_snapshot.txt",
            "receptor_snapshot": run_dir / "inputs" / "receptor.pdbqt",
            "ligand_snapshot": run_dir / "inputs" / "ligand.pdbqt",
            "stdout": run_dir / "stdout.txt",
            "log": run_dir / "log.txt",
            "output": run_dir / "out.pdbqt",
            "run_scores": run_dir / "scores.csv",
            "scores": project_dir / "results" / "scores.csv",
            "run_report": run_dir / "docking_report.md",
            "report": project_dir / "reports" / "docking_report.md",
        }
        for label, path in expected_files.items():
            _require_file(path, label)
        _require_existing_file(run_dir / "stderr.txt", "stderr")

        with expected_files["scores"].open("r", encoding="utf-8-sig", newline="") as handle:
            scores = list(csv.DictReader(handle))
        if not scores:
            raise BasicReleaseVerificationError("scores.csv contains no docking poses.")
        affinity = float(scores[0]["affinity_kcal_mol"])
        if not math.isfinite(affinity):
            raise BasicReleaseVerificationError("The best affinity is not a finite number.")
        report_text = expected_files["report"].read_text(encoding="utf-8")
        if "Docking score 仅供结构结合趋势参考，不能替代实验验证。" not in report_text:
            raise BasicReleaseVerificationError("The packaged workflow report is missing the scientific disclaimer.")

        second_prepared = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "prepare-run",
            str(project_dir),
        )
        second_run_id = str(second_prepared.get("run_id") or "")
        if not second_run_id or second_run_id == run_id:
            raise BasicReleaseVerificationError("A repeated run did not allocate a new run_id.")
        second_executed = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "execute-run",
            str(project_dir),
            second_run_id,
            timeout=180,
        )
        second_metadata = (
            second_executed.get("metadata") if isinstance(second_executed.get("metadata"), dict) else {}
        )
        if second_metadata.get("status") != "finished" or second_metadata.get("exit_code") != 0:
            raise BasicReleaseVerificationError("The repeated packaged Vina run did not finish successfully.")
        _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "analyze-results",
            str(project_dir),
            second_run_id,
        )
        _require_file(project_dir / "runs" / second_run_id / "out.pdbqt", "repeated run output")
        _require_file(project_dir / "runs" / run_id / "out.pdbqt", "first run output after repeat")

        return {
            "ok": True,
            "profile": "basic_stable",
            "release_dir": str(release),
            "work_dir": str(work_root),
            "python": str(python_exe),
            "vina": str(vina_exe),
            "app_version": capabilities.get("app_version"),
            "runtime_boundary": runtime_boundary,
            "basic_mode_available": capabilities.get("basic_mode_available"),
            "assisted_mode_available": capabilities.get("assisted_mode_available"),
            "demo_mode_available": capabilities.get("demo_mode_available"),
            "config_file": config.get("config_file"),
            "run_id": run_id,
            "run_status": metadata.get("status"),
            "exit_code": metadata.get("exit_code"),
            "best_affinity": refreshed_metadata.get("best_affinity", analyzed.get("best_affinity", affinity)),
            "pose_count": len(scores),
            "repeat_run_id": second_run_id,
            "repeat_run_status": second_metadata.get("status"),
            "artifacts": {label: str(path) for label, path in expected_files.items()},
        }
    finally:
        if created_temp and not keep_work_dir:
            shutil.rmtree(work_root, ignore_errors=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a packaged DockStart Basic release.")
    parser.add_argument("release_dir")
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--keep-work-dir", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = verify_basic_release(
            args.release_dir,
            args.work_dir or None,
            keep_work_dir=args.keep_work_dir,
        )
    except Exception as exc:  # noqa: BLE001 - verification CLI emits structured failures.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
