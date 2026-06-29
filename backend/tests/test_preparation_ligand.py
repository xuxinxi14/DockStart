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

from dockstart_core.preparation import (  # noqa: E402
    LIGAND_PREPARATION_OUTPUT,
    _ligand_preparation_script_text,
    load_ligand_preparation_log,
    prepare_ligand_pdbqt,
    validate_ligand_preparation_input,
)
from dockstart_core.project import create_project  # noqa: E402


def _tool_status(rdkit: str = "ok", meeko: str = "ok", ligand_capability: str = "ok") -> dict:
    return {
        "ok": True,
        "project_dir": "",
        "tools": {
            "python": {
                "key": "python",
                "name": "Python",
                "status": "ok",
                "version": "Python 3.11.0",
                "path": sys.executable,
                "message": "mock python",
                "raw_error": "",
                "source": "current_environment",
                "bundled_path": "",
                "is_bundled": False,
            },
            "rdkit": {
                "key": "rdkit",
                "name": "RDKit",
                "status": rdkit,
                "version": "mock-rdkit",
                "path": sys.executable,
                "python_path": sys.executable,
                "python_source": "current_environment",
                "source": "current_environment",
                "capabilities": {"sdf_inline_read": {"status": "ok", "message": "mock"}},
                "message": "mock",
                "raw_error": "",
            },
            "meeko": {
                "key": "meeko",
                "name": "Meeko",
                "status": meeko,
                "version": "mock-meeko",
                "path": sys.executable,
                "python_path": sys.executable,
                "python_source": "current_environment",
                "source": "current_environment",
                "capabilities": {
                    "ligand_preparation": {"status": ligand_capability, "message": "mock ligand capability"}
                },
                "message": "mock",
                "raw_error": "",
            },
        },
    }


class LigandPreparationTests(unittest.TestCase):
    def test_ligand_helper_adds_explicit_hydrogens_before_meeko(self) -> None:
        script = _ligand_preparation_script_text()

        self.assertIn("Chem.AddHs", script)
        self.assertIn("prepare_ligand_for_meeko", script)
        self.assertIn("preparator.prepare(molecule)", script)

    def _create_project(self, temp_dir: str) -> Path:
        created = create_project("ligand_prep", temp_dir)
        self.assertTrue(created["ok"], created)
        return Path(created["project_dir"])

    def _set_ligand_raw(self, project_dir: Path, relative_path: str, content: str = "mock sdf\n") -> Path:
        raw_path = project_dir / relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(content, encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["ligand"]["raw_file"] = relative_path
        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return raw_path

    def test_validate_ligand_preparation_input_missing_raw_record_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            result = validate_ligand_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_RAW_FILE_NOT_RECORDED")

    def test_validate_ligand_preparation_input_missing_raw_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["ligand"]["raw_file"] = "raw/missing.sdf"
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = validate_ligand_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_RAW_FILE_NOT_READY")

    def test_validate_ligand_preparation_input_unsupported_format_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.mol2")
            result = validate_ligand_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_RAW_FORMAT_UNSUPPORTED")

    def test_validate_ligand_preparation_input_missing_tools_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.sdf")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status(rdkit="missing")):
                result = validate_ligand_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_PREPARATION_TOOLS_NOT_READY")

    def test_existing_ligand_pdbqt_without_overwrite_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.sdf")
            output = project_dir / LIGAND_PREPARATION_OUTPUT
            output.write_text("old ligand\n", encoding="utf-8")
            result = validate_ligand_preparation_input(str(project_dir), overwrite=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "LIGAND_PREPARED_FILE_EXISTS")

    def test_existing_ligand_pdbqt_with_overwrite_allows_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.sdf")
            output = project_dir / LIGAND_PREPARATION_OUTPUT
            output.write_text("old ligand\n", encoding="utf-8")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()):
                result = validate_ligand_preparation_input(str(project_dir), overwrite=True)

        self.assertTrue(result["ok"], result)

    def test_prepare_ligand_pdbqt_mock_success_updates_project_and_logs(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = timeout
            output_path = Path(command[-1])
            output_path.write_text("REMARK mock ligand pdbqt\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="mock stdout", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.sdf")
            project_json = project_dir / "project.json"
            before = json.loads(project_json.read_text(encoding="utf-8"))
            before["receptor"]["file"] = "prepared/receptor.pdbqt"
            project_json.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), overwrite=False)
            updated = json.loads(project_json.read_text(encoding="utf-8"))
            log_result = load_ligand_preparation_log(str(project_dir))
            output_exists = (project_dir / LIGAND_PREPARATION_OUTPUT).is_file()

        self.assertTrue(result["ok"], result)
        self.assertEqual(updated["ligand"]["file"], "prepared/ligand.pdbqt")
        self.assertEqual(updated["receptor"]["file"], "prepared/receptor.pdbqt")
        self.assertEqual(updated["preparation"]["ligand"]["status"], "finished")
        self.assertEqual(updated["preparation"]["ligand"]["method"], "rdkit_meeko")
        self.assertTrue(output_exists)
        self.assertIn("mock stdout", log_result["stdout"])
        self.assertIn('"status": "finished"', log_result["log"])

    def test_prepare_ligand_pdbqt_mock_failure_writes_structured_error(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="mock failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_ligand_raw(project_dir, "raw/ligand.sdf")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), overwrite=False)
            updated = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual(updated["preparation"]["ligand"]["status"], "failed")
        self.assertEqual(updated["preparation"]["ligand"]["error"]["code"], "LIGAND_PREPARATION_FAILED")
        self.assertIsNotNone(updated["preparation"]["ligand"]["finished_at"])


if __name__ == "__main__":
    unittest.main()
