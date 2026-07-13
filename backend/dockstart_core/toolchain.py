"""DockStart bundled toolchain status helpers."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from adapters import meeko_adapter, python_adapter, rdkit_adapter, vina_adapter
from dockstart_core.models import ToolCheckResult
from dockstart_core.settings import load_settings
from dockstart_core.toolchain_paths import (
    get_bundled_python_path,
    get_bundled_vina_candidates,
    get_bundled_vina_path,
    get_existing_bundled_vina_path,
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


def _manifest_section(section_name: str) -> tuple[dict[str, Any], str]:
    manifest, manifest_error = _read_manifest(get_toolchain_manifest_path())
    section = manifest.get(section_name)
    return section if isinstance(section, dict) else {}, manifest_error


def _manifest_section_with_aliases(section_name: str, *aliases: str) -> tuple[dict[str, Any], str]:
    manifest, manifest_error = _read_manifest(get_toolchain_manifest_path())
    for name in (section_name, *aliases):
        section = manifest.get(name)
        if isinstance(section, dict):
            return section, manifest_error
    return {}, manifest_error


def _binary_integrity(
    section_name: str,
    binary_path: Path,
    display_name: str,
    missing_message: str,
) -> dict[str, Any]:
    aliases = ("vina",) if section_name == "bundled_vina" else ()
    bundled_manifest, manifest_error = _manifest_section_with_aliases(section_name, *aliases)
    binary_exists = binary_path.is_file()
    sha256 = calculate_file_sha256(binary_path) if binary_exists else ""
    manifest_sha256 = str(bundled_manifest.get("sha256", "") or "")
    sha256_matches = bool(sha256 and manifest_sha256 and sha256 == manifest_sha256)
    manifest_bundled = bool(bundled_manifest.get("bundled", bundled_manifest.get("exists", False)))

    warnings: list[str] = []
    if not binary_exists:
        warnings.append(missing_message)
    if binary_exists and not manifest_bundled:
        warnings.append(f"manifest 中 {section_name}.bundled 不是 true，请先运行装配脚本更新 manifest。")
    if binary_exists and not manifest_sha256:
        warnings.append(f"manifest 中缺少 {section_name}.sha256，无法确认 {display_name} 二进制一致性。")
    if binary_exists and manifest_sha256 and not sha256_matches:
        warnings.append(f"manifest 中的 sha256 与当前 {display_name} 不一致，请重新装配。")
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
        "binary_path": str(binary_path),
        "binary_exists": binary_exists,
        "sha256": sha256,
        "manifest_sha256": manifest_sha256,
        "sha256_matches": sha256_matches,
        "manifest_bundled": manifest_bundled,
        "manifest_version": str(bundled_manifest.get("version", "") or ""),
        "manifest_source": str(bundled_manifest.get("source", bundled_manifest.get("source_label", "")) or ""),
        "manifest_prepared_at": str(bundled_manifest.get("prepared_at", "") or ""),
        "warnings": warnings,
    }


def get_bundled_vina_integrity() -> dict[str, Any]:
    bundled_vina_path = get_existing_bundled_vina_path()
    licenses_dir = get_licenses_dir()
    license_path = licenses_dir / AUTODOCK_VINA_LICENSE_FILE
    notices_path = licenses_dir / "THIRD_PARTY_NOTICES.md"
    base_integrity = _binary_integrity(
        "bundled_vina",
        bundled_vina_path,
        "vina.exe",
        "未发现随应用提供的 vina.exe，Basic 发布包暂时不能运行真实对接。",
    )

    license_exists = license_path.is_file()
    notices_exists = notices_path.is_file()
    notices_has_entry = _notice_has_autodock_vina(notices_path)
    warnings = list(base_integrity["warnings"])
    if not license_exists:
        warnings.append("缺少 resources/licenses/AutoDock-Vina_LICENSE.txt，不能确认 Vina 许可证随包分发。")
    if not notices_has_entry:
        warnings.append("THIRD_PARTY_NOTICES.md 中没有检测到 AutoDock Vina 条目。")

    if not base_integrity["binary_exists"]:
        package_status = "missing"
    elif warnings:
        package_status = "incomplete"
    else:
        package_status = "ready"

    return {
        **base_integrity,
        "status": package_status,
        "license_path": str(license_path),
        "license_exists": license_exists,
        "third_party_notices_path": str(notices_path),
        "third_party_notices_exists": notices_exists,
        "third_party_notices_has_autodock_vina": notices_has_entry,
        "warnings": warnings,
        "message": "随附 Vina 打包完整性检查已完成。",
    }


def validate_bundled_vina_package() -> dict[str, Any]:
    integrity = get_bundled_vina_integrity()
    is_ready = integrity["status"] == "ready"
    return {
        "ok": is_ready,
        "status": integrity["status"],
        "integrity": integrity,
        "warnings": integrity["warnings"],
        "message": "随附 Vina 已满足 Basic 发布条件。" if is_ready else "随附 Vina 尚未满足 Basic 发布条件。",
        "error": None
        if is_ready
        else {
            "code": "BUNDLED_VINA_PACKAGE_INCOMPLETE",
            "message": "随附 Vina 打包检查未通过。",
            "raw_error": "\n".join(integrity["warnings"]),
            "suggestion": "请确认 vina.exe、manifest sha256、AutoDock Vina license 和 THIRD_PARTY_NOTICES.md 均已准备。",
        },
    }


def get_bundled_python_integrity() -> dict[str, Any]:
    bundled_python_path = get_bundled_python_path()
    integrity = _binary_integrity(
        "bundled_python",
        bundled_python_path,
        "python.exe",
        "未发现内置 python.exe，将回退到用户配置 Python 或当前环境。",
    )
    return {
        **integrity,
        "message": "后端 Python runtime 完整性检查已完成。",
    }


def validate_bundled_python_package() -> dict[str, Any]:
    integrity = get_bundled_python_integrity()
    is_ready = integrity["status"] == "ready"
    return {
        "ok": is_ready,
        "status": integrity["status"],
        "integrity": integrity,
        "warnings": integrity["warnings"],
        "message": "后端 Python runtime 已满足 Basic 发布条件。" if is_ready else "后端 Python runtime 尚未满足 Basic 发布条件。",
        "error": None
        if is_ready
        else {
            "code": "BUNDLED_PYTHON_PACKAGE_INCOMPLETE",
            "message": "后端 Python runtime 打包检查未通过。",
            "raw_error": "\n".join(integrity["warnings"]),
            "suggestion": "请确认 python.exe 已放入 resources/python/，并通过 prepare_bundled_python.py 更新 manifest sha256。",
        },
    }


def get_resolved_python(configured_python_path: str | None = None) -> ToolCheckResult:
    settings = load_settings()
    configured_path = settings.tool_paths.python if configured_python_path is None else configured_python_path
    return python_adapter.detect(
        configured_path,
        bundled_path=str(get_bundled_python_path()),
        prefer_configured=bool(configured_path.strip()),
    )


def _full_status(resources_dir: Path, bundled_ok: bool, notices_exists: bool, manifest_exists: bool, license_exists: bool) -> str:
    if bundled_ok and notices_exists and manifest_exists and license_exists:
        return "ready"
    if resources_dir.exists() or notices_exists or manifest_exists:
        return "partial"
    return "missing"


def _detect_bundled_python() -> ToolCheckResult | None:
    bundled_python_path = get_bundled_python_path()
    if not bundled_python_path.is_file():
        return None
    return python_adapter.detect("", bundled_path=str(bundled_python_path))


def _tool_to_dict(result: ToolCheckResult | None) -> dict[str, Any] | None:
    return result.to_dict() if result else None


def build_first_run_toolchain_guidance(
    active_vina: ToolCheckResult,
    resolved_python: ToolCheckResult,
    rdkit_detection: ToolCheckResult,
    meeko_detection: ToolCheckResult,
) -> dict[str, Any]:
    if active_vina.status != "ok":
        return {
            "status": "needs_vina",
            "recommended_action": "先配置 AutoDock Vina。没有 Vina 时无法执行 docking。",
            "primary_page": "settings",
            "message": "未检测到可用 Vina，请在设置页配置 vina.exe，或准备 bundled Vina。",
        }
    if resolved_python.status != "ok":
        return {
            "status": "needs_python",
            "recommended_action": "先配置可用 Python。没有 Python 时无法检测 RDKit/Meeko。",
            "primary_page": "settings",
            "message": "当前 Python 不可用，请在设置页配置 Python 路径。",
        }
    if rdkit_detection.status != "ok" or meeko_detection.status != "ok":
        return {
            "status": "needs_rdkit_meeko",
            "recommended_action": "如果要自动准备 PDBQT，请配置带 RDKit/Meeko 的独立 conda Python 环境。",
            "primary_page": "toolchain-status",
            "message": "Vina 可用，但 RDKit/Meeko 尚未全部检测通过。手动 PDBQT docking 仍可继续。",
        }
    if resolved_python.source != "configured" and resolved_python.source != "bundled":
        return {
            "status": "current_environment",
            "recommended_action": "建议配置独立 conda Python 工具链，提高 RDKit/Meeko preparation 的可复现性。",
            "primary_page": "settings",
            "message": "当前使用的是运行环境 Python，而不是用户配置或 bundled Python。",
        }
    return {
        "status": "ready",
        "recommended_action": "工具链基础状态可用。可以创建项目或打开已有项目。",
        "primary_page": "project-create",
        "message": "Vina、Python、RDKit 和 Meeko 均已检测通过。",
    }


def get_toolchain_status() -> dict[str, Any]:
    resource_dir = get_resource_dir()
    resources_dir = get_toolchain_root()
    tools_dir = resources_dir / "tools"
    vina_dir = resources_dir / "vina"
    legacy_vina_dir = tools_dir / "vina"
    python_dir = resources_dir / "python"
    licenses_dir = get_licenses_dir()
    notices_path = licenses_dir / "THIRD_PARTY_NOTICES.md"
    manifest_path = get_toolchain_manifest_path()
    bundled_vina_path = get_existing_bundled_vina_path()
    preferred_bundled_vina_path = get_bundled_vina_path()
    bundled_python_path = get_bundled_python_path()
    manifest, manifest_error = _read_manifest(manifest_path)
    bundled_vina_integrity = get_bundled_vina_integrity()
    bundled_vina_package = validate_bundled_vina_package()
    bundled_python_integrity = get_bundled_python_integrity()
    bundled_python_package = validate_bundled_python_package()

    settings = load_settings()
    active_vina = vina_adapter.detect(settings.tool_paths.vina, str(bundled_vina_path))
    resolved_python = get_resolved_python(settings.tool_paths.python)
    meeko_detection = meeko_adapter.detect(resolved_python.path, resolved_python.source)
    rdkit_detection = rdkit_adapter.detect(resolved_python.path, resolved_python.source)
    first_run_guidance = build_first_run_toolchain_guidance(
        active_vina,
        resolved_python,
        rdkit_detection,
        meeko_detection,
    )

    bundled_vina_exists = bundled_vina_path.is_file()
    bundled_vina_detection = active_vina if active_vina.source == "bundled" else None
    if bundled_vina_exists and bundled_vina_detection is None:
        bundled_vina_detection = vina_adapter.detect("", str(bundled_vina_path))

    bundled_python_exists = bundled_python_path.is_file()
    bundled_python_detection = resolved_python if resolved_python.source == "bundled" else _detect_bundled_python()

    bundled_vina_status = bundled_vina_detection.status if bundled_vina_detection else "missing"
    bundled_python_status = bundled_python_detection.status if bundled_python_detection else "missing"
    full_status = _full_status(
        resources_dir,
        bundled_ok=bundled_vina_status == "ok",
        notices_exists=notices_path.is_file(),
        manifest_exists=manifest_path.is_file(),
        license_exists=(licenses_dir / AUTODOCK_VINA_LICENSE_FILE).is_file(),
    )
    warnings = bundled_vina_integrity["warnings"] + bundled_python_integrity["warnings"]

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
            "exists": bundled_vina_exists,
            "path": str(bundled_vina_path),
            "preferred_path": str(preferred_bundled_vina_path),
            "version": bundled_vina_detection.version if bundled_vina_detection else "",
            "status": bundled_vina_status,
            "message": (
                bundled_vina_detection.message
                if bundled_vina_detection
                else "未发现随应用提供的 Vina。请重新安装完整 Basic 包，或在设置页配置外部 Vina。"
            ),
            "raw_error": bundled_vina_detection.raw_error if bundled_vina_detection else "",
            "sha256": bundled_vina_integrity["sha256"],
            "package_status": bundled_vina_integrity["status"],
        },
        "bundled_vina_integrity": bundled_vina_integrity,
        "bundled_vina_package": bundled_vina_package,
        "bundled_python": {
            "exists": bundled_python_exists,
            "path": str(bundled_python_path),
            "version": bundled_python_detection.version if bundled_python_detection else bundled_python_integrity["manifest_version"],
            "status": bundled_python_status,
            "message": (
                bundled_python_detection.message
                if bundled_python_detection
                else "未发现随应用提供的后端 Python。将尝试用户配置 Python 或当前 Python 环境。"
            ),
            "raw_error": bundled_python_detection.raw_error if bundled_python_detection else "",
            "sha256": bundled_python_integrity["sha256"],
            "package_status": bundled_python_integrity["status"],
        },
        "bundled_python_integrity": bundled_python_integrity,
        "bundled_python_package": bundled_python_package,
        "warnings": warnings,
        "active_vina": active_vina.to_dict(),
        "active_source": active_vina.source,
        "resolved_python": resolved_python.to_dict(),
        "python_source": resolved_python.source,
        "meeko_for_python": meeko_detection.to_dict(),
        "rdkit_for_python": rdkit_detection.to_dict(),
        "meeko_python_source": meeko_detection.source,
        "rdkit_python_source": rdkit_detection.source,
        "first_run_guidance": first_run_guidance,
        "licenses": {
            "exists": licenses_dir.is_dir(),
            "third_party_notices": str(notices_path),
            "third_party_notices_exists": notices_path.is_file(),
        },
        "resources": {
            "exists": resources_dir.is_dir(),
            "tools_dir_exists": tools_dir.is_dir(),
            "vina_dir_exists": vina_dir.is_dir(),
            "legacy_vina_dir_exists": legacy_vina_dir.is_dir(),
            "bundled_vina_candidates": [str(path) for path in get_bundled_vina_candidates()],
            "python_dir_exists": python_dir.is_dir(),
        },
        "full_status": full_status,
        "message": "DockStart 随附资源状态已读取。",
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
