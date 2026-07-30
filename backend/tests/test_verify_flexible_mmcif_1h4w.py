from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "verify_flexible_mmcif_1h4w.py"
MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "flexible_mmcif_1h4w"
    / "source_manifest.json"
)


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_flexible_mmcif_1h4w",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FlexibleMmcif1H4WVerifierContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_verifier()
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_pins_external_sources_and_current_identity(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 2)
        self.assertFalse(manifest["distributed_with_dockstart"])
        self.assertEqual(
            manifest["sources"]["receptor"]["sha256"],
            "5fa1772dc76ac72c8f974898a3f2e193621f2e6fadc5541463761ab5fea1042a",
        )
        self.assertEqual(
            manifest["sources"]["ligand"]["content_identity"]["sha256"],
            "74fce81d1a3c5b74c56178280a3940038ebd90212e2096017052da8a10087259",
        )
        self.assertEqual(
            manifest["sources"]["ligand"]["content_identity"]["algorithm"],
            "normalize_crlf_and_cr_to_lf_then_first_molblock_through_m_end_sha256_v1",
        )
        self.assertEqual(
            manifest["sources"]["ligand"]["observed_raw_response"]["role"],
            "request_specific_provenance_not_a_portable_identity",
        )
        identity = manifest["expected"]["identity"]
        self.assertEqual(identity["schema_version"], 2)
        self.assertEqual(identity["coordinate_atom_count"], 1893)
        self.assertEqual(identity["nonpolymer_residue_count"], 127)

    def test_manifest_covers_all_global_controls_and_ben_provenance(self) -> None:
        expected = self.manifest["expected"]
        controls = expected["receptor_controls"]
        self.assertEqual(
            set(controls["alternate_locations"]),
            {"A:27", "A:28", "A:122", "A:128", "A:192", "A:224"},
        )
        self.assertEqual(
            controls["template_assignments"],
            {"A:191": "CYX", "A:220": "CYX"},
        )
        self.assertEqual(
            controls["deleted_residues"],
            [
                {
                    "selector": "A:250",
                    "expected_component_id": "BEN",
                    "reason": "co_crystal_ligand",
                }
            ],
        )
        self.assertEqual(
            expected["flexible_preparation"]["selections"],
            ["A:192", "A:221:A"],
        )
        ligand = self.manifest["sources"]["ligand"]
        self.assertEqual(
            (ligand["entry_id"], ligand["label_asym_id"], ligand["auth_seq_id"]),
            ("1h4w", "B", 250),
        )

    def test_cli_requires_both_external_scientific_inputs(self) -> None:
        parser = self.module._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        parsed = parser.parse_args(
            ["--mmcif", "1H4W.cif", "--ligand-sdf", "BEN_A.sdf"]
        )
        self.assertEqual(parsed.mmcif, "1H4W.cif")
        self.assertEqual(parsed.ligand_sdf, "BEN_A.sdf")

    def test_project_chain_has_no_optional_or_synthetic_switch(self) -> None:
        actions = {
            action.dest
            for action in self.module._parser()._actions
            if isinstance(action, argparse.Action)
        }
        self.assertNotIn("run_meeko", actions)
        self.assertNotIn("allow_synthetic", actions)
        policy = self.manifest["acceptance_policy"]
        self.assertTrue(policy["synthetic_outputs_forbidden"])
        self.assertTrue(policy["meeko_failure_is_blocking"])
        self.assertTrue(policy["vina_failure_is_blocking"])

    def test_ligand_preparation_record_uses_project_metadata_layer(self) -> None:
        record = {
            "prep_id": "ligand_001",
            "status": "finished",
            "method": "rdkit_meeko",
        }
        result = {
            "ok": True,
            "method": None,
            "project": {"preparation": {"ligand": record}},
        }
        self.assertEqual(
            self.module._ligand_preparation_record(result),
            record,
        )

    def test_identity_index_includes_nonpolymer_ben(self) -> None:
        indexed = self.module._residue_index(
            {
                "residues": [{"selector": "A:192", "record_type": "ATOM"}],
                "nonpolymer_residues": [
                    {"selector": "A:250", "record_type": "HETATM"}
                ],
            }
        )
        self.assertEqual(indexed["A:250"]["record_type"], "HETATM")

    def test_movement_hash_is_read_from_metadata_artifact_record(self) -> None:
        record = {
            "relative_path": "runs/run_001/flexible_movement.json",
            "size_bytes": 123,
            "sha256": "a" * 64,
        }
        metadata = {"artifacts": {"flexible_movement": record}}
        self.assertEqual(
            self.module._movement_artifact_record(metadata),
            record,
        )

    def test_manifest_pins_fail_closed_flexible_nul_boundary(self) -> None:
        normalization = self.manifest["expected"]["run"]["output_normalization"]
        self.assertEqual(
            normalization["method"],
            "torsdof_flexible_residue_or_endmdl_nul_padding_v3",
        )
        self.assertEqual(normalization["recognized_padding_blocks"], 4)
        self.assertEqual(normalization["recognized_padding_lengths"], [20] * 4)
        self.assertEqual(normalization["nul_bytes_removed"], 80)

    def test_control_byte_profile_keeps_text_line_endings(self) -> None:
        self.assertEqual(
            self.module._unexpected_control_byte_counts(
                b"TORSDOF 1\r\n" + (b"\x00" * 20) + b"\r\nBEGIN_RES GLN A 192\r\n"
            ),
            {0: 20},
        )

    def test_ligand_identity_ignores_only_allowlisted_dynamic_metadata(self) -> None:
        ligand_manifest = copy.deepcopy(self.manifest["sources"]["ligand"])
        molblock = (
            "BEN\n"
            "  ModelServer 0.9.13\n"
            "\n"
            "  0  0  0  0  0  0  0  0  0  0  0\n"
            "M  END\n"
        ).encode("utf-8")
        ligand_manifest["content_identity"] = {
            "algorithm": (
                "normalize_crlf_and_cr_to_lf_then_first_molblock_"
                "through_m_end_sha256_v1"
            ),
            "size_bytes": len(molblock),
            "sha256": hashlib.sha256(molblock).hexdigest(),
        }

        def response(job_id: str, timestamp: str, timing: str) -> bytes:
            properties = dict(ligand_manifest["stable_properties"])
            properties.update(
                {
                    "model_server_result.job_id": job_id,
                    "model_server_result.datetime_utc": timestamp,
                    "model_server_stats.io_time_ms": timing,
                    "model_server_stats.parse_time_ms": "2",
                    "model_server_stats.create_model_time_ms": "3",
                    "model_server_stats.query_time_ms": "4",
                    "model_server_stats.encode_time_ms": "0",
                }
            )
            body = bytearray(molblock)
            for name, value in properties.items():
                body.extend(f"\n> <{name}>\n{value}\n".encode("utf-8"))
            body.extend(b"\n$$$$\n")
            return bytes(body)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BEN.sdf"
            first = response("job-one", "2026-07-29 00:00:00", "1")
            path.write_bytes(first)
            first_identity = self.module._verify_ligand_source_identity(
                path,
                ligand_manifest,
            )
            second = response("job-two", "2026-07-30 00:00:00", "99")
            path.write_bytes(second.replace(b"\n", b"\r\n"))
            second_identity = self.module._verify_ligand_source_identity(
                path,
                ligand_manifest,
            )

        self.assertNotEqual(
            first_identity["raw_sha256"],
            second_identity["raw_sha256"],
        )
        self.assertEqual(
            first_identity["molblock_sha256"],
            second_identity["molblock_sha256"],
        )

    def test_ligand_identity_rejects_molecular_or_record_changes(self) -> None:
        ligand_manifest = copy.deepcopy(self.manifest["sources"]["ligand"])
        molblock = b"BEN\n  ModelServer 0.9.13\n\nM  END\n"
        ligand_manifest["content_identity"] = {
            "algorithm": (
                "normalize_crlf_and_cr_to_lf_then_first_molblock_"
                "through_m_end_sha256_v1"
            ),
            "size_bytes": len(molblock),
            "sha256": hashlib.sha256(molblock).hexdigest(),
        }
        properties = dict(ligand_manifest["stable_properties"])
        for name in ligand_manifest["allowed_dynamic_properties"]:
            properties[name] = "1"
        response = bytearray(molblock)
        for name, value in properties.items():
            response.extend(f"\n> <{name}>\n{value}\n".encode("utf-8"))
        response.extend(b"\n$$$$\n")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BEN.sdf"
            path.write_bytes(bytes(response).replace(b"BEN\n", b"BAD\n", 1))
            with self.assertRaises(self.module.FlexibleMmcifAcceptanceError):
                self.module._verify_ligand_source_identity(path, ligand_manifest)
            path.write_bytes(bytes(response) + b"OTHER\nM  END\n$$$$\n")
            with self.assertRaises(self.module.FlexibleMmcifAcceptanceError):
                self.module._verify_ligand_source_identity(path, ligand_manifest)

    def test_full_verifier_isolates_and_restores_toolchain_environment(self) -> None:
        resource_variable = self.module.RESOURCE_DIR_ENV_VAR
        settings_variable = self.module.SETTINGS_ENV_VAR
        previous_resource = os.environ.get(resource_variable)
        previous_settings = os.environ.get(settings_variable)
        os.environ[resource_variable] = "original-resource-root"
        os.environ[settings_variable] = "original-settings-path"
        observed: dict[str, str] = {}

        def verify_tools(*_args, **_kwargs):
            observed["resource"] = os.environ[resource_variable]
            observed["settings"] = os.environ[settings_variable]
            self.assertNotEqual(observed["resource"], "original-resource-root")
            self.assertNotEqual(observed["settings"], "original-settings-path")
            return {"verified": True}

        def create_project(work_root, *_args, **_kwargs):
            project_dir = Path(work_root) / "project"
            project_dir.mkdir()
            return {"project_dir": str(project_dir)}

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mmcif = root / "source.cif"
                sdf = root / "source.sdf"
                python_path = root / "python.exe"
                vina_path = root / "vina.exe"
                for path in (mmcif, sdf, python_path, vina_path):
                    path.write_bytes(b"x")
                with (
                    mock.patch.object(
                        self.module,
                        "_load_manifest",
                        return_value={"fixture_id": "fixture", "expected": {}},
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_sources",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_tools",
                        side_effect=verify_tools,
                    ),
                    mock.patch.object(
                        self.module,
                        "_create_import_and_prepare_ligand",
                        side_effect=create_project,
                    ),
                    mock.patch.object(
                        self.module,
                        "_verify_identity_and_prepare_flexible",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_configure_project",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(
                        self.module,
                        "_run_analyze_and_report",
                        return_value={"verified": True},
                    ),
                    mock.patch.object(self.module, "save_settings"),
                ):
                    result = self.module.verify_flexible_mmcif_1h4w(
                        mmcif,
                        sdf,
                        python_executable=python_path,
                        vina_executable=vina_path,
                    )
            self.assertTrue(result["ok"])
            self.assertTrue(result["temporary_cleanup"]["removed"])
            self.assertTrue(result["settings_environment"]["restored"])
            self.assertTrue(
                result["toolchain_resource_environment"]["restored"]
            )
            self.assertEqual(
                os.environ.get(resource_variable),
                "original-resource-root",
            )
            self.assertEqual(
                os.environ.get(settings_variable),
                "original-settings-path",
            )
        finally:
            if previous_resource is None:
                os.environ.pop(resource_variable, None)
            else:
                os.environ[resource_variable] = previous_resource
            if previous_settings is None:
                os.environ.pop(settings_variable, None)
            else:
                os.environ[settings_variable] = previous_settings


if __name__ == "__main__":
    unittest.main()
