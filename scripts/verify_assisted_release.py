"""Verify an offline Assisted layout with real preparation and docking."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

try:  # Direct script execution from the scripts directory.
    from dependency_license_bundle import DependencyLicenseBundleError, verify_dependency_license_bundle
except ModuleNotFoundError:  # Import through tests/tools from the repository root.
    from scripts.dependency_license_bundle import DependencyLicenseBundleError, verify_dependency_license_bundle


class AssistedReleaseVerificationError(RuntimeError):
    """Raised when an Assisted pre-package or installed-layout gate fails."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(path: Path, label: str, *, allow_empty: bool = False) -> Path:
    if not path.is_file() or (not allow_empty and path.stat().st_size <= 0):
        raise AssistedReleaseVerificationError(f"{label} is missing or empty: {path}")
    return path


def _runtime_tree_fingerprint(
    path: Path,
    *,
    excluded_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    digest = hashlib.sha256()
    excluded_parts = tuple(PurePosixPath(root).parts for root in excluded_roots)
    files = sorted(
        (
            item
            for item in path.rglob("*")
            if item.is_file()
            and not any(
                item.relative_to(path).parts[: len(parts)] == parts
                for parts in excluded_parts
            )
        ),
        key=lambda item: item.as_posix().lower(),
    )
    total_size = 0
    for item in files:
        relative = item.relative_to(path).as_posix()
        size = item.stat().st_size
        total_size += size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "file_count": len(files), "size_bytes": total_size}


def _runtime_tree_sha256(path: Path) -> str:
    return str(_runtime_tree_fingerprint(path)["sha256"])


