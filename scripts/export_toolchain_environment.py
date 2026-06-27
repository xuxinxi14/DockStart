"""Export a reproducible RDKit/Meeko environment description for DockStart."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT_ENV_VAR = "DOCKSTART_REPO_ROOT"
DEFAULT_ENV_NAME = "dockstart-rdkit-meeko"
DEFAULT_OUTPUT_RELATIVE = Path("docs", "release", "environment-dockstart-rdkit-meeko.yml")


def get_repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root:
        return Path(repo_root).expanduser().resolve()
    configured_root = os.environ.get(REPO_ROOT_ENV_VAR, "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _ensure_backend_path(repo_root: Path) -> None:
    backend_root = repo_root / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))


def get_configured_python_path(repo_root: str | Path | None = None) -> str:
    root = get_repo_root(repo_root)
    _ensure_backend_path(root)
    from dockstart_core.settings import load_settings  # noqa: PLC0415

    return load_settings().tool_paths.python.strip()


def detect_conda_prefix(python_path: str | Path) -> Path | None:
    executable = Path(python_path).expanduser().resolve()
    if not executable.is_file():
        return None
    prefix = executable.parent
    if (prefix / "conda-meta").is_dir():
        return prefix
    if "envs" in [part.lower() for part in prefix.parts] and (prefix / "python.exe").is_file():
        return prefix
    return None


def probe_python_environment(python_path: str | Path) -> dict[str, str]:
    executable = Path(python_path).expanduser().resolve()
    probe_script = r"""
import importlib
import json
import platform
import sys

def version_for(module_name):
    try:
        module = importlib.import_module(module_name)
        return str(getattr(module, "__version__", "") or getattr(module, "rdBase", None).rdkitVersion)
    except Exception:
        return ""

payload = {
    "python_version": platform.python_version(),
    "python_executable": sys.executable,
    "rdkit_version": version_for("rdkit"),
    "meeko_version": version_for("meeko"),
    "numpy_version": version_for("numpy"),
    "scipy_version": version_for("scipy"),
    "platform": platform.platform(),
}
print(json.dumps(payload, ensure_ascii=False))
"""
    completed = subprocess.run(
        [str(executable), "-c", probe_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "").strip())
    payload = json.loads(completed.stdout.strip() or "{}")
    return {key: str(value or "") for key, value in payload.items() if isinstance(key, str)}


def _version_pin(package: str, version: str) -> str:
    return f"  - {package}={version}" if version else f"  - {package}"


def build_environment_yml(metadata: dict[str, str], env_name: str = DEFAULT_ENV_NAME) -> str:
    generated_at = metadata.get("generated_at") or datetime.now(UTC).isoformat()
    python_version = metadata.get("python_version", "")
    python_major_minor = ".".join(python_version.split(".")[:2]) if python_version else "3.11"
    lines = [
        "# DockStart RDKit/Meeko environment export",
        "# This file records a reproducible conda-forge environment target.",
        f"# generated_at: {generated_at}",
        f"# platform: {metadata.get('platform', platform.platform())}",
        f"# rdkit_version: {metadata.get('rdkit_version', '')}",
        f"# meeko_version: {metadata.get('meeko_version', '')}",
        "name: " + env_name,
        "channels:",
        "  - conda-forge",
        "dependencies:",
        f"  - python={python_major_minor}",
        _version_pin("rdkit", metadata.get("rdkit_version", "")),
        _version_pin("meeko", metadata.get("meeko_version", "")),
        _version_pin("numpy", metadata.get("numpy_version", "")),
        _version_pin("scipy", metadata.get("scipy_version", "")),
        "",
    ]
    return "\n".join(lines)


def export_toolchain_environment(
    repo_root: str | Path | None = None,
    python_path: str = "",
    output_path: str | Path | None = None,
    env_name: str = DEFAULT_ENV_NAME,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = get_repo_root(repo_root)
    configured_python = python_path.strip() or get_configured_python_path(root)
    if not configured_python:
        return {
            "ok": False,
            "status": "missing_configured_python",
            "message": "尚未在 DockStart 设置中配置 Python 路径，无法导出 RDKit/Meeko 环境。",
            "suggestion": "请先在设置页配置 dockstart-rdkit-meeko conda 环境中的 python.exe。",
        }

    executable = Path(configured_python).expanduser().resolve()
    if not executable.is_file():
        return {
            "ok": False,
            "status": "python_missing",
            "python_path": str(executable),
            "message": "配置的 Python 路径不存在，无法导出环境。",
            "suggestion": "请检查设置页中的 Python 路径。",
        }

    conda_prefix = detect_conda_prefix(executable)
    if conda_prefix is None:
        return {
            "ok": False,
            "status": "not_conda",
            "python_path": str(executable),
            "message": "当前 configured Python 看起来不是 conda 环境，未导出 conda yml。",
            "suggestion": "推荐使用独立 conda/mamba 环境 dockstart-rdkit-meeko，并在设置页配置该环境的 python.exe。",
        }

    metadata = probe_python_environment(executable)
    metadata["generated_at"] = datetime.now(UTC).isoformat()
    metadata["conda_prefix_name"] = conda_prefix.name
    yml_text = build_environment_yml(metadata, env_name=env_name)
    output = Path(output_path).expanduser().resolve() if output_path else root / DEFAULT_OUTPUT_RELATIVE

    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(yml_text, encoding="utf-8")

    return {
        "ok": True,
        "status": "exported" if not dry_run else "dry_run",
        "python_path": str(executable),
        "conda_prefix": str(conda_prefix),
        "output_path": str(output),
        "metadata": metadata,
        "content": yml_text if dry_run else "",
        "message": "DockStart RDKit/Meeko conda 环境描述已导出。"
        if not dry_run
        else "Dry-run 完成，未写入环境 yml。",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export DockStart RDKit/Meeko conda environment metadata.")
    parser.add_argument("--repo-root", default="", help="DockStart repository root. Defaults to this script's parent.")
    parser.add_argument("--python", default="", help="Python executable override. Defaults to DockStart configured Python.")
    parser.add_argument("--output", default="", help="Output yml path. Defaults to docs/release/environment-dockstart-rdkit-meeko.yml.")
    parser.add_argument("--env-name", default=DEFAULT_ENV_NAME, help="Conda environment name written to yml.")
    parser.add_argument("--dry-run", action="store_true", help="Print metadata without writing yml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = export_toolchain_environment(
            repo_root=args.repo_root or None,
            python_path=args.python,
            output_path=args.output or None,
            env_name=args.env_name,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as exc:  # noqa: BLE001 - script should return structured JSON errors.
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "error",
                    "message": "导出 DockStart RDKit/Meeko 环境描述时发生错误。",
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

