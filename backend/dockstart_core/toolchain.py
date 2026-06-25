"""DockStart bundled toolchain status helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from adapters import vina_adapter
from dockstart_core.settings import load_settings
from dockstart_core.toolchain_paths import (
    get_bundled_vina_path,
    get_licenses_dir,
    get_resource_dir,
    get_runtime_mode,
    get_toolchain_manifest_path,
    get_toolchain_root,
)


def _read_manifest(manifest_path: Path) -> tuple[dict[str, Any], str]:
    if not manifest_path.is_file():
        return {}, ""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - status page should remain readable.
        return {}, str(exc)
    return data if isinstance(data, dict) else {}, ""


def _full_status(resources_dir: Path, bundled_ok: bool, notices_exists: bool, manifest_exists: bool) -> str:
    if bundled_ok and notices_exists and manifest_exists:
        return "ready"
    if resources_dir.exists() or notices_exists or manifest_exists:
        return "partial"
    return "missing"


def get_toolchain_status() -> dict[str, Any]:
    resource_dir = get_resource_dir()
    resources_dir = get_toolchain_root()
    tools_dir = resources_dir / "tools"
    vina_dir = tools_dir / "vina"
    licenses_dir = get_licenses_dir()
    notices_path = licenses_dir / "THIRD_PARTY_NOTICES.md"
    manifest_path = get_toolchain_manifest_path()
    bundled_vina_path = get_bundled_vina_path()
    manifest, manifest_error = _read_manifest(manifest_path)

    settings = load_settings()
    active_vina = vina_adapter.detect(settings.tool_paths.vina, str(bundled_vina_path))
    bundled_exists = bundled_vina_path.is_file()
    bundled_detection = active_vina if active_vina.source == "bundled" else None
    if bundled_exists and bundled_detection is None:
        bundled_detection = vina_adapter.detect("", str(bundled_vina_path))

    bundled_status = bundled_detection.status if bundled_detection else "missing"
    bundled_version = bundled_detection.version if bundled_detection else ""
    bundled_message = (
        bundled_detection.message
        if bundled_detection
        else "未发现内置 Vina。可将 vina.exe 放置到 resources/tools/vina/ 后重新检测。"
    )
    bundled_raw_error = bundled_detection.raw_error if bundled_detection else ""
    full_status = _full_status(
        resources_dir,
        bundled_ok=bundled_status == "ok",
        notices_exists=notices_path.is_file(),
        manifest_exists=manifest_path.is_file(),
    )

    return {
        "ok": True,
        "runtime_mode": get_runtime_mode(),
        "resource_dir": str(resource_dir) if resource_dir else "",
        "toolchain_root": str(resources_dir),
        "tools_dir": str(tools_dir),
        "licenses_dir": str(licenses_dir),
        "manifest_file": str(manifest_path),
        "manifest_exists": manifest_path.is_file(),
        "manifest": manifest,
        "manifest_error": manifest_error,
        "bundled_vina": {
            "exists": bundled_exists,
            "path": str(bundled_vina_path),
            "version": bundled_version,
            "status": bundled_status,
            "message": bundled_message,
            "raw_error": bundled_raw_error,
        },
        "active_vina": active_vina.to_dict(),
        "active_source": active_vina.source,
        "licenses": {
            "exists": licenses_dir.is_dir(),
            "third_party_notices": str(notices_path),
            "third_party_notices_exists": notices_path.is_file(),
        },
        "resources": {
            "exists": resources_dir.is_dir(),
            "tools_dir_exists": tools_dir.is_dir(),
            "vina_dir_exists": vina_dir.is_dir(),
        },
        "full_status": full_status,
        "message": "DockStart 内置工具链状态已读取。",
        "error": None,
    }


def get_toolchain_status_json() -> str:
    return json.dumps(get_toolchain_status(), ensure_ascii=False)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(get_toolchain_status_json())


if __name__ == "__main__":
    main()
