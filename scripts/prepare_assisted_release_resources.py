"""Assemble DockStart Assisted Stable from a pinned offline wheelhouse."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

try:  # Direct script execution from the scripts directory.
    from dependency_license_bundle import BOM_FILENAME, generate_dependency_license_bundle
    from prepare_basic_release_resources import (
        _copy_minimal_python,
        _copy_tree,
        _remove_generated_bytecode,
        _safe_reset_stage,
        _sha256,
        _tree_stats,
        _vina_version,
    )
except ModuleNotFoundError:  # Import through tests/tools from the repository root.
    from scripts.dependency_license_bundle import BOM_FILENAME, generate_dependency_license_bundle
    from scripts.prepare_basic_release_resources import (
        _copy_minimal_python,
        _copy_tree,
        _remove_generated_bytecode,
        _safe_reset_stage,
        _sha256,
        _tree_stats,
        _vina_version,
    )


class AssistedReleasePreparationError(RuntimeError):
    """Raised when the Assisted release tree cannot be assembled safely."""


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssistedReleasePreparationError(f"Expected a JSON object: {path}")
    return payload


def _verified_artifact(wheelhouse: Path, item: dict[str, Any]) -> Path:
    filename = str(item.get("filename") or "")
    expected = str(item.get("sha256") or "").lower()
    if not filename or Path(filename).name != filename or len(expected) != 64:
        raise AssistedReleasePreparationError(f"Invalid pinned artifact entry: {item!r}")
    artifact = wheelhouse / filename
    if not artifact.is_file():
        raise AssistedReleasePreparationError(
            f"Offline artifact is missing: {artifact}. Run scripts/fetch_assisted_sources.py explicitly first.",
        )
    actual = _sha256(artifact)
    if actual != expected:
        raise AssistedReleasePreparationError(
            f"SHA256 mismatch for {filename}: expected {expected}, got {actual}",
        )
    return artifact


def _safe_extract_wheel(wheel: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            logical = PurePosixPath(member.filename)
            if logical.is_absolute() or ".." in logical.parts:
                raise AssistedReleasePreparationError(f"Unsafe wheel member in {wheel.name}: {member.filename}")
            destination = (target / Path(*logical.parts)).resolve()
            try:
                destination.relative_to(target_root)
            except ValueError as exc:
                raise AssistedReleasePreparationError(
                    f"Wheel member escapes target in {wheel.name}: {member.filename}",
                ) from exc
        archive.extractall(target)


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int = 90,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )
    if environment:
        env.update(environment)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _ordered_tree_fingerprint(path: Path) -> dict[str, Any]:
    """Hash a materialized tree without timestamps or absolute paths."""

    digest = hashlib.sha256()
    files = sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix().lower())
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


def _distribution_fingerprint(site_packages: Path, distribution_name: str) -> dict[str, Any]:
    normalized = distribution_name.lower().replace("-", "_")
    selected: Path | None = None
    for metadata_path in site_packages.glob("*.dist-info/METADATA"):
        name_line = next(
            (line.split(":", 1)[1].strip() for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("Name:")),
            "",
        )
        if name_line.lower().replace("-", "_") == normalized:
            selected = metadata_path.parent
            break
    if selected is None:
        raise AssistedReleasePreparationError(f"Installed dist-info is missing for {distribution_name}.")
    record_path = selected / "RECORD"
    if not record_path.is_file():
        raise AssistedReleasePreparationError(f"Installed RECORD is missing for {distribution_name}.")
    with record_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        relative_paths = sorted({str(row[0]) for row in csv.reader(handle) if row and row[0]})
    digest = hashlib.sha256()
    installed_files = 0
    for relative_text in relative_paths:
        logical = PurePosixPath(relative_text)
        if logical.is_absolute() or ".." in logical.parts:
            raise AssistedReleasePreparationError(
                f"Unsafe installed RECORD path for {distribution_name}: {relative_text}",
            )
        installed = site_packages / Path(*logical.parts)
        if not installed.is_file():
            continue
        installed_files += 1
        digest.update(logical.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(installed.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256(installed).encode("ascii"))
        digest.update(b"\n")
    return {
        "installed_tree_sha256": digest.hexdigest(),
        "installed_file_count": installed_files,
        "metadata_sha256": _sha256(selected / "METADATA"),
        "record_sha256": _sha256(record_path),
    }


def _validate_runtime(
    python_exe: Path,
    stage_root: Path,
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    version_check = _run([str(python_exe), "-I", "-B", "--version"], cwd=stage_root)
    version_text = "\n".join(
        part.strip() for part in (version_check.stdout, version_check.stderr) if part.strip()
    )
    if version_check.returncode != 0 or not re.search(r"Python\s+3\.11(?:\.|$)", version_text):
        raise AssistedReleasePreparationError(
            f"Assisted runtime must be CPython 3.11; probe returned {version_text!r}",
        )

    expected = {str(item["name"]).lower(): str(item["version"]) for item in packages}
    probe = (
        "import importlib.metadata as m,json;"
        "names=" + repr(sorted(expected)) + ";"
        "print(json.dumps({n:m.version(n) for n in names},sort_keys=True))"
    )
    completed = _run([str(python_exe), "-I", "-B", "-c", probe], cwd=stage_root)
    if completed.returncode != 0:
        raise AssistedReleasePreparationError(
            "Pinned package probe failed: " + (completed.stderr.strip() or completed.stdout.strip()),
        )
    try:
        versions = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise AssistedReleasePreparationError(
            f"Pinned package probe returned invalid JSON: {completed.stdout!r}",
        ) from exc
    if versions != expected:
        raise AssistedReleasePreparationError(
            f"Pinned package versions do not match: expected {expected}, got {versions}",
        )

    import_probe = (
        "import json,meeko,rdkit,numpy,scipy,gemmi,PIL,tqdm,tomli,colorama;"
        "print(json.dumps({'ok':True,'meeko':getattr(meeko,'__version__','')}))"
    )
    imported = _run([str(python_exe), "-I", "-B", "-c", import_probe], cwd=stage_root)
    if imported.returncode != 0:
        raise AssistedReleasePreparationError(
            "Assisted scientific import probe failed: " + (imported.stderr.strip() or imported.stdout.strip()),
        )

    cli_results: dict[str, dict[str, Any]] = {}
    for name, module in {
        "ligand": "meeko.cli.mk_prepare_ligand",
        "receptor": "meeko.cli.mk_prepare_receptor",
    }.items():
        completed = _run(
            [str(python_exe), "-I", "-B", "-m", module, "--help"],
            cwd=stage_root,
            timeout=120,
        )
        if completed.returncode != 0 or "usage:" not in completed.stdout.lower():
            raise AssistedReleasePreparationError(
                f"Meeko {name} module entry point is unavailable: "
                f"{completed.stderr.strip() or completed.stdout.strip()}",
            )
        cli_results[name] = {"module": module, "status": "ok"}

    return {
        "python_version": version_text,
        "packages": versions,
        "meeko_modules": cli_results,
    }


def prepare_assisted_release_resources(
    repo_root: str | Path,
    target_root: str | Path | None = None,
    wheelhouse: str | Path | None = None,
    *,
    validate_runtime: bool = True,
    generate_dependency_licenses: bool = True,
    prepared_at: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    stage_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else (root / ".release" / "assisted").resolve()
    )
    offline_root = (
        Path(wheelhouse).expanduser().resolve()
        if wheelhouse
        else (root / "_external_download" / "assisted-wheelhouse").resolve()
    )
    _safe_reset_stage(root, stage_root)

    source_resources = root / "resources"
    assisted_source = source_resources / "assisted"
    source_manifest_path = assisted_source / "SOURCE_MANIFEST.json"
    source_manifest = _read_object(source_manifest_path)
    if source_manifest.get("profile") != "assisted_stable":
        raise AssistedReleasePreparationError("Unexpected Assisted source profile.")

    packages = source_manifest.get("packages")
    source_archives = source_manifest.get("source_archives")
    python_lock = source_manifest.get("python")
    vina_lock = source_manifest.get("vina")
    if (
        not isinstance(packages, list)
        or not isinstance(source_archives, list)
        or not isinstance(python_lock, dict)
        or not isinstance(vina_lock, dict)
    ):
        raise AssistedReleasePreparationError("Assisted source manifest is incomplete.")

    source_python = source_resources / "python"
    source_python_exe = source_python / "python.exe"
    expected_python_sha = str(python_lock.get("expected_executable_sha256") or "").lower()
    if not source_python_exe.is_file() or _sha256(source_python_exe) != expected_python_sha:
        raise AssistedReleasePreparationError(
            "Pinned CPython 3.11 base runtime is missing or its python.exe hash changed. "
            "Review and update SOURCE_MANIFEST.json intentionally before building.",
        )
    expected_base_runtime = python_lock.get("expected_base_runtime")
    if not isinstance(expected_base_runtime, dict):
        raise AssistedReleasePreparationError("Pinned CPython base runtime tree fingerprint is missing.")
    source_vina_exe = source_resources / "vina" / "vina.exe"
    expected_vina_sha = str(vina_lock.get("expected_executable_sha256") or "").lower()
    if not source_vina_exe.is_file() or len(expected_vina_sha) != 64 or _sha256(source_vina_exe) != expected_vina_sha:
        raise AssistedReleasePreparationError(
            "Pinned AutoDock Vina 1.2.7 is missing or its executable hash changed. "
            "Review and update SOURCE_MANIFEST.json intentionally before building.",
        )

    verified_packages = [(_verified_artifact(offline_root, item), item) for item in packages]
    verified_sources = [(_verified_artifact(offline_root, item), item) for item in source_archives]

    target = stage_root / "resources"
    target.mkdir(parents=True, exist_ok=True)
    _copy_tree(source_resources / "vina", target / "vina")
    _copy_tree(source_resources / "licenses", target / "licenses")
    dockstart_license = root / "LICENSE"
    if not dockstart_license.is_file():
        raise AssistedReleasePreparationError(f"DockStart root LICENSE is missing: {dockstart_license}")
    shutil.copy2(dockstart_license, target / "licenses" / "DockStart-Apache-2.0.txt")
    dependency_bom = (
        generate_dependency_license_bundle(root, target / "licenses" / "dependencies")
        if generate_dependency_licenses
        else None
    )
    _copy_tree(source_resources / "examples", target / "examples")
    _copy_minimal_python(source_python, target / "python")
    shutil.copy2(assisted_source / "ASSISTED_RUNTIME.md", target / "python" / "README.md")
    shutil.copy2(assisted_source / "ASSISTED_RUNTIME.md", target / "python" / "ASSISTED_RUNTIME.md")
    base_runtime_fingerprint = _ordered_tree_fingerprint(target / "python")
    expected_base_fingerprint = {
        "sha256": str(expected_base_runtime.get("sha256") or "").lower(),
        "file_count": expected_base_runtime.get("file_count"),
        "size_bytes": expected_base_runtime.get("size_bytes"),
    }
    if base_runtime_fingerprint != expected_base_fingerprint:
        raise AssistedReleasePreparationError(
            "Pinned CPython base runtime tree changed; expected "
            f"{expected_base_fingerprint}, got {base_runtime_fingerprint}. "
            "Review the local runtime provenance before updating SOURCE_MANIFEST.json.",
        )
    _copy_tree(root / "backend" / "adapters", stage_root / "backend" / "adapters", runtime_tree=True)
    _copy_tree(root / "backend" / "dockstart_core", stage_root / "backend" / "dockstart_core", runtime_tree=True)
    frontend_dir = stage_root / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "apps" / "desktop" / "package.json", frontend_dir / "package.json")

    site_packages = target / "python" / "Lib" / "site-packages"
    if site_packages.exists():
        shutil.rmtree(site_packages)
    site_packages.mkdir(parents=True)
    package_license_root = target / "licenses" / "python-packages"
    package_license_root.mkdir(parents=True, exist_ok=True)
    for wheel, item in verified_packages:
        _safe_extract_wheel(wheel, site_packages)
        package_license_dir = package_license_root / str(item["name"])
        package_license_dir.mkdir(parents=True, exist_ok=True)
        for member in item.get("license_members", []):
            source_license = site_packages / Path(*PurePosixPath(str(member)).parts)
            if not source_license.is_file():
                raise AssistedReleasePreparationError(
                    f"Pinned wheel {wheel.name} does not contain declared license {member}",
                )
            shutil.copy2(source_license, package_license_dir / source_license.name)

    shutil.copy2(assisted_source / "THIRD_PARTY_NOTICES.md", target / "licenses" / "THIRD_PARTY_NOTICES.md")
    sources_dir = target / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    for archive, _item in verified_sources:
        shutil.copy2(archive, sources_dir / archive.name)
    shutil.copy2(source_manifest_path, sources_dir / "SOURCE_MANIFEST.json")
    shutil.copy2(assisted_source / "requirements.lock", sources_dir / "requirements.lock")
    shutil.copy2(assisted_source / "SOURCES_README.md", sources_dir / "README.md")

    stage_python = target / "python" / "python.exe"
    stage_vina = target / "vina" / "vina.exe"
    if (target / "python" / "Lib" / "ensurepip").exists():
        raise AssistedReleasePreparationError(
            "Assisted runtime must exclude ensurepip and its unpinned pip/setuptools wheels.",
        )
    runtime_probe = (
        _validate_runtime(stage_python, stage_root, packages)
        if validate_runtime
        else {"skipped": True}
    )
    timestamp = prepared_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    _remove_generated_bytecode(stage_root)
    runtime_fingerprint = _ordered_tree_fingerprint(target / "python")
    package_records = {
        str(item["name"]): {
            "version": str(item["version"]),
            "license": str(item["license"]),
            "wheel_filename": wheel.name,
            "wheel_sha256": _sha256(wheel),
            "source_url": str(item["url"]),
            "role": str(item["role"]),
            "replaceable": True,
            **_distribution_fingerprint(site_packages, str(item["name"])),
        }
        for wheel, item in verified_packages
    }
    source_records = {
        str(item["name"]): {
            "version": str(item["version"]),
            "filename": archive.name,
            "sha256": _sha256(archive),
            "source_url": str(item["url"]),
            "reason": str(item["reason"]),
        }
        for archive, item in verified_sources
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "toolchain_name": "DockStart Assisted Stable",
        "release_profile": "assisted_stable",
        "status": "ready",
        "description": (
            "DockStart Assisted Stable includes Vina and a separate, replaceable CPython 3.11 "
            "runtime with pinned RDKit/Meeko dependencies for offline PDBQT preparation."
        ),
        "prepared_at": timestamp,
        "network_used_during_stage": False,
        "includes_bundled_rdkit": True,
        "includes_bundled_meeko": True,
        "integrity_policy": "provenance_and_warning_only; user replacement must remain possible",
        "bundled_vina": {
            "name": "AutoDock Vina",
            "version": _vina_version(stage_vina, "1.2.7"),
            "binary_path": "resources/vina/vina.exe",
            "license": "Apache-2.0",
            "bundled": True,
            "sha256": _sha256(stage_vina),
        },
        "bundled_python": {
            "name": "CPython",
            "version": str(runtime_probe.get("python_version") or "3.11"),
            "binary_path": "resources/python/python.exe",
            "role": "backend_and_preparation_runtime",
            "license": "Python Software Foundation License",
            "bundled": True,
            "includes_site_packages": True,
            "sha256": _sha256(stage_python),
            "base_runtime_tree_sha256": base_runtime_fingerprint["sha256"],
            "base_runtime_file_count": base_runtime_fingerprint["file_count"],
            "base_runtime_size_bytes": base_runtime_fingerprint["size_bytes"],
            "runtime_tree_sha256": runtime_fingerprint["sha256"],
            "runtime_file_count": runtime_fingerprint["file_count"],
            "runtime_size_bytes": runtime_fingerprint["size_bytes"],
        },
        "preparation": {
            "resolution_priority": ["configured", "bundled", "current_environment"],
            "meeko_invocation": ["python", "-I", "-B", "-m", "meeko.cli.<entrypoint>"],
            "modified_upstream_packages": False,
        },
        "packages": package_records,
        "source_archives": source_records,
        "licenses": {
            "dockstart": "resources/licenses/DockStart-Apache-2.0.txt",
            "serde": "resources/licenses/Serde_LICENSE-MIT.txt",
            "third_party_notices": "resources/licenses/THIRD_PARTY_NOTICES.md",
            "package_licenses": "resources/licenses/python-packages/",
            "source_manifest": "resources/sources/SOURCE_MANIFEST.json",
            "source_manifest_sha256": _sha256(source_manifest_path),
            "dependency_bom": (
                "resources/licenses/dependencies/THIRD_PARTY_DEPENDENCIES.json"
                if generate_dependency_licenses
                else None
            ),
        },
    }
    manifest_path = target / "toolchain_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bytecode = [*stage_root.rglob("*.pyc"), *stage_root.rglob("*.pyo")]
    caches = list(stage_root.rglob("__pycache__"))
    if bytecode or caches:
        raise AssistedReleasePreparationError("Assisted stage contains generated Python bytecode/cache files.")
    file_count, size_bytes = _tree_stats(stage_root)
    return {
        "ok": True,
        "profile": "assisted_stable",
        "repo_root": str(root),
        "stage_dir": str(stage_root),
        "wheelhouse": str(offline_root),
        "network_used": False,
        "manifest_file": str(manifest_path),
        "python": str(stage_python),
        "vina": str(stage_vina),
        "runtime_probe": runtime_probe,
        "dependency_license_counts": dependency_bom.get("counts") if dependency_bom else None,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare DockStart Assisted Stable release resources.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-root", default="")
    parser.add_argument("--wheelhouse", default="")
    parser.add_argument("--skip-runtime-check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = prepare_assisted_release_resources(
            args.repo_root,
            args.target_root or None,
            args.wheelhouse or None,
            validate_runtime=not args.skip_runtime_check,
        )
    except Exception as exc:  # noqa: BLE001 - release CLI emits structured failure output.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
