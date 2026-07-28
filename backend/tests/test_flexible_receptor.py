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
from dockstart_core.project import (  # noqa: E402
    _validate_frozen_pose_input_attestation,
    create_project,
    get_run_preflight,
    import_ligand_pdbqt,
    update_vina_run_protocol,
)


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

    def test_legacy_mode_field_preserves_verified_flexible_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                prepared = prepare_flexible_receptor(
                    str(project),
                    ["A:42"],
                    runner=self._runner(),
                )
            self.assertTrue(prepared["ok"], prepared)
            project_json = project / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            protocol = payload["docking_protocol"]
            protocol["mode"] = protocol.pop("receptor_mode")
            project_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            status = get_flexible_receptor_status(str(project))

            self.assertTrue(status["ok"], status)
            self.assertEqual(status["mode"], "flexible")
            self.assertEqual(status["effective_mode"], "flexible")

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

    def test_evaluation_attestation_binds_active_rigid_flex_and_ligand(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project, _ = self._project(root)
            ligand_source = root / "ligand.pdbqt"
            ligand_source.write_text(PDBQT_OUTPUT, encoding="utf-8")
            imported = import_ligand_pdbqt(str(project), str(ligand_source))
            self.assertTrue(imported["ok"], imported)
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                prepared = prepare_flexible_receptor(
                    str(project),
                    ["A:42"],
                    runner=self._runner(),
                )
            self.assertTrue(prepared["ok"], prepared)

            confirmed = update_vina_run_protocol(
                str(project),
                "score_only",
                True,
                True,
            )

            self.assertTrue(confirmed["ok"], confirmed)
            attestation = confirmed["project"]["docking_protocol"][
                "pose_input_attestation"
            ]
            self.assertRegex(attestation["receptor_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(attestation["flex_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(attestation["ligand_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(confirmed["pose_input_attestation"]["valid"])
            self.assertEqual(
                confirmed["pose_input_attestation"]["current_flex_sha256"],
                attestation["flex_sha256"],
            )
            self.assertIsNone(
                _validate_frozen_pose_input_attestation(
                    {"pose_input_attestation": attestation},
                    "score_only",
                    {
                        "receptor": attestation["receptor_sha256"],
                        "flex": attestation["flex_sha256"],
                        "ligand": attestation["ligand_sha256"],
                    },
                )
            )
            flex_mismatch = _validate_frozen_pose_input_attestation(
                {"pose_input_attestation": attestation},
                "score_only",
                {
                    "receptor": attestation["receptor_sha256"],
                    "flex": "0" * 64,
                    "ligand": attestation["ligand_sha256"],
                },
            )
            self.assertEqual(
                flex_mismatch["error"]["code"],
                "RUN_POSE_INPUT_ATTESTATION_HASH_MISMATCH",
            )

            preflight = get_run_preflight(str(project))
            self.assertIn("--flex", preflight["command_preview"])
            self.assertIn(
                prepared["outputs"]["flex_pdbqt"],
                preflight["command_preview"],
            )
            receptor_stats = preflight["input_stats"]["receptor"]
            self.assertEqual(
                receptor_stats["relative_path"],
                prepared["outputs"]["rigid_pdbqt"],
            )

    def test_cif_is_explicitly_rejected_without_audited_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir), suffix=".cif")
            result = validate_flexible_receptor_preparation(str(project), ["A:42"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "FLEX_RECEPTOR_CIF_BRIDGE_UNAVAILABLE")

    def test_bad_residues_require_strict_review_then_explicit_matching_confirmation(self) -> None:
        bad_residues = ["A:226", "A:229"]
        diagnostics = "\n".join(
            f"No template matched for residue_key='{value}'"
            for value in bad_residues
        )

        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            if "--allow_bad_res" not in argv:
                return SimpleNamespace(returncode=1, stdout="", stderr=diagnostics)
            basename = Path(argv[argv.index("--output_basename") + 1])
            Path(str(basename) + "_rigid.pdbqt").write_text(PDBQT_OUTPUT, encoding="utf-8")
            Path(str(basename) + "_flex.pdbqt").write_text(PDBQT_OUTPUT, encoding="utf-8")
            Path(str(basename) + ".json").write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="prepared", stderr=diagnostics)

        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir))
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=self._python_tool(),
            ):
                strict = prepare_flexible_receptor(str(project), ["A:42"], runner=runner)
                confirmed = prepare_flexible_receptor(
                    str(project),
                    ["A:42"],
                    allow_bad_res=True,
                    acknowledged_bad_residues=bad_residues,
                    runner=runner,
                )

            self.assertFalse(strict["ok"])
            self.assertEqual(strict["error"]["code"], "FLEX_BAD_RESIDUES_REVIEW_REQUIRED")
            self.assertEqual(strict["review"]["bad_residues"], bad_residues)
            self.assertTrue(confirmed["ok"])
            self.assertTrue(confirmed["scientific_review"]["allow_bad_res"])
            status = get_flexible_receptor_status(str(project))
            review = status["flexible_receptor"]["scientific_review"]

        self.assertEqual(review["acknowledged_bad_residues"], bad_residues)
        self.assertEqual(review["detected_bad_residues"], bad_residues)


if __name__ == "__main__":
    unittest.main()
