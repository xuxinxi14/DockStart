from __future__ import annotations

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_multiple_ligands_4dm3.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_multiple_ligands_4dm3",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atom_line(
    serial: int,
    name: str,
    atom_type: str,
    charge: float,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
) -> str:
    return (
        f"ATOM  {serial:5d} {name:>4s} UNL     1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00  0.00    {charge:6.3f} {atom_type:<2s}\n"
    )


def _scientific_run(
    run_key: str,
    seed: int,
    mode: int,
    rco_x: float,
    imd_x: float,
    *,
    repeat_of: str = "",
    output_sha: str = "a" * 64,
    raw_sha: str | None = None,
) -> dict[str, object]:
    science_payload = {
        "scores": [
            {
                "mode": 1,
                "joint_affinity_kcal_mol": -9.2,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
                "pose_available": True,
                "score_is_joint": True,
            }
        ],
        "models": [
            {
                "mode": mode,
                "joint_affinity_kcal_mol": -8.5,
                "rmsd_lb": 1.0,
                "rmsd_ub": 2.0,
                "members": [
                    {
                        "member_index": 1,
                        "heavy_coordinates": [[round(rco_x, 3), 0.0, 0.0]],
                    },
                    {
                        "member_index": 2,
                        "heavy_coordinates": [[round(imd_x, 3), 0.0, 0.0]],
                    },
                ],
            }
        ],
    }
    return {
        "run_key": run_key,
        "seed": seed,
        "repeat_of": repeat_of,
        "scores": [
            {
                "mode": 1,
                "joint_affinity_kcal_mol": -9.2 + (seed % 3) * 0.01,
            }
        ],
        "models": [
            {
                "mode": mode,
                "joint_affinity_kcal_mol": -8.5,
                "crystal_no_fit_rmsd": {
                    "RCO": 0.5,
                    "RCO_by_altloc": {"A": 0.7, "B": 0.5},
                    "IMD": 0.4,
                },
                "members": [
                    {"member_index": 1, "heavy_coordinates": [(rco_x, 0.0, 0.0)]},
                    {"member_index": 2, "heavy_coordinates": [(imd_x, 0.0, 0.0)]},
                ],
            }
        ],
        "raw_output": {
            "size_bytes": 100,
            "sha256": raw_sha or output_sha,
        },
        "normalized_output": {
            "size_bytes": 90,
            "sha256": output_sha,
        },
        "canonical_science_payload": science_payload,
        "canonical_science_payload_sha256": _sha256(
            VERIFY._canonical_json_bytes(science_payload)
        ),
    }


