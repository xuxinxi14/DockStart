from __future__ import annotations

import json
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

    def test_prepare_receptor_pdbqt_mock_failure_writes_structured_error(self) -> None:
        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="mock receptor failure")

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
        self.assertIsNotNone(updated["preparation"]["receptor"]["finished_at"])


if __name__ == "__main__":
    unittest.main()
