from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.flexible_receptor import (  # noqa: E402
    get_flexible_receptor_status,
    prepare_flexible_receptor,
    set_receptor_docking_mode,
    validate_flexible_receptor_preparation,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402


def _pdb_atom(serial: int = 1) -> str:
    return (
        f"ATOM  {serial:5d}  CA  ALA A  42      "
        "  1.000   2.000   3.000  1.00 20.00           C\n"
    )


PDBQT_OUTPUT = (
    "ATOM      1  CA  ALA A  42       1.000   2.000   3.000"
    "  1.00 20.00     0.000 C\n"
)


class FlexibleReceptorProjectTests(unittest.TestCase):
    def _project(self, root: Path, *, suffix: str = ".pdb") -> tuple[Path, Path]:
        created = create_project("case", str(root))
        self.assertTrue(created["ok"])
        project = root / "case"
        raw = project / "raw" / f"receptor{suffix}"
        raw.write_text(_pdb_atom() if suffix == ".pdb" else "data_test\n", encoding="utf-8")
        project_json = project / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["receptor"]["raw_file"] = raw.relative_to(project).as_posix()
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return project, raw

    @staticmethod
    def _python_tool() -> ToolCheckResult:
        return ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            path=sys.executable,
            source="current_environment",
        )

    @staticmethod
    def _runner(*, missing_flex: bool = False, mutate: Path | None = None):
        def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
            basename = Path(argv[argv.index("--output_basename") + 1])
            Path(str(basename) + "_rigid.pdbqt").write_text(PDBQT_OUTPUT, encoding="utf-8")
            if not missing_flex:
                Path(str(basename) + "_flex.pdbqt").write_text(PDBQT_OUTPUT, encoding="utf-8")
            Path(str(basename) + ".json").write_text("{}\n", encoding="utf-8")
            if mutate is not None:
                mutate.write_text(_pdb_atom() + _pdb_atom(2), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="prepared", stderr="")

        return run

    def test_success_publishes_hashes_and_activates_only_complete_triplet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                result = prepare_flexible_receptor(project, ["A:42"], runner=self._runner())

            self.assertTrue(result["ok"])
            self.assertEqual(set(result["outputs"]), {"rigid_pdbqt", "flex_pdbqt", "receptor_json"})
            self.assertTrue(all(len(value) == 64 for value in result["sha256"].values()))
            self.assertTrue(all((project / value).is_file() for value in result["outputs"].values()))
            status = get_flexible_receptor_status(str(project))
            self.assertEqual(status["mode"], "flexible")
            self.assertEqual(status["effective_mode"], "flexible")
            self.assertTrue(status["flexible_ready"])
            self.assertTrue(
                (project / "preparation" / "flexible_receptor" / "flex_001" / "input_snapshot.pdb").is_file()
            )

    def test_missing_declared_output_keeps_legacy_project_rigid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                result = prepare_flexible_receptor(
                    str(project),
                    ["A:42"],
                    runner=self._runner(missing_flex=True),
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "DECLARED_OUTPUT_MISSING")
            payload = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertNotIn("docking_protocol", payload)
            status = get_flexible_receptor_status(str(project))
            self.assertEqual(status["effective_mode"], "rigid")

    def test_raw_change_during_execution_prevents_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, raw = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                result = prepare_flexible_receptor(
                    str(project),
                    ["A:42"],
                    runner=self._runner(mutate=raw),
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "FLEX_RECEPTOR_RAW_CHANGED")
            payload = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertNotIn("docking_protocol", payload)
            self.assertEqual(get_flexible_receptor_status(str(project))["effective_mode"], "rigid")

    def test_legacy_project_without_protocol_defaults_to_rigid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            status = get_flexible_receptor_status(str(project))

        self.assertTrue(status["ok"])
        self.assertTrue(status["legacy_default"])
        self.assertEqual(status["mode"], "rigid")
        self.assertEqual(status["effective_mode"], "rigid")

    def test_switching_back_to_rigid_retains_verified_flexible_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                prepared = prepare_flexible_receptor(str(project), ["A:42"], runner=self._runner())
            self.assertTrue(prepared["ok"])

            switched = set_receptor_docking_mode(str(project), "rigid")
            self.assertTrue(switched["ok"])
            status = get_flexible_receptor_status(str(project))
            self.assertEqual(status["mode"], "rigid")
            self.assertEqual(status["effective_mode"], "rigid")
            self.assertTrue(status["flexible_ready"])
            self.assertIsNotNone(status["flexible_receptor"])

            restored = set_receptor_docking_mode(str(project), "flexible")
            self.assertTrue(restored["ok"])
            self.assertEqual(get_flexible_receptor_status(str(project))["effective_mode"], "flexible")

    def test_cif_is_explicitly_rejected_without_audited_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir), suffix=".cif")
            result = validate_flexible_receptor_preparation(str(project), ["A:42"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "FLEX_RECEPTOR_CIF_BRIDGE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
