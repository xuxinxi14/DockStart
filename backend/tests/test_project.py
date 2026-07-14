from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core import __version__  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    PROJECT_DIRS,
    build_vina_config_text,
    cancel_vina_run,
    create_project,
    execute_prepared_vina_run,
    generate_vina_config,
    get_box_params,
    get_next_run_id,
    get_project_workflow_status,
    get_run_preflight,
    get_run_files_status,
    get_run_runtime_status,
    get_vina_config_preview,
    get_vina_params,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_run_metadata,
    load_project,
    prepare_vina_run,
    update_project_run_summary,
    update_box_params,
    update_vina_params,
    validate_run_prerequisites,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core import project as project_module  # noqa: E402
from adapters import vina_adapter  # noqa: E402
from adapters.vina_adapter import ManagedRunResult  # noqa: E402


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
        ]

    def _execute_with_mock_adapter(
        self,
        project_dir: Path,
        run_id: str,
        *,
        exit_code: int = 0,
        create_output: bool = True,
        on_call: object | None = None,
    ) -> dict[str, object]:
        def fake_run(
            command: list[str],
            cwd: str | Path,
            stdout_path: str | Path,
            stderr_path: str | Path,
            log_path: str | Path,
            **_kwargs: object,
        ) -> ManagedRunResult:
            if callable(on_call):
                on_call(command, cwd)
            Path(stdout_path).write_text("fake vina stdout\n", encoding="utf-8")
            Path(stderr_path).write_text("fake vina stderr\n", encoding="utf-8")
            Path(log_path).write_text("fake vina stdout\n", encoding="utf-8")
            if create_output:
                output = Path(cwd) / "runs" / run_id / "out.pdbqt"
                output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
            return ManagedRunResult(pid=4242, exit_code=exit_code)

        with (
            unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()),
            unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed", side_effect=fake_run),
        ):
            return execute_prepared_vina_run(str(project_dir), run_id)

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

    def test_load_project_uses_selected_directory_when_project_json_has_relative_project_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "portable_project"
            project_dir.mkdir()
            for directory in PROJECT_DIRS:
                (project_dir / directory).mkdir()
            (project_dir / "project.json").write_text(
                json.dumps(
                    {
                        "project_name": "portable_project",
                        "created_at": "2026-06-29T00:00:00+00:00",
                        "updated_at": "2026-06-29T00:00:00+00:00",
                        "project_dir": "examples/portable_project",
                        "receptor": {"raw_file": "raw/receptor_demo.pdb", "file": ""},
                        "ligand": {"raw_file": "raw/ligand_demo.sdf", "file": ""},
                        "runs": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            response = load_project(str(project_dir))

        self.assertTrue(response["ok"])
        self.assertEqual(response["project"]["project_dir"], str(project_dir))

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
            self.assertNotIn("--log", metadata["command"])
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

    def test_execute_vina_run_ignores_untrusted_metadata_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            self._set_run_command(project_dir, run_id, "vina --config configs/vina_config.txt")
            observed: list[list[str]] = []

            response = self._execute_with_mock_adapter(
                project_dir,
                run_id,
                on_call=lambda command, _cwd: observed.append(command),
            )

            self.assertTrue(response["ok"])
            self.assertEqual(observed[0][0], "mock-vina")
            self.assertEqual(observed[0][1:3], ["--config", f"runs/{run_id}/config_snapshot.txt"])

    def test_execute_vina_run_does_not_depend_on_live_receptor_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").unlink()

            response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertTrue(response["ok"])

    def test_execute_vina_run_does_not_depend_on_live_ligand_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "prepared" / "ligand.pdbqt").unlink()

            response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertTrue(response["ok"])

    def test_execute_vina_run_does_not_depend_on_live_config_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            (project_dir / "configs" / "vina_config.txt").unlink()

            response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertTrue(response["ok"])

    def test_execute_vina_run_sets_running_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            self._set_run_command(project_dir, run_id, [sys.executable, "-c", "print('ok')"])
            metadata_path = project_dir / "runs" / run_id / "metadata.json"

            def assert_running(command: list[str], _cwd: str | Path) -> None:
                running_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(running_metadata["status"], "running")
                self.assertTrue(running_metadata["started_at"])
                self.assertEqual(command[0], "mock-vina")

            response = self._execute_with_mock_adapter(project_dir, run_id, on_call=assert_running)

            self.assertTrue(response["ok"])

    def test_execute_fake_vina_success_finishes_and_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(response["metadata"]["status"], "finished")
            self.assertEqual(response["metadata"]["exit_code"], 0)
            self.assertIsNone(response["metadata"]["best_affinity"])
            self.assertTrue((project_dir / "runs" / run_id / "out.pdbqt").is_file())
            self.assertTrue((project_dir / "runs" / run_id / "log.txt").is_file())
            self.assertIn("fake vina stdout", (project_dir / "runs" / run_id / "stdout.txt").read_text(encoding="utf-8"))
            self.assertIn("fake vina stdout", (project_dir / "runs" / run_id / "log.txt").read_text(encoding="utf-8"))
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
            response = self._execute_with_mock_adapter(project_dir, run_id, exit_code=2)

            self.assertFalse(response["ok"])
            self.assertEqual(response["metadata"]["status"], "failed")
            self.assertEqual(response["metadata"]["exit_code"], 2)
            self.assertEqual(response["error"]["code"], "VINA_RUN_FAILED")
            self.assertTrue((project_dir / "runs" / run_id / "stdout.txt").is_file())
            self.assertTrue((project_dir / "runs" / run_id / "stderr.txt").is_file())

    def test_execute_fake_vina_zero_without_output_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            response = self._execute_with_mock_adapter(project_dir, run_id, create_output=False)

            self.assertFalse(response["ok"])
            self.assertEqual(response["metadata"]["status"], "failed")
            self.assertEqual(response["metadata"]["exit_code"], 0)
            self.assertIn("没有生成非空 out.pdbqt", response["metadata"]["error_message"])

    def test_execute_vina_run_does_not_parse_docking_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertTrue(response["ok"])
            self.assertIsNone(response["metadata"]["best_affinity"])
            self.assertFalse((project_dir / "results" / "scores.csv").exists())

    def test_execute_is_not_fully_successful_when_final_project_summary_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            first_summary = {"ok": True, "project": {"runs": [{"run_id": run_id, "status": "running"}]}, "error": None}
            failed_summary = {"ok": False, "project": None, "error": {"code": "RUN_SUMMARY_UPDATE_ERROR", "message": "sync failed"}}

            with unittest.mock.patch(
                "dockstart_core.project.update_project_run_summary",
                side_effect=[first_summary, failed_summary],
            ):
                response = self._execute_with_mock_adapter(project_dir, run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["metadata"]["status"], "finished")
            self.assertEqual(response["error"]["code"], "RUN_SUMMARY_UPDATE_ERROR")

    def test_run_preflight_aggregates_blockers_instead_of_returning_first_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(create_project("demo_project", temp_dir)["project_dir"])
            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_missing_result()):
                response = get_run_preflight(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertFalse(response["ready"])
            blocking_keys = {item["key"] for item in response["checks"] if item["blocking"]}
            self.assertTrue({"receptor", "ligand", "tool"}.issubset(blocking_keys))
            self.assertNotIn("config", blocking_keys)
            config_check = next(item for item in response["checks"] if item["key"] == "config")
            self.assertEqual(config_check["status"], "warning")
            self.assertIn("启动时将生成/刷新", config_check["message"])
            self.assertGreaterEqual(len(response["blockers"]), 3)
            self.assertIn("estimate", response)
            self.assertFalse(response["estimate"]["available"])

    def test_run_preflight_reports_pdbqt_box_tool_output_and_system_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            receptor = Path(temp_dir) / "receptor.pdbqt"
            ligand = Path(temp_dir) / "ligand.pdbqt"
            receptor.write_text(
                "ATOM      1  C1  REC A   1      -4.000   2.000  10.000  0.00  0.00     0.000 C\n"
                "ATOM      2  C2  REC A   1      12.000  18.000  30.000  0.00  0.00     0.000 C\n",
                encoding="utf-8",
            )
            ligand.write_text(
                "ATOM      1  N1  LIG B   1       1.000   1.000   1.000  0.00  0.00     0.000 N\nTORSDOF 3\n",
                encoding="utf-8",
            )
            self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor))["ok"])
            self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand))["ok"])
            self.assertTrue(update_box_params(str(project_dir), {"center_x": 0, "center_y": 0, "center_z": 0, "size_x": 31, "size_y": 30, "size_z": 30})["ok"])
            self.assertTrue(generate_vina_config(str(project_dir))["ok"])

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                response = get_run_preflight(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertTrue(response["ready"])
            self.assertEqual(response["input_stats"]["receptor"]["atom_count"], 2)
            self.assertEqual(response["input_stats"]["receptor"]["coordinate_count"], 2)
            self.assertEqual(response["input_stats"]["receptor"]["coordinate_center"], {"x": 4.0, "y": 10.0, "z": 20.0})
            self.assertEqual(response["input_stats"]["receptor"]["coordinate_bounds"]["min"]["x"], -4.0)
            self.assertEqual(response["input_stats"]["receptor"]["coordinate_bounds"]["max"]["z"], 30.0)
            self.assertEqual(response["input_stats"]["ligand"]["torsdof"], 3)
            self.assertIn("C", response["input_stats"]["receptor"]["atom_types"])
            self.assertEqual(response["box"]["volume_angstrom3"], 27900.0)
            self.assertTrue(response["box"]["warnings"])
            self.assertTrue(response["output"]["writable"])
            self.assertGreater(response["output"]["free_bytes"], 0)
            self.assertGreaterEqual(response["system"]["cpu_count"], 1)
            self.assertEqual(response["tool"]["source"], "auto")
            self.assertEqual(response["next_run_id"], "run_001")
            self.assertNotIn("--log", response["command_preview"])

    def test_run_preflight_treats_missing_or_stale_config_as_refreshable_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            (project_dir / "prepared" / "receptor.pdbqt").write_text(
                "ATOM      1  C1  REC A   1       0.000   0.000   0.000  0.00  0.00     0.000 C\n",
                encoding="utf-8",
            )
            (project_dir / "prepared" / "ligand.pdbqt").write_text(
                "ATOM      1  N1  LIG B   1       1.000   1.000   1.000  0.00  0.00     0.000 N\nTORSDOF 0\n",
                encoding="utf-8",
            )
            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                missing = get_run_preflight(str(project_dir))
            self.assertTrue(missing["ready"])
            missing_check = next(item for item in missing["checks"] if item["key"] == "config")
            self.assertFalse(missing_check["blocking"])
            self.assertEqual(missing_check["status"], "warning")

            self.assertTrue(generate_vina_config(str(project_dir))["ok"])
            self.assertTrue(update_box_params(str(project_dir), {"center_x": 1, "center_y": 2, "center_z": 3, "size_x": 20, "size_y": 20, "size_z": 20})["ok"])
            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                stale = get_run_preflight(str(project_dir))
            self.assertTrue(stale["ready"])
            self.assertEqual(stale["config"]["status"], "stale")
            stale_check = next(item for item in stale["checks"] if item["key"] == "config")
            self.assertFalse(stale_check["blocking"])
            self.assertIn("启动时将生成/刷新", stale_check["message"])

    def test_prepare_run_records_reproducibility_snapshots_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                response = prepare_vina_run(str(project_dir))

            self.assertTrue(response["ok"])
            metadata = response["metadata"]
            self.assertEqual(metadata["stage"], "prepared")
            self.assertEqual(metadata["progress"]["percent"], 0)
            self.assertEqual(metadata["app_version"], __version__)
            self.assertEqual(metadata["vina_source"], "auto")
            self.assertEqual(len(metadata["input_sha256"]["receptor"]), 64)
            self.assertEqual(len(metadata["input_sha256"]["ligand"]), 64)
            self.assertEqual(len(metadata["input_sha256"]["config"]), 64)
            config_snapshot = project_dir / metadata["config_snapshot"]
            self.assertEqual(metadata["input_sha256"]["config"], hashlib.sha256(config_snapshot.read_bytes()).hexdigest())
            receptor_snapshot = project_dir / "runs" / response["run_id"] / "inputs" / "receptor.pdbqt"
            ligand_snapshot = project_dir / "runs" / response["run_id"] / "inputs" / "ligand.pdbqt"
            self.assertTrue(receptor_snapshot.is_file())
            self.assertTrue(ligand_snapshot.is_file())
            snapshot_config_text = config_snapshot.read_text(encoding="utf-8")
            self.assertIn(f"receptor = runs/{response['run_id']}/inputs/receptor.pdbqt", snapshot_config_text)
            self.assertIn(f"ligand = runs/{response['run_id']}/inputs/ligand.pdbqt", snapshot_config_text)
            self.assertIn("box", metadata["snapshots"])
            self.assertIn("vina", metadata["snapshots"])
            self.assertIn("fingerprint", metadata["system"])

    def test_runtime_status_returns_progress_elapsed_and_log_tails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            self.assertTrue(self._execute_with_mock_adapter(project_dir, run_id)["ok"])

            response = get_run_runtime_status(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertEqual(response["stage"], "finished")
            self.assertEqual(response["progress"]["percent"], 100)
            self.assertGreaterEqual(response["elapsed_seconds"], 0)
            self.assertIn("fake vina stdout", response["stdout_tail"])
            self.assertIn("fake vina stdout", response["log_tail"])
            self.assertIn("fake vina stderr", response["stderr_tail"])

    def test_execute_never_runs_arbitrary_metadata_command_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            marker = Path(temp_dir) / "metadata-command-executed.txt"
            malicious = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('pwned')"]
            self._set_run_command(project_dir, run_id, malicious)
            observed: list[list[str]] = []

            response = self._execute_with_mock_adapter(
                project_dir,
                run_id,
                on_call=lambda command, _cwd: observed.append(command),
            )

            self.assertTrue(response["ok"])
            self.assertFalse(marker.exists())
            self.assertEqual(observed[0], ["mock-vina", "--config", f"runs/{run_id}/config_snapshot.txt", "--out", f"runs/{run_id}/out.pdbqt"])

    def test_runtime_tail_paths_ignore_metadata_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            secret = project_dir / "secret.txt"
            secret.write_text("DO_NOT_LEAK_THIS", encoding="utf-8")
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"stdout_file": "../../secret.txt", "stderr_file": "../../secret.txt", "log_file": "../../secret.txt"})
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = get_run_runtime_status(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertNotIn("DO_NOT_LEAK_THIS", response["stdout_tail"] + response["stderr_tail"] + response["log_tail"])

    def test_preflight_rejects_outside_config_without_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            outside = Path(temp_dir) / "outside-secret.txt"
            outside.write_text("SECRET_CONFIG", encoding="utf-8")
            project_path = project_dir / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["config"]["vina_config_file"] = "../outside-secret.txt"
            project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                response = get_run_preflight(str(project_dir))

            self.assertFalse(response["ready"])
            config_check = next(item for item in response["checks"] if item["key"] == "config")
            self.assertTrue(config_check["blocking"])
            self.assertEqual(response["config"]["status"], "invalid")
            self.assertEqual(response["config"]["sha256"], "")

    def test_execute_rejects_tampered_snapshot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            snapshot = project_dir / "runs" / run_id / "inputs" / "ligand.pdbqt"
            snapshot.write_text(snapshot.read_text(encoding="utf-8") + "REMARK tampered\n", encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed") as run_mock:
                response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_SNAPSHOT_HASH_MISMATCH")
            run_mock.assert_not_called()

    def test_adapter_started_callback_exception_leaves_no_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured_pid: list[int] = []

            def fail_started(pid: int) -> None:
                captured_pid.append(pid)
                raise RuntimeError("callback exploded")

            result = vina_adapter.run_managed(
                [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
                root,
                root / "stdout.txt",
                root / "stderr.txt",
                root / "log.txt",
                on_started=fail_started,
            )

            self.assertTrue(result.error)
            self.assertEqual(len(captured_pid), 1)
            self.assertFalse(vina_adapter.is_process_running(captured_pid[0]))

    def test_adapter_exclusive_log_creation_refuses_hardlink_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            protected = root / "protected.json"
            protected.write_text('{"safe": true}\n', encoding="utf-8")
            stdout_path = root / "stdout.txt"
            marker = root / "spawned.txt"
            try:
                os.link(protected, stdout_path)
            except OSError:
                self.skipTest("当前文件系统不支持创建硬链接。")

            result = vina_adapter.run_managed(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('spawned')"],
                root,
                stdout_path,
                root / "stderr.txt",
                root / "log.txt",
            )

            self.assertTrue(result.error)
            self.assertIsNone(result.pid)
            self.assertFalse(marker.exists())
            self.assertEqual(protected.read_text(encoding="utf-8"), '{"safe": true}\n')

    def test_windows_terminate_uses_verified_handle_not_taskkill_pid(self) -> None:
        recorded = {"pid": 42, "executable_path": "vina.exe", "creation_token": "old"}
        terminated = {"ok": True, "pid": 42, "message": "terminated", "raw_error": ""}
        with (
            unittest.mock.patch.object(vina_adapter.sys, "platform", "win32"),
            unittest.mock.patch.object(vina_adapter, "_terminate_windows_process_by_handle", return_value=terminated) as handle_mock,
            unittest.mock.patch.object(vina_adapter.subprocess, "run") as taskkill_mock,
        ):
            response = vina_adapter.terminate_process(
                42,
                expected_executable="vina.exe",
                recorded_identity=recorded,
            )

        self.assertTrue(response["ok"])
        handle_mock.assert_called_once_with(42, "vina.exe", recorded, 5.0)
        taskkill_mock.assert_not_called()

    def test_runtime_poll_does_not_overwrite_successful_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            spawned = threading.Event()
            release = threading.Event()
            execution: dict[str, object] = {}
            fake_identity = {
                "pid": 7788,
                "executable_path": "mock-vina",
                "creation_token": "fast-finish",
            }
            executor_identity = {
                "pid": os.getpid(),
                "executable_path": sys.executable,
                "creation_token": "executor-alive",
            }

            def fake_process_identity(pid: int) -> dict[str, object]:
                return executor_identity if pid == os.getpid() else fake_identity

            def fake_verify_process(pid: int, *_args: object, **_kwargs: object) -> dict[str, object]:
                if pid == os.getpid():
                    return {"ok": True, "running": True, "identity": executor_identity}
                return {"ok": False, "running": False, "message": "process already exited"}

            def fake_run(
                command: list[str],
                cwd: str | Path,
                stdout_path: str | Path,
                stderr_path: str | Path,
                log_path: str | Path,
                **kwargs: object,
            ) -> ManagedRunResult:
                Path(stdout_path).write_text("fake vina stdout\n", encoding="utf-8")
                Path(stderr_path).write_text("", encoding="utf-8")
                Path(log_path).write_text("fake vina stdout\n", encoding="utf-8")
                kwargs["on_started"](7788)
                spawned.set()
                self.assertTrue(release.wait(timeout=5))
                output_arg = command[command.index("--out") + 1]
                (Path(cwd) / output_arg).write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                return ManagedRunResult(pid=7788, exit_code=0)

            def execute() -> None:
                execution["response"] = execute_prepared_vina_run(str(project_dir), run_id)

            with (
                unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()),
                unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed", side_effect=fake_run),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.get_process_identity",
                    side_effect=fake_process_identity,
                ),
                unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.verify_process_identity",
                    side_effect=fake_verify_process,
                ),
            ):
                thread = threading.Thread(target=execute, daemon=True)
                thread.start()
                self.assertTrue(spawned.wait(timeout=5))

                for _probe in range(3):
                    settling = get_run_runtime_status(str(project_dir), run_id)
                    self.assertEqual(settling["metadata"]["status"], "running")
                    self.assertTrue(settling["executor_active"])
                    self.assertIn("process_missing_since", settling["metadata"])

                release.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

            response = execution["response"]
            self.assertTrue(response["ok"])
            self.assertEqual(response["metadata"]["status"], "finished")
            self.assertNotIn("process_missing_since", response["metadata"])

    def test_executor_identity_uses_the_kernel_reported_executable(self) -> None:
        identity = vina_adapter.get_process_identity(os.getpid())
        if identity is None or not identity.get("executable_path"):
            self.skipTest("当前平台无法读取本测试进程的创建身份。")
        metadata = {
            "executor_pid": os.getpid(),
            "executor_executable": str(identity["executable_path"]),
            "executor_identity": identity,
        }

        verified = project_module._verify_metadata_process(
            metadata,
            pid_key="executor_pid",
            executable_key="executor_executable",
            identity_key="executor_identity",
        )

        self.assertTrue(verified["ok"], verified)

    def test_runtime_probe_rechecks_identity_before_converging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "pid": 7788,
                    "trusted_executable": "mock-vina",
                    "process_identity": {
                        "pid": 7788,
                        "executable_path": "mock-vina",
                        "creation_token": "recovered",
                    },
                    "process_missing_since": "probe-token",
                }
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.verify_process_identity",
                side_effect=[
                    {"ok": False, "running": False, "message": "transient miss"},
                    {"ok": True, "running": True},
                    {"ok": True, "running": True},
                ],
            ):
                response = get_run_runtime_status(str(project_dir), run_id)

            self.assertEqual(response["metadata"]["status"], "running")
            self.assertTrue(response["process_active"])
            self.assertNotIn("process_missing_since", response["metadata"])

    def test_cancel_after_child_exit_waits_for_live_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            executor_identity = {
                "pid": os.getpid(),
                "executable_path": sys.executable,
                "creation_token": "executor-finalizing",
            }
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "pid": 7788,
                    "trusted_executable": "mock-vina",
                    "process_identity": {
                        "pid": 7788,
                        "executable_path": "mock-vina",
                        "creation_token": "already-exited",
                    },
                    "executor_pid": os.getpid(),
                    "executor_executable": sys.executable,
                    "executor_identity": executor_identity,
                }
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            def fake_verify(pid: int, *_args: object, **_kwargs: object) -> dict[str, object]:
                if pid == os.getpid():
                    return {"ok": True, "running": True, "identity": executor_identity}
                return {"ok": False, "running": False, "message": "child already exited"}

            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.verify_process_identity",
                side_effect=fake_verify,
            ):
                response = cancel_vina_run(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertFalse(response["accepted"])
            self.assertEqual(response["metadata"]["status"], "running")
            self.assertFalse((project_dir / "runs" / run_id / ".cancel_requested").exists())
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            summary = next(item for item in project["runs"] if item["run_id"] == run_id)
            self.assertNotEqual(summary["status"], "interrupted")

    def test_runtime_converges_stale_running_to_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "process_missing_since": "2020-01-01T00:00:01+00:00",
                    "pid": 99999999,
                    "trusted_executable": sys.executable,
                    "process_identity": {"pid": 99999999, "executable_path": sys.executable, "creation_token": "gone"},
                },
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = get_run_runtime_status(str(project_dir), run_id)

            self.assertEqual(response["stage"], "interrupted")
            self.assertEqual(response["metadata"]["status"], "interrupted")
            self.assertFalse(response["process_active"])

    def test_cancel_rejects_pid_identity_mismatch_without_killing_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "pid": os.getpid(),
                    "trusted_executable": str(Path(temp_dir) / "not-vina.exe"),
                    "process_identity": {"pid": os.getpid(), "executable_path": "wrong", "creation_token": "wrong"},
                },
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = cancel_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_CANCEL_IDENTITY_MISMATCH")
            self.assertTrue(vina_adapter.is_process_running(os.getpid()))
            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "running")
            self.assertIn("process_missing_since", final)

    def test_cancel_rejects_legacy_running_metadata_without_creation_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "status": "running",
                    "stage": "running",
                    "started_at": "2020-01-01T00:00:00+00:00",
                    "pid": os.getpid(),
                    "trusted_executable": sys.executable,
                    "process_identity": None,
                },
            )
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.terminate_process") as terminate_mock:
                response = cancel_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_CANCEL_IDENTITY_MISMATCH")
            terminate_mock.assert_not_called()
            self.assertTrue(vina_adapter.is_process_running(os.getpid()))
            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "running")

    def test_execute_rejects_stdout_symlink_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            outside = Path(temp_dir) / "outside-output.txt"
            stdout_path = project_dir / "runs" / run_id / "stdout.txt"
            try:
                stdout_path.symlink_to(outside)
            except OSError:
                self.skipTest("当前 Windows 权限不允许创建符号链接。")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed") as run_mock:
                response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_PATH_SYMLINK_UNSAFE")
            run_mock.assert_not_called()
            self.assertFalse(outside.exists())

    def test_run_directory_symlink_outside_project_is_rejected_before_lock_or_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            original_run_dir = project_dir / "runs" / run_id
            outside_run_dir = Path(temp_dir) / "outside-run"
            shutil.move(str(original_run_dir), str(outside_run_dir))
            (outside_run_dir / ".metadata.lock").unlink(missing_ok=True)
            try:
                original_run_dir.symlink_to(outside_run_dir, target_is_directory=True)
            except OSError:
                self.skipTest("当前 Windows 权限不允许创建符号链接。")

            response = load_run_metadata(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_PATH_UNSAFE")
            self.assertFalse((outside_run_dir / ".metadata.lock").exists())

    def test_execute_rejects_stdout_symlink_to_project_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            project_json = project_dir / "project.json"
            before = project_json.read_bytes()
            stdout_path = project_dir / "runs" / run_id / "stdout.txt"
            try:
                stdout_path.symlink_to(project_json)
            except OSError:
                self.skipTest("当前 Windows 权限不允许创建符号链接。")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed") as run_mock:
                response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_PATH_SYMLINK_UNSAFE")
            run_mock.assert_not_called()
            self.assertEqual(project_json.read_bytes(), before)

    def test_execute_rejects_preexisting_stdout_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            project_json = project_dir / "project.json"
            before = project_json.read_bytes()
            stdout_path = project_dir / "runs" / run_id / "stdout.txt"
            try:
                os.link(project_json, stdout_path)
            except OSError:
                self.skipTest("当前文件系统不支持创建硬链接。")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed") as run_mock:
                response = execute_prepared_vina_run(str(project_dir), run_id)

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "RUN_OUTPUT_ALREADY_EXISTS")
            run_mock.assert_not_called()
            self.assertEqual(project_json.read_bytes(), before)

    def test_project_run_summary_updates_are_serialized_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            original_write = project_module._write_project_json_unlocked
            barrier = threading.Barrier(3)
            responses: list[dict[str, object]] = []

            def slow_write(project_root: Path, project: object) -> None:
                time.sleep(0.05)
                original_write(project_root, project)

            def update(field: str) -> None:
                barrier.wait(timeout=5)
                responses.append(update_project_run_summary(str(project_dir), run_id, {field: field}))

            with unittest.mock.patch.object(project_module, "_write_project_json_unlocked", side_effect=slow_write):
                threads = [
                    threading.Thread(target=update, args=("field_a",)),
                    threading.Thread(target=update, args=("field_b",)),
                ]
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=5)
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())

            self.assertEqual(len(responses), 2)
            self.assertTrue(all(response.get("ok") for response in responses))
            data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            summary = next(item for item in data["runs"] if item["run_id"] == run_id)
            self.assertEqual(summary["field_a"], "field_a")
            self.assertEqual(summary["field_b"], "field_b")

    def test_project_run_summary_lock_is_cross_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            gate = Path(temp_dir) / "start.gate"
            script = """
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[5])
from dockstart_core import project as project_module
original_write = project_module._write_project_json_unlocked
def slow_write(project_root, project):
    time.sleep(0.2)
    original_write(project_root, project)
project_module._write_project_json_unlocked = slow_write
gate = Path(sys.argv[4])
while not gate.exists():
    time.sleep(0.01)
print(json.dumps(project_module.update_project_run_summary(sys.argv[1], sys.argv[2], {sys.argv[3]: sys.argv[3]})))
"""
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(project_dir), run_id, field, str(gate), str(BACKEND_ROOT)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for field in ("process_a", "process_b")
            ]
            gate.write_text("go", encoding="utf-8")
            outputs = [process.communicate(timeout=10) for process in processes]

            for process, (stdout, stderr) in zip(processes, outputs, strict=True):
                self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue(json.loads(stdout)["ok"])
            data = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            summary = next(item for item in data["runs"] if item["run_id"] == run_id)
            self.assertEqual(summary["process_a"], "process_a")
            self.assertEqual(summary["process_b"], "process_b")

    def test_cancel_without_pid_is_accepted_pending_not_false_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"status": "running", "stage": "starting", "started_at": "2020-01-01T00:00:00+00:00", "pid": None})
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = cancel_vina_run(str(project_dir), run_id)

            self.assertTrue(response["ok"])
            self.assertTrue(response["accepted"])
            self.assertFalse(response["cancelled"])
            self.assertEqual(response["stage"], "cancel_pending")
            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(final["status"], "running")
            self.assertEqual(final["stage"], "cancel_pending")

    def test_legacy_finished_runtime_defaults_to_full_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            metadata_path = project_dir / "runs" / run_id / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update({"status": "finished", "stage": "finished", "finished_at": "2020-01-01T00:01:00+00:00"})
            metadata.pop("progress", None)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

            response = get_run_runtime_status(str(project_dir), run_id)

            self.assertEqual(response["progress"]["percent"], 100)

    def test_cancel_vina_run_terminates_process_and_keeps_cancelled_status(self) -> None:
        for _iteration in range(5):
            with tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
                stop_event = threading.Event()
                result: dict[str, object] = {}
                fake_identity = {"pid": 7788, "executable_path": "mock-vina", "creation_token": "test-run"}

                def fake_run(
                    _command: list[str],
                    _cwd: str | Path,
                    stdout_path: str | Path,
                    stderr_path: str | Path,
                    log_path: str | Path,
                    **kwargs: object,
                ) -> ManagedRunResult:
                    Path(stdout_path).write_text("started\n", encoding="utf-8")
                    Path(stderr_path).write_text("", encoding="utf-8")
                    Path(log_path).write_text("started\n", encoding="utf-8")
                    kwargs["on_started"](7788)
                    stop_event.wait(timeout=10)
                    return ManagedRunResult(pid=7788, exit_code=-1)

                def fake_terminate(
                    pid: int,
                    *,
                    expected_executable: str,
                    recorded_identity: dict[str, object] | None,
                    **_kwargs: object,
                ) -> dict[str, object]:
                    self.assertEqual(pid, 7788)
                    self.assertEqual(expected_executable, "mock-vina")
                    self.assertEqual(recorded_identity, fake_identity)
                    stop_event.set()
                    return {"ok": True, "pid": pid, "message": "terminated", "raw_error": ""}

                def execute() -> None:
                    result["response"] = execute_prepared_vina_run(str(project_dir), run_id)

                with (
                    unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()),
                    unittest.mock.patch("dockstart_core.project.vina_adapter.run_managed", side_effect=fake_run),
                    unittest.mock.patch("dockstart_core.project.vina_adapter.get_process_identity", return_value=fake_identity),
                    unittest.mock.patch("dockstart_core.project.vina_adapter.verify_process_identity", return_value={"ok": True, "running": True, "identity": fake_identity}),
                    unittest.mock.patch("dockstart_core.project.vina_adapter.terminate_process", side_effect=fake_terminate),
                ):
                    thread = threading.Thread(target=execute, daemon=True)
                    thread.start()
                    metadata_path = project_dir / "runs" / run_id / "metadata.json"
                    deadline = time.monotonic() + 5
                    metadata: dict[str, object] = {}
                    while time.monotonic() < deadline:
                        try:
                            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            time.sleep(0.02)
                            continue
                        if metadata.get("pid") == 7788:
                            break
                        time.sleep(0.02)
                    self.assertEqual(metadata.get("pid"), 7788)
                    cancelled = cancel_vina_run(str(project_dir), run_id)
                    self.assertTrue(cancelled["ok"])
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())

                final_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(final_metadata["status"], "cancelled")
                self.assertNotIn("error_message", final_metadata)
                execution_response = result["response"]
                self.assertTrue(execution_response["ok"])
                self.assertEqual(execution_response["metadata"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