class MultipleLigand4dm3VerifierTests(unittest.TestCase):
    def test_scientific_boundary_contract_is_exact_and_hashed(self) -> None:
        manifest = VERIFY._load_manifest()
        evidence = VERIFY._scientific_boundary_contract(manifest)
        self.assertEqual(
            evidence["statements"],
            list(VERIFY.EXPECTED_SCIENTIFIC_BOUNDARIES),
        )
        self.assertEqual(
            evidence["sha256"],
            _sha256(
                VERIFY._canonical_json_bytes(evidence["statements"])
            ),
        )
        self.assertTrue(
            evidence[
                "dry_receptor_water_exclusion_is_modelling_assumption"
            ]
        )
        self.assertTrue(
            evidence[
                "neutral_sah_is_not_unique_experimental_microstate"
            ]
        )
        changed = dict(manifest)
        changed["scientific_boundaries"] = list(
            manifest["scientific_boundaries"]
        )[:-1]
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as raised:
            VERIFY._scientific_boundary_contract(changed)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_MANIFEST_INVALID",
        )

    def test_structure_audit_uses_gemmi_model_number_api(self) -> None:
        self.assertIn('"model_id": str(model.num)', VERIFY._STRUCTURE_AUDIT_SCRIPT)
        self.assertNotIn("model.name", VERIFY._STRUCTURE_AUDIT_SCRIPT)

    def test_sdf_portable_identity_excludes_dynamic_properties_only(self) -> None:
        molecule_lf = (
            b"RCO\n"
            b"  ModelServer\n"
            b"\n"
            b"  1  0  0  0  0  0  0  0  0  0  0 V2000\n"
            b"    1.0000    2.0000    3.0000 C   0  0  0  0  0  0\n"
            b"M  END\n"
        )
        left = molecule_lf + b"> <job_id>\nalpha\n\n$$$$\n"
        right = molecule_lf.replace(b"\n", b"\r\n") + (
            b"> <job_id>\r\nbeta\r\n\r\n$$$$\r\n"
        )
        self.assertEqual(
            VERIFY._first_molblock_portable_bytes(left),
            VERIFY._first_molblock_portable_bytes(right),
        )
        changed = left.replace(b"1.0000", b"1.0001", 1)
        self.assertNotEqual(
            VERIFY._first_molblock_portable_bytes(left),
            VERIFY._first_molblock_portable_bytes(changed),
        )

    def test_legacy_untagged_molfile_atom_table_is_accepted(self) -> None:
        molecule = (
            "RCO\n  ModelServer\n\n"
            "  1  0  0  0  0  0  0  0  0  0  0\n"
            "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0\n"
            "M  END\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "RCO_A.sdf"
            path.write_text(molecule, encoding="utf-8", newline="\n")
            records = VERIFY._v2000_heavy_atom_records(path)
        self.assertEqual(
            records,
            [
                {
                    "source_index": 1,
                    "element": "C",
                    "xyz": (1.0, 2.0, 3.0),
                }
            ],
        )

    def test_v3000_molfile_atom_table_is_rejected(self) -> None:
        molecule = (
            "RCO\n  Test\n\n"
            "  0  0  0  0  0  0  0  0  0  0  0 V3000\n"
            "M  V30 BEGIN CTAB\n"
            "M  END\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "RCO_A.sdf"
            path.write_text(molecule, encoding="utf-8", newline="\n")
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._v2000_heavy_atom_records(path)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_SDF_MOLBLOCK_INVALID",
        )

    def test_pose_coordinates_use_frozen_input_index_map_contract(self) -> None:
        prepared_records = [
            {
                "serial": 7,
                "atom_type": "C",
                "charge": 0.125,
                "is_hydrogen": False,
            },
            {
                "serial": 3,
                "atom_type": "OA",
                "charge": -0.5,
                "is_hydrogen": False,
            },
        ]
        contract = VERIFY._prepared_source_atom_contract(
            {1: 7, 2: 3},
            prepared_records,
            2,
            path=Path("prepared.pdbqt"),
        )
        topology = {
            "heavy_atom_count": 2,
            "prepared_pdbqt_atom_count": 2,
            "prepared_pdbqt_source_atom_contract": contract,
        }
        pose = (
            "MODEL 1\n"
            + _atom_line(70, "C1", "C", 0.125, 1.0, 2.0, 3.0)
            + _atom_line(30, "O1", "OA", -0.5, 4.0, 5.0, 6.0)
            + "ENDMDL\n"
        )
        self.assertNotIn("REMARK INDEX MAP", pose)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "member_001.pdbqt"
            path.write_text(pose, encoding="utf-8", newline="\n")
            coordinates = VERIFY._heavy_coordinates_from_member(path, topology)
        self.assertEqual(coordinates, [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])

    def test_pose_coordinates_reject_changed_frozen_atom_identity(self) -> None:
        topology = {
            "heavy_atom_count": 1,
            "prepared_pdbqt_atom_count": 1,
            "prepared_pdbqt_source_atom_contract": [
                {
                    "source_index": 1,
                    "pdbqt_atom_ordinal_zero_based": 0,
                    "prepared_pdbqt_serial": 1,
                    "atom_type": "C",
                    "charge": 0.125,
                }
            ],
        }
        pose = _atom_line(99, "O1", "OA", -0.5, 1.0, 2.0, 3.0)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "member_001.pdbqt"
            path.write_text(pose, encoding="utf-8", newline="\n")
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._heavy_coordinates_from_member(path, topology)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_OUTPUT_IDENTITY_INVALID",
        )

    def test_source_hash_gate_rejects_changed_molecular_content(self) -> None:
        mol = (
            b"RCO\n  Test\n\n"
            b"  1  0  0  0  0  0  0  0  0  0  0 V2000\n"
            b"    1.0000    2.0000    3.0000 C   0  0  0  0  0  0\n"
            b"M  END\n"
        )
        portable = VERIFY._first_molblock_portable_bytes(mol)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mmcif = root / "4DM3.cif"
            mmcif.write_bytes(b"data_4DM3\n")
            paths = {"structure_mmcif": mmcif}
            source_files: dict[str, object] = {
                "structure_mmcif": {
                    "content_identity": {
                        "algorithm": "raw_bytes_sha256_v1",
                        "size_bytes": mmcif.stat().st_size,
                        "sha256": VERIFY._sha256(mmcif),
                    }
                }
            }
            for key, filename in (
                ("sah_instance_sdf", "SAH_A.sdf"),
                ("rco_instance_sdf", "RCO_A.sdf"),
                ("imd_instance_sdf", "IMD_A.sdf"),
            ):
                path = root / filename
                path.write_bytes(mol + b"> <job>\nvolatile\n\n$$$$\n")
                paths[key] = path
                source_files[key] = {
                    "content_identity": {
                        "algorithm": VERIFY.SDF_PORTABLE_IDENTITY_ALGORITHM,
                        "size_bytes": len(portable),
                        "sha256": _sha256(portable),
                    }
                }
            manifest = {"source_files": source_files}
            evidence = VERIFY._verify_sources(
                manifest,
                paths,
                snapshot_root=root / "verified_sources",
            )
            self.assertFalse(evidence["network_or_download_used"])
            rco_snapshot = Path(
                evidence["snapshot_paths"]["rco_instance_sdf"]
            )
            self.assertEqual(rco_snapshot.read_bytes(), portable)

            paths["rco_instance_sdf"].write_bytes(
                mol.replace(b"1.0000", b"1.1000", 1)
            )
            self.assertEqual(rco_snapshot.read_bytes(), portable)
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._verify_sources(manifest, paths)
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_SOURCE_HASH_MISMATCH",
        )

    def test_box_uses_both_rco_altlocs_and_six_angstrom_padding(self) -> None:
        rco_a = [
            (29.565, 41.946, 21.249),
            *[(28.0, 44.0, 18.0)] * 7,
        ]
        rco_b = [
            (29.0, 46.260, 20.0),
            *[(28.0, 44.0, 18.0)] * 7,
        ]
        imd = [
            (26.182, 43.0, 14.240),
            *[(27.0, 44.0, 15.0)] * 4,
        ]
        box = VERIFY._calculate_box(
            {"A": rco_a, "B": rco_b},
            imd,
            padding_angstrom=6.0,
            spacing_angstrom=0.375,
        )
        self.assertEqual(
            box["center"],
            {"x": 27.8735, "y": 44.103, "z": 17.7445},
        )
        self.assertEqual(
            box["axis_intervals"],
            {"x": 42, "y": 44, "z": 52},
        )
        self.assertEqual(
            box["effective_size"],
            {"x": 15.75, "y": 16.5, "z": 19.5},
        )

    def test_manifest_coordinate_identity_hashes_are_reproducible(self) -> None:
        manifest = VERIFY._load_manifest()
        references = manifest["reference_poses"]
        for record in [
            *references["rco"]["conformers"],
            references["imd"],
        ]:
            atoms = [
                {
                    "name": row[0],
                    "element": row[1],
                    "xyz": [float(value) for value in row[2:]],
                }
                for row in record["atoms"]
            ]
            self.assertEqual(
                VERIFY._coordinate_identity(atoms),
                record["coordinate_sha256"],
            )

    def test_no_fit_rmsd_permutes_identity_but_never_aligns(self) -> None:
        reference = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        swapped = [(2.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
        automorphisms = [(0, 1), (1, 0)]
        self.assertEqual(
            VERIFY._symmetry_no_fit_rmsd(
                swapped,
                reference,
                automorphisms,
            ),
            0.0,
        )
        translated = [(3.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
        self.assertEqual(
            VERIFY._symmetry_no_fit_rmsd(
                translated,
                reference,
                automorphisms,
            ),
            1.0,
        )

    def test_rco_input_coordinates_are_replaced_with_altloc_b(self) -> None:
        sdf = (
            "RCO\n  Test\n\n"
            "  2  0  0  0  0  0  0  0  0  0  0 V2000\n"
            "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0\n"
            "    4.0000    5.0000    6.0000 O   0  0  0  0  0  0\n"
            "M  END\n$$$$\n"
        )
        audit = {
            "reference": {
                "RCO": {
                    "A": [
                        {"name": "C1", "element": "C", "xyz": [1.0, 2.0, 3.0]},
                        {"name": "O2", "element": "O", "xyz": [4.0, 5.0, 6.0]},
                    ],
                    "B": [
                        {"name": "C1", "element": "C", "xyz": [1.5, 2.5, 3.5]},
                        {"name": "O2", "element": "O", "xyz": [4.5, 5.5, 6.5]},
                    ],
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "RCO_A.sdf"
            path.write_text(sdf, encoding="utf-8", newline="\n")
            override, names = VERIFY._rco_altloc_b_coordinate_override(
                path,
                audit,
            )
        self.assertEqual(names, ["C1", "O2"])
        self.assertEqual(override, [(1.5, 2.5, 3.5), (4.5, 5.5, 6.5)])

    def test_imd_resonance_symmetry_is_added_only_after_type_charge_gate(
        self,
    ) -> None:
        pdbqt = (
            "REMARK INDEX MAP 1 1 2 2 3 3 4 4 5 5\n"
            + _atom_line(1, "N1", "N", -0.250)
            + _atom_line(2, "C2", "A", 0.399)
            + _atom_line(3, "N3", "N", -0.250)
            + _atom_line(4, "C4", "A", 0.240)
            + _atom_line(5, "C5", "A", 0.240)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "IMD_A.pdbqt"
            path.write_text(pdbqt, encoding="utf-8", newline="\n")
            topology = {
                "heavy_atom_count": 5,
                "automorphisms": [[0, 1, 2, 3, 4]],
            }
            evidence = VERIFY._add_imd_vina_resonance_symmetry(
                topology,
                path,
            )
        self.assertEqual(
            evidence["permutation_zero_based"],
            [2, 1, 0, 4, 3],
        )
        self.assertIn([2, 1, 0, 4, 3], topology["automorphisms"])

    def test_sah_merge_uses_unique_hetatm_chain_a_2001_serials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protein_pdb = root / "protein.pdb"
            protein_pdb.write_text(
                _atom_line(10, "CA", "C", 0.0),
                encoding="utf-8",
                newline="\n",
            )
            sah_pdbqt = root / "SAH.pdbqt"
            sah_pdbqt.write_text(
                _atom_line(1, "C1", "C", 0.1)
                + _atom_line(2, "N1", "N", -0.1),
                encoding="utf-8",
                newline="\n",
            )

            def fake_preparation(command, **_kwargs):
                protein_output = Path(command[command.index("-p") + 1])
                json_output = Path(command[command.index("-j") + 1])
                protein_output.write_text(
                    _atom_line(10, "CA", "C", 0.0),
                    encoding="utf-8",
                    newline="\n",
                )
                json_output.write_text("{}\n", encoding="utf-8")
                return {
                    "command": command,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                }

            with mock.patch.object(
                VERIFY,
                "_run_preparation",
                side_effect=fake_preparation,
            ):
                receptor, evidence = VERIFY._prepare_receptor(
                    Path("python.exe"),
                    protein_pdb,
                    sah_pdbqt,
                    root,
                    1,
                )
            lines = receptor.read_text(encoding="utf-8").splitlines()
        sah_lines = lines[-2:]
        self.assertEqual([int(line[6:11]) for line in lines], [10, 11, 12])
        self.assertTrue(all(line.startswith("HETATM") for line in sah_lines))
        self.assertTrue(all(line[17:20] == "SAH" for line in sah_lines))
        self.assertTrue(all(line[21:22] == "A" for line in sah_lines))
        self.assertTrue(all(line[22:26] == "2001" for line in sah_lines))
        self.assertEqual(
            evidence["combined_receptor"]["rigid_cofactor"][
                "first_atom_serial"
            ],
            11,
        )

    def test_effective_torsion_contract_is_rco_two_imd_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rco = root / "RCO.pdbqt"
            rco.write_text(
                "ROOT\n"
                + _atom_line(1, "C1", "A", 0.0)
                + "ENDROOT\n"
                + "BRANCH 1 2\n"
                + _atom_line(2, "O1", "OA", -0.2)
                + "ENDBRANCH 1 2\n"
                + "BRANCH 1 3\n"
                + _atom_line(3, "O3", "OA", -0.2)
                + "ENDBRANCH 1 3\n"
                + "TORSDOF 2\n",
                encoding="utf-8",
                newline="\n",
            )
            imd = root / "IMD.pdbqt"
            imd.write_text(
                "ROOT\n"
                + _atom_line(1, "N1", "N", -0.1)
                + "ENDROOT\nTORSDOF 0\n",
                encoding="utf-8",
                newline="\n",
            )
            self.assertEqual(
                VERIFY._pdbqt_torsion_counts(rco),
                {"branch_count": 2, "torsdof": 2},
            )
            self.assertEqual(
                VERIFY._pdbqt_torsion_counts(imd),
                {"branch_count": 0, "torsdof": 0},
            )

    def test_top5_is_diagnostic_while_top20_sampling_is_hard_gate(self) -> None:
        primary = [
            _scientific_run("seed_1", 12345, 15, 0.00, 0.00),
            _scientific_run("seed_2", 23456, 12, 0.05, 0.03),
            _scientific_run("seed_3", 34567, 14, 0.10, 0.06),
        ]
        repeat = _scientific_run(
            "seed_1_repeat",
            12345,
            15,
            0.00,
            0.00,
            repeat_of="seed_1",
        )
        repeat["scores"] = primary[0]["scores"]
        repeat["canonical_science_payload"] = primary[0][
            "canonical_science_payload"
        ]
        repeat["canonical_science_payload_sha256"] = primary[0][
            "canonical_science_payload_sha256"
        ]
        thresholds = {
            "maximum_per_member_rmsd_angstrom": 2.0,
            "top_n": 5,
            "minimum_top_n_seed_recoveries": 2,
            "maximum_best_affinity_range_kcal_mol": 1.0,
            "maximum_cross_seed_member_rmsd_angstrom": 2.0,
        }
        topologies = {
            "RCO": {"heavy_atom_count": 1, "automorphisms": [[0]]},
            "IMD": {"heavy_atom_count": 1, "automorphisms": [[0]]},
        }
        result = VERIFY._evaluate_scientific_acceptance(
            [*primary, repeat],
            topologies,
            thresholds,
        )
        self.assertTrue(result["all_scientific_gates_passed"])
        self.assertFalse(result["ranking_target_met"])
        self.assertTrue(result["ranking_diagnostic_only"])
        self.assertEqual(
            [item["selected_mode"] for item in result["distinct_seeds"]],
            [15, 12, 14],
        )

        repeat.pop("raw_output")
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as missing_raw:
            VERIFY._evaluate_scientific_acceptance(
                [*primary, repeat],
                topologies,
                thresholds,
            )
        self.assertEqual(
            missing_raw.exception.code,
            "MULTIPLE_LIGAND_4DM3_REPEATABILITY_EVIDENCE_MISSING",
        )
        repeat["raw_output"] = {
            "size_bytes": 100,
            "sha256": "b" * 64,
        }
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as changed_raw:
            VERIFY._evaluate_scientific_acceptance(
                [*primary, repeat],
                topologies,
                thresholds,
            )
        self.assertEqual(
            changed_raw.exception.code,
            "MULTIPLE_LIGAND_4DM3_REPEATABILITY_FAILED",
        )
        repeat["raw_output"] = primary[0]["raw_output"]
        primary[2]["models"] = []
        with self.assertRaises(
            VERIFY.MultipleLigand4dm3AcceptanceError
        ) as raised:
            VERIFY._evaluate_scientific_acceptance(
                [*primary, repeat],
                topologies,
                thresholds,
            )
        self.assertEqual(
            raised.exception.code,
            "MULTIPLE_LIGAND_4DM3_CRYSTAL_POSE_NOT_RECOVERED",
        )

    def test_project_artifact_snapshot_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "runs" / "run_001" / "scores.csv"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"mode,score\n1,-9.2\n")
            record = {
                "relative_path": "runs/run_001/scores.csv",
                "size_bytes": artifact.stat().st_size,
                "sha256": VERIFY._sha256(artifact),
            }
            evidence = VERIFY._audit_project_snapshot(
                root,
                record,
                label="scores",
                expected_relative_path="runs/run_001/scores.csv",
            )
            self.assertEqual(evidence["sha256"], record["sha256"])
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as missing:
                VERIFY._audit_project_snapshot(
                    root,
                    None,
                    label="scores",
                    expected_relative_path="runs/run_001/scores.csv",
                )
            self.assertEqual(
                missing.exception.code,
                "MULTIPLE_LIGAND_4DM3_ARTIFACT_ATTESTATION_MISSING",
            )
            artifact.write_bytes(b"tampered\n")
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as changed:
                VERIFY._audit_project_snapshot(
                    root,
                    record,
                    label="scores",
                    expected_relative_path="runs/run_001/scores.csv",
                )
            self.assertEqual(
                changed.exception.code,
                "MULTIPLE_LIGAND_4DM3_ARTIFACT_HASH_MISMATCH",
            )

    def test_raw_output_evidence_handles_normalized_and_unchanged_runs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "runs" / "run_001"
            run_root.mkdir(parents=True)
            vina = root / "vina.exe"
            vina.write_bytes(b"pinned-vina")
            vina_identity = {
                "path": str(vina),
                "version": "1.2.7",
                "size_bytes": vina.stat().st_size,
                "sha256": VERIFY._sha256(vina),
            }

            def snapshot(filename: str, payload: bytes):
                path = run_root / filename
                path.write_bytes(payload)
                return {
                    "relative_path": f"runs/run_001/{filename}",
                    "size_bytes": path.stat().st_size,
                    "sha256": VERIFY._sha256(path),
                }

            artifacts = {
                "output": snapshot("out.pdbqt", b"normalized-output\n"),
                "scores": snapshot("scores.csv", b"mode,score\n1,-9.2\n"),
                "joint_poses": snapshot("joint_poses.json", b"{}\n"),
                "report": snapshot(
                    "multi_ligand_report.md",
                    b"# report\n",
                ),
            }
            output = artifacts["output"]
            unchanged = {
                "artifacts": artifacts,
                "execution_vina": vina_identity,
                "output_normalization": {
                    "status": "not_required",
                    "changed": False,
                    "raw_output_file": "",
                    "source_size_bytes": output["size_bytes"],
                    "source_sha256": output["sha256"],
                    "normalized_size_bytes": output["size_bytes"],
                    "normalized_sha256": output["sha256"],
                },
            }
            evidence = VERIFY._audit_finished_run_artifacts(
                root,
                "run_001",
                unchanged,
                expected_vina_path=vina,
                expected_vina_identity=vina_identity,
            )
            self.assertFalse(evidence["raw_output"]["archived"])
            self.assertEqual(
                evidence["raw_output"]["relative_path"],
                output["relative_path"],
            )

            raw = snapshot(
                "out.vina_raw.pdbqt",
                b"raw\x00output\n",
            )
            normalized = {
                **unchanged,
                "artifacts": {
                    **artifacts,
                    "out_vina_raw": raw,
                },
                "output_normalization": {
                    "status": "normalized",
                    "changed": True,
                    "raw_output_file": raw["relative_path"],
                    "source_size_bytes": raw["size_bytes"],
                    "source_sha256": raw["sha256"],
                    "normalized_size_bytes": output["size_bytes"],
                    "normalized_sha256": output["sha256"],
                },
            }
            evidence = VERIFY._audit_finished_run_artifacts(
                root,
                "run_001",
                normalized,
                expected_vina_path=vina,
                expected_vina_identity=vina_identity,
            )
            self.assertTrue(evidence["raw_output"]["archived"])
            self.assertEqual(evidence["raw_output"]["sha256"], raw["sha256"])

    def test_repeatability_compares_science_not_run_paths_or_timestamps(
        self,
    ) -> None:
        scores = [
            {
                "mode": 1,
                "joint_affinity_kcal_mol": -9.2,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
                "pose_available": True,
                "score_is_joint": True,
            }
        ]
        models = [
            {
                "mode": 1,
                "joint_affinity_kcal_mol": -9.2,
                "rmsd_lb": 0.0,
                "rmsd_ub": 0.0,
                "run_id": "ignored",
                "members": [
                    {
                        "member_index": 1,
                        "file": "runs/run_001/pose.pdbqt",
                        "heavy_coordinates": [(1.0, 2.0, 3.0)],
                    }
                ],
            }
        ]
        left = VERIFY._canonical_science_payload(
            scores=scores,
            models=models,
        )
        models[0]["run_id"] = "changed"
        models[0]["members"][0]["file"] = "runs/run_004/pose.pdbqt"
        right = VERIFY._canonical_science_payload(
            scores=scores,
            models=models,
        )
        self.assertEqual(left, right)

    def test_failure_restores_settings_and_removes_temporary_project(
        self,
    ) -> None:
        manifest = {
            "fixture_id": "multiple_ligands_4dm3_external",
            "source_files": {},
            "scientific_boundaries": list(
                VERIFY.EXPECTED_SCIENTIFIC_BOUNDARIES
            ),
        }
        configured_error = VERIFY.MultipleLigand4dm3AcceptanceError(
            "TEST_TOOL_FAILURE",
            "synthetic tool failure",
        )

        def fake_verify_sources(
            _manifest,
            _paths,
            *,
            snapshot_root,
        ):
            snapshot_root.mkdir()
            snapshots = {}
            for key in VERIFY.SOURCE_KEYS:
                path = snapshot_root / f"{key}.dat"
                path.write_bytes(b"verified")
                snapshots[key] = str(path)
            return {
                "network_or_download_used": False,
                "files": {},
                "snapshot_paths": snapshots,
            }

        with (
            mock.patch.object(VERIFY, "_load_manifest", return_value=manifest),
            mock.patch.object(
                VERIFY,
                "_resolve_source_paths",
                return_value={},
            ),
            mock.patch.object(
                VERIFY,
                "_verify_sources",
                side_effect=fake_verify_sources,
            ),
            mock.patch.object(
                VERIFY,
                "_verify_toolchain",
                side_effect=configured_error,
            ),
            mock.patch.dict(
                os.environ,
                {VERIFY.SETTINGS_ENV_VAR: "original-settings.json"},
                clear=False,
            ),
        ):
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY.verify_multiple_ligands_4dm3(
                    ".",
                    python_executable=SCRIPT_PATH,
                    vina_executable=SCRIPT_PATH,
                )
            self.assertEqual(
                os.environ[VERIFY.SETTINGS_ENV_VAR],
                "original-settings.json",
            )
        cleanup = raised.exception.details["temporary_cleanup"]
        self.assertTrue(cleanup["cleanup_called"])
        self.assertTrue(cleanup["removed"])
        self.assertEqual(cleanup["error"], "")
        self.assertFalse(Path(cleanup["path"]).exists())
        self.assertEqual(
            raised.exception.steps["exact_scientific_toolchain"]["error"][
                "code"
            ],
            "TEST_TOOL_FAILURE",
        )


if __name__ == "__main__":
    unittest.main()
