from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.hydrated_postprocess import (  # noqa: E402
    HydratedPostprocessError,
    postprocess_hydrated_pdbqt,
)


def _atom(
    serial: int,
    atom_name: str,
    x: float,
    y: float,
    z: float,
    atom_type: str,
) -> str:
    return (
        f"ATOM  {serial:5d} {atom_name:<4} UNL     1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00  0.00     0.000 {atom_type:<2}"
    )


def _water_map_text(
    assignments: dict[tuple[int, int, int], float],
    *,
    nelements: tuple[int, int, int] = (20, 20, 20),
    spacing: float = 1.0,
    center: tuple[float, float, float] = (10.0, 10.0, 10.0),
    default: float = 0.0,
) -> str:
    shape = tuple(value + 1 for value in nelements)
    values = [default] * (shape[0] * shape[1] * shape[2])
    for (x_index, y_index, z_index), value in assignments.items():
        flat_index = (
            z_index * shape[1] + y_index
        ) * shape[0] + x_index
        values[flat_index] = value
    return "\n".join(
        (
            "GRID_PARAMETER_FILE receptor.gpf",
            "GRID_DATA_FILE receptor.maps.fld",
            "MACROMOLECULE receptor.pdbqt",
            f"SPACING {spacing}",
            f"NELEMENTS {nelements[0]} {nelements[1]} {nelements[2]}",
            f"CENTER {center[0]} {center[1]} {center[2]}",
            *(f"{value:.4f}" for value in values),
        )
    ) + "\n"


def _pose(
    mode: int,
    affinity: float,
    water_specs: list[tuple[int, str, float, float, float]],
    *,
    first_heavy_name: str = "C1",
    first_heavy_type: str = "C",
    first_heavy_coordinate: tuple[float, float, float] = (1.0, 1.0, 1.0),
    second_heavy_coordinate: tuple[float, float, float] = (1.0, 2.0, 1.0),
    branch_endpoint: int = 2,
) -> list[str]:
    return [
        f"MODEL {mode}",
        f"REMARK VINA RESULT: {affinity:.3f} 0.000 0.000",
        "ROOT",
        _atom(
            1,
            first_heavy_name,
            *first_heavy_coordinate,
            first_heavy_type,
        ),
        "ENDROOT",
        f"BRANCH   1   {branch_endpoint}",
        _atom(
            2,
            "C2",
            *second_heavy_coordinate,
            "C",
        ),
        *(
            _atom(serial, name, x, y, z, "W")
            for serial, name, x, y, z in water_specs
        ),
        f"ENDBRANCH   1   {branch_endpoint}",
        "TORSDOF 1",
        "ENDMDL",
    ]


