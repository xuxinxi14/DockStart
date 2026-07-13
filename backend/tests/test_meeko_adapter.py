from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import meeko_adapter  # noqa: E402


class MeekoAdapterCommandTests(unittest.TestCase):
    def test_bundled_runtime_exposes_actual_ligand_and_receptor_interfaces(self) -> None:
        runtime = Path(__file__).resolve().parents[2] / "resources" / "python" / "python.exe"
        if not runtime.is_file():
            self.skipTest("本地未装配 Assisted Python runtime。")
        detected = meeko_adapter.detect_meeko_capabilities(str(runtime), "bundled")
        self.assertEqual(detected["status"], "ok", detected)
        ligand = detected["capabilities"]["ligand_preparation"]
        receptor = detected["capabilities"]["receptor_preparation"]
        self.assertEqual(ligand["status"], "ok", ligand)
        self.assertTrue(ligand["molecule_preparation_callable"])
        self.assertIn(ligand["writer_interface"], {"PDBQTWriterLegacy", "PDBQTWriter"})
        self.assertEqual(receptor["status"], "ok", receptor)
        self.assertTrue(receptor["module_imported"])
        self.assertTrue(receptor["main_callable"])

    def test_build_module_command_uses_isolated_no_bytecode_invocation(self) -> None:
        command = meeko_adapter.build_module_command(
            "python.exe",
            meeko_adapter.MEEKO_RECEPTOR_MODULE,
            ["--read_pdb", "raw input.pdb", "-o", "prepared/receptor"],
        )
        self.assertEqual(
            command,
            [
                "python.exe",
                "-I",
                "-B",
                "-m",
                "meeko.cli.mk_prepare_receptor",
                "--read_pdb",
                "raw input.pdb",
                "-o",
                "prepared/receptor",
            ],
        )

    def test_build_module_command_rejects_untrusted_module_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的 Meeko 模块入口"):
            meeko_adapter.build_module_command("python.exe", "project.user_module", [])

    def test_run_preparation_command_adds_python_isolation_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch("adapters.meeko_adapter.subprocess.run") as mocked_run:
                meeko_adapter.run_preparation_command(
                    ["python.exe", "helper script.py", "input.sdf"],
                    cwd=Path(temporary_dir),
                )
        called_command = mocked_run.call_args.args[0]
        self.assertEqual(called_command[:3], ["python.exe", "-I", "-B"])
        self.assertEqual(called_command[3:], ["helper script.py", "input.sdf"])
        self.assertFalse(mocked_run.call_args.kwargs["check"])

    def test_existing_module_command_is_not_duplicated(self) -> None:
        command = ["python.exe", "-I", "-B", "-m", meeko_adapter.MEEKO_LIGAND_MODULE, "--help"]
        self.assertEqual(meeko_adapter._with_isolated_python_flags(command), command)


if __name__ == "__main__":
    unittest.main()
