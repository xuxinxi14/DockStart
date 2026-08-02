from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import meeko_adapter  # noqa: E402
from dockstart_core.preparation import (  # noqa: E402
    RECEPTOR_PREPARATION_OUTPUT,
    build_receptor_preparation_command_or_script,
    load_receptor_preparation_log,
    prepare_receptor_pdbqt,
    validate_receptor_preparation_input,
)
from dockstart_core.project import create_project  # noqa: E402


def _tool_status(
    meeko: str = "ok",
    receptor_capability: str = "ok",
    cli: bool = True,
    cif_input_available: bool = True,
) -> dict:
    cli_candidates = [sys.executable] if cli else []
    return {
        "ok": True,
        "project_dir": "",
        "tools": {
            "python": {
                "key": "python",
                "name": "Python",
                "status": "ok",
                "version": "Python 3.11.0",
                "path": sys.executable,
                "message": "mock python",
                "raw_error": "",
                "source": "current_environment",
                "bundled_path": "",
                "is_bundled": False,
            },
            "rdkit": {
                "key": "rdkit",
                "name": "RDKit",
                "status": "missing",
                "version": "",
                "path": sys.executable,
                "python_path": sys.executable,
                "python_source": "current_environment",
                "source": "current_environment",
                "capabilities": {},
                "message": "not needed for receptor",
                "raw_error": "",
            },
            "meeko": {
                "key": "meeko",
                "name": "Meeko",
                "status": meeko,
                "version": "mock-meeko",
                "path": sys.executable,
                "python_path": sys.executable,
                "python_source": "current_environment",
                "source": "current_environment",
                "capabilities": {
                    "receptor_preparation": {
                        "status": receptor_capability,
                        "message": "mock receptor capability",
                        "cli_candidates_found": cli_candidates,
                        "cif_input_available": cif_input_available,
                        "cif_parser": "gemmi" if cif_input_available else "",
                    }
                },
                "message": "mock",
                "raw_error": "",
            },
        },
    }


def _pdb_atom(
    serial: int,
    atom_name: str,
    residue_name: str,
    chain: str,
    residue_number: int,
    *,
    record_type: str = "ATOM",
    altloc: str = "",
    x: float = 1.0,
) -> str:
    return (
        f"{record_type:<6}{serial:5d} {atom_name:^4}{altloc:1}{residue_name:>3} {chain:1}"
        f"{residue_number:4d}{' ':1}   {x:8.3f}{2.0:8.3f}{3.0:8.3f}"
        f"  1.00 20.00          {'C':>2}\n"
    )


def _receptor_with_altloc() -> str:
    return "".join(
        (
            _pdb_atom(1, "N", "ALA", "A", 42),
            _pdb_atom(2, "CB", "THR", "A", 43, altloc="A"),
            _pdb_atom(3, "CB", "THR", "A", 43, altloc="B", x=1.2),
            _pdb_atom(
                4,
                "C1",
                "LIG",
                "B",
                301,
                record_type="HETATM",
                x=4.0,
            ),
        )
    )


def _receptor_with_interrupted_residue_order() -> str:
    return "".join(
        (
            "REMARK displaced atom record\n",
            _pdb_atom(4, "CB", "SER", "A", 2, x=4.0),
            _pdb_atom(1, "N", "ALA", "A", 1, x=1.0),
            _pdb_atom(2, "CA", "ALA", "A", 1, x=2.0),
            _pdb_atom(3, "N", "SER", "A", 2, x=3.0),
            _pdb_atom(5, "OG", "SER", "A", 2, x=5.0),
            "TER\n",
        )
    )


def _reviewed_receptor_options() -> dict[str, object]:
    return {
        "protocol": "meeko_receptor_controls",
        "receptor_controls": {
            "schema_version": 1,
            "allow_bad_res": False,
            "alternate_locations": {"A:43": "B"},
            "template_assignments": {},
            "deleted_residues": [
                {
                    "selector": "B:301",
                    "expected_component_id": "LIG",
                    "reason": "co_crystal_ligand",
                }
            ],
        },
    }


