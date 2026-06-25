"""Shared paths for DockStart bundled toolchain resources."""

from __future__ import annotations

import os
from pathlib import Path

TOOLCHAIN_ROOT_ENV_VAR = "DOCKSTART_REPO_ROOT"


def get_project_root() -> Path:
    configured_root = os.environ.get(TOOLCHAIN_ROOT_ENV_VAR, "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def get_resources_dir(project_root: str | Path | None = None) -> Path:
    root = Path(project_root).expanduser().resolve() if project_root else get_project_root()
    return root / "resources"


def get_bundled_vina_path(project_root: str | Path | None = None) -> Path:
    return get_resources_dir(project_root) / "tools" / "vina" / "vina.exe"
