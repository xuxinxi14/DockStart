from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.flexible_movement import (  # noqa: E402
    analyze_flexible_movement,
    analyze_flexible_movement_texts,
    canonical_flexible_movement_json,
    flexible_movement_sha256,
)


def _atom(
    serial: int,
    atom_name: str,
    atom_type: str,
    x: float,
    y: float,
    z: float,
    *,
    residue_name: str,
    chain_id: str,
    residue_number: int,
    insertion_code: str = "",
    alternate_location: str = "",
    partial_charge: float = 0.0,
    record: str = "ATOM",
) -> str:
    return (
        f"{record:<6}{serial:>5} "
        f"{atom_name:<4}"
        f"{alternate_location:1}"
        f"{residue_name:>3}"
        f" "
        f"{chain_id:1}"
        f"{residue_number:>4}"
        f"{insertion_code:1}"
        f"   "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}"
        f"{1.0:>6.2f}{0.0:>6.2f}"
        f"    "
        f"{partial_charge:>6.3f}"
        f" "
        f"{atom_type:>2}"
    )


def _ligand(
    coordinates: dict[int, tuple[float, float, float]],
    *,
    first_name: str = "C1",
    second_charge: float = -0.2,
) -> str:
    atoms = [
        _atom(
            1,
            first_name,
            "C",
            *coordinates[1],
            residue_name="LIG",
            chain_id="L",
            residue_number=1,
            partial_charge=0.1,
        ),
        _atom(
            2,
            "O1",
            "OA",
            *coordinates[2],
            residue_name="LIG",
            chain_id="L",
            residue_number=1,
            partial_charge=second_charge,
        ),
        _atom(
            3,
            "H1",
            "HD",
            *coordinates[3],
            residue_name="LIG",
            chain_id="L",
            residue_number=1,
            partial_charge=0.1,
        ),
    ]
    return "\n".join(["ROOT", *atoms, "ENDROOT", "TORSDOF 0"]) + "\n"


def _tyr_block(
    coordinates: dict[int, tuple[float, float, float]],
    *,
    insertion_code: str = "A",
    atom_insertion_code: str = "",
    end_insertion_code: str | None = None,
) -> str:
    end_code = insertion_code if end_insertion_code is None else end_insertion_code
    atoms = [
        _atom(
            10,
            "CA",
            "C",
            *coordinates[10],
            residue_name="TYR",
            chain_id="A",
            residue_number=221,
            insertion_code=atom_insertion_code,
        ),
        _atom(
            11,
            "CB",
            "C",
            *coordinates[11],
            residue_name="TYR",
            chain_id="A",
            residue_number=221,
            insertion_code=atom_insertion_code,
        ),
        _atom(
            13,
            "H1",
            "HD",
            *coordinates[13],
            residue_name="TYR",
            chain_id="A",
            residue_number=221,
            insertion_code=atom_insertion_code,
        ),
        _atom(
            14,
            "G0",
            "G0",
            *coordinates[14],
            residue_name="TYR",
            chain_id="A",
            residue_number=221,
            insertion_code=atom_insertion_code,
        ),
    ]
    branch_atom = _atom(
        12,
        "OH",
        "OA",
        *coordinates[12],
        residue_name="TYR",
        chain_id="A",
        residue_number=221,
        insertion_code=atom_insertion_code,
        partial_charge=-0.3,
    )
    return (
        f"BEGIN_RES TYR A 221{insertion_code}\n"
        + "\n".join(
            [
                "ROOT",
                *atoms,
                "ENDROOT",
                "BRANCH 11 12",
                branch_atom,
                "ENDBRANCH 11 12",
            ]
        )
        + f"\nEND_RES TYR A 221{end_code}\n"
    )


def _lys_block(
    coordinates: dict[int, tuple[float, float, float]],
) -> str:
    ca = _atom(
        20,
        "CA",
        "C",
        *coordinates[20],
        residue_name="LYS",
        chain_id="B",
        residue_number=9,
    )
    nz = _atom(
        21,
        "NZ",
        "N",
        *coordinates[21],
        residue_name="LYS",
        chain_id="B",
        residue_number=9,
        partial_charge=-0.1,
    )
    return (
        "BEGIN_RES LYS B 9\n"
        f"ROOT\n{ca}\nENDROOT\n"
        f"BRANCH 20 21\n{nz}\nENDBRANCH 20 21\n"
        "END_RES LYS B 9\n"
    )