def _run_json_module(
    python_exe: Path,
    backend_dir: Path,
    env: dict[str, str],
    module: str,
    *arguments: str,
    timeout: int = 180,
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
        raise AssistedReleaseVerificationError(
            f"{module} {' '.join(arguments)} exited {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssistedReleaseVerificationError(
            f"{module} returned invalid JSON: {completed.stdout!r}",
        ) from exc
    if not isinstance(payload, dict):
        raise AssistedReleaseVerificationError(f"{module} returned a non-object JSON payload.")
    if payload.get("ok") is False:
        raise AssistedReleaseVerificationError(
            f"{module} {' '.join(arguments)} reported failure: {json.dumps(payload, ensure_ascii=False)}",
        )
    return payload


def _convert_pdb_fixture_to_cif(
    python_exe: Path,
    pdb_path: Path,
    cif_path: Path,
    env: dict[str, str],
) -> None:
    """Create an offline CIF fixture with the packaged Gemmi runtime."""

    script = (
        "import gemmi,sys;"
        "structure=gemmi.read_structure(sys.argv[1]);"
        "structure.make_mmcif_document().write_file(sys.argv[2])"
    )
    completed = subprocess.run(
        [str(python_exe), "-I", "-B", "-c", script, str(pdb_path), str(cif_path)],
        cwd=cif_path.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise AssistedReleaseVerificationError(
            "Packaged Gemmi could not create the CIF release fixture: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )
    _require_file(cif_path, "Assisted CIF receptor fixture")


def _runtime_probe(python_exe: Path, layout: Path, manifest: dict[str, Any]) -> dict[str, str]:
    expected_packages = manifest.get("packages")
    if not isinstance(expected_packages, dict):
        raise AssistedReleaseVerificationError("Assisted manifest has no package inventory.")
    expected = {str(name).lower(): str(item.get("version") or "") for name, item in expected_packages.items()}
    script = (
        "import importlib.metadata as m,json;"
        "names=" + repr(sorted(expected)) + ";"
        "print(json.dumps({n:m.version(n) for n in names},sort_keys=True))"
    )
    completed = subprocess.run(
        [str(python_exe), "-I", "-B", "-c", script],
        cwd=layout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    if completed.returncode != 0:
        raise AssistedReleaseVerificationError(
            "Assisted package inventory probe failed: " + (completed.stderr.strip() or completed.stdout.strip()),
        )
    actual = json.loads(completed.stdout.strip())
    if actual != expected:
        raise AssistedReleaseVerificationError(f"Assisted package versions changed: expected {expected}, got {actual}")

    for module in ("meeko.cli.mk_prepare_ligand", "meeko.cli.mk_prepare_receptor"):
        help_result = subprocess.run(
            [str(python_exe), "-I", "-B", "-m", module, "--help"],
            cwd=layout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if help_result.returncode != 0 or "usage:" not in help_result.stdout.lower():
            raise AssistedReleaseVerificationError(f"Meeko module entry point is unavailable: {module}")
    return actual


def _verify_legal_resources(resources_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
    required_licenses = {
        "dockstart": resources_dir / "licenses" / "DockStart-Apache-2.0.txt",
        "3dmol": resources_dir / "licenses" / "3Dmol_LICENSE.txt",
        "react": resources_dir / "licenses" / "React_LICENSE.txt",
        "react_dom": resources_dir / "licenses" / "React-DOM_LICENSE.txt",
        "phosphor_icons": resources_dir / "licenses" / "Phosphor-Icons_LICENSE.txt",
        "tauri_apache": resources_dir / "licenses" / "Tauri_LICENSE_APACHE-2.0.txt",
        "tauri_mit": resources_dir / "licenses" / "Tauri_LICENSE_MIT.txt",
        "tauri_dialog": resources_dir / "licenses" / "Tauri-plugin-dialog_LICENSE.spdx",
        "serde": resources_dir / "licenses" / "Serde_LICENSE-MIT.txt",
        "meeko": resources_dir / "licenses" / "Meeko-LGPL-2.1.txt",
        "rdkit": resources_dir / "licenses" / "RDKit-BSD-3-Clause.md",
        "numpy": resources_dir / "licenses" / "NumPy_LICENSE.txt",
        "scipy": resources_dir / "licenses" / "SciPy_LICENSE.txt",
        "gemmi": resources_dir / "licenses" / "Gemmi-MPL-2.0.txt",
        "pillow": resources_dir / "licenses" / "Pillow_LICENSE.txt",
        "tqdm": resources_dir / "licenses" / "tqdm_LICENSE.txt",
        "tomli": resources_dir / "licenses" / "tomli_LICENSE.txt",
        "colorama": resources_dir / "licenses" / "colorama_LICENSE.txt",
        "python": resources_dir / "licenses" / "Python_LICENSE.txt",
        "notices": resources_dir / "licenses" / "THIRD_PARTY_NOTICES.md",
    }
    for name, path in required_licenses.items():
        _require_file(path, f"{name} license/notice")
    notices = required_licenses["notices"].read_text(encoding="utf-8")
    if "MPL-2.0 AND MIT" not in notices or "replacement" not in notices.lower():
        raise AssistedReleaseVerificationError("Assisted notices do not preserve tqdm dual licensing/replacement policy.")

    copied_source_manifest = _require_file(
        resources_dir / "sources" / "SOURCE_MANIFEST.json",
        "Assisted source manifest",
    )
    expected_source_manifest_sha = str((manifest.get("licenses") or {}).get("source_manifest_sha256") or "")
    if len(expected_source_manifest_sha) != 64 or _sha256(copied_source_manifest) != expected_source_manifest_sha:
        raise AssistedReleaseVerificationError("Assisted source manifest SHA256 changed.")

    sources = manifest.get("source_archives")
    if not isinstance(sources, dict):
        raise AssistedReleaseVerificationError("Assisted manifest has no source archive inventory.")
    verified: dict[str, str] = {}
    for name in ("meeko", "gemmi", "tqdm"):
        record = sources.get(name)
        if not isinstance(record, dict):
            raise AssistedReleaseVerificationError(f"Assisted source record is missing for {name}.")
        archive = _require_file(resources_dir / "sources" / str(record.get("filename") or ""), f"{name} source")
        actual = _sha256(archive)
        if actual != str(record.get("sha256") or ""):
            raise AssistedReleaseVerificationError(f"{name} source archive SHA256 changed.")
        verified[name] = actual
    return verified


def _find_project_dir(payload: dict[str, Any]) -> Path:
    path_text = str(payload.get("project_dir") or "")
    if not path_text and isinstance(payload.get("project"), dict):
        path_text = str(payload["project"].get("project_dir") or "")
    if not path_text:
        raise AssistedReleaseVerificationError("Assisted demo creation did not return project_dir.")
    return Path(path_text).resolve()


def verify_assisted_release(
    layout_dir: str | Path,
    work_dir: str | Path | None = None,
    *,
    gate: str = "post-package",
    keep_work_dir: bool = False,
) -> dict[str, Any]:
    layout = Path(layout_dir).expanduser().resolve()
    backend_dir = layout / "backend"
    resources_dir = layout / "resources"
    python_exe = _require_file(resources_dir / "python" / "python.exe", "Assisted Python")
    if (resources_dir / "python" / "Lib" / "ensurepip").exists():
        raise AssistedReleaseVerificationError("Assisted package contains untracked ensurepip wheels.")
    vina_exe = _require_file(resources_dir / "vina" / "vina.exe", "AutoDock Vina")
    _require_file(backend_dir / "dockstart_core" / "preparation.py", "DockStart preparation backend")
    manifest = json.loads(
        _require_file(resources_dir / "toolchain_manifest.json", "Assisted toolchain manifest").read_text(encoding="utf-8"),
    )
    if manifest.get("release_profile") != "assisted_stable":
        raise AssistedReleaseVerificationError("Layout is not the assisted_stable release profile.")
    if manifest.get("includes_bundled_rdkit") is not True or manifest.get("includes_bundled_meeko") is not True:
        raise AssistedReleaseVerificationError("Assisted manifest does not declare RDKit/Meeko as bundled.")

    expected_tree_hash = str(manifest.get("bundled_python", {}).get("runtime_tree_sha256") or "")
    if len(expected_tree_hash) != 64 or _runtime_tree_sha256(resources_dir / "python") != expected_tree_hash:
        raise AssistedReleaseVerificationError("Pristine release runtime tree does not match its staged fingerprint.")
    source_manifest = json.loads(
        _require_file(resources_dir / "sources" / "SOURCE_MANIFEST.json", "Assisted source manifest").read_text(
            encoding="utf-8",
        ),
    )
    source_base = ((source_manifest.get("python") or {}).get("expected_base_runtime") or {})
    bundled_python = manifest.get("bundled_python") or {}
    expected_base = {
        "sha256": str(source_base.get("sha256") or ""),
        "file_count": source_base.get("file_count"),
        "size_bytes": source_base.get("size_bytes"),
    }
    staged_base = {
        "sha256": str(bundled_python.get("base_runtime_tree_sha256") or ""),
        "file_count": bundled_python.get("base_runtime_file_count"),
        "size_bytes": bundled_python.get("base_runtime_size_bytes"),
    }
    actual_base = _runtime_tree_fingerprint(
        resources_dir / "python",
        excluded_roots=("Lib/site-packages",),
    )
    if staged_base != expected_base or actual_base != expected_base:
        raise AssistedReleaseVerificationError(
            "Assisted CPython base runtime does not match the pinned pre-wheel tree fingerprint.",
        )
    package_versions = _runtime_probe(python_exe, layout, manifest)
    source_hashes = _verify_legal_resources(resources_dir, manifest)
    try:
        dependency_licenses = verify_dependency_license_bundle(resources_dir / "licenses" / "dependencies")
    except DependencyLicenseBundleError as exc:
        raise AssistedReleaseVerificationError(f"Assisted dependency license bundle is invalid: {exc}") from exc

    created_temp = work_dir is None
    work_root = (
        Path(tempfile.mkdtemp(prefix="DockStart Assisted 离线 稳定性 回归 "))
        if created_temp
        else Path(work_dir).expanduser().resolve()
    )
    work_root.mkdir(parents=True, exist_ok=True)
    settings_path = work_root / "本地 设置.json"
    env = os.environ.copy()
    env.update(
        {
            "DOCKSTART_RESOURCE_DIR": str(layout),
            "DOCKSTART_SETTINGS_PATH": str(settings_path),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "DOCKSTART_OFFLINE": "1",
        },
    )

    try:
        settings_path.unlink(missing_ok=True)
        bundled_capabilities = _run_json_module(python_exe, backend_dir, env, "dockstart_core.capabilities")
        if not bundled_capabilities.get("assisted_mode_available"):
            raise AssistedReleaseVerificationError("Bundled Assisted capability is not available.")
        if bundled_capabilities.get("python_status", {}).get("source") != "bundled":
            raise AssistedReleaseVerificationError("Empty settings did not fall back to bundled Python.")

        settings_path.write_text(
            json.dumps({"tool_paths": {"vina": "", "python": str(python_exe)}, "project": {}}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        demo = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.demo_projects",
            "create",
            str(work_root),
            "assisted_raw",
        )
        project_dir = _find_project_dir(demo)
        cif_source = work_root / "assisted_receptor.cif"
        _convert_pdb_fixture_to_cif(
            python_exe,
            _require_file(project_dir / "raw" / "receptor.pdb", "Assisted source receptor PDB"),
            cif_source,
            env,
        )
        imported_cif = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.structure_fetch",
            "import-receptor-raw",
            str(project_dir),
            str(cif_source),
        )
        receptor_input = str(imported_cif.get("raw_file") or "")
        if not receptor_input.lower().endswith(".cif"):
            raise AssistedReleaseVerificationError("Assisted CIF import did not record a CIF receptor input.")
        (project_dir / "prepared" / "receptor.pdbqt").unlink(missing_ok=True)
        (project_dir / "prepared" / "ligand.pdbqt").unlink(missing_ok=True)
        _run_json_module(python_exe, backend_dir, env, "dockstart_core.preparation", "reset", str(project_dir), "receptor")
        _run_json_module(python_exe, backend_dir, env, "dockstart_core.preparation", "reset", str(project_dir), "ligand")

        tools = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.preparation",
            "tool-status",
            str(project_dir),
        )
        if tools.get("python_source") != "configured":
            raise AssistedReleaseVerificationError("User-configured Python did not take priority for preparation.")
        for key in ("python", "rdkit", "meeko"):
            if tools.get("tools", {}).get(key, {}).get("status") != "ok":
                raise AssistedReleaseVerificationError(f"Preparation tool is not ready: {key}")

        receptor = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.preparation",
            "prepare-receptor",
            str(project_dir),
            timeout=300,
        )
        ligand = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.preparation",
            "prepare-ligand",
            str(project_dir),
            timeout=300,
        )
        _require_file(project_dir / "prepared" / "receptor.pdbqt", "prepared receptor PDBQT")
        _require_file(project_dir / "prepared" / "ligand.pdbqt", "prepared ligand PDBQT")

        for target, payload in (("receptor", receptor), ("ligand", ligand)):
            project_payload = payload.get("project") if isinstance(payload.get("project"), dict) else {}
            preparation_payload = (
                project_payload.get("preparation")
                if isinstance(project_payload.get("preparation"), dict)
                else {}
            )
            preparation = (
                preparation_payload.get(target)
                if isinstance(preparation_payload.get(target), dict)
                else {}
            )
            command = preparation.get("command")
            if not isinstance(command, list) or "-I" not in command or "-B" not in command:
                raise AssistedReleaseVerificationError(f"{target} preparation did not record isolated Python flags.")
            if preparation.get("python_source") != "configured":
                raise AssistedReleaseVerificationError(f"{target} preparation did not record configured Python provenance.")

        _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "generate-config", str(project_dir))
        _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "validate-run", str(project_dir))
        prepared_run = _run_json_module(python_exe, backend_dir, env, "dockstart_core.project", "prepare-run", str(project_dir))
        run_id = str(prepared_run.get("run_id") or "")
        if not run_id and isinstance(prepared_run.get("metadata"), dict):
            run_id = str(prepared_run["metadata"].get("run_id") or "")
        if not run_id:
            raise AssistedReleaseVerificationError("Assisted prepare-run returned no run_id.")
        executed = _run_json_module(
            python_exe,
            backend_dir,
            env,
            "dockstart_core.project",
            "execute-run",
            str(project_dir),
            run_id,
            timeout=300,
        )
        run_metadata = executed.get("metadata") if isinstance(executed.get("metadata"), dict) else {}
        if run_metadata.get("status") != "finished" or run_metadata.get("exit_code") != 0:
            raise AssistedReleaseVerificationError(f"Assisted Vina run failed: {run_metadata}")
        _run_json_module(
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
        scores_path = _require_file(project_dir / "results" / "scores.csv", "Assisted scores.csv")
        with scores_path.open("r", encoding="utf-8-sig", newline="") as handle:
            scores = list(csv.DictReader(handle))
        if not scores or not math.isfinite(float(scores[0]["affinity_kcal_mol"])):
            raise AssistedReleaseVerificationError("Assisted docking produced no finite affinity score.")
        report = _require_file(project_dir / "reports" / "docking_report.md", "Assisted report").read_text(encoding="utf-8")
        if "Docking score 仅供结构结合趋势参考，不能替代实验验证。" not in report:
            raise AssistedReleaseVerificationError("Assisted report is missing the scientific disclaimer.")

        generated_bytecode = [*layout.rglob("*.pyc"), *layout.rglob("*.pyo")]
        generated_caches = list(layout.rglob("__pycache__"))
        if generated_bytecode or generated_caches:
            raise AssistedReleaseVerificationError("Read-only Assisted layout generated Python bytecode/cache files.")

        return {
            "ok": True,
            "profile": "assisted_stable",
            "gate": gate,
            "offline": True,
            "layout_dir": str(layout),
            "work_dir": str(work_root),
            "python": str(python_exe),
            "vina": str(vina_exe),
            "package_versions": package_versions,
            "source_hashes": source_hashes,
            "dependency_licenses": dependency_licenses,
            "bundled_fallback_verified": True,
            "configured_python_priority_verified": True,
            "receptor_input": receptor_input,
            "ligand_input": "raw/ligand.sdf",
            "cif_preparation_verified": True,
            "run_id": run_id,
            "run_status": run_metadata.get("status"),
            "best_affinity": float(scores[0]["affinity_kcal_mol"]),
            "no_generated_bytecode": True,
        }
    finally:
        if created_temp and not keep_work_dir:
            shutil.rmtree(work_root, ignore_errors=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a DockStart Assisted release/install layout.")
    parser.add_argument("layout_dir")
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--gate", choices=("development", "post-package", "post-install"), default="post-package")
    parser.add_argument("--keep-work-dir", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = verify_assisted_release(
            args.layout_dir,
            args.work_dir or None,
            gate=args.gate,
            keep_work_dir=args.keep_work_dir,
        )
    except Exception as exc:  # noqa: BLE001 - verification CLI emits structured failure output.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
