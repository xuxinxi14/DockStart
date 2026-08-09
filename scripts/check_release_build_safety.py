"""Fail-closed Windows release-build cleanup preflight.

This helper never removes files.  It inventories DockStart installation paths
from the Windows registry and proves that every caller-supplied cleanup root is
an isolated child of ``<repo>/.release``.  A release script may proceed only
when this command exits successfully.

The registry-independent validation functions are deliberately pure enough to
exercise on non-Windows CI.  Only the CLI and registry discovery layer require
Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


UNINSTALL_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
_PERCENT_ENVIRONMENT = re.compile(r"%([^%]+)%")


class ReleaseBuildSafetyError(RuntimeError):
    """Raised when a release cleanup cannot be proven safe."""


@dataclass(frozen=True)
class RegistryInstallRecord:
    """Raw registry evidence for one DockStart installation location."""

    kind: str
    hive: str
    view: str
    key: str
    display_name: str = "DockStart"
    install_location: str | None = None
    uninstall_string: str | None = None


@dataclass(frozen=True)
class ResolvedInstallation:
    """A registry record whose installation directory has been resolved."""

    record: RegistryInstallRecord
    install_root: Path
    path_source: str


def _environment_lookup(environ: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {str(key).casefold(): str(value) for key, value in source.items()}


def expand_environment_variables(
    value: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Expand Windows ``%NAME%`` variables.

    Missing variables are rejected instead of being left in a path.  Expansion
    is repeated so a variable whose value references another variable remains
    deterministic and fail closed.
    """

    lookup = _environment_lookup(environ)
    expanded = str(value)

    def percent_replacement(match: re.Match[str]) -> str:
        name = match.group(1)
        replacement = lookup.get(name.casefold())
        if replacement is None:
            raise ReleaseBuildSafetyError(f"路径包含未定义的环境变量：%{name}%")
        return replacement

    for _ in range(16):
        replaced = _PERCENT_ENVIRONMENT.sub(percent_replacement, expanded)
        if replaced == expanded:
            return replaced
        expanded = replaced
    raise ReleaseBuildSafetyError("环境变量展开层级过深，无法安全解析路径。")


