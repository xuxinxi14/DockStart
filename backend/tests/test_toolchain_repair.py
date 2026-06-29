from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import toolchain_repair  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402


def tool(key: str, status: str, path: str = "", source: str = "auto") -> dict[str, object]:
    return ToolCheckResult(
        key=key,
        name=key,
        status=status,  # type: ignore[arg-type]
        version="1.0.0" if status == "ok" else "",
        path=path,
        message="mock",
        source=source,  # type: ignore[arg-type]
    ).to_dict()


def toolchain_payload(
    vina: str = "ok",
    python: str = "ok",
    rdkit: str = "ok",
    meeko: str = "ok",
    python_path: str = "C:\\conda\\envs\\dockstart-rdkit-meeko\\python.exe",
) -> dict[str, object]:
    return {
        "active_vina": tool("vina", vina, "C:\\Tools\\Vina\\vina.exe"),
        "resolved_python": tool("python", python, python_path, "configured"),
        "rdkit_for_python": tool("rdkit", rdkit, python_path, "configured"),
        "meeko_for_python": tool("meeko", meeko, python_path, "configured"),
    }


class ToolchainRepairSuggestionTests(unittest.TestCase):
    def test_vina_missing_returns_basic_mode_repair_suggestion(self) -> None:
        with patch.object(toolchain_repair, "get_toolchain_status", return_value=toolchain_payload(vina="missing")):
            response = toolchain_repair.get_toolchain_repair_suggestions()

        self.assertTrue(response["ok"])
        suggestions = response["suggestions"]
        self.assertTrue(any(item["issue"] == "vina_missing" for item in suggestions))
        vina_suggestion = next(item for item in suggestions if item["issue"] == "vina_missing")
        self.assertIn("Basic Mode", vina_suggestion["affected_mode"])
        self.assertIn("vina --version", vina_suggestion["copyable_commands"])

    def test_missing_rdkit_meeko_returns_assisted_mode_suggestion(self) -> None:
        with patch.object(
            toolchain_repair,
            "get_toolchain_status",
            return_value=toolchain_payload(rdkit="missing", meeko="missing"),
        ):
            response = toolchain_repair.get_toolchain_repair_suggestions()

        suggestions = response["suggestions"]
        self.assertTrue(any(item["issue"] == "python_rdkit_meeko_incomplete" for item in suggestions))
        python_suggestion = next(item for item in suggestions if item["issue"] == "python_rdkit_meeko_incomplete")
        self.assertEqual(python_suggestion["affected_mode"], "Assisted Mode")
        self.assertIn("conda create", python_suggestion["copyable_commands"][0])

    def test_microsoft_store_python_gets_warning(self) -> None:
        store_python = (
            "C:\\Users\\user\\AppData\\Local\\Microsoft\\WindowsApps\\"
            "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\\python.exe"
        )
        with patch.object(
            toolchain_repair,
            "get_toolchain_status",
            return_value=toolchain_payload(python_path=store_python, rdkit="missing", meeko="missing"),
        ):
            response = toolchain_repair.get_toolchain_repair_suggestions()

        suggestions = response["suggestions"]
        self.assertTrue(any(item["issue"] == "microsoft_store_python_not_recommended" for item in suggestions))

    def test_ready_toolchain_has_no_repair_suggestions(self) -> None:
        with patch.object(toolchain_repair, "get_toolchain_status", return_value=toolchain_payload()):
            response = toolchain_repair.get_toolchain_repair_suggestions()

        self.assertTrue(response["ok"])
        self.assertEqual(response["suggestions"], [])
        self.assertIn("没有需要修复", response["message"])


if __name__ == "__main__":
    unittest.main()
