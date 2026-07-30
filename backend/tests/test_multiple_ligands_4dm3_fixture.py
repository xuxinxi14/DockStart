from __future__ import annotations

import hashlib
import json
import unittest
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scientific"
    / "multiple_ligands_4dm3"
)
MANIFEST_PATH = FIXTURE_DIR / "source_manifest.json"


def _coordinate_hash(atoms: list[list[str]]) -> str:
    payload = json.dumps(
        sorted(atoms, key=lambda atom: atom[0]),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class MultipleLigands4dm3FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_fixture_distributes_metadata_only(self) -> None:
        self.assertEqual(self.manifest["distribution"], "metadata_only")
        self.assertFalse(
            self.manifest["contains_upstream_coordinate_files"],
        )
        self.assertEqual(
            {path.name for path in FIXTURE_DIR.iterdir() if path.is_file()},
            {"README.md", "source_manifest.json"},
        )

    def test_source_identities_and_local_filenames_are_pinned(self) -> None:
        expected = {
            "structure_mmcif": (
                True,
                "4DM3.cif",
                516180,
                "e1320f9ed829103e77097753cb2c25ca6fb95a02d130f7967216bbed74ee4649",
            ),
            "legacy_pdb_cross_evidence": (
                False,
                "4DM3.pdb",
                403866,
                "58aa939e9979de2694d51a2d5e2106a944576a3213768fe75d3e60b6cbff0cad",
            ),
            "rco_instance_sdf": (
                True,
                "RCO_A.sdf",
                803,
                "d78494113d561fb929b89aba744c6c6eb3a17cd4e63c073c63445077cbafe676",
            ),
            "imd_instance_sdf": (
                True,
                "IMD_A.sdf",
                527,
                "51cb03e7ac7f91b3244350775eb4f95b2182b03603e455688bae3c720318625d",
            ),
            "sah_instance_sdf": (
                True,
                "SAH_A.sdf",
                2503,
                "947af8e59da961f4795e93c0c2ff7d962c7b0175c2f211004c1026dd29f562b0",
            ),
        }
        sources = self.manifest["source_files"]
        self.assertEqual(set(sources), set(expected))
        self.assertEqual(
            len({record["local_filename"] for record in sources.values()}),
            len(sources),
        )
        for name, (
            required,
            local_filename,
            size_bytes,
            sha256,
        ) in expected.items():
            with self.subTest(source=name):
                record = sources[name]
                self.assertIs(record["required"], required)
                self.assertEqual(record["local_filename"], local_filename)
                self.assertEqual(
                    record["content_identity"]["size_bytes"],
                    size_bytes,
                )
                self.assertEqual(
                    record["content_identity"]["sha256"],
                    sha256,
                )
                self.assertTrue(record["url"].startswith("https://"))

        sdf_algorithm = (
            "normalize_crlf_and_cr_to_lf_then_"
            "first_molblock_through_m_end_sha256_v1"
        )
        for name in (
            "rco_instance_sdf",
            "imd_instance_sdf",
            "sah_instance_sdf",
        ):
            self.assertEqual(
                sources[name]["content_identity"]["algorithm"],
                sdf_algorithm,
            )

    def test_structure_selection_is_same_chain_and_exactly_partitioned(
        self,
    ) -> None:
        selection = self.manifest["structure_selection"]
        receptor = selection["receptor"]
        self.assertEqual(selection["model_number"], 1)
        self.assertEqual(receptor["author_chain_id"], "A")
        self.assertEqual(receptor["polymer"]["atom_site_count"], 2016)
        self.assertEqual(
            receptor["retained_cofactor"],
            {
                "component_id": "SAH",
                "label_asym_id": "C",
                "author_chain_id": "A",
                "author_sequence_id": 2001,
                "atom_site_count": 26,
            },
        )
        self.assertEqual(
            [
                (
                    ligand["component_id"],
                    ligand["author_chain_id"],
                    ligand["author_sequence_id"],
                )
                for ligand in selection["docking_ligands"]
            ],
            [("RCO", "A", 2002), ("IMD", "A", 2003)],
        )
        accounting = selection["atom_accounting"]
        self.assertEqual(
            accounting["source_total_atom_site_count"],
            accounting["retained_receptor_atom_site_count"]
            + accounting["excluded_atom_site_count"],
        )
        self.assertEqual(
            (
                accounting["source_total_atom_site_count"],
                accounting["retained_receptor_atom_site_count"],
                accounting["excluded_atom_site_count"],
            ),
            (4394, 2042, 2352),
        )
        self.assertEqual(
            selection["same_protomer_guard"][
                "minimum_author_chain_B_polymer_to_reference_ligand_distance_angstrom"
            ],
            17.76553,
        )

    def test_reference_coordinate_hashes_and_rco_altloc_rule(self) -> None:
        references = self.manifest["reference_poses"]
        rco = references["rco"]
        self.assertEqual(rco["run_input_altloc"], "B")
        self.assertEqual(
            rco["run_input_selection_rule"],
            "highest_occupancy_complete_altloc",
        )
        self.assertTrue(
            rco["input_derivation"]["require_complete_bijection"],
        )
        self.assertEqual(
            rco["input_derivation"]["atom_name_assignment"],
            "complete_element_plus_altloc_A_xyz_3dp_bijection",
        )
        self.assertIn(
            {
                "C1": "C3",
                "C2": "C2",
                "C3": "C1",
                "C4": "C6",
                "C5": "C5",
                "C6": "C4",
                "O1": "O3",
                "O3": "O1",
            },
            rco["symmetry_oracle"]["explicit_atom_name_permutations"],
        )
        self.assertEqual(
            rco["symmetry_oracle"]["output_atom_identity_source"],
            "Meeko_REMARK_INDEX_MAP",
        )
        self.assertEqual(
            [(entry["altloc"], entry["occupancy"]) for entry in rco["conformers"]],
            [("A", 0.33), ("B", 0.48)],
        )
        for conformer in rco["conformers"]:
            with self.subTest(altloc=conformer["altloc"]):
                self.assertEqual(
                    _coordinate_hash(conformer["atoms"]),
                    conformer["coordinate_sha256"],
                )

        imd = references["imd"]
        self.assertEqual(
            _coordinate_hash(imd["atoms"]),
            imd["coordinate_sha256"],
        )
        symmetry = imd["symmetry_oracle"]
        self.assertTrue(symmetry["include_explicit_resonance_mapping"])
        self.assertIn(
            {
                "C2": "C2",
                "C4": "C5",
                "C5": "C4",
                "N1": "N3",
                "N3": "N1",
            },
            symmetry["explicit_atom_name_permutations"],
        )

    def test_box_is_recomputed_from_reference_union(self) -> None:
        references = self.manifest["reference_poses"]
        atoms = [
            atom
            for conformer in references["rco"]["conformers"]
            for atom in conformer["atoms"]
        ] + references["imd"]["atoms"]
        coordinates = {
            axis: [Decimal(atom[index]) for atom in atoms]
            for axis, index in (("x", 2), ("y", 3), ("z", 4))
        }
        contract = self.manifest["box_contract"]
        padding = Decimal(str(contract["padding_each_side_angstrom"]))
        spacing = Decimal(str(contract["spacing_angstrom"]))

        expected_minimum: dict[str, Decimal] = {}
        expected_maximum: dict[str, Decimal] = {}
        expected_center: dict[str, Decimal] = {}
        expected_intervals: dict[str, int] = {}
        expected_size: dict[str, Decimal] = {}
        for axis in ("x", "y", "z"):
            minimum = min(coordinates[axis])
            maximum = max(coordinates[axis])
            intervals = int(
                (
                    (maximum - minimum + 2 * padding) / spacing
                ).to_integral_value(rounding=ROUND_CEILING)
            )
            if intervals % 2:
                intervals += 1
            expected_minimum[axis] = minimum
            expected_maximum[axis] = maximum
            expected_center[axis] = (minimum + maximum) / 2
            expected_intervals[axis] = intervals
            expected_size[axis] = Decimal(intervals) * spacing

        self.assertEqual(
            expected_minimum,
            {
                axis: Decimal(str(value))
                for axis, value in contract["source_bounds"]["minimum"].items()
            },
        )
        self.assertEqual(
            expected_maximum,
            {
                axis: Decimal(str(value))
                for axis, value in contract["source_bounds"]["maximum"].items()
            },
        )
        self.assertEqual(
            expected_center,
            {
                axis: Decimal(str(value))
                for axis, value in contract["center"].items()
            },
        )
        self.assertEqual(expected_intervals, contract["intervals"])
        self.assertEqual(
            expected_size,
            {
                axis: Decimal(str(value))
                for axis, value in contract["size_angstrom"].items()
            },
        )
        self.assertEqual(expected_intervals, {"x": 42, "y": 44, "z": 52})

    def test_toolchain_protocol_and_run_matrix_are_frozen(self) -> None:
        toolchain = self.manifest["toolchain"]
        self.assertEqual(toolchain["python_runtime"]["version"], "3.11.15")
        self.assertEqual(
            toolchain["python_runtime"]["sha256"],
            "28d2965d061656abbe3a46100bd3342d1152185289445d489782a21a8cfe9b57",
        )
        self.assertEqual(
            toolchain["packages"],
            {
                "meeko": "0.7.1",
                "rdkit": "2026.03.3",
                "gemmi": "0.7.5",
            },
        )
        self.assertEqual(toolchain["vina"]["version"], "1.2.7")
        self.assertEqual(
            toolchain["vina"]["sha256"],
            "e0c4b2715e0c1a74f6e92d0f3be0328ac97542eafbc111e6b1efad897a73cce5",
        )

        protocol = self.manifest["docking_protocol"]
        self.assertEqual(
            {
                "protocol": protocol["protocol"],
                "maturity": protocol["maturity"],
                "scoring": protocol["scoring"],
                "exhaustiveness": protocol["exhaustiveness"],
                "num_modes": protocol["num_modes"],
                "min_rmsd": protocol["min_rmsd"],
                "energy_range": protocol["energy_range"],
                "cpu": protocol["cpu"],
            },
            {
                "protocol": "multiple_ligands",
                "maturity": "experimental",
                "scoring": "vina",
                "exhaustiveness": 32,
                "num_modes": 20,
                "min_rmsd": 1,
                "energy_range": 5,
                "cpu": 1,
            },
        )
        runs = self.manifest["run_matrix"]
        self.assertEqual(
            [(run["seed"], run["role"]) for run in runs],
            [
                (12345, "distinct_seed"),
                (23456, "distinct_seed"),
                (34567, "distinct_seed"),
                (12345, "repeatability"),
            ],
        )
        self.assertEqual(runs[-1]["repeat_of"], runs[0]["run_id"])
        preparation = self.manifest["preparation_contract"]
        self.assertEqual(
            preparation["ligands"]["expected_effective_torsions"],
            {"RCO": 2, "IMD": 0},
        )
        sah = preparation["receptor"]["sah_preparation"]
        self.assertFalse(sah["used_as_Meeko_receptor_residue_template"])
        self.assertEqual(
            sah["pipeline"],
            "add_hydrogens_then_Meeko_ligand_PDBQT_preparation",
        )
        self.assertEqual(
            preparation["ligands"]["chemical_states"]["IMD"],
            {
                "canonical_smiles": "c1c[nH+]c[nH]1",
                "formal_charge": 1,
                "automatic_neutralization_forbidden": True,
            },
        )

    def test_acceptance_separates_hard_gates_from_ranking_diagnostic(
        self,
    ) -> None:
        acceptance = self.manifest["acceptance"]
        recovery = acceptance["crystal_recovery"]
        self.assertTrue(recovery["hard_gate"])
        self.assertFalse(recovery["perform_rigid_body_fit"])
        self.assertEqual(
            recovery["member_threshold_angstrom"],
            {"RCO": 2.0, "IMD": 2.0},
        )
        self.assertEqual(recovery["maximum_rank_considered"], 20)
        self.assertTrue(
            recovery["all_distinct_seeds_require_a_recovered_joint_pose"],
        )
        self.assertEqual(
            recovery["selected_recovered_pose_policy"],
            (
                "minimum_joint_member_rmsd_quadratic_mean_"
                "then_lowest_rank_tiebreak_v1"
            ),
        )

        diagnostic = acceptance["ranking_diagnostic"]
        self.assertFalse(diagnostic["hard_gate"])
        self.assertEqual(
            (
                diagnostic["top_k"],
                diagnostic["target_distinct_seed_count"],
                diagnostic["total_distinct_seed_count"],
            ),
            (5, 2, 3),
        )
        self.assertTrue(
            diagnostic["failure_must_not_fail_overall_acceptance"],
        )

        stability = acceptance["cross_seed_stability"]
        self.assertTrue(stability["hard_gate"])
        self.assertEqual(
            stability["selected_pose_pairwise_diameter_max_angstrom"],
            {"RCO": 2.0, "IMD": 2.0},
        )
        self.assertEqual(
            stability["mode_1_joint_affinity_range_max_kcal_mol"],
            1.0,
        )

        repeatability = acceptance["same_seed_repeatability"]
        self.assertTrue(repeatability["hard_gate"])
        self.assertTrue(
            repeatability["raw_output_pdbqt_sha256_must_match"],
        )
        self.assertTrue(
            repeatability["normalized_output_pdbqt_sha256_must_match"],
        )
        self.assertTrue(repeatability["parsed_score_rows_must_match"])
        self.assertTrue(
            repeatability["canonical_scientific_payload_must_match"],
        )
        self.assertTrue(repeatability["logs_are_not_byte_identity_oracles"])

        semantics = acceptance["score_semantics"]
        self.assertEqual(
            semantics["score_granularity"],
            "one_score_per_joint_pose",
        )
        self.assertEqual(semantics["per_member_affinity"], "unavailable")
        self.assertFalse(semantics["infer_per_member_affinity"])
        self.assertFalse(
            semantics[
                "compare_joint_score_to_single_ligand_score_as_equivalent"
            ],
        )

    def test_observed_evidence_is_platform_bound_not_a_hard_oracle(
        self,
    ) -> None:
        evidence = self.manifest["observed_reference_evidence"]
        self.assertFalse(evidence["hard_acceptance_oracle"])
        self.assertFalse(evidence["cross_platform_byte_or_ranking_oracle"])
        self.assertEqual(
            [
                (
                    run["seed"],
                    run["first_joint_recovered_rank"],
                    run["joint_recovered_pose_count"],
                    run["selected_minimum_joint_rmsd_rank"],
                    Decimal(str(run["mode_1_joint_affinity_kcal_mol"])),
                )
                for run in evidence["distinct_seed_results"]
            ],
            [
                (12345, 15, 3, 15, Decimal("-9.170")),
                (23456, 15, 4, 16, Decimal("-9.185")),
                (34567, 10, 4, 20, Decimal("-9.183")),
            ],
        )
        self.assertEqual(
            evidence["ranking_diagnostic"][
                "distinct_seeds_with_top_5_joint_recovery"
            ],
            0,
        )
        self.assertEqual(
            Decimal(str(evidence["mode_1_joint_affinity_range_kcal_mol"])),
            Decimal("0.015"),
        )
        repeat = evidence["same_seed_repeatability"]
        self.assertEqual(
            repeat["raw_output_pdbqt_sha256"],
            "dced8285e569bb807fed0d96601cf6c20bb81ed0ddbf53e0ee4f313333f19527",
        )
        self.assertEqual(
            repeat["normalized_output_pdbqt_sha256"],
            "104c0a7398199b23af02ca576ba2e25fa8dd203f0d7132b9aa7ac4bc25daece5",
        )
        self.assertEqual(
            (
                repeat["raw_nul_byte_count"],
                repeat["pdbqt_model_block_count"],
                repeat["project_api_joint_pose_count"],
            ),
            (700, 40, 20),
        )


if __name__ == "__main__":
    unittest.main()
