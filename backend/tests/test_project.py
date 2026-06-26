from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import (  # noqa: E402
    PROJECT_DIRS,
    build_vina_config_text,
    create_project,
    execute_prepared_vina_run,
    generate_vina_config,
    get_box_params,
    get_next_run_id,
    get_project_workflow_status,
    get_run_files_status,
    get_vina_config_preview,
    get_vina_params,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_run_metadata,
    load_project,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
    validate_run_prerequisites,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402


class ProjectTests(unittest.TestCase):
    def _create_project_with_imports(self, temp_dir: str) -> Path:
        project_response = create_project("demo_project", temp_dir)
        project_dir = Path(project_response["project_dir"])
        receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
        ligand_source = Path(temp_dir) / "ligand_source.pdbqt"
        receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
        ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

        receptor_response = import_receptor_pdbqt(str(project_dir), str(receptor_source))
        ligand_response = import_ligand_pdbqt(str(project_dir), str(ligand_source))

        self.assertTrue(receptor_response["ok"])
        self.assertTrue(ligand_response["ok"])
        return project_dir

    def _create_config_ready_project(self, temp_dir: str) -> Path:
        project_dir = self._create_project_with_imports(temp_dir)
        response = generate_vina_config(str(project_dir))
        self.assertTrue(response["ok"])
        return project_dir

    def _vina_ok_result(self) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="ok",
            version="1.2.5",
            path="mock-vina",
            message="已检测到 AutoDock Vina。",
            source="auto",
        )

    def _vina_missing_result(self) -> ToolCheckResult:
        return ToolCheckResult(
            key="vina",
            name="AutoDock Vina",
            status="missing",
            message="未检测到 AutoDock Vina。",
            source="auto",
        )

    def _create_prepared_run_with_command(self, temp_dir: str, command: list[str] | None = None) -> tuple[Path, str]:
        project_dir = self._create_config_ready_project(temp_dir)
        with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
            response = prepare_vina_run(str(project_dir))
        self.assertTrue(response["ok"])
        run_id = response["run_id"]
        if command is not None:
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["command"] = command
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return project_dir, run_id

    def _write_fake_vina(self, temp_dir: str, exit_code: int = 0, create_output: bool = True) -> Path:
        script_path = Path(temp_dir) / f"fake_vina_{exit_code}_{int(create_output)}.py"
        script_path.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import pathlib",
                    "import sys",
                    "print('fake vina stdout')",
                    "print('fake vina stderr', file=sys.stderr)",
                    "args = sys.argv[1:]",
                    "def arg_value(flag):",
                    "    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else ''",
                    "out_file = arg_value('--out')",
                    "log_file = arg_value('--log')",
                    "if log_file:",
                    "    pathlib.Path(log_file).parent.mkdir(parents=True, exist_ok=True)",
                    "    pathlib.Path(log_file).write_text('fake vina log\\n', encoding='utf-8')",
                    "if out_file and " + ("True" if create_output else "False") + ":",
                    "    pathlib.Path(out_file).parent.mkdir(parents=True, exist_ok=True)",
                    "    pathlib.Path(out_file).write_text('MODEL 1\\nENDMDL\\n', encoding='utf-8')",
                    f"raise SystemExit({exit_code})",
                ],
            )
            + "\n",
            encoding="utf-8",
        )
        return script_path

    def _set_run_command(self, project_dir: Path, run_id: str, command: list[str] | str) -> None:
        metadata_path = project_dir / "runs" / run_id / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["command"] = command
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _fake_vina_command(self, script_path: Path, run_id: str) -> list[str]:
        return [
            sys.executable,
            str(script_path),
            "--config",
            "configs/vina_config.txt",
            "--out",
            f"runs/{run_id}/out.pdbqt",
            "--log",
            f"runs/{run_id}/log.txt",
        ]

    def test_create_project_generates_full_structure_and_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = create_project("demo_project", temp_dir)
            project_dir = Path(temp_dir) / "demo_project"

            self.assertTrue(response["ok"])
            for directory in PROJECT_DIRS:
                self.assertTrue((project_dir / directory).is_dir())
            self.assertTrue((project_dir / "project.json").is_file())

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["project_name"], "demo_project")
            self.assertEqual(project["project_dir"], str(project_dir))
            self.assertEqual(project["box"]["size_x"], 20)
            self.assertEqual(project["vina"]["cpu"], 0)
            self.assertIsNone(project["vina"]["seed"])
            self.assertEqual(project["config"]["vina_config_file"], "")
            self.assertEqual(project["config"]["generated_at"], "")

    def test_existing_project_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "demo_project"
            project_dir.mkdir()
            sentinel = project_dir / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")

            response = create_project("demo_project", temp_dir)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_DIR_EXISTS")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_import_missing_pdbqt_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = project_response["project_dir"]

            response = import_receptor_pdbqt(project_dir, str(Path(temp_dir) / "missing.pdbqt"))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PDBQT_FILE_NOT_FOUND")

    def test_import_empty_pdbqt_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            empty_file = Path(temp_dir) / "empty.pdbqt"
            empty_file.write_text("", encoding="utf-8")

            response = import_ligand_pdbqt(project_response["project_dir"], str(empty_file))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PDBQT_FILE_EMPTY")

    def test_import_receptor_copies_file_and_updates_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "source_receptor.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")

            response = import_receptor_pdbqt(str(project_dir), str(receptor_source))

            self.assertTrue(response["ok"])
            copied_file = project_dir / "prepared" / "receptor.pdbqt"
            self.assertTrue(copied_file.is_file())
            self.assertEqual(copied_file.read_text(encoding="utf-8"), "REMARK receptor\n")

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["receptor"]["file"], "prepared/receptor.pdbqt")
            self.assertEqual(project["receptor"]["source"], "local")

    def test_import_ligand_copies_file_and_updates_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "source_ligand.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

            response = import_ligand_pdbqt(str(project_dir), str(ligand_source))

            self.assertTrue(response["ok"])
            copied_file = project_dir / "prepared" / "ligand.pdbqt"
            self.assertTrue(copied_file.is_file())
            self.assertEqual(copied_file.read_text(encoding="utf-8"), "REMARK ligand\n")

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["ligand"]["file"], "prepared/ligand.pdbqt")
            self.assertEqual(project["ligand"]["source"], "local")

    def test_imports_then_load_project_reads_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "receptor.pdbqt"
            ligand_source = Path(temp_dir) / "ligand.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

            import_receptor_pdbqt(str(project_dir), str(receptor_source))
            import_ligand_pdbqt(str(project_dir), str(ligand_source))
            loaded = load_project(str(project_dir))

            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["project"]["project_name"], "demo_project")
            self.assertEqual(loaded["project"]["receptor"]["file"], "prepared/receptor.pdbqt")
            self.assertEqual(loaded["project"]["ligand"]["file"], "prepared/ligand.pdbqt")

    def test_get_box_params_reads_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = get_box_params(project_response["project_dir"])

            self.assertTrue(response["ok"])
            self.assertEqual(response["box"]["center_x"], 0)
            self.assertEqual(response["box"]["size_x"], 20)

    def test_update_box_params_saves_valid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            box = {
                "center_x": 1.5,
                "center_y": 2,
                "center_z": 3,
                "size_x": 10,
                "size_y": 11,
                "size_z": 12,
            }

            response = update_box_params(str(project_dir), box)

            self.assertTrue(response["ok"])
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["box"]["center_x"], 1.5)
            self.assertEqual(project["box"]["size_z"], 12)

    def test_center_allows_negative_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_box_params(
                project_response["project_dir"],
                {
                    "center_x": -10,
                    "center_y": -0.5,
                    "center_z": -3.25,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["project"]["box"]["center_x"], -10)

    def test_size_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_box_params(
                project_response["project_dir"],
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 0,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_SIZE_NOT_POSITIVE")

    def test_non_numeric_box_param_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_box_params(
                project_response["project_dir"],
                {
                    "center_x": "abc",
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_PARAM_INVALID")

    def test_empty_box_param_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_box_params(
                project_response["project_dir"],
                {
                    "center_x": "",
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 20,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_PARAM_REQUIRED")

    def test_large_size_returns_warning_but_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_box_params(
                project_response["project_dir"],
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 61,
                    "size_y": 20,
                    "size_z": 20,
                },
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])
            self.assertEqual(response["project"]["box"]["size_x"], 61)

    def test_get_box_params_missing_project_json_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = get_box_params(temp_dir)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_JSON_NOT_FOUND")

    def test_updated_at_changes_after_saving_box(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            before = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["updated_at"]

            with unittest.mock.patch("dockstart_core.project._now_iso", return_value="2099-01-01T00:00:00+00:00"):
                response = update_box_params(
                    str(project_dir),
                    {
                        "center_x": 1,
                        "center_y": 1,
                        "center_z": 1,
                        "size_x": 20,
                        "size_y": 20,
                        "size_z": 20,
                    },
                )

            after = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["updated_at"]

            self.assertTrue(response["ok"])
            self.assertNotEqual(before, after)
            self.assertEqual(after, "2099-01-01T00:00:00+00:00")

    def test_get_vina_params_reads_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = get_vina_params(project_response["project_dir"])

            self.assertTrue(response["ok"])
            self.assertEqual(response["vina"]["exhaustiveness"], 8)
            self.assertEqual(response["vina"]["num_modes"], 9)
            self.assertEqual(response["vina"]["energy_range"], 4)
            self.assertEqual(response["vina"]["cpu"], 0)
            self.assertIsNone(response["vina"]["seed"])

    def test_update_vina_params_saves_valid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])

            response = update_vina_params(
                str(project_dir),
                {
                    "exhaustiveness": 16,
                    "num_modes": 12,
                    "energy_range": 3.5,
                    "cpu": 4,
                    "seed": 12345,
                },
            )

            self.assertTrue(response["ok"])
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["vina"]["exhaustiveness"], 16)
            self.assertEqual(project["vina"]["energy_range"], 3.5)
            self.assertEqual(project["vina"]["seed"], 12345)

    def test_exhaustiveness_must_be_positive_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            zero_response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 0, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
            )
            decimal_response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": "8.5", "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
            )

            self.assertFalse(zero_response["ok"])
            self.assertEqual(zero_response["error"]["code"], "VINA_PARAM_POSITIVE_REQUIRED")
            self.assertFalse(decimal_response["ok"])
            self.assertEqual(decimal_response["error"]["code"], "VINA_PARAM_INTEGER_REQUIRED")

    def test_num_modes_must_be_positive_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": -1, "energy_range": 4, "cpu": 0, "seed": None},
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_PARAM_POSITIVE_REQUIRED")

    def test_energy_range_must_be_positive_number(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 0, "cpu": 0, "seed": None},
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_PARAM_POSITIVE_REQUIRED")

    def test_cpu_must_be_non_negative_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": -1, "seed": None},
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_PARAM_NON_NEGATIVE_REQUIRED")

    def test_seed_can_be_null_or_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            null_response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
            )
            empty_response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": ""},
            )

            self.assertTrue(null_response["ok"])
            self.assertIsNone(null_response["project"]["vina"]["seed"])
            self.assertTrue(empty_response["ok"])
            self.assertIsNone(empty_response["project"]["vina"]["seed"])

    def test_seed_must_be_integer_when_filled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": "1.2"},
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_SEED_INTEGER_REQUIRED")

    def test_invalid_vina_values_return_structured_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            cases = [
                {"exhaustiveness": "", "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
                {"exhaustiveness": 8, "num_modes": "abc", "energy_range": 4, "cpu": 0, "seed": None},
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": float("nan"), "cpu": 0, "seed": None},
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": "Infinity", "cpu": 0, "seed": None},
            ]

            for vina in cases:
                with self.subTest(vina=vina):
                    response = update_vina_params(project_response["project_dir"], vina)
                    self.assertFalse(response["ok"])
                    self.assertIn("error", response)
                    self.assertIn("message", response["error"])

    def test_large_exhaustiveness_returns_warning_but_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 65, "num_modes": 9, "energy_range": 4, "cpu": 1, "seed": None},
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])
            self.assertEqual(response["project"]["vina"]["exhaustiveness"], 65)

    def test_large_num_modes_returns_warning_but_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 51, "energy_range": 4, "cpu": 1, "seed": None},
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])
            self.assertEqual(response["project"]["vina"]["num_modes"], 51)

    def test_large_energy_range_returns_warning_but_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 10.5, "cpu": 1, "seed": None},
            )

            self.assertTrue(response["ok"])
            self.assertTrue(response["warnings"])
            self.assertEqual(response["project"]["vina"]["energy_range"], 10.5)

    def test_cpu_zero_returns_warning_but_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
            )

            self.assertTrue(response["ok"])
            self.assertTrue(any("Vina 自动" in warning for warning in response["warnings"]))

    def test_updated_at_changes_after_saving_vina(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            before = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["updated_at"]

            with unittest.mock.patch("dockstart_core.project._now_iso", return_value="2099-01-02T00:00:00+00:00"):
                response = update_vina_params(
                    str(project_dir),
                    {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": None},
                )

            after = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["updated_at"]

            self.assertTrue(response["ok"])
            self.assertNotEqual(before, after)
            self.assertEqual(after, "2099-01-02T00:00:00+00:00")

    def test_get_vina_params_missing_project_json_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = get_vina_params(temp_dir)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_JSON_NOT_FOUND")

    def test_build_vina_config_text_with_imported_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)

            response = build_vina_config_text(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertIn("receptor = prepared/receptor.pdbqt", response["config_text"])
            self.assertIn("ligand = prepared/ligand.pdbqt", response["config_text"])
            self.assertIn("center_x = 0", response["config_text"])
            self.assertIn("exhaustiveness = 8", response["config_text"])

    def test_vina_config_omits_seed_when_null(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)

            response = build_vina_config_text(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertNotIn("seed =", response["config_text"])

    def test_vina_config_includes_seed_when_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            update_vina_params(
                str(project_dir),
                {"exhaustiveness": 8, "num_modes": 9, "energy_range": 4, "cpu": 0, "seed": 12345},
            )

            response = build_vina_config_text(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertIn("seed = 12345", response["config_text"])

    def test_vina_config_includes_cpu_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)

            response = build_vina_config_text(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertIn("cpu = 0", response["config_text"])

    def test_config_receptor_empty_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = get_vina_config_preview(project_response["project_dir"])

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_FILE_NOT_SET")

    def test_config_ligand_empty_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
            import_receptor_pdbqt(str(project_dir), str(receptor_source))

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_FILE_NOT_SET")

    def test_config_missing_prepared_receptor_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").unlink()

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_FILE_NOT_FOUND")

    def test_config_missing_prepared_ligand_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "ligand.pdbqt").unlink()

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_FILE_NOT_FOUND")

    def test_config_raw_receptor_without_prepared_hints_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            raw_file = project_dir / "raw" / "receptor_1ABC.pdb"
            raw_file.write_text("ATOM receptor\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor_1ABC.pdb"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_PDBQT_NOT_PREPARED")
            self.assertIn("尚未准备 prepared/receptor.pdbqt", response["error"]["message"])

    def test_config_raw_ligand_without_prepared_hints_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
            import_receptor_pdbqt(str(project_dir), str(receptor_source))
            raw_file = project_dir / "raw" / "ligand_2244.sdf"
            raw_file.write_text("ligand sdf\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["ligand"]["raw_file"] = "raw/ligand_2244.sdf"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_PDBQT_NOT_PREPARED")
            self.assertIn("尚未准备 prepared/ligand.pdbqt", response["error"]["message"])

    def test_config_preparation_failed_hints_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
            import_receptor_pdbqt(str(project_dir), str(receptor_source))
            raw_file = project_dir / "raw" / "ligand_2244.sdf"
            raw_file.write_text("ligand sdf\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["ligand"]["raw_file"] = "raw/ligand_2244.sdf"
            project["preparation"]["ligand"]["status"] = "failed"
            project["preparation"]["ligand"]["log_file"] = "prepared/logs/ligand_preparation_log.json"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_PREPARATION_FAILED")
            self.assertIn("preparation 日志", response["error"]["message"])

    def test_config_invalid_box_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["box"]["size_x"] = 0
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "BOX_SIZE_NOT_POSITIVE")

    def test_config_invalid_vina_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["vina"]["exhaustiveness"] = 0
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_vina_config_preview(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_PARAM_POSITIVE_REQUIRED")

    def test_project_workflow_status_recommends_receptor_preparation_when_raw_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            raw_file = project_dir / "raw" / "receptor_1ABC.pdb"
            raw_file.write_text("ATOM receptor\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor_1ABC.pdb"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["raw"]["receptor"]["status"], "ok")
            self.assertEqual(response["prepared"]["receptor"]["status"], "missing")
            self.assertIn("准备 receptor PDBQT", response["next_recommended_action"])

    def test_project_workflow_status_reports_preparation_failed_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "ligand.pdbqt").unlink()
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["preparation"]["ligand"]["status"] = "failed"
            project["preparation"]["ligand"]["log_file"] = "prepared/logs/ligand_preparation_log.json"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["preparation"]["ligand"]["status"], "failed")
            self.assertIn("ligand PDBQT 自动准备失败", response["next_recommended_action"])

    def test_project_workflow_status_old_project_without_preparation_is_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project.pop("preparation", None)
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["preparation"]["receptor"]["status"], "not_started")
            self.assertEqual(response["prepared"]["receptor"]["status"], "ok")

    def test_project_workflow_status_viewer_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertFalse(response["viewer"]["can_view_raw_receptor"])
            self.assertFalse(response["viewer"]["can_view_prepared_ligand"])
            self.assertFalse(response["viewer"]["can_view_docking_output"])
            self.assertEqual(response["viewer"]["available_runs"], [])

    def test_project_workflow_status_viewer_raw_and_prepared_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            raw_file = project_dir / "raw" / "receptor_1ABC.pdb"
            raw_file.write_text("ATOM receptor\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor_1ABC.pdb"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertTrue(response["viewer"]["can_view_raw_receptor"])
            self.assertTrue(response["viewer"]["can_view_prepared_receptor"])
            self.assertTrue(response["viewer"]["can_view_prepared_ligand"])

    def test_project_workflow_status_viewer_docking_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            run_dir = project_dir / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "out.pdbqt").write_text("MODEL 1\nREMARK pose\nENDMDL\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["runs"] = [{"run_id": "run_001", "status": "finished"}]
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = get_project_workflow_status(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertTrue(response["viewer"]["can_view_docking_output"])
            self.assertEqual(response["viewer"]["available_runs"][0]["run_id"], "run_001")

    def test_generate_vina_config_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)

            response = generate_vina_config(str(project_dir))

            self.assertTrue(response["ok"])
            config_file = project_dir / "configs" / "vina_config.txt"
            self.assertTrue(config_file.is_file())
            self.assertEqual(config_file.read_text(encoding="utf-8"), response["config_text"])

    def test_generate_vina_config_updates_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            project_json = project_dir / "project.json"
            before = json.loads(project_json.read_text(encoding="utf-8"))["updated_at"]

            with unittest.mock.patch("dockstart_core.project._now_iso", return_value="2099-01-03T00:00:00+00:00"):
                response = generate_vina_config(str(project_dir))

            project = json.loads(project_json.read_text(encoding="utf-8"))

            self.assertTrue(response["ok"])
            self.assertNotEqual(before, project["updated_at"])
            self.assertEqual(project["updated_at"], "2099-01-03T00:00:00+00:00")
            self.assertEqual(project["config"]["vina_config_file"], "configs/vina_config.txt")
            self.assertEqual(project["config"]["generated_at"], "2099-01-03T00:00:00+00:00")

    def test_get_next_run_id_returns_run_001_when_runs_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            self.assertEqual(get_next_run_id(project_response["project_dir"]), "run_001")

    def test_get_next_run_id_returns_run_002_when_run_001_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            (project_dir / "runs" / "run_001").mkdir()

            self.assertEqual(get_next_run_id(str(project_dir)), "run_002")

    def test_prepare_vina_run_does_not_overwrite_existing_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            existing_run = project_dir / "runs" / "run_001"
            existing_run.mkdir()
            sentinel = existing_run / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                response = prepare_vina_run(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["run_id"], "run_002")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertTrue((project_dir / "runs" / "run_002").is_dir())

    def test_validate_run_prerequisites_missing_project_json_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            response = validate_run_prerequisites(str(Path(temp_dir) / "missing_project"))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_JSON_NOT_FOUND")
            self.assertEqual(response["checks"][0]["key"], "project_json")

    def test_validate_run_prerequisites_missing_receptor_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = validate_run_prerequisites(project_response["project_dir"])

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_FILE_NOT_SET")
            self.assertEqual(response["checks"][-1]["key"], "receptor")

    def test_validate_run_prerequisites_missing_ligand_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor_source = Path(temp_dir) / "receptor_source.pdbqt"
            receptor_source.write_text("REMARK receptor\n", encoding="utf-8")
            import_receptor_pdbqt(str(project_dir), str(receptor_source))

            response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_FILE_NOT_SET")
            self.assertEqual(response["checks"][-1]["key"], "ligand")

    def test_validate_run_prerequisites_raw_receptor_without_prepared_hints_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            raw_file = project_dir / "raw" / "receptor_1ABC.pdb"
            raw_file.write_text("ATOM receptor\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor_1ABC.pdb"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_PDBQT_NOT_PREPARED")
            self.assertEqual(response["checks"][-1]["key"], "receptor")
            self.assertEqual(response["checks"][-1]["status"], "missing")

    def test_validate_run_prerequisites_empty_prepared_receptor_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").write_text("", encoding="utf-8")

            response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_FILE_EMPTY")
            self.assertEqual(response["checks"][-1]["key"], "receptor")

    def test_validate_run_prerequisites_empty_prepared_ligand_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "ligand.pdbqt").write_text("", encoding="utf-8")

            response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_FILE_EMPTY")
            self.assertEqual(response["checks"][-1]["key"], "ligand")

    def test_validate_run_prerequisites_missing_config_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)

            response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_CONFIG_NOT_FOUND")
            self.assertEqual(response["checks"][-1]["key"], "vina_config")

    def test_validate_run_prerequisites_vina_missing_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_missing_result()):
                response = validate_run_prerequisites(str(project_dir))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_NOT_AVAILABLE")
            self.assertEqual(response["checks"][-1]["key"], "vina")
            self.assertIn(response["checks"][-1]["status"], {"missing", "error"})

    def test_prepare_vina_run_writes_run_skeleton_and_updates_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()) as detect_mock:
                response = prepare_vina_run(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["run_id"], "run_001")
            detect_mock.assert_called_once()

            run_dir = project_dir / "runs" / "run_001"
            metadata_path = run_dir / "metadata.json"
            command_preview_path = run_dir / "command_preview.txt"
            config_snapshot_path = run_dir / "config_snapshot.txt"

            self.assertTrue(run_dir.is_dir())
            self.assertTrue(metadata_path.is_file())
            self.assertTrue(command_preview_path.is_file())
            self.assertTrue(config_snapshot_path.is_file())
            self.assertFalse((run_dir / "out.pdbqt").exists())
            self.assertFalse((run_dir / "log.txt").exists())

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["status"], "prepared")
            self.assertEqual(metadata["run_id"], "run_001")
            self.assertIsInstance(metadata["command"], list)
            self.assertIn("--config", metadata["command"])
            self.assertEqual(metadata["config_file"], "configs/vina_config.txt")
            self.assertEqual(metadata["output_file"], "runs/run_001/out.pdbqt")
            self.assertEqual(metadata["log_file"], "runs/run_001/log.txt")
            self.assertIsNone(metadata["exit_code"])
            self.assertIsNone(metadata["best_affinity"])

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(len(project["runs"]), 1)
            self.assertEqual(project["runs"][0]["run_id"], "run_001")
            self.assertEqual(project["runs"][0]["status"], "prepared")
            self.assertEqual(project["runs"][0]["metadata_file"], "runs/run_001/metadata.json")

            loaded = load_run_metadata(str(project_dir), "run_001")
            self.assertTrue(loaded["ok"])
            self.assertEqual(loaded["metadata"]["run_id"], "run_001")

    def test_prepare_vina_run_uses_resolved_bundled_vina_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            bundled_path = str(Path(temp_dir) / "resources" / "tools" / "vina" / "vina.exe")
            bundled_result = ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="ok",
                version="1.2.5",
                path=bundled_path,
                message="已检测到内置 AutoDock Vina。",
                source="bundled",
                bundled_path=bundled_path,
                is_bundled=True,
            )

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=bundled_result):
                response = prepare_vina_run(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertEqual(response["metadata"]["vina_path"], bundled_path)
            self.assertEqual(response["metadata"]["command"][0], bundled_path)
            metadata = json.loads((project_dir / "runs" / "run_001" / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["command"][0], bundled_path)

    def test_execute_vina_run_missing_metadata_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = execute_prepared_vina_run(project_response["project_dir"], "run_001")

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_METADATA_NOT_FOUND")

    def test_execute_vina_run_rejects_non_prepared_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["status"] = "finished"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_STATUS_NOT_EXECUTABLE")

    def test_execute_vina_run_rejects_non_array_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            self._set_run_command(project_dir, run_id, "vina --config configs/vina_config.txt")

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_COMMAND_INVALID")

    def test_execute_vina_run_missing_receptor_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").unlink()

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RECEPTOR_FILE_NOT_FOUND")

    def test_execute_vina_run_missing_ligand_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "prepared" / "ligand.pdbqt").unlink()

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "LIGAND_FILE_NOT_FOUND")

    def test_execute_vina_run_missing_config_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "configs" / "vina_config.txt").unlink()

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_CONFIG_NOT_FOUND")

    def test_execute_vina_run_sets_running_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            self._set_run_command(project_dir, run_id, [sys.executable, "-c", "print('ok')"])
            metadata_path = project_dir / "runs" / run_id / "metadata.json"

            def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                running_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(running_metadata["status"], "running")
                self.assertTrue(running_metadata["started_at"])
                out_path = project_dir / "runs" / run_id / "out.pdbqt"
                out_path.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="stdout", stderr="stderr")

            with unittest.mock.patch("dockstart_core.project.subprocess.run", side_effect=fake_run):
                response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertTrue(response["ok"])

    def test_execute_fake_vina_success_finishes_and_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            fake_vina = self._write_fake_vina(temp_dir, exit_code=0, create_output=True)
            self._set_run_command(project_dir, run_id, self._fake_vina_command(fake_vina, run_id))

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(response["metadata"]["status"], "finished")
            self.assertEqual(response["metadata"]["exit_code"], 0)
            self.assertIsNone(response["metadata"]["best_affinity"])
            self.assertTrue((project_dir / "runs" / run_id / "out.pdbqt").is_file())
            self.assertTrue((project_dir / "runs" / run_id / "log.txt").is_file())
            self.assertIn("fake vina stdout", (project_dir / "runs" / run_id / "stdout.txt").read_text(encoding="utf-8"))
            self.assertIn("fake vina stderr", (project_dir / "runs" / run_id / "stderr.txt").read_text(encoding="utf-8"))

            metadata = json.loads((project_dir / "runs" / run_id / "metadata.json").read_text(encoding="utf-8"))
            self.assertTrue(metadata["started_at"])
            self.assertTrue(metadata["finished_at"])
            self.assertEqual(metadata["stdout_file"], f"runs/{run_id}/stdout.txt")
            self.assertEqual(metadata["stderr_file"], f"runs/{run_id}/stderr.txt")

            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["runs"][0]["status"], "finished")
            self.assertEqual(project["runs"][0]["exit_code"], 0)
            self.assertTrue(project["runs"][0]["finished_at"])

            file_status = get_run_files_status(str(project_dir), run_id)
            self.assertTrue(file_status["ok"])
            self.assertEqual(file_status["metadata"]["status"], "finished")

    def test_execute_fake_vina_nonzero_exit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            fake_vina = self._write_fake_vina(temp_dir, exit_code=2, create_output=True)
            self._set_run_command(project_dir, run_id, self._fake_vina_command(fake_vina, run_id))

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["metadata"]["status"], "failed")
            self.assertEqual(response["metadata"]["exit_code"], 2)
            self.assertEqual(response["error"]["code"], "VINA_RUN_FAILED")
            self.assertTrue((project_dir / "runs" / run_id / "stdout.txt").is_file())
            self.assertTrue((project_dir / "runs" / run_id / "stderr.txt").is_file())

    def test_execute_fake_vina_zero_without_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            fake_vina = self._write_fake_vina(temp_dir, exit_code=0, create_output=False)
            self._set_run_command(project_dir, run_id, self._fake_vina_command(fake_vina, run_id))

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["metadata"]["status"], "failed")
            self.assertEqual(response["metadata"]["exit_code"], 0)
            self.assertIn("没有生成非空 out.pdbqt", response["metadata"]["error_message"])

    def test_execute_vina_run_does_not_parse_docking_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            fake_vina = self._write_fake_vina(temp_dir, exit_code=0, create_output=True)
            self._set_run_command(project_dir, run_id, self._fake_vina_command(fake_vina, run_id))

            response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertIsNone(response["metadata"]["best_affinity"])
            self.assertFalse((project_dir / "results" / "scores.csv").exists())


if __name__ == "__main__":
    unittest.main()