def _strip_path_quotes(value: str, *, label: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ReleaseBuildSafetyError(f"{label}为空。")
    if stripped[0] == '"':
        if len(stripped) < 2 or stripped[-1] != '"':
            raise ReleaseBuildSafetyError(f"{label}包含不配对的引号。")
        stripped = stripped[1:-1].strip()
    elif stripped[-1] == '"':
        raise ReleaseBuildSafetyError(f"{label}包含不配对的引号。")
    if not stripped or '"' in stripped:
        raise ReleaseBuildSafetyError(f"{label}包含非法引号。")
    if "\x00" in stripped:
        raise ReleaseBuildSafetyError(f"{label}包含空字符。")
    return stripped


def normalize_absolute_path(
    value: str,
    *,
    label: str,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return a canonical absolute path, following any existing junctions.

    ``Path.resolve(strict=False)`` resolves every existing path component while
    still allowing a not-yet-created cleanup directory at the tail.
    """

    expanded = expand_environment_variables(str(value), environ=environ)
    stripped = _strip_path_quotes(expanded, label=label)
    try:
        candidate = Path(stripped).expanduser()
        if not candidate.is_absolute():
            raise ReleaseBuildSafetyError(f"{label}必须是绝对路径：{stripped}")
        return candidate.resolve(strict=False)
    except ReleaseBuildSafetyError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReleaseBuildSafetyError(f"无法规范化{label}：{stripped}") from exc


def extract_uninstaller_executable(uninstall_string: str) -> str:
    """Extract the executable token from a Windows ``UninstallString``.

    Quoted executables and unquoted absolute paths containing spaces are both
    supported.  Wrapper commands such as a relative ``MsiExec.exe`` are later
    rejected by :func:`normalize_absolute_path`.
    """

    command = str(uninstall_string).strip()
    if not command:
        raise ReleaseBuildSafetyError("UninstallString 为空，无法确定安装路径。")
    if "\x00" in command:
        raise ReleaseBuildSafetyError("UninstallString 包含空字符。")

    if command[0] in {'"', "'"}:
        quote = command[0]
        closing = command.find(quote, 1)
        if closing < 0:
            raise ReleaseBuildSafetyError("UninstallString 包含不配对的引号。")
        executable = command[1:closing].strip()
    else:
        match = re.match(r"(?is)^(.+?\.exe)(?=\s|$)", command)
        if match is None:
            raise ReleaseBuildSafetyError("UninstallString 不包含可识别的 .exe 路径。")
        executable = match.group(1).strip()

    if not executable or not executable.casefold().endswith(".exe"):
        raise ReleaseBuildSafetyError("UninstallString 的首个命令不是可识别的 .exe。")
    return executable


def resolve_registry_installation(
    record: RegistryInstallRecord,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedInstallation:
    """Resolve a registry record to the installation directory."""

    source = f"{record.hive} {record.view} {record.key}"
    if record.display_name.strip().casefold() != "dockstart":
        raise ReleaseBuildSafetyError(f"注册记录不是 DockStart：{source}")

    raw_location = str(record.install_location or "").strip()
    if raw_location:
        install_root = normalize_absolute_path(
            raw_location,
            label=f"注册表安装路径（{source}）",
            environ=environ,
        )
        if install_root.exists() and not install_root.is_dir():
            raise ReleaseBuildSafetyError(f"注册表安装路径不是目录：{install_root}")
        return ResolvedInstallation(record, install_root, "install_location")

    if record.kind == "uninstall":
        raw_uninstall = str(record.uninstall_string or "").strip()
        if not raw_uninstall:
            raise ReleaseBuildSafetyError(
                f"DockStart 卸载记录缺少 InstallLocation 和 UninstallString：{source}",
            )
        executable = extract_uninstaller_executable(
            expand_environment_variables(raw_uninstall, environ=environ),
        )
        executable_path = normalize_absolute_path(
            executable,
            label=f"卸载程序路径（{source}）",
            environ=environ,
        )
        if executable_path.exists() and not executable_path.is_file():
            raise ReleaseBuildSafetyError(f"注册表卸载程序路径不是文件：{executable_path}")
        return ResolvedInstallation(record, executable_path.parent, "uninstall_string")

    raise ReleaseBuildSafetyError(f"DockStart publisher 注册记录缺少默认安装路径：{source}")


def _is_same_or_descendant(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _paths_overlap(first: Path, second: Path) -> bool:
    return _is_same_or_descendant(first, second) or _is_same_or_descendant(second, first)


def default_install_directories(
    *,
    environ: Mapping[str, str] | None = None,
) -> list[Path]:
    """Return canonical default DockStart install directories.

    The real NSIS gate rejects non-empty default locations even when the
    registry is already clean.  The pre-build safety gate must enforce the
    same condition so a stale uninstaller or partial installation is reported
    before an expensive release build begins.
    """

    lookup = _environment_lookup(environ)
    directories: list[Path] = []
    seen: set[str] = set()
    for variable in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        raw_base = lookup.get(variable.casefold(), "").strip()
        if not raw_base:
            continue
        base = normalize_absolute_path(
            raw_base,
            label=f"{variable} default install root",
            environ=environ,
        )
        candidate = (base / "DockStart").resolve(strict=False)
        identity = os.path.normcase(str(candidate))
        if identity not in seen:
            seen.add(identity)
            directories.append(candidate)
    return directories


def _path_has_entries_or_is_not_directory(path: Path) -> bool:
    if not path.exists():
        return False
    if not path.is_dir():
        return True
    try:
        return next(path.iterdir(), None) is not None
    except OSError as exc:
        raise ReleaseBuildSafetyError(f"Cannot inspect default DockStart install directory: {path}") from exc


def _resolve_repo_and_cleanup_roots(
    repo_root: str | Path,
    cleanup_roots: Sequence[str | Path],
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path, list[Path]]:
    if not cleanup_roots:
        raise ReleaseBuildSafetyError("至少需要一个 --cleanup-root。")
    try:
        repo = Path(repo_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReleaseBuildSafetyError(f"仓库根目录不存在或无法解析：{repo_root}") from exc
    if not repo.is_dir():
        raise ReleaseBuildSafetyError(f"仓库根路径不是目录：{repo}")

    lexical_release = Path(os.path.abspath(repo / ".release"))
    try:
        resolved_release = lexical_release.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReleaseBuildSafetyError(f"无法解析发布暂存根目录：{lexical_release}") from exc
    if resolved_release == repo or not _is_same_or_descendant(resolved_release, repo):
        raise ReleaseBuildSafetyError(
            f"repo/.release 解析到了仓库之外或仓库根目录：{resolved_release}",
        )
    if lexical_release.exists() and not lexical_release.is_dir():
        raise ReleaseBuildSafetyError(f"repo/.release 已存在但不是目录：{lexical_release}")

    resolved_cleanup_roots: list[Path] = []
    seen: set[str] = set()
    for index, raw_cleanup in enumerate(cleanup_roots, start=1):
        expanded = expand_environment_variables(str(raw_cleanup), environ=environ)
        stripped = _strip_path_quotes(expanded, label=f"cleanup root #{index}")
        try:
            lexical_cleanup = Path(stripped).expanduser()
            if not lexical_cleanup.is_absolute():
                raise ReleaseBuildSafetyError(
                    f"cleanup root #{index} 必须是绝对路径：{stripped}",
                )
            lexical_cleanup = Path(os.path.abspath(lexical_cleanup))
        except ReleaseBuildSafetyError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ReleaseBuildSafetyError(f"无法解析 cleanup root #{index}：{stripped}") from exc

        if lexical_cleanup == lexical_release or not _is_same_or_descendant(
            lexical_cleanup,
            lexical_release,
        ):
            raise ReleaseBuildSafetyError(
                f"cleanup root 必须严格位于 repo/.release 下，且不能等于 .release：{lexical_cleanup}",
            )

        try:
            resolved_cleanup = lexical_cleanup.resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ReleaseBuildSafetyError(f"无法解析 cleanup root #{index}：{lexical_cleanup}") from exc
        if resolved_cleanup == resolved_release or not _is_same_or_descendant(
            resolved_cleanup,
            resolved_release,
        ):
            raise ReleaseBuildSafetyError(
                "cleanup root 通过现有 junction/symlink 解析到了 repo/.release 之外："
                f"{lexical_cleanup} -> {resolved_cleanup}",
            )
        if lexical_cleanup.exists() and not lexical_cleanup.is_dir():
            raise ReleaseBuildSafetyError(f"cleanup root 已存在但不是目录：{lexical_cleanup}")

        identity = os.path.normcase(str(resolved_cleanup))
        if identity not in seen:
            seen.add(identity)
            resolved_cleanup_roots.append(resolved_cleanup)

    return repo, resolved_release, resolved_cleanup_roots


def validate_release_build_safety(
    repo_root: str | Path,
    cleanup_roots: Sequence[str | Path],
    registry_records: Sequence[RegistryInstallRecord],
    *,
    require_no_existing_install: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate cleanup containment and registry-install separation."""

    repo, release_root, cleanups = _resolve_repo_and_cleanup_roots(
        repo_root,
        cleanup_roots,
        environ=environ,
    )
    installations = [
        resolve_registry_installation(record, environ=environ) for record in registry_records
    ]
    nonempty_default_directories = [
        path
        for path in default_install_directories(environ=environ)
        if _path_has_entries_or_is_not_directory(path)
    ]

    if require_no_existing_install and (installations or nonempty_default_directories):
        locations = sorted({str(item.install_root) for item in installations})
        locations.extend(
            str(path)
            for path in nonempty_default_directories
            if str(path) not in locations
        )
        raise ReleaseBuildSafetyError(
            "检测到现有 DockStart 安装；当前门禁要求系统中不能存在任何安装："
            + json.dumps(locations, ensure_ascii=False),
        )

    for installation in installations:
        for cleanup in cleanups:
            if _paths_overlap(installation.install_root, cleanup):
                raise ReleaseBuildSafetyError(
                    "DockStart 安装路径与发布清理路径重叠，拒绝继续："
                    f"install={installation.install_root}; cleanup={cleanup}; "
                    f"registry={installation.record.hive} {installation.record.key}",
                )

    return {
        "ok": True,
        "repo_root": str(repo),
        "release_root": str(release_root),
        "cleanup_roots": [str(path) for path in cleanups],
        "require_no_existing_install": require_no_existing_install,
        "existing_installations": [
            {
                "kind": item.record.kind,
                "hive": item.record.hive,
                "view": item.record.view,
                "key": item.record.key,
                "install_root": str(item.install_root),
                "path_source": item.path_source,
            }
            for item in installations
        ],
        "nonempty_default_install_directories": [
            str(path) for path in nonempty_default_directories
        ],
    }


def read_tauri_publisher(repo_root: str | Path) -> str:
    """Read and validate the Tauri publisher used in the HKCU registry key."""

    config_path = Path(repo_root) / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        publisher = str(config["bundle"]["publisher"]).strip()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBuildSafetyError(f"无法从 Tauri 配置读取 publisher：{config_path}") from exc
    if not publisher or any(character in publisher for character in ("\\", "/", "\x00")):
        raise ReleaseBuildSafetyError("Tauri publisher 为空或不能安全用作注册表键名。")
    return publisher


def _query_optional_value(winreg_module: Any, key: Any, name: str) -> str | None:
    try:
        value = winreg_module.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseBuildSafetyError(f"无法读取注册表值 {name}。") from exc
    return str(value) if value is not None else ""


def _is_no_more_registry_items(error: OSError) -> bool:
    """Return whether ``EnumKey`` reported Windows ERROR_NO_MORE_ITEMS."""

    return getattr(error, "winerror", None) == 259


def collect_windows_registry_records(
    publisher: str,
    *,
    winreg_module: Any | None = None,
) -> list[RegistryInstallRecord]:
    """Enumerate DockStart install evidence in HKCU/HKLM 32/64-bit views."""

    if winreg_module is None:
        if sys.platform != "win32":
            raise ReleaseBuildSafetyError("注册表枚举仅支持 Windows。")
        import winreg as winreg_module  # type: ignore[no-redef]  # pylint: disable=import-outside-toplevel

    roots = (
        ("HKCU", winreg_module.HKEY_CURRENT_USER),
        ("HKLM", winreg_module.HKEY_LOCAL_MACHINE),
    )
    views = (
        ("64-bit", winreg_module.KEY_WOW64_64KEY),
        ("32-bit", winreg_module.KEY_WOW64_32KEY),
    )
    records: list[RegistryInstallRecord] = []

    for hive_name, hive in roots:
        for view_name, view_flag in views:
            try:
                parent = winreg_module.OpenKey(
                    hive,
                    UNINSTALL_REGISTRY_PATH,
                    0,
                    winreg_module.KEY_READ | view_flag,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ReleaseBuildSafetyError(
                    f"无法枚举 {hive_name} {view_name} 的卸载注册表。",
                ) from exc

            with parent:
                index = 0
                while True:
                    try:
                        child_name = winreg_module.EnumKey(parent, index)
                    except OSError as exc:
                        if _is_no_more_registry_items(exc):
                            break
                        raise ReleaseBuildSafetyError(
                            f"枚举卸载注册表时失败：{hive_name} {view_name}",
                        ) from exc
                    index += 1
                    try:
                        child = winreg_module.OpenKey(
                            parent,
                            child_name,
                            0,
                            winreg_module.KEY_READ | view_flag,
                        )
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise ReleaseBuildSafetyError(
                            f"无法打开卸载注册表项：{hive_name} {child_name}",
                        ) from exc
                    with child:
                        display_name = _query_optional_value(
                            winreg_module,
                            child,
                            "DisplayName",
                        )
                        if str(display_name or "").strip().casefold() != "dockstart":
                            continue
                        install_location = _query_optional_value(
                            winreg_module,
                            child,
                            "InstallLocation",
                        )
                        uninstall_string = _query_optional_value(
                            winreg_module,
                            child,
                            "UninstallString",
                        )
                        records.append(
                            RegistryInstallRecord(
                                kind="uninstall",
                                hive=hive_name,
                                view=view_name,
                                key=f"{UNINSTALL_REGISTRY_PATH}\\{child_name}",
                                display_name=str(display_name or ""),
                                install_location=install_location,
                                uninstall_string=uninstall_string,
                            ),
                        )

    publisher_key = rf"Software\{publisher}\DockStart"
    try:
        manufacturer = winreg_module.OpenKey(
            winreg_module.HKEY_CURRENT_USER,
            publisher_key,
            0,
            winreg_module.KEY_READ,
        )
    except FileNotFoundError:
        manufacturer = None
    except OSError as exc:
        raise ReleaseBuildSafetyError(f"无法读取 HKCU {publisher_key}。") from exc

    if manufacturer is not None:
        with manufacturer:
            try:
                default_location = winreg_module.QueryValue(manufacturer, None)
            except FileNotFoundError:
                default_location = None
            except OSError as exc:
                raise ReleaseBuildSafetyError(
                    f"无法读取 HKCU {publisher_key} 的默认安装路径。",
                ) from exc
        records.append(
            RegistryInstallRecord(
                kind="publisher",
                hive="HKCU",
                view="default",
                key=publisher_key,
                install_location=(
                    str(default_location) if default_location is not None else None
                ),
            ),
        )

    return records


def run_windows_preflight(
    repo_root: str | Path,
    cleanup_roots: Sequence[str | Path],
    *,
    require_no_existing_install: bool = False,
    environ: Mapping[str, str] | None = None,
    winreg_module: Any | None = None,
) -> dict[str, Any]:
    """Discover Windows registry evidence and run the pure safety validator."""

    publisher = read_tauri_publisher(repo_root)
    records = collect_windows_registry_records(publisher, winreg_module=winreg_module)
    result = validate_release_build_safety(
        repo_root,
        cleanup_roots,
        records,
        require_no_existing_install=require_no_existing_install,
        environ=environ,
    )
    result["publisher"] = publisher
    return result


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在 Windows 发布构建清理前校验 .release 路径与现有 DockStart 安装隔离。",
    )
    parser.add_argument("--repo-root", required=True, help="DockStart 仓库根目录。")
    parser.add_argument(
        "--cleanup-root",
        action="append",
        required=True,
        help="将被清理的绝对目录；可重复，且必须严格位于 repo/.release 下。",
    )
    parser.add_argument(
        "--require-no-existing-install",
        action="store_true",
        help="只要检测到任意现有 DockStart 安装就拒绝继续。",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if sys.platform != "win32":
        print("DockStart 发布构建安全 preflight 仅支持 Windows。", file=sys.stderr)
        return 2
    try:
        result = run_windows_preflight(
            args.repo_root,
            args.cleanup_root,
            require_no_existing_install=args.require_no_existing_install,
        )
    except ReleaseBuildSafetyError as exc:
        print(f"发布构建安全检查失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - unexpected uncertainty must fail closed.
        print(f"发布构建安全检查发生未知错误，已拒绝继续：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
