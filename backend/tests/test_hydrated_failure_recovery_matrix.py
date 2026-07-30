from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core import hydrated  # noqa: E402
from dockstart_core.hydrated_run import load_hydrated_results  # noqa: E402
from dockstart_core.project import execute_prepared_vina_run  # noqa: E402
from tests import test_hydrated_preparation as preparation_support  # noqa: E402
from tests import test_hydrated_project_integration as integration_support  # noqa: E402
from tests import test_hydrated_project_maps as maps_support  # noqa: E402


class _PreparationExitFailure:
    def __init__(
        self,
        *,
        rdkit_module: Path,
        meeko_module: Path,
    ) -> None:
        self.success = preparation_support.FakeHydratedRunner(
            rdkit_module=rdkit_module,
            meeko_module=meeko_module,
        )

    def __call__(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: int = 300,
    ) -> SimpleNamespace:
        normalized = [str(item) for item in command]
        if normalized[4] == "prepare":
            return SimpleNamespace(
                returncode=17,
                stdout='{"ok": false, "message": "simulated failure"}\n',
                stderr="simulated Meeko non-zero exit\n",
            )
        return self.success(command, cwd, timeout)


class HydratedPreparationFailureRecoveryMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = preparation_support.HydratedLigandPreparationTests(
            methodName="test_success_is_independent_and_freezes_auditable_evidence"
        )
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def test_meeko_nonzero_keeps_previous_active_ligand(self) -> None:
        first = self.support._prepare()
        self.assertTrue(first["ok"], first)
        before_state = self.support._project_data()["hydrated_docking"]

        failed = self.support._prepare(
            _PreparationExitFailure(
                rdkit_module=self.support.rdkit_module,
                meeko_module=self.support.meeko_module,
            )
        )

        self.assertFalse(failed["ok"], failed)
        self.assertEqual(
            failed["error"]["code"],
            "HYDRATED_LIGAND_PREPARATION_FAILED",
        )
        self.assertEqual(
            self.support._project_data()["hydrated_docking"],
            before_state,
        )
        failed_manifest = json.loads(
            (
                self.support.project_dir / failed["manifest_file"]
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(failed_manifest["status"], "failed")
        self.assertEqual(failed_manifest["execution"]["exit_code"], 17)
        status = hydrated.get_status(str(self.support.project_dir))
        self.assertTrue(status["preparation_ready"], status)
        self.assertEqual(
            status["active_ligand_manifest"],
            before_state["active_ligand_manifest"]["path"],
        )

    def test_disk_failure_during_artifact_publication_keeps_previous_pointer(
        self,
    ) -> None:
        first = self.support._prepare()
        self.assertTrue(first["ok"], first)
        before_state = self.support._project_data()["hydrated_docking"]
        original_atomic_write_bytes = hydrated.atomic_write_bytes

        def fail_final_hydrated_output(
            path: str | Path,
            payload: bytes,
        ) -> None:
            if Path(path).name == "ligand_hydrated.pdbqt":
                raise OSError("simulated disk full")
            original_atomic_write_bytes(path, payload)

        runner = preparation_support.FakeHydratedRunner(
            rdkit_module=self.support.rdkit_module,
            meeko_module=self.support.meeko_module,
        )
        with patch(
            "dockstart_core.hydrated.atomic_write_bytes",
            side_effect=fail_final_hydrated_output,
        ):
            failed = self.support._prepare(runner)

        self.assertFalse(failed["ok"], failed)
        self.assertEqual(
            failed["error"]["code"],
            "HYDRATED_LIGAND_PREPARATION_ERROR",
        )
        self.assertIn("simulated disk full", failed["error"]["raw_error"])
        self.assertEqual(
            self.support._project_data()["hydrated_docking"],
            before_state,
        )
        failed_manifest_path = (
            self.support.project_dir / failed["manifest_file"]
        )
        failed_manifest = json.loads(
            failed_manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(failed_manifest["status"], "failed")
        self.assertNotEqual(
            failed["manifest_file"],
            before_state["active_ligand_manifest"]["path"],
        )
        status = hydrated.get_status(str(self.support.project_dir))
        self.assertTrue(status["preparation_ready"], status)

    def test_schema_v0_with_legacy_string_pointers_migrates_fail_closed(
        self,
    ) -> None:
        project_json = self.support.project_dir / "project.json"
        payload = self.support._project_data()
        payload.pop("schema_version", None)
        legacy_state = {
            "active_ligand_manifest": (
                "protocols/hydrated/ligand_preparations/"
                "hydrated_ligand_001/manifest.json"
            ),
            "active_maps_manifest": (
                "maps/hydrated_001/hydrated_manifest.json"
            ),
            "legacy_note": "preserve-me",
        }
        payload["hydrated_docking"] = legacy_state
        project_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status = hydrated.get_status(str(self.support.project_dir))

        self.assertTrue(status["ok"], status)
        self.assertEqual(status["status"], "invalid")
        self.assertFalse(status["preparation_ready"])
        self.assertEqual(status["maps_status"], "invalid")
        migrated = json.loads(project_json.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], 1)
        self.assertEqual(migrated["hydrated_docking"], legacy_state)
        backup = self.support.project_dir / "project.json.schema-v0.bak"
        self.assertTrue(backup.is_file())
        self.assertEqual(
            json.loads(backup.read_text(encoding="utf-8"))[
                "hydrated_docking"
            ],
            legacy_state,
        )


class HydratedMapsFailureRecoveryMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = maps_support.HydratedProjectMapsTests(
            methodName="test_generates_independent_base_and_best_water_maps"
        )
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    @staticmethod
    def _nonzero_autogrid_runner(
        executable: str,
        gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        root = Path(working_directory)
        (root / log_file).write_text(
            "ERROR: simulated AutoGrid failure\n",
            encoding="utf-8",
        )
        return {
            "ok": False,
            "command": [
                executable,
                "-p",
                gpf_file,
                "-l",
                log_file,
            ],
            "exit_code": 23,
            "stdout": "",
            "stderr": "simulated AutoGrid non-zero exit\n",
            "error": "simulated AutoGrid non-zero exit",
        }

    def test_autogrid_nonzero_or_missing_output_keeps_previous_active_maps(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        first = self.support._generate(project_dir)
        self.assertTrue(first["ok"], first)
        before_pointer = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]

        cases = (
            ("nonzero", self._nonzero_autogrid_runner),
            (
                "missing_hd_map",
                self.support._runner(omit_name="receptor.HD.map"),
            ),
        )
        for name, runner in cases:
            with self.subTest(name=name):
                failed = self.support._generate(
                    project_dir,
                    runner=runner,
                )
                self.assertFalse(failed["ok"], failed)
                self.assertEqual(
                    self.support._project_data(project_dir)[
                        "hydrated_docking"
                    ]["active_maps_manifest"],
                    before_pointer,
                )
                failed_manifest = json.loads(
                    (
                        project_dir / failed["manifest_file"]
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(failed_manifest["status"], "failed")
                status = self.support._status(project_dir)
                self.assertTrue(status["maps_ready"], status)
                self.assertEqual(
                    status["active_maps_manifest"],
                    before_pointer["path"],
                )

    def test_concurrent_generation_is_serial_and_publishes_unique_ready_sets(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        base_runner = self.support._runner()
        runner_state_lock = threading.Lock()
        runner_active = 0
        runner_max_active = 0

        def observed_runner(
            executable: str,
            gpf_file: str,
            log_file: str,
            working_directory: str | Path,
            **kwargs: object,
        ) -> dict[str, object]:
            nonlocal runner_active, runner_max_active
            with runner_state_lock:
                runner_active += 1
                runner_max_active = max(runner_max_active, runner_active)
            try:
                time.sleep(0.05)
                return base_runner(
                    executable,
                    gpf_file,
                    log_file,
                    working_directory,
                    **kwargs,
                )
            finally:
                with runner_state_lock:
                    runner_active -= 1

        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self.support._autogrid_detection(),
        ):
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        hydrated.generate_hydrated_maps,
                        str(project_dir),
                        {
                            "spacing": 1.0,
                            "grid_points": {
                                "x": 8,
                                "y": 8,
                                "z": 8,
                            },
                        },
                        observed_runner,
                    )
                    for _ in range(4)
                ]
                results = [
                    future.result(timeout=30)
                    for future in futures
                ]

        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(runner_max_active, 1)
        self.assertEqual(
            sorted(result["map_set_id"] for result in results),
            [
                "hydrated_001",
                "hydrated_002",
                "hydrated_003",
                "hydrated_004",
            ],
        )
        by_id = {
            str(result["map_set_id"]): result
            for result in results
        }
        for map_set_id, result in by_id.items():
            manifest_path = project_dir / result["manifest_file"]
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["map_set_id"], map_set_id)
            self.assertEqual(
                manifest["maps"]["prefix"],
                f"maps/{map_set_id}/receptor",
            )
            self.assertTrue(
                manifest["grid_coverage"]["covers_requested_box"]
            )
            self.assertRegex(
                manifest["grid_coverage_sha256"],
                r"^[0-9a-f]{64}$",
            )
            required = set(manifest["maps"]["required_files"])
            recorded = {
                str(item["name"])
                for item in manifest["maps"]["files"]
            }
            self.assertEqual(required, recorded)
            self.assertIn("receptor.W.map", required)
            for item in manifest["maps"]["files"]:
                path = project_dir / item["relative_path"]
                self.assertTrue(path.is_file(), path)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    item["sha256"],
                )

        project_payload = self.support._project_data(project_dir)
        active_pointer = project_payload["hydrated_docking"][
            "active_maps_manifest"
        ]
        final_result = by_id["hydrated_004"]
        final_manifest_path = project_dir / final_result["manifest_file"]
        self.assertEqual(
            active_pointer["path"],
            final_result["manifest_file"],
        )
        self.assertEqual(
            active_pointer["sha256"],
            hashlib.sha256(final_manifest_path.read_bytes()).hexdigest(),
        )
        final_manifest = json.loads(
            final_manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            active_pointer["grid_coverage_sha256"],
            final_manifest["grid_coverage_sha256"],
        )
        status = self.support._status(project_dir)
        self.assertTrue(status["maps_ready"], status)
        self.assertEqual(
            status["active_maps_manifest"],
            final_result["manifest_file"],
        )

    def test_keyboard_interrupt_releases_lock_preserves_active_and_retry_succeeds(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        first = self.support._generate(project_dir)
        self.assertTrue(first["ok"], first)
        before_pointer = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]
        successful_runner = self.support._runner()

        def interrupted_runner(
            executable: str,
            gpf_file: str,
            log_file: str,
            working_directory: str | Path,
            **kwargs: object,
        ) -> dict[str, object]:
            successful_runner(
                executable,
                gpf_file,
                log_file,
                working_directory,
                **kwargs,
            )
            raise KeyboardInterrupt("simulated AutoGrid interrupt")

        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self.support._autogrid_detection(),
        ):
            with self.assertRaisesRegex(
                KeyboardInterrupt,
                "simulated AutoGrid interrupt",
            ):
                hydrated.generate_hydrated_maps(
                    str(project_dir),
                    {
                        "spacing": 1.0,
                        "grid_points": {
                            "x": 8,
                            "y": 8,
                            "z": 8,
                        },
                    },
                    runner=interrupted_runner,
                )

        self.assertEqual(
            self.support._project_data(project_dir)["hydrated_docking"][
                "active_maps_manifest"
            ],
            before_pointer,
        )
        interrupted_dir = project_dir / "maps" / "hydrated_002"
        self.assertTrue(interrupted_dir.is_dir())
        base_manifest = interrupted_dir / "manifest.json"
        self.assertTrue(base_manifest.is_file())
        base_manifest_bytes = base_manifest.read_bytes()
        base_audit = json.loads(
            base_manifest_bytes.decode("utf-8")
        )
        self.assertEqual(base_audit["map_set_id"], "hydrated_002")
        self.assertEqual(base_audit["status"], "interrupted")
        self.assertEqual(
            base_audit["error"]["code"],
            "AUTOGRID_RUN_INTERRUPTED",
        )
        self.assertFalse(
            (
                interrupted_dir
                / hydrated.HYDRATED_MAPS_MANIFEST_NAME
            ).exists()
        )

        retried = self.support._generate(project_dir)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["map_set_id"], "hydrated_003")

        interrupted_manifest = (
            interrupted_dir / hydrated.HYDRATED_MAPS_MANIFEST_NAME
        )
        self.assertTrue(interrupted_manifest.is_file())
        interrupted_audit = json.loads(
            interrupted_manifest.read_text(encoding="utf-8")
        )
        self.assertEqual(interrupted_audit["map_set_id"], "hydrated_002")
        self.assertEqual(interrupted_audit["status"], "interrupted")
        self.assertEqual(base_manifest.read_bytes(), base_manifest_bytes)
        self.assertEqual(
            interrupted_audit["recovery"]["status"],
            "recovered_incomplete_record",
        )
        self.assertNotEqual(
            before_pointer["path"],
            interrupted_manifest.relative_to(project_dir).as_posix(),
        )

        after_pointer = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]
        self.assertEqual(after_pointer, retried["active_maps_manifest"])
        status = self.support._status(project_dir)
        self.assertTrue(status["maps_ready"], status)
        self.assertEqual(
            status["active_maps_manifest"],
            retried["manifest_file"],
        )

    def test_hard_kill_orphan_is_recovered_as_non_active_before_retry(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        first = self.support._generate(project_dir)
        self.assertTrue(first["ok"], first)
        before_pointer = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]

        orphan_dir = project_dir / "maps" / "hydrated_002"
        orphan_dir.mkdir()
        (orphan_dir / "receptor.gpf").write_text(
            "partial process-kill residue\n",
            encoding="utf-8",
        )
        self.assertFalse(
            (orphan_dir / "manifest.json").exists()
        )
        self.assertFalse(
            (
                orphan_dir
                / hydrated.HYDRATED_MAPS_MANIFEST_NAME
            ).exists()
        )

        retried = self.support._generate(project_dir)

        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["map_set_id"], "hydrated_003")
        recovered_manifest = (
            orphan_dir / hydrated.HYDRATED_MAPS_MANIFEST_NAME
        )
        self.assertTrue(recovered_manifest.is_file())
        recovered_audit = json.loads(
            recovered_manifest.read_text(encoding="utf-8")
        )
        self.assertEqual(recovered_audit["map_set_id"], "hydrated_002")
        self.assertEqual(recovered_audit["status"], "interrupted")
        self.assertEqual(
            recovered_audit["error"]["code"],
            "HYDRATED_MAPS_INCOMPLETE_RECORD_RECOVERED",
        )
        self.assertEqual(
            recovered_audit["recovery"]["active_at_recovery"],
            False,
        )
        self.assertNotEqual(
            before_pointer["path"],
            recovered_manifest.relative_to(project_dir).as_posix(),
        )
        active_pointer = self.support._project_data(project_dir)[
            "hydrated_docking"
        ]["active_maps_manifest"]
        self.assertEqual(active_pointer, retried["active_maps_manifest"])
        self.assertNotEqual(
            active_pointer["path"],
            recovered_manifest.relative_to(project_dir).as_posix(),
        )
        status = self.support._status(project_dir)
        self.assertTrue(status["maps_ready"], status)
        self.assertEqual(
            status["active_maps_manifest"],
            retried["manifest_file"],
        )


class HydratedRunFailureRecoveryMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = integration_support.HydratedProjectIntegrationTests(
            methodName=(
                "test_results_and_reports_keep_raw_affinity_and_water_semantics"
            )
        )
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)

    def _execute_failure(
        self,
        project_dir: Path,
        run_id: str,
        *,
        exit_code: int,
    ) -> dict[str, object]:
        def fake_run(
            command: list[str],
            cwd: str | Path,
            stdout_path: str | Path,
            stderr_path: str | Path,
            log_path: str | Path,
            **_kwargs: object,
        ) -> ManagedRunResult:
            _ = command, cwd
            Path(stdout_path).write_text(
                (
                    integration_support._vina_log()
                    if exit_code == 0
                    else "simulated Vina failure\n"
                ),
                encoding="utf-8",
            )
            Path(stderr_path).write_text(
                "" if exit_code == 0 else "simulated non-zero exit\n",
                encoding="utf-8",
            )
            Path(log_path).write_text(
                (
                    integration_support._vina_log()
                    if exit_code == 0
                    else "simulated Vina failure\n"
                ),
                encoding="utf-8",
            )
            return ManagedRunResult(
                pid=4242,
                exit_code=exit_code,
            )

        with (
            patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self.support._vina_detection(),
            ),
            patch(
                "dockstart_core.project.vina_adapter.run_managed",
                side_effect=fake_run,
            ),
        ):
            return execute_prepared_vina_run(
                str(project_dir),
                run_id,
            )

    def test_vina_nonzero_and_missing_output_are_terminal_then_new_run_succeeds(
        self,
    ) -> None:
        project_dir = self.support._create_ready_project()
        failed_run_ids: list[str] = []

        for name, exit_code in (("nonzero", 7), ("missing_out", 0)):
            with self.subTest(name=name):
                prepared = self.support._prepare_run(project_dir)
                run_id = str(prepared["run_id"])
                failed_run_ids.append(run_id)
                failed = self._execute_failure(
                    project_dir,
                    run_id,
                    exit_code=exit_code,
                )
                self.assertFalse(failed["ok"], failed)
                self.assertEqual(failed["error"]["code"], "VINA_RUN_FAILED")
                self.assertEqual(failed["metadata"]["status"], "failed")
                self.assertEqual(failed["metadata"]["stage"], "failed")
                self.assertEqual(
                    failed["metadata"]["exit_code"],
                    exit_code,
                )
                self.assertEqual(
                    failed["metadata"]["hydrated_postprocess"]["status"],
                    "not_run",
                )
                self.assertFalse(
                    (
                        project_dir / "runs" / run_id / "out.pdbqt"
                    ).exists()
                )

        recovery = self.support._prepare_run(project_dir)
        recovery_run_id = str(recovery["run_id"])
        recovered = self.support._execute(
            project_dir,
            recovery_run_id,
        )

        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual(recovered["metadata"]["status"], "finished")
        self.assertEqual(recovered["metadata"]["stage"], "finished")
        for failed_run_id in failed_run_ids:
            self.assertEqual(
                self.support._metadata_data(
                    project_dir,
                    failed_run_id,
                )["status"],
                "failed",
            )

    def test_verified_run_freezes_complete_tool_version_tuple(self) -> None:
        project_dir = self.support._create_ready_project()
        prepared = self.support._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self.support._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)

        results = load_hydrated_results(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        provenance = results["provenance"]
        self.assertEqual(provenance["status"], "verified")
        toolchain = provenance["toolchain"]
        version_tuple = tuple(
            toolchain[key]["version"]
            for key in (
                "python",
                "rdkit",
                "meeko",
                "autogrid",
                "vina",
            )
        )
        self.assertEqual(
            version_tuple,
            (
                "Python 3.11.15",
                "2026.3.3-test",
                "0.7.1-test",
                "4.2.7",
                "1.2.7",
            ),
        )
        for key in ("python", "rdkit", "meeko", "autogrid", "vina"):
            self.assertRegex(toolchain[key]["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(toolchain[key]["size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
