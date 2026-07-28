from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.pose_comparison import (  # noqa: E402
    compare_local_only_pose_texts,
    compare_local_only_poses,
)


def _atom(
    serial: int,
    name: str,
    atom_type: str,
    x: float,
    y: float,
    z: float,
    *,
    charge: float = 0.0,
    residue: str = "LIG",
    chain: str = "L",
    residue_number: int = 1,
) -> str:
    return (
        f"{'ATOM':<6}{serial:>5} "
        f"{name:<4}"
        f" "
        f"{residue:>3}"
        f" "
        f"{chain:1}"
        f"{residue_number:>4}"
        f" "
        f"   "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}"
        f"{1.0:>6.2f}{0.0:>6.2f}"
        f"    "
        f"{charge:>6.3f}"
        f" "
        f"{atom_type:>2}"
    )


def _pose(
    atoms: list[str],
    *,
    smiles: str = "",
    mapping: list[tuple[int, int]] | None = None,
    wrapped: bool = False,
    extra_lines: list[str] | None = None,
) -> str:
    lines: list[str] = []
    if wrapped:
        lines.append("MODEL 1")
    if smiles:
        lines.append(f"REMARK SMILES {smiles}")
    if mapping is not None:
        pairs = " ".join(f"{left} {right}" for left, right in mapping)
        lines.append(f"REMARK SMILES IDX {pairs}")
    lines.extend(["ROOT", *atoms, "ENDROOT"])
    lines.extend(extra_lines or [])
    lines.append("TORSDOF 0")
    if wrapped:
        lines.append("ENDMDL")
    return "\n".join(lines) + "\n"


