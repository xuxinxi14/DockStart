"""DockStart bundled toolchain status helpers."""

from __future__ import annotations

import hashlib
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

AUTODOCK_VINA_LICENSE_FILE = "AutoDock-Vina_LICENSE.txt"
AUTODOCK_VINA_NOTICE_KEYWORD = "autodock vina"


def _read_manifest(manifest_path: Path) -> tuple[dict[str, Any], str]:
    if not manifest_path.is_file():
        return {}, ""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - status page should remain readable.
        return {}, str(exc)
    return data if isinstance(data, dict) else {}, ""


def calculate_file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _notice_has_autodock_vina(notices_path: Path) -> bool:
    if not notices_path.is_file():
        return False
    try:
        return AUTODOCK_VINA_NOTICE_KEYWORD in notices_path.read_text(encoding="utf-8").lower()
    except Exception:
        return False


def get_bundled_vina_integrity() -> dict[str, Any]:
    bundled_vina_path = get_bundled_vina_path()
    licenses_dir = get_licenses_dir()
    license_path = licenses_dir / AUTODOCK_VINA_LICENSE_FILE
    notices_path = licenses_dir / "THIRD_PARTY_NOTICES.md"
    manifest_path = get_toolchain_manifest_path()
    manifest, manifest_error = _read_manifest(manifest_path)
    bundled_manifest = manifest.get("bundled_vina") if isinstance(manifest.get("bundled_vina"), dict) else {}

    binary_exists = bundled_vina_path.is_file()
    sha256 = calculate_file_sha256(bundled_vina_path) if binary_exists else ""
    manifest_sha256 = str(bundled_manifest.get("sha256", "") or "")
    sha256_matches = bool(sha256 and manifest_sha256 and sha256 == manifest_sha256)
    license_exists = license_path.is_file()
    notices_exists = notices_path.is_file()
    notices_has_entry = _notice_has_autodock_vina(notices_path)
    manifest_bundled = bool(bundled_manifest.get("bundled"))

    warnings: list[str] = []
    if not binary_exists:
        warnings.append("未发现内置 vina.exe，Full 打包暂不能使用内置 Vina。")
    if binary_exists and not manifest_bundled:
        warnings.append("manifest 中 bundled_vina.bundled 不是 true，请先运行装配脚本更新 manifest。")
    if binary_exists and not manifest_sha256:
        warnings.append("manifest 中缺少 bundled_vina.sha256，无法确认二进制一致性。")
    if binary_exists and manifest_sha256 and not sha256_matches:
        warnings.append("manifest 中的 sha256 与当前 vina.exe 不一致，请重新装配。")
    if not license_exists:
        warnings.append("缺少 resources/licenses/AutoDock-Vina_LICENSE.txt，不能确认 Vina 许可证随包分发。")
    if not notices_has_entry:
        warnings.append("THIRD_PARTY_NOTICES.md 中没有检测到 AutoDock Vina 条目。")
    if manifest_error:
        warnings.append("toolchain_manifest.json 读取失败，请检查 JSON 格式。")

    if not binary_exists:
        package_status = "missing"
    elif warnings:
        package_status = "incomplete"
    else:
        package_status = "ready"

    return {
        "status": package_status,
        "binary_path": str(bundled_vina_path),
        "binary_exists": binary_exists,
        "sha256": sha256,
        "manifest_sha256": manifest_sha256,
        "sha256_matches": sha256_matches,
        "manifest_bundled": manifest_bundled,
        "manifest_version": str(bundled_manifest.get("version", "") or ""),
        "manifest_source": str(bundled_manifest.get("source", "") or ""),
        "manifest_prepared_at": str(bundled_manifest.get("prepared_at", "") or ""),
        "license_path": str(license_path),
        "license_exists": license_exists,
        "third_party_notices_path": str(notices_path),
        "third_party_notices_exists": notices_exists,
        "third_party_notices_has_autodock_vina": notices_has_entry,
        "warnings": warnings,
        "message": "内置 Vina 打包完整性检查已完成。",
    }


def validate_bundled_vina_package() -> dict[str, Any]:
    integrity = get_bundled_vina_integrity()
    is_ready = integrity["status"] == "ready"
    return {
        "ok": is_ready,
        "status": integrity["status"],
        "integrity": integrity,
        "warnings": integrity["warnings"],
        "message": "内置 Vina 可以用于 Full 打包。" if is_ready else "内置 Vina 尚未满足 Full 打包条件。",
        "error": None
        if is_ready
        else {
            "code": "BUNDLED_VINA_PACKAGE_INCOMPLETE",
            "message": "内置 Vina 打包检查未通过。",
            "raw_error": "\n".join(integrity["warnings"]),
            "suggestion": "请确认 vina.exe、manifest sha256、AutoDock Vina license 和 THIRD_PARTY_NOTICES.md 均已准备。",
        },
    }


def _full_status(
    resources_dir: Path,
    bundled_ok: bool,
    notices_exists: bool,
    manifest_exists: bool,
    license_exists: bool,
) -> str:
    if bundled_ok and notices_exists and manifest_exists and license_exists:
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
    bundled_integrity = get_bundled_vina_integrity()
    bundled_package = validate_bundled_vina_package()

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
        license_exists=(licenses_dir / AUTODOCK_VINA_LICENSE_FILE).is_file(),
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
            "sha256": bundled_integrity["sha256"],
            "package_status": bundled_integrity["status"],
        },
        "bundled_vina_integrity": bundled_integrity,
        "bundled_vina_package": bundled_package,
        "warnings": bundled_integrity["warnings"],
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
