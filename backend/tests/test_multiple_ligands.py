from __future__ import annotations

import json
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
    import_receptor_pdbqt,
    load_scores_csv,
    recover_project_state,
)


def _pdbqt(atom_name: str, atom_type: str, torsdof: int) -> str:
    return (
        "ROOT\n"
        f"ATOM      1  {atom_name:<3} LIG A   1       "
        f"1.000   2.000   3.000  1.00  0.00     0.000 {atom_type}\n"
        "ENDROOT\n"
        f"TORSDOF {torsdof}\n"
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
            {"member_index": 1, "atom_count": 1, "torsdof": 0},
            {"member_index": 2, "atom_count": 1, "torsdof": 1},
        ]

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
                    "protocol_id": "ad4_maps",
                    "run_mode": "dock",
                },
                "MULTIPLE_LIGAND_SCORING_PROTOCOL_UNSUPPORTED",
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
