"""Generate and verify the release dependency license bundle.

The generator intentionally uses only the Python standard library plus the
already-required Cargo executable.  It records the Windows production Cargo
graph and the production npm packages materialized by package-lock.json, then
copies the license/notice files supplied by each package into the release
stage.  Crates that do not supply a license file, and every MPL-2.0 crate, also
carry the exact crates.io ``.crate`` archive verified against Cargo.lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


SCHEMA_VERSION = 1
WINDOWS_TARGET = "x86_64-pc-windows-msvc"
BOM_FILENAME = "THIRD_PARTY_DEPENDENCIES.json"
LICENSE_PREFIX = re.compile(r"^(?:licen[cs]e|copying|notice|copyright)(?:$|[._ -].*)", re.IGNORECASE)
CODE_SUFFIXES = {
    ".c",
    ".class",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".map",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
    ".wasm",
}
SKIPPED_SOURCE_DIRECTORIES = {".git", "node_modules", "target"}


class DependencyLicenseBundleError(RuntimeError):
    """Raised when a dependency license bundle cannot be trusted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyLicenseBundleError(f"Unable to read JSON object: {path}") from exc
    if not isinstance(payload, dict):
        raise DependencyLicenseBundleError(f"Expected a JSON object: {path}")
    return payload


def _safe_slug(name: str) -> str:
    slug = quote(name, safe="@._-")
    if not slug or slug in {".", ".."}:
        raise DependencyLicenseBundleError(f"Unsafe package name: {name!r}")
    return slug


def _safe_relative_path(value: str) -> Path:
    logical = PurePosixPath(value)
    if logical.is_absolute() or not logical.parts or ".." in logical.parts:
        raise DependencyLicenseBundleError(f"Unsafe bundle path: {value!r}")
    return Path(*logical.parts)


def _license_files(package_root: Path, explicit_license_file: str | None = None) -> list[Path]:
    root = package_root.resolve(strict=True)
    selected: dict[str, Path] = {}
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(part in SKIPPED_SOURCE_DIRECTORIES for part in relative.parts[:-1]):
            continue
        if not LICENSE_PREFIX.match(candidate.name) or candidate.suffix.casefold() in CODE_SUFFIXES:
            continue
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise DependencyLicenseBundleError(f"License file escapes package root: {candidate}") from exc
        selected[relative.as_posix()] = candidate

    if explicit_license_file:
        explicit = (root / _safe_relative_path(explicit_license_file)).resolve(strict=False)
        try:
            explicit.relative_to(root)
        except ValueError as exc:
            raise DependencyLicenseBundleError(
                f"Declared license file escapes package root: {explicit_license_file}",
            ) from exc
        if not explicit.is_file():
            raise DependencyLicenseBundleError(f"Declared license file is missing: {explicit}")
        selected[explicit.relative_to(root).as_posix()] = explicit
    return [selected[key] for key in sorted(selected, key=str.casefold)]


def _copy_record(source: Path, destination: Path, bundle_root: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": destination.relative_to(bundle_root).as_posix(),
        "sha256": _sha256(destination),
        "size_bytes": destination.stat().st_size,
    }


