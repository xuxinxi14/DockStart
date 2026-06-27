"""Shared paths for DockStart bundled toolchain resources."""

from __future__ import annotations

import os
from pathlib import Path

TOOLCHAIN_ROOT_ENV_VAR = "DOCKSTART_REPO_ROOT"
RESOURCE_DIR_ENV_VAR = "DOCKSTART_RESOURCE_DIR"


def _configured_path(env_var: str) -> Path | None:
    configured = os.environ.get(env_var, "").strip()
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def get_project_root() -> Path:
    configured_root = _configured_path(TOOLCHAIN_ROOT_ENV_VAR)
    if configured_root:
        return configured_root
    return Path(__file__).resolve().parents[2]


def get_resource_dir() -> Path | None:
    return _configured_path(RESOURCE_DIR_ENV_VAR)


def get_runtime_mode() -> str:
    if get_resource_dir() is not None:
        return "packaged"
    if get_project_root():
        return "dev"
    return "unknown"


def get_toolchain_root(project_root: str | Path | None = None) -> Path:
    resource_dir = get_resource_dir()
    if resource_dir is not None:
        return resource_dir / "resources"
    root = Path(project_root).expanduser().resolve() if project_root else get_project_root()
    return root / "resources"


def get_resources_dir(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root)


def get_licenses_dir(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root) / "licenses"


def get_toolchain_manifest_path(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root) / "toolchain_manifest.json"


def get_bundled_vina_path(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root) / "vina" / "vina.exe"


def get_legacy_bundled_vina_path(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root) / "tools" / "vina" / "vina.exe"


def get_bundled_vina_candidates(project_root: str | Path | None = None) -> list[Path]:
    return [
        get_bundled_vina_path(project_root),
        get_legacy_bundled_vina_path(project_root),
    ]


def get_existing_bundled_vina_path(project_root: str | Path | None = None) -> Path:
    for candidate in get_bundled_vina_candidates(project_root):
        if candidate.is_file():
            return candidate
    return get_bundled_vina_path(project_root)


def get_bundled_python_path(project_root: str | Path | None = None) -> Path:
    return get_toolchain_root(project_root) / "python" / "python.exe"
