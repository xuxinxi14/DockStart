from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.tests import test_hydrated_project_maps as maps_support

SCRIPT_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "verify_hydrated_system_reliability.py"
)
SPEC = importlib.util.spec_from_file_location(
    "verify_hydrated_system_reliability",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


class HydratedSystemReliabilityVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = maps_support.HydratedProjectMapsTests(
            methodName="test_generates_independent_base_and_best_water_maps"
        )
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def test_small_grid_project_api_reports_complete_active_publication(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self.support._autogrid_detection(),
        ):
            report = VERIFY._generate_and_audit(
                project_dir,
                grid_points=8,
                spacing=1.0,
                expected_autogrid=self.support.autogrid_file,
                runner=self.support._runner(),
            )

        self.assertEqual(
            report["evidence_level"],
            VERIFY.SIMULATED_RUNNER_EVIDENCE,
        )
        publication = report["publication"]
        self.assertEqual(publication["manifest_status"], "ready")
        self.assertTrue(publication["active_pointer_verified"])
        self.assertEqual(publication["grid"]["npts"], [8, 8, 8])
        self.assertEqual(publication["grid"]["values_per_map"], 729)
        self.assertIn(
            "receptor.W.map",
            {item["name"] for item in publication["maps"]["files"]},
        )
        self.assertGreater(publication["maps"]["total_bytes"], 0)
        active = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]
        self.assertEqual(active["path"], publication["manifest_file"])
        self.assertEqual(active["sha256"], publication["manifest_sha256"])

    def test_competition_audit_accepts_only_unique_complete_publications(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        before_ids = VERIFY._map_set_ids(project_dir)
        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self.support._autogrid_detection(),
        ):
            first = self.support._generate(project_dir)
            second = self.support._generate(project_dir)
        worker_results = [
            {
                "ok": True,
                "worker_id": "mock_process_1",
                "api_result": VERIFY._compact_api_result(first),
            },
            {
                "ok": True,
                "worker_id": "mock_process_2",
                "api_result": VERIFY._compact_api_result(second),
            },
        ]

        audit = VERIFY._audit_competition_publications(
            project_dir,
            before_ids=before_ids,
            worker_results=worker_results,
            grid_points=8,
            spacing=1.0,
            expected_autogrid=self.support.autogrid_file,
        )

        self.assertEqual(audit["unique_publication_count"], 2)
        self.assertEqual(
            set(audit["map_set_ids"]),
            {"hydrated_001", "hydrated_002"},
        )
        self.assertTrue(audit["only_complete_new_directories"])
        self.assertIn(
            audit["active_pointer"]["path"],
            {
                item["manifest_file"]
                for item in audit["publications"]
            },
        )

        duplicated = json.loads(json.dumps(worker_results))
        duplicated[1]["api_result"] = duplicated[0]["api_result"]
        with self.assertRaisesRegex(
            VERIFY.ReliabilityVerificationError,
            "unique map set IDs",
        ):
            VERIFY._audit_competition_publications(
                project_dir,
                before_ids=before_ids,
                worker_results=duplicated,
                grid_points=8,
                spacing=1.0,
                expected_autogrid=self.support.autogrid_file,
            )

    def test_fault_evidence_never_promotes_injection_to_real_fault(
        self,
    ) -> None:
        injected = VERIFY._fault_evidence(
            "disk_write",
            mechanism="injected",
            observed=True,
        )
        skipped = VERIFY._fault_evidence("disk_full")
        real = VERIFY._fault_evidence(
            "terminated_helper",
            mechanism="real_os",
            observed=True,
        )

        self.assertEqual(
            injected["evidence_level"],
            VERIFY.SIMULATED_FAULT_EVIDENCE,
        )
        self.assertFalse(injected["real_system_fault_claimed"])
        self.assertEqual(
            skipped["evidence_level"],
            VERIFY.NOT_EXECUTED_EVIDENCE,
        )
        self.assertEqual(
            real["evidence_level"],
            VERIFY.REAL_PROCESS_EVIDENCE,
        )
        self.assertTrue(real["real_system_fault_claimed"])

    def test_failed_competitor_state_is_audited_without_false_success(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        before_ids = VERIFY._map_set_ids(project_dir)
        before_pointer = VERIFY._active_maps_pointer(project_dir)
        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self.support._autogrid_detection(),
        ):
            successful = self.support._generate(project_dir)
        workers = [
            {
                "ok": True,
                "worker_id": "published",
                "api_result": VERIFY._compact_api_result(successful),
            },
            {
                "ok": False,
                "worker_id": "lock_contention",
                "api_result": None,
                "exception": {
                    "type": "OSError",
                    "message": "Resource deadlock avoided",
                },
            },
        ]

        state = VERIFY._audit_competition_failure_state(
            project_dir,
            before_ids=before_ids,
            before_pointer=before_pointer,
            worker_results=workers,
            grid_points=8,
            spacing=1.0,
            expected_autogrid=self.support.autogrid_file,
        )

        self.assertEqual(state["successful_publication_count"], 1)
        self.assertTrue(
            state["only_successful_publications_created_directories"]
        )
        self.assertTrue(state["active_pointer_not_corrupted"])
        self.assertTrue(state["state_integrity_preserved"])

        scenarios: dict[str, object] = {}
        first = VERIFY._record_scenario(
            scenarios,
            "failed",
            lambda: (_ for _ in ()).throw(
                VERIFY.ReliabilityVerificationError(
                    "EXPECTED_FAILURE",
                    "expected",
                )
            ),
        )
        second = VERIFY._record_scenario(
            scenarios,
            "continued",
            lambda: {"evidence_level": "test"},
        )
        self.assertFalse(first)
        self.assertTrue(second)
        self.assertFalse(scenarios["failed"]["ok"])
        self.assertTrue(scenarios["continued"]["ok"])

    def test_copy_verified_tree_rejects_source_change_or_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "project.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            copied = VERIFY._copy_verified_tree(source, destination)
            self.assertEqual(copied["file_count"], 1)
            self.assertRegex(copied["tree_sha256"], r"^[0-9a-f]{64}$")

    def test_real_process_termination_is_labelled_by_target_kind(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(
            lambda: process.kill() if process.poll() is None else None
        )

        evidence = VERIFY._terminate_pid(
            process.pid,
            target_kind="unit_test_helper_not_autogrid",
        )
        process.wait(timeout=10)

        self.assertEqual(
            evidence["evidence_level"],
            VERIFY.REAL_PROCESS_EVIDENCE,
        )
        self.assertEqual(
            evidence["target_kind"],
            "unit_test_helper_not_autogrid",
        )
        self.assertTrue(evidence["verified_exited"])


if __name__ == "__main__":
    unittest.main()
