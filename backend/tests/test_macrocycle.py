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

from dockstart_core import macrocycle  # noqa: E402
from dockstart_core.project import create_project  # noqa: E402

BACE_LIGAND = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "macrocycle_bace1"
    / "BACE_1_ligand.sdf"
)
BACE_LIGAND_MOL2 = BACE_LIGAND.with_suffix(".mol2")


def _fake_analysis() -> dict:
    options = macrocycle._normalize_options(None)
    atoms = [
        {
            "index_zero_based": 0,
            "number_one_based": 1,
            "name": "C1",
            "element": "C",
            "atomic_number": 6,
            "formal_charge": 0,
            "is_aromatic": False,
            "chiral_tag": "CHI_UNSPECIFIED",
            "coordinates": [0.0, 0.0, 0.0],
        },
        {
            "index_zero_based": 1,
            "number_one_based": 2,
            "name": "C2",
            "element": "C",
            "atomic_number": 6,
            "formal_charge": 0,
            "is_aromatic": False,
            "chiral_tag": "CHI_UNSPECIFIED",
            "coordinates": [1.0, 0.0, 0.0],
        },
    ]
    atom_sha = macrocycle._canonical_json_sha256(atoms)
    bond_topology = [
        {
            "atom_indices_zero_based": [0, 1],
            "bond_type": "SINGLE",
            "bond_order": 1.0,
            "is_aromatic": False,
            "is_conjugated": False,
            "stereo": "STEREONONE",
            "stereo_atom_indices_zero_based": [],
        }
    ]
    bond_topology_sha = macrocycle._canonical_json_sha256(bond_topology)
    exact = [[0, 1]]
    candidate_id = macrocycle._candidate_id(
        atom_sha,
        bond_topology_sha,
        options,
        exact,
    )
    bond = {
        "atom_indices_zero_based": [0, 1],
        "atom_numbers_one_based": [1, 2],
        "atom_labels": ["C1", "C2"],
        "endpoints": atoms,
        "bond_type": "SINGLE",
        "bond_order": 1.0,
        "is_aromatic": False,
        "stereo": "STEREONONE",
        "is_conjugated": False,
    }
    result = {
        "protocol_id": macrocycle.PROTOCOL_ID,
        "schema_version": macrocycle.REVIEW_SCHEMA_VERSION,
        "analysis_version": macrocycle.ANALYSIS_VERSION,
        "meeko_api_profile": macrocycle.MEEKO_API_PROFILE,
        "options": options,
        "tool_versions": {
            "python": "test",
            "rdkit": "test",
            "meeko": "0.7.1",
        },
        "hydrogen_policy": macrocycle.HYDROGEN_POLICY,
        "atom_indexing": {
            "hydrogen_policy": macrocycle.HYDROGEN_POLICY,
            "source_atom_count": 2,
            "prepared_atom_count": 2,
            "source_heavy_atom_indices_zero_based": [0, 1],
            "source_to_prepared_indices_zero_based": [0, 1],
            "added_hydrogen_indices_zero_based": [],
            "source_indices_preserved": True,
        },
        "molecule": {
            "name": "mock macrocycle",
            "source_atom_count": 2,
            "atom_count": 2,
            "bond_count": 1,
            "conformer_count": 1,
            "record_count": 1,
            "has_3d_coordinates": True,
        },
        "atom_table_sha256": atom_sha,
        "atoms": atoms,
        "bond_topology_sha256": bond_topology_sha,
        "bond_topology": bond_topology,
        "rings": [
            {
                "ring_id": "ring_001",
                "size": 12,
                "atom_indices_zero_based": [0, 1],
                "atom_numbers_one_based": [1, 2],
                "is_aromatic": False,
                "meeko_breakable_size": True,
                "over_supported_size": False,
            }
        ],
        "macrocycle_ring_ids": ["ring_001"],
        "is_macrocycle": True,
        "candidate_sets": [
            {
                "candidate_id": candidate_id,
                "exact_bonds": exact,
                "bonds": [bond],
                "bond_score": 100.0,
                "unbroken_ring_ids": [],
                "ring_coverage_complete": True,
            }
        ],
        "candidate_count_total": 1,
        "recommended_candidate_id": candidate_id,
        "flexible_supported": True,
        "rigid_supported": True,
        "unsupported_reasons": [],
    }
    result["analysis_sha256"] = macrocycle._canonical_json_sha256(
        macrocycle._analysis_payload(result)
    )
    return result


class MacrocycleDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        created = create_project("macrocycle_case", str(base))
        self.assertTrue(created["ok"], created)
        self.project_dir = base / "macrocycle_case"
        self.raw_path = self.project_dir / "raw" / "ligand.sdf"
        self.raw_path.write_bytes(b"mock ligand\n")
        project_path = self.project_dir / "project.json"
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        payload["ligand"]["raw_file"] = "raw/ligand.sdf"
        project_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _review(self) -> dict:
        with patch(
            "dockstart_core.macrocycle.analyze_macrocycle_file",
            return_value=_fake_analysis(),
        ):
            result = macrocycle.create_review(str(self.project_dir))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "reviewed")
        return result

    def _confirm(self) -> dict:
        reviewed = self._review()
        review = reviewed["review"]
        result = macrocycle.confirm_selection(
            str(self.project_dir),
            review["review_id"],
            review["recommended_candidate_id"],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["state"], "confirmed")
        return result

    def _require_toolkit(self) -> dict:
        try:
            return macrocycle._toolkit()
        except macrocycle.MacrocycleError as exc:
            self.skipTest(f"RDKit/Meeko 大环工具链不可用：{exc}")

    def _write_implicit_hydrogen_macrocycle(self) -> None:
        toolkit = self._require_toolkit()
        Chem = toolkit["Chem"]
        from rdkit.Chem import AllChem

        molecule = Chem.MolFromSmiles("C1CCCCCCCCCCC1")
        self.assertIsNotNone(molecule)
        molecule = Chem.AddHs(molecule)
        parameters = AllChem.ETKDGv3()
        parameters.randomSeed = 20260729
        self.assertEqual(AllChem.EmbedMolecule(molecule, parameters), 0)
        heavy_only = Chem.RemoveHs(molecule)
        self.assertTrue(heavy_only.GetConformer().Is3D())
        self.assertTrue(
            any(atom.GetNumImplicitHs() > 0 for atom in heavy_only.GetAtoms())
        )
        self.raw_path.write_text(
            Chem.MolToMolBlock(heavy_only),
            encoding="utf-8",
        )

    def _build_actual_implicit_hydrogen_plan(
        self,
    ) -> tuple[dict, dict, Path, Path]:
        self._write_implicit_hydrogen_macrocycle()
        reviewed = macrocycle.create_review(str(self.project_dir))
        self.assertTrue(reviewed["ok"], reviewed)
        review = reviewed["review"]
        self.assertTrue(review["is_macrocycle"])
        self.assertTrue(review["flexible_supported"], review)
        self.assertGreater(
            len(review["atom_indexing"]["added_hydrogen_indices_zero_based"]),
            0,
        )
        source_atom_count = review["atom_indexing"]["source_atom_count"]
        self.assertTrue(
            all(
                index < source_atom_count
                for candidate in review["candidate_sets"]
                for pair in candidate["exact_bonds"]
                for index in pair
            )
        )
        confirmed = macrocycle.confirm_selection(
            str(self.project_dir),
            review["review_id"],
            review["recommended_candidate_id"],
        )
        self.assertTrue(confirmed["ok"], confirmed)
        record_dir = self.project_dir / "preparation" / "ligand_actual"
        record_dir.mkdir()
        output = record_dir / "candidate_ligand.pdbqt"
        plan = macrocycle.build_reviewed_preparation_plan(
            str(self.project_dir),
            sys.executable,
            output,
            record_dir=record_dir,
            expected_review_id=review["review_id"],
            expected_confirmation_sha256=confirmed["confirmation"]["record"][
                "sha256"
            ],
        )
        self.assertTrue(plan["ok"], plan)
        frozen = self.project_dir / plan["input_snapshot_file"]
        return review, plan, frozen, output

    @staticmethod
    def _rebind_contract(contract: dict) -> None:
        contract["contract_binding_sha256"] = (
            macrocycle._canonical_json_sha256(
                {
                    key: value
                    for key, value in contract.items()
                    if key
                    not in {
                        "resolved_at",
                        "runtime_input",
                        "contract_binding_sha256",
                    }
                }
            )
        )

    def test_normalizes_options_and_rejects_unknown_fields(self) -> None:
        normalized = macrocycle._normalize_options(
            {
                "min_ring_size": 9,
                "allow_atom_type_a_endpoints": True,
            }
        )
        self.assertEqual(normalized["min_ring_size"], 9)
        self.assertEqual(normalized["max_ring_size"], 33)
        self.assertEqual(normalized["max_breaks"], 4)
        self.assertTrue(normalized["allow_atom_type_a_endpoints"])
        with self.assertRaises(macrocycle.MacrocycleError) as context:
            macrocycle._normalize_options({"frontend_bonds": [[0, 1]]})
        self.assertEqual(context.exception.code, "MACROCYCLE_OPTIONS_UNKNOWN")
        with self.assertRaises(macrocycle.MacrocycleError) as context:
            macrocycle._normalize_options({"double_bond_penalty": 70})
        self.assertEqual(
            context.exception.code,
            "MACROCYCLE_DOUBLE_BOND_PENALTY_UNSUPPORTED",
        )
        with self.assertRaises(macrocycle.MacrocycleError) as context:
            macrocycle._normalize_options(
                {
                    "keep_chorded_rings": True,
                    "keep_equivalent_rings": False,
                }
            )
        self.assertEqual(
            context.exception.code,
            "MACROCYCLE_RING_POLICY_CONFLICT",
        )

    def test_review_confirm_status_reset_and_resolve_contract(self) -> None:
        confirmed = self._confirm()
        review = confirmed["review"]
        confirmation = confirmed["confirmation"]
        self.assertEqual(confirmation["exact_bonds"], [[0, 1]])
        self.assertEqual(
            confirmation["selected_bonds"],
            review["candidate_sets"][0]["bonds"],
        )

        status = macrocycle.get_status(str(self.project_dir))
        self.assertEqual(status["state"], "confirmed")
        self.assertTrue(status["integrity"]["ok"])
        self.assertTrue(status["can_prepare"])

        resolved = macrocycle.resolve_confirmed_contract(
            str(self.project_dir),
            review["review_id"],
            confirmation["record"]["sha256"],
        )
        self.assertTrue(resolved["ok"], resolved)
        contract = resolved["contract"]
        self.assertEqual(contract["exact_bonds"], [[0, 1]])
        self.assertEqual(
            contract["confirmation_binding_sha256"],
            confirmation["binding_sha256"],
        )

        reset = macrocycle.reset_selection(str(self.project_dir))
        self.assertTrue(reset["ok"], reset)
        self.assertEqual(reset["state"], "reviewed")
        self.assertIsNone(reset["confirmation"])
        self.assertFalse(reset["can_prepare"])
        self.assertTrue(
            list(
                (
                    self.project_dir
                    / "preparation"
                    / "macrocycle_reviews"
                    / review["review_id"]
                ).glob("confirmation_*.json")
            ),
            "reset must not delete immutable confirmations",
        )

    def test_review_detects_source_toctou_before_activation(self) -> None:
        def mutate_live_source(_path: Path, _options: object) -> dict:
            self.raw_path.write_bytes(b"changed while analysing\n")
            return _fake_analysis()

        with patch(
            "dockstart_core.macrocycle.analyze_macrocycle_file",
            side_effect=mutate_live_source,
        ):
            result = macrocycle.create_review(str(self.project_dir))
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "MACROCYCLE_SOURCE_CHANGED_DURING_REVIEW",
        )
        status = macrocycle.get_status(str(self.project_dir))
        self.assertEqual(status["state"], "not_reviewed")
        self.assertIsNone(status["review"])

    def test_build_plan_freezes_reviewed_input_and_worker_uses_exact_bonds(
        self,
    ) -> None:
        confirmed = self._confirm()
        review = confirmed["review"]
        confirmation_sha = confirmed["confirmation"]["record"]["sha256"]
        record_dir = self.project_dir / "preparation" / "ligand_999"
        record_dir.mkdir()
        output = record_dir / "candidate_ligand.pdbqt"
        plan = macrocycle.build_reviewed_preparation_plan(
            str(self.project_dir),
            sys.executable,
            output,
            record_dir=record_dir,
            expected_review_id=review["review_id"],
            expected_confirmation_sha256=confirmation_sha,
        )
        self.assertTrue(plan["ok"], plan)
        frozen = self.project_dir / plan["input_snapshot_file"]
        self.assertEqual(frozen.read_bytes(), b"mock ligand\n")
        self.assertNotIn(str(self.raw_path), plan["argv"])
        self.assertEqual(plan["argv"][1:4], ["-I", "-B", "-c"])
        self.assertEqual(plan["argv"][4], macrocycle.ISOLATED_MODULE_RUNNER)
        self.assertEqual(
            Path(plan["argv"][5]).resolve(),
            macrocycle.BACKEND_ROOT.resolve(),
        )
        self.assertEqual(plan["argv"][6], "prepare-worker")
        self.assertEqual(
            plan["expected_output_evidence"]["exact_bonds"],
            [[0, 1]],
        )

        class FakeMolecule:
            @staticmethod
            def GetBondBetweenAtoms(left: int, right: int) -> object | None:
                return object() if (left, right) == (0, 1) else None

        class FakePreparator:
            def __init__(self) -> None:
                self._macrocycle_typer = SimpleNamespace(max_breaks=0)

            @staticmethod
            def prepare(*_args: object, **_kwargs: object) -> list[object]:
                pseudos = [
                    SimpleNamespace(is_pseudo_atom=True, atom_type="G0"),
                    SimpleNamespace(is_pseudo_atom=True, atom_type="G1"),
                ]
                return [
                    SimpleNamespace(
                        ring_closure_info=SimpleNamespace(
                            bonds_removed=[(0, 1)]
                        ),
                        atoms=pseudos,
                    )
                ]

        class FakeWriter:
            @staticmethod
            def write_string(
                _setup: object,
                add_index_map: bool = False,
            ) -> tuple[str, bool, str]:
                self.assertTrue(add_index_map)
                return (
                    "REMARK SMILES C1CCCCC1\n"
                    "ATOM      1  C1  LIG A   1       0.000   0.000   0.000  0.000 C\n"
                    "TORSDOF 0\n",
                    True,
                    "",
                )

        atom_sha = plan["contract"]["atom_table_sha256"]
        fake_analysis = _fake_analysis()
        with (
            patch(
                "dockstart_core.macrocycle._toolkit",
                return_value={
                    "PDBQTWriterLegacy": FakeWriter,
                    "meeko_version": "0.7.1",
                    "rdkit_version": "test",
                },
            ),
            patch(
                "dockstart_core.macrocycle._load_single_molecule",
                return_value=FakeMolecule(),
            ),
            patch(
                "dockstart_core.macrocycle._prepare_explicit_hydrogens",
                return_value=(
                    FakeMolecule(),
                    plan["contract"]["atom_indexing"],
                ),
            ),
            patch(
                "dockstart_core.macrocycle._atom_table",
                return_value=(
                    fake_analysis["atoms"],
                    atom_sha,
                    object(),
                ),
            ),
            patch(
                "dockstart_core.macrocycle._bond_topology",
                return_value=(
                    fake_analysis["bond_topology"],
                    plan["contract"]["bond_topology_sha256"],
                ),
            ),
            patch(
                "dockstart_core.macrocycle._candidate_setup",
                return_value=(
                    object(),
                    object(),
                    {"bond_break_combos": [[(0, 1)]]},
                    set(),
                ),
            ),
            patch(
                "dockstart_core.macrocycle._coordinate_list",
                side_effect=lambda _conf, index: [float(index), 0.0, 0.0],
            ),
            patch(
                "dockstart_core.macrocycle._meeko_preparator",
                return_value=FakePreparator(),
            ),
        ):
            prepared = macrocycle.prepare_reviewed_macrocycle(
                frozen,
                output,
                plan["contract"],
            )
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(prepared["actual_bonds"], [[0, 1]])
        self.assertEqual(prepared["glue_pseudo_atom_count"], 2)
        self.assertTrue(output.exists())

    def test_bace_analysis_supports_windows_unicode_paths(self) -> None:
        self._require_toolkit()
        self.assertTrue(BACE_LIGAND.is_file(), BACE_LIGAND)
        unicode_dir = Path(self.temporary.name) / "大环审查"
        unicode_dir.mkdir()
        unicode_path = unicode_dir / "配体分子.sdf"
        unicode_path.write_bytes(BACE_LIGAND.read_bytes())

        expected = macrocycle.analyze_macrocycle_file(BACE_LIGAND)
        actual = macrocycle.analyze_macrocycle_file(unicode_path)

        self.assertTrue(actual["is_macrocycle"])
        self.assertTrue(actual["flexible_supported"], actual)
        self.assertEqual(actual["candidate_count_total"], 7)
        recommended = next(
            candidate
            for candidate in actual["candidate_sets"]
            if candidate["candidate_id"]
            == actual["recommended_candidate_id"]
        )
        self.assertEqual(recommended["exact_bonds"], [[2, 3]])
        self.assertEqual(
            actual["atom_table_sha256"],
            expected["atom_table_sha256"],
        )
        self.assertEqual(
            actual["bond_topology_sha256"],
            expected["bond_topology_sha256"],
        )
        self.assertEqual(
            [
                candidate["candidate_id"]
                for candidate in actual["candidate_sets"]
            ],
            [
                candidate["candidate_id"]
                for candidate in expected["candidate_sets"]
            ],
        )

        self.raw_path.write_bytes(unicode_path.read_bytes())
        reviewed = macrocycle.create_review(str(self.project_dir))
        self.assertTrue(reviewed["ok"], reviewed)
        review = reviewed["review"]
        confirmed = macrocycle.confirm_selection(
            str(self.project_dir),
            review["review_id"],
            review["recommended_candidate_id"],
        )
        self.assertTrue(confirmed["ok"], confirmed)
        record_dir = self.project_dir / "preparation" / "ligand_bace"
        record_dir.mkdir()
        output = record_dir / "candidate_ligand.pdbqt"
        plan = macrocycle.build_reviewed_preparation_plan(
            str(self.project_dir),
            sys.executable,
            output,
            record_dir=record_dir,
            expected_review_id=review["review_id"],
            expected_confirmation_sha256=confirmed["confirmation"]["record"][
                "sha256"
            ],
        )
        self.assertTrue(plan["ok"], plan)
        frozen = self.project_dir / plan["input_snapshot_file"]
        prepared = macrocycle.prepare_reviewed_macrocycle(
            frozen,
            output,
            plan["contract"],
        )
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(
            prepared["actual_bonds"],
            plan["contract"]["exact_bonds"],
        )
        self.assertEqual(
            prepared["glue_pseudo_atom_count"],
            2 * len(plan["contract"]["exact_bonds"]),
        )

    def test_official_bace_mol2_has_same_topology_and_recommended_break(self) -> None:
        self._require_toolkit()
        self.assertTrue(BACE_LIGAND_MOL2.is_file(), BACE_LIGAND_MOL2)

        sdf = macrocycle.analyze_macrocycle_file(BACE_LIGAND)
        mol2 = macrocycle.analyze_macrocycle_file(BACE_LIGAND_MOL2)
        recommended = next(
            candidate
            for candidate in mol2["candidate_sets"]
            if candidate["candidate_id"] == mol2["recommended_candidate_id"]
        )

        self.assertTrue(mol2["is_macrocycle"])
        self.assertTrue(mol2["flexible_supported"], mol2)
        self.assertEqual(mol2["candidate_count_total"], 7)
        self.assertEqual(recommended["exact_bonds"], [[2, 3]])
        self.assertEqual(
            mol2["bond_topology_sha256"],
            sdf["bond_topology_sha256"],
        )

    def test_implicit_hydrogens_use_same_review_and_worker_mapping(
        self,
    ) -> None:
        review, plan, frozen, output = (
            self._build_actual_implicit_hydrogen_plan()
        )
        prepared = macrocycle.prepare_reviewed_macrocycle(
            frozen,
            output,
            plan["contract"],
        )
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(
            prepared["atom_indexing"],
            review["atom_indexing"],
        )
        self.assertEqual(
            prepared["bond_topology_sha256"],
            review["bond_topology_sha256"],
        )
        self.assertEqual(
            prepared["actual_bonds"],
            plan["contract"]["exact_bonds"],
        )
        self.assertTrue(output.is_file())

    def test_worker_rejects_rebound_bond_topology_tamper(self) -> None:
        _, plan, frozen, output = self._build_actual_implicit_hydrogen_plan()
        contract = json.loads(json.dumps(plan["contract"]))
        contract["bond_topology"][0]["is_conjugated"] = not contract[
            "bond_topology"
        ][0]["is_conjugated"]
        contract["bond_topology_sha256"] = (
            macrocycle._canonical_json_sha256(contract["bond_topology"])
        )
        self._rebind_contract(contract)

        prepared = macrocycle.prepare_reviewed_macrocycle(
            frozen,
            output,
            contract,
        )
        self.assertFalse(prepared["ok"], prepared)
        self.assertEqual(
            prepared["error"]["code"],
            "MACROCYCLE_BOND_TOPOLOGY_MISMATCH",
        )
        self.assertFalse(output.exists())

    def test_worker_rejects_rebound_toolkit_version_tamper(self) -> None:
        _, plan, frozen, output = self._build_actual_implicit_hydrogen_plan()
        for key in ("rdkit", "meeko"):
            with self.subTest(tool=key):
                contract = json.loads(json.dumps(plan["contract"]))
                contract["tool_versions"][key] = "tampered-version"
                self._rebind_contract(contract)
                prepared = macrocycle.prepare_reviewed_macrocycle(
                    frozen,
                    output,
                    contract,
                )
                self.assertFalse(prepared["ok"], prepared)
                self.assertEqual(
                    prepared["error"]["code"],
                    "MACROCYCLE_TOOLKIT_VERSION_MISMATCH",
                )
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
