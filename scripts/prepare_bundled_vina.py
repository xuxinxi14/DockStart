"""Prepare a local AutoDock Vina binary for DockStart bundled resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT_ENV_VAR = "DOCKSTART_REPO_ROOT"
VINA_BINARY_RELATIVE = Path("resources", "tools", "vina", "vina.exe")
LICENSE_TARGET_RELATIVE = Path("resources", "licenses", "AutoDock-Vina_LICENSE.txt")
MANIFEST_RELATIVE = Path("resources", "toolchain_manifest.json")
LICENSE_CANDIDATE_NAMES = {
    "license",
    "license.txt",
    "license.md",
    "copying",
    "copying.txt",
    "copying.md",
}


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


def find_vina_binary(source: str | Path) -> Path:
    source_path = Path(source).expanduser().resolve()
    if source_path.is_file():
        if source_path.name.lower() != "vina.exe":
            raise ValueError("source 文件必须是 vina.exe。")
        return source_path
    if not source_path.is_dir():
        raise FileNotFoundError(f"未找到 Vina 来源路径：{source_path}")

    candidates = sorted(source_path.rglob("vina.exe"), key=lambda item: (len(item.parts), str(item).lower()))
    if not candidates:
        raise FileNotFoundError(f"在目录中没有找到 vina.exe：{source_path}")
    return candidates[0]


def find_dlls(vina_binary: str | Path) -> list[Path]:
    binary_dir = Path(vina_binary).expanduser().resolve().parent
    return sorted(binary_dir.glob("*.dll"), key=lambda item: item.name.lower())


def find_license_file(source: str | Path, explicit_license_path: str | Path | None = None) -> Path | None:
    if explicit_license_path:
        license_path = Path(explicit_license_path).expanduser().resolve()
        if not license_path.is_file():
            raise FileNotFoundError(f"未找到指定的 AutoDock Vina license 文件：{license_path}")
        return license_path

    source_path = Path(source).expanduser().resolve()
    search_root = source_path if source_path.is_dir() else source_path.parent
    candidates: list[Path] = []
    for item in search_root.rglob("*"):
        if item.is_file() and item.name.lower() in LICENSE_CANDIDATE_NAMES:
            candidates.append(item)
    return sorted(candidates, key=lambda item: (len(item.parts), str(item).lower()))[0] if candidates else None


def detect_vina_version(vina_binary: str | Path) -> str:
    try:
        completed = subprocess.run(
            [str(Path(vina_binary)), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"(\d+(?:\.\d+)+)", first_line)
    return match.group(1) if match else first_line


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


def prepare_bundled_vina(
    source: str | Path,
    repo_root: str | Path | None = None,
    version: str = "",
    license_path: str | Path | None = None,
    source_label: str = "",
) -> dict[str, Any]:
    root = get_repo_root(repo_root)
    source_path = Path(source).expanduser().resolve()
    vina_binary = find_vina_binary(source_path)
    target_binary = root / VINA_BINARY_RELATIVE
    copied_binary = _copy_file(vina_binary, target_binary)

    copied_dlls: list[str] = []
    for dll in find_dlls(vina_binary):
        target_dll = target_binary.parent / dll.name
        if _copy_file(dll, target_dll):
            copied_dlls.append(str(target_dll))

    selected_license = find_license_file(source_path, license_path)
    license_target = root / LICENSE_TARGET_RELATIVE
    license_copied = False
    if selected_license:
        license_copied = _copy_file(selected_license, license_target)

    sha256 = calculate_file_sha256(target_binary)
    prepared_at = datetime.now(UTC).isoformat()
    resolved_version = version.strip() or detect_vina_version(target_binary)
    manifest_path = root / MANIFEST_RELATIVE
    manifest = _load_manifest(manifest_path)
    manifest["status"] = "partial"
    manifest.setdefault("tools", {})
    manifest.setdefault("licenses", {})
    manifest["bundled_vina"] = {
        "name": "AutoDock Vina",
        "version": resolved_version,
        "binary_path": VINA_BINARY_RELATIVE.as_posix(),
        "license": "Apache-2.0",
        "source": source_label.strip() or str(source_path),
        "bundled": True,
        "sha256": sha256,
        "prepared_at": prepared_at,
    }
    manifest["tools"]["vina"] = {
        "name": "AutoDock Vina",
        "role": "docking_engine",
        "bundled_path": VINA_BINARY_RELATIVE.as_posix(),
        "required_for_mvp_run": True,
        "bundled_by_default": False,
        "license": "Apache-2.0",
        "resolution_priority": ["bundled", "configured", "auto"],
    }
    manifest["licenses"]["autodock_vina"] = LICENSE_TARGET_RELATIVE.as_posix()
    manifest["licenses"]["third_party_notices"] = Path("resources", "licenses", "THIRD_PARTY_NOTICES.md").as_posix()
    _write_manifest(manifest_path, manifest)

    return {
        "ok": True,
        "source": str(source_path),
        "vina_binary_source": str(vina_binary),
        "target_binary": str(target_binary),
        "copied_binary": copied_binary,
        "copied_dlls": copied_dlls,
        "license_source": str(selected_license) if selected_license else "",
        "license_target": str(license_target),
        "license_copied": license_copied,
        "version": resolved_version,
        "sha256": sha256,
        "prepared_at": prepared_at,
        "manifest_path": str(manifest_path),
        "message": "Bundled Vina resources prepared from local files. No network download was performed.",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare local AutoDock Vina files for DockStart bundled resources.")
    parser.add_argument("source", help="Local vina.exe path or extracted AutoDock Vina directory.")
    parser.add_argument("--repo-root", default="", help="DockStart repository root. Defaults to this script's parent.")
    parser.add_argument("--version", default="", help="Optional Vina version override for the manifest.")
    parser.add_argument("--license-path", default="", help="Optional explicit AutoDock Vina license file path.")
    parser.add_argument("--source-label", default="", help="Optional source label recorded in the manifest.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = prepare_bundled_vina(
            args.source,
            repo_root=args.repo_root or None,
            version=args.version,
            license_path=args.license_path or None,
            source_label=args.source_label,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - command line entrypoint returns JSON errors.
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "准备内置 AutoDock Vina 文件时发生错误。",
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