def _write_inputs(
    root: Path,
    *,
    hydrated_lines: list[str],
    map_assignments: dict[tuple[int, int, int], float],
    receptor_lines: list[str] | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    hydrated = root / "hydrated_raw.pdbqt"
    receptor = root / "receptor.pdbqt"
    water_map = root / "receptor.W.map"
    retained = root / "retained_water_annotated.pdbqt"
    water_free = root / "water_free_ligand.pdbqt"
    hydrated.write_text("\n".join(hydrated_lines) + "\n", encoding="utf-8")
    receptor.write_text(
        "\n".join(
            receptor_lines
            or [
                _atom(1, "CA", 19.0, 19.0, 19.0, "C"),
                _atom(2, "H1", 5.0, 5.0, 5.0, "HD"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    water_map.write_text(
        _water_map_text(map_assignments),
        encoding="ascii",
    )
    return hydrated, receptor, water_map, retained, water_free


def _water_atom_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.startswith(("ATOM  ", "HETATM"))
        and line.split()[-1].upper() == "W"
    ]


class HydratedPostprocessTests(unittest.TestCase):
    def test_filters_each_model_and_preserves_scores_and_torsion_records(self) -> None:
        hydrated_lines = [
            *_pose(
                1,
                -8.0,
                [
                    (10, "WAT", 4.0, 4.0, 4.0),
                    (11, "WAT", 2.0, 1.0, 1.0),
                ],
            ),
            *_pose(
                2,
                -7.5,
                [
                    (10, "WAT", 8.0, 8.0, 8.0),
                    (11, "WAT", 19.0, 18.0, 19.0),
                    (12, "WAT", 5.0, 5.0, 5.0),
                ],
            ),
        ]
        assignments = {
            (4, 4, 4): -0.6,
            (5, 5, 5): -0.7,
            (8, 8, 8): -0.4,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = _write_inputs(
                root,
                hydrated_lines=hydrated_lines,
                map_assignments=assignments,
            )
            hydrated, receptor, water_map, retained, water_free = paths
            raw_before = hydrated.read_bytes()

            result = postprocess_hydrated_pdbqt(*paths)

            self.assertTrue(result["ok"])
            self.assertEqual(
                result["summary"],
                {
                    "pose_count": 2,
                    "raw_water_count": 5,
                    "candidate_water_count": 5,
                    "retained_water_count": 3,
                    "strong_water_count": 2,
                    "weak_water_count": 1,
                    "displaced_water_count": 2,
                },
            )
            self.assertEqual(
                [pose["retained_water_count"] for pose in result["poses"]],
                [1, 2],
            )
            self.assertEqual(
                [pose["mode"] for pose in result["poses"]],
                [1, 2],
            )
            self.assertEqual(
                [pose["raw_affinity"] for pose in result["poses"]],
                [-8.0, -7.5],
            )

            mode_one_displaced = result["poses"][0]["waters"][1]
            self.assertIn(
                "ligand_heavy_atom_overlap",
                mode_one_displaced["removal_reasons"],
            )
            mode_two_receptor_overlap = result["poses"][1]["waters"][1]
            self.assertIn(
                "receptor_non_hd_atom_overlap",
                mode_two_receptor_overlap["removal_reasons"],
            )
            mode_two_hd_site = result["poses"][1]["waters"][2]
            self.assertEqual(mode_two_hd_site["classification"], "strong")

            retained_text = retained.read_text(encoding="utf-8")
            water_free_text = water_free.read_text(encoding="utf-8")
            self.assertEqual(len(_water_atom_lines(retained_text)), 3)
            self.assertEqual(len(_water_atom_lines(water_free_text)), 0)
            self.assertEqual(
                retained_text.count("REMARK DOCKSTART WATER"),
                3,
            )
            for score_line in (
                "REMARK VINA RESULT: -8.000 0.000 0.000",
                "REMARK VINA RESULT: -7.500 0.000 0.000",
            ):
                self.assertIn(score_line, retained_text)
                self.assertIn(score_line, water_free_text)
            for keyword in (
                "MODEL ",
                "ENDMDL",
                "ROOT",
                "ENDROOT",
                "BRANCH ",
                "ENDBRANCH ",
                "TORSDOF ",
            ):
                raw_count = sum(
                    keyword in line
                    for line in hydrated.read_text(
                        encoding="utf-8"
                    ).splitlines()
                )
                self.assertEqual(
                    sum(keyword in line for line in retained_text.splitlines()),
                    raw_count,
                )
                self.assertEqual(
                    sum(keyword in line for line in water_free_text.splitlines()),
                    raw_count,
                )
            self.assertEqual(hydrated.read_bytes(), raw_before)
            self.assertEqual(
                result["outputs"]["retained_water_annotated"]["sha256"],
                hashlib.sha256(retained.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["outputs"]["water_free_ligand"]["sha256"],
                hashlib.sha256(water_free.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["manifest"]["semantics"]["affinity"],
                "original_ad4_hydrated_affinity_unchanged",
            )

    def test_water_identity_uses_final_atom_type_not_atom_name(self) -> None:
        hydrated_lines = _pose(
            1,
            -6.0,
            [(10, "O1", 4.0, 4.0, 4.0)],
            first_heavy_name="WAT",
            first_heavy_type="C",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _write_inputs(
                Path(temp_dir),
                hydrated_lines=hydrated_lines,
                map_assignments={(4, 4, 4): -0.6},
            )

            result = postprocess_hydrated_pdbqt(*paths)

            self.assertEqual(result["summary"]["candidate_water_count"], 1)
            self.assertEqual(result["summary"]["retained_water_count"], 1)
            water_free = paths[-1].read_text(encoding="utf-8")
            self.assertIn("WAT", water_free)
            self.assertNotIn(
                _atom(10, "O1", 4.0, 4.0, 4.0, "W"),
                water_free,
            )

    def test_strict_overlap_and_map_threshold_boundaries(self) -> None:
        hydrated_lines = _pose(
            1,
            -6.0,
            [
                (10, "WAT", 2.03, 0.0, 0.0),
                (11, "WAT", 6.0, 0.0, 0.0),
                (12, "WAT", 10.0, 0.0, 0.0),
            ],
            first_heavy_coordinate=(0.0, 0.0, 0.0),
            second_heavy_coordinate=(0.0, 4.0, 0.0),
        )
        assignments = {
            (2, 0, 0): -0.5,
            (6, 0, 0): -0.3,
            (10, 0, 0): -0.5001,
        }
        receptor_lines = [_atom(1, "CA", 20.0, 20.0, 20.0, "C")]

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _write_inputs(
                Path(temp_dir),
                hydrated_lines=hydrated_lines,
                map_assignments=assignments,
                receptor_lines=receptor_lines,
            )

            result = postprocess_hydrated_pdbqt(*paths)

            waters = result["poses"][0]["waters"]
            self.assertEqual(
                [water["classification"] for water in waters],
                ["weak", "displaced", "strong"],
            )
            self.assertNotIn(
                "ligand_heavy_atom_overlap",
                waters[0]["removal_reasons"],
            )
            self.assertEqual(result["summary"]["retained_water_count"], 2)

    def test_sampling_uses_minimum_in_plus_minus_rounded_radius(self) -> None:
        hydrated_lines = _pose(
            1,
            -6.0,
            [(10, "WAT", 4.0, 4.0, 4.0)],
        )
        assignments = {
            (4, 4, 4): -0.1,
            (5, 4, 4): -0.7,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _write_inputs(
                Path(temp_dir),
                hydrated_lines=hydrated_lines,
                map_assignments=assignments,
            )

            result = postprocess_hydrated_pdbqt(*paths)

            water = result["poses"][0]["waters"][0]
            self.assertEqual(water["classification"], "strong")
            self.assertEqual(water["map_affinity"], -0.7)
            self.assertEqual(
                water["map_sample"]["grid_index"],
                {"x": 5, "y": 4, "z": 4},
            )
            self.assertEqual(
                result["manifest"]["parameters"]["map_sample_radius_steps"],
                1,
            )

    def test_rejects_water_used_as_branch_endpoint_without_overwriting_outputs(
        self,
    ) -> None:
        hydrated_lines = _pose(
            1,
            -6.0,
            [(10, "WAT", 4.0, 4.0, 4.0)],
            branch_endpoint=10,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _write_inputs(
                Path(temp_dir),
                hydrated_lines=hydrated_lines,
                map_assignments={(4, 4, 4): -0.6},
            )
            retained = paths[-2]
            water_free = paths[-1]
            retained.write_text("retained sentinel\n", encoding="utf-8")
            water_free.write_text("dry sentinel\n", encoding="utf-8")

            with self.assertRaises(HydratedPostprocessError) as error:
                postprocess_hydrated_pdbqt(*paths)

            self.assertEqual(
                error.exception.code,
                "HYDRATED_PDBQT_WATER_BRANCH_ENDPOINT",
            )
            self.assertEqual(
                retained.read_text(encoding="utf-8"),
                "retained sentinel\n",
            )
            self.assertEqual(
                water_free.read_text(encoding="utf-8"),
                "dry sentinel\n",
            )

    def test_rejects_atoms_outside_models_and_unclosed_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = [_atom(1, "C1", 1.0, 1.0, 1.0, "C")]
            paths = _write_inputs(
                root,
                hydrated_lines=outside,
                map_assignments={},
            )
            with self.assertRaises(HydratedPostprocessError) as outside_error:
                postprocess_hydrated_pdbqt(*paths)
            self.assertEqual(
                outside_error.exception.code,
                "HYDRATED_PDBQT_ATOM_OUTSIDE_MODEL",
            )

            unclosed = _pose(
                1,
                -6.0,
                [(10, "WAT", 4.0, 4.0, 4.0)],
            )[:-1]
            paths[0].write_text(
                "\n".join(unclosed) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(HydratedPostprocessError) as model_error:
                postprocess_hydrated_pdbqt(*paths)
            self.assertEqual(
                model_error.exception.code,
                "HYDRATED_PDBQT_MODEL_UNCLOSED",
            )

    def test_nine_mode_count_contract_is_isolated_per_model(self) -> None:
        retained_by_mode = [1, 1, 0, 1, 2, 1, 2, 2, 1]
        coordinates = [
            (x, y, z)
            for z in (3.0, 7.0)
            for y in (3.0, 7.0, 11.0)
            for x in (3.0, 7.0, 11.0)
        ]
        self.assertEqual(len(coordinates), 18)
        hydrated_lines: list[str] = []
        assignments: dict[tuple[int, int, int], float] = {}
        strong_remaining = 7
        weak_remaining = 4
        coordinate_index = 0

        for mode, retained_count in enumerate(retained_by_mode, start=1):
            water_specs: list[tuple[int, str, float, float, float]] = []
            for water_offset in range(2):
                coordinate = coordinates[coordinate_index]
                coordinate_index += 1
                water_specs.append(
                    (
                        10 + water_offset,
                        "WAT",
                        coordinate[0],
                        coordinate[1],
                        coordinate[2],
                    )
                )
                if water_offset < retained_count:
                    if strong_remaining:
                        assignments[
                            tuple(int(value) for value in coordinate)
                        ] = -0.6
                        strong_remaining -= 1
                    else:
                        assignments[
                            tuple(int(value) for value in coordinate)
                        ] = -0.4
                        weak_remaining -= 1
            hydrated_lines.extend(
                _pose(
                    mode,
                    -8.0 + ((mode - 1) * 0.1),
                    water_specs,
                    first_heavy_coordinate=(20.0, 20.0, 20.0),
                    second_heavy_coordinate=(20.0, 19.0, 20.0),
                )
            )

        self.assertEqual(strong_remaining, 0)
        self.assertEqual(weak_remaining, 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _write_inputs(
                Path(temp_dir),
                hydrated_lines=hydrated_lines,
                map_assignments=assignments,
                receptor_lines=[
                    _atom(1, "CA", 0.0, 20.0, 0.0, "C"),
                ],
            )

            result = postprocess_hydrated_pdbqt(*paths)

            self.assertEqual(result["summary"]["candidate_water_count"], 18)
            self.assertEqual(result["summary"]["strong_water_count"], 7)
            self.assertEqual(result["summary"]["weak_water_count"], 4)
            self.assertEqual(result["summary"]["displaced_water_count"], 7)
            self.assertEqual(
                [
                    pose["retained_water_count"]
                    for pose in result["poses"]
                ],
                retained_by_mode,
            )
            self.assertEqual(
                [pose["mode"] for pose in result["poses"]],
                list(range(1, 10)),
            )

            raw_text = paths[0].read_text(encoding="utf-8")
            retained_text = paths[-2].read_text(encoding="utf-8")
            water_free_text = paths[-1].read_text(encoding="utf-8")
            self.assertEqual(len(_water_atom_lines(retained_text)), 11)
            self.assertEqual(len(_water_atom_lines(water_free_text)), 0)
            for keyword in (
                "ROOT",
                "ENDROOT",
                "BRANCH ",
                "ENDBRANCH ",
                "TORSDOF ",
            ):
                expected = sum(
                    keyword in line
                    for line in raw_text.splitlines()
                )
                self.assertEqual(
                    sum(
                        keyword in line
                        for line in retained_text.splitlines()
                    ),
                    expected,
                )
                self.assertEqual(
                    sum(
                        keyword in line
                        for line in water_free_text.splitlines()
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