def _run_cargo_metadata(cargo_manifest: Path, target: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--manifest-path",
            str(cargo_manifest),
            "--locked",
            "--offline",
            "--filter-platform",
            target,
        ],
        cwd=cargo_manifest.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise DependencyLicenseBundleError(
            "cargo metadata failed for the locked Windows target: "
            + (completed.stderr.strip() or completed.stdout.strip()),
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DependencyLicenseBundleError("cargo metadata did not return valid JSON.") from exc
    if not isinstance(payload, dict):
        raise DependencyLicenseBundleError("cargo metadata did not return a JSON object.")
    return payload


def _reachable_cargo_packages(metadata: dict[str, Any], cargo_manifest: Path) -> list[dict[str, Any]]:
    packages = metadata.get("packages")
    resolve = metadata.get("resolve")
    if not isinstance(packages, list) or not isinstance(resolve, dict):
        raise DependencyLicenseBundleError("Cargo metadata has no package/resolve graph.")
    nodes = resolve.get("nodes")
    if not isinstance(nodes, list):
        raise DependencyLicenseBundleError("Cargo metadata has no resolve nodes.")

    packages_by_id = {str(item.get("id")): item for item in packages if isinstance(item, dict)}
    nodes_by_id = {str(item.get("id")): item for item in nodes if isinstance(item, dict)}
    manifest = cargo_manifest.resolve()
    roots = [
        package
        for package in packages_by_id.values()
        if Path(str(package.get("manifest_path") or "")).resolve() == manifest
    ]
    if len(roots) != 1:
        raise DependencyLicenseBundleError(f"Unable to identify the DockStart Cargo root for {manifest}.")
    root_id = str(roots[0]["id"])

    reachable = {root_id}
    pending = [root_id]
    while pending:
        package_id = pending.pop()
        node = nodes_by_id.get(package_id)
        if not isinstance(node, dict):
            raise DependencyLicenseBundleError(f"Cargo resolve node is missing: {package_id}")
        for dependency in node.get("deps") or []:
            if not isinstance(dependency, dict):
                continue
            kinds = dependency.get("dep_kinds") or []
            if kinds and all(isinstance(kind, dict) and kind.get("kind") == "dev" for kind in kinds):
                continue
            dependency_id = str(dependency.get("pkg") or "")
            if dependency_id and dependency_id not in reachable:
                reachable.add(dependency_id)
                pending.append(dependency_id)

    result = [packages_by_id[item] for item in reachable if item != root_id]
    return sorted(result, key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("version", ""))))


def _cargo_lock_records(cargo_lock: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    try:
        payload = tomllib.loads(cargo_lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DependencyLicenseBundleError(f"Unable to parse Cargo.lock: {cargo_lock}") from exc
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in payload.get("package") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("name") or ""), str(item.get("version") or ""), str(item.get("source") or ""))
        records[key] = item
    return records


def _find_crate_archive(
    package: dict[str, Any],
    checksum: str,
    cargo_home: Path,
) -> Path:
    name = str(package.get("name") or "")
    version = str(package.get("version") or "")
    filename = f"{name}-{version}.crate"
    candidates: list[Path] = []
    manifest_path = Path(str(package.get("manifest_path") or ""))
    source_root = manifest_path.parent
    if source_root.parent.parent.name == "src":
        candidates.append(cargo_home / "registry" / "cache" / source_root.parent.name / filename)
    cache_root = cargo_home / "registry" / "cache"
    if cache_root.is_dir():
        candidates.extend(path / filename for path in cache_root.iterdir() if path.is_dir())

    seen: set[Path] = set()
    mismatches: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        actual = _sha256(resolved)
        if actual == checksum:
            return resolved
        mismatches.append(f"{resolved}={actual}")
    detail = "; ".join(mismatches) if mismatches else "no matching archive was found in Cargo cache"
    raise DependencyLicenseBundleError(
        f"Exact crate archive unavailable for {name} {version}; expected Cargo.lock checksum {checksum}: {detail}",
    )


