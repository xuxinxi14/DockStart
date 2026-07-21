from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.screening import (  # noqa: E402
    archive_screening,
    create_screening,
    get_screening_status,
    main,
    request_screening_cancel,
    resume_screening,
    run_screening,
    stage_screening_inputs,
)


BOX = {
    "center_x": 1,
    "center_y": 2,
    "center_z": 3,
    "size_x": 20,
    "size_y": 20,
    "size_z": 20,
}
VINA = {
    "scoring": "vina",
    "exhaustiveness": 8,
    "num_modes": 9,
    "energy_range": 3,
    "cpu": 2,
    "seed": 123,
}


def _pdbqt(atom_name: str = "C") -> str:
    return (
        f"ATOM      1  {atom_name:<3} LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C\n"
        "TORSDOF 0\n"
    )


def _successful_runner(calls: list[tuple[str, int]], *, fail_first_item_once: bool = False):
    def runner(**kwargs):
        item_id = kwargs["item"]["item_id"]
        attempt = kwargs["attempt"]
        calls.append((item_id, attempt))
        if fail_first_item_once and item_id == "ligand_0001" and attempt == 1:
            kwargs["stderr_path"].write_text("temporary error", encoding="utf-8")
            return {"exit_code": 2, "error": "temporary error"}
        affinity = -8.0 - float(kwargs["item"]["order"])
        kwargs["output_path"].write_text(_pdbqt(), encoding="utf-8")
        kwargs["log_path"].write_text(
            "mode |   affinity | dist from best mode\n"
            "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
            "-----+------------+----------+----------\n"
            f"   1      {affinity:.2f}      0.000      0.000\n",
            encoding="utf-8",
        )
        return {"exit_code": 0, "pid": 101}

    return runner


class ScreeningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "prepared").mkdir()
        (self.root / "prepared" / "receptor.pdbqt").write_text(_pdbqt("N"), encoding="utf-8")
        (self.root / "prepared" / "zeta.pdbqt").write_text(_pdbqt("C"), encoding="utf-8")
        (self.root / "prepared" / "alpha.pdbqt").write_text(_pdbqt("O"), encoding="utf-8")
        self.vina = self.root / "vina.exe"
        self.vina.write_bytes(b"placeholder")
        self.project_json = self.root / "project.json"
        self.project_json.write_text('{"schema_version": 99, "sentinel": true}\n', encoding="utf-8")
        self.project_before = self.project_json.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, **overrides):
        mock_detection = bool(overrides.pop("mock_detection", True))
        arguments = {
            "project_dir": str(self.root),
            "receptor_file": "prepared/receptor.pdbqt",
            "ligand_files": ["prepared/zeta.pdbqt", "prepared/alpha.pdbqt"],
            "vina_path": str(self.vina),
            "box": BOX,
            "vina": VINA,
            "max_retries": 1,
            "top_n": 2,
        }
        arguments.update(overrides)
        if not mock_detection:
            return create_screening(**arguments)
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="configured",
            message="ok",
            raw_error="",
        )
        with patch("dockstart_core.screening.vina_adapter.detect", return_value=detection):
            return create_screening(**arguments)

    def test_create_is_deterministic_atomic_and_does_not_change_project_json(self) -> None:
        response = self.create()
        self.assertTrue(response["ok"], response)
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["source_file"] for item in state["items"]],
            ["prepared/alpha.pdbqt", "prepared/zeta.pdbqt"],
        )
        self.assertEqual(state["queue"], ["ligand_0001", "ligand_0002"])
        self.assertEqual(self.project_json.read_bytes(), self.project_before)
        self.assertFalse(any(state_path.parent.glob("*.tmp")))
        self.assertFalse(state["outputs"]["sdf"]["generated"])
        self.assertIn("原始配体拓扑", state["outputs"]["sdf"]["reason"])
        self.assertEqual(state["tools"]["vina"]["source"], "explicit")
        self.assertEqual(
            state["tools"]["vina"]["sha256"],
            hashlib.sha256(self.vina.read_bytes()).hexdigest(),
        )

    def test_stage_external_pdbqt_is_content_addressed_atomic_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as external_dir:
            source = Path(external_dir) / "candidate.pdbqt"
            source.write_text(_pdbqt("S"), encoding="utf-8")
            first = stage_screening_inputs(str(self.root), [str(source)])
            self.assertTrue(first["ok"], first)
            relative = first["staged"][0]["file"]
            self.assertTrue(relative.startswith("screening/staging/"))
            self.assertFalse(Path(relative).is_absolute())
            staged_path = self.root / relative
            self.assertEqual(staged_path.read_bytes(), source.read_bytes())
            self.assertFalse(any(staged_path.parent.glob("*.tmp")))

            second = stage_screening_inputs(str(self.root), [str(source)])
            self.assertTrue(second["ok"], second)
            self.assertEqual(second["staged"][0]["file"], relative)
            created = self.create(ligand_files=[relative])
            self.assertTrue(created["ok"], created)
            self.assertEqual(created["screening"]["items"][0]["source_file"], relative)

    def test_create_without_vina_path_uses_settings_detection_and_records_tool(self) -> None:
        detection = SimpleNamespace(
            status="ok",
            path=str(self.vina),
            version="1.2.7",
            source="configured",
            message="ok",
        )
        settings = SimpleNamespace(tool_paths=SimpleNamespace(vina=str(self.vina)))
        with (
            patch("dockstart_core.screening.load_settings", return_value=settings),
            patch("dockstart_core.screening.vina_adapter.detect", return_value=detection) as detect,
        ):
            response = self.create(vina_path=None, mock_detection=False)
        self.assertTrue(response["ok"], response)
        detect.assert_called_once_with(str(self.vina))
        tool = response["screening"]["tools"]["vina"]
        self.assertEqual(tool["version"], "1.2.7")
        self.assertEqual(tool["source"], "configured")
        self.assertEqual(tool["detection_status"], "ok")

    def test_explicit_vina_path_must_pass_adapter_detection(self) -> None:
        response = self.create(mock_detection=False)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_CREATE_ERROR")
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_retry_queue_attempt_directories_and_ranked_csv(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        response = run_screening(
            str(self.root),
            runner=_successful_runner(calls, fail_first_item_once=True),
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["screening"]["status"], "completed")
        self.assertEqual(
            calls,
            [("ligand_0001", 1), ("ligand_0002", 1), ("ligand_0001", 2)],
        )
        self.assertTrue(
            (self.root / "screening" / "attempts" / "ligand_0001" / "attempt_001" / "attempt.json").is_file(),
        )
        self.assertTrue(
            (self.root / "screening" / "attempts" / "ligand_0001" / "attempt_002" / "out.pdbqt").is_file(),
        )
        with (self.root / "screening" / "results" / "screening_summary.csv").open(
            encoding="utf-8",
            newline="",
        ) as handle:
            summary = list(csv.DictReader(handle))
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0]["attempts"], "2")
        with (self.root / "screening" / "results" / "screening_top_n.csv").open(
            encoding="utf-8",
            newline="",
        ) as handle:
            top = list(csv.DictReader(handle))
        self.assertEqual([row["rank"] for row in top], ["1", "2"])
        self.assertLessEqual(float(top[0]["best_affinity_kcal_mol"]), float(top[1]["best_affinity_kcal_mol"]))
        self.assertFalse((self.root / "screening" / "results" / "screening.sdf").exists())

    def test_cancel_after_active_ligand_then_resume_remaining_queue(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        base_runner = _successful_runner(calls)

        def canceling_runner(**kwargs):
            result = base_runner(**kwargs)
            canceled = request_screening_cancel(str(self.root))
            self.assertTrue(canceled["ok"])
            return result

        canceled = run_screening(str(self.root), runner=canceling_runner)
        self.assertTrue(canceled["ok"])
        self.assertEqual(canceled["screening"]["status"], "canceled")
        self.assertEqual(calls, [("ligand_0001", 1)])
        self.assertEqual(canceled["screening"]["queue"], ["ligand_0002"])

        resumed = resume_screening(str(self.root))
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["screening"]["status"], "ready")
        calls.clear()
        finished = run_screening(str(self.root), runner=_successful_runner(calls))
        self.assertEqual(finished["screening"]["status"], "completed")
        self.assertEqual(calls, [("ligand_0002", 1)])

    def test_archive_terminal_job_clears_active_state_and_preserves_history(self) -> None:
        self.assertTrue(self.create()["ok"])
        finished = run_screening(str(self.root), runner=_successful_runner([]))
        self.assertEqual(finished["screening"]["status"], "completed")
        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)
        archive_dir = self.root / archived["archive"]
        self.assertTrue((archive_dir / "screening.json").is_file())
        self.assertTrue((archive_dir / "results" / "screening_summary.csv").is_file())
        self.assertFalse((self.root / "screening" / "screening.json").exists())
        self.assertFalse((self.root / "screening" / "inputs").exists())

        next_job = self.create(ligand_files=["prepared/alpha.pdbqt"])
        self.assertTrue(next_job["ok"], next_job)
        self.assertEqual(next_job["screening"]["screening_id"], "screening_002")

    def test_archive_refuses_nonterminal_job_and_create_never_overwrites(self) -> None:
        self.assertTrue(self.create()["ok"])
        duplicate = self.create()
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["error"]["code"], "SCREENING_ALREADY_EXISTS")
        refused = archive_screening(str(self.root))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "SCREENING_NOT_TERMINAL")
        self.assertTrue((self.root / "screening" / "screening.json").is_file())

    def test_ready_job_can_be_canceled_immediately_then_archived(self) -> None:
        self.assertTrue(self.create()["ok"])
        canceled = request_screening_cancel(str(self.root))
        self.assertTrue(canceled["ok"], canceled)
        self.assertEqual(canceled["screening"]["status"], "canceled")
        archived = archive_screening(str(self.root))
        self.assertTrue(archived["ok"], archived)

    def test_resume_refuses_while_recorded_pid_is_alive(self) -> None:
        self.assertTrue(self.create()["ok"])
        state_path = self.root / "screening" / "screening.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "running"
        state["items"][0]["status"] = "running"
        state["items"][0]["attempts"] = [{"status": "running", "pid": 4242}]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with patch("dockstart_core.screening.vina_adapter.is_process_running", return_value=True):
            response = resume_screening(str(self.root))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "SCREENING_PROCESS_ACTIVE")
        unchanged = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(unchanged["items"][0]["status"], "running")

    def test_runner_started_callback_persists_pid_before_completion(self) -> None:
        self.assertTrue(self.create(ligand_files=["prepared/alpha.pdbqt"])["ok"])

        def runner(**kwargs):
            kwargs["on_started"](5151)
            current = json.loads(
                (self.root / "screening" / "screening.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(current["items"][0]["attempts"][0]["pid"], 5151)
            kwargs["output_path"].write_text(_pdbqt(), encoding="utf-8")
            kwargs["log_path"].write_text("   1      -7.00      0.000      0.000\n", encoding="utf-8")
            return {"exit_code": 0}

        response = run_screening(str(self.root), runner=runner)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["screening"]["items"][0]["attempts"][0]["pid"], 5151)

    def test_resource_limits_are_rejected_before_state_creation(self) -> None:
        response = self.create(
            resource_limits={"max_ligands": 1},
        )
        self.assertFalse(response["ok"])
        self.assertIn("资源上限", response["error"]["raw_error"])
        self.assertFalse((self.root / "screening" / "screening.json").exists())

    def test_project_escape_is_rejected(self) -> None:
        outside = self.root.parent / "outside-screening-ligand.pdbqt"
        outside.write_text(_pdbqt(), encoding="utf-8")
        try:
            response = self.create(ligand_files=[str(outside)])
            self.assertFalse(response["ok"])
            self.assertIn("项目目录内", response["error"]["raw_error"])
        finally:
            outside.unlink(missing_ok=True)

    def test_max_retries_finishes_with_failure_and_keeps_error(self) -> None:
        self.assertTrue(self.create(max_retries=0, ligand_files=["prepared/alpha.pdbqt"])["ok"])

        def failed_runner(**_kwargs):
            return {"exit_code": 9, "error": "mock failure"}

        response = run_screening(str(self.root), runner=failed_runner)
        self.assertEqual(response["screening"]["status"], "completed_with_failures")
        self.assertEqual(response["screening"]["items"][0]["attempt_count"], 1)
        self.assertEqual(response["screening"]["items"][0]["last_error"], "mock failure")

    def test_cli_status_prints_one_json_document(self) -> None:
        self.assertTrue(self.create()["ok"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["status", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["screening"]["status"], "ready")

    def test_cli_stage_and_archive_commands_return_json(self) -> None:
        with tempfile.TemporaryDirectory() as external_dir:
            source = Path(external_dir) / "cli-input.pdbqt"
            source.write_text(_pdbqt(), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["stage", "--project", str(self.root), "--file", str(source)],
                )
            self.assertEqual(exit_code, 0)
            staged = json.loads(output.getvalue())
            self.assertTrue(staged["staged"][0]["file"].startswith("screening/staging/"))

        self.assertTrue(self.create()["ok"])
        self.assertTrue(run_screening(str(self.root), runner=_successful_runner([]))["ok"])
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["archive", "--project", str(self.root)])
        self.assertEqual(exit_code, 0)
        archived = json.loads(output.getvalue())
        self.assertTrue(archived["ok"])
        self.assertTrue((self.root / archived["archive"] / "screening.json").is_file())

    def test_interrupted_batch_requires_explicit_resume(self) -> None:
        self.assertTrue(self.create()["ok"])
        calls: list[tuple[str, int]] = []
        response = run_screening(str(self.root), runner=_successful_runner(calls), max_items=1)
        self.assertEqual(response["screening"]["status"], "interrupted")
        refused = run_screening(str(self.root), runner=_successful_runner(calls))
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"]["code"], "SCREENING_NOT_READY")
        self.assertTrue(resume_screening(str(self.root))["ok"])


if __name__ == "__main__":
    unittest.main()
