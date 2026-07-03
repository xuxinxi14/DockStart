from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.demo_projects import (  # noqa: E402
    DEMO_DISCLAIMER,
    create_demo_project,
    list_available_demo_projects,
    validate_demo_project,
)
from dockstart_core.project import load_project  # noqa: E402


class DemoProjectTests(unittest.TestCase):
    def test_list_available_demo_projects_returns_templates(self) -> None:
        response = list_available_demo_projects()

        self.assertTrue(response["ok"])
        demo_types = {item["demo_type"] for item in response["demos"]}
        self.assertIn("basic_pdbqt", demo_types)
        self.assertIn("assisted_raw", demo_types)
        self.assertIn("viewer_result", demo_types)
        for demo in response["demos"]:
            self.assertIn("只用于学习 DockStart 操作流程", demo["disclaimer"])
            self.assertIn("entry_step", demo)
            self.assertIn("button_label", demo)
            self.assertIsInstance(demo["tags"], list)

    def test_create_basic_demo_project_copies_files_and_updates_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = create_demo_project(temp_dir, "basic_pdbqt")
            project_dir = Path(response["project_dir"])

            self.assertTrue(response["ok"])
            self.assertEqual(project_dir.name, "basic_demo_001")
            self.assertTrue((project_dir / "project.json").is_file())
            self.assertTrue((project_dir / "receptor.pdbqt").is_file())
            self.assertTrue((project_dir / "ligand.pdbqt").is_file())
            self.assertEqual(response["entry_page"], "import-pdbqt")
            self.assertIn(DEMO_DISCLAIMER, response["disclaimer"])

            loaded = load_project(str(project_dir))

        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["project"]["project_dir"], str(project_dir))
        self.assertEqual(loaded["project"]["receptor"]["file"], "receptor.pdbqt")
        self.assertEqual(loaded["project"]["ligand"]["file"], "ligand.pdbqt")

    def test_create_assisted_demo_project_copies_raw_and_reference_pdbqt_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = create_demo_project(temp_dir, "assisted_raw")
            project_dir = Path(response["project_dir"])

            self.assertTrue(response["ok"])
            self.assertEqual(response["entry_page"], "preparation")
            self.assertTrue((project_dir / "raw" / "receptor.pdb").is_file())
            self.assertTrue((project_dir / "raw" / "ligand.sdf").is_file())
            self.assertTrue((project_dir / "prepared" / "receptor.pdbqt").is_file())
            self.assertTrue((project_dir / "prepared" / "ligand.pdbqt").is_file())
            loaded = load_project(str(project_dir))

        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["project"]["receptor"]["raw_file"], "raw/receptor.pdb")
        self.assertEqual(loaded["project"]["ligand"]["raw_file"], "raw/ligand.sdf")
        self.assertEqual(loaded["project"]["receptor"]["file"], "prepared/receptor.pdbqt")
        self.assertEqual(loaded["project"]["ligand"]["file"], "prepared/ligand.pdbqt")

    def test_create_demo_project_uses_next_available_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = create_demo_project(temp_dir, "basic_pdbqt")
            second = create_demo_project(temp_dir, "basic_pdbqt")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(first["project_dir"].endswith("basic_demo_001"))
        self.assertTrue(second["project_dir"].endswith("basic_demo_002"))

    def test_create_viewer_result_demo_project_opens_finished_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = create_demo_project(temp_dir, "viewer_result")
            project_dir = Path(response["project_dir"])

            self.assertTrue(response["ok"])
            self.assertEqual(response["entry_page"], "result")
            self.assertEqual(response["entry_run_id"], "run_001")
            self.assertTrue((project_dir / "runs" / "run_001" / "metadata.json").is_file())
            self.assertTrue((project_dir / "runs" / "run_001" / "scores.csv").is_file())
            loaded = load_project(str(project_dir))

        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["project"]["runs"][0]["run_id"], "run_001")
        self.assertEqual(loaded["project"]["runs"][0]["status"], "finished")

    def test_validate_demo_project_reports_valid_basic_demo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_demo_project(temp_dir, "basic_pdbqt")
            response = validate_demo_project(created["project_dir"])

        self.assertTrue(response["ok"])
        self.assertEqual(response["demo_type"], "basic_pdbqt")
        self.assertTrue(all(item["exists"] for item in response["checks"]))

    def test_invalid_demo_type_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = create_demo_project(temp_dir, "unknown_demo")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "DEMO_TYPE_INVALID")


if __name__ == "__main__":
    unittest.main()