def _generate_cargo_records(
    metadata: dict[str, Any],
    cargo_manifest: Path,
    cargo_lock: Path,
    cargo_home: Path,
    bundle_root: Path,
) -> list[dict[str, Any]]:
    lock_records = _cargo_lock_records(cargo_lock)
    output: list[dict[str, Any]] = []
    for package in _reachable_cargo_packages(metadata, cargo_manifest):
        name = str(package.get("name") or "")
        version = str(package.get("version") or "")
        source = str(package.get("source") or "")
        lock_record = lock_records.get((name, version, source))
        if not isinstance(lock_record, dict):
            raise DependencyLicenseBundleError(f"Cargo.lock record is missing for {name} {version} ({source}).")
        checksum = str(lock_record.get("checksum") or "").lower()
        if source.startswith("registry+") and len(checksum) != 64:
            raise DependencyLicenseBundleError(f"Cargo.lock checksum is missing for registry crate {name} {version}.")

        package_root = Path(str(package.get("manifest_path") or "")).resolve(strict=True).parent
        slug = _safe_slug(name)
        package_output = bundle_root / "cargo" / slug / version
        license_records: list[dict[str, Any]] = []
        for source_license in _license_files(package_root, str(package.get("license_file") or "") or None):
            relative = source_license.relative_to(package_root)
            record = _copy_record(
                source_license,
                package_output / "license-files" / relative,
                bundle_root,
            )
            record["source_path"] = relative.as_posix()
            license_records.append(record)

        license_expression = str(package.get("license") or "")
        missing_license_files = not license_records
        archive_reasons: list[str] = []
        if "MPL-2.0" in license_expression.upper():
            archive_reasons.append("mpl-2.0-source-availability")
        if missing_license_files:
            archive_reasons.append("no-source-license-file")

        archive_record: dict[str, Any] | None = None
        if archive_reasons:
            if not source.startswith("registry+") or len(checksum) != 64:
                raise DependencyLicenseBundleError(
                    f"{name} {version} requires an exact source archive but is not a checksummed registry crate.",
                )
            archive = _find_crate_archive(package, checksum, cargo_home)
            archive_record = _copy_record(
                archive,
                package_output / "source" / archive.name,
                bundle_root,
            )
            archive_record.update(
                {
                    "reason": archive_reasons,
                    "cargo_lock_checksum": checksum,
                    "cargo_lock_checksum_verified": archive_record["sha256"] == checksum,
                },
            )

        output.append(
            {
                "ecosystem": "cargo",
                "name": name,
                "version": version,
                "license": license_expression or None,
                "source": source or None,
                "cargo_lock_checksum": checksum or None,
                "license_files_missing": missing_license_files,
                "license_files": license_records,
                "source_archive": archive_record,
            },
        )
    return output