LIGAND_INPUT = _ligand(
    {
        1: (1.0, 0.0, 0.0),
        2: (-1.0, 0.0, 0.0),
        3: (0.0, 1.0, 0.0),
    }
)
TYR_INPUT_COORDINATES = {
    10: (0.0, 0.0, 0.0),
    11: (1.0, 0.0, 0.0),
    12: (2.0, 0.0, 0.0),
    13: (0.0, 1.0, 0.0),
    14: (0.0, 2.0, 0.0),
}
LYS_INPUT_COORDINATES = {
    20: (5.0, 0.0, 0.0),
    21: (6.0, 0.0, 0.0),
}
FLEX_INPUT = _tyr_block(TYR_INPUT_COORDINATES) + _lys_block(
    LYS_INPUT_COORDINATES
)


def _mode(
    mode: int,
    ligand_coordinates: dict[int, tuple[float, float, float]],
    tyr_coordinates: dict[int, tuple[float, float, float]],
    lys_coordinates: dict[int, tuple[float, float, float]],
    *,
    ligand_first_name: str = "C1",
    ligand_second_charge: float = -0.2,
    tyr_insertion_code: str = "A",
    tyr_end_insertion_code: str | None = None,
) -> str:
    return (
        f"MODEL {mode}\n"
        f"REMARK VINA RESULT: {-7.0 + mode:.3f} 0.000 0.000\n"
        + _ligand(
            ligand_coordinates,
            first_name=ligand_first_name,
            second_charge=ligand_second_charge,
        )
        + _tyr_block(
            tyr_coordinates,
            insertion_code=tyr_insertion_code,
            end_insertion_code=tyr_end_insertion_code,
        )
        + _lys_block(lys_coordinates)
        + "ENDMDL\n"
    )


MODE_1_LIGAND = {
    1: (1.0, 2.0, 0.0),
    2: (-1.0, 2.0, 0.0),
    3: (99.0, 99.0, 99.0),
}
MODE_2_LIGAND = {
    1: (0.0, 1.0, 0.0),
    2: (0.0, -1.0, 0.0),
    3: (-99.0, -99.0, -99.0),
}
MODE_1_TYR = {
    10: (10.0, 0.0, 0.0),
    11: (1.0, 0.0, 0.0),
    12: (2.0, 2.0, 0.0),
    13: (99.0, 99.0, 99.0),
    14: (99.0, 99.0, 99.0),
}
MODE_2_TYR = {
    10: (20.0, 0.0, 0.0),
    11: (1.0, 1.0, 0.0),
    12: (2.0, 3.0, 0.0),
    13: (-99.0, -99.0, -99.0),
    14: (-99.0, -99.0, -99.0),
}
MODE_1_LYS = {
    20: (15.0, 0.0, 0.0),
    21: (6.0, 0.0, 2.0),
}
MODE_2_LYS = {
    20: (25.0, 0.0, 0.0),
    21: (6.0, 0.0, 4.0),
}
OUTPUT = _mode(
    1,
    MODE_1_LIGAND,
    MODE_1_TYR,
    MODE_1_LYS,
) + _mode(
    2,
    MODE_2_LIGAND,
    MODE_2_TYR,
    MODE_2_LYS,
)


