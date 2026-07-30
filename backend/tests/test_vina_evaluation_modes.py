from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    _project_report_file,
    analyze_vina_run_results,
    build_markdown_report,
    build_vina_config_text,
    cancel_vina_run,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    get_project_workflow_status,
    get_run_preflight,
    get_run_files_status,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_project,
    load_vina_evaluation,
    parse_vina_evaluation_text,
    prepare_vina_run,
    recover_project_state,
    update_vina_run_protocol,
    validate_config_prerequisites,
    validate_pose_input_attestation,
)
from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.result_export import get_result_export_status  # noqa: E402
from dockstart_core.viewer import (  # noqa: E402
    list_docking_poses,
    load_docking_pose_for_viewer,
)


VINA_EVALUATION_LOG = """AutoDock Vina v1.2.7
Scoring function : vina
Rigid receptor: receptor.pdbqt
Ligand: ligand.pdbqt
Grid center: X 10 Y 11 Z 12
Grid size  : X 20 Y 21 Z 22
Grid space : 0.375

Estimated Free Energy of Binding   : -7.537 (kcal/mol)
(1) Final Intermolecular Energy     : -8.198 (kcal/mol)
    Ligand - Receptor               : -8.198 (kcal/mol)
    Ligand - Flex side chains       : 0.000 (kcal/mol)
(2) Final Total Internal Energy     : 0.000 (kcal/mol)
    Ligand                          : 0.000 (kcal/mol)
    Flex - Receptor                 : 0.000 (kcal/mol)
    Flex - Flex side chains         : 0.000 (kcal/mol)
(3) Torsional Free Energy           : 0.661 (kcal/mol)
(4) Unbound System's Energy         : 0.000 (kcal/mol)
"""

LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)