def _production_npm_packages(package_lock: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    if package_lock.get("lockfileVersion") not in {2, 3}:
        raise DependencyLicenseBundleError("package-lock.json must use lockfileVersion 2 or 3.")
    packages = package_lock.get("packages")
    if not isinstance(packages, dict) or "" not in packages:
        raise DependencyLicenseBundleError("package-lock.json has no packages inventory.")
    result = [
        (path, item)
        for path, item in packages.items()
        if path.startswith("node_modules/") and isinstance(item, dict) and item.get("dev") is not True
    ]
    return sorted(result, key=lambda item: item[0].casefold())


def _generate_npm_records(
    desktop_root: Path,
    package_lock: dict[str, Any],
    bundle_root: Path,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for package_path, lock_record in _production_npm_packages(package_lock):
        package_root = (desktop_root / _safe_relative_path(package_path)).resolve(strict=True)
        try:
            package_root.relative_to((desktop_root / "node_modules").resolve(strict=True))
        except ValueError as exc:
            raise DependencyLicenseBundleError(f"npm package path escapes node_modules: {package_path}") from exc
        package_json = _read_json_object(package_root / "package.json")
        name = str(package_json.get("name") or "")
        version = str(lock_record.get("version") or package_json.get("version") or "")
        if not name or str(package_json.get("version") or "") != version:
            raise DependencyLicenseBundleError(f"Installed npm package does not match lock entry: {package_path}")

        package_output = bundle_root / "npm" / _safe_slug(name) / version
        license_records: list[dict[str, Any]] = []
        for source_license in _license_files(package_root):
            relative = source_license.relative_to(package_root)
            record = _copy_record(
                source_license,
                package_output / "license-files" / relative,
                bundle_root,
            )
            record["source_path"] = relative.as_posix()
            license_records.append(record)
        output.append(
            {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "license": str(lock_record.get("license") or package_json.get("license") or "") or None,
                "source": str(lock_record.get("resolved") or "") or None,
                "integrity": str(lock_record.get("integrity") or "") or None,
                "package_lock_path": package_path,
                "license_files_missing": not license_records,
                "license_files": license_records,
            },
        )
    return sorted(output, key=lambda item: (str(item["name"]).casefold(), str(item["version"])))


def _iter_payload_file_records(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for ecosystem in ("cargo", "npm"):
        packages = payload.get(ecosystem)
        if not isinstance(packages, list):
            raise DependencyLicenseBundleError(f"BOM field {ecosystem!r} must be an array.")
        for package in packages:
            if not isinstance(package, dict):
                raise DependencyLicenseBundleError(f"Invalid {ecosystem} package record.")
            license_files = package.get("license_files")
            if not isinstance(license_files, list):
                raise DependencyLicenseBundleError(f"License file inventory is missing for {ecosystem} package.")
            for record in license_files:
                if not isinstance(record, dict):
                    raise DependencyLicenseBundleError("Invalid license file record.")
                yield record
            archive = package.get("source_archive")
            if archive is not None:
                if not isinstance(archive, dict):
                    raise DependencyLicenseBundleError("Invalid source archive record.")
                yield archive


def generate_dependency_license_bundle(
    repo_root: str | Path,
    destination: str | Path,
    *,
    target: str = WINDOWS_TARGET,
    cargo_metadata: dict[str, Any] | None = None,
    cargo_home: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=True)
    bundle = Path(destination).expanduser().resolve(strict=False)
    cargo_manifest = root / "apps" / "desktop" / "src-tauri" / "Cargo.toml"
    cargo_lock = cargo_manifest.parent / "Cargo.lock"
    package_lock_path = root / "apps" / "desktop" / "package-lock.json"
    desktop_root = package_lock_path.parent
    for required in (cargo_manifest, cargo_lock, package_lock_path):
        if not required.is_file():
            raise DependencyLicenseBundleError(f"Dependency lock input is missing: {required}")

    parent = bundle.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle.name}-", dir=parent))
    try:
        metadata = cargo_metadata or _run_cargo_metadata(cargo_manifest, target)
        package_lock = _read_json_object(package_lock_path)
        resolved_cargo_home = (
            Path(cargo_home).expanduser().resolve(strict=True)
            if cargo_home
            else Path(os.environ.get("CARGO_HOME") or (Path.home() / ".cargo")).expanduser().resolve(strict=True)
        )
        cargo_records = _generate_cargo_records(
            metadata,
            cargo_manifest,
            cargo_lock,
            resolved_cargo_home,
            temporary,
        )
        npm_records = _generate_npm_records(desktop_root, package_lock, temporary)
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "target": target,
            "lock_files": {
                "cargo": {
                    "path": "apps/desktop/src-tauri/Cargo.lock",
                    "sha256": _sha256(cargo_lock),
                },
                "npm": {
                    "path": "apps/desktop/package-lock.json",
                    "sha256": _sha256(package_lock_path),
                },
            },
            "counts": {"cargo": len(cargo_records), "npm": len(npm_records)},
            "cargo": cargo_records,
            "npm": npm_records,
        }
        (temporary / BOM_FILENAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        verify_dependency_license_bundle(
            temporary,
            expected_cargo_lock_sha256=_sha256(cargo_lock),
            expected_package_lock_sha256=_sha256(package_lock_path),
        )
        if bundle.exists():
            if not bundle.is_dir():
                raise DependencyLicenseBundleError(f"Bundle destination is not a directory: {bundle}")
            shutil.rmtree(bundle)
        temporary.replace(bundle)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return _read_json_object(bundle / BOM_FILENAME)


def verify_dependency_license_bundle(
    bundle_root: str | Path,
    *,
    expected_cargo_lock_sha256: str | None = None,
    expected_package_lock_sha256: str | None = None,
) -> dict[str, Any]:
    bundle = Path(bundle_root).expanduser().resolve(strict=True)
    payload = _read_json_object(bundle / BOM_FILENAME)
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("target") != WINDOWS_TARGET:
        raise DependencyLicenseBundleError("Dependency BOM schema or Windows target is unexpected.")
    lock_files = payload.get("lock_files")
    if not isinstance(lock_files, dict):
        raise DependencyLicenseBundleError("Dependency BOM has no lock file provenance.")
    cargo_lock_sha = str((lock_files.get("cargo") or {}).get("sha256") or "").lower()
    package_lock_sha = str((lock_files.get("npm") or {}).get("sha256") or "").lower()
    if len(cargo_lock_sha) != 64 or len(package_lock_sha) != 64:
        raise DependencyLicenseBundleError("Dependency BOM contains an invalid lock file SHA256.")
    if expected_cargo_lock_sha256 and cargo_lock_sha != expected_cargo_lock_sha256.lower():
        raise DependencyLicenseBundleError("Dependency BOM does not match the expected Cargo.lock SHA256.")
    if expected_package_lock_sha256 and package_lock_sha != expected_package_lock_sha256.lower():
        raise DependencyLicenseBundleError("Dependency BOM does not match the expected package-lock.json SHA256.")

    expected_paths = {BOM_FILENAME}
    for ecosystem in ("cargo", "npm"):
        packages = payload.get(ecosystem)
        if not isinstance(packages, list) or not packages:
            raise DependencyLicenseBundleError(f"Dependency BOM has no {ecosystem} packages.")
        for package in packages:
            if not isinstance(package, dict):
                raise DependencyLicenseBundleError(f"Invalid {ecosystem} package record.")
            license_files = package.get("license_files")
            missing = package.get("license_files_missing")
            if not isinstance(license_files, list) or missing is not (len(license_files) == 0):
                raise DependencyLicenseBundleError(
                    f"Inconsistent license-file state for {package.get('name')} {package.get('version')}.",
                )
            if ecosystem == "cargo":
                license_expression = str(package.get("license") or "")
                archive = package.get("source_archive")
                archive_required = missing or "MPL-2.0" in license_expression.upper()
                if archive_required and not isinstance(archive, dict):
                    raise DependencyLicenseBundleError(
                        f"Required crate archive is missing for {package.get('name')} {package.get('version')}.",
                    )
                if isinstance(archive, dict):
                    checksum = str(package.get("cargo_lock_checksum") or "").lower()
                    if archive.get("cargo_lock_checksum_verified") is not True or archive.get("sha256") != checksum:
                        raise DependencyLicenseBundleError(
                            f"Crate archive checksum is not tied to Cargo.lock for {package.get('name')}.",
                        )

    for record in _iter_payload_file_records(payload):
        relative_text = str(record.get("path") or "")
        relative = _safe_relative_path(relative_text)
        if relative_text in expected_paths:
            raise DependencyLicenseBundleError(f"Duplicate dependency bundle file: {relative_text}")
        expected_paths.add(relative_text)
        path = (bundle / relative).resolve(strict=False)
        try:
            path.relative_to(bundle)
        except ValueError as exc:
            raise DependencyLicenseBundleError(f"Dependency bundle file escapes root: {relative_text}") from exc
        if not path.is_file():
            raise DependencyLicenseBundleError(f"Dependency bundle file is missing: {relative_text}")
        expected_sha = str(record.get("sha256") or "").lower()
        if len(expected_sha) != 64 or _sha256(path) != expected_sha:
            raise DependencyLicenseBundleError(f"Dependency bundle SHA256 mismatch: {relative_text}")
        if path.stat().st_size != record.get("size_bytes"):
            raise DependencyLicenseBundleError(f"Dependency bundle size mismatch: {relative_text}")

    actual_paths = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise DependencyLicenseBundleError(
            f"Dependency bundle inventory mismatch; missing={missing}, extra={extra}",
        )
    counts = payload.get("counts") or {}
    if counts.get("cargo") != len(payload["cargo"]) or counts.get("npm") != len(payload["npm"]):
        raise DependencyLicenseBundleError("Dependency BOM package counts are inconsistent.")
    return {
        "ok": True,
        "target": payload["target"],
        "cargo_packages": len(payload["cargo"]),
        "npm_packages": len(payload["npm"]),
        "file_count": len(actual_paths),
        "cargo_lock_sha256": cargo_lock_sha,
        "package_lock_sha256": package_lock_sha,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or verify DockStart dependency license bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    generate.add_argument("--destination", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("bundle_root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        if args.command == "generate":
            result = generate_dependency_license_bundle(args.repo_root, args.destination)
            output = {"ok": True, "counts": result["counts"], "target": result["target"]}
        else:
            output = verify_dependency_license_bundle(args.bundle_root)
    except Exception as exc:  # noqa: BLE001 - release tooling emits a structured error.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
