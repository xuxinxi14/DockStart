from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.mmcif_identity import (  # noqa: E402
    MmcifIdentityError,
    audit_mmcif_residue_identities,
    resolve_mmcif_flexible_selections,
    verify_mmcif_pdb_bridge,
)


FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "flexible_mmcif_identity"
)
REPO_ROOT = BACKEND_ROOT.parent


class MmcifIdentityContractTests(unittest.TestCase):
    def test_freezes_author_label_insertion_altloc_occupancy_and_model(self) -> None:
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )

        self.assertTrue(contract["ok"])
        self.assertEqual(contract["model"]["id"], "1")
        self.assertEqual(contract["residue_count"], 2)
        self.assertEqual(len(contract["identity_sha256"]), 64)
        plain, inserted = contract["residues"]
        self.assertEqual(plain["selector"], "A:10")
        self.assertEqual(plain["label"]["chain_id"], "AA")
        self.assertEqual(plain["label"]["sequence_id"], "1")
        self.assertEqual(inserted["selector"], "A:10:A")
        self.assertEqual(inserted["meeko_id"], "A:10A")
        self.assertEqual(inserted["label"]["sequence_id"], "2")
        self.assertEqual(inserted["alternate_locations"]["ids"], ["A", "B"])
        self.assertEqual(
            inserted["alternate_locations"]["occupancy"]["A"]["minimum"],
            0.6,
        )
        self.assertEqual(
            inserted["alternate_locations"]["occupancy"]["B"]["minimum"],
            0.4,
        )

    def test_verifies_complete_gemmi_pdb_bridge(self) -> None:
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )
        result = verify_mmcif_pdb_bridge(
            contract,
            FIXTURE_ROOT / "minimal_identity_gemmi.pdb",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["verified_residue_count"], 2)
        self.assertEqual(result["verified_atom_count"], 6)
        self.assertEqual(result["bridge"]["polymer_atom_count"], 6)
        self.assertEqual(len(result["verification_sha256"]), 64)

    def test_bundled_gemmi_bridge_matches_contract_when_runtime_is_present(self) -> None:
        bundled_python = REPO_ROOT / "resources" / "python" / "python.exe"
        if not bundled_python.is_file():
            self.skipTest("Assisted Python runtime is not present in this checkout")
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            generated = Path(temp_dir) / "generated_by_gemmi.pdb"
            completed = subprocess.run(
                [
                    str(bundled_python),
                    "-I",
                    "-B",
                    "-c",
                    (
                        "import gemmi,sys; "
                        "structure=gemmi.read_structure(sys.argv[1]); "
                        "structure.write_pdb(sys.argv[2])"
                    ),
                    str(FIXTURE_ROOT / "minimal_identity.cif"),
                    str(generated),
                ],
                capture_output=True,
                text=True,
                shell=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = verify_mmcif_pdb_bridge(contract, generated)

        self.assertEqual(result["verified_atom_count"], 6)
        self.assertEqual(result["bridge"]["model_id"], "1")

    def test_resolves_author_selector_and_requires_explicit_altloc(self) -> None:
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )
        with self.assertRaises(MmcifIdentityError) as unresolved:
            resolve_mmcif_flexible_selections(contract, ["A:10:A"])
        with self.assertRaises(MmcifIdentityError) as label_number:
            resolve_mmcif_flexible_selections(contract, ["AA:2"])

        resolved = resolve_mmcif_flexible_selections(
            contract,
            ["A:10:A"],
            resolved_altlocs={"A:10:A": "B"},
        )

        self.assertEqual(unresolved.exception.code, "MMCIF_ALTLOC_CHOICE_REQUIRED")
        self.assertEqual(
            label_number.exception.code,
            "MMCIF_FLEX_RESIDUE_NOT_FOUND",
        )
        self.assertEqual(resolved["meeko_flexres"], ["A:10A"])
        self.assertEqual(resolved["wanted_altlocs"], ["A:10A=B"])
        self.assertEqual(
            resolved["selected_residues"][0]["label"]["chain_id"],
            "AA",
        )
        self.assertEqual(len(resolved["selection_sha256"]), 64)

    def test_rejects_changed_identity_contract(self) -> None:
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )
        changed = copy.deepcopy(contract)
        changed["residues"][0]["author"]["sequence_id"] = 11

        with self.assertRaises(MmcifIdentityError) as raised:
            resolve_mmcif_flexible_selections(changed, ["A:11"])

        self.assertEqual(
            raised.exception.code,
            "MMCIF_IDENTITY_CONTRACT_CHANGED",
        )

    def test_rejects_bridge_residue_renumbering(self) -> None:
        contract = audit_mmcif_residue_identities(
            FIXTURE_ROOT / "minimal_identity.cif"
        )
        bridge_text = (
            FIXTURE_ROOT / "minimal_identity_gemmi.pdb"
        ).read_text(encoding="utf-8")
        changed = bridge_text.replace("GLY A  10 ", "GLY A  11 ", 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            changed_path = Path(temp_dir) / "changed.pdb"
            changed_path.write_text(changed, encoding="utf-8")
            with self.assertRaises(MmcifIdentityError) as raised:
                verify_mmcif_pdb_bridge(contract, changed_path)

        self.assertEqual(
            raised.exception.code,
            "MMCIF_BRIDGE_PDB_ATOM_MISSING",
        )

    def test_rejects_multiple_models_before_any_selection(self) -> None:
        text = (FIXTURE_ROOT / "minimal_identity.cif").read_text(
            encoding="utf-8"
        )
        changed = text.replace(
            "ATOM 6 O OG B SER AA 1 2 A 14.200 13.200 14.200 0.40 23.00 10 SER A OG 1",
            "ATOM 6 O OG B SER AA 1 2 A 14.200 13.200 14.200 0.40 23.00 10 SER A OG 2",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "multiple_models.cif"
            path.write_text(changed, encoding="utf-8")
            with self.assertRaises(MmcifIdentityError) as raised:
                audit_mmcif_residue_identities(path)

        self.assertEqual(raised.exception.code, "MMCIF_MULTIPLE_MODELS")

    def test_rejects_bridge_that_loses_single_model_identifier(self) -> None:
        text = (FIXTURE_ROOT / "minimal_identity.cif").read_text(
            encoding="utf-8"
        )
        changed = "\n".join(
            (
                line[:-1] + "2"
                if line.startswith(("ATOM ", "HETATM ")) and line.endswith(" 1")
                else line
            )
            for line in text.splitlines()
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "model_2.cif"
            path.write_text(changed + "\n", encoding="utf-8")
            contract = audit_mmcif_residue_identities(path)
            with self.assertRaises(MmcifIdentityError) as raised:
                verify_mmcif_pdb_bridge(
                    contract,
                    FIXTURE_ROOT / "minimal_identity_gemmi.pdb",
                )

        self.assertEqual(
            raised.exception.code,
            "MMCIF_BRIDGE_PDB_MODEL_CHANGED",
        )

    def test_rejects_ambiguous_author_to_label_residue_mapping(self) -> None:
        text = (FIXTURE_ROOT / "minimal_identity.cif").read_text(
            encoding="utf-8"
        )
        changed = text.replace(
            "ATOM 2 C CA . GLY AA 1 1 ?",
            "ATOM 2 C CA . GLY AA 1 9 ?",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ambiguous_mapping.cif"
            path.write_text(changed, encoding="utf-8")
            with self.assertRaises(MmcifIdentityError) as raised:
                audit_mmcif_residue_identities(path)

        self.assertEqual(
            raised.exception.code,
            "MMCIF_AUTHOR_RESIDUE_MAPPING_AMBIGUOUS",
        )

    def test_rejects_invalid_occupancy(self) -> None:
        text = (FIXTURE_ROOT / "minimal_identity.cif").read_text(
            encoding="utf-8"
        )
        changed = text.replace(
            "14.200 13.200 14.200 0.40",
            "14.200 13.200 14.200 1.40",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid_occupancy.cif"
            path.write_text(changed, encoding="utf-8")
            with self.assertRaises(MmcifIdentityError) as raised:
                audit_mmcif_residue_identities(path)

        self.assertEqual(
            raised.exception.code,
            "MMCIF_OCCUPANCY_OUT_OF_RANGE",
        )


if __name__ == "__main__":
    unittest.main()
