import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import (  # noqa: E402
    BoxSettings,
    DockStartProject,
    VinaSettings,
    _build_run_snapshot_config,
    _validate_vina_grid_resource,
    build_vina_config_text,
    create_project,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    load_project,
    parse_vina_evaluation_text,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
    update_vina_run_protocol,
    validate_vina_params,
    validate_vina_runtime_capabilities,
)
from dockstart_core.models import ToolCheckResult  # noqa: E402


RECEPTOR_PDBQT = (
    "ATOM      1  C   REC A   1       0.000   0.000   0.000  1.00  0.00     0.000 C\n"
)
LIGAND_PDBQT = (
    "ROOT\n"
    "ATOM      1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C\n"
    "ENDROOT\n"
    "TORSDOF 0\n"
)


def _energy_block(score: float) -> str:
    return f"""Estimated Free Energy of Binding   : {score:.3f} (kcal/mol)
(1) Final Intermolecular Energy     : -1.000 (kcal/mol)
    Ligand - Receptor               : -1.000 (kcal/mol)
    Ligand - Flex side chains       : 0.000 (kcal/mol)
(2) Final Total Internal Energy     : 0.000 (kcal/mol)
    Ligand                          : 0.000 (kcal/mol)
    Flex - Receptor                 : 0.000 (kcal/mol)
    Flex - Flex side chains         : 0.000 (kcal/mol)
(3) Torsional Free Energy           : 0.000 (kcal/mol)
(4) Unbound System's Energy         : 0.000 (kcal/mol)
"""