class PoseComparisonMetricTests(unittest.TestCase):
    def test_translation_reports_same_value_for_all_four_metrics(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0, charge=0.1),
                _atom(2, "O1", "OA", 2, 0, 0, charge=-0.2),
                _atom(3, "H1", "HD", 2, 1, 0, charge=0.1),
            ]
        )
        optimized_pose = _pose(
            [
                _atom(1, "C1", "C", 1, 2, 2, charge=0.1),
                _atom(2, "O1", "OA", 3, 2, 2, charge=-0.2),
                # Hydrogen motion is deliberately unrelated and must be ignored.
                _atom(3, "H1", "HD", 100, 100, 100, charge=0.1),
            ]
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["heavy_atom_count"], 2)
        self.assertEqual(result["mapping_method"], "pdbqt_serial_full_identity")
        self.assertAlmostEqual(
            result["heavy_atom_rmsd_no_alignment_angstrom"], 3.0, places=6
        )
        self.assertAlmostEqual(
            result["mean_heavy_atom_displacement_angstrom"], 3.0, places=6
        )
        self.assertAlmostEqual(
            result["max_heavy_atom_displacement_angstrom"], 3.0, places=6
        )
        self.assertAlmostEqual(
            result["centroid_displacement_angstrom"], 3.0, places=6
        )
        self.assertEqual(result["excluded_atom_counts"]["input_hydrogen"], 1)
        self.assertFalse(result["alignment_applied"])
        self.assertTrue(any("H/HD/HS" in item for item in result["warnings"]))

    def test_rotation_keeps_centroid_but_is_not_aligned_away(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "C", 1, 0, 0),
                _atom(2, "O1", "OA", -1, 0, 0),
            ]
        )
        optimized_pose = _pose(
            [
                _atom(1, "C1", "C", 0, 1, 0),
                _atom(2, "O1", "OA", 0, -1, 0),
            ]
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(
            result["heavy_atom_rmsd_no_alignment_angstrom"],
            math.sqrt(2),
            places=6,
        )
        self.assertAlmostEqual(
            result["mean_heavy_atom_displacement_angstrom"],
            math.sqrt(2),
            places=6,
        )
        self.assertEqual(result["centroid_displacement_angstrom"], 0.0)

    def test_mixed_displacements_report_mean_rmsd_max_and_max_atom(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0),
                _atom(2, "O1", "OA", 2, 0, 0),
            ]
        )
        optimized_pose = _pose(
            [
                _atom(1, "C1", "C", 1, 0, 0),
                _atom(2, "O1", "OA", 2, 0, 0),
            ]
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(
            result["heavy_atom_rmsd_no_alignment_angstrom"],
            math.sqrt(0.5),
            places=6,
        )
        self.assertEqual(result["mean_heavy_atom_displacement_angstrom"], 0.5)
        self.assertEqual(result["max_heavy_atom_displacement_angstrom"], 1.0)
        self.assertEqual(result["centroid_displacement_angstrom"], 0.5)
        self.assertEqual(result["max_displacement_atom"]["input_serial"], 1)
        self.assertEqual(result["max_displacement_atom"]["atom_name"], "C1")

    def test_serial_fallback_allows_atom_line_reordering(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0),
                _atom(2, "O1", "OA", 1, 0, 0),
            ]
        )
        optimized_pose = _pose(
            [
                _atom(2, "O1", "OA", 1, 1, 0),
                _atom(1, "C1", "C", 0, 1, 0),
            ]
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["heavy_atom_count"], 2)
        self.assertEqual(result["heavy_atom_rmsd_no_alignment_angstrom"], 1.0)

    def test_smiles_mapping_has_priority_and_allows_serial_renumbering(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0, charge=0.1),
                _atom(2, "O1", "OA", 1, 0, 0, charge=-0.1),
            ],
            smiles="CO",
            mapping=[(1, 1), (2, 2)],
        )
        optimized_pose = _pose(
            [
                _atom(202, "O1", "OA", 1, 2, 0, charge=-0.1),
                _atom(101, "C1", "C", 0, 2, 0, charge=0.1),
            ],
            smiles="CO",
            mapping=[(1, 101), (2, 202)],
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mapping_method"], "meeko_smiles_index")
        self.assertEqual(result["heavy_atom_rmsd_no_alignment_angstrom"], 2.0)
        self.assertEqual(result["max_displacement_atom"]["match_key"], "smiles:1")
        self.assertEqual(result["max_displacement_atom"]["input_serial"], 1)
        self.assertEqual(result["max_displacement_atom"]["optimized_serial"], 101)
        self.assertFalse(any("缺少完整 Meeko" in item for item in result["warnings"]))

    def test_macrocycle_pseudo_hydrogen_and_flex_atoms_are_excluded(self) -> None:
        input_pose = _pose(
            [
                _atom(1, "C1", "CG0", 0, 0, 0),
                _atom(2, "*1", "G0", 1, 0, 0),
                _atom(3, "H1", "HD", 0, 1, 0),
                _atom(4, "N1", "NA", 2, 0, 0),
            ]
        )
        optimized_pose = _pose(
            [
                _atom(4, "N1", "NA", 2, 1, 0),
                _atom(2, "*1", "G0", 99, 99, 99),
                _atom(1, "C1", "CG0", 0, 1, 0),
                _atom(3, "H1", "HD", 99, 99, 99),
            ],
            extra_lines=[
                "BEGIN_RES TYR A 42",
                _atom(1, "CB", "C", 50, 50, 50, residue="TYR", chain="A", residue_number=42),
                _atom(2, "OH", "OA", 51, 50, 50, residue="TYR", chain="A", residue_number=42),
                "END_RES TYR A 42",
            ],
        )

        result = compare_local_only_pose_texts(input_pose, optimized_pose)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["heavy_atom_count"], 2)
        self.assertEqual(result["heavy_atom_rmsd_no_alignment_angstrom"], 1.0)
        self.assertEqual(result["excluded_atom_counts"]["input_pseudo"], 1)
        self.assertEqual(result["excluded_atom_counts"]["input_hydrogen"], 1)
        self.assertEqual(
            result["excluded_atom_counts"]["optimized_flexible_receptor"], 2
        )
        self.assertTrue(any("柔性受体" in item for item in result["warnings"]))
        self.assertTrue(any("G*" in item for item in result["warnings"]))

    def test_file_wrapper_records_hashes(self) -> None:
        pose = _pose([_atom(1, "C1", "C", 0, 0, 0)])
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "输入 配体.pdbqt"
            optimized_path = Path(temp_dir) / "优化 配体.pdbqt"
            input_path.write_text(pose, encoding="utf-8")
            optimized_path.write_text(pose, encoding="utf-8")

            result = compare_local_only_poses(input_path, optimized_path)

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["input_sha256"]), 64)
        self.assertEqual(len(result["optimized_sha256"]), 64)


class PoseComparisonFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0, charge=0.1),
                _atom(2, "O1", "OA", 1, 0, 0, charge=-0.1),
            ]
        )

    def assert_error_code(
        self,
        input_pose: str,
        optimized_pose: str,
        expected_code: str,
    ) -> None:
        result = compare_local_only_pose_texts(input_pose, optimized_pose)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], expected_code, result)

    def test_rejects_multiple_models_and_unclosed_model(self) -> None:
        second_model = (
            _pose([_atom(1, "C1", "C", 0, 0, 0)], wrapped=True)
            + _pose([_atom(1, "C1", "C", 1, 0, 0)], wrapped=True)
        )
        self.assert_error_code(
            second_model,
            self.valid,
            "LOCAL_POSE_INPUT_MULTIPLE_MODELS",
        )
        unclosed = "MODEL 1\nROOT\n" + _atom(1, "C1", "C", 0, 0, 0) + "\n"
        self.assert_error_code(
            unclosed,
            self.valid,
            "LOCAL_POSE_INPUT_MODEL_INVALID",
        )

    def test_rejects_malformed_coordinate_and_duplicate_serial(self) -> None:
        malformed = self.valid.replace("   0.000", "     nan", 1)
        self.assert_error_code(
            malformed,
            self.valid,
            "LOCAL_POSE_INPUT_ATOM_INVALID",
        )
        duplicate = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0),
                _atom(1, "O1", "OA", 1, 0, 0),
            ]
        )
        self.assert_error_code(
            duplicate,
            self.valid,
            "LOCAL_POSE_INPUT_ATOM_ID_DUPLICATE",
        )
        missing_type = _atom(1, "C1", "C", 0, 0, 0).rsplit(" ", 1)[0] + "\n"
        self.assert_error_code(
            missing_type,
            self.valid,
            "LOCAL_POSE_INPUT_ATOM_INVALID",
        )

    def test_rejects_atom_set_and_full_identity_changes(self) -> None:
        missing_atom = _pose([_atom(1, "C1", "C", 0, 0, 0, charge=0.1)])
        self.assert_error_code(
            self.valid,
            missing_atom,
            "LOCAL_POSE_ATOM_SET_MISMATCH",
        )
        changed_type = self.valid.replace("OA\n", "NA\n")
        self.assert_error_code(
            self.valid,
            changed_type,
            "LOCAL_POSE_ATOM_IDENTITY_MISMATCH",
        )
        changed_charge = self.valid.replace("-0.100", "-0.300")
        self.assert_error_code(
            self.valid,
            changed_charge,
            "LOCAL_POSE_ATOM_IDENTITY_MISMATCH",
        )

    def test_rejects_incomplete_or_disagreeing_smiles_mapping(self) -> None:
        mapped = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0),
                _atom(2, "O1", "OA", 1, 0, 0),
            ],
            smiles="CO",
            mapping=[(1, 1), (2, 2)],
        )
        self.assert_error_code(
            mapped,
            self.valid,
            "LOCAL_POSE_TOPOLOGY_MAPPING_MISMATCH",
        )
        odd_mapping = mapped.replace(
            "REMARK SMILES IDX 1 1 2 2",
            "REMARK SMILES IDX 1 1 2",
        )
        self.assert_error_code(
            odd_mapping,
            mapped,
            "LOCAL_POSE_INPUT_SMILES_MAPPING_INVALID",
        )
        changed_smiles = mapped.replace("REMARK SMILES CO", "REMARK SMILES CN")
        self.assert_error_code(
            mapped,
            changed_smiles,
            "LOCAL_POSE_TOPOLOGY_MAPPING_MISMATCH",
        )

    def test_rejects_unclosed_flexible_residue_block(self) -> None:
        invalid = self.valid + "BEGIN_RES TYR A 42\n" + _atom(
            7,
            "CB",
            "C",
            1,
            2,
            3,
            residue="TYR",
            chain="A",
            residue_number=42,
        )
        self.assert_error_code(
            invalid,
            self.valid,
            "LOCAL_POSE_INPUT_FLEX_BLOCK_INVALID",
        )

    def test_rejects_smiles_mapping_that_does_not_cover_heavy_atoms(self) -> None:
        incomplete = _pose(
            [
                _atom(1, "C1", "C", 0, 0, 0),
                _atom(2, "O1", "OA", 1, 0, 0),
            ],
            smiles="CO",
            mapping=[(1, 1)],
        )
        self.assert_error_code(
            incomplete,
            incomplete,
            "LOCAL_POSE_INPUT_SMILES_MAPPING_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
