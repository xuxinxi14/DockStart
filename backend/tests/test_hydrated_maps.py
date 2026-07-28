from __future__ import annotations

import hashlib
import math
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.hydrated_maps import (  # noqa: E402
    HydratedMapError,
    generate_best_water_map,
    parse_autogrid_map,
)


def _map_text(
    values: list[float | str],
    *,
    spacing: float = 1.0,
    nelements: tuple[int, int, int] = (2, 2, 2),
    center: tuple[float, float, float] = (1.0, 1.0, 1.0),
    gpf: str = "receptor.gpf",
    fld: str = "receptor.maps.fld",
    receptor: str = "receptor.pdbqt",
) -> str:
    lines = [
        f"GRID_PARAMETER_FILE {gpf}",
        f"GRID_DATA_FILE {fld}",
        f"MACROMOLECULE {receptor}",
        f"SPACING {spacing}",
        f"NELEMENTS {nelements[0]} {nelements[1]} {nelements[2]}",
        f"CENTER {center[0]} {center[1]} {center[2]}",
        *(str(value) for value in values),
    ]
    return "\n".join(lines) + "\n"


def _write_map(
    path: Path,
    values: list[float | str],
    **kwargs: object,
) -> None:
    path.write_text(_map_text(values, **kwargs), encoding="ascii")


