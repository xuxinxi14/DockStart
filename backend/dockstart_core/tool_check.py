"""Unified tool detection entrypoint for DockStart."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from adapters import meeko_adapter, python_adapter, rdkit_adapter, viewer_adapter, vina_adapter
from dockstart_core.models import ToolCheckResult
from dockstart_core.settings import load_settings

_Detector = Callable[[], ToolCheckResult]


def _safe_detect(key: str, name: str, detector: _Detector) -> ToolCheckResult:
    try:
        return detector()
    except Exception as exc:  # noqa: BLE001 - keep the detection page resilient.
        return ToolCheckResult(
            key=key,
            name=name,
            status="error",
            message=f"{name} 检测过程发生未处理错误。",
            raw_error=str(exc),
        )


def check_all_tools() -> list[ToolCheckResult]:
    settings = load_settings()
    configured_python = settings.tool_paths.python.strip()
    python_result = _safe_detect(
        "python",
        "Python",
        lambda: python_adapter.detect(configured_python, prefer_configured=bool(configured_python)),
    )
    selected_python = python_result.path or configured_python
    python_source = python_result.source

    return [
        python_result,
        _safe_detect("vina", "AutoDock Vina", lambda: vina_adapter.detect(settings.tool_paths.vina)),
        _safe_detect("meeko", "Meeko", lambda: meeko_adapter.detect(selected_python, python_source)),
        _safe_detect("rdkit", "RDKit", lambda: rdkit_adapter.detect(selected_python, python_source)),
        _safe_detect("viewer_3dmol", "3Dmol.js", viewer_adapter.detect),
    ]


def check_all_tools_json() -> str:
    return json.dumps([result.to_dict() for result in check_all_tools()], ensure_ascii=False)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(check_all_tools_json())


if __name__ == "__main__":
    main()
