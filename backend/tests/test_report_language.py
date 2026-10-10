from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_report as fixtures
import test_vina_evaluation_modes as evaluation_fixtures
from dockstart_core.hydrated_run import build_hydrated_markdown_report
from dockstart_core.multiple_ligands import _build_report
from dockstart_core.project import analyze_vina_run_results, build_markdown_report, export_markdown_report, get_report_status
from dockstart_core.report_language import get_report_language, report_locale, rt
from dockstart_core.screening import _screening_report_text


class ReportLanguageTests(unittest.TestCase):
    def ready_run(self, directory):
        return fixtures.MarkdownReportTests()._create_report_ready_run(directory)

    def test_english_report_has_all_sections_and_scientific_disclaimer(self):
        with tempfile.TemporaryDirectory() as directory:
            project, run = self.ready_run(directory)
            report = build_markdown_report(str(project), run, report_language="en-US")
            self.assertTrue(report["ok"])
            self.assertEqual(report["report_language"], "en-US")
            self.assertIn("## 8. Score Statistics", report["report_text"])
            self.assertIn("## 11. Structure Review Summary", report["report_text"])
            self.assertIn("cannot replace experimental validation", report["report_text"])
            self.assertNotRegex(report["report_text"], r"[\u3400-\u9fff]")
            self.assertEqual(get_report_language(), "zh-CN")

    def test_user_names_paths_commands_and_scores_are_not_translated(self):
        with tempfile.TemporaryDirectory() as directory:
            project, run = self.ready_run(directory)
            path = project / "project.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["project_name"] = "配体 {0} 项目  中文"
            ligand = "prepared/配体  项目.pdbqt"
            (project / "prepared/ligand.pdbqt").rename(project / ligand)
            data["ligand"]["file"] = ligand
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            metadata_path = project / "runs" / run / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["command"].extend(["--literal", "配体  项目 {1}"])
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            before = {item: (project / item).read_bytes() for item in [ligand, f"runs/{run}/scores.csv", f"runs/{run}/log.txt"]}
            report = build_markdown_report(str(project), run, report_language="en-US")["report_text"]
            self.assertIn("- Project name: 配体 {0} 项目  中文", report)
            self.assertIn(ligand, report)
            command = re.search(r"```json\n(.*?)\n```", report, re.DOTALL)[1]
            self.assertEqual(json.loads(command), metadata["command"])
            self.assertIn("| 1 | -8.7 | 0 | 0 |", report)
            self.assertEqual(before, {item: (project / item).read_bytes() for item in before})

    def test_evaluation_reports_are_english_without_fabricating_ranked_poses(self):
        for mode in ["score_only", "local_only"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                project, run = evaluation_fixtures.VinaEvaluationModeTests()._create_finished_evaluation_run(directory, run_mode=mode)
                analyzed = analyze_vina_run_results(str(project), run)
                self.assertTrue(analyzed["ok"], analyzed)
                result = build_markdown_report(str(project), run, report_language="en-US")
                self.assertTrue(result["ok"], result)
                self.assertNotRegex(result["report_text"], r"[\u3400-\u9fff]")
                self.assertNotIn("## 7. Docking Score Results", result["report_text"])
                self.assertIn("cannot replace experimental validation", result["report_text"])

    def test_regeneration_records_language_and_current_report_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            project, run = self.ready_run(directory)
            last_report = None
            for language in ["en-US", "zh-CN", "en-US"]:
                result = export_markdown_report(str(project), run, report_language=language)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["metadata"]["report_language"], language)
                content = (project / result["report_file"]).read_bytes()
                self.assertEqual(content, (project / result["project_report_file"]).read_bytes())
                self.assertEqual(result["metadata"]["artifacts"]["report"]["sha256"], hashlib.sha256(content).hexdigest())
                if last_report is not None:
                    self.assertEqual(result["metadata"]["report_history"][-1]["artifacts"]["report"]["sha256"], last_report)
                last_report = hashlib.sha256(content).hexdigest()
                self.assertEqual(get_report_status(str(project), run)["metadata"]["report_language"], language)
                self.assertIn("Score Statistics" if language == "en-US" else "评分统计摘要", content.decode("utf-8"))

    def test_invalid_language_never_writes_or_mutates_a_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project, run = self.ready_run(directory)
            before = {item.relative_to(project): item.read_bytes() for item in project.rglob("*") if item.is_file()}
            result = export_markdown_report(str(project), run, report_language="../unknown")
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "REPORT_LANGUAGE_INVALID")
            self.assertEqual(before, {item.relative_to(project): item.read_bytes() for item in project.rglob("*") if item.is_file()})

    def test_other_report_protocols_are_localized_without_translating_members(self):
        with report_locale("en-US"):
            multi = _build_report({}, {})
            batch = _screening_report_text({"items": []})
            self.assertIn("Simultaneous Multi-ligand", multi)
            self.assertIn("joint pose of both ligands", multi)
            for text in [multi, batch]:
                self.assertIn("cannot replace experimental validation", text)
                self.assertNotRegex(text, r"[\u3400-\u9fff]")
        with patch("dockstart_core.hydrated_run.load_hydrated_results", return_value={"ok": True, "metadata": {}, "modes": []}):
            hydrated = build_hydrated_markdown_report(".", "run_001", report_language="en-US")
        self.assertTrue(hydrated["ok"])
        self.assertNotRegex(hydrated["report_text"], r"[\u3400-\u9fff]")
        self.assertIn("Experimental Hydrated AD4", hydrated["report_text"])

    def test_template_values_are_inserted_once_and_locale_is_scoped(self):
        with report_locale("en-US"):
            self.assertEqual(rt("- 项目名称: {0}", "配体 {0}  中文"), "- Project name: 配体 {0}  中文")
            self.assertEqual(rt("- 项目名称: 配体  中文"), "- Project name: 配体  中文")
            self.assertNotRegex(rt("当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查水分子。"), r"[\u3400-\u9fff]")
            self.assertNotRegex(rt("未检测到金属离子记录。"), r"[\u3400-\u9fff]")
            with report_locale("zh-CN"):
                self.assertEqual(rt("评分统计摘要"), "评分统计摘要")
        self.assertEqual(get_report_language(), "zh-CN")


if __name__ == "__main__":
    unittest.main()
