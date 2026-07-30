from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core import ad4zn as ad4zn_module  # noqa: E402
from dockstart_core.ad4zn import (  # noqa: E402
    REQUIRED_CONFIRMATIONS,
    get_status as get_ad4zn_status,
    prepare_receptor as prepare_ad4zn_receptor,
    save_review,
)
from dockstart_core.autogrid import (  # noqa: E402
    generate_maps,
    validate_active_maps,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    build_markdown_report,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    get_report_status,
    import_receptor_pdbqt,
    load_scores_csv,
    prepare_vina_run,
    recover_project_state,
)
from tests import test_ad4zn_project_integration as ad4zn_support  # noqa: E402


VINA_LOG = """mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -7.4          0          0
"""


class AD4ZnFailureRecoveryMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        reference_sha256 = hashlib.sha256(
            ad4zn_support.PARAMETER_TEXT.encode("utf-8")
        ).hexdigest()
        patcher = patch.object(
            ad4zn_module,
            "SUPPORTED_PARAMETER_REFERENCE_SHA256",
            reference_sha256,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.support = ad4zn_support.AD4ZnProjectIntegrationTests(
            methodName="runTest"
        )

    def _autogrid_detection(
        self,
        root: str,
        *,
        version: str = "4.2.7.x.2019-07-11",
    ):
        executable = Path(root) / "autogrid4.exe"
        if not executable.exists():
            executable.write_bytes(b"mock AutoGrid executable")
        return self.support._tool(
            "autogrid4",
            "AutoGrid4",
            version,
            executable,
        )

    def _vina_detection(self, root: str):
        executable = Path(root) / "vina.exe"
        if not executable.exists():
            executable.write_bytes(b"mock Vina 1.2.7 executable")
        return self.support._tool(
            "vina",
            "AutoDock Vina",
            "1.2.7",
            executable,
        )

    def _generate_maps(
        self,
        project_dir: Path,
        root: str,
        *,
        runner=None,
        version: str = "4.2.7.x.2019-07-11",
    ) -> dict[str, object]:
        with patch(
            "dockstart_core.autogrid.autogrid_adapter.detect",
            return_value=self._autogrid_detection(root, version=version),
        ):
            return generate_maps(
                str(project_dir),
                runner=runner or self.support._fake_autogrid,
            )

    def _ready_project(
        self,
        root: str,
    ) -> tuple[Path, Path, dict[str, object]]:
        project_dir, parameter = self.support._prepared_project(root)
        generated = self._generate_maps(project_dir, root)
        self.assertTrue(generated["ok"], generated)
        configured = generate_vina_config(str(project_dir))
        self.assertTrue(configured["ok"], configured)
        return project_dir, parameter, generated

    def _prepare_run(
        self,
        project_dir: Path,
        root: str,
    ) -> dict[str, object]:
        with patch(
            "dockstart_core.project.vina_adapter.detect",
            return_value=self._vina_detection(root),
        ):
            prepared = prepare_vina_run(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return prepared

    @staticmethod
    def _successful_vina(
        command: list[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
        **_kwargs: object,
    ) -> ManagedRunResult:
        Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        Path(log_path).write_text(VINA_LOG, encoding="utf-8")
        output = Path(cwd) / command[command.index("--out") + 1]
        output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ManagedRunResult(pid=None, exit_code=0)

    @staticmethod
    def _failed_vina(
        _command: list[str],
        _cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
        **_kwargs: object,
    ) -> ManagedRunResult:
        Path(stdout_path).write_text("synthetic Vina failure\n", encoding="utf-8")
        Path(stderr_path).write_text("synthetic Vina failure\n", encoding="utf-8")
        Path(log_path).write_text("synthetic Vina failure\n", encoding="utf-8")
        return ManagedRunResult(
            pid=None,
            exit_code=2,
            error="synthetic Vina failure",
        )

    def _execute(
        self,
        project_dir: Path,
        root: str,
        run_id: str,
        runner,
    ) -> dict[str, object]:
        executor_identity = {
            "pid": os.getpid(),
            "executable_path": sys.executable,
            "creation_token": "ad4zn-failure-matrix",
        }
        with (
            patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_detection(root),
            ),
            patch(
                "dockstart_core.project.vina_adapter.run_managed",
                side_effect=runner,
            ),
            patch(
                "dockstart_core.project.vina_adapter.get_process_identity",
                return_value=executor_identity,
            ),
        ):
            return execute_prepared_vina_run(str(project_dir), run_id)

    def _finished_run(
        self,
        root: str,
    ) -> tuple[Path, str]:
        project_dir, _parameter, _generated = self._ready_project(root)
        prepared = self._prepare_run(project_dir, root)
        run_id = str(prepared["run_id"])
        executed = self._execute(
            project_dir,
            root,
            run_id,
            self._successful_vina,
        )
        self.assertTrue(executed["ok"], executed)
        return project_dir, run_id

    @staticmethod
    def _nonzero_autogrid(
        _executable: str,
        _gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        root = Path(working_directory)
        (root / log_file).write_text(
            "AutoGrid 4.2.7\nERROR synthetic non-zero exit\n",
            encoding="utf-8",
        )
        return {
            "ok": False,
            "command": ["autogrid4"],
            "exit_code": 2,
            "stdout": "",
            "stderr": "synthetic non-zero exit",
            "error": "synthetic non-zero exit",
        }

    @staticmethod
    def _missing_map_autogrid(
        executable: str,
        gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        **kwargs: object,
    ) -> dict[str, object]:
        result = (
            ad4zn_support.AD4ZnProjectIntegrationTests._fake_autogrid(
                executable,
                gpf_file,
                log_file,
                working_directory,
                **kwargs,
            )
        )
        (Path(working_directory) / "receptor.C.map").unlink()
        return result

    def test_prepared_run_rejects_each_frozen_ad4zn_evidence_byte_tamper(
        self,
    ) -> None:
        expected_codes = {
            "parameter": "RUN_AD4ZN_EVIDENCE_INVALID",
            "gpf": "RUN_AD4ZN_EVIDENCE_INVALID",
            "tz_receptor": "RUN_SNAPSHOT_HASH_MISMATCH",
            "maps_manifest": "RUN_AD4_MANIFEST_HASH_MISMATCH",
            "map": "RUN_AD4_MAP_HASH_MISMATCH",
        }
        for target, expected_code in expected_codes.items():
            with self.subTest(target=target), tempfile.TemporaryDirectory() as root:
                project_dir, _parameter, _generated = self._ready_project(root)
                prepared = self._prepare_run(project_dir, root)
                run_id = str(prepared["run_id"])
                run_dir = project_dir / "runs" / run_id
                paths = {
                    "parameter": run_dir / "inputs" / "ad4zn" / "AD4Zn.dat",
                    "gpf": run_dir / "inputs" / "ad4zn" / "receptor.gpf",
                    "tz_receptor": run_dir / "inputs" / "receptor.pdbqt",
                    "maps_manifest": run_dir
                    / "inputs"
                    / "maps"
                    / "manifest.json",
                    "map": project_dir
                    / prepared["metadata"]["snapshots"]["ad4_maps"]["files"][0][
                        "relative_path"
                    ],
                }
                tampered = paths[target]
                tampered.write_bytes(tampered.read_bytes() + b"\n# byte-tamper\n")

                with patch(
                    "dockstart_core.project.vina_adapter.run_managed"
                ) as run_mock:
                    rejected = execute_prepared_vina_run(
                        str(project_dir),
                        run_id,
                    )

                self.assertFalse(rejected["ok"], rejected)
                self.assertEqual(rejected["error"]["code"], expected_code)
                run_mock.assert_not_called()
                metadata = json.loads(
                    (run_dir / "metadata.json").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["status"], "prepared")

    def test_autogrid_nonzero_or_missing_map_keeps_failed_audit_and_recovers(
        self,
    ) -> None:
        for label, runner in (
            ("nonzero", self._nonzero_autogrid),
            ("missing_map", self._missing_map_autogrid),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root:
                project_dir, _parameter = self.support._prepared_project(root)

                failed = self._generate_maps(
                    project_dir,
                    root,
                    runner=runner,
                )

                self.assertFalse(failed["ok"], failed)
                self.assertEqual(
                    failed["error"]["code"],
                    "AUTOGRID_RUN_FAILED",
                )
                failed_manifest = (
                    project_dir / str(failed["manifest_file"])
                )
                self.assertTrue(failed_manifest.is_file())
                self.assertEqual(
                    json.loads(
                        failed_manifest.read_text(encoding="utf-8")
                    )["status"],
                    "failed",
                )
                inactive = validate_active_maps(str(project_dir))
                self.assertFalse(inactive["ok"], inactive)
                self.assertEqual(
                    inactive["error"]["code"],
                    "MAPS_NOT_PREPARED",
                )

                recovered = self._generate_maps(project_dir, root)
                self.assertTrue(recovered["ok"], recovered)
                self.assertNotEqual(
                    recovered["map_set_id"],
                    failed["map_set_id"],
                )
                ready = validate_active_maps(str(project_dir))
                self.assertTrue(ready["ready"], ready)

    def test_wrong_autogrid_version_refuses_runner_then_supported_version_recovers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter = self.support._prepared_project(root)
            runner = Mock(side_effect=self.support._fake_autogrid)

            rejected = self._generate_maps(
                project_dir,
                root,
                runner=runner,
                version="4.2.6",
            )

            self.assertFalse(rejected["ok"], rejected)
            self.assertEqual(
                rejected["error"]["code"],
                "AD4ZN_AUTOGRID_VERSION_UNSUPPORTED",
            )
            runner.assert_not_called()
            self.assertEqual(
                list((project_dir / "maps").glob("ad4zn_*")),
                [],
            )

            recovered = self._generate_maps(project_dir, root)
            self.assertTrue(recovered["ok"], recovered)
            self.assertTrue(
                validate_active_maps(str(project_dir))["ready"]
            )

    def test_vina_nonzero_failure_is_terminal_and_a_new_run_can_succeed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter, _generated = self._ready_project(root)
            first = self._prepare_run(project_dir, root)
            failed = self._execute(
                project_dir,
                root,
                str(first["run_id"]),
                self._failed_vina,
            )

            self.assertFalse(failed["ok"], failed)
            self.assertEqual(failed["error"]["code"], "VINA_RUN_FAILED")
            self.assertEqual(failed["metadata"]["status"], "failed")
            self.assertEqual(failed["metadata"]["exit_code"], 2)

            second = self._prepare_run(project_dir, root)
            self.assertEqual(second["run_id"], "run_002")
            recovered = self._execute(
                project_dir,
                root,
                str(second["run_id"]),
                self._successful_vina,
            )
            self.assertTrue(recovered["ok"], recovered)
            self.assertEqual(recovered["metadata"]["status"], "finished")
            first_metadata = json.loads(
                (
                    project_dir
                    / "runs"
                    / str(first["run_id"])
                    / "metadata.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(first_metadata["status"], "failed")

    def test_stale_ad4zn_running_state_recovers_to_interrupted_and_allows_new_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter, _generated = self._ready_project(root)
            first = self._prepare_run(project_dir, root)
            run_id = str(first["run_id"])
            metadata_path = (
                project_dir / "runs" / run_id / "metadata.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            dead_identity = {
                "pid": 99999999,
                "executable_path": str(Path(root) / "vina.exe"),
                "creation_token": "dead-ad4zn-run",
            }
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "pid": 99999999,
                    "trusted_executable": str(Path(root) / "vina.exe"),
                    "process_identity": dead_identity,
                }
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with patch(
                "dockstart_core.project.vina_adapter.verify_process_identity",
                return_value={
                    "ok": False,
                    "running": False,
                    "message": "synthetic dead process",
                },
            ):
                recovered = recover_project_state(str(project_dir))

            self.assertTrue(recovered["ok"], recovered)
            self.assertIn(run_id, recovered["recovered_runs"])
            interrupted = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            self.assertEqual(interrupted["status"], "interrupted")

            second = self._prepare_run(project_dir, root)
            self.assertEqual(second["run_id"], "run_002")
            executed = self._execute(
                project_dir,
                root,
                str(second["run_id"]),
                self._successful_vina,
            )
            self.assertTrue(executed["ok"], executed)

    def test_receptor_change_downgrades_review_and_site_binding_until_reprepared(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, _parameter, old_maps = self._ready_project(root)
            changed_source = Path(root) / "changed_receptor.pdbqt"
            changed_source.write_text(
                ad4zn_support.RECEPTOR_PDBQT
                + "REMARK receptor changed after human review\n",
                encoding="utf-8",
            )
            imported = import_receptor_pdbqt(
                str(project_dir),
                str(changed_source),
            )
            self.assertTrue(imported["ok"], imported)

            downgraded = get_ad4zn_status(str(project_dir))
            self.assertFalse(downgraded["review_valid"], downgraded)
            self.assertFalse(downgraded["preparation_ready"], downgraded)
            self.assertTrue(
                any(
                    issue["code"] == "AD4ZN_REVIEW_RECEPTOR_CHANGED"
                    for issue in downgraded["issues"]
                )
            )
            invalid_maps = validate_active_maps(str(project_dir))
            self.assertFalse(invalid_maps["ready"], invalid_maps)
            blocked = prepare_vina_run(str(project_dir))
            self.assertFalse(blocked["ok"], blocked)
            self.assertEqual(
                blocked["error"]["code"],
                "MAPS_VALIDATION_FAILED",
            )

            reviewed = save_review(
                str(project_dir),
                {
                    "selected_site_id": downgraded["sites"][0]["site_id"],
                    "confirmations": {
                        key: True for key in REQUIRED_CONFIRMATIONS
                    },
                },
            )
            self.assertTrue(reviewed["ok"], reviewed)
            prepared_receptor = prepare_ad4zn_receptor(
                str(project_dir),
                {},
            )
            self.assertTrue(prepared_receptor["ok"], prepared_receptor)
            replacement_maps = self._generate_maps(project_dir, root)
            self.assertTrue(replacement_maps["ok"], replacement_maps)
            self.assertNotEqual(
                replacement_maps["map_set_id"],
                old_maps["map_set_id"],
            )
            self.assertTrue(
                generate_vina_config(str(project_dir))["ok"]
            )
            prepared_run = self._prepare_run(project_dir, root)
            self.assertEqual(prepared_run["run_id"], "run_001")

    def test_analysis_rejects_tampered_finished_log_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, run_id = self._finished_run(root)
            log_path = project_dir / "runs" / run_id / "log.txt"
            log_path.write_text(
                log_path.read_text(encoding="utf-8").replace("-7.4", "-1.0"),
                encoding="utf-8",
            )

            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )

            self.assertFalse(
                analyzed["ok"],
                "tampered finished-run log bytes were accepted",
            )
            self.assertIn(
                "HASH_MISMATCH",
                analyzed["error"]["code"],
            )
            self.assertFalse(
                (project_dir / "runs" / run_id / "scores.csv").exists()
            )

    def test_score_loader_rejects_tampered_scores_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, run_id = self._finished_run(root)
            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )
            self.assertTrue(analyzed["ok"], analyzed)
            scores_path = project_dir / "runs" / run_id / "scores.csv"
            scores_path.write_text(
                scores_path.read_text(encoding="utf-8").replace(
                    "-7.4",
                    "-1.0",
                ),
                encoding="utf-8",
            )

            loaded = load_scores_csv(str(project_dir), run_id)

            self.assertFalse(
                loaded["ok"],
                "tampered scores.csv bytes were accepted by load_scores_csv",
            )
            self.assertIn(
                "HASH_MISMATCH",
                loaded["error"]["code"],
            )

    def test_report_builder_rejects_tampered_scores_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, run_id = self._finished_run(root)
            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )
            self.assertTrue(analyzed["ok"], analyzed)
            scores_path = project_dir / "runs" / run_id / "scores.csv"
            scores_path.write_text(
                scores_path.read_text(encoding="utf-8").replace(
                    "-7.4",
                    "-1.0",
                ),
                encoding="utf-8",
            )

            report = build_markdown_report(
                str(project_dir),
                run_id,
            )

            self.assertFalse(
                report["ok"],
                "tampered scores.csv bytes were accepted by report generation",
            )
            self.assertIn(
                "HASH_MISMATCH",
                report["error"]["code"],
            )

    def test_report_status_does_not_mark_tampered_export_as_exported(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, run_id = self._finished_run(root)
            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )
            self.assertTrue(analyzed["ok"], analyzed)
            exported = export_markdown_report(
                str(project_dir),
                run_id,
            )
            self.assertTrue(exported["ok"], exported)
            report_path = project_dir / str(exported["report_file"])
            report_path.write_bytes(
                report_path.read_bytes() + b"\n<!-- byte-tamper -->\n"
            )

            status = get_report_status(str(project_dir), run_id)

            self.assertTrue(status["ok"], status)
            self.assertNotEqual(status["report_status"], "exported")
            run_report = next(
                item
                for item in status["files"]
                if item["key"] == "run_report"
            )
            self.assertNotEqual(run_report["status"], "ok")


if __name__ == "__main__":
    unittest.main()
