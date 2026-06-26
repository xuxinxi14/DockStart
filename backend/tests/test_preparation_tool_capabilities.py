from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.meeko_adapter import detect_meeko_capabilities  # noqa: E402
from adapters.rdkit_adapter import detect_rdkit_capabilities  # noqa: E402
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.preparation import get_preparation_tool_status  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402


def _completed(stdout: dict | str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    output = json.dumps(stdout, ensure_ascii=False) if isinstance(stdout, dict) else stdout
    return subprocess.CompletedProcess(args=["python", "-c", "probe"], returncode=returncode, stdout=output, stderr=stderr)


class PreparationToolCapabilityTests(unittest.TestCase):
    def test_rdkit_capabilities_mock_python_success(self) -> None:
        payload = {
            "import_available": True,
            "version": "2025.03.1",
            "capabilities": {
                "import": {"status": "ok", "message": "RDKit 可导入。"},
                "sdf_inline_read": {"status": "ok", "message": "RDKit 可读取内联 SDF 样本。"},
            },
        }
        with patch("adapters.rdkit_adapter.subprocess.run", return_value=_completed(payload)):
            result = detect_rdkit_capabilities(sys.executable, "current_environment")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], "2025.03.1")
        self.assertEqual(result["capabilities"]["sdf_inline_read"]["status"], "ok")

    def test_rdkit_capabilities_mock_missing_package(self) -> None:
        with patch(
            "adapters.rdkit_adapter.subprocess.run",
            return_value=_completed("", returncode=1, stderr="ModuleNotFoundError: No module named 'rdkit'"),
        ):
            result = detect_rdkit_capabilities(sys.executable, "current_environment")

        self.assertEqual(result["status"], "missing")
        self.assertIn("不会自动安装", result["message"])

    def test_meeko_capabilities_mock_python_success_with_unknown_preparation_api(self) -> None:
        payload = {
            "import_available": True,
            "version": "0.6.1",
            "capabilities": {
                "import": {"status": "ok", "message": "Meeko 可导入。"},
                "ligand_preparation": {
                    "status": "unknown",
                    "message": "Meeko 可导入，但未能确认配体准备 API 或 CLI。",
                    "api_candidates_found": [],
                    "cli_candidates_found": [],
                },
                "receptor_preparation": {
                    "status": "unknown",
                    "message": "Meeko 可导入，但未能确认受体准备 API 或 CLI。",
                    "api_candidates_found": [],
                    "cli_candidates_found": [],
                },
            },
        }
        with patch("adapters.meeko_adapter.subprocess.run", return_value=_completed(payload)):
            result = detect_meeko_capabilities(sys.executable, "current_environment")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], "0.6.1")
        self.assertEqual(result["capabilities"]["ligand_preparation"]["status"], "unknown")
        self.assertEqual(result["capabilities"]["receptor_preparation"]["status"], "unknown")

    def test_meeko_capabilities_mock_missing_package(self) -> None:
        with patch(
            "adapters.meeko_adapter.subprocess.run",
            return_value=_completed("", returncode=1, stderr="ModuleNotFoundError: No module named 'meeko'"),
        ):
            result = detect_meeko_capabilities(sys.executable, "current_environment")

        self.assertEqual(result["status"], "missing")
        self.assertIn("不会自动安装", result["message"])

    def test_missing_python_path_returns_structured_missing(self) -> None:
        result = detect_rdkit_capabilities("Z:/missing/python.exe", "configured")

        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["python_source"], "configured")
        self.assertIn("Python 路径不存在", result["message"])

    def test_get_preparation_tool_status_uses_mocked_resolved_python(self) -> None:
        python_result = ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            version="Python 3.11.0",
            path=sys.executable,
            message="mock python",
            source="current_environment",
        )
        rdkit_result = {
            "key": "rdkit",
            "name": "RDKit",
            "status": "ok",
            "version": "mock-rdkit",
            "path": sys.executable,
            "python_path": sys.executable,
            "python_source": "current_environment",
            "source": "current_environment",
            "capabilities": {"sdf_inline_read": {"status": "ok", "message": "mock"}},
            "message": "mock",
            "raw_error": "",
        }
        meeko_result = {
            "key": "meeko",
            "name": "Meeko",
            "status": "ok",
            "version": "mock-meeko",
            "path": sys.executable,
            "python_path": sys.executable,
            "python_source": "current_environment",
            "source": "current_environment",
            "capabilities": {"ligand_preparation": {"status": "unknown", "message": "mock"}},
            "message": "mock",
            "raw_error": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("prep_tools", temp_dir)
            project_dir = created["project_dir"]
            with (
                patch("dockstart_core.preparation.get_resolved_python", return_value=python_result),
                patch("adapters.rdkit_adapter.detect_rdkit_capabilities", return_value=rdkit_result),
                patch("adapters.meeko_adapter.detect_meeko_capabilities", return_value=meeko_result),
            ):
                result = get_preparation_tool_status(project_dir)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tools"]["python"]["source"], "current_environment")
        self.assertEqual(result["tools"]["rdkit"]["capabilities"]["sdf_inline_read"]["status"], "ok")
        self.assertEqual(result["tools"]["meeko"]["capabilities"]["ligand_preparation"]["status"], "unknown")

    def test_get_preparation_tool_status_missing_project_returns_structured_error(self) -> None:
        result = get_preparation_tool_status("Z:/missing/project")

        self.assertFalse(result["ok"])
        self.assertIn("message", result["error"])


if __name__ == "__main__":
    unittest.main()
