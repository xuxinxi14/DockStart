from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402
from dockstart_core.result_export import _allocate_export_dir, export_result_sdf, get_result_export_status  # noqa: E402


PDBQT_WITH_TOPOLOGY = (
    "REMARK SMILES CCO\n"
    "REMARK SMILES IDX 1 1 2 2 3 3\n"
    "MODEL 1\n"
    "ATOM      1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
    "ENDMDL\n"
)


class ResultExportTests(unittest.TestCase):
    def _run(self, root: str, text: str = PDBQT_WITH_TOPOLOGY) -> Path:
        created = create_project("case", root)
        project = Path(created["project_dir"])
        run_dir = project / "runs" / "run_001"
        run_dir.mkdir()
        (run_dir / "out.pdbqt").write_text(text, encoding="utf-8")
        (run_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "run_id": "run_001",
                    "status": "finished",
                    "output_file": "runs/run_001/out.pdbqt",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return project

    def test_status_reports_embedded_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self._run(temp_dir)
            result = get_result_export_status(str(project), "run_001")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["ready"])
        self.assertTrue(result["inspection"]["embedded_topology"])

    def test_export_publishes_sdf_and_appends_run_record(self) -> None:
        def runner(argv: list[str], **_: object) -> SimpleNamespace:
            output = Path(argv[argv.index("--write_sdf") + 1])
            output.write_text("mock sdf\n$$$$\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="exported", stderr="")

        tool = ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            path=sys.executable,
            source="current_environment",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self._run(temp_dir)
            with patch("dockstart_core.result_export.get_resolved_python", return_value=tool):
                result = export_result_sdf(str(project), "run_001", runner=runner)
            metadata = json.loads((project / "runs" / "run_001" / "metadata.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(metadata["result_exports"]), 1)
        self.assertEqual(metadata["result_exports"][0]["output_file"], result["export"]["output_file"])

    def test_missing_topology_blocks_before_runner(self) -> None:
        runner = Mock()
        tool = ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            path=sys.executable,
            source="current_environment",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self._run(temp_dir, "MODEL 1\nENDMDL\n")
            with patch("dockstart_core.result_export.get_resolved_python", return_value=tool):
                result = export_result_sdf(str(project), "run_001", runner=runner)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MISSING_ORIGINAL_TOPOLOGY")
        runner.assert_not_called()

    def test_flexible_run_blocks_topology_export(self) -> None:
        runner = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self._run(temp_dir)
            metadata_file = project / "runs" / "run_001" / "metadata.json"
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            metadata["docking_protocol"] = {"mode": "flexible"}
            metadata_file.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            status = get_result_export_status(str(project), "run_001")
            result = export_result_sdf(str(project), "run_001", runner=runner)

        self.assertFalse(status["ok"])
        self.assertEqual(status["error"]["code"], "FLEXIBLE_RESULT_SDF_UNSUPPORTED")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "FLEXIBLE_RESULT_SDF_UNSUPPORTED")
        runner.assert_not_called()

    def test_export_directory_allocation_skips_existing_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self._run(temp_dir)
            first_id, first_dir = _allocate_export_dir(project, "run_001")
            second_id, second_dir = _allocate_export_dir(project, "run_001")

        self.assertEqual(first_id, "sdf_001")
        self.assertEqual(second_id, "sdf_002")
        self.assertNotEqual(first_dir, second_dir)


if __name__ == "__main__":
    unittest.main()
