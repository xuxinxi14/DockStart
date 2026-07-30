from __future__ import annotations

import hashlib
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
from dockstart_core.autogrid import (  # noqa: E402
    compute_requested_box_grid_coverage,
)
from dockstart_core.hydrated_run import (  # noqa: E402
    _vina_score_table_numeric_interval,
    build_hydrated_markdown_report,
    get_hydrated_run_preflight,
    load_hydrated_results,
    prepare_hydrated_run,
    vina_affinity_serialization_matches,
    vina_rmsd_serialization_matches,
    vina_score_table_token_is_canonical,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    HYDRATED_PROTOCOL_ID,
    HYDRATED_RETAINED_OUTPUT_NAME,
    HYDRATED_WATER_FREE_OUTPUT_NAME,
    HYDRATED_WATERS_MANIFEST_NAME,
    _metadata_protocol_id,
    _hydrated_frozen_grid_coverage,
    _project_report_file,
    _project_scores_file,
    _run_report_filename,
    analyze_vina_run_results,
    build_markdown_report,
    cancel_vina_run,
    create_project,
    execute_prepared_vina_run,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    recover_project_state,
    update_box_params,
    update_vina_params,
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


def _single_hydrated_output(*, affinity: float = -8.0) -> str:
    return (
        "\n".join(
            _pose(
                1,
                affinity,
                water_serial=10,
                water_coordinate=(2.0, 3.0, 4.0),
            )
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


def _vina_log(
    *,
    verbosity: int = 1,
    rows: tuple[tuple[int, float, float, float], ...] | None = None,
) -> str:
    effective_rows = rows or (
        (1, -8.0, 0.0, 0.0),
        (2, -7.5, 0.0, 0.0),
    )
    number_format = ".4f" if verbosity == 2 else ".4g"
    return (
        "mode | affinity | dist from best mode\n"
        "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
        "-----+------------+----------+----------\n"
        + "".join(
            (
                f"{mode:4d} "
                f"{format(affinity, number_format):>12} "
                f"{format(rmsd_lb, number_format):>11} "
                f"{format(rmsd_ub, number_format):>11}\n"
            )
            for mode, affinity, rmsd_lb, rmsd_ub in effective_rows
        )
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

    def test_affinity_serialization_models_vina_stream_context(self) -> None:
        self.assertTrue(
            vina_affinity_serialization_matches(
                "-12.7",
                -12.703,
                verbosity=1,
            )
        )
        self.assertFalse(
            vina_affinity_serialization_matches(
                "-8.049",
                -8.0,
                verbosity=1,
            )
        )
        for raw_affinity in (-9.999, -9.996):
            with self.subTest(raw_affinity=raw_affinity):
                self.assertFalse(
                    vina_affinity_serialization_matches(
                        "-10",
                        raw_affinity,
                        verbosity=1,
                    )
                )
        self.assertTrue(
            vina_affinity_serialization_matches(
                "-12.7030",
                -12.703,
                verbosity=2,
            )
        )
        self.assertFalse(
            vina_affinity_serialization_matches(
                "-12.7000",
                -12.705,
                verbosity=2,
            )
        )

    def test_rmsd_serialization_models_vina_stream_context(self) -> None:
        self.assertTrue(
            vina_rmsd_serialization_matches(
                "12.35",
                12.346,
                verbosity=1,
            )
        )
        self.assertFalse(
            vina_rmsd_serialization_matches(
                "12.34",
                12.346,
                verbosity=1,
            )
        )
        self.assertTrue(
            vina_rmsd_serialization_matches(
                "12.3456",
                12.346,
                verbosity=2,
            )
        )
        self.assertFalse(
            vina_rmsd_serialization_matches(
                "12.3500",
                12.346,
                verbosity=2,
            )
        )
        self.assertFalse(
            vina_rmsd_serialization_matches(
                "10",
                9.996,
                verbosity=1,
            )
        )

    def test_score_serialization_accepts_values_emitted_by_both_stream_states(
        self,
    ) -> None:
        unrounded_values = (
            -100.0004,
            -10.0004,
            -9.9994,
            -1.2344,
            -0.0004,
            0.0,
            0.0004,
            1.2344,
            9.9994,
            10.0004,
            12.3456,
            99.9994,
        )
        for verbosity, log_format in ((1, ".4g"), (2, ".4f")):
            for unrounded in unrounded_values:
                with self.subTest(
                    verbosity=verbosity,
                    unrounded=unrounded,
                ):
                    pdbqt_value = float(format(unrounded, ".3f"))
                    log_text = format(unrounded, log_format)
                    self.assertTrue(
                        vina_rmsd_serialization_matches(
                            log_text,
                            pdbqt_value,
                            verbosity=verbosity,
                        )
                    )

    def test_score_table_tokens_require_exact_vina_lexical_format(self) -> None:
        accepted = (
            (1, "-12.7"),
            (1, "0"),
            (1, "12.35"),
            (2, "-12.7000"),
            (2, "0.0000"),
            (2, "12.3500"),
        )
        rejected = (
            (1, "-12.7000"),
            (1, "+12.35"),
            (1, "0012.35"),
            (1, "0.000"),
            (2, "-12.7"),
            (2, "12.35"),
            (2, "0"),
        )
        for verbosity, token in accepted:
            with self.subTest(kind="accepted", verbosity=verbosity, token=token):
                self.assertTrue(
                    vina_score_table_token_is_canonical(
                        token,
                        verbosity=verbosity,
                    )
                )
        for verbosity, token in rejected:
            with self.subTest(kind="rejected", verbosity=verbosity, token=token):
                self.assertFalse(
                    vina_score_table_token_is_canonical(
                        token,
                        verbosity=verbosity,
                    )
                )

    def test_significant_digit_intervals_are_directional_at_powers_of_ten(
        self,
    ) -> None:
        cases = (
            ("10", (9.9995, 10.005)),
            ("-10", (-10.005, -9.9995)),
            ("100", (99.995, 100.05)),
            ("-100", (-100.05, -99.995)),
            ("0.0001", (0.000099995, 0.00010005)),
            ("-0.0001", (-0.00010005, -0.000099995)),
            ("0", (0.0, 0.0)),
        )
        for token, expected in cases:
            with self.subTest(token=token):
                actual = _vina_score_table_numeric_interval(
                    token,
                    verbosity=1,
                )
                self.assertIsNotNone(actual)
                assert actual is not None
                self.assertAlmostEqual(actual[0], expected[0], places=12)
                self.assertAlmostEqual(actual[1], expected[1], places=12)

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

    def _vina_detection(
        self,
        *,
        version: str = "1.2.7",
        maps_supported: bool = True,
    ) -> ToolCheckResult:
        def feature(option: str) -> dict[str, object]:
            supported = maps_supported if option == "maps" else True
            return {
                "option": f"--{option}",
                "status": "supported" if supported else "unsupported",
                "supported": supported,
                "advertised": supported,
            }

        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version=version,
            path=str(self.vina_file),
            message="mock Vina",
            source="configured",
            capabilities={
                "status": "ok",
                "source": "help_advanced",
                "checked": True,
                "version": version,
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

    def _rewrite_run_artifact_and_sync_metadata(
        self,
        project_dir: Path,
        run_id: str,
        artifact_key: str,
        text: str,
    ) -> None:
        metadata = self._metadata_data(project_dir, run_id)
        artifact = metadata["artifacts"][artifact_key]
        artifact_path = project_dir / str(artifact["relative_path"])
        artifact_path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        artifact["size_bytes"] = artifact_path.stat().st_size
        artifact["sha256"] = digest
        metadata["artifact_sha256"][artifact_key] = digest
        if isinstance(metadata.get("output_sha256"), dict):
            metadata["output_sha256"][artifact_key] = digest
        self._write_metadata(project_dir, run_id, metadata)

    def _forge_frozen_grid_coverage(
        self,
        project_dir: Path,
        run_id: str,
    ) -> None:
        manifest_path = (
            project_dir
            / "runs"
            / run_id
            / "inputs"
            / "maps"
            / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["grid_coverage"]["axis_coverage"]["x"][
            "minimum_margin_angstrom"
        ] += 0.25
        canonical = json.dumps(
            manifest["grid_coverage"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        coverage_sha256 = hashlib.sha256(canonical).hexdigest()
        manifest["grid_coverage_sha256"] = coverage_sha256
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_sha256 = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        manifest_size = manifest_path.stat().st_size

        metadata = self._metadata_data(project_dir, run_id)
        for record in (
            metadata["snapshots"]["ad4_maps"]["manifest"],
            metadata["artifacts"]["hydrated_maps_manifest"],
        ):
            record["sha256"] = manifest_sha256
            record["size_bytes"] = manifest_size
        metadata["input_sha256"]["maps_manifest"] = manifest_sha256
        for section in (
            metadata["snapshots"]["ad4_maps"],
            metadata["ad4_maps"],
            metadata["hydrated"],
        ):
            section["grid_coverage"] = json.loads(
                json.dumps(manifest["grid_coverage"])
            )
            section["grid_coverage_sha256"] = coverage_sha256
        self._write_metadata(project_dir, run_id, metadata)

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
        (root / "receptor.maps.xyz").write_text(
            "0.0 0.0 0.0\n",
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
                    "grid_points": {"x": 8, "y": 8, "z": 8},
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
        log_text: str | None = None,
        output_text: str | None = None,
        output_bytes: bytes | None = None,
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
            effective_log_text = (
                log_text if log_text is not None else _vina_log()
            )
            Path(stdout_path).write_text(
                effective_log_text,
                encoding="utf-8",
            )
            Path(stderr_path).write_text("", encoding="utf-8")
            Path(log_path).write_text(
                effective_log_text,
                encoding="utf-8",
            )
            output_path = Path(cwd) / command[command.index("--out") + 1]
            if output_bytes is not None:
                output_path.write_bytes(output_bytes)
            else:
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

    def test_frozen_grid_accepts_six_decimal_actual_size(self) -> None:
        box = {
            "center_x": 1.0,
            "center_y": 2.0,
            "center_z": 3.0,
            "size_x": 7.999999,
            "size_y": 7.999999,
            "size_z": 7.999999,
        }
        spacing = 0.3333333
        points = [24, 24, 24]
        computed = compute_requested_box_grid_coverage(
            box,
            points,
            spacing,
        )
        self.assertTrue(computed["ok"], computed)
        manifest = {
            "box": {
                "center": {"x": 1.0, "y": 2.0, "z": 3.0},
                "size": {
                    "x": 7.999999,
                    "y": 7.999999,
                    "z": 7.999999,
                },
            },
            "grid": {
                "center": {"x": 1.0, "y": 2.0, "z": 3.0},
                "requested_box": {
                    "center": {"x": 1.0, "y": 2.0, "z": 3.0},
                    "size": {
                        "x": 7.999999,
                        "y": 7.999999,
                        "z": 7.999999,
                    },
                },
                "spacing": spacing,
                "grid_points": {"x": 24, "y": 24, "z": 24},
                "actual_size": {
                    "x": 7.999999,
                    "y": 7.999999,
                    "z": 7.999999,
                },
            },
            "grid_coverage": computed["coverage"],
            "grid_coverage_sha256": computed["coverage_sha256"],
        }

        verified, issue = _hydrated_frozen_grid_coverage(
            manifest,
            expected_box=box,
            expected_grid=manifest["grid"],
        )

        self.assertEqual(issue, "")
        self.assertEqual(verified, computed["coverage"])

        outside_tolerance = json.loads(json.dumps(manifest))
        outside_tolerance["grid"]["actual_size"]["x"] += 0.000002
        _verified, issue = _hydrated_frozen_grid_coverage(
            outside_tolerance,
            expected_box=box,
            expected_grid=outside_tolerance["grid"],
        )
        self.assertIn("实际网格尺寸", issue)

    def test_preflight_rejects_old_vina_and_missing_maps_capability(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        cases = (
            (
                self._vina_detection(version="1.1.2"),
                "HYDRATED_VINA_VERSION_UNSUPPORTED",
            ),
            (
                self._vina_detection(maps_supported=False),
                "HYDRATED_VINA_MAPS_CAPABILITY_MISSING",
            ),
        )

        for detection, expected_code in cases:
            with self.subTest(expected_code=expected_code), patch(
                "dockstart_core.hydrated_run.vina_adapter.detect",
                return_value=detection,
            ):
                preflight = get_hydrated_run_preflight(str(project_dir))

            self.assertFalse(preflight["ok"], preflight)
            self.assertEqual(preflight["error"]["code"], expected_code)

    def test_active_hydrated_maps_version_gate_reaches_status_and_run(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        project_payload = self._project_data(project_dir)
        pointer = project_payload["hydrated_docking"]["active_maps_manifest"]
        manifest_path = project_dir / pointer["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["autogrid"]["version"] = "4.2.5"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pointer["sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        (project_dir / "project.json").write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status = hydrated.get_status(str(project_dir))
        self.assertFalse(status["maps_ready"], status)
        self.assertIn(
            "AutoGrid 4.2.6+",
            "；".join(status["maps_issues"]),
        )
        with patch(
            "dockstart_core.hydrated_run.vina_adapter.detect",
            return_value=self._vina_detection(),
        ):
            preflight = get_hydrated_run_preflight(str(project_dir))
        self.assertFalse(preflight["ok"], preflight)
        self.assertEqual(
            preflight["error"]["code"],
            "HYDRATED_AUTOGRID_VERSION_UNSUPPORTED",
        )

    def test_historical_atom_type_case_is_canonicalized_through_execution(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        project_payload = self._project_data(project_dir)
        pointer = project_payload["hydrated_docking"]["active_maps_manifest"]
        manifest_path = project_dir / pointer["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        maps = manifest["maps"]
        maps["ligand_atom_types"] = [
            str(item).lower() for item in maps["ligand_atom_types"]
        ]
        maps["autogrid_ligand_atom_types"] = [
            str(item).lower()
            for item in maps["autogrid_ligand_atom_types"]
        ]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pointer["sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        (project_dir / "project.json").write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status = hydrated.get_status(str(project_dir))
        self.assertTrue(status["maps_ready"], status)
        prepared = self._prepare_run(project_dir)
        executed = self._execute(
            project_dir,
            str(prepared["run_id"]),
        )

        self.assertTrue(executed["ok"], executed)
        self.assertEqual(executed["metadata"]["status"], "finished")

    def test_canonical_atom_type_duplicates_in_manifest_fail_closed(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        project_payload = self._project_data(project_dir)
        pointer = project_payload["hydrated_docking"]["active_maps_manifest"]
        manifest_path = project_dir / pointer["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["maps"]["ligand_atom_types"].append("c")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pointer["sha256"] = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        (project_dir / "project.json").write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        status = hydrated.get_status(str(project_dir))

        self.assertFalse(status["maps_ready"], status)
        self.assertIn(
            "原子类型",
            "；".join(status["maps_issues"]),
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
        self.assertTrue(
            metadata["hydrated"]["grid_coverage"][
                "covers_requested_box"
            ]
        )
        self.assertEqual(
            metadata["hydrated"]["grid_coverage_sha256"],
            metadata["ad4_maps"]["grid_coverage_sha256"],
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

    def test_hydrated_postprocess_uses_normalized_output_and_keeps_vina_bytes(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        source = _valid_hydrated_output().encode("utf-8")
        padded = source.replace(
            b"TORSDOF 1\nENDMDL",
            b"TORSDOF 1\r\n" + (b"\x00" * 47) + b"\r\nENDMDL",
        )
        self.assertEqual(padded.count(b"\x00"), 94)

        executed = self._execute(
            project_dir,
            run_id,
            output_bytes=padded,
        )

        self.assertTrue(executed["ok"], executed)
        run_root = project_dir / "runs" / run_id
        self.assertEqual(
            (run_root / "out.vina_raw.pdbqt").read_bytes(),
            padded,
        )
        self.assertNotIn(b"\x00", (run_root / "out.pdbqt").read_bytes())
        self.assertTrue(
            (run_root / HYDRATED_RETAINED_OUTPUT_NAME).is_file()
        )
        normalization = executed["metadata"]["output_normalization"]
        self.assertEqual(normalization["status"], "normalized")
        self.assertEqual(normalization["nul_bytes_removed"], 94)
        self.assertEqual(normalization["recognized_padding_blocks"], 2)
        self.assertEqual(
            executed["metadata"]["hydrated_postprocess"]["status"],
            "finished",
        )
        report = build_hydrated_markdown_report(str(project_dir), run_id)
        self.assertTrue(report["ok"], report)
        self.assertIn("out.vina_raw.pdbqt", report["report_text"])
        self.assertIn(
            normalization["source_sha256"],
            report["report_text"],
        )
        self.assertIn(
            normalization["normalized_sha256"],
            report["report_text"],
        )
        self.assertEqual(
            report["report_text"].count("输出标准化"),
            1,
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

    def test_forged_frozen_grid_coverage_blocks_execution_before_vina(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        self._forge_frozen_grid_coverage(project_dir, run_id)
        started = Mock()

        executed = self._execute(
            project_dir,
            run_id,
            started=started,
        )

        self.assertFalse(executed["ok"], executed)
        self.assertEqual(
            executed["error"]["code"],
            "RUN_HYDRATED_GRID_COVERAGE_INVALID",
        )
        started.assert_not_called()

    def test_forged_frozen_grid_coverage_blocks_results_and_report(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)
        self._forge_frozen_grid_coverage(project_dir, run_id)

        results = load_hydrated_results(str(project_dir), run_id)
        report = build_hydrated_markdown_report(
            str(project_dir),
            run_id,
        )

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "RUN_HYDRATED_POST_GRID_COVERAGE_INVALID",
        )
        self.assertFalse(report["ok"], report)
        self.assertEqual(
            report["error"]["code"],
            "RUN_HYDRATED_POST_GRID_COVERAGE_INVALID",
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
        self.assertIn(
            "冻结 Box 与实际网格覆盖",
            report["report_text"],
        )
        self.assertIn("闭区间覆盖复核：通过", report["report_text"])
        self.assertIn("Raw AD4 affinity", report["report_text"])
        self.assertIn("处理后评分未计算", report["report_text"])
        self.assertIn("| 1 | -8.0 | 1 | 0 | 0 |", report["report_text"])
        self.assertIn("不用于虚拟筛选", report["report_text"])
        self.assertTrue(generic_report["ok"], generic_report)
        self.assertEqual(
            generic_report["report_text"],
            report["report_text"],
        )

    def test_results_accept_energy_range_output_prefix_and_keep_full_log_csv(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        updated = update_vina_params(
            str(project_dir),
            {"energy_range": 0.01},
        )
        self.assertTrue(updated["ok"], updated)
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            rows=(
                (1, -10.87, 0.0, 0.0),
                (2, -10.8, 0.5, 0.8),
            )
        )
        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
            output_text=_single_hydrated_output(affinity=-10.869),
        )
        self.assertTrue(executed["ok"], executed)

        before_analysis = load_hydrated_results(
            str(project_dir),
            run_id,
        )
        self.assertTrue(before_analysis["ok"], before_analysis)
        self.assertEqual(
            [item["mode"] for item in before_analysis["scores"]],
            [1],
        )
        self.assertEqual(
            before_analysis["provenance"]["result_table_validation"][
                "status"
            ],
            "proven_truncated",
        )

        analyzed = analyze_vina_run_results(str(project_dir), run_id)
        self.assertTrue(analyzed["ok"], analyzed)
        self.assertEqual(
            [item["mode"] for item in analyzed["scores"]],
            [1],
        )
        csv_lines = (
            project_dir / "runs" / run_id / "scores.csv"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(csv_lines), 3)
        self.assertTrue(csv_lines[1].startswith("1,"))
        self.assertTrue(csv_lines[2].startswith("2,"))

        after_analysis = load_hydrated_results(str(project_dir), run_id)
        self.assertTrue(after_analysis["ok"], after_analysis)
        self.assertEqual(
            [item["mode"] for item in after_analysis["scores"]],
            [1],
        )
        self.assertEqual(
            after_analysis["score_source"],
            "verified_scores_csv",
        )

    def test_results_reject_impossible_power_of_ten_energy_truncation(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        updated = update_vina_params(
            str(project_dir),
            {"energy_range": 0.003},
        )
        self.assertTrue(updated["ok"], updated)
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self._execute(
            project_dir,
            run_id,
            log_text=_vina_log(
                rows=(
                    (1, -10.0, 0.0, 0.0),
                    (2, -10.0, 1.0, 2.0),
                )
            ),
            output_text=_single_hydrated_output(affinity=-10.0),
        )
        self.assertTrue(executed["ok"], executed)

        results = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "HYDRATED_OUTPUT_TRUNCATION_INVALID",
        )

    def test_unrecorded_scores_csv_cannot_override_frozen_hydrated_log(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)
        unrecorded_scores = project_dir / "runs" / run_id / "scores.csv"
        unrecorded_scores.write_text(
            (
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "2,-7.5,0.0,0.0\n"
                "1,-8.0,0.0,0.0\n"
            ),
            encoding="utf-8",
        )

        results = load_hydrated_results(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(results["score_source"], "verified_frozen_log")
        self.assertEqual(
            [item["mode"] for item in results["scores"]],
            [1, 2],
        )

    def test_recorded_scores_csv_must_match_full_log_order(self) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        executed = self._execute(project_dir, run_id)
        self.assertTrue(executed["ok"], executed)
        analyzed = analyze_vina_run_results(str(project_dir), run_id)
        self.assertTrue(analyzed["ok"], analyzed)
        self._rewrite_run_artifact_and_sync_metadata(
            project_dir,
            run_id,
            "scores",
            (
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "2,-7.5,0.0,0.0\n"
                "1,-8.0,0.0,0.0\n"
            ),
        )

        results = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "HYDRATED_SCORE_MODE_BINDING_MISMATCH",
        )

    def test_results_reject_impossible_vina_score_table_semantics(
        self,
    ) -> None:
        cases = (
            (
                "mode1_nonzero_rmsd",
                ((1, -8.0, 0.1, 0.2), (2, -7.5, 0.2, 0.3)),
                {},
                "HYDRATED_LOG_SCORE_SEMANTICS_INVALID",
            ),
            (
                "negative_rmsd",
                ((1, -8.0, 0.0, 0.0), (2, -7.5, -0.1, 0.3)),
                {},
                "HYDRATED_LOG_SCORE_SEMANTICS_INVALID",
            ),
            (
                "lower_bound_above_upper",
                ((1, -8.0, 0.0, 0.0), (2, -7.5, 0.4, 0.3)),
                {},
                "HYDRATED_LOG_SCORE_SEMANTICS_INVALID",
            ),
            (
                "affinity_order",
                ((1, -8.0, 0.0, 0.0), (2, -8.5, 0.2, 0.3)),
                {},
                "HYDRATED_LOG_SCORE_SEMANTICS_INVALID",
            ),
            (
                "more_than_num_modes",
                ((1, -8.0, 0.0, 0.0), (2, -7.5, 0.2, 0.3)),
                {"num_modes": 1},
                "HYDRATED_LOG_MODE_SEQUENCE_INVALID",
            ),
        )
        for name, rows, vina_updates, expected_code in cases:
            with self.subTest(name=name):
                project_dir = self._create_ready_project()
                if vina_updates:
                    updated = update_vina_params(
                        str(project_dir),
                        vina_updates,
                    )
                    self.assertTrue(updated["ok"], updated)
                prepared = self._prepare_run(project_dir)
                run_id = str(prepared["run_id"])
                executed = self._execute(
                    project_dir,
                    run_id,
                    log_text=_vina_log(rows=rows),
                )
                self.assertTrue(executed["ok"], executed)

                results = load_hydrated_results(str(project_dir), run_id)

                self.assertFalse(results["ok"], results)
                self.assertEqual(results["error"]["code"], expected_code)

    def test_results_reject_impossible_pdbqt_result_semantics(self) -> None:
        cases = (
            (
                "affinity_order",
                _vina_log(
                    rows=(
                        (1, -10.0, 0.0, 0.0),
                        (2, -10.0, 1.0, 2.0),
                    )
                ),
                _valid_hydrated_output()
                .replace(
                    "REMARK VINA RESULT: -8.000 0.000 0.000",
                    "REMARK VINA RESULT: -10.000 0.000 0.000",
                    1,
                )
                .replace(
                    "REMARK VINA RESULT: -7.500 0.000 0.000",
                    "REMARK VINA RESULT: -10.004 1.000 2.000",
                    1,
                ),
            ),
            (
                "rmsd_bounds",
                _vina_log(
                    rows=(
                        (1, -8.0, 0.0, 0.0),
                        (2, -7.5, 10.0, 10.0),
                    )
                ),
                _valid_hydrated_output().replace(
                    "REMARK VINA RESULT: -7.500 0.000 0.000",
                    "REMARK VINA RESULT: -7.500 10.004 10.000",
                    1,
                ),
            ),
        )
        for name, log_text, output_text in cases:
            with self.subTest(name=name):
                project_dir = self._create_ready_project()
                prepared = self._prepare_run(project_dir)
                run_id = str(prepared["run_id"])
                executed = self._execute(
                    project_dir,
                    run_id,
                    log_text=log_text,
                    output_text=output_text,
                )
                self.assertTrue(executed["ok"], executed)

                results = load_hydrated_results(str(project_dir), run_id)

                self.assertFalse(results["ok"], results)
                self.assertEqual(
                    results["error"]["code"],
                    "HYDRATED_PDBQT_RESULT_SEMANTICS_INVALID",
                )

    def test_results_accept_log_rounding_and_keep_raw_pdbqt_affinity(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            rows=(
                (1, -12.7, 0.0, 0.0),
                (2, -12.0, 0.0, 0.0),
            )
        )
        output_text = (
            _valid_hydrated_output()
            .replace(
                "REMARK VINA RESULT: -8.000",
                "REMARK VINA RESULT: -12.703",
                1,
            )
            .replace(
                "REMARK VINA RESULT: -7.500",
                "REMARK VINA RESULT: -12.000",
                1,
            )
        )

        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
            output_text=output_text,
        )
        self.assertTrue(executed["ok"], executed)
        results = load_hydrated_results(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(
            results["modes"][0]["raw_affinity_kcal_mol"],
            -12.703,
        )
        waters_manifest = json.loads(
            (
                project_dir
                / "runs"
                / run_id
                / HYDRATED_WATERS_MANIFEST_NAME
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            waters_manifest["poses"][0]["raw_affinity"],
            -12.703,
        )

    def test_results_accept_verbosity_one_rmsd_significant_digit_rounding(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            rows=(
                (1, -8.0, 0.0, 0.0),
                (2, -7.5, 12.35, 13.67),
            )
        )
        output_text = _valid_hydrated_output().replace(
            "REMARK VINA RESULT: -7.500 0.000 0.000",
            "REMARK VINA RESULT: -7.500 12.346 13.666",
            1,
        )

        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
            output_text=output_text,
        )
        self.assertTrue(executed["ok"], executed)
        results = load_hydrated_results(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(results["modes"][1]["rmsd_lb"], 12.35)
        self.assertEqual(results["modes"][1]["rmsd_ub"], 13.67)

    def test_results_accept_verbosity_two_rmsd_fixed_decimal_rounding(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        updated = update_vina_params(
            str(project_dir),
            {"verbosity": 2},
        )
        self.assertTrue(updated["ok"], updated)
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            verbosity=2,
            rows=(
                (1, -8.0, 0.0, 0.0),
                (2, -7.5, 12.3456, 13.6656),
            )
        )
        output_text = _valid_hydrated_output().replace(
            "REMARK VINA RESULT: -7.500 0.000 0.000",
            "REMARK VINA RESULT: -7.500 12.346 13.666",
            1,
        )

        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
            output_text=output_text,
        )
        self.assertTrue(executed["ok"], executed)
        results = load_hydrated_results(str(project_dir), run_id)

        self.assertTrue(results["ok"], results)
        self.assertEqual(results["modes"][1]["rmsd_lb"], 12.3456)
        self.assertEqual(results["modes"][1]["rmsd_ub"], 13.6656)

    def test_results_reject_log_only_tamper_even_when_serialization_is_reachable(
        self,
    ) -> None:
        cases = (
            {
                "verbosity": 1,
                "original_row": (
                    "   2         -7.5       0.123       0.123"
                ),
                "tampered_row": (
                    "   2         -7.5      0.1231       0.123"
                ),
                "output_result": (
                    "REMARK VINA RESULT: -7.500 0.123 0.123"
                ),
            },
            {
                "verbosity": 2,
                "original_row": (
                    "   2      -7.5000     12.3456     13.6656"
                ),
                "tampered_row": (
                    "   2      -7.5000     12.3457     13.6656"
                ),
                "output_result": (
                    "REMARK VINA RESULT: -7.500 12.346 13.666"
                ),
            },
        )
        for case in cases:
            with self.subTest(verbosity=case["verbosity"]):
                project_dir = self._create_ready_project()
                if case["verbosity"] == 2:
                    updated = update_vina_params(
                        str(project_dir),
                        {"verbosity": 2},
                    )
                    self.assertTrue(updated["ok"], updated)
                prepared = self._prepare_run(project_dir)
                run_id = str(prepared["run_id"])
                original_log = _vina_log(
                    verbosity=int(case["verbosity"]),
                    rows=(
                        (1, -8.0, 0.0, 0.0),
                        (
                            2,
                            -7.5,
                            (
                                0.123
                                if case["verbosity"] == 1
                                else 12.3456
                            ),
                            (
                                0.123
                                if case["verbosity"] == 1
                                else 13.6656
                            ),
                        ),
                    ),
                )
                self.assertIn(str(case["original_row"]), original_log)
                output_text = _valid_hydrated_output().replace(
                    "REMARK VINA RESULT: -7.500 0.000 0.000",
                    str(case["output_result"]),
                    1,
                )
                executed = self._execute(
                    project_dir,
                    run_id,
                    log_text=original_log,
                    output_text=output_text,
                )
                self.assertTrue(executed["ok"], executed)
                before = load_hydrated_results(str(project_dir), run_id)
                self.assertTrue(before["ok"], before)

                tampered_log = original_log.replace(
                    str(case["original_row"]),
                    str(case["tampered_row"]),
                    1,
                )
                self._rewrite_run_artifact_and_sync_metadata(
                    project_dir,
                    run_id,
                    "log",
                    tampered_log,
                )
                after = load_hydrated_results(str(project_dir), run_id)

                self.assertFalse(after["ok"], after)
                self.assertEqual(
                    after["error"]["code"],
                    "HYDRATED_PROVENANCE_VINA_STDOUT_LOG_MISMATCH",
                )

    def test_results_reject_affinity_not_matching_vina_significant_digits(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            rows=(
                (1, -8.049, 0.0, 0.0),
                (2, -7.5, 0.0, 0.0),
            )
        )

        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
        )
        self.assertTrue(executed["ok"], executed)
        results = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "HYDRATED_SCORE_RECORD_MISMATCH",
        )

    def test_results_use_fixed_four_decimals_for_verbosity_two(
        self,
    ) -> None:
        project_dir = self._create_ready_project()
        updated = update_vina_params(
            str(project_dir),
            {"verbosity": 2},
        )
        self.assertTrue(updated["ok"], updated)
        prepared = self._prepare_run(project_dir)
        run_id = str(prepared["run_id"])
        log_text = _vina_log(
            verbosity=2,
            rows=(
                (1, -12.7, 0.0, 0.0),
                (2, -12.0, 0.0, 0.0),
            ),
        )
        output_text = (
            _valid_hydrated_output()
            .replace(
                "REMARK VINA RESULT: -8.000",
                "REMARK VINA RESULT: -12.705",
                1,
            )
            .replace(
                "REMARK VINA RESULT: -7.500",
                "REMARK VINA RESULT: -12.000",
                1,
            )
        )

        executed = self._execute(
            project_dir,
            run_id,
            log_text=log_text,
            output_text=output_text,
        )
        self.assertTrue(executed["ok"], executed)
        results = load_hydrated_results(str(project_dir), run_id)

        self.assertFalse(results["ok"], results)
        self.assertEqual(
            results["error"]["code"],
            "HYDRATED_SCORE_RECORD_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
