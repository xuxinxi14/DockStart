from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import (  # noqa: E402
    build_markdown_report,
    create_project,
    export_markdown_report,
    get_report_status,
)


class MarkdownReportTests(unittest.TestCase):
    def _create_report_ready_run(
        self,
        temp_dir: str,
        *,
        status: str = "finished",
        write_scores: bool = True,
    ) -> tuple[Path, str]:
        project_response = create_project("demo_project", temp_dir)
        self.assertTrue(project_response["ok"])
        project_dir = Path(project_response["project_dir"])
        run_id = "run_001"
        run_dir = project_dir / "runs" / run_id
        run_dir.mkdir(parents=True)

        (project_dir / "prepared" / "receptor.pdbqt").write_text("REMARK receptor\n", encoding="utf-8")
        (project_dir / "prepared" / "ligand.pdbqt").write_text("REMARK ligand\n", encoding="utf-8")
        (project_dir / "configs" / "vina_config.txt").write_text(
            "\n".join(
                [
                    "receptor = prepared/receptor.pdbqt",
                    "ligand = prepared/ligand.pdbqt",
                    "center_x = 1",
                    "center_y = 2",
                    "center_z = 3",
                    "size_x = 20",
                    "size_y = 21",
                    "size_z = 22",
                    "exhaustiveness = 8",
                    "num_modes = 9",
                    "energy_range = 4",
                    "cpu = 0",
                ],
            )
            + "\n",
            encoding="utf-8",
        )

        (run_dir / "log.txt").write_text("mock vina log\n", encoding="utf-8")
        (run_dir / "out.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        (run_dir / "stdout.txt").write_text("stdout\n", encoding="utf-8")
        (run_dir / "stderr.txt").write_text("", encoding="utf-8")
        if write_scores:
            (run_dir / "scores.csv").write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "1,-8.7,0.0,0.0\n"
                "2,-8.2,1.532,2.145\n",
                encoding="utf-8",
            )

        metadata = {
            "run_id": run_id,
            "status": status,
            "started_at": "2026-06-25T00:00:00+00:00",
            "finished_at": "2026-06-25T00:01:00+00:00",
            "vina_version": "1.2.5",
            "command": [
                "vina",
                "--config",
                "configs/vina_config.txt",
                "--out",
                f"runs/{run_id}/out.pdbqt",
            ],
            "config_file": "configs/vina_config.txt",
            "output_file": f"runs/{run_id}/out.pdbqt",
            "log_file": f"runs/{run_id}/log.txt",
            "stdout_file": f"runs/{run_id}/stdout.txt",
            "stderr_file": f"runs/{run_id}/stderr.txt",
            "exit_code": 0,
            "best_affinity": -8.7,
            "scores_file": f"runs/{run_id}/scores.csv",
            "project_scores_file": "results/scores.csv",
            "analyzed_at": "2026-06-25T00:02:00+00:00",
        }
        (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        project_json = project_dir / "project.json"
        project = json.loads(project_json.read_text(encoding="utf-8"))
        project["receptor"] = {"source": "local", "file": "prepared/receptor.pdbqt"}
        project["ligand"] = {"source": "local", "file": "prepared/ligand.pdbqt"}
        project["box"] = {
            "center_x": 1,
            "center_y": 2,
            "center_z": 3,
            "size_x": 20,
            "size_y": 21,
            "size_z": 22,
        }
        project["vina"] = {
            "exhaustiveness": 8,
            "num_modes": 9,
            "energy_range": 4,
            "cpu": 0,
            "seed": 12345,
        }
        project["config"] = {
            "vina_config_file": "configs/vina_config.txt",
            "generated_at": "2026-06-25T00:00:00+00:00",
        }
        project["runs"] = [
            {
                "run_id": run_id,
                "status": status,
                "metadata_file": f"runs/{run_id}/metadata.json",
                "best_affinity": -8.7,
                "scores_file": f"runs/{run_id}/scores.csv",
                "analyzed_at": "2026-06-25T00:02:00+00:00",
            },
        ]
        project_json.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return project_dir, run_id

    def test_export_missing_metadata_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = export_markdown_report(project_response["project_dir"], "run_001")

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_METADATA_NOT_FOUND")

    def test_export_rejects_non_finished_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir, status="prepared")

            response = export_markdown_report(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_STATUS_NOT_FINISHED")

    def test_export_missing_scores_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir, write_scores=False)

            response = export_markdown_report(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "SCORES_CSV_NOT_FOUND")

    def test_build_markdown_report_from_mock_project_and_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertIn("# DockStart Docking Report", response["report_text"])

    def test_report_contains_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("demo_project", report)

    def test_report_contains_receptor_and_ligand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("prepared/receptor.pdbqt", report)
            self.assertIn("prepared/ligand.pdbqt", report)

    def test_report_contains_box_params(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("| center_x | 1 | Å |", report)
            self.assertIn("| size_z | 22 | Å |", report)

    def test_report_contains_vina_params(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("| exhaustiveness | 8 |", report)
            self.assertIn("| seed | 12345 |", report)

    def test_report_contains_command_array(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("```json", report)
            self.assertIn('"--config"', report)
            self.assertIn('"configs/vina_config.txt"', report)

    def test_report_contains_scores_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("| Mode | Affinity kcal/mol | RMSD l.b. | RMSD u.b. |", report)
            self.assertIn("| 1 | -8.7 | 0 | 0 |", report)
            self.assertIn("| 2 | -8.2 | 1.532 | 2.145 |", report)

    def test_report_contains_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            self.assertIn("Docking score 仅供结构结合趋势参考，不能替代实验验证。", report)

    def test_export_writes_project_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = export_markdown_report(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue((project_dir / "reports" / "docking_report.md").is_file())

    def test_export_writes_run_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = export_markdown_report(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue((project_dir / "runs" / run_id / "docking_report.md").is_file())

    def test_export_updates_metadata_report_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = export_markdown_report(str(project_dir), run_id)
            metadata = json.loads((project_dir / "runs" / run_id / "metadata.json").read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertEqual(metadata["report_file"], f"runs/{run_id}/docking_report.md")
            self.assertEqual(metadata["project_report_file"], "reports/docking_report.md")
            self.assertTrue(metadata["reported_at"])

    def test_export_syncs_project_runs_report_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = export_markdown_report(str(project_dir), run_id)
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertEqual(project["runs"][0]["report_file"], f"runs/{run_id}/docking_report.md")
            self.assertEqual(project["runs"][0]["project_report_file"], "reports/docking_report.md")
            self.assertTrue(project["runs"][0]["reported_at"])

    def test_export_does_not_call_autodock_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            with unittest.mock.patch("dockstart_core.project.subprocess.run") as run_mock:
                response = export_markdown_report(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()

    def test_export_does_not_generate_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = export_markdown_report(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(list((project_dir / "reports").glob("*.pdf")), [])
            self.assertEqual(list((project_dir / "runs" / run_id).glob("*.pdf")), [])

    def test_report_does_not_make_drug_efficacy_judgment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            report = build_markdown_report(str(project_dir), run_id)["report_text"]

            for forbidden in ("药效好", "药效不好", "可以治疗", "候选药物", "一定有效"):
                self.assertNotIn(forbidden, report)

    def test_get_report_status_reports_missing_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_report_ready_run(temp_dir)

            response = get_report_status(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue(response["can_export"])
            self.assertEqual(response["report_status"], "missing")


if __name__ == "__main__":
    unittest.main()
