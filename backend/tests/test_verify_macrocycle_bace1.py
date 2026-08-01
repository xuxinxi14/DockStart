from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "verify_macrocycle_bace1.py"
FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "macrocycle_bace1"
)
MANIFEST_PATH = FIXTURE_ROOT / "fixture_manifest.json"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_macrocycle_bace1",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MacrocycleBACE1VerifierContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_verifier()
        cls.manifest = cls.verify._load_manifest()
        cls.expected = cls.manifest["expected"]

    def test_manifest_pins_inputs_candidates_export_and_scope(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["fixture_id"], "macrocycle_bace1")
        self.assertEqual(
            manifest["expected"]["toolchain"]["vina_binary"]["sha256"],
            "e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5",
        )
        self.assertEqual(
            set(manifest["files"]),
            set(self.verify.REQUIRED_FIXTURE_FILES),
        )
        review = manifest["expected"]["review"]
        self.assertEqual(review["candidate_count_total"], 7)
        self.assertEqual(
            review["recommended_candidate_id"],
            "candidate_72113262ddb85177",
        )
        self.assertEqual(
            [item["exact_bonds"] for item in review["candidate_key"]],
            [
                [[0, 1]],
                [[1, 2]],
                [[2, 3]],
                [[3, 4]],
                [[4, 5]],
                [[5, 6]],
                [[6, 7]],
            ],
        )
        topology = manifest["expected"]["exported_pose_topology"]
        self.assertEqual(topology["heavy_atom_count"], 38)
        self.assertEqual(topology["heavy_bond_count"], 39)
        self.assertEqual(topology["total_formal_charge"], 0)
        self.assertFalse(manifest["scientific_scope"]["pose_recovery_claimed"])

    def test_fixed_fixture_identity_passes(self) -> None:
        evidence = self.verify._verify_fixture_inputs(
            FIXTURE_ROOT,
            self.manifest,
        )
        self.assertEqual(
            evidence["files"]["BACE_1_ligand.sdf"]["sha256"],
            "00f914847658da3d64b611518ad13693d4070e2b75685d6a4d45c85d3e91cee1",
        )

    def test_fixture_tamper_and_missing_file_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in self.verify.REQUIRED_FIXTURE_FILES:
                (root / name).write_bytes((FIXTURE_ROOT / name).read_bytes())
            ligand = root / "BACE_1_ligand.sdf"
            ligand.write_bytes(ligand.read_bytes() + b"\n")
            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._verify_fixture_inputs(root, self.manifest)
            self.assertEqual(
                raised.exception.code,
                "MACROCYCLE_BACE1_INPUT_IDENTITY_MISMATCH",
            )

            ligand.write_bytes((FIXTURE_ROOT / ligand.name).read_bytes())
            receptor = root / "BACE_1_receptorH.pdb"
            receptor.unlink()
            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._verify_fixture_inputs(root, self.manifest)
            self.assertEqual(
                raised.exception.code,
                "MACROCYCLE_BACE1_REQUIRED_FILE_MISSING",
            )

    def _review(self) -> dict:
        review_expected = self.expected["review"]
        return {
            "protocol_id": "meeko_macrocycle",
            "is_macrocycle": True,
            "flexible_supported": True,
            "rigid_supported": True,
            "candidate_count_total": review_expected[
                "candidate_count_total"
            ],
            "recommended_candidate_id": review_expected[
                "recommended_candidate_id"
            ],
            "atom_table_sha256": review_expected["atom_table_sha256"],
            "bond_topology_sha256": review_expected[
                "bond_topology_sha256"
            ],
            "analysis_sha256": review_expected["analysis_sha256"],
            "candidate_sets": [
                {
                    "candidate_id": item["candidate_id"],
                    "exact_bonds": copy.deepcopy(item["exact_bonds"]),
                }
                for item in review_expected["candidate_key"]
            ],
            "atom_indexing": {
                "source_atom_count": 87,
                "prepared_atom_count": 87,
                "source_indices_preserved": True,
            },
            "review_id": "review_001",
            "record": {
                "relative_path": "review.json",
                "sha256": "a" * 64,
                "size_bytes": 1,
            },
            "source": {"sha256": "b" * 64, "size_bytes": 2},
            "input_snapshot": {
                "relative_path": "input.sdf",
                "sha256": "b" * 64,
                "size_bytes": 2,
            },
        }

    def test_review_oracle_rejects_candidate_or_recommendation_drift(
        self,
    ) -> None:
        accepted = self.verify._validate_review(
            self._review(),
            self.expected,
        )
        self.assertEqual(accepted["candidate_count_total"], 7)

        for mutate in (
            lambda review: review.__setitem__(
                "recommended_candidate_id",
                "candidate_changed",
            ),
            lambda review: review.__setitem__(
                "candidate_count_total",
                6,
            ),
            lambda review: review["candidate_sets"][2].__setitem__(
                "exact_bonds",
                [[1, 3]],
            ),
        ):
            with self.subTest(mutate=mutate):
                changed = self._review()
                mutate(changed)
                with self.assertRaises(
                    self.verify.MacrocycleAcceptanceError
                ) as raised:
                    self.verify._validate_review(changed, self.expected)
                self.assertEqual(
                    raised.exception.code,
                    "MACROCYCLE_BACE1_REVIEW_ORACLE_MISMATCH",
                )

    def test_missing_contract_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "preparation").mkdir()
            metadata = {
                "status": "finished",
                "published": True,
                "protocol": "meeko_macrocycle",
                "protocol_mode": "reviewed",
                "method": "meeko_macrocycle",
                "macrocycle_contract": {
                    "protocol_id": "meeko_macrocycle",
                    "review_id": "review_001",
                    "confirmation_sha256": "c" * 64,
                    "selection_mode": "candidate",
                    "candidate_id": "candidate_72113262ddb85177",
                    "exact_bonds": [[2, 3]],
                    "atom_table_sha256": self.expected["review"][
                        "atom_table_sha256"
                    ],
                    "bond_topology_sha256": self.expected["review"][
                        "bond_topology_sha256"
                    ],
                },
                "protocol_evidence": {
                    "ok": True,
                    "mode": "reviewed",
                    "issues": [],
                    "evidence": {
                        "ok": True,
                        "protocol_id": "meeko_macrocycle",
                        "selection_mode": "candidate",
                        "candidate_id": "candidate_72113262ddb85177",
                        "expected_bonds": [[2, 3]],
                        "actual_bonds": [[2, 3]],
                        "glue_pseudo_atom_count": 2,
                        "atom_table_sha256": self.expected["review"][
                            "atom_table_sha256"
                        ],
                        "bond_topology_sha256": self.expected["review"][
                            "bond_topology_sha256"
                        ],
                        "review_id": "review_001",
                        "confirmation_sha256": "c" * 64,
                        "meeko_version": "0.7.1",
                        "rdkit_version": "2026.03.3",
                    },
                },
                "macrocycle_contract_file": (
                    "preparation/missing_contract.json"
                ),
                "macrocycle_evidence_file": (
                    "preparation/missing_evidence.json"
                ),
                "macrocycle_input_file": "preparation/missing_input.sdf",
            }
            metadata_file = root / "preparation" / "metadata.json"
            metadata_file.write_text(
                json.dumps(metadata),
                encoding="utf-8",
            )
            response = {
                "ok": True,
                "exit_code": 0,
                "metadata_file": "preparation/metadata.json",
                "output_file": "prepared/ligand.pdbqt",
            }
            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._validate_reviewed_preparation_evidence(
                    root,
                    response,
                    self.expected,
                    review_id="review_001",
                    confirmation_sha256="c" * 64,
                )
        self.assertEqual(
            raised.exception.code,
            "MACROCYCLE_BACE1_ARTIFACT_MISSING",
        )

    def _topology_payload(self, pose_count: int = 2) -> dict:
        topology = self.expected["exported_pose_topology"]
        common = {
            "heavy_atom_count": topology["heavy_atom_count"],
            "heavy_bond_count": topology["heavy_bond_count"],
            "total_formal_charge": topology["total_formal_charge"],
            "canonical_isomeric_smiles_sha256": topology[
                "canonical_isomeric_smiles_sha256"
            ],
            "forbidden_atomic_numbers_present": [],
            "pseudo_atom_symbols_present": [],
        }
        return {
            "ok": True,
            "rdkit_version": self.expected["toolchain"]["rdkit"],
            "source": {
                "atom_count": topology["source_atom_count"],
                **common,
            },
            "pose_count": pose_count,
            "poses": [
                {"pose_index": index, **common}
                for index in range(1, pose_count + 1)
            ],
        }

    def test_exported_pose_gate_rejects_g_star_charge_and_topology_tamper(
        self,
    ) -> None:
        accepted = self.verify._validate_exported_pose_topology(
            self._topology_payload(),
            self.expected,
            expected_mode_count=2,
        )
        self.assertEqual(accepted["pose_count"], 2)

        cases = (
            ("g_star", "pseudo_atom_symbols_present", ["G0"]),
            ("charge", "total_formal_charge", 1),
            ("heavy_bonds", "heavy_bond_count", 38),
        )
        for label, field, value in cases:
            with self.subTest(label=label):
                payload = self._topology_payload()
                payload["poses"][0][field] = value
                with self.assertRaises(
                    self.verify.MacrocycleAcceptanceError
                ) as raised:
                    self.verify._validate_exported_pose_topology(
                        payload,
                        self.expected,
                        expected_mode_count=2,
                    )
                self.assertEqual(
                    raised.exception.code,
                    "MACROCYCLE_BACE1_EXPORTED_TOPOLOGY_MISMATCH",
                )

    def test_mk_export_audit_uses_actual_success_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            input_pdbqt = root / "out.pdbqt"
            python.write_bytes(b"python")
            input_pdbqt.write_bytes(b"pdbqt")
            command = [
                str(python),
                "-I",
                "-B",
                "-m",
                "meeko.cli.mk_export",
                str(input_pdbqt),
                "--write_sdf",
                str(root / "poses.sdf"),
            ]
            accepted = self.verify._validate_export_command_record(
                {
                    "status": "success",
                    "exit_code": 0,
                    "requested_command": command,
                },
                python,
                input_pdbqt,
            )
            self.assertEqual(accepted, command)

            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._validate_export_command_record(
                    {
                        "status": "finished",
                        "exit_code": 0,
                        "requested_command": command,
                    },
                    python,
                    input_pdbqt,
                )
        self.assertEqual(
            raised.exception.code,
            "MACROCYCLE_BACE1_EXPORT_COMMAND_INVALID",
        )

    def test_immutable_snapshot_change_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "evidence.json"
            path.write_text('{"ok":true}\n', encoding="utf-8")
            record = {
                "relative_path": "evidence.json",
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            path.write_text('{"ok":false}\n', encoding="utf-8")
            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._verify_snapshot_unchanged(
                    root,
                    record,
                    "worker evidence",
                )
        self.assertEqual(
            raised.exception.code,
            "MACROCYCLE_BACE1_IMMUTABLE_EVIDENCE_CHANGED",
        )

    def test_project_artifact_escape_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root.parent / "macrocycle-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            try:
                with self.assertRaises(
                    self.verify.MacrocycleAcceptanceError
                ) as raised:
                    self.verify._project_artifact(
                        root,
                        "../macrocycle-outside.txt",
                        "escaped evidence",
                    )
                self.assertEqual(
                    raised.exception.code,
                    "MACROCYCLE_BACE1_ARTIFACT_OUTSIDE_PROJECT",
                )
            finally:
                outside.unlink(missing_ok=True)

    def test_output_path_is_reserved_and_published_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            reservation = self.verify._prepare_output_path(str(output))
            self.assertTrue(output.is_file())
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                reservation[1],
            )

            published = self.verify._write_result(
                *reservation,
                {"ok": True, "value": 0},
            )
            self.assertEqual(published, output)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"ok": True, "value": 0},
            )

    def test_changed_output_reservation_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "evidence.json"
            reservation = self.verify._prepare_output_path(str(output))
            output.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(
                self.verify.MacrocycleAcceptanceError
            ) as raised:
                self.verify._write_result(
                    *reservation,
                    {"ok": True},
                )
            self.assertEqual(
                raised.exception.code,
                "MACROCYCLE_BACE1_OUTPUT_RESERVATION_CHANGED",
            )
            self.verify._release_output_reservation(*reservation)
            self.assertTrue(output.is_file())

    def test_cli_exposes_no_skip_or_synthetic_success_switch(self) -> None:
        parser = self.verify._parser()
        parsed = parser.parse_args([])
        self.assertEqual(Path(parsed.fixture_root), FIXTURE_ROOT)
        actions = {
            action.dest
            for action in parser._actions
            if isinstance(action, argparse.Action)
        }
        for forbidden in (
            "skip_vina",
            "skip_export",
            "allow_synthetic",
            "reuse_project",
            "trust_existing",
        ):
            self.assertNotIn(forbidden, actions)


if __name__ == "__main__":
    unittest.main()