class FlexibleMovementSuccessTests(unittest.TestCase):
    def test_reports_ligand_and_each_residue_separately_for_every_mode(self) -> None:
        result = analyze_flexible_movement_texts(
            LIGAND_INPUT,
            FLEX_INPUT,
            OUTPUT,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["schema_id"], "dockstart.flexible_movement.v1")
        self.assertEqual(result["mode_count"], 2)
        self.assertFalse(result["alignment_applied"])
        self.assertTrue(result["exclude_flexible_ca_root"])
        self.assertEqual(result["input_contract"]["ligand_atom_count"], 3)
        self.assertEqual(result["input_contract"]["ligand_heavy_atom_count"], 2)
        self.assertEqual(result["input_contract"]["flexible_residue_count"], 2)

        first = result["modes"][0]
        self.assertEqual(first["ligand"]["heavy_atom_count"], 2)
        self.assertEqual(first["ligand"]["excluded_atom_counts"]["hydrogen"], 1)
        self.assertEqual(
            first["ligand"]["heavy_atom_rmsd_no_alignment_angstrom"],
            2.0,
        )
        self.assertEqual(first["ligand"]["centroid_displacement_angstrom"], 2.0)

        tyr = first["flexible_residues"][0]
        lys = first["flexible_residues"][1]
        self.assertEqual(tyr["residue"]["canonical_id"], "A:221:A")
        self.assertEqual(tyr["residue"]["insertion_code"], "A")
        self.assertEqual(tyr["movement"]["heavy_atom_count"], 2)
        self.assertEqual(tyr["movement"]["excluded_atom_counts"]["ca_root"], 1)
        self.assertEqual(tyr["movement"]["excluded_atom_counts"]["hydrogen"], 1)
        self.assertEqual(tyr["movement"]["excluded_atom_counts"]["pseudo"], 1)
        self.assertAlmostEqual(
            tyr["movement"]["heavy_atom_rmsd_no_alignment_angstrom"],
            math.sqrt(2),
            places=6,
        )
        self.assertEqual(tyr["movement"]["max_displacement_atom"]["atom_name"], "OH")
        self.assertEqual(lys["residue"]["canonical_id"], "B:9")
        self.assertEqual(lys["movement"]["heavy_atom_count"], 1)
        self.assertEqual(
            lys["movement"]["heavy_atom_rmsd_no_alignment_angstrom"],
            2.0,
        )

        second = result["modes"][1]
        self.assertAlmostEqual(
            second["ligand"]["heavy_atom_rmsd_no_alignment_angstrom"],
            math.sqrt(2),
            places=6,
        )
        second_tyr = second["flexible_residues"][0]["movement"]
        self.assertAlmostEqual(
            second_tyr["heavy_atom_rmsd_no_alignment_angstrom"],
            math.sqrt(5),
            places=6,
        )
        self.assertEqual(second_tyr["mean_heavy_atom_displacement_angstrom"], 2.0)
        self.assertEqual(second_tyr["centroid_displacement_angstrom"], 2.0)
        self.assertEqual(
            second["flexible_residues"][1]["movement"][
                "heavy_atom_rmsd_no_alignment_angstrom"
            ],
            4.0,
        )

    def test_can_include_ca_root_without_mixing_it_into_ligand(self) -> None:
        result = analyze_flexible_movement_texts(
            LIGAND_INPUT,
            FLEX_INPUT,
            OUTPUT,
            exclude_flexible_ca=False,
        )

        self.assertTrue(result["ok"], result)
        first = result["modes"][0]
        self.assertEqual(first["ligand"]["heavy_atom_count"], 2)
        self.assertEqual(first["flexible_residues"][0]["movement"]["heavy_atom_count"], 3)
        self.assertEqual(
            first["flexible_residues"][0]["movement"]["excluded_atom_counts"][
                "ca_root"
            ],
            0,
        )
        self.assertEqual(first["flexible_residues"][1]["movement"]["heavy_atom_count"], 2)
        self.assertFalse(result["exclude_flexible_ca_root"])

    def test_file_api_records_exact_hashes_and_matches_text_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "冻结 ligand.pdbqt"
            flex = root / "receptor_flex.pdbqt"
            output = root / "out.pdbqt"
            ligand.write_bytes(LIGAND_INPUT.encode("utf-8"))
            flex.write_bytes(FLEX_INPUT.encode("utf-8"))
            output.write_bytes(OUTPUT.encode("utf-8"))

            file_result = analyze_flexible_movement(ligand, flex, output)

        text_result = analyze_flexible_movement_texts(
            LIGAND_INPUT,
            FLEX_INPUT,
            OUTPUT,
        )
        self.assertEqual(file_result, text_result)
        self.assertEqual(
            file_result["source_evidence"]["ligand_input"]["size_bytes"],
            len(LIGAND_INPUT.encode("utf-8")),
        )
        self.assertEqual(
            len(file_result["source_evidence"]["vina_output"]["sha256"]),
            64,
        )

    def test_canonical_json_and_sha256_are_stable_and_strict(self) -> None:
        result = analyze_flexible_movement_texts(
            LIGAND_INPUT,
            FLEX_INPUT,
            OUTPUT,
        )

        first = canonical_flexible_movement_json(result)
        second = canonical_flexible_movement_json(dict(reversed(list(result.items()))))
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), result)
        self.assertEqual(flexible_movement_sha256(result), flexible_movement_sha256(result))
        self.assertEqual(len(flexible_movement_sha256(result)), 64)
        with self.assertRaises(ValueError):
            canonical_flexible_movement_json({"bad": float("nan")})


