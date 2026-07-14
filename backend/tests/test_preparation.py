from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import create_project, load_project  # noqa: E402
from dockstart_core.preparation import (  # noqa: E402
    get_preparation_status,
    reset_preparation_status,
    validate_preparation_prerequisites,
)


def _mock_tools() -> dict:
    return {
        "python": {
            "key": "python",
            "name": "Python",
            "status": "ok",
            "version": "Python 3.11.0",
            "path": "python",
            "message": "mock python",
            "raw_error": "",
            "source": "current_environment",
            "bundled_path": "",
            "is_bundled": False,
        },
        "rdkit": {
            "key": "rdkit",
            "name": "RDKit",
            "status": "ok",
            "version": "mock-rdkit",
            "path": "python",
            "message": "mock rdkit",
            "raw_error": "",
            "source": "current_environment",
            "bundled_path": "",
            "is_bundled": False,
        },
        "meeko": {
            "key": "meeko",
            "name": "Meeko",
            "status": "ok",
            "version": "mock-meeko",
            "path": "python",
            "message": "mock meeko",
            "raw_error": "",
            "source": "current_environment",
            "bundled_path": "",
            "is_bundled": False,
        },
    }


class PreparationWorkflowModelTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        response = create_project("demo_project", temp_dir)
        self.assertTrue(response["ok"], response)
        return Path(response["project_dir"])

    def test_old_project_json_is_loaded_with_default_preparation_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data.pop("preparation", None)
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            loaded = load_project(str(project_dir))

        self.assertTrue(loaded["ok"], loaded)
        self.assertEqual(loaded["project"]["preparation"]["receptor"]["status"], "not_started")
        self.assertEqual(loaded["project"]["preparation"]["receptor"]["output_file"], "prepared/receptor.pdbqt")
        self.assertEqual(loaded["project"]["preparation"]["ligand"]["status"], "not_started")
        self.assertEqual(loaded["project"]["preparation"]["ligand"]["output_file"], "prepared/ligand.pdbqt")

    def test_get_preparation_status_without_preparation_field_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data.pop("preparation", None)
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            with patch("dockstart_core.preparation._tool_status", return_value=_mock_tools()):
                result = get_preparation_status(str(project_dir))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["preparation"]["receptor"]["status"], "not_started")
        self.assertEqual(result["files"]["receptor_raw"]["status"], "missing")

    def test_validate_preparation_prerequisites_missing_raw_file_returns_chinese_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch("dockstart_core.preparation._tool_status", return_value=_mock_tools()):
                result = validate_preparation_prerequisites(str(project_dir), "ligand")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_RAW_FILE_NOT_READY")
        self.assertIn("raw 文件", result["error"]["message"])

    def test_validate_preparation_prerequisites_marks_ready_when_raw_and_tools_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_file = project_dir / "raw" / "ligand_2244.sdf"
            raw_file.write_text("mock sdf\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["ligand"]["raw_file"] = "raw/ligand_2244.sdf"
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            with patch("dockstart_core.preparation._tool_status", return_value=_mock_tools()) as mocked:
                result = validate_preparation_prerequisites(str(project_dir), "ligand")
            updated = json.loads(project_json.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["ready"])
        self.assertEqual(updated["preparation"]["ligand"]["status"], "ready")
        self.assertEqual(updated["preparation"]["ligand"]["input_file"], "raw/ligand_2244.sdf")
        self.assertEqual(updated["preparation"]["ligand"]["output_file"], "prepared/ligand.pdbqt")
        mocked.assert_called_once()

    def test_get_preparation_status_reuses_supplied_tool_snapshot_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            tools = _mock_tools()

            with patch("dockstart_core.preparation._tool_status") as mocked:
                result = get_preparation_status(str(project_dir), tools_snapshot=tools)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tools"], tools)
        self.assertIsNot(result["tools"], tools)
        mocked.assert_not_called()

    def test_reset_preparation_status_does_not_delete_prepared_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            prepared = project_dir / "prepared" / "ligand.pdbqt"
            prepared.write_text("prepared ligand\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["preparation"]["ligand"]["status"] = "failed"
            data["preparation"]["ligand"]["error"] = {"message": "mock"}
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            with patch("dockstart_core.preparation._tool_status") as mocked:
                result = reset_preparation_status(str(project_dir), "ligand")
            updated = json.loads(project_json.read_text(encoding="utf-8"))
            prepared_exists = prepared.exists()

        self.assertTrue(result["ok"], result)
        self.assertTrue(prepared_exists)
        self.assertIsNone(result["tools"])
        self.assertEqual(updated["preparation"]["ligand"]["status"], "not_started")
        self.assertEqual(updated["preparation"]["ligand"]["output_file"], "prepared/ligand.pdbqt")
        mocked.assert_not_called()

    def test_preparation_status_does_not_call_real_rdkit_or_meeko_when_tool_status_is_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch("dockstart_core.preparation._tool_status", return_value=_mock_tools()) as mocked:
                result = get_preparation_status(str(project_dir))

        self.assertTrue(result["ok"], result)
        mocked.assert_called_once()


if __name__ == "__main__":
    unittest.main()
