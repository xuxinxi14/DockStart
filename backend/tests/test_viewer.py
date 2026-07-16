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
from dockstart_core.project import create_project, get_box_params  # noqa: E402


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

    def test_meeko_ligand_is_not_truncated_at_endroot_in_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            ligand_path = project_dir / "prepared" / "ligand.pdbqt"
            pdbqt = (
                "ROOT\n"
                "ATOM      1  N   UNL     1       3.732   1.440   0.000  1.00  0.00    -0.384 N \n"
                "ATOM      2  N   UNL     1       2.000   1.440   0.000  1.00  0.00    -0.284 NA\n"
                "ATOM      3  C   UNL     1       2.866   0.940   0.000  1.00  0.00     0.122 C \n"
                "ENDROOT\n"
                "BRANCH   3   4\n"
                "ATOM      4  C   UNL     1       2.866  -0.060   0.000  1.00  0.00     0.016 A \n"
                "ATOM      5  C   UNL     1       2.000  -0.560   0.000  1.00  0.00     0.012 A \n"
                "ATOM      6  C   UNL     1       3.732  -0.560   0.000  1.00  0.00     0.012 A \n"
                "ENDBRANCH   3   4\n"
                "TORSDOF 1\n"
            )
            ligand_path.write_text(pdbqt, encoding="utf-8")
            data = self._read_project_json(project_dir)
            data["ligand"]["file"] = "prepared/ligand.pdbqt"
            self._write_project_json(project_dir, data)

            response = viewer.load_structure_for_viewer(str(project_dir), "ligand_prepared")

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertEqual(
                len([line for line in response["content"].splitlines() if line.startswith("ATOM")]),
                6,
            )
            self.assertNotIn("ENDROOT", response["content"])
            self.assertNotIn("ENDBRANCH", response["content"])
            self.assertEqual(ligand_path.read_text(encoding="utf-8"), pdbqt)
            self.assertTrue(response["warnings"])

    def test_docking_pose_removes_pdbqt_branch_terminators_for_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\n"
                "ROOT\n"
                "ATOM      1  C   UNL     1       0.000   0.000   0.000  1.00  0.00     0.000 C \n"
                "ENDROOT\n"
                "BRANCH   1   2\n"
                "ATOM      2  C   UNL     1       1.400   0.000   0.000  1.00  0.00     0.000 C \n"
                "ENDBRANCH   1   2\n"
                "TORSDOF 1\n"
                "ENDMDL\n",
                encoding="utf-8",
            )

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 1)

            self.assertTrue(response["ok"])
            self.assertEqual(response["format"], "pdb")
            self.assertEqual(
                len([line for line in response["content"].splitlines() if line.startswith("ATOM")]),
                2,
            )
            self.assertNotIn("ENDROOT", response["content"])
            self.assertNotIn("ENDBRANCH", response["content"])

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

    def test_docking_pose_scores_are_matched_by_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text(
                "MODEL 1\nREMARK pose 1\nENDMDL\nMODEL 2\nREMARK pose 2\nENDMDL\n",
                encoding="utf-8",
            )
            (run_dir / "scores.csv").write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n1,-7.1,0,0\n2,-6.4,1.2,2.3\n",
                encoding="utf-8",
            )

            poses = viewer.list_docking_poses(str(project_dir), "run_001")
            pose = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 2)

            self.assertTrue(poses["ok"])
            self.assertEqual(poses["poses"][0]["affinity_kcal_mol"], -7.1)
            self.assertEqual(poses["poses"][1]["rmsd_ub"], 2.3)
            self.assertEqual(pose["score"]["affinity_kcal_mol"], -6.4)

    def test_missing_scores_csv_keeps_pose_visible_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("MODEL 1\nREMARK pose 1\nENDMDL\n", encoding="utf-8")

            poses = viewer.list_docking_poses(str(project_dir), "run_001")

            self.assertTrue(poses["ok"])
            self.assertEqual(len(poses["poses"]), 1)
            self.assertTrue(poses["warnings"])

    def test_docking_output_without_model_is_single_pose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("REMARK single pose\nATOM\n", encoding="utf-8")

            poses = viewer.list_docking_poses(str(project_dir), "run_001")

            self.assertTrue(poses["ok"])
            self.assertEqual(len(poses["poses"]), 1)
            self.assertEqual(poses["poses"][0]["mode"], 1)

    def test_missing_pose_mode_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("MODEL 1\nREMARK pose 1\nENDMDL\n", encoding="utf-8")

            response = viewer.load_docking_pose_for_viewer(str(project_dir), "run_001", 9)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VIEWER_POSE_MODE_NOT_FOUND")

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

    def test_default_box_visualization_payload_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.get_box_visualization(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["visualization"]["unit"], "angstrom")
            self.assertEqual(response["visualization"]["center_x"], 0.0)
            self.assertEqual(response["visualization"]["size_x"], 20.0)
            self.assertEqual(len(response["visualization"]["corners"]), 8)
            self.assertEqual(response["visualization"]["viewer_box_payload"]["dimensions"]["w"], 20.0)

    def test_invalid_box_size_is_rejected_for_visualization_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 0,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_SIZE_NOT_POSITIVE")

    def test_update_box_from_visualization_updates_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": -1.5,
                    "center_y": 2,
                    "center_z": 3,
                    "size_x": 16,
                    "size_y": 18,
                    "size_z": 22,
                },
            )
            box_response = get_box_params(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["visualization"]["center_x"], -1.5)
            self.assertEqual(box_response["box"]["center_x"], -1.5)
            self.assertEqual(box_response["box"]["size_z"], 22)

    def test_large_box_visualization_returns_warning_but_allows_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            response = viewer.update_box_from_visualization(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 61,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])

    def test_box_visualization_does_not_call_external_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)

            with patch.object(subprocess, "run") as run_mock:
                response = viewer.get_box_visualization(str(project_dir))

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