class FlexibleMovementFailureTests(unittest.TestCase):
    def assert_error(
        self,
        ligand: str,
        flex: str,
        output: str,
        expected_code: str,
    ) -> None:
        result = analyze_flexible_movement_texts(ligand, flex, output)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], expected_code, result)

    def test_rejects_ligand_atom_identity_charge_and_count_changes(self) -> None:
        changed_name = OUTPUT.replace("C1   LIG", "N1   LIG", 1)
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            changed_name,
            "FLEX_MOVEMENT_OUTPUT_ATOM_IDENTITY_MISMATCH",
        )

        changed_charge = OUTPUT.replace("-0.200 OA", "-0.500 OA", 1)
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            changed_charge,
            "FLEX_MOVEMENT_OUTPUT_ATOM_IDENTITY_MISMATCH",
        )

        removed_atom = OUTPUT.replace(
            _atom(
                2,
                "O1",
                "OA",
                *MODE_1_LIGAND[2],
                residue_name="LIG",
                chain_id="L",
                residue_number=1,
                partial_charge=-0.2,
            )
            + "\n",
            "",
            1,
        )
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            removed_atom,
            "FLEX_MOVEMENT_OUTPUT_ATOM_SET_MISMATCH",
        )

    def test_rejects_residue_identity_insertion_code_and_atom_set_changes(self) -> None:
        changed_residue = OUTPUT.replace("BEGIN_RES TYR A 221A", "BEGIN_RES TYR A 221B", 1)
        changed_residue = changed_residue.replace(
            "END_RES TYR A 221A",
            "END_RES TYR A 221B",
            1,
        )
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            changed_residue,
            "FLEX_MOVEMENT_OUTPUT_RESIDUE_SET_MISMATCH",
        )

        conflicting_atom = OUTPUT.replace("TYR A 221    ", "TYR A 221B   ", 1)
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            conflicting_atom,
            "FLEX_MOVEMENT_OUTPUT_RESIDUE_ATOM_CONFLICT",
        )

        duplicate_flex_atom = FLEX_INPUT.replace(
            "ENDROOT\nBRANCH 11 12",
            (
                "\n"
                + _atom(
                    11,
                    "XX",
                    "C",
                    0.0,
                    0.0,
                    0.0,
                    residue_name="TYR",
                    chain_id="A",
                    residue_number=221,
                )
                + "\nENDROOT\nBRANCH 11 12"
            ),
            1,
        )
        self.assert_error(
            LIGAND_INPUT,
            duplicate_flex_atom,
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_ATOM_ID_DUPLICATE",
        )

    def test_rejects_non_finite_coordinates_in_every_role(self) -> None:
        bad_ligand = LIGAND_INPUT.replace("   1.000", "     nan", 1)
        self.assert_error(
            bad_ligand,
            FLEX_INPUT,
            OUTPUT,
            "FLEX_MOVEMENT_LIGAND_INPUT_ATOM_INVALID",
        )
        bad_flex = FLEX_INPUT.replace("   0.000", "     inf", 1)
        self.assert_error(
            LIGAND_INPUT,
            bad_flex,
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_ATOM_INVALID",
        )
        bad_output = OUTPUT.replace("   1.000", "     nan", 1)
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            bad_output,
            "FLEX_MOVEMENT_OUTPUT_ATOM_INVALID",
        )

    def test_rejects_output_model_structure_anomalies(self) -> None:
        cases = {
            "unterminated": (
                OUTPUT.rsplit("ENDMDL\n", 1)[0],
                "FLEX_MOVEMENT_OUTPUT_MODEL_UNTERMINATED",
            ),
            "skipped_mode": (
                OUTPUT.replace("MODEL 2", "MODEL 3", 1),
                "FLEX_MOVEMENT_OUTPUT_MODEL_SEQUENCE_INVALID",
            ),
            "nested": (
                OUTPUT.replace("REMARK VINA RESULT:", "MODEL 8\nREMARK VINA RESULT:", 1),
                "FLEX_MOVEMENT_OUTPUT_MODEL_NESTED",
            ),
            "outside": (
                _atom(
                    99,
                    "C9",
                    "C",
                    0.0,
                    0.0,
                    0.0,
                    residue_name="LIG",
                    chain_id="L",
                    residue_number=1,
                )
                + "\n"
                + OUTPUT,
                "FLEX_MOVEMENT_OUTPUT_RECORD_OUTSIDE_MODEL",
            ),
        }
        for name, (output, expected_code) in cases.items():
            with self.subTest(name=name):
                self.assert_error(
                    LIGAND_INPUT,
                    FLEX_INPUT,
                    output,
                    expected_code,
                )

    def test_rejects_nested_mismatched_and_outside_flex_boundaries(self) -> None:
        nested = FLEX_INPUT.replace(
            "ROOT\n",
            "BEGIN_RES PHE C 8\nROOT\n",
            1,
        )
        self.assert_error(
            LIGAND_INPUT,
            nested,
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_BOUNDARY_NESTED",
        )

        mismatched = FLEX_INPUT.replace("END_RES TYR A 221A", "END_RES TYR A 221B", 1)
        self.assert_error(
            LIGAND_INPUT,
            mismatched,
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_BOUNDARY_MISMATCH",
        )

        outside_atom = (
            _atom(
                99,
                "C9",
                "C",
                0.0,
                0.0,
                0.0,
                residue_name="TYR",
                chain_id="A",
                residue_number=221,
            )
            + "\n"
            + FLEX_INPUT
        )
        self.assert_error(
            LIGAND_INPUT,
            outside_atom,
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_ATOM_OUTSIDE_RESIDUE",
        )

    def test_rejects_topology_changes_and_missing_comparable_atoms(self) -> None:
        changed_topology = OUTPUT.replace("TORSDOF 0", "TORSDOF 1", 1)
        self.assert_error(
            LIGAND_INPUT,
            FLEX_INPUT,
            changed_topology,
            "FLEX_MOVEMENT_OUTPUT_TOPOLOGY_MISMATCH",
        )

        hydrogen_only_ligand = (
            "ROOT\n"
            + _atom(
                1,
                "H1",
                "HD",
                0.0,
                0.0,
                0.0,
                residue_name="LIG",
                chain_id="L",
                residue_number=1,
            )
            + "\nENDROOT\nTORSDOF 0\n"
        )
        hydrogen_output = (
            "MODEL 1\n"
            + hydrogen_only_ligand
            + _tyr_block(MODE_1_TYR)
            + _lys_block(MODE_1_LYS)
            + "ENDMDL\n"
        )
        self.assert_error(
            hydrogen_only_ligand,
            FLEX_INPUT,
            hydrogen_output,
            "FLEX_MOVEMENT_NO_COMPARABLE_HEAVY_ATOMS",
        )

    def test_rejects_models_in_inputs_invalid_options_and_control_bytes(self) -> None:
        self.assert_error(
            "MODEL 1\n" + LIGAND_INPUT + "ENDMDL\n",
            FLEX_INPUT,
            OUTPUT,
            "FLEX_MOVEMENT_LIGAND_INPUT_MODEL_FORBIDDEN",
        )
        self.assert_error(
            LIGAND_INPUT,
            "MODEL 1\n" + FLEX_INPUT + "ENDMDL\n",
            OUTPUT,
            "FLEX_MOVEMENT_FLEX_INPUT_MODEL_FORBIDDEN",
        )
        invalid_option = analyze_flexible_movement_texts(
            LIGAND_INPUT,
            FLEX_INPUT,
            OUTPUT,
            exclude_flexible_ca=1,  # type: ignore[arg-type]
        )
        self.assertEqual(
            invalid_option["error"]["code"],
            "FLEX_MOVEMENT_OPTION_INVALID",
        )
        control = analyze_flexible_movement_texts(
            LIGAND_INPUT + "\x00",
            FLEX_INPUT,
            OUTPUT,
        )
        self.assertEqual(
            control["error"]["code"],
            "FLEX_MOVEMENT_LIGAND_INPUT_TEXT_INVALID",
        )

    def test_file_api_rejects_wrong_suffix_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "ligand.txt"
            flex = root / "flex.pdbqt"
            output = root / "missing.pdbqt"
            ligand.write_text(LIGAND_INPUT, encoding="utf-8")
            flex.write_text(FLEX_INPUT, encoding="utf-8")

            wrong_suffix = analyze_flexible_movement(ligand, flex, output)
            self.assertEqual(
                wrong_suffix["error"]["code"],
                "FLEX_MOVEMENT_LIGAND_INPUT_FORMAT_INVALID",
            )

            ligand_pdbqt = root / "ligand.pdbqt"
            ligand_pdbqt.write_text(LIGAND_INPUT, encoding="utf-8")
            missing = analyze_flexible_movement(ligand_pdbqt, flex, output)
            self.assertEqual(
                missing["error"]["code"],
                "FLEX_MOVEMENT_OUTPUT_NOT_FOUND",
            )


if __name__ == "__main__":
    unittest.main()