class VinaAdvancedParameterTests(unittest.TestCase):
    def _ready_project(self, temp_dir: str) -> Path:
        created = create_project("advanced_params", temp_dir)
        self.assertTrue(created["ok"], created)
        project_dir = Path(created["project_dir"])
        receptor_source = Path(temp_dir, "receptor.pdbqt")
        ligand_source = Path(temp_dir, "ligand.pdbqt")
        receptor_source.write_text(RECEPTOR_PDBQT, encoding="utf-8")
        ligand_source.write_text(LIGAND_PDBQT, encoding="utf-8")
        self.assertTrue(
            import_receptor_pdbqt(str(project_dir), str(receptor_source))["ok"],
        )
        self.assertTrue(
            import_ligand_pdbqt(str(project_dir), str(ligand_source))["ok"],
        )
        return project_dir

    def test_missing_advanced_fields_receive_vina_defaults(self) -> None:
        result = validate_vina_params(
            {
                "scoring": "vina",
                "exhaustiveness": 8,
                "num_modes": 9,
                "energy_range": 4,
                "cpu": 0,
                "seed": None,
            }
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["vina"]["max_evals"], 0)
        self.assertEqual(result["vina"]["min_rmsd"], 1)
        self.assertEqual(result["vina"]["spacing"], 0.375)
        self.assertIsNone(result["vina"]["unbound_energy"])
        self.assertFalse(result["vina"]["no_refine"])
        self.assertFalse(result["vina"]["force_even_voxels"])
        self.assertEqual(result["vina"]["verbosity"], 1)

    def test_advanced_parameter_validation_rejects_unsafe_values(self) -> None:
        base = {
            "scoring": "vina",
            "exhaustiveness": 8,
            "max_evals": 0,
            "num_modes": 9,
            "min_rmsd": 1,
            "energy_range": 4,
            "spacing": 0.375,
            "unbound_energy": None,
            "no_refine": False,
            "force_even_voxels": False,
            "verbosity": 1,
            "cpu": 0,
            "seed": None,
        }
        cases = (
            ("max_evals", -1, "VINA_PARAM_NON_NEGATIVE_REQUIRED"),
            ("max_evals", 2_147_483_648, "VINA_MAX_EVALS_TOO_LARGE"),
            ("min_rmsd", -0.1, "VINA_PARAM_NON_NEGATIVE_REQUIRED"),
            ("min_rmsd", float("nan"), "VINA_PARAM_INVALID"),
            ("spacing", 0.09, "VINA_SPACING_OUT_OF_RANGE"),
            ("spacing", 2.01, "VINA_SPACING_OUT_OF_RANGE"),
            ("unbound_energy", True, "VINA_UNBOUND_ENERGY_INVALID"),
            ("unbound_energy", float("nan"), "VINA_UNBOUND_ENERGY_INVALID"),
            ("unbound_energy", float("inf"), "VINA_UNBOUND_ENERGY_INVALID"),
            ("no_refine", "false", "VINA_BOOLEAN_REQUIRED"),
            ("force_even_voxels", 0, "VINA_BOOLEAN_REQUIRED"),
            ("verbosity", 0, "VINA_VERBOSITY_UNSUPPORTED"),
            ("verbosity", 3, "VINA_VERBOSITY_UNSUPPORTED"),
        )
        for key, value, code in cases:
            with self.subTest(key=key, value=value):
                result = validate_vina_params({**base, key: value})
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"]["code"], code)

    def test_basic_update_payload_preserves_saved_advanced_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._ready_project(temp_dir)
            advanced = update_vina_params(
                str(project_dir),
                {
                    "max_evals": 12000,
                    "min_rmsd": 1.5,
                    "spacing": 0.5,
                    "unbound_energy": -2.25,
                    "no_refine": True,
                    "force_even_voxels": True,
                    "verbosity": 2,
                },
            )
            self.assertTrue(advanced["ok"], advanced)

            basic = update_vina_params(
                str(project_dir),
                {
                    "scoring": "vinardo",
                    "exhaustiveness": 12,
                    "num_modes": 5,
                    "energy_range": 3,
                    "cpu": 2,
                    "seed": 17,
                },
            )

            self.assertTrue(basic["ok"], basic)
            vina = basic["project"]["vina"]
            self.assertEqual(vina["max_evals"], 12000)
            self.assertEqual(vina["min_rmsd"], 1.5)
            self.assertEqual(vina["spacing"], 0.5)
            self.assertEqual(vina["unbound_energy"], -2.25)
            self.assertTrue(vina["no_refine"])
            self.assertTrue(vina["force_even_voxels"])
            self.assertEqual(vina["verbosity"], 2)

    def test_unknown_vina_field_survives_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._ready_project(temp_dir)
            project_file = project_dir / "project.json"
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            payload["vina"]["future_option"] = {"enabled": True}
            project_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            updated = update_vina_params(str(project_dir), {"cpu": 3})
            loaded = load_project(str(project_dir))

            self.assertTrue(updated["ok"], updated)
            self.assertTrue(loaded["ok"], loaded)
            self.assertEqual(
                loaded["project"]["vina"]["future_option"],
                {"enabled": True},
            )

    def test_config_applies_fields_only_to_supported_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._ready_project(temp_dir)
            updated = update_vina_params(
                str(project_dir),
                {
                    "scoring": "vinardo",
                    "exhaustiveness": 11,
                    "max_evals": 22000,
                    "num_modes": 7,
                    "min_rmsd": 1.25,
                    "energy_range": 5,
                    "spacing": 0.5,
                    "unbound_energy": 5,
                    "no_refine": True,
                    "force_even_voxels": True,
                    "verbosity": 2,
                    "cpu": 2,
                    "seed": 42,
                },
            )
            self.assertTrue(updated["ok"], updated)

            dock = build_vina_config_text(str(project_dir))
            self.assertTrue(dock["ok"], dock)
            self.assertIn("max_evals = 22000", dock["config_text"])
            self.assertIn("min_rmsd = 1.25", dock["config_text"])
            self.assertIn("spacing = 0.5", dock["config_text"])
            self.assertNotIn("unbound_energy", dock["config_text"])
            self.assertIn("no_refine = true", dock["config_text"])
            self.assertIn("force_even_voxels = true", dock["config_text"])
            self.assertIn("verbosity = 2", dock["config_text"])

            protocol = update_vina_run_protocol(
                str(project_dir),
                "score_only",
                True,
                True,
            )
            self.assertTrue(protocol["ok"], protocol)
            score = build_vina_config_text(str(project_dir))
            self.assertTrue(score["ok"], score)
            self.assertNotIn("max_evals", score["config_text"])
            self.assertNotIn("min_rmsd", score["config_text"])
            self.assertIn("spacing = 0.5", score["config_text"])
            self.assertIn("unbound_energy = 5", score["config_text"])
            self.assertIn("no_refine = true", score["config_text"])
            self.assertIn("force_even_voxels = true", score["config_text"])
            self.assertIn("verbosity = 2", score["config_text"])

            project = DockStartProject(
                project_name="snapshot",
                created_at="",
                updated_at="",
                project_dir=str(project_dir),
                box=BoxSettings(),
                vina=VinaSettings(
                    max_evals=22000,
                    min_rmsd=1.25,
                    spacing=0.5,
                    unbound_energy=5,
                    no_refine=True,
                    force_even_voxels=True,
                    verbosity=2,
                ),
            )
            ad4 = _build_run_snapshot_config(
                project,
                "run_001",
                scoring_protocol="ad4_maps",
            )
            self.assertIn("max_evals = 22000", ad4)
            self.assertIn("min_rmsd = 1.25", ad4)
            self.assertIn("verbosity = 2", ad4)
            self.assertNotIn("spacing =", ad4)
            self.assertNotIn("no_refine", ad4)
            self.assertNotIn("force_even_voxels", ad4)
            self.assertNotIn("unbound_energy", ad4)

            score_snapshot = _build_run_snapshot_config(
                project,
                "run_002",
                run_mode="score_only",
            )
            local_snapshot = _build_run_snapshot_config(
                project,
                "run_003",
                run_mode="local_only",
            )
            self.assertIn("unbound_energy = 5", score_snapshot)
            self.assertNotIn("unbound_energy", local_snapshot)
            project.preserved_data["docking_protocol"] = {
                "receptor_mode": "flexible",
                "run_mode": "score_only",
            }
            flexible_score_snapshot = _build_run_snapshot_config(
                project,
                "run_004",
                run_mode="score_only",
            )
            self.assertNotIn("unbound_energy", flexible_score_snapshot)

            project.preserved_data["docking_protocol"] = {
                "mode": "flexible",
                "run_mode": "score_only",
            }
            legacy_flexible_score_snapshot = _build_run_snapshot_config(
                project,
                "run_005",
                run_mode="score_only",
            )
            self.assertNotIn(
                "unbound_energy",
                legacy_flexible_score_snapshot,
            )

    def test_unbound_energy_accepts_negative_zero_and_positive_finite_values(self) -> None:
        for value in (-12.5, 0, 7.25):
            with self.subTest(value=value):
                result = validate_vina_params(
                    {
                        "scoring": "vina",
                        "exhaustiveness": 8,
                        "num_modes": 9,
                        "energy_range": 4,
                        "cpu": 0,
                        "seed": None,
                        "unbound_energy": value,
                    }
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["vina"]["unbound_energy"], float(value))

    def test_force_even_voxels_adjusts_resource_estimate(self) -> None:
        box = {
            "size_x": 11.625,
            "size_y": 12,
            "size_z": 12,
        }

        normal = _validate_vina_grid_resource(box, 0.375, ["C"], False)
        even = _validate_vina_grid_resource(box, 0.375, ["C"], True)

        self.assertTrue(normal["ok"], normal)
        self.assertTrue(even["ok"], even)
        self.assertEqual(normal["grid_estimate"]["axis_intervals"]["x"], 31)
        self.assertEqual(even["grid_estimate"]["axis_intervals"]["x"], 32)
        self.assertEqual(even["grid_estimate"]["axis_points"]["x"], 33)
        self.assertIn("x", even["grid_estimate"]["adjusted_axes"])
        self.assertGreater(
            even["grid_estimate"]["estimated_map_bytes"],
            normal["grid_estimate"]["estimated_map_bytes"],
        )

    def test_enabled_expert_options_require_runtime_capability_evidence(self) -> None:
        supported = {
            "features": {
                "no_refine": {"status": "supported", "supported": True},
                "force_even_voxels": {"status": "supported", "supported": True},
                "unbound_energy": {"status": "supported", "supported": True},
            }
        }
        requested = {"no_refine": True, "force_even_voxels": True}

        accepted = validate_vina_runtime_capabilities(requested, "vina", supported)
        unknown = validate_vina_runtime_capabilities(requested, "vina", {})
        defaults = validate_vina_runtime_capabilities(
            {"no_refine": False, "force_even_voxels": False},
            "vina",
            {},
        )
        ad4 = validate_vina_runtime_capabilities(requested, "ad4_maps", {})
        explicit_unbound = validate_vina_runtime_capabilities(
            {"unbound_energy": 0},
            "vina",
            supported,
            run_mode="score_only",
        )
        explicit_autobox = validate_vina_runtime_capabilities(
            {},
            "vina",
            {
                "features": {
                    "autobox": {
                        "status": "supported",
                        "supported": True,
                    },
                },
            },
            run_mode="score_only",
            autobox=True,
        )
        unknown_autobox = validate_vina_runtime_capabilities(
            {},
            "vina",
            {},
            run_mode="local_only",
            autobox=True,
        )
        inactive_autobox = validate_vina_runtime_capabilities(
            {},
            "vina",
            {},
            run_mode="dock",
            autobox=True,
        )
        inactive_unbound = validate_vina_runtime_capabilities(
            {"unbound_energy": 0},
            "vina",
            {},
            run_mode="local_only",
        )
        unknown_unbound = validate_vina_runtime_capabilities(
            {"unbound_energy": 0},
            "vina",
            {},
            run_mode="score_only",
        )
        flexible_unbound = validate_vina_runtime_capabilities(
            {"unbound_energy": 0},
            "vina",
            {},
            run_mode="score_only",
            receptor_mode="flexible",
        )

        self.assertTrue(accepted["ok"], accepted)
        self.assertFalse(unknown["ok"], unknown)
        self.assertEqual(unknown["error"]["code"], "VINA_NO_REFINE_UNSUPPORTED")
        self.assertTrue(defaults["ok"], defaults)
        self.assertTrue(ad4["ok"], ad4)
        self.assertTrue(explicit_unbound["ok"], explicit_unbound)
        self.assertTrue(explicit_autobox["ok"], explicit_autobox)
        self.assertFalse(unknown_autobox["ok"], unknown_autobox)
        self.assertEqual(
            unknown_autobox["error"]["code"],
            "VINA_AUTOBOX_UNSUPPORTED",
        )
        self.assertTrue(inactive_autobox["ok"], inactive_autobox)
        self.assertTrue(inactive_unbound["ok"], inactive_unbound)
        self.assertFalse(unknown_unbound["ok"], unknown_unbound)
        self.assertTrue(flexible_unbound["ok"], flexible_unbound)
        self.assertEqual(
            unknown_unbound["error"]["code"],
            "VINA_UNBOUND_ENERGY_UNSUPPORTED",
        )

    def test_prepare_freezes_capability_evidence_for_enabled_options(self) -> None:
        capabilities = {
            "status": "ok",
            "source": "help_advanced",
            "checked": True,
            "version": "1.2.7",
            "help_exit_code": 0,
            "help_sha256": "a" * 64,
            "features": {
                "no_refine": {
                    "option": "--no_refine",
                    "status": "supported",
                    "supported": True,
                },
                "force_even_voxels": {
                    "option": "--force_even_voxels",
                    "status": "supported",
                    "supported": True,
                },
                "unbound_energy": {
                    "option": "--unbound_energy",
                    "status": "supported",
                    "supported": True,
                },
            },
            "message": "ok",
            "raw_error": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._ready_project(temp_dir)
            updated = update_vina_params(
                str(project_dir),
                {"no_refine": True, "force_even_voxels": True},
            )
            generated = generate_vina_config(str(project_dir))
            self.assertTrue(updated["ok"], updated)
            self.assertTrue(generated["ok"], generated)
            detection = ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="ok",
                version="1.2.7",
                path=sys.executable,
                source="configured",
                capabilities=capabilities,
            )

            with patch(
                "dockstart_core.project.vina_adapter.detect",
                return_value=detection,
            ):
                prepared = prepare_vina_run(str(project_dir))

            self.assertTrue(prepared["ok"], prepared)
            metadata = prepared["metadata"]
            self.assertEqual(metadata["vina_capabilities"], capabilities)
            self.assertEqual(
                metadata["vina_tool"]["capabilities"],
                capabilities,
            )
            self.assertTrue(metadata["snapshots"]["vina"]["no_refine"])
            self.assertTrue(
                metadata["snapshots"]["vina"]["force_even_voxels"]
            )

    def test_combined_box_and_spacing_resource_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = self._ready_project(temp_dir)
            box = update_box_params(
                str(project_dir),
                {
                    "center_x": 0,
                    "center_y": 0,
                    "center_z": 0,
                    "size_x": 65,
                    "size_y": 65,
                    "size_z": 65,
                },
            )
            vina = update_vina_params(str(project_dir), {"spacing": 0.1})
            self.assertTrue(box["ok"], box)
            self.assertTrue(vina["ok"], vina)

            result = build_vina_config_text(str(project_dir))

            self.assertFalse(result["ok"], result)
            self.assertEqual(
                result["error"]["code"],
                "VINA_GRID_RESOURCE_LIMIT_EXCEEDED",
            )

    def test_verbose_local_only_selects_post_optimization_energy_block(self) -> None:
        log_text = (
            "AutoDock Vina v1.2.7\n"
            "Scoring function : vina\n"
            "Number of local optimization steps: 9\n"
            "Before local optimization:\n"
            + _energy_block(36.334)
            + "Performing local search ... done.\n"
            + _energy_block(-0.055)
        )

        parsed = parse_vina_evaluation_text(log_text, "local_only")

        self.assertTrue(parsed["ok"], parsed)
        self.assertAlmostEqual(parsed["primary_score_kcal_mol"], -0.055)
        self.assertEqual(parsed["selected_energy_block_index"], 1)
        self.assertEqual(
            parsed["selected_energy_block_role"],
            "after_local_optimization",
        )
        self.assertEqual(len(parsed["energy_blocks"]), 2)


if __name__ == "__main__":
    unittest.main()
