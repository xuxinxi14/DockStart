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

from dockstart_core import viewer  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402


class ViewerTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        response = create_project("viewer_project", temp_dir)
        self.assertTrue(response["ok"])
        return Path(response["project_dir"])

    def _read_project_json(self, project_dir: Path) -> dict:
        return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

    def _write_project_json(self, project_dir: Path, data: dict) -> None:
        (project_dir / "project.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_missing_project_fields_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "legacy_project"
            project_dir.mkdir()
            (project_dir / "project.json").write_text(
                json.dumps(
                    {
                        "project_name": "legacy_project",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "project_dir": str(project_dir),
                    }
                ),
                encoding="utf-8",
            )

            response = viewer.get_viewer_file_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["files"]["receptor_raw"]["message"], "受体原始文件 尚未记录在 project.json 中。")

    def test_receptor_raw_file_status_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_file = project_dir / "raw" / "receptor.pdb"
            raw_file.write_text("ATOM      1  C   ALA A   1       0.000   0.000   0.000\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["raw_file"] = "raw/receptor.pdb"
            self._write_project_json(project_dir, data)

            response = viewer.get_viewer_file_status(str(project_dir))

            receptor = response["files"]["receptor_raw"]
            self.assertTrue(receptor["ok"])
            self.assertEqual(receptor["format"], "pdb")

    def test_prepared_receptor_and_ligand_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").write_text("REMARK receptor\n", encoding="utf-8")
            (project_dir / "prepared" / "ligand.pdbqt").write_text("REMARK ligand\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["file"] = "prepared/receptor.pdbqt"
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            self._write_project_json(project_dir, data)

            receptor = viewer.load_structure_for_viewer(str(project_dir), "receptor_prepared")
            ligand = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(receptor["ok"])
            self.assertIn("REMARK receptor", receptor["content"])
            self.assertTrue(ligand["ok"])
            self.assertIn("REMARK ligand", ligand["content"])

    def test_docking_output_can_be_listed_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\nREMARK pose 1\nENDMDL\nMODEL 2\nREMARK pose 2\nENDMDL\n",
                encoding="utf-8",
            )
            data = self._read_project_json(project_dir)
            data["runs"] = [{"run_id": "run_001", "status": "finished"}]
            self._write_project_json(project_dir, data)

            status = viewer.get_viewer_file_status(str(project_dir))
            poses = viewer.list_docking_poses(str(project_dir), "run_001")
            pose = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 2)

            self.assertTrue(status["files"]["docking_output"]["ok"])
            self.assertEqual([item["mode"] for item in poses["poses"]], [1, 2])
            self.assertTrue(pose["ok"])
            self.assertIn("pose 2", pose["content"])

    def test_oversized_file_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_file = project_dir / "raw" / "big.pdb"
            raw_file.write_text("0123456789abcdef", encoding="utf-8")

            original_limit = viewer.MAX_VIEWER_FILE_BYTES
            viewer.MAX_VIEWER_FILE_BYTES = 8
            try:
                response = viewer.validate_viewer_file(str(project_dir), "raw/big.pdb")
            finally:
                viewer.MAX_VIEWER_FILE_BYTES = original_limit

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_FILE_TOO_LARGE")

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            outside = Path(temp_dir) / "outside.pdb"
            outside.write_text("ATOM\n", encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["receptor"]["raw_file"] = "../outside.pdb"
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "receptor_raw")

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_FILE_OUTSIDE_PROJECT")

    def test_viewer_does_not_call_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch.object(subprocess, "run") as run_mock:
                response = viewer.get_viewer_file_status(str(project_dir))

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