class ReceptorPreparationTests(unittest.TestCase):
    def _create_project(self, temp_dir: str) -> Path:
        created = create_project("receptor_prep", temp_dir)
        self.assertTrue(created["ok"], created)
        return Path(created["project_dir"])

    def _set_receptor_raw(self, project_dir: Path, relative_path: str, content: str = "ATOM\n") -> Path:
        raw_path = project_dir / relative_path
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(content, encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["receptor"]["raw_file"] = relative_path
        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return raw_path

    def test_build_receptor_command_uses_read_pdb_without_prody_for_pdb_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()):
                result = build_receptor_preparation_command_or_script(str(project_dir), overwrite=False)

        self.assertTrue(result["ok"], result)
        self.assertIn("--read_pdb", result["command"])
        self.assertNotIn("-i", result["command"])

    def test_interrupted_residue_records_use_audited_order_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            source = self._set_receptor_raw(
                project_dir,
                "raw/receptor.pdb",
                content=_receptor_with_interrupted_residue_order(),
            )
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(),
            ):
                result = build_receptor_preparation_command_or_script(
                    str(project_dir),
                    overwrite=False,
                )

            self.assertTrue(result["ok"], result)
            self.assertIn("prepare_receptor_pdb_order_meeko.py", result["script_file"])
            self.assertIn("receptor_ordered.pdb", result["intermediate_input_file"])
            self.assertEqual(result["command"][4], str(source))
            self.assertTrue(result["pdb_coordinate_order_repair"]["required"])
            self.assertEqual(
                result["pdb_coordinate_order_repair"]["interrupted_residues"],
                ["A:2"],
            )
            self.assertFalse(result["pdb_coordinate_order_repair"]["deletes_atoms"])
            self.assertFalse(result["pdb_coordinate_order_repair"]["renumbers_atoms"])

    def test_order_bridge_preserves_atom_records_and_runs_meeko(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdb"
            intermediate = root / "ordered.pdb"
            output_stem = root / "candidate_receptor"
            source.write_text(
                _receptor_with_interrupted_residue_order(),
                encoding="utf-8",
            )
            script = root / "bridge.py"
            script.write_text(
                meeko_adapter.receptor_pdb_order_bridge_script_text(),
                encoding="utf-8",
            )
            fake_module = root / "fake" / "meeko" / "cli"
            fake_module.mkdir(parents=True)
            (fake_module.parent / "__init__.py").write_text("", encoding="utf-8")
            (fake_module / "__init__.py").write_text("", encoding="utf-8")
            (fake_module / "mk_prepare_receptor.py").write_text(
                """
import sys
from pathlib import Path

def main():
    source = Path(sys.argv[sys.argv.index('--read_pdb') + 1])
    output_stem = Path(sys.argv[sys.argv.index('-o') + 1])
    output_stem.with_suffix('.pdbqt').write_bytes(source.read_bytes())
    return 0
""".lstrip(),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root / "fake")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(source),
                    str(intermediate),
                    str(output_stem),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            source_atoms = sorted(
                line
                for line in source.read_bytes().splitlines(keepends=True)
                if line.startswith((b"ATOM  ", b"HETATM"))
            )
            ordered_lines = intermediate.read_bytes().splitlines(keepends=True)
            ordered_atoms = [
                line
                for line in ordered_lines
                if line.startswith((b"ATOM  ", b"HETATM"))
            ]
            self.assertEqual(sorted(ordered_atoms), source_atoms)
            self.assertEqual(
                [int(line[6:11]) for line in ordered_atoms],
                [1, 2, 3, 4, 5],
            )
            self.assertEqual(
                output_stem.with_suffix(".pdbqt").read_bytes(),
                intermediate.read_bytes(),
            )

    def test_build_receptor_command_uses_reviewed_altloc_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(
                project_dir,
                "raw/receptor.pdb",
                content=_receptor_with_altloc(),
            )
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(),
            ):
                result = build_receptor_preparation_command_or_script(
                    str(project_dir),
                    options=_reviewed_receptor_options(),
                )

        self.assertTrue(result["ok"], result)
        self.assertNotIn("--allow_bad_res", result["command"])
        self.assertNotIn("--default_altloc", result["command"])
        self.assertEqual(
            result["command"][result["command"].index("--wanted_altloc") + 1],
            "A:43=B",
        )
        self.assertEqual(
            result["command"][result["command"].index("--delete_residues") + 1],
            "B:301",
        )
        self.assertEqual(result["protocol"], "meeko_receptor_controls")
        self.assertEqual(result["protocol_mode"], "reviewed")
        self.assertFalse(result["receptor_controls"]["allow_bad_res"])
        self.assertEqual(
            result["receptor_controls"]["deleted_residues"],
            [
                {
                    "selector": "B:301",
                    "expected_component_id": "LIG",
                    "reason": "co_crystal_ligand",
                }
            ],
        )
        self.assertRegex(result["receptor_controls_sha256"], r"^[0-9a-f]{64}$")

    def test_receptor_controls_reject_free_argv_bad_res_and_cif(self) -> None:
        invalid_options = (
            {
                **_reviewed_receptor_options(),
                "argv": ["--default_altloc", "A"],
            },
            {
                "protocol": "meeko_receptor_controls",
                "receptor_controls": {
                    **dict(_reviewed_receptor_options()["receptor_controls"]),
                    "allow_bad_res": True,
                },
            },
        )
        expected_codes = (
            "RECEPTOR_PREPARATION_OPTIONS_UNKNOWN",
            "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
        )
        for options, expected_code in zip(
            invalid_options,
            expected_codes,
            strict=True,
        ):
            with self.subTest(expected_code=expected_code):
                result = prepare_receptor_pdbqt("unused", options=options)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], expected_code)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(
                project_dir,
                "raw/receptor.cif",
                content="data_test\n",
            )
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(),
            ):
                result = build_receptor_preparation_command_or_script(
                    str(project_dir),
                    options=_reviewed_receptor_options(),
                )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_CONTROLS_PDB_REQUIRED")

    def test_build_receptor_command_uses_gemmi_helper_without_prody_for_cif_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.cif", content="data_test\n")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()):
                result = build_receptor_preparation_command_or_script(str(project_dir), overwrite=False)
            helper = Path(result["command"][3])
            helper_text = helper.read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        self.assertNotIn("-i", result["command"])
        self.assertNotIn("--read_with_prody", result["command"])
        self.assertIn("prepare_receptor_cif_gemmi_meeko.py", result["script_file"])
        self.assertIn("receptor_from_cif.pdb", result["intermediate_input_file"])
        self.assertIn("import gemmi", helper_text)
        self.assertIn('"--read_pdb"', helper_text)

    def test_validate_receptor_preparation_input_missing_raw_record_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_RAW_FILE_NOT_RECORDED")

    def test_validate_receptor_preparation_input_missing_raw_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            project_json = project_dir / "project.json"
            data = json.loads(project_json.read_text(encoding="utf-8"))
            data["receptor"]["raw_file"] = "raw/missing.pdb"
            project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_RAW_FILE_NOT_READY")

    def test_validate_receptor_preparation_input_unsupported_format_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.txt")
            result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_RAW_FORMAT_UNSUPPORTED")

    def test_validate_receptor_preparation_input_missing_meeko_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status(meeko="missing")):
                result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_PREPARATION_TOOLS_NOT_READY")

    def test_validate_receptor_preparation_input_unknown_capability_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(receptor_capability="unknown", cli=False),
            ):
                result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertIn("mk_prepare_receptor", result["error"]["raw_error"])

    def test_validate_cif_requires_gemmi_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.cif", content="data_test\n")
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(cif_input_available=False),
            ):
                result = validate_receptor_preparation_input(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_PREPARATION_TOOLS_NOT_READY")
        self.assertIn("Gemmi CIF parser", result["error"]["raw_error"])

    def test_bundled_runtime_prepares_cif_through_gemmi_without_prody(self) -> None:
        runtime = REPO_ROOT / "resources" / "python" / "python.exe"
        source_pdb = REPO_ROOT / "resources" / "examples" / "assisted_raw" / "raw" / "receptor.pdb"
        if not runtime.is_file():
            self.skipTest("本地未装配 Assisted Python runtime。")
        self.assertTrue(source_pdb.is_file(), source_pdb)

        detected_meeko = meeko_adapter.detect_meeko_capabilities(str(runtime), "bundled")
        receptor_capability = detected_meeko.get("capabilities", {}).get("receptor_preparation", {})
        self.assertTrue(receptor_capability.get("cif_input_available"), receptor_capability)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            cif_path = project_dir / "raw" / "receptor.cif"
            converted = subprocess.run(
                [
                    str(runtime),
                    "-I",
                    "-B",
                    "-c",
                    (
                        "import gemmi,sys; "
                        "structure=gemmi.read_structure(sys.argv[1]); "
                        "structure.make_mmcif_document().write_file(sys.argv[2])"
                    ),
                    str(source_pdb),
                    str(cif_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(converted.returncode, 0, converted.stderr)
            self._set_receptor_raw(
                project_dir,
                "raw/receptor.cif",
                content=cif_path.read_text(encoding="utf-8"),
            )

            tool_status = _tool_status()
            tool_status["tools"]["python"].update(
                {"path": str(runtime), "source": "bundled", "is_bundled": True},
            )
            tool_status["tools"]["meeko"] = detected_meeko
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=tool_status,
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=False)

            metadata = json.loads(
                (project_dir / str(result.get("metadata_file") or "missing")).read_text(encoding="utf-8"),
            )
            stdout = (project_dir / str(result.get("stdout_file") or "missing")).read_text(
                encoding="utf-8",
                errors="replace",
            )
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertTrue((project_dir / RECEPTOR_PREPARATION_OUTPUT).is_file())
            self.assertTrue((project_dir / metadata["intermediate_input_file"]).is_file())
            self.assertIn("Gemmi", stdout)
            self.assertEqual(metadata["method"], "meeko")
            self.assertIn("prepare_receptor_cif_gemmi_meeko.py", metadata["script_file"])
            self.assertNotIn("--read_with_prody", metadata["command"])
            self.assertEqual(project["preparation"]["receptor"]["status"], "finished")

    def test_existing_receptor_pdbqt_without_overwrite_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            output = project_dir / RECEPTOR_PREPARATION_OUTPUT
            output.write_text("old receptor\n", encoding="utf-8")
            result = validate_receptor_preparation_input(str(project_dir), overwrite=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_PREPARED_FILE_EXISTS")

    def test_existing_receptor_pdbqt_with_overwrite_allows_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            output = project_dir / RECEPTOR_PREPARATION_OUTPUT
            output.write_text("old receptor\n", encoding="utf-8")
            with patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()):
                result = validate_receptor_preparation_input(str(project_dir), overwrite=True)

        self.assertTrue(result["ok"], result)

    def test_prepare_receptor_pdbqt_mock_success_updates_project_and_logs(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            output_stem = Path(command[command.index("-o") + 1])
            output_path = output_stem.with_suffix(".pdbqt")
            output_path.write_text("REMARK mock receptor pdbqt\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="mock receptor stdout", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            project_json = project_dir / "project.json"
            before = json.loads(project_json.read_text(encoding="utf-8"))
            before["ligand"]["file"] = "prepared/ligand.pdbqt"
            project_json.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=False)
            updated = json.loads(project_json.read_text(encoding="utf-8"))
            log_result = load_receptor_preparation_log(str(project_dir))
            output_exists = (project_dir / RECEPTOR_PREPARATION_OUTPUT).is_file()

        self.assertTrue(result["ok"], result)
        self.assertEqual(updated["receptor"]["file"], "prepared/receptor.pdbqt")
        self.assertEqual(updated["ligand"]["file"], "prepared/ligand.pdbqt")
        self.assertEqual(updated["preparation"]["receptor"]["status"], "finished")
        self.assertEqual(updated["preparation"]["receptor"]["method"], "meeko")
        self.assertTrue(output_exists)
        self.assertIn("mock receptor stdout", log_result["stdout"])
        self.assertIn('"status": "finished"', log_result["log"])

    def test_reviewed_receptor_controls_are_frozen_in_metadata(self) -> None:
        captured_command: list[str] = []

        def fake_run(
            command: list[str],
            cwd: str | Path,
            timeout: int = 300,
        ) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            captured_command.extend(command)
            output_stem = Path(command[command.index("-o") + 1])
            output_stem.with_suffix(".pdbqt").write_text(
                "REMARK reviewed receptor pdbqt\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="reviewed", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(
                project_dir,
                "raw/receptor.pdb",
                content=_receptor_with_altloc(),
            )
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch(
                    "adapters.meeko_adapter.run_preparation_command",
                    side_effect=fake_run,
                ),
            ):
                result = prepare_receptor_pdbqt(
                    str(project_dir),
                    options=_reviewed_receptor_options(),
                )
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            project = json.loads(
                (project_dir / "project.json").read_text(encoding="utf-8")
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            captured_command[captured_command.index("--wanted_altloc") + 1],
            "A:43=B",
        )
        self.assertEqual(
            captured_command[captured_command.index("--delete_residues") + 1],
            "B:301",
        )
        self.assertEqual(metadata["method"], "meeko_receptor_controls")
        self.assertEqual(metadata["protocol"], "meeko_receptor_controls")
        self.assertEqual(
            metadata["receptor_controls"]["alternate_locations"],
            {"A:43": "B"},
        )
        self.assertEqual(
            metadata["receptor_controls"]["deleted_residues"],
            [
                {
                    "selector": "B:301",
                    "expected_component_id": "LIG",
                    "reason": "co_crystal_ligand",
                }
            ],
        )
        self.assertRegex(metadata["receptor_controls_sha256"], r"^[0-9a-f]{64}$")
        expected_controls_sha256 = hashlib.sha256(
            json.dumps(
                metadata["receptor_controls"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            metadata["receptor_controls_sha256"],
            expected_controls_sha256,
        )
        self.assertEqual(
            metadata["options"]["receptor_controls"],
            metadata["receptor_controls"],
        )
        self.assertEqual(
            project["preparation"]["receptor"]["method"],
            "meeko_receptor_controls",
        )

    def test_prepare_receptor_pdbqt_mock_failure_writes_structured_error(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="mock receptor scientific diagnostic",
                stderr="mock receptor runtime warning",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=False)
            updated = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual(updated["preparation"]["receptor"]["status"], "failed")
        self.assertEqual(updated["preparation"]["receptor"]["error"]["code"], "RECEPTOR_PREPARATION_FAILED")
        self.assertIn(
            "[stdout]\nmock receptor scientific diagnostic",
            updated["preparation"]["receptor"]["error"]["raw_error"],
        )
        self.assertIn(
            "[stderr]\nmock receptor runtime warning",
            updated["preparation"]["receptor"]["error"]["raw_error"],
        )
        self.assertIsNotNone(updated["preparation"]["receptor"]["finished_at"])

    def test_strict_template_failure_requires_reviewed_bad_residue_confirmation(self) -> None:
        diagnostic = (
            "No template matched for residue_key='A:226'\n"
            "No template matched for residue_key='A:315'\n"
            "- Template matching failed for: ['A:226', 'A:315']\n"
        )

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            return subprocess.CompletedProcess(command, 1, stdout="", stderr=diagnostic)

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "RECEPTOR_BAD_RESIDUES_REVIEW_REQUIRED")
        self.assertEqual(result["error"]["bad_residues"], ["A:226", "A:315"])

    def test_reviewed_bad_residue_retry_publishes_only_when_detected_list_matches(self) -> None:
        diagnostic = (
            "No template matched for residue_key='A:226'\n"
            "No template matched for residue_key='A:315'\n"
            "- Template matching failed for: ['A:226', 'A:315'] Ignored due to allow_bad_res.\n"
        )
        captured_command: list[str] = []

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            captured_command.extend(command)
            output_stem = Path(command[command.index("-o") + 1])
            output_stem.with_suffix(".pdbqt").write_text(
                "REMARK reviewed incomplete receptor\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, stdout="written", stderr=diagnostic)

        options = {
            "protocol": "meeko_allow_bad_res_reviewed",
            "acknowledged_bad_residues": ["A:226", "A:315"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            self._set_receptor_raw(project_dir, "raw/receptor.pdb")
            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir), options=options)
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )

        self.assertTrue(result["ok"], result)
        self.assertIn("--allow_bad_res", captured_command)
        self.assertEqual(metadata["method"], "meeko_allow_bad_res_reviewed")
        self.assertTrue(metadata["bad_residue_review"]["lists_match"])
        self.assertEqual(
            metadata["bad_residue_review"]["detected_bad_residues"],
            ["A:226", "A:315"],
        )

    def test_changed_raw_content_rejects_candidate_and_preserves_previous_output(self) -> None:
        raw_path: Path | None = None

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            output_stem = Path(command[command.index("-o") + 1])
            output_stem.with_suffix(".pdbqt").write_text("REMARK stale candidate\n", encoding="utf-8")
            assert raw_path is not None
            raw_path.write_text("ATOM changed after claim\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="mock receptor stdout", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_path = self._set_receptor_raw(project_dir, "raw/receptor.pdb", content="ATOM original\n")
            previous_output = project_dir / RECEPTOR_PREPARATION_OUTPUT
            previous_output.write_text("REMARK previous prepared receptor\n", encoding="utf-8")
            project_json = project_dir / "project.json"
            before = json.loads(project_json.read_text(encoding="utf-8"))
            before["receptor"]["file"] = RECEPTOR_PREPARATION_OUTPUT
            project_json.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=True)

            updated = json.loads(project_json.read_text(encoding="utf-8"))
            metadata = json.loads((project_dir / result["metadata_file"]).read_text(encoding="utf-8"))
            input_snapshot = json.loads((project_dir / metadata["input_snapshot_file"]).read_text(encoding="utf-8"))
            output_check = json.loads((project_dir / metadata["output_check_file"]).read_text(encoding="utf-8"))
            candidate = project_dir / metadata["candidate_output_file"]

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PREPARATION_INPUT_STALE")
            self.assertEqual(previous_output.read_text(encoding="utf-8"), "REMARK previous prepared receptor\n")
            self.assertTrue(candidate.is_file())
            self.assertEqual(candidate.read_text(encoding="utf-8"), "REMARK stale candidate\n")
            self.assertFalse(output_check["published"])
            self.assertFalse(output_check["input_verification"]["matches"])
            self.assertIn("raw 文件内容 SHA256 已变化", output_check["input_verification"]["reasons"])
            self.assertEqual(updated["preparation"]["receptor"]["status"], "failed")
            self.assertEqual(updated["preparation"]["receptor"]["error"]["code"], "PREPARATION_INPUT_STALE")
            self.assertEqual(updated["receptor"]["file"], RECEPTOR_PREPARATION_OUTPUT)
            self.assertEqual(input_snapshot["canonical_input_file"], "raw/receptor.pdb")
            self.assertEqual(len(input_snapshot["input_sha256"]), 64)
            self.assertEqual(metadata["claimed_input"]["sha256"], input_snapshot["input_sha256"])

    def test_changed_raw_reference_rejects_candidate_even_when_bytes_match(self) -> None:
        project_json: Path | None = None

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            output_stem = Path(command[command.index("-o") + 1])
            output_stem.with_suffix(".pdbqt").write_text("REMARK stale candidate\n", encoding="utf-8")
            assert project_json is not None
            project = json.loads(project_json.read_text(encoding="utf-8"))
            project["receptor"]["raw_file"] = "raw/receptor_replacement.pdb"
            project_json.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="mock receptor stdout", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._create_project(temp_dir)
            raw_content = "ATOM identical bytes\n"
            self._set_receptor_raw(project_dir, "raw/receptor.pdb", content=raw_content)
            (project_dir / "raw" / "receptor_replacement.pdb").write_text(raw_content, encoding="utf-8")
            project_json = project_dir / "project.json"

            with (
                patch("dockstart_core.preparation.get_preparation_tool_status", return_value=_tool_status()),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_receptor_pdbqt(str(project_dir), overwrite=False)

            updated = json.loads(project_json.read_text(encoding="utf-8"))
            metadata = json.loads((project_dir / result["metadata_file"]).read_text(encoding="utf-8"))
            output_check = json.loads((project_dir / metadata["output_check_file"]).read_text(encoding="utf-8"))
            candidate = project_dir / metadata["candidate_output_file"]

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "PREPARATION_INPUT_STALE")
            self.assertFalse((project_dir / RECEPTOR_PREPARATION_OUTPUT).exists())
            self.assertTrue(candidate.is_file())
            self.assertFalse(output_check["published"])
            self.assertIn("project.json 的 raw 引用已变化", output_check["input_verification"]["reasons"])
            self.assertNotIn("raw 文件内容 SHA256 已变化", output_check["input_verification"]["reasons"])
            self.assertEqual(updated["receptor"]["raw_file"], "raw/receptor_replacement.pdb")
            self.assertEqual(updated["preparation"]["receptor"]["status"], "failed")
            self.assertEqual(updated["preparation"]["receptor"]["error"]["code"], "PREPARATION_INPUT_STALE")


if __name__ == "__main__":
    unittest.main()
