from __future__ import annotations

import hashlib
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
REPOSITORY_ROOT = BACKEND_ROOT.parent
MMCIF_IDENTITY_FIXTURE = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "flexible_mmcif_identity"
    / "minimal_identity.cif"
)

from dockstart_core.flexible_receptor import (  # noqa: E402
    get_flexible_receptor_identity_context,
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


def _partition_pdbqt_atom(
    serial: int,
    atom_name: str,
    *,
    residue_number: int = 42,
    insertion_code: str = "",
    residue_name: str = "ALA",
    x: float = 1.0,
    y: float = 2.0,
    z: float = 3.0,
) -> str:
    return (
        f"ATOM  {serial:5d} {atom_name:^4} {residue_name:>3} A"
        f"{residue_number:4d}{insertion_code:1}   {x:8.3f}{y:8.3f}{z:8.3f}"
        "  1.00 20.00     0.000 C\n"
    )


def _write_partition_triplet(
    basename: Path,
    *,
    residue_number: int = 42,
    insertion_code: str = "",
    residue_name: str = "ALA",
    missing_flex: bool = False,
) -> None:
    rigid_atom = _partition_pdbqt_atom(
        1,
        "N",
        residue_number=residue_number,
        insertion_code=insertion_code,
        residue_name=residue_name,
    )
    flex_atom = _partition_pdbqt_atom(
        2,
        "CA",
        residue_number=residue_number,
        insertion_code=insertion_code,
        residue_name=residue_name,
    )
    Path(str(basename) + "_rigid.pdbqt").write_text(rigid_atom, encoding="utf-8")
    if not missing_flex:
        Path(str(basename) + "_flex.pdbqt").write_text(flex_atom, encoding="utf-8")
    receptor_json = {
        "monomers": {
            f"A:{residue_number}{insertion_code}": {
                "molsetup": {
                    "atoms": [
                        {
                            "index": 0,
                            "pdbinfo": [
                                "N",
                                residue_name,
                                residue_number,
                                insertion_code,
                                "A",
                            ],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                        {
                            "index": 1,
                            "pdbinfo": [
                                "CA",
                                residue_name,
                                residue_number,
                                insertion_code,
                                "A",
                            ],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                    ]
                },
                "is_flexres_atom": [False, True],
            }
        }
    }
    Path(str(basename) + ".json").write_text(
        json.dumps(receptor_json) + "\n",
        encoding="utf-8",
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
            _write_partition_triplet(basename, missing_flex=missing_flex)
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

    def test_invalid_cif_fails_closed_before_bridge_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, _ = self._project(Path(temp_dir), suffix=".cif")
            result = validate_flexible_receptor_preparation(str(project), ["A:42"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MMCIF_ATOM_SITE_NOT_FOUND")

    def test_mmcif_identity_selection_and_preparation_are_frozen_end_to_end(self) -> None:
        assisted_python = REPOSITORY_ROOT / "resources" / "python" / "python.exe"
        if not assisted_python.is_file():
            self.skipTest("Assisted Python fixture runtime is unavailable")

        python_tool = ToolCheckResult(
            key="python",
            name="Python",
            status="ok",
            path=str(assisted_python),
            source="bundled",
        )

        captured_argv: list[str] = []

        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            captured_argv[:] = argv
            basename = Path(argv[argv.index("--output_basename") + 1])
            _write_partition_triplet(
                basename,
                residue_number=10,
                insertion_code="A",
                residue_name="SER",
            )
            return SimpleNamespace(returncode=0, stdout="prepared", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            project, raw = self._project(Path(temp_dir), suffix=".cif")
            raw.write_bytes(MMCIF_IDENTITY_FIXTURE.read_bytes())
            with patch(
                "dockstart_core.flexible_receptor.get_resolved_python",
                return_value=python_tool,
            ):
                context = get_flexible_receptor_identity_context(str(project))
                validated = validate_flexible_receptor_preparation(
                    str(project),
                    ["A:10:A"],
                    receptor_controls={
                        "schema_version": 1,
                        "allow_bad_res": False,
                        "alternate_locations": {"A:10:A": "B"},
                        "template_assignments": {},
                        "deleted_residues": [],
                    },
                    expected_selection_context_sha256=str(
                        context.get("selection_context_sha256") or ""
                    ),
                    require_selection_context=True,
                )
                prepared = prepare_flexible_receptor(
                    str(project),
                    ["A:10:A"],
                    receptor_controls={
                        "schema_version": 1,
                        "allow_bad_res": False,
                        "alternate_locations": {"A:10:A": "B"},
                        "template_assignments": {},
                        "deleted_residues": [],
                    },
                    expected_selection_context_sha256=str(
                        context.get("selection_context_sha256") or ""
                    ),
                    runner=runner,
                )

            self.assertTrue(context["ok"], context)
            self.assertEqual(context["source_format"], "mmcif")
            self.assertRegex(context["identity_contract_sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((project / context["bridge_file"]).is_file())
            self.assertTrue(validated["ok"], validated)
            self.assertEqual(
                validated["selection_contract"]["selected_residues"][0][
                    "selected_altloc"
                ],
                "B",
            )
            self.assertTrue(prepared["ok"], prepared)
            self.assertIn("--wanted_altloc", captured_argv)
            self.assertEqual(
                captured_argv[captured_argv.index("--wanted_altloc") + 1],
                "A:10A=B",
            )

            status = get_flexible_receptor_status(str(project))
            frozen = status["flexible_receptor"]
            self.assertEqual(frozen["source_format"], "mmcif")
            self.assertEqual(frozen["resolved_altlocs"], {"A:10:A": "B"})
            self.assertEqual(
                frozen["receptor_controls"]["alternate_locations"],
                {"A:10:A": "B"},
            )
            self.assertRegex(
                frozen["receptor_controls_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                frozen["atom_partition"]["partition_sha256"],
                r"^[0-9a-f]{64}$",
            )
            identity = frozen["identity"]
            self.assertEqual(
                identity["identity_contract_sha256"],
                context["identity_contract_sha256"],
            )
            for key in (
                "identity_contract_sha256",
                "selection_sha256",
                "coordinate_identity_sha256",
                "preparation_controls_sha256",
                "bridge_sha256",
                "bridge_verification_sha256",
            ):
                self.assertRegex(identity[key], r"^[0-9a-f]{64}$")
            controls_path = project / identity["preparation_controls_file"]
            self.assertTrue(controls_path.is_file())
            self.assertEqual(
                identity["artifact_sha256"]["preparation_controls"],
                hashlib.sha256(controls_path.read_bytes()).hexdigest(),
            )

            selection_path = project / identity["selection_contract_file"]
            tampered_selection = json.loads(
                selection_path.read_text(encoding="utf-8")
            )
            tampered_selection["selected_residues"][0]["selected_altloc"] = "A"
            selection_path.write_text(
                json.dumps(tampered_selection, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            project_json = project / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["docking_protocol"]["flexible_receptor"]["identity"][
                "artifact_sha256"
            ]["selection_contract"] = hashlib.sha256(
                selection_path.read_bytes()
            ).hexdigest()
            project_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            tampered_status = get_flexible_receptor_status(str(project))
            self.assertFalse(tampered_status["flexible_ready"])
            self.assertTrue(
                any(
                    "选择合同" in issue
                    for issue in tampered_status["integrity"]["issues"]
                )
            )

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
            _write_partition_triplet(basename)
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
