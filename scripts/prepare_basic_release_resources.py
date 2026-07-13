"""Assemble the deterministic resource tree used by DockStart Basic releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TOP_LEVEL_RUNTIME_PATTERNS = (
    "python.exe",
    "pythonw.exe",
    "python*.dll",
    "vcruntime*.dll",
    "*.pyd",
    "*.zip",
    "python*._pth",
    "pyvenv.cfg",
)
EXCLUDED_BASIC_PACKAGES = ("meeko", "rdkit", "numpy", "scipy")


class BasicReleasePreparationError(RuntimeError):
    """Raised when the Basic resource tree cannot be assembled safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _ignore_runtime_items(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lowered = name.lower()
        if lowered in {"site-packages", "__pycache__"}:
            ignored.add(name)
        elif lowered.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _copy_tree(source: Path, target: Path, *, runtime_tree: bool = False) -> None:
    if not source.is_dir():
        raise BasicReleasePreparationError(f"Required directory is missing: {source}")
    shutil.copytree(
        source,
        target,
        ignore=_ignore_runtime_items if runtime_tree else None,
    )


def _safe_reset_stage(repo_root: Path, target: Path) -> None:
    release_root = (repo_root / ".release").resolve()
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(release_root)
    except ValueError as exc:
        raise BasicReleasePreparationError(
            f"Basic release staging must stay under {release_root}: {resolved_target}",
        ) from exc
    if resolved_target == release_root:
        raise BasicReleasePreparationError("Refusing to delete the .release root itself.")
    if resolved_target.exists():
        shutil.rmtree(resolved_target)
    resolved_target.mkdir(parents=True, exist_ok=True)


def _run_version(binary: Path, arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            [str(binary), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except Exception:
        return ""
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return output.splitlines()[0].strip() if completed.returncode == 0 and output.strip() else ""


def _python_version(binary: Path, fallback: str) -> str:
    output = _run_version(binary, ["--version"])
    match = re.search(r"Python\s+(.+)", output, flags=re.IGNORECASE)
    return match.group(1).strip() if match else (output or fallback)


def _vina_version(binary: Path, fallback: str) -> str:
    output = _run_version(binary, ["--version"])
    match = re.search(r"(?:AutoDock\s+)?Vina\s+v?([^\s]+)", output, flags=re.IGNORECASE)
    return match.group(1).strip() if match else (fallback or output)


def _copy_minimal_python(source: Path, target: Path) -> None:
    python_exe = source / "python.exe"
    if not python_exe.is_file():
        raise BasicReleasePreparationError(f"Bundled backend python.exe is missing: {python_exe}")
    target.mkdir(parents=True, exist_ok=True)

    copied: set[Path] = set()
    for pattern in TOP_LEVEL_RUNTIME_PATTERNS:
        for candidate in source.glob(pattern):
            if not candidate.is_file() or candidate in copied:
                continue
            shutil.copy2(candidate, target / candidate.name)
            copied.add(candidate)

    for directory_name in ("DLLs", "Lib"):
        _copy_tree(source / directory_name, target / directory_name, runtime_tree=True)

    readme = source / "README.md"
    if readme.is_file():
        shutil.copy2(readme, target / readme.name)


def _validate_basic_runtime(python_exe: Path) -> dict[str, Any]:
    probe = (
        "import importlib.util,json,pathlib,subprocess,datetime;"
        "mods={name:importlib.util.find_spec(name) is not None "
        "for name in ('meeko','rdkit','numpy','scipy')};"
        "print(json.dumps({'stdlib_ok':True,'modules':mods}))"
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
        raise BasicReleasePreparationError(
            "Staged Basic Python cannot execute the standard-library probe: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )
    try:
        payload = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as exc:
        raise BasicReleasePreparationError(
            f"Staged Basic Python returned invalid probe output: {completed.stdout!r}",
        ) from exc
    unexpected = [name for name, available in payload.get("modules", {}).items() if available]
    if unexpected:
        raise BasicReleasePreparationError(
            "Basic runtime unexpectedly exposes scientific preparation packages: " + ", ".join(unexpected),
        )
    return payload


def _remove_generated_bytecode(path: Path) -> None:
    for cache_dir in sorted(
        (item for item in path.rglob("__pycache__") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        shutil.rmtree(cache_dir)
    for bytecode in path.rglob("*.py[co]"):
        bytecode.unlink()


def _tree_stats(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def prepare_basic_release_resources(
    repo_root: str | Path,
    target_root: str | Path | None = None,
    *,
    validate_runtime: bool = True,
    prepared_at: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    stage_root = (
        Path(target_root).expanduser().resolve()
        if target_root
        else (root / ".release" / "basic").resolve()
    )
    _safe_reset_stage(root, stage_root)
    target = stage_root / "resources"
    target.mkdir(parents=True, exist_ok=True)

    source_resources = root / "resources"
    source_manifest = _read_json(source_resources / "toolchain_manifest.json")
    source_vina = source_resources / "vina" / "vina.exe"
    source_python = source_resources / "python"
    if not source_vina.is_file():
        raise BasicReleasePreparationError(f"Bundled AutoDock Vina is missing: {source_vina}")

    _copy_tree(source_resources / "vina", target / "vina")
    _copy_tree(source_resources / "licenses", target / "licenses")
    _copy_tree(source_resources / "examples", target / "examples")
    _copy_minimal_python(source_python, target / "python")
    _copy_tree(root / "backend" / "adapters", stage_root / "backend" / "adapters", runtime_tree=True)
    _copy_tree(
        root / "backend" / "dockstart_core",
        stage_root / "backend" / "dockstart_core",
        runtime_tree=True,
    )
    frontend_dir = stage_root / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "apps" / "desktop" / "package.json", frontend_dir / "package.json")

    required_stage_files = (
        target / "licenses" / "AutoDock-Vina_LICENSE.txt",
        target / "licenses" / "Python_LICENSE.txt",
        target / "licenses" / "THIRD_PARTY_NOTICES.md",
        target / "examples" / "basic_pdbqt" / "manifest.json",
        target / "examples" / "basic_pdbqt" / "project.json",
        target / "examples" / "basic_pdbqt" / "receptor.pdbqt",
        target / "examples" / "basic_pdbqt" / "ligand.pdbqt",
    )
    missing_stage_files = [str(path) for path in required_stage_files if not path.is_file() or path.stat().st_size <= 0]
    if missing_stage_files:
        raise BasicReleasePreparationError(
            "Basic release resources are incomplete: " + ", ".join(missing_stage_files),
        )

    source_bundled_vina = source_manifest.get("bundled_vina")
    if not isinstance(source_bundled_vina, dict):
        source_bundled_vina = {}
    source_bundled_python = source_manifest.get("bundled_python")
    if not isinstance(source_bundled_python, dict):
        source_bundled_python = {}

    stage_vina = target / "vina" / "vina.exe"
    stage_python = target / "python" / "python.exe"
    vina_version = _vina_version(stage_vina, str(source_bundled_vina.get("version") or ""))
    python_version = _python_version(stage_python, str(source_bundled_python.get("version") or ""))
    timestamp = prepared_at or datetime.now(UTC).replace(microsecond=0).isoformat()

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "toolchain_name": "DockStart Basic",
        "release_profile": "basic_stable",
        "status": "ready",
        "description": (
            "DockStart Basic 稳定包随附 AutoDock Vina 与仅用于 DockStart 后端的 Python runtime；"
            "不随附 RDKit、Meeko 或其科学计算依赖。"
        ),
        "includes_bundled_rdkit": False,
        "includes_bundled_meeko": False,
        "bundled_vina": {
            "name": "AutoDock Vina",
            "version": vina_version,
            "binary_path": "resources/vina/vina.exe",
            "license": "Apache-2.0",
            "source": str(source_bundled_vina.get("source") or "local verified AutoDock Vina runtime"),
            "bundled": True,
            "sha256": _sha256(stage_vina),
            "prepared_at": timestamp,
        },
        "bundled_python": {
            "name": "Python",
            "version": python_version,
            "role": "backend_runtime",
            "binary_path": "resources/python/python.exe",
            "license": "Python Software Foundation License",
            "source": str(source_bundled_python.get("source") or "local verified CPython runtime"),
            "bundled": True,
            "includes_site_packages": False,
            "sha256": _sha256(stage_python),
            "prepared_at": timestamp,
        },
        "tools": {
            "vina": {
                "name": "AutoDock Vina",
                "role": "docking_engine",
                "bundled_path": "resources/vina/vina.exe",
                "required_for_mvp_run": True,
                "bundled_by_default": True,
                "license": "Apache-2.0",
                "resolution_priority": ["bundled", "configured", "auto"],
            },
            "python": {
                "name": "Python",
                "role": "backend_runtime",
                "bundled_path": "resources/python/python.exe",
                "required_for_app_backend": True,
                "required_for_mvp_run": False,
                "bundled_by_default": True,
                "license": "Python Software Foundation License",
                "backend_resolution_priority": ["bundled", "configured", "current_environment"],
                "preparation_resolution_priority": ["configured", "bundled", "current_environment"],
            },
        },
        "licenses": {
            "autodock_vina": "resources/licenses/AutoDock-Vina_LICENSE.txt",
            "python": "resources/licenses/Python_LICENSE.txt",
            "third_party_notices": "resources/licenses/THIRD_PARTY_NOTICES.md",
        },
    }
    manifest_path = target / "toolchain_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runtime_probe = _validate_basic_runtime(stage_python) if validate_runtime else {"skipped": True}
    _remove_generated_bytecode(stage_root)
    file_count, size_bytes = _tree_stats(stage_root)
    return {
        "ok": True,
        "profile": "basic_stable",
        "repo_root": str(root),
        "stage_dir": str(stage_root),
        "resource_dir": str(target),
        "manifest_file": str(manifest_path),
        "python": str(stage_python),
        "vina": str(stage_vina),
        "python_version": python_version,
        "vina_version": vina_version,
        "excluded_packages": list(EXCLUDED_BASIC_PACKAGES),
        "runtime_probe": runtime_probe,
        "file_count": file_count,
        "size_bytes": size_bytes,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare DockStart Basic release resources.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-root", default="")
    parser.add_argument("--skip-runtime-check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = prepare_basic_release_resources(
            args.repo_root,
            args.target_root or None,
            validate_runtime=not args.skip_runtime_check,
        )
    except Exception as exc:  # noqa: BLE001 - release CLI emits structured failure output.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
