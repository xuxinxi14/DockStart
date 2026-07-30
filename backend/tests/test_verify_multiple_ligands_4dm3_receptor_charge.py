from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_multiple_ligands_4dm3.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_multiple_ligands_4dm3_receptor_charge",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _atom_line(
    serial: int,
    name: str,
    atom_type: str,
    charge: float,
    *,
    record_type: str = "ATOM",
    resname: str = "ALA",
    chain: str = "A",
    resnum: int = 12,
    insertion_code: str = "",
    xyz: tuple[float, float, float] = (1.0, 2.0, 3.0),
    coordinate_decimals: int = 3,
) -> str:
    x, y, z = xyz
    return (
        f"{record_type:<6}{serial:5d} {name:>4s} {resname:>3s} "
        f"{chain:1s}{resnum:4d}{insertion_code:1s}   "
        f"{x:8.{coordinate_decimals}f}"
        f"{y:8.{coordinate_decimals}f}"
        f"{z:8.{coordinate_decimals}f}"
        f"  1.00  0.00    {charge:6.3f} {atom_type:<2s}\n"
    )


class MultipleLigand4dm3ReceptorChargeTests(unittest.TestCase):
    def test_pdbqt_parser_freezes_fixed_column_identity(self) -> None:
        text = _atom_line(
            7,
            "OG1",
            "OA",
            -0.321,
            record_type="HETATM",
            resname="THR",
            chain="B",
            resnum=42,
            insertion_code="A",
        )
        record = VERIFY._pdbqt_atom_records_from_text(
            text,
            label="identity-test",
        )[0]
        self.assertEqual(record["record_type"], "HETATM")
        self.assertEqual(record["name"], "OG1")
        self.assertEqual(record["resname"], "THR")
        self.assertEqual(record["chain"], "B")
        self.assertEqual(record["resnum"], 42)
        self.assertEqual(record["insertion_code"], "A")
        self.assertEqual(record["charge_milliunits"], -321)

    def test_receptor_heavy_atom_bijection_allows_added_hydrogen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "protein.pdb"
            prepared = root / "protein.pdbqt"
            source.write_text(
                _atom_line(
                    1,
                    "CA",
                    "C",
                    0.0,
                    xyz=(1.2344, 2.0, 3.0),
                    coordinate_decimals=4,
                )
                + _atom_line(
                    2,
                    "O",
                    "O",
                    0.0,
                    xyz=(4.0, 5.0, 6.0),
                ),
                encoding="utf-8",
                newline="\n",
            )
            prepared.write_text(
                _atom_line(
                    20,
                    "O",
                    "OA",
                    -0.300,
                    xyz=(4.0, 5.0, 6.0),
                )
                + _atom_line(
                    10,
                    "CA",
                    "C",
                    0.200,
                    xyz=(1.234, 2.0, 3.0),
                )
                + _atom_line(
                    30,
                    "H",
                    "HD",
                    0.100,
                    xyz=(1.5, 2.0, 3.0),
                ),
                encoding="utf-8",
                newline="\n",
            )
            evidence = VERIFY._audit_receptor_heavy_atom_bijection(
                source,
                prepared,
            )

        self.assertEqual(evidence["source_heavy_atom_count"], 2)
        self.assertEqual(evidence["prepared_heavy_atom_count"], 2)
        self.assertEqual(evidence["prepared_hydrogen_atom_count"], 1)
        self.assertEqual(evidence["missing_heavy_atom_count"], 0)
        self.assertEqual(evidence["extra_heavy_atom_count"], 0)
        self.assertAlmostEqual(
            evidence["maximum_absolute_axis_delta_angstrom"],
            0.0004,
        )
        self.assertEqual(
            evidence["source_identity_sha256"],
            evidence["prepared_identity_sha256"],
        )
        self.assertEqual(len(evidence["correspondence_sha256"]), 64)

    def test_receptor_heavy_atom_identity_and_coordinate_tampering_fail(
        self,
    ) -> None:
        source_text = (
            _atom_line(1, "CA", "C", 0.0)
            + _atom_line(2, "O", "O", 0.0, xyz=(4.0, 5.0, 6.0))
        )
        prepared_cases = {
            "missing": _atom_line(10, "CA", "C", 0.0),
            "extra": (
                _atom_line(10, "CA", "C", 0.0)
                + _atom_line(20, "O", "OA", 0.0, xyz=(4.0, 5.0, 6.0))
                + _atom_line(30, "CB", "C", 0.0)
            ),
            "coordinate": (
                _atom_line(10, "CA", "C", 0.0)
                + _atom_line(
                    20,
                    "O",
                    "OA",
                    0.0,
                    xyz=(4.001, 5.0, 6.0),
                )
            ),
        }
        expected_codes = {
            "missing": (
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_"
                "HEAVY_ATOM_BIJECTION_FAILED"
            ),
            "extra": (
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_"
                "HEAVY_ATOM_BIJECTION_FAILED"
            ),
            "coordinate": (
                "MULTIPLE_LIGAND_4DM3_RECEPTOR_COORDINATES_CHANGED"
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "protein.pdb"
            source.write_text(source_text, encoding="utf-8", newline="\n")
            for label, prepared_text in prepared_cases.items():
                with self.subTest(label=label):
                    prepared = root / f"{label}.pdbqt"
                    prepared.write_text(
                        prepared_text,
                        encoding="utf-8",
                        newline="\n",
                    )
                    with self.assertRaises(
                        VERIFY.MultipleLigand4dm3AcceptanceError
                    ) as raised:
                        VERIFY._audit_receptor_heavy_atom_bijection(
                            source,
                            prepared,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        expected_codes[label],
                    )

    def test_rco_imd_and_sah_prepared_charge_contracts(self) -> None:
        cases = {
            "RCO": (
                0,
                _atom_line(1, "C1", "A", 0.125)
                + _atom_line(2, "O1", "OA", -0.125),
            ),
            "IMD": (
                1,
                _atom_line(1, "N1", "N", 0.600)
                + _atom_line(2, "C2", "A", 0.400),
            ),
            "SAH": (
                0,
                _atom_line(1, "C1", "C", -0.250)
                + _atom_line(2, "N1", "N", 0.250),
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, (formal_charge, text) in cases.items():
                with self.subTest(label=label):
                    path = root / f"{label}.pdbqt"
                    path.write_text(text, encoding="utf-8", newline="\n")
                    records = VERIFY._pdbqt_atom_records(path)
                    evidence = VERIFY._audit_prepared_partial_charge(
                        label,
                        {"formal_charge": formal_charge},
                        records,
                        path=path,
                    )
                    self.assertTrue(evidence["passed"])
                    self.assertEqual(
                        evidence["expected_formal_charge_e"],
                        formal_charge,
                    )
                    self.assertEqual(
                        evidence["observed_partial_charge_sum_e"],
                        float(formal_charge),
                    )

    def test_prepared_charge_or_formal_charge_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "RCO.pdbqt"
            path.write_text(
                _atom_line(1, "C1", "A", 0.125)
                + _atom_line(2, "O1", "OA", -0.120),
                encoding="utf-8",
                newline="\n",
            )
            records = VERIFY._pdbqt_atom_records(path)
            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as charge_error:
                VERIFY._audit_prepared_partial_charge(
                    "RCO",
                    {"formal_charge": 0},
                    records,
                    path=path,
                )
            self.assertEqual(
                charge_error.exception.code,
                "MULTIPLE_LIGAND_4DM3_PDBQT_CHARGE_MISMATCH",
            )

            with self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as formal_error:
                VERIFY._audit_prepared_partial_charge(
                    "IMD",
                    {"formal_charge": 0},
                    records,
                    path=path,
                )
            self.assertEqual(
                formal_error.exception.code,
                "MULTIPLE_LIGAND_4DM3_FORMAL_CHARGE_CONTRACT_MISMATCH",
            )

    def test_prepare_ligand_freezes_charge_audit_in_both_evidence_layers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "RCO.sdf"
            source.write_text("fixture\n", encoding="utf-8", newline="\n")

            def fake_preparation(command, **_kwargs):
                if "--add_index_map" in command:
                    output = Path(command[command.index("-o") + 1])
                    output.write_text(
                        "REMARK INDEX MAP 1 1\n"
                        + _atom_line(1, "C1", "A", 0.0)
                        + "TORSDOF 0\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                else:
                    explicit_h_sdf = Path(command[-2])
                    topology_path = Path(command[-1])
                    explicit_h_sdf.write_text(
                        "explicit H\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    topology_path.write_text(
                        json.dumps(
                            {
                                "rdkit_version": "test-rdkit",
                                "heavy_atom_count": 1,
                                "atom_count_with_hydrogens": 1,
                                "explicit_h_conformer_is_3d": True,
                                "formal_charge": 0,
                                "canonical_isomeric_smiles": "C",
                                "source_coordinates": [],
                                "prepared_heavy_coordinates": [],
                                "heavy_coordinate_override_applied": False,
                                "automorphisms": [[0]],
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                return {
                    "command": command,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                }

            with (
                mock.patch.object(
                    VERIFY,
                    "_run_preparation",
                    side_effect=fake_preparation,
                ),
                mock.patch.object(
                    VERIFY,
                    "_validated_versions",
                    return_value={"rdkit": "test-rdkit"},
                ),
            ):
                _path, topology, evidence = VERIFY._prepare_ligand(
                    "RCO",
                    source,
                    Path("python.exe"),
                    root,
                    {},
                )

        self.assertTrue(
            topology["prepared_pdbqt_partial_charge_audit"]["passed"]
        )
        self.assertEqual(
            evidence["pdbqt"]["partial_charge_audit"],
            topology["prepared_pdbqt_partial_charge_audit"],
        )
        self.assertEqual(
            evidence["topology"]["prepared_pdbqt_partial_charge_audit"],
            topology["prepared_pdbqt_partial_charge_audit"],
        )


if __name__ == "__main__":
    unittest.main()
