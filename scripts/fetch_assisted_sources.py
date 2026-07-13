"""Fetch the pinned Assisted wheelhouse as an explicit maintainer action.

Release builds never call this script. They accept only a complete local
wheelhouse whose files match resources/assisted/SOURCE_MANIFEST.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any


class AssistedSourceFetchError(RuntimeError):
    """Raised when a pinned upstream artifact cannot be fetched safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("profile") != "assisted_stable":
        raise AssistedSourceFetchError(f"Unexpected Assisted source manifest: {path}")
    return payload


def _iter_artifacts(manifest: dict[str, Any]) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for key in ("packages", "source_archives"):
        values = manifest.get(key)
        if not isinstance(values, list):
            raise AssistedSourceFetchError(f"Manifest field {key} must be a list.")
        for item in values:
            if not isinstance(item, dict):
                raise AssistedSourceFetchError(f"Manifest field {key} contains a non-object item.")
            artifact = {name: str(item.get(name) or "") for name in ("filename", "url", "sha256")}
            filename = Path(artifact["filename"])
            if (
                not artifact["filename"]
                or filename.name != artifact["filename"]
                or not artifact["url"].startswith("https://files.pythonhosted.org/")
                or len(artifact["sha256"]) != 64
            ):
                raise AssistedSourceFetchError(f"Unsafe or incomplete pinned artifact entry: {item!r}")
            artifacts.append(artifact)
    return artifacts


def fetch_assisted_sources(
    repo_root: str | Path,
    wheelhouse: str | Path | None = None,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    manifest_path = root / "resources" / "assisted" / "SOURCE_MANIFEST.json"
    manifest = _load_manifest(manifest_path)
    destination = (
        Path(wheelhouse).expanduser().resolve()
        if wheelhouse
        else root / "_external_download" / "assisted-wheelhouse"
    )
    destination.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for artifact in _iter_artifacts(manifest):
        target = destination / artifact["filename"]
        if target.is_file() and _sha256(target) == artifact["sha256"]:
            action = "verified_existing"
        else:
            partial = target.with_suffix(target.suffix + ".partial")
            if partial.exists():
                partial.unlink()
            try:
                request = urllib.request.Request(
                    artifact["url"],
                    headers={"User-Agent": "DockStart-Assisted-Release-Builder/1"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            actual = _sha256(partial)
            if actual != artifact["sha256"]:
                partial.unlink(missing_ok=True)
                raise AssistedSourceFetchError(
                    f"SHA256 mismatch for {artifact['filename']}: expected {artifact['sha256']}, got {actual}",
                )
            partial.replace(target)
            action = "downloaded"

        records.append(
            {
                "filename": artifact["filename"],
                "sha256": artifact["sha256"],
                "size_bytes": target.stat().st_size,
                "action": action,
            },
        )

    return {
        "ok": True,
        "manifest": str(manifest_path),
        "wheelhouse": str(destination),
        "network_used": any(item["action"] == "downloaded" for item in records),
        "artifacts": records,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch pinned DockStart Assisted release artifacts.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--wheelhouse", default="")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = fetch_assisted_sources(
            args.repo_root,
            args.wheelhouse or None,
            timeout=max(10, args.timeout),
        )
    except Exception as exc:  # noqa: BLE001 - release CLI emits structured failure output.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
