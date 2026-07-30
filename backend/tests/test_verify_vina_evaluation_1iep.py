from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "verify_vina_evaluation_1iep.py"
MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "vina_evaluation_1iep"
    / "source_manifest.json"
)


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_vina_evaluation_1iep",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VinaEvaluation1IEPVerifierContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_verifier()
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_pins_public_inputs_real_vina_and_mode_oracles(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertFalse(manifest["distributed_with_dockstart"])
        self.assertEqual(
            manifest["sources"]["receptor"]["sha256"],
            "761469710e3915b89b274483076dfe49754be7956661bc42f4cc36a182399d59",
        )
        self.assertEqual(
            manifest["sources"]["ligand"]["sha256"],
            "37a20e58e77072c6b3ff07f285a345e647dd4e564af316a06e49518b15b7aa61",
        )
        self.assertEqual(manifest["tool"]["version"], "1.2.7")
        self.assertEqual(
            manifest["tool"]["repository_bundled_path"],
            "resources/vina/vina.exe",
        )
        global_dock = manifest["protocols"]["global_dock"]
        score = manifest["protocols"]["score_only"]
        local = manifest["protocols"]["local_only"]
        self.assertEqual(global_dock["vina"]["max_evals"], 500)
        self.assertEqual(global_dock["vina"]["min_rmsd"], 0.5)
        self.assertEqual(global_dock["vina"]["spacing"], 0.5)
        self.assertEqual(global_dock["vina"]["verbosity"], 2)
        self.assertIn(
            "unbound_energy",
            global_dock["expected"]["forbidden_config_fields"],
        )
        self.assertTrue(score["vina"]["no_refine"])
        self.assertTrue(score["vina"]["force_even_voxels"])
        self.assertEqual(score["vina"]["unbound_energy"], 0.0)
        self.assertFalse(score["expected"]["output_pose_generated"])
        self.assertEqual(local["run_mode"], "local_only")
        self.assertEqual(local["expected"]["input_score_kcal_mol"], -11.595)
        self.assertEqual(local["expected"]["optimized_score_kcal_mol"], -12.483)

    def test_cli_requires_both_inputs_and_exposes_no_skip_switch(self) -> None:
        parser = self.module._parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([])
        parsed = parser.parse_args(
            ["--receptor", "1iep_receptor.pdbqt", "--ligand", "1iep_ligand.pdbqt"]
        )
        self.assertEqual(parsed.receptor, "1iep_receptor.pdbqt")
        self.assertEqual(parsed.ligand, "1iep_ligand.pdbqt")
        actions = {
            action.dest
            for action in parser._actions
            if isinstance(action, argparse.Action)
        }
        self.assertNotIn("skip_vina", actions)
        self.assertNotIn("allow_synthetic", actions)
        self.assertNotIn("optional", actions)

    def test_capability_gate_accepts_evidence_and_rejects_missing_evidence(
        self,
    ) -> None:
        features = {}
        for key, option in (
            ("autobox", "--autobox"),
            ("no_refine", "--no_refine"),
            ("force_even_voxels", "--force_even_voxels"),
            ("unbound_energy", "--unbound_energy"),
        ):
            features[key] = {
                "option": option,
                "status": "supported",
                "supported": True,
                "advertised": True,
                "minimum_version": "1.2.0",
                "version_compatible": True,
            }
        result = self.module._capability_gate_evidence(
            {
                "status": "ok",
                "checked": True,
                "version": "1.2.7",
                "features": features,
            },
            self.manifest,
        )
        self.assertTrue(result["positive"]["accepted"])
        self.assertFalse(result["negative"]["accepted"])
        self.assertEqual(
            result["negative"]["error_code"],
            "VINA_NO_REFINE_UNSUPPORTED",
        )

    def test_source_identity_mutation_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.pdbqt"
            payload = b"ROOT\nENDROOT\nTORSDOF 0\n"
            path.write_bytes(payload)
            expected = {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            evidence = self.module._identity_evidence(path, expected, "input")
            self.assertEqual(evidence["sha256"], expected["sha256"])
            path.write_bytes(payload + b"REMARK changed\n")
            with self.assertRaises(
                self.module.VinaEvaluationAcceptanceError
            ) as context:
                self.module._identity_evidence(path, expected, "input")
            self.assertEqual(
                context.exception.code,
                "VINA_EVALUATION_ORACLE_MISMATCH",
            )

    def test_config_applicability_rejects_global_only_fields_in_evaluation(
        self,
    ) -> None:
        with self.assertRaises(
            self.module.VinaEvaluationAcceptanceError
        ) as context:
            self.module._assert_config_applicability(
                "spacing = 0.375\nmax_evals = 500\n",
                required_lines=["spacing = 0.375"],
                forbidden_fields=["max_evals", "min_rmsd"],
                label="score_only",
            )
        self.assertEqual(
            context.exception.code,
            "VINA_EVALUATION_CONFIG_APPLICABILITY_INVALID",
        )

    def test_project_artifact_path_escape_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside-evidence.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                with self.assertRaises(
                    self.module.VinaEvaluationAcceptanceError
                ) as context:
                    self.module._project_artifact(
                        root,
                        "../outside-evidence.txt",
                        "escaped artifact",
                    )
                self.assertEqual(
                    context.exception.code,
                    "VINA_EVALUATION_ARTIFACT_OUTSIDE_PROJECT",
                )
            finally:
                outside.unlink(missing_ok=True)

    def test_full_verifier_restores_environment_and_removes_projects(self) -> None:
        settings_variable = self.module.SETTINGS_ENV_VAR
        resource_variable = self.module.RESOURCE_DIR_ENV_VAR
        previous_settings = os.environ.get(settings_variable)
        previous_resource = os.environ.get(resource_variable)
        os.environ[settings_variable] = "original-settings"
        os.environ[resource_variable] = "original-resources"
        observed: dict[str, str] = {}

        def verify_sources(*_args, **_kwargs):
            observed["settings"] = os.environ[settings_variable]
            observed["resources"] = os.environ[resource_variable]
            self.assertNotEqual(observed["settings"], "original-settings")
            self.assertNotEqual(observed["resources"], "original-resources")
            return {"verified": True}

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                receptor = root / "receptor.pdbqt"
                ligand = root / "ligand.pdbqt"
                vina = root / "vina.exe"
                for path in (receptor, ligand, vina):
                    path.write_bytes(b"x")
                with (
                    mock.patch.object(
                        self.module,
                        "_verify_sources",
                        side_effect=verify_sources,
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_vina",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_global_dock",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_score_only",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_local_only",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(self.module, "save_settings"),
                ):
                    result = self.module.verify_vina_evaluation_1iep(
                        receptor,
                        ligand,
                        vina_executable=vina,
                    )
            self.assertTrue(result["ok"])
            self.assertTrue(result["temporary_cleanup"]["removed"])
            self.assertTrue(result["settings_environment"]["restored"])
            self.assertTrue(
                result["toolchain_resource_environment"]["restored"]
            )
            self.assertEqual(
                os.environ.get(settings_variable),
                "original-settings",
            )
            self.assertEqual(
                os.environ.get(resource_variable),
                "original-resources",
            )
        finally:
            if previous_settings is None:
                os.environ.pop(settings_variable, None)
            else:
                os.environ[settings_variable] = previous_settings
            if previous_resource is None:
                os.environ.pop(resource_variable, None)
            else:
                os.environ[resource_variable] = previous_resource


if __name__ == "__main__":
    unittest.main()