class HydratedMapTests(unittest.TestCase):
    def test_generates_fixed_best_values_and_hashes(self) -> None:
        oa_values = [-1.0, -0.2, 0.0, 0.1, -2.0] + [-0.1] * 22
        hd_values = [-0.5, -0.3, -0.4, -0.5, 0.2] + [-0.2] * 22

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oa_path = root / "receptor.OA.map"
            hd_path = root / "receptor.HD.map"
            output_path = root / "receptor.W.map"
            _write_map(oa_path, oa_values)
            _write_map(hd_path, hd_values)

            result = generate_best_water_map(oa_path, hd_path, output_path)

            self.assertTrue(result["ok"])
            output_lines = output_path.read_text(encoding="ascii").splitlines()
            self.assertEqual(
                output_lines[6:11],
                ["-0.6000", "-0.1800", "-0.2400", "-0.2000", "-0.2000"],
            )
            self.assertEqual(output_lines[11], "-0.1200")
            self.assertEqual(result["parameters"]["mode"], "BEST")
            self.assertEqual(result["parameters"]["weight"], 0.6)
            self.assertEqual(result["parameters"]["entropy"], -0.2)
            self.assertEqual(result["statistics"]["selected_oa_points"], 1)
            self.assertEqual(result["statistics"]["selected_hd_points"], 24)
            self.assertEqual(result["statistics"]["entropy_points"], 2)
            self.assertEqual(
                result["output"]["sha256"],
                hashlib.sha256(output_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                result["sources"]["oa"]["sha256"],
                hashlib.sha256(oa_path.read_bytes()).hexdigest(),
            )

    def test_zero_does_not_trigger_entropy_and_negative_zero_is_normalized(self) -> None:
        oa_values = [0.0] * 27
        hd_values = [-0.0] * 27

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oa_path = root / "receptor.OA.map"
            hd_path = root / "receptor.HD.map"
            output_path = root / "receptor.W.map"
            _write_map(oa_path, oa_values)
            _write_map(hd_path, hd_values)

            result = generate_best_water_map(oa_path, hd_path, output_path)

            self.assertEqual(result["statistics"]["entropy_points"], 0)
            self.assertEqual(result["statistics"]["minimum"], 0.0)
            self.assertEqual(
                set(output_path.read_text(encoding="ascii").splitlines()[6:]),
                {"0.0000"},
            )

    def test_parse_uses_x_fastest_index_order_and_samples_nearby_minimum(self) -> None:
        values = [float(index) for index in range(27)]
        # For shape 3x3x3, x=2, y=1, z=1 has flat index 14.
        values[14] = -9.0

        with tempfile.TemporaryDirectory() as temp_dir:
            map_path = Path(temp_dir) / "receptor.W.map"
            _write_map(map_path, values)

            parsed = parse_autogrid_map(map_path)
            exact = parsed.minimum_near(2.0, 1.0, 1.0, radius_steps=0)
            nearby = parsed.minimum_near(1.0, 1.0, 1.0, radius_steps=1)

            self.assertIsNotNone(exact)
            self.assertEqual(exact["value"], -9.0)
            self.assertEqual(exact["grid_index"], {"x": 2, "y": 1, "z": 1})
            self.assertIsNotNone(nearby)
            self.assertEqual(nearby["value"], -9.0)
            self.assertEqual(nearby["sample_count"], 27)

    def test_rejects_header_order_and_blank_data_lines(self) -> None:
        values = [0.0] * 27
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wrong_header = root / "wrong.map"
            text = _map_text(values).replace(
                "GRID_PARAMETER_FILE",
                "GRID_DATA_FILE",
                1,
            )
            wrong_header.write_text(text, encoding="ascii")

            with self.assertRaises(HydratedMapError) as header_error:
                parse_autogrid_map(wrong_header)
            self.assertEqual(
                header_error.exception.code,
                "HYDRATED_MAP_HEADER_INVALID",
            )

            blank_data = root / "blank.map"
            blank_data.write_text(
                _map_text(values).replace("\n0.0\n", "\n\n", 1),
                encoding="ascii",
            )
            with self.assertRaises(HydratedMapError) as data_error:
                parse_autogrid_map(blank_data)
            self.assertEqual(
                data_error.exception.code,
                "HYDRATED_MAP_DATA_INVALID",
            )

    def test_rejects_nonfinite_values_and_wrong_point_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nonfinite = root / "nonfinite.map"
            _write_map(nonfinite, ["nan", *([0.0] * 26)])
            with self.assertRaises(HydratedMapError) as finite_error:
                parse_autogrid_map(nonfinite)
            self.assertEqual(
                finite_error.exception.code,
                "HYDRATED_MAP_NUMBER_NONFINITE",
            )

            too_short = root / "short.map"
            _write_map(too_short, [0.0] * 26)
            with self.assertRaises(HydratedMapError) as count_error:
                parse_autogrid_map(too_short)
            self.assertEqual(
                count_error.exception.code,
                "HYDRATED_MAP_POINT_COUNT_MISMATCH",
            )

            too_long = root / "long.map"
            _write_map(too_long, [0.0] * 28)
            with self.assertRaises(HydratedMapError) as long_error:
                parse_autogrid_map(too_long)
            self.assertEqual(
                long_error.exception.code,
                "HYDRATED_MAP_POINT_COUNT_MISMATCH",
            )

    def test_rejects_nonpositive_or_odd_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            zero_spacing = root / "zero.map"
            _write_map(zero_spacing, [0.0] * 27, spacing=0.0)
            with self.assertRaises(HydratedMapError) as spacing_error:
                parse_autogrid_map(zero_spacing)
            self.assertEqual(
                spacing_error.exception.code,
                "HYDRATED_MAP_SPACING_INVALID",
            )

            odd_nelements = root / "odd.map"
            _write_map(
                odd_nelements,
                [0.0] * 36,
                nelements=(3, 2, 2),
            )
            with self.assertRaises(HydratedMapError) as geometry_error:
                parse_autogrid_map(odd_nelements)
            self.assertEqual(
                geometry_error.exception.code,
                "HYDRATED_MAP_NELEMENTS_INVALID",
            )

    def test_rejects_oa_hd_geometry_or_provenance_mismatch_before_write(self) -> None:
        values = [0.0] * 27
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            oa_path = root / "receptor.OA.map"
            hd_path = root / "receptor.HD.map"
            output_path = root / "receptor.W.map"
            output_path.write_text("existing\n", encoding="ascii")
            _write_map(oa_path, values)
            _write_map(
                hd_path,
                values,
                center=(1.0, 1.0, 1.0001),
            )

            with self.assertRaises(HydratedMapError) as geometry_error:
                generate_best_water_map(oa_path, hd_path, output_path)
            self.assertEqual(
                geometry_error.exception.code,
                "HYDRATED_MAP_GEOMETRY_MISMATCH",
            )
            self.assertEqual(
                output_path.read_text(encoding="ascii"),
                "existing\n",
            )

            _write_map(hd_path, values, gpf="other.gpf")
            with self.assertRaises(HydratedMapError) as provenance_error:
                generate_best_water_map(oa_path, hd_path, output_path)
            self.assertEqual(
                provenance_error.exception.code,
                "HYDRATED_MAP_PROVENANCE_MISMATCH",
            )
            self.assertEqual(
                output_path.read_text(encoding="ascii"),
                "existing\n",
            )

    def test_rejects_nonfinite_lookup_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            map_path = Path(temp_dir) / "receptor.W.map"
            _write_map(map_path, [0.0] * 27)
            parsed = parse_autogrid_map(map_path)

            with self.assertRaises(HydratedMapError) as error:
                parsed.minimum_near(
                    math.inf,
                    0.0,
                    0.0,
                    radius_steps=1,
                )
            self.assertEqual(
                error.exception.code,
                "HYDRATED_MAP_COORDINATE_NONFINITE",
            )


if __name__ == "__main__":
    unittest.main()
