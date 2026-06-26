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

from dockstart_core.preparation import prepare_ligand_pdbqt, prepare_receptor_pdbqt  # noqa: E402
from dockstart_core.project import create_project, generate_vina_config, get_project_workflow_status  # noqa: E402


def _tool_status() -> dict:
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
                "status": "ok",
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
                        "cli_candidates_found": [sys.executable],
                    },
                },
                "message": "mock",
                "raw_error": "",
            },
        },
    }


class PreparationSmokeTests(unittest.TestCase):
    def test_mock_raw_to_prepared_to_config_workflow(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            if "-o" in command:
                output_stem = Path(command[command.index("-o") + 1])
                output_stem.with_suffix(".pdbqt").write_text("REMARK mock receptor\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="receptor prepared", stderr="")
            Path(command[-1]).write_text("REMARK mock ligand\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ligand prepared", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("prep_smoke", temp_dir)
            self.assertTrue(created["ok"], created)
            project_dir = Path(created["project_dir"])
            (project_dir / "raw" / "receptor.pdb").write_text("ATOM\n", encoding="utf-8")
            (project_dir / "raw" / "ligand.sdf").write_text("mock sdf\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor.pdb"
            project["ligand"]["raw_file"] = "raw/ligand.sdf"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                receptor = prepare_receptor_pdbqt(str(project_dir))
                ligand = prepare_ligand_pdbqt(str(project_dir))

            config = generate_vina_config(str(project_dir))
            workflow = get_project_workflow_status(str(project_dir))
            updated = json.loads(project_json.read_text(encoding="utf-8"))

        self.assertTrue(receptor["ok"], receptor)
        self.assertTrue(ligand["ok"], ligand)
        self.assertTrue(config["ok"], config)
        self.assertEqual(updated["receptor"]["file"], "prepared/receptor.pdbqt")
        self.assertEqual(updated["ligand"]["file"], "prepared/ligand.pdbqt")
        self.assertEqual(updated["latest_preparation"]["receptor"], "receptor_001")
        self.assertEqual(updated["latest_preparation"]["ligand"], "ligand_001")
        self.assertEqual(workflow["prepared"]["receptor"]["status"], "ok")
        self.assertEqual(workflow["prepared"]["ligand"]["status"], "ok")
        self.assertEqual(workflow["config"]["status"], "ok")
        self.assertIn("可以准备并运行 Vina", workflow["next_recommended_action"])


if __name__ == "__main__":
    unittest.main()
