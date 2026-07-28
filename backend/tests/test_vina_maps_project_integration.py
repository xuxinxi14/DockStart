from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import (  # noqa: E402
    _validate_execute_prerequisites,
    analyze_vina_run_results,
    build_markdown_report,
    build_vina_config_text,
    execute_prepared_vina_run,
    generate_vina_config,
    prepare_vina_run,
    update_vina_run_protocol,
    validate_config_prerequisites,
    validate_run_prerequisites,
)
from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.vina_maps import generate_maps  # noqa: E402
from dockstart_core.autogrid import set_scoring_protocol  # noqa: E402
from tests import test_vina_maps as maps_test_support  # noqa: E402


class VinaMapsProjectIntegrationTests(unittest.TestCase):
    def _prepared_maps_project(
        self,
        temp_dir: str,
    ) -> tuple[
        Path,
        Path,
        maps_test_support.VinaMapsWorkflowTests,
    ]:
        helper = maps_test_support.VinaMapsWorkflowTests(
            methodName="runTest"
        )
        project_dir = helper._create_project(temp_dir, "project_integration")
        executable = Path(temp_dir) / "vina.exe"
        executable.write_bytes(b"mock vina 1.2.7 project integration")
        with patch(
            "dockstart_core.vina_maps.vina_adapter.detect",
            return_value=helper._vina_result(executable),
        ):
            generated = generate_maps(
                str(project_dir),
                runner=helper._runner,
            )
        self.assertTrue(generated["ok"], generated)
        return project_dir, executable, helper

    @staticmethod
    def _load_metadata(project_dir: Path, run_id: str) -> dict[str, object]:
        return json.loads(
            (project_dir / "runs" / run_id / "metadata.json").read_text(
                encoding="utf-8"
            )
        )

    def test_config_command_and_immutable_snapshot_use_maps_without_receptor_grid_options(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                generated_config = generate_vina_config(str(project_dir))
                prerequisites = validate_run_prerequisites(str(project_dir))
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(generated_config["ok"], generated_config)
            self.assertTrue(prerequisites["ok"], prerequisites)
            self.assertTrue(prepared["ok"], prepared)
            self.assertEqual(prerequisites["grid_source"], "precomputed_maps")
            command = prepared["command"]
            self.assertEqual(command[command.index("--scoring") + 1], "vina")
            self.assertIn("--maps", command)
            self.assertNotIn("--receptor", command)

            run_id = prepared["run_id"]
            config_text = (
                project_dir / "runs" / run_id / "config_snapshot.txt"
            ).read_text(encoding="utf-8")
            for forbidden in (
                "receptor =",
                "center_x =",
                "center_y =",
                "center_z =",
                "size_x =",
                "size_y =",
                "size_z =",
                "spacing =",
                "no_refine =",
                "force_even_voxels =",
            ):
                self.assertNotIn(forbidden, config_text)
            self.assertIn(
                f"ligand = runs/{run_id}/inputs/ligand.pdbqt",
                config_text,
            )
            self.assertTrue(
                (
                    project_dir
                    / "runs"
                    / run_id
                    / "inputs"
                    / "receptor.pdbqt"
                ).is_file()
            )
            self.assertTrue(
                (
                    project_dir
                    / "runs"
                    / run_id
                    / "inputs"
                    / "maps"
                    / "manifest.json"
                ).is_file()
            )
            metadata = prepared["metadata"]
            self.assertTrue(metadata["grid_execution"]["grid_only"])
            self.assertFalse(
                metadata["grid_execution"]["receptor_argument_used"]
            )
            self.assertTrue(
                metadata["grid_execution"]["no_refine_equivalent"]
            )

    def test_execute_preflight_rejects_tampered_map_and_manifest_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                self.assertTrue(generate_vina_config(str(project_dir))["ok"])
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]
            metadata = self._load_metadata(project_dir, run_id)
            valid = _validate_execute_prerequisites(
                str(project_dir),
                run_id,
                metadata,
            )
            self.assertTrue(valid["ok"], valid)

            first_map = metadata["snapshots"]["vina_maps"]["files"][0]
            map_path = project_dir / first_map["relative_path"]
            original = map_path.read_bytes()
            map_path.write_bytes(original + b"\n0.0\n")
            corrupted = _validate_execute_prerequisites(
                str(project_dir),
                run_id,
                metadata,
            )
            self.assertFalse(corrupted["ok"])
            self.assertEqual(
                corrupted["error"]["code"],
                "RUN_VINA_MAP_HASH_MISMATCH",
            )
            map_path.write_bytes(original)

            manifest_record = metadata["snapshots"]["vina_maps"]["manifest"]
            manifest_path = project_dir / manifest_record["relative_path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["scoring_function"] = "vinardo"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_record["sha256"] = hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest()
            rebound = _validate_execute_prerequisites(
                str(project_dir),
                run_id,
                metadata,
            )
            self.assertFalse(rebound["ok"])
            self.assertEqual(
                rebound["error"]["code"],
                "RUN_VINA_MANIFEST_BINDING_MISMATCH",
            )

    def test_maps_mode_rejects_non_dock_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            rejected = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                False,
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(
                rejected["error"]["code"],
                "VINA_MAPS_RUN_MODE_UNSUPPORTED",
            )

            project_file = project_dir / "project.json"
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            payload["docking_protocol"]["run_mode"] = "local_only"
            project_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                invalid = validate_config_prerequisites(str(project_dir))
            self.assertFalse(invalid["ok"])
            self.assertEqual(
                invalid["error"]["code"],
                "VINA_MAPS_RUN_MODE_UNSUPPORTED",
            )

    def test_ad4_round_trip_cannot_reactivate_stale_vina_grid_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, _executable, _helper = (
                self._prepared_maps_project(temp_dir)
            )
            ad4 = set_scoring_protocol(str(project_dir), "ad4_maps")
            self.assertTrue(ad4["ok"], ad4)
            vina = set_scoring_protocol(str(project_dir), "vina")
            self.assertTrue(vina["ok"], vina)
            preview = build_vina_config_text(str(project_dir))
            self.assertTrue(preview["ok"], preview)
            self.assertEqual(preview["grid_source"], "receptor")
            self.assertIn(
                "receptor = prepared/receptor.pdbqt",
                preview["config_text"],
            )
            self.assertNotIn("--maps", preview["config_text"])

    def test_execute_marks_result_failed_when_map_changes_during_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                self.assertTrue(generate_vina_config(str(project_dir))["ok"])
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]
            first_map = prepared["metadata"]["snapshots"]["vina_maps"][
                "files"
            ][0]

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                Path(stdout_path).write_text(
                    "mock docking completed\n",
                    encoding="utf-8",
                )
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text(
                    "mock docking completed\n",
                    encoding="utf-8",
                )
                output = (
                    Path(cwd)
                    / command[command.index("--out") + 1]
                )
                output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                changed = Path(cwd) / first_map["relative_path"]
                changed.write_bytes(changed.read_bytes() + b"\n0.0\n")
                return ManagedRunResult(pid=4242, exit_code=0)

            with (
                patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=helper._vina_result(executable),
                ),
                patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_run,
                ),
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    run_id,
                )
            self.assertFalse(executed["ok"])
            self.assertEqual(executed["metadata"]["status"], "failed")
            self.assertEqual(
                executed["error"]["code"],
                "RUN_VINA_MAPS_POST_HASH_MISMATCH",
            )
            self.assertEqual(
                executed["metadata"]["input_snapshot_integrity"]["status"],
                "failed",
            )

    def test_execute_marks_maps_result_failed_when_vina_binary_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                self.assertTrue(generate_vina_config(str(project_dir))["ok"])
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                Path(stdout_path).write_text("done\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text("done\n", encoding="utf-8")
                (Path(cwd) / command[command.index("--out") + 1]).write_text(
                    "MODEL 1\nENDMDL\n",
                    encoding="utf-8",
                )
                executable.write_bytes(b"changed vina binary")
                return ManagedRunResult(pid=4243, exit_code=0)

            with (
                patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=helper._vina_result(executable),
                ),
                patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_run,
                ),
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    run_id,
                )
            self.assertFalse(executed["ok"])
            self.assertEqual(executed["metadata"]["status"], "failed")
            self.assertEqual(
                executed["error"]["code"],
                "RUN_VINA_BINARY_CHANGED",
            )

    def test_report_discloses_grid_only_no_refine_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, executable, helper = self._prepared_maps_project(
                temp_dir
            )
            with patch(
                "dockstart_core.vina_maps.vina_adapter.detect",
                return_value=helper._vina_result(executable),
            ):
                self.assertTrue(generate_vina_config(str(project_dir))["ok"])
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **_kwargs: object,
            ) -> ManagedRunResult:
                log_text = (
                    "mode | affinity | dist from best mode\n"
                    "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
                    "-----+------------+----------+----------\n"
                    "   1       -7.4          0          0\n"
                )
                Path(stdout_path).write_text(log_text, encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text(log_text, encoding="utf-8")
                (Path(cwd) / command[command.index("--out") + 1]).write_text(
                    "MODEL 1\nENDMDL\n",
                    encoding="utf-8",
                )
                return ManagedRunResult(pid=4244, exit_code=0)

            with (
                patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=helper._vina_result(executable),
                ),
                patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=fake_run,
                ),
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    run_id,
                )
            self.assertTrue(executed["ok"], executed)
            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )
            self.assertTrue(analyzed["ok"], analyzed)
            report = build_markdown_report(str(project_dir), run_id)
            self.assertTrue(report["ok"], report)
            self.assertIn(
                "Vina / Vinardo（预计算 maps，grid-only）",
                report["report_text"],
            )
            self.assertIn(
                "grid-only / no-refine 等价",
                report["report_text"],
            )
            self.assertIn(
                "受体 PDBQT 仅作为来源与 SHA256 溯源快照",
                report["report_text"],
            )


if __name__ == "__main__":
    unittest.main()
