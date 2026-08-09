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
    build_markdown_report,
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
    update_run_settings,
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
    @staticmethod
    def _macrocycle_candidate_pdbqt() -> str:
        return (
            "REMARK SMILES C1CCCCCCC1\n"
            "REMARK SMILES IDX 1 1 2 2\n"
            "ROOT\n"
            "ATOM      1  C1  LIG A   1       0.000   0.000   0.000"
            "  1.00  0.00     0.000 C\n"
            "ATOM      2  G1  LIG A   1       1.000   0.000   0.000"
            "  1.00  0.00     0.000 G0\n"
            "ATOM      3  G2  LIG A   1       0.000   1.000   0.000"
            "  1.00  0.00     0.000 G1\n"
            "ENDROOT\n"
            "TORSDOF 1\n"
        )

    def _install_formal_macrocycle_preparation(self, project_dir: Path) -> dict[str, str]:
        ligand = project_dir / "prepared" / "ligand.pdbqt"
        ligand.write_text(self._macrocycle_candidate_pdbqt(), encoding="utf-8")
        ligand_sha256 = hashlib.sha256(ligand.read_bytes()).hexdigest()
        ligand_size = ligand.stat().st_size

        prep_dir = project_dir / "preparation" / "ligand_001"
        prep_dir.mkdir()
        review_dir = (
            project_dir
            / "preparation"
            / "macrocycle_reviews"
            / "review_001"
        )
        review_dir.mkdir(parents=True)
        review_input = review_dir / "input.sdf"
        frozen_input = prep_dir / "macrocycle_input.sdf"
        review_input.write_text("mock reviewed macrocycle\n", encoding="utf-8")
        frozen_input.write_bytes(review_input.read_bytes())
        frozen_sha256 = hashlib.sha256(frozen_input.read_bytes()).hexdigest()
        frozen_size = frozen_input.stat().st_size

        confirmation_sha256 = "c" * 64
        atom_table_sha256 = "a" * 64
        bond_topology = [
            {
                "atom_indices_zero_based": [0, 1],
                "bond_type": "SINGLE",
                "bond_order": 1.0,
                "is_aromatic": False,
                "is_conjugated": False,
                "stereo": "STEREONONE",
                "stereo_atom_indices_zero_based": [],
            }
        ]
        bond_topology_sha256 = hashlib.sha256(
            json.dumps(
                bond_topology,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        atom_indexing = {
            "hydrogen_policy": project_module.MACROCYCLE_HYDROGEN_POLICY,
            "source_atom_count": 2,
            "prepared_atom_count": 2,
            "source_heavy_atom_indices_zero_based": [0, 1],
            "source_to_prepared_indices_zero_based": [0, 1],
            "added_hydrogen_indices_zero_based": [],
            "source_indices_preserved": True,
        }
        contract = {
            "protocol_id": "meeko_macrocycle",
            "schema_version": project_module.MACROCYCLE_CONTRACT_SCHEMA_VERSION,
            "analysis_version": project_module.MACROCYCLE_ANALYSIS_VERSION,
            "meeko_api_profile": project_module.MACROCYCLE_MEEKO_API_PROFILE,
            "hydrogen_policy": project_module.MACROCYCLE_HYDROGEN_POLICY,
            "review_id": "review_001",
            "confirmation_sha256": confirmation_sha256,
            "selection_mode": "candidate",
            "candidate_id": "candidate_001",
            "exact_bonds": [[0, 1]],
            "selected_bonds": [
                {
                    "atom_indices_zero_based": [0, 1],
                    "atom_numbers_one_based": [1, 2],
                    "atom_labels": ["C1", "C2"],
                }
            ],
            "atom_table_sha256": atom_table_sha256,
            "atom_indexing": atom_indexing,
            "bond_topology": bond_topology,
            "bond_topology_sha256": bond_topology_sha256,
            "review_input": {
                "relative_path": review_input.relative_to(project_dir).as_posix(),
                "sha256": frozen_sha256,
                "size_bytes": frozen_size,
            },
            "runtime_input": {
                "relative_path": frozen_input.relative_to(project_dir).as_posix(),
                "sha256": frozen_sha256,
                "size_bytes": frozen_size,
            },
            "tool_versions": {
                "meeko": "0.7.1",
                "rdkit": "2026.03.3",
            },
        }
        contract_file = prep_dir / "macrocycle_contract.json"
        contract_file.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        contract_sha256 = hashlib.sha256(contract_file.read_bytes()).hexdigest()

        candidate_file = prep_dir / "candidate_ligand.pdbqt"
        candidate_file.write_bytes(ligand.read_bytes())
        evidence = {
            "ok": True,
            "protocol_id": "meeko_macrocycle",
            "output_file": str(candidate_file.resolve()),
            "output_sha256": ligand_sha256,
            "output_size_bytes": ligand_size,
            "selection_mode": "candidate",
            "candidate_id": "candidate_001",
            "expected_bonds": [[0, 1]],
            "actual_bonds": [[0, 1]],
            "glue_pseudo_atom_count": 2,
            "atom_table_sha256": atom_table_sha256,
            "bond_topology_sha256": bond_topology_sha256,
            "hydrogen_policy": project_module.MACROCYCLE_HYDROGEN_POLICY,
            "atom_indexing": atom_indexing,
            "review_id": "review_001",
            "confirmation_sha256": confirmation_sha256,
            "meeko_version": "0.7.1",
            "rdkit_version": "2026.03.3",
            "error": None,
        }
        evidence_file = prep_dir / "macrocycle_evidence.json"
        evidence_file.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence_sha256 = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
        inspection = {
            "embedded_topology": True,
            "macrocycle_evidence": True,
            "glue_pseudo_atoms": [
                {"serial": 2, "atom_type": "G0"},
                {"serial": 3, "atom_type": "G1"},
            ],
        }
        contract_relative = contract_file.relative_to(project_dir).as_posix()
        evidence_relative = evidence_file.relative_to(project_dir).as_posix()
        frozen_relative = frozen_input.relative_to(project_dir).as_posix()
        metadata = {
            "prep_id": "ligand_001",
            "target": "ligand",
            "status": "finished",
            "method": "meeko_macrocycle",
            "protocol": "meeko_macrocycle",
            "protocol_mode": "reviewed",
            "published": True,
            "output_non_empty": True,
            "options": {
                "macrocycle": {
                    "mode": "reviewed",
                    "review_id": "review_001",
                    "confirmation_sha256": confirmation_sha256,
                }
            },
            "macrocycle_contract": contract,
            "macrocycle_contract_file": contract_relative,
            "macrocycle_contract_sha256": contract_sha256,
            "macrocycle_evidence_file": evidence_relative,
            "macrocycle_input_file": frozen_relative,
            "macrocycle_expected_output_evidence": {
                "selection_mode": "candidate",
                "candidate_id": "candidate_001",
                "exact_bonds": [[0, 1]],
                "atom_table_sha256": atom_table_sha256,
                "bond_topology_sha256": bond_topology_sha256,
                "confirmation_sha256": confirmation_sha256,
            },
            "protocol_evidence": {
                "ok": True,
                "mode": "reviewed",
                "contract_file": contract_relative,
                "contract_sha256": contract_sha256,
                "contract_snapshot": {
                    "path": contract_relative,
                    "sha256": contract_sha256,
                    "size": contract_file.stat().st_size,
                },
                "input_snapshot": {
                    "path": frozen_relative,
                    "sha256": frozen_sha256,
                    "size": frozen_size,
                },
                "evidence_file": evidence_relative,
                "evidence_snapshot": {
                    "path": evidence_relative,
                    "sha256": evidence_sha256,
                    "size": evidence_file.stat().st_size,
                },
                "evidence": evidence,
                "candidate_output": {
                    "path": candidate_file.relative_to(project_dir).as_posix(),
                    "sha256": ligand_sha256,
                    "size": ligand_size,
                },
                "inspection": inspection,
                "issues": [],
            },
            "meeko_version": "0.7.1",
            "rdkit_version": "2026.03.3",
            "python_source": "bundled",
            "output": {
                "path": "prepared/ligand.pdbqt",
                "sha256": ligand_sha256,
                "size": ligand_size,
            },
        }
        metadata_file = prep_dir / "metadata.json"
        metadata_file.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["preparation"]["ligand"].update(
            {
                "prep_id": "ligand_001",
                "status": "finished",
                "method": "meeko_macrocycle",
                "metadata_file": metadata_file.relative_to(project_dir).as_posix(),
            }
        )
        project_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "contract_sha256": contract_sha256,
            "evidence_sha256": evidence_sha256,
            "bond_topology_sha256": bond_topology_sha256,
            "evidence_file": str(evidence_file),
            "candidate_absolute_path": str(candidate_file.resolve()),
        }

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
        output_bytes: bytes | None = None,
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
                if output_bytes is None:
                    output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
                else:
                    output.write_bytes(output_bytes)
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
            self.assertEqual(project["receptor"]["source_id"], "source_receptor.pdbqt")

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
            self.assertEqual(project["ligand"]["source_id"], "source_ligand.pdbqt")

    def test_import_pdbqt_rejects_existing_hardlink_target_without_touching_external_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            source = Path(temp_dir) / "source_ligand.pdbqt"
            source.write_bytes(b"REMARK new ligand\n")
            external = Path(temp_dir) / "external_ligand.pdbqt"
            external.write_bytes(b"REMARK external original\n")
            target = project_dir / "prepared" / "ligand.pdbqt"
            try:
                os.link(external, target)
            except OSError as exc:
                self.skipTest(f"当前文件系统不支持硬链接测试：{exc}")
            project_before = (project_dir / "project.json").read_bytes()

            response = import_ligand_pdbqt(str(project_dir), str(source))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_FILE_TARGET_HARDLINKED")
            self.assertEqual(external.read_bytes(), b"REMARK external original\n")
            self.assertEqual(target.read_bytes(), b"REMARK external original\n")
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_import_pdbqt_rolls_back_prepared_file_when_project_save_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            target = project_dir / "prepared" / "ligand.pdbqt"
            target.write_bytes(b"REMARK old ligand\n")
            source = Path(temp_dir) / "source_ligand.pdbqt"
            source.write_bytes(b"REMARK new ligand\n")
            project_before = (project_dir / "project.json").read_bytes()
            conflict = project_module._error(
                "PROJECT_SAVE_CONFLICT",
                "project.json 已被其他操作更新。",
            )

            with unittest.mock.patch.object(project_module, "save_project", return_value=conflict):
                response = import_ligand_pdbqt(str(project_dir), str(source))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertEqual(target.read_bytes(), b"REMARK old ligand\n")
            self.assertEqual((project_dir / "project.json").read_bytes(), project_before)

    def test_import_pdbqt_rejects_symlink_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            real_source = Path(temp_dir) / "real_ligand.pdbqt"
            real_source.write_bytes(b"REMARK ligand\n")
            linked_source = Path(temp_dir) / "linked_ligand.pdbqt"
            try:
                linked_source.symlink_to(real_source)
            except OSError as exc:
                self.skipTest(f"当前环境不允许创建符号链接：{exc}")

            response = import_ligand_pdbqt(str(project_dir), str(linked_source))

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PDBQT_SOURCE_UNSAFE")
            self.assertFalse((project_dir / "prepared" / "ligand.pdbqt").exists())

    def test_import_pdbqt_preserves_explicit_display_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "frozen_hash.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

            response = import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
                "P69",
            )

            self.assertTrue(response["ok"])
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["ligand"]["source_id"], "P69")
            self.assertEqual(project["ligand"]["query_type"], "local_file")

    def test_import_pdbqt_preserves_inchi_display_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "frozen_hash.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")
            source_label = "InChI=1S/C8H10N4O2/c1-10-6-5(7(13)11:8(10)14)9-3-2-4-12-6/h2-4H,1H3"

            response = import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
                source_label,
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["project"]["ligand"]["source_id"], source_label)

    def test_import_pdbqt_normalizes_unicode_and_control_characters_in_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "unicode.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

            response = import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
                "  配体 α/β:\x00\t候选\n二号\x1f  ",
            )

            self.assertTrue(response["ok"])
            self.assertEqual(
                response["project"]["ligand"]["source_id"],
                "配体 α/β: 候选 二号",
            )

    def test_import_pdbqt_limits_display_identity_to_240_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "long-label.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")
            source_label = "长" * 300

            response = import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
                source_label,
            )

            self.assertTrue(response["ok"])
            normalized = response["project"]["ligand"]["source_id"]
            self.assertEqual(normalized, "长" * 240)
            self.assertEqual(len(normalized), 240)

    def test_import_pdbqt_empty_normalized_label_falls_back_to_source_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            ligand_source = Path(temp_dir) / "fallback-name.pdbqt"
            ligand_source.write_text("REMARK ligand\n", encoding="utf-8")

            response = import_ligand_pdbqt(
                str(project_dir),
                str(ligand_source),
                " \x00\t\n\x1f ",
            )

            self.assertTrue(response["ok"])
            self.assertEqual(
                response["project"]["ligand"]["source_id"],
                ligand_source.name,
            )

    def test_import_pdbqt_clears_only_imported_target_current_preparation(self) -> None:
        for role, importer in (
            ("receptor", import_receptor_pdbqt),
            ("ligand", import_ligand_pdbqt),
        ):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
                project_response = create_project("demo_project", temp_dir)
                project_dir = Path(project_response["project_dir"])
                project_json = project_dir / "project.json"
                project = json.loads(project_json.read_text(encoding="utf-8"))
                other_role = "ligand" if role == "receptor" else "receptor"
                for target in ("receptor", "ligand"):
                    prep_id = f"{target}_001"
                    project["preparation"][target].update(
                        {
                            "prep_id": prep_id,
                            "status": "finished",
                            "method": "meeko",
                            "input_file": f"raw/{target}.pdb",
                            "metadata_file": f"preparation/{prep_id}/metadata.json",
                            "finished_at": "2026-08-03T00:00:00+00:00",
                        }
                    )
                    project["latest_preparation"][target] = prep_id
                    record_dir = project_dir / "preparation" / prep_id
                    record_dir.mkdir()
                    (record_dir / "metadata.json").write_text(
                        json.dumps({"prep_id": prep_id}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                project_json.write_text(
                    json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                source = Path(temp_dir) / f"new-{role}.pdbqt"
                source.write_text(f"REMARK {role}\n", encoding="utf-8")

                response = importer(str(project_dir), str(source))

                self.assertTrue(response["ok"])
                imported_preparation = response["project"]["preparation"][role]
                self.assertEqual(imported_preparation["prep_id"], "")
                self.assertEqual(imported_preparation["status"], "not_started")
                self.assertIsNone(imported_preparation["method"])
                self.assertEqual(imported_preparation["metadata_file"], "")
                self.assertEqual(response["project"]["latest_preparation"][role], "")
                self.assertEqual(
                    response["project"]["preparation"][other_role]["prep_id"],
                    f"{other_role}_001",
                )
                self.assertEqual(
                    response["project"]["latest_preparation"][other_role],
                    f"{other_role}_001",
                )
                self.assertTrue(
                    (project_dir / "preparation" / f"{role}_001" / "metadata.json").is_file()
                )

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
            self.assertEqual(response["vina"]["scoring"], "vina")
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
                    "scoring": "vinardo",
                    "exhaustiveness": 16,
                    "num_modes": 12,
                    "energy_range": 3.5,
                    "cpu": 4,
                    "seed": 12345,
                },
            )

            self.assertTrue(response["ok"])
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project["vina"]["scoring"], "vinardo")
            self.assertEqual(project["vina"]["exhaustiveness"], 16)
            self.assertEqual(project["vina"]["energy_range"], 3.5)
            self.assertEqual(project["vina"]["seed"], 12345)

    def test_update_vina_params_rejects_ad4_without_maps_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)

            response = update_vina_params(
                project_response["project_dir"],
                {
                    "scoring": "ad4",
                    "exhaustiveness": 8,
                    "num_modes": 9,
                    "energy_range": 4,
                    "cpu": 0,
                    "seed": None,
                },
            )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_SCORING_INVALID")

    def test_update_run_settings_commits_box_vina_and_protocol_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            before = load_project(str(project_dir))["project"]
            box = {
                "center_x": 1.5,
                "center_y": -2,
                "center_z": 3,
                "size_x": 24,
                "size_y": 26,
                "size_z": 28,
            }

            with unittest.mock.patch.object(
                project_module,
                "save_project",
                wraps=project_module.save_project,
            ) as save_mock:
                response = update_run_settings(
                    str(project_dir),
                    box,
                    {"scoring": "vinardo", "exhaustiveness": 16, "seed": 42},
                    "local_only",
                    True,
                )

            self.assertTrue(response["ok"], response)
            self.assertEqual(save_mock.call_count, 1)
            loaded = load_project(str(project_dir))["project"]
            self.assertEqual(loaded["revision"], before["revision"] + 1)
            self.assertEqual(loaded["box"], box)
            self.assertEqual(loaded["vina"]["scoring"], "vinardo")
            self.assertEqual(loaded["vina"]["exhaustiveness"], 16)
            self.assertEqual(loaded["vina"]["seed"], 42)
            self.assertEqual(loaded["docking_protocol"]["run_mode"], "local_only")
            self.assertTrue(loaded["docking_protocol"]["autobox"])

    def test_update_run_settings_validation_failure_persists_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            before = (project_dir / "project.json").read_bytes()

            with unittest.mock.patch.object(project_module, "save_project") as save_mock:
                response = update_run_settings(
                    str(project_dir),
                    {
                        "center_x": 10,
                        "center_y": 11,
                        "center_z": 12,
                        "size_x": 30,
                        "size_y": 30,
                        "size_z": 30,
                    },
                    {"exhaustiveness": 0},
                    "dock",
                    False,
                )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "VINA_PARAM_POSITIVE_REQUIRED")
            save_mock.assert_not_called()
            self.assertEqual((project_dir / "project.json").read_bytes(), before)

    def test_update_run_settings_save_conflict_persists_no_partial_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            before = (project_dir / "project.json").read_bytes()
            conflict = project_module._error(
                "PROJECT_SAVE_CONFLICT",
                "project.json 已被其他操作更新。",
            )

            with unittest.mock.patch.object(project_module, "save_project", return_value=conflict):
                response = update_run_settings(
                    str(project_dir),
                    {
                        "center_x": 10,
                        "center_y": 11,
                        "center_z": 12,
                        "size_x": 30,
                        "size_y": 30,
                        "size_z": 30,
                    },
                    {"exhaustiveness": 16},
                    "dock",
                    False,
                )

            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "PROJECT_SAVE_CONFLICT")
            self.assertEqual((project_dir / "project.json").read_bytes(), before)

    def test_update_run_settings_cli_updates_all_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_response = create_project("demo_project", temp_dir)
            project_dir = Path(project_response["project_dir"])
            box = {
                "center_x": 4,
                "center_y": 5,
                "center_z": 6,
                "size_x": 22,
                "size_y": 23,
                "size_z": 24,
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dockstart_core.project",
                    "update-run-settings",
                    str(project_dir),
                    json.dumps(box),
                    json.dumps({"exhaustiveness": 12, "seed": 7}),
                    "dock",
                    "false",
                ],
                cwd=BACKEND_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"], payload)
            loaded = load_project(str(project_dir))["project"]
            self.assertEqual(loaded["box"], box)
            self.assertEqual(loaded["vina"]["exhaustiveness"], 12)
            self.assertEqual(loaded["vina"]["seed"], 7)
            self.assertEqual(loaded["docking_protocol"]["run_mode"], "dock")
            self.assertFalse(loaded["docking_protocol"]["autobox"])

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
            self.assertIn("scoring = vina", response["config_text"])
            self.assertIn("exhaustiveness = 8", response["config_text"])

    def test_vina_config_supports_vinardo_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project_with_imports(temp_dir)
            response = update_vina_params(
                str(project_dir),
                {
                    "scoring": "vinardo",
                    "exhaustiveness": 8,
                    "num_modes": 9,
                    "energy_range": 4,
                    "cpu": 0,
                    "seed": None,
                },
            )

            self.assertTrue(response["ok"])
            config = build_vina_config_text(str(project_dir))
            self.assertTrue(config["ok"])
            self.assertIn("scoring = vinardo", config["config_text"])

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

    def test_flexible_receptor_run_snapshots_flex_and_executes_with_flex_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            raw = project_dir / "raw" / "receptor.pdb"
            raw.write_text("ATOM      1  CA  ALA A  42       1.000   2.000   3.000\n", encoding="utf-8")
            flex_root = project_dir / "prepared" / "flexible_receptor" / "flex_001"
            flex_root.mkdir(parents=True)
            rigid = flex_root / "receptor_rigid.pdbqt"
            flex = flex_root / "receptor_flex.pdbqt"
            receptor_json = flex_root / "receptor.json"
            rigid.write_text("ATOM      1  CA  ALA A  42       1.000   2.000   3.000  1.00 20.00     0.000 C\n", encoding="utf-8")
            flex.write_text("BEGIN_RES A ALA 42\nATOM      1  CA  ALA A  42       1.000   2.000   3.000  1.00 20.00     0.000 C\nEND_RES A ALA 42\n", encoding="utf-8")
            receptor_json.write_text("{}\n", encoding="utf-8")

            project_json = project_dir / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["receptor"]["raw_file"] = "raw/receptor.pdb"
            payload["docking_protocol"] = {
                "schema_version": 1,
                "receptor_mode": "flexible",
                "flexible_receptor": {
                    "status": "ready",
                    "preparation_id": "flex_001",
                    "source_raw_file": "raw/receptor.pdb",
                    "source_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                    "selected_residues": [{"selector": "A:42", "residue_name": "ALA"}],
                    "rigid_file": rigid.relative_to(project_dir).as_posix(),
                    "flex_file": flex.relative_to(project_dir).as_posix(),
                    "receptor_json_file": receptor_json.relative_to(project_dir).as_posix(),
                    "sha256": {
                        "rigid_pdbqt": hashlib.sha256(rigid.read_bytes()).hexdigest(),
                        "flex_pdbqt": hashlib.sha256(flex.read_bytes()).hexdigest(),
                        "receptor_json": hashlib.sha256(receptor_json.read_bytes()).hexdigest(),
                    },
                },
            }
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(prepared["ok"], prepared)
            run_id = prepared["run_id"]
            flex_snapshot = project_dir / "runs" / run_id / "inputs" / "flex.pdbqt"
            protocol_snapshot = (
                project_dir
                / "runs"
                / run_id
                / "inputs"
                / "flexible_receptor_protocol.json"
            )
            self.assertTrue(flex_snapshot.is_file())
            self.assertTrue(protocol_snapshot.is_file())
            self.assertEqual(
                prepared["metadata"]["snapshots"][
                    "flexible_receptor_protocol"
                ]["sha256"],
                hashlib.sha256(protocol_snapshot.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                prepared["metadata"]["input_sha256"][
                    "flexible_receptor_protocol"
                ],
                hashlib.sha256(protocol_snapshot.read_bytes()).hexdigest(),
            )
            self.assertEqual(prepared["metadata"]["docking_protocol"]["mode"], "flexible")
            self.assertEqual(prepared["metadata"]["docking_protocol"]["protocol_id"], "flexible_single")
            self.assertEqual(
                prepared["metadata"]["command"][-2:],
                ["--flex", f"runs/{run_id}/inputs/flex.pdbqt"],
            )

            original_protocol_snapshot = protocol_snapshot.read_bytes()
            protocol_snapshot.write_bytes(
                original_protocol_snapshot + b"\n"
            )
            blocked = project_module._validate_execute_prerequisites(
                str(project_dir),
                run_id,
                prepared["metadata"],
            )
            self.assertFalse(blocked["ok"])
            self.assertEqual(
                blocked["error"]["code"],
                "RUN_SNAPSHOT_HASH_MISMATCH",
            )
            protocol_snapshot.write_bytes(original_protocol_snapshot)

            observed: list[list[str]] = []
            executed = self._execute_with_mock_adapter(
                project_dir,
                run_id,
                on_call=lambda command, _cwd: observed.append(command),
            )
            self.assertTrue(executed["ok"], executed)
            self.assertEqual(observed[0][-2:], ["--flex", f"runs/{run_id}/inputs/flex.pdbqt"])

    def test_run_attributes_macrocycle_preparation_only_when_ligand_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            ligand = project_dir / "prepared" / "ligand.pdbqt"
            prep_dir = project_dir / "preparation" / "ligand_001"
            prep_dir.mkdir()
            metadata_path = prep_dir / "metadata.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "prep_id": "ligand_001",
                        "status": "finished",
                        "method": "meeko_macrocycle",
                        "protocol": "meeko_macrocycle",
                        "options": {"macrocycle": {"mode": "auto"}},
                        "protocol_evidence": {"ok": True, "inspection": {"embedded_topology": True}},
                        "meeko_version": "0.7.1",
                        "output": {"sha256": hashlib.sha256(ligand.read_bytes()).hexdigest()},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            project_json = project_dir / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["preparation"]["ligand"].update(
                {
                    "prep_id": "ligand_001",
                    "status": "finished",
                    "method": "meeko_macrocycle",
                    "metadata_file": "preparation/ligand_001/metadata.json",
                }
            )
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with unittest.mock.patch("dockstart_core.project.vina_adapter.detect", return_value=self._vina_ok_result()):
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(prepared["ok"], prepared)
            self.assertTrue(prepared["metadata"]["ligand_preparation"]["matched"])
            self.assertEqual(prepared["metadata"]["ligand_preparation"]["protocol"], "meeko_macrocycle")
            self.assertEqual(
                prepared["metadata"]["ligand_preparation"]["integrity"],
                "legacy_partial",
            )
            self.assertFalse(
                prepared["metadata"]["ligand_preparation"]["formal_reviewed"]
            )
            self.assertEqual(
                prepared["metadata"]["ligand_preparation"]["protocol_mode"],
                "legacy",
            )
            snapshot = project_dir / prepared["metadata"]["ligand_preparation_snapshot"]
            self.assertTrue(snapshot.is_file())

    def test_run_freezes_only_fully_verified_formal_macrocycle_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            expected = self._install_formal_macrocycle_preparation(project_dir)

            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(prepared["ok"], prepared)
            attributed = prepared["metadata"]["ligand_preparation"]
            self.assertTrue(attributed["matched"])
            self.assertTrue(attributed["formal_reviewed"])
            self.assertEqual(attributed["integrity"], "formal_reviewed")
            summary = attributed["macrocycle_summary"]
            self.assertEqual(summary["selection_mode"], "candidate")
            self.assertEqual(summary["review_id"], "review_001")
            self.assertEqual(summary["candidate_id"], "candidate_001")
            self.assertEqual(
                summary["break_bonds"],
                [
                    {
                        "atom_numbers_one_based": [1, 2],
                        "atom_labels": ["C1", "C2"],
                        "display": "C1（原子 1）—C2（原子 2）",
                    }
                ],
            )
            self.assertEqual(summary["glue_pseudo_atom_count"], 2)
            self.assertEqual(
                summary["contract_sha256"],
                expected["contract_sha256"],
            )
            self.assertEqual(
                summary["evidence_sha256"],
                expected["evidence_sha256"],
            )
            self.assertEqual(summary["meeko_version"], "0.7.1")
            self.assertEqual(summary["rdkit_version"], "2026.03.3")

            snapshot_file = Path(
                prepared["metadata"]["ligand_preparation_snapshot"]
            )
            snapshot_path = project_dir / snapshot_file
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot, attributed)
            self.assertNotIn(str(project_dir.resolve()), snapshot_path.read_text(encoding="utf-8"))

    def test_formal_macrocycle_tamper_or_missing_evidence_is_not_attributed(self) -> None:
        for mutation in ("tampered", "missing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp_dir:
                project_dir = self._create_config_ready_project(temp_dir)
                installed = self._install_formal_macrocycle_preparation(
                    project_dir
                )
                evidence_path = Path(installed["evidence_file"])
                if mutation == "missing":
                    evidence_path.unlink()
                else:
                    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                    evidence["actual_bonds"] = [[1, 2]]
                    evidence_path.write_text(
                        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                with unittest.mock.patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=self._vina_ok_result(),
                ):
                    prepared = prepare_vina_run(str(project_dir))

                self.assertTrue(prepared["ok"], prepared)
                attributed = prepared["metadata"]["ligand_preparation"]
                self.assertFalse(attributed["matched"])
                self.assertEqual(attributed["integrity"], "rejected")
                self.assertIn("正式大环准备记录未通过完整性校验", attributed["reason"])
                self.assertNotIn("protocol", attributed)
                self.assertEqual(
                    prepared["metadata"]["ligand_preparation_snapshot"],
                    "",
                )
                if mutation == "tampered":
                    self.assertTrue(
                        any(
                            "断环键" in issue
                            for issue in attributed["integrity_issues"]
                        )
                    )
                else:
                    self.assertTrue(
                        any(
                            "worker 证据" in issue
                            for issue in attributed["integrity_issues"]
                        )
                    )

    def test_report_displays_formal_macrocycle_evidence_and_scientific_boundary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            installed = self._install_formal_macrocycle_preparation(project_dir)
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)

            run_id = prepared["run_id"]
            run_dir = project_dir / "runs" / run_id
            scores_file = run_dir / "scores.csv"
            scores_file.write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "1,-8.7,0.0,0.0\n"
                "2,-8.2,1.5,2.1\n",
                encoding="utf-8",
            )
            metadata_path = run_dir / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata.update(
                {
                    "status": "finished",
                    "started_at": "2026-07-29T00:00:00+00:00",
                    "finished_at": "2026-07-29T00:01:00+00:00",
                    "exit_code": 0,
                    "scores_file": scores_file.relative_to(project_dir).as_posix(),
                    "project_scores_file": "results/scores.csv",
                }
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["runs"][0].update(
                {
                    "status": "finished",
                    "scores_file": scores_file.relative_to(project_dir).as_posix(),
                }
            )
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            built = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(built["ok"], built)
            report = built["report_text"]
            self.assertIn("正式审查（完整证据）", report)
            self.assertIn("C1（原子 1）—C2（原子 2）", report)
            self.assertIn("| G* 胶合伪原子 | 2 |", report)
            self.assertIn(installed["contract_sha256"], report)
            self.assertIn(installed["evidence_sha256"], report)
            self.assertIn(installed["bond_topology_sha256"], report)
            self.assertIn(project_module.MACROCYCLE_HYDROGEN_POLICY, report)
            self.assertIn("不证明所选构象、质子化、电荷或结合模式正确", report)
            self.assertIn("不是原始闭环化学拓扑", report)
            self.assertNotIn(installed["candidate_absolute_path"], report)

    def test_report_marks_legacy_macrocycle_as_partial_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_config_ready_project(temp_dir)
            ligand = project_dir / "prepared" / "ligand.pdbqt"
            prep_dir = project_dir / "preparation" / "ligand_001"
            prep_dir.mkdir()
            metadata_file = prep_dir / "metadata.json"
            metadata_file.write_text(
                json.dumps(
                    {
                        "prep_id": "ligand_001",
                        "status": "finished",
                        "method": "meeko_macrocycle",
                        "protocol": "meeko_macrocycle",
                        "protocol_mode": "legacy",
                        "options": {"macrocycle": {"mode": "auto"}},
                        "protocol_evidence": {
                            "ok": True,
                            "mode": "legacy",
                            "inspection": {
                                "embedded_topology": True,
                                "glue_pseudo_atoms": [],
                            },
                        },
                        "meeko_version": "0.7.1",
                        "rdkit_version": "2026.03.3",
                        "output": {
                            "sha256": hashlib.sha256(
                                ligand.read_bytes()
                            ).hexdigest()
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            project_json = project_dir / "project.json"
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["preparation"]["ligand"].update(
                {
                    "prep_id": "ligand_001",
                    "status": "finished",
                    "method": "meeko_macrocycle",
                    "metadata_file": metadata_file.relative_to(
                        project_dir
                    ).as_posix(),
                }
            )
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with unittest.mock.patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=self._vina_ok_result(),
            ):
                prepared = prepare_vina_run(str(project_dir))
            self.assertTrue(prepared["ok"], prepared)

            run_id = prepared["run_id"]
            run_dir = project_dir / "runs" / run_id
            scores_file = run_dir / "scores.csv"
            scores_file.write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "1,-7.5,0.0,0.0\n",
                encoding="utf-8",
            )
            run_metadata_path = run_dir / "metadata.json"
            run_metadata = json.loads(
                run_metadata_path.read_text(encoding="utf-8")
            )
            run_metadata.update(
                {
                    "status": "finished",
                    "started_at": "2026-07-29T00:00:00+00:00",
                    "finished_at": "2026-07-29T00:01:00+00:00",
                    "exit_code": 0,
                    "scores_file": scores_file.relative_to(
                        project_dir
                    ).as_posix(),
                    "project_scores_file": "results/scores.csv",
                }
            )
            run_metadata_path.write_text(
                json.dumps(run_metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["runs"][0].update(
                {
                    "status": "finished",
                    "scores_file": scores_file.relative_to(
                        project_dir
                    ).as_posix(),
                }
            )
            project_json.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            built = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(built["ok"], built)
            report = built["report_text"]
            self.assertIn(
                "旧版兼容（部分证据，非正式审查）",
                report,
            )
            self.assertIn("旧版未冻结精确断环键", report)
            self.assertIn("不得视为正式大环审查", report)
            self.assertIn("不得仅凭 G* 伪原子反推原始断环键", report)

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

    def test_execute_normalizes_observed_windows_vina_nul_padding_and_preserves_raw_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            raw_output = (
                b"MODEL 1\r\n"
                b"REMARK VINA RESULT: -13.877 0.000 0.000\r\n"
                b"ROOT\r\nENDROOT\r\n"
                b"TORSDOF 6\r\n"
                + (b"\x00" * 47)
                + b"\r\nENDMDL\r\n"
            )
            normalized_output = raw_output.replace(b"\x00" * 47, b"")

            response = self._execute_with_mock_adapter(
                project_dir,
                run_id,
                output_bytes=raw_output,
            )

            self.assertTrue(response["ok"], response)
            run_dir = project_dir / "runs" / run_id
            output_path = run_dir / "out.pdbqt"
            raw_path = run_dir / "out.vina_raw.pdbqt"
            self.assertEqual(output_path.read_bytes(), normalized_output)
            self.assertEqual(raw_path.read_bytes(), raw_output)

            metadata = response["metadata"]
            normalization = metadata["output_normalization"]
            self.assertEqual(normalization["status"], "normalized")
            self.assertEqual(normalization["nul_bytes_detected"], 47)
            self.assertEqual(normalization["nul_bytes_removed"], 47)
            self.assertEqual(normalization["recognized_padding_blocks"], 1)
            self.assertEqual(normalization["recognized_padding_lengths"], [47])
            self.assertEqual(
                normalization["source_sha256"],
                hashlib.sha256(raw_output).hexdigest(),
            )
            self.assertEqual(
                normalization["normalized_sha256"],
                hashlib.sha256(normalized_output).hexdigest(),
            )
            self.assertEqual(
                metadata["artifacts"]["out_vina_raw"]["sha256"],
                hashlib.sha256(raw_output).hexdigest(),
            )
            self.assertEqual(
                metadata["artifacts"]["out"]["sha256"],
                hashlib.sha256(normalized_output).hexdigest(),
            )
            self.assertTrue(
                any("NUL" in warning for warning in metadata["warnings"])
            )

            file_status = get_run_files_status(str(project_dir), run_id)
            raw_status = next(
                item
                for item in file_status["files"]
                if item["key"] == "out_vina_raw"
            )
            self.assertEqual(raw_status["status"], "ok")

            scores_file = run_dir / "scores.csv"
            scores_file.write_text(
                "mode,affinity_kcal_mol,rmsd_lb,rmsd_ub\n"
                "1,-13.877,0.0,0.0\n",
                encoding="utf-8",
            )
            metadata_path = run_dir / "metadata.json"
            stored_metadata = json.loads(
                metadata_path.read_text(encoding="utf-8")
            )
            stored_metadata.update(
                {
                    "scores_file": scores_file.relative_to(
                        project_dir
                    ).as_posix(),
                    "project_scores_file": "results/scores.csv",
                }
            )
            metadata_path.write_text(
                json.dumps(stored_metadata, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            project_path = project_dir / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["runs"][0].update(
                {
                    "scores_file": scores_file.relative_to(
                        project_dir
                    ).as_posix(),
                }
            )
            project_path.write_text(
                json.dumps(project, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            report = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(report["ok"], report)
            self.assertIn("out.vina_raw.pdbqt", report["report_text"])
            self.assertIn(
                hashlib.sha256(raw_output).hexdigest(),
                report["report_text"],
            )
            self.assertIn(
                hashlib.sha256(normalized_output).hexdigest(),
                report["report_text"],
            )
            self.assertEqual(
                report["report_text"].count("Vina 输出标准化"),
                1,
            )
            self.assertEqual(
                report["report_text"].count("Vina 原始输出 SHA256"),
                1,
            )
            self.assertEqual(
                report["report_text"].count("标准 PDBQT SHA256"),
                1,
            )

    def test_execute_records_no_change_for_text_safe_vina_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir, run_id = self._create_prepared_run_with_command(temp_dir)
            output = b"MODEL 1\nROOT\nENDROOT\nTORSDOF 0\nENDMDL\n"

            response = self._execute_with_mock_adapter(
                project_dir,
                run_id,
                output_bytes=output,
            )

            self.assertTrue(response["ok"], response)
            normalization = response["metadata"]["output_normalization"]
            self.assertEqual(normalization["status"], "not_required")
            self.assertFalse(normalization["changed"])
            self.assertEqual(normalization["source_sha256"], normalization["normalized_sha256"])
            self.assertEqual(normalization["source_sha256"], hashlib.sha256(output).hexdigest())
            self.assertNotIn("out_vina_raw", response["metadata"]["artifacts"])
            self.assertFalse(
                (
                    project_dir
                    / "runs"
                    / run_id
                    / "out.vina_raw.pdbqt"
                ).exists()
            )

    def test_flexible_output_normalizer_only_accepts_torsdof_to_valid_begin_res_padding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "out.pdbqt"
            raw_output = (
                b"MODEL 1\r\n"
                b"REMARK VINA RESULT: -8.1 0.0 0.0\r\n"
                b"ROOT\r\nENDROOT\r\nTORSDOF 1\r\n"
                + (b"\x00" * 20)
                + b"\r\nBEGIN_RES GLN A 192\r\n"
                b"ROOT\r\nENDROOT\r\nTORSDOF 0\r\n"
                b"END_RES GLN A 192\r\nENDMDL\r\n"
            )
            expected = raw_output.replace(b"\x00" * 20, b"")
            output_path.write_bytes(raw_output)

            normalized = project_module._normalize_vina_pdbqt_output(
                output_path,
                "runs/run_001/out.pdbqt",
                allow_flexible_residue_boundary=True,
            )

            self.assertTrue(normalized["ok"], normalized)
            self.assertEqual(output_path.read_bytes(), expected)
            self.assertEqual(
                (Path(temp_dir) / "out.vina_raw.pdbqt").read_bytes(),
                raw_output,
            )
            record = normalized["record"]
            self.assertEqual(
                record["method"],
                "torsdof_flexible_residue_or_endmdl_nul_padding_v3",
            )
            self.assertEqual(record["nul_bytes_removed"], 20)
            self.assertEqual(record["recognized_padding_blocks"], 1)
            self.assertEqual(
                record["normalized_sha256"],
                hashlib.sha256(expected).hexdigest(),
            )

        invalid_suffixes = (
            b"\r\nBEGIN_RES GLN $ 192\r\n",
            b"\r\nBEGIN_RES GLN A 192 extra\r\n",
            b"\r\nATOM      1  C   GLN A 192\r\n",
        )
        for suffix in invalid_suffixes:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "out.pdbqt"
                raw_output = (
                    b"MODEL 1\nROOT\nENDROOT\nTORSDOF 1\n"
                    + (b"\x00" * 7)
                    + suffix
                    + b"ENDMDL\n"
                )
                output_path.write_bytes(raw_output)

                normalized = project_module._normalize_vina_pdbqt_output(
                    output_path,
                    "runs/run_001/out.pdbqt",
                    allow_flexible_residue_boundary=True,
                )

                self.assertFalse(normalized["ok"])
                self.assertEqual(
                    normalized["error"]["code"],
                    "VINA_OUTPUT_BINARY_CONTROL_CHARACTER",
                )
                self.assertFalse(output_path.exists())
                self.assertEqual(
                    (Path(temp_dir) / "out.vina_raw.pdbqt").read_bytes(),
                    raw_output,
                )

    def test_execute_rejects_unrecognized_control_or_invalid_utf8_output(
        self,
    ) -> None:
        cases = (
            (
                "control",
                b"MODEL 1\nREMARK bad\x01control\nTORSDOF 0\nENDMDL\n",
                "VINA_OUTPUT_BINARY_CONTROL_CHARACTER",
            ),
            (
                "misplaced_nul",
                b"MODEL 1\nREMARK bad\x00padding\nTORSDOF 0\nENDMDL\n",
                "VINA_OUTPUT_BINARY_CONTROL_CHARACTER",
            ),
            (
                "invalid_utf8",
                b"MODEL 1\nREMARK bad\xfftext\nTORSDOF 0\nENDMDL\n",
                "VINA_OUTPUT_TEXT_ENCODING_INVALID",
            ),
        )
        for label, output, expected_code in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                project_dir, run_id = self._create_prepared_run_with_command(
                    temp_dir
                )

                response = self._execute_with_mock_adapter(
                    project_dir,
                    run_id,
                    output_bytes=output,
                )

                self.assertFalse(response["ok"])
                self.assertEqual(response["metadata"]["status"], "failed")
                self.assertEqual(response["error"]["code"], expected_code)
                run_dir = project_dir / "runs" / run_id
                self.assertFalse((run_dir / "out.pdbqt").exists())
                self.assertEqual(
                    (run_dir / "out.vina_raw.pdbqt").read_bytes(),
                    output,
                )
                normalization = response["metadata"]["output_normalization"]
                self.assertEqual(normalization["status"], "failed")
                self.assertEqual(normalization["normalized_file"], "")
                self.assertEqual(normalization["normalized_sha256"], "")
                self.assertEqual(
                    response["metadata"]["artifacts"]["out_vina_raw"][
                        "sha256"
                    ],
                    hashlib.sha256(output).hexdigest(),
                )

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
            self.assertEqual(metadata["vina_snapshot"]["scoring"], "vina")
            self.assertIn("scoring = vina", snapshot_config_text)
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

    def test_adapter_drains_output_in_bounded_lines_before_shutdown_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observed_chunks: list[str] = []

            def slow_output(stream_name: str, chunk: str) -> None:
                if stream_name == "stdout":
                    observed_chunks.append(chunk)
                    time.sleep(0.01)

            script = "for i in range(40): print(f'vina-line-{i:02d}-' + 'x' * 64, flush=True)"
            result = vina_adapter.run_managed(
                [sys.executable, "-c", script],
                root,
                root / "stdout.txt",
                root / "stderr.txt",
                root / "log.txt",
                on_output=slow_output,
            )

            stdout = (root / "stdout.txt").read_text(encoding="utf-8")
            self.assertFalse(result.error)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(stdout, (root / "log.txt").read_text(encoding="utf-8"))
            self.assertEqual("".join(observed_chunks), stdout)
            self.assertLessEqual(len(observed_chunks), 40)

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

    def test_multiple_ligand_scores_use_immutable_verified_snapshots(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("multi_integrity", temp_dir)
            self.assertTrue(created["ok"], created)
            project_dir = Path(created["project_dir"])
            run_id = "run_001"
            run_dir = project_dir / "runs" / run_id
            run_dir.mkdir()
            scores_path = run_dir / "scores.csv"
            joint_path = run_dir / "joint_poses.json"
            scores_text = (
                "mode,joint_affinity_kcal_mol,rmsd_lb,rmsd_ub,"
                "pose_available,score_scope\n"
                "1,-10.0,0.0,0.0,true,joint_two_ligand_pose\n"
                "2,-9.2,1.1,1.5,false,joint_two_ligand_pose\n"
            )
            scores_path.write_text(scores_text, encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "protocol_id": "simultaneous_multi_ligand",
                "run_id": run_id,
                "member_count": 2,
                "scores": [
                    {
                        "mode": 1,
                        "joint_affinity_kcal_mol": -10.0,
                        "rmsd_lb": 0.0,
                        "rmsd_ub": 0.0,
                        "pose_available": True,
                    },
                    {
                        "mode": 2,
                        "joint_affinity_kcal_mol": -9.2,
                        "rmsd_lb": 1.1,
                        "rmsd_ub": 1.5,
                        "pose_available": False,
                    },
                ],
            }
            joint_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            def attestation(path: Path, relative: str) -> dict[str, object]:
                return {
                    "relative_path": relative,
                    "exists": True,
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            scores_relative = f"runs/{run_id}/scores.csv"
            joint_relative = f"runs/{run_id}/joint_poses.json"
            metadata_path = run_dir / "metadata.json"
            metadata = {
                "run_id": run_id,
                "status": "finished",
                "stage": "finished",
                "protocol_id": "simultaneous_multi_ligand",
                "scores_file": scores_relative,
                "joint_poses_file": joint_relative,
                "best_affinity": -10.0,
                "artifacts": {
                    "scores": attestation(scores_path, scores_relative),
                    "joint_poses": attestation(
                        joint_path,
                        joint_relative,
                    ),
                },
            }
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            loaded = project_module.load_scores_csv(
                str(project_dir),
                run_id,
            )
            self.assertTrue(loaded["ok"], loaded)
            self.assertEqual(
                [row["joint_affinity_kcal_mol"] for row in loaded["scores"]],
                [-10.0, -9.2],
            )

            replacement_manifest = json.loads(json.dumps(manifest))
            replacement_manifest["scores"][0]["joint_affinity_kcal_mol"] = -7.5
            original_snapshot_reader = (
                project_module._read_multiple_ligand_result_artifact_snapshot
            )

            def replace_path_after_snapshot(*args, **kwargs):
                snapshot, snapshot_error = original_snapshot_reader(
                    *args,
                    **kwargs,
                )
                if snapshot_error is None:
                    filename = kwargs["filename"]
                    if filename == "scores.csv":
                        scores_path.write_text(
                            scores_text.replace("-10.0", "-7.5", 1),
                            encoding="utf-8",
                        )
                    elif filename == "joint_poses.json":
                        joint_path.write_text(
                            json.dumps(
                                replacement_manifest,
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                return snapshot, snapshot_error

            with unittest.mock.patch.object(
                project_module,
                "_read_multiple_ligand_result_artifact_snapshot",
                side_effect=replace_path_after_snapshot,
            ):
                raced = project_module.load_scores_csv(
                    str(project_dir),
                    run_id,
                )
            self.assertTrue(raced["ok"], raced)
            self.assertEqual(
                [row["joint_affinity_kcal_mol"] for row in raced["scores"]],
                [-10.0, -9.2],
            )
            self.assertIn("-7.5", scores_path.read_text(encoding="utf-8"))

            scores_path.write_text(scores_text, encoding="utf-8")
            joint_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            scores_path.write_text(
                scores_text.replace("-10.0", "-10.1", 1),
                encoding="utf-8",
            )
            tampered_scores = project_module.load_scores_csv(
                str(project_dir),
                run_id,
            )
            self.assertFalse(tampered_scores["ok"])
            self.assertEqual(
                tampered_scores["error"]["code"],
                "MULTIPLE_LIGAND_SCORES_INTEGRITY_ERROR",
            )

            scores_path.write_text(scores_text, encoding="utf-8")
            manifest["scores"][1]["pose_available"] = True
            joint_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            metadata["artifacts"]["joint_poses"] = attestation(
                joint_path,
                joint_relative,
            )
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            mismatched_manifest = project_module.load_scores_csv(
                str(project_dir),
                run_id,
            )
            self.assertFalse(mismatched_manifest["ok"])
            self.assertEqual(
                mismatched_manifest["error"]["code"],
                "MULTIPLE_LIGAND_SCORE_MANIFEST_MISMATCH",
            )

    def test_recovery_restores_multiple_ligand_summary_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("multi_summary", temp_dir)
            self.assertTrue(created["ok"], created)
            project_dir = Path(created["project_dir"])
            run_id = "run_001"
            run_dir = project_dir / "runs" / run_id
            run_dir.mkdir()
            members = [
                {
                    "member_index": 1,
                    "member_id": "ligand_001",
                    "display_name": "first.pdbqt",
                },
                {
                    "member_index": 2,
                    "member_id": "ligand_002",
                    "display_name": "second.pdbqt",
                },
            ]
            (run_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": "prepared",
                        "stage": "prepared",
                        "protocol_id": "simultaneous_multi_ligand",
                        "protocol_name": "多配体共同对接（实验性）",
                        "member_count": 2,
                        "members": members,
                        "run_mode": "dock",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            recovered = project_module.recover_project_state(
                str(project_dir),
            )

            self.assertTrue(recovered["ok"], recovered)
            summary = next(
                item
                for item in recovered["project"]["runs"]
                if item["run_id"] == run_id
            )
            self.assertEqual(
                summary["protocol_id"],
                "simultaneous_multi_ligand",
            )
            self.assertEqual(
                summary["protocol_name"],
                "多配体共同对接（实验性）",
            )
            self.assertEqual(summary["member_count"], 2)
            self.assertEqual(summary["members"], members)

    def test_recovery_preserves_multiple_ligand_artifact_names_and_vina_proof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("multi_recovery", temp_dir)
            self.assertTrue(created["ok"], created)
            project_dir = Path(created["project_dir"])
            run_id = "run_001"
            run_dir = project_dir / "runs" / run_id
            run_dir.mkdir()
            output_path = run_dir / "out.pdbqt"
            output_path.write_text(
                "MODEL 1\nENDMDL\n",
                encoding="utf-8",
            )
            output_relative = f"runs/{run_id}/out.pdbqt"
            executed_bytes = b"vina-at-execution"
            executed_sha256 = hashlib.sha256(executed_bytes).hexdigest()
            vina_path = project_dir / "recorded-vina.exe"
            vina_path.write_bytes(b"vina-observed-afterwards")
            metadata_path = run_dir / "metadata.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": "finished",
                        "stage": "finished",
                        "protocol_id": "simultaneous_multi_ligand",
                        "run_mode": "dock",
                        "output_file": output_relative,
                        "execution_vina": {
                            "path": str(vina_path),
                            "version": "1.2.7",
                            "source": "configured",
                            "size_bytes": len(executed_bytes),
                            "sha256": executed_sha256,
                        },
                        "artifacts": {
                            "output": {
                                "relative_path": output_relative,
                                "exists": True,
                                "size_bytes": output_path.stat().st_size,
                                "sha256": hashlib.sha256(
                                    output_path.read_bytes(),
                                ).hexdigest(),
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            recovered = project_module.recover_project_state(
                str(project_dir),
            )
            final = json.loads(metadata_path.read_text(encoding="utf-8"))
            artifacts = final["artifacts"]

            self.assertTrue(recovered["ok"], recovered)
            self.assertIn("output", artifacts)
            self.assertNotIn("out", artifacts)
            vina_artifact = artifacts["vina_binary_executed"]
            self.assertEqual(vina_artifact["sha256"], executed_sha256)
            self.assertEqual(
                vina_artifact["size_bytes"],
                len(executed_bytes),
            )
            self.assertEqual(
                vina_artifact["verification_status"],
                "verified",
            )
            self.assertTrue(vina_artifact["recorded_at_execution"])
            self.assertNotIn("backfilled_unverified", vina_artifact)

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
