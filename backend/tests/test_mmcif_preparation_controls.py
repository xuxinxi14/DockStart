from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.mmcif_identity import (  # noqa: E402
    MmcifIdentityError,
    _identity_hash_payload,
    _preparation_control_hash_payload,
    _stable_sha256,
    audit_mmcif_residue_identities,
    resolve_mmcif_flexible_selections,
    resolve_mmcif_receptor_preparation_controls,
    validate_mmcif_receptor_preparation_controls,
    verify_mmcif_pdb_bridge,
)


FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "flexible_mmcif_identity"
)


class MmcifPreparationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )

    def _controls(
        self,
        *,
        alternate_locations: dict[str, str] | None = None,
        template_assignments: dict[str, str] | None = None,
        deleted_residues: list[dict[str, str]] | None = None,
    ) -> dict:
        return resolve_mmcif_receptor_preparation_controls(
            self.contract,
            alternate_locations=(
                {"A:10:A": "B"}
                if alternate_locations is None
                else alternate_locations
            ),
            template_assignments=template_assignments,
            deleted_residues=deleted_residues,
        )

    def test_audit_freezes_nonpolymer_and_complete_coordinate_identity(
        self,
    ) -> None:
        self.assertEqual(self.contract["schema_version"], 2)
        self.assertEqual(self.contract["residue_count"], 2)
        self.assertEqual(self.contract["polymer_atom_count"], 6)
        self.assertEqual(self.contract["nonpolymer_residue_count"], 1)
        self.assertEqual(self.contract["nonpolymer_atom_count"], 1)
        self.assertEqual(self.contract["coordinate_atom_count"], 7)
        self.assertEqual(
            len(self.contract["coordinate_identity_sha256"]),
            64,
        )
        water = self.contract["nonpolymer_residues"][0]
        self.assertEqual(water["record_type"], "HETATM")
        self.assertEqual(water["selector"], "A:501")
        self.assertEqual(water["author"]["component_id"], "HOH")
        self.assertEqual(water["label"]["chain_id"], "W")
        self.assertEqual(water["label"]["sequence_id"], "")
        self.assertEqual(water["atom_count"], 1)
        self.assertEqual(len(water["atom_identity_sha256"]), 64)

    def test_requires_explicit_global_altloc_and_accepts_lower_occupancy(
        self,
    ) -> None:
        with self.assertRaises(MmcifIdentityError) as missing:
            self._controls(alternate_locations={})

        controls = self._controls(alternate_locations={"A:10:A": "B"})
        branch = controls["alternate_locations"][0]

        self.assertEqual(
            missing.exception.code,
            "MMCIF_GLOBAL_ALTLOC_CHOICE_REQUIRED",
        )
        self.assertEqual(branch["selected_altloc"], "B")
        self.assertEqual(branch["available_altlocs"], ["A", "B"])
        self.assertEqual(branch["selected_atom_count"], 3)
        self.assertEqual(
            controls["policy"]["occupancy"],
            "audit_only_never_auto_select",
        )
        self.assertEqual(controls["meeko"]["wanted_altlocs"], ["A:10A=B"])

    def test_binds_cyx_template_and_deleted_component_to_full_identity(
        self,
    ) -> None:
        controls = self._controls(
            template_assignments={"A:10": "CYX"},
            deleted_residues=[
                {
                    "selector": "A:501",
                    "expected_component_id": "HOH",
                    "reason": "water",
                }
            ],
        )

        template = controls["template_assignments"][0]
        deleted = controls["deleted_residues"][0]
        self.assertEqual(template["assigned_template"], "CYX")
        self.assertEqual(template["record_type"], "ATOM")
        self.assertEqual(template["author"]["sequence_id"], 10)
        self.assertEqual(template["label"]["chain_id"], "AA")
        self.assertEqual(template["source_component_id"], "GLY")
        self.assertEqual(template["atom_count"], 2)
        self.assertEqual(len(template["atom_identity_sha256"]), 64)
        self.assertEqual(deleted["record_type"], "HETATM")
        self.assertEqual(deleted["author"]["sequence_id"], 501)
        self.assertEqual(deleted["label"]["chain_id"], "W")
        self.assertEqual(deleted["source_component_id"], "HOH")
        self.assertEqual(deleted["expected_component_id"], "HOH")
        self.assertEqual(deleted["atom_count"], 1)
        self.assertEqual(
            controls["meeko"]["set_templates"],
            ["A:10=CYX"],
        )
        self.assertEqual(
            controls["meeko"]["delete_residues"],
            ["A:501"],
        )
        self.assertFalse(controls["meeko"]["allow_bad_res"])
        self.assertEqual(
            validate_mmcif_receptor_preparation_controls(
                self.contract,
                controls,
            ),
            controls,
        )

    def test_rejects_unknown_or_spurious_global_altloc_selector(self) -> None:
        with self.assertRaises(MmcifIdentityError) as unknown:
            self._controls(
                alternate_locations={
                    "A:10:A": "B",
                    "A:999": "A",
                }
            )
        with self.assertRaises(MmcifIdentityError) as unnecessary:
            self._controls(
                alternate_locations={
                    "A:10:A": "B",
                    "A:10": "A",
                }
            )

        self.assertEqual(
            unknown.exception.code,
            "MMCIF_ALTLOC_RESIDUE_NOT_FOUND",
        )
        self.assertEqual(
            unnecessary.exception.code,
            "MMCIF_ALTLOC_CHOICE_NOT_REQUIRED",
        )

    def test_rejects_nonexistent_altloc_branch(self) -> None:
        with self.assertRaises(MmcifIdentityError) as raised:
            self._controls(alternate_locations={"A:10:A": "C"})

        self.assertEqual(
            raised.exception.code,
            "MMCIF_ALTLOC_CHOICE_NOT_FOUND",
        )

    def test_rejects_unknown_or_nonpolymer_template_target(self) -> None:
        with self.assertRaises(MmcifIdentityError) as unknown:
            self._controls(template_assignments={"A:999": "CYX"})
        with self.assertRaises(MmcifIdentityError) as nonpolymer:
            self._controls(template_assignments={"A:501": "CYX"})

        self.assertEqual(
            unknown.exception.code,
            "MMCIF_TEMPLATE_RESIDUE_NOT_FOUND",
        )
        self.assertEqual(
            nonpolymer.exception.code,
            "MMCIF_TEMPLATE_TARGET_NOT_POLYMER",
        )

    def test_rejects_wrong_component_and_polymer_deletion(self) -> None:
        with self.assertRaises(MmcifIdentityError) as component:
            self._controls(
                deleted_residues=[
                    {
                        "selector": "A:501",
                        "expected_component_id": "BEN",
                        "reason": "co_crystal_ligand",
                    }
                ]
            )
        with self.assertRaises(MmcifIdentityError) as polymer:
            self._controls(
                deleted_residues=[
                    {
                        "selector": "A:10",
                        "expected_component_id": "GLY",
                        "reason": "other_reviewed",
                    }
                ]
            )

        self.assertEqual(
            component.exception.code,
            "MMCIF_DELETED_COMPONENT_ID_CHANGED",
        )
        self.assertEqual(
            polymer.exception.code,
            "MMCIF_DELETED_RESIDUE_NOT_NONPOLYMER",
        )

    def test_rejects_unknown_deletion_field_and_reason(self) -> None:
        with self.assertRaises(MmcifIdentityError) as unknown_field:
            self._controls(
                deleted_residues=[
                    {
                        "selector": "A:501",
                        "expected_component_id": "HOH",
                        "reason": "water",
                        "implicit": "yes",
                    }
                ]
            )
        with self.assertRaises(MmcifIdentityError) as unknown_reason:
            self._controls(
                deleted_residues=[
                    {
                        "selector": "A:501",
                        "expected_component_id": "HOH",
                        "reason": "because_the_tool_failed",
                    }
                ]
            )

        self.assertEqual(
            unknown_field.exception.code,
            "MMCIF_DELETED_RESIDUE_CONTROL_INVALID",
        )
        self.assertEqual(
            unknown_reason.exception.code,
            "MMCIF_DELETION_REASON_INVALID",
        )

    def test_rejects_control_tampering_even_with_recomputed_hash(self) -> None:
        controls = self._controls(
            template_assignments={"A:10": "CYX"},
        )
        changed = copy.deepcopy(controls)
        changed["template_assignments"][0]["author"]["sequence_id"] = 11
        changed["control_sha256"] = _stable_sha256(
            _preparation_control_hash_payload(changed)
        )

        with self.assertRaises(MmcifIdentityError) as raised:
            validate_mmcif_receptor_preparation_controls(
                self.contract,
                changed,
            )

        self.assertEqual(
            raised.exception.code,
            "MMCIF_PREPARATION_CONTROL_BINDING_CHANGED",
        )

    def test_rejects_control_tampering_without_recomputed_hash(self) -> None:
        controls = self._controls()
        changed = copy.deepcopy(controls)
        changed["alternate_locations"][0]["selected_atom_count"] += 1

        with self.assertRaises(MmcifIdentityError) as raised:
            validate_mmcif_receptor_preparation_controls(
                self.contract,
                changed,
            )

        self.assertEqual(
            raised.exception.code,
            "MMCIF_PREPARATION_CONTROL_CHANGED",
        )

    def test_bridge_verifies_and_rejects_changed_hetero_identity(self) -> None:
        verified = verify_mmcif_pdb_bridge(
            self.contract,
            FIXTURE_ROOT / "minimal_identity_gemmi.pdb",
        )
        self.assertEqual(verified["verified_atom_count"], 6)
        self.assertEqual(verified["verified_nonpolymer_atom_count"], 1)
        self.assertEqual(verified["verified_coordinate_atom_count"], 7)
        self.assertEqual(verified["bridge"]["nonpolymer_atom_count"], 1)

        bridge_text = (
            FIXTURE_ROOT / "minimal_identity_gemmi.pdb"
        ).read_text(encoding="utf-8")
        changed_text = bridge_text.replace("HOH A 501", "HOH A 502", 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "changed_hetero.pdb"
            path.write_text(changed_text, encoding="utf-8")
            with self.assertRaises(MmcifIdentityError) as raised:
                verify_mmcif_pdb_bridge(self.contract, path)

        self.assertEqual(
            raised.exception.code,
            "MMCIF_BRIDGE_PDB_ATOM_MISSING",
        )

    def test_legacy_identity_contract_remains_usable_for_old_selection_api(
        self,
    ) -> None:
        legacy = copy.deepcopy(self.contract)
        legacy["schema_version"] = 1
        for field in (
            "coordinate_atom_count",
            "polymer_atom_count",
            "nonpolymer_residue_count",
            "nonpolymer_atom_count",
            "nonpolymer_residues",
            "coordinate_identity_sha256",
        ):
            legacy.pop(field, None)
        for residue in legacy["residues"]:
            residue.pop("record_type", None)
            residue.pop("atom_identity_sha256", None)
        legacy["identity_sha256"] = _stable_sha256(
            _identity_hash_payload(legacy)
        )

        selected = resolve_mmcif_flexible_selections(
            legacy,
            ["A:10:A"],
            resolved_altlocs={"A:10:A": "B"},
        )
        self.assertEqual(selected["meeko_flexres"], ["A:10A"])
        with self.assertRaises(MmcifIdentityError) as raised:
            resolve_mmcif_receptor_preparation_controls(
                legacy,
                alternate_locations={"A:10:A": "B"},
            )
        self.assertEqual(
            raised.exception.code,
            "MMCIF_COORDINATE_IDENTITY_CONTRACT_REQUIRED",
        )


if __name__ == "__main__":
    unittest.main()
