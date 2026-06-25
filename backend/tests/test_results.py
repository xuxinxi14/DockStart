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
    analyze_vina_run_results,
    create_project,
    load_scores_csv,
    parse_vina_log_text,
)


STANDARD_VINA_LOG = """AutoDock Vina v1.2.5

mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1        -8.7      0.0      0.0
   2        -8.2      1.532    2.145
"""


class ResultParsingTests(unittest.TestCase):
    def _create_run(
        self,
        temp_dir: str,
        *,
        status: str = "finished",
        log_text: str | None = STANDARD_VINA_LOG,
    ) -> tuple[Path, str]:
        project_response = create_project("demo_project", temp_dir)
        self.assertTrue(project_response["ok"])
        project_dir = Path(project_response["project_dir"])
        run_id = "run_001"
        run_dir = project_dir / "runs" / run_id
        run_dir.mkdir(parents=True)

        if log_text is not None:
            (run_dir / "log.txt").write_text(log_text, encoding="utf-8")
        (run_dir / "out.pdbqt").write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

        metadata = {
            "run_id": run_id,
            "status": status,
            "log_file": f"runs/{run_id}/log.txt",
            "output_file": f"runs/{run_id}/out.pdbqt",
            "best_affinity": None,
        }
        (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        project_json = project_dir / "project.json"
        project = json.loads(project_json.read_text(encoding="utf-8"))
        project["runs"] = [
            {
                "run_id": run_id,
                "status": status,
                "metadata_file": f"runs/{run_id}/metadata.json",
                "best_affinity": None,
            },
        ]
        project_json.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return project_dir, run_id

    def test_parse_vina_log_text_parses_standard_table(self) -> None:
        scores = parse_vina_log_text(STANDARD_VINA_LOG)

        self.assertIsInstance(scores, list)
        self.assertEqual(scores[0]["mode"], 1)
        self.assertEqual(scores[0]["affinity_kcal_mol"], -8.7)
        self.assertEqual(scores[1]["rmsd_lb"], 1.532)
        self.assertEqual(scores[1]["rmsd_ub"], 2.145)

    def test_parse_vina_log_text_parses_negative_affinity(self) -> None:
        scores = parse_vina_log_text(STANDARD_VINA_LOG)

        self.assertIsInstance(scores, list)
        self.assertLess(scores[0]["affinity_kcal_mol"], 0)

    def test_parse_vina_log_text_parses_integer_and_decimal_rmsd(self) -> None:
        log_text = """mode | affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
1 -8 0 2.5
2 -7.5 1 3
"""

        scores = parse_vina_log_text(log_text)

        self.assertIsInstance(scores, list)
        self.assertEqual(scores[0]["affinity_kcal_mol"], -8.0)
        self.assertEqual(scores[0]["rmsd_lb"], 0.0)
        self.assertEqual(scores[0]["rmsd_ub"], 2.5)
        self.assertEqual(scores[1]["rmsd_ub"], 3.0)

    def test_parse_vina_log_text_empty_log_returns_structured_error(self) -> None:
        response = parse_vina_log_text("")

        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "VINA_LOG_EMPTY")

    def test_parse_vina_log_text_missing_table_returns_structured_error(self) -> None:
        response = parse_vina_log_text("Vina finished without score table\n")

        self.assertIsInstance(response, dict)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "VINA_RESULT_TABLE_NOT_FOUND")

    def test_analyze_missing_metadata_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = analyze_vina_run_results(project_response["project_dir"], "run_001")

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_METADATA_NOT_FOUND")

    def test_analyze_rejects_non_finished_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir, status="prepared")

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_STATUS_NOT_FINISHED")

    def test_analyze_missing_log_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir, log_text=None)

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_LOG_NOT_FOUND")

    def test_analyze_writes_run_scores_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue((project_dir / "runs" / run_id / "scores.csv").is_file())

    def test_analyze_writes_project_scores_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue((project_dir / "results" / "scores.csv").is_file())

    def test_analyze_updates_metadata_best_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            response = analyze_vina_run_results(str(project_dir), run_id)
            metadata = json.loads((project_dir / "runs" / run_id / "metadata.json").read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertEqual(metadata["best_affinity"], -8.7)
            self.assertEqual(metadata["scores_file"], f"runs/{run_id}/scores.csv")

    def test_analyze_syncs_project_run_best_affinity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            response = analyze_vina_run_results(str(project_dir), run_id)
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertEqual(project["runs"][0]["best_affinity"], -8.7)
            self.assertEqual(project["runs"][0]["scores_file"], f"runs/{run_id}/scores.csv")

    def test_load_scores_csv_reads_exported_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)
            analyze_vina_run_results(str(project_dir), run_id)

            response = load_scores_csv(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(response["scores"][0]["mode"], 1)
            self.assertEqual(response["scores"][0]["affinity_kcal_mol"], -8.7)

    def test_analyze_does_not_modify_out_pdbqt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)
            out_path = project_dir / "runs" / run_id / "out.pdbqt"
            before = out_path.read_text(encoding="utf-8")

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(out_path.read_text(encoding="utf-8"), before)

    def test_analyze_does_not_call_autodock_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            with unittest.mock.patch("dockstart_core.project.subprocess.run") as run_mock:
                response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            run_mock.assert_not_called()

    def test_analyze_does_not_generate_markdown_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_run(temp_dir)

            response = analyze_vina_run_results(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertFalse((project_dir / "reports" / "docking_report.md").exists())
            self.assertEqual(list((project_dir / "reports").glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
