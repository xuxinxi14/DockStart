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
    RECEPTOR_PREPARATION_OUTPUT,
    get_latest_preparation,
    get_next_preparation_id,
    list_preparation_runs,
    load_preparation_metadata,
    prepare_ligand_pdbqt,
    prepare_receptor_pdbqt,
)
from dockstart_core.project import create_project  # noqa: E402


def _tool_status(target: str = "ligand") -> dict:
    receptor_cli = [sys.executable] if target == "receptor" else []
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
                "status": "ok" if target == "ligand" else "missing",
                "version": "mock-rdkit" if target == "ligand" else "",
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
                "status": "ok",
                "version": "mock-meeko",
                "path": sys.executable,
                "python_path": sys.executable,
                "python_source": "current_environment",
                "source": "current_environment",
                "capabilities": {
                    "ligand_preparation": {"status": "ok", "message": "mock ligand capability"},
                    "receptor_preparation": {
                        "status": "ok",
                        "message": "mock receptor capability",
                        "cli_candidates_found": receptor_cli,
                    },
                },
                "message": "mock",
                "raw_error": "",
            },
        },
    }


class PreparationRecordTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        created = create_project("prep_records", temp_dir)
        self.assertTrue(created["ok"], created)
        return Path(created["project_dir"])

    def _set_raw(self, project_dir: Path, target: str, relative_path: str, content: str) -> None:
        raw_path = project_dir / relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(content, encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data[target]["raw_file"] = relative_path
        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_get_next_preparation_id_increments_by_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self.assertEqual(get_next_preparation_id(str(project_dir), "ligand"), "ligand_001")
            (project_dir / "preparation" / "ligand_001").mkdir(parents=True)
            (project_dir / "preparation" / "receptor_001").mkdir(parents=True)

            self.assertEqual(get_next_preparation_id(str(project_dir), "ligand"), "ligand_002")
            self.assertEqual(get_next_preparation_id(str(project_dir), "receptor"), "receptor_002")

    def test_ligand_preparation_records_increment_and_do_not_overwrite(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            Path(command[-1]).write_text("REMARK mock ligand pdbqt\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=f"stdout for {command[-1]}", stderr="stderr text")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_raw(project_dir, "ligand", "raw/ligand.sdf", "mock sdf\n")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status("ligand")),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
                patch("dockstart_core.project.vina_adapter.detect") as vina_detect,
            ):
                first = prepare_ligand_pdbqt(str(project_dir), overwrite=False)
                second = prepare_ligand_pdbqt(str(project_dir), overwrite=True)

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            runs = list_preparation_runs(str(project_dir), "ligand")
            latest = get_latest_preparation(str(project_dir), "ligand")
            first_metadata = load_preparation_metadata(str(project_dir), "ligand", first["prep_id"])
            first_record_files = {
                "metadata": (project_dir / "preparation" / "ligand_001" / "metadata.json").is_file(),
                "second_metadata": (project_dir / "preparation" / "ligand_002" / "metadata.json").is_file(),
                "stdout": (project_dir / "preparation" / "ligand_001" / "stdout.txt").is_file(),
                "stderr": (project_dir / "preparation" / "ligand_001" / "stderr.txt").is_file(),
                "command": (project_dir / "preparation" / "ligand_001" / "command.json").is_file(),
                "input_snapshot": (project_dir / "preparation" / "ligand_001" / "input_snapshot.json").is_file(),
                "output_check": (project_dir / "preparation" / "ligand_001" / "output_check.json").is_file(),
            }

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["prep_id"], "ligand_001")
        self.assertEqual(second["prep_id"], "ligand_002")
        self.assertEqual(len(runs["runs"]), 2)
        self.assertTrue(all(first_record_files.values()), first_record_files)
        self.assertEqual(project["latest_preparation"]["ligand"], "ligand_002")
        self.assertEqual(project["preparation"]["ligand"]["prep_id"], "ligand_002")
        self.assertEqual(project["preparation"]["ligand"]["metadata_file"], "preparation/ligand_002/metadata.json")
        self.assertEqual(project["ligand"]["file"], LIGAND_PREPARATION_OUTPUT)
        self.assertEqual(latest["prep_id"], "ligand_002")
        self.assertEqual(first_metadata["metadata"]["status"], "finished")
        self.assertIsInstance(first_metadata["metadata"]["command"], list)
        vina_detect.assert_not_called()

    def test_receptor_preparation_failure_keeps_metadata_and_latest(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            return subprocess.CompletedProcess(command, 2, stdout="receptor stdout", stderr="receptor stderr")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_raw(project_dir, "receptor", "raw/receptor.pdb", "ATOM\n")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status("receptor")),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
                patch("dockstart_core.project.vina_adapter.detect") as vina_detect,
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=False)

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            metadata = load_preparation_metadata(str(project_dir), "receptor", "receptor_001")
            output_check = json.loads((project_dir / "preparation" / "receptor_001" / "output_check.json").read_text(encoding="utf-8"))
            record_files = {
                "stdout": (project_dir / "preparation" / "receptor_001" / "stdout.txt").is_file(),
                "stderr": (project_dir / "preparation" / "receptor_001" / "stderr.txt").is_file(),
                "output": (project_dir / RECEPTOR_PREPARATION_OUTPUT).exists(),
            }

        self.assertFalse(result["ok"])
        self.assertEqual(result["prep_id"], "receptor_001")
        self.assertEqual(project["latest_preparation"]["receptor"], "receptor_001")
        self.assertEqual(project["preparation"]["receptor"]["status"], "failed")
        self.assertEqual(project["preparation"]["receptor"]["exit_code"], 2)
        self.assertEqual(metadata["metadata"]["status"], "failed")
        self.assertEqual(metadata["metadata"]["exit_code"], 2)
        self.assertEqual(output_check["success"], False)
        self.assertTrue(record_files["stdout"])
        self.assertTrue(record_files["stderr"])
        self.assertFalse(record_files["output"])
        vina_detect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
