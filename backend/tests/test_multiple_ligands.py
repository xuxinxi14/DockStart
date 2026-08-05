from __future__ import annotations

import json
import math
import os
import stat
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

from dockstart_core.models import ToolCheckResult  # noqa: E402
import dockstart_core.multiple_ligands as multiple_ligands  # noqa: E402
from dockstart_core.autogrid import generate_maps  # noqa: E402
from dockstart_core.multiple_ligands import (  # noqa: E402
    PROTOCOL_ID,
    build_multiple_ligand_markdown_report,
    cancel_multiple_ligand_run,
    execute_multiple_ligand_run,
    export_multiple_ligand_markdown_report,
    get_multiple_ligand_run_status,
    load_multiple_ligand_pose,
    main,
    parse_multiple_ligand_output_text,
    prepare_multiple_ligand_run,
)
from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    build_markdown_report,
    create_project,
    get_run_runtime_status,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_scores_csv,
    recover_project_state,
    update_box_params,
    update_vina_params,
)


def _pdbqt(atom_name: str, atom_type: str, torsdof: int) -> str:
    return (
        "ROOT\n"
        f"ATOM      1  {atom_name:<3} LIG A   1       "
        f"1.000   2.000   3.000  1.00  0.00     0.000 {atom_type}\n"
        "ENDROOT\n"
        f"TORSDOF {torsdof}\n"
    )


def _rigid_pdbqt_with_coordinates(
    coordinates: list[tuple[float, float, float]],
    atom_type: str = "C",
) -> str:
    atom_lines = []
    for serial, (x, y, z) in enumerate(coordinates, start=1):
        atom_lines.append(
            f"ATOM  {serial:5d}  C{serial:<2} LIG A   1    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}"
            f"  1.00  0.00     0.000 {atom_type}"
        )
    return (
        "ROOT\n"
        + "\n".join(atom_lines)
        + "\nENDROOT\n"
        + "TORSDOF 0\n"
    )


def _pdbqt_atom_line(
    serial: int,
    atom_name: str,
    x: float,
    y: float,
    z: float,
    atom_type: str = "C",
) -> str:
    return (
        f"ATOM  {serial:5d}  {atom_name:<3} LIG A   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00  0.00     0.000 {atom_type}"
    )


def _flexible_pdbqt_with_coordinates(
    root_x: float,
    axis_x: float,
    extra_x: float,
) -> str:
    return (
        "ROOT\n"
        + _pdbqt_atom_line(1, "C1", root_x, 0.0, 0.0)
        + "\nENDROOT\n"
        + "BRANCH 1 2\n"
        + _pdbqt_atom_line(2, "C2", axis_x, 0.0, 0.0)
        + "\n"
        + _pdbqt_atom_line(3, "C3", extra_x, 0.0, 0.0)
        + "\nENDBRANCH 1 2\n"
        + "TORSDOF 1\n"
    )


def _degenerate_branch_pdbqt(
    root_x: float,
    axis_x: float,
) -> str:
    return (
        "ROOT\n"
        + _pdbqt_atom_line(1, "C1", root_x, 0.0, 0.0)
        + "\nENDROOT\n"
        + "BRANCH 1 2\n"
        + _pdbqt_atom_line(2, "C2", axis_x, 0.0, 0.0)
        + "\nENDBRANCH 1 2\n"
        + "TORSDOF 1\n"
    )


def _large_rigid_pdbqt(atom_count: int) -> str:
    return (
        "ROOT\n"
        + "\n".join(
            _pdbqt_atom_line(
                serial,
                "C",
                float(serial) / 1000.0,
                0.0,
                0.0,
            )
            for serial in range(1, atom_count + 1)
        )
        + "\nENDROOT\nTORSDOF 0\n"
    )


def _rigid_pdbqt_with_hydrogen(
    heavy_x: float,
    hydrogen_x: float,
) -> str:
    return (
        "ROOT\n"
        f"ATOM      1  C1  LIG A   1    {heavy_x:8.3f}"
        "   0.000   0.000  1.00  0.00     0.000 C\n"
        f"ATOM      2  H1  LIG A   1    {hydrogen_x:8.3f}"
        "   0.000   0.000  1.00  0.00     0.000 HD\n"
        "ENDROOT\n"
        "TORSDOF 0\n"
    )


def _joint_model(mode: int, affinity: float, rmsd_lb: float, rmsd_ub: float) -> str:
    return (
        f"MODEL {mode}\n"
        f"REMARK VINA RESULT: {affinity:.3f} {rmsd_lb:.3f} {rmsd_ub:.3f}\n"
        + _pdbqt("C1", "C", 0)
        + _pdbqt("O1", "OA", 1)
        + "ENDMDL\n"
    )


def _topology_pdbqt(
    branch_end: int,
    charges: tuple[str, str, str] = ("0.000", "0.000", "0.000"),
) -> str:
    atoms = [
        (1, "C1", "C"),
        (2, "N1", "N"),
        (3, "O1", "OA"),
    ]
    atom_lines = [
        (
            f"ATOM  {serial:5d}  {name:<3} LIG A   1       "
            f"{serial:.3f}   2.000   3.000  1.00  0.00     "
            f"{charges[serial - 1]:>6} {atom_type}"
        )
        for serial, name, atom_type in atoms
    ]
    return (
        "ROOT\n"
        f"{atom_lines[0]}\n"
        "ENDROOT\n"
        f"BRANCH 1 {branch_end}\n"
        f"{atom_lines[1]}\n"
        f"{atom_lines[2]}\n"
        f"ENDBRANCH 1 {branch_end}\n"
        "TORSDOF 1\n"
    )


def _nested_branch_pdbqt(
    branch_count: int,
    *,
    torsdof: int | None = None,
    terminal_extra_atom: bool = True,
) -> str:
    if branch_count < 0:
        raise ValueError("branch_count must be non-negative")
    atom_total = (
        branch_count + 2
        if branch_count and terminal_extra_atom
        else branch_count + 1
    )
    atoms = [
        (
            f"ATOM  {serial:5d}  C{serial % 100:<2} LIG A   1    "
            f"{float(serial):8.3f}{2.0:8.3f}{3.0:8.3f}"
            "  1.00  0.00     0.000 C"
        )
        for serial in range(1, atom_total + 1)
    ]
    lines = ["ROOT", atoms[0], "ENDROOT"]
    for serial in range(1, branch_count + 1):
        lines.extend(
            [
                f"BRANCH {serial} {serial + 1}",
                atoms[serial],
            ]
        )
    if branch_count and terminal_extra_atom:
        lines.append(atoms[-1])
    for serial in range(branch_count, 0, -1):
        lines.append(f"ENDBRANCH {serial} {serial + 1}")
    lines.append(
        f"TORSDOF {branch_count if torsdof is None else torsdof}"
    )
    return "\n".join(lines) + "\n"


JOINT_OUTPUT = _joint_model(1, -10.0, 0.0, 0.0) + _joint_model(
    2,
    -9.2,
    1.2,
    2.3,
)

VINA_LOG = """AutoDock Vina v1.2.7
mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1      -10.000      0.000      0.000
   2       -9.200      1.200      2.300
   3       -8.700      3.000      4.000
"""


def _vina_log_modes(modes: list[int]) -> str:
    rows = [
        f"{mode:4d}      {-10.0 + index:.3f}      0.000      0.000"
        for index, mode in enumerate(modes)
    ]
    return (
        "AutoDock Vina v1.2.7\n"
        "mode |   affinity | dist from best mode\n"
        "     | (kcal/mol) | rmsd l.b.| rmsd u.b.\n"
        "-----+------------+----------+----------\n"
        + "\n".join(rows)
        + "\n"
    )


class MultipleLigandParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.members = [
            {
                "member_index": 1,
                "atom_count": 1,
                "heavy_atom_count": 1,
                "branch_count": 0,
                "effective_branch_count": 0,
                "degenerate_branch_count": 0,
                "torsdof": 0,
            },
            {
                "member_index": 2,
                "atom_count": 1,
                "heavy_atom_count": 1,
                "branch_count": 0,
                "effective_branch_count": 0,
                "degenerate_branch_count": 0,
                "torsdof": 1,
            },
        ]

    def test_log_output_score_comparison_allows_only_display_rounding(self) -> None:
        self.assertTrue(
            multiple_ligands._vina_score_output_values_match(-17.65, -17.653)
        )
        self.assertFalse(
            multiple_ligands._vina_score_output_values_match(-17.65, -17.656)
        )

    def test_parser_preserves_joint_semantics_and_member_order(self) -> None:
        parsed = parse_multiple_ligand_output_text(JOINT_OUTPUT, self.members)
        self.assertTrue(parsed["ok"], parsed)
        self.assertEqual([pose["mode"] for pose in parsed["models"]], [1, 2])
        self.assertEqual(parsed["models"][0]["joint_affinity_kcal_mol"], -10.0)
        self.assertEqual(
            [member["member_index"] for member in parsed["models"][0]["members"]],
            [1, 2],
        )
        self.assertFalse(parsed["per_member_scores_available"])
        self.assertEqual(parsed["rmsd_scope"], "joint_pose_relative_to_best_mode")

    def test_parser_rejects_model_with_wrong_member_count(self) -> None:
        broken = JOINT_OUTPUT.replace(_pdbqt("O1", "OA", 1), "", 1)
        parsed = parse_multiple_ligand_output_text(broken, self.members)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "MULTIPLE_LIGAND_MEMBER_COUNT_INVALID")

    def test_parser_rejects_member_identity_stat_mismatch(self) -> None:
        broken = JOINT_OUTPUT.replace("TORSDOF 1", "TORSDOF 2", 1)
        parsed = parse_multiple_ligand_output_text(broken, self.members)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "MULTIPLE_LIGAND_MEMBER_STATS_MISMATCH")

    def test_parser_rejects_missing_joint_score_remark(self) -> None:
        broken = JOINT_OUTPUT.replace(
            "REMARK VINA RESULT: -10.000 0.000 0.000\n",
            "",
            1,
        )
        parsed = parse_multiple_ligand_output_text(broken, self.members)
        self.assertFalse(parsed["ok"])
        self.assertEqual(parsed["error"]["code"], "MULTIPLE_LIGAND_MODEL_SCORE_MISSING")

    def test_parser_requires_models_from_one_in_continuous_order(self) -> None:
        broken = _joint_model(1, -10.0, 0.0, 0.0) + _joint_model(
            3,
            -9.0,
            1.0,
            2.0,
        )
        parsed = parse_multiple_ligand_output_text(
            broken,
            self.members,
        )
        self.assertFalse(parsed["ok"])
        self.assertEqual(
            parsed["error"]["code"],
            "MULTIPLE_LIGAND_MODEL_SEQUENCE_INVALID",
        )

    def test_output_shell_tags_require_column_zero_and_uppercase(self) -> None:
        cases = [
            (
                JOINT_OUTPUT.replace("MODEL 1", " MODEL 1", 1),
                "MULTIPLE_LIGAND_OUTPUT_TAG_ALIGNMENT_INVALID",
            ),
            (
                JOINT_OUTPUT.replace("MODEL 1", "model 1", 1),
                "MULTIPLE_LIGAND_OUTPUT_TAG_CASE_INVALID",
            ),
            (
                JOINT_OUTPUT.replace("ENDMDL", "endmdl", 1),
                "MULTIPLE_LIGAND_OUTPUT_TAG_CASE_INVALID",
            ),
            (
                JOINT_OUTPUT.replace(
                    "TORSDOF 1\nENDMDL",
                    "TORSDOF 1\n remark trailing\nENDMDL",
                    1,
                ),
                "MULTIPLE_LIGAND_OUTPUT_TAG_ALIGNMENT_INVALID",
            ),
        ]
        for content, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                parsed = parse_multiple_ligand_output_text(
                    content,
                    self.members,
                )
                self.assertFalse(parsed["ok"])
                self.assertEqual(
                    parsed["error"]["code"],
                    expected_code,
                )

    def test_output_rejects_trailing_record_prefix_spoof(self) -> None:
        broken = JOINT_OUTPUT.replace(
            "TORSDOF 1\nENDMDL",
            "TORSDOF 1\nCONECTX 1 2\nENDMDL",
            1,
        )
        parsed = parse_multiple_ligand_output_text(
            broken,
            self.members,
        )
        self.assertFalse(parsed["ok"])
        self.assertEqual(
            parsed["error"]["code"],
            "MULTIPLE_LIGAND_MODEL_TRAILING_STRUCTURE",
        )

    def test_output_grid_coverage_uses_pdbqt_rounding_tolerance(
        self,
    ) -> None:
        box = {
            "center_x": 0.0,
            "center_y": 0.0,
            "center_z": 0.0,
            "size_x": 20.0,
            "size_y": 20.0,
            "size_z": 20.0,
        }
        grid = {
            # 54 * 0.37499 / 2 = 10.12473 Å.  A PDBQT coordinate
            # serialized as 10.125 is only 0.00027 Å beyond the exact
            # boundary and must remain accepted by the 0.0005 Å output
            # quantization allowance; 10.126 is genuinely outside.
            "spacing_angstrom": 0.37499,
            "axis_intervals": {"x": 54, "y": 54, "z": 54},
            "config_number_serialization": (
                multiple_ligands.CONFIG_FLOAT_SERIALIZATION
            ),
        }

        def models_at(x_value: float) -> list[dict]:
            def bounds(value: float) -> dict:
                return {
                    "min": {"x": value, "y": 0.0, "z": 0.0},
                    "max": {"x": value, "y": 0.0, "z": 0.0},
                }

            return [
                {
                    "mode": 1,
                    "members": [
                        {
                            "member_index": member_index,
                            "atom_count": 1,
                            "heavy_atom_count": 1,
                            "hydrogen_atom_count": 0,
                            "coordinate_count": 1,
                            "heavy_coordinate_count": 1,
                            "hydrogen_coordinate_count": 0,
                            "coordinate_bounds": bounds(
                                x_value if member_index == 1 else 0.0
                            ),
                            "heavy_coordinate_bounds": bounds(
                                x_value if member_index == 1 else 0.0
                            ),
                            "hydrogen_coordinate_bounds": None,
                        }
                        for member_index in (1, 2)
                    ],
                }
            ]

        exact = multiple_ligands._validate_output_grid_coverage(
            box,
            grid,
            models_at(10.124),
        )
        rounded = multiple_ligands._validate_output_grid_coverage(
            box,
            grid,
            models_at(10.125),
        )
        outside = multiple_ligands._validate_output_grid_coverage(
            box,
            grid,
            models_at(10.126),
        )
        self.assertTrue(exact["ok"], exact)
        self.assertTrue(rounded["ok"], rounded)
        self.assertFalse(outside["ok"])
        self.assertEqual(
            outside["error"]["code"],
            "MULTIPLE_LIGAND_OUTPUT_OUTSIDE_EFFECTIVE_GRID",
        )

    def test_path_helper_detects_windows_reparse_attribute(self) -> None:
        details = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0x400,
        )
        with (
            patch.object(
                multiple_ligands.os,
                "lstat",
                return_value=details,
            ),
            patch.object(
                multiple_ligands.stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                0x400,
                create=True,
            ),
        ):
            self.assertTrue(
                multiple_ligands._path_is_link_or_reparse(
                    Path("mock-junction")
                )
            )


class MultipleLigandRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        created = create_project("joint_project", str(self.root))
        self.assertTrue(created["ok"], created)
        self.project_dir = Path(created["project_dir"])

        receptor = self.root / "receptor_source.pdbqt"
        receptor.write_text(
            "ATOM      1  N   REC A   1       0.000   0.000   0.000  1.00  0.00     0.000 N\n",
            encoding="utf-8",
        )
        imported = import_receptor_pdbqt(str(self.project_dir), str(receptor))
        self.assertTrue(imported["ok"], imported)

        self.ligand_1 = self.root / "ligand_one.pdbqt"
        self.ligand_2 = self.root / "ligand_two.pdbqt"
        self.ligand_1.write_text(_pdbqt("C1", "C", 0), encoding="utf-8")
        self.ligand_2.write_text(_pdbqt("O1", "OA", 1), encoding="utf-8")
        self.vina = self.root / "vina.exe"
        self.vina.write_bytes(b"mock vina 1.2.7")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _detection(self, *, version: str = "1.2.7") -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version=version,
            path=str(self.vina),
            message="已检测到 AutoDock Vina。",
            source="configured",
            capabilities={
                "features": {
                    "multiple_ligands": {
                        "supported": True,
                        "status": "supported",
                    },
                    "no_refine": {"supported": True, "status": "supported"},
                    "force_even_voxels": {
                        "supported": True,
                        "status": "supported",
                    },
                },
            },
        )

    def _prepare(self) -> dict:
        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.detect",
            return_value=self._detection(),
        ):
            return prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1), str(self.ligand_2)],
            )

    def _successful_runner(self, *, mutate_after: callable | None = None):
        def runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            command = list(command)
            output_relative = command[command.index("--out") + 1]
            output_path = Path(cwd) / output_relative
            output_path.write_text(JOINT_OUTPUT, encoding="utf-8")
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            if mutate_after is not None:
                mutate_after()
            return ManagedRunResult(123, 0, "")

        return runner

    def _run(self, run_id: str, *, runner=None) -> dict:
        with (
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.detect",
                return_value=self._detection(),
            ),
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.run_managed",
                side_effect=runner or self._successful_runner(),
            ),
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.get_process_identity",
                side_effect=lambda pid: {
                    "pid": pid,
                    "executable_path": (
                        str(self.vina)
                        if pid == 123
                        else sys.executable
                    ),
                    "creation_token": f"mock-{pid}",
                },
            ),
        ):
            return execute_multiple_ligand_run(str(self.project_dir), run_id)

    def _set_protocol(self, protocol: dict) -> None:
        path = self.project_dir / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["docking_protocol"] = protocol
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_prepare_requires_exactly_two_unique_members(self) -> None:
        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.detect",
            return_value=self._detection(),
        ):
            too_few = prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1)],
            )
            duplicate = prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1), str(self.ligand_1)],
            )
        self.assertEqual(
            too_few["error"]["code"],
            "MULTIPLE_LIGAND_EXACTLY_TWO_REQUIRED",
        )
        self.assertEqual(
            duplicate["error"]["code"],
            "MULTIPLE_LIGAND_DUPLICATE_INPUT",
        )

    def test_prepare_records_dockstart_version(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        metadata = prepared["metadata"]
        self.assertEqual(metadata["app_version"], multiple_ligands.__version__)
        self.assertEqual(
            metadata["app"],
            {
                "name": "DockStart",
                "version": multiple_ligands.__version__,
            },
        )

    def test_prepare_freezes_joint_search_complexity_and_box_fit(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        metadata = prepared["metadata"]
        complexity = metadata["search_complexity"]
        self.assertEqual(complexity["rigid_body_dof"], 12)
        self.assertEqual(complexity["total_search_torsions"], 0)
        self.assertEqual(complexity["total_declared_torsdof"], 1)
        self.assertEqual(complexity["total_search_dof"], 12)
        self.assertTrue(complexity["within_experimental_limit"])
        self.assertEqual(
            complexity["maximum_experimental_member_atoms"],
            multiple_ligands.MAX_EXPERIMENTAL_MEMBER_ATOMS,
        )
        self.assertEqual(
            complexity["maximum_experimental_member_bytes"],
            multiple_ligands.MAX_EXPERIMENTAL_MEMBER_BYTES,
        )
        coverage = metadata["box_coverage"]
        self.assertTrue(
            coverage[
                "all_applicable_rigid_heavy_atom_diameters_fit_box_diagonal"
            ]
        )
        self.assertEqual(len(coverage["members"]), 2)
        self.assertTrue(
            all(
                member[
                    "rigid_heavy_atom_diameter_fits_box_diagonal"
                ]
                for member in coverage["members"]
            )
        )

    def test_prepare_uses_vina_effective_torsions_for_complexity_limit(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _nested_branch_pdbqt(13, torsdof=0),
            encoding="utf-8",
        )
        self.ligand_2.write_text(
            _nested_branch_pdbqt(12, torsdof=0),
            encoding="utf-8",
        )
        response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_FLEXIBILITY_RISK_LIMIT_EXCEEDED",
        )
        details = json.loads(response["error"]["raw_error"])
        self.assertEqual(details["total_search_torsions"], 25)
        self.assertEqual(details["total_declared_torsdof"], 0)
        self.assertEqual(details["total_search_dof"], 37)

    def test_ad4_command_uses_two_ligands_and_precomputed_maps(self) -> None:
        loaded = multiple_ligands.load_project(str(self.project_dir))
        self.assertTrue(loaded["ok"], loaded)
        project = multiple_ligands._project_from_dict(
            loaded["project"],
            self.project_dir,
        )
        maps_prefix = "runs/run_001/inputs/ad4_maps/receptor"
        command = multiple_ligands._build_command(
            str(self.vina),
            "run_001",
            ad4_maps_prefix=maps_prefix,
        )
        ligand_index = command.index("--ligand")
        self.assertEqual(
            command[ligand_index + 1 : ligand_index + 3],
            [
                "runs/run_001/inputs/ligand_001.pdbqt",
                "runs/run_001/inputs/ligand_002.pdbqt",
            ],
        )
        self.assertEqual(command[command.index("--maps") + 1], maps_prefix)
        self.assertEqual(command[command.index("--scoring") + 1], "ad4")

        config = multiple_ligands._build_config(
            project,
            "run_001",
            ad4_maps_prefix=maps_prefix,
        )
        self.assertIn("scoring = ad4", config)
        self.assertNotIn("receptor =", config)
        self.assertNotIn("center_x =", config)
        self.assertNotIn("spacing =", config)

    def test_standard_ad4_prepare_freezes_maps_and_executes_joint_run(self) -> None:
        imported = import_ligand_pdbqt(
            str(self.project_dir),
            str(self.ligand_1),
        )
        self.assertTrue(imported["ok"], imported)
        autogrid = self.root / "autogrid4.exe"
        autogrid.write_bytes(b"mock autogrid")

        def fake_autogrid(
            _executable: str,
            gpf_file: str,
            log_file: str,
            working_directory: str | Path,
            **_kwargs: object,
        ) -> dict:
            root = Path(working_directory)
            gpf_text = (root / gpf_file).read_text(encoding="utf-8")
            ligand_types = next(
                line.split()[1:]
                for line in gpf_text.splitlines()
                if line.startswith("ligand_types ")
            )
            for name in (
                "receptor.maps.fld",
                *(f"receptor.{atom_type}.map" for atom_type in ligand_types),
                "receptor.e.map",
                "receptor.d.map",
            ):
                (root / name).write_text(f"mock {name}\n", encoding="utf-8")
            (root / log_file).write_text(
                "Successful Completion\n",
                encoding="utf-8",
            )
            return {
                "ok": True,
                "command": ["autogrid4", "-p", gpf_file, "-l", log_file],
                "exit_code": 0,
                "stdout": "AutoGrid complete\n",
                "stderr": "",
                "error": "",
            }

        autogrid_detection = ToolCheckResult(
            key="autogrid4",
            name="AutoGrid4",
            status="ok",
            version="4.2.6",
            path=str(autogrid),
            message="ok",
            source="configured",
        )
        with patch(
            "dockstart_core.autogrid.autogrid_adapter.detect",
            return_value=autogrid_detection,
        ):
            generated = generate_maps(
                str(self.project_dir),
                {"ligand_atom_types": ["C", "OA"]},
                runner=fake_autogrid,
            )
        self.assertTrue(generated["ok"], generated)

        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.detect",
            return_value=self._detection(),
        ):
            prepared = prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1), str(self.ligand_2)],
            )
        self.assertTrue(prepared["ok"], prepared)
        metadata = prepared["metadata"]
        self.assertEqual(metadata["scoring_protocol"], "ad4_maps")
        self.assertEqual(metadata["scoring_function"], "ad4")
        self.assertTrue(metadata["snapshots"]["ad4_maps"]["files"])
        self.assertIn("--maps", metadata["command"])
        self.assertIn("--scoring", metadata["command"])
        config = (
            self.project_dir / metadata["config_file"]
        ).read_text(encoding="utf-8")
        self.assertIn("scoring = ad4", config)
        self.assertNotIn("receptor =", config)

        executed = self._run(prepared["run_id"])
        self.assertTrue(executed["ok"], executed)
        self.assertEqual(executed["metadata"]["status"], "finished")

    def test_degenerate_leaf_branches_do_not_inflate_search_dimension(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _nested_branch_pdbqt(
                13,
                torsdof=0,
                terminal_extra_atom=False,
            ),
            encoding="utf-8",
        )
        self.ligand_2.write_text(
            _nested_branch_pdbqt(
                12,
                torsdof=0,
                terminal_extra_atom=False,
            ),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        complexity = prepared["metadata"]["search_complexity"]
        self.assertEqual(complexity["total_branch_records"], 25)
        self.assertEqual(complexity["total_degenerate_branches"], 2)
        self.assertEqual(complexity["total_search_torsions"], 23)
        self.assertEqual(complexity["total_search_dof"], 35)

    def test_torsdof_without_branches_does_not_inflate_search_dimension(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _pdbqt("C1", "C", 25),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        complexity = prepared["metadata"]["search_complexity"]
        self.assertEqual(complexity["total_search_torsions"], 0)
        self.assertEqual(complexity["total_declared_torsdof"], 26)
        self.assertEqual(complexity["total_search_dof"], 12)
        self.assertEqual(
            complexity["torsion_declaration_difference_members"],
            [1, 2],
        )

    def test_branch_records_accept_tabs_and_multiple_spaces(self) -> None:
        valid = _topology_pdbqt(2)
        cases = [
            valid.replace("BRANCH 1 2", "BRANCH\t1\t2").replace(
                "ENDBRANCH 1 2",
                "ENDBRANCH\t1\t2",
            ),
            valid.replace("BRANCH 1 2", "BRANCH    1    2").replace(
                "ENDBRANCH 1 2",
                "ENDBRANCH    1    2",
            ),
        ]
        for content in cases:
            with self.subTest(content=content):
                validation = (
                    multiple_ligands._validate_single_ligand_torsion_tree(
                        content.splitlines(),
                        1,
                    )
                )
                self.assertTrue(validation["ok"], validation)
                self.assertEqual(validation["branch_count"], 1)
                self.assertEqual(validation["effective_branch_count"], 1)

    def test_noncanonical_pdbqt_record_syntax_fails_closed(self) -> None:
        valid = _topology_pdbqt(2)
        cases = [
            (
                valid.replace("BRANCH 1 2", " BRANCH 1 2"),
                "MULTIPLE_LIGAND_INPUT_TAG_ALIGNMENT_INVALID",
            ),
            (
                valid.replace("BRANCH 1 2", "branch 1 2"),
                "MULTIPLE_LIGAND_INPUT_TAG_CASE_INVALID",
            ),
            (
                valid.replace("ENDROOT\n", "ENDROOT\n   \t\n"),
                "MULTIPLE_LIGAND_INPUT_WHITESPACE_RECORD_INVALID",
            ),
            (
                valid.replace("ATOM  ", "ATOM\t", 1),
                "MULTIPLE_LIGAND_INPUT_ATOM_RECORD_INVALID",
            ),
            (
                valid.replace("BRANCH 1 2", "BRANCH 1 2 trailing"),
                "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
            ),
        ]
        for content, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                validation = (
                    multiple_ligands._validate_single_ligand_torsion_tree(
                        content.splitlines(),
                        1,
                    )
                )
                self.assertFalse(validation["ok"])
                self.assertEqual(
                    validation["error"]["code"],
                    expected_code,
                )

    def test_nested_branch_parent_must_be_in_current_segment(self) -> None:
        invalid = _nested_branch_pdbqt(2).replace(
            "BRANCH 2 3",
            "BRANCH 1 3",
        ).replace(
            "ENDBRANCH 2 3",
            "ENDBRANCH 1 3",
        )
        validation = (
            multiple_ligands._validate_single_ligand_torsion_tree(
                invalid.splitlines(),
                1,
            )
        )
        self.assertFalse(validation["ok"])
        self.assertEqual(
            validation["error"]["code"],
            "MULTIPLE_LIGAND_INPUT_BRANCH_PARENT_INVALID",
        )

    def test_coincident_branch_axis_fails_strict_quality_gate(self) -> None:
        invalid = _topology_pdbqt(2).replace(
            "2.000   2.000   3.000",
            "1.000   2.000   3.000",
            1,
        )
        validation = (
            multiple_ligands._validate_single_ligand_torsion_tree(
                invalid.splitlines(),
                1,
            )
        )
        self.assertFalse(validation["ok"])
        self.assertEqual(
            validation["error"]["code"],
            "MULTIPLE_LIGAND_INPUT_BRANCH_AXIS_INVALID",
        )

    def test_axis_aligned_extent_is_warning_not_rotation_blocker(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _rigid_pdbqt_with_coordinates(
                [(-12.0, 0.0, 0.0), (12.0, 0.0, 0.0)]
            ),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        coverage = prepared["metadata"]["box_coverage"]
        self.assertEqual(
            coverage["axis_aligned_oversized_members"],
            [1],
        )
        self.assertFalse(
            coverage["all_input_axis_aligned_extents_fit"]
        )
        self.assertTrue(
            coverage["members"][0][
                "rigid_heavy_atom_diameter_fits_box_diagonal"
            ]
        )
        self.assertTrue(
            any(
                "全局搜索允许旋转" in warning
                for warning in prepared["metadata"]["warnings"]
            )
        )

    def test_flexible_input_diameter_is_not_a_rigid_fit_blocker(self) -> None:
        self.ligand_1.write_text(
            _flexible_pdbqt_with_coordinates(-20.0, 20.0, 21.0),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        coverage = prepared["metadata"]["box_coverage"]
        member = coverage["members"][0]
        self.assertEqual(member["effective_search_torsions"], 1)
        self.assertFalse(
            member["rigid_heavy_atom_diameter_gate_applicable"]
        )
        self.assertIsNone(
            member["maximum_heavy_atom_distance_angstrom"]
        )
        self.assertIsNone(
            member["rigid_heavy_atom_diameter_fits_box_diagonal"]
        )
        self.assertEqual(
            coverage["flexible_diameter_audit_members"],
            [1],
        )

    def test_rigid_diameter_excludes_distant_hd_atom(self) -> None:
        self.ligand_1.write_text(
            _rigid_pdbqt_with_hydrogen(0.0, 100.0),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        member = prepared["metadata"]["box_coverage"]["members"][0]
        self.assertTrue(
            member["rigid_heavy_atom_diameter_gate_applicable"]
        )
        self.assertEqual(
            member["maximum_heavy_atom_distance_angstrom"],
            0.0,
        )
        self.assertTrue(
            member["rigid_heavy_atom_diameter_fits_box_diagonal"]
        )

    def test_degenerate_leaf_is_rigid_for_box_diameter_gate(self) -> None:
        self.ligand_1.write_text(
            _degenerate_branch_pdbqt(-20.0, 20.0),
            encoding="utf-8",
        )
        response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            (
                "MULTIPLE_LIGAND_BOX_DIAGONAL_TOO_SMALL_"
                "FOR_INPUT_GEOMETRY"
            ),
        )
        details = json.loads(response["error"]["raw_error"])
        self.assertEqual(
            details[0]["maximum_heavy_atom_distance_angstrom"],
            40.0,
        )

    def test_effective_grid_diagonal_not_requested_diagonal_is_gate(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _rigid_pdbqt_with_coordinates(
                [(-17.5, 0.0, 0.0), (17.5, 0.0, 0.0)]
            ),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        coverage = prepared["metadata"]["box_coverage"]
        requested_diagonal = math.sqrt(3 * (20.0**2))
        effective_diagonal = coverage[
            "effective_grid_diagonal_angstrom"
        ]
        member = coverage["members"][0]
        self.assertLess(requested_diagonal, 35.0)
        self.assertGreaterEqual(effective_diagonal, 35.0)
        self.assertEqual(
            member["maximum_heavy_atom_distance_angstrom"],
            35.0,
        )
        self.assertTrue(
            member["rigid_heavy_atom_diameter_fits_box_diagonal"]
        )

    def test_prepare_rejects_diameter_larger_than_box_diagonal(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _rigid_pdbqt_with_coordinates(
                [(-20.0, 0.0, 0.0), (20.0, 0.0, 0.0)]
            ),
            encoding="utf-8",
        )
        response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            (
                "MULTIPLE_LIGAND_BOX_DIAGONAL_TOO_SMALL_"
                "FOR_INPUT_GEOMETRY"
            ),
        )
        details = json.loads(response["error"]["raw_error"])
        self.assertEqual(
            details[0]["maximum_heavy_atom_distance_angstrom"],
            40.0,
        )

    def test_source_pose_outside_box_is_audited_but_not_rejected(
        self,
    ) -> None:
        self.ligand_1.write_text(
            _rigid_pdbqt_with_coordinates([(100.0, 100.0, 100.0)]),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        coverage = prepared["metadata"]["box_coverage"]
        self.assertEqual(
            coverage["outside_requested_box_source_pose_members"],
            [1],
        )
        self.assertFalse(
            coverage["members"][0][
                "source_pose_inside_requested_box"
            ]
        )
        self.assertTrue(
            coverage["members"][0][
                "rigid_heavy_atom_diameter_fits_box_diagonal"
            ]
        )
        self.assertTrue(
            any(
                "全局搜索会移动配体" in warning
                for warning in prepared["metadata"]["warnings"]
            )
        )

    def test_prepare_rejects_incompatible_protocols_and_old_vina(self) -> None:
        cases = [
            (
                {
                    "engine": "vina",
                    "protocol_id": "flexible_single",
                    "mode": "flexible",
                    "receptor_mode": "flexible",
                    "run_mode": "dock",
                },
                "MULTIPLE_LIGAND_RIGID_RECEPTOR_REQUIRED",
            ),
            (
                {
                    "engine": "vina",
                    "protocol_id": "vina_maps",
                    "grid_source": "precomputed_maps",
                    "run_mode": "dock",
                },
                "MULTIPLE_LIGAND_PRECOMPUTED_MAPS_UNSUPPORTED",
            ),
            (
                {
                    "engine": "vina",
                    "protocol_id": "rigid_single",
                    "run_mode": "score_only",
                },
                "MULTIPLE_LIGAND_GLOBAL_SEARCH_REQUIRED",
            ),
            (
                {
                    "engine": "ad4_maps",
                    "protocol_id": "ad4zn_beta",
                    "run_mode": "dock",
                },
                "MULTIPLE_LIGAND_AD4_SUBPROTOCOL_UNSUPPORTED",
            ),
        ]
        for protocol, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self._set_protocol(protocol)
                response = self._prepare()
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], expected_code)

        self._set_protocol(
            {
                "engine": "vina",
                "protocol_id": "rigid_single",
                "run_mode": "dock",
            },
        )
        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.detect",
            return_value=self._detection(version="1.1.2"),
        ):
            response = prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1), str(self.ligand_2)],
            )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_VINA_VERSION_UNSUPPORTED",
        )

    def test_prepare_rejects_hydrated_and_macrocycle_pseudo_atom_types(self) -> None:
        for atom_type in ("W", "G0", "CG0"):
            with self.subTest(atom_type=atom_type):
                unsupported = self.root / f"unsupported_{atom_type}.pdbqt"
                unsupported.write_text(
                    _pdbqt("D1", atom_type, 0),
                    encoding="utf-8",
                )
                with patch(
                    "dockstart_core.multiple_ligands.vina_adapter.detect",
                    return_value=self._detection(),
                ):
                    response = prepare_multiple_ligand_run(
                        str(self.project_dir),
                        [str(self.ligand_1), str(unsupported)],
                    )
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    "MULTIPLE_LIGAND_UNSUPPORTED_ATOM_TYPE",
                )

    def test_prepare_rejects_malformed_or_multi_model_torsion_trees(
        self,
    ) -> None:
        valid = _topology_pdbqt(2)
        cases = [
            (
                "MODEL 1\n" + valid + "ENDMDL\n",
                "MULTIPLE_LIGAND_INPUT_MODEL_FORBIDDEN",
            ),
            (
                valid.replace("ROOT\n", "", 1),
                "MULTIPLE_LIGAND_INPUT_ROOT_COUNT_INVALID",
            ),
            (
                valid.replace("ENDBRANCH 1 2", "ENDBRANCH 1 3"),
                "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
            ),
            (
                valid.replace("ENDBRANCH 1 2\n", ""),
                "MULTIPLE_LIGAND_INPUT_BRANCH_TREE_INVALID",
            ),
            (
                valid.replace("BRANCH 1 2", "BRANCH 1 99").replace(
                    "ENDBRANCH 1 2",
                    "ENDBRANCH 1 99",
                ),
                "MULTIPLE_LIGAND_INPUT_BRANCH_ENDPOINT_INVALID",
            ),
            (
                valid.replace("BRANCH 1 2", "BRANCH 1 1").replace(
                    "ENDBRANCH 1 2",
                    "ENDBRANCH 1 1",
                ),
                "MULTIPLE_LIGAND_INPUT_BRANCH_ENDPOINT_INVALID",
            ),
            (
                valid.replace("ATOM      3", "ATOM      2"),
                "MULTIPLE_LIGAND_INPUT_ATOM_SERIAL_INVALID",
            ),
            (
                valid + "TORSDOF 1\n",
                "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            ),
            (
                valid.replace("TORSDOF 1\n", ""),
                "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            ),
            (
                valid.replace("TORSDOF 1", "TORSDOF invalid"),
                "MULTIPLE_LIGAND_INPUT_TORSDOF_INVALID",
            ),
            (
                valid + "REMARK trailing record\n",
                "MULTIPLE_LIGAND_INPUT_TORSDOF_NOT_TERMINAL",
            ),
        ]
        for index, (content, expected_code) in enumerate(cases):
            with self.subTest(index=index, expected_code=expected_code):
                self.ligand_1.write_text(content, encoding="utf-8")
                response = self._prepare()
                self.assertFalse(response["ok"])
                self.assertEqual(
                    response["error"]["code"],
                    expected_code,
                )
        self.ligand_1.write_text(
            _pdbqt("C1", "C", 0),
            encoding="utf-8",
        )

    def test_member_atom_cap_precedes_quadratic_diameter_scan(self) -> None:
        self.ligand_1.write_text(
            _large_rigid_pdbqt(
                multiple_ligands.MAX_EXPERIMENTAL_MEMBER_ATOMS + 1
            ),
            encoding="utf-8",
        )
        with patch.object(
            multiple_ligands,
            "_maximum_interatomic_distance",
            side_effect=AssertionError(
                "quadratic diameter scan must not run"
            ),
        ):
            response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_ATOM_LIMIT_EXCEEDED",
        )

    def test_member_atom_cap_accepts_exact_boundary(self) -> None:
        self.ligand_1.write_text(
            _large_rigid_pdbqt(
                multiple_ligands.MAX_EXPERIMENTAL_MEMBER_ATOMS
            ),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(
            prepared["metadata"]["members"][0]["stats"]["atom_count"],
            multiple_ligands.MAX_EXPERIMENTAL_MEMBER_ATOMS,
        )

    def test_member_file_byte_cap_precedes_text_parser(self) -> None:
        content = _pdbqt("C1", "C", 0)
        self.ligand_1.write_text(content, encoding="utf-8")
        with (
            patch.object(
                multiple_ligands,
                "MAX_EXPERIMENTAL_MEMBER_BYTES",
                len(content.encode("utf-8")) - 1,
            ),
            patch.object(
                multiple_ligands,
                "_validate_single_ligand_torsion_tree",
                side_effect=AssertionError(
                    "oversized member must not reach text parser"
                ),
            ),
        ):
            response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_FILE_LIMIT_EXCEEDED",
        )

    def test_real_member_file_byte_cap_rejects_sixteen_mib_plus_one(
        self,
    ) -> None:
        self.assertEqual(
            multiple_ligands.MAX_EXPERIMENTAL_MEMBER_BYTES,
            16 * 1024 * 1024,
        )
        with self.ligand_1.open("wb") as handle:
            handle.truncate(
                multiple_ligands.MAX_EXPERIMENTAL_MEMBER_BYTES + 1
            )
        response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_FILE_LIMIT_EXCEEDED",
        )

    def test_prepare_requires_explicit_multiple_ligands_capability(self) -> None:
        detection = self._detection()
        detection.capabilities["features"].pop("multiple_ligands")
        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.detect",
            return_value=detection,
        ):
            response = prepare_multiple_ligand_run(
                str(self.project_dir),
                [str(self.ligand_1), str(self.ligand_2)],
            )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_VINA_CAPABILITY_UNCONFIRMED",
        )

    def test_joint_atom_type_union_enforces_two_gib_grid_limit(self) -> None:
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["box"].update(
            {
                "size_x": 52.0,
                "size_y": 52.0,
                "size_z": 52.0,
            }
        )
        project["vina"]["spacing"] = 0.1
        project_path.write_text(
            json.dumps(project, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        single_member = multiple_ligands._validate_vina_grid_resource(
            project["box"],
            0.1,
            ["C"],
            False,
        )
        self.assertTrue(single_member["ok"], single_member)
        self.assertLessEqual(
            single_member["grid_estimate"]["estimated_map_bytes"],
            single_member["grid_estimate"]["hard_limit_bytes"],
        )

        response = self._prepare()
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_GRID_RESOURCE_LIMIT_EXCEEDED",
        )
        estimate = json.loads(response["error"]["raw_error"])
        self.assertEqual(estimate["atom_types"], ["C", "OA"])
        self.assertGreater(
            estimate["estimated_map_bytes"],
            estimate["hard_limit_bytes"],
        )

    def test_prepare_freezes_ordered_members_and_single_ligand_flag(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        metadata = prepared["metadata"]
        self.assertEqual(metadata["protocol_id"], PROTOCOL_ID)
        self.assertEqual(
            [member["member_index"] for member in metadata["members"]],
            [1, 2],
        )
        self.assertNotEqual(
            metadata["members"][0]["sha256"],
            metadata["members"][1]["sha256"],
        )
        self.assertEqual(
            metadata["grid_estimate"]["atom_types"],
            ["C", "OA"],
        )
        self.assertEqual(metadata["grid_estimate"]["map_count"], 2)
        self.assertEqual(
            metadata["grid_estimate"]["hard_limit_bytes"],
            2 * 1024 * 1024 * 1024,
        )
        command = metadata["command"]
        self.assertEqual(command.count("--ligand"), 1)
        ligand_index = command.index("--ligand")
        self.assertEqual(
            command[ligand_index + 1 : ligand_index + 3],
            [
                f"runs/{prepared['run_id']}/inputs/ligand_001.pdbqt",
                f"runs/{prepared['run_id']}/inputs/ligand_002.pdbqt",
            ],
        )
        self.assertEqual(command[ligand_index + 3], "--out")
        config = (
            self.project_dir
            / "runs"
            / prepared["run_id"]
            / "config_snapshot.txt"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ligand =", config)
        self.assertIn(
            f"receptor = runs/{prepared['run_id']}/inputs/receptor.pdbqt",
            config,
        )

    def test_config_numbers_round_trip_into_grid_estimate(self) -> None:
        box = {
            "center_x": 123456.789012345,
            "center_y": -123456.789012345,
            "center_z": 0.00000123456789,
            "size_x": 20.25001,
            "size_y": 20.0,
            "size_z": 20.0,
        }
        updated_box = update_box_params(
            str(self.project_dir),
            box,
        )
        self.assertTrue(updated_box["ok"], updated_box)
        updated_vina = update_vina_params(
            str(self.project_dir),
            {"spacing": 0.375, "force_even_voxels": False},
        )
        self.assertTrue(updated_vina["ok"], updated_vina)
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        metadata = prepared["metadata"]
        config = (
            self.project_dir
            / "runs"
            / prepared["run_id"]
            / "config_snapshot.txt"
        ).read_text(encoding="utf-8")
        values = {
            key.strip(): value.strip()
            for key, value in (
                line.split("=", 1)
                for line in config.splitlines()
                if "=" in line
            )
        }
        for key in (
            "center_x",
            "center_y",
            "center_z",
            "size_x",
            "size_y",
            "size_z",
            "spacing",
        ):
            source = (
                metadata["vina"][key]
                if key == "spacing"
                else metadata["box"][key]
            )
            self.assertEqual(float(values[key]), float(source))
        self.assertNotEqual(values["size_x"], "20.25")
        self.assertEqual(
            metadata["grid_estimate"]["axis_intervals"]["x"],
            55,
        )
        self.assertEqual(
            metadata["grid_estimate"][
                "config_number_serialization"
            ],
            multiple_ligands.CONFIG_FLOAT_SERIALIZATION,
        )

        updated_even = update_vina_params(
            str(self.project_dir),
            {"force_even_voxels": True},
        )
        self.assertTrue(updated_even["ok"], updated_even)
        prepared_even = self._prepare()
        self.assertTrue(prepared_even["ok"], prepared_even)
        self.assertEqual(
            prepared_even["metadata"]["grid_estimate"][
                "axis_intervals"
            ]["x"],
            56,
        )

    def test_run_creates_joint_results_member_poses_and_report(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        result = self._run(prepared["run_id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["pose_count"], 2)
        self.assertEqual(result["best_affinity"], -10.0)
        self.assertEqual(len(result["scores"]), 3)
        self.assertEqual(
            [row["pose_available"] for row in result["scores"]],
            [True, True, False],
        )
        self.assertTrue(result["scores"][0]["score_is_joint"])
        output_coverage = result["metadata"]["output_box_coverage"]
        self.assertTrue(
            output_coverage[
                "all_output_movable_heavy_atoms_inside_effective_grid"
            ]
        )
        self.assertEqual(output_coverage["audited_model_count"], 2)
        self.assertEqual(output_coverage["audited_member_pose_count"], 4)
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["best_affinity"], -10.0)
        generic_scores = load_scores_csv(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertTrue(generic_scores["ok"], generic_scores)
        self.assertEqual(
            [
                row["affinity_kcal_mol"]
                for row in generic_scores["scores"]
            ],
            [-10.0, -9.2, -8.7],
        )
        self.assertEqual(
            [
                row["pose_available"]
                for row in generic_scores["scores"]
            ],
            [True, True, False],
        )

        run_dir = self.project_dir / "runs" / prepared["run_id"]
        joint = json.loads(
            (run_dir / "joint_poses.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(joint["models"][0]["members"][0]["member_index"], 1)
        self.assertFalse(joint["per_member_scores_available"])
        self.assertTrue(
            joint["output_box_coverage"][
                "all_output_movable_heavy_atoms_inside_effective_grid"
            ]
        )
        self.assertTrue((run_dir / "poses/mode_001/member_001.pdbqt").is_file())
        self.assertTrue((run_dir / "poses/mode_001/member_002.pdbqt").is_file())
        report = (run_dir / "multi_ligand_report.md").read_text(
            encoding="utf-8",
        )
        self.assertIn("多配体共同对接（实验性）", report)
        self.assertIn("不是串行批量筛选", report)
        self.assertIn("不提供单个配体的独立评分贡献", report)
        self.assertIn("## 对接箱体", report)
        self.assertIn("| center_x | 0.0 |", report)
        self.assertIn("## 搜索复杂度门禁", report)
        self.assertIn("| PDBQT BRANCH 记录总数 | 0 |", report)
        self.assertIn("| 合计有效搜索扭转 | 0 |", report)
        self.assertIn("| PDBQT 声明 TORSDOF 合计 | 1 |", report)
        self.assertIn("## Box 几何覆盖门禁", report)
        self.assertIn(
            "源文件绝对位置和当前朝向不构成全局搜索硬门禁",
            report,
        )
        self.assertIn("## 输出构象 Box 覆盖复核", report)
        self.assertIn(
            "| 全部可移动重原子位于实际网格内 | true |",
            report,
        )
        self.assertIn("## Vina 输出文本完整性", report)
        self.assertIn("## 完整冻结 Vina 参数", report)
        for key in result["metadata"]["vina"]:
            self.assertIn(f"| {key} |", report)
        self.assertIn("## 实际命令", report)
        self.assertIn('"--ligand"', report)
        self.assertIn("## multiple_ligands 能力证据", report)
        self.assertIn('"supported": true', report)
        self.assertIn("## 完整性证据", report)
        for sha256 in (
            result["metadata"]["snapshots"]["receptor"]["sha256"],
            result["metadata"]["snapshots"]["config"]["sha256"],
            result["metadata"]["artifacts"]["output"]["sha256"],
            result["metadata"]["execution_vina"]["sha256"],
        ):
            self.assertIn(sha256, report)
        self.assertIn("## 时间", report)
        self.assertIn("| 1 | -10.0 | 0.0 | 0.0 | 是 |", report)

        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8"),
        )
        summary = next(
            item
            for item in project["runs"]
            if item["run_id"] == prepared["run_id"]
        )
        self.assertEqual(summary["status"], "finished")
        self.assertEqual(summary["protocol_id"], PROTOCOL_ID)

    def test_run_rejects_output_atom_outside_effective_grid(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        outside_output = JOINT_OUTPUT.replace(
            "       1.000   2.000   3.000",
            "      12.000   2.000   3.000",
            1,
        )

        def outside_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            command = list(command)
            output_relative = command[command.index("--out") + 1]
            (Path(cwd) / output_relative).write_text(
                outside_output,
                encoding="utf-8",
            )
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        result = self._run(
            prepared["run_id"],
            runner=outside_runner,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MULTIPLE_LIGAND_OUTPUT_OUTSIDE_EFFECTIVE_GRID",
        )
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["status"], "failed")
        audit = status["metadata"]["output_box_coverage"]
        self.assertFalse(
            audit[
                "all_output_movable_heavy_atoms_inside_effective_grid"
            ]
        )
        self.assertEqual(
            audit["acceptance_policy"],
            "vina_non_hydrogen_grid_semantics_fail_closed",
        )
        self.assertIn("output", status["metadata"]["artifacts"])

    def test_run_accepts_hydrogen_outside_grid_with_audit_warning(
        self,
    ) -> None:
        input_member = _rigid_pdbqt_with_hydrogen(0.0, 1.0)
        output_member = _rigid_pdbqt_with_hydrogen(0.0, 12.0)
        self.ligand_1.write_text(input_member, encoding="utf-8")
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        hydrogen_output = (
            "MODEL 1\n"
            "REMARK VINA RESULT: -10.000 0.000 0.000\n"
            + output_member
            + _pdbqt("O1", "OA", 1)
            + "ENDMDL\n"
        )

        def hydrogen_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            command = list(command)
            output_relative = command[command.index("--out") + 1]
            (Path(cwd) / output_relative).write_text(
                hydrogen_output,
                encoding="utf-8",
            )
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        result = self._run(
            prepared["run_id"],
            runner=hydrogen_runner,
        )
        self.assertTrue(result["ok"], result)
        audit = result["metadata"]["output_box_coverage"]
        self.assertTrue(
            audit[
                "all_output_movable_heavy_atoms_inside_effective_grid"
            ]
        )
        self.assertFalse(
            audit["all_output_atoms_inside_effective_grid"]
        )
        self.assertEqual(
            audit["hydrogen_outside_effective_grid_pose_count"],
            1,
        )
        self.assertTrue(
            any(
                "H/HD" in warning
                for warning in result["metadata"]["warnings"]
            )
        )

    def test_run_archives_and_normalizes_recognized_nul_padding(
        self,
    ) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        raw_output = JOINT_OUTPUT.encode("utf-8").replace(
            b"TORSDOF 0\nROOT",
            b"TORSDOF 0\n" + (b"\x00" * 65) + b"\nROOT",
        ).replace(
            b"TORSDOF 1\nENDMDL",
            b"TORSDOF 1\n" + (b"\x00" * 18) + b"\nENDMDL",
        )
        self.assertEqual(raw_output.count(b"\x00"), 166)

        def nul_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            command = list(command)
            output_relative = command[command.index("--out") + 1]
            (Path(cwd) / output_relative).write_bytes(raw_output)
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        result = self._run(
            prepared["run_id"],
            runner=nul_runner,
        )
        self.assertTrue(result["ok"], result)
        normalization = result["metadata"]["output_normalization"]
        self.assertEqual(normalization["status"], "normalized")
        self.assertEqual(normalization["recognized_padding_blocks"], 4)
        self.assertEqual(normalization["nul_bytes_detected"], 166)
        self.assertEqual(normalization["nul_bytes_removed"], 166)
        run_dir = self.project_dir / "runs" / prepared["run_id"]
        normalized_path = run_dir / "out.pdbqt"
        raw_path = run_dir / "out.vina_raw.pdbqt"
        self.assertTrue(raw_path.is_file())
        self.assertEqual(raw_path.read_bytes(), raw_output)
        self.assertNotIn(b"\x00", normalized_path.read_bytes())
        self.assertEqual(
            result["metadata"]["artifacts"]["out_vina_raw"]["sha256"],
            normalization["source_sha256"],
        )
        report = (run_dir / "multi_ligand_report.md").read_text(
            encoding="utf-8",
        )
        self.assertIn("| 检测 / 移除 NUL 字节 | 166 / 166 |", report)

    def test_crlf_nul_before_next_member_remarks_is_normalized(
        self,
    ) -> None:
        output_path = self.root / "crlf_member_boundary.pdbqt"
        payload = (
            b"MODEL 1\r\n"
            b"REMARK VINA RESULT: -10.000 0.000 0.000\r\n"
            b"ROOT\r\n"
            b"ATOM      1  C1  LIG A   1       0.000   0.000   "
            b"0.000  1.00  0.00     0.000 C\r\n"
            b"ENDROOT\r\n"
            b"TORSDOF 0\r\n"
            + (b"\x00" * 37)
            + b"\r\n"
            b"REMARK SMILES C\r\n"
            b"REMARK SMILES IDX 1 1\r\n"
            b"ROOT\r\n"
            b"ATOM      1  O1  LIG A   1       0.000   0.000   "
            b"0.000  1.00  0.00     0.000 OA\r\n"
            b"ENDROOT\r\n"
            b"TORSDOF 0\r\n"
            b"ENDMDL\r\n"
        )
        output_path.write_bytes(payload)
        normalized = multiple_ligands._normalize_vina_pdbqt_output(
            output_path,
            "crlf_member_boundary.pdbqt",
            allow_multiple_ligand_member_boundary=True,
        )
        self.assertTrue(normalized["ok"], normalized)
        self.assertEqual(
            normalized["record"]["recognized_padding_blocks"],
            1,
        )
        self.assertEqual(
            normalized["record"]["nul_bytes_removed"],
            37,
        )
        published = output_path.read_bytes()
        self.assertNotIn(b"\x00", published)
        self.assertIn(
            (
                b"\r\nREMARK SMILES C\r\n"
                b"REMARK SMILES IDX 1 1\r\nROOT"
            ),
            published,
        )
        self.assertEqual(published.count(b"\r\n"), payload.count(b"\r\n"))

    def test_arbitrary_nul_in_output_fails_closed_and_preserves_raw(
        self,
    ) -> None:
        output_path = self.root / "arbitrary_nul.pdbqt"
        payload = (
            b"MODEL 1\n"
            b"ROOT\n"
            b"ATOM      1  C1\x00 LIG A   1       0.000   0.000   "
            b"0.000  1.00  0.00     0.000 C\n"
            b"ENDROOT\n"
            b"TORSDOF 0\n"
            b"ENDMDL\n"
        )
        output_path.write_bytes(payload)
        normalized = multiple_ligands._normalize_vina_pdbqt_output(
            output_path,
            "arbitrary_nul.pdbqt",
            allow_multiple_ligand_member_boundary=True,
        )
        self.assertFalse(normalized["ok"])
        self.assertEqual(
            normalized["error"]["code"],
            "VINA_OUTPUT_BINARY_CONTROL_CHARACTER",
        )
        self.assertFalse(output_path.exists())
        raw_path = self.root / "arbitrary_nul.vina_raw.pdbqt"
        self.assertEqual(raw_path.read_bytes(), payload)

    def test_success_commit_precedes_project_publication_and_late_cancel_loses(
        self,
    ) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        observed_statuses: list[str] = []
        cancellation_results: list[dict] = []
        original_write = multiple_ligands._safe_atomic_write_text
        project_outputs = {
            multiple_ligands.PROJECT_SCORES_FILE,
            multiple_ligands.PROJECT_REPORT_FILE,
        }

        def observe_project_publication(
            project_root,
            relative_path,
            payload,
        ):
            relative = Path(relative_path).as_posix()
            if relative in project_outputs:
                status = get_multiple_ligand_run_status(
                    str(self.project_dir),
                    prepared["run_id"],
                )
                observed_statuses.append(status["status"])
                if not cancellation_results:
                    cancellation_results.append(
                        cancel_multiple_ligand_run(
                            str(self.project_dir),
                            prepared["run_id"],
                        )
                    )
            return original_write(project_root, relative_path, payload)

        with patch.object(
            multiple_ligands,
            "_safe_atomic_write_text",
            side_effect=observe_project_publication,
        ):
            result = self._run(prepared["run_id"])

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "finished")
        self.assertTrue(result["project_outputs_published"])
        self.assertEqual(observed_statuses, ["finished", "finished"])
        self.assertEqual(len(cancellation_results), 1)
        self.assertTrue(cancellation_results[0]["ok"])
        self.assertFalse(cancellation_results[0]["accepted"])
        self.assertFalse(cancellation_results[0]["cancelled"])
        self.assertEqual(cancellation_results[0]["status"], "finished")
        self.assertFalse(
            multiple_ligands._cancel_marker_path(
                self.project_dir,
                prepared["run_id"],
            ).exists()
        )
        self.assertTrue(
            (
                self.project_dir
                / multiple_ligands.PROJECT_SCORES_FILE
            ).is_file()
        )
        self.assertTrue(
            (
                self.project_dir
                / multiple_ligands.PROJECT_REPORT_FILE
            ).is_file()
        )

    def test_stdout_score_modes_must_be_continuous_unique_and_bounded(
        self,
    ) -> None:
        cases = [
            [1, 1, 2],
            [1, 3],
            list(range(1, 11)),
        ]
        for modes in cases:
            with self.subTest(modes=modes):
                prepared = self._prepare()
                self.assertTrue(prepared["ok"], prepared)

                def invalid_score_runner(
                    command,
                    cwd,
                    stdout_path,
                    stderr_path,
                    log_path,
                    *,
                    on_started=None,
                    on_output=None,
                ):
                    if on_started is not None:
                        on_started(123)
                    output_path = (
                        Path(cwd)
                        / command[command.index("--out") + 1]
                    )
                    output_path.write_text(
                        JOINT_OUTPUT,
                        encoding="utf-8",
                    )
                    invalid_log = _vina_log_modes(modes)
                    Path(stdout_path).write_text(
                        invalid_log,
                        encoding="utf-8",
                    )
                    Path(log_path).write_text(
                        invalid_log,
                        encoding="utf-8",
                    )
                    Path(stderr_path).write_text(
                        "",
                        encoding="utf-8",
                    )
                    return ManagedRunResult(123, 0, "")

                result = self._run(
                    prepared["run_id"],
                    runner=invalid_score_runner,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"]["code"],
                    "MULTIPLE_LIGAND_SCORE_MODE_SEQUENCE_INVALID",
                )
                status = get_multiple_ligand_run_status(
                    str(self.project_dir),
                    prepared["run_id"],
                )
                self.assertEqual(status["status"], "failed")

    def test_pre_run_input_config_and_binary_mutation_fail_closed(self) -> None:
        targets = ("receptor", "member", "config", "binary")
        for target in targets:
            with self.subTest(target=target):
                prepared = self._prepare()
                run_id = prepared["run_id"]
                run_dir = self.project_dir / "runs" / run_id
                if target == "receptor":
                    path = run_dir / "inputs/receptor.pdbqt"
                elif target == "member":
                    path = run_dir / "inputs/ligand_002.pdbqt"
                elif target == "config":
                    path = run_dir / "config_snapshot.txt"
                else:
                    path = self.vina
                path.write_bytes(path.read_bytes() + b"\nmutation")
                with (
                    patch(
                        "dockstart_core.multiple_ligands.vina_adapter.detect",
                        return_value=self._detection(),
                    ),
                    patch(
                        "dockstart_core.multiple_ligands.vina_adapter.run_managed",
                    ) as mocked_run,
                ):
                    result = execute_multiple_ligand_run(
                        str(self.project_dir),
                        run_id,
                    )
                self.assertFalse(result["ok"])
                self.assertIn(
                    result["error"]["code"],
                    {
                        "MULTIPLE_LIGAND_SNAPSHOT_HASH_MISMATCH",
                        "MULTIPLE_LIGAND_VINA_BINARY_MISMATCH",
                    },
                )
                mocked_run.assert_not_called()
                if target == "binary":
                    self.vina.write_bytes(b"mock vina 1.2.7")

    def test_frozen_joint_grid_estimate_rejects_metadata_mutation(self) -> None:
        prepared = self._prepare()
        run_id = prepared["run_id"]
        metadata_path = self.project_dir / "runs" / run_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["grid_estimate"]["map_count"] = 999
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with (
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.detect",
                return_value=self._detection(),
            ),
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.run_managed",
            ) as mocked_run,
        ):
            result = execute_multiple_ligand_run(
                str(self.project_dir),
                run_id,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MULTIPLE_LIGAND_GRID_ESTIMATE_ATTESTATION_INVALID",
        )
        mocked_run.assert_not_called()

    def test_frozen_scientific_gates_reject_metadata_mutation(self) -> None:
        for field, nested_field, expected_code in (
            (
                "search_complexity",
                "total_search_torsions",
                "MULTIPLE_LIGAND_SEARCH_COMPLEXITY_ATTESTATION_INVALID",
            ),
            (
                "box_coverage",
                (
                    "all_applicable_rigid_heavy_atom_"
                    "diameters_fit_box_diagonal"
                ),
                "MULTIPLE_LIGAND_BOX_COVERAGE_ATTESTATION_INVALID",
            ),
        ):
            with self.subTest(field=field):
                prepared = self._prepare()
                run_id = prepared["run_id"]
                metadata_path = (
                    self.project_dir
                    / "runs"
                    / run_id
                    / "metadata.json"
                )
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                original = metadata[field][nested_field]
                metadata[field][nested_field] = (
                    not original
                    if isinstance(original, bool)
                    else int(original) + 1
                )
                metadata_path.write_text(
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with (
                    patch(
                        "dockstart_core.multiple_ligands.vina_adapter.detect",
                        return_value=self._detection(),
                    ),
                    patch(
                        "dockstart_core.multiple_ligands.vina_adapter.run_managed",
                    ) as mocked_run,
                ):
                    result = execute_multiple_ligand_run(
                        str(self.project_dir),
                        run_id,
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], expected_code)
                mocked_run.assert_not_called()

    def test_post_run_mutation_invalidates_success(self) -> None:
        prepared = self._prepare()
        run_dir = self.project_dir / "runs" / prepared["run_id"]
        member = run_dir / "inputs/ligand_001.pdbqt"
        result = self._run(
            prepared["run_id"],
            runner=self._successful_runner(
                mutate_after=lambda: member.write_bytes(
                    member.read_bytes() + b"\nmutation",
                ),
            ),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MULTIPLE_LIGAND_POST_RUN_INTEGRITY_FAILED",
        )
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["metadata"]["status"], "failed")

    def test_unexpected_runner_io_error_converges_running_to_failed(
        self,
    ) -> None:
        prepared = self._prepare()

        def io_error_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            raise OSError("simulated runner I/O failure")

        result = self._run(
            prepared["run_id"],
            runner=io_error_runner,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MULTIPLE_LIGAND_EXECUTION_IO_ERROR",
        )
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["status"], "failed")

    def test_report_manifest_io_error_is_structured(self) -> None:
        prepared = self._prepare()
        finished = self._run(prepared["run_id"])
        self.assertTrue(finished["ok"], finished)
        with patch.object(
            multiple_ligands,
            "_sha256_file",
            side_effect=OSError("simulated hash read failure"),
        ):
            report = build_multiple_ligand_markdown_report(
                str(self.project_dir),
                prepared["run_id"],
            )
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["error"]["code"],
            "MULTIPLE_LIGAND_JOINT_MANIFEST_READ_ERROR",
        )

    def test_result_paths_reject_project_escape_symlinks(self) -> None:
        for target in ("poses", "results", "reports"):
            with self.subTest(target=target):
                prepared = self._prepare()
                self.assertTrue(prepared["ok"], prepared)
                if target == "poses":
                    link_path = (
                        self.project_dir
                        / "runs"
                        / prepared["run_id"]
                        / "poses"
                    )
                else:
                    link_path = self.project_dir / target
                outside = self.root / f"outside_{target}"
                outside.mkdir()
                restore_directory = link_path.is_dir()
                if restore_directory:
                    link_path.rmdir()
                try:
                    link_path.symlink_to(
                        outside,
                        target_is_directory=True,
                    )
                except OSError as exc:
                    if not link_path.exists() and restore_directory:
                        link_path.mkdir()
                    self.skipTest(
                        f"当前平台不允许创建目录符号链接：{exc}"
                    )
                try:
                    response = self._run(prepared["run_id"])
                    self.assertFalse(response["ok"])
                    self.assertEqual(
                        response["error"]["code"],
                        "MULTIPLE_LIGAND_RESULT_PATH_UNSAFE",
                    )
                    self.assertEqual(list(outside.iterdir()), [])
                    status = get_multiple_ligand_run_status(
                        str(self.project_dir),
                        prepared["run_id"],
                    )
                    self.assertEqual(status["status"], "prepared")
                finally:
                    if os.path.lexists(link_path):
                        link_path.unlink()
                    if restore_directory:
                        link_path.mkdir()

    def test_atomic_result_write_blocks_parent_replacement_race(self) -> None:
        results = self.project_dir / "results"
        displaced = self.project_dir / "results_displaced_by_test"
        outside = self.root / "outside_atomic_write"
        outside.mkdir()
        probe = outside / "probe.txt"
        rename_succeeded: list[bool] = []
        original_write = multiple_ligands._write_bytes_to_verified_parent

        def replace_parent_during_inner_write(parent, payload):
            try:
                results.rename(displaced)
            except OSError:
                rename_succeeded.append(False)
            else:
                rename_succeeded.append(True)
                os.symlink(
                    outside,
                    results,
                    target_is_directory=True,
                )
            return original_write(parent, payload)

        try:
            with patch.object(
                multiple_ligands,
                "_write_bytes_to_verified_parent",
                side_effect=replace_parent_during_inner_write,
            ):
                try:
                    written = multiple_ligands._safe_atomic_write_text(
                        self.project_dir,
                        Path("results", "probe.txt"),
                        "project-only",
                    )
                except multiple_ligands._ProtocolPathError:
                    written = None

            self.assertEqual(len(rename_succeeded), 1)
            if os.name == "nt":
                self.assertFalse(
                    rename_succeeded[0],
                    "Windows 父目录句柄必须阻止 results 被重命名。",
                )
                self.assertIsNotNone(written)
                self.assertEqual(
                    (results / "probe.txt").read_text(encoding="utf-8"),
                    "project-only",
                )
            self.assertFalse(
                probe.exists(),
                "安全写不得经替换后的目录链接写入项目外。",
            )
        finally:
            if os.path.lexists(results) and results.is_symlink():
                results.unlink()
            if displaced.exists():
                if results.exists():
                    results.rmdir()
                displaced.rename(results)

    def test_prepared_run_can_be_cancelled_without_a_process(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        with patch(
            "dockstart_core.multiple_ligands.vina_adapter.terminate_process",
        ) as terminate:
            cancelled = cancel_multiple_ligand_run(
                str(self.project_dir),
                prepared["run_id"],
            )
        self.assertTrue(cancelled["ok"], cancelled)
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["status"], "cancelled")
        terminate.assert_not_called()
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["metadata"]["stage"], "cancelled")
        self.assertFalse(
            multiple_ligands._cancel_marker_path(
                self.project_dir,
                prepared["run_id"],
            ).exists()
        )

    def test_running_cancel_uses_verified_identity_and_execute_converges(self) -> None:
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        cancel_results: list[dict] = []
        runtime_snapshots: list[dict] = []
        recovery_snapshots: list[dict] = []

        def cancelling_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            assert on_started is not None
            on_started(123)
            runtime_snapshots.append(
                get_run_runtime_status(
                    str(self.project_dir),
                    prepared["run_id"],
                )
            )
            recovery_snapshots.append(
                recover_project_state(str(self.project_dir))
            )
            cancel_results.append(
                cancel_multiple_ligand_run(
                    str(self.project_dir),
                    prepared["run_id"],
                )
            )
            Path(stdout_path).write_text("", encoding="utf-8")
            Path(stderr_path).write_text("cancelled", encoding="utf-8")
            Path(log_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 1, "")

        with (
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.verify_process_identity",
                return_value={"ok": True, "running": True},
            ),
            patch(
                "dockstart_core.multiple_ligands.vina_adapter.terminate_process",
                return_value={
                    "ok": True,
                    "pid": 123,
                    "message": "terminated",
                    "raw_error": "",
                },
            ) as terminate,
        ):
            result = self._run(
                prepared["run_id"],
                runner=cancelling_runner,
            )
        self.assertEqual(len(cancel_results), 1)
        self.assertTrue(runtime_snapshots[0]["ok"], runtime_snapshots[0])
        self.assertEqual(
            runtime_snapshots[0]["metadata"]["status"],
            "running",
        )
        self.assertNotEqual(runtime_snapshots[0]["stage"], "interrupted")
        self.assertTrue(recovery_snapshots[0]["ok"], recovery_snapshots[0])
        recovered_run = next(
            item
            for item in recovery_snapshots[0]["project"]["runs"]
            if item["run_id"] == prepared["run_id"]
        )
        self.assertEqual(recovered_run["status"], "running")
        self.assertTrue(cancel_results[0]["cancelled"], cancel_results[0])
        terminate.assert_called_once()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "cancelled")
        status = get_multiple_ligand_run_status(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertEqual(status["status"], "cancelled")
        recovered_after_cancel = recover_project_state(
            str(self.project_dir)
        )
        self.assertTrue(
            recovered_after_cancel["ok"],
            recovered_after_cancel,
        )
        recovered_cancelled = next(
            item
            for item in recovered_after_cancel["project"]["runs"]
            if item["run_id"] == prepared["run_id"]
        )
        self.assertEqual(recovered_cancelled["status"], "cancelled")

    def test_same_size_and_torsdof_member_swap_is_rejected_by_identity(self) -> None:
        self.ligand_2.write_text(
            _pdbqt("O1", "OA", 0),
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)

        def swapped_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            output_path = Path(cwd) / command[command.index("--out") + 1]
            output_path.write_text(
                "MODEL 1\n"
                "REMARK VINA RESULT: -10.000 0.000 0.000\n"
                + _pdbqt("O1", "OA", 0)
                + _pdbqt("C1", "C", 0)
                + "ENDMDL\n",
                encoding="utf-8",
            )
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        response = self._run(
            prepared["run_id"],
            runner=swapped_runner,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_IDENTITY_MISMATCH",
        )

    def test_same_atoms_and_torsdof_different_topology_swap_is_rejected(
        self,
    ) -> None:
        topology_one = _topology_pdbqt(2)
        topology_two = _topology_pdbqt(3)
        self.ligand_1.write_text(topology_one, encoding="utf-8")
        self.ligand_2.write_text(topology_two, encoding="utf-8")
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)
        first_stats = prepared["metadata"]["members"][0]["stats"]
        second_stats = prepared["metadata"]["members"][1]["stats"]
        self.assertEqual(first_stats["atom_count"], second_stats["atom_count"])
        self.assertEqual(first_stats["torsdof"], second_stats["torsdof"])
        self.assertNotEqual(
            first_stats["identity_sha256"],
            second_stats["identity_sha256"],
        )

        def topology_swap_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            output_path = Path(cwd) / command[command.index("--out") + 1]
            output_path.write_text(
                "MODEL 1\n"
                "REMARK VINA RESULT: -10.000 0.000 0.000\n"
                + topology_two
                + topology_one
                + "ENDMDL\n",
                encoding="utf-8",
            )
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        response = self._run(
            prepared["run_id"],
            runner=topology_swap_runner,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_IDENTITY_MISMATCH",
        )

    def test_same_topology_different_partial_charge_swap_is_rejected(
        self,
    ) -> None:
        charge_one_input = _topology_pdbqt(
            2,
            ("0.0000", "0.0110", "0.0030"),
        )
        charge_two_input = _topology_pdbqt(
            2,
            ("0.0000", "0.0030", "0.0110"),
        )
        charge_one_output = _topology_pdbqt(
            2,
            ("0.000", "0.011", "0.003"),
        )
        charge_two_output = _topology_pdbqt(
            2,
            ("0.000", "0.003", "0.011"),
        )
        self.assertEqual(
            multiple_ligands._pdbqt_identity_sha256(
                charge_one_input.splitlines()
            ),
            multiple_ligands._pdbqt_identity_sha256(
                charge_one_output.splitlines()
            ),
        )
        self.assertNotEqual(
            multiple_ligands._pdbqt_identity_sha256(
                charge_one_input.splitlines()
            ),
            multiple_ligands._pdbqt_identity_sha256(
                charge_two_input.splitlines()
            ),
        )
        self.ligand_1.write_text(
            charge_one_input,
            encoding="utf-8",
        )
        self.ligand_2.write_text(
            charge_two_input,
            encoding="utf-8",
        )
        prepared = self._prepare()
        self.assertTrue(prepared["ok"], prepared)

        def charge_swap_runner(
            command,
            cwd,
            stdout_path,
            stderr_path,
            log_path,
            *,
            on_started=None,
            on_output=None,
        ):
            if on_started is not None:
                on_started(123)
            output_path = Path(cwd) / command[command.index("--out") + 1]
            output_path.write_text(
                "MODEL 1\n"
                "REMARK VINA RESULT: -10.000 0.000 0.000\n"
                + charge_two_output
                + charge_one_output
                + "ENDMDL\n",
                encoding="utf-8",
            )
            Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
            Path(log_path).write_text(VINA_LOG, encoding="utf-8")
            Path(stderr_path).write_text("", encoding="utf-8")
            return ManagedRunResult(123, 0, "")

        response = self._run(
            prepared["run_id"],
            runner=charge_swap_runner,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(
            response["error"]["code"],
            "MULTIPLE_LIGAND_MEMBER_IDENTITY_MISMATCH",
        )

    def test_pose_loader_and_report_verify_saved_artifacts(self) -> None:
        prepared = self._prepare()
        finished = self._run(prepared["run_id"])
        self.assertTrue(finished["ok"], finished)
        generic_report = build_markdown_report(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertTrue(generic_report["ok"], generic_report)
        self.assertIn(
            "多配体共同对接（实验性）",
            generic_report["report_text"],
        )
        loaded = load_multiple_ligand_pose(
            str(self.project_dir),
            prepared["run_id"],
            1,
            2,
        )
        self.assertTrue(loaded["ok"], loaded)
        self.assertIn("TORSDOF 1", loaded["content"])
        self.assertEqual(loaded["joint_score"]["joint_affinity_kcal_mol"], -10.0)
        self.assertFalse(loaded["per_member_score_available"])

        run_report = (
            self.project_dir
            / "runs"
            / prepared["run_id"]
            / "multi_ligand_report.md"
        )
        run_report.unlink()
        built = build_multiple_ligand_markdown_report(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertTrue(built["ok"], built)
        self.assertIn("多配体共同对接（实验性）", built["report_text"])
        self.assertFalse(run_report.exists())
        report = export_multiple_ligand_markdown_report(
            str(self.project_dir),
            prepared["run_id"],
        )
        self.assertTrue(report["ok"], report)
        self.assertTrue(run_report.is_file())
        pose_path = (
            self.project_dir
            / "runs"
            / prepared["run_id"]
            / "poses/mode_001/member_002.pdbqt"
        )
        pose_path.write_bytes(pose_path.read_bytes() + b"\nmutation")
        corrupted = load_multiple_ligand_pose(
            str(self.project_dir),
            prepared["run_id"],
            1,
            2,
        )
        self.assertFalse(corrupted["ok"])
        self.assertEqual(
            corrupted["error"]["code"],
            "MULTIPLE_LIGAND_POSE_HASH_MISMATCH",
        )

    def test_report_export_transaction_preserves_concurrent_metadata_fields(
        self,
    ) -> None:
        prepared = self._prepare()
        finished = self._run(prepared["run_id"])
        self.assertTrue(finished["ok"], finished)
        run_dir = self.project_dir / "runs" / prepared["run_id"]
        report_path = run_dir / "multi_ligand_report.md"
        report_path.unlink()
        original_write = multiple_ligands._safe_atomic_write_text
        injected = False

        def inject_concurrent_field(
            project_root,
            relative_path,
            payload,
        ):
            nonlocal injected
            relative = Path(relative_path).as_posix()
            if (
                not injected
                and relative
                == f"runs/{prepared['run_id']}/multi_ligand_report.md"
            ):
                metadata_path = run_dir / "metadata.json"
                current = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                current["concurrent_audit_marker"] = "preserve-me"
                metadata_path.write_text(
                    json.dumps(
                        current,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                injected = True
            return original_write(project_root, relative_path, payload)

        with patch.object(
            multiple_ligands,
            "_safe_atomic_write_text",
            side_effect=inject_concurrent_field,
        ):
            exported = export_multiple_ligand_markdown_report(
                str(self.project_dir),
                prepared["run_id"],
            )
        self.assertTrue(exported["ok"], exported)
        self.assertTrue(exported["project_report_published"])
        latest = json.loads(
            (run_dir / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            latest["concurrent_audit_marker"],
            "preserve-me",
        )
        self.assertEqual(
            latest["artifacts"]["report"]["sha256"],
            multiple_ligands._sha256_file(report_path),
        )
        self.assertEqual(
            latest["report_file"],
            f"runs/{prepared['run_id']}/multi_ligand_report.md",
        )
        self.assertEqual(
            latest["project_report_file"],
            "reports/simultaneous_multi_ligand_report.md",
        )
        self.assertTrue(latest["reported_at"])
        self.assertIn("project_report", latest["artifacts"])
        self.assertEqual(
            exported["reported_at"],
            latest["reported_at"],
        )

    def test_completed_pose_and_report_reads_reject_symlinks(self) -> None:
        prepared = self._prepare()
        finished = self._run(prepared["run_id"])
        self.assertTrue(finished["ok"], finished)
        pose_path = (
            self.project_dir
            / "runs"
            / prepared["run_id"]
            / "poses"
            / "mode_001"
            / "member_001.pdbqt"
        )
        outside_pose = self.root / "outside_pose.pdbqt"
        outside_pose.write_bytes(pose_path.read_bytes())
        pose_path.unlink()
        try:
            pose_path.symlink_to(outside_pose)
        except OSError as exc:
            self.skipTest(f"当前平台不允许创建文件符号链接：{exc}")
        linked_pose = load_multiple_ligand_pose(
            str(self.project_dir),
            prepared["run_id"],
            1,
            1,
        )
        self.assertFalse(linked_pose["ok"])
        self.assertEqual(
            linked_pose["error"]["code"],
            "MULTIPLE_LIGAND_POSE_PATH_UNSAFE",
        )
        pose_path.unlink()

        reports_dir = self.project_dir / "reports"
        project_report = (
            reports_dir
            / "simultaneous_multi_ligand_report.md"
        )
        project_report.unlink()
        reports_dir.rmdir()
        outside_reports = self.root / "outside_reports"
        outside_reports.mkdir()
        try:
            reports_dir.symlink_to(
                outside_reports,
                target_is_directory=True,
            )
        except OSError as exc:
            reports_dir.mkdir()
            self.skipTest(
                f"当前平台不允许创建目录符号链接：{exc}"
            )
        try:
            exported = export_multiple_ligand_markdown_report(
                str(self.project_dir),
                prepared["run_id"],
            )
            self.assertTrue(exported["ok"], exported)
            self.assertFalse(exported["project_report_published"])
            self.assertTrue(exported["warnings"])
            self.assertTrue(
                (
                    self.project_dir
                    / "runs"
                    / prepared["run_id"]
                    / "multi_ligand_report.md"
                ).is_file()
            )
            self.assertEqual(list(outside_reports.iterdir()), [])
        finally:
            reports_dir.unlink()
            reports_dir.mkdir()

    def test_cli_status_uses_json_contract(self) -> None:
        prepared = self._prepare()
        buffer = StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "multiple_ligands.py",
                    "status",
                    str(self.project_dir),
                    prepared["run_id"],
                ],
            ),
            redirect_stdout(buffer),
        ):
            main()
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["metadata"]["protocol_id"], PROTOCOL_ID)

    def test_cli_cancel_uses_json_contract(self) -> None:
        prepared = self._prepare()
        buffer = StringIO()
        with (
            patch(
                "sys.argv",
                [
                    "multiple_ligands.py",
                    "cancel",
                    str(self.project_dir),
                    prepared["run_id"],
                ],
            ),
            redirect_stdout(buffer),
        ):
            main()
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["accepted"])
        self.assertTrue(payload["cancelled"])
        self.assertEqual(payload["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
