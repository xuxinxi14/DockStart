from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.preparation import (  # noqa: E402
    MAX_LIGAND_PREPARATION_OPTIONS_JSON_BYTES,
    build_ligand_preparation_command_or_script,
    main,
    prepare_ligand_pdbqt,
)
from dockstart_core.project import create_project  # noqa: E402


def _tool_status() -> dict:
    return {
        "ok": True,
        "project_dir": "",
        "tools": {
            "python": {
                "status": "ok",
                "version": "Python 3.11.0",
                "path": sys.executable,
                "source": "current_environment",
            },
            "rdkit": {"status": "ok", "version": "mock-rdkit"},
            "meeko": {
                "status": "ok",
                "version": "mock-meeko",
                "capabilities": {"ligand_preparation": {"status": "ok"}},
            },
        },
    }


def _pdbqt() -> str:
    return (
        "REMARK SMILES C1CCCCCCC1\n"
        "REMARK SMILES IDX 1 1 2 2\n"
        "ROOT\n"
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000"
        "  1.00  0.00     0.000 C\n"
        "ENDROOT\n"
        "TORSDOF 0\n"
    )


class MacrocyclePreparationIntegrationTests(unittest.TestCase):
    def _project(self, parent: str) -> Path:
        created = create_project("macrocycle_prep", parent)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        raw = project_dir / "raw" / "ligand.sdf"
        raw.write_text("mock sdf\n", encoding="utf-8")
        project_json = project_dir / "project.json"
        data = json.loads(project_json.read_text(encoding="utf-8"))
        data["ligand"]["raw_file"] = "raw/ligand.sdf"
        project_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return project_dir

    def test_omitted_options_keep_the_legacy_script_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with patch(
                "dockstart_core.preparation.get_preparation_tool_status",
                return_value=_tool_status(),
            ):
                built = build_ligand_preparation_command_or_script(str(project_dir))

        self.assertTrue(built["ok"], built)
        self.assertTrue(built["script_file"].endswith("prepare_ligand_rdkit_meeko.py"))
        self.assertNotIn("protocol", built)
        self.assertNotIn("options", built)
        self.assertNotIn("meeko.cli.mk_prepare_ligand", built["command"])

    def test_explicit_macrocycle_protocol_uses_plan_and_records_evidence(self) -> None:
        observed: list[str] = []

        def fake_run(command: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
            _ = cwd, timeout
            observed.extend(command)
            output = Path(command[command.index("--out") + 1])
            output.write_text(_pdbqt(), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="macrocycle prepared", stderr="")

        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {"mode": "rigid"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            with (
                patch(
                    "dockstart_core.preparation.get_preparation_tool_status",
                    return_value=_tool_status(),
                ),
                patch("adapters.meeko_adapter.run_preparation_command", side_effect=fake_run),
            ):
                result = prepare_ligand_pdbqt(str(project_dir), options=options)
            metadata = json.loads(
                (project_dir / result["metadata_file"]).read_text(encoding="utf-8")
            )
            project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertIn("meeko.cli.mk_prepare_ligand", observed)
        self.assertIn("--rigid_macrocycles", observed)
        self.assertEqual(project["preparation"]["ligand"]["method"], "meeko_macrocycle")
        self.assertEqual(metadata["protocol"], "meeko_macrocycle")
        self.assertEqual(metadata["options"]["macrocycle"]["mode"], "rigid")
        self.assertTrue(metadata["protocol_evidence"]["ok"])
        inspection = metadata["protocol_evidence"]["inspection"]
        self.assertTrue(inspection["embedded_topology"])
        self.assertEqual(inspection["torsdof"], 0)

    def test_invalid_macrocycle_options_fail_before_creating_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._project(temp_dir)
            result = prepare_ligand_pdbqt(
                str(project_dir),
                options={
                    "protocol": "meeko_macrocycle",
                    "macrocycle": {"mode": "unsupported"},
                },
            )
            records = list((project_dir / "preparation").glob("ligand_*"))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "INVALID_MACROCYCLE_MODE")
        self.assertEqual(records, [])


class MacrocyclePreparationCliTests(unittest.TestCase):
    def test_cli_passes_size_limited_options_json_to_prepare(self) -> None:
        options = {
            "protocol": "meeko_macrocycle",
            "macrocycle": {"mode": "auto", "min_ring_size": 8},
        }
        stdout = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["preparation", "prepare-ligand", "project", "false", json.dumps(options)],
            ),
            patch(
                "dockstart_core.preparation.prepare_ligand_pdbqt",
                return_value={"ok": True, "project": None},
            ) as prepared,
            redirect_stdout(stdout),
        ):
            main()

        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        prepared.assert_called_once_with("project", overwrite=False, options=options)

    def test_cli_rejects_invalid_or_oversized_options_json(self) -> None:
        for raw, code in (
            ("{invalid", "LIGAND_PREPARATION_OPTIONS_JSON_INVALID"),
            (
                '"' + ("x" * MAX_LIGAND_PREPARATION_OPTIONS_JSON_BYTES) + '"',
                "LIGAND_PREPARATION_OPTIONS_TOO_LARGE",
            ),
        ):
            with self.subTest(code=code):
                stdout = io.StringIO()
                with (
                    patch.object(
                        sys,
                        "argv",
                        ["preparation", "prepare-ligand", "project", "false", raw],
                    ),
                    redirect_stdout(stdout),
                ):
                    main()
                payload = json.loads(stdout.getvalue())
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], code)


if __name__ == "__main__":
    unittest.main()
