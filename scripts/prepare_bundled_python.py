"""Prepare a local Python runtime for DockStart bundled resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT_ENV_VAR = "DOCKSTART_REPO_ROOT"
PYTHON_BINARY_RELATIVE = Path("resources", "python", "python.exe")
MANIFEST_RELATIVE = Path("resources", "toolchain_manifest.json")
RUNTIME_DIR_RELATIVE = Path("resources", "python")
LICENSE_TARGET_RELATIVE = Path("resources", "licenses", "Python_LICENSE.txt")
RUNTIME_DIR_NAMES = {"DLLs", "Lib", "Scripts"}
TOP_LEVEL_FILE_PATTERNS = ("python*.dll", "*.pyd", "*.zip", "python*._pth", "pyvenv.cfg")
LICENSE_CANDIDATE_NAMES = ("LICENSE.txt", "LICENSE", "LICENSE.md", "COPYING", "COPYING.txt")


def get_repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root:
        return Path(repo_root).expanduser().resolve()
    configured_root = os.environ.get(REPO_ROOT_ENV_VAR, "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def calculate_file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    file_path = Path(path)
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_python_binary(source: str | Path) -> Path:
    source_path = Path(source).expanduser().resolve()
    if source_path.is_file():
        if source_path.name.lower() != "python.exe":
            raise ValueError("source 文件必须是 python.exe。")
        return source_path
    if not source_path.is_dir():
        raise FileNotFoundError(f"未找到 Python 来源路径：{source_path}")

    direct_candidate = source_path / "python.exe"
    if direct_candidate.is_file():
        return direct_candidate

    candidates = sorted(source_path.rglob("python.exe"), key=lambda item: (len(item.parts), str(item).lower()))
    if not candidates:
        raise FileNotFoundError(f"在目录中没有找到 python.exe：{source_path}")
    return candidates[0]


def detect_python_version(python_binary: str | Path) -> str:
    try:
        completed = subprocess.run(
            [str(Path(python_binary)), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"Python\s+(.+)", first_line, flags=re.IGNORECASE)
    return match.group(1).strip() if match else first_line


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        return {
            "schema_version": 1,
            "toolchain_name": "DockStart Full",
            "status": "partial",
            "tools": {},
            "licenses": {},
        }
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_file(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return False
    shutil.copy2(source, target)
    return True


def _ignore_runtime_tree(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lower_name = name.lower()
        if lower_name in {"site-packages", "__pycache__"}:
            ignored.add(name)
        if lower_name.endswith((".pyc", ".pyo")):
            ignored.add(name)
    return ignored


def _copy_tree(source: Path, target: Path) -> bool:
    if not source.is_dir():
        return False
    if source.resolve() == target.resolve():
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=_ignore_runtime_tree)
    return True


def _iter_top_level_files(runtime_root: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for pattern in TOP_LEVEL_FILE_PATTERNS:
        for candidate in runtime_root.glob(pattern):
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate)
                yield candidate


def _find_license_file(runtime_root: Path) -> Path | None:
    for name in LICENSE_CANDIDATE_NAMES:
        candidate = runtime_root / name
        if candidate.is_file():
            return candidate
    return None


def prepare_bundled_python(
    source: str | Path,
    repo_root: str | Path | None = None,
    version: str = "",
    source_label: str = "",
) -> dict[str, Any]:
    root = get_repo_root(repo_root)
    source_path = Path(source).expanduser().resolve()
    python_binary = find_python_binary(source_path)
    runtime_root = python_binary.parent
    target_dir = root / RUNTIME_DIR_RELATIVE
    target_binary = root / PYTHON_BINARY_RELATIVE
    copied_binary = _copy_file(python_binary, target_binary)

    copied_files: list[str] = []
    for source_file in _iter_top_level_files(runtime_root):
        target_file = target_dir / source_file.name
        if _copy_file(source_file, target_file):
            copied_files.append(str(target_file))

    copied_dirs: list[str] = []
    for dirname in sorted(RUNTIME_DIR_NAMES):
        source_dir = runtime_root / dirname
        target_runtime_dir = target_dir / dirname
        if _copy_tree(source_dir, target_runtime_dir):
            copied_dirs.append(str(target_runtime_dir))

    license_source = _find_license_file(runtime_root)
    license_target = root / LICENSE_TARGET_RELATIVE
    license_copied = False
    if license_source:
        license_copied = _copy_file(license_source, license_target)

    sha256 = calculate_file_sha256(target_binary)
    prepared_at = datetime.now(UTC).isoformat()
    resolved_version = version.strip() or detect_python_version(target_binary)
    manifest_path = root / MANIFEST_RELATIVE
    manifest = _load_manifest(manifest_path)
    manifest["status"] = "partial"
    manifest.setdefault("tools", {})
    manifest.setdefault("licenses", {})
    manifest["bundled_python"] = {
        "name": "Python",
        "version": resolved_version,
        "binary_path": PYTHON_BINARY_RELATIVE.as_posix(),
        "license": "Python Software Foundation License",
        "source": source_label.strip() or str(source_path),
        "bundled": True,
        "sha256": sha256,
        "prepared_at": prepared_at,
    }
    manifest["tools"]["python"] = {
        "name": "Python",
        "role": "runtime",
        "bundled_path": PYTHON_BINARY_RELATIVE.as_posix(),
        "required_for_mvp_run": False,
        "bundled_by_default": False,
        "license": "Python Software Foundation License",
        "backend_resolution_priority": ["bundled", "configured", "current_environment"],
        "preparation_resolution_priority": ["configured", "bundled", "current_environment"],
    }
    manifest["licenses"]["python"] = LICENSE_TARGET_RELATIVE.as_posix() if license_source else ""
    manifest["licenses"].setdefault(
        "third_party_notices",
        Path("resources", "licenses", "THIRD_PARTY_NOTICES.md").as_posix(),
    )
    _write_manifest(manifest_path, manifest)

    return {
        "ok": True,
        "source": str(source_path),
        "python_binary_source": str(python_binary),
        "target_binary": str(target_binary),
        "copied_binary": copied_binary,
        "copied_files": copied_files,
        "copied_dirs": copied_dirs,
        "license_source": str(license_source) if license_source else "",
        "license_target": str(license_target),
        "license_copied": license_copied,
        "version": resolved_version,
        "sha256": sha256,
        "prepared_at": prepared_at,
        "manifest_path": str(manifest_path),
        "message": "Bundled Python resources prepared from local files. No network download or package installation was performed.",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare local Python runtime files for DockStart bundled resources.")
    parser.add_argument("source", help="Local python.exe path or local Python runtime directory.")
    parser.add_argument("--repo-root", default="", help="DockStart repository root. Defaults to this script's parent.")
    parser.add_argument("--version", default="", help="Optional Python version override for the manifest.")
    parser.add_argument("--source-label", default="", help="Optional source label recorded in the manifest.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = prepare_bundled_python(
            args.source,
            repo_root=args.repo_root or None,
            version=args.version,
            source_label=args.source_label,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - command line entrypoint returns JSON errors.
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "准备内置 Python runtime 文件时发生错误。",
                    "raw_error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