class VinaEvaluationModeTests(unittest.TestCase):
    def test_project_evaluation_reports_are_isolated_by_scoring_protocol(
        self,
    ) -> None:
        self.assertEqual(
            _project_report_file(
                {
                    "run_mode": "score_only",
                    "scoring_protocol": "vina",
                },
            ),
            "reports/score_only_report.md",
        )
        self.assertEqual(
            _project_report_file(
                {
                    "run_mode": "score_only",
                    "scoring_protocol": "ad4_maps",
                },
            ),
            "reports/ad4_score_only_report.md",
        )
        self.assertEqual(
            _project_report_file(
                {
                    "run_mode": "local_only",
                    "scoring_protocol": "ad4_maps",
                },
            ),
            "reports/ad4_local_only_report.md",
        )

    def _vina_ok_result(self, path: str | None = None) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=path or sys.executable,
            message="已检测到 AutoDock Vina。",
            source="auto",
            capabilities={
                "status": "ok",
                "source": "help_advanced",
                "checked": True,
                "version": "1.2.7",
                "help_exit_code": 0,
                "help_sha256": "0" * 64,
                "features": {
                    "autobox": {
                        "option": "--autobox",
                        "status": "supported",
                        "supported": True,
                        "advertised": True,
                        "minimum_version": "1.2.3",
                        "version_compatible": True,
                        "message": "mock autobox: supported",
                    },
                },
                "message": "mock capability profile",
                "raw_error": "",
            },
        )

    def _create_input_ready_project(self, temp_dir: str) -> Path:
        created = create_project("evaluation_project", temp_dir)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
        ligand_source = Path(temp_dir) / "ligand_source.pdbqt"
        receptor_source.write_text(
            "ATOM      1  C   REC A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n",
            encoding="utf-8",
        )
        ligand_source.write_text(LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor_source))["ok"],
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand_source))["ok"],
        )
        return project_dir

    def _create_config_ready_project(
        self,
        temp_dir: str,
        *,
        run_mode: str | None = None,
        autobox: bool = False,
    ) -> Path:
        project_dir = self._create_input_ready_project(temp_dir)
        if run_mode is not None:
            updated = update_vina_run_protocol(
                str(project_dir),
                run_mode,
                autobox,
                run_mode != "dock",
            )
            self.assertTrue(updated["ok"], updated)
        generated = generate_vina_config(str(project_dir))
        self.assertTrue(generated["ok"], generated)
        return project_dir

    def _create_finished_evaluation_run(
        self,
        temp_dir: str,
        *,
        run_mode: str = "score_only",
        autobox: bool = True,
        unbound_energy: float | None = None,
        logged_unbound_energy: float | None = None,
    ) -> tuple[Path, str]:
        project_dir = self._create_input_ready_project(temp_dir)
        run_id = "run_001"
        run_dir = project_dir / "runs" / run_id
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir(parents=True)
        shutil.copyfile(
            project_dir / "prepared" / "receptor.pdbqt",
            inputs_dir / "receptor.pdbqt",
        )
        shutil.copyfile(
            project_dir / "prepared" / "ligand.pdbqt",
            inputs_dir / "ligand.pdbqt",
        )
        config_text = (
            "receptor = runs/run_001/inputs/receptor.pdbqt\n"
            "ligand = runs/run_001/inputs/ligand.pdbqt\n"
            "scoring = vina\n"
            "cpu = 0\n"
        )
        if unbound_energy is not None:
            config_text += f"unbound_energy = {unbound_energy:g}\n"
        (run_dir / "config_snapshot.txt").write_text(
            config_text,
            encoding="utf-8",
        )
        effective_logged_unbound = (
            unbound_energy
            if logged_unbound_energy is None
            else logged_unbound_energy
        )
        evaluation_log = VINA_EVALUATION_LOG
        if effective_logged_unbound is not None:
            evaluation_log = evaluation_log.replace(
                "(4) Unbound System's Energy         : 0.000 (kcal/mol)",
                (
                    "(4) Unbound System's Energy         : "
                    f"{effective_logged_unbound:.3f} (kcal/mol)"
                ),
            )
            evaluation_log = evaluation_log.replace(
                "Estimated Free Energy of Binding   : -7.537 (kcal/mol)",
                (
                    "Estimated Free Energy of Binding   : "
                    f"{-7.537 - effective_logged_unbound:.3f} (kcal/mol)"
                ),
            )
        (run_dir / "log.txt").write_text(evaluation_log, encoding="utf-8")

        output_file = ""
        pose_file = f"runs/{run_id}/inputs/ligand.pdbqt"
        command = [
            "vina",
            "--config",
            f"runs/{run_id}/config_snapshot.txt",
            f"--{run_mode}",
        ]
        if autobox:
            command.append("--autobox")
        if run_mode == "local_only":
            output_file = f"runs/{run_id}/optimized.pdbqt"
            pose_file = output_file
            (run_dir / "optimized.pdbqt").write_text(LIGAND_PDBQT, encoding="utf-8")
            command.extend(["--out", output_file])

        metadata = {
            "run_id": run_id,
            "status": "finished",
            "run_mode": run_mode,
            "autobox": autobox,
            "scoring_protocol": "vina",
            "scoring_function": "vina",
            "started_at": "2026-07-28T00:00:00+00:00",
            "finished_at": "2026-07-28T00:00:01+00:00",
            "vina_version": "1.2.7",
            "vina_sha256": "mock-vina-sha256",
            "command": command,
            "config_snapshot": f"runs/{run_id}/config_snapshot.txt",
            "log_file": f"runs/{run_id}/log.txt",
            "output_file": output_file,
            "pose_file": pose_file,
            "input_sha256": {
                "receptor": "mock-receptor-sha256",
                "ligand": "mock-ligand-sha256",
                "config": "mock-config-sha256",
            },
            "snapshots": {
                "vina": {
                    "scoring": "vina",
                    "spacing": 0.375,
                    "unbound_energy": unbound_energy,
                    "no_refine": False,
                    "force_even_voxels": False,
                    "verbosity": 1,
                    "cpu": 0,
                },
            },
            "docking_protocol": {
                "engine": "vina",
                "mode": "rigid",
                "receptor_mode": "rigid",
                "run_mode": run_mode,
                "autobox": autobox,
            },
            "exit_code": 0,
        }
        if unbound_energy is not None:
            metadata["artifacts"] = {
                "log": {
                    "sha256": hashlib.sha256(
                        (run_dir / "log.txt").read_bytes(),
                    ).hexdigest(),
                },
            }
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        project_json = project_dir / "project.json"
        project = json.loads(project_json.read_text(encoding="utf-8"))
        project["runs"] = [
            {
                "run_id": run_id,
                "status": "finished",
                "run_mode": run_mode,
                "metadata_file": f"runs/{run_id}/metadata.json",
            },
        ]
        project_json.write_text(
            json.dumps(project, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return project_dir, run_id

    def test_legacy_project_without_run_protocol_defaults_to_global_docking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            project_before = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertNotIn("docking_protocol", project_before)

            config = build_vina_config_text(str(project_dir))
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(config["ok"], config)
            self.assertEqual(config["run_mode"], "dock")
            self.assertFalse(config["autobox"])
            self.assertIn("center_x = 0", config["config_text"])
            self.assertIn("exhaustiveness = 8", config["config_text"])
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(prepared["metadata"]["run_mode"], "dock")
            self.assertFalse(prepared["metadata"]["autobox"])
            self.assertNotIn("--score_only", prepared["command"])
            self.assertNotIn("--local_only", prepared["command"])
            self.assertNotIn("--autobox", prepared["command"])
            self.assertEqual(
                prepared["command"][prepared["command"].index("--out") + 1],
                "runs/run_001/out.pdbqt",
            )

    def test_run_protocol_persists_without_discarding_existing_protocol_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["docking_protocol"] = {
                "engine": "vina",
                "mode": "rigid",
                "custom_marker": "preserve-me",
            }
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            updated = update_vina_run_protocol(
                str(project_dir),
                "local_only",
                True,
                True,
            )
            loaded = load_project(str(project_dir))

            self.assertTrue(updated["ok"], updated)
            self.assertTrue(loaded["ok"], loaded)
            protocol = loaded["project"]["docking_protocol"]
            self.assertEqual(protocol["run_mode"], "local_only")
            self.assertTrue(protocol["autobox"])
            self.assertEqual(protocol["engine"], "vina")
            self.assertEqual(protocol["mode"], "rigid")
            self.assertEqual(protocol["custom_marker"], "preserve-me")
            self.assertEqual(
                protocol["pose_input_attestation"]["claim"],
                "same_receptor_coordinate_frame",
            )

            dock = update_vina_run_protocol(str(project_dir), "dock", True)
            self.assertTrue(dock["ok"], dock)
            self.assertFalse(dock["project"]["docking_protocol"]["autobox"])

    def test_pose_context_confirmation_survives_ordinary_saves_and_mode_round_trip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            confirmed = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                True,
            )
            self.assertTrue(confirmed["ok"], confirmed)
            original_attestation = confirmed["project"]["docking_protocol"][
                "pose_input_attestation"
            ]

            ordinary_save = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                False,
                False,
            )
            self.assertTrue(ordinary_save["ok"], ordinary_save)
            self.assertEqual(
                ordinary_save["pose_input_attestation"]["status"],
                "confirmed",
            )
            self.assertEqual(
                ordinary_save["project"]["docking_protocol"][
                    "pose_input_attestation"
                ],
                original_attestation,
            )

            local = update_vina_run_protocol(
                str(project_dir),
                "local_only",
                True,
                False,
            )
            self.assertTrue(local["ok"], local)
            self.assertEqual(
                local["pose_input_attestation"]["status"],
                "confirmed",
            )
            self.assertEqual(
                local["project"]["docking_protocol"][
                    "pose_input_attestation"
                ],
                original_attestation,
            )

            dock = update_vina_run_protocol(
                str(project_dir),
                "dock",
                False,
                False,
            )
            self.assertTrue(dock["ok"], dock)
            self.assertEqual(
                dock["pose_input_attestation"]["status"],
                "not_required",
            )
            self.assertEqual(
                dock["project"]["docking_protocol"][
                    "pose_input_attestation"
                ],
                original_attestation,
            )

            score_again = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                False,
            )
            self.assertTrue(score_again["ok"], score_again)
            self.assertEqual(
                score_again["pose_input_attestation"]["status"],
                "confirmed",
            )
            self.assertEqual(
                score_again["project"]["docking_protocol"][
                    "pose_input_attestation"
                ],
                original_attestation,
            )

    def test_ad4_maps_evaluation_forces_project_box_instead_of_autobox(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["docking_protocol"] = {
                "engine": "ad4_maps",
                "run_mode": "dock",
                "autobox": False,
            }
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            updated = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                True,
            )

            self.assertTrue(updated["ok"], updated)
            self.assertEqual(updated["run_mode"], "score_only")
            self.assertFalse(updated["autobox"])
            self.assertFalse(
                updated["project"]["docking_protocol"]["autobox"],
            )
            self.assertEqual(
                updated["pose_input_attestation"]["status"],
                "confirmed",
            )

    def test_evaluation_mode_without_pose_context_confirmation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            updated = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
            )
            self.assertTrue(updated["ok"], updated)
            self.assertEqual(
                updated["pose_input_attestation"]["status"],
                "missing",
            )

            config = validate_config_prerequisites(str(project_dir))
            prepared = prepare_vina_run(str(project_dir))
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                preflight = get_run_preflight(str(project_dir))

            self.assertFalse(config["ok"], config)
            self.assertEqual(
                config["error"]["code"],
                "POSE_INPUT_ATTESTATION_REQUIRED",
            )
            self.assertFalse(prepared["ok"], prepared)
            self.assertEqual(
                prepared["error"]["code"],
                "POSE_INPUT_ATTESTATION_REQUIRED",
            )
            self.assertTrue(preflight["ok"], preflight)
            self.assertFalse(preflight["ready"], preflight)
            self.assertEqual(
                preflight["pose_input_attestation"]["status"],
                "missing",
            )
            self.assertFalse(
                preflight["pose_input_attestation"]["valid"],
            )
            check = next(
                item
                for item in preflight["checks"]
                if item["key"] == "pose_input_attestation"
            )
            self.assertTrue(check["blocking"])
            self.assertIn("不会自动证明", check["detail"])

    def test_update_run_protocol_cli_keeps_three_argument_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            legacy = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dockstart_core.project",
                    "update-run-protocol",
                    str(project_dir),
                    "score_only",
                    "true",
                ],
                cwd=BACKEND_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            legacy_payload = json.loads(legacy.stdout)
            self.assertTrue(legacy_payload["ok"], legacy_payload)
            self.assertEqual(
                legacy_payload["pose_input_attestation"]["status"],
                "missing",
            )

            confirmed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dockstart_core.project",
                    "update-run-protocol",
                    str(project_dir),
                    "score_only",
                    "true",
                    "true",
                ],
                cwd=BACKEND_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            confirmed_payload = json.loads(confirmed.stdout)
            self.assertTrue(confirmed_payload["ok"], confirmed_payload)
            self.assertEqual(
                confirmed_payload["pose_input_attestation"]["status"],
                "confirmed",
            )

    def test_matching_pose_context_confirmation_passes_and_reports_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            updated = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                True,
            )
            self.assertTrue(updated["ok"], updated)
            generated = generate_vina_config(str(project_dir))
            self.assertTrue(generated["ok"], generated)

            status = validate_pose_input_attestation(str(project_dir))
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                preflight = get_run_preflight(str(project_dir))

            expected_receptor_hash = hashlib.sha256(
                (project_dir / "prepared" / "receptor.pdbqt").read_bytes(),
            ).hexdigest()
            expected_ligand_hash = hashlib.sha256(
                (project_dir / "prepared" / "ligand.pdbqt").read_bytes(),
            ).hexdigest()
            self.assertTrue(status["ok"], status)
            attestation = status["pose_input_attestation"]
            self.assertTrue(attestation["required"])
            self.assertTrue(attestation["valid"])
            self.assertEqual(attestation["status"], "confirmed")
            self.assertEqual(
                attestation["receptor_sha256"],
                expected_receptor_hash,
            )
            self.assertEqual(
                attestation["ligand_sha256"],
                expected_ligand_hash,
            )
            self.assertEqual(
                attestation["claim"],
                "same_receptor_coordinate_frame",
            )
            self.assertTrue(attestation["confirmed_at"])
            self.assertTrue(preflight["ready"], preflight)
            self.assertEqual(
                preflight["pose_input_attestation"]["status"],
                "confirmed",
            )

    def test_replacing_either_pose_input_invalidates_confirmation(self) -> None:
        replacements = {
            "receptor": (
                "prepared/receptor.pdbqt",
                "ATOM      1  C   REC A   1       9.000   0.000   0.000  1.00  0.00     0.000 C\n",
            ),
            "ligand": (
                "prepared/ligand.pdbqt",
                LIGAND_PDBQT.replace("1.000   2.000   3.000", "8.000   2.000   3.000"),
            ),
        }
        for role, (relative_path, replacement) in replacements.items():
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_input_ready_project(temp_dir)
                updated = update_vina_run_protocol(
                    str(project_dir),
                    "local_only",
                    True,
                    True,
                )
                self.assertTrue(updated["ok"], updated)
                generated = generate_vina_config(str(project_dir))
                self.assertTrue(generated["ok"], generated)
                (project_dir / relative_path).write_text(
                    replacement,
                    encoding="utf-8",
                )

                status = validate_pose_input_attestation(str(project_dir))
                config = validate_config_prerequisites(str(project_dir))
                prepared = prepare_vina_run(str(project_dir))

                self.assertTrue(status["ok"], status)
                self.assertEqual(
                    status["pose_input_attestation"]["status"],
                    "stale",
                )
                self.assertFalse(
                    status["pose_input_attestation"]["valid"],
                )
                self.assertFalse(config["ok"], config)
                self.assertEqual(
                    config["error"]["code"],
                    "POSE_INPUT_ATTESTATION_STALE",
                )
                self.assertFalse(prepared["ok"], prepared)
                self.assertEqual(
                    prepared["error"]["code"],
                    "POSE_INPUT_ATTESTATION_STALE",
                )

    def test_global_docking_does_not_require_pose_context_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            updated = update_vina_run_protocol(
                str(project_dir),
                "dock",
                False,
            )
            self.assertTrue(updated["ok"], updated)
            self.assertFalse(
                updated["pose_input_attestation"]["required"],
            )
            generated = generate_vina_config(str(project_dir))
            self.assertTrue(generated["ok"], generated)
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                preflight = get_run_preflight(str(project_dir))
                prepared = prepare_vina_run(str(project_dir))
            self.assertFalse(
                preflight["pose_input_attestation"]["required"],
            )
            self.assertTrue(
                preflight["pose_input_attestation"]["valid"],
            )
            self.assertEqual(
                preflight["pose_input_attestation"]["status"],
                "not_required",
            )
            self.assertTrue(prepared["ok"], prepared)
            self.assertNotIn(
                "pose_input_attestation",
                prepared["metadata"]["docking_protocol"],
            )

    def test_prepared_run_freezes_pose_context_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_input_ready_project(temp_dir)
            updated = update_vina_run_protocol(
                str(project_dir),
                "local_only",
                True,
                True,
            )
            self.assertTrue(updated["ok"], updated)
            frozen = updated["project"]["docking_protocol"][
                "pose_input_attestation"
            ]
            generated = generate_vina_config(str(project_dir))
            self.assertTrue(generated["ok"], generated)
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(
                prepared["metadata"]["docking_protocol"][
                    "pose_input_attestation"
                ],
                frozen,
            )
            self.assertNotIn(
                "pose_input_attestation",
                prepared["metadata"],
            )

            (project_dir / "prepared" / "ligand.pdbqt").write_text(
                LIGAND_PDBQT.replace(
                    "1.000   2.000   3.000",
                    "7.000   2.000   3.000",
                ),
                encoding="utf-8",
            )
            metadata = json.loads(
                (
                    project_dir
                    / "runs"
                    / prepared["run_id"]
                    / "metadata.json"
                ).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                metadata["docking_protocol"]["pose_input_attestation"],
                frozen,
            )
            current = validate_pose_input_attestation(str(project_dir))
            self.assertEqual(
                current["pose_input_attestation"]["status"],
                "stale",
            )

    def test_execute_rejects_prepared_evaluation_without_frozen_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="score_only",
                autobox=True,
            )
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)

            metadata_path = (
                project_dir
                / "runs"
                / prepared["run_id"]
                / "metadata.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["docking_protocol"].pop(
                "pose_input_attestation",
                None,
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            executed = execute_prepared_vina_run(
                str(project_dir),
                prepared["run_id"],
            )

            self.assertFalse(executed["ok"], executed)
            self.assertEqual(
                executed["error"]["code"],
                "RUN_POSE_INPUT_ATTESTATION_MISSING",
            )

    def test_execute_rejects_frozen_confirmation_for_other_input_hashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="local_only",
                autobox=True,
            )
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)

            metadata_path = (
                project_dir
                / "runs"
                / prepared["run_id"]
                / "metadata.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["docking_protocol"]["pose_input_attestation"][
                "ligand_sha256"
            ] = "0" * 64
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            executed = execute_prepared_vina_run(
                str(project_dir),
                prepared["run_id"],
            )

            self.assertFalse(executed["ok"], executed)
            self.assertEqual(
                executed["error"]["code"],
                "RUN_POSE_INPUT_ATTESTATION_HASH_MISMATCH",
            )

    def test_evaluation_commands_use_mode_specific_flags_output_and_autobox(self) -> None:
        cases = (
            ("score_only", "--score_only", ""),
            ("local_only", "--local_only", "runs/run_001/optimized.pdbqt"),
        )
        for run_mode, expected_flag, expected_output in cases:
            with self.subTest(run_mode=run_mode), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_config_ready_project(
                    temp_dir,
                    run_mode=run_mode,
                    autobox=True,
                )
                with unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=self._vina_ok_result(),
                ):
                    prepared = prepare_vina_run(str(project_dir))

                self.assertTrue(prepared["ok"], prepared)
                command = prepared["command"]
                self.assertIn(expected_flag, command)
                self.assertIn("--autobox", command)
                self.assertEqual(prepared["metadata"]["output_file"], expected_output)
                self.assertNotIn("center_x", (project_dir / "runs" / "run_001" / "config_snapshot.txt").read_text(encoding="utf-8"))
                self.assertNotIn("exhaustiveness", (project_dir / "runs" / "run_001" / "config_snapshot.txt").read_text(encoding="utf-8"))
                if run_mode == "score_only":
                    self.assertNotIn("--out", command)
                    self.assertEqual(
                        prepared["metadata"]["pose_file"],
                        "runs/run_001/inputs/ligand.pdbqt",
                    )
                else:
                    self.assertEqual(
                        command[command.index("--out") + 1],
                        "runs/run_001/optimized.pdbqt",
                    )
                    self.assertEqual(
                        prepared["metadata"]["pose_file"],
                        "runs/run_001/optimized.pdbqt",
                    )

    def test_new_local_only_run_executes_input_score_then_local_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="local_only",
                autobox=True,
            )
            fake_vina = Path(temp_dir) / "vina.exe"
            fake_vina.write_bytes(b"stable mock vina binary")
            detection = self._vina_ok_result(str(fake_vina))
            calls: list[list[str]] = []

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                calls.append(command)
                is_baseline = "--score_only" in command
                log_text = VINA_EVALUATION_LOG.replace(
                    "-7.537",
                    "-6.500" if is_baseline else "-7.250",
                )
                Path(stdout_path).write_text(log_text, encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text(log_text, encoding="utf-8")
                if "--out" in command:
                    output = Path(cwd) / command[command.index("--out") + 1]
                    output.write_text(LIGAND_PDBQT, encoding="utf-8")
                return ManagedRunResult(pid=9000 + len(calls), exit_code=0)

            with (
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=detection,
                ),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_run,
                ),
            ):
                prepared = prepare_vina_run(str(project_dir))
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    prepared["run_id"],
                )

            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(
                prepared["metadata"]["execution_plan"]["kind"],
                "local_only_with_baseline",
            )
            self.assertEqual(len(prepared["commands"]), 2)
            self.assertIn("阶段 1：输入姿势评分", prepared["command_preview"])
            self.assertIn("阶段 2：局部优化", prepared["command_preview"])
            self.assertTrue(executed["ok"], executed)
            self.assertEqual(len(calls), 2)
            self.assertIn("--score_only", calls[0])
            self.assertNotIn("--out", calls[0])
            self.assertIn("--local_only", calls[1])
            self.assertEqual(
                calls[1][calls[1].index("--out") + 1],
                "runs/run_001/optimized.pdbqt",
            )
            for command in calls:
                self.assertIn("--autobox", command)
                self.assertEqual(
                    command[command.index("--config") + 1],
                    "runs/run_001/config_snapshot.txt",
                )

            metadata = executed["metadata"]
            self.assertEqual(metadata["execution_phases"]["input_score"]["status"], "finished")
            self.assertEqual(
                metadata["execution_phases"]["local_optimization"]["status"],
                "finished",
            )
            self.assertAlmostEqual(metadata["baseline_score_kcal_mol"], -6.5)
            self.assertAlmostEqual(metadata["primary_score_kcal_mol"], -7.25)
            self.assertAlmostEqual(metadata["score_change_kcal_mol"], -0.75)
            self.assertTrue(metadata["comparison_available"])
            self.assertEqual(
                metadata["output_normalization"]["status"],
                "not_required",
            )
            self.assertFalse(
                (
                    project_dir
                    / "runs"
                    / prepared["run_id"]
                    / "optimized.vina_raw.pdbqt"
                ).exists()
            )
            files = get_run_files_status(str(project_dir), prepared["run_id"])
            files_by_key = {item["key"]: item for item in files["files"]}
            self.assertTrue(files_by_key["baseline_log"]["exists"])
            self.assertTrue(files_by_key["baseline_stdout"]["exists"])
            self.assertTrue(files_by_key["baseline_stderr"]["exists"])

            analyzed = analyze_vina_run_results(str(project_dir), prepared["run_id"])
            self.assertTrue(analyzed["ok"], analyzed)
            comparison = analyzed["evaluation"]["comparison"]
            self.assertTrue(comparison["comparable"])
            self.assertAlmostEqual(comparison["input_score_kcal_mol"], -6.5)
            self.assertAlmostEqual(comparison["optimized_score_kcal_mol"], -7.25)
            self.assertAlmostEqual(comparison["delta_score_kcal_mol"], -0.75)
            self.assertEqual(comparison["delta_definition"], "optimized_minus_input")
            self.assertTrue(comparison["geometry"]["ok"])
            self.assertEqual(
                comparison["geometry"]["heavy_atom_rmsd_no_alignment_angstrom"],
                0.0,
            )
            input_pose = load_docking_pose_for_viewer(
                str(project_dir),
                prepared["run_id"],
                1,
                "input",
            )
            optimized_pose = load_docking_pose_for_viewer(
                str(project_dir),
                prepared["run_id"],
                1,
                "optimized",
            )
            self.assertTrue(input_pose["ok"], input_pose)
            self.assertTrue(optimized_pose["ok"], optimized_pose)
            self.assertEqual(
                input_pose["relative_path"],
                "runs/run_001/inputs/ligand.pdbqt",
            )
            self.assertEqual(
                optimized_pose["relative_path"],
                "runs/run_001/optimized.pdbqt",
            )
            self.assertEqual(input_pose["pose_kind"], "input")
            self.assertEqual(optimized_pose["pose_kind"], "optimized")
            report = build_markdown_report(str(project_dir), prepared["run_id"])
            self.assertTrue(report["ok"], report)
            self.assertIn("## 5. 优化前后评分比较", report["report_text"])
            self.assertIn("评分变化（优化后－输入）", report["report_text"])
            self.assertIn("## 6. 姿势位移", report["report_text"])
            self.assertIn("未对齐重原子 RMSD", report["report_text"])
            self.assertIn("## 7. 分阶段运行记录", report["report_text"])
            self.assertEqual(
                report["report_text"].count("Vina 输出标准化"),
                1,
            )
            self.assertNotIn("Mode 1", report["report_text"])
            baseline_log = project_dir / "runs" / prepared["run_id"] / "baseline_log.txt"
            baseline_log.write_text(
                baseline_log.read_text(encoding="utf-8") + "\nmodified\n",
                encoding="utf-8",
            )
            rejected = analyze_vina_run_results(str(project_dir), prepared["run_id"])
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "LOCAL_ONLY_ARTIFACT_HASH_MISMATCH",
            )

    def test_local_only_baseline_failure_never_starts_local_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="local_only",
                autobox=True,
            )
            calls: list[list[str]] = []

            def fail_baseline(
                command: list[str],
                _cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                calls.append(command)
                Path(stdout_path).write_text("baseline failed\n", encoding="utf-8")
                Path(stderr_path).write_text("mock baseline error\n", encoding="utf-8")
                Path(log_path).write_text("baseline failed\n", encoding="utf-8")
                return ManagedRunResult(pid=9001, exit_code=2)

            with (
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=self._vina_ok_result(),
                ),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fail_baseline,
                ),
            ):
                prepared = prepare_vina_run(str(project_dir))
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    prepared["run_id"],
                )

            self.assertFalse(executed["ok"])
            self.assertEqual(len(calls), 1)
            self.assertIn("--score_only", calls[0])
            self.assertEqual(executed["metadata"]["status"], "failed")
            self.assertEqual(
                executed["metadata"]["execution_phases"]["input_score"]["status"],
                "failed",
            )
            self.assertEqual(
                executed["metadata"]["execution_phases"]["local_optimization"]["status"],
                "skipped",
            )
            self.assertFalse(
                (project_dir / "runs" / prepared["run_id"] / "optimized.pdbqt").exists(),
            )

    def test_local_only_cancel_after_baseline_does_not_start_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="local_only",
                autobox=True,
            )
            fake_vina = Path(temp_dir) / "vina.exe"
            fake_vina.write_bytes(b"stable mock vina binary")
            detection = self._vina_ok_result(str(fake_vina))
            calls: list[list[str]] = []
            cancel_responses: list[dict[str, object]] = []

            def score_then_cancel(
                command: list[str],
                _cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                calls.append(command)
                Path(stdout_path).write_text(VINA_EVALUATION_LOG, encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text(VINA_EVALUATION_LOG, encoding="utf-8")
                cancel_responses.append(
                    cancel_vina_run(str(project_dir), "run_001"),
                )
                return ManagedRunResult(pid=9001, exit_code=0)

            with (
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=detection,
                ),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=score_then_cancel,
                ),
            ):
                prepared = prepare_vina_run(str(project_dir))
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    prepared["run_id"],
                )

            self.assertTrue(cancel_responses[0]["accepted"])
            self.assertTrue(executed["ok"], executed)
            self.assertEqual(executed["metadata"]["status"], "cancelled")
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                executed["metadata"]["execution_phases"]["input_score"]["status"],
                "cancelled",
            )
            self.assertEqual(
                executed["metadata"]["execution_phases"]["local_optimization"]["status"],
                "skipped",
            )
            self.assertFalse(
                (project_dir / "runs" / prepared["run_id"] / "optimized.pdbqt").exists(),
            )

    def test_prepared_legacy_local_only_run_keeps_single_stage_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(
                temp_dir,
                run_mode="local_only",
                autobox=True,
            )
            calls: list[list[str]] = []

            def fake_legacy_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                calls.append(command)
                Path(stdout_path).write_text(VINA_EVALUATION_LOG, encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text(VINA_EVALUATION_LOG, encoding="utf-8")
                output = Path(cwd) / command[command.index("--out") + 1]
                output.write_text(LIGAND_PDBQT, encoding="utf-8")
                return ManagedRunResult(pid=9001, exit_code=0)

            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            metadata_path = project_dir / "runs" / prepared["run_id"] / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.pop("execution_plan", None)
            metadata.pop("execution_phases", None)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with (
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=self._vina_ok_result(),
                ),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_legacy_run,
                ),
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    prepared["run_id"],
                )

            self.assertTrue(executed["ok"], executed)
            self.assertEqual(len(calls), 1)
            self.assertIn("--local_only", calls[0])
            self.assertNotIn("--score_only", calls[0])
            self.assertNotIn("baseline_log_file", executed["metadata"])

    def test_parse_real_vina_energy_block_for_both_evaluation_modes(self) -> None:
        for run_mode, output_generated in (("score_only", False), ("local_only", True)):
            with self.subTest(run_mode=run_mode):
                parsed = parse_vina_evaluation_text(VINA_EVALUATION_LOG, run_mode)

                self.assertTrue(parsed["ok"], parsed)
                self.assertEqual(parsed["run_mode"], run_mode)
                self.assertEqual(parsed["scoring_function"], "vina")
                self.assertAlmostEqual(parsed["primary_score_kcal_mol"], -7.537)
                self.assertEqual(parsed["grid"]["spacing_angstrom"], 0.375)
                self.assertEqual(parsed["output_pose_generated"], output_generated)
                terms = {item["key"]: item for item in parsed["energy_terms"]}
                self.assertAlmostEqual(
                    terms["final_intermolecular_energy"]["value_kcal_mol"],
                    -8.198,
                )
                self.assertEqual(terms["final_intermolecular_energy"]["term_number"], 1)
                self.assertAlmostEqual(
                    terms["torsional_free_energy"]["value_kcal_mol"],
                    0.661,
                )

    def test_duplicate_energy_term_is_rejected_instead_of_silently_overwritten(self) -> None:
        duplicate_log = (
            VINA_EVALUATION_LOG
            + "Estimated Free Energy of Binding : -7.000 (kcal/mol)\n"
        )

        parsed = parse_vina_evaluation_text(duplicate_log, "score_only")

        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "VINA_EVALUATION_TERM_DUPLICATE")

    def test_analysis_writes_evaluation_json_without_fabricating_scores(self) -> None:
        for run_mode in ("score_only", "local_only"):
            with self.subTest(run_mode=run_mode), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_finished_evaluation_run(
                    temp_dir,
                    run_mode=run_mode,
                )

                analyzed = analyze_vina_run_results(str(project_dir), run_id)
                loaded = load_vina_evaluation(str(project_dir), run_id)

                self.assertTrue(analyzed["ok"], analyzed)
                self.assertTrue(loaded["ok"], loaded)
                self.assertEqual(analyzed["scores"], [])
                self.assertEqual(analyzed["evaluation"]["run_mode"], run_mode)
                self.assertAlmostEqual(analyzed["primary_score_kcal_mol"], -7.537)
                if run_mode == "score_only":
                    self.assertEqual(
                        analyzed["evaluation"]["unbound_energy"],
                        {
                            "mode": "vina_default",
                            "value_kcal_mol": None,
                        },
                    )
                self.assertTrue(
                    (project_dir / "runs" / run_id / "evaluation.json").is_file(),
                )
                self.assertFalse((project_dir / "runs" / run_id / "scores.csv").exists())
                self.assertFalse((project_dir / "results" / "scores.csv").exists())
                metadata = json.loads(
                    (project_dir / "runs" / run_id / "metadata.json").read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    metadata["evaluation_file"],
                    f"runs/{run_id}/evaluation.json",
                )
                self.assertAlmostEqual(metadata["primary_score_kcal_mol"], -7.537)
                self.assertIsNone(metadata.get("best_affinity"))

    def test_explicit_unbound_energy_is_audited_in_evaluation_and_report(self) -> None:
        for value in (0.0, 5.0):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_finished_evaluation_run(
                    temp_dir,
                    unbound_energy=value,
                )

                analyzed = analyze_vina_run_results(str(project_dir), run_id)

                self.assertTrue(analyzed["ok"], analyzed)
                reference = analyzed["evaluation"]["unbound_energy"]
                self.assertEqual(reference["mode"], "explicit")
                self.assertEqual(reference["value_kcal_mol"], value)
                self.assertIn("结构准备", reference["comparison_warning"])

                report = build_markdown_report(str(project_dir), run_id)
                self.assertTrue(report["ok"], report)
                self.assertIn(
                    f"- 未结合态参考能量: {value:g} kcal/mol（显式）",
                    report["report_text"],
                )
                self.assertIn("只与输入、结构准备、评分函数和参考能量相同", report["report_text"])

    def test_explicit_unbound_energy_log_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(
                temp_dir,
                unbound_energy=5.0,
                logged_unbound_energy=0.0,
            )

            analyzed = analyze_vina_run_results(str(project_dir), run_id)

            self.assertFalse(analyzed["ok"])
            self.assertEqual(
                analyzed["error"]["code"],
                "VINA_EVALUATION_UNBOUND_REFERENCE_MISMATCH",
            )
            self.assertIn("snapshot=5.0", analyzed["error"]["raw_error"])
            self.assertIn("log=0.0", analyzed["error"]["raw_error"])

    def test_explicit_unbound_energy_requires_term_four_and_energy_balance(self) -> None:
        cases = (
            (
                "wrong-term-number",
                "(4) Unbound System's Energy",
                "(9) Unbound System's Energy",
                "VINA_EVALUATION_UNBOUND_TERM_INVALID",
            ),
            (
                "wrong-score-balance",
                "Estimated Free Energy of Binding   : -12.537",
                "Estimated Free Energy of Binding   : -11.537",
                "VINA_EVALUATION_ENERGY_BALANCE_MISMATCH",
            ),
        )
        for label, before, after, expected_code in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_finished_evaluation_run(
                    temp_dir,
                    unbound_energy=5.0,
                )
                log_path = project_dir / "runs" / run_id / "log.txt"
                log_path.write_text(
                    log_path.read_text(encoding="utf-8").replace(before, after),
                    encoding="utf-8",
                )
                metadata_path = project_dir / "runs" / run_id / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["artifacts"]["log"]["sha256"] = hashlib.sha256(
                    log_path.read_bytes(),
                ).hexdigest()
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                analyzed = analyze_vina_run_results(str(project_dir), run_id)

                self.assertFalse(analyzed["ok"])
                self.assertEqual(analyzed["error"]["code"], expected_code)

    def test_modern_score_only_run_rejects_log_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(
                temp_dir,
                unbound_energy=5.0,
            )
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            log_path = project_dir / "runs" / run_id / "log.txt"
            original_log = log_path.read_bytes()
            metadata["execution_vina"] = {"version": "1.2.7"}
            metadata["artifacts"] = {
                "log": {
                    "sha256": hashlib.sha256(original_log).hexdigest(),
                },
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            log_path.write_bytes(
                original_log.replace(b"-12.537", b"-11.537"),
            )

            analyzed = analyze_vina_run_results(str(project_dir), run_id)

            self.assertFalse(analyzed["ok"])
            self.assertEqual(
                analyzed["error"]["code"],
                "VINA_EVALUATION_ARTIFACT_HASH_MISMATCH",
            )

    def test_explicit_unbound_energy_requires_recorded_log_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(
                temp_dir,
                unbound_energy=5.0,
            )
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.pop("artifacts", None)
            metadata.pop("artifact_sha256", None)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            analyzed = analyze_vina_run_results(str(project_dir), run_id)

            self.assertFalse(analyzed["ok"])
            self.assertEqual(
                analyzed["error"]["code"],
                "VINA_EVALUATION_ARTIFACT_HASH_MISSING",
            )

    def test_tampered_evaluation_is_rejected_by_loader_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(
                temp_dir,
                unbound_energy=5.0,
            )
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            self.assertTrue(analyzed["ok"], analyzed)
            evaluation_path = project_dir / "runs" / run_id / "evaluation.json"
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
            evaluation["unbound_energy"]["value_kcal_mol"] = 777
            evaluation_path.write_text(
                json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            loaded = load_vina_evaluation(str(project_dir), run_id)
            report = build_markdown_report(str(project_dir), run_id)

            for payload in (loaded, report):
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    payload["error"]["code"],
                    "VINA_EVALUATION_ARTIFACT_HASH_MISMATCH",
                )

    def test_evaluation_rejects_boolean_explicit_unbound_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(
                temp_dir,
                unbound_energy=5.0,
            )
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            self.assertTrue(analyzed["ok"], analyzed)
            evaluation_path = project_dir / "runs" / run_id / "evaluation.json"
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
            evaluation["unbound_energy"]["value_kcal_mol"] = True
            evaluation_path.write_text(
                json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            new_hash = hashlib.sha256(evaluation_path.read_bytes()).hexdigest()
            metadata["artifacts"]["evaluation"]["sha256"] = new_hash
            metadata["artifact_sha256"]["evaluation"] = new_hash
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            loaded = load_vina_evaluation(str(project_dir), run_id)

            self.assertFalse(loaded["ok"])
            self.assertEqual(
                loaded["error"]["code"],
                "VINA_EVALUATION_SCHEMA_INVALID",
            )

    def test_recovery_and_workflow_viewer_preserve_evaluation_run_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(temp_dir)
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            self.assertTrue(analyzed["ok"], analyzed)

            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["runs"] = []
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            recovered = recover_project_state(str(project_dir))
            workflow = get_project_workflow_status(str(project_dir))

            self.assertTrue(recovered["ok"], recovered)
            self.assertTrue(workflow["ok"], workflow)
            summary = recovered["project"]["runs"][0]
            self.assertEqual(summary["run_mode"], "score_only")
            self.assertTrue(summary["autobox"])
            self.assertEqual(
                summary["pose_file"],
                f"runs/{run_id}/inputs/ligand.pdbqt",
            )
            self.assertEqual(
                summary["evaluation_file"],
                f"runs/{run_id}/evaluation.json",
            )
            self.assertAlmostEqual(summary["primary_score_kcal_mol"], -7.537)
            self.assertTrue(workflow["viewer"]["can_view_docking_output"])
            self.assertEqual(
                workflow["viewer"]["available_runs"][0]["run_mode"],
                "score_only",
            )
            self.assertEqual(
                workflow["viewer"]["available_runs"][0]["pose_file"],
                f"runs/{run_id}/inputs/ligand.pdbqt",
            )

    def test_workflow_recommendation_ignores_latest_run_from_another_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(temp_dir)
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            self.assertTrue(analyzed["ok"], analyzed)

            switched = update_vina_run_protocol(
                str(project_dir),
                "local_only",
                True,
                False,
            )
            self.assertTrue(switched["ok"], switched)

            workflow = get_project_workflow_status(str(project_dir))

            self.assertTrue(workflow["ok"], workflow)
            self.assertEqual(workflow["latest_run"]["run_mode"], "score_only")
            self.assertIsNone(workflow["latest_run_for_current_mode"])
            self.assertIn(
                "请生成 configs/vina_config.txt",
                workflow["next_recommended_action"],
            )
            self.assertNotIn(
                "可以查看结果",
                workflow["next_recommended_action"],
            )

            restored = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                False,
            )
            self.assertTrue(restored["ok"], restored)
            restored_workflow = get_project_workflow_status(str(project_dir))
            self.assertEqual(
                restored_workflow["latest_run_for_current_mode"]["run_id"],
                run_id,
            )

    def test_evaluation_report_does_not_fabricate_mode_ranking_or_rmsd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(temp_dir)
            analyzed = analyze_vina_run_results(str(project_dir), run_id)
            self.assertTrue(analyzed["ok"], analyzed)

            built = build_markdown_report(str(project_dir), run_id)
            exported = export_markdown_report(str(project_dir), run_id)

            self.assertTrue(built["ok"], built)
            self.assertTrue(exported["ok"], exported)
            report = built["report_text"]
            self.assertIn("# DockStart 当前姿势评分报告", report)
            self.assertIn("- 任务类型: score_only", report)
            self.assertIn("- 主要评分: -7.537 kcal/mol", report)
            self.assertIn("未执行全局构象搜索", report)
            self.assertNotIn("| Mode | Affinity", report)
            self.assertNotIn("RMSD l.b.", report)
            self.assertNotIn("RMSD u.b.", report)
            self.assertNotIn("Mode 1", report)
            self.assertIn(
                "Docking score 仅供结构结合趋势参考，不能替代实验验证。",
                report,
            )
            self.assertTrue(
                (project_dir / "runs" / run_id / "evaluation_report.md").is_file(),
            )
            self.assertTrue((project_dir / "reports" / "score_only_report.md").is_file())

    def test_score_only_result_sdf_export_is_explicitly_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(temp_dir)

            status = get_result_export_status(str(project_dir), run_id)

            self.assertFalse(status["ok"])
            self.assertEqual(
                status["error"]["code"],
                "SCORE_ONLY_SDF_NOT_APPLICABLE",
            )

    def test_score_only_viewer_uses_immutable_input_pose_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_finished_evaluation_run(temp_dir)
            self.assertFalse((project_dir / "runs" / run_id / "out.pdbqt").exists())

            poses = list_docking_poses(str(project_dir), run_id)
            pose = load_docking_pose_for_viewer(str(project_dir), run_id, 1)

            self.assertTrue(poses["ok"], poses)
            self.assertEqual(
                poses["relative_path"],
                f"runs/{run_id}/inputs/ligand.pdbqt",
            )
            self.assertEqual(len(poses["poses"]), 1)
            self.assertTrue(pose["ok"], pose)
            self.assertEqual(
                pose["relative_path"],
                f"runs/{run_id}/inputs/ligand.pdbqt",
            )
            self.assertIn("ATOM      1", pose["content"])


if __name__ == "__main__":
    unittest.main()
