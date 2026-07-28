from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core import hydrated  # noqa: E402
from dockstart_core.hydrated_run import (  # noqa: E402
    build_hydrated_markdown_report,
    load_hydrated_results,
    prepare_hydrated_run,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    HYDRATED_PROTOCOL_ID,
    HYDRATED_RETAINED_OUTPUT_NAME,
    HYDRATED_WATER_FREE_OUTPUT_NAME,
    HYDRATED_WATERS_MANIFEST_NAME,
    _metadata_protocol_id,
    _project_report_file,
    _project_scores_file,
    _run_report_filename,
    build_markdown_report,
    cancel_vina_run,
    create_project,
    execute_prepared_vina_run,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    recover_project_state,
    update_box_params,
)
from tests import test_hydrated_project_maps as maps_support  # noqa: E402


RECEPTOR_PDBQT = maps_support.RECEPTOR_PDBQT
STANDARD_LIGAND_PDBQT = maps_support.STANDARD_LIGAND_PDBQT


def _atom_line(
    serial: int,
    atom_name: str,
    x: float,
    y: float,
    z: float,
    atom_type: str,
) -> str:
    return (
        f"ATOM  {serial:5d} {atom_name:<4} UNL     1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00  0.00     0.000 {atom_type:<2}"
    )


def _pose(
    mode: int,
    affinity: float,
    *,
    water_serial: int,
    water_coordinate: tuple[float, float, float],
    water_is_branch_endpoint: bool = False,
) -> list[str]:
    branch_endpoint = water_serial if water_is_branch_endpoint else 2
    return [
        f"MODEL {mode}",
        f"REMARK VINA RESULT: {affinity:.3f} 0.000 0.000",
        "ROOT",
        _atom_line(1, "C1", -2.0, -2.0, -2.0, "C"),
        "ENDROOT",
        f"BRANCH   1   {branch_endpoint}",
        _atom_line(2, "C2", -3.0, -3.0, -3.0, "C"),
        _atom_line(
            water_serial,
            "WAT",
            water_coordinate[0],
            water_coordinate[1],
            water_coordinate[2],
            "W",
        ),
        f"ENDBRANCH   1   {branch_endpoint}",
        "TORSDOF 1",
        "ENDMDL",
    ]


def _valid_hydrated_output() -> str:
    return (
        "\n".join(
            [
                *_pose(
                    1,
                    -8.0,
                    water_serial=10,
                    water_coordinate=(2.0, 3.0, 4.0),
                ),
                *_pose(
                    2,
                    -7.5,
                    water_serial=11,
                    water_coordinate=(0.0, 3.0, 4.0),
                ),
            ]
        )
        + "\n"
    )


def _invalid_hydrated_output() -> str:
    return (
        "\n".join(
            _pose(
                1,
                -8.0,
                water_serial=10,
                water_coordinate=(2.0, 3.0, 4.0),
                water_is_branch_endpoint=True,
            )
        )
        + "\n"
    )


def _vina_log() -> str:
    return (
        "mode | affinity | dist from best mode\n"
        "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
        "-----+------------+----------+----------\n"
        "   1       -8.000      0.000      0.000\n"
        "   2       -7.500      0.000      0.000\n"
    )


class HydratedProjectIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tool_root = self.base / "tools"
        self.tool_root.mkdir()
        self.python_file = self.tool_root / "python.exe"
        self.rdkit_module = self.tool_root / "rdkit_init.py"
        self.meeko_module = self.tool_root / "meeko_init.py"
        self.autogrid_file = self.tool_root / "autogrid4.exe"
        self.vina_file = self.tool_root / "vina.exe"
        self.python_file.write_bytes(b"python-v1")
        self.rdkit_module.write_bytes(b"rdkit-v1")
        self.meeko_module.write_bytes(b"meeko-v1")
        self.autogrid_file.write_bytes(b"autogrid-v1")
        self.vina_file.write_bytes(b"vina-v1.2.7")
        self.project_counter = 0

    def _metadata(self) -> dict[str, object]:
        return {
            "scoring_protocol": "ad4_maps",
            "protocol_id": HYDRATED_PROTOCOL_ID,
            "run_mode": "dock",
            "docking_protocol": {
                "protocol_id": HYDRATED_PROTOCOL_ID,
                "mode": "rigid",
                "stability": "experimental",
            },
        }

    def _preparation_tools(self) -> dict[str, object]:
        return {
            "python": {
                "status": "ok",
                "version": "Python 3.11.15",
                "path": str(self.python_file),
                "source": "bundled",
            },
            "rdkit": {
                "status": "ok",
                "version": "2026.3.3-test",
                "capabilities": {"import": {"status": "ok"}},
            },
            "meeko": {
                "status": "ok",
                "version": "0.7.1-test",
                "capabilities": {
                    "import": {"status": "ok"},
                    "ligand_preparation": {"status": "ok"},
                },
            },
        }

    def _autogrid_detection(self) -> ToolCheckResult:
        return ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.7",
            path=str(self.autogrid_file),
            message="mock AutoGrid4",
            source="configured",
        )

    def _vina_detection(self) -> ToolCheckResult:
        def feature(option: str) -> dict[str, object]:
            return {
                "option": f"--{option}",
                "status": "supported",
                "supported": True,
                "advertised": True,
            }

        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.7",
            path=str(self.vina_file),
            message="mock Vina",
            source="configured",
            capabilities={
                "status": "ok",
                "source": "help_advanced",
                "checked": True,
                "version": "1.2.7",
                "features": {
                    "maps": feature("maps"),
                    "write_maps": feature("write_maps"),
                    "no_refine": feature("no_refine"),
                    "force_even_voxels": feature("force_even_voxels"),
                },
            },
        )

    @staticmethod
    def _project_data(project_dir: Path) -> dict[str, object]:
        return json.loads(
            (project_dir / "project.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def _metadata_data(project_dir: Path, run_id: str) -> dict[str, object]:
        return json.loads(
            (project_dir / "runs" / run_id / "metadata.json").read_text(
                encoding="utf-8"
            )
        )

    @staticmethod
    def _write_metadata(
        project_dir: Path,
        run_id: str,
        metadata: dict[str, object],
    ) -> None:
        (project_dir / "runs" / run_id / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _autogrid_runner(
        self,
        executable: str,
        gpf_file: str,
        log_file: str,
        working_directory: str | Path,
        **_kwargs: object,
    ) -> dict[str, object]:
        root = Path(working_directory)
        gpf = (root / gpf_file).read_text(encoding="utf-8")
        ligand_types = next(
            line.split()[1:]
            for line in gpf.splitlines()
            if line.startswith("ligand_types ")
        )
        points = tuple(
            int(value)
            for value in next(
                line.split()[1:]
                for line in gpf.splitlines()
                if line.startswith("npts ")
            )
        )
        spacing = float(
            next(
                line.split()[1]
                for line in gpf.splitlines()
                if line.startswith("spacing ")
            )
        )
        center = tuple(
            float(value)
            for value in next(
                line.split()[1:]
                for line in gpf.splitlines()
                if line.startswith("gridcenter ")
            )
        )
        names = [
            "receptor.maps.fld",
            *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
            "receptor.e.map",
            "receptor.d.map",
        ]
        point_count = math.prod(value + 1 for value in points)
        for name in names:
            path = root / name
            if name == "receptor.maps.fld":
                path.write_text("validated fld\n", encoding="ascii")
                continue
            value = (
                -1.0
                if name == "receptor.OA.map"
                else -0.5
                if name == "receptor.HD.map"
                else -0.1
            )
            path.write_text(
                maps_support._map_text(
                    [value] * point_count,
                    spacing=spacing,
                    nelements=points,
                    center=center,
                ),
                encoding="ascii",
            )
        (root / log_file).write_text(
            "Successful Completion\n",
            encoding="utf-8",
        )
        return {
            "ok": True,
            "command": [executable, "-p", gpf_file, "-l", log_file],
            "exit_code": 0,
            "stdout": "AutoGrid complete\n",
            "stderr": "",
            "error": "",
        }

    def _create_ready_project(self) -> Path:
        self.project_counter += 1
        name = f"hydrated_integration_{self.project_counter}"
        created = create_project(name, str(self.base))
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])

        receptor_source = self.base / f"{name}_receptor.pdbqt"
        ligand_source = self.base / f"{name}_ligand.pdbqt"
        receptor_source.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand_source.write_text(STANDARD_LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(
                str(project_dir),
                str(receptor_source),
            )["ok"]
        )
        self.assertTrue(
            import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
            )["ok"]
        )
        self.assertTrue(
            update_box_params(
                str(project_dir),
                {
                    "center_x": 1,
                    "center_y": 2,
                    "center_z": 3,
                    "size_x": 8,
                    "size_y": 8,
                    "size_z": 8,
                },
            )["ok"]
        )

        raw = project_dir / "raw" / "ligand.sdf"
        raw.write_text(maps_support._sdf(), encoding="utf-8")
        project_json = project_dir / "project.json"
        project_payload = self._project_data(project_dir)
        project_payload["ligand"]["raw_file"] = "raw/ligand.sdf"
        project_payload["docking_protocol"] = {
            "engine": "vina",
            "protocol_id": "rigid_single",
            "receptor_mode": "rigid",
        }
        project_json.write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        preparation_runner = maps_support._PreparationRunner(
            self.rdkit_module,
            self.meeko_module,
        )
        with (
            patch(
                "dockstart_core.hydrated._tool_status",
                return_value=self._preparation_tools(),
            ),
            patch(
                "dockstart_core.hydrated.meeko_adapter.run_preparation_command",
                side_effect=preparation_runner,
            ),
        ):
            prepared = hydrated.prepare_hydrated_ligand(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)

        with patch(
            "dockstart_core.hydrated.autogrid_adapter.detect",
            return_value=self._autogrid_detection(),
        ):
            generated = hydrated.generate_hydrated_maps(
                str(project_dir),
                {
                    "spacing": 1.0,
                    "grid_points": {"x": 2, "y": 2, "z": 2},
                },
                runner=self._autogrid_runner,
            )
        self.assertTrue(generated["ok"], generated)
        return project_dir

    def _prepare_run(self, project_dir: Path) -> dict[str, object]:
        with (
            patch(
                "dockstart_core.hydrated.autogrid_adapter.detect",
                return_value=self._autogrid_detection(),
            ),
            patch(
                "dockstart_core.hydrated_run.vina_adapter.detect",
                return_value=self._vina_detection(),
            ),
        ):
            prepared = prepare_hydrated_run(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return prepared

    def _execute(
        self,
        project_dir: Path,
        run_id: str,
        *,
        output_text: str | None = None,
        started: Mock | None = None,
    ) -> dict[str, object]:
        def fake_run(
            command: list[str],
            cwd: str | Path,
            stdout_path: str | Path,
            stderr_path: str | Path,
            log_path: str | Path,
            **_kwargs: object,
        ) -> ManagedRunResult:
            if started is not None:
                started(command)
            log_text = _vina_log()
            Path(stdout_path).write_text(log_text, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            Path(log_path).write_text(log_text, encoding="utf-8")
            output_path = Path(cwd) / command[command.index("--out") + 1]
            output_path.write_text(
                output_text
                if output_text is not None
                else _valid_hydrated_output(),
                encoding="utf-8",
            )
            return ManagedRunResult(pid=4242, exit_code=0)

        with (
            patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_detection(),
            ),
            patch(
                "dockstart_core.project.vina_adapter.run_managed",
                side_effect=fake_run,
            ),
        ):
            return execute_prepared_vina_run(str(project_dir), run_id)

    def test_hydrated_protocol_identity_is_not_collapsed_to_plain_ad4(
        self,
    ) -> None:
        metadata = self._metadata()

        self.assertEqual(_metadata_protocol_id(metadata), HYDRATED_PROTOCOL_ID)
        self.assertEqual(
            _project_scores_file(metadata),
            "results/hydrated_ad4_scores.csv",
        )
        self.assertEqual(
            _project_report_file(metadata),
            "reports/hydrated_ad4_docking_report.md",
        )
        self.assertEqual(
            _run_report_filename(metadata),
            "hydrated_docking_report.md",
        )

    def test_prepare_freezes_active_hydrated_ligand_and_final_maps(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        active_status = hydrated.get_status(str(project_dir))

        prepared = self._prepare_run(project_dir)

        run_id = str(prepared["run_id"])
        metadata = prepared["metadata"]
        run_root = project_dir / "runs" / run_id
        ligand_snapshot = run_root / "inputs" / "ligand.pdbqt"
        ligand_manifest = (
            run_root / "inputs" / "hydrated" / "ligand_manifest.json"
        )
        maps_manifest = run_root / "inputs" / "maps" / "manifest.json"
        self.assertEqual(
            ligand_snapshot.read_bytes(),
            (
                project_dir
                / active_status["manifest"]["outputs"]["hydrated_pdbqt"][
                    "path"
                ]
            ).read_bytes(),
        )
        self.assertEqual(
            ligand_manifest.read_bytes(),
            (
                project_dir / active_status["active_ligand_manifest"]
            ).read_bytes(),
        )
        self.assertEqual(
            maps_manifest.read_bytes(),
            (
                project_dir / active_status["active_maps_manifest"]
            ).read_bytes(),
        )
        frozen_names = {
            path.name
            for path in (run_root / "inputs" / "maps").iterdir()
            if path.is_file()
        }
        self.assertIn("receptor.W.map", frozen_names)
        self.assertIn("--maps", metadata["command"])
        self.assertEqual(
            metadata["command"][metadata["command"].index("--scoring") + 1],
            "ad4",
        )
        self.assertNotIn("--receptor", metadata["command"])
        self.assertEqual(
            metadata["hydrated"]["ligand_preparation_manifest_snapshot"],
            f"runs/{run_id}/inputs/hydrated/ligand_manifest.json",
        )
        self.assertEqual(
            metadata["hydrated"]["maps_manifest_snapshot"],
            f"runs/{run_id}/inputs/maps/manifest.json",
        )

    def test_execute_generates_raw_retained_water_free_and_manifest(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])

        executed = self._execute(project_dir, run_id)

        self.assertTrue(executed["ok"], executed)
        metadata = executed["metadata"]
        self.assertEqual(metadata["status"], "finished")
        self.assertEqual(metadata["stage"], "finished")
        run_root = project_dir / "runs" / run_id
        expected = (
            "out.pdbqt",
            HYDRATED_RETAINED_OUTPUT_NAME,
            HYDRATED_WATER_FREE_OUTPUT_NAME,
            HYDRATED_WATERS_MANIFEST_NAME,
        )
        for filename in expected:
            self.assertTrue((run_root / filename).is_file(), filename)
            self.assertGreater((run_root / filename).stat().st_size, 0)
        self.assertEqual(
            metadata["output_file"],
            f"runs/{run_id}/out.pdbqt",
        )
        self.assertEqual(
            metadata["pose_file"],
            f"runs/{run_id}/{HYDRATED_RETAINED_OUTPUT_NAME}",
        )
        postprocess = metadata["hydrated_postprocess"]
        self.assertEqual(postprocess["status"], "finished")
        self.assertEqual(postprocess["summary"]["pose_count"], 2)
        self.assertEqual(postprocess["summary"]["strong_water_count"], 2)
        self.assertEqual(postprocess["summary"]["retained_water_count"], 2)
        self.assertIn("REMARK VINA RESULT: -8.000", (run_root / "out.pdbqt").read_text(encoding="utf-8"))
        self.assertIn(
            "REMARK DOCKSTART WATER",
            (run_root / HYDRATED_RETAINED_OUTPUT_NAME).read_text(
                encoding="utf-8"
            ),
        )
        self.assertNotIn(
            " W\n",
            (run_root / HYDRATED_WATER_FREE_OUTPUT_NAME).read_text(
                encoding="utf-8"
            ),
        )

    def test_postprocess_failure_is_terminal_and_keeps_raw_output(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])

        executed = self._execute(
            project_dir,
            run_id,
            output_text=_invalid_hydrated_output(),
        )

        self.assertFalse(executed["ok"])
        self.assertEqual(executed["metadata"]["status"], "failed")
        self.assertEqual(
            executed["metadata"]["stage"],
            "postprocess_failed",
        )
        self.assertEqual(
            executed["metadata"]["hydrated_postprocess"]["status"],
            "failed",
        )
        self.assertEqual(
            executed["error"]["code"],
            "HYDRATED_PDBQT_WATER_BRANCH_ENDPOINT",
        )
        raw = project_dir / "runs" / run_id / "out.pdbqt"
        self.assertTrue(raw.is_file())
        self.assertEqual(raw.read_text(encoding="utf-8"), _invalid_hydrated_output())
        self.assertFalse(
            (project_dir / "runs" / run_id / HYDRATED_RETAINED_OUTPUT_NAME).exists()
        )

    def test_tampered_frozen_water_map_or_manifest_blocks_execution(
        self,
    ) -> None:
        cases = ("water_map", "maps_manifest", "ligand_manifest")
        for case in cases:
            with self.subTest(case=case):
                project_dir = self._create_ready_project()
                prepared = self._prepare_run(project_dir)
                run_id = str(prepared["run_id"])
                if case == "water_map":
                    target = (
                        project_dir
                        / "runs"
                        / run_id
                        / "inputs"
                        / "maps"
                        / "receptor.W.map"
                    )
                elif case == "maps_manifest":
                    target = (
                        project_dir
                        / "runs"
                        / run_id
                        / "inputs"
                        / "maps"
                        / "manifest.json"
                    )
                else:
                    target = (
                        project_dir
                        / "runs"
                        / run_id
                        / "inputs"
                        / "hydrated"
                        / "ligand_manifest.json"
                    )
                target.write_bytes(target.read_bytes() + b"\n")
                started = Mock()

                executed = self._execute(
                    project_dir,
                    run_id,
                    started=started,
                )

                self.assertFalse(executed["ok"], executed)
                started.assert_not_called()
                self.assertIn(
                    executed["error"]["code"],
                    {
                        "RUN_AD4_MAP_HASH_MISMATCH",
                        "RUN_AD4_MANIFEST_HASH_MISMATCH",
                        "RUN_SNAPSHOT_HASH_MISMATCH",
                    },
                )

    def test_ligand_manifest_tampering_during_or_after_run_is_rejected(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        target = (
            project_dir
            / "runs"
            / run_id
            / "inputs"
            / "hydrated"
            / "ligand_manifest.json"
        )

        def tamper_during_run(_command: list[str]) -> None:
            target.write_bytes(target.read_bytes() + b"\n")

        executed = self._execute(
            project_dir,
            run_id,
            started=Mock(side_effect=tamper_during_run),
        )

        self.assertFalse(executed["ok"], executed)
        self.assertEqual(executed["metadata"]["status"], "failed")
        self.assertEqual(executed["metadata"]["stage"], "failed")
        self.assertEqual(
            executed["error"]["code"],
            "RUN_HYDRATED_POST_HASH_MISMATCH",
        )
        self.assertEqual(
            executed["metadata"]["input_snapshot_integrity"]["status"],
            "failed",
        )

        clean_project = self._create_ready_project()
        clean_prepared = self._prepare_run(clean_project)
        clean_run_id = str(clean_prepared["run_id"])
        clean_executed = self._execute(clean_project, clean_run_id)
        self.assertTrue(clean_executed["ok"], clean_executed)
        clean_manifest = (
            clean_project
            / "runs"
            / clean_run_id
            / "inputs"
            / "hydrated"
            / "ligand_manifest.json"
        )
        clean_manifest.write_bytes(clean_manifest.read_bytes() + b"\n")

        results = load_hydrated_results(str(clean_project), clean_run_id)
        report = build_hydrated_markdown_report(
            str(clean_project),
            clean_run_id,
        )

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "RUN_HYDRATED_POST_HASH_MISMATCH",
        )
        self.assertFalse(report["ok"], report)
        self.assertEqual(
            report["error"]["code"],
            "RUN_HYDRATED_POST_HASH_MISMATCH",
        )

    def test_cancel_during_postprocessing_is_not_accepted(self) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        metadata = self._metadata_data(project_dir, run_id)
        metadata.update(
            {
                "status": "running",
                "stage": "postprocessing",
                "started_at": "2026-07-29T00:00:00+00:00",
                "vina_finished_at": "2026-07-29T00:01:00+00:00",
                "pid": None,
            }
        )
        self._write_metadata(project_dir, run_id, metadata)

        cancelled = cancel_vina_run(str(project_dir), run_id)

        self.assertTrue(cancelled["ok"], cancelled)
        self.assertFalse(cancelled["accepted"])
        self.assertFalse(cancelled["cancelled"])
        self.assertEqual(cancelled["stage"], "postprocessing")
        after = self._metadata_data(project_dir, run_id)
        self.assertEqual(after["status"], "running")
        self.assertEqual(after["stage"], "postprocessing")

    def test_recovery_converges_dead_postprocessing_to_failed(self) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        run_root = project_dir / "runs" / run_id
        raw = run_root / "out.pdbqt"
        raw.write_text(_valid_hydrated_output(), encoding="utf-8")
        metadata = self._metadata_data(project_dir, run_id)
        metadata.update(
            {
                "status": "running",
                "stage": "postprocessing",
                "started_at": "2026-07-29T00:00:00+00:00",
                "vina_finished_at": "2026-07-29T00:01:00+00:00",
                "pid": None,
                "executor_pid": None,
            }
        )
        self._write_metadata(project_dir, run_id, metadata)

        recovered = recover_project_state(str(project_dir))

        self.assertTrue(recovered["ok"], recovered)
        self.assertIn(run_id, recovered["recovered_runs"])
        after = self._metadata_data(project_dir, run_id)
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["stage"], "postprocess_failed")
        self.assertEqual(
            after["hydrated_postprocess"]["error"]["code"],
            "HYDRATED_POSTPROCESS_INTERRUPTED",
        )
        self.assertTrue(raw.is_file())
        self.assertEqual(raw.read_text(encoding="utf-8"), _valid_hydrated_output())

    def test_results_and_reports_keep_raw_affinity_and_water_semantics(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)

        results = load_hydrated_results(str(project_dir), run_id)
        report = build_hydrated_markdown_report(str(project_dir), run_id)
        generic_report = build_markdown_report(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(
            [item["raw_affinity_kcal_mol"] for item in results["modes"]],
            [-8.0, -7.5],
        )
        self.assertEqual(
            [item["affinity_kcal_mol"] for item in results["modes"]],
            [-8.0, -7.5],
        )
        self.assertNotIn("processed_affinity_kcal_mol", results["modes"][0])
        self.assertEqual(results["water_summary"]["strong_water_count"], 2)
        self.assertEqual(results["water_summary"]["weak_water_count"], 0)
        self.assertEqual(results["water_summary"]["displaced_water_count"], 0)
        self.assertFalse(results["score_semantics"]["postprocessed_affinity"])
        self.assertEqual(
            results["score_semantics"]["ranking"],
            "within_run_pose_ranking_only",
        )

        self.assertTrue(report["ok"], report)
        self.assertIn("Raw AD4 affinity", report["report_text"])
        self.assertIn("处理后评分未计算", report["report_text"])
        self.assertIn("| 1 | -8.0 | 1 | 0 | 0 |", report["report_text"])
        self.assertIn("不用于虚拟筛选", report["report_text"])
        self.assertTrue(generic_report["ok"], generic_report)
        self.assertEqual(
            generic_report["report_text"],
            report["report_text"],
        )


if __name__ == "__main__":
    unittest.main()
